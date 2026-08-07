# 会话记录 · 角色清单可配置（2026-08-07）

## 目标

「角色与权限」中角色不再写死，支持新增 / 改名 / 删除自定义角色。

## 改动摘要

- `db.py`：新增 `roles` 表；内置角色种子；`role_label_of` / `ensure_roles_seeded`
- `app.py`：`GET/POST /api/admin/roles`，`PATCH/DELETE /api/admin/roles/<code>`；删除自定义角色时人员迁回 `employee`
- `static/index.html`：左侧「新增角色」；右侧改名/删除；添加人员下拉读角色目录

## 约定

- 内置角色（employee / dept_owner / hr_specialist / system_owner / super_admin / finance）：可改显示名，不可删
- 自定义角色：可删；有人占用则先改回普通员工再删

## 手测

1. 超管进入角色与权限 → 新增「门店店长」→ 出现在列表并可配权限
2. 改名内置「普通员工」→ 显示名变更，code 不变
3. 给某人分配自定义角色后删除该角色 → 人回到普通员工
4. 删除「超级管理员」→ 应被拒绝
