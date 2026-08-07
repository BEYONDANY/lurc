# 会话：账号延期与申请对齐

日期：2026-08-07

## 改动
- 延期入口改用 `m-batch-grant` 大面板（天数、关联账号敏感提示、审批链预览）
- 无敏感：`account_extend` 直属一步 application
- 含敏感（任一可登录业务账号 `has_sensitive=1`）：`account_extend_sensitive` 直属→一级→财务
- 终审通过后延长 `users.account_expire`；兼容旧单步待办

## 验证
- 徐好好无敏感延期 → 马宁通过 → 有效期更新
- 含敏感预览/提交链：马宁 → 吴锦志 → 常明明
