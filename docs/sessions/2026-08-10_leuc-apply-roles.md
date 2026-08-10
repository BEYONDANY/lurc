# 本系统申请改为选角色

## 背景
账号/权限申请里「本系统（LEUC）」权限目录是过时硬编码，与「角色与权限」不一致。

## 方案
采用选角色（A）：与 RBAC 同源；审批通过后合并写入 `user_roles`。范围：全部内置+自定义角色（隐藏 employee_a/b）。

## 改动
- `app.py`：`/api/bind/systems`、业务系统管理对 LEUC 返回 `apply_mode=roles` + roles；新增 `leuc_roles` 申请与审批生效 `add_user_roles`
- `leuc_approval_ext.py`：详情展示申请角色
- `static/index.html`：申请弹窗对本系统勾选角色；业务系统管理展示本系统角色目录

## 验证
- AST 通过
- 手测：自助申请 → 本系统 → 选择角色 → 提交 → 直属审批通过后用户角色生效；业务系统管理 · LEUC · 本系统角色与角色页一致
