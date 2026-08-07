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


def build_apply_form_fields(db, meta: dict | None, app=None) -> list[dict]:
    """把申请 meta 转成详情可读字段列表。"""
    meta = meta if isinstance(meta, dict) else {}
    app = dict(app) if app and not isinstance(app, dict) else (app or {})
    fields: list[dict] = []

    def add(key, label, value, *, editable=False, input_type="text"):
        if value is None or value == "" or value == []:
            return
        if isinstance(value, bool):
            value = "是" if value else "否"
        fields.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "editable": editable,
                "input_type": input_type,
            }
        )

    # 解析关联对象
    subject_name = None
    uid = meta.get("leuc_user_id") or app.get("applicant_id")
    if uid:
        u = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?", (int(uid),)
        ).fetchone()
        if u:
            subject_name = u["display_name"] or u["username"]
            add("leuc_user_id", "申请对象", f"{subject_name}（#{uid}）")

    sid = meta.get("system_id") or app.get("system_id")
    if sid:
        sy = db.execute(
            "SELECT name, code FROM systems WHERE id = ?", (int(sid),)
        ).fetchone()
        if sy:
            add("system_id", "业务系统", f"{sy['name']}（{sy['code']}）")

    sids = meta.get("system_ids") or []
    if isinstance(sids, list) and sids:
        names = []
        for x in sids:
            try:
                sy = db.execute(
                    "SELECT name, code FROM systems WHERE id = ?", (int(x),)
                ).fetchone()
                if sy:
                    names.append(f"{sy['name']}（{sy['code']}）")
                else:
                    names.append(str(x))
            except Exception:
                names.append(str(x))
        add("system_ids", "业务系统列表", "、".join(names))

    labels = {
        "days": ("延期天数", "number", True),
        "with_sensitive": ("含敏感权限", "bool", True),
        "create_new": ("新建账号", "bool", True),
        "remark": ("申请备注", "textarea", True),
        "account_name": ("拟开通账号", "text", True),
        "account_id": ("账号池ID", "number", False),
        "phone": ("手机", "text", True),
        "email": ("邮箱", "text", True),
        "applicant_name": ("姓名", "text", True),
        "applicant_job": ("岗位", "text", True),
        "oa_person_code": ("OA人员编码", "text", False),
        "beisen_user_id": ("北森用户ID", "text", False),
        "new_expire": ("延期后有效期", "text", False),
        "perm_summary": ("权限摘要", "text", True),
        "reason": ("申请原因", "textarea", True),
        "comment": ("说明", "textarea", True),
    }
    shown = {
        "leuc_user_id",
        "system_id",
        "system_ids",
        "steps",
        "step_label",
        "pending_ccs",
        "cc",
        "cc_label",
        "read_only",
        "effect_done",
        "needs_resubmit",
        "reject_from_step",
        "reject_to_step",
        "owner_id",
        "user_permissions",
    }
    for key, (label, itype, editable) in labels.items():
        if key in meta and key not in shown:
            add(key, label, meta.get(key), editable=editable, input_type=itype)
            shown.add(key)
    # 其余未识别字段也展示（便于看全表单）
    for k, v in meta.items():
        if k in shown:
            continue
        if isinstance(v, (dict, list)) and k not in ("items",):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = str(v)
        add(k, k, v, editable=False)
    # 申请单头信息
    if app.get("flow_code"):
        fields.insert(0, {
            "key": "flow_code",
            "label": "流程类型",
            "value": app.get("flow_code"),
            "editable": False,
            "input_type": "text",
        })
    if app.get("title"):
        fields.insert(0, {
            "key": "title",
            "label": "申请标题",
            "value": app.get("title"),
            "editable": False,
            "input_type": "text",
        })
    return fields


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
