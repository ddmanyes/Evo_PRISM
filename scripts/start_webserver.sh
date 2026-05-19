#!/bin/bash
# Web server launcher for launchd — sources .env then starts uvicorn.
# launchd does not inherit shell environment, so API keys must be loaded here.

BIO_DB_ROOT="/Volumes/NO NAME/bio_DB"
VENV="/Users/zhanqiru/.venvs/hermes-bio-memory"

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
