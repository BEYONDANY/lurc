# O-V2.0 会话 · 聊天改消息与待办跳转（2026-08-07）

## 需求

1. 界面「聊天」统一改为「消息」
2. 产生待办时系统发消息，可点击进入详情办理

## 改动

1. UI：右下角 FAB、面板标题、部门和人员入口等「聊天」→「消息」
2. `messages.ref_type` / `ref_id`；触发器 `todos_notify_pending`：插入 `bucket=pending` 待办时自动发系统消息
3. 右下角提醒与系统消息气泡可点「去办理」→ 打开流程详情并带通过/驳回（或开通）

## 文件

- `leuc-proto/db.py`
- `leuc-proto/app.py`
- `leuc-proto/static/index.html`
