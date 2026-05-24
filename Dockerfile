# Evo_PRISM — Production Docker Image
# ─────────────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   builder  — compiles C extensions, resolves lockfile deps with uv
#   runtime  — slim final image (no compiler toolchain)
#
# Embedding providers (EMBEDDING_PROVIDER env var):
#   llamacpp  — default; needs llamacpp sidecar (see docker-compose.yml)
#   openai    — set EMBEDDING_PROVIDER=openai + OPENAI_API_KEY  (no sidecar)
#   google    — set EMBEDDING_PROVIDER=google + GOOGLE_API_KEY  (no sidecar)
#
# Quick start (full local stack, exact paper setup):
#   docker compose up -d
#   docker compose exec evo-prism pytest tests/ -v
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml uv.lock ./

# uv sync installs deps from the lockfile into the system Python (no venv needed)
ENV UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir "uv>=0.4,<1" \
    && uv sync --no-dev --no-install-project

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.title="Evo_PRISM" \
      org.opencontainers.image.description="Evolutionary Platform for Runtime Intelligence & Semantic Memory" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/ddmanyes/Evo_PRISM" \
      org.opencontainers.image.documentation="https://github.com/ddmanyes/Evo_PRISM/blob/main/gigascience_reviewer_pack/REPRODUCE.md"

# libgomp1: OpenMP (numpy/scipy parallel ops); curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 evoprism

WORKDIR /app

# Copy installed packages from builder (uv installed into system site-packages)
COPY --from=builder /usr/local/lib/python3.11/site-packages \
                    /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source (.dockerignore excludes large data dirs + .duckdb files)
COPY --chown=evoprism:evoprism . .

RUN chmod +x entrypoint.sh \
    && mkdir -p /data/bio_db /data/gold /data/silver /data/results \
    && chown -R evoprism:evoprism /data

# Environment defaults — no inline comments inside ENV block (Dockerfile syntax)
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV BIO_DB_ROOT=/data/bio_db
ENV MCSEG_RESULTS_ROOT=/data/results/mcseg
ENV EMBEDDING_PROVIDER=llamacpp
ENV LLAMACPP_BASE_URL=http://embedding:8081/v1
ENV EMBEDDING_MODEL=bge-m3
ENV EMBEDDING_DIM=1024
ENV INFERENCE_BACKEND=claude

# MCP HTTP/SSE transport (8080) + FastAPI Web UI (8000)
EXPOSE 8000 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

USER evoprism
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["server"]
