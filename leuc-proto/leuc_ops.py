# AI-GEN-BEGIN
"""LEUC 运维能力：随机密码、发信落库、定时任务、手机号解析。"""
from __future__ import annotations

import json
import random
import secrets
import string
import threading
import time
from datetime import datetime, timedelta
from typing import Any

# 特殊符号集合（发信密码）
_PASSWORD_SPECIAL = "!@#$%^&*_-+=?"


def extract_leorg_phone(emp: dict[str, Any]) -> str | None:
    """LeOrg 人员手机号：优先 mobilePhone，其次 mobile / phone。"""
    for key in ("mobilePhone", "mobile_phone", "mobile", "phone", "Phone", "MobilePhone"):
        v = emp.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if not s or "*" in s:
            continue
        return s
    return None


def gen_account_password(min_len: int = 8, max_len: int = 12) -> str:
    """8–12 位随机密码：至少各含 1 个大写、小写、数字、特殊符号。"""
    length = random.randint(min_len, max_len)
    upper = secrets.choice(string.ascii_uppercase)
    lower = secrets.choice(string.ascii_lowercase)
    digit = secrets.choice(string.digits)
    special = secrets.choice(_PASSWORD_SPECIAL)
    pool = string.ascii_letters + string.digits + _PASSWORD_SPECIAL
    rest = [secrets.choice(pool) for _ in range(length - 4)]
    chars = [upper, lower, digit, special, *rest]
    random.SystemRandom().shuffle(chars)
    return "".join(chars)


def record_credential_notify(
    db,
    *,
    user_id: int,
    username: str,
    password: str,
    phone: str | None,
    email: str | None,
    reason: str = "account_created",
) -> dict[str, Any]:
    """创建账号后写发送记录（不真实发送）；优先手机。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    channel = "phone" if phone else ("email" if email else "none")
    target = phone or email or ""
    status = "recorded" if target else "skipped"
    body = (
        f"【LEUC】账号已创建\n登录名：{username}\n初始密码：{password}\n"
        f"请尽快登录并修改密码。"
    )
    title = "账号开通通知"
    cur = db.execute(
        """INSERT INTO notify_send_records
        (user_id, channel, target, title, body, status, reason, meta_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            channel,
            target,
            title,
            body,
            status,
            reason,
            json.dumps(
                {"username": username, "has_password": True},
                ensure_ascii=False,
            ),
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "channel": channel,
        "target": target,
        "status": status,
        "password": password,
    }


def ensure_ops_tables(conn) -> None:
    """任务表、发信记录表、部门内置标记。"""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_tasks (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          interval_hours REAL NOT NULL DEFAULT 6,
          last_run_at TEXT,
          next_run_at TEXT,
          last_status TEXT,
          last_message TEXT,
          config_json TEXT,
          updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notify_send_records (
          id INTEGER PRIMARY KEY,
          user_id INTEGER,
          channel TEXT NOT NULL,
          target TEXT,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'recorded',
          reason TEXT,
          meta_json TEXT,
          created_at TEXT NOT NULL,
          sent_at TEXT
        )"""
    )
    # AI-GEN-BEGIN
    conn.execute(
        """CREATE TABLE IF NOT EXISTS task_run_logs (
          id INTEGER PRIMARY KEY,
          task_code TEXT NOT NULL,
          trigger_type TEXT NOT NULL,
          actor_user_id INTEGER,
          status TEXT NOT NULL,
          message TEXT,
          summary_json TEXT,
          started_at TEXT NOT NULL,
          finished_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY,
          actor_user_id INTEGER,
          actor_name TEXT,
          action TEXT NOT NULL,
          target_type TEXT,
          target_id TEXT,
          detail_json TEXT,
          ip TEXT,
          created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sync_change_logs (
          id INTEGER PRIMARY KEY,
          run_id INTEGER,
          entity_type TEXT NOT NULL,
          change_type TEXT NOT NULL,
          entity_key TEXT,
          entity_name TEXT,
          detail_json TEXT,
          created_at TEXT NOT NULL
        )"""
    )
    # AI-GEN-END
    dept_cols = [r[1] for r in conn.execute("PRAGMA table_info(departments)").fetchall()]
    if dept_cols and "is_builtin" not in dept_cols:
        conn.execute(
            "ALTER TABLE departments ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0"
        )
    if dept_cols and "dept_code" not in dept_cols:
        conn.execute("ALTER TABLE departments ADD COLUMN dept_code TEXT")
    step_cols = [r[1] for r in conn.execute("PRAGMA table_info(application_steps)").fetchall()]
    if step_cols and "step_kind" not in step_cols:
        conn.execute(
            "ALTER TABLE application_steps ADD COLUMN step_kind TEXT NOT NULL DEFAULT 'approve'"
        )
    if step_cols and "parallel_group" not in step_cols:
        conn.execute("ALTER TABLE application_steps ADD COLUMN parallel_group TEXT")
    app_cols = [r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
    if app_cols and "reject_to_step" not in app_cols:
        conn.execute("ALTER TABLE applications ADD COLUMN reject_to_step INTEGER")
    if app_cols and "reject_from_step" not in app_cols:
        conn.execute("ALTER TABLE applications ADD COLUMN reject_from_step INTEGER")
    # 种子：LeOrg 自动同步任务
    row = conn.execute(
        "SELECT id FROM scheduled_tasks WHERE code = 'leorg_sync'"
    ).fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not row:
        next_at = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO scheduled_tasks
            (code, name, enabled, interval_hours, next_run_at, last_status, config_json, updated_at)
            VALUES ('leorg_sync', '同步部门和人员', 1, 6, ?, 'idle', '{}', ?)""",
            (next_at, now),
        )
    # 外部人员部门（与公司平级、不可删）
    ext = conn.execute(
        "SELECT id FROM departments WHERE dept_code = 'external' LIMIT 1"
    ).fetchone()
    if not ext:
        by_name = conn.execute(
            "SELECT id FROM departments WHERE name = '外部人员' LIMIT 1"
        ).fetchone()
        if by_name:
            conn.execute(
                """UPDATE departments SET dept_code = 'external', is_builtin = 1,
                   parent_id = NULL WHERE id = ?""",
                (by_name["id"],),
            )
        else:
            conn.execute(
                """INSERT INTO departments
                (name, parent_id, owner_user_id, leorg_id, manager_leorg_emp_id, sort_order,
                 is_builtin, dept_code)
                VALUES ('外部人员', NULL, NULL, NULL, NULL, 9990, 1, 'external')"""
            )


def get_external_dept_id(db) -> int | None:
    row = db.execute(
        "SELECT id FROM departments WHERE dept_code = 'external' LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def list_scheduled_tasks(db) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM scheduled_tasks ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def update_scheduled_task(db, code: str, *, interval_hours=None, enabled=None) -> dict | None:
    row = db.execute(
        "SELECT * FROM scheduled_tasks WHERE code = ?", (code,)
    ).fetchone()
    if not row:
        return None
    now = datetime.now()
    iv = float(interval_hours) if interval_hours is not None else float(row["interval_hours"])
    iv = max(0.25, min(iv, 168))  # 15 分钟 ~ 7 天
    en = int(enabled) if enabled is not None else int(row["enabled"] or 0)
    next_at = (now + timedelta(hours=iv)).strftime("%Y-%m-%d %H:%M:%S") if en else None
    db.execute(
        """UPDATE scheduled_tasks
        SET interval_hours = ?, enabled = ?, next_run_at = ?, updated_at = ?
        WHERE code = ?""",
        (iv, en, next_at, now.strftime("%Y-%m-%d %H:%M:%S"), code),
    )
    return dict(
        db.execute("SELECT * FROM scheduled_tasks WHERE code = ?", (code,)).fetchone()
    )


# AI-GEN-BEGIN
def write_audit_log(
    db,
    *,
    action: str,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: Any = None,
    ip: str | None = None,
) -> int | None:
    """写操作/审计日志。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail_json = None
    if detail is not None:
        detail_json = (
            detail
            if isinstance(detail, str)
            else json.dumps(detail, ensure_ascii=False, default=str)
        )
    cur = db.execute(
        """INSERT INTO audit_logs
        (actor_user_id, actor_name, action, target_type, target_id, detail_json, ip, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            actor_user_id,
            actor_name,
            action,
            target_type,
            str(target_id) if target_id is not None else None,
            detail_json,
            ip,
            now,
        ),
    )
    return cur.lastrowid


def begin_task_run(
    db,
    *,
    task_code: str,
    trigger_type: str,
    actor_user_id: int | None = None,
) -> int:
    """创建执行中任务记录，返回 run_id。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute(
        """INSERT INTO task_run_logs
        (task_code, trigger_type, actor_user_id, status, message, summary_json, started_at, finished_at)
        VALUES (?,?,?, 'running', NULL, NULL, ?, NULL)""",
        (task_code, trigger_type, actor_user_id, now),
    )
    if cur.lastrowid is None:
        raise RuntimeError("创建任务执行记录失败")
    return int(cur.lastrowid)


def finish_task_run(
    db,
    run_id: int,
    *,
    status: str,
    message: str | None = None,
    summary: Any = None,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary_json = None
    if summary is not None:
        summary_json = (
            summary
            if isinstance(summary, str)
            else json.dumps(summary, ensure_ascii=False, default=str)
        )
    db.execute(
        """UPDATE task_run_logs
        SET status=?, message=?, summary_json=?, finished_at=?
        WHERE id=?""",
        (status, (message or "")[:2000], summary_json, now, run_id),
    )


def append_sync_change_logs(db, run_id: int, changes: list[dict[str, Any]]) -> int:
    """批量写入同步变化明细。"""
    if not changes:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for c in changes:
        detail = c.get("detail")
        detail_json = (
            None
            if detail is None
            else (
                detail
                if isinstance(detail, str)
                else json.dumps(detail, ensure_ascii=False, default=str)
            )
        )
        rows.append(
            (
                run_id,
                c.get("entity_type") or "unknown",
                c.get("change_type") or "update",
                str(c.get("entity_key") or ""),
                c.get("entity_name"),
                detail_json,
                now,
            )
        )
    db.executemany(
        """INSERT INTO sync_change_logs
        (run_id, entity_type, change_type, entity_key, entity_name, detail_json, created_at)
        VALUES (?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def list_task_runs(db, task_code: str | None = None, *, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit or 50), 200))
    if task_code:
        rows = db.execute(
            """SELECT * FROM task_run_logs
            WHERE task_code = ? ORDER BY id DESC LIMIT ?""",
            (task_code, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM task_run_logs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("summary_json"):
            try:
                d["summary"] = json.loads(d["summary_json"])
            except Exception:
                d["summary"] = None
        out.append(d)
    return out


def list_sync_changes(
    db, run_id: int, *, limit: int = 200, offset: int = 0, entity_type: str | None = None
) -> tuple[list[dict], int]:
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    if entity_type:
        total = db.execute(
            "SELECT COUNT(*) AS c FROM sync_change_logs WHERE run_id=? AND entity_type=?",
            (run_id, entity_type),
        ).fetchone()["c"]
        rows = db.execute(
            """SELECT * FROM sync_change_logs
            WHERE run_id=? AND entity_type=?
            ORDER BY id ASC LIMIT ? OFFSET ?""",
            (run_id, entity_type, limit, offset),
        ).fetchall()
    else:
        total = db.execute(
            "SELECT COUNT(*) AS c FROM sync_change_logs WHERE run_id=?",
            (run_id,),
        ).fetchone()["c"]
        rows = db.execute(
            """SELECT * FROM sync_change_logs
            WHERE run_id=? ORDER BY id ASC LIMIT ? OFFSET ?""",
            (run_id, limit, offset),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail_json"):
            try:
                d["detail"] = json.loads(d["detail_json"])
            except Exception:
                d["detail"] = d["detail_json"]
        out.append(d)
    return out, int(total)


def list_audit_logs(
    db, *, limit: int = 100, action: str | None = None
) -> list[dict]:
    limit = max(1, min(int(limit or 100), 300))
    if action:
        rows = db.execute(
            """SELECT * FROM audit_logs WHERE action = ?
            ORDER BY id DESC LIMIT ?""",
            (action, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail_json"):
            try:
                d["detail"] = json.loads(d["detail_json"])
            except Exception:
                d["detail"] = d["detail_json"]
        out.append(d)
    return out


# AI-GEN-END


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_task_scheduler(app, run_leorg_sync_job_fn) -> None:
    """后台线程：按 scheduled_tasks 触发 LeOrg 同步（带执行记录）。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        while True:
            try:
                with app.app_context():
                    from db import connect

                    conn = connect()
                    try:
                        ensure_ops_tables(conn)
                        now = datetime.now()
                        tasks = conn.execute(
                            """SELECT * FROM scheduled_tasks
                            WHERE enabled = 1 AND next_run_at IS NOT NULL
                              AND next_run_at <= ?""",
                            (now.strftime("%Y-%m-%d %H:%M:%S"),),
                        ).fetchall()
                        for t in tasks:
                            if t["code"] != "leorg_sync":
                                continue
                            # AI-GEN-BEGIN
                            result = run_leorg_sync_job_fn(
                                conn,
                                trigger_type="schedule",
                                actor_user_id=None,
                                actor_name="scheduler",
                                ip=None,
                            )
                            status = result.get("status") or "error"
                            msg = result.get("message") or ""
                            # AI-GEN-END
                            iv = float(t["interval_hours"] or 6)
                            next_at = (datetime.now() + timedelta(hours=iv)).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                            conn.execute(
                                """UPDATE scheduled_tasks
                                SET last_run_at = ?, next_run_at = ?, last_status = ?,
                                    last_message = ?, updated_at = ?
                                WHERE id = ?""",
                                (
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    next_at,
                                    status,
                                    (msg or "")[:500],
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    t["id"],
                                ),
                            )
                            conn.commit()
                    finally:
                        conn.close()
            except Exception:
                pass
            time.sleep(30)

    th = threading.Thread(target=_loop, name="leuc-scheduler", daemon=True)
    th.start()
# AI-GEN-END
