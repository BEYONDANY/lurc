# O-V2.0 会话 · 北森账号真实同步（2026-08-07）

## 需求

系统账号管理里北森「模拟同步」改为真实同步，用组织人员数据（[LeOrg API](https://leorg-ai.lecoosys.com/api/docs/)）。

## 实现

1. `POST /api/sys-accounts/sync`（兼容旧 `sync-demo`）
2. 北森：`GET /v1/employees`（在职+试用）取 `beisen_id` → `system_accounts.account_uid` 幂等写入
3. LeOrg 不可用时降级：本系统 `users.beisen_user_id` 非空人员
4. 前端按钮「同步北森账号」，去掉模拟文案

## 验证

- 实测 LeOrg 扫描约 942 人，首次新增 941、更新 1；再次同步全量更新
