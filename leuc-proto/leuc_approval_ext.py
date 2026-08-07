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
    """驳回到指定节点：目标节点重开，中间节点重置为 waiting。"""
    steps = db.execute(
        """SELECT * FROM application_steps
        WHERE application_id = ? ORDER BY step_order""",
        (app_id,),
    ).fetchall()
    if not steps:
        return {"ok": False, "error": "无审批步骤"}
    orders = {int(s["step_order"]) for s in steps}
    target = int(reject_to_step)
    if target not in orders or target >= int(current_step_order):
        return {"ok": False, "error": "驳回目标节点无效"}
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    # 当前步标记驳回
    db.execute(
        """UPDATE application_steps SET status = 'rejected', decided_at = ?, remark = ?
        WHERE application_id = ? AND step_order = ?""",
        (now, (remark or "").strip() or None, app_id, current_step_order),
    )
    db.execute(
        "UPDATE todos SET bucket = 'done', status = 'rejected', remark = ? WHERE id = ?",
        ((remark or "").strip() or None, todo_row["id"]),
    )
    # 中间步骤重置
    for s in steps:
        so = int(s["step_order"])
        if target < so < int(current_step_order):
            db.execute(
                """UPDATE application_steps
                SET status = 'waiting', todo_id = NULL, decided_at = NULL
                WHERE id = ?""",
                (s["id"],),
            )
    target_row = db.execute(
        """SELECT * FROM application_steps
        WHERE application_id = ? AND step_order = ?""",
        (app_id, target),
    ).fetchone()
    meta = todo_row["meta"]
    tcur = db.execute(
        """INSERT INTO todos
        (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
         application_id, step_order, meta)
        VALUES (?,?,?,?, 'pending', 'open', ?, ?, ?, ?)""",
        (
            target_row["assignee_id"],
            app["applicant_id"],
            f"{app['title']} · {target_row['step_label']}（驳回重办）",
            todo_row["todo_type"],
            now,
            app_id,
            target,
            meta,
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
        (target, now, target, current_step_order, app_id),
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
