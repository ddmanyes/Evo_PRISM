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

# `uv sync` always manages its own project .venv regardless of UV_SYSTEM_PYTHON
# (that env var only affects uv's pip-compatible commands), so it silently installed
# into /build/.venv here — a directory the runtime stage never copies, leaving the
# final image with no third-party deps at all. Use `uv export` + `uv pip install
# --system` instead, which genuinely targets system site-packages.
ENV UV_SYSTEM_PYTHON=1
# scikit-misc (transitive dep of omicverse): uv's hash-pinned requirements line makes
# `uv pip install` fall through to the sdist and try to build it from source — meson's
# version_please.py fails outside a git checkout. Plain `pip install` (no hash pinning)
# correctly picks the prebuilt manylinux wheel. Installing it FIRST, before the uv pass,
# is required — just filtering its own line out of requirements.txt isn't enough, since
# `uv pip install` still resolves+rebuilds it as omicverse's declared dependency; only a
# package that's already present and satisfies the requirement gets left alone.
RUN pip install --no-cache-dir "uv>=0.4,<1" scikit-misc==0.5.2 \
    && uv export --no-dev --no-install-project --extra bulk-analysis --extra mcseg \
        --format requirements-txt -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

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

# config/settings.py derives L1_ROOT/L2_ROOT/RESULTS_ROOT/DATA_ROOT/DYNAMIC_CODE_DIR
# as subdirectories of BIO_DB_ROOT (e.g. L1_ROOT = BIO_DB_ROOT/"gold", holding
# hermes_cache.duckdb) — they were previously created as siblings (/data/gold etc.),
# a path that nothing in settings.py actually points at, so tools writing to L1
# cache (bio_memory_write and friends) failed with "No such file or directory".
RUN chmod +x entrypoint.sh \
    && mkdir -p /data/bio_db/gold /data/bio_db/silver /data/bio_db/results_ana \
               /data/bio_db/data_ana /data/bio_db/results/dynamic_code /data/bio_db/results/mcseg \
    && chown -R evoprism:evoprism /data \
    && chown evoprism:evoprism /app

# Environment defaults — no inline comments inside ENV block (Dockerfile syntax)
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV BIO_DB_ROOT=/data/bio_db
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

# Bake the DuckDB VSS extension into the image (evoprism's ~/.duckdb cache) so it's
# available regardless of whether 00_init_db.py's incidental "INSTALL vss" ever runs
# in a given container instance. It only runs on genuine first boot (no existing
# bio_memory.duckdb); a container started against a pre-existing DB (e.g. a
# bind-mounted real database) skips it entirely, so any migration touching the
# HNSW-indexed analysis_artifacts table would otherwise fail with "extension vss
# not found" the first time that container runs.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL vss')"

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["server"]
