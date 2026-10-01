#!/usr/bin/env bash
# 简谱旋律查歌 —— 起**写后端**（FastAPI 版）。
#   ./run-api.sh          起在 8770
#   ./run-api.sh 8790     换端口
#
# 依赖用 uv 管（pyproject.toml + uv.lock）: 有 uv 就用 `uv run`（自动按 lock 建环境），
# 没有就退回系统 python + 已装的包。
# 旧的标准库版随时可退回:  python3 app/server.py 8770
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8770}"
if [ -z "${JPSUBMIT_TOKEN:-}" ]; then
  echo "[!] JPSUBMIT_TOKEN 没设 —— 这个端口的写接口对谁都开放（仅本机/内网可接受）"
fi
if command -v uv >/dev/null 2>&1; then
  echo "[i] 用 uv（pyproject.toml + uv.lock）"
  exec uv run python app/api.py "$PORT"
else
  echo "[i] 没装 uv，用系统 python"
  exec python3 app/api.py "$PORT"
fi
