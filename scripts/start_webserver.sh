#!/bin/bash
# Web server launcher for launchd — sources .env then starts uvicorn.
# launchd does not inherit shell environment, so API keys must be loaded here.
#
# Paths are self-resolved from this script's location, so the file is
# portable across machines (no hard-coded /Volumes / /Users paths).
# Override via env vars BIO_DB_ROOT / VENV if your layout differs.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIO_DB_ROOT="${BIO_DB_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV="${VENV:-$BIO_DB_ROOT/.venv}"

if [ ! -x "$VENV/bin/uvicorn" ]; then
    echo "ERROR: uvicorn not found at $VENV/bin/uvicorn" >&2
    echo "Set VENV env var or symlink \$BIO_DB_ROOT/.venv to your venv." >&2
    exit 1
fi

mkdir -p "$BIO_DB_ROOT/logs"

if [ -f "$BIO_DB_ROOT/.env" ]; then
    set -a
    source "$BIO_DB_ROOT/.env"
    set +a
fi

cd "$BIO_DB_ROOT"
exec "$VENV/bin/uvicorn" server.web_app:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info
