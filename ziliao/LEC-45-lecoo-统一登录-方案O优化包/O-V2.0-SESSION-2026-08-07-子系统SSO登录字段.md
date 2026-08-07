# 会话：子系统 SSO 登录字段

- 日期：2026-08-07
- 范围：`leuc-proto`

## 改动

1. `systems.sso_login_field`：`account_uid|account_name|email|phone|itcode`；北森默认 `account_uid`。
2. 业务系统管理可改 SSO 登录字段；账号管理支持添加/导入/新建绑定（必填唯一标识）。
3. 北森 SSO `sub`：**仅**用已申请开通且可登录的账号池字段；**不用**通讯录 `users.beisen_user_id`。

## 验收

- 北森 `sso_login_field=account_uid`，标签「北森用户ID」
- 仅有通讯录北森ID、未绑定账号池 → SSO 拒绝
- 账号池已绑 + `can_login=1` → `sub` 取池内 `account_uid`
