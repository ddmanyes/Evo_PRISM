# Evo_PRISM (Evo-PRISM)

**Evolutionary Platform for Runtime Intelligence & Semantic Memory**

> **Language:** English · [中文版](README_zh.md)

Evo_PRISM is a general-purpose, self-evolving LLM-Agent toolset lifecycle runtime and permanent semantic memory system powered by **LLM Agent + MCP (Model Context Protocol)**. It adopts an innovative three-layer data architecture (L3 raw data → L2 structured features → L1 semantic cache), combined with **HELIX** for tool health tracking and **ENGRAM** for permanent artifact memory, enabling seamless plain-language interactions with complex tools without coding. Every analysis result is archived, semantically searchable, and strictly traceable to the exact version of the tool that produced it, driving autonomous tool promotion and optimization.

To demonstrate the platform's capabilities in handling complex data provenance, high-complexity tool lifecycle management, and large-scale feature extraction, the project ships with a **flagship vertical showcase: the Bioinformatics Analysis Module**, which fully integrates Spatial Transcriptomics, Bulk RNA-seq, scRNA-seq, and Proteomics analysis capabilities.

[![CI](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.5.2-yellow)](https://duckdb.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![Docker](https://img.shields.io/badge/Docker-ddmann375000%2Fevo--prism%3A0.1.0-blue)](https://hub.docker.com/r/ddmann375000/evo-prism)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

---

## Why Evo_PRISM?

Evo_PRISM addresses core pain points in modern LLM agent development and complex tool execution, enabling self-evolving intelligence for demanding analytical workflows.

Modern LLM-driven analysis workflows suffer from three named failure modes that conventional tools cannot detect:

- **Code Provenance Vacuum** — AI-generated scripts vanish after the session ends; results on disk have no traceable link to the code, parameters, or package versions that produced them.
- **Silent Methodological Failure** — incorrect normalization, outdated statistical assumptions, or sparse-matrix numerical errors produce plausible-looking outputs with no runtime warning, contaminating downstream conclusions silently.
- **Methodological Drift** — the same raw data analyzed at different times or by different researchers yields subtly different results due to inconsistent thresholds or tool versions, making reproducibility impossible to audit.

Evo_PRISM addresses all three systematically:

| Problem                                               | Solution                                                                                                           |
| :---------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------- |
| 🔁 Re-running expensive analyses on every query       | **L1 Semantic Cache**: similar queries answered in milliseconds, saving resources dramatically               |
| 📂 Output artifacts scattered and unsearchable        | **ENGRAM Permanent Memory**: every artifact auto-archived with hybrid 3-way semantic search                  |
| 🐛 No traceability between results and code versions  | **HELIX Version Tracking**: tool versions hard-linked to analysis history, preventing version drift          |
| 🧑‍💻 Non-coders locked out of complex tools         | **Plain-language Agent**: invoke tools via Web UI, MCP, or Telegram — no coding required                    |
| 🛠️ Static toolchains that cannot adapt to new needs | **Self-evolving Code Promotion**: hot-spots auto-detected; code reused ≥3× is promoted to a permanent tool |

---

## Architecture

### Three-Layer Data Architecture

![Evo_PRISM Three-Layer Architecture](docs/images/三層架構_eng.png)

| Layer |  Name  | Description                                                     |
| :---: | :----: | :-------------------------------------------------------------- |
|  L3  | Bronze | Immutable raw data (FASTQ, SpaceRanger outs)                    |
|  L2  | Silver | DuckDB + Parquet structured features (30B → 416 MB compressed) |
|  L1  |  Gold  | HNSW semantic cache, TTL 7 days                                 |

### HELIX — Tool Health-Evolving Loop

![HELIX Simple Flow](docs/images/helix簡單流程圖.png)

![HELIX Architecture Loop](docs/images/HELIX_架構圖_2.png)

**HELIX** tracks every analysis tool's version, detects hot-spots, measures cyclomatic complexity (Radon CC), and drives stabilization refactors — ensuring the Agent always calls a healthy, well-maintained version.

**Code Promotion is human-confirmed by design.** When a generated script meets the promotion threshold (reused ≥ 3×, `fpromote ≥ 3.0`), LLM review produces a draft — but a human administrator runs `approve_candidate()` before it enters the permanent `analysis/` directory. This intentional gate prevents the "LLM generates → LLM reviews → self-validates" closed loop that undermines trust in autonomous code evolution. The `UserApproval` signal (`+1` / `0` / `-1`) also lets operators veto high-frequency but methodologically flawed scripts before promotion.

#### HELIX Memory System

HELIX uses a **dual-track memory system** so the agent can reconstruct a tool's full evolutionary history at any future diagnostic session.

**① HELIX-Vision — VLM Visual Memory**

After each stabilization iteration, a **640×640 PNG snapshot** is auto-rendered, encoding the full diagnostic context in four quadrants and stored as base64 in `tool_stabilization_log.diagnosis_img`:

| Quadrant          | Content                                                 |
| :---------------- | :------------------------------------------------------ |
| Source Heatmap    | Per-line token count — visualizes complexity hot-spots |
| Complexity Gauge  | Current Radon cyclomatic complexity (CC) measurement    |
| Revision Timeline | Version fingerprint history from `tool_change_log`    |
| Diagnostic Text   | AI assessment summary and action plan                   |

Each snapshot costs **~100 VLM vision tokens** — approximately 10× compression vs equivalent plain text — enabling low-cost historical recall across sessions.

**② Ebbinghaus Forgetting Curve**

`scheduler/helix_expire_snapshots.py` (runs every Sunday at 04:00) progressively downsamples old snapshots to simulate biological memory decay — spatial layout survives while fine text fades over time:

```text
Days 0–180 after iteration closed :  640×640  ~100 VLM tokens  full resolution
Days 180–365 after iteration closed:  320×320   ~25 VLM tokens  50% downsampled
Days > 365 after iteration closed  :  160×160    ~6 VLM tokens  25% downsampled
```

This ensures recent diagnostics remain precise while historical memory retains spatial structure at minimal cost, enabling the Agent to rapidly reconstruct a tool's full evolutionary arc across sessions.

### ENGRAM — Permanent Artifact Memory

![ENGRAM Architecture](docs/images/engram_架構圖1_eng.png)

**ENGRAM** permanently archives every analysis artifact (CSV, Parquet, images, reports) and enables Hybrid 3-way RRF semantic search (Exact SQL + HNSW + BM25 FTS), linked to HELIX for full version provenance.

### HELIX × ENGRAM Closed Loop — Blast Radius Analysis

The two subsystems form a **closed feedback loop**:

```text
HELIX detects tool version change
        ↓
Blast Radius query traverses ENGRAM Lineage Graph
        ↓
System identifies which historical analysis results were produced
by the old tool version and may now be stale
        ↓
Precision improves as ENGRAM accumulates richer lineage metadata (71.4% → 83.3%)
```

This is what conventional workflow managers (Snakemake, Nextflow, DVC) cannot do: they detect file-input staleness, but **not code-logic staleness**. When a tool's normalization method is corrected, Evo_PRISM can retroactively flag every result produced by the buggy version — without re-running anything. Use `bio_impact` to query the current blast radius of any tool change.

### Agent Decision Flow

```text
Query
 ├─ Step 1  Exact SQL match (0 tokens, < 1 s)              ← Already done? Return directly
 ├─ Step 2  HNSW semantic search (cosine ≥ 0.88)           ← Similar query cached? Return cache
 ├─ Step 3A Standard analysis tool (L2 Parquet ready)
 │           └─ bio_find_tool: 0-token semantic search → reuse existing function before writing new code
 ├─ Step 3B Code Promotion reuse (generated before?)
 └─ Step 3C Fresh code generation (sandbox + retry)
               └─ Success → store in history → available for 3B next time
               └─ Reused ≥ 3× → promoted to a permanent 3A tool (human-confirmed)
```

---

## Empirical Performance & Benchmarks

Evo_PRISM has been rigorously validated across multiple computational biology scenarios, establishing its scalability, reliability, and token efficiency.

### 1. Tool Catalog Self-Reinforcing Flywheel (R10)
As the active tool catalog evolves and accumulates promoted tools, semantic search capability exhibits a **monotonic increase in hit rate** while maintaining flat sub-millisecond query search times:
* **Monotonic Search Convergence:** Semantic search hit rate (cosine similarity $\ge 0.45$) rises from **20.0%** (2 tools) to **100.0%** (25 tools), confirming that the platform becomes increasingly self-sufficient.
* **Low Latency Scalability:** Due to DuckDB's in-memory HNSW index, search latency remains exceptionally flat, rising from **1.40 ms** (2 tools) to only **1.96 ms** (25 tools), with P95 latency strictly below **2.0 ms** (see **[Figure 8](docs/paper/figures/figure_r10_flywheel.png)** in the paper).

### 2. High-Throughput Spatial Ingestion (R5 alt)
The 10x Genomics Visium HD end-to-end ingestion pipeline (Stages 0–7: cell segmentation, RNA attribution counting, downstream Scanpy clustering, and GeoJSON export) was benchmarked across multiple tissue sections (see **[Table S16](docs/paper/supplementary.md#table-s16-visium-hd-ingestion-throughput-and-resource-profiling-benchmark-r5-alt)**):
* **Throughput:** Achieves up to **14.3 cells/sec** (processing a 4.79 GB image with 1,493 cell boundaries and 13.4k genes in **104.5 seconds**).
* **Storage Footprint:** Single-cell output footprints are highly compressed, requiring only **2.08 MB** to **5.85 MB** total disk storage (GeoJSON + H5AD matrices).

### 3. Evolutionary Precision and Self-Healing
* **Blast Radius Precision (ENGRAM Flywheel):** Recursive SQL CTE lineage precision autonomously converges from **71.4%** (Phase A, metadata-scarce) to **83.3%** (Phase B, metadata-saturated) at 100% recall under zero manual configuration (see **[Table S15](docs/paper/supplementary.md#table-s15-ground-truth-oracle-query-set-construction-and-annotation-protocol)**).
* **Code Health Self-Healing (HELIX Flywheel):** Average repository `HealthScore` heals from a technical-debt warning state of **0.61** back to **0.94** under active McCabe cyclomatic complexity reduction (**-80%** median reduction).

### 4. Token Economy and Robustness
* **Context Preservation:** The external `Figure Cache` strips high-volume multi-modal base64 payloads at the MCP border, achieving a **98.2%** transfer token saving rate.
* **100% Test Pass Rate:** The robust test suite containing **679 automated tests** achieves a **100.0% pass rate**, guaranteeing the runtime safety and statistical accuracy of all core components.

---

## Quick Start

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
- `bge-m3-Q8_0.gguf` (605 MB embedding model — download the Q8_0 quantized version from [HuggingFace BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3))

```bash
# 1. Place the downloaded model into the project's models/ folder
mkdir -p models
# Move bge-m3-Q8_0.gguf into models/

# 2. Copy the environment config (no API key needed for MCP mode)
cp .env.example .env

# 3. Start all services (first run downloads the image, ~343 MB)
docker compose up -d

# 4. Initialize the database (one-time only)
docker compose exec evo-prism python scripts/00_init_db.py
```

Done! MCP HTTP ready at **<http://localhost:8080>** · Web UI at <http://localhost:8000>

> Or pull the pre-built image directly: `docker pull ddmann375000/evo-prism:0.1.0`
>
> HPC / Singularity cluster users: see [SETUP.md](SETUP.md#singularity).

---

### 💡 Path 2: Manual Install + MCP (IDE / CLI)

> This path lets you call 25 analysis tools directly in natural language from **Claude Code CLI** or **Antigravity IDE** — no browser needed.

**Prerequisites:**

- Python ≥ 3.10 (3.11 recommended)
- [uv](https://github.com/astral-sh/uv) package manager (`pip install uv`)
- [llama.cpp](https://github.com/ggml-org/llama.cpp) compiled (for the local embedding service)
- `bge-m3-Q8_0.gguf` (605 MB, placed at `~/llama.cpp/models/`)
- Anthropic API Key

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

# Step 5: Run all database schema migrations (one-time only)
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    .venv/bin/python "$script"
done
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

- **Claude Code CLI**: a `.mcp.json` is already in the project root — run `claude` from this directory to auto-load all tools
- **Antigravity IDE**: Settings → MCP Servers, add a new entry — see [MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md)
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

## Test Data

`tests/fixtures/bulk_rna/` ships with ready-to-use Bulk RNA-seq demo data — no extra downloads needed. It includes `deseq2_counts_top1000.csv` (~400 KB, 1,000 genes × 84 samples), `deseq2_coldata.tsv` (sample metadata), and `gene_sets/hair_follicle.yaml` (example pathway gene sets), sufficient to demo `bio_run_deg`, `bio_run_enrichment`, and `bio_run_heatmaps`.

```bash
# Run the full automated test suite
.venv/bin/python -m pytest tests/ -v --tb=short
# 679 tests collected
```

---

## LLM + MCP Integration

Evo_PRISM uses **MCP (Model Context Protocol)** as the standard bridge between LLMs and tools. Any MCP-compatible client (Claude Code, Antigravity IDE, Web UI) can invoke 17 built-in tools directly (18 with sandbox enabled) — the LLM autonomously decides when to query history, trigger analysis, or retrieve reports, with no manual intervention required.

Both **stdio** and **HTTP** transports are supported for external AI client integration.

```bash
# Standalone HTTP server
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

### Available Tools (25 by default)

| Tool | Description |
| :--- | :--- |
| `bio_history_lookup` | Sample analysis history |
| `bio_history_timeline` | Recent N-day timeline |
| `bio_history_check` | Check whether an analysis has completed |
| `bio_history_search` | L1 HNSW semantic search |
| `bio_memory_query` | L1 cache full report retrieval |
| `bio_memory_write` | Write to L1 cache |
| `bio_register_sample` | Register a new sample |
| `bio_read_report` | Read raw analysis report |
| `bio_artifact_search` | ENGRAM 3-way RRF semantic search |
| `bio_artifact_summary` | ENGRAM artifact summary |
| `bio_get_artifact` | Retrieve analysis output file handle (path + download URL + preview) |
| `bio_get_figure` | Retrieve a single figure by ID via MCP ImageContent (on-demand VLM loading) |
| `bio_check_l2_sufficiency` | Check L2 readiness status |
| `bio_find_tool` | Semantic search for reusable analysis functions (tool discovery before writing code) |
| `bio_run_spatial_eda` | Spatial transcriptomics EDA analysis |
| `bio_run_bulk_eda` | Bulk RNA-seq EDA analysis |
| `bio_run_deg` | Differential expression analysis (DEG) + volcano plot |
| `bio_run_enrichment` | ORA enrichment analysis + dot plot |
| `bio_run_heatmaps` | Expression heatmap generation |
| `bio_tool_health` | HELIX tool health report |
| `bio_failure_summary` | Aggregate analysis failure diagnostics (HELIX PM1 self-diagnosis) |
| `bio_impact` | Change blast-radius assessment |
| `bio_run_mcseg_roi` † | Visium HD ROI multi-scale cell segmentation (GPU, 30–90 min) |
| `bio_run_mcseg_fullslide` † | Full-slide tiled cell segmentation (GPU, hours) |
| `bio_compute_crc_metrics` † | CRC Visium HD spatial metrics computation |
| `bio_execute_code` ⚠️ | Sandboxed Python execution (requires `MCP_ENABLE_DANGEROUS_TOOLS=true`) |

> † Requires the MCseg backend (`scripts/msseg/`) which is not included in this repository. These tools appear in the tool list but will return an import error if called without the backend installed.

For detailed configuration, see [MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md) and [MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md).

---


## Scheduled Tasks

Six background schedulers handle database backup, L1/figure-cache TTL cleanup, HNSW index rebuilding, new-sample scanning, and HELIX snapshot downsampling. See [docs/guides/SCHEDULED_TASKS.md](docs/guides/SCHEDULED_TASKS.md) for the full task table and launchd installation instructions.

---

## Project Structure

```text
Evo_PRISM/                      ← Project root
│
├── Core code (git-tracked)
│   ├── config/                 ← Centralized settings (paths, safe_write, db_utils)
│   ├── scripts/                ← One-shot L3→L2 conversion + schema migrations (v0–v19)
│   ├── analysis/               ← Analysis library (HELIX / ENGRAM / EDA / cache)
│   │   ├── tool_registry.py    ← HELIX-Core (version management)
│   │   ├── artifact_registry.py← ENGRAM-Core (permanent memory)
│   │   └── tool_visualizer.py  ← HELIX-Vision (visual snapshots)
│   ├── server/                 ← FastAPI Web UI + Agent + MCP Server
│   ├── scheduler/              ← Scheduled tasks (backup / cleanup / rebuild / scan)
│   ├── tests/                  ← Test suite (49 files, 679 tests)
│   ├── gene_sets/              ← Pathway gene set YAML configs
│   └── start_bioagent.sh       ← One-command startup script
│
├── Documentation (git-tracked)
│   └── docs/
│       ├── images/             ← Architecture diagrams (three-layer / HELIX / ENGRAM)
│       ├── guides/             ← Operation guides (MCP / L3 Ingest / Data Integration)
│       ├── launchd/            ← macOS launchd plist templates
│       └── logs/               ← Development logs (PROGRESS / execution_trace)
│
└── Local data directories (.gitignore excluded)
    ├── bio_memory.duckdb       ← Primary database (*.duckdb)
    ├── silver/                 ← L2 Parquet feature store
    ├── gold/                   ← L1 semantic cache DuckDB
    ├── crc_visium_data/        ← L3 raw data (~39 GB)
    ├── bulk_rna_data/          ← Bulk RNA-seq raw data
    └── proteome_data/          ← Proteomics data
```

---

## Testing

```bash
cd "$BIO_DB_ROOT"
.venv/bin/python -m pytest tests/ -v --tb=short
```

Expected: **679 tests collected** across 49 test files, covering HELIX versioning, ENGRAM artifact search, MCP stdio/HTTP transport, sandbox security, Code Promotion, and the bioinformatics analysis pipeline. A small number of sandbox `FileNotFoundError` failures are environment-dependent and not logic failures.

See [docs/guides/TESTING.md](docs/guides/TESTING.md) for the full per-file breakdown.

---

## Documentation

| Document                                                                    | Description                                               |
| :-------------------------------------------------------------------------- | :-------------------------------------------------------- |
| [CLAUDE.md](CLAUDE.md)                                                         | Project constitution (development rules + schema + paths) |
| [SETUP.md](SETUP.md)                                                           | Detailed environment setup guide                          |
| [TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md)                                 | Technical overview: architecture, HELIX/ENGRAM formulas, benchmark results |
| [docs/logs/PROGRESS.md](docs/logs/PROGRESS.md)                                 | Implementation progress archive                           |
| [docs/guides/DATA_INTEGRATION_GUIDE.md](docs/guides/DATA_INTEGRATION_GUIDE.md) | Cross-project data integration guide                      |
| [docs/guides/L3_DATA_INGEST_GUIDE.md](docs/guides/L3_DATA_INGEST_GUIDE.md)     | L3 sample ingestion guide                                 |
| [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md)                 | MCP stdio configuration (Claude Code / Antigravity)       |
| [docs/guides/MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md)                 | MCP HTTP transport guide                                  |
| [docs/guides/STAR_SCHEMA.md](docs/guides/STAR_SCHEMA.md)                       | Star Schema views — design and usage examples            |
| [docs/guides/TESTING.md](docs/guides/TESTING.md)                               | Full test suite breakdown (49 files, 679 tests)          |
| [docs/guides/SCHEDULED_TASKS.md](docs/guides/SCHEDULED_TASKS.md)               | Scheduled task table + launchd setup                      |

---

## Extending the Toolbox

The core design philosophy of Evo_PRISM: **the LLM extends itself (Self-Evolution)**. `CLAUDE.md` is the project constitution — once the LLM reads it, a single instruction is enough to autonomously add a new analysis domain or extend an existing tool: it outputs the playbook, analysis function, MCP wiring, and HELIX version registration without manual intervention. All results are immediately callable from any MCP client and archived into ENGRAM.

For the full extension workflow, see [CLAUDE.md](CLAUDE.md).

---

## Contributing

PRs and Issues welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

---

## License

MIT License — © 2026 Chan Chi Ru. See [LICENSE](LICENSE).
