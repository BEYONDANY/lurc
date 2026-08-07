# 会话记录 · 系统负责人开通录入账号（2026-08-07）

## 改动

- 新建账号申请到「系统负责人开通」时，待办显示 **开通**（非直接通过）
- 弹窗录入：业务系统账号名（必填）、备注；展示 todo/app/申请人/系统等 ID
- `POST /api/todo/<id>/decide` 支持 `account_name` / `remark`；开通后关联 `system_accounts` + `user_system_accounts`

## 手测

`gaojia` → 我的待办 → 开通 → 填账号与备注 → 确认；申请人个人中心可见新账号。
