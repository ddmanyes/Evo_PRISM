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

# ── Initialise L1 semantic-cache schema (idempotent; memory_recent table) ────
# Separate DB from bio_memory.duckdb (L1_CACHE_PATH, e.g. gold/hermes_cache.duckdb),
# so it isn't covered by the check above and needs its own existence check.
if [ ! -f "${BIO_DB_ROOT}/gold/hermes_cache.duckdb" ]; then
    echo "[entrypoint] First boot: initialising L1 cache schema..."
    python scripts/03_init_l1_cache.py
fi

# ── Run pending migrations only (NOT a blind re-run of every script) ────────
# The old approach re-executed every NN_migrate_schema_v*.py on every boot. That's
# unsafe: later migrations restructure what earlier ones created (e.g. v14 moves
# analysis_artifacts.inline_data into a separate blobs table), so re-running an
# old script's own post-migration verification against an already-newer schema
# throws (v9 asserts inline_data exists; v14 already removed it) — confirmed via
# a real 126-sample/269-analysis database already at v23: re-running from v1
# crash-looped the container at v9. run_pending_migrations.py only executes
# scripts whose target version is newer than schema_migrations' current max.
python scripts/run_pending_migrations.py

# 04_migrate_l1_rrf.py operates on the separate L1 cache db (not tracked by
# schema_migrations) and is itself idempotent (ADD COLUMN IF NOT EXISTS),
# documented safe to run on every boot regardless of version — no gating needed.
python scripts/04_migrate_l1_rrf.py

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
