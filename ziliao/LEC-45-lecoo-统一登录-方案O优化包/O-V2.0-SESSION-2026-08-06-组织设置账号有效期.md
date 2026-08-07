# 会话：我的组织 · 设置账号有效期

日期：2026-08-06

## 做了什么

- 「设置账号有效期」仅在**角色开通**（人事专员 / 超管）或**人员开通**（`can_set_account_expire=1`）时可见
- 演示人员开通：`wangqiang`
- 支持指定到期日 / **永不过期**（`account_expire = NULL`）
- API：`POST /api/org/members/account-expire`（须可管目标人员）
- 文档：`V2.0/用户中心功能模块整理-O-V2.1.md`

## 手测

- `zhangsan`：无入口、接口 403
- `wangqiang` / `sunli`：有入口；可设日期与永不过期
