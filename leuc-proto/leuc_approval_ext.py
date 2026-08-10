# AI-GEN-BEGIN
"""审批流扩展：申请人确认、知会、驳回指定节点、合并系统管理员。"""
from __future__ import annotations

import json
import re
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
    # AI-GEN-BEGIN
    now = now or datetime.now().strftime("%Y-%m-%d")
    created = []
    for cc in ccs or []:
        tcur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
             application_id, step_order, meta)
            VALUES (?,?,?,?, 'pending', 'unread', ?, ?, NULL, ?)""",
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
    # AI-GEN-END


def normalize_cc_status(status: str | None, bucket: str | None = None) -> str:
    """知会状态归一：unread/read（兼容旧 open/approved/rejected）。"""
    # AI-GEN-BEGIN
    st = (status or "").strip().lower()
    if st in ("read", "approved", "done"):
        return "read"
    if st in ("unread", "open", "pending", ""):
        if bucket == "done" and st in ("",):
            return "read"
        return "unread"
    if bucket == "done":
        return "read"
    return "unread"
    # AI-GEN-END


def cc_status_label(cc_status: str) -> str:
    # AI-GEN-BEGIN
    return "已阅" if cc_status == "read" else "待阅"
    # AI-GEN-END


def build_cc_dimension(db, app_id, user_brief_fn=None):
    """申请单知会维度：不入主审批链，独立待阅/已阅。"""
    # AI-GEN-BEGIN
    if not app_id:
        return {"items": [], "summary": "无知会", "read_count": 0, "total": 0}
    # 不用 LIKE '%…%'：psycopg 会把 % 当成占位符导致 500
    rows = db.execute(
        """SELECT * FROM todos
        WHERE application_id = ?
        ORDER BY id""",
        (int(app_id),),
    ).fetchall()
    items = []
    read_count = 0
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except Exception:
            meta = {}
        if not (meta.get("cc") or r["todo_type"] == "知会确认"):
            continue
        cc_st = normalize_cc_status(r["status"], r["bucket"])
        if cc_st == "read":
            read_count += 1
        assignee = None
        if user_brief_fn:
            assignee = user_brief_fn(db, r["assignee_id"])
        else:
            u = db.execute(
                "SELECT id, username, display_name, role FROM users WHERE id = ?",
                (r["assignee_id"],),
            ).fetchone()
            assignee = dict(u) if u else {"id": r["assignee_id"]}
        items.append(
            {
                "todo_id": r["id"],
                "assignee": assignee,
                "assignee_id": r["assignee_id"],
                "cc_label": meta.get("cc_label") or "知会确认",
                "cc_status": cc_st,
                "status_label": cc_status_label(cc_st),
                "read_at": meta.get("read_at"),
                "remark": (r["remark"] if "remark" in r.keys() else None) or None,
                "created_at": r["created_at"],
            }
        )
    total = len(items)
    if total == 0:
        summary = "无知会"
    else:
        summary = f"已阅 {read_count}/{total}"
    return {
        "items": items,
        "summary": summary,
        "read_count": read_count,
        "total": total,
    }
    # AI-GEN-END


def expand_account_permissions(db, system_id, perm_summary, has_sensitive=False) -> list[dict]:
    """把账号 perm_summary 展开为权限项列表（含敏感标识）。"""
    # AI-GEN-BEGIN
    defs = db.execute(
        """SELECT id, perm_code, perm_name, parent_id, is_sensitive, enabled
        FROM sensitive_perm_defs
        WHERE system_id = ? AND enabled = 1
        ORDER BY id""",
        (int(system_id),),
    ).fetchall()
    catalog = [dict(d) for d in defs]
    summary = (perm_summary or "").strip()
    if not catalog:
        if not summary:
            return []
        return [
            {
                "id": None,
                "perm_code": "",
                "perm_name": summary,
                "is_sensitive": 1 if has_sensitive else 0,
                "matched": True,
            }
        ]
    # 全部 / 全部·敏感 → 目录全项
    if ("全部" in summary) or summary in ("", "敏感权限"):
        out = []
        for d in catalog:
            item = dict(d)
            item["matched"] = True
            if "敏感" in summary or has_sensitive:
                # 保留目录自身敏感标记；账号级敏感时仍展示全部
                pass
            out.append(item)
        if summary == "敏感权限":
            out = [x for x in out if x.get("is_sensitive")] or out
        return out
    # 按分隔符拆名称匹配
    parts = [
        p.strip()
        for p in re.split(r"[·,/、;；|]+", summary)
        if p and p.strip() and p.strip() not in ("敏感", "普通开通", "普通权限")
    ]
    if not parts:
        return [
            {
                "id": None,
                "perm_code": "",
                "perm_name": summary or ("敏感权限" if has_sensitive else "普通权限"),
                "is_sensitive": 1 if has_sensitive else 0,
                "matched": True,
            }
        ]
    out = []
    for d in catalog:
        if d["perm_name"] in parts or d["perm_code"] in parts:
            item = dict(d)
            item["matched"] = True
            out.append(item)
    if not out:
        for p in parts:
            out.append(
                {
                    "id": None,
                    "perm_code": "",
                    "perm_name": p,
                    "is_sensitive": 1 if has_sensitive else 0,
                    "matched": True,
                }
            )
    return out
    # AI-GEN-END


def user_permission_snapshot(db, user_id: int) -> dict[str, Any]:
    """延期审批详情：用户权限快照（突出敏感）。"""
    # AI-GEN-BEGIN
    rows = db.execute(
        """SELECT a.id, a.account_name, a.account_label, a.can_login, a.has_sensitive,
                  a.perm_summary, s.id AS system_id, s.code AS system_code, s.name AS system_name
           FROM user_system_accounts a
           JOIN systems s ON s.id = a.system_id
           WHERE a.user_id = ?
           ORDER BY a.has_sensitive DESC, s.id, a.id""",
        (int(user_id),),
    ).fetchall()
    accounts = []
    for r in rows:
        item = dict(r)
        item["permissions"] = expand_account_permissions(
            db, r["system_id"], r["perm_summary"], bool(r["has_sensitive"])
        )
        accounts.append(item)
    sensitive = [a for a in accounts if a.get("has_sensitive")]
    return {
        "accounts": accounts,
        "sensitive_accounts": sensitive,
        "sensitive_count": len(sensitive),
        "account_count": len(accounts),
    }
    # AI-GEN-END


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
        append_flow_event(
            db,
            app_id,
            "rejected",
            step_order=cur_order,
            step_key="reject",
            step_label="驳回至申请人",
            actor_user_id=todo_row["assignee_id"],
            assignee_id=assignee_id,
            remark=note,
            detail={"reject_to_step": 0, "reject_to_label": "申请人"},
            now=now,
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
    append_flow_event(
        db,
        app_id,
        "rejected",
        step_order=cur_order,
        step_key="reject",
        step_label=f"驳回至{target_row['step_label']}",
        actor_user_id=todo_row["assignee_id"],
        assignee_id=target_row["assignee_id"],
        remark=note,
        detail={
            "reject_to_step": target,
            "reject_to_label": target_row["step_label"],
        },
        now=now,
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
    append_flow_event(
        db,
        app_id,
        "resubmitted",
        step_order=int(cur_order or 0),
        step_key="resubmit",
        step_label="再次提交",
        actor_user_id=todo_row["assignee_id"],
        assignee_id=target_row["assignee_id"],
        remark=note or "修改后重提",
        detail={
            "jump_to_step": target,
            "jump_to_label": target_row["step_label"],
        },
        now=now,
    )
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
        # 当前有效期至
        if uid:
            u = db.execute(
                "SELECT account_expire FROM users WHERE id = ?", (int(uid),)
            ).fetchone()
            row(
                "account_expire",
                "当前有效期至",
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
        if not isinstance(snap, dict):
            snap = {"accounts": accts or []}
        if accts:
            table = {
                "title": "本人全部账号与权限",
                "headers": ["系统", "账号", "登录", "敏感", "权限"],
                "rows": [
                    [
                        a.get("system_name") or "—",
                        a.get("account_name") or "—",
                        "可登" if a.get("can_login") else "已关",
                        "敏感" if a.get("has_sensitive") else "—",
                        a.get("perm_summary")
                        or "、".join(
                            (p.get("perm_name") or "")
                            for p in (a.get("permissions") or [])
                            if p.get("perm_name")
                        )
                        or "—",
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
            # 供详情页「全项详情」按钮
            "user_permissions": snap if isinstance(snap, dict) else {"accounts": accts or []},
            "show_full_perm_detail": True,
        }

    # AI-GEN-BEGIN
    # —— 新建外部人员（审批通过后落库）——
    if flow == "external_create" or meta.get("external_create"):
        section_title = "新建外部人员"
        row("initiator", "发起人", _user_label(db, meta.get("initiator_id") or uid))
        row("display_name", "姓名", meta.get("display_name"), editable=True)
        row("username", "登录用户名", meta.get("username") or "（通过后生成）")
        row("phone", "手机", meta.get("phone") or "—", editable=True)
        row("email", "邮箱", meta.get("email") or "—", editable=True)
        row("person_type", "人员类型", "外部人员")
        row("dept", "归属部门", meta.get("dept_name") or "外部人员")
        if meta.get("created_user_id"):
            row("created_user", "已创建账号", _user_label(db, meta.get("created_user_id")))
        if meta.get("remark"):
            row("remark", "说明", meta.get("remark"), editable=True, input_type="textarea")
        return {
            "section_title": section_title,
            "rows": rows,
            "table": None,
            "line_headers": None,
            "lines": None,
        }
    # AI-GEN-END

    # AI-GEN-BEGIN
    # —— 本系统角色申请 ——
    if flow == "leuc_roles" or meta.get("roles"):
        section_title = "本系统角色申请"
        row("applicant", "申请人", _user_label(db, uid))
        row("system", "系统", meta.get("system_name") or _sys_label(db, meta.get("system_id")))
        labels = meta.get("role_labels") or meta.get("roles") or []
        if isinstance(labels, list):
            labels = "、".join(str(x) for x in labels if x)
        row("roles", "申请角色", labels or "—")
        if meta.get("granted_roles"):
            granted = meta.get("granted_roles")
            if isinstance(granted, list):
                granted = "、".join(str(x) for x in granted)
            row("granted_roles", "生效后角色", granted)
        return {
            "section_title": section_title,
            "rows": rows,
            "table": None,
            "line_headers": None,
            "lines": None,
        }
    # AI-GEN-END

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


# AI-GEN-BEGIN
def ensure_flow_events_table(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS application_flow_events (
          id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
          application_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          step_order INTEGER,
          step_key TEXT,
          step_label TEXT,
          actor_user_id INTEGER,
          assignee_id INTEGER,
          remark TEXT,
          detail_json TEXT,
          created_at TEXT NOT NULL
        )"""
    )


def append_flow_event(
    db,
    app_id: int,
    event_type: str,
    *,
    step_order: int | None = None,
    step_key: str | None = None,
    step_label: str | None = None,
    actor_user_id: int | None = None,
    assignee_id: int | None = None,
    remark: str | None = None,
    detail: Any = None,
    now: str | None = None,
) -> int | None:
    """追加流程事件（驳回/重提等不可变历史）。"""
    ensure_flow_events_table(db)
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail_json = None
    if detail is not None:
        detail_json = (
            detail
            if isinstance(detail, str)
            else json.dumps(detail, ensure_ascii=False, default=str)
        )
    cur = db.execute(
        """INSERT INTO application_flow_events
        (application_id, event_type, step_order, step_key, step_label,
         actor_user_id, assignee_id, remark, detail_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            int(app_id),
            event_type,
            step_order,
            step_key,
            step_label,
            actor_user_id,
            assignee_id,
            (remark or None),
            detail_json,
            now,
        ),
    )
    return int(cur.lastrowid) if cur.lastrowid is not None else None


def list_flow_events(db, app_id: int) -> list[dict]:
    ensure_flow_events_table(db)
    rows = db.execute(
        """SELECT * FROM application_flow_events
        WHERE application_id = ? ORDER BY id""",
        (int(app_id),),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail_json"):
            try:
                d["detail"] = json.loads(d["detail_json"])
            except Exception:
                d["detail"] = d["detail_json"]
        else:
            d["detail"] = None
        out.append(d)
    return out


def cancel_application_flow(
    db,
    *,
    app_id: int,
    actor_user_id: int,
    remark: str | None = None,
    now: str | None = None,
) -> dict:
    """撤销未结束的申请（pending / returned）。"""
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    app = db.execute("SELECT * FROM applications WHERE id = ?", (int(app_id),)).fetchone()
    if not app:
        return {"ok": False, "error": "申请不存在"}
    st = (app["status"] or "").strip()
    if st not in ("pending", "returned"):
        return {"ok": False, "error": "申请已结束，无法撤销"}
    note = (remark or "").strip() or "申请人撤销"
    # 关闭未完成待办
    db.execute(
        """UPDATE todos SET bucket = 'done', status = 'cancelled', remark = ?
        WHERE application_id = ? AND bucket = 'pending'
          AND status IN ('open', 'unread')""",
        (note, int(app_id)),
    )
    db.execute(
        """UPDATE todos SET status = 'cancelled', remark = ?
        WHERE application_id = ? AND bucket = 'initiated'""",
        (note, int(app_id)),
    )
    db.execute(
        """UPDATE application_steps SET status = 'cancelled', decided_at = ?, remark = ?
        WHERE application_id = ? AND status IN ('pending', 'waiting')""",
        (now, note, int(app_id)),
    )
    db.execute(
        """UPDATE applications SET status = 'cancelled', updated_at = ?,
            reject_to_step = NULL, reject_from_step = NULL
        WHERE id = ?""",
        (now, int(app_id)),
    )
    append_flow_event(
        db,
        int(app_id),
        "cancelled",
        step_order=None,
        step_key="cancel",
        step_label="撤销申请",
        actor_user_id=int(actor_user_id),
        assignee_id=int(actor_user_id),
        remark=note,
        detail={"by": "applicant_or_initiator"},
        now=now,
    )
    return {"ok": True, "message": "已撤销申请"}
# AI-GEN-END
