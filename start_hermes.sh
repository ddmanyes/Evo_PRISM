#!/usr/bin/env bash
set -euo pipefail
# Usage:
#   bash start_hermes.sh           # 互動式選擇模式
#   bash start_hermes.sh --claude  # Claude API + embedding only
#   bash start_hermes.sh --local   # Gemma 4 Vision + embedding

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.venvs/hermes-bio-memory/bin/python"
LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"
EMBED_MODEL="$HOME/llama.cpp/models/bge-m3-Q8_0.gguf"
VISION_MODEL="$HOME/gemma-4-26B-A4B-it-UD-IQ2_M.gguf"
MMPROJ="$HOME/mmproj-BF16.gguf"
LOG_DIR="$SCRIPT_DIR/logs"
EMBED_PID=""
VISION_PID=""
WEB_PID=""

mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[hermes]${NC} $*"; }
warn()  { echo -e "${YELLOW}[hermes]${NC} $*"; }
error() { echo -e "${RED}[hermes]${NC} $*"; }

# 解析參數或互動選擇
MODE="${1:-}"
if [ -z "$MODE" ]; then
    echo ""
    echo "選擇推理後端："
    echo "  1) Claude API（雲端，需 ANTHROPIC_API_KEY）"
    echo "  2) 本機 Gemma 4 Vision（離線，需 ~16GB RAM）"
    echo ""
    read -rp "請輸入 1 或 2 [預設 1]: " CHOICE
    case "${CHOICE:-1}" in
        2) MODE="--local" ;;
        *) MODE="--claude" ;;
    esac
fi

case "$MODE" in
    --local)  USE_LOCAL=1 ;;
    --claude) USE_LOCAL=0 ;;
    *) error "未知參數 $MODE，請用 --claude 或 --local"; exit 1 ;;
esac

cleanup() {
    echo ""
    warn "stopping..."
    [ -n "$VISION_PID" ] && kill "$VISION_PID" 2>/dev/null || true
    [ -n "$EMBED_PID" ]  && kill "$EMBED_PID"  2>/dev/null || true
    [ -n "$WEB_PID" ]    && kill "$WEB_PID"    2>/dev/null || true
    [ -n "$VISION_PID" ] && info "Gemma 4 Vision stopped (PID=$VISION_PID)"
    [ -n "$EMBED_PID" ]  && info "embedding server stopped (PID=$EMBED_PID)"
    [ -n "$WEB_PID" ]    && info "web server stopped (PID=$WEB_PID)"
    exit 0
}
trap cleanup SIGINT SIGTERM

# 1. Gemma 4 Vision server (port 8080) — 只在 local 模式啟動
if [ "$USE_LOCAL" -eq 1 ]; then
    if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
        warn "Gemma 4 already running on port 8080 (not managed — Ctrl+C will NOT stop it)"
    else
        info "Starting Gemma 4 Vision server (port 8080)..."
        "$LLAMA_BIN" \
            -m "$VISION_MODEL" \
            --mmproj "$MMPROJ" \
            --port 8080 \
            --ctx-size 16384 \
            --n-gpu-layers 99 \
            --flash-attn on \
            -ctk q8_0 \
            -ctv q8_0 \
            --threads "$(sysctl -n hw.physicalcpu 2>/dev/null || echo 4)" \
            > "$LOG_DIR/llama_server.log" 2>&1 &
        VISION_PID=$!
        info "Gemma 4 PID=$VISION_PID  log -> $LOG_DIR/llama_server.log"

        info "Waiting for model to load (up to 120s)..."
        READY=0
        for i in $(seq 1 60); do
            if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
                info "Gemma 4 ready after ${i}x2s"
                READY=1; break
            fi
            if ! kill -0 "$VISION_PID" 2>/dev/null; then
                error "Gemma 4 exited unexpectedly, check $LOG_DIR/llama_server.log"
                exit 1
            fi
            sleep 2
        done
        if [ "$READY" -eq 0 ]; then
            error "Gemma 4 not ready after 120s, aborting"
            kill "$VISION_PID" 2>/dev/null || true; exit 1
        fi
    fi
fi

# 2. Embedding server (port 8081)
if curl -sf http://localhost:8081/health >/dev/null 2>&1; then
    warn "embedding server already running on port 8081 (not managed — Ctrl+C will NOT stop it)"
else
    info "Starting embedding server bge-m3 (port 8081)..."
    "$LLAMA_BIN" \
        -m "$EMBED_MODEL" \
        --embedding \
        --port 8081 \
        --ctx-size 8192 \
        --n-gpu-layers 99 \
        > "$LOG_DIR/embed_server.log" 2>&1 &
    EMBED_PID=$!
    info "embedding server PID=$EMBED_PID  log -> $LOG_DIR/embed_server.log"

    info "Waiting for embedding server to load (up to 60s)..."
    READY=0
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8081/health >/dev/null 2>&1; then
            info "embedding server ready after ${i}x2s"
            READY=1; break
        fi
        if ! kill -0 "$EMBED_PID" 2>/dev/null; then
            error "embedding server exited unexpectedly, check $LOG_DIR/embed_server.log"
            exit 1
        fi
        sleep 2
    done
    if [ "$READY" -eq 0 ]; then
        error "embedding server not ready after 60s, aborting"
        kill "$EMBED_PID" 2>/dev/null || true; exit 1
    fi
fi

# 3. FastAPI Web UI (port 8000)
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    warn "web server already running on port 8000 (not managed — Ctrl+C will NOT stop it)"
else
    info "Starting Hermes Web UI (port 8000)..."
    cd "$SCRIPT_DIR"
    "$VENV" server/web_app.py \
        > "$LOG_DIR/web_app.log" 2>&1 &
    WEB_PID=$!
    info "web server PID=$WEB_PID  log -> $LOG_DIR/web_app.log"

    READY=0
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
            info "web server ready after ${i}x2s"
            READY=1; break
        fi
        sleep 2
    done
    if [ "$READY" -eq 0 ]; then
        error "web server failed to start, check $LOG_DIR/web_app.log"
        kill "$EMBED_PID" 2>/dev/null || true
        kill "$VISION_PID" 2>/dev/null || true
        exit 1
    fi
fi

BACKEND_LABEL="Claude API"
[ "$USE_LOCAL" -eq 1 ] && BACKEND_LABEL="本機 Gemma 4 Vision"

echo ""
info "========================================"
info "  Hermes ready  [$BACKEND_LABEL]"
info "  http://localhost:8000"
info "  Press Ctrl+C to stop managed services"
info "========================================"
echo ""

# Wait on managed PIDs
WAIT_PIDS=()
[ -n "$VISION_PID" ] && WAIT_PIDS+=("$VISION_PID")
[ -n "$EMBED_PID" ]  && WAIT_PIDS+=("$EMBED_PID")
[ -n "$WEB_PID" ]    && WAIT_PIDS+=("$WEB_PID")

if [ ${#WAIT_PIDS[@]} -gt 0 ]; then
    wait "${WAIT_PIDS[@]}"
else
    while true; do sleep 60; done
fi
