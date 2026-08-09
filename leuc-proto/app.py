# AI-GEN-BEGIN
"""Lecoo 用户中心 LEUC · SQLite 多角色交互原型服务。"""
from __future__ import annotations

# AI-GEN-BEGIN
# 直接 `python app.py` 时若落在 Rosetta(x86_64)+用户站 cryptography(arm64)，北森 SSO 会挂。
# 作为入口脚本时自动切到本目录 .venv + arch -arm64。
import os as _os
import sys as _sys
from pathlib import Path as _Path


def _boot_arm64_venv() -> None:
    if _os.environ.get("LEUC_REEXEC") == "1":
        return
    if __name__ != "__main__":
        return
    root = _Path(__file__).resolve().parent
    venv_py = root / ".venv" / "bin" / "python3"
    if not venv_py.is_file():
        venv_py = root / ".venv" / "bin" / "python"
    if not venv_py.is_file():
        return
    in_venv = _Path(_sys.prefix).resolve() == (root / ".venv").resolve()
    if in_venv:
        try:
            import cryptography  # noqa: F401

            return
        except Exception:
            pass
    env = {**_os.environ, "PYTHONNOUSERSITE": "1", "LEUC_REEXEC": "1"}
    arch = "/usr/bin/arch"
    if _Path(arch).is_file():
        _os.execve(
            arch,
            [arch, "-arm64", str(venv_py), str(_Path(__file__).resolve()), *_sys.argv[1:]],
            env,
        )
    _os.execve(str(venv_py), [str(venv_py), str(_Path(__file__).resolve()), *_sys.argv[1:]], env)


_boot_arm64_venv()
# AI-GEN-END

import csv
import io
import base64
import hashlib
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, session

from db import (
    ALL_BUTTONS,
    ALL_CAPS,
    ALL_MENUS,
    BUILTIN_ROLE_CODES,
    DEFAULT_ROLE_CAPS,
    DEFAULT_ROLE_MENUS,
    EMPLOYEE_ROLE_CODES,
    EXTERNAL_MENUS,
    LEUC_SYSTEM_CODE,
    ROLE_LABELS,
    ROLE_MENUS,
    SYSTEM_ROLE_EXCLUDE,
    alloc_username,
    connect,
    ensure_roles_seeded,
    ensure_user_roles_migrated,
    ensure_username_available,
    init_db,
    migrate_schema,
    name_to_pinyin,
    normalize_username,
    preview_unique_usernames,
    role_label_of,
    sync_primary_role,
    user_roles_of,
)

# AI-GEN-BEGIN
from leuc_approval_ext import (
    append_applicant_confirm,
    build_apply_form_fields,
    build_apply_form_view,
    collect_cc_for_system_owners,
    editable_form_keys,
    group_bind_items_by_owner,
    jump_to_reject_from_step,
    merge_todo_meta_updates,
    reject_to_specified_step,
    spawn_cc_todos,
    user_permission_snapshot,
)
from leuc_ops import (
    append_sync_change_logs,
    begin_task_run,
    extract_leorg_phone,
    finish_task_run,
    gen_account_password,
    get_external_dept_id,
    get_leave_close_record,
    list_audit_logs,
    list_leave_close_records,
    list_scheduled_tasks,
    list_sync_changes,
    list_task_runs,
    record_credential_notify,
    start_task_scheduler,
    update_scheduled_task,
    write_audit_log,
)
# AI-GEN-END

# AI-GEN-BEGIN
try:
    from beisen_sso import launch_url as beisen_launch_url
    from beisen_sso import load_config as beisen_load_config
    from beisen_sso import status_dict as beisen_status_dict
except Exception as _beisen_import_err:  # 缺依赖等时降级，不影响主流程
    _beisen_err_msg = f"beisen_sso 不可用：{_beisen_import_err}"
    beisen_launch_url = None
    beisen_load_config = lambda: None
    beisen_status_dict = lambda: {
        "ok": False,
        "enabled": False,
        "error": _beisen_err_msg,
    }

try:
    from leorg_client import LeorgClient
    from leorg_client import load_config as leorg_load_config
    from leorg_client import status_dict as leorg_status_dict
except Exception:
    LeorgClient = None
    leorg_load_config = lambda: None
    leorg_status_dict = lambda: {
        "ok": False,
        "enabled": False,
        "error": "leorg_client 不可用",
    }
# AI-GEN-END

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = "leuc-proto-demo-key"

STATIC = Path(__file__).resolve().parent / "static"
DEMO_OTP = "888888"
DEMO_RESET = "666666"
CAPTCHA_THRESHOLD = 1  # 失败 ≥1 次需图片验证码
FAIL_VERIFY_THRESHOLD = 10
CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# AI-GEN-BEGIN
# 系统超管：不进「部门和人员」通讯录
SYSTEM_ADMIN_USERNAME = "admin"


def is_hidden_from_org(row_or_user) -> bool:
    """系统超管等不在部门人员列表展示。"""
    if not row_or_user:
        return False
    if isinstance(row_or_user, dict):
        uname = (row_or_user.get("username") or "").strip()
    else:
        try:
            uname = (row_or_user["username"] or "").strip()
        except Exception:
            uname = ""
    return uname == SYSTEM_ADMIN_USERNAME


def ensure_system_admin(conn) -> None:
    """确保存在 admin 超管（全权限），且不挂在部门树上。"""
    from db import ALL_BUTTONS, ALL_MENUS

    row = conn.execute(
        "SELECT id FROM users WHERE username = ?", (SYSTEM_ADMIN_USERNAME,)
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE users SET
              password = '123456',
              display_name = '超级管理员',
              role = 'super_admin',
              dept_id = NULL,
              phone = NULL,
              email = NULL,
              itcode = 'admin',
              beisen_user_id = NULL,
              leorg_emp_id = NULL,
              person_type = 'internal',
              can_proxy_apply = 1,
              can_set_account_expire = 1
            WHERE username = ?""",
            (SYSTEM_ADMIN_USERNAME,),
        )
        uid = int(row["id"] if hasattr(row, "keys") else row[0])
    else:
        cur = conn.execute(
            """INSERT INTO users
            (username, password, display_name, role, dept_id, phone, email, itcode,
             password_expire, account_expire, person_type,
             can_proxy_apply, can_set_account_expire, beisen_user_id, leorg_emp_id)
            VALUES (?, '123456', '超级管理员', 'super_admin', NULL, NULL, NULL, 'admin',
                    '2099-12-31', NULL, 'internal', 1, 1, NULL, NULL)""",
            (SYSTEM_ADMIN_USERNAME,),
        )
        uid = int(cur.lastrowid)
    # 角色表灌满全部菜单/按钮
    conn.execute("DELETE FROM role_menus WHERE role = 'super_admin'")
    conn.execute("DELETE FROM role_caps WHERE role = 'super_admin'")
    conn.executemany(
        "INSERT INTO role_menus (role, menu_id) VALUES ('super_admin', ?)",
        [(m["id"],) for m in ALL_MENUS],
    )
    conn.executemany(
        "INSERT INTO role_caps (role, cap_id) VALUES ('super_admin', ?)",
        [(b["id"],) for b in ALL_BUTTONS],
    )
    # 不占用部门负责人
    conn.execute(
        "UPDATE departments SET owner_user_id = NULL WHERE owner_user_id = ?",
        (uid,),
    )
# AI-GEN-END


# AI-GEN-BEGIN
_DB_ENSURE_LOCK = threading.Lock()
_DB_ENSURED = False


def ensure_db(force: bool = False):
    """无种子或旧结构时强制重建（演示库）。启动后默认只跑一次，避免 poll 并发打爆 SQLite。"""
    global _DB_ENSURED
    if _DB_ENSURED and not force:
        return
    with _DB_ENSURE_LOCK:
        if _DB_ENSURED and not force:
            return
        _ensure_db_locked()
        _DB_ENSURED = True


def _ensure_db_locked():
    """ensure_db 持锁后的实际逻辑。"""
    # AI-GEN-END
    init_db(force=False)
    conn = connect()
    try:
        migrate_schema(conn)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        if "users" not in tables or "systems" not in tables or "oauth_codes" not in tables:
            conn.close()
            init_db(force=True)
            return
        cols = [r[1] for r in conn.execute("PRAGMA table_info(systems)").fetchall()]
        has_oidc = "redirect_uris" in cols and "require_pkce" in cols and "access_mode" in cols
        has_seed = conn.execute(
            "SELECT 1 FROM systems WHERE code = 'laiku_erp' AND access_mode = 'apply'"
        ).fetchone()
        dept_cols = [r[1] for r in conn.execute("PRAGMA table_info(departments)").fetchall()]
        has_org_tree = "parent_id" in dept_cols and "messages" in tables
        msg_cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()] if "messages" in tables else []
        has_chat = "msg_type" in msg_cols
        has_org_seed = conn.execute(
            "SELECT 1 FROM departments WHERE name = '来酷科技'"
        ).fetchone()
        # 不再强制要求 ≥500 人种子：清空部门后由 LeOrg 同步回填
        has_roster_seed = True
        has_sensitive = "sensitive_perm_defs" in tables and conn.execute(
            "SELECT 1 FROM approval_chain_steps WHERE flow_code = 'sensitive'"
        ).fetchone()
        has_hr = "hr_sync_roster" in tables
        has_sys_acct = "system_accounts" in tables and "grant_applications" in tables
        user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        has_itcode = "itcode" in user_cols
        has_person_type = "person_type" in user_cols
        has_account_expire = "account_expire" in user_cols
        has_bio = "face_enrolled" in user_cols and "fingerprint_enrolled" in user_cols
        has_proxy_apply = "can_proxy_apply" in user_cols
        has_set_expire = "can_set_account_expire" in user_cols
        has_set_expire_seed = True  # 改为角色能力配置，不再依赖人员演示标记
        has_role_menus = "role_menus" in tables and "role_caps" in tables and conn.execute(
            "SELECT 1 FROM role_menus WHERE role = 'super_admin' LIMIT 1"
        ).fetchone()
        has_btn_seed = conn.execute(
            "SELECT 1 FROM role_caps WHERE role = 'super_admin' AND cap_id = 'org_add'"
        ).fetchone() if "role_caps" in tables else None
        has_fp_table = "user_fingerprints" in tables
        has_forbid_external = "forbid_external" in cols
        has_sys_sensitive_flag = "has_sensitive" in cols
        # AI-GEN-BEGIN
        sens_cols = (
            [r[1] for r in conn.execute("PRAGMA table_info(sensitive_perm_defs)").fetchall()]
            if "sensitive_perm_defs" in tables
            else []
        )
        has_perm_tree = "parent_id" in sens_cols and "is_sensitive" in sens_cols
        has_perm_tree_seed = conn.execute(
            "SELECT 1 FROM sensitive_perm_defs WHERE perm_code = 'biz_root'"
        ).fetchone() if "sensitive_perm_defs" in tables else None
        has_system_owners = "system_owners" in tables
        # 不再依赖 liufang/zhangcai/admin 等种子账号是否存在
        has_demo_sys_admins = True
        # AI-GEN-END
        # 员工A 仅来酷×1；财务角色独立（勿与员工A 同 label）
        has_emp_a_clean = True  # 已去掉员工演示账号
        has_finance_role = conn.execute(
            "SELECT 1 FROM role_menus WHERE role = 'finance' LIMIT 1"
        ).fetchone() or conn.execute(
            "SELECT 1 FROM role_caps WHERE role = 'finance' LIMIT 1"
        ).fetchone() or True
        has_portal_cb = conn.execute(
            "SELECT 1 FROM systems WHERE code = 'laiku_erp' AND redirect_uris LIKE '%demo/home/callback?app=%'"
        ).fetchone()
        has_oa_forms = "oa_forms" in tables and "oa_form_lines" in tables
        has_feishu = conn.execute(
            "SELECT 1 FROM systems WHERE code = 'feishu'"
        ).fetchone()
        # 敏感/外部链：直属 → 一级 → 财务
        sens_first = conn.execute(
            """SELECT step_key FROM approval_chain_steps
            WHERE flow_code='sensitive' AND enabled=1 ORDER BY step_order LIMIT 1"""
        ).fetchone()
        has_sens_chain_v3 = sens_first and sens_first["step_key"] == "direct_leader"
        has_external_chain = conn.execute(
            "SELECT 1 FROM approval_chain_steps WHERE flow_code='external'"
        ).fetchone()
        beisen_forbid = conn.execute(
            "SELECT forbid_external FROM systems WHERE code='beisen'"
        ).fetchone()
        has_beisen_forbid = beisen_forbid and beisen_forbid["forbid_external"] == 1
        has_account_extend_seed = True  # 延期能力仍在；种子待办不再强制
        # AI-GEN-BEGIN
        # 仅在关键 schema / 演示系统缺失时重建；不清空部门后因人数变少而重种通讯录
        need_force = not (
            has_oidc
            and has_seed
            and has_org_tree
            and has_sensitive
            and has_hr
            and has_sys_acct
            and has_itcode
            and has_chat
            and has_finance_role
            and has_portal_cb
            and has_oa_forms
            and has_sens_chain_v3
            and has_external_chain
            and has_person_type
            and has_account_expire
            and has_bio
            and has_proxy_apply
            and has_set_expire
            and has_role_menus
            and has_btn_seed
            and has_fp_table
            and has_forbid_external
            and has_sys_sensitive_flag
            and has_perm_tree
            and has_perm_tree_seed
            and has_system_owners
            and has_demo_sys_admins
            and has_feishu
            and has_beisen_forbid
        )
        # 部门可由「清空 + LeOrg 同步」托管；允许空树，勿因无部门整库重种
        has_kept_admin = conn.execute(
            """SELECT 1 FROM users
            WHERE username = 'admin' OR role IN ('super_admin','hr_specialist')
            LIMIT 1"""
        ).fetchone()
        has_org_ok = (
            bool(has_org_seed)
            or conn.execute(
                "SELECT 1 FROM departments WHERE leorg_id IS NOT NULL LIMIT 1"
            ).fetchone()
            or conn.execute("SELECT 1 FROM departments LIMIT 1").fetchone()
            or bool(has_kept_admin)
        )
        if need_force or not has_org_ok:
            conn.close()
            init_db(force=True)
            conn = connect()
            try:
                ensure_system_admin(conn)
                conn.commit()
            finally:
                conn.close()
            return
        # AI-GEN-END
        # AI-GEN-BEGIN
        # 软迁移：审批链部门特例表 + 演示部门负责人
        if "approval_chain_dept_overrides" not in tables:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS approval_chain_dept_overrides (
                  id INTEGER PRIMARY KEY,
                  flow_code TEXT NOT NULL,
                  step_key TEXT NOT NULL,
                  dept_id INTEGER NOT NULL,
                  assignee_user_id INTEGER NOT NULL,
                  UNIQUE(flow_code, step_key, dept_id)
                )"""
            )
        btit = conn.execute(
            "SELECT id, owner_user_id FROM departments WHERE name = 'BTIT' ORDER BY id LIMIT 1"
        ).fetchone()
        cp = conn.execute(
            """SELECT id, owner_user_id FROM departments
            WHERE name = '产品营销' AND parent_id = (
              SELECT id FROM departments WHERE name = '来酷科技' LIMIT 1
            ) LIMIT 1"""
        ).fetchone()
        maning = conn.execute(
            "SELECT id FROM users WHERE username = 'maning' LIMIT 1"
        ).fetchone()
        wujinzhi = conn.execute(
            "SELECT id FROM users WHERE username = 'wujinzhi' LIMIT 1"
        ).fetchone()
        if btit and maning and not btit["owner_user_id"]:
            conn.execute(
                "UPDATE departments SET owner_user_id = ? WHERE id = ?",
                (maning["id"], btit["id"]),
            )
        if cp and wujinzhi and not cp["owner_user_id"]:
            conn.execute(
                "UPDATE departments SET owner_user_id = ? WHERE id = ?",
                (wujinzhi["id"], cp["id"]),
            )
        # AI-GEN-BEGIN
        # 测试链路角色：徐好好员工 · 马宁直属 · 吴锦志一级 · 高佳ERP负责人 · 常明明财务
        gaojia = conn.execute(
            "SELECT id FROM users WHERE username = 'gaojia' LIMIT 1"
        ).fetchone()
        chang = conn.execute(
            "SELECT id FROM users WHERE username = 'changmingming' LIMIT 1"
        ).fetchone()
        xu = conn.execute(
            "SELECT id FROM users WHERE username = 'xuhaohao' LIMIT 1"
        ).fetchone()
        # AI-GEN-BEGIN
        def _demo_set_role(uid, role_code):
            """演示账号：写主角色 + user_roles（幂等补种）。"""
            if not uid:
                return
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role_code, uid))
            try:
                if role_code in EMPLOYEE_ROLE_CODES:
                    conn.execute("DELETE FROM user_roles WHERE user_id=?", (uid,))
                    conn.execute(
                        "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?,?)",
                        (uid, role_code),
                    )
                else:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?,?)",
                        (uid, role_code),
                    )
                    conn.execute(
                        "DELETE FROM user_roles WHERE user_id=? AND role IN ('employee','employee_a','employee_b')",
                        (uid,),
                    )
                    sync_primary_role(conn, uid)
            except Exception:
                pass

        if maning:
            _demo_set_role(maning["id"], "dept_owner")
        if wujinzhi:
            _demo_set_role(wujinzhi["id"], "dept_owner")
        if xu:
            _demo_set_role(xu["id"], "employee")
        if gaojia:
            _demo_set_role(gaojia["id"], "system_owner")
            erp = conn.execute(
                "SELECT id FROM systems WHERE code = 'laiku_erp' LIMIT 1"
            ).fetchone()
            if erp:
                conn.execute(
                    "UPDATE systems SET owner_user_id = ? WHERE id = ?",
                    (gaojia["id"], erp["id"]),
                )
                conn.execute(
                    "DELETE FROM system_owners WHERE system_id = ?", (erp["id"],)
                )
                conn.execute(
                    "INSERT OR IGNORE INTO system_owners (system_id, user_id) VALUES (?,?)",
                    (erp["id"], gaojia["id"]),
                )
        if chang:
            _demo_set_role(chang["id"], "finance")
            conn.execute(
                """UPDATE approval_chain_steps SET assignee_user_id = ?
                WHERE step_key = 'finance' AND flow_code IN ('sensitive','external')""",
                (chang["id"],),
            )
        # AI-GEN-END
        # 按钮：设置部门负责人（已有库补种）
        for role in ("super_admin", "hr_specialist", "dept_owner"):
            conn.execute(
                """INSERT OR IGNORE INTO role_caps (role, cap_id)
                VALUES (?, 'org_set_owner')""",
                (role,),
            )
        # AI-GEN-BEGIN
        # 按钮：添加/删除部门（已有库软补，不覆盖角色其它按钮配置）
        for role in ("super_admin", "hr_specialist", "dept_owner"):
            for cap in ("org_dept_add", "org_dept_delete"):
                conn.execute(
                    """INSERT OR IGNORE INTO role_caps (role, cap_id)
                    VALUES (?, ?)""",
                    (role, cap),
                )
        # AI-GEN-END
        # 北森消息菜单（oa_forms）
        ensure_roles_seeded(conn)
        # AI-GEN-BEGIN
        # OA 改为需账号绑定，不再全员自动开通；清掉历史「全员登录自动开通」假绑定
        conn.execute(
            "UPDATE systems SET access_mode = 'apply' WHERE code = 'oa' AND access_mode = 'open'"
        )
        oa_row = conn.execute(
            "SELECT id FROM systems WHERE code = 'oa' LIMIT 1"
        ).fetchone()
        if oa_row:
            conn.execute(
                """DELETE FROM user_system_accounts
                WHERE system_id = ? AND account_label = '全员登录自动开通'""",
                (oa_row["id"],),
            )
        # AI-GEN-END
        # AI-GEN-END
        ensure_system_admin(conn)
        conn.commit()
        # AI-GEN-END
    except Exception as exc:
        # 软迁移失败不再整库重种（避免「清空部门」后被通讯录种子覆盖）
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        import sys

        print(f"[ensure_db] soft-migrate skipped: {exc}", file=sys.stderr)
        return
    conn.close()


ensure_db()  # 启动/热更新：多级部门 + 准入模式


def get_db():
    # AI-GEN-BEGIN
    if "db" not in g:
        # 初始化已在启动时完成；请求路径不再反复 executescript
        if not _DB_ENSURED:
            ensure_db()
        g.db = connect()
    return g.db
    # AI-GEN-END


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_user(row):
    if not row:
        return None
    keys = row.keys()
    fps = []
    try:
        db = get_db()
        fps = [
            {"id": r["id"], "label": r["label"], "enrolled_at": r["enrolled_at"]}
            for r in db.execute(
                """SELECT id, label, enrolled_at FROM user_fingerprints
                WHERE user_id = ? ORDER BY id""",
                (row["id"],),
            ).fetchall()
        ]
    except Exception:
        fps = []
        db = None
    # AI-GEN-BEGIN
    role = row["role"]
    menus, caps = [], []
    role_codes = []
    if db is not None:
        try:
            ensure_user_roles_migrated(db)
            role_codes = user_roles_of(db, row["id"])
        except Exception:
            role_codes = [role] if role else ["employee"]
        if not role_codes:
            role_codes = [role] if role else ["employee"]
        menu_set, cap_set = set(), set()
        try:
            for rc in role_codes:
                for r in db.execute(
                    "SELECT menu_id FROM role_menus WHERE role = ?", (rc,)
                ).fetchall():
                    menu_set.add(r["menu_id"])
                for r in db.execute(
                    "SELECT cap_id FROM role_caps WHERE role = ?", (rc,)
                ).fetchall():
                    cap_set.add(r["cap_id"])
            menus = sorted(menu_set)
            caps = sorted(cap_set)
        except Exception:
            menus, caps = [], []
    else:
        role_codes = [role] if role else ["employee"]
    if not menus:
        for rc in role_codes:
            menus.extend(DEFAULT_ROLE_MENUS.get(rc, ROLE_MENUS.get(rc, [])))
        menus = list(dict.fromkeys(menus))
    if not caps:
        for rc in role_codes:
            caps.extend(DEFAULT_ROLE_CAPS.get(rc, []))
        caps = list(dict.fromkeys(caps))
    # 系统超管：始终全菜单 + 全按钮
    if (row["username"] or "") == SYSTEM_ADMIN_USERNAME or "super_admin" in role_codes:
        menus = [m["id"] for m in ALL_MENUS]
        caps = [b["id"] for b in ALL_BUTTONS]
    # AI-GEN-BEGIN
    # 外部人员角色 / person_type：仅个人中心 + 安全管理
    ptype = row["person_type"] if "person_type" in keys else "internal"
    is_external = (
        "external" in role_codes
        or ptype == "external"
    ) and (row["username"] or "") != SYSTEM_ADMIN_USERNAME
    if is_external:
        menus = list(EXTERNAL_MENUS)
        caps = []
    # AI-GEN-END
    if ("can_proxy_apply" in keys and row["can_proxy_apply"]) and "proxy_apply" not in caps:
        caps.append("proxy_apply")
    if (
        "can_set_account_expire" in keys and row["can_set_account_expire"]
    ) and "set_account_expire" not in caps:
        caps.append("set_account_expire")
    role_labels = []
    if db is not None:
        role_labels = [
            {"code": c, "label": role_label_of(db, c)} for c in role_codes
        ]
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "roles": role_codes,
        "role_labels": role_labels,
        "role_label": role_label_of(db, row["role"]) if db is not None else ROLE_LABELS.get(row["role"], row["role"]),
        "dept_id": row["dept_id"],
        "phone": row["phone"],
        "email": row["email"],
        "itcode": row["itcode"] if "itcode" in keys else row["username"],
        "beisen_user_id": (
            (row["beisen_user_id"] or None) if "beisen_user_id" in keys else None
        ),
        # AI-GEN-BEGIN
        "leorg_emp_id": (
            (row["leorg_emp_id"] if "leorg_emp_id" in keys else None)
        ),
        # AI-GEN-END
        "password_expire": row["password_expire"],
        "account_expire": row["account_expire"] if "account_expire" in keys else None,
        "person_type": row["person_type"] if "person_type" in keys else "internal",
        "feishu_bound": bool(row["feishu_bound"]),
        "wecom_bound": bool(row["wecom_bound"]),
        "face_enrolled": bool(row["face_enrolled"]) if "face_enrolled" in keys else False,
        "face_enrolled_at": row["face_enrolled_at"] if "face_enrolled_at" in keys else None,
        "fingerprint_enrolled": bool(fps) or (
            bool(row["fingerprint_enrolled"]) if "fingerprint_enrolled" in keys else False
        ),
        "fingerprint_enrolled_at": row["fingerprint_enrolled_at"]
        if "fingerprint_enrolled_at" in keys
        else None,
        "fingerprints": fps,
        "menus": menus,
        "caps": caps,
        "buttons": caps,  # 按钮权限 = role_caps
        "can_proxy_apply": "proxy_apply" in caps,
        "can_set_account_expire": "set_account_expire" in caps,
        "status": (row["status"] if "status" in keys else "active") or "active",
    }
    # AI-GEN-END


def user_has_cap(user, cap_id: str) -> bool:
    # AI-GEN-BEGIN
    if not user:
        return False
    return cap_id in (user.get("caps") or [])
    # AI-GEN-END


# AI-GEN-BEGIN
def user_has_role(user, *codes) -> bool:
    """用户是否拥有任一角色（多角色并集；兼容仅有主角色）。"""
    if not user or not codes:
        return False
    have = user.get("roles")
    if not have:
        r = user.get("role")
        have = [r] if r else []
    want = set(codes)
    return any(c in want for c in have)
# AI-GEN-END


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return row_user(row)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return jsonify({"ok": False, "error": "未登录"}), 401
        return fn(u, *args, **kwargs)

    return wrapper


def require_dept_manage(user):
    """是否具备任一部门的管理权。"""
    if user_has_cap(user, "manage_all_org") or user_has_role(user, "super_admin", "hr_specialist"):
        return True
    db = get_db()
    return bool(managed_dept_ids(db, user))


def require_hr_manage(user):
    return user_has_cap(user, "manage_all_org") or user_has_role(user, "hr_specialist", "super_admin")


def all_departments(db):
    rows = db.execute(
        "SELECT * FROM departments ORDER BY sort_order, id"
    ).fetchall()
    out = []
    for d in rows:
        item = dict(d)
        owner = db.execute(
            "SELECT id, display_name, username FROM users WHERE id = ?",
            (d["owner_user_id"],),
        ).fetchone()
        item["owner_name"] = owner["display_name"] if owner else None
        item["owner_username"] = owner["username"] if owner else None
        extras = db.execute(
            """SELECT u.id, u.display_name, u.username FROM dept_extra_owners e
            JOIN users u ON u.id = e.user_id WHERE e.dept_id = ?""",
            (d["id"],),
        ).fetchall()
        item["extra_owners"] = [dict(e) for e in extras]
        out.append(item)
    return out


def children_map(depts):
    m = {}
    for d in depts:
        m.setdefault(d["parent_id"], []).append(d["id"])
    return m


def subtree_ids(depts, root_id):
    ch = children_map(depts)
    out = []
    stack = [root_id]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(ch.get(cur, []))
    return out


def build_org_tree(depts, manage_ids=None):
    """扁平部门 → 树；manage_ids 标注可管节点。"""
    by_id = {d["id"]: {**d, "children": [], "can_manage": False} for d in depts}
    for d in by_id.values():
        if manage_ids is not None:
            d["can_manage"] = d["id"] in manage_ids
        pid = d.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(d)
    for d in by_id.values():
        d["children"].sort(
            key=lambda x: (int(x.get("sort_order") or 0), int(x["id"]))
        )
    roots = [
        d
        for d in by_id.values()
        if not d.get("parent_id") or d["parent_id"] not in by_id
    ]
    roots.sort(key=lambda x: (int(x.get("sort_order") or 0), int(x["id"])))
    return roots



# AI-GEN-BEGIN
def dept_ancestor_chain(depts, dept_id):
    """从根到指定部门的祖先链（含自身）。"""
    if not dept_id:
        return []
    by_id = {d["id"]: d for d in depts}
    chain = []
    cur = by_id.get(int(dept_id))
    seen = set()
    while cur and cur["id"] not in seen:
        seen.add(cur["id"])
        chain.append(cur)
        pid = cur.get("parent_id")
        cur = by_id.get(pid) if pid else None
    chain.reverse()
    return chain


def dept_path_label(chain, sep=" / "):
    """部门全路径文案。"""
    return sep.join((d.get("name") or "").strip() for d in (chain or []) if d.get("name"))
# AI-GEN-END


def managed_dept_ids(db, user):
    """用户作为负责人/额外负责人所管部门及其全部下级。"""
    if user_has_cap(user, "manage_all_org") or user_has_role(user, "super_admin", "hr_specialist"):
        return {d["id"] for d in db.execute("SELECT id FROM departments").fetchall()}
    depts = [dict(r) for r in db.execute("SELECT * FROM departments").fetchall()]
    owned = set()
    for d in depts:
        if d["owner_user_id"] == user["id"]:
            owned.add(d["id"])
    for r in db.execute(
        "SELECT dept_id FROM dept_extra_owners WHERE user_id = ?", (user["id"],)
    ).fetchall():
        owned.add(r["dept_id"])
    result = set()
    for oid in owned:
        result.update(subtree_ids(depts, oid))
    return result


def can_manage_dept(user, dept_id):
    if user_has_cap(user, "manage_all_org") or user_has_role(user, "super_admin"):
        return True
    return int(dept_id) in managed_dept_ids(get_db(), user)


def can_manage_member(user, target_row):
    if user_has_cap(user, "manage_all_org") or user_has_role(user, "super_admin"):
        return True
    if not target_row or not target_row["dept_id"]:
        return False
    return can_manage_dept(user, target_row["dept_id"])


def can_apply_for_user(user, target_row):
    """账号申请：全员可为本人；具备代人能力可为他人；部门负责人可管下级。"""
    # AI-GEN-BEGIN
    if not target_row:
        return False
    if int(user["id"]) == int(target_row["id"]):
        return True
    if user_has_cap(user, "proxy_apply") or user_has_role(user, "hr_specialist", "super_admin"):
        return True
    if user.get("can_proxy_apply"):
        return True
    if user_has_role(user, "dept_owner"):
        return can_manage_member(user, target_row)
    return False
    # AI-GEN-END


def user_can_set_account_expire(user):
    """设置账号有效期：角色能力或人员开通。"""
    # AI-GEN-BEGIN
    if not user:
        return False
    if user_has_cap(user, "set_account_expire"):
        return True
    return bool(user.get("can_set_account_expire"))
    # AI-GEN-END


# AI-GEN-BEGIN
CN_TZ = ZoneInfo("Asia/Shanghai")


def now_cn() -> datetime:
    """当前中国时区（Asia/Shanghai）时间。"""
    return datetime.now(CN_TZ)


def now_ts() -> str:
    """业务时间戳：中国时区年月日时分秒（待办/申请列表与详情展示）。"""
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")


def default_account_expire(days: int = 90) -> str:
    """新建账号默认有效期：今天 + N 天。"""
    return (now_cn() + timedelta(days=int(days))).strftime("%Y-%m-%d")


# 进程级时区（Docker 内 datetime.now 等也会对齐；Windows 仍以 now_cn/now_ts 为准）
_os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    import time as _time

    if hasattr(_time, "tzset"):
        _time.tzset()
except Exception:
    pass
# AI-GEN-END


def find_approver(db, applicant_id):
    """账号延期等：找上级负责人（本人是负责人则找父级负责人）。"""
    u = db.execute("SELECT * FROM users WHERE id = ?", (applicant_id,)).fetchone()
    if not u or not u["dept_id"]:
        return None
    dept = db.execute("SELECT * FROM departments WHERE id = ?", (u["dept_id"],)).fetchone()
    if not dept:
        return None
    owner_id = dept["owner_user_id"]
    if owner_id and owner_id != applicant_id:
        return owner_id
    # 本人是本级负责人 → 找父级
    parent_id = dept["parent_id"]
    while parent_id:
        parent = db.execute(
            "SELECT * FROM departments WHERE id = ?", (parent_id,)
        ).fetchone()
        if not parent:
            break
        if parent["owner_user_id"] and parent["owner_user_id"] != applicant_id:
            return parent["owner_user_id"]
        parent_id = parent["parent_id"]
    # 兜底超管
    sa = db.execute(
        "SELECT id FROM users WHERE role = 'super_admin' ORDER BY id LIMIT 1"
    ).fetchone()
    return sa["id"] if sa else None


def find_level1_leader(db, applicant_id, skip_ids=None):
    """一级领导：直属之上一级部门负责人（同人则继续上溯）。"""
    skip = set(skip_ids or [])
    skip.add(applicant_id)
    u = db.execute("SELECT * FROM users WHERE id = ?", (applicant_id,)).fetchone()
    if not u or not u["dept_id"]:
        return None
    dept = db.execute("SELECT * FROM departments WHERE id = ?", (u["dept_id"],)).fetchone()
    if not dept:
        return None
    direct = find_approver(db, applicant_id)
    if direct:
        skip.add(direct)
    parent_id = dept["parent_id"]
    # 若直属就是本部门负责人，从父级开始找一级
    while parent_id:
        parent = db.execute(
            "SELECT * FROM departments WHERE id = ?", (parent_id,)
        ).fetchone()
        if not parent:
            break
        oid = parent["owner_user_id"]
        if oid and oid not in skip:
            return oid
        parent_id = parent["parent_id"]
    sa = db.execute(
        "SELECT id FROM users WHERE role = 'super_admin' ORDER BY id LIMIT 1"
    ).fetchone()
    return sa["id"] if sa else None


# AI-GEN-BEGIN
def find_dept_chain_override(db, flow_code, step_key, applicant_id):
    """部门特例：仅匹配申请人所属部门，不向父/子部门继承。"""
    u = db.execute("SELECT dept_id FROM users WHERE id = ?", (applicant_id,)).fetchone()
    if not u or not u["dept_id"]:
        return None
    row = db.execute(
        """SELECT assignee_user_id FROM approval_chain_dept_overrides
        WHERE flow_code = ? AND step_key = ? AND dept_id = ?""",
        (flow_code, step_key, u["dept_id"]),
    ).fetchone()
    return row["assignee_user_id"] if row else None


def resolve_chain_assignee(db, step, applicant_id, used_ids=None, flow_code="sensitive"):
    """解析审批链步骤的实际审批人。直属/一级：部门特例优先，否则动态。"""
    used = set(used_ids or [])
    key = step["step_key"]
    if key in ("direct_leader", "level1_leader"):
        ov = find_dept_chain_override(db, flow_code, key, applicant_id)
        if ov:
            return ov
        if key == "direct_leader":
            return find_approver(db, applicant_id)
        return find_level1_leader(db, applicant_id, skip_ids=used)
    if key in ("finance", "user", "fixed"):
        return step["assignee_user_id"]
    if step["assignee_user_id"]:
        return step["assignee_user_id"]
    return find_approver(db, applicant_id)


def materialize_approval_chain(db, flow_code, applicant_id):
    """解析审批链；申请人=审批人时跳过该步。返回 [(step_key, step_label, assignee_id), ...]。"""
    chain = db.execute(
        """SELECT * FROM approval_chain_steps
        WHERE flow_code = ? AND enabled = 1 ORDER BY step_order""",
        (flow_code,),
    ).fetchall()
    if not chain and flow_code == "external":
        chain = db.execute(
            """SELECT * FROM approval_chain_steps
            WHERE flow_code = 'sensitive' AND enabled = 1 ORDER BY step_order"""
        ).fetchall()
    used = set()
    out = []
    for step in chain:
        assignee = resolve_chain_assignee(
            db, step, applicant_id, used_ids=used, flow_code=flow_code
        )
        if not assignee:
            continue
        if int(assignee) == int(applicant_id):
            continue
        if int(assignee) in used:
            continue
        used.add(int(assignee))
        out.append((step["step_key"], step["step_label"], int(assignee)))
    return out
# AI-GEN-END


def user_may_access_system(db, user_row, system_row):
    """外部人员不可登录 forbid_external=1 的系统。"""
    if not user_row or not system_row:
        return False, "系统或用户不存在"
    ptype = user_row["person_type"] if "person_type" in user_row.keys() else "internal"
    forbid = system_row["forbid_external"] if "forbid_external" in system_row.keys() else 0
    if ptype == "external" and forbid:
        return False, f"{system_row['name']} 禁止外部人员登录"
    return True, ""
# AI-GEN-END



# AI-GEN-BEGIN
def append_system_owner_step(db, system_id, steps, *, purpose="open"):
    """在审批链末尾追加系统负责人（开通或关闭）；跳过已在链中的人。"""
    # AI-GEN-BEGIN
    steps = list(steps or [])
    used = {s[2] for s in steps if s and s[2]}
    owners = list_system_owner_ids(db, system_id) if system_id else []
    if not owners:
        row = db.execute(
            "SELECT owner_user_id FROM systems WHERE id = ?", (system_id,)
        ).fetchone() if system_id else None
        if row and row["owner_user_id"]:
            owners = [row["owner_user_id"]]
    label = (
        "系统负责人关闭账号" if purpose == "close" else "系统负责人开通"
    )
    for oid in owners:
        if oid and oid not in used:
            steps.append(("system_owner", label, oid))
            break
    return steps
    # AI-GEN-END


def prepare_flow_steps(db, steps, applicant_id, system_id=None):
    """主链追加申请人确认，并收集系统管理员知会对象。"""
    # AI-GEN-BEGIN
    steps = list(steps or [])
    ccs = collect_cc_for_system_owners(db, find_approver, steps, applicant_id)
    steps = append_applicant_confirm(steps, applicant_id)
    return steps, ccs
    # AI-GEN-END


def get_provision_targets(db, meta: dict | None, application=None) -> list[dict]:
    """待开通账号列表：按申请明细行（同一系统多行也各开一个账号）。"""
    # AI-GEN-BEGIN
    meta = meta if isinstance(meta, dict) else {}
    app = dict(application) if application and not isinstance(application, dict) else (application or {})
    targets: list[dict] = []

    def _sys_name(sid, fallback=None):
        if fallback:
            return fallback
        if not sid:
            return "—"
        sy = db.execute(
            "SELECT name, code FROM systems WHERE id = ?", (int(sid),)
        ).fetchone()
        return sy["name"] if sy else f"系统#{sid}"

    items = meta.get("items") or meta.get("lines") or []
    if isinstance(items, list) and items:
        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            # 仅「新建账号」行需要负责人开通
            create_new = it.get("create_new")
            if create_new is None:
                create_new = meta.get("create_new", True)
            if not create_new:
                continue
            sid = it.get("system_id") or app.get("system_id")
            if not sid:
                continue
            sid = int(sid)
            name = _sys_name(sid, it.get("system_name"))
            perms = it.get("perm_names") or []
            if isinstance(perms, str):
                perms = [perms]
            perm_txt = "、".join(str(x) for x in perms if x)
            sens = bool(
                it.get("with_sensitive")
                if it.get("with_sensitive") is not None
                else meta.get("with_sensitive")
            )
            label = name
            if perm_txt:
                label = f"{name} · {perm_txt}"
            elif sens:
                label = f"{name} · 含敏感"
            # 同行次提示（同系统多账号）
            same_sys_n = sum(
                1
                for j, x in enumerate(items)
                if isinstance(x, dict)
                and int(x.get("system_id") or 0) == sid
                and (x.get("create_new") if x.get("create_new") is not None else True)
                and j <= idx
            )
            if same_sys_n > 1 or sum(
                1
                for x in items
                if isinstance(x, dict) and int(x.get("system_id") or 0) == sid
            ) > 1:
                label = f"{label}（第{same_sys_n}个账号）"
            targets.append(
                {
                    "line_key": str(idx),
                    "line_index": idx,
                    "system_id": sid,
                    "system_name": name,
                    "label": label,
                    "perm_names": list(perms),
                    "with_sensitive": sens,
                    "create_new": True,
                    "item": it,
                }
            )
        if targets:
            return targets

    # 无明细行：按系统去重回退
    sids = meta.get("system_ids") or []
    if not isinstance(sids, list):
        sids = [sids] if sids else []
    if not sids and (meta.get("system_id") or app.get("system_id")):
        sids = [meta.get("system_id") or app.get("system_id")]
    seen = set()
    for i, sid in enumerate(sids):
        if not sid:
            continue
        sid = int(sid)
        if sid in seen:
            continue
        seen.add(sid)
        name = _sys_name(sid)
        targets.append(
            {
                "line_key": str(i),
                "line_index": i,
                "system_id": sid,
                "system_name": name,
                "label": name,
                "perm_names": [],
                "with_sensitive": bool(meta.get("with_sensitive")),
                "create_new": True,
                "item": None,
            }
        )
    return targets
    # AI-GEN-END


def provision_account_apply(
    db,
    application,
    with_sensitive=False,
    account_name=None,
    remark=None,
    account_id=None,
    system_id=None,
    *,
    notify=True,
    mark_app=True,
):
    """系统负责人开通：从账号池选择业务账号并关联申请人，可选敏感与备注。"""
    # AI-GEN-BEGIN
    sid = int(system_id) if system_id not in (None, "") else application["system_id"]
    uid = application["applicant_id"]
    if not sid or not uid:
        return {"ok": False, "error": "缺少系统或申请人"}
    sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not sys_row or not user:
        return {"ok": False, "error": "系统或用户不存在"}

    pool = None
    pool_aid = int(account_id) if account_id not in (None, "") else None
    if pool_aid:
        pool = db.execute(
            "SELECT * FROM system_accounts WHERE id = ? AND system_id = ? LIMIT 1",
            (pool_aid, sid),
        ).fetchone()
        if not pool:
            return {"ok": False, "error": f"所选账号不在「{sys_row['name']}」账号池中"}
        acct_name = (pool["account_name"] or "").strip()
    else:
        acct_name = (account_name or "").strip()
        if not acct_name:
            return {"ok": False, "error": f"请为「{sys_row['name']}」从账号池选择业务系统账号"}
        pool = db.execute(
            """SELECT * FROM system_accounts
            WHERE system_id = ? AND account_name = ? LIMIT 1""",
            (sid, acct_name),
        ).fetchone()
        if not pool:
            return {
                "ok": False,
                "error": f"「{sys_row['name']}」账号不在全部账户中，请从账号池选择",
            }

    if pool["leuc_user_id"] and int(pool["leuc_user_id"]) != int(uid):
        other = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?",
            (pool["leuc_user_id"],),
        ).fetchone()
        who = (
            f"{other['display_name']}({other['username']})"
            if other
            else str(pool["leuc_user_id"])
        )
        return {"ok": False, "error": f"账号名已被占用：{acct_name} → {who}"}

    note = (remark or "").strip()
    summary = note or ("敏感权限" if with_sensitive else "普通开通")
    label = note or "账号申请开通"
    now = datetime.now().strftime("%Y-%m-%d")

    exists = db.execute(
        """SELECT * FROM user_system_accounts
        WHERE user_id = ? AND system_id = ? AND account_name = ? LIMIT 1""",
        (uid, sid, acct_name),
    ).fetchone()

    if exists:
        hs = 1 if with_sensitive else int(exists["has_sensitive"] or 0)
        db.execute(
            """UPDATE user_system_accounts
            SET can_login = 1, has_sensitive = ?, perm_summary = ?, account_label = ?
            WHERE id = ?""",
            (hs, summary, label, exists["id"]),
        )
        account_id = exists["id"]
        db.execute(
            """UPDATE system_accounts SET leuc_user_id = ?, status = 'bound',
            display_name = COALESCE(display_name, ?) WHERE id = ?""",
            (uid, user["display_name"], pool["id"]),
        )
        pool_id = pool["id"]
    else:
        pool_id = pool["id"]
        db.execute(
            """UPDATE system_accounts SET leuc_user_id = ?, status = 'bound',
            display_name = COALESCE(display_name, ?) WHERE id = ?""",
            (uid, user["display_name"], pool_id),
        )
        cur2 = db.execute(
            """INSERT INTO user_system_accounts
            (user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
            VALUES (?,?,?,?,1,1,?,?)""",
            (
                uid,
                sid,
                acct_name,
                label,
                1 if with_sensitive else 0,
                summary,
            ),
        )
        account_id = cur2.lastrowid

    if mark_app:
        db.execute(
            "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
            (now, application["id"]),
        )
    if notify:
        msg_body = (
            f"「{sys_row['name']}」账号 {acct_name} 已开通"
            + ("（含敏感权限）" if with_sensitive else "")
            + (f"。备注：{note}" if note else "")
            + "，可登录使用。"
        )
        push_system_message(db, uid, "账号申请已开通", msg_body)
    return {
        "ok": True,
        "system": sys_row["name"],
        "system_id": sid,
        "account": acct_name,
        "account_id": account_id,
        "pool_account_id": pool_id,
        "applicant_id": uid,
        "application_id": application["id"],
        "remark": note,
        "with_sensitive": with_sensitive,
    }
    # AI-GEN-END


def _match_provision(provisions: list, target: dict) -> dict | None:
    """匹配某一开通目标行：优先 line_key / line_index，再回退未占用的 system_id。"""
    # AI-GEN-BEGIN
    if not isinstance(target, dict):
        return None
    lk = target.get("line_key")
    lk_s = str(lk) if lk is not None else None
    li = target.get("line_index")
    sid = target.get("system_id")
    sid_i = int(sid) if sid not in (None, "") else None

    for p in provisions:
        if not isinstance(p, dict) or p.get("_used"):
            continue
        if lk_s is not None and p.get("line_key") is not None and str(p.get("line_key")) == lk_s:
            return p
    if li is not None:
        for p in provisions:
            if not isinstance(p, dict) or p.get("_used"):
                continue
            if p.get("line_index") is not None and int(p.get("line_index")) == int(li):
                return p
    # 旧客户端：仅按 system_id，每条 provision 只能匹配一次
    if sid_i is not None:
        for p in provisions:
            if not isinstance(p, dict) or p.get("_used"):
                continue
            if p.get("system_id") in (None, ""):
                continue
            if int(p.get("system_id")) != sid_i:
                continue
            # 若带了别的 line_key，说明专属于另一行
            if p.get("line_key") is not None and lk_s is not None and str(p.get("line_key")) != lk_s:
                continue
            return p
    return None
    # AI-GEN-END


def provision_account_apply_multi(
    db,
    application,
    provisions: list | None,
    *,
    meta: dict | None = None,
    with_sensitive=False,
    remark=None,
):
    """按申请明细行逐个开通；同系统多行须各选不同池账号。"""
    # AI-GEN-BEGIN
    meta = meta if isinstance(meta, dict) else {}
    targets = get_provision_targets(db, meta, application)
    if not targets:
        return {"ok": False, "error": "无可开通的业务系统"}
    prov_list = [dict(p) for p in (provisions or []) if isinstance(p, dict)]
    # 兼容旧单账号入参
    if not prov_list and (meta.get("_single_account_id") or meta.get("_single_account_name")):
        prov_list = [
            {
                "line_key": targets[0].get("line_key"),
                "system_id": targets[0]["system_id"],
                "account_id": meta.get("_single_account_id"),
                "account_name": meta.get("_single_account_name"),
            }
        ]
    missing = []
    matched = []
    for t in targets:
        p = _match_provision(prov_list, t)
        if not p or not (p.get("account_id") or (p.get("account_name") or "").strip()):
            missing.append(t)
            continue
        p["_used"] = True
        matched.append((t, p))
    if missing:
        names = "、".join(t.get("label") or t.get("system_name") for t in missing)
        return {
            "ok": False,
            "error": f"请为以下申请行选择账号：{names}",
            "need_account_input": True,
            "provision_targets": targets,
            "missing_line_keys": [t.get("line_key") for t in missing],
            "missing_system_ids": [t["system_id"] for t in missing],
        }
    # 禁止同一池账号绑定到多行
    seen_pool = set()
    for t, p in matched:
        key = None
        if p.get("account_id") not in (None, ""):
            key = ("id", int(p["account_id"]))
        else:
            an = (p.get("account_name") or "").strip()
            if an:
                key = ("name", int(t["system_id"]), an)
        if key and key in seen_pool:
            return {
                "ok": False,
                "error": f"「{t.get('label') or t.get('system_name')}」与其它行选择了同一账号，请各选不同账号",
                "need_account_input": True,
                "provision_targets": targets,
            }
        if key:
            seen_pool.add(key)

    results = []
    parts = []
    for t, p in matched:
        sid = int(t["system_id"])
        sens = bool(
            t.get("with_sensitive") if t.get("with_sensitive") is not None else with_sensitive
        )
        r = provision_account_apply(
            db,
            application,
            with_sensitive=sens,
            account_name=(p.get("account_name") or "").strip() or None,
            remark=remark,
            account_id=p.get("account_id"),
            system_id=sid,
            notify=False,
            mark_app=False,
        )
        if not r.get("ok"):
            return r
        r["line_key"] = t.get("line_key")
        r["line_label"] = t.get("label")
        results.append(r)
        parts.append(f"{t.get('label') or r.get('system')} / {r.get('account')}")
    now = datetime.now().strftime("%Y-%m-%d")
    db.execute(
        "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
        (now, application["id"]),
    )
    uid = application["applicant_id"]
    push_system_message(
        db,
        uid,
        "账号申请已开通",
        f"已开通 {len(results)} 个账号：{'；'.join(parts)}"
        + (f"。备注：{remark}" if remark else ""),
    )
    return {
        "ok": True,
        "count": len(results),
        "items": results,
        "system": results[0].get("system") if results else None,
        "account": results[0].get("account") if results else None,
        "message": f"已开通 {len(results)} 个：{'；'.join(parts)}",
        "applicant_id": uid,
        "application_id": application["id"],
    }
    # AI-GEN-END


def serialize_todo(db, row):
    """待办序列化：附带排查用 ID、当前审核人与进度。"""
    # AI-GEN-BEGIN
    d = dict(row)
    meta = {}
    try:
        meta = json.loads(d.get("meta") or "{}")
    except Exception:
        meta = {}
    d["meta_obj"] = meta
    d["todo_id"] = d.get("id")
    app_id = d.get("application_id")
    step_key = None
    flow_code = None
    applicant = None
    system = None
    app_row = None
    if app_id:
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if app_row:
            flow_code = app_row["flow_code"]
            d["flow_code"] = flow_code
            d["applicant_id"] = app_row["applicant_id"]
            d["system_id"] = app_row["system_id"]
            d["app_status"] = app_row["status"]
            d["total_steps"] = app_row["total_steps"]
            d["current_step"] = app_row["current_step"]
            step = db.execute(
                """SELECT * FROM application_steps
                WHERE application_id = ? AND step_order = ?""",
                (app_id, d.get("step_order") or app_row["current_step"]),
            ).fetchone()
            if step:
                step_key = step["step_key"]
                d["step_key"] = step_key
                d["step_label"] = step["step_label"]
            au = db.execute(
                "SELECT id, username, display_name, role FROM users WHERE id = ?",
                (app_row["applicant_id"],),
            ).fetchone()
            if au:
                applicant = dict(au)
            if app_row["system_id"]:
                sy = db.execute(
                    "SELECT id, code, name FROM systems WHERE id = ?",
                    (app_row["system_id"],),
                ).fetchone()
                if sy:
                    system = dict(sy)
            summary = build_application_flow(db, app_id)
            d["current_approver"] = summary.get("current_approver")
            d["progress"] = summary.get("progress")
            d["progress_label"] = summary.get("progress_label")
            d["forecast_count"] = len(summary.get("forecast") or [])
    else:
        assignee = _user_brief(db, d.get("assignee_id"))
        d["current_approver"] = (
            None
            if d.get("status") in ("approved", "rejected") and d.get("bucket") == "done"
            else assignee
        )
        d["progress"] = "1/1"
        d["progress_label"] = d.get("todo_type") or "审批"
        d["forecast_count"] = 0
        if meta.get("leuc_user_id"):
            au = db.execute(
                "SELECT id, username, display_name, role FROM users WHERE id = ?",
                (int(meta["leuc_user_id"]),),
            ).fetchone()
            if au:
                applicant = dict(au)
    d["applicant"] = applicant
    d["system"] = system
    d["initiator"] = _user_brief(db, d.get("initiator_id"))
    create_new = bool(meta.get("create_new"))
    need_form = (
        d.get("todo_type") == "账号申请"
        and step_key == "system_owner"
        and flow_code in ("account_apply", "account_apply_sensitive", "sensitive")
        and create_new
    )
    d["need_provision_form"] = need_form
    d["create_new"] = create_new
    d["with_sensitive"] = bool(
        meta.get("with_sensitive")
        or (flow_code in ("account_apply_sensitive", "sensitive") if flow_code else False)
    )
    # AI-GEN-BEGIN
    if need_form:
        d["provision_targets"] = get_provision_targets(db, meta, app_row)
    else:
        d["provision_targets"] = []
    # AI-GEN-END
    # AI-GEN-BEGIN
    d["is_cc"] = bool(meta.get("cc") or d.get("todo_type") == "知会确认")
    d["is_confirm"] = step_key == "applicant_confirm"
    d["rejectable_steps"] = []
    if app_id and d.get("bucket") == "pending" and not d["is_cc"] and not d["is_confirm"]:
        # 0 = 申请人修改重提
        d["rejectable_steps"] = [
            {"order": 0, "label": "申请人（修改后重提）", "key": "applicant_edit"}
        ]
        prior = db.execute(
            """SELECT step_order, step_label, step_key FROM application_steps
            WHERE application_id = ? AND step_order < ?
              AND step_key != 'applicant_confirm'
            ORDER BY step_order""",
            (app_id, d.get("step_order") or 1),
        ).fetchall()
        d["rejectable_steps"].extend(
            [
                {
                    "order": r["step_order"],
                    "label": r["step_label"],
                    "key": r["step_key"],
                }
                for r in prior
            ]
        )
    app_status = d.get("app_status")
    reject_from = None
    if app_id and app_row:
        try:
            reject_from = app_row["reject_from_step"] if "reject_from_step" in app_row.keys() else None
        except Exception:
            reject_from = None
    d["can_resubmit"] = bool(
        d.get("bucket") == "pending"
        and d.get("status") == "open"
        and not d["is_cc"]
        and (
            meta.get("needs_resubmit")
            or app_status == "returned"
            or (d.get("step_order") in (0, "0") and app_status == "returned")
        )
    )
    d["reject_from_step"] = reject_from
    form_view = build_apply_form_view(db, meta, app_row if app_id else None)
    form_fields = form_view.get("rows") or build_apply_form_fields(
        db, meta, app_row if app_id else None
    )
    d["form_view"] = form_view
    d["form_fields"] = form_fields
    d["editable_keys"] = editable_form_keys(form_fields) if d["can_resubmit"] else []
    # 延期：展示用户权限详情（突出敏感）
    if flow_code in ("account_extend", "account_extend_sensitive") or meta.get("days"):
        uid = meta.get("leuc_user_id") or (applicant or {}).get("id")
        if uid:
            d["user_permissions"] = user_permission_snapshot(db, int(uid))
    # AI-GEN-END
    if applicant and system:
        d["suggest_account"] = f"{applicant['username']}_{system['code']}"
    else:
        d["suggest_account"] = ""
    return d
    # AI-GEN-END


def _user_brief(db, uid):
    # AI-GEN-BEGIN
    if not uid:
        return None
    u = db.execute(
        "SELECT id, username, display_name, role FROM users WHERE id = ?",
        (int(uid),),
    ).fetchone()
    return dict(u) if u else None
    # AI-GEN-END


# AI-GEN-BEGIN
def _persist_decide_remark(db, tid, remark, *, app_id=None, step_order=None):
    """审批操作备注：写入 todos.remark，有申请单时同步 application_steps。"""
    note = (remark or "").strip() or None
    try:
        db.execute("UPDATE todos SET remark = ? WHERE id = ?", (note, int(tid)))
    except Exception:
        pass
    if app_id is not None and step_order is not None:
        try:
            db.execute(
                """UPDATE application_steps SET remark = ?
                WHERE application_id = ? AND step_order = ?""",
                (note, int(app_id), int(step_order)),
            )
        except Exception:
            pass
# AI-GEN-END


def build_application_flow(db, app_id):
    """拼装申请单流程：时间线 / 当前审核人 / 预测步骤。"""
    # AI-GEN-BEGIN
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not app:
        return {
            "application": None,
            "timeline": [],
            "forecast": [],
            "current_approver": None,
            "progress": "",
            "progress_label": "",
        }
    steps = db.execute(
        """SELECT * FROM application_steps
        WHERE application_id = ? ORDER BY step_order""",
        (app_id,),
    ).fetchall()
    timeline = []
    current_approver = None
    current_label = None
    current_order = None
    for s in steps:
        st = (s["status"] or "").strip()
        assignee = _user_brief(db, s["assignee_id"])
        if st in ("approved", "rejected", "skipped"):
            phase = "done"
        elif st == "pending":
            phase = "current"
            current_approver = assignee
            current_label = s["step_label"]
            current_order = s["step_order"]
        else:
            phase = "forecast"
        timeline.append(
            {
                "step_order": s["step_order"],
                "step_key": s["step_key"],
                "step_label": s["step_label"],
                "status": st,
                "phase": phase,
                "assignee": assignee,
                "assignee_id": s["assignee_id"],
                "todo_id": s["todo_id"],
                "decided_at": s["decided_at"],
                "remark": (s["remark"] if "remark" in s.keys() else None) or None,
                "step_kind": (s["step_kind"] if "step_kind" in s.keys() else "approve"),
            }
        )
    forecast = [t for t in timeline if t["phase"] == "forecast"]
    total = int(app["total_steps"] or len(timeline) or 1)
    cur = int(current_order or app["current_step"] or 1)
    if app["status"] in ("approved", "rejected", "done"):
        progress = f"{total}/{total}"
        progress_label = "已结束"
        current_approver = None
    else:
        progress = f"{cur}/{total}"
        progress_label = current_label or f"第{cur}步"
    applicant = _user_brief(db, app["applicant_id"])
    # AI-GEN-BEGIN
    # 驳回到申请人（current_step=0）：时间线插入虚拟当前节点
    if app["status"] == "returned" and int(app["current_step"] or -1) == 0:
        current_approver = applicant
        progress = f"0/{total}"
        progress_label = "申请人修改重提"
        timeline.insert(
            0,
            {
                "step_order": 0,
                "step_key": "applicant_edit",
                "step_label": "申请人修改重提",
                "status": "pending",
                "phase": "current",
                "assignee": applicant,
                "assignee_id": app["applicant_id"],
                "todo_id": None,
                "decided_at": None,
                "remark": None,
                "step_kind": "edit",
            },
        )
        forecast = [t for t in timeline if t["phase"] == "forecast"]
    # AI-GEN-END
    system = None
    if app["system_id"]:
        sy = db.execute(
            "SELECT id, code, name FROM systems WHERE id = ?", (app["system_id"],)
        ).fetchone()
        if sy:
            system = dict(sy)
    return {
        "application": {
            "id": app["id"],
            "flow_code": app["flow_code"],
            "title": app["title"],
            "status": app["status"],
            "current_step": app["current_step"],
            "total_steps": app["total_steps"],
            "created_at": app["created_at"],
            "updated_at": app["updated_at"],
            "reject_to_step": (
                app["reject_to_step"] if "reject_to_step" in app.keys() else None
            ),
            "reject_from_step": (
                app["reject_from_step"] if "reject_from_step" in app.keys() else None
            ),
            "applicant": applicant,
            "system": system,
        },
        "timeline": timeline,
        "forecast": forecast,
        "current_approver": current_approver,
        "progress": progress,
        "progress_label": progress_label,
    }
    # AI-GEN-END


def build_todo_flow(db, todo_row):
    """待办完整流程视图（有 application 走多级链，否则单步）。"""
    # AI-GEN-BEGIN
    todo_ser = serialize_todo(db, todo_row)
    app_id = todo_row["application_id"] if "application_id" in todo_row.keys() else todo_row.get("application_id")
    if app_id:
        flow = build_application_flow(db, app_id)
        flow["todo"] = todo_ser
        return flow
    meta = {}
    try:
        meta = json.loads(todo_row["meta"] or "{}")
    except Exception:
        meta = {}
    assignee = _user_brief(db, todo_row["assignee_id"])
    st = (todo_row["status"] or "").strip()
    done = todo_row["bucket"] == "done" or st in ("approved", "rejected")
    phase = "done" if done else "current"
    step_label = meta.get("step_label") or todo_row["todo_type"] or "审批"
    timeline = [
        {
            "step_order": 1,
            "step_key": "direct_leader",
            "step_label": step_label,
            "status": st if done else "pending",
            "phase": phase,
            "assignee": assignee,
            "assignee_id": todo_row["assignee_id"],
            "todo_id": todo_row["id"],
            "decided_at": todo_row["created_at"] if done else None,
            "remark": (
                (todo_row["remark"] if "remark" in todo_row.keys() else None)
                or meta.get("decide_remark")
                or None
            ),
        }
    ]
    return {
        "application": None,
        "todo": todo_ser,
        "timeline": timeline,
        "forecast": [],
        "current_approver": None if done else assignee,
        "progress": "1/1",
        "progress_label": step_label,
    }
    # AI-GEN-END


def user_has_sensitive_accounts(db, user_id) -> bool:
    """用户任一可登录业务账号带敏感标记。"""
    # AI-GEN-BEGIN
    row = db.execute(
        """SELECT 1 FROM user_system_accounts
        WHERE user_id = ? AND has_sensitive = 1 AND can_login = 1 LIMIT 1""",
        (int(user_id),),
    ).fetchone()
    return bool(row)
    # AI-GEN-END


def apply_account_expire_extend(db, user_id, days=90):
    """延长 LEUC 账号有效期，返回新到期日。"""
    # AI-GEN-BEGIN
    days = int(days or 90)
    urow = db.execute(
        "SELECT account_expire FROM users WHERE id = ?", (int(user_id),)
    ).fetchone()
    if not urow:
        return {"ok": False, "error": "用户不存在"}
    base = datetime.now()
    if urow["account_expire"]:
        try:
            base = datetime.strptime(urow["account_expire"], "%Y-%m-%d")
        except ValueError:
            pass
    if base < datetime.now():
        base = datetime.now()
    new_expire = (base + timedelta(days=days)).strftime("%Y-%m-%d")
    db.execute(
        "UPDATE users SET account_expire = ? WHERE id = ?",
        (new_expire, int(user_id)),
    )
    return {"ok": True, "new_expire": new_expire, "days": days}
    # AI-GEN-END


def preview_apply_flow(
    db, *, apply_type, subject_id, system_id=None, with_sensitive=False, days=90
):
    """提交前流程预测（不落库）。"""
    # AI-GEN-BEGIN
    subject = db.execute(
        "SELECT * FROM users WHERE id = ?", (int(subject_id),)
    ).fetchone()
    if not subject:
        return {"ok": False, "error": "目标用户不存在"}
    steps = []
    flow_code = apply_type
    title = ""
    # AI-GEN-BEGIN
    if apply_type == "account_extend":
        has_sens = bool(with_sensitive) or user_has_sensitive_accounts(db, subject_id)
        if has_sens:
            steps = materialize_approval_chain(db, "sensitive", subject_id)
            flow_code = "account_extend_sensitive"
            title = f"账号延期 {days} 天（含敏感·长链）"
        else:
            direct = find_approver(db, subject_id)
            if not direct or int(direct) == int(subject_id):
                return {"ok": False, "error": "未找到直属审批人"}
            steps = [("direct_leader", "直属领导", int(direct))]
            flow_code = "account_extend"
            title = f"账号延期 {days} 天"
    elif apply_type in (
        "account_close",
        "account_perm_close",
        "account",
        "normal_perm",
        "system_access",
    ):
        # AI-GEN-END
        direct = find_approver(db, subject_id)
        if not direct or int(direct) == int(subject_id):
            return {"ok": False, "error": "未找到直属审批人"}
        steps = [("direct_leader", "直属领导", int(direct))]
        flow_code = apply_type
        title = {
            "account_close": "账号、权限关闭（关登录）",
            "account_perm_close": "账号、权限关闭",
        }.get(apply_type, "直属审批")
    elif apply_type in (
        "sensitive_close",
        "account_close_sensitive",
        "sensitive",
        "external",
    ):
        code = "external" if apply_type == "external" else "sensitive"
        steps = materialize_approval_chain(db, code, subject_id)
        flow_code = apply_type
        title = (
            "账号、权限关闭（敏感）"
            if apply_type in ("sensitive_close", "account_close_sensitive")
            else "敏感/外部审批链"
        )
    elif apply_type in ("account_apply", "account_apply_sensitive"):
        if not system_id:
            return {"ok": False, "error": "请先选择业务系统"}
        sys_row = db.execute(
            "SELECT * FROM systems WHERE id = ?", (int(system_id),)
        ).fetchone()
        if not sys_row:
            return {"ok": False, "error": "系统不存在"}
        use_sens = bool(with_sensitive) and int(sys_row["has_sensitive"] or 0)
        if use_sens:
            steps = materialize_approval_chain(db, "sensitive", subject_id)
            flow_code = "account_apply_sensitive"
            title = f"账号申请 · {sys_row['name']} · 含敏感"
        else:
            direct = find_approver(db, subject_id)
            if not direct or int(direct) == int(subject_id):
                return {"ok": False, "error": "未找到直属审批人"}
            steps = [("direct_leader", "直属领导", int(direct))]
            flow_code = "account_apply"
            title = f"账号申请 · {sys_row['name']}"
        steps = append_system_owner_step(db, int(system_id), steps)
    else:
        return {"ok": False, "error": f"未知类型: {apply_type}"}

    if not steps:
        return {"ok": False, "error": "审批链为空"}
    preview = []
    for i, (key, label, aid) in enumerate(steps, start=1):
        preview.append(
            {
                "step_order": i,
                "step_key": key,
                "step_label": label,
                "phase": "forecast" if i > 1 else "current",
                "status": "waiting" if i > 1 else "pending",
                "assignee": _user_brief(db, aid),
            }
        )
    return {
        "ok": True,
        "flow_code": flow_code,
        "title": title,
        "subject": _user_brief(db, subject_id),
        "timeline": preview,
        "forecast": [p for p in preview if p["phase"] == "forecast"],
        "current_approver": preview[0]["assignee"] if preview else None,
        "progress": f"1/{len(preview)}",
        "progress_label": preview[0]["step_label"] if preview else "",
        "chain_text": " → ".join(
            f"{p['step_label']}({(p['assignee'] or {}).get('display_name') or '?'})"
            for p in preview
        ),
    }
    # AI-GEN-END



def start_multi_step_apply(
    db, *, flow_code, todo_type, title, init_title, subject_id, initiator_id,
    system_id, steps, meta_extra=None, perm_id=None, cc_list=None,
):
    """创建多级审批申请单，返回 (app_id, first_todo, first_assignee, step_preview)。"""
    # AI-GEN-BEGIN
    now = now_ts()
    meta_extra = meta_extra or {}
    if not steps:
        return None, None, None, []
    # 标准化步骤并落 step_kind
    norm_steps = []
    for s in steps:
        if len(s) >= 5:
            norm_steps.append((s[0], s[1], s[2], s[3] or "approve", s[4]))
        elif len(s) == 4:
            norm_steps.append((s[0], s[1], s[2], s[3] or "approve", None))
        else:
            norm_steps.append((s[0], s[1], s[2], "approve", None))
    cur = db.execute(
        """INSERT INTO applications
        (flow_code, applicant_id, perm_def_id, system_id, title, status,
         current_step, total_steps, created_at, updated_at, provisioned)
        VALUES (?,?,?,?,?, 'pending', 1, ?, ?, ?, 0)""",
        (flow_code, subject_id, perm_id, system_id, title, len(norm_steps), now, now),
    )
    app_id = cur.lastrowid
    first_assignee = None
    first_todo = None
    step_preview = []
    first_is_system_owner = False
    for i, (step_key, step_label, assignee, step_kind, parallel_group) in enumerate(
        norm_steps, start=1
    ):
        status = "pending" if i == 1 else "waiting"
        todo_id = None
        if i == 1:
            tcur = db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
                 application_id, step_order, meta)
                VALUES (?,?,?,?, 'pending', 'open', ?, ?, ?, ?)""",
                (
                    assignee,
                    initiator_id,
                    f"{title} · {step_label}",
                    todo_type,
                    now,
                    app_id,
                    i,
                    json.dumps({**meta_extra, "step_label": step_label}, ensure_ascii=False),
                ),
            )
            todo_id = tcur.lastrowid
            first_assignee = assignee
            first_todo = todo_id
            first_is_system_owner = step_key == "system_owner"
        try:
            db.execute(
                """INSERT INTO application_steps
                (application_id, step_order, step_key, step_label, assignee_id, status, todo_id,
                 step_kind, parallel_group)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    app_id, i, step_key, step_label, assignee, status, todo_id,
                    step_kind, parallel_group,
                ),
            )
        except Exception:
            db.execute(
                """INSERT INTO application_steps
                (application_id, step_order, step_key, step_label, assignee_id, status, todo_id)
                VALUES (?,?,?,?,?,?,?)""",
                (app_id, i, step_key, step_label, assignee, status, todo_id),
            )
        au = db.execute(
            "SELECT display_name FROM users WHERE id = ?", (assignee,)
        ).fetchone()
        step_preview.append(
            {
                "order": i,
                "label": step_label,
                "assignee": au["display_name"] if au else assignee,
                "step_key": step_key,
                "step_kind": step_kind,
            }
        )
    # 首节点即系统管理员时同步知会
    if first_is_system_owner and cc_list:
        spawn_cc_todos(
            db,
            app_id=app_id,
            initiator_id=initiator_id,
            todo_type=todo_type,
            title=title,
            meta=meta_extra,
            ccs=cc_list,
            now=now,
        )
    elif cc_list:
        meta_extra = {**meta_extra, "pending_ccs": cc_list}
    db.execute(
        """INSERT INTO todos
        (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, application_id, meta)
        VALUES (?,?,?,?, 'initiated', 'open', ?, ?, ?)""",
        (
            first_assignee,
            initiator_id,
            init_title,
            todo_type,
            now,
            app_id,
            json.dumps({"steps": step_preview, **meta_extra}, ensure_ascii=False),
        ),
    )
    return app_id, first_todo, first_assignee, step_preview
    # AI-GEN-END


def auto_provision_sensitive(db, application):
    """审批通过后自动开通敏感权限（系统级标记，与权限目录无关）。"""
    # AI-GEN-BEGIN
    sid = application["system_id"]
    perm = None
    if application["perm_def_id"]:
        perm = db.execute(
            "SELECT * FROM sensitive_perm_defs WHERE id = ?", (application["perm_def_id"],)
        ).fetchone()
        if perm and not sid:
            sid = perm["system_id"]
    if not sid:
        return {"ok": False, "error": "缺少系统"}
    sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not sys_row:
        return {"ok": False, "error": "系统不存在"}
    uid = application["applicant_id"]
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    exists = db.execute(
        "SELECT * FROM user_system_accounts WHERE user_id = ? AND system_id = ? ORDER BY id LIMIT 1",
        (uid, sys_row["id"]),
    ).fetchone()
    summary = f"{perm['perm_name']}·敏感" if perm else "敏感权限"
    if exists:
        db.execute(
            """UPDATE user_system_accounts
            SET can_login = 1, has_sensitive = 1, perm_summary = ?
            WHERE id = ?""",
            (summary, exists["id"]),
        )
        account_id = exists["id"]
    else:
        cur = db.execute(
            """INSERT INTO user_system_accounts
            (user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
            VALUES (?,?,?,?,1,1,1,?)""",
            (
                uid,
                sys_row["id"],
                f"{(user['username'] if user else 'user')}_{sys_row['code']}_sens",
                "敏感开通",
                summary,
            ),
        )
        account_id = cur.lastrowid
    db.execute(
        "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
        (now_ts(), application["id"]),
    )
    owner_ids = list_system_owner_ids(db, sys_row["id"])
    notify_ids = owner_ids or ([sys_row["owner_user_id"]] if sys_row["owner_user_id"] else [])
    for oid in notify_ids:
        db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, application_id, meta)
            VALUES (?,?,?,?, 'done', 'auto_provisioned', ?, ?, ?)""",
            (
                oid,
                uid,
                f"【自动开通】{(user['display_name'] if user else uid)} · {sys_row['name']} · 敏感权限",
                "敏感开通",
                now_ts(),
                application["id"],
                json.dumps({"account_id": account_id, "auto": True}, ensure_ascii=False),
            ),
        )
    db.execute(
        """UPDATE todos SET status = 'approved', title = ?
        WHERE application_id = ? AND bucket = 'initiated'""",
        (
            f"敏感权限 · {sys_row['name']}（已自动开通）",
            application["id"],
        ),
    )
    return {
        "ok": True,
        "account_id": account_id,
        "system": sys_row["name"],
        "perm": perm["perm_name"] if perm else "敏感权限",
    }
    # AI-GEN-END


# AI-GEN-BEGIN
def auto_revoke_sensitive(db, application, account_id=None):
    """审批通过后关闭敏感权限标记。"""
    uid = application["applicant_id"]
    aid = account_id
    if not aid:
        exists = db.execute(
            """SELECT * FROM user_system_accounts
            WHERE user_id = ? AND system_id = ? AND has_sensitive = 1
            ORDER BY id LIMIT 1""",
            (uid, application["system_id"]),
        ).fetchone()
        if not exists:
            return {"ok": False, "error": "未找到可关闭的敏感账号"}
        aid = exists["id"]
    row = db.execute(
        "SELECT a.*, s.name AS system_name FROM user_system_accounts a JOIN systems s ON s.id=a.system_id WHERE a.id=?",
        (aid,),
    ).fetchone()
    if not row or int(row["user_id"]) != int(uid):
        return {"ok": False, "error": "账号不存在或不属于申请人"}
    summary = (row["perm_summary"] or "").replace("·敏感", "").replace("敏感", "").strip() or "普通权限"
    db.execute(
        """UPDATE user_system_accounts
        SET has_sensitive = 0, perm_summary = ? WHERE id = ?""",
        (summary, aid),
    )
    db.execute(
        "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
        (now_ts(), application["id"]),
    )
    db.execute(
        """UPDATE todos SET status = 'approved', title = ?
        WHERE application_id = ? AND bucket = 'initiated'""",
        (f"账号、权限关闭 · {row['system_name']}（已关闭）", application["id"]),
    )
    return {"ok": True, "account_id": aid, "system": row["system_name"]}


def close_user_system_account(db, user_id, account_id):
    """关闭指定系统账号登录；本系统（LEUC）同时关闭 users.status。"""
    # AI-GEN-BEGIN
    row = db.execute(
        """SELECT a.*, s.name AS system_name, s.code AS system_code FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id WHERE a.id = ?""",
        (account_id,),
    ).fetchone()
    if not row or int(row["user_id"]) != int(user_id):
        return {"ok": False, "error": "账号不存在或不属于该用户"}
    if (row["system_code"] or "") == LEUC_SYSTEM_CODE:
        return close_leuc_user(db, user_id)
    db.execute(
        "UPDATE user_system_accounts SET can_login = 0 WHERE id = ?", (account_id,)
    )
    db.execute(
        """UPDATE system_accounts SET status = 'closed'
        WHERE leuc_user_id = ? AND system_id = ? AND account_name = ?""",
        (user_id, row["system_id"], row["account_name"]),
    )
    return {
        "ok": True,
        "system": row["system_name"],
        "account": row["account_name"],
        "closed_leuc": False,
    }
    # AI-GEN-END


# AI-GEN-BEGIN
def _strip_perm_names_from_summary(summary, perm_names):
    """从 perm_summary 文本中尽量移除已关闭的权限名。"""
    text = (summary or "").strip()
    if not text or not perm_names:
        return text or "普通权限"
    for name in perm_names:
        if not name:
            continue
        text = text.replace(str(name), "")
    for sep in ("、", ",", "，", "/", "|", "·"):
        while sep + sep in text:
            text = text.replace(sep + sep, sep)
        text = text.strip(sep + " ")
    return text or "普通权限"


def execute_account_perm_close_items(db, application, meta=None):
    """审批通过后按明细行执行关闭账号 / 普通权限 / 敏感权限。"""
    meta = meta if isinstance(meta, dict) else {}
    uid = int(meta.get("leuc_user_id") or application["applicant_id"])
    items = meta.get("items") or []
    if not isinstance(items, list):
        items = []
    results = []
    errors = []
    if not items:
        # 兼容旧单行：仅 account_id + close_login / close_sensitive
        aid = meta.get("account_id")
        if meta.get("close_login") and aid:
            r = close_user_system_account(db, uid, int(aid))
            if r.get("ok"):
                results.append(f"关登录 {r['system']}/{r['account']}")
            else:
                errors.append(r.get("error") or "关闭登录失败")
        if meta.get("close_sensitive") and aid:
            r = auto_revoke_sensitive(db, application, account_id=int(aid))
            if r.get("ok"):
                results.append(f"关敏感 {r.get('system')}")
            else:
                errors.append(r.get("error") or "关闭敏感失败")
        if errors and not results:
            return {"ok": False, "error": errors[0], "results": results}
        db.execute(
            """UPDATE todos SET status = 'approved', title = ?
            WHERE application_id = ? AND bucket = 'initiated'""",
            (f"账号、权限关闭（已关闭）", application["id"]),
        )
        db.execute(
            "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
            (now_ts(), application["id"]),
        )
        return {
            "ok": True,
            "results": results,
            "summary": "；".join(results) if results else "已关闭",
            "error": "; ".join(errors) if errors else None,
        }

    for it in items:
        if not isinstance(it, dict):
            continue
        aid = it.get("account_id")
        if not aid:
            continue
        close_type = (it.get("close_type") or "").strip()
        if close_type == "account" or it.get("close_login"):
            r = close_user_system_account(db, uid, int(aid))
            if r.get("ok"):
                results.append(f"关登录 {r['system']}/{r['account']}")
            else:
                errors.append(r.get("error") or "关闭登录失败")
            continue
        # 关闭权限行
        if it.get("close_sensitive"):
            r = auto_revoke_sensitive(db, application, account_id=int(aid))
            if r.get("ok"):
                results.append(f"关敏感 {r.get('system')}")
            else:
                errors.append(r.get("error") or "关闭敏感失败")
        perm_names = it.get("perm_names") or []
        if perm_names:
            row = db.execute(
                "SELECT id, perm_summary FROM user_system_accounts WHERE id = ?",
                (int(aid),),
            ).fetchone()
            if row:
                new_summary = _strip_perm_names_from_summary(row["perm_summary"], perm_names)
                db.execute(
                    "UPDATE user_system_accounts SET perm_summary = ? WHERE id = ?",
                    (new_summary, int(aid)),
                )
            results.append("关权限 " + "、".join(str(x) for x in perm_names if x))
    # 更新发起待办标题
    db.execute(
        """UPDATE todos SET status = 'approved', title = ?
        WHERE application_id = ? AND bucket = 'initiated'""",
        (f"账号、权限关闭（已关闭·{len(results)}项）", application["id"]),
    )
    db.execute(
        "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
        (now_ts(), application["id"]),
    )
    if errors and not results:
        return {"ok": False, "error": errors[0], "results": results}
    return {
        "ok": True,
        "results": results,
        "error": "; ".join(errors) if errors else None,
        "summary": "；".join(results) if results else "已处理",
    }
# AI-GEN-END


# AI-GEN-BEGIN
def ensure_user_leuc_account(db, user_row):
    """确保用户有本系统（LEUC）登录账号行，便于自助关闭与离职统一处理。"""
    if not user_row:
        return None
    sys = db.execute(
        "SELECT id, name, code FROM systems WHERE code = ?", (LEUC_SYSTEM_CODE,)
    ).fetchone()
    if not sys:
        return None
    uid = int(user_row["id"])
    existing = db.execute(
        """SELECT * FROM user_system_accounts
        WHERE user_id = ? AND system_id = ? ORDER BY id LIMIT 1""",
        (uid, sys["id"]),
    ).fetchone()
    keys = user_row.keys()
    status = (user_row["status"] if "status" in keys else "active") or "active"
    can_login = 0 if status == "closed" else 1
    uname = user_row["username"]
    if existing:
        if int(existing["can_login"] or 0) != can_login:
            db.execute(
                "UPDATE user_system_accounts SET can_login = ? WHERE id = ?",
                (can_login, existing["id"]),
            )
            existing = db.execute(
                "SELECT * FROM user_system_accounts WHERE id = ?", (existing["id"],)
            ).fetchone()
        return existing
    db.execute(
        """INSERT INTO user_system_accounts
        (user_id, system_id, account_name, account_label, can_login, has_sensitive,
         perm_summary, is_default)
        VALUES (?,?,?,?,?,?,?,1)""",
        (
            uid,
            sys["id"],
            uname,
            "本系统登录",
            can_login,
            0,
            "LEUC 本系统登录",
        ),
    )
    return db.execute(
        """SELECT * FROM user_system_accounts
        WHERE user_id = ? AND system_id = ? ORDER BY id DESC LIMIT 1""",
        (uid, sys["id"]),
    ).fetchone()


def close_leuc_user(db, user_id):
    """关闭本系统账号：users.status=closed，并关掉 leuc 登录账号行。"""
    urow = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not urow:
        return {"ok": False, "error": "用户不存在"}
    ensure_user_leuc_account(db, urow)
    db.execute(
        "UPDATE users SET status = 'closed' WHERE id = ?", (int(user_id),)
    )
    sys = db.execute(
        "SELECT id, name FROM systems WHERE code = ?", (LEUC_SYSTEM_CODE,)
    ).fetchone()
    acct_name = urow["username"]
    if sys:
        db.execute(
            "UPDATE user_system_accounts SET can_login = 0 WHERE user_id = ? AND system_id = ?",
            (int(user_id), sys["id"]),
        )
        row = db.execute(
            """SELECT account_name FROM user_system_accounts
            WHERE user_id = ? AND system_id = ? ORDER BY id LIMIT 1""",
            (int(user_id), sys["id"]),
        ).fetchone()
        if row:
            acct_name = row["account_name"]
    return {
        "ok": True,
        "system": (sys["name"] if sys else "本系统（LEUC）"),
        "account": acct_name,
        "closed_leuc": True,
    }


def reopen_leuc_user(db, user_id):
    """重新打开本系统账号：users.status=active，恢复 leuc 可登录。"""
    # AI-GEN-BEGIN
    urow = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not urow:
        return {"ok": False, "error": "用户不存在"}
    ensure_user_leuc_account(db, urow)
    db.execute(
        "UPDATE users SET status = 'active' WHERE id = ?", (int(user_id),)
    )
    sys = db.execute(
        "SELECT id FROM systems WHERE code = ?", (LEUC_SYSTEM_CODE,)
    ).fetchone()
    if sys:
        db.execute(
            "UPDATE user_system_accounts SET can_login = 1 WHERE user_id = ? AND system_id = ?",
            (int(user_id), sys["id"]),
        )
    return {"ok": True, "reopened_leuc": True}
    # AI-GEN-END


def user_is_closed(row) -> bool:
    if not row:
        return True
    keys = row.keys()
    if "status" not in keys:
        return False
    return (row["status"] or "active") == "closed"


def _notify_subsystem_account_close(
    db,
    *,
    system_row,
    user_id: int,
    account_name: str,
    pool_account_id=None,
    account_uid: str | None = None,
    reason: str,
    closed_at: str,
) -> dict:
    """通知子系统关闭账号：有 close_api_url 则 HTTP 回调，否则写入本地模拟回执。"""
    # AI-GEN-BEGIN
    keys = system_row.keys() if hasattr(system_row, "keys") else []
    sid = int(system_row["system_id"] if "system_id" in keys else system_row["id"])
    scode = (
        system_row["system_code"]
        if "system_code" in keys
        else (system_row["code"] if "code" in keys else "")
    ) or ""
    sname = (
        system_row["system_name"]
        if "system_name" in keys
        else (system_row["name"] if "name" in keys else "")
    ) or ""
    payload = {
        "event": "account.close",
        "reason": reason,
        "system_id": sid,
        "system_code": scode,
        "system_name": sname,
        "leuc_user_id": int(user_id),
        "account_name": account_name,
        "account_uid": account_uid,
        "pool_account_id": pool_account_id,
        "closed_at": closed_at,
    }
    # 本系统：无需远程
    if scode == LEUC_SYSTEM_CODE:
        return {
            "remote_status": "local_only",
            "remote_http_status": None,
            "remote_message": "本系统本地关闭",
        }
    close_url = None
    if "close_api_url" in keys:
        close_url = (system_row["close_api_url"] or "").strip() or None
    client_id = system_row["client_id"] if "client_id" in keys else ""

    def _write_inbox(msg: str):
        db.execute(
            """INSERT INTO subsystem_close_inbox
            (system_id, system_code, account_name, account_uid, leuc_user_id,
             reason, payload_json, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                sid,
                scode,
                account_name,
                account_uid,
                int(user_id),
                reason,
                json.dumps(payload, ensure_ascii=False),
                closed_at,
            ),
        )
        return msg

    if not close_url:
        # 原型：未配置回调时本地模拟「子系统侧记录」
        _write_inbox("simulated")
        return {
            "remote_status": "simulated",
            "remote_http_status": None,
            "remote_message": "未配置 close_api_url，已写入本地子系统关闭回执",
        }

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Leuc-System-Code": scode,
            "X-Leuc-Client-Id": client_id or "",
        }
        # 同源相对路径：转为本服务绝对地址
        if close_url.startswith("/"):
            base = "http://127.0.0.1:5055"
            try:
                if request and getattr(request, "host_url", None):
                    base = request.host_url.rstrip("/")
            except Exception:
                pass
            close_url = base + close_url
        req = Request(close_url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", None) or 200
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {"raw": raw[:300]}
        ok = bool(data.get("ok", True)) if isinstance(data, dict) else True
        return {
            "remote_status": "success" if ok else "failed",
            "remote_http_status": int(code),
            "remote_message": (
                (data.get("message") if isinstance(data, dict) else None)
                or raw[:200]
                or "ok"
            ),
        }
    except Exception as ex:
        _write_inbox(f"http_error:{ex}")
        return {
            "remote_status": "failed",
            "remote_http_status": None,
            "remote_message": f"回调失败：{ex}",
        }
    # AI-GEN-END


def close_user_for_leave(
    db,
    user_id: int,
    *,
    source: str = "leorg_incr",
    reason: str = "LeOrg 在职转离职",
    sync_run_id: int | None = None,
    leorg_emp: dict | None = None,
) -> dict:
    """离职立即关闭：本系统 + 全部绑定业务账号，并写独立关闭记录 / 通知子系统。"""
    # AI-GEN-BEGIN
    from leuc_ops import ensure_ops_tables

    ensure_ops_tables(db)
    uid = int(user_id)
    urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not urow:
        return {"ok": False, "error": "用户不存在"}
    # 幂等：已关闭且已有离职记录则直接返回最近一条
    if user_is_closed(urow):
        last = db.execute(
            """SELECT id FROM leave_close_records
            WHERE user_id = ? ORDER BY id DESC LIMIT 1""",
            (uid,),
        ).fetchone()
        if last:
            return {
                "ok": True,
                "already_closed": True,
                "record_id": int(last["id"]),
                "closed_count": 0,
            }

    try:
        now = now_ts()
    except Exception:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    leuc_r = close_leuc_user(db, uid)
    if not leuc_r.get("ok"):
        return leuc_r

    accts = db.execute(
        """SELECT a.*, s.code AS system_code, s.name AS system_name,
                  s.client_id, s.close_api_url, s.is_builtin
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ?
        ORDER BY a.id""",
        (uid,),
    ).fetchall()

    items_out = []
    closed_names = []
    for a in accts:
        sid = int(a["system_id"])
        aname = a["account_name"]
        # 本地关闭（LEUC 已在 close_leuc_user 处理）
        local_status = "closed"
        if (a["system_code"] or "") != LEUC_SYSTEM_CODE:
            db.execute(
                "UPDATE user_system_accounts SET can_login = 0 WHERE id = ?",
                (int(a["id"]),),
            )
            db.execute(
                """UPDATE system_accounts SET status = 'closed'
                WHERE leuc_user_id = ? AND system_id = ? AND account_name = ?""",
                (uid, sid, aname),
            )
        pool = db.execute(
            """SELECT id, account_uid FROM system_accounts
            WHERE system_id = ? AND account_name = ? LIMIT 1""",
            (sid, aname),
        ).fetchone()
        pool_id = int(pool["id"]) if pool else None
        account_uid = (pool["account_uid"] if pool and "account_uid" in pool.keys() else None)
        remote = _notify_subsystem_account_close(
            db,
            system_row=a,
            user_id=uid,
            account_name=aname,
            pool_account_id=pool_id,
            account_uid=account_uid,
            reason=reason,
            closed_at=now,
        )
        label = f"{a['system_name']}/{aname}"
        closed_names.append(label)
        items_out.append(
            {
                "system_id": sid,
                "system_code": a["system_code"],
                "system_name": a["system_name"],
                "account_id": int(a["id"]),
                "pool_account_id": pool_id,
                "account_name": aname,
                "local_status": local_status,
                "remote_status": remote.get("remote_status"),
                "remote_http_status": remote.get("remote_http_status"),
                "remote_message": remote.get("remote_message"),
            }
        )

    # 无绑定业务账号时也保证有本系统一行
    if not items_out:
        items_out.append(
            {
                "system_id": None,
                "system_code": LEUC_SYSTEM_CODE,
                "system_name": leuc_r.get("system"),
                "account_id": None,
                "pool_account_id": None,
                "account_name": leuc_r.get("account"),
                "local_status": "closed",
                "remote_status": "local_only",
                "remote_http_status": None,
                "remote_message": "本系统本地关闭",
            }
        )

    leorg_emp_id = None
    beisen_user_id = urow["beisen_user_id"] if "beisen_user_id" in urow.keys() else None
    if "leorg_emp_id" in urow.keys() and urow["leorg_emp_id"] is not None:
        leorg_emp_id = int(urow["leorg_emp_id"])
    if leorg_emp and isinstance(leorg_emp, dict):
        if leorg_emp.get("id") is not None:
            leorg_emp_id = int(leorg_emp["id"])
        bid = (
            leorg_emp.get("beisen_id")
            or leorg_emp.get("beisenId")
            or leorg_emp.get("beisen_user_id")
        )
        if bid:
            beisen_user_id = str(bid).strip()

    summary = (
        f"关闭 {len(items_out)} 个账号："
        + "；".join(closed_names[:8])
        + ("…" if len(closed_names) > 8 else "")
    )
    cur = db.execute(
        """INSERT INTO leave_close_records
        (user_id, username, display_name, leorg_emp_id, beisen_user_id,
         source, reason, sync_run_id, closed_at, summary, detail_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid,
            urow["username"],
            urow["display_name"],
            leorg_emp_id,
            beisen_user_id,
            source,
            reason,
            sync_run_id,
            now,
            summary,
            json.dumps(
                {"leorg_emp": leorg_emp, "item_count": len(items_out)},
                ensure_ascii=False,
                default=str,
            ),
        ),
    )
    rid = int(cur.lastrowid)
    for it in items_out:
        db.execute(
            """INSERT INTO leave_close_items
            (record_id, system_id, system_code, system_name, account_id,
             pool_account_id, account_name, local_status, remote_status,
             remote_http_status, remote_message, closed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                it.get("system_id"),
                it.get("system_code"),
                it.get("system_name"),
                it.get("account_id"),
                it.get("pool_account_id"),
                it.get("account_name"),
                it.get("local_status"),
                it.get("remote_status"),
                it.get("remote_http_status"),
                it.get("remote_message"),
                now,
            ),
        )

    write_audit_log(
        db,
        action="leave.close",
        actor_user_id=None,
        actor_name=source,
        target_type="user",
        target_id=str(uid),
        detail={
            "record_id": rid,
            "reason": reason,
            "summary": summary,
            "sync_run_id": sync_run_id,
        },
    )
    try:
        push_system_message(
            db,
            uid,
            "离职关账已执行",
            f"因「{reason}」，已关闭本系统及关联业务账号（共 {len(items_out)} 个）。",
        )
    except Exception:
        pass
    return {
        "ok": True,
        "record_id": rid,
        "user_id": uid,
        "closed_count": len(items_out),
        "summary": summary,
        "items": items_out,
    }
    # AI-GEN-END
# AI-GEN-END


def get_risk(username: str):
    db = get_db()
    row = db.execute("SELECT * FROM login_risk WHERE username = ?", (username,)).fetchone()
    fail = int(row["fail_count"]) if row else 0
    return {
        "fail_count": fail,
        "need_captcha": fail >= CAPTCHA_THRESHOLD,
        "need_verify": fail >= FAIL_VERIFY_THRESHOLD,
    }


def set_risk(username: str, fail_count: int):
    db = get_db()
    db.execute(
        """INSERT INTO login_risk (username, fail_count, updated_at)
        VALUES (?,?,?)
        ON CONFLICT(username) DO UPDATE SET
          fail_count=excluded.fail_count,
          updated_at=excluded.updated_at""",
        (username, fail_count, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()


# AI-GEN-BEGIN
def _new_captcha_code(length: int = 4) -> str:
    return "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(length))


def _captcha_svg(code: str) -> str:
    """生成简易图片验证码 SVG。"""
    w, h = 140, 44
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'<rect width="100%" height="100%" rx="8" fill="#F2F6FD"/>',
    ]
    for _ in range(5):
        x1, y1 = secrets.randbelow(w), secrets.randbelow(h)
        x2, y2 = secrets.randbelow(w), secrets.randbelow(h)
        color = secrets.choice(["#AFC3EE", "#7C9AD6", "#C5D4F0"])
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.2"/>'
        )
    for i, ch in enumerate(code):
        x = 18 + i * 28 + secrets.randbelow(4)
        y = 28 + secrets.randbelow(6)
        rot = secrets.randbelow(24) - 12
        fill = secrets.choice(["#101838", "#1677E0", "#1E3A7A", "#2E5AAC"])
        parts.append(
            f'<text x="{x}" y="{y}" fill="{fill}" font-size="22" font-family="monospace" '
            f'font-weight="700" transform="rotate({rot} {x} {y})">{ch}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def check_login_captcha(username: str, captcha_input: str) -> tuple[bool, str]:
    """校验图片验证码（不区分大小写）；用过后作废。"""
    expected = (session.get("login_captcha") or "").strip().upper()
    bound_user = (session.get("login_captcha_user") or "").strip()
    got = (captcha_input or "").strip().upper()
    # 用过后即清，防重放
    session.pop("login_captcha", None)
    session.pop("login_captcha_user", None)
    if not expected:
        return False, "请先获取图片验证码"
    if bound_user and username and bound_user != username:
        return False, "验证码已失效，请刷新后重试"
    if not got or got != expected:
        return False, "图片验证码错误"
    return True, ""
# AI-GEN-END


def user_accounts_for_system(user_id: int, system_code: str, only_loginable: bool = True):
    db = get_db()
    sys_row = db.execute(
        "SELECT * FROM systems WHERE code = ? AND status = 'enabled'", (system_code,)
    ).fetchone()
    if not sys_row:
        return []
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return []
    ok, _err = user_may_access_system(db, user, sys_row)
    if not ok:
        return []
    sql = """SELECT a.*, s.code AS system_code, s.name AS system_name
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ? AND s.code = ? AND s.status = 'enabled'"""
    if only_loginable:
        sql += " AND a.can_login = 1"
    sql += " ORDER BY a.is_default DESC, a.id"
    rows = [dict(r) for r in db.execute(sql, (user_id, system_code)).fetchall()]
    # 全员登录：无业务账号时自动开通一个默认账号（外部人员除外若系统禁外部）
    if not rows and (sys_row["access_mode"] if "access_mode" in sys_row.keys() else "apply") == "open":
        acct_name = f"{user['username']}_{system_code}"
        cur = db.execute(
            """INSERT INTO user_system_accounts
            (user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
            VALUES (?,?,?,?,1,1,0,?)""",
            (user_id, sys_row["id"], acct_name, "全员登录自动开通", "普通权限"),
        )
        db.commit()
        rows = user_accounts_for_system(user_id, system_code, only_loginable)
    return rows


def my_systems(user_id: int):
    db = get_db()
    systems = db.execute(
        """SELECT DISTINCT s.*
        FROM systems s
        JOIN user_system_accounts a ON a.system_id = s.id
        WHERE a.user_id = ? AND s.status = 'enabled'
        ORDER BY s.id""",
        (user_id,),
    ).fetchall()
    out = []
    for s in systems:
        accts = db.execute(
            """SELECT id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary
            FROM user_system_accounts
            WHERE user_id = ? AND system_id = ?
            ORDER BY is_default DESC, id""",
            (user_id, s["id"]),
        ).fetchall()
        out.append(
            {
                "code": s["code"],
                "name": s["name"],
                "client_id": s["client_id"],
                "account_count": len(accts),
                "can_login_any": any(a["can_login"] for a in accts),
                "has_sensitive_any": any(a["has_sensitive"] for a in accts),
                "accounts": [dict(a) for a in accts],
            }
        )
    return out


def member_row_enriched(m):
    systems = my_systems(m["id"])
    keys = m.keys()
    db = get_db()
    status = (m["status"] if "status" in keys else "active") or "active"
    return {
        "id": m["id"],
        "username": m["username"],
        "display_name": m["display_name"],
        "role": m["role"],
        "role_label": role_label_of(db, m["role"]),
        "dept_id": m["dept_id"],
        "phone": m["phone"] if "phone" in keys else None,
        "email": m["email"] if "email" in keys else None,
        "itcode": m["itcode"] if "itcode" in keys else None,
        "beisen_user_id": (m["beisen_user_id"] if "beisen_user_id" in keys else None) or None,
        "account_expire": m["account_expire"] if "account_expire" in keys else None,
        "person_type": m["person_type"] if "person_type" in keys else "internal",
        "status": status,
        "systems": systems,
        "can_login_any": any(s["can_login_any"] for s in systems),
        "has_sensitive": any(s["has_sensitive_any"] for s in systems),
    }


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/demo-users")
def demo_users():
    # AI-GEN-BEGIN
    # 快速切换：固定顺序；超管按钮文案为「超级管理员」
    order = [
        "xuhaohao",
        "gaojia",
        "maning",
        "wujinzhi",
        "liyang",
        "changmingming",
        "admin",
    ]
    button_labels = {
        "admin": "超级管理员",
    }
    rows = get_db().execute(
        f"""SELECT username, display_name, role, password FROM users
        WHERE username IN ({",".join("?" * len(order))})""",
        order,
    ).fetchall()
    by_user = {r["username"]: r for r in rows}
    users = []
    for uname in order:
        r = by_user.get(uname)
        if not r:
            continue
        users.append(
            {
                "username": r["username"],
                "display_name": r["display_name"],
                "role": r["role"],
                "role_label": role_label_of(get_db(), r["role"]),
                "button_label": button_labels.get(uname, r["display_name"]),
                "password": r["password"],
            }
        )
    return jsonify({"ok": True, "users": users})
    # AI-GEN-END


# AI-GEN-BEGIN
@app.get("/api/demo/org-pick")
def demo_org_pick():
    """演示切换：按部门筛选任意人（免登录，仅原型）。含 admin。"""
    db = get_db()
    depts = all_departments(db)
    q = (request.args.get("q") or "").strip()
    dept_id = request.args.get("dept_id")
    focus_id = int(dept_id) if dept_id else None

    sql = "SELECT * FROM users WHERE 1=1"
    params: list = []
    if focus_id:
        ids = subtree_ids(depts, focus_id)
        if not ids:
            members = []
        else:
            sql += f" AND dept_id IN ({','.join('?' * len(ids))})"
            params.extend(ids)
            if q:
                like = f"%{q}%"
                sql += " AND (display_name LIKE ? OR username LIKE ? OR phone LIKE ? OR email LIKE ?)"
                params.extend([like, like, like, like])
            sql += " ORDER BY dept_id, id"
            members = list(db.execute(sql, params).fetchall())
    else:
        if q:
            like = f"%{q}%"
            sql += " AND (display_name LIKE ? OR username LIKE ? OR phone LIKE ? OR email LIKE ?)"
            params.extend([like, like, like, like])
        sql += " ORDER BY CASE WHEN username = ? THEN 0 ELSE 1 END, dept_id, id"
        params.append(SYSTEM_ADMIN_USERNAME)
        members = list(db.execute(sql, params).fetchall())

    out = [member_row_enriched(m) for m in members]
    # 未筛选时人数过多，截断以免弹窗卡顿
    truncated = False
    if not focus_id and not q and len(out) > 80:
        out = out[:80]
        truncated = True
    return jsonify({
        "ok": True,
        "departments": depts,
        "tree": build_org_tree(depts),
        "members": out,
        "focus_dept_id": focus_id,
        "truncated": truncated,
        "hint": "未选部门时仅显示前 80 人，请用左侧部门或搜索缩小范围" if truncated else None,
    })
# AI-GEN-END


# AI-GEN-BEGIN
def _can_config_roles(user) -> bool:
    return user_has_cap(user, "config_roles") or user_has_role(user, "super_admin")


def _can_view_roles(user) -> bool:
    """查看角色配置 / 人员绑定：配置或分配权限均可。"""
    return (
        _can_config_roles(user)
        or user_has_cap(user, "role_assign")
        or user_has_role(user, "super_admin")
    )


def _can_assign_roles(user) -> bool:
    return (
        user_has_cap(user, "role_assign")
        or user_has_cap(user, "config_roles")
        or user_has_role(user, "super_admin")
    )


def _slug_role_code(label: str) -> str:
    """从中文名生成角色 code；失败则用时间戳。"""
    raw = (name_to_pinyin(label) or "").strip().lower()
    raw = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    raw = "_".join(p for p in raw.split("_") if p)
    if not raw:
        raw = "role"
    if raw[0].isdigit():
        raw = "r_" + raw
    return raw[:40]


@app.get("/api/admin/roles")
@login_required
def admin_roles_get(user):
    """角色列表 + 菜单/能力配置（超管 / config_roles / role_assign 可看）。"""
    if not _can_view_roles(user):
        return jsonify({"ok": False, "error": "无权查看角色"}), 403
    db = get_db()
    ensure_roles_seeded(db)
    ensure_user_roles_migrated(db)
    db.commit()
    roles = []
    for row in db.execute(
        "SELECT code, label, is_builtin, sort_order FROM roles ORDER BY sort_order, code"
    ).fetchall():
        role = row["code"]
        if role in ("employee_a", "employee_b"):
            continue
        menus = [r["menu_id"] for r in db.execute(
            "SELECT menu_id FROM role_menus WHERE role=? ORDER BY menu_id", (role,)
        ).fetchall()]
        caps = [r["cap_id"] for r in db.execute(
            "SELECT cap_id FROM role_caps WHERE role=? ORDER BY cap_id", (role,)
        ).fetchall()]
        if not menus:
            menus = list(DEFAULT_ROLE_MENUS.get(role, DEFAULT_ROLE_MENUS.get("employee", [])))
        if not caps and role in DEFAULT_ROLE_CAPS:
            caps = list(DEFAULT_ROLE_CAPS.get(role, []))
        n = db.execute(
            "SELECT COUNT(*) AS c FROM user_roles WHERE role=?", (role,)
        ).fetchone()["c"]
        roles.append({
            "role": role,
            "label": row["label"],
            "is_builtin": bool(row["is_builtin"]),
            "menus": menus,
            "caps": caps,
            "user_count": n,
        })
    return jsonify({
        "ok": True,
        "roles": roles,
        "all_menus": ALL_MENUS,
        "all_buttons": ALL_BUTTONS,
        "all_caps": ALL_CAPS,  # 兼容旧前端
        "can_config": _can_config_roles(user),
        "can_assign": _can_assign_roles(user),
    })


@app.get("/api/admin/roles/<role>/members")
@login_required
def admin_role_members(user, role):
    """某角色下的人员列表（可搜）。"""
    # AI-GEN-BEGIN
    if not _can_view_roles(user):
        return jsonify({"ok": False, "error": "无权查看"}), 403
    db = get_db()
    ensure_roles_seeded(db)
    ensure_user_roles_migrated(db)
    if role in ("employee_a", "employee_b") or not db.execute(
        "SELECT 1 FROM roles WHERE code=?", (role,)
    ).fetchone():
        return jsonify({"ok": False, "error": "无效角色"}), 400
    q = (request.args.get("q") or "").strip()
    sql = """SELECT u.id, u.username, u.display_name, u.role, u.dept_id, u.phone, u.email,
                    d.name AS dept_name
             FROM user_roles ur
             JOIN users u ON u.id = ur.user_id
             LEFT JOIN departments d ON d.id = u.dept_id
             WHERE ur.role = ?"""
    params: list = [role]
    if q:
        like = f"%{q}%"
        sql += " AND (u.display_name LIKE ? OR u.username LIKE ? OR u.phone LIKE ? OR u.email LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY u.display_name, u.id LIMIT 200"
    rows = db.execute(sql, params).fetchall()
    members = []
    for r in rows:
        rlist = user_roles_of(db, r["id"])
        members.append(
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "role": r["role"],
                "roles": rlist,
                "role_labels": [{"code": c, "label": role_label_of(db, c)} for c in rlist],
                "role_label": role_label_of(db, r["role"]),
                "dept_id": r["dept_id"],
                "dept_name": r["dept_name"],
                "phone": r["phone"],
                "email": r["email"],
                "is_system_admin": (r["username"] or "") == SYSTEM_ADMIN_USERNAME,
            }
        )
    return jsonify(
        {
            "ok": True,
            "role": role,
            "role_label": role_label_of(db, role),
            "total": len(members),
            "members": members,
            "truncated": len(members) >= 200,
            "can_assign": _can_assign_roles(user),
        }
    )
    # AI-GEN-END


@app.get("/api/admin/role-users")
@login_required
def admin_role_users(user):
    """有本系统角色的用户列表（不含仅普通员工）。"""
    # AI-GEN-BEGIN
    if not _can_view_roles(user):
        return jsonify({"ok": False, "error": "无权查看"}), 403
    db = get_db()
    ensure_roles_seeded(db)
    ensure_user_roles_migrated(db)
    q = (request.args.get("q") or "").strip()
    excl = tuple(SYSTEM_ROLE_EXCLUDE)
    placeholders = ",".join("?" * len(excl))
    sql = f"""SELECT DISTINCT u.id, u.username, u.display_name, u.role, u.dept_id, u.phone, u.email,
                     d.name AS dept_name
              FROM user_roles ur
              JOIN users u ON u.id = ur.user_id
              LEFT JOIN departments d ON d.id = u.dept_id
              WHERE ur.role NOT IN ({placeholders})"""
    params: list = list(excl)
    if q:
        like = f"%{q}%"
        sql += " AND (u.display_name LIKE ? OR u.username LIKE ? OR u.phone LIKE ? OR u.email LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY u.display_name, u.id LIMIT 300"
    rows = db.execute(sql, params).fetchall()
    users = []
    for r in rows:
        rlist = [c for c in user_roles_of(db, r["id"]) if c not in SYSTEM_ROLE_EXCLUDE]
        if not rlist:
            continue
        users.append(
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "role": r["role"],
                "roles": user_roles_of(db, r["id"]),
                "system_roles": rlist,
                "role_labels": [
                    {"code": c, "label": role_label_of(db, c)}
                    for c in user_roles_of(db, r["id"])
                ],
                "dept_id": r["dept_id"],
                "dept_name": r["dept_name"],
                "phone": r["phone"],
                "email": r["email"],
                "is_system_admin": (r["username"] or "") == SYSTEM_ADMIN_USERNAME,
            }
        )
    return jsonify(
        {
            "ok": True,
            "total": len(users),
            "users": users,
            "truncated": len(users) >= 300,
            "can_assign": _can_assign_roles(user),
        }
    )
    # AI-GEN-END


def _count_super_admins(db) -> int:
    return db.execute(
        "SELECT COUNT(DISTINCT user_id) AS c FROM user_roles WHERE role='super_admin'"
    ).fetchone()["c"]


def _set_user_roles(db, uid, roles, *, allow_empty_to_employee=True):
    """全量设置用户角色；返回最终 role 列表。"""
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise ValueError("用户不存在")
    if (row["username"] or "") == SYSTEM_ADMIN_USERNAME:
        roles = ["super_admin"]
    cleaned = []
    for r in roles or []:
        r = (r or "").strip()
        if not r or r in ("employee_a", "employee_b"):
            continue
        if not db.execute("SELECT 1 FROM roles WHERE code=?", (r,)).fetchone():
            raise ValueError(f"无效角色: {r}")
        if r not in cleaned:
            cleaned.append(r)
    # 去掉员工类，最后若空补 employee
    cleaned = [c for c in cleaned if c not in EMPLOYEE_ROLE_CODES]
    had_super = "super_admin" in user_roles_of(db, uid)
    if had_super and "super_admin" not in cleaned:
        if _count_super_admins(db) <= 1:
            raise ValueError("至少保留一名超级管理员")
    db.execute("DELETE FROM user_roles WHERE user_id=?", (uid,))
    if cleaned:
        db.executemany(
            "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?,?)",
            [(uid, c) for c in cleaned],
        )
    elif allow_empty_to_employee:
        db.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, 'employee')",
            (uid,),
        )
    sync_primary_role(db, uid)
    return user_roles_of(db, uid)


@app.get("/api/admin/users/<int:uid>/roles")
@login_required
def admin_user_roles_get(user, uid):
    # AI-GEN-BEGIN
    if not _can_view_roles(user):
        return jsonify({"ok": False, "error": "无权查看"}), 403
    db = get_db()
    ensure_user_roles_migrated(db)
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    roles = user_roles_of(db, uid)
    return jsonify(
        {
            "ok": True,
            "user_id": uid,
            "display_name": row["display_name"],
            "username": row["username"],
            "role": row["role"],
            "roles": roles,
            "role_labels": [{"code": c, "label": role_label_of(db, c)} for c in roles],
            "can_assign": _can_assign_roles(user),
        }
    )
    # AI-GEN-END


@app.put("/api/admin/users/<int:uid>/roles")
@login_required
def admin_user_roles_put(user, uid):
    """全量覆盖用户角色绑定。"""
    # AI-GEN-BEGIN
    if not _can_assign_roles(user):
        return jsonify({"ok": False, "error": "无权分配角色"}), 403
    data = request.get_json(force=True) or {}
    roles = data.get("roles")
    if roles is None:
        return jsonify({"ok": False, "error": "请传 roles 数组"}), 400
    db = get_db()
    ensure_roles_seeded(db)
    ensure_user_roles_migrated(db)
    try:
        final = _set_user_roles(db, uid, list(roles))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    db.commit()
    row = db.execute(
        "SELECT display_name, role FROM users WHERE id=?", (uid,)
    ).fetchone()
    return jsonify(
        {
            "ok": True,
            "message": f"已更新 {row['display_name']} 的角色",
            "user_id": uid,
            "role": row["role"],
            "roles": final,
            "role_labels": [{"code": c, "label": role_label_of(db, c)} for c in final],
        }
    )
    # AI-GEN-END


@app.post("/api/admin/users/<int:uid>/roles")
@login_required
def admin_user_roles_add(user, uid):
    """追加一个角色。"""
    # AI-GEN-BEGIN
    if not _can_assign_roles(user):
        return jsonify({"ok": False, "error": "无权分配角色"}), 403
    data = request.get_json(force=True) or {}
    role = (data.get("role") or "").strip()
    db = get_db()
    ensure_roles_seeded(db)
    ensure_user_roles_migrated(db)
    cur = [c for c in user_roles_of(db, uid) if c not in EMPLOYEE_ROLE_CODES]
    if role and role not in cur:
        cur.append(role)
    try:
        final = _set_user_roles(db, uid, cur)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    db.commit()
    row = db.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
    return jsonify(
        {
            "ok": True,
            "message": f"已为 {row['display_name']} 绑定 {role_label_of(db, role)}",
            "roles": final,
        }
    )
    # AI-GEN-END


@app.delete("/api/admin/users/<int:uid>/roles/<role>")
@login_required
def admin_user_roles_remove(user, uid, role):
    """去掉用户的某一角色。"""
    # AI-GEN-BEGIN
    if not _can_assign_roles(user):
        return jsonify({"ok": False, "error": "无权解除角色绑定"}), 403
    db = get_db()
    ensure_user_roles_migrated(db)
    cur = [c for c in user_roles_of(db, uid) if c != role and c not in EMPLOYEE_ROLE_CODES]
    try:
        final = _set_user_roles(db, uid, cur)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    db.commit()
    row = db.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
    return jsonify(
        {
            "ok": True,
            "message": f"已解除 {row['display_name']} 的「{role_label_of(db, role)}」",
            "roles": final,
        }
    )
    # AI-GEN-END


@app.post("/api/admin/roles")
@login_required
def admin_roles_create(user):
    """新增自定义角色。"""
    if not _can_config_roles(user):
        return jsonify({"ok": False, "error": "无权配置角色"}), 403
    data = request.get_json(force=True) or {}
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "请填写角色名称"}), 400
    if len(label) > 40:
        return jsonify({"ok": False, "error": "角色名称过长"}), 400
    db = get_db()
    ensure_roles_seeded(db)
    code = (data.get("code") or "").strip() or _slug_role_code(label)
    code = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in code.lower())
    code = "_".join(p for p in code.split("_") if p)[:40] or "role"
    if db.execute("SELECT 1 FROM roles WHERE code=?", (code,)).fetchone():
        code = f"{code}_{secrets.token_hex(2)}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sort = db.execute("SELECT COALESCE(MAX(sort_order), 100) AS s FROM roles").fetchone()["s"] + 10
    db.execute(
        """INSERT INTO roles (code, label, is_builtin, sort_order, created_at)
        VALUES (?, ?, 0, ?, ?)""",
        (code, label, sort, now),
    )
    base_menus = [
        r["menu_id"] for r in db.execute(
            "SELECT menu_id FROM role_menus WHERE role='employee'"
        ).fetchall()
    ] or list(DEFAULT_ROLE_MENUS.get("employee", []))
    if base_menus:
        db.executemany(
            "INSERT OR IGNORE INTO role_menus (role, menu_id) VALUES (?,?)",
            [(code, m) for m in base_menus],
        )
    db.commit()
    return jsonify({"ok": True, "role": code, "label": label, "message": f"已创建角色 {label}"})


@app.patch("/api/admin/roles/<role>")
@login_required
def admin_roles_rename(user, role):
    """修改角色显示名（内置/自定义均可）。"""
    if not _can_config_roles(user):
        return jsonify({"ok": False, "error": "无权配置角色"}), 403
    data = request.get_json(force=True) or {}
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "请填写角色名称"}), 400
    if len(label) > 40:
        return jsonify({"ok": False, "error": "角色名称过长"}), 400
    db = get_db()
    row = db.execute("SELECT code FROM roles WHERE code=?", (role,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "角色不存在"}), 404
    db.execute("UPDATE roles SET label=? WHERE code=?", (label, role))
    db.commit()
    return jsonify({"ok": True, "message": f"已更名为 {label}"})


@app.delete("/api/admin/roles/<role>")
@login_required
def admin_roles_delete(user, role):
    """删除自定义角色；解除人员绑定并 sync 主角色。"""
    if not _can_config_roles(user):
        return jsonify({"ok": False, "error": "无权配置角色"}), 403
    db = get_db()
    row = db.execute(
        "SELECT code, label, is_builtin FROM roles WHERE code=?", (role,)
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "角色不存在"}), 404
    if row["is_builtin"] or role in BUILTIN_ROLE_CODES or role == "employee":
        return jsonify({"ok": False, "error": "内置角色不可删除"}), 400
    uids = [
        r["user_id"]
        for r in db.execute(
            "SELECT user_id FROM user_roles WHERE role=?", (role,)
        ).fetchall()
    ]
    db.execute("DELETE FROM user_roles WHERE role=?", (role,))
    for uid in uids:
        sync_primary_role(db, uid)
    db.execute("DELETE FROM role_menus WHERE role=?", (role,))
    db.execute("DELETE FROM role_caps WHERE role=?", (role,))
    db.execute("DELETE FROM roles WHERE code=?", (role,))
    db.commit()
    msg = f"已删除角色 {row['label']}"
    if uids:
        msg += f"，已解除 {len(uids)} 人绑定"
    return jsonify({"ok": True, "message": msg, "moved": len(uids)})


@app.post("/api/admin/roles/<role>/config")
@login_required
def admin_roles_save(user, role):
    if not _can_config_roles(user):
        return jsonify({"ok": False, "error": "无权配置角色"}), 403
    db = get_db()
    ensure_roles_seeded(db)
    if not db.execute("SELECT 1 FROM roles WHERE code=?", (role,)).fetchone():
        return jsonify({"ok": False, "error": "未知角色"}), 400
    data = request.get_json(force=True) or {}
    menus = data.get("menus")
    caps = data.get("caps")
    if menus is not None:
        allow = {m["id"] for m in ALL_MENUS}
        menus = [m for m in menus if m in allow]
        db.execute("DELETE FROM role_menus WHERE role=?", (role,))
        db.executemany(
            "INSERT INTO role_menus (role, menu_id) VALUES (?,?)",
            [(role, m) for m in menus],
        )
    if caps is not None:
        allow = {c["id"] for c in ALL_CAPS}
        caps = [c for c in caps if c in allow]
        db.execute("DELETE FROM role_caps WHERE role=?", (role,))
        db.executemany(
            "INSERT INTO role_caps (role, cap_id) VALUES (?,?)",
            [(role, c) for c in caps],
        )
    db.commit()
    return jsonify({"ok": True, "message": f"已保存角色 {role_label_of(db, role)} 配置"})


@app.post("/api/admin/users/<int:uid>/role")
@login_required
def admin_assign_role(user, uid):
    """兼容旧接口：追加/设为该角色（写入 user_roles）。"""
    if not _can_assign_roles(user):
        return jsonify({"ok": False, "error": "无权分配角色"}), 403
    data = request.get_json(force=True) or {}
    role = (data.get("role") or "").strip()
    db = get_db()
    ensure_roles_seeded(db)
    ensure_user_roles_migrated(db)
    replace = bool(data.get("replace"))
    if replace:
        cur = [role] if role else []
    else:
        cur = [c for c in user_roles_of(db, uid) if c not in EMPLOYEE_ROLE_CODES]
        if role and role not in cur:
            cur.append(role)
    try:
        final = _set_user_roles(db, uid, cur)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    db.commit()
    row = db.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
    return jsonify({
        "ok": True,
        "message": f"已将 {row['display_name']} 绑定 {role_label_of(db, role)}",
        "roles": final,
    })


@app.delete("/api/admin/users/<int:uid>/role")
@login_required
def admin_unbind_role(user, uid):
    """兼容旧接口：解除全部本系统角色，改回普通员工。"""
    # AI-GEN-BEGIN
    if not _can_assign_roles(user):
        return jsonify({"ok": False, "error": "无权解除角色绑定"}), 403
    db = get_db()
    ensure_user_roles_migrated(db)
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    if (row["username"] or "") == SYSTEM_ADMIN_USERNAME:
        return jsonify({"ok": False, "error": "系统超管账号不可解除角色"}), 400
    try:
        final = _set_user_roles(db, uid, [])
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": f"已解除 {row['display_name']} 的角色绑定，现为普通员工",
            "user_id": uid,
            "roles": final,
            "to_role": "employee",
        }
    )
    # AI-GEN-END
# AI-GEN-END


@app.post("/api/login")
def api_login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    verify_channel = data.get("verify_channel") or "phone"
    verify_code = (data.get("verify_code") or "").strip()
    verify_contact = (
        data.get("verify_contact") or data.get("phone") or data.get("email") or ""
    ).strip()
    captcha = (data.get("captcha") or data.get("captcha_code") or "").strip()
    source = data.get("source") or "leuc"
    account_id = data.get("account_id")

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    risk = get_risk(username)

    # AI-GEN-BEGIN
    # 失败 ≥1：图片验证码
    if risk["need_captcha"]:
        ok_cap, err_cap = check_login_captcha(username, captcha)
        if not ok_cap:
            return jsonify(
                {
                    "ok": False,
                    "error": err_cap,
                    "risk": risk,
                    "need_captcha": True,
                    "need_verify": risk["need_verify"],
                    "refresh_captcha": True,
                }
            ), 400

    # 失败 ≥10：绑定手机/邮箱 + 短信/邮箱验证码
    if risk["need_verify"]:
        if verify_channel not in ("phone", "email"):
            verify_channel = "phone"
        if not verify_contact:
            return jsonify(
                {
                    "ok": False,
                    "error": "密码错误已达风控阈值，请输入绑定的手机号或邮箱并完成验证",
                    "risk": risk,
                    "need_verify": True,
                    "need_captcha": True,
                }
            ), 400
        if not row:
            return jsonify(
                {
                    "ok": False,
                    "error": "手机号/邮箱与账号绑定不一致，或账号不存在",
                    "risk": risk,
                    "need_verify": True,
                    "need_captcha": True,
                }
            ), 400
        if verify_channel == "phone":
            bound = (row["phone"] or "").strip()
            if not bound:
                return jsonify(
                    {
                        "ok": False,
                        "error": "该账号未绑定手机，请改用邮箱验证或联系管理员",
                        "risk": risk,
                        "need_verify": True,
                        "need_captcha": True,
                    }
                ), 400
            if verify_contact.replace(" ", "") != bound.replace(" ", ""):
                return jsonify(
                    {
                        "ok": False,
                        "error": "手机号与账号绑定不一致",
                        "risk": risk,
                        "need_verify": True,
                        "need_captcha": True,
                    }
                ), 400
        else:
            bound = (row["email"] or "").strip().lower()
            if not bound:
                return jsonify(
                    {
                        "ok": False,
                        "error": "该账号未绑定邮箱，请改用手机验证或联系管理员",
                        "risk": risk,
                        "need_verify": True,
                        "need_captcha": True,
                    }
                ), 400
            if verify_contact.strip().lower() != bound:
                return jsonify(
                    {
                        "ok": False,
                        "error": "邮箱与账号绑定不一致",
                        "risk": risk,
                        "need_verify": True,
                        "need_captcha": True,
                    }
                ), 400
        if verify_code != DEMO_OTP:
            return jsonify(
                {
                    "ok": False,
                    "error": f"验证码错误（演示码 {DEMO_OTP}）",
                    "risk": risk,
                    "need_verify": True,
                    "need_captcha": True,
                }
            ), 400
    # AI-GEN-END

    if not row or row["password"] != password:
        fc = risk["fail_count"] + 1
        set_risk(username or "_", fc)
        risk = get_risk(username or "_")
        return jsonify(
            {
                "ok": False,
                "error": "账号或密码错误",
                "risk": risk,
                "need_verify": risk["need_verify"],
                "need_captcha": risk["need_captcha"],
                "refresh_captcha": True,
            }
        ), 401

    # AI-GEN-BEGIN
    if user_is_closed(row):
        return jsonify(
            {
                "ok": False,
                "error": "账号已关闭，无法登录（如离职关闭）",
                "flow": "account_closed",
            }
        ), 403
    # AI-GEN-END

    set_risk(username, 0)
    session.pop("login_captcha", None)
    session.pop("login_captcha_user", None)
    user = row_user(row)

    if source == "leuc":
        session["user_id"] = user["id"]
        session["login_source"] = "leuc"
        session.pop("oidc", None)
        return jsonify({"ok": True, "flow": "leuc_home", "user": user})

    if source == "oidc":
        return _finish_oidc_after_login(user, data.get("account_id"))

    # 兼容旧演示：source=erp → 来酷ERP
    if source == "erp":
        # AI-GEN-BEGIN
        session["oidc"] = {
            "client_id": "client_laiku_erp",
            "redirect_uri": f"{_issuer()}/demo/home/callback?app=laiku_erp",
            "state": secrets.token_urlsafe(8),
            "scope": "openid profile",
            "nonce": secrets.token_urlsafe(8),
            "code_challenge": None,
            "code_challenge_method": None,
        }
        # AI-GEN-END
        return _finish_oidc_after_login(user, data.get("account_id"))

    return jsonify({"ok": False, "error": "未知登录来源"}), 400


@app.get("/api/captcha")
def api_captcha():
    """登录图片验证码。format=json 时返回 code+svg（原型联调）。"""
    # AI-GEN-BEGIN
    username = (request.args.get("username") or "").strip()
    code = _new_captcha_code(4)
    session["login_captcha"] = code
    session["login_captcha_user"] = username
    svg = _captcha_svg(code)
    if request.args.get("format") == "json":
        return jsonify({"ok": True, "code": code, "svg": svg, "demo": True})
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["X-Demo-Captcha"] = code
    return resp
    # AI-GEN-END


def _parse_redirect_uris(raw: str):
    return [u.strip() for u in (raw or "").replace("\n", ",").split(",") if u.strip()]


# AI-GEN-BEGIN
def _is_loopback_netloc(netloc: str) -> bool:
    host = (netloc or "").split("@")[-1].lower()
    if host.startswith("["):
        return False
    name = host.rsplit(":", 1)[0]
    return name in ("127.0.0.1", "localhost", "0.0.0.0")


def _redirect_uri_allowed(client_row, redirect_uri: str) -> bool:
    allowed = _parse_redirect_uris(client_row["redirect_uris"])
    if redirect_uri in allowed:
        return True
    # 允许同 path 不同 query（演示 callback?app=xx）
    p = urlparse(redirect_uri)
    for a in allowed:
        ap = urlparse(a)
        if (ap.scheme, ap.netloc, ap.path) == (p.scheme, p.netloc, p.path):
            return True
        # 手机/局域网：登记为 127.0.0.1/localhost 时，允许用当前访问 Host 替换
        if (
            ap.scheme == p.scheme
            and ap.path == p.path
            and _is_loopback_netloc(ap.netloc)
            and p.netloc == request.host
        ):
            return True
    return False
# AI-GEN-END


def _finish_oidc_after_login(user, account_id=None):
    oidc = session.get("oidc") or {}
    client_id = oidc.get("client_id")
    if not client_id:
        return jsonify({"ok": False, "error": "缺少 OIDC 会话，请从业务系统重新发起登录"}), 400
    db = get_db()
    client = db.execute(
        "SELECT * FROM systems WHERE client_id = ? AND status = 'enabled'", (client_id,)
    ).fetchone()
    if not client:
        return jsonify(
            {
                "ok": False,
                "error": "无法使用 LEUC 登录",
                "flow": "no_permission",
                "system": client_id,
                "user": user,
                "hint": "当前账号未开通该系统登录权限",
            }
        ), 403
    accts = user_accounts_for_system(user["id"], client["code"])
    if not accts:
        return jsonify(
            {
                "ok": False,
                "error": "无法使用 LEUC 登录",
                "flow": "no_permission",
                "system": client["name"],
                "user": user,
                "hint": "当前账号未开通该系统登录权限",
            }
        ), 403

    def issue(acct):
        session["user_id"] = user["id"]
        session["login_source"] = "oidc"
        session["oidc_account_id"] = acct["id"]
        code = secrets.token_urlsafe(24)
        exp = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
        db.execute(
            """INSERT INTO oauth_codes
            (code, client_id, user_id, account_id, redirect_uri, scope, nonce,
             code_challenge, code_challenge_method, expires_at, used)
            VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (
                code,
                client_id,
                user["id"],
                acct["id"],
                oidc.get("redirect_uri"),
                oidc.get("scope") or client["scopes"],
                oidc.get("nonce"),
                oidc.get("code_challenge"),
                oidc.get("code_challenge_method"),
                exp,
            ),
        )
        db.commit()
        redirect_uri = oidc.get("redirect_uri")
        q = dict(parse_qsl(urlparse(redirect_uri).query, keep_blank_values=True))
        q["code"] = code
        if oidc.get("state"):
            q["state"] = oidc["state"]
        p = urlparse(redirect_uri)
        final = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))
        return jsonify(
            {
                "ok": True,
                "flow": "oidc_redirect",
                "user": user,
                "account": acct,
                "system": {"code": client["code"], "name": client["name"], "client_id": client_id},
                "redirect_uri": final,
                "auto": len(accts) == 1,
            }
        )

    if len(accts) == 1:
        return issue(accts[0])
    if account_id:
        picked = next((a for a in accts if a["id"] == int(account_id)), None)
        if not picked:
            return jsonify({"ok": False, "error": "无效账号"}), 400
        return issue(picked)
    # 统一身份已认证，仅待选业务账号 —— 写入 user_id，避免选账号时被判「未登录」
    session["user_id"] = user["id"]
    session["login_source"] = "oidc"
    session["pending_user_id"] = user["id"]
    session["pending_system_code"] = client["code"]
    return jsonify(
        {
            "ok": True,
            "flow": "select_account",
            "user": user,
            "system": {"code": client["code"], "name": client["name"], "client_id": client_id},
            "accounts": accts,
        }
    )


@app.post("/api/login/select-account")
def select_account():
    data = request.get_json(force=True) or {}
    account_id = data.get("account_id")
    pending = session.get("pending_user_id")
    if not pending:
        return jsonify({"ok": False, "error": "无待选账号会话，请重新发起 OIDC 登录"}), 400
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (pending,)).fetchone()
    user = row_user(row)
    return _finish_oidc_after_login(user, account_id)


@app.post("/api/password/forgot/send")
def forgot_send():
    """忘记密码：仅支持绑定手机号或邮箱（不接受用户名）。"""
    # AI-GEN-BEGIN
    data = request.get_json(force=True) or {}
    contact = (data.get("contact") or data.get("account") or "").strip()
    channel = data.get("channel") or "phone"
    if channel not in ("phone", "email"):
        channel = "phone"
    if not contact:
        return jsonify(
            {"ok": False, "error": "请输入绑定的手机号或邮箱"}
        ), 400
    db = get_db()
    if channel == "phone":
        row = db.execute(
            "SELECT * FROM users WHERE phone = ?", (contact.replace(" ", ""),)
        ).fetchone()
        if not row:
            # 兼容带空格输入
            row = db.execute(
                "SELECT * FROM users WHERE replace(phone,' ','') = ?",
                (contact.replace(" ", ""),),
            ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "未找到该手机号绑定的账号"}), 404
        if not row["phone"]:
            return jsonify({"ok": False, "error": "该账号未绑定手机"}), 400
    else:
        row = db.execute(
            "SELECT * FROM users WHERE lower(email) = ?", (contact.lower(),)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "未找到该邮箱绑定的账号"}), 404
        if not row["email"]:
            return jsonify({"ok": False, "error": "该账号未绑定邮箱"}), 400
    db.execute(
        """INSERT INTO reset_codes (account, channel, code, created_at)
        VALUES (?,?,?,?)
        ON CONFLICT(account) DO UPDATE SET channel=excluded.channel, code=excluded.code, created_at=excluded.created_at""",
        (row["username"], channel, DEMO_RESET, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    if channel == "phone":
        p = row["phone"]
        masked = p[:3] + "****" + p[-4:] if len(p) >= 7 else p
    else:
        masked = row["email"]
    return jsonify(
        {
            "ok": True,
            "message": f"验证码已发送至 {masked}（演示码 {DEMO_RESET}）",
            "username": row["username"],
            "demo_code": DEMO_RESET,
        }
    )
    # AI-GEN-END


@app.post("/api/password/forgot/reset")
def forgot_reset():
    """忘记密码重置：仅手机号或邮箱 + 验证码。"""
    # AI-GEN-BEGIN
    data = request.get_json(force=True) or {}
    contact = (data.get("contact") or data.get("account") or "").strip()
    channel = data.get("channel") or "phone"
    code = (data.get("code") or "").strip()
    new_password = data.get("new_password") or ""
    if channel not in ("phone", "email"):
        channel = "phone"
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "新密码至少 6 位"}), 400
    if not contact:
        return jsonify({"ok": False, "error": "请输入绑定的手机号或邮箱"}), 400
    db = get_db()
    if channel == "phone":
        row = db.execute(
            "SELECT * FROM users WHERE replace(ifnull(phone,''),' ','') = ?",
            (contact.replace(" ", ""),),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM users WHERE lower(ifnull(email,'')) = ?",
            (contact.lower(),),
        ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "未找到对应用户"}), 404
    rc = db.execute("SELECT * FROM reset_codes WHERE account = ?", (row["username"],)).fetchone()
    if not rc or rc["code"] != code:
        return jsonify({"ok": False, "error": f"验证码错误（演示码 {DEMO_RESET}）"}), 400
    if rc["channel"] and rc["channel"] != channel:
        return jsonify({"ok": False, "error": "验证通道与发送时不一致"}), 400
    db.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, row["id"]))
    db.execute("DELETE FROM reset_codes WHERE account = ?", (row["username"],))
    set_risk(row["username"], 0)
    db.commit()
    return jsonify({"ok": True, "message": "密码已重置，请使用新密码登录", "username": row["username"]})
    # AI-GEN-END


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
@login_required
def me(user):
    return jsonify(
        {
            "ok": True,
            "user": user,
            "login_source": session.get("login_source", "leuc"),
            "erp_account_id": session.get("erp_account_id"),
        }
    )


# AI-GEN-BEGIN
@app.post("/api/security/password")
@login_required
def security_password(user):
    """修改/设置登录密码（已有密码须校验旧密码）。"""
    data = request.get_json(force=True) or {}
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "新密码至少 6 位"}), 400
    db = get_db()
    row = db.execute("SELECT password FROM users WHERE id = ?", (user["id"],)).fetchone()
    cur = (row["password"] or "") if row else ""
    if cur:
        if old_password != cur:
            return jsonify({"ok": False, "error": "旧密码不正确"}), 400
    db.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user["id"]))
    db.commit()
    return jsonify({"ok": True, "message": "密码已更新", "user": current_user()})


@app.post("/api/security/contact/send")
@login_required
def security_contact_send(user):
    """手机/邮箱添加或改绑：向新联系方式发送验证码。"""
    data = request.get_json(force=True) or {}
    channel = data.get("channel") or "phone"
    value = (data.get("value") or "").strip()
    if channel not in ("phone", "email"):
        return jsonify({"ok": False, "error": "channel 须为 phone 或 email"}), 400
    if not value:
        return jsonify({"ok": False, "error": "请填写新的手机号或邮箱"}), 400
    if channel == "phone":
        value = value.replace(" ", "")
        if len(value) < 7:
            return jsonify({"ok": False, "error": "手机号格式不正确"}), 400
    else:
        if "@" not in value:
            return jsonify({"ok": False, "error": "邮箱格式不正确"}), 400
        value = value.lower()
    db = get_db()
    if channel == "phone":
        other = db.execute(
            "SELECT id FROM users WHERE replace(ifnull(phone,''),' ','') = ? AND id != ?",
            (value, user["id"]),
        ).fetchone()
    else:
        other = db.execute(
            "SELECT id FROM users WHERE lower(ifnull(email,'')) = ? AND id != ?",
            (value, user["id"]),
        ).fetchone()
    if other:
        return jsonify({"ok": False, "error": "该联系方式已被其他账号占用"}), 400
    acct_key = f"sec:{user['username']}:{channel}"
    db.execute(
        """INSERT INTO reset_codes (account, channel, code, created_at)
        VALUES (?,?,?,?)
        ON CONFLICT(account) DO UPDATE SET channel=excluded.channel, code=excluded.code, created_at=excluded.created_at""",
        (acct_key, value, DEMO_OTP, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    masked = value[:3] + "****" + value[-4:] if channel == "phone" and len(value) >= 7 else value
    return jsonify(
        {
            "ok": True,
            "message": f"验证码已发送至 {masked}（演示码 {DEMO_OTP}）",
            "demo_code": DEMO_OTP,
        }
    )


@app.post("/api/security/contact/bind")
@login_required
def security_contact_bind(user):
    """校验验证码后写入手机/邮箱。"""
    data = request.get_json(force=True) or {}
    channel = data.get("channel") or "phone"
    value = (data.get("value") or "").strip()
    code = (data.get("code") or "").strip()
    if channel not in ("phone", "email"):
        return jsonify({"ok": False, "error": "channel 须为 phone 或 email"}), 400
    if not value or not code:
        return jsonify({"ok": False, "error": "请填写联系方式与验证码"}), 400
    if channel == "phone":
        value = value.replace(" ", "")
    else:
        value = value.lower()
    db = get_db()
    acct_key = f"sec:{user['username']}:{channel}"
    rc = db.execute("SELECT * FROM reset_codes WHERE account = ?", (acct_key,)).fetchone()
    if not rc or rc["code"] != code:
        return jsonify({"ok": False, "error": f"验证码错误（演示码 {DEMO_OTP}）"}), 400
    if (rc["channel"] or "") != value:
        return jsonify({"ok": False, "error": "联系方式与发码时不一致，请重新获取验证码"}), 400
    col = "phone" if channel == "phone" else "email"
    db.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (value, user["id"]))
    db.execute("DELETE FROM reset_codes WHERE account = ?", (acct_key,))
    db.commit()
    label = "手机" if channel == "phone" else "邮箱"
    return jsonify(
        {"ok": True, "message": f"{label}已更新", "user": current_user()}
    )


@app.post("/api/security/bio")
@login_required
def security_bio(user):
    """人脸（单条）/ 指纹（可多条）：录入或删除。"""
    # AI-GEN-BEGIN
    data = request.get_json(force=True) or {}
    kind = data.get("kind") or ""
    action = data.get("action") or ""
    if kind not in ("face", "fingerprint"):
        return jsonify({"ok": False, "error": "kind 须为 face 或 fingerprint"}), 400
    if action not in ("enroll", "delete"):
        return jsonify({"ok": False, "error": "action 须为 enroll 或 delete"}), 400
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    if kind == "face":
        if action == "enroll":
            db.execute(
                "UPDATE users SET face_enrolled = 1, face_enrolled_at = ? WHERE id = ?",
                (now, user["id"]),
            )
            msg = "人脸已录入"
        else:
            db.execute(
                "UPDATE users SET face_enrolled = 0, face_enrolled_at = NULL WHERE id = ?",
                (user["id"],),
            )
            msg = "人脸已删除"
    else:
        if action == "enroll":
            cnt = db.execute(
                "SELECT COUNT(*) AS c FROM user_fingerprints WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["c"]
            if cnt >= 10:
                return jsonify({"ok": False, "error": "最多录入 10 枚指纹"}), 400
            label = (data.get("label") or "").strip()
            if not label:
                label = f"指纹{cnt + 1}"
            db.execute(
                """INSERT INTO user_fingerprints (user_id, label, enrolled_at)
                VALUES (?,?,?)""",
                (user["id"], label, now),
            )
            db.execute(
                """UPDATE users SET fingerprint_enrolled = 1, fingerprint_enrolled_at = ?
                WHERE id = ?""",
                (now, user["id"]),
            )
            msg = f"已录入「{label}」"
        else:
            fp_id = data.get("fingerprint_id") or data.get("id")
            if fp_id:
                row = db.execute(
                    "SELECT * FROM user_fingerprints WHERE id = ? AND user_id = ?",
                    (int(fp_id), user["id"]),
                ).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "指纹不存在"}), 404
                db.execute("DELETE FROM user_fingerprints WHERE id = ?", (int(fp_id),))
                msg = f"已删除「{row['label']}」"
            else:
                db.execute(
                    "DELETE FROM user_fingerprints WHERE user_id = ?", (user["id"],)
                )
                msg = "已清空全部指纹"
            left = db.execute(
                "SELECT COUNT(*) AS c FROM user_fingerprints WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["c"]
            if left == 0:
                db.execute(
                    """UPDATE users SET fingerprint_enrolled = 0,
                    fingerprint_enrolled_at = NULL WHERE id = ?""",
                    (user["id"],),
                )
    db.commit()
    return jsonify({"ok": True, "message": msg, "user": current_user()})
    # AI-GEN-END


@app.post("/api/security/sso")
@login_required
def security_sso(user):
    """SSO：绑定或解绑飞书/企业微信。"""
    data = request.get_json(force=True) or {}
    provider = data.get("provider") or ""
    action = data.get("action") or ""
    if provider not in ("feishu", "wecom"):
        return jsonify({"ok": False, "error": "provider 须为 feishu 或 wecom"}), 400
    if action not in ("bind", "unbind"):
        return jsonify({"ok": False, "error": "action 须为 bind 或 unbind"}), 400
    col = "feishu_bound" if provider == "feishu" else "wecom_bound"
    label = "飞书" if provider == "feishu" else "企业微信"
    val = 1 if action == "bind" else 0
    db = get_db()
    db.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (val, user["id"]))
    db.commit()
    msg = f"{label}已绑定" if action == "bind" else f"{label}已解绑"
    return jsonify({"ok": True, "message": msg, "user": current_user()})


# AI-GEN-END


@app.get("/api/home")
@login_required
def home(user):
    db = get_db()
    pending = db.execute(
        "SELECT * FROM todos WHERE assignee_id = ? AND bucket = 'pending' ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    done = db.execute(
        "SELECT * FROM todos WHERE assignee_id = ? AND bucket = 'done' ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    initiated = db.execute(
        "SELECT * FROM todos WHERE initiator_id = ? AND bucket = 'initiated' ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    # AI-GEN-BEGIN
    depts = all_departments(db)
    my_dept_id = user.get("dept_id")
    chain = dept_ancestor_chain(depts, my_dept_id)
    # AI-GEN-END
    return jsonify(
        {
            "ok": True,
            "user": user,
            "systems": my_systems(user["id"]),
            "todos": {
                "pending": [serialize_todo(db, r) for r in pending],
                "done": [serialize_todo(db, r) for r in done],
                "initiated": [serialize_todo(db, r) for r in initiated],
            },
            # AI-GEN-BEGIN
            # 个人中心：仅本人所属组织路径
            "org_tree": build_org_tree(chain),
            "dept_path": dept_path_label(chain),
            "my_dept_id": my_dept_id,
            # AI-GEN-END
        }
    )


@app.get("/api/todo/<int:tid>")
@login_required
def todo_detail(user, tid):
    """待办详情（开通录入用，含排查 ID）。"""
    # AI-GEN-BEGIN
    db = get_db()
    row = db.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "待办不存在"}), 404
    if row["assignee_id"] != user["id"] and not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "无权查看"}), 403
    return jsonify({"ok": True, "todo": serialize_todo(db, row)})
    # AI-GEN-END


@app.get("/api/todo/<int:tid>/flow")
@login_required
def todo_flow(user, tid):
    """待办完整流程：时间线 + 当前审核人 + 流程预测。"""
    # AI-GEN-BEGIN
    db = get_db()
    row = db.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "待办不存在"}), 404
    allowed = (
        row["assignee_id"] == user["id"]
        or row["initiator_id"] == user["id"]
        or user_has_role(user, "super_admin")
    )
    if not allowed and row["application_id"]:
        app = db.execute(
            "SELECT applicant_id FROM applications WHERE id = ?",
            (row["application_id"],),
        ).fetchone()
        if app and app["applicant_id"] == user["id"]:
            allowed = True
    if not allowed:
        return jsonify({"ok": False, "error": "无权查看流程"}), 403
    flow = build_todo_flow(db, row)
    return jsonify({"ok": True, **flow})
    # AI-GEN-END


@app.post("/api/todo/<int:tid>/resubmit")
@login_required
def todo_resubmit(user, tid):
    """驳回后修改表单并再次提交，直达原驳回人。"""
    # AI-GEN-BEGIN
    data = request.get_json(force=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "待办不存在"}), 404
    if row["assignee_id"] != user["id"] and not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "仅当前处理人可重提"}), 403
    if row["bucket"] != "pending" or row["status"] != "open":
        return jsonify({"ok": False, "error": "该待办不可重提"}), 400
    app_id = row["application_id"]
    if not app_id:
        return jsonify({"ok": False, "error": "非流程待办"}), 400
    app_row = db.execute(
        "SELECT * FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    if not app_row or app_row["status"] != "returned":
        return jsonify({"ok": False, "error": "仅驳回待改单的申请可重提"}), 400
    try:
        reject_from = (
            app_row["reject_from_step"]
            if "reject_from_step" in app_row.keys()
            else None
        )
    except Exception:
        reject_from = None
    if reject_from in (None, "", 0, "0"):
        return jsonify({"ok": False, "error": "缺少原驳回节点，无法重提"}), 400
    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END
    remark = (data.get("remark") or "").strip() or None
    meta = merge_todo_meta_updates(row["meta"], data.get("form") or data.get("meta") or {})
    meta.pop("needs_resubmit", None)
    meta.pop("reject_from_step", None)
    meta.pop("reject_to_step", None)
    meta_json = json.dumps(meta, ensure_ascii=False)
    # 同步同申请单 meta（业务字段）
    for t in db.execute(
        "SELECT id, meta FROM todos WHERE application_id = ?", (app_id,)
    ).fetchall():
        merged = merge_todo_meta_updates(t["meta"], data.get("form") or data.get("meta") or {})
        merged.pop("needs_resubmit", None)
        merged.pop("reject_from_step", None)
        merged.pop("reject_to_step", None)
        db.execute(
            "UPDATE todos SET meta = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), t["id"]),
        )
    db.execute(
        "UPDATE todos SET bucket = 'done', status = 'approved', remark = ? WHERE id = ?",
        (remark or "修改后重提", tid),
    )
    result = jump_to_reject_from_step(
        db,
        app_id=app_id,
        reject_from_step=int(reject_from),
        todo_row=row,
        meta_json=meta_json,
        remark=remark,
        now=now,
        todo_type=row["todo_type"],
    )
    if not result.get("ok"):
        return jsonify(result), 400
    db.commit()
    return jsonify(result)
    # AI-GEN-END


@app.post("/api/apply/preview-flow")
@login_required
def apply_preview_flow(user):
    """提交前流程预测（不落库）。"""
    # AI-GEN-BEGIN
    data = request.get_json(force=True) or {}
    apply_type = (data.get("type") or data.get("flow_code") or "").strip()
    if apply_type == "password_extend":
        apply_type = "account_extend"
    subject_id = data.get("subject_user_id") or data.get("for_user_id") or user["id"]
    subject_id = int(subject_id)
    if subject_id != int(user["id"]):
        if not user_has_role(user, "hr_specialist", "super_admin", "dept_owner"):
            return jsonify({"ok": False, "error": "无权代他人预览"}), 403
    system_id = data.get("system_id")
    with_sensitive = bool(data.get("with_sensitive"))
    days = int(data.get("days") or 90)
    db = get_db()
    result = preview_apply_flow(
        db,
        apply_type=apply_type,
        subject_id=subject_id,
        system_id=int(system_id) if system_id else None,
        with_sensitive=with_sensitive,
        days=days,
    )
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)
    # AI-GEN-END


@app.get("/api/org/overview")
@app.get("/api/dept/overview")
@login_required
def org_overview(user):
    """组织概览：scope=mine 本人路径；scope=manage 全员可读完整通讯录（管理写操作另鉴权）。"""
    # AI-GEN-BEGIN
    db = get_db()
    depts = all_departments(db)
    manage_ids = managed_dept_ids(db, user)
    # AI-GEN-BEGIN
    # 空部门树时 manage_ids 为空；超管/人事仍应可同步、添加根部门
    can_manage = bool(manage_ids) or user_has_cap(user, "manage_all_org") or user_has_role(
        user, "super_admin", "hr_specialist"
    )
    # AI-GEN-END
    q = (request.args.get("q") or "").strip()
    dept_id = request.args.get("dept_id")
    focus_id = int(dept_id) if dept_id else None
    scope = (request.args.get("scope") or "mine").strip()
    if scope not in ("mine", "manage"):
        scope = "mine"

    unread = db.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE to_user_id = ? AND is_read = 0",
        (user["id"],),
    ).fetchone()["c"]
    base = {
        "ok": True,
        "scope": scope,
        "can_manage": can_manage,
        "can_set_account_expire": user_can_set_account_expire(user),
        "can_set_dept_owner": user_can_set_dept_owner(user),
        "manage_dept_ids": sorted(manage_ids),
        "unread_messages": unread,
    }

    if scope == "mine":
        my_dept_id = user.get("dept_id")
        chain = dept_ancestor_chain(depts, my_dept_id)
        me_row = db.execute(
            "SELECT * FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        members = [member_row_enriched(me_row)] if me_row else []
        if q:
            like = q.lower()
            members = [
                m
                for m in members
                if like in (m.get("display_name") or "").lower()
                or like in (m.get("username") or "").lower()
                or like in (m.get("phone") or "").lower()
                or like in (m.get("email") or "").lower()
            ]
        return jsonify(
            {
                **base,
                "departments": chain,
                "tree": build_org_tree(chain, manage_ids),
                "focus_dept_id": my_dept_id,
                "my_dept_id": my_dept_id,
                "dept_path": dept_path_label(chain),
                "members": members,
            }
        )

    # scope=manage：全员可读完整部门树与人员；写操作仍由 can_manage / 按钮权限控制
    # AI-GEN-BEGIN
    sql = "FROM users WHERE 1=1 AND username != ?"
    params: list = [SYSTEM_ADMIN_USERNAME]
    if focus_id:
        ids = subtree_ids(depts, focus_id)
        if not ids:
            return jsonify(
                {
                    **base,
                    "departments": depts,
                    "tree": build_org_tree(depts, manage_ids),
                    "focus_dept_id": focus_id,
                    "my_dept_id": user.get("dept_id"),
                    "dept_path": dept_path_label(
                        dept_ancestor_chain(depts, user.get("dept_id"))
                    ),
                    "members": [],
                    "pagination": {
                        "page": 1,
                        "page_size": 20,
                        "total": 0,
                        "total_pages": 1,
                    },
                }
            )
        sql += f" AND dept_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    if q:
        sql += " AND (display_name LIKE ? OR username LIKE ? OR phone LIKE ? OR email LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    total = int(db.execute(f"SELECT COUNT(*) AS c {sql}", params).fetchone()["c"])
    nopage = (request.args.get("nopage") or "").strip() in ("1", "true", "yes")
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get("page_size") or 20)
    except (TypeError, ValueError):
        page_size = 20
    page = max(1, page)
    page_size = min(100, max(10, page_size))
    if nopage:
        # 选人/设负责人等需全量候选人
        rows = db.execute(
            f"SELECT * {sql} ORDER BY dept_id, id", params
        ).fetchall()
        page = 1
        page_size = max(total, 1)
        total_pages = 1
    else:
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        rows = db.execute(
            f"SELECT * {sql} ORDER BY dept_id, id LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    members = [member_row_enriched(m) for m in rows if not is_hidden_from_org(m)]

    return jsonify(
        {
            **base,
            "departments": depts,
            "tree": build_org_tree(depts, manage_ids),
            "focus_dept_id": focus_id,
            "my_dept_id": user.get("dept_id"),
            "dept_path": dept_path_label(
                dept_ancestor_chain(depts, user.get("dept_id"))
            ),
            "members": members,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }
    )
    # AI-GEN-END



# AI-GEN-BEGIN
def user_can_set_dept_owner(user, dept_id=None) -> bool:
    """是否可设置部门负责人：按钮 / 人事超管 / 可管该部门。"""
    if not user:
        return False
    if user_has_cap(user, "org_set_owner") or user_has_cap(user, "manage_all_org"):
        return True
    if user_has_role(user, "super_admin", "hr_specialist"):
        return True
    if dept_id is None:
        return bool(managed_dept_ids(get_db(), user))
    return int(dept_id) in managed_dept_ids(get_db(), user)


@app.post("/api/org/departments/<int:dept_id>/owner")
@login_required
def org_set_dept_owner(user, dept_id):
    """设置部门主负责人与额外负责人。"""
    if not user_can_set_dept_owner(user, dept_id):
        return jsonify({"ok": False, "error": "无权设置该部门负责人"}), 403
    data = request.get_json(force=True) or {}
    owner_uid = data.get("owner_user_id")
    if owner_uid in ("", None):
        owner_uid = None
    else:
        try:
            owner_uid = int(owner_uid)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "负责人无效"}), 400
    extra_ids = data.get("extra_owner_ids")
    if extra_ids is None:
        extra_ids = data.get("extra_owners") or []
    try:
        extra_ids = [int(x) for x in extra_ids if x not in ("", None)]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "额外负责人无效"}), 400
    # 主负责人不重复进额外
    if owner_uid is not None:
        extra_ids = [x for x in extra_ids if x != owner_uid]
    # 去重保序
    seen = set()
    extra_ids = [x for x in extra_ids if not (x in seen or seen.add(x))]

    db = get_db()
    dept = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not dept:
        return jsonify({"ok": False, "error": "部门不存在"}), 404
    if owner_uid is not None:
        ou = db.execute("SELECT * FROM users WHERE id = ?", (owner_uid,)).fetchone()
        if not ou:
            return jsonify({"ok": False, "error": "负责人不存在"}), 404
    for eid in extra_ids:
        if not db.execute("SELECT id FROM users WHERE id = ?", (eid,)).fetchone():
            return jsonify({"ok": False, "error": f"额外负责人不存在：{eid}"}), 404

    db.execute(
        "UPDATE departments SET owner_user_id = ? WHERE id = ?",
        (owner_uid, dept_id),
    )
    db.execute("DELETE FROM dept_extra_owners WHERE dept_id = ?", (dept_id,))
    if extra_ids:
        db.executemany(
            "INSERT OR IGNORE INTO dept_extra_owners (dept_id, user_id) VALUES (?,?)",
            [(dept_id, eid) for eid in extra_ids],
        )
    # 部门负责人仅为部门属性，不自动绑定 dept_owner 角色
    db.commit()
    owner = None
    if owner_uid:
        owner = db.execute(
            "SELECT id, username, display_name, role FROM users WHERE id = ?",
            (owner_uid,),
        ).fetchone()
    extras = db.execute(
        """SELECT u.id, u.username, u.display_name FROM dept_extra_owners e
        JOIN users u ON u.id = e.user_id WHERE e.dept_id = ? ORDER BY u.id""",
        (dept_id,),
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "message": f"已更新「{dept['name']}」负责人",
            "dept_id": dept_id,
            "dept_name": dept["name"],
            "owner": dict(owner) if owner else None,
            "extra_owners": [dict(e) for e in extras],
        }
    )


# AI-GEN-BEGIN
@app.patch("/api/org/members/<int:uid>")
@login_required
def org_update_member(user, uid):
    """编辑人员：姓名/手机/邮箱/部门/状态（关闭可再打开）。"""
    db = get_db()
    migrate_schema(db)
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "人员不存在"}), 404
    if row["username"] == SYSTEM_ADMIN_USERNAME:
        return jsonify({"ok": False, "error": "系统超管不可在此编辑"}), 400
    if not can_manage_member(user, row):
        return jsonify({"ok": False, "error": "无权编辑该人员"}), 403

    data = request.get_json(force=True) or {}
    display_name = (
        str(data.get("display_name")).strip()
        if "display_name" in data
        else row["display_name"]
    )
    if not display_name:
        return jsonify({"ok": False, "error": "姓名必填"}), 400

    phone = data.get("phone") if "phone" in data else row["phone"]
    phone = (str(phone).strip() if phone is not None else "") or None
    email = data.get("email") if "email" in data else row["email"]
    email = (str(email).strip() if email is not None else "") or None

    new_dept = row["dept_id"]
    if "dept_id" in data:
        try:
            new_dept = int(data.get("dept_id")) if data.get("dept_id") not in ("", None) else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "部门无效"}), 400
        if new_dept is None:
            return jsonify({"ok": False, "error": "部门必选"}), 400
        if not can_manage_dept(user, new_dept):
            return jsonify({"ok": False, "error": "无权调入该部门"}), 403
        if not db.execute("SELECT id FROM departments WHERE id = ?", (new_dept,)).fetchone():
            return jsonify({"ok": False, "error": "部门不存在"}), 404

    new_status = (row["status"] if "status" in row.keys() else "active") or "active"
    if "status" in data:
        new_status = (data.get("status") or "").strip()
        if new_status not in ("active", "closed"):
            return jsonify({"ok": False, "error": "status 须为 active/closed"}), 400
        if new_status == "closed" and int(uid) == int(user["id"]):
            return jsonify({"ok": False, "error": "不能关闭自己的账号"}), 400

    db.execute(
        """UPDATE users SET display_name=?, phone=?, email=?, dept_id=?
        WHERE id=?""",
        (display_name, phone, email, new_dept, uid),
    )
    old_status = (row["status"] if "status" in row.keys() else "active") or "active"
    if new_status != old_status:
        if new_status == "closed":
            close_leuc_user(db, uid)
        else:
            reopen_leuc_user(db, uid)
    db.commit()
    updated = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return jsonify(
        {
            "ok": True,
            "message": "已保存人员信息",
            "member": member_row_enriched(updated),
        }
    )


@app.patch("/api/org/departments/<int:dept_id>")
@login_required
def org_update_department(user, dept_id):
    """修改部门名称。"""
    if not can_manage_dept(user, dept_id):
        return jsonify({"ok": False, "error": "无权编辑该部门"}), 403
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "部门名称必填"}), 400
    db = get_db()
    migrate_schema(db)
    row = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "部门不存在"}), 404
    db.execute("UPDATE departments SET name = ? WHERE id = ?", (name, dept_id))
    db.commit()
    return jsonify({"ok": True, "message": f"已改名为「{name}」", "id": dept_id, "name": name})


# AI-GEN-BEGIN
@app.post("/api/org/departments")
@login_required
def org_create_department(user):
    """添加部门：挂到指定上级（须可管该上级）；无上级时仅 HR/超管可建根级。"""
    # AI-GEN-BEGIN
    if not user_has_cap(user, "org_dept_add"):
        return jsonify({"ok": False, "error": "无「添加部门」按钮权限"}), 403
    # AI-GEN-END
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "部门名称必填"}), 400
    parent_id = data.get("parent_id")
    if parent_id in ("", None):
        parent_id = None
    else:
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "上级部门无效"}), 400

    db = get_db()
    migrate_schema(db)
    if parent_id is None:
        if not (
            user_has_cap(user, "manage_all_org")
            or user["role"] in ("super_admin", "hr_specialist")
        ):
            return jsonify(
                {"ok": False, "error": "创建根级部门需人事/超管权限；请选择上级部门"}
            ), 403
    else:
        if not can_manage_dept(user, parent_id):
            return jsonify({"ok": False, "error": "无权在该上级下创建部门"}), 403
        parent = db.execute(
            "SELECT id FROM departments WHERE id = ?", (parent_id,)
        ).fetchone()
        if not parent:
            return jsonify({"ok": False, "error": "上级部门不存在"}), 404

    max_sort = db.execute(
        """SELECT COALESCE(MAX(sort_order), 0) AS s FROM departments
        WHERE IFNULL(parent_id, -1) = IFNULL(?, -1)""",
        (parent_id,),
    ).fetchone()["s"]
    cur = db.execute(
        """INSERT INTO departments
        (name, parent_id, owner_user_id, leorg_id, sort_order)
        VALUES (?,?,NULL,NULL,?)""",
        (name, parent_id, int(max_sort) + 10),
    )
    new_id = int(cur.lastrowid)
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": f"已添加部门「{name}」",
            "id": new_id,
            "name": name,
            "parent_id": parent_id,
        }
    )


@app.delete("/api/org/departments/<int:dept_id>")
@login_required
def org_delete_department(user, dept_id):
    """删除部门：无子部门、无人员；不可删唯一根部门。"""
    # AI-GEN-BEGIN
    if not user_has_cap(user, "org_dept_delete"):
        return jsonify({"ok": False, "error": "无「删除部门」按钮权限"}), 403
    # AI-GEN-END
    if not can_manage_dept(user, dept_id):
        return jsonify({"ok": False, "error": "无权删除该部门"}), 403
    db = get_db()
    migrate_schema(db)
    row = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "部门不存在"}), 404
    # AI-GEN-BEGIN
    if ("is_builtin" in row.keys() and row["is_builtin"]) or (
        "dept_code" in row.keys() and row["dept_code"] == "external"
    ):
        return jsonify({"ok": False, "error": "内置部门「外部人员」不可删除"}), 400
    # AI-GEN-END
    child = db.execute(
        "SELECT id FROM departments WHERE parent_id = ? LIMIT 1", (dept_id,)
    ).fetchone()
    if child:
        return jsonify({"ok": False, "error": "请先删除或移走子部门"}), 400
    member = db.execute(
        "SELECT id FROM users WHERE dept_id = ? LIMIT 1", (dept_id,)
    ).fetchone()
    if member:
        return jsonify({"ok": False, "error": "部门下仍有人员，请先调离或删除人员"}), 400
    total = db.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]
    if total <= 1:
        return jsonify({"ok": False, "error": "不能删除唯一根部门"}), 400
    db.execute("DELETE FROM dept_extra_owners WHERE dept_id = ?", (dept_id,))
    db.execute(
        "DELETE FROM approval_chain_dept_overrides WHERE dept_id = ?", (dept_id,)
    )
    db.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
    db.commit()
    return jsonify(
        {"ok": True, "message": f"已删除部门「{row['name']}」", "id": dept_id}
    )
# AI-GEN-END


@app.post("/api/org/departments/<int:dept_id>/move")
@login_required
def org_move_department(user, dept_id):
    """同级调整部门顺序：direction=up|down。"""
    if not can_manage_dept(user, dept_id):
        return jsonify({"ok": False, "error": "无权调整该部门"}), 403
    data = request.get_json(force=True) or {}
    direction = (data.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        return jsonify({"ok": False, "error": "direction 须为 up/down"}), 400
    db = get_db()
    migrate_schema(db)
    row = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "部门不存在"}), 404
    parent_id = row["parent_id"]
    siblings = db.execute(
        """SELECT id, sort_order FROM departments
        WHERE IFNULL(parent_id, -1) = IFNULL(?, -1)
        ORDER BY sort_order, id""",
        (parent_id,),
    ).fetchall()
    ids = [int(s["id"]) for s in siblings]
    if dept_id not in ids:
        return jsonify({"ok": False, "error": "同级列表异常"}), 400
    idx = ids.index(dept_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(ids):
        return jsonify({"ok": False, "error": "已在同级边界，无法移动"}), 400
    a, b = siblings[idx], siblings[swap_idx]
    # 若 sort_order 相同，用 id 交换后再规范化
    order_a = int(a["sort_order"] or a["id"])
    order_b = int(b["sort_order"] or b["id"])
    if order_a == order_b:
        for i, s in enumerate(siblings):
            db.execute(
                "UPDATE departments SET sort_order = ? WHERE id = ?",
                ((i + 1) * 10, s["id"]),
            )
        siblings = db.execute(
            """SELECT id, sort_order FROM departments
            WHERE IFNULL(parent_id, -1) = IFNULL(?, -1)
            ORDER BY sort_order, id""",
            (parent_id,),
        ).fetchall()
        a = next(s for s in siblings if int(s["id"]) == dept_id)
        b = next(s for s in siblings if int(s["id"]) == ids[swap_idx])
        order_a = int(a["sort_order"])
        order_b = int(b["sort_order"])
    db.execute(
        "UPDATE departments SET sort_order = ? WHERE id = ?",
        (order_b, a["id"]),
    )
    db.execute(
        "UPDATE departments SET sort_order = ? WHERE id = ?",
        (order_a, b["id"]),
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": "已调整部门顺序",
            "id": dept_id,
            "swapped_with": int(b["id"]),
        }
    )
# AI-GEN-END


@app.post("/api/org/members/account-expire")
@login_required
def set_members_account_expire(user):
    """部门侧设置账号有效期：指定日期或永不过期（NULL）。需角色/人员开通且可管目标。"""
    if not user_can_set_account_expire(user):
        return jsonify({"ok": False, "error": "未开通「设置账号有效期」权限"}), 403
    data = request.get_json(force=True) or {}
    ids = data.get("user_ids") or []
    never = bool(data.get("never_expire"))
    expire = (data.get("account_expire") or "").strip() or None
    if not never:
        if not expire:
            return jsonify({"ok": False, "error": "请选择有效期或勾选永不过期"}), 400
        try:
            datetime.strptime(expire, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "有效期格式应为 YYYY-MM-DD"}), 400
    else:
        expire = None
    if not ids:
        return jsonify({"ok": False, "error": "请选择人员"}), 400
    db = get_db()
    updated = 0
    for uid in ids:
        row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row or not can_manage_member(user, row):
            continue
        db.execute("UPDATE users SET account_expire = ? WHERE id = ?", (expire, uid))
        updated += 1
    db.commit()
    return jsonify({"ok": True, "updated": updated, "account_expire": expire})
# AI-GEN-END


@app.get("/api/member/<int:uid>/permissions")
@login_required
def member_perms(user, uid):
    target = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    # 全员可看部门内权限概览；管理操作另鉴权
    return jsonify({"ok": True, "user": row_user(target), "systems": my_systems(uid)})


@app.post("/api/org/message")
@app.post("/api/chat/send")
@login_required
def send_org_message(user):
    data = request.get_json(force=True) or {}
    to_user_id = data.get("to_user_id")
    title = (data.get("title") or "").strip() or "即时消息"
    body = (data.get("body") or "").strip()
    msg_type = (data.get("msg_type") or "chat").strip()
    if msg_type not in ("chat", "system"):
        msg_type = "chat"
    if not to_user_id or not body:
        return jsonify({"ok": False, "error": "收件人与内容必填"}), 400
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (to_user_id,)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "收件人不存在"}), 404
    if int(to_user_id) == user["id"]:
        return jsonify({"ok": False, "error": "不能发给自己"}), 400
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute(
        """INSERT INTO messages (from_user_id, to_user_id, title, body, created_at, is_read, msg_type)
        VALUES (?,?,?,?,?,0,?)""",
        (user["id"], to_user_id, title, body, now, msg_type),
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "id": cur.lastrowid,
            "message": "已发送",
            "item": {
                "id": cur.lastrowid,
                "from_user_id": user["id"],
                "to_user_id": int(to_user_id),
                "from_name": user["display_name"],
                "title": title,
                "body": body,
                "created_at": now,
                "msg_type": msg_type,
                "is_read": 0,
            },
        }
    )


@app.get("/api/org/messages")
@app.get("/api/chat/list")
@login_required
def list_org_messages(user):
    db = get_db()
    peer_id = request.args.get("peer_id")
    box = request.args.get("box") or "inbox"
    if peer_id is not None and peer_id != "":
        pid = int(peer_id)
        if pid == 0:
            rows = db.execute(
                """SELECT m.*, '系统' AS peer_name, 'system' AS peer_username
                FROM messages m
                WHERE m.to_user_id = ? AND m.msg_type = 'system'
                ORDER BY m.id ASC""",
                (user["id"],),
            ).fetchall()
            db.execute(
                """UPDATE messages SET is_read = 1
                WHERE to_user_id = ? AND msg_type = 'system' AND is_read = 0""",
                (user["id"],),
            )
        else:
            rows = db.execute(
                """SELECT m.*,
                  CASE WHEN m.from_user_id = ? THEN '我' ELSE u.display_name END AS peer_name,
                  CASE WHEN m.from_user_id = ? THEN ? ELSE u.username END AS peer_username
                FROM messages m
                JOIN users u ON u.id = CASE WHEN m.from_user_id = ? THEN m.to_user_id ELSE m.from_user_id END
                WHERE ((m.from_user_id = ? AND m.to_user_id = ?)
                    OR (m.from_user_id = ? AND m.to_user_id = ?))
                  AND COALESCE(m.msg_type,'chat') = 'chat'
                ORDER BY m.id ASC""",
                (
                    user["id"],
                    user["id"],
                    user["username"],
                    user["id"],
                    user["id"],
                    pid,
                    pid,
                    user["id"],
                ),
            ).fetchall()
            db.execute(
                """UPDATE messages SET is_read = 1
                WHERE to_user_id = ? AND from_user_id = ? AND is_read = 0""",
                (user["id"], pid),
            )
        db.commit()
        return jsonify({"ok": True, "messages": [dict(r) for r in rows]})

    if box == "sent":
        rows = db.execute(
            """SELECT m.*, u.display_name AS peer_name, u.username AS peer_username
            FROM messages m JOIN users u ON u.id = m.to_user_id
            WHERE m.from_user_id = ? ORDER BY m.id DESC""",
            (user["id"],),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT m.*,
              CASE WHEN m.from_user_id = 0 THEN '系统' ELSE u.display_name END AS peer_name,
              CASE WHEN m.from_user_id = 0 THEN 'system' ELSE u.username END AS peer_username
            FROM messages m
            LEFT JOIN users u ON u.id = m.from_user_id
            WHERE m.to_user_id = ? ORDER BY m.id DESC""",
            (user["id"],),
        ).fetchall()
    return jsonify({"ok": True, "messages": [dict(r) for r in rows]})


@app.get("/api/chat/peers")
@login_required
def chat_peers(user):
    """最近会话列表（含系统）。"""
    db = get_db()
    rows = db.execute(
        """SELECT * FROM (
          SELECT
            CASE WHEN m.from_user_id = 0 THEN 0
                 WHEN m.from_user_id = ? THEN m.to_user_id
                 ELSE m.from_user_id END AS peer_id,
            MAX(m.id) AS last_id,
            SUM(CASE WHEN m.to_user_id = ? AND m.is_read = 0 THEN 1 ELSE 0 END) AS unread
          FROM messages m
          WHERE m.to_user_id = ? OR m.from_user_id = ?
          GROUP BY peer_id
        ) t ORDER BY last_id DESC""",
        (user["id"], user["id"], user["id"], user["id"]),
    ).fetchall()
    out = []
    for r in rows:
        pid = r["peer_id"]
        last = db.execute("SELECT * FROM messages WHERE id = ?", (r["last_id"],)).fetchone()
        if pid == 0:
            out.append(
                {
                    "peer_id": 0,
                    "peer_name": "系统消息",
                    "peer_username": "system",
                    "msg_type": "system",
                    "unread": r["unread"],
                    "last_body": last["body"] if last else "",
                    "last_at": last["created_at"] if last else "",
                }
            )
        else:
            u = db.execute(
                "SELECT id, display_name, username FROM users WHERE id = ?", (pid,)
            ).fetchone()
            if not u:
                continue
            out.append(
                {
                    "peer_id": pid,
                    "peer_name": u["display_name"],
                    "peer_username": u["username"],
                    "msg_type": "chat",
                    "unread": r["unread"],
                    "last_body": last["body"] if last else "",
                    "last_at": last["created_at"] if last else "",
                }
            )
    return jsonify({"ok": True, "peers": out})


@app.get("/api/chat/poll")
@login_required
def chat_poll(user):
    """拉取 since_id 之后的新消息（用于即时提醒）。"""
    since = int(request.args.get("since_id") or 0)
    db = get_db()
    rows = db.execute(
        """SELECT m.*,
          CASE WHEN m.from_user_id = 0 THEN '系统'
               ELSE COALESCE(u.display_name,'未知') END AS from_name,
          CASE WHEN m.from_user_id = 0 THEN 'system'
               ELSE COALESCE(u.username,'') END AS from_username
        FROM messages m
        LEFT JOIN users u ON u.id = m.from_user_id
        WHERE m.to_user_id = ? AND m.id > ?
        ORDER BY m.id ASC LIMIT 50""",
        (user["id"], since),
    ).fetchall()
    max_id = since
    items = []
    for r in rows:
        d = dict(r)
        items.append(d)
        max_id = max(max_id, d["id"])
    if not items:
        # 仍返回当前最大 id，便于客户端对齐
        top = db.execute(
            "SELECT MAX(id) AS m FROM messages WHERE to_user_id = ?", (user["id"],)
        ).fetchone()
        if top and top["m"]:
            max_id = max(max_id, top["m"])
    return jsonify({"ok": True, "messages": items, "max_id": max_id})


@app.get("/api/chat/stream")
@login_required
def chat_stream(user):
    """SSE 实时推送：有新消息立即推给浏览器（右下角弹出+提示音）。"""
    # AI-GEN-BEGIN
    import time as _time

    since = int(request.args.get("since_id") or 0)
    uid = int(user["id"])

    def _fetch(since_id: int):
        # 流式循环里每次新开连接，避免持有跨 yield 的 request 级 db
        conn = connect()
        try:
            rows = conn.execute(
                """SELECT m.*,
                  CASE WHEN m.from_user_id = 0 THEN '系统'
                       ELSE COALESCE(u.display_name,'未知') END AS from_name,
                  CASE WHEN m.from_user_id = 0 THEN 'system'
                       ELSE COALESCE(u.username,'') END AS from_username
                FROM messages m
                LEFT JOIN users u ON u.id = m.from_user_id
                WHERE m.to_user_id = ? AND m.id > ?
                ORDER BY m.id ASC LIMIT 50""",
                (uid, since_id),
            ).fetchall()
            items = [dict(r) for r in rows]
            max_id = since_id
            for d in items:
                max_id = max(max_id, d["id"])
            if not items:
                top = conn.execute(
                    "SELECT MAX(id) AS m FROM messages WHERE to_user_id = ?",
                    (uid,),
                ).fetchone()
                if top and top["m"]:
                    max_id = max(max_id, int(top["m"]))
            return items, max_id
        finally:
            conn.close()

    def generate():
        nonlocal since
        # 首包：对齐游标（不弹历史）
        _items, since = _fetch(since)
        yield f"event: ready\ndata: {json.dumps({'ok': True, 'max_id': since}, ensure_ascii=False)}\n\n"
        idle = 0
        while True:
            try:
                items, max_id = _fetch(since)
                if items:
                    since = max_id
                    idle = 0
                    payload = {
                        "ok": True,
                        "messages": items,
                        "max_id": max_id,
                    }
                    yield f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    since = max(since, max_id)
                    idle += 1
                    # 心跳，避免代理断开
                    if idle % 15 == 0:
                        yield f"event: ping\ndata: {json.dumps({'max_id': since})}\n\n"
                _time.sleep(0.8)
            except GeneratorExit:
                break
            except Exception:
                yield f"event: error\ndata: {json.dumps({'ok': False})}\n\n"
                break

    resp = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
    return resp
    # AI-GEN-END


@app.post("/api/chat/system-notify")
@login_required
def chat_system_notify(user):
    """发送系统即时提醒。演示：发给自己任意角色可发；群发需人事/超管/系统负责人。"""
    data = request.get_json(force=True) or {}
    to_user_id = data.get("to_user_id")
    body = (data.get("body") or "").strip() or "您有一条新的系统通知"
    title = (data.get("title") or "系统通知").strip()
    db = get_db()
    if to_user_id is not None:
        targets = [int(to_user_id)]
        # 给别人发系统消息需管理角色
        if int(to_user_id) != user["id"] and not user_has_role(
            user, "hr_specialist", "super_admin", "system_owner"
        ):
            return jsonify({"ok": False, "error": "无权限向他人发送系统消息"}), 403
    else:
        if not user_has_role(user, "hr_specialist", "super_admin", "system_owner"):
            return jsonify({"ok": False, "error": "无权限群发系统消息"}), 403
        targets = [r["id"] for r in db.execute("SELECT id FROM users").fetchall()]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ids = []
    for tid in targets:
        if to_user_id is None and tid == user["id"]:
            continue
        cur = db.execute(
            """INSERT INTO messages
            (from_user_id, to_user_id, title, body, created_at, is_read, msg_type)
            VALUES (0,?,?,?,?,0,'system')""",
            (tid, title, body, now),
        )
        ids.append(cur.lastrowid)
    # 发给自己时也写入
    if to_user_id is not None and int(to_user_id) == user["id"] and not ids:
        cur = db.execute(
            """INSERT INTO messages
            (from_user_id, to_user_id, title, body, created_at, is_read, msg_type)
            VALUES (0,?,?,?,?,0,'system')""",
            (user["id"], title, body, now),
        )
        ids.append(cur.lastrowid)
    db.commit()
    return jsonify({"ok": True, "count": len(ids), "ids": ids, "message": f"已发送 {len(ids)} 条系统消息"})


def push_system_message(db, to_user_id, title, body, ref_type=None, ref_id=None):
    # AI-GEN-BEGIN
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute(
        """INSERT INTO messages
        (from_user_id, to_user_id, title, body, created_at, is_read, msg_type, ref_type, ref_id)
        VALUES (0,?,?,?,?,0,'system',?,?)""",
        (to_user_id, title, body, now, ref_type, ref_id),
    )
    return cur.lastrowid
    # AI-GEN-END


@app.get("/api/sensitive/catalog")
@login_required
def sensitive_catalog(user):
    rows = get_db().execute(
        """SELECT d.*, s.name AS system_name, s.code AS system_code
        FROM sensitive_perm_defs d
        JOIN systems s ON s.id = d.system_id
        WHERE d.enabled = 1 AND s.status = 'enabled'
        ORDER BY d.id"""
    ).fetchall()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})


@app.get("/api/sensitive/config")
@login_required
def sensitive_config_get(user):
    """敏感权限：超管或 sensitive_config 按钮可配置审批链。"""
    # AI-GEN-BEGIN
    if not (user_has_cap(user, "sensitive_config") or user_has_role(user, "super_admin")):
        return jsonify({"ok": False, "error": "无权配置敏感审批链"}), 403
    db = get_db()
    chain = db.execute(
        """SELECT c.*, u.display_name AS assignee_name, u.username AS assignee_username
        FROM approval_chain_steps c
        LEFT JOIN users u ON u.id = c.assignee_user_id
        WHERE c.flow_code = 'sensitive'
        ORDER BY c.step_order"""
    ).fetchall()
    overrides = db.execute(
        """SELECT o.id, o.step_key, o.dept_id, o.assignee_user_id,
            d.name AS dept_name, u.display_name AS assignee_name, u.username AS assignee_username
        FROM approval_chain_dept_overrides o
        JOIN departments d ON d.id = o.dept_id
        JOIN users u ON u.id = o.assignee_user_id
        WHERE o.flow_code = 'sensitive'
        ORDER BY o.step_key, d.name"""
    ).fetchall()
    systems = db.execute(
        """SELECT id, code, name, has_sensitive FROM systems
        WHERE status='enabled' ORDER BY id"""
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "chain": [dict(r) for r in chain],
            "overrides": [dict(r) for r in overrides],
            "systems": [dict(r) for r in systems],
            "note": "直属/一级默认按部门负责人动态解析；可按「申请人所属部门」单独指定审批人（不向父子部门继承）。"
            "财务为全局固定人。示例：徐好好∈BTIT→直属马宁，一级产品营销→吴锦志。",
        }
    )
    # AI-GEN-END


@app.post("/api/sensitive/config/chain")
@login_required
def sensitive_config_chain(user):
    if not (user_has_cap(user, "sensitive_config") or user_has_role(user, "super_admin")):
        return jsonify({"ok": False, "error": "无权配置审批链"}), 403
    data = request.get_json(force=True) or {}
    steps = data.get("steps") or []
    if not steps:
        return jsonify({"ok": False, "error": "步骤不能为空"}), 400
    db = get_db()
    db.execute("DELETE FROM approval_chain_steps WHERE flow_code = 'sensitive'")
    for i, s in enumerate(steps, start=1):
        key = s.get("step_key") or "user"
        # 直属/一级全局不用固定人；财务保留 assignee
        assignee = s.get("assignee_user_id") or None
        if key in ("direct_leader", "level1_leader"):
            assignee = None
        db.execute(
            """INSERT INTO approval_chain_steps
            (flow_code, step_order, step_key, step_label, assignee_user_id, enabled)
            VALUES ('sensitive',?,?,?,?,?)""",
            (
                i,
                key,
                s.get("step_label") or f"步骤{i}",
                assignee,
                1 if s.get("enabled", True) else 0,
            ),
        )
    db.commit()
    return jsonify({"ok": True, "message": "审批链已保存"})


# AI-GEN-BEGIN
@app.post("/api/sensitive/config/overrides")
@login_required
def sensitive_config_overrides(user):
    """保存直属/一级的部门特例（整表替换 sensitive 流）。"""
    if not (user_has_cap(user, "sensitive_config") or user_has_role(user, "super_admin")):
        return jsonify({"ok": False, "error": "无权配置审批链"}), 403
    data = request.get_json(force=True) or {}
    items = data.get("overrides")
    if items is None:
        return jsonify({"ok": False, "error": "缺少 overrides"}), 400
    db = get_db()
    db.execute(
        "DELETE FROM approval_chain_dept_overrides WHERE flow_code = 'sensitive'"
    )
    for it in items:
        key = it.get("step_key")
        dept_id = it.get("dept_id")
        uid = it.get("assignee_user_id")
        if key not in ("direct_leader", "level1_leader"):
            continue
        if not dept_id or not uid:
            continue
        db.execute(
            """INSERT INTO approval_chain_dept_overrides
            (flow_code, step_key, dept_id, assignee_user_id)
            VALUES ('sensitive',?,?,?)
            ON CONFLICT(flow_code, step_key, dept_id)
            DO UPDATE SET assignee_user_id = excluded.assignee_user_id""",
            (key, int(dept_id), int(uid)),
        )
    db.commit()
    return jsonify({"ok": True, "message": "部门特例已保存"})
# AI-GEN-END


@app.post("/api/sensitive/config/defs")
@login_required
def sensitive_config_defs(user):
    """权限目录增删改：系统管理员负责自己的系统（或超管）。"""
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "system_owner"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    action = data.get("action") or "upsert"
    db = get_db()
    if action == "toggle":
        row = db.execute(
            "SELECT * FROM sensitive_perm_defs WHERE id = ?", (data.get("id"),)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "权限项不存在"}), 404
        if not require_sys_owner(user, row["system_id"]):
            return jsonify({"ok": False, "error": "非负责系统"}), 403
        db.execute(
            "UPDATE sensitive_perm_defs SET enabled = ? WHERE id = ?",
            (1 if data.get("enabled") else 0, data.get("id")),
        )
        db.commit()
        return jsonify({"ok": True, "message": "已更新启用状态"})
    system_id = data.get("system_id")
    perm_code = (data.get("perm_code") or "").strip()
    perm_name = (data.get("perm_name") or "").strip()
    if not system_id or not perm_code or not perm_name:
        return jsonify({"ok": False, "error": "系统/编码/名称必填"}), 400
    if not require_sys_owner(user, int(system_id)):
        return jsonify({"ok": False, "error": "非负责系统"}), 403
    if data.get("id"):
        db.execute(
            """UPDATE sensitive_perm_defs
            SET system_id=?, perm_code=?, perm_name=?, description=?, enabled=?, is_sensitive=0
            WHERE id=?""",
            (
                system_id,
                perm_code,
                perm_name,
                data.get("description") or "",
                1 if data.get("enabled", True) else 0,
                data.get("id"),
            ),
        )
    else:
        parent_id = data.get("parent_id")
        db.execute(
            """INSERT INTO sensitive_perm_defs
            (system_id, perm_code, perm_name, description, parent_id, is_sensitive, enabled)
            VALUES (?,?,?,?,?,0,?)""",
            (
                system_id,
                perm_code,
                perm_name,
                data.get("description") or "",
                parent_id,
                1 if data.get("enabled", True) else 0,
            ),
        )
    db.commit()
    return jsonify({"ok": True, "message": "权限项已保存"})
    # AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/perms/sync-demo")
@login_required
def admin_perms_sync_demo(user, sid):
    """模拟从子系统同步权限目录。"""
    # AI-GEN-BEGIN
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    db = get_db()
    if not db.execute("SELECT id FROM systems WHERE id = ?", (sid,)).fetchone():
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    demo = [
        ("sync_root", "同步权限组", None),
        ("sync_read", "只读", "sync_root"),
        ("sync_write", "读写", "sync_root"),
    ]
    code_to_id = {}
    for r in db.execute(
        "SELECT id, perm_code FROM sensitive_perm_defs WHERE system_id=?", (sid,)
    ).fetchall():
        code_to_id[r["perm_code"]] = r["id"]
    added = 0
    for code, name, parent_code in demo:
        if code in code_to_id:
            continue
        parent_id = code_to_id.get(parent_code) if parent_code else None
        cur = db.execute(
            """INSERT INTO sensitive_perm_defs
            (system_id, perm_code, perm_name, description, parent_id, is_sensitive, enabled)
            VALUES (?,?,?,?,?,0,1)""",
            (sid, code, name, "子系统同步", parent_id),
        )
        code_to_id[code] = cur.lastrowid
        added += 1
    db.commit()
    return jsonify({"ok": True, "added": added, "message": f"已同步权限目录，新增 {added} 项"})
    # AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/perms/import")
@login_required
def admin_perms_import(user, sid):
    """手动导入权限目录：每行 编码,名称[,父编码]。"""
    # AI-GEN-BEGIN
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    data = request.get_json(force=True) or {}
    csv_text = (data.get("csv") or "").strip()
    if not csv_text:
        return jsonify({"ok": False, "error": "请粘贴导入内容"}), 400
    db = get_db()
    if not db.execute("SELECT id FROM systems WHERE id=?", (sid,)).fetchone():
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
    if lines and ("编码" in lines[0] or "code" in lines[0].lower()):
        lines = lines[1:]
    pending = []
    for ln in lines:
        parts = [p.strip() for p in ln.replace("\t", ",").split(",")]
        if len(parts) < 2:
            continue
        pending.append((parts[0], parts[1], parts[2] if len(parts) > 2 else None))
    code_to_id = {
        r["perm_code"]: r["id"]
        for r in db.execute(
            "SELECT id, perm_code FROM sensitive_perm_defs WHERE system_id=?", (sid,)
        ).fetchall()
    }
    added = 0
    for code, name, parent_code in pending:
        if code in code_to_id:
            continue
        parent_id = code_to_id.get(parent_code) if parent_code else None
        cur = db.execute(
            """INSERT INTO sensitive_perm_defs
            (system_id, perm_code, perm_name, description, parent_id, is_sensitive, enabled)
            VALUES (?,?,?,?,?,0,1)""",
            (sid, code, name, "手动导入", parent_id),
        )
        code_to_id[code] = cur.lastrowid
        added += 1
    db.commit()
    return jsonify({"ok": True, "added": added, "message": f"已导入 {added} 项权限"})
    # AI-GEN-END


@app.get("/api/apply/my-accounts")
@login_required
def apply_my_accounts(user):
    """自助关闭：本人可登录账号 / 含敏感账号列表（附系统权限目录）。"""
    # AI-GEN-BEGIN
    db = get_db()
    uid = int(request.args.get("user_id") or user["id"])
    if uid != user["id"] and not user_has_role(user, "hr_specialist", "super_admin", "dept_owner"):
        return jsonify({"ok": False, "error": "无权查看他人账号"}), 403
    if uid != user["id"] and user_has_role(user, "dept_owner"):
        target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not can_manage_member(user, target):
            return jsonify({"ok": False, "error": "仅可查看下级账号"}), 403
    # AI-GEN-BEGIN
    target_u = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if target_u:
        ensure_user_leuc_account(db, target_u)
        db.commit()
    # AI-GEN-END
    rows = db.execute(
        """SELECT a.id, a.account_name, a.account_label, a.can_login, a.has_sensitive,
                  a.perm_summary, a.system_id, s.name AS system_name, s.code AS system_code,
                  s.has_sensitive AS sys_has_sensitive, s.is_builtin AS system_is_builtin
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ?
        ORDER BY CASE WHEN s.code = ? THEN 1 ELSE 0 END, s.id, a.id""",
        (uid, LEUC_SYSTEM_CODE),
    ).fetchall()
    perms_by_sys = {}
    for r in db.execute(
        """SELECT id, system_id, perm_code, perm_name, parent_id, is_sensitive, enabled
        FROM sensitive_perm_defs WHERE enabled=1 ORDER BY id"""
    ).fetchall():
        perms_by_sys.setdefault(r["system_id"], []).append(dict(r))
    items = []
    for r in rows:
        item = dict(r)
        item["permissions"] = perms_by_sys.get(r["system_id"], [])
        items.append(item)
    return jsonify(
        {
            "ok": True,
            "accounts": items,
            "closable": [a for a in items if a["can_login"]],
            "sensitive": [a for a in items if a["has_sensitive"]],
        }
    )
    # AI-GEN-END


@app.post("/api/apply/submit")
@login_required
def apply_submit(user):
    """申请账号/账号延期/普通权限→直属；敏感权限与外部人员→直属+一级+财务。"""
    data = request.get_json(force=True) or {}
    apply_type = (data.get("type") or "").strip()
    # 兼容旧前端 password_extend
    if apply_type == "password_extend":
        apply_type = "account_extend"
    days = int(data.get("days") or 90)
    system_name = (data.get("system_name") or "").strip()
    remark = (data.get("remark") or "").strip()
    db = get_db()
    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END

    # 人事/负责人可代他人发起敏感/外部申请（链按目标用户解析）
    subject = user
    for_user_id = data.get("for_user_id")
    if for_user_id and int(for_user_id) != int(user["id"]):
        if not user_has_role(user, "hr_specialist", "super_admin", "dept_owner"):
            return jsonify({"ok": False, "error": "无权代他人申请"}), 403
        subject = db.execute("SELECT * FROM users WHERE id = ?", (int(for_user_id),)).fetchone()
        if not subject:
            return jsonify({"ok": False, "error": "目标用户不存在"}), 404
        if user_has_role(user, "dept_owner") and not can_manage_member(user, subject):
            return jsonify({"ok": False, "error": "仅可代本部门下级申请"}), 403

    # AI-GEN-BEGIN
    # 账号延期：大面板同申请；含敏感业务账号 → 直属→一级→财务，否则仅直属
    if apply_type == "account_extend":
        has_sens = user_has_sensitive_accounts(db, subject["id"])
        days = max(1, min(int(days or 90), 3650))
        todo_type = "账号延期"
        sens_tag = "（含敏感）" if has_sens else ""
        title = f"{subject['display_name']} · 账号延期 {days} 天{sens_tag}"
        init_title = f"账号延期 {days} 天{sens_tag}（审批中）"
        meta_extra = {
            "leuc_user_id": subject["id"],
            "days": days,
            "with_sensitive": has_sens,
        }
        if has_sens:
            steps = materialize_approval_chain(db, "sensitive", subject["id"])
            flow_code = "account_extend_sensitive"
            if not steps:
                result = apply_account_expire_extend(db, subject["id"], days)
                if not result.get("ok"):
                    return jsonify({"ok": False, "error": result.get("error")}), 400
                db.commit()
                return jsonify(
                    {
                        "ok": True,
                        "auto_approved": True,
                        "message": f"无待审批节点，已延期至 {result['new_expire']}",
                        "new_expire": result["new_expire"],
                    }
                )
        else:
            approver_id = find_approver(db, subject["id"])
            if not approver_id or int(approver_id) == int(subject["id"]):
                return jsonify({"ok": False, "error": "未找到直属审批人"}), 400
            steps = [("direct_leader", "直属领导", int(approver_id))]
            flow_code = "account_extend"
        # AI-GEN-BEGIN
        steps, cc_list = prepare_flow_steps(db, steps, subject["id"])
        meta_extra["user_permissions"] = user_permission_snapshot(db, subject["id"])
        # AI-GEN-END
        app_id, first_todo, first_assignee, step_preview = start_multi_step_apply(
            db,
            flow_code=flow_code,
            todo_type=todo_type,
            title=title,
            init_title=init_title,
            subject_id=subject["id"],
            initiator_id=user["id"],
            system_id=None,
            steps=steps,
            meta_extra=meta_extra,
            cc_list=cc_list,
        )
        db.commit()
        au = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?", (first_assignee,)
        ).fetchone()
        chain = " → ".join(s["label"] for s in step_preview)
        return jsonify(
            {
                "ok": True,
                "application_id": app_id,
                "todo_id": first_todo,
                "chain": step_preview,
                "with_sensitive": has_sens,
                "approver": dict(au) if au else None,
                "message": (
                    f"账号延期已提交，等待 {au['display_name']}（{step_preview[0]['label']}）；"
                    f"链：{chain}"
                ),
            }
        )

    # AI-GEN-BEGIN
    # 账号、权限关闭（多行）：与申请同款 items 落库，详情按行展示
    if apply_type == "account_perm_close":
        raw_items = data.get("items") or []
        if not isinstance(raw_items, list) or not raw_items:
            return jsonify({"ok": False, "error": "请至少填写一行关闭明细"}), 400
        line_items = []
        any_sens = False
        any_login = False
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            account_id = it.get("account_id")
            if not account_id:
                return jsonify({"ok": False, "error": "每一行须选择业务系统账号"}), 400
            acct = db.execute(
                """SELECT a.*, s.name AS system_name, s.has_sensitive AS sys_has_sensitive
                FROM user_system_accounts a
                JOIN systems s ON s.id = a.system_id WHERE a.id = ?""",
                (int(account_id),),
            ).fetchone()
            if not acct or int(acct["user_id"]) != int(subject["id"]):
                return jsonify({"ok": False, "error": "账号不存在或不属于该用户"}), 400
            close_type = (it.get("close_type") or "").strip()
            if close_type not in ("account", "perm"):
                return jsonify({"ok": False, "error": "请选择关闭账号或关闭权限"}), 400
            close_login = close_type == "account" or bool(it.get("close_login"))
            close_sensitive = close_type == "perm" and bool(it.get("close_sensitive"))
            perm_ids = [int(x) for x in (it.get("perm_ids") or []) if x is not None]
            perm_names = list(it.get("perm_names") or [])
            if close_type == "account":
                if not acct["can_login"]:
                    return jsonify(
                        {
                            "ok": False,
                            "error": f"{acct['system_name']} / {acct['account_name']} 已不可登录",
                        }
                    ), 400
                close_sensitive = False
                perm_ids = []
                perm_names = []
                any_login = True
            else:
                if not perm_ids and not close_sensitive:
                    return jsonify(
                        {"ok": False, "error": "关闭权限时请选择权限或勾选关闭敏感"}
                    ), 400
                if close_sensitive and not int(acct["has_sensitive"] or 0):
                    return jsonify(
                        {
                            "ok": False,
                            "error": f"{acct['system_name']} / {acct['account_name']} 当前无敏感权限",
                        }
                    ), 400
                if perm_ids and not perm_names:
                    for pid in perm_ids:
                        pr = db.execute(
                            "SELECT perm_name FROM sensitive_perm_defs WHERE id = ?",
                            (int(pid),),
                        ).fetchone()
                        if pr:
                            perm_names.append(pr["perm_name"])
                if close_sensitive:
                    any_sens = True
            line_items.append(
                {
                    "leuc_user_id": subject["id"],
                    "display_name": subject["display_name"],
                    "username": subject["username"],
                    "system_id": int(acct["system_id"]),
                    "system_name": acct["system_name"],
                    "account_id": int(acct["id"]),
                    "account_name": acct["account_name"],
                    "close_type": close_type,
                    "close_login": close_login,
                    "close_sensitive": close_sensitive,
                    "perm_ids": perm_ids,
                    "perm_names": perm_names,
                }
            )
        if not line_items:
            return jsonify({"ok": False, "error": "请至少填写一行关闭明细"}), 400
        todo_type = "账号、权限关闭"
        names = "、".join(
            dict.fromkeys(
                f"{x['system_name']}/{x['account_name']}" for x in line_items
            )
        )
        if any_sens:
            flow_code = "account_close_sensitive"
            steps = materialize_approval_chain(db, "sensitive", subject["id"])
            title = f"{subject['display_name']} · 账号、权限关闭 · {names}（含敏感）"
        else:
            flow_code = "account_close"
            approver_id = find_approver(db, subject["id"])
            if not approver_id or int(approver_id) == int(subject["id"]):
                return jsonify({"ok": False, "error": "未找到直属审批人"}), 400
            steps = [("direct_leader", "直属领导", int(approver_id))]
            title = f"{subject['display_name']} · 账号、权限关闭 · {names}"
        if not steps:
            return jsonify({"ok": False, "error": "审批链为空"}), 400
        init_title = f"账号、权限关闭 · {len(line_items)} 行（审批中）"
        primary = line_items[0]
        meta_extra = {
            "account_id": primary["account_id"],
            "system_id": primary["system_id"],
            "account_name": primary["account_name"],
            "system_name": primary["system_name"],
            "leuc_user_id": subject["id"],
            "close_login": any_login or any(x.get("close_login") for x in line_items),
            "close_sensitive": any_sens,
            "items": line_items,
        }
        app_id, first_todo, first_assignee, step_preview = start_multi_step_apply(
            db,
            flow_code=flow_code,
            todo_type=todo_type,
            title=title,
            init_title=init_title,
            subject_id=subject["id"],
            initiator_id=user["id"],
            system_id=primary["system_id"],
            steps=steps,
            meta_extra=meta_extra,
        )
        db.commit()
        au = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?", (first_assignee,)
        ).fetchone()
        return jsonify(
            {
                "ok": True,
                "application_id": app_id,
                "todo_id": first_todo,
                "chain": step_preview,
                "approver": dict(au) if au else None,
                "message": (
                    f"账号、权限关闭已提交（{len(line_items)} 行），等待 "
                    f"{au['display_name']}（{step_preview[0]['label']}）；"
                    f"链：{' → '.join(s['label'] for s in step_preview)}"
                ),
            }
        )
    # AI-GEN-END

    # 账号关闭：直属一步 application，待办类型与「账号、权限申请」对齐
    if apply_type == "account_close":
        account_id = data.get("account_id")
        if not account_id:
            return jsonify({"ok": False, "error": "请选择要关闭的系统账号"}), 400
        acct = db.execute(
            """SELECT a.*, s.name AS system_name FROM user_system_accounts a
            JOIN systems s ON s.id = a.system_id WHERE a.id = ?""",
            (int(account_id),),
        ).fetchone()
        if not acct or int(acct["user_id"]) != int(subject["id"]):
            return jsonify({"ok": False, "error": "账号不存在或不属于该用户"}), 400
        if not acct["can_login"]:
            return jsonify({"ok": False, "error": "该账号已不可登录"}), 400
        approver_id = find_approver(db, subject["id"])
        if not approver_id or int(approver_id) == int(subject["id"]):
            return jsonify({"ok": False, "error": "未找到直属审批人"}), 400
        todo_type = "账号、权限关闭"
        title = (
            f"{subject['display_name']} · 账号、权限关闭 · "
            f"{acct['system_name']} / {acct['account_name']}"
        )
        init_title = f"账号、权限关闭 · {acct['system_name']}（审批中）"
        meta_extra = {
            "account_id": acct["id"],
            "system_id": acct["system_id"],
            "account_name": acct["account_name"],
            "system_name": acct["system_name"],
            "leuc_user_id": subject["id"],
            "close_login": True,
            "items": [
                {
                    "leuc_user_id": subject["id"],
                    "display_name": subject["display_name"],
                    "username": subject["username"],
                    "system_id": acct["system_id"],
                    "system_name": acct["system_name"],
                    "account_id": acct["id"],
                    "account_name": acct["account_name"],
                    "close_type": "account",
                    "close_login": True,
                    "close_sensitive": False,
                    "perm_ids": [],
                    "perm_names": [],
                }
            ],
        }
        steps = [("direct_leader", "直属领导", int(approver_id))]
        app_id, first_todo, first_assignee, step_preview = start_multi_step_apply(
            db,
            flow_code="account_close",
            todo_type=todo_type,
            title=title,
            init_title=init_title,
            subject_id=subject["id"],
            initiator_id=user["id"],
            system_id=acct["system_id"],
            steps=steps,
            meta_extra=meta_extra,
        )
        db.commit()
        au = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?", (first_assignee,)
        ).fetchone()
        return jsonify(
            {
                "ok": True,
                "application_id": app_id,
                "todo_id": first_todo,
                "chain": step_preview,
                "approver": dict(au) if au else None,
                "message": f"账号、权限关闭已提交，等待直属 {au['display_name']} 审批",
            }
        )

    # 敏感权限关闭：与开通同链（直属→一级→财务）；待办类型统一「账号、权限关闭」
    if apply_type == "sensitive_close":
        account_id = data.get("account_id")
        if not account_id:
            return jsonify({"ok": False, "error": "请选择要关闭敏感权限的账号"}), 400
        acct = db.execute(
            """SELECT a.*, s.name AS system_name, s.id AS sid FROM user_system_accounts a
            JOIN systems s ON s.id = a.system_id WHERE a.id = ?""",
            (int(account_id),),
        ).fetchone()
        if not acct or int(acct["user_id"]) != int(subject["id"]):
            return jsonify({"ok": False, "error": "账号不存在或不属于该用户"}), 400
        if not acct["has_sensitive"]:
            return jsonify({"ok": False, "error": "该账号当前无敏感权限"}), 400
        flow_code = "sensitive_close"
        todo_type = "账号、权限关闭"
        title = (
            f"{subject['display_name']} · 账号、权限关闭 · "
            f"{acct['system_name']} / {acct['account_name']}（敏感）"
        )
        init_title = f"账号、权限关闭 · {acct['system_name']}（审批中）"
        meta_extra = {
            "account_id": acct["id"],
            "system_id": acct["system_id"],
            "account_name": acct["account_name"],
            "system_name": acct["system_name"],
            "close_sensitive": True,
            "leuc_user_id": subject["id"],
            "items": [
                {
                    "leuc_user_id": subject["id"],
                    "display_name": subject["display_name"],
                    "username": subject["username"],
                    "system_id": acct["system_id"],
                    "system_name": acct["system_name"],
                    "account_id": acct["id"],
                    "account_name": acct["account_name"],
                    "close_type": "perm",
                    "close_login": False,
                    "close_sensitive": True,
                    "perm_ids": [],
                    "perm_names": [],
                }
            ],
        }
        steps = materialize_approval_chain(db, "sensitive", subject["id"])
        if not steps:
            cur = db.execute(
                """INSERT INTO applications
                (flow_code, applicant_id, perm_def_id, system_id, title, status,
                 current_step, total_steps, created_at, updated_at, provisioned)
                VALUES (?,?,NULL,?,?,'approved',0,0,?,?,0)""",
                (flow_code, subject["id"], acct["system_id"], title, now, now),
            )
            app_id = cur.lastrowid
            app_row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
            result = auto_revoke_sensitive(db, app_row, account_id=acct["id"])
            push_system_message(
                db, subject["id"], "敏感权限已关闭",
                f"{acct['system_name']} / {acct['account_name']}",
            )
            db.commit()
            if not result.get("ok"):
                return jsonify({"ok": False, "error": result.get("error")}), 500
            return jsonify(
                {
                    "ok": True,
                    "application_id": app_id,
                    "auto_approved": True,
                    "message": f"无待审批节点，已关闭敏感：{result['system']}",
                }
            )
        app_id, first_todo, first_assignee, step_preview = start_multi_step_apply(
            db,
            flow_code=flow_code,
            todo_type=todo_type,
            title=title,
            init_title=init_title,
            subject_id=subject["id"],
            initiator_id=user["id"],
            system_id=acct["system_id"],
            steps=steps,
            meta_extra=meta_extra,
        )
        db.commit()
        au = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?", (first_assignee,)
        ).fetchone()
        return jsonify(
            {
                "ok": True,
                "application_id": app_id,
                "todo_id": first_todo,
                "chain": step_preview,
                "approver": dict(au) if au else None,
                "message": (
                    f"账号、权限关闭已提交，等待 {au['display_name']}（{step_preview[0]['label']}）；"
                    f"链：{' → '.join(s['label'] for s in step_preview)}"
                ),
            }
        )
    # AI-GEN-END

    # 敏感权限 / 外部人员：直属 → 一级 → 财务（申请人=审批人时跳过）
    if apply_type in ("sensitive", "external"):
        flow_code = apply_type
        todo_type = "敏感权限" if apply_type == "sensitive" else "外部人员"
        if apply_type == "sensitive":
            # 敏感=系统级复选框；可选兼容旧 perm_def_id
            perm_def_id = data.get("perm_def_id")
            system_id = data.get("system_id")
            perm = None
            if perm_def_id:
                perm = db.execute(
                    """SELECT d.*, s.name AS system_name, s.code AS system_code, s.has_sensitive
                    FROM sensitive_perm_defs d JOIN systems s ON s.id = d.system_id
                    WHERE d.id = ? AND d.enabled = 1""",
                    (perm_def_id,),
                ).fetchone()
                if not perm:
                    return jsonify({"ok": False, "error": "权限项不存在"}), 400
                system_id = perm["system_id"]
            if not system_id:
                return jsonify({"ok": False, "error": "请选择要申请敏感权限的系统"}), 400
            sys_row = db.execute(
                "SELECT * FROM systems WHERE id = ? AND status='enabled'", (int(system_id),)
            ).fetchone()
            if not sys_row:
                return jsonify({"ok": False, "error": "系统不存在或已禁用"}), 400
            if not int(sys_row["has_sensitive"] or 0):
                return jsonify({"ok": False, "error": "该系统未开启「是否有敏感权限」"}), 400
            title = (
                f"{subject['display_name']} · 敏感权限 · {sys_row['name']}"
                + (f" · {perm['perm_name']}" if perm else "")
            )
            perm_id = perm["id"] if perm else None
            system_id = sys_row["id"]
            init_title = f"敏感权限 · {sys_row['name']}（审批中）"
            meta_extra = {"system_id": system_id, "sensitive_flag": True}
            if perm_id:
                meta_extra["perm_def_id"] = perm_id
        else:
            reason = remark or system_name or "外部人员权限"
            title = f"{subject['display_name']} · 外部人员 · {reason}"
            perm_id = None
            system_id = None
            init_title = f"外部人员 · {reason}（审批中）"
            meta_extra = {"external": True, "remark": reason}

        steps = materialize_approval_chain(db, flow_code, subject["id"])
        # AI-GEN-BEGIN
        if flow_code == "sensitive" and system_id:
            steps = append_system_owner_step(db, system_id, steps)
        steps, cc_list = prepare_flow_steps(db, steps, subject["id"], system_id)
        # AI-GEN-END
        if not steps:
            cur = db.execute(
                """INSERT INTO applications
                (flow_code, applicant_id, perm_def_id, system_id, title, status,
                 current_step, total_steps, created_at, updated_at, provisioned)
                VALUES (?,?,?,?,?, 'approved', 0, 0, ?, ?, 0)""",
                (flow_code, subject["id"], perm_id, system_id, title, now, now),
            )
            app_id = cur.lastrowid
            msg = "无待审批节点（已跳过本人），"
            if flow_code == "sensitive" and system_id:
                app_row = db.execute(
                    "SELECT * FROM applications WHERE id = ?", (app_id,)
                ).fetchone()
                result = provision_account_apply(db, app_row, with_sensitive=True)
                db.commit()
                if not result.get("ok"):
                    return jsonify({"ok": False, "error": result.get("error")}), 500
                return jsonify(
                    {
                        "ok": True,
                        "application_id": app_id,
                        "auto_approved": True,
                        "message": msg + f"已开通：{result['system']} / {result['account']}",
                    }
                )
            db.commit()
            return jsonify(
                {"ok": True, "application_id": app_id, "auto_approved": True, "message": msg + "已通过"}
            )

        app_id, first_todo, first_assignee, step_preview = start_multi_step_apply(
            db,
            flow_code=flow_code,
            todo_type=todo_type,
            title=title,
            init_title=init_title,
            subject_id=subject["id"],
            initiator_id=user["id"],
            system_id=system_id,
            steps=steps,
            meta_extra=meta_extra,
            perm_id=perm_id,
            cc_list=cc_list,
        )
        db.commit()
        au = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?", (first_assignee,)
        ).fetchone()
        return jsonify(
            {
                "ok": True,
                "application_id": app_id,
                "todo_id": first_todo,
                "chain": step_preview,
                "approver": dict(au) if au else None,
                "message": (
                    f"已提交，等待 {au['display_name']}（{step_preview[0]['label']}）审批；"
                    f"链：{' → '.join(s['label'] for s in step_preview)}"
                ),
            }
        )

    type_map = {
        "account": ("申请账号", f"{subject['display_name']} · 申请账号" + (f"（{system_name}）" if system_name else "")),
        "normal_perm": ("普通权限", f"{subject['display_name']} · 普通权限" + (f"（{system_name or remark or '业务权限'}）")),
        "system_access": ("账号申请", f"{subject['display_name']} · 账号申请" + (system_name or "业务系统")),
    }
    if apply_type not in type_map:
        return jsonify({"ok": False, "error": "未知申请类型"}), 400
    todo_type, title = type_map[apply_type]
    if remark and apply_type != "normal_perm":
        title = f"{title} · {remark}"
    approver_id = find_approver(db, subject["id"])
    if not approver_id:
        return jsonify({"ok": False, "error": "未找到直属审批人"}), 400
    # 若解析到本人（极端兜底），跳到上级失败则报错
    if int(approver_id) == int(subject["id"]):
        return jsonify({"ok": False, "error": "无法跳过：无上级审批人"}), 400
    cur = db.execute(
        """INSERT INTO todos (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
        VALUES (?,?,?,?, 'pending', 'open', ?, NULL)""",
        (approver_id, user["id"], title, todo_type, now),
    )
    db.execute(
        """INSERT INTO todos (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
        VALUES (?,?,?,?, 'initiated', 'open', ?, NULL)""",
        (
            approver_id,
            user["id"],
            title.replace(f"{subject['display_name']} · ", "", 1),
            todo_type,
            now,
        ),
    )
    db.commit()
    approver = db.execute(
        "SELECT display_name, username FROM users WHERE id = ?", (approver_id,)
    ).fetchone()
    return jsonify(
        {
            "ok": True,
            "todo_id": cur.lastrowid,
            "approver": dict(approver) if approver else None,
            "message": f"已提交，等待直属上级 {approver['display_name']} 审批",
        }
    )


@app.post("/api/todo/<int:tid>/decide")
@login_required
def todo_decide(user, tid):
    data = request.get_json(force=True) or {}
    decision = data.get("decision")  # approved | rejected
    if decision not in ("approved", "rejected"):
        return jsonify({"ok": False, "error": "decision 须为 approved/rejected"}), 400
    # AI-GEN-BEGIN
    remark = (data.get("remark") or data.get("comment") or "").strip()
    # AI-GEN-END
    db = get_db()
    row = db.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "待办不存在"}), 404
    if row["assignee_id"] != user["id"] and not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "仅审批人可处理"}), 403
    if row["bucket"] != "pending":
        return jsonify({"ok": False, "error": "该待办已处理"}), 400

    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END
    app_id = row["application_id"] if "application_id" in row.keys() else None
    step_order_for_remark = row["step_order"] if "step_order" in row.keys() else None

    # 账号申请（原账号绑定）/授权：待办通过 = 确认建议匹配
    # 多级审批（有 application_id）走下方统一链路，不走 grant 直通
    if row["todo_type"] in ("账号授权", "账号绑定", "账号申请") and not app_id:
        grant = db.execute(
            "SELECT * FROM grant_applications WHERE todo_id = ?", (tid,)
        ).fetchone()
        if not grant:
            return jsonify({"ok": False, "error": "未找到授权申请，请到系统账号管理处理"}), 400
        if decision == "rejected":
            db.execute(
                "UPDATE grant_applications SET status = 'rejected', decided_at = ? WHERE id = ?",
                (now, grant["id"]),
            )
            db.execute(
                "UPDATE todos SET bucket = 'done', status = 'rejected' WHERE id = ?",
                (tid,),
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify({"ok": True, "message": "已驳回账号申请"})
        account_id = grant["suggested_account_id"]
        if not account_id:
            return jsonify(
                {
                    "ok": False,
                    "error": "无自动匹配账号，请到「系统账号管理」选择或新建绑定",
                    "need_manual": True,
                    "grant_id": grant["id"],
                }
            ), 400
        bind_leuc_to_system_account(db, grant["leuc_user_id"], account_id)
        db.execute(
            """UPDATE grant_applications
            SET status = 'bound', bound_account_id = ?, decided_at = ? WHERE id = ?""",
            (account_id, now, grant["id"]),
        )
        db.execute(
            "UPDATE todos SET bucket = 'done', status = 'approved' WHERE id = ?",
            (tid,),
        )
        try:
            meta = json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}
        if meta.get("oa_line_id"):
            db.execute(
                "UPDATE oa_form_lines SET handle_status = 'done', remark = ? WHERE id = ?",
                ("已绑定", meta["oa_line_id"]),
            )
            _oa_refresh_form_status(db, meta.get("oa_form_id"))
        # AI-GEN-BEGIN
        _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
        # AI-GEN-END
        db.commit()
        return jsonify({"ok": True, "message": "已确认账号申请并完成绑定", "provisioned": True})

    # AI-GEN-BEGIN
    # 兼容旧版单步「账号关闭」待办（无 application）
    if row["todo_type"] == "账号关闭" and not app_id:
        try:
            meta = json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}
        if decision == "rejected":
            db.execute(
                "UPDATE todos SET bucket = 'done', status = 'rejected' WHERE id = ?", (tid,)
            )
            db.execute(
                """UPDATE todos SET status = 'rejected'
                WHERE initiator_id = ? AND todo_type = '账号关闭' AND bucket = 'initiated' AND status = 'open'""",
                (row["initiator_id"],),
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify({"ok": True, "message": "已驳回账号关闭"})
        uid = meta.get("leuc_user_id") or row["initiator_id"]
        aid = meta.get("account_id")
        if not aid:
            return jsonify({"ok": False, "error": "待办缺少账号"}), 400
        result = close_user_system_account(db, int(uid), int(aid))
        if not result.get("ok"):
            return jsonify({"ok": False, "error": result.get("error")}), 400
        db.execute(
            "UPDATE todos SET bucket = 'done', status = 'approved' WHERE id = ?", (tid,)
        )
        db.execute(
            """UPDATE todos SET status = 'approved'
            WHERE initiator_id = ? AND todo_type = '账号关闭' AND bucket = 'initiated' AND status = 'open'""",
            (row["initiator_id"],),
        )
        push_system_message(
            db,
            int(uid),
            "账号已关闭",
            f"{result['system']} / {result['account']} 已按申请关闭登录",
        )
        # AI-GEN-BEGIN
        _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
        # AI-GEN-END
        db.commit()
        return jsonify(
            {
                "ok": True,
                "message": f"已关闭：{result['system']} / {result['account']}",
            }
        )
    # AI-GEN-END

    # OA：离职人员未匹配 · 人事核对
    if row["todo_type"] == "人员核对":
        try:
            meta = json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}
        db.execute(
            "UPDATE todos SET bucket = 'done', status = ? WHERE id = ?",
            (decision, tid),
        )
        if meta.get("oa_line_id"):
            db.execute(
                "UPDATE oa_form_lines SET handle_status = ?, remark = ? WHERE id = ?",
                (
                    "done" if decision == "approved" else "rejected",
                    "人事已核对" if decision == "approved" else "已驳回",
                    meta["oa_line_id"],
                ),
            )
            _oa_refresh_form_status(db, meta.get("oa_form_id"))
        # AI-GEN-BEGIN
        _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
        # AI-GEN-END
        db.commit()
        return jsonify({"ok": True, "message": "人员核对已处理"})

    # OA：关闭账号
    if row["todo_type"] == "关闭账号":
        try:
            meta = json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}
        if decision == "rejected":
            db.execute(
                "UPDATE todos SET bucket = 'done', status = 'rejected' WHERE id = ?", (tid,)
            )
            if meta.get("oa_line_id"):
                db.execute(
                    "UPDATE oa_form_lines SET handle_status = 'rejected', remark = ? WHERE id = ?",
                    ("已驳回关闭", meta["oa_line_id"]),
                )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify({"ok": True, "message": "已驳回关闭账号"})
        uid = meta.get("leuc_user_id")
        sid = meta.get("system_id")
        if not uid or not sid:
            return jsonify({"ok": False, "error": "待办缺少关闭对象"}), 400
        db.execute(
            "UPDATE user_system_accounts SET can_login = 0 WHERE user_id = ? AND system_id = ?",
            (uid, sid),
        )
        db.execute(
            """UPDATE system_accounts SET status = 'closed'
            WHERE leuc_user_id = ? AND system_id = ?""",
            (uid, sid),
        )
        db.execute(
            "UPDATE todos SET bucket = 'done', status = 'approved' WHERE id = ?", (tid,)
        )
        if meta.get("oa_line_id"):
            db.execute(
                "UPDATE oa_form_lines SET handle_status = 'done', remark = ? WHERE id = ?",
                ("已关闭账号", meta["oa_line_id"]),
            )
            _oa_refresh_form_status(db, meta.get("oa_form_id"))
        sys_row = db.execute("SELECT name FROM systems WHERE id = ?", (sid,)).fetchone()
        push_system_message(
            db,
            int(uid),
            "账号已关闭",
            f"系统负责人已关闭您在「{sys_row['name'] if sys_row else '系统'}」的登录账号（OA 离职）。",
        )
        # AI-GEN-BEGIN
        _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
        # AI-GEN-END
        db.commit()
        return jsonify({"ok": True, "message": "已关闭该系统账号", "closed": True})

    # OA：新建人员（人事确认后自动建 LEUC 账号，并继续发起绑定）
    if row["todo_type"] == "新建人员":
        try:
            meta = json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}
        if decision == "rejected":
            db.execute(
                "UPDATE todos SET bucket = 'done', status = 'rejected' WHERE id = ?", (tid,)
            )
            if meta.get("oa_form_id"):
                db.execute(
                    "UPDATE oa_forms SET status = 'rejected' WHERE id = ?",
                    (meta["oa_form_id"],),
                )
                db.execute(
                    "UPDATE oa_form_lines SET handle_status = 'rejected' WHERE form_id = ?",
                    (meta["oa_form_id"],),
                )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify({"ok": True, "message": "已驳回新建人员"})
        name = (meta.get("applicant_name") or "新员工").strip()
        oa_code = (meta.get("oa_person_code") or "").strip()
        from db import alloc_username

        username = alloc_username(db, name)
        cur = db.execute(
            """INSERT INTO users
            (username, password, display_name, role, dept_id, phone, email, itcode,
             password_expire, account_expire, feishu_bound, wecom_bound)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                username,
                "123456",
                name,
                "employee_a",
                3,
                meta.get("phone") or "",
                meta.get("email") or f"{username}@lecoo.com",
                oa_code or username,
                "2027-01-01",
                default_account_expire(90),
                0,
                0,
            ),
        )
        new_uid = cur.lastrowid
        db.execute(
            "UPDATE todos SET bucket = 'done', status = 'approved', meta = ? WHERE id = ?",
            (
                json.dumps({**meta, "created_user_id": new_uid, "username": username}, ensure_ascii=False),
                tid,
            ),
        )
        if meta.get("oa_form_id"):
            db.execute(
                "UPDATE oa_forms SET leuc_user_id = ? WHERE id = ?",
                (new_uid, meta["oa_form_id"]),
            )
            # 继续为明细发起绑定
            _oa_spawn_bind_for_form(db, meta["oa_form_id"], new_uid, user["id"])
        # AI-GEN-BEGIN
        _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
        # AI-GEN-END
        db.commit()
        return jsonify(
            {
                "ok": True,
                "message": f"已新建人员 {name}（{username}），并已发起账号申请待办",
                "username": username,
            }
        )

    # AI-GEN-BEGIN
    # 知会确认：只阅读确认，不推进主审批链
    try:
        _meta_cc = json.loads(row["meta"] or "{}")
    except Exception:
        _meta_cc = {}
    if row["todo_type"] == "知会确认" or _meta_cc.get("cc"):
        db.execute(
            "UPDATE todos SET bucket = 'done', status = ? WHERE id = ?",
            ("approved" if decision == "approved" else "rejected", tid),
        )
        _persist_decide_remark(db, tid, remark)
        db.commit()
        return jsonify({"ok": True, "message": "已阅知会", "cc": True})
    # AI-GEN-END

    # 敏感权限开通 / 关闭 / 外部人员 / 账号申请 / 账号权限关闭 / 账号延期：多级审批
    if app_id and row["todo_type"] in (
        "敏感权限",
        "敏感权限关闭",
        "账号、权限关闭",
        "账号关闭",
        "账号延期",
        "外部人员",
        "账号申请",
        "北森离职关闭",
    ):
        app_row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if not app_row:
            return jsonify({"ok": False, "error": "申请单不存在"}), 404
        flow_todo_type = row["todo_type"]
        # AI-GEN-BEGIN
        # step_order=0 表示申请人修改重提，不能用 `or` 回落
        raw_so = row["step_order"] if "step_order" in row.keys() else None
        if raw_so is None:
            step_order = int(app_row["current_step"] or 1)
        else:
            step_order = int(raw_so)
        step_order_for_remark = step_order if step_order > 0 else None
        # 开通前校验：新建账号须录入账号名（避免先落库再报错）
        if decision == "approved" and step_order > 0:
            cur_step = db.execute(
                """SELECT step_key FROM application_steps
                WHERE application_id = ? AND step_order = ?""",
                (app_id, step_order),
            ).fetchone()
            nxt_chk = db.execute(
                """SELECT id, step_key FROM application_steps
                WHERE application_id = ? AND step_order = ?""",
                (app_id, step_order + 1),
            ).fetchone()
            try:
                meta_pre = json.loads(row["meta"] or "{}")
            except Exception:
                meta_pre = {}
            at_owner_effect = cur_step and cur_step["step_key"] == "system_owner" and (
                not nxt_chk or nxt_chk["step_key"] == "applicant_confirm"
            )
            if at_owner_effect and meta_pre.get("create_new"):
                targets = get_provision_targets(db, meta_pre, app_row)
                provisions = data.get("provisions")
                if not isinstance(provisions, list):
                    provisions = []
                # 单行兼容旧字段
                if (
                    not provisions
                    and len(targets) == 1
                    and (
                        data.get("account_id")
                        or (data.get("account_name") or "").strip()
                    )
                ):
                    provisions = [
                        {
                            "line_key": targets[0].get("line_key"),
                            "system_id": targets[0]["system_id"],
                            "account_id": data.get("account_id"),
                            "account_name": (data.get("account_name") or "").strip()
                            or None,
                        }
                    ]
                prov_probe = [dict(p) for p in provisions if isinstance(p, dict)]
                missing = []
                for t in targets:
                    p = _match_provision(prov_probe, t)
                    if not p or not (
                        p.get("account_id") or (p.get("account_name") or "").strip()
                    ):
                        missing.append(t)
                        continue
                    p["_used"] = True
                if missing:
                    names = "、".join(
                        t.get("label") or t.get("system_name") for t in missing
                    )
                    return jsonify(
                        {
                            "ok": False,
                            "error": f"请为以下申请行选择账号后再开通：{names}",
                            "need_account_input": True,
                            "todo_id": tid,
                            "application_id": app_id,
                            "applicant_id": app_row["applicant_id"],
                            "system_id": app_row["system_id"],
                            "provision_targets": targets,
                            "missing_line_keys": [t.get("line_key") for t in missing],
                        }
                    ), 400
                data["_resolved_provisions"] = provisions
        # AI-GEN-END
        db.execute(
            "UPDATE todos SET bucket = 'done', status = ? WHERE id = ?",
            (decision, tid),
        )
        if step_order > 0:
            db.execute(
                """UPDATE application_steps SET status = ?, decided_at = ?
                WHERE application_id = ? AND step_order = ?""",
                (decision, now, app_id, step_order),
            )
        if decision == "rejected":
            # AI-GEN-BEGIN
            reject_to = data.get("reject_to_step")
            # 含 0=申请人；未传则直接结束
            if reject_to not in (None, ""):
                result = reject_to_specified_step(
                    db,
                    app_id=app_id,
                    current_step_order=int(step_order),
                    reject_to_step=int(reject_to),
                    todo_row=row,
                    remark=remark,
                    now=now,
                )
                if not result.get("ok"):
                    return jsonify(result), 400
                db.commit()
                return jsonify(result)
            # AI-GEN-END
            db.execute(
                "UPDATE applications SET status = 'rejected', updated_at = ? WHERE id = ?",
                (now, app_id),
            )
            db.execute(
                """UPDATE todos SET status = 'rejected'
                WHERE application_id = ? AND bucket = 'initiated'""",
                (app_id,),
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify({"ok": True, "message": "已驳回，申请结束"})

        # AI-GEN-BEGIN
        # 驳回改单后再次通过：直达原驳回人
        try:
            reject_from = (
                app_row["reject_from_step"]
                if app_row and "reject_from_step" in app_row.keys()
                else None
            )
        except Exception:
            reject_from = None
        if (
            decision == "approved"
            and (app_row["status"] if app_row else None) == "returned"
            and reject_from not in (None, "", 0, "0")
        ):
            try:
                meta_jump = json.loads(row["meta"] or "{}")
            except Exception:
                meta_jump = {}
            meta_jump.pop("needs_resubmit", None)
            meta_jump.pop("reject_from_step", None)
            meta_jump.pop("reject_to_step", None)
            meta_json = json.dumps(meta_jump, ensure_ascii=False)
            db.execute("UPDATE todos SET meta = ? WHERE id = ?", (meta_json, tid))
            # 同步 initiated / 同单其它待办 meta 业务字段
            db.execute(
                """UPDATE todos SET meta = ? WHERE application_id = ?
                AND bucket IN ('initiated', 'pending') AND id != ?""",
                (meta_json, app_id, tid),
            )
            db.execute(
                "UPDATE todos SET bucket = 'done', status = 'approved', remark = ? WHERE id = ?",
                ((remark or "").strip() or "修改后重提", tid),
            )
            result = jump_to_reject_from_step(
                db,
                app_id=app_id,
                reject_from_step=int(reject_from),
                todo_row=row,
                meta_json=meta_json,
                remark=remark,
                now=now,
                todo_type=flow_todo_type,
            )
            if not result.get("ok"):
                return jsonify(result), 400
            _persist_decide_remark(
                db, tid, remark, app_id=app_id, step_order=step_order_for_remark
            )
            db.commit()
            return jsonify(result)
        # AI-GEN-END

        nxt = db.execute(
            """SELECT * FROM application_steps
            WHERE application_id = ? AND step_order = ?""",
            (app_id, step_order + 1),
        ).fetchone()
        # AI-GEN-BEGIN
        # 下一步是申请人确认：先执行业务开通/延期，再流转确认
        if (
            decision == "approved"
            and nxt
            and nxt["step_key"] == "applicant_confirm"
        ):
            try:
                meta_fx = json.loads(row["meta"] or "{}")
            except Exception:
                meta_fx = {}
            fx_msg = None
            if app_row["flow_code"] in ("account_extend", "account_extend_sensitive"):
                uid = meta_fx.get("leuc_user_id") or app_row["applicant_id"]
                days = int(meta_fx.get("days") or 90)
                result = apply_account_expire_extend(db, int(uid), days)
                if not result.get("ok"):
                    return jsonify({"ok": False, "error": result.get("error") or "延期失败"}), 400
                push_system_message(
                    db,
                    int(uid),
                    "账号延期已生效",
                    f"已延期 {result['days']} 天，新有效期 {result['new_expire']}（待申请人确认关闭）",
                )
                fx_msg = f"已延期至 {result['new_expire']}"
                meta_fx["effect_done"] = True
                meta_fx["new_expire"] = result["new_expire"]
            elif app_row["flow_code"] in (
                "sensitive",
                "account_apply",
                "account_apply_sensitive",
            ):
                with_sens = app_row["flow_code"] in (
                    "sensitive",
                    "account_apply_sensitive",
                ) or bool(meta_fx.get("with_sensitive"))
                create_new = bool(meta_fx.get("create_new"))
                if create_new or app_row["flow_code"].startswith("account_apply"):
                    # AI-GEN-BEGIN
                    provisions = data.get("_resolved_provisions") or data.get("provisions")
                    if not isinstance(provisions, list) or not provisions:
                        if data.get("account_id") or (data.get("account_name") or "").strip():
                            targets = get_provision_targets(db, meta_fx, app_row)
                            t0 = targets[0] if targets else None
                            provisions = [
                                {
                                    "line_key": (t0 or {}).get("line_key"),
                                    "system_id": (t0 or {}).get("system_id")
                                    or app_row["system_id"],
                                    "account_id": data.get("account_id"),
                                    "account_name": (data.get("account_name") or "").strip()
                                    or None,
                                }
                            ]
                    result = provision_account_apply_multi(
                        db,
                        app_row,
                        provisions,
                        meta=meta_fx,
                        with_sensitive=with_sens,
                        remark=(data.get("remark") or remark or "").strip() or None,
                    )
                    # AI-GEN-END
                    if not result.get("ok"):
                        return jsonify(result), 400
                    fx_msg = result.get("message") or (
                        f"已开通 {result.get('system')} / {result.get('account')}"
                    )
                    meta_fx["effect_done"] = True
                    meta_fx["provisioned_items"] = result.get("items") or []
                else:
                    result = auto_provision_sensitive(db, app_row)
                    if not result.get("ok"):
                        return jsonify({"ok": False, "error": result.get("error")}), 400
                    fx_msg = "敏感权限已开通"
                    meta_fx["effect_done"] = True
            # 把 effect 标记写回待办 meta，供确认节点使用
            new_meta = json.dumps(meta_fx, ensure_ascii=False)
            db.execute("UPDATE todos SET meta = ? WHERE id = ?", (new_meta, tid))
            row = dict(row)
            row["meta"] = new_meta
        # AI-GEN-END
        if nxt:
            db.execute(
                "UPDATE applications SET current_step = ?, updated_at = ? WHERE id = ?",
                (nxt["step_order"], now, app_id),
            )
            tcur = db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at,
                 application_id, step_order, meta)
                VALUES (?,?,?,?, 'pending', 'open', ?, ?, ?, ?)""",
                (
                    nxt["assignee_id"],
                    app_row["applicant_id"],
                    f"{app_row['title']} · {nxt['step_label']}",
                    flow_todo_type,
                    now,
                    app_id,
                    nxt["step_order"],
                    row["meta"],
                ),
            )
            db.execute(
                """UPDATE application_steps SET status = 'pending', todo_id = ?
                WHERE id = ?""",
                (tcur.lastrowid, nxt["id"]),
            )
            # AI-GEN-BEGIN
            # 到达系统管理员节点时同步知会其直接领导
            if nxt["step_key"] == "system_owner":
                try:
                    meta_n = json.loads(row["meta"] or "{}")
                except Exception:
                    meta_n = {}
                ccs = meta_n.get("pending_ccs") or collect_cc_for_system_owners(
                    db,
                    find_approver,
                    [("system_owner", nxt["step_label"], nxt["assignee_id"])],
                    app_row["applicant_id"],
                )
                if ccs:
                    spawn_cc_todos(
                        db,
                        app_id=app_id,
                        initiator_id=app_row["applicant_id"],
                        todo_type=flow_todo_type,
                        title=app_row["title"],
                        meta=meta_n,
                        ccs=ccs,
                        now=now,
                    )
            # AI-GEN-END
            db.execute(
                """UPDATE todos SET title = ?
                WHERE application_id = ? AND bucket = 'initiated'""",
                (
                    f"{app_row['title'].split(' · ', 1)[-1] if ' · ' in app_row['title'] else app_row['title']}（审批中·{nxt['step_label']}）",
                    app_id,
                ),
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            au = db.execute(
                "SELECT display_name FROM users WHERE id = ?", (nxt["assignee_id"],)
            ).fetchone()
            return jsonify(
                {
                    "ok": True,
                    "message": f"已通过，流转至 {au['display_name'] if au else ''}（{nxt['step_label']}）",
                    "next_step": nxt["step_label"],
                }
            )

        # AI-GEN-BEGIN
        # 无下一步：申请人确认后关闭；或最终生效
        cur_fin = db.execute(
            """SELECT step_key FROM application_steps
            WHERE application_id = ? AND step_order = ?""",
            (app_id, step_order),
        ).fetchone()
        final_status = "done" if (cur_fin and cur_fin["step_key"] == "applicant_confirm") else "approved"
        # AI-GEN-END
        db.execute(
            "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
            (final_status, now, app_id),
        )
        db.execute(
            """UPDATE todos SET status = 'approved'
            WHERE application_id = ? AND bucket = 'initiated'""",
            (app_id,),
        )
        # AI-GEN-BEGIN
        # 申请人确认关闭：业务已在上一步生效
        if cur_fin and cur_fin["step_key"] == "applicant_confirm":
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            db.commit()
            return jsonify({"ok": True, "message": "已确认，申请单关闭", "closed": True})
        # AI-GEN-END
        # AI-GEN-BEGIN
        if app_row["flow_code"] in ("account_extend", "account_extend_sensitive"):
            try:
                meta = json.loads(row["meta"] or "{}")
            except Exception:
                meta = {}
            uid = meta.get("leuc_user_id") or app_row["applicant_id"]
            days = int(meta.get("days") or 90)
            result = apply_account_expire_extend(db, int(uid), days)
            if not result.get("ok"):
                return jsonify({"ok": False, "error": result.get("error") or "延期失败"}), 400
            push_system_message(
                db,
                int(uid),
                "账号延期已生效",
                f"已延期 {result['days']} 天，新有效期 {result['new_expire']}",
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify(
                {
                    "ok": True,
                    "message": f"审批完成，已延期至 {result['new_expire']}",
                    "new_expire": result["new_expire"],
                }
            )
        if app_row["flow_code"] in (
            "account_close",
            "sensitive_close",
            "account_close_sensitive",
        ):
            # AI-GEN-BEGIN
            try:
                meta = json.loads(row["meta"] or "{}")
            except Exception:
                meta = {}
            uid = meta.get("leuc_user_id") or app_row["applicant_id"]
            result = execute_account_perm_close_items(db, app_row, meta)
            if not result.get("ok"):
                _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
                db.commit()
                return jsonify({"ok": False, "error": result.get("error") or "关闭失败"}), 400
            summary = result.get("summary") or "；".join(result.get("results") or []) or "已关闭"
            push_system_message(db, int(uid), "账号、权限关闭已生效", summary)
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            db.commit()
            return jsonify(
                {
                    "ok": True,
                    "message": f"审批完成：{summary}",
                    "revoked": True,
                    "results": result.get("results") or [],
                }
            )
            # AI-GEN-END
        if app_row["flow_code"] in ("beisen_leave", "beisen_leave_sensitive"):
            # AI-GEN-BEGIN
            try:
                meta = json.loads(row["meta"] or "{}")
            except Exception:
                meta = {}
            uid = meta.get("leuc_user_id") or app_row["applicant_id"]
            aid = meta.get("account_id")
            if not aid:
                return jsonify({"ok": False, "error": "离职单缺少账号"}), 400
            result = close_user_system_account(db, int(uid), int(aid))
            if not result.get("ok"):
                return jsonify({"ok": False, "error": result.get("error") or "关闭失败"}), 400
            line_id = meta.get("oa_line_id")
            form_id = meta.get("oa_form_id")
            if line_id:
                db.execute(
                    """UPDATE oa_form_lines SET handle_status = 'done', remark = ?
                    WHERE id = ?""",
                    (
                        f"审批通过已关闭：{result.get('system')} / {result.get('account')}",
                        line_id,
                    ),
                )
            if form_id:
                _oa_refresh_form_status(db, form_id)
            push_system_message(
                db,
                int(uid),
                "北森离职账号已关闭",
                f"{result['system']} / {result['account']} 已按北森离职审批关闭",
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify(
                {
                    "ok": True,
                    "message": f"审批完成，已关闭：{result['system']} / {result['account']}",
                    "closed_leuc": bool(result.get("closed_leuc")),
                    "revoked": True,
                }
            )
            # AI-GEN-END
        # AI-GEN-END
        # AI-GEN-BEGIN
        if app_row["flow_code"] in ("sensitive", "account_apply", "account_apply_sensitive"):
            # 若最后一步是系统负责人开通，由当前审批人开通
            last = db.execute(
                """SELECT step_key FROM application_steps
                WHERE application_id = ? AND step_key != 'applicant_confirm'
                ORDER BY step_order DESC LIMIT 1""",
                (app_id,),
            ).fetchone()
            cur_last = db.execute(
                """SELECT step_key FROM application_steps
                WHERE application_id = ? AND step_order = ?""",
                (app_id, step_order),
            ).fetchone()
            # 若当前已是确认节点或 effect 已做过，跳过重复开通
            try:
                meta_skip = json.loads(row["meta"] or "{}")
            except Exception:
                meta_skip = {}
            if meta_skip.get("effect_done") or (
                cur_last and cur_last["step_key"] == "applicant_confirm"
            ):
                _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
                db.commit()
                return jsonify({"ok": True, "message": "审批完成"})
            with_sens = app_row["flow_code"] in ("sensitive", "account_apply_sensitive")
            try:
                meta = json.loads(row["meta"] or "{}")
            except Exception:
                meta = {}
            create_new = bool(meta.get("create_new"))
            account_name = (data.get("account_name") or "").strip()
            account_id = data.get("account_id")
            remark = (data.get("remark") or "").strip()
            # AI-GEN-BEGIN
            # 新建账号：系统负责人须为每个待开通系统选账号
            if last and last["step_key"] == "system_owner" and create_new:
                targets = get_provision_targets(db, meta, app_row)
                provisions = data.get("provisions")
                if not isinstance(provisions, list):
                    provisions = []
                if (
                    not provisions
                    and len(targets) == 1
                    and (account_id or account_name)
                ):
                    provisions = [
                        {
                            "line_key": targets[0].get("line_key"),
                            "system_id": targets[0]["system_id"],
                            "account_id": account_id,
                            "account_name": account_name or None,
                        }
                    ]
                prov_probe = [dict(p) for p in provisions if isinstance(p, dict)]
                missing = []
                for t in targets:
                    p = _match_provision(prov_probe, t)
                    if not p or not (
                        p.get("account_id") or (p.get("account_name") or "").strip()
                    ):
                        missing.append(t)
                        continue
                    p["_used"] = True
                if missing:
                    names = "、".join(
                        t.get("label") or t.get("system_name") for t in missing
                    )
                    return jsonify(
                        {
                            "ok": False,
                            "error": f"请为以下申请行选择账号后再开通：{names}",
                            "need_account_input": True,
                            "todo_id": tid,
                            "application_id": app_id,
                            "applicant_id": app_row["applicant_id"],
                            "system_id": app_row["system_id"],
                            "provision_targets": targets,
                            "missing_line_keys": [t.get("line_key") for t in missing],
                        }
                    ), 400
                result = provision_account_apply_multi(
                    db,
                    app_row,
                    provisions,
                    meta=meta,
                    with_sensitive=with_sens,
                    remark=remark or None,
                )
            elif app_row["flow_code"] == "sensitive":
                result = provision_account_apply(db, app_row, with_sensitive=True)
            else:
                result = provision_account_apply(
                    db,
                    app_row,
                    with_sensitive=False,
                    account_name=account_name or None,
                    account_id=account_id,
                    remark=remark or None,
                )
            # AI-GEN-END
            if not result.get("ok"):
                return jsonify(result), 400
            # 回写开通信息到待办 meta，便于排查
            meta.update(
                {
                    "provisioned_account": result.get("account"),
                    "provisioned_account_id": result.get("account_id"),
                    "pool_account_id": result.get("pool_account_id"),
                    "provisioned_items": result.get("items") or [],
                    "remark": remark,
                }
            )
            db.execute(
                "UPDATE todos SET meta = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), tid),
            )
            # AI-GEN-BEGIN
            _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
            # AI-GEN-END
            db.commit()
            return jsonify(
                {
                    "ok": True,
                    "message": f"审批完成，已开通：{result['system']} / {result['account']}",
                    "provisioned": True,
                    "account_id": result.get("account_id"),
                    "pool_account_id": result.get("pool_account_id"),
                    "application_id": app_id,
                    "applicant_id": app_row["applicant_id"],
                    "system_id": app_row["system_id"],
                }
            )
        # AI-GEN-END
        push_system_message(
            db,
            app_row["applicant_id"],
            "外部人员申请已通过",
            app_row["title"],
        )
        # AI-GEN-BEGIN
        _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
        # AI-GEN-END
        db.commit()
        return jsonify({"ok": True, "message": "外部人员审批完成"})

    db.execute(
        "UPDATE todos SET bucket = 'done', status = ? WHERE id = ?",
        (decision, tid),
    )
    db.execute(
        """UPDATE todos SET status = ? WHERE initiator_id = ? AND title LIKE ?
        AND bucket = 'initiated' AND status = 'open'""",
        (
            decision,
            row["initiator_id"],
            "%" + (row["todo_type"] or "") + "%",
        ),
    )
    # AI-GEN-BEGIN
    # 兼容旧版单步「账号延期」待办（无 application）
    if (
        decision == "approved"
        and row["todo_type"] in ("账号延期", "密码延期")
        and not app_id
        and row["initiator_id"]
    ):
        target_id = row["initiator_id"]
        days = 90
        try:
            meta = json.loads(row["meta"] or "{}") if "meta" in row.keys() else {}
            if meta.get("leuc_user_id"):
                target_id = int(meta["leuc_user_id"])
            if meta.get("days"):
                days = int(meta["days"])
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        apply_account_expire_extend(db, target_id, days)
    # AI-GEN-END
    # AI-GEN-BEGIN
    _persist_decide_remark(db, tid, remark, app_id=app_id, step_order=step_order_for_remark)
    # AI-GEN-END
    db.commit()
    return jsonify({"ok": True, "message": "已通过" if decision == "approved" else "已驳回"})


@app.post("/api/dept/members")
@login_required
def add_member(user):
    """手动添加：仅支持新建外部人员（落到内置「外部人员」部门）。"""
    # AI-GEN-BEGIN
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    display_name = (data.get("display_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    role = "external"
    person_type = "external"
    db = get_db()
    migrate_schema(db)
    ensure_roles_seeded(db)
    dept_id = get_external_dept_id(db)
    if not dept_id:
        return jsonify({"ok": False, "error": "外部人员部门未初始化"}), 500
    if not display_name:
        return jsonify({"ok": False, "error": "姓名必填"}), 400
    want = (data.get("username") or "").strip() or alloc_username(db, display_name)
    if normalize_username(want) == SYSTEM_ADMIN_USERNAME:
        return jsonify({"ok": False, "error": "用户名 admin 为系统保留"}), 400
    ok, uname_or_err = ensure_username_available(db, want)
    if not ok:
        return jsonify({"ok": False, "error": uname_or_err}), 400
    username = uname_or_err
    preview_base = name_to_pinyin(display_name)
    beisen_user_id = (data.get("beisen_user_id") or "").strip() or None
    password = gen_account_password()
    acct_expire = default_account_expire(90)
    cur = db.execute(
        """INSERT INTO users
        (username, password, display_name, role, dept_id, phone, email, itcode,
         password_expire, account_expire, person_type, beisen_user_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            username,
            password,
            display_name,
            role,
            dept_id,
            phone or None,
            email or None,
            username,
            "2026-12-31",
            acct_expire,
            person_type,
            beisen_user_id,
        ),
    )
    uid = int(cur.lastrowid)
    try:
        db.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, ?)",
            (uid, role),
        )
    except Exception:
        pass
    notify = record_credential_notify(
        db,
        user_id=uid,
        username=username,
        password=password,
        phone=phone or None,
        email=email or None,
        reason="external_create",
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "user": {
                "id": uid,
                "username": username,
                "display_name": display_name,
                "pinyin_base": preview_base,
                "person_type": person_type,
                "dept_id": dept_id,
            },
            "notify": {
                "channel": notify.get("channel"),
                "target": notify.get("target"),
                "status": notify.get("status"),
            },
            "message": (
                f"已创建外部人员 {username}；初始密码已按"
                f"{'手机' if notify.get('channel')=='phone' else ('邮箱' if notify.get('channel')=='email' else '无联系方式')}"
                f"写入发送记录（未真实发送）"
            ),
        }
    )
    # AI-GEN-END


@app.patch("/api/org/members/<int:uid>/beisen-user-id")
@login_required
def patch_member_beisen_user_id(user, uid):
    """部门侧维护北森 BeisenUserID（SSO uty=id 用）。"""
    # AI-GEN-BEGIN
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    if not (
        user_has_role(user, "super_admin", "hr_specialist")
        or can_manage_dept(user, target["dept_id"])
    ):
        return jsonify({"ok": False, "error": "无权限修改该人员"}), 403
    data = request.get_json(force=True) or {}
    bid = (data.get("beisen_user_id") or "").strip()
    if bid:
        clash = db.execute(
            "SELECT id, username FROM users WHERE beisen_user_id = ? AND id != ?",
            (bid, uid),
        ).fetchone()
        if clash:
            return jsonify(
                {
                    "ok": False,
                    "error": f"北森用户ID已被 {clash['username']} 占用",
                }
            ), 400
    db.execute(
        "UPDATE users SET beisen_user_id = ? WHERE id = ?",
        (bid or None, uid),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return jsonify({"ok": True, "user": row_user(row)})
    # AI-GEN-END


@app.post("/api/dept/members/batch-grant")
@login_required
def batch_grant_systems(user):
    """兼容旧接口：部门负责人「申请绑定」。"""
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    if "leuc_user_ids" not in data and data.get("user_ids"):
        data = {**data, "leuc_user_ids": data.get("user_ids")}
        request._cached_json = (data, data)
    return bind_apply(user)


@app.get("/api/dept/apply-systems")
@login_required
def dept_apply_systems(user):
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    rows = get_db().execute(
        "SELECT id, code, name, access_mode, status FROM systems WHERE status='enabled' AND access_mode='apply' AND code != ? ORDER BY id",
        (LEUC_SYSTEM_CODE,),
    ).fetchall()
    return jsonify({"ok": True, "systems": [dict(r) for r in rows]})


@app.post("/api/dept/members/batch")
@login_required
def batch_members(user):
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    ids = data.get("user_ids") or []
    action = data.get("action")  # grant_login | revoke_login | mark_sensitive
    system_code = data.get("system_code") or "oa"
    db = get_db()
    sys_row = db.execute("SELECT id FROM systems WHERE code = ?", (system_code,)).fetchone()
    if not sys_row:
        return jsonify({"ok": False, "error": "系统不存在"}), 400
    sid = sys_row["id"]
    updated = 0
    for uid in ids:
        row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row or not can_manage_member(user, row):
            continue
        if action == "grant_login":
            exists = db.execute(
                "SELECT id FROM user_system_accounts WHERE user_id = ? AND system_id = ?",
                (uid, sid),
            ).fetchone()
            if exists:
                db.execute(
                    "UPDATE user_system_accounts SET can_login = 1 WHERE id = ?",
                    (exists["id"],),
                )
            else:
                db.execute(
                    """INSERT INTO user_system_accounts
                    (user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
                    VALUES (?,?,?,?,1,1,0,?)""",
                    (uid, sid, f"{row['username']}_{system_code}", "批量开通", "普通权限"),
                )
            updated += 1
        elif action == "revoke_login":
            db.execute(
                "UPDATE user_system_accounts SET can_login = 0 WHERE user_id = ? AND system_id = ?",
                (uid, sid),
            )
            updated += 1
        elif action == "mark_sensitive":
            db.execute(
                "UPDATE user_system_accounts SET has_sensitive = 1 WHERE user_id = ? AND system_id = ?",
                (uid, sid),
            )
            updated += 1
    db.commit()
    return jsonify({"ok": True, "updated": updated})


@app.post("/api/dept/members/import")
@login_required
def import_members(user):
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    text = data.get("csv") or ""
    dept_id = int(data.get("dept_id") or user["dept_id"])
    if not can_manage_dept(user, dept_id):
        return jsonify({"ok": False, "error": "仅可导入到可管部门"}), 403
    # 格式：姓名,手机,邮箱,角色(可选)
    reader = csv.reader(io.StringIO(text.strip()))
    db = get_db()
    created = []
    for row in reader:
        if not row or not row[0].strip() or row[0].strip().startswith("#"):
            continue
        if row[0].strip() in ("姓名", "name", "display_name"):
            continue
        display_name = row[0].strip()
        phone = row[1].strip() if len(row) > 1 else ""
        email = row[2].strip() if len(row) > 2 else ""
        role = row[3].strip() if len(row) > 3 and row[3].strip() in ROLE_MENUS else "employee_a"
        username = alloc_username(db, display_name)
        cur = db.execute(
            """INSERT INTO users
            (username, password, display_name, role, dept_id, phone, email, itcode,
             password_expire, account_expire)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                username,
                "123456",
                display_name,
                role,
                dept_id,
                phone or None,
                email or None,
                username,
                "2026-12-31",
                default_account_expire(90),
            ),
        )
        created.append({"id": cur.lastrowid, "username": username, "display_name": display_name})
    db.commit()
    return jsonify({"ok": True, "created": created, "count": len(created)})


@app.post("/api/username/preview")
@login_required
def username_preview(user):
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    name = (data.get("display_name") or "").strip()
    db = get_db()
    return jsonify(
        {
            "ok": True,
            "pinyin_base": name_to_pinyin(name),
            "username": alloc_username(db, name),
        }
    )


@app.get("/api/hr/users")
@login_required
def hr_users(user):
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "仅人事专员可管理用户"}), 403
    db = get_db()
    q = (request.args.get("q") or "").strip()
    sql = """SELECT u.*, d.name AS dept_name FROM users u
        LEFT JOIN departments d ON d.id = u.dept_id
        WHERE u.username != ?"""
    params = [SYSTEM_ADMIN_USERNAME]
    if q:
        sql += " AND (u.display_name LIKE ? OR u.username LIKE ? OR u.phone LIKE ? OR u.email LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY u.id"
    rows = db.execute(sql, params).fetchall()
    return jsonify(
        {
            "ok": True,
            "users": [
                {
                    **row_user(r),
                    "dept_name": r["dept_name"],
                }
                for r in rows
                if not is_hidden_from_org(r)
            ],
        }
    )


@app.get("/api/hr/sync-roster")
@login_required
def hr_sync_roster(user):
    """部门架构同步花名册：待初始化用户。"""
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    status = request.args.get("status") or "pending"
    rows = db.execute(
        """SELECT r.*, d.name AS dept_name FROM hr_sync_roster r
        LEFT JOIN departments d ON d.id = r.dept_id
        WHERE r.status = ?
        ORDER BY r.id""",
        (status,),
    ).fetchall()
    previews = preview_unique_usernames(db, [r["display_name"] for r in rows])
    preview = []
    for r, pv in zip(rows, previews):
        preview.append({**dict(r), **pv})
    depts = all_departments(db)
    return jsonify(
        {
            "ok": True,
            "roster": preview,
            "tree": build_org_tree(depts),
            "rule": "用户名=姓名拼音全拼，全局唯一；冲突自动加 1、2…",
        }
    )


@app.post("/api/hr/sync-init")
@login_required
def hr_sync_init(user):
    """部门同步建人：可编辑确认用户名后直接创建（内部人员）。"""
    # AI-GEN-BEGIN
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    ids = data.get("roster_ids")  # None = 全部 pending
    # username_map: {"roster_id": "username"} 或 items: [{roster_id, username}]
    username_map = {str(k): v for k, v in (data.get("username_map") or {}).items()}
    for it in data.get("items") or []:
        if it.get("roster_id") is not None:
            username_map[str(it["roster_id"])] = it.get("username") or ""
    db = get_db()
    if ids:
        rows = db.execute(
            f"""SELECT * FROM hr_sync_roster
            WHERE status = 'pending' AND id IN ({','.join('?'*len(ids))})
            ORDER BY id""",
            ids,
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM hr_sync_roster WHERE status = 'pending' ORDER BY id"
        ).fetchall()
    if not rows:
        return jsonify({"ok": False, "error": "没有待同步人员"}), 400
    now = datetime.now().strftime("%Y-%m-%d")
    created = []
    linked = []
    for r in rows:
        dept = db.execute(
            "SELECT id FROM departments WHERE id = ?", (r["dept_id"],)
        ).fetchone()
        if not dept:
            continue
        # AI-GEN-BEGIN
        # 部门已落库后，优先挂回已有账号（拼音用户名 / 未绑 leorg），避免 gaojia1 重复人
        rkeys = r.keys()
        leorg_emp_id = r["leorg_emp_id"] if "leorg_emp_id" in rkeys else None
        beisen_user_id = None
        if "beisen_user_id" in rkeys and r["beisen_user_id"]:
            beisen_user_id = str(r["beisen_user_id"]).strip() or None
        emp_stub = {
            "id": leorg_emp_id,
            "name": r["display_name"],
            "emp_no": r["emp_no"] if "emp_no" in rkeys else None,
            "email": r["email"],
        }
        exist = _find_user_for_leorg_emp(
            db, emp_stub, beisen_user_id, (emp_stub.get("emp_no") or "") or "", r["email"]
        )
        if exist:
            uid = int(exist["id"])
            if leorg_emp_id is not None:
                db.execute(
                    """UPDATE users SET leorg_emp_id = NULL
                    WHERE leorg_emp_id = ? AND id != ?""",
                    (int(leorg_emp_id), uid),
                )
            db.execute(
                """UPDATE users SET display_name=?, dept_id=?,
                    phone=COALESCE(?, phone), email=COALESCE(?, email),
                    itcode=COALESCE(?, itcode),
                    leorg_emp_id=COALESCE(?, leorg_emp_id),
                    beisen_user_id=COALESCE(?, beisen_user_id)
                WHERE id=?""",
                (
                    r["display_name"],
                    r["dept_id"],
                    r["phone"],
                    r["email"],
                    (r["emp_no"] if "emp_no" in rkeys and r["emp_no"] else None),
                    int(leorg_emp_id) if leorg_emp_id is not None else None,
                    beisen_user_id,
                    uid,
                ),
            )
            db.execute(
                """UPDATE hr_sync_roster
                SET status = 'synced', created_user_id = ?, synced_at = ?, dept_id = ?
                WHERE id = ?""",
                (uid, now, r["dept_id"], r["id"]),
            )
            linked.append(
                {
                    "roster_id": r["id"],
                    "user_id": uid,
                    "display_name": r["display_name"],
                    "username": exist["username"],
                    "dept_id": r["dept_id"],
                    "linked": True,
                }
            )
            continue
        # AI-GEN-END
        pinyin_base = name_to_pinyin(r["display_name"])
        want = (username_map.get(str(r["id"])) or "").strip() or alloc_username(
            db, r["display_name"]
        )
        ok, uname_or_err = ensure_username_available(db, want)
        if not ok:
            return jsonify(
                {"ok": False, "error": f"{r['display_name']}：{uname_or_err}"}
            ), 400
        username = uname_or_err
        itcode = (r["emp_no"] if "emp_no" in rkeys and r["emp_no"] else None) or username
        acct_expire = default_account_expire(90)
        cur = db.execute(
            """INSERT INTO users
            (username, password, display_name, role, dept_id, phone, email, itcode,
             password_expire, account_expire, person_type, leorg_emp_id, beisen_user_id)
            VALUES (?,?,?,?,?,?,?,?,?,?, 'internal', ?, ?)""",
            (
                username,
                "123456",
                r["display_name"],
                "employee_a",
                r["dept_id"],
                r["phone"],
                r["email"],
                itcode,
                "2026-12-31",
                acct_expire,
                int(leorg_emp_id) if leorg_emp_id is not None else None,
                beisen_user_id,
            ),
        )
        uid = cur.lastrowid
        oa = db.execute("SELECT id, code FROM systems WHERE code = 'oa'").fetchone()
        if oa:
            db.execute(
                """INSERT INTO user_system_accounts
                (user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
                VALUES (?,?,?,?,1,1,0,?)""",
                (uid, oa["id"], f"{username}_oa", "部门同步初始化", "普通员工"),
            )
        db.execute(
            """UPDATE hr_sync_roster
            SET status = 'synced', created_user_id = ?, synced_at = ?
            WHERE id = ?""",
            (uid, now, r["id"]),
        )
        created.append(
            {
                "roster_id": r["id"],
                "user_id": uid,
                "display_name": r["display_name"],
                "username": username,
                "pinyin_base": pinyin_base,
                "dept_id": r["dept_id"],
                "unique_suffix": username != pinyin_base,
            }
        )
    # AI-GEN-BEGIN
    owner_stats = _resolve_dept_owners_from_leorg(db)
    # AI-GEN-END
    db.commit()
    return jsonify(
        {
            "ok": True,
            "count": len(created) + len(linked),
            "created": created,
            "linked": linked,
            "owners": owner_stats,
            "message": (
                f"已处理 {len(created) + len(linked)} 人"
                f"（新建 {len(created)} / 挂回已有账号 {len(linked)}，初始密码 123456）"
            ),
        }
    )
    # AI-GEN-END


@app.post("/api/hr/sync-pull")
@login_required
def hr_sync_pull(user):
    """从 LeOrg 拉部门+人员。

    preview=true（默认）：只对比变化，写入草稿供确认；
    preview=false：保持旧行为直接落库。
    """
    # AI-GEN-BEGIN
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    if data.get("people"):
        return _hr_sync_pull_mock(data["people"])
    if data.get("mock"):
        return _hr_sync_pull_mock(None)
    if LeorgClient is None or not leorg_load_config():
        return jsonify(
            {
                "ok": False,
                "error": "未配置 LeOrg，请复制 .env.example → .env 并填写 LEORG_*",
            }
        ), 400

    db = get_db()
    migrate_schema(db)
    mode = (data.get("mode") or "auto").strip().lower()
    days = int(data.get("days") or 7)
    preview = data.get("preview")
    if preview is None:
        preview = True
    preview = bool(preview)
    state = _leorg_sync_state(db)
    mapped_orgs = db.execute(
        "SELECT COUNT(*) AS c FROM departments WHERE leorg_id IS NOT NULL"
    ).fetchone()["c"]
    if mode == "auto":
        mode = (
            "full"
            if (not state or not state.get("last_full_at") or mapped_orgs == 0)
            else "incr"
        )
    if mode not in ("full", "incr"):
        return jsonify({"ok": False, "error": "mode 仅支持 auto/full/incr"}), 400

    try:
        client = LeorgClient()
        orgs = client.list_organizations(status=1)
        max_change_id = int(state.get("last_change_id") or 0) if state else 0
        change_rows = 0
        if mode == "full":
            emps = client.list_employees(emp_status=1)
            emps_prob = client.list_employees(emp_status=2)
            seen = {e.get("id") for e in emps}
            for e in emps_prob:
                if e.get("id") not in seen:
                    emps.append(e)
            try:
                max_change_id = max(
                    max_change_id, client.latest_change_id(days=max(days, 1))
                )
            except Exception:
                pass
        else:
            new_changes = client.list_employee_changes(days=days, after_id=max_change_id)
            change_rows = len(new_changes)
            emp_ids = sorted(
                {
                    int(c.get("entity_id") or c.get("emp_id") or c.get("employee_id"))
                    for c in new_changes
                    if (
                        c.get("entity_id") is not None
                        or c.get("emp_id") is not None
                        or c.get("employee_id") is not None
                    )
                }
            )
            emps = []
            for eid in emp_ids:
                try:
                    detail = client.get_employee(eid)
                except Exception:
                    detail = None
                if detail:
                    emps.append(detail)
            if new_changes:
                max_change_id = max(int(c.get("id") or 0) for c in new_changes)

        if preview:
            changes = _diff_leorg_organizations(db, orgs)
            changes.extend(_diff_leorg_employees(db, emps))
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 清理同用户旧草稿
            db.execute(
                "DELETE FROM leorg_sync_draft WHERE created_by = ?", (user["id"],)
            )
            cur = db.execute(
                """INSERT INTO leorg_sync_draft
                (created_by, mode, max_change_id, changes_json, created_at)
                VALUES (?,?,?,?,?)""",
                (
                    user["id"],
                    mode,
                    max_change_id,
                    json.dumps(changes, ensure_ascii=False),
                    now,
                ),
            )
            db.commit()
            draft_id = int(cur.lastrowid)
            summary = _sync_changes_summary(changes)
            msg = (
                f"【{'全量' if mode == 'full' else '增量'}预览】"
                f"共 {len(changes)} 条变更待确认"
                f"（部门 {summary['org']} / 人员 {summary['user']} / 花名册 {summary['roster']}）"
            )
            if mode == "incr":
                msg += f"；变更条数 {change_rows}"
            return jsonify(
                {
                    "ok": True,
                    "preview": True,
                    "draft_id": draft_id,
                    "mode": mode,
                    "message": msg,
                    "changes": [
                        {
                            "key": c["key"],
                            "kind": c["kind"],
                            "action": c["action"],
                            "title": c["title"],
                            "detail": c.get("detail") or "",
                            "fields": c.get("fields") or [],
                        }
                        for c in changes
                    ],
                    "summary": summary,
                    "fetched": {"orgs": len(orgs), "employees": len(emps)},
                    "sync_state": _leorg_sync_state(db),
                }
            )

        # 直接落库（兼容旧调用）
        org_stats = _sync_leorg_organizations(db, orgs)
        emp_stats = _sync_leorg_employees(db, emps)
        # AI-GEN-BEGIN
        realign = _realign_users_dept_from_leorg(db, emps)
        emp_stats["realign"] = realign
        # AI-GEN-END
        if mode == "incr":
            emp_stats["change_rows"] = change_rows
        owner_stats = _resolve_dept_owners_from_leorg(db)
        org_stats["owners"] = owner_stats
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_leorg_sync_state(
            db,
            mode=mode,
            last_change_id=max_change_id,
            org_mapped=org_stats.get("mapped") or 0,
            emp_touched=(emp_stats.get("roster_added") or 0)
            + (emp_stats.get("users_updated") or 0),
            now=now,
            is_full=(mode == "full"),
        )
        db.commit()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"LeOrg 拉取失败: {exc}"}), 502

    msg = (
        f"【{'全量' if mode == 'full' else '增量'}】"
        f"部门 +{org_stats['inserted']}/改{org_stats['updated']}；"
        f"负责人已关联 {owner_stats.get('resolved', 0)}"
        f"（待建人 {owner_stats.get('pending', 0)}）；"
        f"人员拉取 {len(emps)} 人"
        f"（待初始化 +{emp_stats['roster_added']}，更新用户 {emp_stats['users_updated']}，跳过 {emp_stats['skipped']}）"
    )
    if mode == "incr":
        msg += f"；变更条数 {emp_stats.get('change_rows', 0)}"
    return jsonify(
        {
            "ok": True,
            "preview": False,
            "message": msg,
            "mode": mode,
            "organizations": org_stats,
            "employees": emp_stats,
            "fetched": {"orgs": len(orgs), "employees": len(emps)},
            "sync_state": _leorg_sync_state(db),
        }
    )
    # AI-GEN-END


@app.post("/api/hr/sync-apply")
@login_required
def hr_sync_apply(user):
    """确认接受同步草稿中的变更。"""
    # AI-GEN-BEGIN
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    draft_id = data.get("draft_id")
    if not draft_id:
        return jsonify({"ok": False, "error": "缺少 draft_id"}), 400
    db = get_db()
    migrate_schema(db)
    draft = db.execute(
        "SELECT * FROM leorg_sync_draft WHERE id = ?", (int(draft_id),)
    ).fetchone()
    if not draft:
        return jsonify({"ok": False, "error": "草稿不存在或已应用"}), 404
    if int(draft["created_by"]) != int(user["id"]) and not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "无权应用他人草稿"}), 403
    changes = json.loads(draft["changes_json"] or "[]")
    keys = data.get("keys")
    apply_all = bool(data.get("all"))
    if apply_all or keys is None:
        selected = changes
    else:
        keyset = set(keys or [])
        selected = [c for c in changes if c.get("key") in keyset]
    if not selected:
        return jsonify({"ok": False, "error": "未选择任何变更"}), 400

    applied = _apply_leorg_sync_changes(db, selected)
    # AI-GEN-BEGIN
    # 部门先于人员已在 _apply 内保证；再用 payload 重挂部门（修复误挂根部门）
    stubs = []
    for c in selected:
        p = c.get("payload") or {}
        eid = p.get("leorg_emp_id")
        oid = p.get("org_leorg_id")
        if eid is None or oid in (None, ""):
            continue
        stubs.append({"id": int(eid), "org_id": int(oid), "emp_status": 1})
    realign = _realign_users_dept_from_leorg(db, stubs) if stubs else {}
    # AI-GEN-END
    owner_stats = _resolve_dept_owners_from_leorg(db)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    org_mapped = db.execute(
        "SELECT COUNT(*) AS c FROM departments WHERE leorg_id IS NOT NULL"
    ).fetchone()["c"]
    _save_leorg_sync_state(
        db,
        mode=draft["mode"],
        last_change_id=int(draft["max_change_id"] or 0),
        org_mapped=org_mapped,
        emp_touched=applied.get("user", 0) + applied.get("roster", 0),
        now=now,
        is_full=(draft["mode"] == "full"),
    )
    db.execute("DELETE FROM leorg_sync_draft WHERE id = ?", (int(draft_id),))
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": (
                f"已接受 {sum(applied.values())} 条变更"
                f"（部门 {applied.get('org', 0)} / 人员 {applied.get('user', 0)}"
                f" / 花名册 {applied.get('roster', 0)} / 关闭 {applied.get('close', 0)}）"
                + (
                    f"；已纠正部门 {realign.get('users_fixed', 0)} 人"
                    if realign
                    else ""
                )
            ),
            "applied": applied,
            "realign": realign,
            "owners": owner_stats,
            "sync_state": _leorg_sync_state(db),
        }
    )
    # AI-GEN-END


@app.post("/api/hr/org-clear")
@login_required
def hr_org_clear(user):
    """清空部门人员：部门树 + 普通员工；保留管理演示账号。"""
    # AI-GEN-BEGIN
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    if not data.get("confirm"):
        return jsonify({"ok": False, "error": "请传 confirm=true 确认清空"}), 400
    db = get_db()
    stats = _clear_my_organization(db)
    db.execute("DELETE FROM leorg_sync_state")
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": (
                f"已清空：部门 {stats['depts_deleted']}（含根部门，未重建），"
                f"员工 {stats['users_deleted']}（保留 {stats['users_kept']}），"
                f"待办 {stats.get('todos_deleted', 0)}，"
                f"申请 {stats.get('applications_deleted', 0)}"
            ),
            **stats,
        }
    )
    # AI-GEN-END


@app.get("/api/leorg/status")
def leorg_status():
    # AI-GEN-BEGIN
    out = leorg_status_dict()
    try:
        db = get_db()
        migrate_schema(db)
        out["sync_state"] = _leorg_sync_state(db)
        out["dept_count"] = db.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]
        out["user_count"] = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        out["leorg_dept_mapped"] = db.execute(
            "SELECT COUNT(*) AS c FROM departments WHERE leorg_id IS NOT NULL"
        ).fetchone()["c"]
    except Exception as exc:
        out["sync_state_error"] = str(exc)
    return jsonify(out)
    # AI-GEN-END


def _leorg_sync_state(db):
    # AI-GEN-BEGIN
    row = db.execute("SELECT * FROM leorg_sync_state WHERE id = 1").fetchone()
    # 无水位记录时返回空 dict，避免调用方 .get 空指针
    return dict(row) if row else {}
    # AI-GEN-END


def _save_leorg_sync_state(db, *, mode, last_change_id, org_mapped, emp_touched, now, is_full):
    # AI-GEN-BEGIN
    exists = db.execute("SELECT id FROM leorg_sync_state WHERE id = 1").fetchone()
    if exists:
        if is_full:
            db.execute(
                """UPDATE leorg_sync_state SET last_mode=?, last_full_at=?,
                   last_change_id=?, org_mapped=?, emp_touched=?, updated_at=? WHERE id=1""",
                (mode, now, last_change_id, org_mapped, emp_touched, now),
            )
        else:
            db.execute(
                """UPDATE leorg_sync_state SET last_mode=?, last_incr_at=?,
                   last_change_id=?, org_mapped=?, emp_touched=?, updated_at=? WHERE id=1""",
                (mode, now, last_change_id, org_mapped, emp_touched, now),
            )
    else:
        db.execute(
            """INSERT INTO leorg_sync_state
            (id, last_mode, last_full_at, last_incr_at, last_change_id, org_mapped, emp_touched, updated_at)
            VALUES (1,?,?,?,?,?,?,?)""",
            (
                mode,
                now if is_full else None,
                None if is_full else now,
                last_change_id,
                org_mapped,
                emp_touched,
                now,
            ),
        )
    # AI-GEN-END


def _clear_my_organization(db):
    """清空部门与员工，保留非 employee 管理账号；不重建根部门；清空全部审批数据。"""
    # AI-GEN-BEGIN
    keep_usernames = {SYSTEM_ADMIN_USERNAME}  # 系统超管始终保留且不挂组织
    keep_roles = {"super_admin", "hr_specialist", "finance", "system_owner"}
    keep_rows = db.execute(
        f"""SELECT id, username, role FROM users
        WHERE role IN ({",".join("?" * len(keep_roles))})""",
        tuple(keep_roles),
    ).fetchall()
    if keep_usernames:
        extra = db.execute(
            f"""SELECT id, username, role FROM users
            WHERE username IN ({",".join("?" * len(keep_usernames))})""",
            tuple(keep_usernames),
        ).fetchall()
        seen = {int(r["id"]) for r in keep_rows}
        for r in extra:
            if int(r["id"]) not in seen:
                keep_rows.append(r)
    keep_ids = {int(r["id"]) for r in keep_rows}
    if not keep_ids:
        # 兜底：至少留当前库里 id 最小的超管/人事
        row = db.execute(
            "SELECT id FROM users WHERE role IN ('super_admin','hr_specialist') ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            keep_ids.add(int(row["id"]))

    drop_ids = [
        int(r["id"])
        for r in db.execute("SELECT id FROM users").fetchall()
        if int(r["id"]) not in keep_ids
    ]

    # —— 全部审批/待办/申请（含保留账号相关）——
    todos_n = db.execute("SELECT COUNT(*) AS c FROM todos").fetchone()["c"]
    apps_n = db.execute("SELECT COUNT(*) AS c FROM applications").fetchone()["c"]
    grants_n = db.execute("SELECT COUNT(*) AS c FROM grant_applications").fetchone()["c"]
    db.execute("DELETE FROM application_steps")
    db.execute("DELETE FROM applications")
    db.execute("DELETE FROM todos")
    db.execute("DELETE FROM grant_applications")
    db.execute("DELETE FROM messages")
    try:
        db.execute("DELETE FROM leorg_sync_draft")
    except Exception:
        pass

    # 解除部门引用
    db.execute("UPDATE departments SET owner_user_id = NULL, parent_id = NULL")
    db.execute("DELETE FROM dept_extra_owners")
    db.execute("DELETE FROM approval_chain_dept_overrides")
    db.execute("DELETE FROM hr_sync_roster")
    db.execute("UPDATE users SET dept_id = NULL")

    depts_deleted = db.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]
    db.execute("DELETE FROM departments")

    # 删除普通员工及相关行
    for uid in drop_ids:
        db.execute("DELETE FROM user_system_accounts WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM user_fingerprints WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM oauth_codes WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (uid,))
        db.execute(
            "UPDATE system_accounts SET leuc_user_id = NULL, status = 'unbound' WHERE leuc_user_id = ?",
            (uid,),
        )
        db.execute(
            "UPDATE oa_forms SET leuc_user_id = NULL WHERE leuc_user_id = ?", (uid,)
        )
        db.execute("DELETE FROM system_owners WHERE user_id = ?", (uid,))
        try:
            db.execute("DELETE FROM user_roles WHERE user_id = ?", (uid,))
        except Exception:
            pass
        db.execute("DELETE FROM users WHERE id = ?", (uid,))

    # 不再重建「来酷科技」根部门；保留账号均不挂部门，后续同步或手动添加再建

    return {
        "depts_deleted": depts_deleted,
        "users_deleted": len(drop_ids),
        "users_kept": len(keep_ids),
        "root_id": None,
        "kept_usernames": [r["username"] for r in keep_rows],
        "todos_deleted": todos_n,
        "applications_deleted": apps_n,
        "grant_applications_deleted": grants_n,
    }
    # AI-GEN-END


def _hr_sync_pull_mock(samples):
    """旧演示：本地假数据写入花名册。"""
    # AI-GEN-BEGIN
    samples = samples or [
        {"display_name": "周新", "dept_id": 3, "phone": "13910000021", "email": "zhouxin@lecoo.com", "emp_no": "E2001"},
        {"display_name": "吴新", "dept_id": 4, "phone": "13910000022", "email": "wuxin@lecoo.com", "emp_no": "E2002"},
    ]
    db = get_db()
    added = []
    for p in samples:
        name = (p.get("display_name") or "").strip()
        dept_id = p.get("dept_id")
        if not name or not dept_id:
            continue
        exists = db.execute(
            """SELECT id FROM hr_sync_roster
            WHERE display_name = ? AND dept_id = ? AND status = 'pending'""",
            (name, dept_id),
        ).fetchone()
        if exists:
            continue
        cur = db.execute(
            """INSERT INTO hr_sync_roster
            (display_name, dept_id, phone, email, emp_no, source, status)
            VALUES (?,?,?,?,?, 'org_sync', 'pending')""",
            (
                name,
                int(dept_id),
                p.get("phone"),
                p.get("email"),
                p.get("emp_no"),
            ),
        )
        added.append({"id": cur.lastrowid, "display_name": name, "dept_id": dept_id})
    db.commit()
    return jsonify({"ok": True, "added": added, "message": f"已从部门架构拉取 {len(added)} 人待初始化"})
    # AI-GEN-END


# AI-GEN-BEGIN
def _sync_changes_summary(changes):
    out = {"org": 0, "user": 0, "roster": 0, "close": 0, "total": len(changes)}
    for c in changes or []:
        k = c.get("kind")
        if k == "org":
            out["org"] += 1
        elif k == "user":
            out["user"] += 1
        elif k == "roster":
            out["roster"] += 1
        elif k == "close":
            out["close"] += 1
    return out


def _leorg_dept_maps(db):
    leorg_to_local = {
        int(r["leorg_id"]): int(r["id"])
        for r in db.execute(
            "SELECT id, leorg_id FROM departments WHERE leorg_id IS NOT NULL"
        ).fetchall()
    }
    local_rows = {
        int(r["id"]): dict(r)
        for r in db.execute("SELECT * FROM departments").fetchall()
    }
    return leorg_to_local, local_rows


# AI-GEN-BEGIN
def _fallback_root_dept_id(db):
    row = db.execute(
        "SELECT id FROM departments WHERE parent_id IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def _resolve_emp_dept_id(leorg_to_local, org_leorg, fallback_dept_id):
    """LeOrg org_id → 本地 dept_id；未映射时才用 fallback（可为 None）。"""
    if org_leorg is None or org_leorg == "":
        return fallback_dept_id
    try:
        lid = int(org_leorg)
    except (TypeError, ValueError):
        return fallback_dept_id
    return leorg_to_local.get(lid) or fallback_dept_id


def _remap_payload_dept(payload, leorg_to_local, fallback_dept_id):
    """apply 时按最新部门映射重算 payload/fields 中的 dept_id。"""
    if not payload:
        return
    org_leorg = payload.get("org_leorg_id")
    if org_leorg is None or org_leorg == "":
        return
    new_dept = _resolve_emp_dept_id(leorg_to_local, org_leorg, fallback_dept_id)
    if new_dept is None:
        return
    payload["dept_id"] = int(new_dept)
    for f in payload.get("fields") or []:
        if f.get("field") == "dept_id":
            f["new"] = int(new_dept)
# AI-GEN-END


def _diff_leorg_organizations(db, orgs):
    """对比 LeOrg 部门与本地，返回待确认变更（不写库）。"""
    leorg_to_local, local_rows = _leorg_dept_maps(db)
    changes = []
    sorted_orgs = sorted(
        orgs,
        key=lambda o: (
            0 if o.get("parent_id") in (None, 0) else 1,
            int(o.get("org_level") or 99),
            int(o.get("id") or 0),
        ),
    )
    # 预计算远端 parent 映射（leorg_id → parent_leorg_id）
    remote_parent = {}
    for o in sorted_orgs:
        lid = o.get("id")
        if lid is None:
            continue
        remote_parent[int(lid)] = (
            int(o["parent_id"]) if o.get("parent_id") not in (None, 0, "") else None
        )

    for o in sorted_orgs:
        lid = o.get("id")
        if lid is None:
            continue
        lid = int(lid)
        name = (o.get("name") or "").strip() or f"org-{lid}"
        mgr = o.get("manager_emp_id")
        try:
            mgr = int(mgr) if mgr not in (None, "") else None
        except (TypeError, ValueError):
            mgr = None
        parent_leorg = remote_parent.get(lid)
        payload = {
            "leorg_id": lid,
            "name": name,
            "parent_leorg_id": parent_leorg,
            "manager_leorg_emp_id": mgr,
        }
        local_id = leorg_to_local.get(lid)
        if not local_id:
            # 名称兜底命中视为「绑定映射」更新
            hit = db.execute(
                "SELECT * FROM departments WHERE name = ? AND leorg_id IS NULL LIMIT 1",
                (name,),
            ).fetchone()
            if hit:
                fields = [{"field": "leorg_id", "old": None, "new": lid}]
                if mgr is not None:
                    fields.append(
                        {
                            "field": "manager_leorg_emp_id",
                            "old": hit["manager_leorg_emp_id"]
                            if "manager_leorg_emp_id" in hit.keys()
                            else None,
                            "new": mgr,
                        }
                    )
                changes.append(
                    {
                        "key": f"org:bind:{lid}",
                        "kind": "org",
                        "action": "bind",
                        "title": f"部门映射：{name}",
                        "detail": f"本地「{name}」绑定 LeOrg#{lid}",
                        "fields": fields,
                        "payload": {**payload, "local_id": int(hit["id"]), "mode": "bind"},
                    }
                )
            else:
                changes.append(
                    {
                        "key": f"org:new:{lid}",
                        "kind": "org",
                        "action": "insert",
                        "title": f"新增部门：{name}",
                        "detail": f"LeOrg#{lid}",
                        "fields": [
                            {"field": "name", "old": None, "new": name},
                            {"field": "parent_leorg_id", "old": None, "new": parent_leorg},
                        ],
                        "payload": {**payload, "mode": "insert"},
                    }
                )
            continue

        row = local_rows.get(local_id) or {}
        fields = []
        if (row.get("name") or "") != name:
            fields.append({"field": "name", "old": row.get("name"), "new": name})
        old_parent_leorg = None
        if row.get("parent_id"):
            for lg, loc in leorg_to_local.items():
                if loc == row["parent_id"]:
                    old_parent_leorg = lg
                    break
        if old_parent_leorg != parent_leorg:
            fields.append(
                {
                    "field": "parent_leorg_id",
                    "old": old_parent_leorg,
                    "new": parent_leorg,
                }
            )
        old_mgr = row.get("manager_leorg_emp_id")
        try:
            old_mgr = int(old_mgr) if old_mgr not in (None, "") else None
        except (TypeError, ValueError):
            old_mgr = None
        if old_mgr != mgr:
            fields.append(
                {"field": "manager_leorg_emp_id", "old": old_mgr, "new": mgr}
            )
        if fields:
            changes.append(
                {
                    "key": f"org:upd:{lid}",
                    "kind": "org",
                    "action": "update",
                    "title": f"更新部门：{row.get('name') or name}",
                    "detail": "；".join(
                        f"{f['field']}: {f['old']} → {f['new']}" for f in fields
                    ),
                    "fields": fields,
                    "payload": {**payload, "local_id": local_id, "mode": "update"},
                }
            )
    return changes


def _find_user_for_leorg_emp(db, e, beisen_user_id, emp_no, email):
    """匹配本地用户：优先已绑定 leorg；其次北森/工号/邮箱；再按拼音用户名挂回保留账号。"""
    # AI-GEN-BEGIN
    leorg_emp_id = e.get("id")
    name = (e.get("name") or "").strip()
    user = None

    def _prefer_bare(candidates):
        """同组时优先未绑 leorg 的保留账号（gaojia 优于 gaojia1）。"""
        rows = [dict(r) if not isinstance(r, dict) else r for r in candidates if r]
        if not rows:
            return None
        bare = [r for r in rows if r.get("leorg_emp_id") in (None, "")]
        pool = bare or rows
        # 用户名恰好等于姓名拼音的优先
        py = name_to_pinyin(name) if name else ""
        if py:
            exact = [r for r in pool if (r.get("username") or "") == py]
            if exact:
                return exact[0]
        # 管理角色优先于同步建出的 employee
        for role in (
            "super_admin",
            "system_owner",
            "hr_specialist",
            "finance",
            "dept_owner",
        ):
            hit = [r for r in pool if (r.get("role") or "") == role]
            if hit:
                return hit[0]
        return pool[0]

    if leorg_emp_id is not None:
        rows = db.execute(
            "SELECT * FROM users WHERE leorg_emp_id = ?", (int(leorg_emp_id),)
        ).fetchall()
        # 若已有 gaojia1 绑了 leorg，同时存在未绑的 gaojia，仍优先 gaojia
        if name:
            py = name_to_pinyin(name)
            if py:
                bare = db.execute(
                    """SELECT * FROM users
                    WHERE (username = ? OR itcode = ?) AND leorg_emp_id IS NULL""",
                    (py, py),
                ).fetchall()
                if bare:
                    user = _prefer_bare(list(bare) + list(rows))
        if not user and rows:
            user = rows[0]
    if not user and beisen_user_id:
        user = db.execute(
            "SELECT * FROM users WHERE beisen_user_id = ?", (beisen_user_id,)
        ).fetchone()
    if not user and emp_no:
        for c in dict.fromkeys(
            [
                emp_no,
                emp_no.lstrip("0") or emp_no,
                f"e{emp_no}",
                f"e{emp_no.lstrip('0') or emp_no}",
            ]
        ):
            user = db.execute(
                "SELECT * FROM users WHERE itcode = ? OR username = ?", (c, c)
            ).fetchone()
            if user:
                break
    if not user and email:
        user = db.execute(
            "SELECT * FROM users WHERE lower(email) = lower(?)", (email,)
        ).fetchone()
    # 拼音用户名：清空部门后保留的演示账号（gaojia / wuhongliang / …）
    if not user and name:
        py = name_to_pinyin(name)
        if py:
            rows = db.execute(
                "SELECT * FROM users WHERE username = ? OR itcode = ?",
                (py, py),
            ).fetchall()
            user = _prefer_bare(rows)
    # 唯一同名且未绑 leorg
    if not user and name:
        rows = db.execute(
            "SELECT * FROM users WHERE display_name = ? AND leorg_emp_id IS NULL",
            (name,),
        ).fetchall()
        if len(rows) == 1:
            user = rows[0]
    return user
    # AI-GEN-END


def _diff_leorg_employees(db, emps, leorg_to_local=None):
    """对比 LeOrg 人员与本地用户/花名册。

    leorg_to_local：可选投影映射（含即将新增的部门占位时仍可能不全）；
    真正可靠映射在 sync-apply 落库后按 org_leorg_id 重算。
    """
    if leorg_to_local is None:
        leorg_to_local, _ = _leorg_dept_maps(db)
    fallback_dept_id = _fallback_root_dept_id(db) or 1
    changes = []

    def _beisen_id_of(row):
        v = row.get("beisen_id")
        if v is None or v == "":
            v = row.get("beisenId") or row.get("beisen_user_id")
        if v is None or v == "":
            return None
        return str(v).strip()

    for e in emps:
        emp_status = e.get("emp_status")
        emp_no = (e.get("emp_no") or "").strip()
        name = (e.get("name") or "").strip()
        email = (e.get("email") or "").strip() or None
        phone = extract_leorg_phone(e)
        leorg_emp_id = e.get("id")
        beisen_user_id = _beisen_id_of(e)
        org_leorg = e.get("org_id")
        try:
            org_leorg_id = int(org_leorg) if org_leorg not in (None, "") else None
        except (TypeError, ValueError):
            org_leorg_id = None
        # AI-GEN-BEGIN
        dept_id = _resolve_emp_dept_id(leorg_to_local, org_leorg_id, fallback_dept_id)
        # AI-GEN-END
        if not name:
            continue

        user = _find_user_for_leorg_emp(db, e, beisen_user_id, emp_no, email)

        if emp_status == 0:
            if user and not user_is_closed(user):
                changes.append(
                    {
                        "key": f"close:{user['id']}",
                        "kind": "close",
                        "action": "close",
                        "title": f"关闭账号：{user['display_name']}",
                        "detail": f"LeOrg 已离职 · {user['username']}（将关闭本系统及全部绑定账号）",
                        "fields": [
                            {"field": "status", "old": "active", "new": "closed"}
                        ],
                        "payload": {
                            "user_id": int(user["id"]),
                            "mode": "close",
                            "leorg_emp_id": int(leorg_emp_id)
                            if leorg_emp_id is not None
                            else None,
                        },
                    }
                )
            continue

        # AI-GEN-BEGIN
        payload_base = {
            "leorg_emp_id": int(leorg_emp_id) if leorg_emp_id is not None else None,
            "org_leorg_id": org_leorg_id,
            "display_name": name,
            "dept_id": dept_id,
            "phone": phone,
            "email": email,
            "emp_no": emp_no or None,
            "beisen_user_id": beisen_user_id,
        }
        # AI-GEN-END

        if user:
            fields = []
            if (user["display_name"] or "") != name:
                fields.append(
                    {"field": "display_name", "old": user["display_name"], "new": name}
                )
            if int(user["dept_id"] or 0) != int(dept_id):
                fields.append(
                    {"field": "dept_id", "old": user["dept_id"], "new": dept_id}
                )
            if email and (user["email"] or "") != email:
                fields.append({"field": "email", "old": user["email"], "new": email})
            if phone and (user["phone"] or "") != phone:
                fields.append({"field": "phone", "old": user["phone"], "new": phone})
            if emp_no and (user["itcode"] or "") != emp_no:
                fields.append({"field": "itcode", "old": user["itcode"], "new": emp_no})
            if beisen_user_id and (user["beisen_user_id"] or "") != beisen_user_id:
                fields.append(
                    {
                        "field": "beisen_user_id",
                        "old": user["beisen_user_id"],
                        "new": beisen_user_id,
                    }
                )
            ukeys = user.keys()
            if leorg_emp_id is not None:
                old_le = user["leorg_emp_id"] if "leorg_emp_id" in ukeys else None
                if old_le != int(leorg_emp_id):
                    fields.append(
                        {
                            "field": "leorg_emp_id",
                            "old": old_le,
                            "new": int(leorg_emp_id),
                        }
                    )
            if fields:
                changes.append(
                    {
                        "key": f"user:{user['id']}",
                        "kind": "user",
                        "action": "update",
                        "title": f"更新人员：{user['display_name']}",
                        "detail": "；".join(
                            f"{f['field']}: {f['old']} → {f['new']}" for f in fields
                        ),
                        "fields": fields,
                        "payload": {
                            **payload_base,
                            "user_id": int(user["id"]),
                            "mode": "user_update",
                            "fields": fields,
                        },
                    }
                )
            continue

        # 花名册
        exists = None
        if leorg_emp_id is not None:
            exists = db.execute(
                """SELECT * FROM hr_sync_roster
                WHERE leorg_emp_id = ? AND status = 'pending'""",
                (int(leorg_emp_id),),
            ).fetchone()
        if not exists and emp_no:
            exists = db.execute(
                """SELECT * FROM hr_sync_roster
                WHERE emp_no = ? AND status = 'pending'""",
                (emp_no,),
            ).fetchone()
        if exists:
            fields = []
            for fld, newv in (
                ("display_name", name),
                ("dept_id", dept_id),
                ("phone", phone),
                ("email", email),
            ):
                oldv = exists[fld] if fld in exists.keys() else None
                if str(oldv or "") != str(newv or ""):
                    fields.append({"field": fld, "old": oldv, "new": newv})
            if fields:
                changes.append(
                    {
                        "key": f"roster:upd:{exists['id']}",
                        "kind": "roster",
                        "action": "update",
                        "title": f"更新待初始化：{name}",
                        "detail": "；".join(
                            f"{f['field']}: {f['old']} → {f['new']}" for f in fields
                        ),
                        "fields": fields,
                        "payload": {
                            **payload_base,
                            "roster_id": int(exists["id"]),
                            "mode": "roster_update",
                        },
                    }
                )
            continue

        if leorg_emp_id is not None:
            done = db.execute(
                """SELECT id FROM hr_sync_roster
                WHERE leorg_emp_id = ? AND status = 'synced'""",
                (int(leorg_emp_id),),
            ).fetchone()
            if done:
                continue

        changes.append(
            {
                "key": f"user:new:{leorg_emp_id or emp_no or name}",
                "kind": "user",
                "action": "insert",
                "title": f"新增人员：{name}",
                "detail": f"自动生成登录名 · 部门#{dept_id}",
                "fields": [
                    {"field": "display_name", "old": None, "new": name},
                    {"field": "dept_id", "old": None, "new": dept_id},
                    {"field": "phone", "old": None, "new": phone},
                ],
                "payload": {**payload_base, "mode": "user_create"},
            }
        )
    return changes


def _apply_leorg_sync_changes(db, selected):
    """按草稿条目应用变更。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    counts = {"org": 0, "user": 0, "roster": 0, "close": 0}
    # 部门：先 insert/bind，再 update（含 parent）
    org_items = [c for c in selected if c.get("kind") == "org"]
    other = [c for c in selected if c.get("kind") != "org"]

    # 第一遍：insert / bind
    for c in org_items:
        p = c.get("payload") or {}
        mode = p.get("mode")
        lid = int(p["leorg_id"])
        name = p.get("name") or f"org-{lid}"
        mgr = p.get("manager_leorg_emp_id")
        if mode == "insert":
            cur = db.execute(
                """INSERT INTO departments
                (name, parent_id, owner_user_id, leorg_id, manager_leorg_emp_id, sort_order)
                VALUES (?,?,NULL,?,?,?)""",
                (name, None, lid, mgr, lid),
            )
            counts["org"] += 1
        elif mode == "bind":
            db.execute(
                """UPDATE departments SET leorg_id = ?, manager_leorg_emp_id = ?, name = ?
                WHERE id = ?""",
                (lid, mgr, name, int(p["local_id"])),
            )
            counts["org"] += 1

    leorg_to_local, _ = _leorg_dept_maps(db)

    # 第二遍：update + 统一挂 parent
    for c in org_items:
        p = c.get("payload") or {}
        lid = int(p["leorg_id"])
        local_id = p.get("local_id") or leorg_to_local.get(lid)
        if not local_id:
            continue
        if p.get("mode") == "update":
            db.execute(
                """UPDATE departments SET name = ?, manager_leorg_emp_id = ?
                WHERE id = ?""",
                (p.get("name"), p.get("manager_leorg_emp_id"), int(local_id)),
            )
            counts["org"] += 1
        parent_leorg = p.get("parent_leorg_id")
        parent_local = (
            leorg_to_local.get(int(parent_leorg)) if parent_leorg else None
        )
        if parent_local == int(local_id):
            parent_local = None
        db.execute(
            "UPDATE departments SET parent_id = ? WHERE id = ?",
            (parent_local, int(local_id)),
        )

    # AI-GEN-BEGIN
    # 部门落库后再映射人员：预览时草稿里的 dept_id 常因空树落成根部门
    leorg_to_local, _ = _leorg_dept_maps(db)
    fallback_dept_id = _fallback_root_dept_id(db) or 1
    for c in other:
        _remap_payload_dept(c.get("payload") or {}, leorg_to_local, fallback_dept_id)
    # AI-GEN-END

    for c in other:
        p = c.get("payload") or {}
        mode = p.get("mode")
        if mode == "user_update":
            uid = int(p["user_id"])
            sets = []
            params = []
            for f in p.get("fields") or []:
                fld = f.get("field")
                if fld in (
                    "display_name",
                    "dept_id",
                    "phone",
                    "email",
                    "itcode",
                    "beisen_user_id",
                    "leorg_emp_id",
                ):
                    sets.append(f"{fld} = ?")
                    params.append(f.get("new"))
            if sets:
                params.append(uid)
                db.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
                )
                counts["user"] += 1
        elif mode == "user_create":
            # AI-GEN-BEGIN
            display_name = (p.get("display_name") or "").strip()
            if not display_name:
                continue
            username = alloc_username(db, display_name)
            password = gen_account_password()
            acct_expire = default_account_expire(90)
            cur = db.execute(
                """INSERT INTO users
                (username, password, display_name, role, dept_id, phone, email, itcode,
                 password_expire, account_expire, person_type, leorg_emp_id, beisen_user_id)
                VALUES (?,?,?,?,?,?,?,?,?,?, 'internal', ?, ?)""",
                (
                    username,
                    password,
                    display_name,
                    "employee_a",
                    p.get("dept_id"),
                    p.get("phone"),
                    p.get("email"),
                    p.get("emp_no") or username,
                    "2099-12-31",
                    acct_expire,
                    p.get("leorg_emp_id"),
                    p.get("beisen_user_id"),
                ),
            )
            uid = int(cur.lastrowid)
            try:
                ensure_user_roles_migrated(db)
                db.execute(
                    "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, ?)",
                    (uid, "employee_a"),
                )
            except Exception:
                pass
            record_credential_notify(
                db,
                user_id=uid,
                username=username,
                password=password,
                phone=p.get("phone"),
                email=p.get("email"),
                reason="leorg_sync_create",
            )
            counts["user"] = counts.get("user", 0) + 1
            # AI-GEN-END
        elif mode == "roster_insert":
            # 兼容旧草稿：仍写入花名册
            db.execute(
                """INSERT INTO hr_sync_roster
                (display_name, dept_id, phone, email, emp_no, leorg_emp_id, beisen_user_id, source, status, synced_at)
                VALUES (?,?,?,?,?,?,?, 'leorg', 'pending', ?)""",
                (
                    p.get("display_name"),
                    p.get("dept_id"),
                    p.get("phone"),
                    p.get("email"),
                    p.get("emp_no"),
                    p.get("leorg_emp_id"),
                    p.get("beisen_user_id"),
                    now,
                ),
            )
            counts["roster"] += 1
        elif mode == "roster_update":
            db.execute(
                """UPDATE hr_sync_roster
                SET display_name=?, dept_id=?, phone=?, email=?, emp_no=?,
                    leorg_emp_id=?, beisen_user_id=?, source='leorg', synced_at=?
                WHERE id=?""",
                (
                    p.get("display_name"),
                    p.get("dept_id"),
                    p.get("phone"),
                    p.get("email"),
                    p.get("emp_no"),
                    p.get("leorg_emp_id"),
                    p.get("beisen_user_id"),
                    now,
                    int(p["roster_id"]),
                ),
            )
            counts["roster"] += 1
        elif mode == "close":
            # AI-GEN-BEGIN
            r = close_user_for_leave(
                db,
                int(p["user_id"]),
                source="leorg_preview_apply",
                reason="LeOrg 同步确认：在职转离职",
                leorg_emp={"id": p.get("leorg_emp_id")} if p.get("leorg_emp_id") else None,
            )
            if r.get("ok"):
                counts["close"] += 1
            # AI-GEN-END
    return counts
# AI-GEN-END


def _sync_leorg_organizations(db, orgs, change_sink: list | None = None):
    """按 leorg_id upsert 部门，两遍设置 parent_id；写入 manager_leorg_emp_id。"""
    # AI-GEN-BEGIN
    inserted = 0
    updated = 0
    leorg_to_local: dict[int, int] = {}
    # 已有映射
    for row in db.execute(
        "SELECT id, leorg_id FROM departments WHERE leorg_id IS NOT NULL"
    ).fetchall():
        leorg_to_local[int(row["leorg_id"])] = int(row["id"])

    # 按层级粗排：无 parent 优先
    sorted_orgs = sorted(
        orgs,
        key=lambda o: (
            0 if o.get("parent_id") in (None, 0) else 1,
            int(o.get("org_level") or 99),
            int(o.get("id") or 0),
        ),
    )

    for o in sorted_orgs:
        lid = o.get("id")
        if lid is None:
            continue
        lid = int(lid)
        name = (o.get("name") or "").strip() or f"org-{lid}"
        mgr = o.get("manager_emp_id")
        try:
            mgr = int(mgr) if mgr not in (None, "") else None
        except (TypeError, ValueError):
            mgr = None
        local_id = leorg_to_local.get(lid)
        if local_id:
            before = db.execute(
                "SELECT name, manager_leorg_emp_id FROM departments WHERE id = ?",
                (local_id,),
            ).fetchone()
            db.execute(
                """UPDATE departments SET name = ?, leorg_id = ?,
                   manager_leorg_emp_id = ? WHERE id = ?""",
                (name, lid, mgr, local_id),
            )
            updated += 1
            # AI-GEN-BEGIN
            before_name = before["name"] if before else None
            before_mgr = before["manager_leorg_emp_id"] if before else None
            if change_sink is not None and (
                before_name != name or (before_mgr or None) != (mgr or None)
            ):
                change_sink.append(
                    {
                        "entity_type": "org",
                        "change_type": "update",
                        "entity_key": str(lid),
                        "entity_name": name,
                        "detail": {
                            "local_id": local_id,
                            "before": {
                                "name": before_name,
                                "manager_leorg_emp_id": before_mgr,
                            },
                            "after": {"name": name, "manager_leorg_emp_id": mgr},
                        },
                    }
                )
            # AI-GEN-END
        else:
            # 名称兜底匹配（种子通讯录已有同名部门时复用，避免重复树）
            hit = db.execute(
                "SELECT id FROM departments WHERE name = ? AND leorg_id IS NULL LIMIT 1",
                (name,),
            ).fetchone()
            if hit:
                local_id = int(hit["id"])
                db.execute(
                    """UPDATE departments SET leorg_id = ?, manager_leorg_emp_id = ?
                    WHERE id = ?""",
                    (lid, mgr, local_id),
                )
                updated += 1
                if change_sink is not None:
                    change_sink.append(
                        {
                            "entity_type": "org",
                            "change_type": "bind",
                            "entity_key": str(lid),
                            "entity_name": name,
                            "detail": {"local_id": local_id, "manager_leorg_emp_id": mgr},
                        }
                    )
            else:
                cur = db.execute(
                    """INSERT INTO departments
                    (name, parent_id, owner_user_id, leorg_id, manager_leorg_emp_id, sort_order)
                    VALUES (?,?,NULL,?,?,?)""",
                    (name, None, lid, mgr, lid),
                )
                local_id = int(cur.lastrowid)
                inserted += 1
                if change_sink is not None:
                    change_sink.append(
                        {
                            "entity_type": "org",
                            "change_type": "insert",
                            "entity_key": str(lid),
                            "entity_name": name,
                            "detail": {"local_id": local_id, "manager_leorg_emp_id": mgr},
                        }
                    )
            leorg_to_local[lid] = local_id

    # 第二遍：挂 parent
    for o in sorted_orgs:
        lid = o.get("id")
        if lid is None:
            continue
        lid = int(lid)
        local_id = leorg_to_local.get(lid)
        if not local_id:
            continue
        parent_leorg = o.get("parent_id")
        parent_local = leorg_to_local.get(int(parent_leorg)) if parent_leorg else None
        if parent_local == local_id:
            parent_local = None
        db.execute(
            "UPDATE departments SET parent_id = ? WHERE id = ?",
            (parent_local, local_id),
        )

    return {
        "inserted": inserted,
        "updated": updated,
        "mapped": len(leorg_to_local),
    }
    # AI-GEN-END


def _resolve_dept_owners_from_leorg(db):
    """按 departments.manager_leorg_emp_id → users.leorg_emp_id 回填主负责人。

    已建 LEUC 账号才写入 owner_user_id；不自动改用户角色（与角色多对多解耦）。
    LeOrg 未指定负责人时清空该部门主负责人（不影响额外负责人）。
    """
    # AI-GEN-BEGIN
    migrate_schema(db)
    resolved = 0
    pending = 0
    cleared = 0
    depts = db.execute(
        """SELECT id, manager_leorg_emp_id, owner_user_id FROM departments
        WHERE leorg_id IS NOT NULL"""
    ).fetchall()
    for d in depts:
        mid = d["manager_leorg_emp_id"]
        if mid in (None, ""):
            if d["owner_user_id"] is not None:
                db.execute(
                    "UPDATE departments SET owner_user_id = NULL WHERE id = ?",
                    (d["id"],),
                )
                cleared += 1
            continue
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        u = db.execute(
            "SELECT id, role FROM users WHERE leorg_emp_id = ?", (mid,)
        ).fetchone()
        if not u:
            pending += 1
            continue
        uid = int(u["id"])
        if d["owner_user_id"] != uid:
            db.execute(
                "UPDATE departments SET owner_user_id = ? WHERE id = ?",
                (uid, d["id"]),
            )
        resolved += 1
    return {"resolved": resolved, "pending": pending, "cleared": cleared}
    # AI-GEN-END


# AI-GEN-BEGIN
def _realign_users_dept_from_leorg(db, emps):
    """按 LeOrg org_id 重挂本地用户/花名册部门（修复预览空树误挂根部门）。"""
    leorg_to_local, _ = _leorg_dept_maps(db)
    fixed_users = 0
    fixed_roster = 0
    unmapped = 0
    for e in emps:
        if e.get("emp_status") == 0:
            continue
        leorg_emp_id = e.get("id")
        if leorg_emp_id is None:
            continue
        dept_id = _resolve_emp_dept_id(leorg_to_local, e.get("org_id"), None)
        if not dept_id:
            unmapped += 1
            continue
        dept_id = int(dept_id)
        eid = int(leorg_emp_id)
        u = db.execute(
            "SELECT id, dept_id FROM users WHERE leorg_emp_id = ?", (eid,)
        ).fetchone()
        if u and int(u["dept_id"] or 0) != dept_id:
            db.execute("UPDATE users SET dept_id = ? WHERE id = ?", (dept_id, u["id"]))
            fixed_users += 1
        r = db.execute(
            "SELECT id, dept_id FROM hr_sync_roster WHERE leorg_emp_id = ?", (eid,)
        ).fetchone()
        if r and int(r["dept_id"] or 0) != dept_id:
            db.execute(
                "UPDATE hr_sync_roster SET dept_id = ? WHERE id = ?", (dept_id, r["id"])
            )
            fixed_roster += 1
    return {
        "users_fixed": fixed_users,
        "roster_fixed": fixed_roster,
        "unmapped": unmapped,
    }
# AI-GEN-END


def _sync_leorg_employees(
    db, emps, change_sink: list | None = None, *, sync_run_id: int | None = None
):
    """幂等写入：已有用户按 leorg_emp_id/工号/邮箱/北森ID更新；否则 upsert 花名册。"""
    # AI-GEN-BEGIN
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    leorg_to_local = {
        int(r["leorg_id"]): int(r["id"])
        for r in db.execute(
            "SELECT id, leorg_id FROM departments WHERE leorg_id IS NOT NULL"
        ).fetchall()
    }
    fallback_dept = db.execute(
        "SELECT id FROM departments WHERE parent_id IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    fallback_dept_id = int(fallback_dept["id"]) if fallback_dept else 1

    roster_added = 0
    roster_updated = 0
    users_updated = 0
    skipped = 0
    beisen_filled = 0
    closed = 0

    def _beisen_id_of(row):
        v = row.get("beisen_id")
        if v is None or v == "":
            v = row.get("beisenId") or row.get("beisen_user_id")
        if v is None or v == "":
            return None
        return str(v).strip()

    for e in emps:
        emp_status = e.get("emp_status")
        emp_no = (e.get("emp_no") or "").strip()
        name = (e.get("name") or "").strip()
        email = (e.get("email") or "").strip() or None
        phone = extract_leorg_phone(e)
        leorg_emp_id = e.get("id")
        beisen_user_id = _beisen_id_of(e)
        org_leorg = e.get("org_id")
        # AI-GEN-BEGIN
        dept_id = _resolve_emp_dept_id(leorg_to_local, org_leorg, fallback_dept_id)
        # AI-GEN-END
        if not name:
            skipped += 1
            continue

        # 离职：删除 pending 花名册；本地未关则立即关闭本系统+全部绑定账号
        if emp_status == 0:
            if leorg_emp_id is not None:
                db.execute(
                    """DELETE FROM hr_sync_roster
                    WHERE leorg_emp_id = ? AND status = 'pending'""",
                    (int(leorg_emp_id),),
                )
            user = _find_user_for_leorg_emp(db, e, beisen_user_id, emp_no, email)
            if user and not user_is_closed(user):
                # AI-GEN-BEGIN
                r = close_user_for_leave(
                    db,
                    int(user["id"]),
                    source="leorg_incr",
                    reason="LeOrg emp_status=0 在职转离职",
                    sync_run_id=sync_run_id,
                    leorg_emp=e,
                )
                if r.get("ok") and not r.get("already_closed"):
                    closed += 1
                    if change_sink is not None:
                        change_sink.append(
                            {
                                "entity_type": "user",
                                "change_type": "leave_close",
                                "entity_key": str(user["id"]),
                                "entity_name": user["display_name"],
                                "detail": {
                                    "record_id": r.get("record_id"),
                                    "summary": r.get("summary"),
                                },
                            }
                        )
                # AI-GEN-END
            else:
                skipped += 1
            continue

        # AI-GEN-BEGIN
        user = _find_user_for_leorg_emp(db, e, beisen_user_id, emp_no, email)

        if user:
            uid = int(user["id"])
            if leorg_emp_id is not None:
                db.execute(
                    """UPDATE users SET leorg_emp_id = NULL
                    WHERE leorg_emp_id = ? AND id != ?""",
                    (int(leorg_emp_id), uid),
                )
            sets = ["dept_id = ?"]
            params: list = [dept_id]
            # 管理/演示账号保留本地显示名；普通员工跟 LeOrg
            role = (user["role"] if not isinstance(user, dict) else user.get("role")) or ""
            if role in (
                "employee",
                "employee_a",
                "employee_b",
            ) or not role:
                sets.insert(0, "display_name = ?")
                params.insert(0, name)
            if email:
                sets.append("email = ?")
                params.append(email)
            if phone:
                sets.append("phone = ?")
                params.append(phone)
            if emp_no:
                sets.append("itcode = ?")
                params.append(emp_no)
            if leorg_emp_id is not None:
                sets.append("leorg_emp_id = ?")
                params.append(int(leorg_emp_id))
            if beisen_user_id:
                sets.append("beisen_user_id = ?")
                params.append(beisen_user_id)
                beisen_filled += 1
            params.append(uid)
            before_snap = {
                "dept_id": user["dept_id"] if "dept_id" in user.keys() else None,
                "display_name": user["display_name"]
                if "display_name" in user.keys()
                else None,
                "email": user["email"] if "email" in user.keys() else None,
                "phone": user["phone"] if "phone" in user.keys() else None,
                "leorg_emp_id": user["leorg_emp_id"]
                if "leorg_emp_id" in user.keys()
                else None,
            }
            db.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            users_updated += 1
            # AI-GEN-BEGIN
            after_snap = {
                "dept_id": dept_id,
                "display_name": name
                if role in ("employee", "employee_a", "employee_b") or not role
                else before_snap.get("display_name"),
                "email": email or before_snap.get("email"),
                "phone": phone or before_snap.get("phone"),
                "leorg_emp_id": int(leorg_emp_id)
                if leorg_emp_id is not None
                else before_snap.get("leorg_emp_id"),
            }
            dirty = any(
                (before_snap.get(k) or None) != (after_snap.get(k) or None)
                for k in ("dept_id", "display_name", "email", "phone", "leorg_emp_id")
            )
            if change_sink is not None and dirty:
                change_sink.append(
                    {
                        "entity_type": "user",
                        "change_type": "update",
                        "entity_key": str(leorg_emp_id or uid),
                        "entity_name": name,
                        "detail": {
                            "user_id": uid,
                            "username": user["username"]
                            if "username" in user.keys()
                            else None,
                            "before": before_snap,
                            "after": {
                                **after_snap,
                                "beisen_user_id": beisen_user_id,
                            },
                        },
                    }
                )
            # AI-GEN-END
            if leorg_emp_id is not None:
                db.execute(
                    """UPDATE hr_sync_roster
                    SET status='synced', created_user_id=?, dept_id=?,
                        display_name=?, email=?, emp_no=?, beisen_user_id=?, synced_at=?
                    WHERE leorg_emp_id=?""",
                    (
                        uid,
                        dept_id,
                        name,
                        email,
                        emp_no or None,
                        beisen_user_id,
                        now,
                        int(leorg_emp_id),
                    ),
                )
            continue

        exists = None
        if leorg_emp_id is not None:
            exists = db.execute(
                """SELECT id FROM hr_sync_roster
                WHERE leorg_emp_id = ? AND status = 'pending'""",
                (int(leorg_emp_id),),
            ).fetchone()
        if not exists and emp_no:
            exists = db.execute(
                """SELECT id FROM hr_sync_roster
                WHERE emp_no = ? AND status = 'pending'""",
                (emp_no,),
            ).fetchone()
        if exists:
            db.execute(
                """UPDATE hr_sync_roster
                SET display_name=?, dept_id=?, phone=?, email=?, emp_no=?,
                    leorg_emp_id=?, beisen_user_id=?, source='leorg', synced_at=?
                WHERE id=?""",
                (
                    name,
                    dept_id,
                    phone,
                    email,
                    emp_no or None,
                    int(leorg_emp_id) if leorg_emp_id is not None else None,
                    beisen_user_id,
                    now,
                    int(exists["id"]),
                ),
            )
            roster_updated += 1
            if change_sink is not None:
                change_sink.append(
                    {
                        "entity_type": "roster",
                        "change_type": "update",
                        "entity_key": str(leorg_emp_id or exists["id"]),
                        "entity_name": name,
                        "detail": {"roster_id": int(exists["id"]), "dept_id": dept_id},
                    }
                )
            continue

        # 已确认创建过的花名册：纠正部门，不再重复插入
        if leorg_emp_id is not None:
            done = db.execute(
                """SELECT id FROM hr_sync_roster
                WHERE leorg_emp_id = ? AND status = 'synced'""",
                (int(leorg_emp_id),),
            ).fetchone()
            if done:
                db.execute(
                    "UPDATE hr_sync_roster SET dept_id = ? WHERE id = ?",
                    (dept_id, int(done["id"])),
                )
                skipped += 1
                continue

        # AI-GEN-BEGIN
        # 新用户：直接生成登录名并创建，写发送记录
        username = alloc_username(db, name)
        password = gen_account_password()
        acct_expire = default_account_expire(90)
        cur = db.execute(
            """INSERT INTO users
            (username, password, display_name, role, dept_id, phone, email, itcode,
             password_expire, account_expire, person_type, leorg_emp_id, beisen_user_id)
            VALUES (?,?,?,?,?,?,?,?,?,?, 'internal', ?, ?)""",
            (
                username,
                password,
                name,
                "employee_a",
                dept_id,
                phone,
                email,
                emp_no or username,
                "2099-12-31",
                acct_expire,
                int(leorg_emp_id) if leorg_emp_id is not None else None,
                beisen_user_id,
            ),
        )
        # AI-GEN-BEGIN
        if cur.lastrowid is None:
            raise RuntimeError(f"创建用户失败：未返回 id（{username}）")
        uid = int(cur.lastrowid)
        # 确保角色目录存在后再写关联（避免 PG 外键失败污染事务）
        ensure_roles_seeded(db)
        db.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, ?)",
            (uid, "employee_a"),
        )
        # AI-GEN-END
        record_credential_notify(
            db,
            user_id=uid,
            username=username,
            password=password,
            phone=phone,
            email=email,
            reason="leorg_sync_create",
        )
        roster_added += 1  # 复用计数表示新增人员
        if change_sink is not None:
            change_sink.append(
                {
                    "entity_type": "user",
                    "change_type": "insert",
                    "entity_key": str(leorg_emp_id or uid),
                    "entity_name": name,
                    "detail": {
                        "user_id": uid,
                        "username": username,
                        "dept_id": dept_id,
                        "phone": phone,
                        "email": email,
                        "leorg_emp_id": leorg_emp_id,
                        "beisen_user_id": beisen_user_id,
                        "account_expire": acct_expire,
                    },
                }
            )
        # AI-GEN-END

    return {
        "roster_added": roster_added,
        "users_created": roster_added,
        "roster_updated": roster_updated,
        "users_updated": users_updated,
        "beisen_filled": beisen_filled,
        "skipped": skipped,
        "closed": closed,
    }
    # AI-GEN-END


def require_sys_owner(user, system_id=None):
    """超管 / 系统管理员；指定 system_id 时校验是否为该系统管理员（可多人）。"""
    # AI-GEN-BEGIN
    if user_has_role(user, "super_admin"):
        return True
    if not user_has_role(user, "system_owner"):
        return False
    if system_id is None:
        return True
    db = get_db()
    row = db.execute(
        """SELECT 1 FROM system_owners WHERE system_id = ? AND user_id = ?
           UNION
           SELECT 1 FROM systems WHERE id = ? AND owner_user_id = ?""",
        (system_id, user["id"], system_id, user["id"]),
    ).fetchone()
    return bool(row)
    # AI-GEN-END


def list_system_owner_ids(db, system_id):
    ids = [
        r["user_id"]
        for r in db.execute(
            "SELECT user_id FROM system_owners WHERE system_id = ? ORDER BY user_id",
            (system_id,),
        ).fetchall()
    ]
    if not ids:
        row = db.execute(
            "SELECT owner_user_id FROM systems WHERE id = ?", (system_id,)
        ).fetchone()
        if row and row["owner_user_id"]:
            ids = [row["owner_user_id"]]
    return ids


def set_system_owners(db, system_id, owner_ids):
    """写入多名系统管理员；同步 systems.owner_user_id 为首位。"""
    # AI-GEN-BEGIN
    uniq = []
    for uid in owner_ids or []:
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        if uid and uid not in uniq:
            uniq.append(uid)
    db.execute("DELETE FROM system_owners WHERE system_id = ?", (system_id,))
    for uid in uniq:
        db.execute(
            "INSERT OR IGNORE INTO system_owners (system_id, user_id) VALUES (?,?)",
            (system_id, uid),
        )
    primary = uniq[0] if uniq else None
    db.execute(
        "UPDATE systems SET owner_user_id = ? WHERE id = ?", (primary, system_id)
    )
    return uniq
    # AI-GEN-END


def fetch_system_owners(db, system_id):
    ids = list_system_owner_ids(db, system_id)
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        f"""SELECT id, username, display_name, role FROM users WHERE id IN ({ph})
        ORDER BY id""",
        ids,
    ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def managed_system_ids(db, user):
    """当前用户可管理的系统 id 列表；超管返回 None 表示全部。"""
    if user_has_role(user, "super_admin"):
        return None
    if not user_has_role(user, "system_owner"):
        return []
    ids = {
        r["system_id"]
        for r in db.execute(
            "SELECT system_id FROM system_owners WHERE user_id = ?", (user["id"],)
        ).fetchall()
    }
    for r in db.execute(
        "SELECT id FROM systems WHERE owner_user_id = ?", (user["id"],)
    ).fetchall():
        ids.add(r["id"])
    return sorted(ids)



def match_system_account(db, leuc_user, system_id):
    """按手机/邮箱/itcode/用户名/姓名匹配子系统账号，返回候选列表。"""
    accounts = db.execute(
        """SELECT * FROM system_accounts
        WHERE system_id = ? AND (leuc_user_id IS NULL OR leuc_user_id = ?)
        ORDER BY id""",
        (system_id, leuc_user["id"]),
    ).fetchall()
    scored = []
    phone = (leuc_user["phone"] or "").strip()
    email = (leuc_user["email"] or "").strip().lower()
    itcode = ((leuc_user["itcode"] if "itcode" in leuc_user.keys() else None) or leuc_user["username"] or "").strip().lower()
    username = (leuc_user["username"] or "").strip().lower()
    name = (leuc_user["display_name"] or "").strip()
    for a in accounts:
        hits = []
        score = 0
        if phone and a["phone"] and a["phone"] == phone:
            hits.append("手机号"); score += 40
        if email and a["email"] and a["email"].lower() == email:
            hits.append("邮箱"); score += 35
        a_it = (a["itcode"] or "").strip().lower()
        if itcode and a_it and a_it == itcode:
            hits.append("itcode"); score += 30
        an = (a["account_name"] or "").lower()
        if username and (an == username or an.startswith(username + "_") or username in an):
            hits.append("用户名"); score += 20
        if name and a["display_name"] and a["display_name"] == name:
            hits.append("姓名"); score += 15
        if score > 0 or a["leuc_user_id"] == leuc_user["id"]:
            if a["leuc_user_id"] == leuc_user["id"]:
                hits.append("已绑定"); score += 100
            scored.append({"account": dict(a), "score": score, "hits": hits})
    scored.sort(key=lambda x: (-x["score"], x["account"]["id"]))
    return scored


def bind_leuc_to_system_account(db, leuc_user_id, account_id, can_login=True):
    """绑定 LEUC 用户与子系统账号，并写入/更新 user_system_accounts。"""
    acct = db.execute("SELECT * FROM system_accounts WHERE id = ?", (account_id,)).fetchone()
    if not acct:
        return None
    user = db.execute("SELECT * FROM users WHERE id = ?", (leuc_user_id,)).fetchone()
    if not user:
        return None
    db.execute(
        """UPDATE system_accounts SET leuc_user_id = ?, status = 'bound' WHERE id = ?""",
        (leuc_user_id, account_id),
    )
    exists = db.execute(
        """SELECT id FROM user_system_accounts
        WHERE user_id = ? AND system_id = ? AND account_name = ?""",
        (leuc_user_id, acct["system_id"], acct["account_name"]),
    ).fetchone()
    if exists:
        db.execute(
            "UPDATE user_system_accounts SET can_login = ? WHERE id = ?",
            (1 if can_login else 0, exists["id"]),
        )
        return exists["id"]
    cur = db.execute(
        """INSERT INTO user_system_accounts
        (user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
        VALUES (?,?,?,?,1,?,?,?)""",
        (
            leuc_user_id,
            acct["system_id"],
            acct["account_name"],
            acct["display_name"] or "子系统账号",
            1 if can_login else 0,
            0,
            "授权绑定",
        ),
    )
    return cur.lastrowid


@app.post("/api/hr/batch-grant")
@login_required
def hr_batch_grant(user):
    """兼容旧接口：转为账号绑定申请。"""
    return bind_apply(user)


@app.get("/api/bind/systems")
@app.get("/api/hr/grant-systems")
@login_required
def bind_systems(user):
    """可选系统列表（含禁外部、权限目录、是否有敏感）。"""
    # AI-GEN-BEGIN
    db = get_db()
    rows = db.execute(
        """SELECT id, code, name, access_mode, forbid_external, has_sensitive
        FROM systems WHERE status='enabled' ORDER BY CASE WHEN code = ? THEN 0 ELSE 1 END, id""",
        (LEUC_SYSTEM_CODE,),
    ).fetchall()
    perms_by_sys = {}
    for r in db.execute(
        """SELECT id, system_id, perm_code, perm_name, description, parent_id,
           is_sensitive, enabled
        FROM sensitive_perm_defs WHERE enabled=1 ORDER BY id"""
    ).fetchall():
        perms_by_sys.setdefault(r["system_id"], []).append(dict(r))
    systems_out = []
    for r in rows:
        perms = perms_by_sys.get(r["id"], [])
        sens_ids = [p["id"] for p in perms if p.get("is_sensitive")]
        has_sens = bool(r["has_sensitive"]) if "has_sensitive" in r.keys() else bool(sens_ids)
        systems_out.append(
            {
                **dict(r),
                "mode_label": "全员登录" if r["access_mode"] == "open" else "需账号绑定",
                "forbid_external": int(r["forbid_external"] or 0),
                "has_sensitive": int(has_sens),
                "has_sensitive_defs": bool(sens_ids),
                "default_perm_def_id": sens_ids[0] if sens_ids else None,
                "permissions": perms,
            }
        )
    return jsonify({"ok": True, "systems": systems_out})
    # AI-GEN-END


@app.get("/api/bind/accounts")
@login_required
def bind_accounts_list(user):
    """某系统的子系统账号池，供多选绑定。"""
    system_id = request.args.get("system_id")
    if not system_id:
        return jsonify({"ok": False, "error": "system_id 必填"}), 400
    db = get_db()
    rows = db.execute(
        """SELECT a.*, u.display_name AS leuc_name, u.username AS leuc_username
        FROM system_accounts a
        LEFT JOIN users u ON u.id = a.leuc_user_id
        WHERE a.system_id = ?
        ORDER BY a.id""",
        (int(system_id),),
    ).fetchall()
    return jsonify({"ok": True, "accounts": [dict(r) for r in rows]})


# AI-GEN-BEGIN
@app.get("/api/bind/user-bound-accounts")
@login_required
def bind_user_bound_accounts(user):
    """某人在业务系统中已绑定的账号（单选池）。"""
    uid = request.args.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "user_id 必填"}), 400
    uid = int(uid)
    target = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    if not can_apply_for_user(user, target):
        return jsonify({"ok": False, "error": "无权查看该人员已绑定账号"}), 403
    db = get_db()
    sid = request.args.get("system_id")
    sql = """SELECT a.id, a.account_name, a.account_label, a.system_id,
               a.can_login, a.has_sensitive, a.perm_summary,
               s.name AS system_name, s.code AS system_code
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ? AND s.status = 'enabled'"""
    params = [uid]
    if sid:
        sql += " AND a.system_id = ?"
        params.append(int(sid))
    sql += " ORDER BY s.id, a.is_default DESC, a.id"
    rows = db.execute(sql, params).fetchall()
    return jsonify({"ok": True, "accounts": [dict(r) for r in rows]})
# AI-GEN-END


@app.post("/api/bind/direct")
@login_required
def bind_direct(user):
    """超管 / 人事专员：直接绑定（一 LEUC 可绑多个系统账号）。"""
    if not (user_has_cap(user, "direct_bind") or user_has_role(user, "hr_specialist", "super_admin")):
        return jsonify({"ok": False, "error": "未开通直接绑定能力"}), 403
    data = request.get_json(force=True) or {}
    items = data.get("items") or []
    leuc_user_ids = list(data.get("leuc_user_ids") or [])
    if data.get("leuc_user_id"):
        leuc_user_ids = [data.get("leuc_user_id")]
    account_ids = data.get("account_ids") or []
    system_ids = data.get("system_ids") or []
    db = get_db()
    bound = []

    def _check_forbid(urow, system_id):
        sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone()
        ok, err = user_may_access_system(db, urow, sys_row)
        return ok, err

    # 明细行：每人×系统（允许同一系统重复）
    # AI-GEN-BEGIN
    if items:
        for it in items:
            uid = int(it.get("leuc_user_id") or 0)
            sid = int(it.get("system_id") or 0)
            if not uid or not sid:
                continue
            urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not urow:
                continue
            ok, err = _check_forbid(urow, sid)
            if not ok:
                return jsonify({"ok": False, "error": f"{urow['display_name']}：{err}"}), 400
            matches = match_system_account(db, urow, sid)
            if not matches:
                # 无匹配池账号时跳过该行
                continue
            aid = matches[0]["account"]["id"]
            acct = matches[0]["account"]
            bind_leuc_to_system_account(db, uid, int(aid))
            bound.append(f"{urow['display_name']}/{acct['account_name']}")
        seen_uid = set()
        for it in items:
            uid = int(it.get("leuc_user_id") or 0)
            if not uid or uid in seen_uid:
                continue
            seen_uid.add(uid)
            urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not urow:
                continue
            names = [b.split("/", 1)[1] for b in bound if b.startswith(urow["display_name"] + "/")]
            if names:
                push_system_message(
                    db, uid, "账号申请完成",
                    f"人事/超管已为您直接绑定：{', '.join(names)}",
                )
        db.commit()
        return jsonify({
            "ok": True, "bound": bound,
            "message": f"已按明细直接绑定 {len(bound)} 条",
        })
    # AI-GEN-END

    if not leuc_user_ids:
        return jsonify({"ok": False, "error": "请选择 LEUC 用户"}), 400
    if not account_ids and not system_ids:
        return jsonify({"ok": False, "error": "请勾选系统账号，或选择系统（按匹配直接绑定）"}), 400

    # 指定账号：要求仅 1 个 LEUC 用户
    if account_ids:
        if len(leuc_user_ids) != 1:
            return jsonify({"ok": False, "error": "勾选具体系统账号时请只选 1 个 LEUC 用户"}), 400
        uid = int(leuc_user_ids[0])
        urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not urow:
            return jsonify({"ok": False, "error": "用户不存在"}), 404
        for aid in account_ids:
            acct = db.execute("SELECT * FROM system_accounts WHERE id = ?", (aid,)).fetchone()
            if not acct:
                continue
            ok, err = _check_forbid(urow, acct["system_id"])
            if not ok:
                return jsonify({"ok": False, "error": err}), 400
            bind_leuc_to_system_account(db, uid, int(aid))
            bound.append(f"{urow['display_name']}/{acct['account_name']}")
        if bound:
            push_system_message(
                db, uid, "账号申请完成",
                f"人事/超管已为您直接绑定：{', '.join(a.split('/',1)[1] for a in bound)}",
            )
        db.commit()
        return jsonify({
            "ok": True, "bound": bound,
            "message": f"已为 {urow['display_name']} 直接绑定 {len(bound)} 个系统账号",
        })
    # 按系统自动匹配后直接绑定
    for uid in leuc_user_ids:
        urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not urow:
            continue
        names = []
        for sid in system_ids:
            ok, err = _check_forbid(urow, int(sid))
            if not ok:
                return jsonify({"ok": False, "error": f"{urow['display_name']}：{err}"}), 400
            matches = match_system_account(db, urow, sid)
            if not matches:
                continue
            aid = matches[0]["account"]["id"]
            acct = matches[0]["account"]
            bind_leuc_to_system_account(db, int(uid), int(aid))
            names.append(acct["account_name"])
            bound.append(f"{urow['display_name']}/{acct['account_name']}")
        if names:
            push_system_message(
                db, int(uid), "账号申请完成",
                f"人事/超管已为您直接绑定：{', '.join(names)}",
            )
    db.commit()
    return jsonify({
        "ok": True, "bound": bound,
        "message": f"已直接绑定 {len(bound)} 条（按匹配结果）",
    })


@app.post("/api/bind/apply")
@login_required
def bind_apply(user):
    """申请绑定：进入系统账号管理 + 待办。支持明细 items（可重复系统）或按系统自动匹配。"""
    data = request.get_json(force=True) or {}
    items = data.get("items") or []
    # 兼容旧字段 user_ids / system_ids
    account_ids = data.get("account_ids") or []
    system_ids = data.get("system_ids") or []
    leuc_user_ids = list(data.get("leuc_user_ids") or data.get("user_ids") or [])
    if data.get("leuc_user_id"):
        leuc_user_ids = [data.get("leuc_user_id")]
    if not leuc_user_ids and not items and user_has_role(user, "employee_a", "employee_b"):
        leuc_user_ids = [user["id"]]

    db = get_db()
    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END
    created = []

    def _create_grant(urow, sys_row, suggested, hints, uid, sid):
        owner_id = (sys_row["owner_user_id"] if sys_row else None) or user["id"]
        title = f"{urow['display_name']}（{urow['username']}）· 账号申请 {sys_row['name']}"
        if suggested:
            acct = db.execute(
                "SELECT account_name FROM system_accounts WHERE id = ?", (suggested,)
            ).fetchone()
            if acct:
                title = f"{title} / {acct['account_name']}"
        tcur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
            VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
            (
                owner_id,
                user["id"],
                title,
                "账号申请",
                now,
                json.dumps(
                    {"system_id": sid, "leuc_user_id": uid, "account_id": suggested},
                    ensure_ascii=False,
                ),
            ),
        )
        todo_id = tcur.lastrowid
        gcur = db.execute(
            """INSERT INTO grant_applications
            (requester_id, system_id, leuc_user_id, status, suggested_account_id,
             match_hints, created_at, todo_id)
            VALUES (?,?,?,?,?,?,?,?)""",
            (user["id"], sid, uid, "pending", suggested, hints, now, todo_id),
        )
        db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
            VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
            (
                owner_id,
                user["id"],
                f"账号申请 {sys_row['name']} · {urow['display_name']}",
                "账号申请",
                now,
                json.dumps({"grant_id": gcur.lastrowid}, ensure_ascii=False),
            ),
        )
        return {
            "grant_id": gcur.lastrowid,
            "user": urow["display_name"],
            "system": sys_row["name"],
            "suggested": suggested,
        }

    # AI-GEN-BEGIN
    if items:
        groups = group_bind_items_by_owner(db, items, list_system_owner_ids)
        for g in groups:
            uid = g["leuc_user_id"]
            urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not urow:
                continue
            if not can_apply_for_user(user, urow):
                return jsonify(
                    {"ok": False, "error": f"无权为 {urow['display_name']} 申请账号"}
                ), 403
            sys_rows = []
            with_sensitive = False
            for sid in g["system_ids"]:
                sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
                if not sys_row:
                    continue
                ok, err = user_may_access_system(db, urow, sys_row)
                if not ok:
                    return jsonify({"ok": False, "error": f"{urow['display_name']}：{err}"}), 400
                sys_rows.append(sys_row)
                if g["with_sensitive"] and int(sys_row["has_sensitive"] or 0):
                    with_sensitive = True
            if not sys_rows:
                continue
            if with_sensitive:
                steps = materialize_approval_chain(db, "sensitive", uid)
                flow_code = "account_apply_sensitive"
            else:
                direct = find_approver(db, uid)
                if not direct or int(direct) == int(uid):
                    return jsonify(
                        {"ok": False, "error": f"{urow['display_name']}：未找到直属审批人"}
                    ), 400
                steps = [("direct_leader", "直属领导", direct)]
                flow_code = "account_apply"
            for sys_row in sys_rows:
                steps = append_system_owner_step(db, int(sys_row["id"]), steps)
            steps, cc_list = prepare_flow_steps(db, steps, uid)
            if not steps:
                return jsonify({"ok": False, "error": "审批链为空"}), 400
            names = "、".join(s["name"] for s in sys_rows)
            title = (
                f"{urow['display_name']} · 账号申请 · {names}"
                + (" · 含敏感" if with_sensitive else "")
            )
            init_title = f"账号申请 · {names}（审批中）"
            primary_sid = int(sys_rows[0]["id"])
            # AI-GEN-BEGIN
            # 落库全部明细行（不去重），供审批详情按行展示
            line_items = []
            for it in g.get("items") or []:
                sid = int(it.get("system_id") or 0)
                sy = next((s for s in sys_rows if int(s["id"]) == sid), None)
                aid = it.get("account_id")
                acct_name = (it.get("account_name") or "").strip()
                if not acct_name and aid:
                    ar = db.execute(
                        "SELECT account_name FROM user_system_accounts WHERE id = ?",
                        (int(aid),),
                    ).fetchone()
                    if not ar:
                        ar = db.execute(
                            "SELECT account_name FROM system_accounts WHERE id = ?",
                            (int(aid),),
                        ).fetchone()
                    if ar:
                        acct_name = ar["account_name"]
                perm_ids = it.get("perm_ids") or []
                perm_names = list(it.get("perm_names") or [])
                if perm_ids and not perm_names:
                    for pid in perm_ids:
                        pr = db.execute(
                            "SELECT perm_name FROM sensitive_perm_defs WHERE id = ?",
                            (int(pid),),
                        ).fetchone()
                        if pr:
                            perm_names.append(pr["perm_name"])
                line_items.append(
                    {
                        "leuc_user_id": uid,
                        "display_name": urow["display_name"],
                        "username": urow["username"],
                        "system_id": sid,
                        "system_name": sy["name"] if sy else it.get("system_name"),
                        "account_id": aid,
                        "account_name": acct_name,
                        "create_new": bool(it.get("create_new")),
                        "with_sensitive": bool(it.get("with_sensitive")),
                        "perm_ids": [int(x) for x in perm_ids if x is not None],
                        "perm_names": perm_names,
                    }
                )
            # AI-GEN-END
            app_id, first_todo, first_assignee, preview = start_multi_step_apply(
                db,
                flow_code=flow_code,
                todo_type="账号申请",
                title=title,
                init_title=init_title,
                subject_id=uid,
                initiator_id=user["id"],
                system_id=primary_sid,
                steps=steps,
                meta_extra={
                    "system_id": primary_sid,
                    "system_ids": [int(s["id"]) for s in sys_rows],
                    "leuc_user_id": uid,
                    "with_sensitive": with_sensitive,
                    "create_new": True,
                    "items": line_items,
                },
                cc_list=cc_list,
            )
            au = db.execute(
                "SELECT display_name FROM users WHERE id = ?", (first_assignee,)
            ).fetchone()
            created.append(
                {
                    "application_id": app_id,
                    "todo_id": first_todo,
                    "user": urow["display_name"],
                    "system": names,
                    "systems": [s["name"] for s in sys_rows],
                    "with_sensitive": with_sensitive,
                    "chain": preview,
                    "approver": au["display_name"] if au else first_assignee,
                }
            )
        db.commit()
        return jsonify(
            {
                "ok": True,
                "count": len(created),
                "items": created,
                "message": (
                    f"已提交 {len(created)} 条账号申请；"
                    + (
                        f"等待 {created[0]['approver']} 审批；链："
                        + " → ".join(s["label"] for s in created[0].get("chain") or [])
                        if created
                        else ""
                    )
                ),
            }
        )
    # AI-GEN-END

    if not leuc_user_ids:
        return jsonify({"ok": False, "error": "请选择 LEUC 用户"}), 400

    if account_ids:
        if len(leuc_user_ids) != 1:
            return jsonify({"ok": False, "error": "指定系统账号绑定时请只选 1 个 LEUC 用户"}), 400
        uid = int(leuc_user_ids[0])
        urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not urow:
            return jsonify({"ok": False, "error": "用户不存在"}), 404
        if not can_apply_for_user(user, urow):
            return jsonify({"ok": False, "error": "无权为该用户申请绑定"}), 403
        for aid in account_ids:
            acct = db.execute("SELECT * FROM system_accounts WHERE id = ?", (aid,)).fetchone()
            if not acct:
                continue
            sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (acct["system_id"],)).fetchone()
            ok, err = user_may_access_system(db, urow, sys_row)
            if not ok:
                return jsonify({"ok": False, "error": err}), 400
            owner_id = (sys_row["owner_user_id"] if sys_row else None) or user["id"]
            title = f"{urow['display_name']}（{urow['username']}）· 账号申请 {sys_row['name']} / {acct['account_name']}"
            tcur = db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
                VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
                (
                    owner_id,
                    user["id"],
                    title,
                    "账号申请",
                    now,
                    json.dumps(
                        {"system_id": acct["system_id"], "leuc_user_id": uid, "account_id": aid},
                        ensure_ascii=False,
                    ),
                ),
            )
            todo_id = tcur.lastrowid
            gcur = db.execute(
                """INSERT INTO grant_applications
                (requester_id, system_id, leuc_user_id, status, suggested_account_id,
                 match_hints, created_at, todo_id)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    user["id"],
                    acct["system_id"],
                    uid,
                    "pending",
                    aid,
                    json.dumps([{"account_id": aid, "score": 100, "hits": ["指定账号"]}], ensure_ascii=False),
                    now,
                    todo_id,
                ),
            )
            db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
                VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
                (
                    owner_id,
                    user["id"],
                    f"账号申请 {sys_row['name']} · {acct['account_name']}",
                    "账号申请",
                    now,
                    json.dumps({"grant_id": gcur.lastrowid}, ensure_ascii=False),
                ),
            )
            created.append({"grant_id": gcur.lastrowid, "account": acct["account_name"]})
        db.commit()
        return jsonify(
            {
                "ok": True,
                "count": len(created),
                "items": created,
                "message": f"已提交 {len(created)} 条账号申请（待系统负责人确认）",
            }
        )

    if not system_ids:
        return jsonify({"ok": False, "error": "请选择系统或系统账号"}), 400
    for sid in system_ids:
        sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
        if not sys_row:
            continue
        owner_id = sys_row["owner_user_id"] or user["id"]
        for uid in leuc_user_ids:
            urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not urow:
                continue
            if not can_apply_for_user(user, urow):
                continue
            ok, err = user_may_access_system(db, urow, sys_row)
            if not ok:
                return jsonify({"ok": False, "error": f"{urow['display_name']}：{err}"}), 400
            matches = match_system_account(db, urow, sid)
            suggested = matches[0]["account"]["id"] if matches else None
            hints = json.dumps(
                [
                    {"account_id": m["account"]["id"], "score": m["score"], "hits": m["hits"]}
                    for m in matches[:5]
                ],
                ensure_ascii=False,
            )
            title = f"{urow['display_name']}（{urow['username']}）· 账号申请 {sys_row['name']}"
            tcur = db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
                VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
                (
                    owner_id,
                    user["id"],
                    title,
                    "账号申请",
                    now,
                    json.dumps({"system_id": sid, "leuc_user_id": uid}, ensure_ascii=False),
                ),
            )
            todo_id = tcur.lastrowid
            gcur = db.execute(
                """INSERT INTO grant_applications
                (requester_id, system_id, leuc_user_id, status, suggested_account_id,
                 match_hints, created_at, todo_id)
                VALUES (?,?,?,?,?,?,?,?)""",
                (user["id"], sid, uid, "pending", suggested, hints, now, todo_id),
            )
            db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
                VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
                (
                    owner_id,
                    user["id"],
                    f"账号申请 {sys_row['name']} · {urow['display_name']}",
                    "账号申请",
                    now,
                    json.dumps({"grant_id": gcur.lastrowid}, ensure_ascii=False),
                ),
            )
            created.append(
                {
                    "grant_id": gcur.lastrowid,
                    "user": urow["display_name"],
                    "system": sys_row["name"],
                    "suggested": suggested,
                }
            )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "count": len(created),
            "items": created,
            "message": f"已提交 {len(created)} 条账号申请至系统账号管理与负责人待办",
        }
    )


@app.get("/api/sys-accounts/overview")
@login_required
def sys_accounts_overview(user):
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    system_id = request.args.get("system_id")
    manage_ids = managed_system_ids(db, user)
    migrate_schema(db)
    # AI-GEN-BEGIN
    if manage_ids is None:
        systems = db.execute(
            "SELECT id, code, name, sso_login_field FROM systems ORDER BY id"
        ).fetchall()
    else:
        if not manage_ids:
            return jsonify({"ok": True, "systems": [], "accounts": [], "grants": []})
        ph0 = ",".join("?" * len(manage_ids))
        systems = db.execute(
            f"""SELECT id, code, name, sso_login_field FROM systems
            WHERE id IN ({ph0}) ORDER BY id""",
            manage_ids,
        ).fetchall()
    # AI-GEN-END
    sys_ids = [s["id"] for s in systems]
    if not sys_ids:
        return jsonify({"ok": True, "systems": [], "accounts": [], "grants": []})
    if system_id:
        sid = int(system_id)
        if sid not in sys_ids:
            return jsonify({"ok": False, "error": "非负责的系统"}), 403
        filter_ids = [sid]
    else:
        filter_ids = sys_ids
    ph = ",".join("?" * len(filter_ids))
    accounts = db.execute(
        f"""SELECT a.*, s.name AS system_name, u.display_name AS leuc_name, u.username AS leuc_username
        FROM system_accounts a
        JOIN systems s ON s.id = a.system_id
        LEFT JOIN users u ON u.id = a.leuc_user_id
        WHERE a.system_id IN ({ph})
        ORDER BY a.system_id, a.id""",
        filter_ids,
    ).fetchall()
    grants = db.execute(
        f"""SELECT g.*, s.name AS system_name,
            u.display_name AS leuc_name, u.username AS leuc_username,
            u.phone AS leuc_phone, u.email AS leuc_email, u.itcode AS leuc_itcode,
            sa.account_name AS suggested_name
        FROM grant_applications g
        JOIN systems s ON s.id = g.system_id
        JOIN users u ON u.id = g.leuc_user_id
        LEFT JOIN system_accounts sa ON sa.id = g.suggested_account_id
        WHERE g.system_id IN ({ph}) AND g.status = 'pending'
        ORDER BY g.id DESC""",
        filter_ids,
    ).fetchall()
    grant_out = []
    for g in grants:
        urow = db.execute("SELECT * FROM users WHERE id = ?", (g["leuc_user_id"],)).fetchone()
        matches = match_system_account(db, urow, g["system_id"])
        item = dict(g)
        item["matches"] = [
            {
                "account_id": m["account"]["id"],
                "account_uid": m["account"].get("account_uid"),
                "account_name": m["account"]["account_name"],
                "display_name": m["account"]["display_name"],
                "phone": m["account"]["phone"],
                "email": m["account"]["email"],
                "itcode": m["account"]["itcode"],
                "score": m["score"],
                "hits": m["hits"],
                "status": m["account"]["status"],
            }
            for m in matches[:8]
        ]
        grant_out.append(item)
    # AI-GEN-BEGIN
    systems_out = [enrich_system_sso_fields(dict(s)) for s in systems]
    # AI-GEN-END
    return jsonify(
        {
            "ok": True,
            "systems": systems_out,
            "accounts": [dict(a) for a in accounts],
            "grants": grant_out,
            "match_fields": ["手机号", "邮箱", "itcode", "用户名", "姓名"],
        }
    )


@app.get("/api/sys-accounts/catalog")
@login_required
def sys_accounts_catalog(user):
    """系统账号总览：全量绑定情况；可筛无归属账号。"""
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    unbound_only = str(request.args.get("unbound_only") or "").lower() in ("1", "true", "yes")
    system_id = request.args.get("system_id") or ""
    q = (request.args.get("q") or "").strip().lower()

    manage_ids = managed_system_ids(db, user)
    migrate_schema(db)
    # AI-GEN-BEGIN
    if manage_ids is None:
        systems = db.execute(
            "SELECT id, code, name, sso_login_field FROM systems ORDER BY id"
        ).fetchall()
    else:
        if not manage_ids:
            return jsonify(
                {
                    "ok": True,
                    "systems": [],
                    "accounts": [],
                    "stats": {"total": 0, "bound": 0, "unbound": 0},
                    "by_system": [],
                }
            )
        ph0 = ",".join("?" * len(manage_ids))
        systems = db.execute(
            f"""SELECT id, code, name, sso_login_field FROM systems
            WHERE id IN ({ph0}) ORDER BY id""",
            manage_ids,
        ).fetchall()
    # AI-GEN-END
    sys_ids = [s["id"] for s in systems]
    if not sys_ids:
        return jsonify(
            {
                "ok": True,
                "systems": [],
                "accounts": [],
                "stats": {"total": 0, "bound": 0, "unbound": 0},
                "by_system": [],
            }
        )

    filter_ids = sys_ids
    if system_id:
        sid = int(system_id)
        if sid not in sys_ids:
            return jsonify({"ok": False, "error": "非负责的系统"}), 403
        filter_ids = [sid]

    ph = ",".join("?" * len(filter_ids))
    rows = db.execute(
        f"""SELECT a.*, s.name AS system_name, s.code AS system_code,
            s.sso_login_field AS sso_login_field,
            u.display_name AS leuc_name, u.username AS leuc_username, u.id AS leuc_id
        FROM system_accounts a
        JOIN systems s ON s.id = a.system_id
        LEFT JOIN users u ON u.id = a.leuc_user_id
        WHERE a.system_id IN ({ph})
        ORDER BY s.id, a.id""",
        filter_ids,
    ).fetchall()

    accounts = []
    for r in rows:
        item = dict(r)
        bound = bool(item.get("leuc_user_id"))
        item["bound"] = bound
        item["bind_label"] = "已绑定" if bound else "无归属"
        if unbound_only and bound:
            continue
        if q:
            blob = " ".join(
                str(x or "")
                for x in (
                    item.get("system_name"),
                    item.get("account_uid"),
                    item.get("account_name"),
                    item.get("display_name"),
                    item.get("phone"),
                    item.get("email"),
                    item.get("itcode"),
                    item.get("leuc_name"),
                    item.get("leuc_username"),
                )
            ).lower()
            if q not in blob:
                continue
        accounts.append(item)

    # AI-GEN-BEGIN
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size") or 50)
    except ValueError:
        page_size = 50
    page_size = min(100, max(10, page_size))
    filtered_total = len(accounts)
    total_pages = max(1, (filtered_total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    page_accounts = accounts[start : start + page_size]
    # AI-GEN-END

    # 统计基于负责范围内全部账号（不受 unbound/q 过滤影响总数口径：按 filter_ids 全量）
    all_rows = [dict(r) for r in rows]
    bound_n = sum(1 for a in all_rows if a.get("leuc_user_id"))
    unbound_n = len(all_rows) - bound_n
    by_system = []
    systems_out = []
    for s in systems:
        sd = enrich_system_sso_fields(dict(s))
        systems_out.append(sd)
        if s["id"] not in filter_ids:
            continue
        subset = [a for a in all_rows if a["system_id"] == s["id"]]
        b = sum(1 for a in subset if a.get("leuc_user_id"))
        by_system.append(
            {
                "id": s["id"],
                "code": s["code"],
                "name": s["name"],
                "sso_login_field": sd.get("sso_login_field"),
                "sso_login_field_label": sd.get("sso_login_field_label"),
                "total": len(subset),
                "bound": b,
                "unbound": len(subset) - b,
            }
        )

    return jsonify(
        {
            "ok": True,
            "systems": systems_out,
            "accounts": page_accounts,
            "stats": {
                "total": len(all_rows),
                "bound": bound_n,
                "unbound": unbound_n,
                "filtered": filtered_total,
            },
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": filtered_total,
                "total_pages": total_pages,
            },
            "by_system": by_system,
            "filters": {
                "unbound_only": unbound_only,
                "system_id": system_id or None,
                "q": q or None,
            },
        }
    )


@app.post("/api/sys-accounts/confirm-bind")
@login_required
def sys_accounts_confirm_bind(user):
    """确认匹配绑定，或指定账号绑定；无账号时可新建并绑定。"""
    data = request.get_json(force=True) or {}
    grant_id = data.get("grant_id")
    account_id = data.get("account_id")
    create_new = data.get("create_new")
    db = get_db()
    grant = db.execute("SELECT * FROM grant_applications WHERE id = ?", (grant_id,)).fetchone()
    if not grant or grant["status"] != "pending":
        return jsonify({"ok": False, "error": "授权申请不存在或已处理"}), 404
    if not require_sys_owner(user, grant["system_id"]):
        return jsonify({"ok": False, "error": "无权限"}), 403
    now = datetime.now().strftime("%Y-%m-%d")
    urow = db.execute("SELECT * FROM users WHERE id = ?", (grant["leuc_user_id"],)).fetchone()
    if create_new:
        # AI-GEN-BEGIN
        migrate_schema(db)
        acct_name = (data.get("account_name") or "").strip()
        acct_uid = (data.get("account_uid") or "").strip()
        if not acct_uid or not acct_name:
            return jsonify(
                {
                    "ok": False,
                    "error": "新建绑定须填写唯一标识（SSO登录字段）与账号名",
                    "need_account_uid": True,
                }
            ), 400
        exists = db.execute(
            """SELECT id FROM system_accounts
            WHERE system_id = ? AND account_uid = ? LIMIT 1""",
            (grant["system_id"], acct_uid),
        ).fetchone()
        if exists:
            account_id = exists["id"]
            db.execute(
                """UPDATE system_accounts
                SET account_name=?, display_name=?, phone=?, email=?, itcode=?,
                    status='bound', leuc_user_id=?, source='manual'
                WHERE id=?""",
                (
                    acct_name,
                    urow["display_name"],
                    urow["phone"],
                    urow["email"],
                    urow["itcode"] if "itcode" in urow.keys() else urow["username"],
                    urow["id"],
                    account_id,
                ),
            )
        else:
            cur = db.execute(
                """INSERT INTO system_accounts
                (system_id, account_uid, account_name, display_name, phone, email, itcode,
                 status, leuc_user_id, source, created_at)
                VALUES (?,?,?,?,?,?,?, 'bound', ?, 'manual', ?)""",
                (
                    grant["system_id"],
                    acct_uid,
                    acct_name,
                    urow["display_name"],
                    urow["phone"],
                    urow["email"],
                    urow["itcode"] if "itcode" in urow.keys() else urow["username"],
                    urow["id"],
                    now,
                ),
            )
            account_id = cur.lastrowid
        # AI-GEN-END
    if not account_id:
        account_id = grant["suggested_account_id"]
    if not account_id:
        return jsonify({"ok": False, "error": "请选择账号或新建绑定"}), 400
    bind_leuc_to_system_account(db, grant["leuc_user_id"], account_id)
    db.execute(
        """UPDATE grant_applications
        SET status = 'bound', bound_account_id = ?, decided_at = ? WHERE id = ?""",
        (account_id, now, grant_id),
    )
    if grant["todo_id"]:
        db.execute(
            "UPDATE todos SET bucket = 'done', status = 'approved' WHERE id = ?",
            (grant["todo_id"],),
        )
    db.execute(
        """UPDATE todos SET status = 'approved'
        WHERE initiator_id = ? AND todo_type IN ('账号授权','账号绑定','账号申请') AND bucket = 'initiated' AND status = 'open'
        AND meta LIKE ?""",
        (grant["requester_id"], f'%"grant_id": {grant_id}%'),
    )
    # also match grant_id without space
    db.execute(
        """UPDATE todos SET status = 'approved'
        WHERE todo_type IN ('账号授权','账号绑定','账号申请') AND bucket = 'initiated' AND status = 'open'
        AND meta LIKE ?""",
        (f'%"grant_id":{grant_id}%',),
    )
    db.commit()
    acct = db.execute("SELECT * FROM system_accounts WHERE id = ?", (account_id,)).fetchone()
    sys_row = db.execute("SELECT name FROM systems WHERE id = ?", (grant["system_id"],)).fetchone()
    push_system_message(
        db,
        grant["leuc_user_id"],
        "账号申请完成",
        f"您账号申请的「{sys_row['name'] if sys_row else '系统'}」账号 {acct['account_name']} 已确认，可登录使用。",
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": f"已绑定 {urow['display_name']} ↔ {acct['account_name']}",
            "account_id": account_id,
        }
    )


@app.post("/api/sys-accounts/reject")
@login_required
def sys_accounts_reject(user):
    data = request.get_json(force=True) or {}
    grant_id = data.get("grant_id")
    db = get_db()
    grant = db.execute("SELECT * FROM grant_applications WHERE id = ?", (grant_id,)).fetchone()
    if not grant:
        return jsonify({"ok": False, "error": "不存在"}), 404
    if not require_sys_owner(user, grant["system_id"]):
        return jsonify({"ok": False, "error": "无权限"}), 403
    now = datetime.now().strftime("%Y-%m-%d")
    db.execute(
        "UPDATE grant_applications SET status = 'rejected', decided_at = ? WHERE id = ?",
        (now, grant_id),
    )
    if grant["todo_id"]:
        db.execute(
            "UPDATE todos SET bucket = 'done', status = 'rejected' WHERE id = ?",
            (grant["todo_id"],),
        )
    db.commit()
    return jsonify({"ok": True, "message": "已驳回账号申请"})


# AI-GEN-BEGIN
def _parse_system_account_csv_row(row):
    """解析账号池 CSV 行。

    支持：
    - 唯一标识,账号名,姓名,手机,邮箱,itcode
    - 账号名,姓名,手机,邮箱,itcode（兼容旧格式，account_uid=账号名）
    """
    cells = [((c or "").strip()) for c in row]
    if not cells or not cells[0] or cells[0].startswith("#"):
        return None
    head0 = cells[0]
    if head0 in (
        "账号", "account_name", "账号名",
        "唯一标识", "account_uid", "uid", "外部ID",
    ):
        return None
    # 6 列起视为带唯一标识
    if len(cells) >= 6:
        account_uid = cells[0]
        account_name = cells[1] or account_uid
        display_name = cells[2]
        phone = cells[3] or None
        email = cells[4] or None
        itcode = cells[5] or None
    else:
        account_name = cells[0]
        account_uid = account_name
        display_name = cells[1] if len(cells) > 1 else ""
        phone = cells[2] if len(cells) > 2 else None
        email = cells[3] if len(cells) > 3 else None
        itcode = cells[4] if len(cells) > 4 else None
    if not account_uid:
        return None
    return {
        "account_uid": account_uid,
        "account_name": account_name or account_uid,
        "display_name": display_name or None,
        "phone": phone or None,
        "email": email or None,
        "itcode": itcode or None,
    }


def upsert_system_account(db, system_id, *, account_uid, account_name, display_name=None,
                          phone=None, email=None, itcode=None, source="import", now=None):
    """按 system_id + account_uid 幂等写入账号池。"""
    migrate_schema(db)
    now = now or datetime.now().strftime("%Y-%m-%d")
    account_uid = (account_uid or "").strip()
    account_name = (account_name or account_uid).strip()
    if not account_uid:
        raise ValueError("account_uid 必填")
    exists = db.execute(
        """SELECT id FROM system_accounts
        WHERE system_id = ? AND account_uid = ? LIMIT 1""",
        (int(system_id), account_uid),
    ).fetchone()
    if exists:
        db.execute(
            """UPDATE system_accounts
            SET account_name=?, display_name=?, phone=?, email=?, itcode=?, source=?
            WHERE id=?""",
            (
                account_name,
                display_name,
                phone,
                email,
                itcode,
                source,
                exists["id"],
            ),
        )
        return exists["id"], False
    cur = db.execute(
        """INSERT INTO system_accounts
        (system_id, account_uid, account_name, display_name, phone, email, itcode,
         status, source, created_at)
        VALUES (?,?,?,?,?,?,?, 'unbound', ?, ?)""",
        (
            int(system_id),
            account_uid,
            account_name,
            display_name,
            phone,
            email,
            itcode,
            source,
            now,
        ),
    )
    return cur.lastrowid, True


def _import_accounts_from_csv(system_id, text, source):
    db = get_db()
    migrate_schema(db)
    now = datetime.now().strftime("%Y-%m-%d")
    reader = csv.reader(io.StringIO(text.strip()))
    added = 0
    updated = 0
    for row in reader:
        parsed = _parse_system_account_csv_row(row)
        if not parsed:
            continue
        _, is_new = upsert_system_account(
            db,
            system_id,
            account_uid=parsed["account_uid"],
            account_name=parsed["account_name"],
            display_name=parsed["display_name"],
            phone=parsed["phone"],
            email=parsed["email"],
            itcode=parsed["itcode"],
            source=source,
            now=now,
        )
        if is_new:
            added += 1
        else:
            updated += 1
    db.commit()
    return {"added": added, "updated": updated}


# AI-GEN-BEGIN
def _beisen_id_of_emp(row) -> str | None:
    v = row.get("beisen_id")
    if v is None or v == "":
        v = row.get("beisenId") or row.get("beisen_user_id")
    if v is None or v == "":
        return None
    return str(v).strip()


def sync_beisen_accounts_from_org(db, system_id: int) -> dict:
    """北森账号池真实同步：优先 LeOrg /v1/employees（beisen_id），失败则用本系统组织人员。

    文档：https://leorg-ai.lecoosys.com/api/docs/
    """
    migrate_schema(db)
    now = datetime.now().strftime("%Y-%m-%d")
    added = 0
    updated = 0
    skipped = 0
    source = "leorg"
    source_label = "LeOrg"
    people: list[dict] = []

    cfg = leorg_load_config()
    if cfg and cfg.enabled and LeorgClient is not None:
        try:
            client = LeorgClient(cfg)
            seen: set[int] = set()
            # 在职 + 试用（与部门人员同步一致）
            for st in (1, 2):
                for e in client.list_employees(emp_status=st):
                    eid = e.get("id")
                    if eid is not None:
                        if int(eid) in seen:
                            continue
                        seen.add(int(eid))
                    people.append(e)
        except Exception as exc:
            # 降级：用本地组织已同步的北森 ID
            source = "local_org"
            source_label = f"本系统组织人员（LeOrg 失败：{exc}）"
            people = []

    if not people:
        source = "local_org" if source != "leorg" else "local_org"
        source_label = "本系统组织人员" if source_label == "LeOrg" else source_label
        rows = db.execute(
            """SELECT username, display_name, phone, email, itcode, beisen_user_id
            FROM users
            WHERE beisen_user_id IS NOT NULL AND TRIM(beisen_user_id) != ''
              AND COALESCE(status, 'active') != 'closed'
            ORDER BY id"""
        ).fetchall()
        for r in rows:
            people.append(
                {
                    "beisen_id": r["beisen_user_id"],
                    "name": r["display_name"],
                    "emp_no": r["itcode"] or r["username"],
                    "email": r["email"],
                    "mobile": r["phone"],
                    "username": r["username"],
                }
            )

    for e in people:
        bid = _beisen_id_of_emp(e)
        if not bid:
            skipped += 1
            continue
        name = (e.get("name") or e.get("display_name") or "").strip() or bid
        emp_no = (e.get("emp_no") or e.get("itcode") or "").strip()
        email = (e.get("email") or "").strip() or None
        phone = (e.get("mobile") or e.get("phone") or "").strip() or None
        if phone and "*" in phone:
            phone = None
        uname = (e.get("username") or "").strip()
        if email and "@" in email:
            account_name = email.split("@", 1)[0]
        elif emp_no:
            account_name = emp_no
        elif uname:
            account_name = uname
        else:
            account_name = f"beisen_{bid}"
        _, is_new = upsert_system_account(
            db,
            system_id,
            account_uid=bid,
            account_name=account_name,
            display_name=name,
            phone=phone,
            email=email,
            itcode=emp_no or uname or None,
            source=source,
            now=now,
        )
        if is_new:
            added += 1
        else:
            updated += 1

    db.commit()
    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "total": len(people),
        "source": source,
        "source_label": source_label,
    }
# AI-GEN-END


@app.post("/api/sys-accounts")
@login_required
def sys_accounts_create(user):
    """手动添加子系统账号（须含唯一标识 / SSO 登录字段值）。"""
    # AI-GEN-BEGIN
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    system_id = int(data.get("system_id") or 0)
    if not require_sys_owner(user, system_id):
        return jsonify({"ok": False, "error": "非负责的系统"}), 403
    account_uid = (data.get("account_uid") or "").strip()
    account_name = (data.get("account_name") or "").strip()
    if not account_uid:
        return jsonify({"ok": False, "error": "唯一标识（SSO登录字段）必填"}), 400
    if not account_name:
        account_name = account_uid
    db = get_db()
    migrate_schema(db)
    sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone()
    if not sys_row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    try:
        aid, is_new = upsert_system_account(
            db,
            system_id,
            account_uid=account_uid,
            account_name=account_name,
            display_name=(data.get("display_name") or "").strip() or None,
            phone=(data.get("phone") or "").strip() or None,
            email=(data.get("email") or "").strip() or None,
            itcode=(data.get("itcode") or "").strip() or None,
            source="manual",
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    db.commit()
    field = (
        sys_row["sso_login_field"]
        if "sso_login_field" in sys_row.keys() and sys_row["sso_login_field"]
        else ("account_uid" if sys_row["code"] == "beisen" else "account_name")
    )
    label = sso_login_field_label(field, sys_row["code"])
    return jsonify(
        {
            "ok": True,
            "id": aid,
            "created": is_new,
            "account_uid": account_uid,
            "account_name": account_name,
            "sso_login_field": field,
            "sso_login_field_label": label,
            "message": (
                f"已添加账号 {account_name}（{label}={account_uid}）"
                if is_new
                else f"已更新账号 {account_name}（{label}={account_uid}）"
            ),
        }
    )
    # AI-GEN-END


@app.post("/api/sys-accounts/import")
@login_required
def sys_accounts_import(user):
    """导入/同步子系统账号。CSV：唯一标识(SSO),账号名,姓名,手机,邮箱,itcode"""
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    system_id = int(data.get("system_id") or 0)
    if not require_sys_owner(user, system_id):
        return jsonify({"ok": False, "error": "非负责的系统"}), 403
    stats = _import_accounts_from_csv(system_id, data.get("csv") or "", data.get("source") or "import")
    return jsonify(
        {
            "ok": True,
            **stats,
            "message": f"已导入/同步，新增 {stats['added']}、更新 {stats['updated']} 个子系统账号",
        }
    )


@app.post("/api/sys-accounts/sync")
@app.post("/api/sys-accounts/sync-demo")
@login_required
def sys_accounts_sync(user):
    """子系统账号同步。北森：LeOrg 员工 beisen_id → 账号池；其它系统仍为演示数据。"""
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    system_id = int(data.get("system_id") or 0)
    if not require_sys_owner(user, system_id):
        return jsonify({"ok": False, "error": "非负责的系统"}), 403
    # AI-GEN-BEGIN
    db = get_db()
    migrate_schema(db)
    sys_row = db.execute(
        "SELECT id, code, name FROM systems WHERE id = ?", (system_id,)
    ).fetchone()
    if not sys_row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    code = sys_row["code"] or ""

    if code == "beisen":
        try:
            stats = sync_beisen_accounts_from_org(db, system_id)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"北森账号同步失败：{exc}"}), 500
        skip_n = int(stats.get("skipped") or 0)
        skip_part = f"，跳过无北森ID {skip_n}" if skip_n else ""
        return jsonify(
            {
                "ok": True,
                **stats,
                "system": sys_row["name"],
                "real": True,
                "message": (
                    f"已从{stats.get('source_label') or 'LeOrg'}同步北森账号："
                    f"新增 {stats['added']}、更新 {stats['updated']}"
                    f"（扫描 {stats.get('total', 0)} 人{skip_part}）"
                ),
            }
        )

    csv_text = (
        "唯一标识,账号名,姓名,手机,邮箱,itcode\n"
        "SYNC-A-001,sync_demo_a,同步甲,13920000001,a@lecoo.com,synca\n"
        "SYNC-B-001,sync_demo_b,同步乙,13920000002,b@lecoo.com,syncb\n"
    )
    stats = _import_accounts_from_csv(system_id, csv_text, "sync")
    return jsonify(
        {
            "ok": True,
            **stats,
            "real": False,
            "message": (
                f"已演示同步 {sys_row['name']}：新增 {stats['added']}、更新 {stats['updated']} "
                f"（非北森仍为模拟；北森请选北森后同步）"
            ),
        }
    )
    # AI-GEN-END


@app.get("/api/admin/systems")
@app.get("/api/my-systems")
@login_required
def admin_systems(user):
    """业务系统管理：列举全部可登录系统（申请账号同源列表）。仅超管/系统管理员。"""
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "system_owner"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    manage_ids = managed_system_ids(db, user)
    # 系统管理员仅看自己负责的系统；超管看全部并可分配
    if manage_ids is None:
        rows = db.execute("SELECT * FROM systems ORDER BY id").fetchall()
    else:
        if not manage_ids:
            return jsonify(
                {
                    "ok": True,
                    "systems": [],
                    "issuer": _issuer(),
                    "owner_candidates": [],
                    "is_super": False,
                }
            )
        ph = ",".join("?" * len(manage_ids))
        rows = db.execute(
            f"SELECT * FROM systems WHERE id IN ({ph}) ORDER BY id", manage_ids
        ).fetchall()
    perms_by_sys = {}
    for r in db.execute(
        """SELECT id, system_id, perm_code, perm_name, description, parent_id,
           is_sensitive, enabled
        FROM sensitive_perm_defs ORDER BY id"""
    ).fetchall():
        perms_by_sys.setdefault(r["system_id"], []).append(dict(r))
    accts_by_sys = {}
    for r in db.execute(
        """SELECT a.id, a.system_id, a.account_uid, a.account_name, a.display_name, a.phone, a.email,
           a.itcode, a.status, a.source, a.leuc_user_id,
           u.display_name AS leuc_name, u.username AS leuc_username
        FROM system_accounts a
        LEFT JOIN users u ON u.id = a.leuc_user_id
        ORDER BY a.system_id, a.id"""
    ).fetchall():
        item_a = dict(r)
        item_a["bound"] = bool(r["leuc_user_id"])
        item_a["bind_label"] = "已绑定" if r["leuc_user_id"] else "无归属"
        accts_by_sys.setdefault(r["system_id"], []).append(item_a)
    # LEUC 侧已开通账号（可登录视角）
    login_by_sys = {}
    for r in db.execute(
        """SELECT a.id, a.system_id, a.account_name, a.account_label, a.can_login,
           a.has_sensitive, a.perm_summary, a.is_default,
           u.display_name AS leuc_name, u.username AS leuc_username, u.id AS leuc_user_id
        FROM user_system_accounts a
        JOIN users u ON u.id = a.user_id
        ORDER BY a.system_id, a.id"""
    ).fetchall():
        login_by_sys.setdefault(r["system_id"], []).append(dict(r))
    out = []
    for r in rows:
        item = dict(r)
        item["client_secret_masked"] = "••••••••"
        item["redirect_uri_list"] = _parse_redirect_uris(r["redirect_uris"])
        accounts = accts_by_sys.get(r["id"], [])
        item["accounts"] = accounts
        item["account_pool_count"] = len(accounts)
        item["account_bound_count"] = sum(1 for a in accounts if a.get("bound"))
        item["account_unbound_count"] = item["account_pool_count"] - item["account_bound_count"]
        item["login_accounts"] = login_by_sys.get(r["id"], [])
        item["has_sensitive"] = int(
            r["has_sensitive"] if "has_sensitive" in r.keys() else 0
        )
        item["permissions"] = perms_by_sys.get(r["id"], [])
        owners = fetch_system_owners(db, r["id"])
        item["owners"] = owners
        item["owner_user_ids"] = [o["id"] for o in owners]
        item["can_manage"] = manage_ids is None or r["id"] in manage_ids
        # AI-GEN-BEGIN
        item["is_builtin"] = int(r["is_builtin"] if "is_builtin" in r.keys() else 0)
        if "sso_login_field" not in item or not item.get("sso_login_field"):
            item["sso_login_field"] = (
                "account_uid" if item.get("code") == "beisen" else "account_name"
            )
        enrich_system_sso_fields(item)
        # AI-GEN-END
        out.append(item)
    cand = db.execute(
        """SELECT id, username, display_name, role FROM users
        WHERE role IN ('system_owner','super_admin')
        ORDER BY CASE role WHEN 'system_owner' THEN 0 ELSE 1 END, id"""
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "systems": out,
            "issuer": _issuer(),
            "owner_candidates": [dict(c) for c in cand],
            "is_super": user_has_role(user, "super_admin"),
            # AI-GEN-BEGIN
            "sso_login_fields": [
                {"value": v, "label": SSO_LOGIN_FIELD_LABELS[v]} for v in SSO_LOGIN_FIELDS
            ],
            # AI-GEN-END
        }
    )
    # AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/owners")
@login_required
def admin_system_owners(user, sid):
    """超管指定系统管理员（可多人）。"""
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "仅超级管理员可指定系统管理员"}), 403
    data = request.get_json(force=True) or {}
    owner_ids = data.get("owner_user_ids") or data.get("owners") or []
    db = get_db()
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    saved = set_system_owners(db, sid, owner_ids)
    db.commit()
    owners = fetch_system_owners(db, sid)
    return jsonify(
        {
            "ok": True,
            "owner_user_ids": saved,
            "owners": owners,
            "message": f"已指定 {len(owners)} 名系统管理员" if owners else "已清空系统管理员",
        }
    )
    # AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/has-sensitive")
@login_required
def admin_system_has_sensitive(user, sid):
    # AI-GEN-BEGIN
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    data = request.get_json(force=True) or {}
    flag = 1 if data.get("has_sensitive") else 0
    db = get_db()
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    db.execute("UPDATE systems SET has_sensitive = ? WHERE id = ?", (flag, sid))
    db.commit()
    return jsonify(
        {
            "ok": True,
            "has_sensitive": flag,
            "message": "已标记含敏感权限" if flag else "已取消敏感权限标记",
        }
    )
    # AI-GEN-END


# AI-GEN-BEGIN
SSO_LOGIN_FIELDS = ("account_uid", "account_name", "email", "phone", "itcode")
SSO_LOGIN_FIELD_LABELS = {
    "account_uid": "唯一标识/外部用户ID",
    "account_name": "业务系统账号名",
    "email": "邮箱",
    "phone": "手机号",
    "itcode": "工号/itcode",
}


def sso_login_field_label(field: str, system_code: str | None = None) -> str:
    f = (field or "account_name").strip()
    if system_code == "beisen" and f == "account_uid":
        return "北森用户ID"
    return SSO_LOGIN_FIELD_LABELS.get(f, f)


def enrich_system_sso_fields(item: dict) -> dict:
    code = item.get("code")
    field = (item.get("sso_login_field") or "account_name").strip() or "account_name"
    item["sso_login_field"] = field
    item["sso_login_field_label"] = sso_login_field_label(field, code)
    item["sso_login_field_options"] = [
        {
            "value": v,
            "label": sso_login_field_label(v, code if v == "account_uid" else None),
        }
        for v in SSO_LOGIN_FIELDS
    ]
    # 北森选项里 account_uid 固定显示「北森用户ID」
    if code == "beisen":
        for opt in item["sso_login_field_options"]:
            if opt["value"] == "account_uid":
                opt["label"] = "北森用户ID"
    return item


@app.post("/api/admin/systems/<int:sid>/sso-login-field")
@login_required
def admin_system_sso_login_field(user, sid):
    """配置子系统 SSO 登录字段（账号池中用于签发 sub 的字段）。"""
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    data = request.get_json(force=True) or {}
    field = (data.get("sso_login_field") or "").strip()
    if field not in SSO_LOGIN_FIELDS:
        return jsonify(
            {
                "ok": False,
                "error": f"sso_login_field 须为 {' / '.join(SSO_LOGIN_FIELDS)}",
            }
        ), 400
    db = get_db()
    migrate_schema(db)
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    db.execute("UPDATE systems SET sso_login_field = ? WHERE id = ?", (field, sid))
    db.commit()
    label = sso_login_field_label(field, row["code"])
    return jsonify(
        {
            "ok": True,
            "sso_login_field": field,
            "sso_login_field_label": label,
            "message": f"已设置 SSO 登录字段为「{label}」",
        }
    )
# AI-GEN-END


@app.post("/api/admin/systems")
@login_required
def admin_create_system(user):
    if not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "仅超管可添加系统"}), 403
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    redirect_uris = (data.get("redirect_uris") or "").strip()
    if not name or not code or not redirect_uris:
        return jsonify({"ok": False, "error": "名称、code、回调地址必填"}), 400
    client_id = (data.get("client_id") or f"client_{code}").strip()
    client_secret = data.get("client_secret") or ("sk_" + uuid.uuid4().hex[:20])
    require_pkce = 1 if data.get("require_pkce", True) else 0
    access_mode = data.get("access_mode") or "apply"
    if access_mode not in ("open", "apply"):
        return jsonify({"ok": False, "error": "access_mode 须为 open(全员登录) 或 apply(需账号绑定)"}), 400
    forbid_external = 1 if data.get("forbid_external") else 0
    # AI-GEN-BEGIN
    sso_login_field = (data.get("sso_login_field") or "").strip()
    if not sso_login_field:
        sso_login_field = "account_uid" if code == "beisen" else "account_name"
    if sso_login_field not in SSO_LOGIN_FIELDS:
        return jsonify(
            {"ok": False, "error": f"sso_login_field 须为 {' / '.join(SSO_LOGIN_FIELDS)}"}
        ), 400
    # AI-GEN-END
    owner_ids = data.get("owner_user_ids") or data.get("owners") or []
    db = get_db()
    migrate_schema(db)
    try:
        cur = db.execute(
            """INSERT INTO systems
            (code, name, client_id, client_secret, redirect_uris, scopes, grant_types,
             token_endpoint_auth_method, require_pkce, access_mode, forbid_external,
             sso_login_field, status, owner_user_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                code,
                name,
                client_id,
                client_secret,
                redirect_uris,
                data.get("scopes") or "openid profile",
                "authorization_code",
                "client_secret_post",
                require_pkce,
                access_mode,
                forbid_external,
                sso_login_field,
                "enabled",
                None,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        sid = cur.lastrowid
        set_system_owners(db, sid, owner_ids)
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "error": f"创建失败：{e}"}), 400
    mode_label = "全员登录" if access_mode == "open" else "需账号绑定"
    return jsonify(
        {
            "ok": True,
            "id": sid,
            "client_id": client_id,
            "client_secret": client_secret,
            "access_mode": access_mode,
            "sso_login_field": sso_login_field,
            "owners": fetch_system_owners(db, sid),
            "message": f"已添加（{mode_label}），请妥善保存 secret",
        }
    )


# AI-GEN-BEGIN
@app.patch("/api/admin/systems/<int:sid>")
@app.put("/api/admin/systems/<int:sid>")
@login_required
def admin_update_system(user, sid):
    """编辑业务系统：name / 回调 / 准入 / 外部登录 / SSO字段 / 管理员；code 与 client_id 不可改。"""
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    data = request.get_json(force=True) or {}
    db = get_db()
    migrate_schema(db)
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    # 拒绝篡改不可变字段
    if "code" in data and (data.get("code") or "").strip() and (data.get("code") or "").strip() != row["code"]:
        return jsonify({"ok": False, "error": "code 不可修改"}), 400
    if (
        "client_id" in data
        and (data.get("client_id") or "").strip()
        and (data.get("client_id") or "").strip() != row["client_id"]
    ):
        return jsonify({"ok": False, "error": "client_id 不可修改"}), 400

    name = (data.get("name") if "name" in data else row["name"]) or ""
    name = str(name).strip()
    redirect_uris = (
        data.get("redirect_uris") if "redirect_uris" in data else row["redirect_uris"]
    ) or ""
    redirect_uris = str(redirect_uris).strip()
    if not name or not redirect_uris:
        return jsonify({"ok": False, "error": "名称、回调地址必填"}), 400

    access_mode = data.get("access_mode") if "access_mode" in data else row["access_mode"]
    access_mode = (access_mode or "apply").strip()
    if access_mode not in ("open", "apply"):
        return jsonify({"ok": False, "error": "access_mode 须为 open 或 apply"}), 400

    if "forbid_external" in data:
        forbid_external = 1 if data.get("forbid_external") else 0
    else:
        forbid_external = int(row["forbid_external"] or 0)

    if "require_pkce" in data:
        require_pkce = 1 if data.get("require_pkce") else 0
    else:
        require_pkce = int(row["require_pkce"] or 1)

    sso_login_field = (
        data.get("sso_login_field")
        if "sso_login_field" in data
        else (row["sso_login_field"] if "sso_login_field" in row.keys() else None)
    )
    sso_login_field = (sso_login_field or "").strip()
    if not sso_login_field:
        sso_login_field = "account_uid" if row["code"] == "beisen" else "account_name"
    if sso_login_field not in SSO_LOGIN_FIELDS:
        return jsonify(
            {"ok": False, "error": f"sso_login_field 须为 {' / '.join(SSO_LOGIN_FIELDS)}"}
        ), 400

    scopes = data.get("scopes") if "scopes" in data else row["scopes"]
    scopes = (scopes or "openid profile").strip()

    # 仅超管可改系统管理员（先校验，避免半更新）
    touch_owners = "owner_user_ids" in data or "owners" in data
    if touch_owners and not user_has_role(user, "super_admin"):
        return jsonify({"ok": False, "error": "仅超管可修改系统管理员"}), 403

    db.execute(
        """UPDATE systems SET
          name=?, redirect_uris=?, access_mode=?, forbid_external=?,
          require_pkce=?, sso_login_field=?, scopes=?
        WHERE id=?""",
        (
            name,
            redirect_uris,
            access_mode,
            forbid_external,
            require_pkce,
            sso_login_field,
            scopes,
            sid,
        ),
    )
    if touch_owners:
        owner_ids = data.get("owner_user_ids") or data.get("owners") or []
        set_system_owners(db, sid, owner_ids)

    db.commit()
    owners = fetch_system_owners(db, sid)
    return jsonify(
        {
            "ok": True,
            "id": sid,
            "code": row["code"],
            "client_id": row["client_id"],
            "name": name,
            "redirect_uris": redirect_uris,
            "access_mode": access_mode,
            "forbid_external": forbid_external,
            "sso_login_field": sso_login_field,
            "sso_login_field_label": sso_login_field_label(sso_login_field, row["code"]),
            "owners": owners,
            "message": "已保存系统配置",
        }
    )
# AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/status")
@login_required
def admin_system_status(user, sid):
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    data = request.get_json(force=True) or {}
    status = data.get("status")
    if status not in ("enabled", "disabled"):
        return jsonify({"ok": False, "error": "status 须为 enabled/disabled"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    # AI-GEN-BEGIN
    if int(row["is_builtin"] if "is_builtin" in row.keys() else 0):
        return jsonify({"ok": False, "error": "本系统为内置系统，不可禁用或删除"}), 400
    # AI-GEN-END
    db.execute("UPDATE systems SET status = ? WHERE id = ?", (status, sid))
    db.commit()
    return jsonify({"ok": True, "status": status})


@app.post("/api/admin/systems/<int:sid>/forbid-external")
@login_required
def admin_system_forbid_external(user, sid):
    """系统标识：禁止外部人员登录（默认关）。"""
    # AI-GEN-BEGIN
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    data = request.get_json(force=True) or {}
    forbid = 1 if data.get("forbid_external") else 0
    db = get_db()
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    db.execute("UPDATE systems SET forbid_external = ? WHERE id = ?", (forbid, sid))
    db.commit()
    return jsonify(
        {
            "ok": True,
            "forbid_external": forbid,
            "message": "已开启禁止外部人员" if forbid else "已允许外部人员（若业务允许）",
        }
    )
    # AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/rotate-secret")
@login_required
def rotate_secret(user, sid):
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    db = get_db()
    row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    new_secret = "sk_" + uuid.uuid4().hex[:20]
    db.execute("UPDATE systems SET client_secret = ? WHERE id = ?", (new_secret, sid))
    db.commit()
    return jsonify({"ok": True, "client_secret": new_secret})


@app.get("/api/admin/systems/<int:sid>/credentials")
@login_required
def admin_system_credentials(user, sid):
    """系统管理员 / 超管查看 client_id 与 client_secret。"""
    # AI-GEN-BEGIN
    if not require_sys_owner(user, sid):
        return jsonify({"ok": False, "error": "无权限或不负责该系统"}), 403
    db = get_db()
    row = db.execute(
        "SELECT id, code, name, client_id, client_secret, redirect_uris, status FROM systems WHERE id = ?",
        (sid,),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "系统不存在"}), 404
    return jsonify(
        {
            "ok": True,
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "client_id": row["client_id"],
            "client_secret": row["client_secret"],
            "redirect_uris": row["redirect_uris"],
            "status": row["status"],
        }
    )
    # AI-GEN-END


def _issuer():
    return request.host_url.rstrip("/")


@app.get("/.well-known/openid-configuration")
def openid_configuration():
    iss = _issuer()
    return jsonify(
        {
            "issuer": iss,
            "authorization_endpoint": f"{iss}/oauth/authorize",
            "token_endpoint": f"{iss}/oauth/token",
            "userinfo_endpoint": f"{iss}/oauth/userinfo",
            "jwks_uri": f"{iss}/.well-known/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["HS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "scopes_supported": ["openid", "profile", "email"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "claims_supported": ["sub", "name", "preferred_username", "email", "phone_number"],
        }
    )


@app.get("/.well-known/jwks.json")
def jwks():
    # 演示：不暴露真实密钥；生产应使用非对称 JWK
    return jsonify({"keys": []})


@app.get("/api/systems/public")
def systems_public():
    rows = get_db().execute(
        "SELECT id, code, name, client_id, redirect_uris, status, require_pkce, access_mode FROM systems ORDER BY id"
    ).fetchall()
    # AI-GEN-BEGIN
    return jsonify(
        {
            "ok": True,
            "issuer": _issuer(),
            "systems": [dict(r) for r in rows],
        }
    )
    # AI-GEN-END


# AI-GEN-BEGIN
def _oa_can_view(user):
    return user_has_role(user, "hr_specialist", "system_owner", "super_admin")


def _oa_hr_user_id(db):
    row = db.execute(
        "SELECT id FROM users WHERE role = 'hr_specialist' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _oa_find_user(db, oa_person_code, applicant_name=None, beisen_user_id=None):
    """匹配 LEUC 用户：优先北森 userId，再 itcode/username，再唯一姓名。"""
    # AI-GEN-BEGIN
    bid = str(beisen_user_id or "").strip()
    if bid:
        row = db.execute(
            "SELECT * FROM users WHERE beisen_user_id = ?", (bid,)
        ).fetchone()
        if row:
            return row
    code = (oa_person_code or "").strip()
    if code:
        row = db.execute(
            "SELECT * FROM users WHERE itcode = ? OR username = ? OR beisen_user_id = ?",
            (code, code, code),
        ).fetchone()
        if row:
            return row
    name = (applicant_name or "").strip()
    if name:
        rows = db.execute(
            "SELECT * FROM users WHERE display_name = ?", (name,)
        ).fetchall()
        if len(rows) == 1:
            return rows[0]
    return None
    # AI-GEN-END


def _oa_refresh_form_status(db, form_id):
    if not form_id:
        return
    lines = db.execute(
        "SELECT handle_status FROM oa_form_lines WHERE form_id = ?", (form_id,)
    ).fetchall()
    if not lines:
        return
    statuses = [l["handle_status"] for l in lines]
    if all(s == "done" for s in statuses):
        st = "done"
    elif any(s in ("pending_bind", "pending_close", "pending_create_user", "pending") for s in statuses):
        st = "processing"
    elif any(s == "rejected" for s in statuses) and all(
        s in ("done", "rejected") for s in statuses
    ):
        st = "done"
    else:
        st = "processing"
    db.execute("UPDATE oa_forms SET status = ? WHERE id = ?", (st, form_id))


def _oa_spawn_bind_line(db, form_id, line_id, sys_row, leuc_user, requester_id, now):
    """为一条申请明细创建账号绑定待办 + grant。"""
    owner_id = sys_row["owner_user_id"] or requester_id
    urow = leuc_user
    matches = match_system_account(db, urow, sys_row["id"])
    suggested = matches[0]["account"]["id"] if matches else None
    hints = json.dumps(
        [
            {"account_id": m["account"]["id"], "score": m["score"], "hits": m["hits"]}
            for m in matches[:5]
        ],
        ensure_ascii=False,
    )
    title = f"OA账号申请 · {urow['display_name']} 绑定 {sys_row['name']}"
    tcur = db.execute(
        """INSERT INTO todos
        (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
        VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
        (
            owner_id,
            requester_id,
            title,
            "账号申请",
            now,
            json.dumps(
                {
                    "oa_form_id": form_id,
                    "oa_line_id": line_id,
                    "system_id": sys_row["id"],
                    "leuc_user_id": urow["id"],
                },
                ensure_ascii=False,
            ),
        ),
    )
    todo_id = tcur.lastrowid
    gcur = db.execute(
        """INSERT INTO grant_applications
        (requester_id, system_id, leuc_user_id, status, suggested_account_id,
         match_hints, created_at, todo_id)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            requester_id,
            sys_row["id"],
            urow["id"],
            "pending",
            suggested,
            hints,
            now,
            todo_id,
        ),
    )
    db.execute(
        """INSERT INTO todos
        (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
        VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
        (
            owner_id,
            requester_id,
            f"OA账号申请 {sys_row['name']}",
            "账号申请",
            now,
            json.dumps({"grant_id": gcur.lastrowid, "oa_line_id": line_id}, ensure_ascii=False),
        ),
    )
    db.execute(
        """UPDATE oa_form_lines
        SET handle_status = 'pending_bind', todo_id = ?, grant_id = ?, remark = ?
        WHERE id = ?""",
        (todo_id, gcur.lastrowid, "待系统负责人确认绑定", line_id),
    )
    return todo_id


def _oa_spawn_bind_for_form(db, form_id, leuc_user_id, requester_id):
    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END
    urow = db.execute("SELECT * FROM users WHERE id = ?", (leuc_user_id,)).fetchone()
    if not urow:
        return
    lines = db.execute(
        """SELECT * FROM oa_form_lines
        WHERE form_id = ? AND handle_status IN ('pending_create_user','pending')""",
        (form_id,),
    ).fetchall()
    for line in lines:
        sys_row = None
        if line["system_id"]:
            sys_row = db.execute(
                "SELECT * FROM systems WHERE id = ?", (line["system_id"],)
            ).fetchone()
        elif line["system_code"]:
            sys_row = db.execute(
                "SELECT * FROM systems WHERE code = ?", (line["system_code"],)
            ).fetchone()
        if not sys_row:
            db.execute(
                "UPDATE oa_form_lines SET handle_status = 'skipped', remark = ? WHERE id = ?",
                ("系统未登记，跳过", line["id"]),
            )
            continue
        _oa_spawn_bind_line(db, form_id, line["id"], sys_row, urow, requester_id, now)
    _oa_refresh_form_status(db, form_id)


@app.get("/api/oa/forms")
@login_required
def oa_forms_list(user):
    if not _oa_can_view(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    forms = db.execute(
        """SELECT f.*, u.display_name AS leuc_name, u.username AS leuc_username
        FROM oa_forms f
        LEFT JOIN users u ON u.id = f.leuc_user_id
        ORDER BY f.id DESC"""
    ).fetchall()
    out = []
    for f in forms:
        item = dict(f)
        # AI-GEN-BEGIN
        rem = item.get("remark") or ""
        if rem.startswith("{") and "processType" in rem:
            try:
                item["beisen_payload"] = json.loads(rem)
            except Exception:
                item["beisen_payload"] = None
        else:
            item["beisen_payload"] = None
        # AI-GEN-END
        item["lines"] = [
            dict(l)
            for l in db.execute(
                "SELECT * FROM oa_form_lines WHERE form_id = ? ORDER BY id", (f["id"],)
            ).fetchall()
        ]
        out.append(item)
    return jsonify({"ok": True, "forms": out})


@app.post("/api/oa/simulate-account-apply")
@login_required
def oa_simulate_account_apply(user):
    """模拟：OA「账号/权限申请」审批通过后推送至 LEUC。"""
    if not _oa_can_view(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    # 默认演示：给「刘一」申请来酷+北森（可能无 LEUC 人）；或指定已有用户
    scenario = data.get("scenario") or "mixed"
    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END
    db = get_db()
    form_no = data.get("oa_form_no") or f"OA-ACCT-{datetime.now().strftime('%H%M%S')}"

    if scenario == "existing":
        # 已知用户：张三申请科技ERP
        lines_spec = [
            {
                "system_code": "keji_erp",
                "req_category": "账号开通",
                "applicant_name": "张三",
                "applicant_job": "研发",
                "oa_person_code": "zhangsan",
            }
        ]
        title = "OA账号申请 · 张三 · 科技ERP"
    elif scenario == "new_person":
        lines_spec = [
            {
                "system_code": "laiku_erp",
                "req_category": "账号开通",
                "applicant_name": "刘一",
                "applicant_job": "新人",
                "oa_person_code": "E1001",
            },
            {
                "system_code": "beisen",
                "req_category": "账号开通",
                "applicant_name": "刘一",
                "applicant_job": "新人",
                "oa_person_code": "E1001",
            },
        ]
        title = "OA账号申请 · 刘一（待建人员）"
    else:
        # mixed：李四申请北森（已有人）+ 陈二申请来酷（可能无唯一 LEUC）
        lines_spec = data.get("lines") or [
            {
                "system_code": "beisen",
                "req_category": "账号开通",
                "applicant_name": "李四",
                "applicant_job": "业务",
                "oa_person_code": "lisi",
            },
            {
                "system_code": "laiku_erp",
                "req_category": "账号开通",
                "applicant_name": "陈二",
                "applicant_job": "运营",
                "oa_person_code": "E-CHEN2",
            },
        ]
        title = "OA账号申请 · 混合演示单"

    # 按申请人分组处理（一张单可多人多行，演示简化：按行各自匹配）
    fcur = db.execute(
        """INSERT INTO oa_forms
        (form_type, oa_form_no, status, title, applicant_name, oa_person_code,
         leuc_user_id, approved_at, created_at, remark)
        VALUES ('account_apply', ?, 'received', ?, ?, ?, NULL, ?, ?, ?)""",
        (
            form_no,
            title,
            lines_spec[0].get("applicant_name"),
            lines_spec[0].get("oa_person_code"),
            now,
            now,
            "OA审批已通过，LEUC 自动接单",
        ),
    )
    form_id = fcur.lastrowid
    created_todos = []

    # 按 oa_person_code 分组
    groups = {}
    for spec in lines_spec:
        key = (spec.get("oa_person_code") or "") + "|" + (spec.get("applicant_name") or "")
        groups.setdefault(key, []).append(spec)

    for key, specs in groups.items():
        code = specs[0].get("oa_person_code")
        name = specs[0].get("applicant_name")
        urow = _oa_find_user(db, code, name)
        line_ids = []
        for spec in specs:
            sys_row = db.execute(
                "SELECT * FROM systems WHERE code = ?", (spec.get("system_code"),)
            ).fetchone()
            lcur = db.execute(
                """INSERT INTO oa_form_lines
                (form_id, system_id, system_code, system_name, req_category, system_entity,
                 applicant_name, applicant_job, oa_person_code, handle_status, remark)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    form_id,
                    sys_row["id"] if sys_row else None,
                    spec.get("system_code"),
                    sys_row["name"] if sys_row else spec.get("system_code"),
                    spec.get("req_category") or "账号开通",
                    spec.get("system_entity") or "",
                    name,
                    spec.get("applicant_job") or "",
                    code,
                    "pending",
                    "",
                ),
            )
            line_ids.append((lcur.lastrowid, sys_row, spec))

        if urow:
            db.execute(
                "UPDATE oa_forms SET leuc_user_id = COALESCE(leuc_user_id, ?) WHERE id = ?",
                (urow["id"], form_id),
            )
            for lid, sys_row, spec in line_ids:
                if not sys_row:
                    db.execute(
                        "UPDATE oa_form_lines SET handle_status='skipped', remark=? WHERE id=?",
                        ("系统未登记", lid),
                    )
                    continue
                tid = _oa_spawn_bind_line(
                    db, form_id, lid, sys_row, urow, user["id"], now
                )
                created_todos.append({"type": "账号申请", "todo_id": tid, "user": urow["display_name"]})
        else:
            hr_id = _oa_hr_user_id(db) or user["id"]
            for lid, sys_row, spec in line_ids:
                db.execute(
                    "UPDATE oa_form_lines SET handle_status='pending_create_user', remark=? WHERE id=?",
                    ("未匹配到 LEUC 用户，待人事新建", lid),
                )
            tcur = db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
                VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
                (
                    hr_id,
                    user["id"],
                    f"OA账号申请 · 新建人员 {name}（{code}）",
                    "新建人员",
                    now,
                    json.dumps(
                        {
                            "oa_form_id": form_id,
                            "applicant_name": name,
                            "oa_person_code": code,
                            "line_ids": [x[0] for x in line_ids],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            db.execute(
                """INSERT INTO todos
                (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
                VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
                (
                    hr_id,
                    user["id"],
                    f"新建人员 {name}",
                    "新建人员",
                    now,
                    json.dumps({"oa_form_id": form_id}, ensure_ascii=False),
                ),
            )
            created_todos.append(
                {"type": "新建人员", "todo_id": tcur.lastrowid, "user": name}
            )

    _oa_refresh_form_status(db, form_id)
    db.commit()
    return jsonify(
        {
            "ok": True,
            "form_id": form_id,
            "oa_form_no": form_no,
            "todos": created_todos,
            "message": f"已接收 OA 账号申请单 {form_no}，生成 {len(created_todos)} 类待办",
        }
    )


@app.post("/api/oa/simulate-leave")
@login_required
def oa_simulate_leave(user):
    """模拟：北森离职审批通过 → 按账号是否敏感进入审批链，末步系统负责人关闭。

    无敏感：直属 → 系统负责人关闭
    有敏感：直属→一级→财务 → 系统负责人关闭
    本系统（LEUC）账号单独一单，排在末尾，由本系统管理员关闭。

    对齐北森字段：userId / approvalResultType / processType / lastWorkDate / EmployeeStatus
    """
    # AI-GEN-BEGIN
    if not _oa_can_view(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    # AI-GEN-BEGIN
    now = now_ts()
    # AI-GEN-END
    db = get_db()

    payload_in = data.get("beisen") or data.get("payload") or data
    user_id = (
        payload_in.get("userId")
        or payload_in.get("UserID")
        or payload_in.get("user_id")
        or data.get("userId")
        or data.get("beisen_user_id")
    )
    original_id = payload_in.get("originalId") or data.get("originalId")
    name = (
        (payload_in.get("Name") or payload_in.get("name") or data.get("applicant_name") or "")
        .strip()
    )
    email = (payload_in.get("Email") or payload_in.get("email") or "").strip()
    last_work = (
        payload_in.get("lastWorkDate")
        or payload_in.get("LastWorkDate")
        or data.get("lastWorkDate")
        or now
    )
    emp_status = payload_in.get("EmployeeStatus")
    if emp_status is None:
        emp_status = data.get("EmployeeStatus", 8)
    approval_result = (
        payload_in.get("approvalResultType")
        or data.get("approvalResultType")
        or "Passed"
    )
    process_type = (
        payload_in.get("processType")
        or data.get("processType")
        or "DimissionProcessNew"
    )
    oa_code = (
        (data.get("oa_person_code") or original_id or "").strip()
        or (str(user_id).strip() if user_id else "")
        or "unknown"
    )
    form_no = (
        data.get("oa_form_no")
        or payload_in.get("formNo")
        or f"BS-LEAVE-{datetime.now().strftime('%H%M%S')}"
    )

    beisen_payload = {
        "userId": int(user_id) if str(user_id or "").isdigit() else user_id,
        "originalId": original_id,
        "approvalResultType": approval_result,
        "processType": process_type,
        "lastWorkDate": last_work,
        "EmployeeStatus": emp_status,
        "Name": name or None,
        "Email": email or None,
        "source": "leuc-proto-simulate",
    }

    if str(approval_result) not in ("Passed", "1", "通过"):
        return jsonify(
            {
                "ok": False,
                "error": f"仅模拟审批通过场景，收到 approvalResultType={approval_result}",
                "beisen_payload": beisen_payload,
            }
        ), 400

    urow = _oa_find_user(
        db, oa_code, name or None, beisen_user_id=user_id
    )
    if not urow and email:
        urow = db.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()

    remark_json = json.dumps(beisen_payload, ensure_ascii=False)
    display = name or (urow["display_name"] if urow else oa_code)
    fcur = db.execute(
        """INSERT INTO oa_forms
        (form_type, oa_form_no, status, title, applicant_name, oa_person_code,
         leuc_user_id, approved_at, created_at, remark)
        VALUES ('leave', ?, 'received', ?, ?, ?, ?, ?, ?, ?)""",
        (
            form_no,
            f"北森离职审批通过 · {display}",
            display,
            str(user_id or oa_code),
            urow["id"] if urow else None,
            last_work,
            now,
            remark_json,
        ),
    )
    form_id = fcur.lastrowid
    created = []

    if not urow:
        hr_id = _oa_hr_user_id(db) or user["id"]
        lcur = db.execute(
            """INSERT INTO oa_form_lines
            (form_id, system_name, applicant_name, oa_person_code, handle_status, remark)
            VALUES (?,?,?,?, 'pending_create_user', ?)""",
            (
                form_id,
                "—",
                display,
                str(user_id or oa_code),
                "未匹配到 LEUC 用户，待人事核对",
            ),
        )
        tcur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
            VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
            (
                hr_id,
                user["id"],
                f"北森离职 · 人员未匹配 {display}",
                "人员核对",
                now,
                json.dumps(
                    {
                        "oa_form_id": form_id,
                        "oa_line_id": lcur.lastrowid,
                        "applicant_name": display,
                        "oa_person_code": str(user_id or oa_code),
                        "beisen_user_id": str(user_id) if user_id else None,
                        "leave": True,
                        "source": "beisen",
                        "beisen_payload": beisen_payload,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        created.append({"type": "人员核对", "todo_id": tcur.lastrowid})
        _oa_refresh_form_status(db, form_id)
        db.commit()
        return jsonify(
            {
                "ok": True,
                "form_id": form_id,
                "beisen_payload": beisen_payload,
                "message": f"北森离职消息 {form_no} 未匹配到用户，已派人人事核对",
                "todos": created,
            }
        )

    # 确保有本系统账号，并纳入末尾关闭
    ensure_user_leuc_account(db, urow)
    accts = db.execute(
        """SELECT a.id AS account_id, a.system_id, a.account_name, a.has_sensitive,
                  s.name AS system_name, s.code AS system_code
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ? AND a.can_login = 1
        ORDER BY CASE WHEN s.code = ? THEN 1 ELSE 0 END, s.id, a.id""",
        (urow["id"], LEUC_SYSTEM_CODE),
    ).fetchall()

    if not accts:
        db.execute(
            """INSERT INTO oa_form_lines
            (form_id, applicant_name, oa_person_code, handle_status, remark)
            VALUES (?,?,?, 'done', ?)""",
            (form_id, urow["display_name"], str(user_id or oa_code), "无可关闭的可登录账号"),
        )
        db.execute("UPDATE oa_forms SET status = 'done' WHERE id = ?", (form_id,))
        db.commit()
        return jsonify(
            {
                "ok": True,
                "form_id": form_id,
                "beisen_payload": beisen_payload,
                "message": f"{urow['display_name']} 无可关闭账号",
                "applications": [],
            }
        )

    admin_fb = db.execute(
        """SELECT id FROM users
        WHERE username = ? OR role = 'super_admin'
        ORDER BY CASE WHEN username = ? THEN 0 ELSE 1 END, id LIMIT 1""",
        (SYSTEM_ADMIN_USERNAME, SYSTEM_ADMIN_USERNAME),
    ).fetchone()
    fallback_owner = admin_fb["id"] if admin_fb else user["id"]

    apps_out = []
    for a in accts:
        has_sens = bool(int(a["has_sensitive"] or 0))
        if has_sens:
            steps = materialize_approval_chain(db, "sensitive", urow["id"])
            flow_code = "beisen_leave_sensitive"
            sens_tag = "含敏感"
        else:
            direct = find_approver(db, urow["id"])
            if not direct or int(direct) == int(urow["id"]):
                steps = []
            else:
                steps = [("direct_leader", "直属领导", int(direct))]
            flow_code = "beisen_leave"
            sens_tag = "普通"
        if not steps:
            # 无直属时退化：仅系统负责人关闭
            steps = []
        before = len(steps)
        steps = append_system_owner_step(
            db, a["system_id"], steps, purpose="close"
        )
        if len(steps) == before:
            steps.append(
                ("system_owner", "系统负责人关闭账号", int(fallback_owner))
            )

        is_leuc = (a["system_code"] or "") == LEUC_SYSTEM_CODE
        title = (
            f"北森离职关闭 · {urow['display_name']} · "
            f"{a['system_name']} / {a['account_name']}（{sens_tag}）"
        )
        init_title = f"北森离职关闭 · {a['system_name']}（审批中）"
        lcur = db.execute(
            """INSERT INTO oa_form_lines
            (form_id, system_id, system_code, system_name, applicant_name, oa_person_code,
             handle_status, remark)
            VALUES (?,?,?,?,?,?, 'pending_close', ?)""",
            (
                form_id,
                a["system_id"],
                a["system_code"],
                a["system_name"],
                urow["display_name"],
                str(user_id or oa_code),
                f"已进入审批（{sens_tag}，末步系统负责人关闭）",
            ),
        )
        meta_extra = {
            "account_id": a["account_id"],
            "system_id": a["system_id"],
            "account_name": a["account_name"],
            "system_name": a["system_name"],
            "leuc_user_id": urow["id"],
            "close_login": True,
            "beisen_leave": True,
            "is_leuc": is_leuc,
            "oa_form_id": form_id,
            "oa_line_id": lcur.lastrowid,
            "beisen_payload": beisen_payload,
            "with_sensitive": has_sens,
        }
        app_id, first_todo, first_assignee, step_preview = start_multi_step_apply(
            db,
            flow_code=flow_code,
            todo_type="北森离职关闭",
            title=title,
            init_title=init_title,
            subject_id=urow["id"],
            initiator_id=user["id"],
            system_id=a["system_id"],
            steps=steps,
            meta_extra=meta_extra,
        )
        chain = " → ".join(s["label"] for s in step_preview)
        apps_out.append(
            {
                "application_id": app_id,
                "todo_id": first_todo,
                "system": a["system_name"],
                "account": a["account_name"],
                "flow_code": flow_code,
                "with_sensitive": has_sens,
                "chain": chain,
                "is_leuc": is_leuc,
            }
        )
        created.append(
            {
                "type": "北森离职关闭",
                "todo_id": first_todo,
                "system": a["system_name"],
            }
        )

    db.execute(
        "UPDATE oa_forms SET status = 'processing' WHERE id = ?", (form_id,)
    )
    db.commit()
    n_biz = sum(1 for x in apps_out if not x.get("is_leuc"))
    n_leuc = sum(1 for x in apps_out if x.get("is_leuc"))
    return jsonify(
        {
            "ok": True,
            "form_id": form_id,
            "oa_form_no": form_no,
            "user": urow["display_name"],
            "beisen_user_id": urow["beisen_user_id"] if urow["beisen_user_id"] else None,
            "beisen_payload": beisen_payload,
            "applications": apps_out,
            "todos": created,
            "message": (
                f"北森离职消息 {form_no} 已生成 {n_biz} 笔业务系统关闭审批"
                + (f" + 1 笔本系统关闭" if n_leuc else "")
                + "（按敏感区分，末步系统负责人关闭）"
            ),
        }
    )
    # AI-GEN-END


# AI-GEN-END

@app.get("/api/demo/portal-systems")
def demo_portal_systems():
    """演示导航页：返回可点击登录的业务系统（含 client_secret，仅原型）。"""
    # AI-GEN-BEGIN
    beisen_st = beisen_status_dict()
    rows = get_db().execute(
        """SELECT id, code, name, client_id, client_secret, redirect_uris,
                  access_mode, status, require_pkce
           FROM systems WHERE status = 'enabled' AND code != ? ORDER BY id""",
        (LEUC_SYSTEM_CODE,),
    ).fetchall()
    systems = []
    for r in rows:
        d = dict(r)
        d["mode_label"] = "全员登录" if d.get("access_mode") == "open" else "需账号绑定"
        d["portal_redirect"] = f"{_issuer()}/demo/home/callback?app={d['code']}"
        if d.get("code") == "beisen":
            d["beisen_sso_enabled"] = bool(beisen_st.get("enabled"))
            d["beisen_sso_error"] = beisen_st.get("error")
            d["beisen_sso_uty"] = beisen_st.get("uty")
            # 真实 SSO 成功后进入北森门户
            d["beisen_portal_url"] = (
                beisen_st.get("return_url") or "https://www.italent.cn/"
            )
            d["beisen_sso_go"] = "/beisen/sso/go?return_url=" + quote(
                d["beisen_portal_url"], safe=""
            )
        systems.append(d)
    return jsonify(
        {
            "ok": True,
            "systems": systems,
            "beisen_sso": beisen_st,
        }
    )
    # AI-GEN-END


# AI-GEN-BEGIN
def _bound_system_accounts_for_sso(db, leuc_user_id, system_code: str = "beisen"):
    """用户在某系统全部「已开通可登录」的账号池记录（可多条）。"""
    return db.execute(
        """SELECT a.*, s.code AS system_code, s.sso_login_field, u.can_login, u.is_default
        FROM system_accounts a
        JOIN systems s ON s.id = a.system_id
        JOIN user_system_accounts u ON u.user_id = a.leuc_user_id
          AND u.system_id = a.system_id AND u.account_name = a.account_name
        WHERE a.leuc_user_id = ? AND s.code = ?
          AND IFNULL(a.status, '') != 'closed'
          AND u.can_login = 1
        ORDER BY u.is_default DESC, a.id""",
        (leuc_user_id, system_code),
    ).fetchall()


def _bound_system_account_for_sso(db, leuc_user_id, system_code: str = "beisen"):
    """取用户在某系统「已申请开通且可登录」的绑定账号池记录。

    必须同时满足：
    - system_accounts.leuc_user_id 已绑定
    - user_system_accounts.can_login = 1（申请/开通后才可登录）
    不用通讯录 users.beisen_user_id。
    多账号时优先：默认账号 → 合法北森用户ID(正整数) → id 较小。
    """
    rows = _bound_system_accounts_for_sso(db, leuc_user_id, system_code)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    # 优先合法正整数 account_uid（uty=id）
    numeric = []
    for r in rows:
        uid = (r["account_uid"] or "").strip()
        if _beisen_sub_is_positive_id(uid):
            numeric.append(r)
    if len(numeric) == 1:
        return numeric[0]
    if numeric:
        # 多个合法：仍取默认优先（已 ORDER BY is_default）
        return numeric[0]
    return rows[0]


def _beisen_sub_is_positive_id(sub: str) -> bool:
    """uty=id 时北森要求 BeisenUserID 为正整数；非数字会被当成 0 报错。"""
    s = (sub or "").strip()
    if not s or not s.isdigit():
        return False
    try:
        return int(s) > 0
    except ValueError:
        return False


def _value_from_sso_login_field(acct_row, field: str) -> str:
    f = (field or "account_name").strip()
    if not acct_row:
        return ""
    keys = acct_row.keys() if hasattr(acct_row, "keys") else []
    if f in keys and acct_row[f]:
        return str(acct_row[f]).strip()
    return ""


def _serialize_beisen_sso_account(acct, field: str = "account_uid") -> dict:
    sub = _value_from_sso_login_field(acct, field)
    return {
        "pool_account_id": acct["id"],
        "account_name": acct["account_name"],
        "display_name": acct["display_name"] if "display_name" in acct.keys() else None,
        "account_uid": (acct["account_uid"] or None) if "account_uid" in acct.keys() else None,
        "sub": sub,
        "is_default": bool(acct["is_default"]) if "is_default" in acct.keys() else False,
        "uty_id_ok": _beisen_sub_is_positive_id(sub) if field == "account_uid" else bool(sub),
    }


def _beisen_sso_diagnose(user, account_id=None) -> dict:
    """诊断北森 SSO 不可用原因（不含通讯录兜底）；支持多账号。"""
    db = get_db()
    migrate_schema(db)
    sys_row = db.execute(
        "SELECT id, sso_login_field FROM systems WHERE code = 'beisen' LIMIT 1"
    ).fetchone()
    if not sys_row:
        return {
            "ok": False,
            "error": "未配置北森业务系统",
            "need_bind": True,
        }
    field = (
        sys_row["sso_login_field"]
        if "sso_login_field" in sys_row.keys() and sys_row["sso_login_field"]
        else "account_uid"
    )
    field_label = sso_login_field_label(field, "beisen")
    rows = _bound_system_accounts_for_sso(db, user["id"], "beisen")
    accounts = [_serialize_beisen_sso_account(r, field) for r in rows]

    # 池中有绑定但不可登录
    pool_any = db.execute(
        """SELECT a.id FROM system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.leuc_user_id = ? AND s.code = 'beisen'
        LIMIT 1""",
        (user["id"],),
    ).fetchone()

    if not accounts:
        if pool_any is not None:
            return {
                "ok": False,
                "error": (
                    "北森账号池已关联但尚未开通可登录权限。"
                    "请先完成账号/权限申请，由系统负责人确认开通后再 SSO。"
                ),
                "need_apply": True,
                "sso_login_field": field,
                "sso_login_field_label": field_label,
                "accounts": [],
            }
        return {
            "ok": False,
            "error": (
                "须先申请并开通北森系统账号后才能 SSO 登录。"
                "请走账号申请，由系统负责人在「系统账号管理」绑定账号池中的北森用户ID；"
                "通讯录中的北森ID不能直接用于登录。"
            ),
            "need_bind": True,
            "sso_login_field": field,
            "sso_login_field_label": field_label,
            "accounts": [],
        }

    chosen = None
    if account_id is not None:
        try:
            aid = int(account_id)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "账号选择无效",
                "accounts": accounts,
                "need_choose": True,
                "sso_login_field": field,
                "sso_login_field_label": field_label,
            }
        for r in rows:
            if int(r["id"]) == aid:
                chosen = r
                break
        if not chosen:
            return {
                "ok": False,
                "error": "所选北森账号未绑定或不属于当前用户",
                "accounts": accounts,
                "need_choose": True,
                "sso_login_field": field,
                "sso_login_field_label": field_label,
            }
    elif len(accounts) > 1:
        # 多账号且未指定：要求选择（避免误用演示账号 SYNC-*）
        return {
            "ok": False,
            "error": f"您绑定了 {len(accounts)} 个北森账号，请选择要用哪个登录",
            "need_choose": True,
            "accounts": accounts,
            "sso_login_field": field,
            "sso_login_field_label": field_label,
        }
    else:
        chosen = rows[0]

    sub = _value_from_sso_login_field(chosen, field)
    if not sub:
        return {
            "ok": False,
            "error": (
                f"已绑定北森账号 {chosen['account_name']}，但缺少「{field_label}」。"
                "请在系统账号管理补全该账号池字段后再登录。"
            ),
            "need_sso_field": True,
            "account_name": chosen["account_name"],
            "accounts": accounts,
            "sso_login_field": field,
            "sso_login_field_label": field_label,
        }
    if field == "account_uid" and not _beisen_sub_is_positive_id(sub):
        return {
            "ok": False,
            "error": (
                f"账号 {chosen['account_name']} 的北森用户ID「{sub}」不是正整数，"
                "北森 SSO（uty=id）会报 Argument not positive。"
                "请改选真实北森用户ID，或在账号池修正唯一标识。"
            ),
            "need_sso_field": True,
            "need_choose": len(accounts) > 1,
            "account_name": chosen["account_name"],
            "accounts": accounts,
            "sso_login_field": field,
            "sso_login_field_label": field_label,
        }
    return {
        "ok": True,
        "sub": sub,
        "account_name": chosen["account_name"],
        "pool_account_id": chosen["id"],
        "sso_login_field": field,
        "sso_login_field_label": field_label,
        "source": "system_accounts",
        "accounts": accounts,
    }


def _beisen_resolve_sub(user, data=None, uty: str = "id", account_id=None):
    """解析北森 SSO sub：仅用已开通绑定的账号池字段（不用通讯录 beisen_user_id）。"""
    data = data or {}
    override = (data.get("sub") or request.args.get("sub") or "").strip()
    if override:
        return override
    if account_id is None:
        account_id = data.get("account_id") or data.get("pool_account_id")
        if account_id is None and request:
            account_id = request.args.get("account_id")
    mode = (uty or "id").strip().lower()
    detail = _beisen_sso_diagnose(user, account_id=account_id)
    if not detail.get("ok"):
        return ""
    if mode == "email":
        db = get_db()
        acct = None
        for r in _bound_system_accounts_for_sso(db, user["id"], "beisen"):
            if int(r["id"]) == int(detail["pool_account_id"]):
                acct = r
                break
        return _value_from_sso_login_field(acct, "email")
    if mode in ("jobcode", "job_code"):
        db = get_db()
        acct = None
        for r in _bound_system_accounts_for_sso(db, user["id"], "beisen"):
            if int(r["id"]) == int(detail["pool_account_id"]):
                acct = r
                break
        return _value_from_sso_login_field(acct, "itcode")
    return (detail.get("sub") or "").strip()
# AI-GEN-END


@app.get("/api/beisen/sso/status")
def beisen_sso_status():
    """北森真实 SSO 是否已配置。"""
    return jsonify({"ok": True, **beisen_status_dict()})


@app.get("/api/beisen/sso/accounts")
@login_required
def beisen_sso_accounts(user):
    """当前用户可 SSO 的北森账号列表（多账号时需选择）。"""
    # AI-GEN-BEGIN
    detail = _beisen_sso_diagnose(user, account_id=None)
    accounts = detail.get("accounts") or []
    tip = session.pop("beisen_sso_choose_tip", None) or None
    return jsonify(
        {
            "ok": True,
            "need_choose": bool(detail.get("need_choose")) or len(accounts) > 1,
            "accounts": accounts,
            "sso_login_field": detail.get("sso_login_field"),
            "sso_login_field_label": detail.get("sso_login_field_label"),
            "error": None if accounts else detail.get("error"),
            "tip": tip,
            "need_bind": bool(detail.get("need_bind")),
            "need_apply": bool(detail.get("need_apply")),
        }
    )
    # AI-GEN-END


@app.post("/api/beisen/sso/launch")
@login_required
def beisen_sso_launch(user):
    """已登录用户签发北森 id_token，返回 AuthCenter 跳转 URL。"""
    cfg = beisen_load_config()
    if not cfg or not getattr(cfg, "enabled", False):
        return jsonify(
            {
                "ok": False,
                "error": "北森 SSO 未配置：请在 .env 填写 BEISEN_SSO_*",
                "hint": "复制 .env.example → .env，填写 TENANT_ID / PUBLIC_KEY / PRIVATE_KEY",
            }
        ), 400
    data = request.get_json(silent=True) or {}
    uty = (data.get("uty") or request.args.get("uty") or cfg.uty or "id").strip()
    # AI-GEN-BEGIN
    account_id = data.get("account_id") or data.get("pool_account_id") or request.args.get("account_id")
    detail = _beisen_sso_diagnose(user, account_id=account_id)
    override = (data.get("sub") or request.args.get("sub") or "").strip()
    if not override and detail.get("need_choose") and not account_id:
        return jsonify(
            {
                "ok": False,
                "error": detail.get("error") or "请选择北森账号",
                "need_choose": True,
                "accounts": detail.get("accounts") or [],
                "uty": uty,
            }
        ), 400
    if not override and not detail.get("ok"):
        return jsonify(
            {
                "ok": False,
                "error": detail.get("error") or "缺少登录标识 sub",
                "uty": uty,
                "need_bind": bool(detail.get("need_bind")),
                "need_apply": bool(detail.get("need_apply")),
                "need_sso_field": bool(detail.get("need_sso_field")),
                "need_choose": bool(detail.get("need_choose")),
                "accounts": detail.get("accounts") or [],
            }
        ), 400
    sub = override or detail.get("sub") or ""
    if not sub:
        return jsonify({"ok": False, "error": "缺少登录标识 sub", "uty": uty}), 400
    if (uty or "id").lower() == "id" and not _beisen_sub_is_positive_id(sub):
        return jsonify(
            {
                "ok": False,
                "error": (
                    f"sub「{sub}」不是正整数 BeisenUserID，北森会报 Argument not positive。"
                    "请选择真实北森用户ID账号。"
                ),
                "need_choose": True,
                "accounts": detail.get("accounts") or [],
                "uty": uty,
            }
        ), 400
    # AI-GEN-END
    return_url = data.get("return_url")
    if return_url is None:
        return_url = request.args.get("return_url")
    try:
        out = beisen_launch_url(cfg, sub=sub, uty=uty, return_url=return_url)
    except Exception as e:
        return jsonify({"ok": False, "error": f"签发失败: {e}"}), 500
    return jsonify(
        {
            "ok": True,
            "flow": "beisen_sso_redirect",
            "redirect_url": out["redirect_url"],
            "sub": out["sub"],
            "uty": out["uty"],
            "aud": out["aud"],
            "iss": out["iss"],
            "appid": out["appid"],
            "return_url": out.get("return_url"),
            # AI-GEN-BEGIN
            "sso_source": "override" if override else "system_accounts",
            "bound_account": detail.get("account_name") if detail.get("ok") else None,
            "pool_account_id": detail.get("pool_account_id") if detail.get("ok") else None,
            # AI-GEN-END
            "user": {
                "id": user["id"],
                "username": user.get("username"),
                "display_name": user.get("display_name"),
                "email": user.get("email"),
            },
        }
    )


def _beisen_choose_html(user, accounts, *, return_url, uty, error=None):
    """多北森账号选择页 HTML。"""
    # AI-GEN-BEGIN
    rows = []
    for a in accounts:
        ok = a.get("uty_id_ok")
        badge = (
            '<span style="color:#067647;font-size:12px">可用</span>'
            if ok
            else '<span style="color:#b42318;font-size:12px">ID非法(非正整数)</span>'
        )
        q = urlencode(
            {
                "account_id": a["pool_account_id"],
                "uty": uty or "id",
                "return_url": return_url or "https://www.italent.cn/",
            }
        )
        disabled = "" if ok else "opacity:.55;pointer-events:none"
        rows.append(
            f"""<a href="/beisen/sso/go?{q}" style="display:block;padding:12px 14px;margin:8px 0;
            border:1px solid #d0d5dd;border-radius:10px;text-decoration:none;color:#101828;{disabled}">
            <b>{a.get('display_name') or a.get('account_name')}</b>
            · <code>{a.get('account_name')}</code>
            · 北森用户ID <code>{a.get('account_uid') or '-'}</code>
            {badge}
            </a>"""
        )
    err = f"<p style='color:#b42318'>{error}</p>" if error else ""
    return f"""<!doctype html><meta charset=utf-8>
<title>选择北森账号</title>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:520px;margin:40px auto;padding:0 16px">
<h2>选择北森账号登录</h2>
<p>{user.get('display_name')}（{user.get('username')}）绑定了多个北森账号，请选择本次 SSO 使用哪一个。</p>
{err}
{''.join(rows) or '<p>暂无可用账号</p>'}
<p style="margin-top:20px"><a href="/demo/home">返回业务系统导航</a> · <a href="/">用户中心</a></p>
</body>"""
    # AI-GEN-END


@app.get("/beisen/sso/go")
def beisen_sso_go():
    """浏览器直达：未登录先走 /sso，已登录 302 跳北森；多账号时先选择。"""
    cfg = beisen_load_config()
    if not cfg or not getattr(cfg, "enabled", False):
        return (
            "<!doctype html><meta charset=utf-8><title>北森 SSO</title>"
            "<p>北森 SSO 未配置。请复制 <code>.env.example</code> 为 "
            "<code>.env</code> 并填写 <code>BEISEN_SSO_*</code>。</p>"
            "<p><a href='/demo/home'>返回业务系统导航</a></p>",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    user = current_user()
    if not user:
        session["beisen_sso_pending"] = {
            "sub": (request.args.get("sub") or "").strip() or None,
            "uty": (request.args.get("uty") or "").strip() or None,
            "return_url": request.args.get("return_url"),
            "account_id": (request.args.get("account_id") or "").strip() or None,
        }
        return redirect("/sso?next=beisen_sso")
    pending = session.pop("beisen_sso_pending", None) or {}
    data = {
        "sub": request.args.get("sub") or pending.get("sub"),
        "uty": request.args.get("uty") or pending.get("uty"),
        "return_url": request.args.get("return_url")
        if "return_url" in request.args
        else pending.get("return_url"),
        "account_id": request.args.get("account_id") or pending.get("account_id"),
    }
    uty = (data.get("uty") or cfg.uty or "id").strip()
    # AI-GEN-BEGIN
    account_id = data.get("account_id")
    detail = _beisen_sso_diagnose(user, account_id=account_id)
    ret = data.get("return_url") or cfg.return_url or "https://www.italent.cn/"
    # 多账号 / 非法 ID：回用户中心首页弹窗选择（不再用独立简陋页）
    if detail.get("need_choose") and not account_id:
        session["beisen_sso_choose_tip"] = detail.get("error") or ""
        q = urlencode(
            {
                "beisen_sso_choose": "1",
                "return_url": ret,
                "uty": uty,
            }
        )
        return redirect(f"/?{q}")
    override = (data.get("sub") or "").strip()
    sub = override or (detail.get("sub") if detail.get("ok") else "") or ""
    if not detail.get("ok") and not override:
        tip = detail.get("error") or "缺少登录标识 sub。"
        if detail.get("need_choose") or (detail.get("accounts") or []):
            session["beisen_sso_choose_tip"] = tip
            q = urlencode(
                {
                    "beisen_sso_choose": "1",
                    "return_url": ret,
                    "uty": uty,
                }
            )
            return redirect(f"/?{q}")
        tip += (
            " <a href='/'>回用户中心申请北森账号</a> · "
            "<a href='/demo/home'>返回导航</a>"
        )
        return (
            f"<!doctype html><meta charset=utf-8><title>北森 SSO</title>"
            f"<p>{tip}</p>",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    if (uty or "id").lower() == "id" and not _beisen_sub_is_positive_id(sub):
        session["beisen_sso_choose_tip"] = (
            f"账号标识「{sub}」不是正整数北森用户ID，无法 SSO。"
            "请选择真实北森用户ID账号（如 630702408）。"
        )
        q = urlencode(
            {
                "beisen_sso_choose": "1",
                "return_url": ret,
                "uty": uty,
            }
        )
        return redirect(f"/?{q}")
    # AI-GEN-END
    try:
        out = beisen_launch_url(
            cfg, sub=sub, uty=uty, return_url=data.get("return_url")
        )
    except Exception as e:
        return (
            f"<!doctype html><meta charset=utf-8><title>北森 SSO</title>"
            f"<p>签发失败：{e}</p>",
            500,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    return redirect(out["redirect_url"])


# AI-GEN-END


@app.get("/demo/home")
def demo_home():
    return send_from_directory(STATIC, "home.html")


@app.get("/demo/home/callback")
def demo_home_callback():
    return send_from_directory(STATIC, "home.html")


@app.get("/demo/oa")
def demo_oa():
    """旧独立 OA 页已废弃，改到用户中心「北森消息」。"""
    # AI-GEN-BEGIN
    return redirect("/?view=oa_forms")
    # AI-GEN-END


@app.get("/demo/erp")
@app.get("/demo/erp/callback")
def demo_erp_legacy():
    """兼容旧地址，跳转到 /demo/home。"""
    q = request.query_string.decode() if request.query_string else ""
    target = "/demo/home/callback" if request.path.endswith("/callback") else "/demo/home"
    if q:
        target = f"{target}?{q}"
    return redirect(target)


@app.get("/sso")
def sso_page():
    """业务系统跳转到 LEUC 后的统一登录/选账号页。"""
    return send_from_directory(STATIC, "sso.html")


@app.get("/oauth/authorize")
def oauth_authorize():
    client_id = request.args.get("client_id", "")
    redirect_uri = request.args.get("redirect_uri", "")
    response_type = request.args.get("response_type", "")
    scope = request.args.get("scope", "openid profile")
    state = request.args.get("state", "")
    nonce = request.args.get("nonce", "")
    code_challenge = request.args.get("code_challenge")
    code_challenge_method = request.args.get("code_challenge_method", "S256")

    db = get_db()
    client = db.execute("SELECT * FROM systems WHERE client_id = ?", (client_id,)).fetchone()
    if not client:
        return jsonify({"error": "invalid_client", "error_description": "未知 client_id"}), 400
    if client["status"] != "enabled":
        return jsonify({"error": "unauthorized_client", "error_description": "客户端已禁用"}), 400
    if response_type != "code":
        return jsonify({"error": "unsupported_response_type"}), 400
    if not _redirect_uri_allowed(client, redirect_uri):
        return jsonify({"error": "invalid_request", "error_description": "redirect_uri 未登记"}), 400
    if client["require_pkce"] and not code_challenge:
        return jsonify({"error": "invalid_request", "error_description": "要求 PKCE (code_challenge)"}), 400

    session["oidc"] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "system_code": client["code"],
        "system_name": client["name"],
    }
    q = urlencode({"client_id": client_id, "app": client["code"]})
    return (
        f'<!doctype html><meta charset="utf-8"><title>跳转 LEUC</title>'
        f"<p>正在跳转到 Lecoo 用户中心…</p>"
        f'<script>location.href="/sso?{q}"</script>',
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )


@app.get("/api/oidc/session")
def oidc_session():
    oidc = session.get("oidc") or {}
    if not oidc.get("client_id"):
        return jsonify({"ok": False, "error": "无进行中的业务系统授权，请从 ERP 重新点击 LEUC 登录"}), 400
    return jsonify(
        {
            "ok": True,
            "oidc": {
                "client_id": oidc.get("client_id"),
                "system_code": oidc.get("system_code"),
                "system_name": oidc.get("system_name"),
                "redirect_uri": oidc.get("redirect_uri"),
            },
            "logged_in": bool(session.get("user_id")),
            "user": current_user(),
        }
    )


@app.post("/api/oidc/continue")
def oidc_continue():
    """已登录用户继续 OIDC：选账号或直接回跳。"""
    user = current_user()
    if not user:
        # 兼容：密码已过、待选业务账号阶段仅有 pending_user_id
        pending = session.get("pending_user_id")
        if pending:
            row = get_db().execute("SELECT * FROM users WHERE id = ?", (pending,)).fetchone()
            user = row_user(row)
    if not user:
        return jsonify({"ok": False, "error": "未登录", "flow": "need_login"}), 401
    data = request.get_json(silent=True) or {}
    return _finish_oidc_after_login(user, data.get("account_id"))


@app.post("/api/oidc/start")
def oidc_start():
    """前端演示：模拟业务系统发起授权（含 PKCE）。"""
    data = request.get_json(force=True) or {}
    client_id = data.get("client_id")
    db = get_db()
    client = db.execute("SELECT * FROM systems WHERE client_id = ?", (client_id,)).fetchone()
    if not client:
        return jsonify({"ok": False, "error": "未知客户端"}), 404
    if client["status"] != "enabled":
        return jsonify({"ok": False, "error": "客户端已禁用，无法发起联合登录"}), 400
    redirect_uri = _parse_redirect_uris(client["redirect_uris"])[0]
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    state = secrets.token_urlsafe(12)
    nonce = secrets.token_urlsafe(12)
    session["oidc"] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": client["scopes"],
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "system_code": client["code"],
        "system_name": client["name"],
    }
    session["pkce_verifier"] = verifier
    authorize_url = "/oauth/authorize?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": client["scopes"],
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return jsonify(
        {
            "ok": True,
            "system": {"code": client["code"], "name": client["name"], "client_id": client_id},
            "pkce_verifier": verifier,
            "authorize_url": authorize_url,
            "message": "已按 OIDC Authorization Code + PKCE 发起",
        }
    )


@app.post("/oauth/token")
def oauth_token():
    # 支持 form 与 JSON
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    grant_type = data.get("grant_type")
    code = data.get("code")
    redirect_uri = data.get("redirect_uri")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    code_verifier = data.get("code_verifier")

    # Basic auth
    auth = request.authorization
    if auth and not client_id:
        client_id, client_secret = auth.username, auth.password

    if grant_type != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    db = get_db()
    client = db.execute("SELECT * FROM systems WHERE client_id = ?", (client_id,)).fetchone()
    if not client or client["client_secret"] != client_secret:
        return jsonify({"error": "invalid_client"}), 401
    if client["status"] != "enabled":
        return jsonify({"error": "unauthorized_client"}), 400

    row = db.execute("SELECT * FROM oauth_codes WHERE code = ?", (code,)).fetchone()
    if not row or row["used"]:
        return jsonify({"error": "invalid_grant", "error_description": "code 无效或已使用"}), 400
    if row["client_id"] != client_id:
        return jsonify({"error": "invalid_grant"}), 400
    if row["redirect_uri"] != redirect_uri and not _redirect_uri_allowed(
        client, redirect_uri or ""
    ):
        # 严格：须与授权时一致
        if row["redirect_uri"] != redirect_uri:
            return jsonify({"error": "invalid_grant", "error_description": "redirect_uri 不匹配"}), 400
    if row["expires_at"] < datetime.now().isoformat(timespec="seconds"):
        return jsonify({"error": "invalid_grant", "error_description": "code 已过期"}), 400

    if row["code_challenge"]:
        method = (row["code_challenge_method"] or "S256").upper()
        if not code_verifier:
            return jsonify({"error": "invalid_grant", "error_description": "缺少 code_verifier"}), 400
        if method == "S256":
            calc = (
                base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if calc != row["code_challenge"]:
                return jsonify({"error": "invalid_grant", "error_description": "PKCE 校验失败"}), 400
        elif code_verifier != row["code_challenge"]:
            return jsonify({"error": "invalid_grant", "error_description": "PKCE 校验失败"}), 400

    db.execute("UPDATE oauth_codes SET used = 1 WHERE code = ?", (code,))
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO oauth_tokens
        (access_token, refresh_token, client_id, user_id, account_id, scope, expires_at)
        VALUES (?,?,?,?,?,?,?)""",
        (access, refresh, client_id, row["user_id"], row["account_id"], row["scope"], exp),
    )
    db.commit()

    user = db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    acct = db.execute(
        "SELECT * FROM user_system_accounts WHERE id = ?", (row["account_id"],)
    ).fetchone()
    # 简化 id_token（演示 HS256 风格 payload，非完整 JWT 签名校验）
    now = int(datetime.now().timestamp())
    id_payload = {
        "iss": _issuer(),
        "sub": str(user["id"]),
        "aud": client_id,
        "exp": now + 3600,
        "iat": now,
        "nonce": row["nonce"],
        "name": user["display_name"],
        "preferred_username": user["username"],
        "email": user["email"],
        "account_name": acct["account_name"] if acct else None,
    }
    id_token = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(json.dumps(id_payload).encode()).rstrip(b"=").decode()
        + ".demo"
    )
    return jsonify(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh,
            "scope": row["scope"],
            "id_token": id_token,
        }
    )


@app.get("/oauth/userinfo")
def oauth_userinfo():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "invalid_token"}), 401
    token = auth[7:]
    db = get_db()
    row = db.execute("SELECT * FROM oauth_tokens WHERE access_token = ?", (token,)).fetchone()
    if not row or row["expires_at"] < datetime.now().isoformat(timespec="seconds"):
        return jsonify({"error": "invalid_token"}), 401
    user = db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    acct = db.execute(
        "SELECT * FROM user_system_accounts WHERE id = ?", (row["account_id"],)
    ).fetchone()
    return jsonify(
        {
            "sub": str(user["id"]),
            "name": user["display_name"],
            "preferred_username": user["username"],
            "email": user["email"],
            "phone_number": user["phone"],
            "account_name": acct["account_name"] if acct else None,
            "account_label": acct["account_label"] if acct else None,
            "perm_summary": (acct["perm_summary"] if acct and "perm_summary" in acct.keys() else None),
            "client_id": row["client_id"],
        }
    )


@app.get("/oidc/callback")
def oidc_callback_page():
    """演示业务系统回调页：展示 code 并用 secret 换 token。"""
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    app_code = request.args.get("app", "")
    return send_from_directory(STATIC, "callback.html")


@app.get("/api/risk")
def api_risk():
    username = request.args.get("username", "")
    return jsonify({"ok": True, "risk": get_risk(username)})


@app.post("/api/risk/reset")
def risk_reset():
    data = request.get_json(force=True) or {}
    set_risk(data.get("username") or "", 0)
    return jsonify({"ok": True})


@app.post("/api/risk/set")
def risk_set():
    """演示：直接把失败次数设为指定值。"""
    data = request.get_json(force=True) or {}
    set_risk(data.get("username") or "", int(data.get("fail_count") or 0))
    return jsonify({"ok": True, "risk": get_risk(data.get("username") or "")})


# AI-GEN-BEGIN
def _run_scheduled_leorg_sync(
    conn, change_sink: list | None = None, *, sync_run_id: int | None = None
) -> tuple[str, dict]:
    """定时/手动同步：拉取并应用；change_sink 收集明细变化。"""
    from leorg_client import LeorgClient, load_config, status_dict

    st = status_dict()
    if not st.get("enabled"):
        return "LeOrg 未配置，跳过", {"skipped": True}
    client = LeorgClient(load_config())
    migrate_schema(conn)
    state = _leorg_sync_state(conn) or {}
    mode = "full" if not state.get("last_full_at") else "incr"
    orgs = [o for o in (client.list_organizations(status=1) or []) if isinstance(o, dict)]
    org_stats = _sync_leorg_organizations(conn, orgs, change_sink=change_sink) or {}
    if mode == "full":
        emps = (client.list_employees(emp_status=1) or []) + (
            client.list_employees(emp_status=2) or []
        )
        max_cid = client.latest_change_id(days=1)
    else:
        after = int(state.get("last_change_id") or 0)
        changes = client.list_employee_changes(days=7, after_id=after) or []
        emps = []
        max_cid = after
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            max_cid = max(max_cid, int(ch.get("id") or 0))
            # AI-GEN-BEGIN
            eid = (
                ch.get("emp_id")
                or ch.get("employee_id")
                or ch.get("entity_id")
            )
            # AI-GEN-END
            if eid is None:
                continue
            detail = client.get_employee(int(eid))
            if detail:
                emps.append(detail)
    emps = [e for e in emps if isinstance(e, dict)]
    emp_stats = (
        _sync_leorg_employees(
            conn, emps, change_sink=change_sink, sync_run_id=sync_run_id
        )
        or {}
    )
    _resolve_dept_owners_from_leorg(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_leorg_sync_state(
        conn,
        mode=mode,
        last_change_id=int(max_cid or 0),
        org_mapped=int(org_stats.get("inserted", 0) + org_stats.get("updated", 0)),
        emp_touched=int(
            emp_stats.get("users_updated", 0)
            + emp_stats.get("roster_added", 0)
            + emp_stats.get("closed", 0)
        ),
        now=now,
        is_full=(mode == "full"),
    )
    summary = {
        "mode": mode,
        "org": org_stats,
        "emp": emp_stats,
        "change_count": len(change_sink or []),
    }
    return f"{mode} ok org={org_stats} emp={emp_stats}", summary


def _execute_leorg_sync_job(
    conn,
    *,
    trigger_type: str = "schedule",
    actor_user_id: int | None = None,
    actor_name: str | None = None,
    ip: str | None = None,
) -> dict:
    """执行同步并写入任务执行记录 / 变化明细 / 审计日志。"""
    ensure_ops_tables = __import__("leuc_ops", fromlist=["ensure_ops_tables"]).ensure_ops_tables
    ensure_ops_tables(conn)
    run_id = begin_task_run(
        conn,
        task_code="leorg_sync",
        trigger_type=trigger_type,
        actor_user_id=actor_user_id,
    )
    change_sink: list = []
    try:
        msg, summary = _run_scheduled_leorg_sync(
            conn, change_sink=change_sink, sync_run_id=run_id
        )
        status = "ok" if not summary.get("skipped") else "skipped"
        append_sync_change_logs(conn, run_id, change_sink)
        finish_task_run(conn, run_id, status=status, message=msg, summary=summary)
        write_audit_log(
            conn,
            action="task.run",
            actor_user_id=actor_user_id,
            actor_name=actor_name,
            target_type="scheduled_task",
            target_id="leorg_sync",
            detail={
                "run_id": run_id,
                "trigger": trigger_type,
                "status": status,
                "summary": summary,
                "message": msg,
            },
            ip=ip,
        )
        conn.commit()
        return {
            "ok": status in ("ok", "skipped"),
            "status": status,
            "message": msg,
            "run_id": run_id,
            "summary": summary,
            "change_count": len(change_sink),
        }
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        # 回滚后重建执行失败记录（新事务）
        try:
            ensure_ops_tables(conn)
            run_id2 = begin_task_run(
                conn,
                task_code="leorg_sync",
                trigger_type=trigger_type,
                actor_user_id=actor_user_id,
            )
            err = f"{type(e).__name__}: {e}"
            finish_task_run(conn, run_id2, status="error", message=err, summary={"error": err})
            write_audit_log(
                conn,
                action="task.run",
                actor_user_id=actor_user_id,
                actor_name=actor_name,
                target_type="scheduled_task",
                target_id="leorg_sync",
                detail={"run_id": run_id2, "trigger": trigger_type, "status": "error", "error": err},
                ip=ip,
            )
            conn.commit()
            run_id = run_id2
            msg = err
        except Exception:
            msg = f"{type(e).__name__}: {e}"
            run_id = None
        import sys
        import traceback

        traceback.print_exc(file=sys.stderr)
        return {
            "ok": False,
            "status": "error",
            "message": msg,
            "run_id": run_id,
            "summary": None,
            "change_count": 0,
        }


@app.get("/api/admin/tasks")
@login_required
def admin_tasks_list(user):
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    return jsonify({"ok": True, "tasks": list_scheduled_tasks(db)})


@app.patch("/api/admin/tasks/<code>")
@login_required
def admin_tasks_update(user, code):
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    db = get_db()
    migrate_schema(db)
    row = update_scheduled_task(
        db,
        code,
        interval_hours=data.get("interval_hours"),
        enabled=data.get("enabled"),
    )
    if not row:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    # AI-GEN-BEGIN
    write_audit_log(
        db,
        action="task.update",
        actor_user_id=user["id"],
        actor_name=user.get("display_name") or user.get("username"),
        target_type="scheduled_task",
        target_id=code,
        detail={
            "interval_hours": data.get("interval_hours"),
            "enabled": data.get("enabled"),
        },
        ip=request.headers.get("X-Forwarded-For") or request.remote_addr,
    )
    # AI-GEN-END
    db.commit()
    return jsonify({"ok": True, "task": row})


@app.post("/api/admin/tasks/<code>/run")
@login_required
def admin_tasks_run(user, code):
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    if code != "leorg_sync":
        return jsonify({"ok": False, "error": "暂不支持该任务手动执行"}), 400
    db = get_db()
    migrate_schema(db)
    # AI-GEN-BEGIN
    result = _execute_leorg_sync_job(
        db,
        trigger_type="manual",
        actor_user_id=user["id"],
        actor_name=user.get("display_name") or user.get("username"),
        ip=request.headers.get("X-Forwarded-For") or request.remote_addr,
    )
    status = result.get("status") or "error"
    msg = result.get("message") or ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    task = db.execute("SELECT * FROM scheduled_tasks WHERE code = ?", (code,)).fetchone()
    iv = float(task["interval_hours"] or 6) if task else 6
    next_at = (datetime.now() + timedelta(hours=iv)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """UPDATE scheduled_tasks
        SET last_run_at=?, next_run_at=?, last_status=?, last_message=?, updated_at=?
        WHERE code=?""",
        (now, next_at, status, (msg or "")[:500], now, code),
    )
    db.commit()
    return jsonify(
        {
            "ok": bool(result.get("ok")),
            "message": msg,
            "status": status,
            "run_id": result.get("run_id"),
            "summary": result.get("summary"),
            "change_count": result.get("change_count") or 0,
        }
    )
    # AI-GEN-END


@app.get("/api/admin/tasks/<code>/runs")
@login_required
def admin_task_runs(user, code):
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    limit = int(request.args.get("limit") or 50)
    return jsonify({"ok": True, "runs": list_task_runs(db, code, limit=limit)})
    # AI-GEN-END


@app.get("/api/admin/task-runs/<int:run_id>/changes")
@login_required
def admin_task_run_changes(user, run_id):
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    limit = int(request.args.get("limit") or 200)
    offset = int(request.args.get("offset") or 0)
    entity_type = request.args.get("entity_type") or None
    items, total = list_sync_changes(
        db, run_id, limit=limit, offset=offset, entity_type=entity_type
    )
    run = db.execute("SELECT * FROM task_run_logs WHERE id = ?", (run_id,)).fetchone()
    return jsonify(
        {
            "ok": True,
            "run": dict(run) if run else None,
            "changes": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )
    # AI-GEN-END


@app.get("/api/admin/audit-logs")
@login_required
def admin_audit_logs(user):
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    limit = int(request.args.get("limit") or 100)
    action = request.args.get("action") or None
    return jsonify({"ok": True, "logs": list_audit_logs(db, limit=limit, action=action)})
    # AI-GEN-END


@app.get("/api/admin/leave-closes")
@login_required
def admin_leave_closes(user):
    """离职关账记录列表（独立列表）。"""
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    from leuc_ops import ensure_ops_tables

    ensure_ops_tables(db)
    q = request.args.get("q") or None
    limit = int(request.args.get("limit") or 100)
    offset = int(request.args.get("offset") or 0)
    rows, total = list_leave_close_records(db, q=q, limit=limit, offset=offset)
    return jsonify({"ok": True, "records": rows, "total": total})
    # AI-GEN-END


@app.get("/api/admin/leave-closes/<int:rid>")
@login_required
def admin_leave_close_detail(user, rid):
    """离职关账详情（含各子系统本地/远程状态）。"""
    # AI-GEN-BEGIN
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    from leuc_ops import ensure_ops_tables

    ensure_ops_tables(db)
    rec = get_leave_close_record(db, rid)
    if not rec:
        return jsonify({"ok": False, "error": "记录不存在"}), 404
    return jsonify({"ok": True, "record": rec})
    # AI-GEN-END


@app.post("/api/internal/subsystem-account-close")
def internal_subsystem_account_close():
    """子系统关闭回调（原型/联调）：落库 subsystem_close_inbox，模拟子系统侧记录。"""
    # AI-GEN-BEGIN
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    from leuc_ops import ensure_ops_tables

    ensure_ops_tables(db)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """INSERT INTO subsystem_close_inbox
        (system_id, system_code, account_name, account_uid, leuc_user_id,
         reason, payload_json, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            data.get("system_id"),
            data.get("system_code"),
            data.get("account_name"),
            data.get("account_uid"),
            data.get("leuc_user_id"),
            data.get("reason") or "account.close",
            json.dumps(data, ensure_ascii=False, default=str),
            now,
        ),
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": "子系统已接收关闭指令并落库",
            "received_at": now,
        }
    )
    # AI-GEN-END


@app.get("/api/admin/notify-records")
@login_required
def admin_notify_records(user):
    if not user_has_role(user, "super_admin", "hr_specialist"):
        return jsonify({"ok": False, "error": "无权限"}), 403
    db = get_db()
    migrate_schema(db)
    rows = db.execute(
        """SELECT r.*, u.display_name, u.username
        FROM notify_send_records r
        LEFT JOIN users u ON u.id = r.user_id
        ORDER BY r.id DESC LIMIT 200"""
    ).fetchall()
    return jsonify({"ok": True, "records": [dict(r) for r in rows]})


# AI-GEN-END


@app.post("/api/switch-role")
def switch_role():
    data = request.get_json(force=True) or {}
    username = data.get("username")
    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    # AI-GEN-BEGIN
    if user_is_closed(row):
        return jsonify({"ok": False, "error": "账号已关闭，无法切换登录"}), 403
    # AI-GEN-END
    session.clear()
    session["user_id"] = row["id"]
    session["login_source"] = "leuc"
    return jsonify({"ok": True, "user": row_user(row)})


def main():
    # AI-GEN-BEGIN
    # 默认不强制重建，避免每次启动清空 LeOrg 同步数据；需要重建时：
    #   python -c "from db import init_db; init_db(force=True)"
    init_db(force=False)
    start_task_scheduler(app, _execute_leorg_sync_job)
    # 0.0.0.0：本机 + 同网手机可访问；回调按请求 Host 动态生成
    host = _os.environ.get("LEUC_HOST", "0.0.0.0")
    port = int(_os.environ.get("LEUC_PORT", "5055"))
    print(f"LEUC 原型: http://127.0.0.1:{port}  （监听 {host}:{port}，手机用电脑局域网 IP）")
    print("管理账号: admin / sunli / zhangcai / zhaomin / liufang / huangwei  密码 123456")
    # debug 热重载易把后台进程弄挂（Connection refused）；本地联调默认关 reloader
    app.run(host=host, port=port, debug=True, use_reloader=False, threaded=True)
    # AI-GEN-END


if __name__ == "__main__":
    main()
# AI-GEN-END
