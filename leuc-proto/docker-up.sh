#!/usr/bin/env bash
# AI-GEN-BEGIN
# 本机一键 Docker 启动 LEUC 原型
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker，请先安装 Docker Desktop / Docker Engine"
  exit 1
fi

if [[ ! -f .env && -f .env.example ]]; then
  echo "[leuc] 未找到 .env，已从 .env.example 复制（可按需填写）"
  cp .env.example .env
fi

mkdir -p data
echo "[leuc] docker compose up --build -d"
docker compose up --build -d
echo
echo "已启动：http://127.0.0.1:${LEUC_PORT:-5055}"
echo "日志：docker compose logs -f"
echo "停止：docker compose down"
# AI-GEN-END
