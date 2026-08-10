# AI-GEN-BEGIN
"""一键关账 / 恢复：代关他人、凭证附件、有接口直关、无接口管理员待办。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path


def ensure_bulk_close_tables(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bulk_close_records (
          id INTEGER PRIMARY KEY,
          target_user_id INTEGER NOT NULL,
          operator_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'processing',
          source TEXT NOT NULL DEFAULT 'proxy_bulk',
          reason TEXT,
          credential_note TEXT,
          effective_at TEXT,
          application_id INTEGER,
          created_at TEXT NOT NULL,
          finished_at TEXT,
          summary TEXT,
          detail_json TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bulk_close_items (
          id INTEGER PRIMARY KEY,
          record_id INTEGER NOT NULL,
          system_id INTEGER,
          system_code TEXT,
          system_name TEXT,
          account_id INTEGER,
          pool_account_id INTEGER,
          account_name TEXT,
          exec_mode TEXT NOT NULL,
          exec_status TEXT NOT NULL,
          todo_id INTEGER,
          local_status TEXT,
          remote_http_status INTEGER,
          remote_message TEXT,
          closed_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bulk_close_files (
          id INTEGER PRIMARY KEY,
          record_id INTEGER NOT NULL,
          kind TEXT NOT NULL DEFAULT 'attachment',
          file_name TEXT NOT NULL,
          content_type TEXT,
          size INTEGER,
          storage_path TEXT NOT NULL,
          created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bulk_close_events (
          id INTEGER PRIMARY KEY,
          record_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          actor_user_id INTEGER,
          message TEXT,
          detail_json TEXT,
          created_at TEXT NOT NULL
        )"""
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_bulk_event(conn, record_id, event_type, *, actor_user_id=None, message=None, detail=None):
    conn.execute(
        """INSERT INTO bulk_close_events
        (record_id, event_type, actor_user_id, message, detail_json, created_at)
        VALUES (?,?,?,?,?,?)""",
        (
            int(record_id),
            event_type,
            actor_user_id,
            message,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None,
            _now(),
        ),
    )


def uploads_dir(base_dir: str | Path) -> Path:
    p = Path(base_dir) / "uploads" / "bulk_close"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_bulk_files(conn, record_id, files, *, base_dir: str | Path, kind_default="attachment"):
    """files: list of werkzeug FileStorage or dict {filename, content_type, data:bytes, kind}."""
    out = []
    root = uploads_dir(base_dir)
    for f in files or []:
        if hasattr(f, "filename"):
            name = (f.filename or "file").strip() or "file"
            ctype = getattr(f, "content_type", None) or "application/octet-stream"
            data = f.read()
            kind = kind_default
        else:
            name = (f.get("filename") or "file").strip() or "file"
            ctype = f.get("content_type") or "application/octet-stream"
            data = f.get("data") or b""
            kind = f.get("kind") or kind_default
        if not data:
            continue
        safe = f"{record_id}_{uuid.uuid4().hex[:10]}_{os.path.basename(name)}"
        path = root / safe
        path.write_bytes(data)
        rel = str(path.relative_to(Path(base_dir))) if Path(base_dir) in path.parents else str(path)
        cur = conn.execute(
            """INSERT INTO bulk_close_files
            (record_id, kind, file_name, content_type, size, storage_path, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (int(record_id), kind, name, ctype, len(data), rel, _now()),
        )
        out.append({"id": cur.lastrowid, "file_name": name, "kind": kind, "size": len(data)})
    return out


def list_bulk_close_records(conn, *, operator_id=None, target_user_id=None, limit=50):
    sql = "SELECT * FROM bulk_close_records WHERE 1=1"
    params = []
    if operator_id is not None:
        sql += " AND operator_id = ?"
        params.append(int(operator_id))
    if target_user_id is not None:
        sql += " AND target_user_id = ?"
        params.append(int(target_user_id))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_bulk_close_detail(conn, record_id: int) -> dict | None:
    rec = conn.execute(
        "SELECT * FROM bulk_close_records WHERE id = ?", (int(record_id),)
    ).fetchone()
    if not rec:
        return None
    items = conn.execute(
        "SELECT * FROM bulk_close_items WHERE record_id = ? ORDER BY id",
        (int(record_id),),
    ).fetchall()
    files = conn.execute(
        "SELECT id, kind, file_name, content_type, size, created_at FROM bulk_close_files WHERE record_id = ?",
        (int(record_id),),
    ).fetchall()
    events = conn.execute(
        "SELECT * FROM bulk_close_events WHERE record_id = ? ORDER BY id",
        (int(record_id),),
    ).fetchall()
    return {
        "record": dict(rec),
        "items": [dict(x) for x in items],
        "files": [dict(x) for x in files],
        "events": [dict(x) for x in events],
    }


def system_has_close_api(system_row) -> bool:
    """有 close_api_url 视为可接口直关；空则走管理员待办。"""
    try:
        url = (system_row["close_api_url"] or "").strip()
    except Exception:
        url = ""
    return bool(url)
# AI-GEN-END
