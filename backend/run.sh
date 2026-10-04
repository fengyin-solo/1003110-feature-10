#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 虚拟环境可能是从别的机器拷来的（python 软链失效），探测到不可用就重建；
# 个别环境下旧目录删不干净，改放到 .venv-broken-* 而不是硬删。
VENV=.venv
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import fastapi" >/dev/null 2>&1; then
  if [ -e "$VENV" ]; then
    mv "$VENV" ".venv-broken-$(date +%s)" 2>/dev/null || true
  fi
  if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/pip" install -q -r requirements.txt
fi
exec "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000
