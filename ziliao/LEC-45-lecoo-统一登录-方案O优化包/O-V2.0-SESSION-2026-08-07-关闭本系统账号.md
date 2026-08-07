# O-V2.0 会话 · 关闭本系统账号（2026-08-07）

## 背景

离职审批等场景需要关闭 **本系统（LEUC）** 登录账号；业务系统管理需展示「本系统」且不可删除/禁用。

## 改动摘要

1. **内置系统** `systems.code=leuc`（本系统（LEUC）），`is_builtin=1`，业务系统管理可见，不可禁用。
2. **`users.status`**：`active` / `closed`；关闭后禁止登录与 `switch-role`。
3. **离职模拟** `POST /api/oa/simulate-leave`：先关业务系统可登录账号，**末步**调用 `close_leuc_user` 关本系统。
4. **自助关闭**：`/api/apply/my-accounts` 自动补本系统账号行，可选关闭 LEUC。
5. 申请/绑定/门户系统列表 **排除** leuc（非 OIDC 业务接入）。

## 涉及文件

- `leuc-proto/db.py`
- `leuc-proto/app.py`
- `leuc-proto/static/index.html`

## 验证

- migrate 后存在 leuc 内置系统
- 禁用 leuc → 400「不可禁用或删除」
- 离职末步 → `users.status=closed`，再登录 → 403 `account_closed`
