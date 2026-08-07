# AI-GEN-BEGIN
"""Lecoo 用户中心 LEUC · SQLite 多角色交互原型服务。"""
from __future__ import annotations

import csv
import io
import base64
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, session

from db import (
    ALL_BUTTONS,
    ALL_CAPS,
    ALL_MENUS,
    BUILTIN_ROLE_CODES,
    DEFAULT_ROLE_CAPS,
    DEFAULT_ROLE_MENUS,
    ROLE_LABELS,
    ROLE_MENUS,
    alloc_username,
    connect,
    ensure_roles_seeded,
    ensure_username_available,
    init_db,
    load_role_labels,
    migrate_schema,
    name_to_pinyin,
    normalize_username,
    preview_unique_usernames,
    role_label_of,
)

# AI-GEN-BEGIN
try:
    from beisen_sso import launch_url as beisen_launch_url
    from beisen_sso import load_config as beisen_load_config
    from beisen_sso import status_dict as beisen_status_dict
except Exception:  # 缺 cryptography 时降级，不影响主流程
    beisen_launch_url = None
    beisen_load_config = lambda: None
    beisen_status_dict = lambda: {
        "ok": False,
        "enabled": False,
        "error": "beisen_sso 不可用（缺 cryptography）",
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
# 系统超管：不进「我的组织」通讯录
SYSTEM_ADMIN_USERNAME = "admin"


def is_hidden_from_org(row_or_user) -> bool:
    """系统超管等不在组织人员列表展示。"""
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
    """确保存在 admin 超管（全权限），且不挂在组织树上。"""
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


def ensure_db():
    """无种子或旧结构时强制重建（演示库）。"""
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
        # 不再强制要求 ≥500 人种子：清空组织后由 LeOrg 同步回填
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
        # 仅在关键 schema / 演示系统缺失时重建；不清空组织后因人数变少而重种通讯录
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
        # 组织可由「清空 + LeOrg 同步」托管；有根部门或已映射 LeOrg 即视为有效
        has_org_ok = bool(has_org_seed) or conn.execute(
            "SELECT 1 FROM departments WHERE leorg_id IS NOT NULL LIMIT 1"
        ).fetchone() or conn.execute(
            "SELECT 1 FROM departments LIMIT 1"
        ).fetchone()
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
        if maning:
            conn.execute(
                "UPDATE users SET role = 'dept_owner' WHERE id = ?", (maning["id"],)
            )
        if wujinzhi:
            conn.execute(
                "UPDATE users SET role = 'dept_owner' WHERE id = ?", (wujinzhi["id"],)
            )
        if xu:
            conn.execute(
                "UPDATE users SET role = 'employee' WHERE id = ?", (xu["id"],)
            )
        if gaojia:
            conn.execute(
                "UPDATE users SET role = 'system_owner' WHERE id = ?", (gaojia["id"],)
            )
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
            conn.execute(
                "UPDATE users SET role = 'finance' WHERE id = ?", (chang["id"],)
            )
            conn.execute(
                """UPDATE approval_chain_steps SET assignee_user_id = ?
                WHERE step_key = 'finance' AND flow_code IN ('sensitive','external')""",
                (chang["id"],),
            )
        # 按钮：设置部门负责人（已有库补种）
        for role in ("super_admin", "hr_specialist", "dept_owner"):
            conn.execute(
                """INSERT OR IGNORE INTO role_caps (role, cap_id)
                VALUES (?, 'org_set_owner')""",
                (role,),
            )
        # AI-GEN-END
        ensure_system_admin(conn)
        conn.commit()
        # AI-GEN-END
    except Exception as exc:
        # 软迁移失败不再整库重种（避免「清空组织」后被通讯录种子覆盖）
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


ensure_db()  # 启动/热更新：多级组织 + 准入模式


def get_db():
    if "db" not in g:
        ensure_db()
        g.db = connect()
    return g.db


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
    if db is not None:
        try:
            menus = [
                r["menu_id"]
                for r in db.execute(
                    "SELECT menu_id FROM role_menus WHERE role = ? ORDER BY menu_id",
                    (role,),
                ).fetchall()
            ]
            caps = [
                r["cap_id"]
                for r in db.execute(
                    "SELECT cap_id FROM role_caps WHERE role = ? ORDER BY cap_id",
                    (role,),
                ).fetchall()
            ]
        except Exception:
            menus, caps = [], []
    if not menus:
        menus = list(DEFAULT_ROLE_MENUS.get(role, ROLE_MENUS.get(role, [])))
    if not caps:
        caps = list(DEFAULT_ROLE_CAPS.get(role, []))
    # 系统超管：始终全菜单 + 全按钮
    if (row["username"] or "") == SYSTEM_ADMIN_USERNAME or role == "super_admin":
        menus = [m["id"] for m in ALL_MENUS]
        caps = [b["id"] for b in ALL_BUTTONS]
    if ("can_proxy_apply" in keys and row["can_proxy_apply"]) and "proxy_apply" not in caps:
        caps.append("proxy_apply")
    if (
        "can_set_account_expire" in keys and row["can_set_account_expire"]
    ) and "set_account_expire" not in caps:
        caps.append("set_account_expire")
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "role_label": role_label_of(db, row["role"]) if db is not None else ROLE_LABELS.get(row["role"], row["role"]),
        "dept_id": row["dept_id"],
        "phone": row["phone"],
        "email": row["email"],
        "itcode": row["itcode"] if "itcode" in keys else row["username"],
        "beisen_user_id": (
            (row["beisen_user_id"] or None) if "beisen_user_id" in keys else None
        ),
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
    }
    # AI-GEN-END


def user_has_cap(user, cap_id: str) -> bool:
    # AI-GEN-BEGIN
    if not user:
        return False
    return cap_id in (user.get("caps") or [])
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
    """是否具备任一组织的管理权。"""
    if user_has_cap(user, "manage_all_org") or user["role"] in ("super_admin", "hr_specialist"):
        return True
    db = get_db()
    return bool(managed_dept_ids(db, user))


def require_hr_manage(user):
    return user_has_cap(user, "manage_all_org") or user["role"] in ("hr_specialist", "super_admin")


def all_departments(db):
    rows = db.execute("SELECT * FROM departments ORDER BY id").fetchall()
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
    roots = [d for d in by_id.values() if not d.get("parent_id") or d["parent_id"] not in by_id]
    return roots


def managed_dept_ids(db, user):
    """用户作为负责人/额外负责人所管组织及其全部下级。"""
    if user_has_cap(user, "manage_all_org") or user["role"] in ("super_admin", "hr_specialist"):
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
    if user_has_cap(user, "manage_all_org") or user["role"] == "super_admin":
        return True
    return int(dept_id) in managed_dept_ids(get_db(), user)


def can_manage_member(user, target_row):
    if user_has_cap(user, "manage_all_org") or user["role"] == "super_admin":
        return True
    if not target_row or not target_row["dept_id"]:
        return False
    return can_manage_dept(user, target_row["dept_id"])


def can_apply_for_user(user, target_row):
    """账号申请：全员可为本人；具备代人能力可为他人；组织负责人可管下级。"""
    # AI-GEN-BEGIN
    if not target_row:
        return False
    if int(user["id"]) == int(target_row["id"]):
        return True
    if user_has_cap(user, "proxy_apply") or user["role"] in ("hr_specialist", "super_admin"):
        return True
    if user.get("can_proxy_apply"):
        return True
    if user["role"] == "dept_owner":
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


def default_account_expire(days: int = 90) -> str:
    """新建账号默认有效期：今天 + N 天。"""
    # AI-GEN-BEGIN
    return (datetime.now() + timedelta(days=int(days))).strftime("%Y-%m-%d")
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
    """一级领导：直属之上一级组织负责人（同人则继续上溯）。"""
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
def append_system_owner_step(db, system_id, steps):
    """在审批链末尾追加系统负责人开通（跳过已在链中的人）。"""
    steps = list(steps or [])
    used = {s[2] for s in steps if s and s[2]}
    owners = list_system_owner_ids(db, system_id) if system_id else []
    if not owners:
        row = db.execute(
            "SELECT owner_user_id FROM systems WHERE id = ?", (system_id,)
        ).fetchone() if system_id else None
        if row and row["owner_user_id"]:
            owners = [row["owner_user_id"]]
    for oid in owners:
        if oid and oid not in used:
            steps.append(("system_owner", "系统负责人开通", oid))
            break
    return steps


def provision_account_apply(
    db, application, with_sensitive=False, account_name=None, remark=None
):
    """系统负责人开通：录入业务账号名并关联申请人，可选敏感与备注。"""
    # AI-GEN-BEGIN
    sid = application["system_id"]
    uid = application["applicant_id"]
    if not sid or not uid:
        return {"ok": False, "error": "缺少系统或申请人"}
    sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not sys_row or not user:
        return {"ok": False, "error": "系统或用户不存在"}
    acct_name = (account_name or "").strip()
    if not acct_name:
        acct_name = f"{user['username']}_{sys_row['code']}"
    note = (remark or "").strip()
    summary = note or ("敏感权限" if with_sensitive else "普通开通")
    label = note or "账号申请开通"
    now = datetime.now().strftime("%Y-%m-%d")

    # 同系统账号名占用校验（已绑其他人则拒绝）
    pool = db.execute(
        """SELECT * FROM system_accounts
        WHERE system_id = ? AND account_name = ? LIMIT 1""",
        (sid, acct_name),
    ).fetchone()
    if pool and pool["leuc_user_id"] and int(pool["leuc_user_id"]) != int(uid):
        other = db.execute(
            "SELECT display_name, username FROM users WHERE id = ?",
            (pool["leuc_user_id"],),
        ).fetchone()
        who = f"{other['display_name']}({other['username']})" if other else str(pool["leuc_user_id"])
        return {"ok": False, "error": f"账号名已被占用：{acct_name} → {who}"}

    exists = db.execute(
        """SELECT * FROM user_system_accounts
        WHERE user_id = ? AND system_id = ? AND account_name = ? LIMIT 1""",
        (uid, sid, acct_name),
    ).fetchone()
    if not exists:
        # 同系统已有任意账号时仍允许再开一条（按账号名）
        exists = None

    if exists:
        hs = 1 if with_sensitive else int(exists["has_sensitive"] or 0)
        db.execute(
            """UPDATE user_system_accounts
            SET can_login = 1, has_sensitive = ?, perm_summary = ?, account_label = ?
            WHERE id = ?""",
            (hs, summary, label, exists["id"]),
        )
        account_id = exists["id"]
        if pool:
            db.execute(
                """UPDATE system_accounts SET leuc_user_id = ?, status = 'bound',
                display_name = COALESCE(display_name, ?) WHERE id = ?""",
                (uid, user["display_name"], pool["id"]),
            )
            pool_id = pool["id"]
        else:
            pool_id = None
    else:
        if not pool:
            cur = db.execute(
                """INSERT INTO system_accounts
                (system_id, account_name, display_name, phone, email, itcode, status, leuc_user_id, source, created_at)
                VALUES (?,?,?,?,?,?, 'bound', ?, 'apply', ?)""",
                (
                    sid,
                    acct_name,
                    user["display_name"],
                    user["phone"],
                    user["email"],
                    user["itcode"] if "itcode" in user.keys() else user["username"],
                    uid,
                    now,
                ),
            )
            pool_id = cur.lastrowid
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

    db.execute(
        "UPDATE applications SET status = 'provisioned', provisioned = 1, updated_at = ? WHERE id = ?",
        (now, application["id"]),
    )
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


def serialize_todo(db, row):
    """待办序列化：附带排查用 ID 与开通表单标记。"""
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
    if app_id:
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if app_row:
            flow_code = app_row["flow_code"]
            d["flow_code"] = flow_code
            d["applicant_id"] = app_row["applicant_id"]
            d["system_id"] = app_row["system_id"]
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
    d["applicant"] = applicant
    d["system"] = system
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
    # 建议账号名
    if applicant and system:
        d["suggest_account"] = f"{applicant['username']}_{system['code']}"
    else:
        d["suggest_account"] = ""
    return d
    # AI-GEN-END


def start_multi_step_apply(
    db, *, flow_code, todo_type, title, init_title, subject_id, initiator_id,
    system_id, steps, meta_extra=None, perm_id=None,
):
    """创建多级审批申请单，返回 (app_id, first_todo, first_assignee, step_preview)。"""
    now = datetime.now().strftime("%Y-%m-%d")
    meta_extra = meta_extra or {}
    if not steps:
        return None, None, None, []
    cur = db.execute(
        """INSERT INTO applications
        (flow_code, applicant_id, perm_def_id, system_id, title, status,
         current_step, total_steps, created_at, updated_at, provisioned)
        VALUES (?,?,?,?,?, 'pending', 1, ?, ?, ?, 0)""",
        (flow_code, subject_id, perm_id, system_id, title, len(steps), now, now),
    )
    app_id = cur.lastrowid
    first_assignee = None
    first_todo = None
    step_preview = []
    for i, (step_key, step_label, assignee) in enumerate(steps, start=1):
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
            }
        )
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
        (datetime.now().strftime("%Y-%m-%d"), application["id"]),
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
                datetime.now().strftime("%Y-%m-%d"),
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
        (datetime.now().strftime("%Y-%m-%d"), application["id"]),
    )
    db.execute(
        """UPDATE todos SET status = 'approved', title = ?
        WHERE application_id = ? AND bucket = 'initiated'""",
        (f"敏感权限关闭 · {row['system_name']}（已关闭）", application["id"]),
    )
    return {"ok": True, "account_id": aid, "system": row["system_name"]}


def close_user_system_account(db, user_id, account_id):
    """关闭指定系统账号登录。"""
    row = db.execute(
        """SELECT a.*, s.name AS system_name FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id WHERE a.id = ?""",
        (account_id,),
    ).fetchone()
    if not row or int(row["user_id"]) != int(user_id):
        return {"ok": False, "error": "账号不存在或不属于该用户"}
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
    }
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
def _can_config_roles(user) -> bool:
    return user_has_cap(user, "config_roles") or user["role"] == "super_admin"


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
    """角色列表 + 菜单/能力配置（超管或 config_roles）。"""
    if not _can_config_roles(user):
        return jsonify({"ok": False, "error": "无权配置角色"}), 403
    db = get_db()
    ensure_roles_seeded(db)
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
        n = db.execute("SELECT COUNT(*) AS c FROM users WHERE role=?", (role,)).fetchone()["c"]
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
    })


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
    # 默认拷贝普通员工菜单
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
    """删除自定义角色；占用人员改回普通员工。"""
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
    moved = db.execute(
        "UPDATE users SET role='employee' WHERE role=?", (role,)
    ).rowcount
    db.execute("DELETE FROM role_menus WHERE role=?", (role,))
    db.execute("DELETE FROM role_caps WHERE role=?", (role,))
    db.execute("DELETE FROM roles WHERE code=?", (role,))
    db.commit()
    msg = f"已删除角色 {row['label']}"
    if moved:
        msg += f"，{moved} 人已改回普通员工"
    return jsonify({"ok": True, "message": msg, "moved": moved})


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
    """给人分配角色。"""
    if not (
        user_has_cap(user, "role_assign")
        or user_has_cap(user, "config_roles")
        or user["role"] == "super_admin"
    ):
        return jsonify({"ok": False, "error": "无权分配角色"}), 403
    data = request.get_json(force=True) or {}
    role = (data.get("role") or "").strip()
    db = get_db()
    ensure_roles_seeded(db)
    if (
        not role
        or role in ("employee_a", "employee_b")
        or not db.execute("SELECT 1 FROM roles WHERE code=?", (role,)).fetchone()
    ):
        return jsonify({"ok": False, "error": "无效角色"}), 400
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    db.commit()
    return jsonify({
        "ok": True,
        "message": f"已将 {row['display_name']} 设为 {role_label_of(db, role)}",
    })
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
        session["oidc"] = {
            "client_id": "client_laiku_erp",
            "redirect_uri": "http://127.0.0.1:5055/demo/home/callback?app=laiku_erp",
            "state": secrets.token_urlsafe(8),
            "scope": "openid profile",
            "nonce": secrets.token_urlsafe(8),
            "code_challenge": None,
            "code_challenge_method": None,
        }
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


def _redirect_uri_allowed(client_row, redirect_uri: str) -> bool:
    allowed = _parse_redirect_uris(client_row["redirect_uris"])
    if redirect_uri in allowed:
        return True
    # 允许同 path 不同 query（演示 callback?app=xx）
    p = urlparse(redirect_uri)
    base = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    for a in allowed:
        ap = urlparse(a)
        if (ap.scheme, ap.netloc, ap.path) == (p.scheme, p.netloc, p.path):
            return True
    return False


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
            # 个人中心精简组织树：仅结构 + 负责人
            "org_tree": build_org_tree(all_departments(db)),
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
    if row["assignee_id"] != user["id"] and user["role"] != "super_admin":
        return jsonify({"ok": False, "error": "无权查看"}), 403
    return jsonify({"ok": True, "todo": serialize_todo(db, row)})
    # AI-GEN-END


@app.get("/api/org/overview")
@app.get("/api/dept/overview")
@login_required
def org_overview(user):
    """全员可看完整组织树；管理人员可管下级。"""
    db = get_db()
    depts = all_departments(db)
    manage_ids = managed_dept_ids(db, user)
    can_manage = bool(manage_ids)
    q = (request.args.get("q") or "").strip()
    dept_id = request.args.get("dept_id")
    focus_id = int(dept_id) if dept_id else None

    sql = "SELECT * FROM users WHERE 1=1 AND username != ?"
    params = [SYSTEM_ADMIN_USERNAME]
    if focus_id:
        # 选中节点时展示该节点及下级人员
        ids = subtree_ids(depts, focus_id)
        sql += f" AND dept_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    if q:
        sql += " AND (display_name LIKE ? OR username LIKE ? OR phone LIKE ? OR email LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY dept_id, id"
    members = db.execute(sql, params).fetchall()
    members = [m for m in members if not is_hidden_from_org(m)]

    unread = db.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE to_user_id = ? AND is_read = 0",
        (user["id"],),
    ).fetchone()["c"]

    return jsonify(
        {
            "ok": True,
            "can_manage": can_manage,
            "can_set_account_expire": user_can_set_account_expire(user),
            "can_set_dept_owner": user_can_set_dept_owner(user),
            "manage_dept_ids": sorted(manage_ids),
            "departments": depts,
            "tree": build_org_tree(depts, manage_ids),
            "focus_dept_id": focus_id,
            "members": [member_row_enriched(m) for m in members],
            "unread_messages": unread,
        }
    )


# AI-GEN-BEGIN
def user_can_set_dept_owner(user, dept_id=None) -> bool:
    """是否可设置部门负责人：按钮 / 人事超管 / 可管该部门。"""
    if not user:
        return False
    if user_has_cap(user, "org_set_owner") or user_has_cap(user, "manage_all_org"):
        return True
    if user["role"] in ("super_admin", "hr_specialist"):
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
    # 主负责人若仍是普通员工，升为组织负责人（便于侧栏/能力）
    if owner_uid is not None:
        ou = db.execute("SELECT role FROM users WHERE id = ?", (owner_uid,)).fetchone()
        if ou and ou["role"] in ("employee", "employee_a", "employee_b"):
            db.execute(
                "UPDATE users SET role = 'dept_owner' WHERE id = ?", (owner_uid,)
            )
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


@app.post("/api/org/members/account-expire")
@login_required
def set_members_account_expire(user):
    """组织侧设置账号有效期：指定日期或永不过期（NULL）。需角色/人员开通且可管目标。"""
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
    # 全员可看组织内权限概览；管理操作另鉴权
    return jsonify({"ok": True, "user": row_user(target), "systems": my_systems(uid)})


@app.post("/api/org/message")
@app.post("/api/chat/send")
@login_required
def send_org_message(user):
    data = request.get_json(force=True) or {}
    to_user_id = data.get("to_user_id")
    title = (data.get("title") or "").strip() or "即时聊天"
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
        if int(to_user_id) != user["id"] and user["role"] not in (
            "hr_specialist",
            "super_admin",
            "system_owner",
        ):
            return jsonify({"ok": False, "error": "无权限向他人发送系统消息"}), 403
    else:
        if user["role"] not in ("hr_specialist", "super_admin", "system_owner"):
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


def push_system_message(db, to_user_id, title, body):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute(
        """INSERT INTO messages
        (from_user_id, to_user_id, title, body, created_at, is_read, msg_type)
        VALUES (0,?,?,?,?,0,'system')""",
        (to_user_id, title, body, now),
    )
    return cur.lastrowid


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
    if not (user_has_cap(user, "sensitive_config") or user["role"] == "super_admin"):
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
    if not (user_has_cap(user, "sensitive_config") or user["role"] == "super_admin"):
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
    if not (user_has_cap(user, "sensitive_config") or user["role"] == "super_admin"):
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
    if user["role"] not in ("super_admin", "system_owner"):
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
    if uid != user["id"] and user["role"] not in ("hr_specialist", "super_admin", "dept_owner"):
        return jsonify({"ok": False, "error": "无权查看他人账号"}), 403
    if uid != user["id"] and user["role"] == "dept_owner":
        target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not can_manage_member(user, target):
            return jsonify({"ok": False, "error": "仅可查看下级账号"}), 403
    rows = db.execute(
        """SELECT a.id, a.account_name, a.account_label, a.can_login, a.has_sensitive,
                  a.perm_summary, a.system_id, s.name AS system_name, s.code AS system_code,
                  s.has_sensitive AS sys_has_sensitive
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ?
        ORDER BY s.id, a.id""",
        (uid,),
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
    now = datetime.now().strftime("%Y-%m-%d")

    # 人事/负责人可代他人发起敏感/外部申请（链按目标用户解析）
    subject = user
    for_user_id = data.get("for_user_id")
    if for_user_id and int(for_user_id) != int(user["id"]):
        if user["role"] not in ("hr_specialist", "super_admin", "dept_owner"):
            return jsonify({"ok": False, "error": "无权代他人申请"}), 403
        subject = db.execute("SELECT * FROM users WHERE id = ?", (int(for_user_id),)).fetchone()
        if not subject:
            return jsonify({"ok": False, "error": "目标用户不存在"}), 404
        if user["role"] == "dept_owner" and not can_manage_member(user, subject):
            return jsonify({"ok": False, "error": "仅可代本组织下级申请"}), 403

    # 账号关闭：直属审批 → 关闭 can_login
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
        title = (
            f"{subject['display_name']} · 账号关闭 · {acct['system_name']} / {acct['account_name']}"
        )
        approver_id = find_approver(db, subject["id"])
        if not approver_id or int(approver_id) == int(subject["id"]):
            return jsonify({"ok": False, "error": "未找到直属审批人"}), 400
        meta = json.dumps(
            {
                "account_id": acct["id"],
                "system_id": acct["system_id"],
                "account_name": acct["account_name"],
                "system_name": acct["system_name"],
                "leuc_user_id": subject["id"],
            },
            ensure_ascii=False,
        )
        cur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
            VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
            (approver_id, user["id"], title, "账号关闭", now, meta),
        )
        db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
            VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
            (
                approver_id,
                user["id"],
                f"账号关闭 · {acct['system_name']} / {acct['account_name']}",
                "账号关闭",
                now,
                meta,
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
                "message": f"账号关闭已提交，等待直属 {approver['display_name']} 审批",
            }
        )

    # 敏感权限关闭：与开通同链（直属→一级→财务）
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
        todo_type = "敏感权限关闭"
        title = (
            f"{subject['display_name']} · 敏感权限关闭 · {acct['system_name']} / {acct['account_name']}"
        )
        init_title = f"敏感权限关闭 · {acct['system_name']}（审批中）"
        meta_extra = {
            "account_id": acct["id"],
            "system_id": acct["system_id"],
            "account_name": acct["account_name"],
            "system_name": acct["system_name"],
            "close_sensitive": True,
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
        cur = db.execute(
            """INSERT INTO applications
            (flow_code, applicant_id, perm_def_id, system_id, title, status,
             current_step, total_steps, created_at, updated_at, provisioned)
            VALUES (?,?,NULL,?,?,'pending',1,?,?,?,0)""",
            (flow_code, subject["id"], acct["system_id"], title, len(steps), now, now),
        )
        app_id = cur.lastrowid
        first_assignee = None
        first_todo = None
        step_preview = []
        for i, (step_key, step_label, assignee) in enumerate(steps, start=1):
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
                        user["id"],
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
                }
            )
        db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, application_id, meta)
            VALUES (?,?,?,?, 'initiated', 'open', ?, ?, ?)""",
            (
                first_assignee,
                user["id"],
                init_title,
                todo_type,
                now,
                app_id,
                json.dumps({"steps": step_preview, **meta_extra}, ensure_ascii=False),
            ),
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
                    f"敏感关闭已提交，等待 {au['display_name']}（{step_preview[0]['label']}）；"
                    f"链：{' → '.join(s['label'] for s in step_preview)}"
                ),
            }
        )

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

        cur = db.execute(
            """INSERT INTO applications
            (flow_code, applicant_id, perm_def_id, system_id, title, status,
             current_step, total_steps, created_at, updated_at, provisioned)
            VALUES (?,?,?,?,?, 'pending', 1, ?, ?, ?, 0)""",
            (flow_code, subject["id"], perm_id, system_id, title, len(steps), now, now),
        )
        app_id = cur.lastrowid
        first_assignee = None
        first_todo = None
        step_preview = []
        for i, (step_key, step_label, assignee) in enumerate(steps, start=1):
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
                        user["id"],
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
                }
            )
        db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, application_id, meta)
            VALUES (?,?,?,?, 'initiated', 'open', ?, ?, ?)""",
            (
                first_assignee,
                user["id"],
                init_title,
                todo_type,
                now,
                app_id,
                json.dumps({"steps": step_preview}, ensure_ascii=False),
            ),
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
        "account_extend": ("账号延期", f"{subject['display_name']} · 账号延期 {days} 天"),
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
    # AI-GEN-BEGIN
    meta = None
    if apply_type == "account_extend":
        meta = json.dumps(
            {"leuc_user_id": subject["id"], "days": days},
            ensure_ascii=False,
        )
    cur = db.execute(
        """INSERT INTO todos (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
        VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
        (approver_id, user["id"], title, todo_type, now, meta),
    )
    db.execute(
        """INSERT INTO todos (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
        VALUES (?,?,?,?, 'initiated', 'open', ?, ?)""",
        (
            approver_id,
            user["id"],
            title.replace(f"{subject['display_name']} · ", "", 1),
            todo_type,
            now,
            meta,
        ),
    )
    # AI-GEN-END
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
    db = get_db()
    row = db.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "待办不存在"}), 404
    if row["assignee_id"] != user["id"] and user["role"] != "super_admin":
        return jsonify({"ok": False, "error": "仅审批人可处理"}), 403
    if row["bucket"] != "pending":
        return jsonify({"ok": False, "error": "该待办已处理"}), 400

    now = datetime.now().strftime("%Y-%m-%d")
    app_id = row["application_id"] if "application_id" in row.keys() else None

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
        db.commit()
        return jsonify({"ok": True, "message": "已确认账号申请并完成绑定", "provisioned": True})

    # 账号关闭（个人自助 · 直属通过后关闭）
    if row["todo_type"] == "账号关闭":
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
        db.commit()
        return jsonify(
            {
                "ok": True,
                "message": f"已关闭：{result['system']} / {result['account']}",
            }
        )

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
        db.commit()
        return jsonify(
            {
                "ok": True,
                "message": f"已新建人员 {name}（{username}），并已发起账号申请待办",
                "username": username,
            }
        )

    # 敏感权限开通 / 关闭 / 外部人员：多级审批
    if app_id and row["todo_type"] in ("敏感权限", "敏感权限关闭", "外部人员", "账号申请"):
        app_row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if not app_row:
            return jsonify({"ok": False, "error": "申请单不存在"}), 404
        flow_todo_type = row["todo_type"]
        step_order = row["step_order"] or app_row["current_step"]
        # AI-GEN-BEGIN
        # 开通前校验：新建账号须录入账号名（避免先落库再报错）
        if decision == "approved":
            cur_step = db.execute(
                """SELECT step_key FROM application_steps
                WHERE application_id = ? AND step_order = ?""",
                (app_id, step_order),
            ).fetchone()
            nxt_chk = db.execute(
                """SELECT id FROM application_steps
                WHERE application_id = ? AND step_order = ?""",
                (app_id, step_order + 1),
            ).fetchone()
            try:
                meta_pre = json.loads(row["meta"] or "{}")
            except Exception:
                meta_pre = {}
            if (
                cur_step
                and cur_step["step_key"] == "system_owner"
                and not nxt_chk
                and meta_pre.get("create_new")
                and not (data.get("account_name") or "").strip()
            ):
                return jsonify(
                    {
                        "ok": False,
                        "error": "请输入要开通的业务系统账号名",
                        "need_account_input": True,
                        "todo_id": tid,
                        "application_id": app_id,
                        "applicant_id": app_row["applicant_id"],
                        "system_id": app_row["system_id"],
                    }
                ), 400
        # AI-GEN-END
        db.execute(
            "UPDATE todos SET bucket = 'done', status = ? WHERE id = ?",
            (decision, tid),
        )
        db.execute(
            """UPDATE application_steps SET status = ?, decided_at = ?
            WHERE application_id = ? AND step_order = ?""",
            (decision, now, app_id, step_order),
        )
        if decision == "rejected":
            db.execute(
                "UPDATE applications SET status = 'rejected', updated_at = ? WHERE id = ?",
                (now, app_id),
            )
            db.execute(
                """UPDATE todos SET status = 'rejected'
                WHERE application_id = ? AND bucket = 'initiated'""",
                (app_id,),
            )
            db.commit()
            return jsonify({"ok": True, "message": "已驳回，申请结束"})

        nxt = db.execute(
            """SELECT * FROM application_steps
            WHERE application_id = ? AND step_order = ?""",
            (app_id, step_order + 1),
        ).fetchone()
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
            db.execute(
                """UPDATE todos SET title = ?
                WHERE application_id = ? AND bucket = 'initiated'""",
                (
                    f"{app_row['title'].split(' · ', 1)[-1] if ' · ' in app_row['title'] else app_row['title']}（审批中·{nxt['step_label']}）",
                    app_id,
                ),
            )
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

        db.execute(
            "UPDATE applications SET status = 'approved', updated_at = ? WHERE id = ?",
            (now, app_id),
        )
        db.execute(
            """UPDATE todos SET status = 'approved'
            WHERE application_id = ? AND bucket = 'initiated'""",
            (app_id,),
        )
        if app_row["flow_code"] == "sensitive_close":
            try:
                meta = json.loads(row["meta"] or "{}")
            except Exception:
                meta = {}
            result = auto_revoke_sensitive(
                db, app_row, account_id=meta.get("account_id")
            )
            if not result.get("ok"):
                db.commit()
                return jsonify({"ok": False, "error": result.get("error") or "关闭失败"}), 500
            push_system_message(
                db,
                app_row["applicant_id"],
                "敏感权限已关闭",
                f"审批已通过：{result['system']}",
            )
            db.commit()
            return jsonify(
                {
                    "ok": True,
                    "message": f"审批完成，已关闭敏感：{result['system']}",
                    "revoked": True,
                }
            )
        # AI-GEN-BEGIN
        if app_row["flow_code"] in ("sensitive", "account_apply", "account_apply_sensitive"):
            # 若最后一步是系统负责人开通，由当前审批人开通
            last = db.execute(
                """SELECT step_key FROM application_steps
                WHERE application_id = ? ORDER BY step_order DESC LIMIT 1""",
                (app_id,),
            ).fetchone()
            with_sens = app_row["flow_code"] in ("sensitive", "account_apply_sensitive")
            try:
                meta = json.loads(row["meta"] or "{}")
            except Exception:
                meta = {}
            create_new = bool(meta.get("create_new"))
            account_name = (data.get("account_name") or "").strip()
            remark = (data.get("remark") or "").strip()
            # 新建账号：系统负责人开通必须录入账号名
            if (
                last
                and last["step_key"] == "system_owner"
                and create_new
                and not account_name
            ):
                return jsonify(
                    {
                        "ok": False,
                        "error": "请输入要开通的业务系统账号名",
                        "need_account_input": True,
                        "todo_id": tid,
                        "application_id": app_id,
                        "applicant_id": app_row["applicant_id"],
                        "system_id": app_row["system_id"],
                    }
                ), 400
            kwargs = {
                "with_sensitive": with_sens if last and last["step_key"] == "system_owner"
                else (True if app_row["flow_code"] == "sensitive" else with_sens),
                "account_name": account_name or None,
                "remark": remark or None,
            }
            if last and last["step_key"] == "system_owner":
                result = provision_account_apply(db, app_row, **kwargs)
            elif app_row["flow_code"] == "sensitive":
                result = provision_account_apply(db, app_row, with_sensitive=True)
            else:
                result = provision_account_apply(db, app_row, with_sensitive=False)
            if not result.get("ok"):
                return jsonify({"ok": False, "error": result.get("error") or "开通失败"}), 400
            # 回写开通信息到待办 meta，便于排查
            meta.update(
                {
                    "provisioned_account": result.get("account"),
                    "provisioned_account_id": result.get("account_id"),
                    "pool_account_id": result.get("pool_account_id"),
                    "remark": remark,
                }
            )
            db.execute(
                "UPDATE todos SET meta = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), tid),
            )
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
    if decision == "approved" and row["todo_type"] in ("账号延期", "密码延期") and row["initiator_id"]:
        # AI-GEN-BEGIN
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
        urow = db.execute(
            "SELECT account_expire FROM users WHERE id = ?", (target_id,)
        ).fetchone()
        base = datetime.now()
        if urow and urow["account_expire"]:
            try:
                base = datetime.strptime(urow["account_expire"], "%Y-%m-%d")
            except ValueError:
                pass
        if base < datetime.now():
            base = datetime.now()
        new_expire = (base + timedelta(days=days)).strftime("%Y-%m-%d")
        db.execute(
            "UPDATE users SET account_expire = ? WHERE id = ?",
            (new_expire, target_id),
        )
        # AI-GEN-END
    db.commit()
    return jsonify({"ok": True, "message": "已通过" if decision == "approved" else "已驳回"})


@app.post("/api/dept/members")
@login_required
def add_member(user):
    """手动添加：先建人并确认登录用户名；区分内部/外部。"""
    # AI-GEN-BEGIN
    if not require_dept_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    display_name = (data.get("display_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    role = data.get("role") or "employee_a"
    person_type = (data.get("person_type") or "internal").strip()
    if person_type not in ("internal", "external"):
        person_type = "internal"
    dept_id = int(data.get("dept_id") or user["dept_id"])
    if not can_manage_dept(user, dept_id):
        return jsonify({"ok": False, "error": "仅可管理下级组织"}), 403
    if not display_name:
        return jsonify({"ok": False, "error": "姓名必填"}), 400
    if role not in ROLE_MENUS:
        role = "employee_a"
    db = get_db()
    want = (data.get("username") or "").strip() or alloc_username(db, display_name)
    if normalize_username(want) == SYSTEM_ADMIN_USERNAME:
        return jsonify({"ok": False, "error": "用户名 admin 为系统保留"}), 400
    ok, uname_or_err = ensure_username_available(db, want)
    if not ok:
        return jsonify({"ok": False, "error": uname_or_err}), 400
    username = uname_or_err
    preview_base = name_to_pinyin(display_name)
    beisen_user_id = (data.get("beisen_user_id") or "").strip() or None
    acct_expire = default_account_expire(90)
    cur = db.execute(
        """INSERT INTO users
        (username, password, display_name, role, dept_id, phone, email, itcode,
         password_expire, account_expire, person_type, beisen_user_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            acct_expire,
            person_type,
            beisen_user_id,
        ),
    )
    db.commit()
    type_label = "外部人员" if person_type == "external" else "内部人员"
    return jsonify(
        {
            "ok": True,
            "user": {
                "id": cur.lastrowid,
                "username": username,
                "display_name": display_name,
                "pinyin_base": preview_base,
                "person_type": person_type,
            },
            "message": f"已创建{type_label}账号 {username}（初始密码 123456）；后续再绑定系统",
        }
    )
    # AI-GEN-END


@app.patch("/api/org/members/<int:uid>/beisen-user-id")
@login_required
def patch_member_beisen_user_id(user, uid):
    """组织侧维护北森 BeisenUserID（SSO uty=id 用）。"""
    # AI-GEN-BEGIN
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    if not (
        user["role"] in ("super_admin", "hr_specialist")
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
    """兼容旧接口：组织负责人「申请绑定」。"""
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
        "SELECT id, code, name, access_mode, status FROM systems WHERE status='enabled' AND access_mode='apply' ORDER BY id"
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
        return jsonify({"ok": False, "error": "仅可导入到可管组织"}), 403
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
    """组织架构同步花名册：待初始化用户。"""
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
    """组织同步建人：可编辑确认用户名后直接创建（内部人员）。"""
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
    for r in rows:
        dept = db.execute(
            "SELECT id FROM departments WHERE id = ?", (r["dept_id"],)
        ).fetchone()
        if not dept:
            continue
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
        rkeys = r.keys()
        leorg_emp_id = r["leorg_emp_id"] if "leorg_emp_id" in rkeys else None
        beisen_user_id = None
        if "beisen_user_id" in rkeys and r["beisen_user_id"]:
            beisen_user_id = str(r["beisen_user_id"]).strip() or None
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
                (uid, oa["id"], f"{username}_oa", "组织同步初始化", "普通员工"),
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
    db.commit()
    return jsonify(
        {
            "ok": True,
            "count": len(created),
            "created": created,
            "message": f"已确认创建 {len(created)} 人（内部·直接创建，初始密码 123456）",
        }
    )
    # AI-GEN-END


@app.post("/api/hr/sync-pull")
@login_required
def hr_sync_pull(user):
    """从 LeOrg 同步组织 + 人员（幂等 upsert；默认增量，可 full）。"""
    # AI-GEN-BEGIN
    if not require_hr_manage(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    # 兼容旧演示：显式传入 people 时仍走本地模拟
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
    state = _leorg_sync_state(db)
    mapped_orgs = db.execute(
        "SELECT COUNT(*) AS c FROM departments WHERE leorg_id IS NOT NULL"
    ).fetchone()["c"]
    if mode == "auto":
        mode = "full" if (not state or not state.get("last_full_at") or mapped_orgs == 0) else "incr"
    if mode not in ("full", "incr"):
        return jsonify({"ok": False, "error": "mode 仅支持 auto/full/incr"}), 400

    try:
        client = LeorgClient()
        # 组织体量小：始终全量 upsert（幂等）
        orgs = client.list_organizations(status=1)
        org_stats = _sync_leorg_organizations(db, orgs)

        max_change_id = int(state.get("last_change_id") or 0) if state else 0
        if mode == "full":
            emps = client.list_employees(emp_status=1)
            emps_prob = client.list_employees(emp_status=2)
            seen = {e.get("id") for e in emps}
            for e in emps_prob:
                if e.get("id") not in seen:
                    emps.append(e)
            emp_stats = _sync_leorg_employees(db, emps)
            # 全量后抬升水位，避免下次增量重放
            try:
                max_change_id = max(max_change_id, client.latest_change_id(days=max(days, 1)))
            except Exception:
                pass
            fetched_emps = len(emps)
        else:
            new_changes = client.list_employee_changes(days=days, after_id=max_change_id)
            emp_ids = sorted(
                {
                    int(c["entity_id"])
                    for c in new_changes
                    if c.get("entity_id") is not None
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
            emp_stats = _sync_leorg_employees(db, emps)
            if new_changes:
                max_change_id = max(int(c.get("id") or 0) for c in new_changes)
            fetched_emps = len(emps)
            emp_stats["change_rows"] = len(new_changes)
            emp_stats["changed_ids"] = len(emp_ids)

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
        f"组织 +{org_stats['inserted']}/改{org_stats['updated']}；"
        f"人员拉取 {fetched_emps} 人"
        f"（待初始化 +{emp_stats['roster_added']}，更新用户 {emp_stats['users_updated']}，跳过 {emp_stats['skipped']}）"
    )
    if mode == "incr":
        msg += f"；变更条数 {emp_stats.get('change_rows', 0)}"
    return jsonify(
        {
            "ok": True,
            "message": msg,
            "mode": mode,
            "organizations": org_stats,
            "employees": emp_stats,
            "fetched": {"orgs": len(orgs), "employees": fetched_emps},
            "sync_state": _leorg_sync_state(db),
        }
    )
    # AI-GEN-END


@app.post("/api/hr/org-clear")
@login_required
def hr_org_clear(user):
    """清空我的组织：部门树 + 普通员工；保留管理演示账号。"""
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
                f"已清空组织：删除部门 {stats['depts_deleted']}，"
                f"删除员工 {stats['users_deleted']}，保留账号 {stats['users_kept']}，"
                f"根组织 id={stats['root_id']}"
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
    return dict(row) if row else None
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
    """清空部门与员工，保留非 employee 管理账号，重建空根组织。"""
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
        db.execute("DELETE FROM messages WHERE from_user_id = ? OR to_user_id = ?", (uid, uid))
        db.execute("DELETE FROM user_system_accounts WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM user_fingerprints WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM oauth_codes WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (uid,))
        db.execute(
            "DELETE FROM todos WHERE assignee_id = ? OR initiator_id = ?", (uid, uid)
        )
        # 申请单
        app_ids = [
            int(r["id"])
            for r in db.execute(
                "SELECT id FROM applications WHERE applicant_id = ?", (uid,)
            ).fetchall()
        ]
        for aid in app_ids:
            db.execute("DELETE FROM application_steps WHERE application_id = ?", (aid,))
            db.execute("DELETE FROM applications WHERE id = ?", (aid,))
        db.execute(
            "DELETE FROM application_steps WHERE assignee_id = ?", (uid,)
        )
        db.execute(
            "DELETE FROM grant_applications WHERE requester_id = ? OR leuc_user_id = ?",
            (uid, uid),
        )
        db.execute(
            "UPDATE system_accounts SET leuc_user_id = NULL, status = 'unbound' WHERE leuc_user_id = ?",
            (uid,),
        )
        db.execute(
            "UPDATE oa_forms SET leuc_user_id = NULL WHERE leuc_user_id = ?", (uid,)
        )
        db.execute("DELETE FROM system_owners WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM users WHERE id = ?", (uid,))

    cur = db.execute(
        "INSERT INTO departments (name, parent_id, owner_user_id, leorg_id) VALUES (?,?,NULL,NULL)",
        ("来酷科技", None),
    )
    root_id = int(cur.lastrowid)
    if keep_ids:
        db.execute(
            f"UPDATE users SET dept_id = ? WHERE id IN ({','.join('?' * len(keep_ids))})",
            (root_id, *keep_ids),
        )
    # 系统超管不挂组织树
    db.execute(
        "UPDATE users SET dept_id = NULL WHERE username = ?",
        (SYSTEM_ADMIN_USERNAME,),
    )
    # 根组织负责人：优先非系统超管的 super_admin / hr
    owner = db.execute(
        """SELECT id FROM users
        WHERE role IN ('super_admin','hr_specialist') AND username != ?
        ORDER BY CASE role WHEN 'hr_specialist' THEN 0 ELSE 1 END, id LIMIT 1""",
        (SYSTEM_ADMIN_USERNAME,),
    ).fetchone()
    if owner:
        db.execute(
            "UPDATE departments SET owner_user_id = ? WHERE id = ?",
            (int(owner["id"]), root_id),
        )

    return {
        "depts_deleted": depts_deleted,
        "users_deleted": len(drop_ids),
        "users_kept": len(keep_ids),
        "root_id": root_id,
        "kept_usernames": [r["username"] for r in keep_rows],
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
    return jsonify({"ok": True, "added": added, "message": f"已从组织架构拉取 {len(added)} 人待初始化"})
    # AI-GEN-END


def _sync_leorg_organizations(db, orgs):
    """按 leorg_id upsert 部门，两遍设置 parent_id。"""
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
        local_id = leorg_to_local.get(lid)
        if local_id:
            db.execute(
                "UPDATE departments SET name = ?, leorg_id = ? WHERE id = ?",
                (name, lid, local_id),
            )
            updated += 1
        else:
            # 名称兜底匹配（种子通讯录已有同名部门时复用，避免重复树）
            hit = db.execute(
                "SELECT id FROM departments WHERE name = ? AND leorg_id IS NULL LIMIT 1",
                (name,),
            ).fetchone()
            if hit:
                local_id = int(hit["id"])
                db.execute(
                    "UPDATE departments SET leorg_id = ? WHERE id = ?",
                    (lid, local_id),
                )
                updated += 1
            else:
                cur = db.execute(
                    "INSERT INTO departments (name, parent_id, owner_user_id, leorg_id) VALUES (?,?,NULL,?)",
                    (name, None, lid),
                )
                local_id = int(cur.lastrowid)
                inserted += 1
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


def _sync_leorg_employees(db, emps):
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
        phone = (e.get("mobile") or "").strip() or None
        if phone and "*" in phone:
            phone = None
        leorg_emp_id = e.get("id")
        beisen_user_id = _beisen_id_of(e)
        org_leorg = e.get("org_id")
        dept_id = (
            leorg_to_local.get(int(org_leorg))
            if org_leorg is not None
            else None
        ) or fallback_dept_id
        if not name:
            skipped += 1
            continue

        # 离职：不入花名册；已有用户仅跳过（幂等）
        if emp_status == 0:
            if leorg_emp_id is not None:
                db.execute(
                    """DELETE FROM hr_sync_roster
                    WHERE leorg_emp_id = ? AND status = 'pending'""",
                    (int(leorg_emp_id),),
                )
            skipped += 1
            continue

        user = None
        if leorg_emp_id is not None:
            user = db.execute(
                "SELECT id FROM users WHERE leorg_emp_id = ?",
                (int(leorg_emp_id),),
            ).fetchone()
        if not user and beisen_user_id:
            user = db.execute(
                "SELECT id FROM users WHERE beisen_user_id = ?",
                (beisen_user_id,),
            ).fetchone()
        if not user and emp_no:
            for c in dict.fromkeys(
                [emp_no, emp_no.lstrip("0") or emp_no, f"e{emp_no}", f"e{emp_no.lstrip('0') or emp_no}"]
            ):
                user = db.execute(
                    "SELECT id FROM users WHERE itcode = ? OR username = ?",
                    (c, c),
                ).fetchone()
                if user:
                    break
        if not user and email:
            user = db.execute(
                "SELECT id FROM users WHERE lower(email) = lower(?)", (email,)
            ).fetchone()

        if user:
            sets = ["display_name = ?", "dept_id = ?"]
            params: list = [name, dept_id]
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
            params.append(int(user["id"]))
            db.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            users_updated += 1
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
            continue

        # 已确认创建过的花名册不再重复插入
        if leorg_emp_id is not None:
            done = db.execute(
                """SELECT id FROM hr_sync_roster
                WHERE leorg_emp_id = ? AND status = 'synced'""",
                (int(leorg_emp_id),),
            ).fetchone()
            if done:
                skipped += 1
                continue

        db.execute(
            """INSERT INTO hr_sync_roster
            (display_name, dept_id, phone, email, emp_no, leorg_emp_id, beisen_user_id, source, status, synced_at)
            VALUES (?,?,?,?,?,?,?, 'leorg', 'pending', ?)""",
            (
                name,
                dept_id,
                phone,
                email,
                emp_no or None,
                int(leorg_emp_id) if leorg_emp_id is not None else None,
                beisen_user_id,
                now,
            ),
        )
        roster_added += 1

    return {
        "roster_added": roster_added,
        "roster_updated": roster_updated,
        "users_updated": users_updated,
        "beisen_filled": beisen_filled,
        "skipped": skipped,
    }
    # AI-GEN-END


def require_sys_owner(user, system_id=None):
    """超管 / 系统管理员；指定 system_id 时校验是否为该系统管理员（可多人）。"""
    # AI-GEN-BEGIN
    if user["role"] == "super_admin":
        return True
    if user["role"] != "system_owner":
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
    if user["role"] == "super_admin":
        return None
    if user["role"] != "system_owner":
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
        FROM systems WHERE status='enabled' ORDER BY id"""
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
    if not (user_has_cap(user, "direct_bind") or user["role"] in ("hr_specialist", "super_admin")):
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
    if not leuc_user_ids and not items and user["role"] in ("employee_a", "employee_b"):
        leuc_user_ids = [user["id"]]

    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d")
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
        for it in items:
            uid = int(it.get("leuc_user_id") or 0)
            sid = int(it.get("system_id") or 0)
            if not uid or not sid:
                continue
            urow = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not urow:
                continue
            if not can_apply_for_user(user, urow):
                return jsonify(
                    {"ok": False, "error": f"无权为 {urow['display_name']} 申请账号"}
                ), 403
            sys_row = db.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
            if not sys_row:
                continue
            ok, err = user_may_access_system(db, urow, sys_row)
            if not ok:
                return jsonify({"ok": False, "error": f"{urow['display_name']}：{err}"}), 400
            with_sensitive = bool(it.get("with_sensitive")) and int(
                sys_row["has_sensitive"] or 0
            )
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
            steps = append_system_owner_step(db, sid, steps)
            if not steps:
                return jsonify({"ok": False, "error": "审批链为空"}), 400
            title = (
                f"{urow['display_name']} · 账号申请 · {sys_row['name']}"
                + (" · 含敏感" if with_sensitive else "")
            )
            init_title = f"账号申请 · {sys_row['name']}（审批中）"
            app_id, first_todo, first_assignee, preview = start_multi_step_apply(
                db,
                flow_code=flow_code,
                todo_type="账号申请",
                title=title,
                init_title=init_title,
                subject_id=uid,
                initiator_id=user["id"],
                system_id=sid,
                steps=steps,
                meta_extra={
                    "system_id": sid,
                    "leuc_user_id": uid,
                    "with_sensitive": with_sensitive,
                    "create_new": True,
                },
            )
            au = db.execute(
                "SELECT display_name FROM users WHERE id = ?", (first_assignee,)
            ).fetchone()
            created.append(
                {
                    "application_id": app_id,
                    "todo_id": first_todo,
                    "user": urow["display_name"],
                    "system": sys_row["name"],
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
    if manage_ids is None:
        systems = db.execute("SELECT id, code, name FROM systems ORDER BY id").fetchall()
    else:
        if not manage_ids:
            return jsonify({"ok": True, "systems": [], "accounts": [], "grants": []})
        ph0 = ",".join("?" * len(manage_ids))
        systems = db.execute(
            f"SELECT id, code, name FROM systems WHERE id IN ({ph0}) ORDER BY id",
            manage_ids,
        ).fetchall()
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
    return jsonify(
        {
            "ok": True,
            "systems": [dict(s) for s in systems],
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
    if manage_ids is None:
        systems = db.execute("SELECT id, code, name FROM systems ORDER BY id").fetchall()
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
            f"SELECT id, code, name FROM systems WHERE id IN ({ph0}) ORDER BY id",
            manage_ids,
        ).fetchall()
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

    # 统计基于负责范围内全部账号（不受 unbound/q 过滤影响总数口径：按 filter_ids 全量）
    all_rows = [dict(r) for r in rows]
    bound_n = sum(1 for a in all_rows if a.get("leuc_user_id"))
    unbound_n = len(all_rows) - bound_n
    by_system = []
    for s in systems:
        if s["id"] not in filter_ids:
            continue
        subset = [a for a in all_rows if a["system_id"] == s["id"]]
        b = sum(1 for a in subset if a.get("leuc_user_id"))
        by_system.append(
            {
                "id": s["id"],
                "code": s["code"],
                "name": s["name"],
                "total": len(subset),
                "bound": b,
                "unbound": len(subset) - b,
            }
        )

    return jsonify(
        {
            "ok": True,
            "systems": [dict(s) for s in systems],
            "accounts": accounts,
            "stats": {
                "total": len(all_rows),
                "bound": bound_n,
                "unbound": unbound_n,
                "filtered": len(accounts),
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
        acct_name = (data.get("account_name") or f"{urow['username']}_auto").strip()
        cur = db.execute(
            """INSERT INTO system_accounts
            (system_id, account_name, display_name, phone, email, itcode, status, leuc_user_id, source, created_at)
            VALUES (?,?,?,?,?,?, 'bound', ?, 'manual', ?)""",
            (
                grant["system_id"],
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


@app.post("/api/sys-accounts/import")
@login_required
def sys_accounts_import(user):
    """导入/同步子系统账号。CSV：账号名,姓名,手机,邮箱,itcode"""
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    system_id = int(data.get("system_id") or 0)
    if not require_sys_owner(user, system_id):
        return jsonify({"ok": False, "error": "非负责的系统"}), 403
    text = data.get("csv") or ""
    source = data.get("source") or "import"
    reader = csv.reader(io.StringIO(text.strip()))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d")
    added = 0
    for row in reader:
        if not row or not row[0].strip() or row[0].strip().startswith("#"):
            continue
        if row[0].strip() in ("账号", "account_name", "账号名"):
            continue
        account_name = row[0].strip()
        display_name = row[1].strip() if len(row) > 1 else ""
        phone = row[2].strip() if len(row) > 2 else None
        email = row[3].strip() if len(row) > 3 else None
        itcode = row[4].strip() if len(row) > 4 else None
        exists = db.execute(
            "SELECT id FROM system_accounts WHERE system_id = ? AND account_name = ?",
            (system_id, account_name),
        ).fetchone()
        if exists:
            db.execute(
                """UPDATE system_accounts
                SET display_name=?, phone=?, email=?, itcode=?, source=?
                WHERE id=?""",
                (display_name or None, phone, email, itcode, source, exists["id"]),
            )
        else:
            db.execute(
                """INSERT INTO system_accounts
                (system_id, account_name, display_name, phone, email, itcode, status, source, created_at)
                VALUES (?,?,?,?,?,?, 'unbound', ?, ?)""",
                (system_id, account_name, display_name, phone, email, itcode, source, now),
            )
            added += 1
    db.commit()
    return jsonify({"ok": True, "added": added, "message": f"已导入/同步，新增 {added} 个子系统账号"})


@app.post("/api/sys-accounts/sync-demo")
@login_required
def sys_accounts_sync_demo(user):
    """模拟从子系统同步一批账号。"""
    if not require_sys_owner(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    system_id = int(data.get("system_id") or 0)
    if not require_sys_owner(user, system_id):
        return jsonify({"ok": False, "error": "非负责的系统"}), 403
    csv_text = (
        "账号名,姓名,手机,邮箱,itcode\n"
        "sync_demo_a,同步甲,13920000001,a@lecoo.com,synca\n"
        "sync_demo_b,同步乙,13920000002,b@lecoo.com,syncb\n"
    )
    return _import_accounts_internal(system_id, csv_text, "sync")


def _import_accounts_internal(system_id, text, source):
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d")
    reader = csv.reader(io.StringIO(text.strip()))
    added = 0
    for row in reader:
        if not row or not row[0].strip() or row[0].strip().startswith("#"):
            continue
        if row[0].strip() in ("账号", "account_name", "账号名"):
            continue
        account_name = row[0].strip()
        display_name = row[1].strip() if len(row) > 1 else ""
        phone = row[2].strip() if len(row) > 2 else None
        email = row[3].strip() if len(row) > 3 else None
        itcode = row[4].strip() if len(row) > 4 else None
        exists = db.execute(
            "SELECT id FROM system_accounts WHERE system_id = ? AND account_name = ?",
            (system_id, account_name),
        ).fetchone()
        if not exists:
            db.execute(
                """INSERT INTO system_accounts
                (system_id, account_name, display_name, phone, email, itcode, status, source, created_at)
                VALUES (?,?,?,?,?,?, 'unbound', ?, ?)""",
                (system_id, account_name, display_name, phone, email, itcode, source, now),
            )
            added += 1
    db.commit()
    return jsonify({"ok": True, "added": added, "message": f"已从子系统同步，新增 {added} 个账号"})


@app.get("/api/admin/systems")
@app.get("/api/my-systems")
@login_required
def admin_systems(user):
    """业务系统管理：列举全部可登录系统（申请账号同源列表）。仅超管/系统管理员。"""
    # AI-GEN-BEGIN
    if user["role"] not in ("super_admin", "system_owner"):
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
        """SELECT a.id, a.system_id, a.account_name, a.display_name, a.phone, a.email,
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
            "is_super": user["role"] == "super_admin",
        }
    )
    # AI-GEN-END


@app.post("/api/admin/systems/<int:sid>/owners")
@login_required
def admin_system_owners(user, sid):
    """超管指定系统管理员（可多人）。"""
    # AI-GEN-BEGIN
    if user["role"] != "super_admin":
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


@app.post("/api/admin/systems")
@login_required
def admin_create_system(user):
    if user["role"] != "super_admin":
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
    owner_ids = data.get("owner_user_ids") or data.get("owners") or []
    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO systems
            (code, name, client_id, client_secret, redirect_uris, scopes, grant_types,
             token_endpoint_auth_method, require_pkce, access_mode, forbid_external, status, owner_user_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            "owners": fetch_system_owners(db, sid),
            "message": f"已添加（{mode_label}），请妥善保存 secret",
        }
    )


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
    return jsonify(
        {
            "ok": True,
            "issuer": "http://127.0.0.1:5055",
            "systems": [dict(r) for r in rows],
        }
    )


# AI-GEN-BEGIN
def _oa_can_view(user):
    return user["role"] in ("hr_specialist", "system_owner", "super_admin")


def _oa_hr_user_id(db):
    row = db.execute(
        "SELECT id FROM users WHERE role = 'hr_specialist' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _oa_find_user(db, oa_person_code, applicant_name=None):
    code = (oa_person_code or "").strip()
    if code:
        row = db.execute(
            "SELECT * FROM users WHERE itcode = ? OR username = ?", (code, code)
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
    now = datetime.now().strftime("%Y-%m-%d")
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
    now = datetime.now().strftime("%Y-%m-%d")
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
    """模拟：北森注销单据通过 → 直接关闭各系统账号（不经系统负责人待办）。"""
    # AI-GEN-BEGIN
    if not _oa_can_view(user):
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(force=True) or {}
    now = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    oa_code = (data.get("oa_person_code") or "lisi").strip()
    name = (data.get("applicant_name") or "").strip()
    form_no = data.get("oa_form_no") or f"BS-LEAVE-{datetime.now().strftime('%H%M%S')}"

    urow = _oa_find_user(db, oa_code, name or None)
    fcur = db.execute(
        """INSERT INTO oa_forms
        (form_type, oa_form_no, status, title, applicant_name, oa_person_code,
         leuc_user_id, approved_at, created_at, remark)
        VALUES ('leave', ?, 'received', ?, ?, ?, ?, ?, ?, ?)""",
        (
            form_no,
            f"北森注销 · {name or (urow['display_name'] if urow else oa_code)}",
            name or (urow["display_name"] if urow else oa_code),
            oa_code,
            urow["id"] if urow else None,
            now,
            now,
            "北森注销单据已通过，系统直接关闭账号",
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
            (form_id, "—", name or oa_code, oa_code, "未匹配到 LEUC 用户，待人事核对"),
        )
        tcur = db.execute(
            """INSERT INTO todos
            (assignee_id, initiator_id, title, todo_type, bucket, status, created_at, meta)
            VALUES (?,?,?,?, 'pending', 'open', ?, ?)""",
            (
                hr_id,
                user["id"],
                f"北森注销 · 人员未匹配 {name or oa_code}",
                "人员核对",
                now,
                json.dumps(
                    {
                        "oa_form_id": form_id,
                        "oa_line_id": lcur.lastrowid,
                        "applicant_name": name or oa_code,
                        "oa_person_code": oa_code,
                        "leave": True,
                        "source": "beisen",
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
                "message": f"北森注销单 {form_no} 未匹配到用户，已派人人事核对",
                "todos": created,
            }
        )

    accts = db.execute(
        """SELECT a.system_id, s.name AS system_name, s.code AS system_code,
            GROUP_CONCAT(a.account_name) AS account_names, COUNT(*) AS cnt
        FROM user_system_accounts a
        JOIN systems s ON s.id = a.system_id
        WHERE a.user_id = ? AND a.can_login = 1
        GROUP BY a.system_id""",
        (urow["id"],),
    ).fetchall()
    if not accts:
        db.execute(
            """INSERT INTO oa_form_lines
            (form_id, applicant_name, oa_person_code, handle_status, remark)
            VALUES (?,?,?, 'done', ?)""",
            (form_id, urow["display_name"], oa_code, "无可关闭的可登录账号"),
        )
        db.execute("UPDATE oa_forms SET status = 'done' WHERE id = ?", (form_id,))
        db.commit()
        return jsonify(
            {
                "ok": True,
                "form_id": form_id,
                "message": f"{urow['display_name']} 无可关闭账号",
                "closed": [],
            }
        )

    closed = []
    for a in accts:
        db.execute(
            "UPDATE user_system_accounts SET can_login = 0 WHERE user_id = ? AND system_id = ?",
            (urow["id"], a["system_id"]),
        )
        db.execute(
            """UPDATE system_accounts SET status = 'closed'
            WHERE leuc_user_id = ? AND system_id = ?""",
            (urow["id"], a["system_id"]),
        )
        db.execute(
            """INSERT INTO oa_form_lines
            (form_id, system_id, system_code, system_name, applicant_name, oa_person_code,
             handle_status, remark)
            VALUES (?,?,?,?,?,?, 'done', ?)""",
            (
                form_id,
                a["system_id"],
                a["system_code"],
                a["system_name"],
                urow["display_name"],
                oa_code,
                f"北森直办已关闭：{a['account_names']}",
            ),
        )
        closed.append(
            {
                "system": a["system_name"],
                "accounts": a["account_names"],
            }
        )

    push_system_message(
        db,
        urow["id"],
        "账号已注销关闭",
        f"北森注销单 {form_no} 已生效，已关闭 {len(closed)} 个系统的可登录账号",
    )
    db.execute("UPDATE oa_forms SET status = 'done' WHERE id = ?", (form_id,))
    db.commit()
    return jsonify(
        {
            "ok": True,
            "form_id": form_id,
            "oa_form_no": form_no,
            "user": urow["display_name"],
            "closed": closed,
            "todos": [],
            "message": f"北森注销单 {form_no} 已直接关闭 {len(closed)} 个系统账号（无待办）",
        }
    )


# AI-GEN-END


@app.get("/api/demo/portal-systems")
def demo_portal_systems():
    """演示导航页：返回可点击登录的业务系统（含 client_secret，仅原型）。"""
    # AI-GEN-BEGIN
    beisen_st = beisen_status_dict()
    rows = get_db().execute(
        """SELECT id, code, name, client_id, client_secret, redirect_uris,
                  access_mode, status, require_pkce
           FROM systems WHERE status = 'enabled' ORDER BY id"""
    ).fetchall()
    systems = []
    for r in rows:
        d = dict(r)
        d["mode_label"] = "全员登录" if d.get("access_mode") == "open" else "需账号绑定"
        d["portal_redirect"] = f"http://127.0.0.1:5055/demo/home/callback?app={d['code']}"
        if d.get("code") == "beisen":
            d["beisen_sso_enabled"] = beisen_st["enabled"]
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
def _beisen_resolve_sub(user, data=None, uty: str = "id"):
    """按 uty 取登录标识：id→beisen_user_id，email→邮箱，jobcode→工号/itcode。"""
    data = data or {}
    override = (data.get("sub") or request.args.get("sub") or "").strip()
    if override:
        return override
    mode = (uty or "id").strip().lower()
    if mode == "email":
        return (user.get("email") or "").strip()
    if mode in ("jobcode", "job_code"):
        return (user.get("itcode") or user.get("username") or "").strip()
    # 默认 id
    return str(user.get("beisen_user_id") or "").strip()


@app.get("/api/beisen/sso/status")
def beisen_sso_status():
    """北森真实 SSO 是否已配置。"""
    return jsonify({"ok": True, **beisen_status_dict()})


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
    sub = _beisen_resolve_sub(user, data, uty=uty)
    if not sub:
        tip = {
            "id": "当前用户无北森用户ID，请在「我的组织」补全 beisen_user_id",
            "email": "当前用户无邮箱，请补全邮箱或传 sub",
            "jobcode": "当前用户无工号/itcode，请补全或传 sub",
        }.get(uty, "缺少登录标识 sub")
        return jsonify({"ok": False, "error": tip, "uty": uty}), 400
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
            "user": {
                "id": user["id"],
                "username": user.get("username"),
                "display_name": user.get("display_name"),
                "email": user.get("email"),
                "beisen_user_id": user.get("beisen_user_id"),
            },
        }
    )


@app.get("/beisen/sso/go")
def beisen_sso_go():
    """浏览器直达：未登录先走 /sso，已登录 302 跳北森。"""
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
        }
        return redirect("/sso?next=beisen_sso")
    pending = session.pop("beisen_sso_pending", None) or {}
    data = {
        "sub": request.args.get("sub") or pending.get("sub"),
        "uty": request.args.get("uty") or pending.get("uty"),
        "return_url": request.args.get("return_url")
        if "return_url" in request.args
        else pending.get("return_url"),
    }
    uty = (data.get("uty") or cfg.uty or "id").strip()
    sub = _beisen_resolve_sub(user, data, uty=uty)
    if not sub:
        tip = {
            "id": "当前用户无北森用户ID。请到「我的组织」补全，或 "
            "<code>?sub=北森UserID</code>。",
            "email": "当前用户无邮箱。请补全邮箱或 <code>?sub=...</code>。",
            "jobcode": "当前用户无工号。请补全 itcode 或 <code>?sub=...</code>。",
        }.get(uty, "缺少登录标识 sub。")
        return (
            f"<!doctype html><meta charset=utf-8><title>北森 SSO</title>"
            f"<p>{tip}</p>",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )
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
    """独立 OA 单据页（风格对齐业务系统导航）。"""
    # AI-GEN-BEGIN
    return send_from_directory(STATIC, "oa.html")
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


@app.post("/api/switch-role")
def switch_role():
    data = request.get_json(force=True) or {}
    username = data.get("username")
    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    session.clear()
    session["user_id"] = row["id"]
    session["login_source"] = "leuc"
    return jsonify({"ok": True, "user": row_user(row)})


def main():
    # AI-GEN-BEGIN
    # 默认不强制重建，避免每次启动清空 LeOrg 同步数据；需要重建时：
    #   python -c "from db import init_db; init_db(force=True)"
    init_db(force=False)
    # AI-GEN-END
    print("LEUC 原型: http://127.0.0.1:5055")
    print("管理账号: admin / sunli / zhangcai / zhaomin / liufang / huangwei  密码 123456")
    app.run(host="127.0.0.1", port=5055, debug=True)


if __name__ == "__main__":
    main()
# AI-GEN-END
