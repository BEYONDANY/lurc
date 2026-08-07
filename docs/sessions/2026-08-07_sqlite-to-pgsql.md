# 会话：SQLite 迁移 PostgreSQL

- 日期：2026-08-07
- 范围：`leuc-proto` 数据层与 Docker Compose

## 目标

弃用 SQLite，改为独立 PostgreSQL 容器。

## 实现

1. `docker-compose.yml` 增加 `db`（`postgres:16-alpine`，容器名 `leuc-pgsql`，卷 `leuc_pgdata`）
2. 新增 `pgcompat.py`：兼容原 sqlite3 调用（`?` / `INSERT OR IGNORE` / `lastrowid` / `PRAGMA table_info`）
3. `db.py` 改连 Postgres，SCHEMA 使用 IDENTITY，待办通知改为 PG 函数触发器
4. `requirements.txt` 增加 `psycopg[binary]`；entrypoint 等待数据库就绪

## 验证

- `leuc-pgsql` healthy，`leuc-proto` healthy
- `admin/123456` 登录 200，`/api/chat/poll` 200
