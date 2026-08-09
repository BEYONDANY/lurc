# 会话：外部人员角色与菜单权限刷新

## 目标

- 刷新「角色与权限」可选菜单/按钮目录（含离职关账等现网项）
- 新增内置角色「外部人员」（`external`），默认仅「个人中心」「安全管理」
- 新建/存量 `person_type=external` 绑定该角色

## 改动

- `db.py`：`ALL_MENUS` / `ALL_BUTTONS` / `DEFAULT_ROLE_*` / `BUILTIN_ROLE_DEFS`；`ensure_roles_seeded` 软补菜单按钮并迁移外部人员
- `app.py`：`row_user` 对外部角色收敛菜单；新建外部人员默认 `role=external`
- `static/index.html`：新建外部人员默认角色与说明文案

## 验证

- Docker 内查询：`external` 标签为「外部人员」，菜单为 `home` + `security`
- `GET /api/admin/roles`：角色列表含外部人员；`all_menus` / `all_buttons` 中文正常
