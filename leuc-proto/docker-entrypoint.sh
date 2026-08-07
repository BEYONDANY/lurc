#!/bin/sh
# AI-GEN-BEGIN
set -e
cd /app
mkdir -p /app/data

echo "[leuc] 等待 PostgreSQL ${DATABASE_URL:-postgresql://leuc:leuc@db:5432/leuc}"
i=0
until python -c "import psycopg,os; psycopg.connect(os.environ.get('DATABASE_URL','postgresql://leuc:leuc@db:5432/leuc')).close()" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "[leuc] PostgreSQL 等待超时"
    exit 1
  fi
  sleep 1
done
echo "[leuc] PostgreSQL 已就绪"

# 可选：首次强制重建种子（危险，默认关闭）
if [ "${LEUC_FORCE_INIT}" = "1" ]; then
  echo "[leuc] LEUC_FORCE_INIT=1 → 重建 PostgreSQL 种子库"
  python -c "from db import init_db; init_db(force=True)"
fi

echo "[leuc] Docker 启动 http://0.0.0.0:${LEUC_PORT:-5055}"
exec "$@"
# AI-GEN-END
