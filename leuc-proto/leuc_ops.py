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


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_task_scheduler(app, run_leorg_sync_fn) -> None:
    """后台线程：按 scheduled_tasks 触发 LeOrg 同步。"""
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
                            try:
                                msg = run_leorg_sync_fn(conn)
                                status = "ok"
                            except Exception as e:  # noqa: BLE001
                                # AI-GEN-BEGIN
                                msg = f"{type(e).__name__}: {e}"
                                status = "error"
                                try:
                                    conn.rollback()
                                except Exception:
                                    pass
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
