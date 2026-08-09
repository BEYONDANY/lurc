# PostgreSQL 初始化快照

`01-leuc-data.sql.gz` 为当前环境全量库导出（含部门、人员、角色、账号绑定等）。

Postgres 官方镜像**仅在数据卷首次创建时**自动执行本目录脚本。

## 拉取后带数据启动

```bash
cd leuc-proto
docker compose down -v          # 清空旧卷，才能导入快照
docker compose up --build -d
```

## 重新导出快照（维护用）

```bash
# 需已有运行中的 leuc-pgsql
./scripts/export_pg_snapshot.sh
# Windows PowerShell 可用：
# docker exec leuc-pgsql pg_dump -U leuc -d leuc --no-owner --no-acl -f /tmp/dump.sql
# 再去掉 \restrict 行后 gzip 到本目录
```
