# 会话记录 · 2026-08-07 · 北森 SSO 多账号选择

## 问题

徐好好 SSO 跳转北森报错：
`Argument not positive. Value: (0)`
（见 https://oapi.italent.cn/SSO/Error?...）

## 根因

一人绑定 2 个可登录北森账号：
- `sync_demo_b` / `SYNC-B-001`（演示，非正整数）← 旧逻辑默认选中
- `xuhh2` / `630702408`（真实 BeisenUserID）

`uty=id` 时北森把非法 sub 解析成 0。

## 改动

- 多账号必须先选择；`/beisen/sso/go` 出选择页
- `uty=id` 校验 sub 为正整数
- `POST /api/beisen/sso/launch` 支持 `account_id`；未选返回 `need_choose`
- `GET /api/beisen/sso/accounts` 列可选账号

## 手测（徐好好）

1. 登录 xuhaohao → `/demo/home` 点北森 → 出现选择页
2. 选 `xuhh2` / `630702408` → 进入 italent
3. 选 `SYNC-B-001` → 提示 ID 非法，不跳转
