# 一键关账（代关他人）

## 需求
- 仅代关他人：选人、上传凭证/附件、可指定生效时间
- 有 `close_api_url`：接口直关；无接口：生成系统管理员待办
- 保留审批记录与各系统执行标识（api_ok / todo_pending 等）
- 可按「上次关闭批次」或「全部关联」恢复

## 改动
- `leuc_bulk_close.py`：批次/明细/附件/事件表
- `app.py`：`execute_proxy_bulk_close` / restore；HTTP：
  - `POST /api/apply/bulk-close`（multipart）
  - `POST /api/apply/bulk-restore`
  - `GET /api/apply/bulk-close/records`、`/<id>`、`/<id>/run`
  - 待办类型「一键关账」decide 回写 `todo_done` / `todo_rejected`
- `static/index.html`：自助申请「一键关账/恢复」弹窗；业务系统可配 `close_api_url`
- `pgcompat.py`：`system_settings` 加入 `_NO_RETURNING_ID`（修复启动崩溃）

## 验证
- 容器内冒烟：代关 xuhaohao → 有接口直关；清空 OA 的 `close_api_url` 后生成待办 #46
- Docker 已重建：`http://127.0.0.1:5055`
