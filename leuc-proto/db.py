# AI-GEN-BEGIN
"""LEUC 原型 SQLite 初始化与种子数据。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "leuc.db"
_SEED_ROOT_ID = 1
_SEED_BTIT_ID = 1

try:
    from pypinyin import lazy_pinyin as _lazy_pinyin
except ImportError:  # pragma: no cover
    _lazy_pinyin = None

# 无 pypinyin 时的兜底（覆盖种子姓名）
_FALLBACK = {
    "张": "zhang", "三": "san", "李": "li", "四": "si", "王": "wang", "强": "qiang",
    "赵": "zhao", "敏": "min", "陈": "chen", "超": "chao", "钱": "qian", "七": "qi",
    "周": "zhou", "八": "ba", "吴": "wu", "九": "jiu", "普": "pu", "通": "tong",
    "员": "yuan", "工": "gong", "部": "bu", "门": "men", "负": "fu", "责": "ze",
    "人": "ren", "系": "xi", "统": "tong", "管": "guan", "理": "li", "员": "yuan",
    "孙": "sun", "丽": "li", "财": "cai", "专": "zhuan", "事": "shi",
    "刘": "liu", "一": "yi", "陈": "chen", "二": "er", "赵": "zhao", "六": "liu",
    "新": "xin", "周": "zhou",
}


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departments (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER,
  owner_user_id INTEGER,
  leorg_id INTEGER UNIQUE,
  manager_leorg_emp_id INTEGER,
  FOREIGN KEY (parent_id) REFERENCES departments(id)
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL,
  dept_id INTEGER,
  phone TEXT,
  email TEXT,
  itcode TEXT,
  password_expire TEXT,
  account_expire TEXT,
  person_type TEXT NOT NULL DEFAULT 'internal',
  feishu_bound INTEGER DEFAULT 0,
  wecom_bound INTEGER DEFAULT 0,
  face_enrolled INTEGER DEFAULT 0,
  face_enrolled_at TEXT,
  fingerprint_enrolled INTEGER DEFAULT 0,
  fingerprint_enrolled_at TEXT,
  can_proxy_apply INTEGER DEFAULT 0,
  can_set_account_expire INTEGER DEFAULT 0,
  beisen_user_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  FOREIGN KEY (dept_id) REFERENCES departments(id)
);

CREATE TABLE IF NOT EXISTS dept_extra_owners (
  dept_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  PRIMARY KEY (dept_id, user_id)
);

CREATE TABLE IF NOT EXISTS systems (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  client_id TEXT NOT NULL UNIQUE,
  client_secret TEXT NOT NULL,
  redirect_uris TEXT NOT NULL DEFAULT '',
  scopes TEXT NOT NULL DEFAULT 'openid profile',
  grant_types TEXT NOT NULL DEFAULT 'authorization_code',
  token_endpoint_auth_method TEXT NOT NULL DEFAULT 'client_secret_post',
  require_pkce INTEGER NOT NULL DEFAULT 1,
  access_mode TEXT NOT NULL DEFAULT 'apply',
  forbid_external INTEGER NOT NULL DEFAULT 0,
  has_sensitive INTEGER NOT NULL DEFAULT 0,
  sso_login_field TEXT NOT NULL DEFAULT 'account_name',
  status TEXT NOT NULL DEFAULT 'enabled',
  is_builtin INTEGER NOT NULL DEFAULT 0,
  owner_user_id INTEGER,
  created_at TEXT
);

-- AI-GEN-BEGIN
CREATE TABLE IF NOT EXISTS system_owners (
  system_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  PRIMARY KEY (system_id, user_id),
  FOREIGN KEY (system_id) REFERENCES systems(id),
  FOREIGN KEY (user_id) REFERENCES users(id)
);
-- AI-GEN-END

CREATE TABLE IF NOT EXISTS user_system_accounts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  system_id INTEGER NOT NULL,
  account_name TEXT NOT NULL,
  account_label TEXT,
  is_default INTEGER DEFAULT 0,
  can_login INTEGER DEFAULT 1,
  has_sensitive INTEGER DEFAULT 0,
  perm_summary TEXT DEFAULT '',
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (system_id) REFERENCES systems(id)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  from_user_id INTEGER NOT NULL,
  to_user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  is_read INTEGER DEFAULT 0,
  msg_type TEXT NOT NULL DEFAULT 'chat',
  ref_type TEXT,
  ref_id INTEGER
);

CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY,
  assignee_id INTEGER,
  initiator_id INTEGER,
  title TEXT NOT NULL,
  todo_type TEXT NOT NULL,
  bucket TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  application_id INTEGER,
  step_order INTEGER,
  meta TEXT
);

CREATE TABLE IF NOT EXISTS approval_chain_steps (
  id INTEGER PRIMARY KEY,
  flow_code TEXT NOT NULL,
  step_order INTEGER NOT NULL,
  step_key TEXT NOT NULL,
  step_label TEXT NOT NULL,
  assignee_user_id INTEGER,
  enabled INTEGER DEFAULT 1
);

-- AI-GEN-BEGIN
-- 审批链部门特例：仅匹配申请人所属部门（不继承父子）
CREATE TABLE IF NOT EXISTS approval_chain_dept_overrides (
  id INTEGER PRIMARY KEY,
  flow_code TEXT NOT NULL,
  step_key TEXT NOT NULL,
  dept_id INTEGER NOT NULL,
  assignee_user_id INTEGER NOT NULL,
  UNIQUE(flow_code, step_key, dept_id)
);
-- AI-GEN-END

CREATE TABLE IF NOT EXISTS sensitive_perm_defs (
  id INTEGER PRIMARY KEY,
  system_id INTEGER NOT NULL,
  perm_code TEXT NOT NULL,
  perm_name TEXT NOT NULL,
  description TEXT DEFAULT '',
  parent_id INTEGER,
  is_sensitive INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER DEFAULT 1,
  FOREIGN KEY (system_id) REFERENCES systems(id)
);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  flow_code TEXT NOT NULL,
  applicant_id INTEGER NOT NULL,
  perm_def_id INTEGER,
  system_id INTEGER,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  current_step INTEGER DEFAULT 1,
  total_steps INTEGER DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  provisioned INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS application_steps (
  id INTEGER PRIMARY KEY,
  application_id INTEGER NOT NULL,
  step_order INTEGER NOT NULL,
  step_key TEXT NOT NULL,
  step_label TEXT NOT NULL,
  assignee_id INTEGER,
  status TEXT NOT NULL,
  todo_id INTEGER,
  decided_at TEXT
);

CREATE TABLE IF NOT EXISTS login_risk (
  username TEXT PRIMARY KEY,
  fail_count INTEGER DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS reset_codes (
  account TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  code TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_fingerprints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  enrolled_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS role_menus (
  role TEXT NOT NULL,
  menu_id TEXT NOT NULL,
  PRIMARY KEY (role, menu_id)
);

CREATE TABLE IF NOT EXISTS role_caps (
  role TEXT NOT NULL,
  cap_id TEXT NOT NULL,
  PRIMARY KEY (role, cap_id)
);

CREATE TABLE IF NOT EXISTS roles (
  code TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  is_builtin INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_codes (
  code TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  account_id INTEGER,
  redirect_uri TEXT NOT NULL,
  scope TEXT,
  nonce TEXT,
  code_challenge TEXT,
  code_challenge_method TEXT,
  expires_at TEXT NOT NULL,
  used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  access_token TEXT PRIMARY KEY,
  refresh_token TEXT,
  client_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  account_id INTEGER,
  scope TEXT,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hr_sync_roster (
  id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  dept_id INTEGER NOT NULL,
  phone TEXT,
  email TEXT,
  emp_no TEXT,
  leorg_emp_id INTEGER,
  beisen_user_id TEXT,
  source TEXT DEFAULT 'org_sync',
  status TEXT NOT NULL DEFAULT 'pending',
  created_user_id INTEGER,
  synced_at TEXT
);

CREATE TABLE IF NOT EXISTS leorg_sync_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_mode TEXT,
  last_full_at TEXT,
  last_incr_at TEXT,
  last_change_id INTEGER DEFAULT 0,
  org_mapped INTEGER DEFAULT 0,
  emp_touched INTEGER DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS system_accounts (
  id INTEGER PRIMARY KEY,
  system_id INTEGER NOT NULL,
  account_uid TEXT,
  account_name TEXT NOT NULL,
  display_name TEXT,
  phone TEXT,
  email TEXT,
  itcode TEXT,
  status TEXT NOT NULL DEFAULT 'unbound',
  leuc_user_id INTEGER,
  source TEXT DEFAULT 'manual',
  created_at TEXT,
  FOREIGN KEY (system_id) REFERENCES systems(id),
  FOREIGN KEY (leuc_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS grant_applications (
  id INTEGER PRIMARY KEY,
  requester_id INTEGER NOT NULL,
  system_id INTEGER NOT NULL,
  leuc_user_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  suggested_account_id INTEGER,
  bound_account_id INTEGER,
  match_hints TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  todo_id INTEGER,
  FOREIGN KEY (system_id) REFERENCES systems(id),
  FOREIGN KEY (leuc_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS oa_forms (
  id INTEGER PRIMARY KEY,
  form_type TEXT NOT NULL,
  oa_form_no TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'received',
  title TEXT,
  applicant_name TEXT,
  oa_person_code TEXT,
  leuc_user_id INTEGER,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  remark TEXT
);

CREATE TABLE IF NOT EXISTS oa_form_lines (
  id INTEGER PRIMARY KEY,
  form_id INTEGER NOT NULL,
  system_id INTEGER,
  system_code TEXT,
  system_name TEXT,
  req_category TEXT,
  system_entity TEXT,
  applicant_name TEXT,
  applicant_job TEXT,
  oa_person_code TEXT,
  handle_status TEXT NOT NULL DEFAULT 'pending',
  todo_id INTEGER,
  grant_id INTEGER,
  remark TEXT,
  FOREIGN KEY (form_id) REFERENCES oa_forms(id)
);
"""


def name_to_pinyin(display_name: str) -> str:
    name = (display_name or "").strip()
    if not name:
        return "user"
    if _lazy_pinyin:
        raw = "".join(_lazy_pinyin(name))
    else:
        raw = "".join(_FALLBACK.get(ch, ch if re.match(r"[A-Za-z0-9]", ch) else "") for ch in name)
    raw = re.sub(r"[^a-zA-Z0-9]", "", raw).lower()
    return raw or "user"


def alloc_username(conn: sqlite3.Connection, display_name: str) -> str:
    """姓名拼音全拼；重复则加 1、2… 自增。全局唯一。"""
    base = name_to_pinyin(display_name)
    exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (base,)).fetchone()
    if not exists:
        return base
    n = 1
    while True:
        cand = f"{base}{n}"
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (cand,)).fetchone():
            return cand
        n += 1


# AI-GEN-BEGIN
def normalize_username(username: str) -> str:
    raw = (username or "").strip().lower()
    return re.sub(r"[^a-z0-9_]", "", raw)


def ensure_username_available(
    conn: sqlite3.Connection, username: str, exclude_user_id: int | None = None
) -> tuple[bool, str]:
    """校验登录用户名：非空、合法、全局唯一。返回 (ok, error_or_username)。"""
    uname = normalize_username(username)
    if not uname:
        return False, "登录用户名不能为空（仅字母数字下划线）"
    if exclude_user_id:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ? AND id != ?",
            (uname, exclude_user_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (uname,)
        ).fetchone()
    if row:
        return False, f"用户名 {uname} 已被占用"
    return True, uname
# AI-GEN-END


def preview_unique_usernames(
    conn: sqlite3.Connection, display_names: list[str]
) -> list[dict]:
    """批量预览全局唯一用户名（含本批内互斥）。"""
    taken = {r[0] for r in conn.execute("SELECT username FROM users").fetchall()}
    out = []
    for name in display_names:
        base = name_to_pinyin(name)
        cand = base
        n = 1
        while cand in taken:
            cand = f"{base}{n}"
            n += 1
        taken.add(cand)
        out.append(
            {
                "display_name": name,
                "pinyin_base": base,
                "username_preview": cand,
                "will_suffix": cand != base,
            }
        )
    return out


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate_schema(conn: sqlite3.Connection) -> None:
    """存量库补列（CREATE IF NOT EXISTS 不会改已有表）。"""
    # AI-GEN-BEGIN
    dept_cols = _table_cols(conn, "departments")
    if dept_cols and "leorg_id" not in dept_cols:
        conn.execute("ALTER TABLE departments ADD COLUMN leorg_id INTEGER")
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_departments_leorg_id "
                "ON departments(leorg_id) WHERE leorg_id IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass
    # AI-GEN-BEGIN
    if dept_cols and "manager_leorg_emp_id" not in dept_cols:
        conn.execute(
            "ALTER TABLE departments ADD COLUMN manager_leorg_emp_id INTEGER"
        )
    # AI-GEN-END
    roster_cols = _table_cols(conn, "hr_sync_roster")
    if roster_cols and "leorg_emp_id" not in roster_cols:
        conn.execute("ALTER TABLE hr_sync_roster ADD COLUMN leorg_emp_id INTEGER")
    if roster_cols and "beisen_user_id" not in roster_cols:
        conn.execute("ALTER TABLE hr_sync_roster ADD COLUMN beisen_user_id TEXT")
    user_cols = _table_cols(conn, "users")
    if user_cols and "leorg_emp_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN leorg_emp_id INTEGER")
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_leorg_emp_id "
                "ON users(leorg_emp_id) WHERE leorg_emp_id IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass
    if user_cols and "beisen_user_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN beisen_user_id TEXT")
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_beisen_user_id "
                "ON users(beisen_user_id) WHERE beisen_user_id IS NOT NULL "
                "AND beisen_user_id != ''"
            )
        except sqlite3.OperationalError:
            pass
    # AI-GEN-BEGIN
    sa_cols = _table_cols(conn, "system_accounts")
    if sa_cols and "account_uid" not in sa_cols:
        conn.execute("ALTER TABLE system_accounts ADD COLUMN account_uid TEXT")
    # 旧数据用 account_name 回填唯一标识；同系统重复名则加 #id
    if sa_cols or "account_uid" in (_table_cols(conn, "system_accounts") or []):
        rows = conn.execute(
            """SELECT id, system_id, account_name, account_uid FROM system_accounts
            WHERE account_uid IS NULL OR account_uid = ''"""
        ).fetchall()
        for r in rows:
            base = (r["account_name"] or f"acct-{r['id']}").strip()
            uid = base
            clash = conn.execute(
                """SELECT id FROM system_accounts
                WHERE system_id = ? AND account_uid = ? AND id != ? LIMIT 1""",
                (r["system_id"], uid, r["id"]),
            ).fetchone()
            if clash:
                uid = f"{base}#{r['id']}"
            conn.execute(
                "UPDATE system_accounts SET account_uid = ? WHERE id = ?",
                (uid, r["id"]),
            )
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_system_accounts_uid "
                "ON system_accounts(system_id, account_uid) "
                "WHERE account_uid IS NOT NULL AND account_uid != ''"
            )
        except sqlite3.OperationalError:
            pass
    # AI-GEN-BEGIN
    # 子系统 SSO 登录字段：account_uid / account_name / email / phone / itcode
    sys_cols = _table_cols(conn, "systems")
    sso_field_just_added = bool(sys_cols and "sso_login_field" not in sys_cols)
    if sso_field_just_added:
        conn.execute(
            "ALTER TABLE systems ADD COLUMN sso_login_field TEXT NOT NULL DEFAULT 'account_name'"
        )
    if sys_cols or "sso_login_field" in (_table_cols(conn, "systems") or []):
        # 首次补列：北森→account_uid；空值补默认
        if sso_field_just_added:
            conn.execute(
                "UPDATE systems SET sso_login_field = 'account_uid' WHERE code = 'beisen'"
            )
        conn.execute(
            """UPDATE systems SET sso_login_field = CASE
              WHEN code = 'beisen' THEN 'account_uid' ELSE 'account_name' END
            WHERE sso_login_field IS NULL OR sso_login_field = ''"""
        )
    # AI-GEN-END
    conn.execute(
        """CREATE TABLE IF NOT EXISTS leorg_sync_state (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          last_mode TEXT,
          last_full_at TEXT,
          last_incr_at TEXT,
          last_change_id INTEGER DEFAULT 0,
          org_mapped INTEGER DEFAULT 0,
          emp_touched INTEGER DEFAULT 0,
          updated_at TEXT
        )"""
    )
    # AI-GEN-BEGIN
    conn.execute(
        """CREATE TABLE IF NOT EXISTS roles (
          code TEXT PRIMARY KEY,
          label TEXT NOT NULL,
          is_builtin INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 100,
          created_at TEXT
        )"""
    )
    ensure_roles_seeded(conn)
    # AI-GEN-BEGIN
    user_cols2 = _table_cols(conn, "users")
    if user_cols2 and "status" not in user_cols2:
        conn.execute(
            "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    sys_cols2 = _table_cols(conn, "systems")
    if sys_cols2 and "is_builtin" not in sys_cols2:
        conn.execute(
            "ALTER TABLE systems ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0"
        )
    ensure_leuc_system(conn)
    # AI-GEN-BEGIN
    msg_cols = _table_cols(conn, "messages")
    if msg_cols and "ref_type" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN ref_type TEXT")
    if msg_cols and "ref_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN ref_id INTEGER")
    ensure_todo_notify_trigger(conn)
    # AI-GEN-END


# AI-GEN-BEGIN
LEUC_SYSTEM_CODE = "leuc"


def ensure_todo_notify_trigger(conn: sqlite3.Connection) -> None:
    """创建 pending 待办时自动给办理人发可跳转的系统消息。"""
    conn.execute("DROP TRIGGER IF EXISTS todos_notify_pending")
    conn.execute(
        """CREATE TRIGGER todos_notify_pending
        AFTER INSERT ON todos
        WHEN NEW.bucket = 'pending' AND NEW.assignee_id IS NOT NULL
        BEGIN
          INSERT INTO messages
            (from_user_id, to_user_id, title, body, created_at, is_read, msg_type, ref_type, ref_id)
          VALUES (
            0,
            NEW.assignee_id,
            '待办：' || NEW.todo_type,
            '您有新待办「' || NEW.title || '」，点击办理。',
            datetime('now', 'localtime'),
            0,
            'system',
            'todo',
            NEW.id
          );
        END"""
    )


def ensure_leuc_system(conn: sqlite3.Connection) -> None:
    """内置本系统（LEUC）：业务系统管理可见，不可删/不可禁用。"""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    row = conn.execute(
        "SELECT id FROM systems WHERE code = ?", (LEUC_SYSTEM_CODE,)
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE systems SET is_builtin = 1, status = 'enabled',
              name = CASE WHEN name IS NULL OR name = '' THEN '本系统（LEUC）' ELSE name END
            WHERE code = ?""",
            (LEUC_SYSTEM_CODE,),
        )
        return
    conn.execute(
        """INSERT INTO systems
        (code, name, client_id, client_secret, redirect_uris, scopes, grant_types,
         token_endpoint_auth_method, require_pkce, access_mode, forbid_external,
         has_sensitive, sso_login_field, status, is_builtin, owner_user_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,NULL,?)""",
        (
            LEUC_SYSTEM_CODE,
            "本系统（LEUC）",
            "client_leuc",
            "sk_leuc_builtin_not_for_oidc",
            "",
            "openid profile",
            "authorization_code",
            "client_secret_post",
            0,
            "open",
            0,
            0,
            "account_name",
            "enabled",
            now,
        ),
    )
# AI-GEN-END


# AI-GEN-BEGIN
# 内置角色：code 固定（业务硬编码依赖）；可改显示名，不可删除
BUILTIN_ROLE_DEFS = [
    ("employee", "普通员工", 10),
    ("dept_owner", "部门负责人", 20),
    ("hr_specialist", "人事专员", 30),
    ("system_owner", "系统管理员", 40),
    ("super_admin", "超级管理员", 50),
    ("finance", "财务", 60),
]
BUILTIN_ROLE_CODES = {c for c, _, _ in BUILTIN_ROLE_DEFS}


def ensure_roles_seeded(conn: sqlite3.Connection) -> None:
    """补种内置角色目录（不覆盖已改显示名）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for code, label, sort in BUILTIN_ROLE_DEFS:
        conn.execute(
            """INSERT OR IGNORE INTO roles (code, label, is_builtin, sort_order, created_at)
            VALUES (?, ?, 1, ?, ?)""",
            (code, label, sort, now),
        )
    # 文案统一：组织→部门（仅旧默认显示名）
    conn.execute(
        "UPDATE roles SET label='部门负责人' WHERE code='dept_owner' AND label='组织负责人'"
    )
    # AI-GEN-BEGIN
    # 软补：北森消息菜单（不覆盖角色其它菜单配置）
    for role in ("hr_specialist", "system_owner", "super_admin"):
        conn.execute(
            "INSERT OR IGNORE INTO role_menus (role, menu_id) VALUES (?, 'oa_forms')",
            (role,),
        )
    # AI-GEN-END
    # 兼容旧演示角色：若库里仍有人占用则登记，否则忽略
    for code, label in (("employee_a", "普通员工A"), ("employee_b", "普通员工B")):
        n = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role=?", (code,)).fetchone()
        if n and n["c"]:
            conn.execute(
                """INSERT OR IGNORE INTO roles (code, label, is_builtin, sort_order, created_at)
                VALUES (?, ?, 1, 15, ?)""",
                (code, label, now),
            )


def load_role_labels(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT code, label FROM roles ORDER BY sort_order, code"
        ).fetchall()
        if rows:
            return {r["code"]: r["label"] for r in rows}
    except Exception:
        pass
    return dict(ROLE_LABELS)


def role_label_of(conn: sqlite3.Connection, code: str) -> str:
    if not code:
        return ""
    try:
        row = conn.execute("SELECT label FROM roles WHERE code=?", (code,)).fetchone()
        if row:
            return row["label"]
    except Exception:
        pass
    return ROLE_LABELS.get(code, code)
# AI-GEN-END


def backfill_demo_beisen_user_ids(conn: sqlite3.Connection) -> None:
    """演示账号补北森 UserID（已有值不覆盖）。admin 不补假 ID。"""
    # AI-GEN-BEGIN
    cols = _table_cols(conn, "users")
    if not cols or "beisen_user_id" not in cols:
        return
    demo_beisen = [
        ("sunli", "6100002"),
        ("zhangcai", "6100003"),
        ("zhaomin", "6100004"),
        ("liufang", "6100005"),
        ("huangwei", "6100006"),
        ("zhangsan", "6100101"),
        ("lisi", "6100102"),
        ("wangqiang", "6100103"),
        ("wujiu", "6100109"),
        ("xuhaohao", "6100201"),
    ]
    for uname, bid in demo_beisen:
        conn.execute(
            """UPDATE users SET beisen_user_id = ?
            WHERE username = ? AND (beisen_user_id IS NULL OR beisen_user_id = '')""",
            (bid, uname),
        )
    # AI-GEN-END


def init_db(force: bool = False) -> None:
    if force and DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect()
    conn.executescript(SCHEMA)
    migrate_schema(conn)
    n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    d = conn.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]
    # 仅空库播种；已有部门/同步数据时不再灌入演示账号
    if n == 0 and d == 0:
        seed(conn)
    backfill_demo_beisen_user_ids(conn)
    conn.commit()
    conn.close()


def seed(conn: sqlite3.Connection) -> None:
    # AI-GEN-BEGIN
    # 默认空部门根：人员/部门由 LeOrg 同步回填；仅保留系统超管 admin（不挂部门）
    conn.execute(
        "INSERT INTO departments (id, name, parent_id, owner_user_id, leorg_id) VALUES (1,?,?,NULL,NULL)",
        ("来酷科技", None),
    )
    root_id = 1
    btit_id = 1
    # 系统超管：全权限，不在「部门和人员」展示
    conn.execute(
        """INSERT INTO users
        (id, username, password, display_name, role, dept_id, phone, email, itcode,
         password_expire, account_expire, feishu_bound, wecom_bound,
         face_enrolled, fingerprint_enrolled, person_type,
         can_proxy_apply, can_set_account_expire, beisen_user_id, leorg_emp_id)
        VALUES (1, 'admin', '123456', '超级管理员', 'super_admin', NULL, NULL, NULL, 'admin',
                '2099-12-31', NULL, 0, 0, 0, 0, 'internal', 1, 1, NULL, NULL)"""
    )
    # AI-GEN-END
    # 角色默认菜单 / 能力（可在「角色」页改）
    menu_rows = []
    for role, menus in DEFAULT_ROLE_MENUS.items():
        for mid in menus:
            menu_rows.append((role, mid))
    conn.executemany("INSERT INTO role_menus (role, menu_id) VALUES (?,?)", menu_rows)
    cap_rows = []
    for role, caps in DEFAULT_ROLE_CAPS.items():
        for cap in caps:
            cap_rows.append((role, cap))
    if cap_rows:
        conn.executemany("INSERT INTO role_caps (role, cap_id) VALUES (?,?)", cap_rows)
    ensure_roles_seeded(conn)
    # 负责人：根部门暂无负责人；部门由 LeOrg 同步后配置
    # AI-GEN-BEGIN
    gaojia = None
    chang = None
    # AI-GEN-END
    global _SEED_ROOT_ID, _SEED_BTIT_ID
    _SEED_ROOT_ID = root_id
    _SEED_BTIT_ID = btit_id
    # AI-GEN-END

    conn.execute(
        """INSERT INTO messages (id, from_user_id, to_user_id, title, body, created_at, is_read, msg_type)
        VALUES
        (1, 0, 1, '系统通知', '欢迎使用 LEUC；请用 admin / 123456 登录后从 LeOrg 同步部门。', '2026-08-04 09:00:00', 0, 'system')
        """
    )

    # OIDC 客户端：统一回跳到业务系统导航页（按 app=code 区分）
    now = "2026-08-04T12:00:00"
    portal_cb = "http://127.0.0.1:5055/demo/home/callback"
    # AI-GEN-BEGIN
    # 末项 sso_login_field：北森用 account_uid，其它用 account_name
    systems = [
        # access_mode; forbid_external; has_sensitive; sso_login_field; is_builtin
        (1, "oa", "OA 办公", "client_oa", "sk_oa_demo_secret", f"{portal_cb}?app=oa", "openid profile email", "authorization_code", "client_secret_post", 1, "open", 0, 0, "account_name", "enabled", 0, None, now),
        (2, "bip", "BIP", "client_bip", "sk_bip_demo_secret", f"{portal_cb}?app=bip", "openid profile", "authorization_code", "client_secret_post", 1, "open", 0, 0, "account_name", "enabled", 0, None, now),
        (3, "laiku_erp", "来酷ERP", "client_laiku_erp", "sk_laiku_erp_secret", f"{portal_cb}?app=laiku_erp", "openid profile", "authorization_code", "client_secret_post", 1, "apply", 0, 1, "account_name", "enabled", 0, None, now),
        (4, "keji_erp", "科技ERP", "client_keji_erp", "sk_keji_erp_secret", f"{portal_cb}?app=keji_erp", "openid profile", "authorization_code", "client_secret_post", 1, "apply", 0, 1, "account_name", "enabled", 0, None, now),
        (5, "beisen", "北森", "client_beisen", "sk_beisen_demo_secret", f"{portal_cb}?app=beisen", "openid profile", "authorization_code", "client_secret_post", 1, "apply", 1, 1, "account_uid", "enabled", 0, None, now),
        (6, "feishu", "飞书", "client_feishu", "sk_feishu_demo_secret", f"{portal_cb}?app=feishu", "openid profile", "authorization_code", "client_secret_post", 1, "apply", 1, 0, "account_name", "enabled", 0, None, now),
        (7, "leuc", "本系统（LEUC）", "client_leuc", "sk_leuc_builtin_not_for_oidc", "", "openid profile", "authorization_code", "client_secret_post", 0, "open", 0, 0, "account_name", "enabled", 1, None, now),
    ]
    conn.executemany(
        """INSERT INTO systems
        (id, code, name, client_id, client_secret, redirect_uris, scopes, grant_types,
         token_endpoint_auth_method, require_pkce, access_mode, forbid_external, has_sensitive,
         sso_login_field, status, is_builtin, owner_user_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        systems,
    )
    # AI-GEN-END
    # AI-GEN-BEGIN
    # 空库仅超管；系统负责人由后续同步/分配配置
    # AI-GEN-END

    # 超管示例绑定（便于门户演示）
    accounts = [
        (1, 1, 3, "admin_laiku", "超管来酷", 1, 1, 1, "全部·敏感"),
        (2, 1, 4, "admin_keji", "超管科技", 1, 1, 1, "全部·敏感"),
        (3, 1, 1, "admin_oa", "超管OA", 1, 1, 0, "全部"),
    ]
    conn.executemany(
        """INSERT INTO user_system_accounts
        (id, user_id, system_id, account_name, account_label, is_default, can_login, has_sensitive, perm_summary)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        accounts,
    )
    # 徐好好不预绑来酷ERP，便于走「申请账号」测试链路
    # AI-GEN-BEGIN
    # AI-GEN-END

    # 敏感/外部人员审批链：直属 → 一级领导 → 财务（申请人=审批人时运行时跳过）
    conn.executemany(
        """INSERT INTO approval_chain_steps
        (id, flow_code, step_order, step_key, step_label, assignee_user_id, enabled)
        VALUES (?,?,?,?,?,?,?)""",
        [
            (1, "sensitive", 1, "direct_leader", "直属领导", None, 1),
            (2, "sensitive", 2, "level1_leader", "一级领导", None, 1),
            (3, "sensitive", 3, "finance", "财务", (chang["id"] if chang else 3), 1),
            (4, "external", 1, "direct_leader", "直属领导", None, 1),
            (5, "external", 2, "level1_leader", "一级领导", None, 1),
            (6, "external", 3, "finance", "财务", (chang["id"] if chang else 3), 1),
        ],
    )
    # 权限目录（系统管理员维护/同步；与「是否有敏感权限」复选框无关）
    conn.executemany(
        """INSERT INTO sensitive_perm_defs
        (id, system_id, perm_code, perm_name, description, parent_id, is_sensitive, enabled)
        VALUES (?,?,?,?,?,?,?,?)""",
        [
            (1, 3, "biz_root", "业务权限", "来酷ERP业务权限组", None, 0, 1),
            (2, 3, "order_edit", "订单编辑", "普通业务", 1, 0, 1),
            (3, 3, "cost_view", "成本查询", "来酷ERP成本数据", 1, 0, 1),
            (4, 3, "audit_ro", "审计只读", "来酷ERP审计账号", 1, 0, 1),
            (5, 4, "kj_root", "科技权限", "科技ERP权限组", None, 0, 1),
            (6, 4, "cost_query", "成本查询", "科技ERP成本", 5, 0, 1),
            (7, 5, "hr_root", "人事权限", "北森人事权限组", None, 0, 1),
            (8, 5, "salary_view", "薪酬查看", "北森薪酬", 7, 0, 1),
            (9, 1, "oa_root", "OA权限", "OA权限组", None, 0, 1),
            (10, 1, "oa_approve", "审批办理", "OA审批", 9, 0, 1),
        ],
    )

    # 部门架构待同步花名册（人事专员初始化用户）
    conn.executemany(
        """INSERT INTO hr_sync_roster
        (id, display_name, dept_id, phone, email, emp_no, source, status)
        VALUES (?,?,?,?,?,?, 'org_sync', 'pending')""",
        [
            (1, "刘一", _SEED_BTIT_ID, "13910000001", "liuyi@lecoo.com", "E1001"),
            (2, "陈二", _SEED_BTIT_ID, "13910000002", "chener@lecoo.com", "E1002"),
            (3, "张三", _SEED_BTIT_ID, "13910000003", "zhangsan.new@lecoo.com", "E1003"),
            (4, "赵六", _SEED_BTIT_ID, "13910000004", "zhaoliu@lecoo.com", "E1004"),
            (5, "孙丽", _SEED_ROOT_ID, "13910000005", "sunli2@lecoo.com", "E1005"),
        ],
    )

    # 子系统账号池（可同步/导入；部分未绑定供匹配演示）
    now_d = "2026-08-04"
    conn.executemany(
        """INSERT INTO system_accounts
        (id, system_id, account_uid, account_name, display_name, phone, email, itcode, status, leuc_user_id, source, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            # 已绑定
            (1, 3, "ERP-ZS-001", "zhangsan_laiku", "张三", "13800000001", "zhangsan@lecoo.com", "zhangsan", "bound", 1, "sync", now_d),
            (2, 3, "ERP-LS-001", "lisi_laiku_main", "李四", "13800000002", "lisi@lecoo.com", "lisi", "bound", 2, "sync", now_d),
            (3, 3, "ERP-LS-002", "lisi_laiku_audit", "李四审计", "13800000002", "lisi@lecoo.com", "lisi", "bound", 2, "sync", now_d),
            # 未绑定：可用手机/邮箱/姓名匹配到新同步用户或现有用户
            (4, 3, "ERP-LY-001", "liuyi_erp", "刘一", "13910000001", "liuyi@lecoo.com", "liuyi", "unbound", None, "sync", now_d),
            (5, 3, "ERP-CE-001", "chener01", "陈二", "13910000002", "chener@lecoo.com", None, "unbound", None, "import", now_d),
            (6, 4, "KJ-ZL-001", "zhaoliu_kj", "赵六", None, "zhaoliu@lecoo.com", "zhaoliu", "unbound", None, "sync", now_d),
            (7, 3, "ERP-OR-001", "orphan_erp", "待建用户", "13919999999", "orphan@lecoo.com", "orphan", "unbound", None, "import", now_d),
            # 北森：account_uid = BeisenUserID（数字）
            (8, 5, "630701809", "beisen_wujiu", "吴九", "13800000009", "wujiu@lecoo.com", "wujiu", "unbound", None, "sync", now_d),
        ],
    )

    todos = [
        (2, 1, 2, "部门同步确认", "系统治理", "pending", "open", "2026-08-02", None, None, None),
        (3, 4, 1, "来酷ERP secret 轮换确认", "系统治理", "pending", "open", "2026-08-03", None, None, None),
        (4, 2, 2, "部门同步确认", "系统治理", "initiated", "open", "2026-08-02", None, None, None),
    ]
    conn.executemany(
        """INSERT INTO todos
        (id, assignee_id, initiator_id, title, todo_type, bucket, status, created_at, application_id, step_order, meta)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        todos,
    )


# AI-GEN-BEGIN
# 菜单目录（角色配置页勾选）；实际生效读 role_menus 表
ALL_MENUS = [
    {"id": "home", "label": "个人中心", "group": "个人"},
    {"id": "security", "label": "安全管理", "group": "个人"},
    {"id": "todo", "label": "我的待办", "group": "个人"},
    {"id": "apply", "label": "自助申请", "group": "个人"},
    {"id": "my_org", "label": "部门和人员", "group": "个人"},
    {"id": "my_systems", "label": "业务系统管理", "group": "业务系统"},
    {"id": "sys_accounts", "label": "系统账号管理", "group": "业务系统"},
    {"id": "oa_forms", "label": "北森消息", "group": "业务系统"},
    {"id": "admin_sensitive", "label": "敏感审批链", "group": "系统设置"},
    {"id": "admin_roles", "label": "角色与权限", "group": "系统设置"},
]

# 按钮权限（挂在菜单下；写入 role_caps）
ALL_BUTTONS = [
    {"id": "manage_all_org", "label": "管理全部部门", "menu": "my_org"},
    {"id": "org_add", "label": "添加人员", "menu": "my_org"},
    {"id": "org_import", "label": "导入人员", "menu": "my_org"},
    {"id": "org_sync", "label": "部门同步", "menu": "my_org"},
    {"id": "org_set_owner", "label": "设置部门负责人", "menu": "my_org"},
    {"id": "proxy_apply", "label": "代人申请账号/权限", "menu": "my_org"},
    {"id": "direct_bind", "label": "直接绑定", "menu": "my_org"},
    {"id": "set_account_expire", "label": "设置账号有效期", "menu": "my_org"},
    {"id": "manage_systems", "label": "维护业务系统", "menu": "my_systems"},
    {"id": "sys_add", "label": "添加系统", "menu": "my_systems"},
    {"id": "sys_perm_edit", "label": "维护权限目录", "menu": "my_systems"},
    {"id": "sys_acct_sync", "label": "同步/导入账号池", "menu": "sys_accounts"},
    {"id": "config_roles", "label": "配置角色菜单/按钮", "menu": "admin_roles"},
    {"id": "role_assign", "label": "分配人员角色", "menu": "admin_roles"},
    {"id": "sensitive_config", "label": "配置敏感审批链", "menu": "admin_sensitive"},
]
ALL_CAPS = ALL_BUTTONS  # 兼容旧名

# 默认菜单（写入 role_menus；兼容旧 ROLE_MENUS 读取）
DEFAULT_ROLE_MENUS = {
    "employee": ["home", "security", "todo", "apply", "my_org"],
    "finance": ["home", "security", "todo", "apply", "my_org"],
    "hr_specialist": ["home", "security", "todo", "apply", "my_org", "oa_forms"],
    "system_owner": ["home", "security", "todo", "apply", "my_org", "my_systems", "sys_accounts", "oa_forms"],
    "super_admin": [
        "home", "security", "todo", "apply", "my_org",
        "my_systems", "sys_accounts", "oa_forms",
        "admin_sensitive", "admin_roles",
    ],
    "employee_a": ["home", "security", "todo", "apply", "my_org"],
    "employee_b": ["home", "security", "todo", "apply", "my_org"],
    "dept_owner": ["home", "security", "todo", "apply", "my_org"],
}

DEFAULT_ROLE_CAPS = {
    "employee": [],
    "finance": [],
    "hr_specialist": [
        "manage_all_org", "org_add", "org_import", "org_sync", "org_set_owner",
        "proxy_apply", "direct_bind", "set_account_expire",
    ],
    "system_owner": ["manage_systems", "sys_perm_edit", "sys_acct_sync"],
    "super_admin": [
        "manage_all_org", "org_add", "org_import", "org_sync", "org_set_owner",
        "proxy_apply", "direct_bind", "set_account_expire",
        "manage_systems", "sys_add", "sys_perm_edit", "sys_acct_sync",
        "config_roles", "role_assign", "sensitive_config",
    ],
    "dept_owner": ["org_add", "org_import", "org_set_owner", "proxy_apply", "set_account_expire"],
}

ROLE_MENUS = DEFAULT_ROLE_MENUS  # 兼容旧引用；运行时优先 DB

ROLE_LABELS = {
    "employee": "普通员工",
    "employee_a": "普通员工",
    "employee_b": "普通员工",
    "dept_owner": "部门负责人",
    "hr_specialist": "人事专员",
    "system_owner": "系统管理员",
    "super_admin": "超级管理员",
    "finance": "财务",
}
# AI-GEN-END
