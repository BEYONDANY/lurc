# 会话：本系统申请仅权限不可新建账号

## 改动
- 账号/权限申请选本系统（LEUC）时：禁用「新建账号」，自动绑定人员登录账号
- 提交校验：本系统必须选权限，禁止 create_new
- `/api/bind/user-bound-accounts` 自动 `ensure_user_leuc_account`
- `/api/bind/apply` 拒绝对本系统的新建账号申请
- 角色配置：`org_add` / `proxy_apply` 按钮挂回「部门和人员」（页面入口仍在自助申请）

## 文件
- `leuc-proto/static/index.html`
- `leuc-proto/app.py`
- `leuc-proto/db.py`
