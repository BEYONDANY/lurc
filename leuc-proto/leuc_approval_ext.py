# AI-GEN-BEGIN
"""审批流扩展：申请人确认、知会、驳回指定节点、合并系统管理员。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def normalize_step_tuple(step) -> tuple:
    """统一为 (step_key, step_label, assignee_id, step_kind, parallel_group)。"""
    if len(step) >= 5:
        return (step[0], step[1], step[2], step[3] or "approve", step[4])
    if len(step) == 4:
        return (step[0], step[1], step[2], step[3] or "approve", None)
    return (step[0], step[1], step[2], "approve", None)


def append_applicant_confirm(steps, applicant_id: int):
    """审批链末尾追加申请人确认。"""
    out = [normalize_step_tuple(s) for s in (steps or [])]
    used = {int(s[2]) for s in out if s[2]}
    # 即使申请人已出现过，确认节点仍要回到自己（允许重复）
    out.append(
        ("applicant_confirm", "申请人确认", int(applicant_id), "confirm", None)
    )
    return out


def collect_cc_for_system_owners(db, find_approver_fn, steps, applicant_id: int):
    """系统管理员节点 → 知其直接领导（只阅读确认）。"""
    ccs = []
    seen = set()
    for s in steps:
        sk, sl, aid, kind, _pg = normalize_step_tuple(s)
        if sk != "system_owner" or not aid:
            continue
        leader = find_approver_fn(db, int(aid))
        if not leader:
            continue
        lid = int(leader)
        if lid in (int(aid), int(applicant_id)):
            continue
        if lid in seen:
            continue
        seen.add(lid)
        au = db.execute(
            "SELECT display_name FROM users WHERE id = ?", (int(aid),)
        ).fetchone()
        owner_name = au["display_name"] if au else str(aid)
        ccs.append(
            {
                "assignee_id": lid,
                "owner_id": int(aid),
                "label": f"知会·{owner_name}的直接领导",
                "parallel_group": f"cc-owner-{aid}",
            }
        )
    return ccs


def spawn_cc_todos(db, *, app_id, initiator_id, todo_type, title, meta, ccs, now=None):
    """与系统管理员节点同步创建知会待办（不阻塞主链）。"""
    now = now or datetime.now().strftime("%Y-%m-%d")
    created = []
    for cc in ccs or []:
        tcur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
             application_id, step_order, meta)
            VALUES (?,?,?,?, 'pending', 'open', ?, ?, NULL, ?)""",
            (
                cc["assignee_id"],
                initiator_id,
                f"{title} · {cc['label']}",
                "知会确认",
                now,
                app_id,
                json.dumps(
                    {
                        **(meta or {}),
                        "cc": True,
                        "cc_label": cc["label"],
                        "owner_id": cc.get("owner_id"),
                        "read_only": True,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        created.append(tcur.lastrowid)
    return created


def user_permission_snapshot(db, user_id: int) -> dict[str, Any]:
    """延期审批详情：用户权限快照（突出敏感）。"""
    rows = db.execute(
        """SELECT a.id, a.account_name, a.account_label, a.can_login, a.has_sensitive,
                  a.perm_summary, s.id AS system_id, s.code AS system_code, s.name AS system_name
           FROM user_system_accounts a
           JOIN systems s ON s.id = a.system_id
           WHERE a.user_id = ?
           ORDER BY a.has_sensitive DESC, s.id, a.id""",
        (int(user_id),),
    ).fetchall()
    accounts = [dict(r) for r in rows]
    sensitive = [a for a in accounts if a.get("has_sensitive")]
    return {
        "accounts": accounts,
        "sensitive_accounts": sensitive,
        "sensitive_count": len(sensitive),
        "account_count": len(accounts),
    }


def _meta_with_resubmit(meta_raw, *, reject_from_step: int, reject_to_step: int) -> str:
    try:
        meta = json.loads(meta_raw or "{}")
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta["needs_resubmit"] = True
    meta["reject_from_step"] = int(reject_from_step)
    meta["reject_to_step"] = int(reject_to_step)
    return json.dumps(meta, ensure_ascii=False)


def reject_to_specified_step(
    db,
    *,
    app_id: int,
    current_step_order: int,
    reject_to_step: int,
    todo_row,
    remark: str | None,
    now: str,
):
    """驳回到指定节点（含 0=申请人修改重提）；目标可改单，重提后回到驳回人。"""
    steps = db.execute(
        """SELECT * FROM application_steps
        WHERE application_id = ? ORDER BY step_order""",
        (app_id,),
    ).fetchall()
    if not steps:
        return {"ok": False, "error": "无审批步骤"}
    orders = {int(s["step_order"]) for s in steps}
    target = int(reject_to_step)
    cur_order = int(current_step_order)
    # 0 = 申请人修改重提；其它须为当前步之前的真实节点
    if target != 0 and (target not in orders or target >= cur_order):
        return {"ok": False, "error": "驳回目标节点无效"}
    if target == 0 and cur_order < 1:
        return {"ok": False, "error": "驳回目标节点无效"}
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    note = (remark or "").strip() or None
    # 当前步标记驳回
    db.execute(
        """UPDATE application_steps SET status = 'rejected', decided_at = ?, remark = ?
        WHERE application_id = ? AND step_order = ?""",
        (now, note, app_id, cur_order),
    )
    db.execute(
        "UPDATE todos SET bucket = 'done', status = 'rejected', remark = ? WHERE id = ?",
        (note, todo_row["id"]),
    )
    # 中间步骤重置
    for s in steps:
        so = int(s["step_order"])
        if target < so < cur_order:
            db.execute(
                """UPDATE application_steps
                SET status = 'waiting', todo_id = NULL, decided_at = NULL, remark = NULL
                WHERE id = ?""",
                (s["id"],),
            )

    meta_json = _meta_with_resubmit(
        todo_row["meta"], reject_from_step=cur_order, reject_to_step=target
    )

    if target == 0:
        # 申请人修改重提
        label = "申请人修改重提"
        assignee_id = int(app["applicant_id"])
        tcur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
             application_id, step_order, meta)
            VALUES (?,?,?,?, 'pending', 'open', ?, ?, ?, ?)""",
            (
                assignee_id,
                app["applicant_id"],
                f"{app['title']} · {label}",
                todo_row["todo_type"],
                now,
                app_id,
                0,
                meta_json,
            ),
        )
        db.execute(
            """UPDATE applications
            SET status = 'returned', current_step = 0, updated_at = ?,
                reject_to_step = 0, reject_from_step = ?
            WHERE id = ?""",
            (now, cur_order, app_id),
        )
        db.execute(
            """UPDATE todos SET title = ?, status = 'open'
            WHERE application_id = ? AND bucket = 'initiated'""",
            (f"{app['title']}（已驳回至申请人，待修改重提）", app_id),
        )
        return {
            "ok": True,
            "message": "已驳回至申请人，可修改后再次提交",
            "reject_to_step": 0,
            "todo_id": tcur.lastrowid,
        }

    target_row = db.execute(
        """SELECT * FROM application_steps
        WHERE application_id = ? AND step_order = ?""",
        (app_id, target),
    ).fetchone()
    tcur = db.execute(
        """INSERT INTO todos
        (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
         application_id, step_order, meta)
        VALUES (?,?,?,?, 'pending', 'open', ?, ?, ?, ?)""",
        (
            target_row["assignee_id"],
            app["applicant_id"],
            f"{app['title']} · {target_row['step_label']}（驳回重办·可改单）",
            todo_row["todo_type"],
            now,
            app_id,
            target,
            meta_json,
        ),
    )
    db.execute(
        """UPDATE application_steps
        SET status = 'pending', todo_id = ?, decided_at = NULL, remark = NULL
        WHERE id = ?""",
        (tcur.lastrowid, target_row["id"]),
    )
    db.execute(
        """UPDATE applications
        SET status = 'returned', current_step = ?, updated_at = ?,
            reject_to_step = ?, reject_from_step = ?
        WHERE id = ?""",
        (target, now, target, cur_order, app_id),
    )
    db.execute(
        """UPDATE todos SET title = ?, status = 'open'
        WHERE application_id = ? AND bucket = 'initiated'""",
        (
            f"{app['title']}（已驳回至{target_row['step_label']}，待修改重提）",
            app_id,
        ),
    )
    return {
        "ok": True,
        "message": f"已驳回至「{target_row['step_label']}」，可修改后再次提交",
        "reject_to_step": target,
        "todo_id": tcur.lastrowid,
    }


def jump_to_reject_from_step(
    db,
    *,
    app_id: int,
    reject_from_step: int,
    todo_row,
    meta_json: str,
    remark: str | None,
    now: str,
    todo_type: str | None = None,
):
    """改单重提后直接回到原驳回人节点。"""
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not app:
        return {"ok": False, "error": "申请不存在"}
    target = int(reject_from_step)
    target_row = db.execute(
        """SELECT * FROM application_steps
        WHERE application_id = ? AND step_order = ?""",
        (app_id, target),
    ).fetchone()
    if not target_row:
        return {"ok": False, "error": "原驳回节点不存在"}
    note = (remark or "").strip() or None
    cur_order = todo_row["step_order"]
    if cur_order not in (None, "", 0, "0"):
        db.execute(
            """UPDATE application_steps
            SET status = 'approved', decided_at = ?, remark = ?
            WHERE application_id = ? AND step_order = ?""",
            (now, note or "修改后重提", app_id, int(cur_order)),
        )
    # 中间仍 waiting 的步骤标记跳过（直达驳回人）
    db.execute(
        """UPDATE application_steps SET status = 'skipped'
        WHERE application_id = ? AND status = 'waiting'
          AND step_order > ? AND step_order < ?""",
        (app_id, int(cur_order or 0), target),
    )
    tcur = db.execute(
        """INSERT INTO todos
        (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
         application_id, step_order, meta)
        VALUES (?,?,?,?, 'pending', 'open', ?, ?, ?, ?)""",
        (
            target_row["assignee_id"],
            app["applicant_id"],
            f"{app['title']} · {target_row['step_label']}（重提待审）",
            todo_type or todo_row["todo_type"],
            now,
            app_id,
            target,
            meta_json,
        ),
    )
    db.execute(
        """UPDATE application_steps
        SET status = 'pending', todo_id = ?, decided_at = NULL, remark = NULL
        WHERE id = ?""",
        (tcur.lastrowid, target_row["id"]),
    )
    db.execute(
        """UPDATE applications
        SET status = 'pending', current_step = ?, updated_at = ?,
            reject_to_step = NULL, reject_from_step = NULL
        WHERE id = ?""",
        (target, now, app_id),
    )
    db.execute(
        """UPDATE todos SET title = ?, status = 'open'
        WHERE application_id = ? AND bucket = 'initiated'""",
        (f"{app['title']}（已重提·{target_row['step_label']}）", app_id),
    )
    au = db.execute(
        "SELECT display_name FROM users WHERE id = ?", (target_row["assignee_id"],)
    ).fetchone()
    return {
        "ok": True,
        "message": f"已重新提交，流转至 {au['display_name'] if au else ''}（{target_row['step_label']}）",
        "todo_id": tcur.lastrowid,
        "next_step": target_row["step_label"],
    }


def merge_todo_meta_updates(meta_raw, updates: dict | None) -> dict:
    try:
        meta = json.loads(meta_raw or "{}")
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    updates = updates or {}
    # 仅允许改业务字段，避免篡改流程控制键
    blocked = {
        "steps",
        "pending_ccs",
        "cc",
        "cc_label",
        "read_only",
        "effect_done",
        "needs_resubmit",
        "reject_from_step",
        "reject_to_step",
        "step_label",
        "owner_id",
    }
    for k, v in updates.items():
        if k in blocked or str(k).startswith("_"):
            continue
        meta[k] = v
    meta.pop("needs_resubmit", None)
    return meta


def _user_label(db, uid) -> str:
    if not uid:
        return "—"
    u = db.execute(
        "SELECT display_name, username FROM users WHERE id = ?", (int(uid),)
    ).fetchone()
    if not u:
        return f"#{uid}"
    return f"{u['display_name'] or u['username']}（{u['username']}）"


def _sys_label(db, sid) -> str:
    if not sid:
        return "—"
    sy = db.execute(
        "SELECT name, code FROM systems WHERE id = ?", (int(sid),)
    ).fetchone()
    if not sy:
        return f"#{sid}"
    return sy["name"] or sy["code"] or f"#{sid}"


def build_apply_form_view(db, meta: dict | None, app=None) -> dict:
    """按申请表单样式组装详情：摘要行 + 明细表（与自助申请面板一致）。"""
    # AI-GEN-BEGIN
    meta = meta if isinstance(meta, dict) else {}
    app = dict(app) if app and not isinstance(app, dict) else (app or {})
    flow = (app.get("flow_code") or "").strip()
    todo_hint = (meta.get("todo_type") or app.get("title") or "").strip()
    uid = meta.get("leuc_user_id") or app.get("applicant_id")
    rows: list[dict] = []
    table = None
    section_title = "申请明细"

    def row(key, label, value, *, editable=False, input_type="text"):
        if value is None or value == "":
            return
        if isinstance(value, bool):
            value = "是" if value else "否"
        rows.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "editable": editable,
                "input_type": input_type,
            }
        )

    # —— 账号延期（与 openAccountExtend 表单一致）——
    if flow in ("account_extend", "account_extend_sensitive") or meta.get("days") is not None:
        section_title = "延期明细"
        row("applicant", "申请人", _user_label(db, uid))
        # 当前有效期
        if uid:
            u = db.execute(
                "SELECT account_expire FROM users WHERE id = ?", (int(uid),)
            ).fetchone()
            row(
                "account_expire",
                "当前有效期",
                (u["account_expire"] if u and u["account_expire"] else "未设置"),
            )
        row(
            "days",
            "延期天数",
            meta.get("days"),
            editable=True,
            input_type="number",
        )
        if meta.get("new_expire"):
            row("new_expire", "延期后有效期", meta.get("new_expire"))
        sens = bool(meta.get("with_sensitive"))
        row(
            "with_sensitive",
            "含敏感权限",
            "是（走直属→一级→财务）" if sens else "否（直属领导）",
            editable=True,
            input_type="bool",
        )
        snap = meta.get("user_permissions") or {}
        accts = snap.get("accounts") if isinstance(snap, dict) else None
        if not accts and uid:
            snap = user_permission_snapshot(db, int(uid))
            accts = snap.get("accounts") or []
        if accts:
            table = {
                "title": "关联业务账号（用于判断是否含敏感）",
                "headers": ["系统", "账号", "登录", "敏感"],
                "rows": [
                    [
                        a.get("system_name") or "—",
                        a.get("account_name") or "—",
                        "可登" if a.get("can_login") else "已关",
                        "敏感" if a.get("has_sensitive") else "—",
                    ]
                    for a in accts
                ],
            }
        return {
            "section_title": section_title,
            "rows": rows,
            "table": table,
            "line_headers": None,
            "lines": None,
        }

    # —— 账号/权限关闭 ——
    if flow in (
        "account_close",
        "sensitive_close",
        "account_close_sensitive",
    ) or meta.get("close_login") or meta.get("close_sensitive") or (
        isinstance(meta.get("items"), list)
        and any(
            isinstance(it, dict) and it.get("close_type") in ("account", "perm")
            for it in (meta.get("items") or [])
        )
    ):
        # AI-GEN-BEGIN
        section_title = "关闭明细"
        raw_items = meta.get("items") or meta.get("lines") or []
        if not isinstance(raw_items, list):
            raw_items = []
        lines = []
        if raw_items:
            for i, it in enumerate(raw_items, start=1):
                if not isinstance(it, dict):
                    continue
                person = it.get("display_name") or _user_label(
                    db, it.get("leuc_user_id") or uid
                )
                if it.get("username") and "（" not in str(person):
                    person = f"{person}（{it['username']}）"
                sys_name = it.get("system_name") or _sys_label(db, it.get("system_id"))
                acct = it.get("account_name") or (
                    f"账号#{it['account_id']}" if it.get("account_id") else "—"
                )
                close_type = (it.get("close_type") or "").strip()
                if close_type == "account" or (
                    not close_type and it.get("close_login") and not it.get("perm_ids")
                ):
                    type_txt = "关闭账号"
                    perm_txt = "—"
                    sens = "—"
                else:
                    type_txt = "关闭权限"
                    perms = it.get("perm_names") or []
                    if isinstance(perms, str):
                        perms = [perms]
                    perm_txt = "、".join(str(x) for x in perms if x) or "—"
                    sens = "是" if it.get("close_sensitive") else "否"
                lines.append([str(i), person, sys_name, acct, type_txt, perm_txt, sens])
        if not lines:
            # 兼容旧单行 meta
            sys_name = meta.get("system_name") or _sys_label(
                db, meta.get("system_id") or app.get("system_id")
            )
            acct_name = meta.get("account_name") or "—"
            if meta.get("close_login") and not meta.get("close_sensitive"):
                type_txt, perm_txt, sens = "关闭账号", "—", "—"
            elif meta.get("close_sensitive"):
                type_txt, perm_txt, sens = "关闭权限", "—", "是"
            else:
                type_txt, perm_txt, sens = "关闭权限", (meta.get("remark") or "—"), "否"
            lines = [
                [
                    "1",
                    _user_label(db, uid),
                    sys_name,
                    acct_name,
                    type_txt,
                    perm_txt,
                    sens,
                ]
            ]
        table = {
            "title": f"关闭明细（共 {len(lines)} 行）",
            "headers": [
                "#",
                "人员",
                "业务系统",
                "业务系统账号",
                "关闭类型",
                "权限",
                "敏感权限",
            ],
            "rows": lines,
        }
        row("applicant", "申请人", _user_label(db, uid))
        row("line_count", "明细行数", str(len(lines)))
        if meta.get("remark"):
            row("remark", "备注", meta.get("remark"), editable=True, input_type="textarea")
        return {
            "section_title": section_title,
            "rows": rows,
            "table": table,
            "line_headers": None,
            "lines": None,
        }
        # AI-GEN-END

    # —— 账号、权限申请 ——
    if flow in (
        "account_apply",
        "account_apply_sensitive",
        "sensitive",
    ) or meta.get("create_new") is not None or meta.get("system_ids") or meta.get("items"):
        section_title = "申请明细"
        # 优先用落库的明细行 items（多行完整保留）；否则回退 system_ids
        raw_items = meta.get("items") or meta.get("lines") or []
        if not isinstance(raw_items, list):
            raw_items = []
        lines = []
        if raw_items:
            for i, it in enumerate(raw_items, start=1):
                if not isinstance(it, dict):
                    continue
                person = it.get("display_name") or _user_label(
                    db, it.get("leuc_user_id") or uid
                )
                if it.get("username") and "（" not in str(person):
                    person = f"{person}（{it['username']}）"
                sys_name = it.get("system_name") or _sys_label(db, it.get("system_id"))
                if it.get("create_new"):
                    acct = "新建账号"
                else:
                    acct = it.get("account_name") or (
                        f"账号#{it['account_id']}" if it.get("account_id") else "已有账号"
                    )
                perms = it.get("perm_names") or []
                if isinstance(perms, str):
                    perms = [perms]
                perm_txt = "、".join(str(x) for x in perms if x) or "—"
                sens = "是" if it.get("with_sensitive") else "否"
                lines.append([str(i), person, sys_name, acct, perm_txt, sens])
        if not lines:
            sids = meta.get("system_ids") or []
            if not isinstance(sids, list):
                sids = [sids] if sids else []
            if not sids and (meta.get("system_id") or app.get("system_id")):
                sids = [meta.get("system_id") or app.get("system_id")]
            create_new = bool(meta.get("create_new"))
            with_sens = bool(meta.get("with_sensitive") or meta.get("sensitive_flag"))
            for i, sid in enumerate(sids or [None], start=1):
                lines.append(
                    [
                        str(i),
                        _user_label(db, uid),
                        _sys_label(db, sid) if sid else "—",
                        "新建账号" if create_new else (meta.get("account_name") or "已有账号"),
                        "—",
                        "是" if with_sens else "否",
                    ]
                )
        if not lines:
            lines = [
                [
                    "1",
                    _user_label(db, uid),
                    _sys_label(db, app.get("system_id")),
                    "—",
                    "—",
                    "否",
                ]
            ]
        table = {
            "title": f"申请明细（共 {len(lines)} 行）",
            "headers": ["#", "人员", "业务系统", "业务系统账号", "权限", "敏感权限"],
            "rows": lines,
        }
        # 摘要只保留申请人；具体明细看表格（避免多行被压成一行摘要）
        row("applicant", "申请人", _user_label(db, uid))
        row("line_count", "明细行数", str(len(lines)))
        if meta.get("remark"):
            row("remark", "备注", meta.get("remark"), editable=True, input_type="textarea")
        return {
            "section_title": section_title,
            "rows": rows,
            "table": table,
            "line_headers": None,
            "lines": None,
        }

    # —— 其它申请：通用按行明细 ——
    section_title = "申请明细"
    row("applicant", "申请人", _user_label(db, uid or app.get("applicant_id")))
    if app.get("title"):
        row("title", "申请事项", app.get("title"))
    if meta.get("system_name") or app.get("system_id") or meta.get("system_id"):
        row(
            "system",
            "业务系统",
            meta.get("system_name")
            or _sys_label(db, meta.get("system_id") or app.get("system_id")),
        )
    if meta.get("account_name"):
        row("account_name", "系统账号", meta.get("account_name"), editable=True)
    if meta.get("applicant_name"):
        row("applicant_name", "姓名", meta.get("applicant_name"), editable=True)
    if meta.get("phone"):
        row("phone", "手机", meta.get("phone"), editable=True)
    if meta.get("email"):
        row("email", "邮箱", meta.get("email"), editable=True)
    if meta.get("remark") or meta.get("reason") or meta.get("comment"):
        row(
            "remark",
            "说明",
            meta.get("remark") or meta.get("reason") or meta.get("comment"),
            editable=True,
            input_type="textarea",
        )
    # 兜底：若几乎无内容，给一行提示
    if len(rows) <= 1 and app.get("title"):
        row("title", "申请事项", app.get("title"))
    return {
        "section_title": section_title,
        "rows": rows,
        "table": table,
        "line_headers": None,
        "lines": None,
    }
    # AI-GEN-END


def build_apply_form_fields(db, meta: dict | None, app=None) -> list[dict]:
    """兼容旧调用：返回摘要行字段。"""
    view = build_apply_form_view(db, meta, app)
    return list(view.get("rows") or [])


def editable_form_keys(form_fields: list[dict]) -> list[str]:
    return [f["key"] for f in (form_fields or []) if f.get("editable")]


def group_bind_items_by_owner(db, items, list_system_owner_ids_fn):
    """同一申请人 + 同一系统管理员合并；任一含敏感则整单走敏感链。"""
    groups: dict[tuple, dict] = {}
    for it in items or []:
        uid = int(it.get("leuc_user_id") or 0)
        sid = int(it.get("system_id") or 0)
        if not uid or not sid:
            continue
        owners = list_system_owner_ids_fn(db, sid) or []
        owner_key = int(owners[0]) if owners else 0
        key = (uid, owner_key)
        g = groups.setdefault(
            key,
            {
                "leuc_user_id": uid,
                "owner_id": owner_key,
                "system_ids": [],
                "with_sensitive": False,
                "items": [],
            },
        )
        if sid not in g["system_ids"]:
            g["system_ids"].append(sid)
        g["with_sensitive"] = g["with_sensitive"] or bool(it.get("with_sensitive"))
        g["items"].append(it)
    return list(groups.values())
# AI-GEN-END
