#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.venvs/hermes-bio-memory/bin/python"
LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"
MODEL="$HOME/gemma-4-26B-A4B-it-UD-IQ2_M.gguf"
MMPROJ="$HOME/mmproj-BF16.gguf"
LOG_DIR="$SCRIPT_DIR/logs"
LLAMA_PID=""
WEB_PID=""

mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[hermes]${NC} $*"; }
warn()  { echo -e "${YELLOW}[hermes]${NC} $*"; }
error() { echo -e "${RED}[hermes]${NC} $*"; }

cleanup() {
    echo ""
    warn "stopping..."
    [ -n "$LLAMA_PID" ] && kill "$LLAMA_PID" 2>/dev/null || true
    [ -n "$WEB_PID" ]   && kill "$WEB_PID"   2>/dev/null || true
    [ -n "$LLAMA_PID" ] && info "llama server stopped (PID=$LLAMA_PID)"
    [ -n "$WEB_PID" ]   && info "web server stopped (PID=$WEB_PID)"
    exit 0
}
trap cleanup SIGINT SIGTERM

# 1. llama.cpp server (port 8080)
if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
    warn "llama server already running on port 8080 (not managed by this script — Ctrl+C will NOT stop it)"
else
    info "Starting Gemma 4 Vision server (port 8080)..."
    "$LLAMA_BIN" \
        -m "$MODEL" \
        --mmproj "$MMPROJ" \
        --port 8080 \
        --ctx-size 16384 \
        --n-gpu-layers 99 \
        --flash-attn on \
        -ctk q8_0 \
        -ctv q8_0 \
        --threads "$(sysctl -n hw.physicalcpu 2>/dev/null || echo 4)" \
        > "$LOG_DIR/llama_server.log" 2>&1 &
    LLAMA_PID=$!
    info "llama server PID=$LLAMA_PID  log -> $LOG_DIR/llama_server.log"

    info "Waiting for model to load (up to 120s)..."
    READY=0
    for i in $(seq 1 60); do
        if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
            info "llama server ready after ${i}x2s"
            READY=1
            break
        fi
        if ! kill -0 "$LLAMA_PID" 2>/dev/null; then
            error "llama server exited unexpectedly, check $LOG_DIR/llama_server.log"
            exit 1
        fi
        sleep 2
    done

    if [ "$READY" -eq 0 ]; then
        error "llama server not ready after 120s, aborting"
        kill "$LLAMA_PID" 2>/dev/null || true
        exit 1
    fi
fi

# 2. FastAPI Web UI (port 8000)
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    warn "web server already running on port 8000 (not managed by this script — Ctrl+C will NOT stop it)"
else
    info "Starting Hermes Web UI (port 8000)..."
    cd "$SCRIPT_DIR"
    "$VENV" server/web_app.py \
        > "$LOG_DIR/web_app.log" 2>&1 &
    WEB_PID=$!
    info "web server PID=$WEB_PID  log -> $LOG_DIR/web_app.log"

    # 60s — heavy scientific Python imports (scanpy/anndata) can be slow on cold start
    READY=0
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
            info "web server ready after ${i}x2s"
            READY=1
            break
        fi
        sleep 2
    done

    if [ "$READY" -eq 0 ]; then
        error "web server failed to start, check $LOG_DIR/web_app.log"
        kill "$LLAMA_PID" 2>/dev/null || true
        exit 1
    fi
fi

echo ""
info "========================================"
info "  Hermes Bio-Memory ready"
info "  http://localhost:8000"
info "  Press Ctrl+C to stop managed services"
info "========================================"
echo ""

# Wait on specific PIDs so we notice if either server crashes
if [ -n "$LLAMA_PID" ] && [ -n "$WEB_PID" ]; then
    wait "$LLAMA_PID" "$WEB_PID"
elif [ -n "$LLAMA_PID" ]; then
    wait "$LLAMA_PID"
elif [ -n "$WEB_PID" ]; then
    wait "$WEB_PID"
else
    # Both were already running — just keep script alive for Ctrl+C
    while true; do sleep 60; done
fi
