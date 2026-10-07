#!/usr/bin/env bash
# POSIX counterpart to start_backend.ps1, for Linux/macOS and containers.
#
#   ./scripts/start_backend.sh              # development, with autoreload
#   MODE=production ./scripts/start_backend.sh
#
# Environment:
#   PORT              listen port (default 8000)
#   MODE              "development" (default) or "production"
#   UVICORN_WORKERS   worker count in production mode (default 1)
#
# On worker count, see README section 7.7: the auth rate limiter keeps its
# counters in process memory, so N workers multiply the effective limit by N.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-8000}"
MODE="${MODE:-development}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

# Prefer the project virtualenv when present, otherwise fall back to whatever
# python is active — which is the normal case inside a container.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    PYTHON="python"
fi

if ! "$PYTHON" -c "import backend.main" >/dev/null 2>&1; then
    echo "error: cannot import backend.main with $PYTHON" >&2
    echo "hint:  pip install -r backend/requirements.txt" >&2
    exit 1
fi

if [ "$MODE" = "production" ]; then
    echo "Starting backend on port $PORT with $UVICORN_WORKERS worker(s)."
    exec "$PYTHON" -m uvicorn backend.main:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        --workers "$UVICORN_WORKERS"
else
    # --reload is development-only: it spawns a file watcher and is unsafe and
    # slow under load.
    echo "Starting backend on port $PORT with autoreload (development)."
    exec "$PYTHON" -m uvicorn backend.main:app \
        --host 127.0.0.1 \
        --port "$PORT" \
        --reload
fi
