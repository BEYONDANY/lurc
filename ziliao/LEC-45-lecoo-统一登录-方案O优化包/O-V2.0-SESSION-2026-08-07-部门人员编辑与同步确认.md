# 会话记录 · 2026-08-07 · 部门人员编辑与同步确认

## 需求

- 部门和人员：可编辑人员（姓名、部门、手机、邮箱、状态；关闭后可再打开）
- 部门：可改名、同级调整顺序
- 权限与现有「可管本级及下级」一致（HR/超管全部）
- 同步人员和部门时先对比变化，确认后才接受写入

## 改动

- `leuc-proto/db.py`：`departments.sort_order`；`leorg_sync_draft` 草稿表
- `leuc-proto/app.py`
  - `PATCH /api/org/members/<id>`
  - `PATCH /api/org/departments/<id>`、`POST .../move`
  - `POST /api/hr/sync-pull` 默认 `preview=true` 出 diff 草稿
  - `POST /api/hr/sync-apply` 按勾选 keys 落库
- `leuc-proto/static/index.html`：编辑人员弹窗；部门改名/上下移；同步差异确认弹窗

## 手测

- admin 编辑人员手机、关闭再打开成功
- 部门改名、同级上移成功
- `sort_order` / `leorg_sync_draft` 迁移生效

## 原型

http://127.0.0.1:5055 · 密码 `123456`
