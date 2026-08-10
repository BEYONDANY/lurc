# 修复同步部门和人员失败

## 根因

1. `app.py` 使用了 `ensure_email_suffix_allowed` / `list_system_settings` 等，但未从 `db` 导入 → 同步任务 `NameError` 中断
2. LeOrg 接口手机号均为脱敏（如 `136****1644`），此前「缺手机即跳过建用户」导致人员几乎全跳过

## 修复

- 补全 `db` 导入；重建容器
- 同步建用户：无可用手机仍建档（phone 空）+ warn 报警，不整批失败
- 默认邮箱后缀增加 `@lenovo.com`

## 验证

- 任务 run#20/#21 状态 `ok`（修复导入后）
