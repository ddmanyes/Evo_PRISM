#!/usr/bin/env bash
# 停止所有 BioAgent 相關服務（port 8080 推理引擎、8081 Embedding、8000 Web UI）

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[bioagent]${NC} $*"; }
warn() { echo -e "${YELLOW}[bioagent]${NC} $*"; }

stop_port() {
    local port=$1
    local name=$2
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        info "$name (port $port) stopped"
    else
        warn "$name (port $port) was not running"
    fi
}

stop_port 8080 "Gemma 4 Vision server"
stop_port 8081 "Embedding server (bge-m3)"
stop_port 8000 "Web UI (FastAPI)"

info "All services stopped."
