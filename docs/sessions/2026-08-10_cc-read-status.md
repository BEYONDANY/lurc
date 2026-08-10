# 会话：知会确认独立状态与已读标记

## 改动
1. 知会待办 status：`unread` → `read`（兼容旧 `open`/`approved`）
2. 确认已阅写入 `meta.read_at`；不可驳回
3. 流程详情增加 `cc_dimension`（待阅/已阅汇总）
4. 待办列表与详情：知会 pill、已阅按钮、知会维度区块
5. 修复：`build_cc_dimension` 去掉 SQL `LIKE '%…%'`（psycopg 把 `%` 当占位符导致切徐好好 `/api/home` 500）

## 文件
- `leuc-proto/leuc_approval_ext.py`
- `leuc-proto/app.py`
- `leuc-proto/static/index.html`
