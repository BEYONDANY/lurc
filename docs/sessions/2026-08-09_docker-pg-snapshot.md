# 会话：Docker 附带全量 PostgreSQL 快照

## 目标

拉取代码后可通过 `docker compose` 启动，并自动带上当前全部业务数据。

## 做法

- 导出 `docker/init/01-leuc-data.sql.gz`（约 915 用户 / 275 部门）
- `docker-compose.yml` 将 `docker/init` 挂到 Postgres `docker-entrypoint-initdb.d`
- 首次建卷时自动导入；已有卷需 `docker compose down -v` 后重建

## 验证

`down -v && up --build -d` 后：`users=915`，`liyang=李杨`，`liyang2=黎洋`。
