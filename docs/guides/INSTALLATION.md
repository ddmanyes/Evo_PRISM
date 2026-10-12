# Advanced installation and existing deployment

Existing deployment and advanced installation reference. **For personal use start with the [single-user guide](SINGLE_USER.md)**. Root Compose uses lab-specific build paths, a Tailscale address, shared model endpoints and workers; adapt it before use on another machine.

The retained fixture examples require local test files that may not be included in a public checkout.


Choose your path first, then follow the steps:

| I want to...                                               | Recommended path                                             | Difficulty |
| :--------------------------------------------------------- | :----------------------------------------------------------- | :--------: |
| Get up and running as fast as possible                     | 🐳[Docker Compose](#-path-1-docker-compose-recommended)         |   ★☆☆   |
| Call analysis tools directly from Claude Code / IDE        | 💡[Manual Install + MCP](#-path-2-manual-install--mcp-ide--cli) |   ★★☆   |
| Use the full web chat interface or fully offline inference | 🌐[Manual Install + Web UI](#-path-3-manual-install--web-ui)    |   ★★★   |

---

### 🐳 Path 1: Docker Compose (Recommended)

No Python setup needed — everything runs inside the container.

**Prerequisites:**

- [Docker Engine ≥ 24 + Docker Compose v2](https://docs.docker.com/get-docker/)
- **`scikit-misc` (an `omicverse` dependency, pulled in by the `bulk-analysis`/`mcseg` extras) has no Linux arm64 wheel on PyPI** — only macOS arm64 and Linux x86_64. A native arm64 build (e.g. on Apple Silicon Docker Desktop) falls back to a source build whose meson script fails outright. If you need those extras, build/run with `--platform linux/amd64` (works under Rosetta emulation, just slower).
- Embedding backend, pick one:
  - **Bundled sidecar**: `bge-m3-Q8_0.gguf` (605 MB — download the Q8_0 GGUF from [HuggingFace ggml-org/bge-m3-Q8_0-GGUF](https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF); `BAAI/bge-m3` itself has no GGUF) into `models/`, and add the `embedding` service back to `docker-compose.yml` (see the file's comments for the exact block)
  - **Reuse an existing OpenAI-compatible embedding endpoint** (e.g. another llama-server instance already running somewhere reachable): skip the sidecar and just point `LLAMACPP_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` at it — this is what the checked-in `docker-compose.yml` currently does (points at the shared bge-m3:8081 instance that sb/lcdda also use)

```bash
# 1. If using the bundled sidecar, place the model into models/ (skip if reusing an existing endpoint)
mkdir -p models
# Move bge-m3-Q8_0.gguf into models/

# 2. Copy the environment config (no API key needed for MCP mode)
cp .env.example .env

# 3. Start all services (first run downloads/builds the image)
docker compose up -d

# 4. Verify — DB schema init + pending migrations run automatically on container start
curl -s "http://${EP_HOST}:8000/health"  # set EP_HOST to the configured bind address
```

The default command is **core mode**: all persistent project data comes from the
Evo_PRISM directory on KINGSTON, and the service does not depend on
`/Volumes/SSD`. To opt in to read-only SSD-backed raw L3 inputs for MCseg:

```bash
test -d /Volumes/SSD
docker compose -f docker-compose.yml -f docker-compose.ssd.yml \
  up -d --force-recreate
```

Before unplugging the SSD, recreate the service in core mode:

```bash
docker compose up -d --force-recreate
```

See [Optional SSD Mount](../../docs/guides/OPTIONAL_SSD_MOUNT.md) for verification and
recovery commands.

> ⚠️ **Don't manually loop over every `scripts/[0-9][0-9]_migrate_*.py`** — `entrypoint.sh` already runs only the migrations that are actually pending (via `scripts/run_pending_migrations.py`, gated on `schema_migrations`'s current max version), on every start. Blindly re-running *all* migration scripts against an already-migrated database is unsafe: later migrations restructure what earlier ones created (e.g. a later migration moved an early one's `inline_data` column into a separate table), so an early script's own post-migration check can fail against an already-newer schema and crash-loop the container.

Done! Both the Web UI and MCP (mounted at `/mcp`) are served from the **same port**, **the configured host on port 8000** — there is no separate `:8080` MCP endpoint in the default `web` entrypoint mode.

> Or pull the pre-built image directly: `docker pull ddmann375000/evo-prism:0.1.0`
>
> HPC / Singularity cluster users: see [SETUP.md](../../SETUP.md#singularity).

---

### 💡 Path 2: Manual Install + MCP (IDE / CLI)

> This path lets you call analysis tools directly in natural language from **Claude Code CLI** or **Antigravity IDE** — no browser needed.

**Prerequisites:**

- Python ≥ 3.10 (3.11 recommended)
- [uv](https://github.com/astral-sh/uv) package manager (`pip install uv`)
- [llama.cpp](https://github.com/ggml-org/llama.cpp) compiled (for the local embedding service)
- `bge-m3-Q8_0.gguf` (605 MB, placed at `~/llama.cpp/models/`)
- An API key only if using the corresponding Web Agent backend

**Installation:**

```bash
# Step 1: Create a virtual environment
# Note: if the project lives on an ExFAT drive or Google Drive sync folder,
# the Python venv must be on a local (APFS/ext4) filesystem — use a symlink to bridge back
python3 -m venv ~/.venvs/hermes-bio-memory
ln -s ~/.venvs/hermes-bio-memory .venv

# Step 2: Install all Python packages
uv sync --no-install-project

# Step 3: Configure environment variables
cp .env.example .env
# Open .env and fill in: ANTHROPIC_API_KEY=sk-ant-...

# Step 4: Initialize the database (one-time only)
.venv/bin/python scripts/00_init_db.py
.venv/bin/python scripts/03_init_l1_cache.py

# Step 5: Run all database schema migrations (one-time only)
.venv/bin/python scripts/run_pending_migrations.py
.venv/bin/python scripts/04_migrate_l1_rrf.py
```

**Start the Embedding Server (required):**

```bash
# The Embedding Server converts queries to vectors for semantic search
# The & at the end runs it in the background so your terminal stays free
~/llama.cpp/build/bin/llama-server \
  -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99 &

# Verify it started successfully (expect: {"status":"ok"})
curl http://localhost:8081/health
```

**Connect an MCP client:**

- **Claude Code CLI**: copy `.mcp.json.example` to `.mcp.json` and configure absolute paths before connecting
- **Antigravity IDE**: Settings → MCP Servers, add a new entry — see [MCP_JSON_SETUP.md](../../docs/guides/MCP_JSON_SETUP.md)
- **External HTTP client**: `python server/bio_memory_server.py --transport http --port 8082`

---

### 🌐 Path 3: Manual Install + Web UI

> Complete Path 2 first, then run:

```bash
bash start_bioagent.sh --claude  # Claude API backend (recommended, requires ANTHROPIC_API_KEY)
bash start_bioagent.sh --google  # Gemini API backend (requires GOOGLE_API_KEY)
bash start_bioagent.sh --local   # Fully offline inference (requires Gemma 4 26B model, ~16 GB RAM)
```

Open your browser: **[http://localhost:8000](http://localhost:8000)**

---

### 🧪 Verify Your Installation

The project ships with a built-in test dataset — no external downloads needed:

```python
from analysis.bulk_eda import run_deg_analysis

result = run_deg_analysis(
    counts_path="tests/fixtures/bulk_rna/deseq2_counts_top1000.csv",
    coldata_path="tests/fixtures/bulk_rna/deseq2_coldata.tsv",
    condition_col="group",
    ref_level="ctrl",
)
print(result["summary"])   # prints differentially expressed gene count summary
```

Or run it directly in Claude Code CLI:

```
bio_run_deg counts_path=tests/fixtures/bulk_rna/deseq2_counts_top1000.csv
            coldata_path=tests/fixtures/bulk_rna/deseq2_coldata.tsv
            condition_col=group ref_level=ctrl
```

---
