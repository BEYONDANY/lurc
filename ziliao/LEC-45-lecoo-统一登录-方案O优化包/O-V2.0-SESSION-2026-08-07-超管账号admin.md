# 会话：超管账号 admin（全权限、不在组织）

- 日期：2026-08-07
- 范围：`leuc-proto`

## 需求

- 超管登录名：`admin` / 密码 `123456`
- 拥有全部菜单与按钮权限
- **不在「我的组织」人员列表中显示**（也不挂部门、不做部门负责人）

## 改动

1. `app.py`：`SYSTEM_ADMIN_USERNAME` + `ensure_system_admin()`（启动/`ensure_db` 兜底）
2. `row_user`：`admin` / `super_admin` 强制 `ALL_MENUS` + `ALL_BUTTONS`
3. 组织与人事列表过滤：`/api/org/overview`、`/api/hr/users` 排除 `admin`
4. 清空组织时保留 `admin`，并强制 `dept_id=NULL`
5. 手工建人禁止用户名 `admin`
6. `db.py` 空库种子仅建 `admin`（不挂部门）；去掉假北森 ID 回填
7. 登录页脚文案更新

## 验证

- 登录 `admin`/`123456` 成功，菜单 9、按钮 14
- `/api/org/overview` 成员中无 `admin`
