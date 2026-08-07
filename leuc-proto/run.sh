#!/bin/zsh
# AI-GEN-BEGIN
# 强制 arm64 + 项目 .venv，避免 Rosetta(x86_64) 加载用户目录 arm64 cryptography 失败
set -e
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1
export LEUC_REEXEC=1

PY_BASE="/Library/Developer/CommandLineTools/usr/bin/python3"
if [[ ! -x "$PY_BASE" ]]; then
  PY_BASE="$(command -v python3)"
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "[leuc] 创建 .venv (arm64)…"
  arch -arm64 "$PY_BASE" -m venv .venv
  arch -arm64 .venv/bin/python -m pip install -U pip
  arch -arm64 .venv/bin/python -m pip install -r requirements.txt
fi

# 自检：必须能 import cryptography，且进程为 arm64
if ! arch -arm64 .venv/bin/python -c "import platform,cryptography; assert platform.machine()=='arm64'; from beisen_sso import status_dict; assert status_dict()['enabled']" 2>/tmp/leuc-boot-check.err; then
  echo "[leuc] 北森 SSO 依赖自检失败，请查看 /tmp/leuc-boot-check.err"
  cat /tmp/leuc-boot-check.err >&2 || true
  echo "[leuc] 尝试重装 cryptography…"
  arch -arm64 .venv/bin/python -m pip install --force-reinstall --no-cache-dir 'cryptography>=42'
  arch -arm64 .venv/bin/python -c "import platform,cryptography; assert platform.machine()=='arm64'; from beisen_sso import status_dict; assert status_dict()['enabled']"
fi

echo "[leuc] 使用 arm64 .venv 启动 http://127.0.0.1:5055"
exec arch -arm64 .venv/bin/python app.py
# AI-GEN-END
