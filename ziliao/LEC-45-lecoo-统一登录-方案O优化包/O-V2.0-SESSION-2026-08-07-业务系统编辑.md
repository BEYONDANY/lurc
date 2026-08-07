# 会话记录 · 2026-08-07 · 业务系统管理可编辑

## 需求

- 业务系统管理支持编辑，交互同「添加系统」
- `code`、`client_id` 不可变

## 改动

- `leuc-proto/app.py`：`PATCH/PUT /api/admin/systems/<sid>`，可改名称/回调/准入/外部登录/SSO字段/管理员；拒绝改 code、client_id；超管才可改 owners
- `leuc-proto/static/index.html`：复用 `m-add-sys`；`openEditSys` 锁定 code/client；状态卡「编辑」按钮；`submitAddSys` 编辑走 PATCH

## 手测

- 系统负责人/超管点「编辑」→ 改名称或回调 → 保存成功
- 编辑态 code / client_id 只读；接口传入不同值返回 400
- 非超管编辑不展示管理员多选

## 原型

http://127.0.0.1:5055 · 密码 `123456`
