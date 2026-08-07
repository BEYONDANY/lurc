# 会话：子系统账号池唯一标识

日期：2026-08-07

## 改动
- `system_accounts.account_uid`：系统内唯一标识
- 唯一索引：`(system_id, account_uid)`
- 存量用 `account_name` 回填
- 导入 CSV：`唯一标识,账号名,姓名,手机,邮箱,itcode`（兼容旧 5 列）
- 列表 / 搜索 / 开通选账号 / 待确认匹配展示 uid

## 验证
- 迁移后无空 uid；同 uid upsert 更新不新增；唯一约束生效
