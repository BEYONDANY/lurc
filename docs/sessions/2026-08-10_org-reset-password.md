# 部门和人员：按角色直接改密码

## 背景
在「部门和人员」为有权限角色增加直接修改人员密码；不另做改密审批流。角色通过现有账号/权限申请或角色配置获得按钮权限。

## 改动
- `db.py`：新增按钮 `org_reset_password`（修改人员密码）；默认给 super_admin / hr_specialist / dept_owner，并软补种
- `app.py`：`POST /api/org/members/reset-password`（校验按钮权限 + 可管范围）；写审计、发信记录、系统消息
- `leuc_ops.py`：重置密码发信文案区分
- `static/index.html`：可管人员行显示「改密码」，弹窗设密/随机生成

## 验证
- 语法：app/db/leuc_ops AST 通过
- 手测：admin 进部门和人员 → 可管人员「改密码」→ 设新密码后可用新密码登录；无该按钮角色看不到入口
- 角色页可勾选「修改人员密码」按钮
