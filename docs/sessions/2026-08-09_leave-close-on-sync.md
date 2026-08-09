# 会话：增量同步离职自动关账

## 需求
增量同步发现在职→离职时立即关闭本系统及全部绑定账号；独立关闭记录列表；子系统侧尽量回调留痕。

## 实现
- `close_user_for_leave`：关 LEUC + 全部 `user_system_accounts`，写 `leave_close_records/items`
- 未配置 `systems.close_api_url` 时写入 `subsystem_close_inbox`（模拟子系统回执）
- 增量同步 `_sync_leorg_employees` / 预览确认 apply 接入
- 菜单「离职关账记录」+ API `/api/admin/leave-closes`

## 验证
手动对 xuhaohao 关账：5 个账号关闭，inbox=4；API 列表/详情可读；随后 reopen 恢复演示账号。
