#!/usr/bin/env bash
# Evo_PRISM Docker entrypoint
# Usage:
#   server   — start MCP + FastAPI Web UI (default)
#   test     — run pytest test suite
#   shell    — drop to bash (debugging)
#   init-db  — initialise DuckDB schema only
set -euo pipefail

MODE="${1:-server}"

# ── Initialise DB schema on first boot (idempotent) ──────────────────────────
if [ ! -f "${BIO_DB_ROOT}/bio_memory.duckdb" ]; then
    echo "[entrypoint] First boot: initialising database schema..."
    python scripts/00_init_db.py
fi

# ── Run migrations on every start (idempotent scripts) ───────────────────────
scripts=$(ls scripts/[0-9][0-9]_migrate_schema_*.py 2>/dev/null | sort -V)
if [ -n "$scripts" ]; then
    echo "[entrypoint] Running migrations..."
    while IFS= read -r script; do
        echo "[entrypoint] Running: $script"
        python "$script"
    done <<< "$scripts"
fi

case "$MODE" in
  server)
    echo "[entrypoint] Starting Evo_PRISM MCP + Web UI..."
    exec python server/bio_memory_server.py
    ;;
  web)
    echo "[entrypoint] Starting FastAPI Web UI only (port 8000)..."
    exec uvicorn server.web_app:app --host 0.0.0.0 --port 8000
    ;;
  test)
    echo "[entrypoint] Running test suite..."
    exec pytest tests/ -v --tb=short "${@:2}"
    ;;
  init-db)
    echo "[entrypoint] Initialising database schema..."
    exec python scripts/00_init_db.py
    ;;
  shell)
    exec bash
    ;;
  *)
    echo "Unknown mode: $MODE"
    echo "Valid modes: server | web | test | init-db | shell"
    exit 1
    ;;
esac
