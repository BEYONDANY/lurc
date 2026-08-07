#!/bin/sh
# AI-GEN-BEGIN
set -e
cd /app
mkdir -p /app/data

# 可选：首次强制重建种子（危险，默认关闭）
if [ "${LEUC_FORCE_INIT}" = "1" ]; then
  echo "[leuc] LEUC_FORCE_INIT=1 → 重建 SQLite 种子库"
  python -c "from db import init_db; init_db(force=True)"
fi

echo "[leuc] Docker 启动 http://0.0.0.0:${LEUC_PORT:-5055}"
exec "$@"
# AI-GEN-END
