# 会话记录 · 角色菜单 + 按钮权限可配置（2026-08-06）

## 目标

角色与权限管理做成可配置；权限除菜单外支持**按钮**级控制。

## 改动摘要

- `db.py`：`ALL_BUTTONS`（挂菜单）写入 `role_caps`；`DEFAULT_ROLE_CAPS` 按角色种子按钮
- `app.py`：`row_user` 返回 `buttons`；`GET /api/admin/roles` 返回 `all_buttons`；种子检测 `org_add` 触发重建
- `static/index.html`：侧栏合并为「角色与权限」；配置页勾选菜单 + 按菜单分组的按钮；`hasBtn` 控制组织/系统页操作显隐
- `V2.1` 文档同步说明

## 手测

1. `chenchao` 登录 → 系统设置 → 角色与权限
2. 勾选/取消某角色按钮（如人事 `proxy_apply`）保存
3. 换 `sunli` 登录，确认「代人申请」等按钮随配置显隐
