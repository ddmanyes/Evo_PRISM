# Evo_PRISM

**Evolutionary Platform for Runtime Intelligence & Semantic Memory**

> **Language:** English · [繁體中文版](README_zh.md)

[![CI](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/ddmanyes/Evo_PRISM/releases/tag/v.0.1)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

[Why Evo_PRISM](#why-evo_prism) · [Quick start](#quick-start) · [Architecture](#architecture) · [MCP tools](#mcp-tool-catalog) · [Benchmark](#benchmark-snapshot) · [Docs](#documentation)

Evo_PRISM is a local-first runtime that connects natural-language requests to versioned analysis tools and searchable, provenance-aware memory through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

Two subsystems keep the runtime accountable over time: **HELIX** tracks tool discovery, versions, health, and human-gated promotion; **ENGRAM** archives analysis artifacts and links them back to the tool version that produced them. The repository's flagship bioinformatics showcase covers spatial transcriptomics, bulk RNA-seq, scRNA-seq, and MCseg-assisted spatial workflows.

## Why Evo_PRISM?

LLM-driven analysis often fails after the first successful run: generated code disappears, methods drift, artifacts become hard to find, and the result can no longer be tied to the exact tool that produced it. Evo_PRISM treats those as runtime and memory problems instead of leaving them to prompt discipline.

| Failure mode | Evo_PRISM response |
| :--- | :--- |
| Generated analysis code vanishes after a session | HELIX registers reusable tools and records their version lineage |
| Plausible results hide methodological or implementation errors | Health and failure diagnostics make tool behavior inspectable |
| The same analysis changes across people or time | Analysis history links results, parameters, and tool versions |
| Outputs are scattered across files and folders | ENGRAM registers artifacts for exact and semantic retrieval |
| Expensive work is repeated for similar requests | L1 cache and prior-history lookup reuse validated results |

## Core capabilities

- **MCP-native access** over stdio or HTTP for compatible agents and IDEs.
- **Three-layer data path** from immutable raw data (L3) to structured features (L2) and a semantic cache (L1).
- **HELIX tool lifecycle** with semantic discovery, version tracking, health monitoring, and human-reviewed promotion.
- **ENGRAM artifact memory** with exact SQL, HNSW vector search, BM25 full-text search, and reciprocal-rank fusion.
- **Version-aware impact analysis** that can identify historical artifacts affected by a tool change.
- **Bioinformatics workflows** for sample history, spatial and bulk RNA analysis, differential expression, enrichment, heatmaps, cell annotation, and MCseg integration.

## Quick Start

The verified path for this public checkout is a manual Python environment. Python 3.11 is recommended; the project supports Python 3.10 and newer.

### 1. Install

```bash
git clone https://github.com/ddmanyes/Evo_PRISM.git
cd Evo_PRISM

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install "uv>=0.4,<1"
uv sync --no-install-project

cp .env.example .env
python scripts/00_init_db.py
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    python "$script"
done
```

If the repository lives on ExFAT or in a synchronized folder, create the virtual environment on a local APFS/ext4 volume and symlink it back as `.venv`.

### 2. Configure embeddings

The default provider is a local [llama.cpp](https://github.com/ggml-org/llama.cpp) server using [`bge-m3-Q8_0.gguf`](https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF):

```bash
~/llama.cpp/build/bin/llama-server \
  --model ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding \
  --port 8081 \
  --ctx-size 8192 \
  --n-gpu-layers 99
```

Confirm that the server is ready:

```bash
curl http://localhost:8081/health
```

Google and OpenAI embedding providers are also supported. Set the matching provider and API key in `.env`; see [`.env.example`](.env.example) for the available variables.

### 3. Connect an MCP client

For stdio transport, copy the provided template and replace every absolute-path placeholder:

```bash
cp .mcp.json.example .mcp.json
```

See the [MCP JSON setup guide](docs/guides/MCP_JSON_SETUP.md) for client-specific configuration. To expose the standalone HTTP transport instead:

```bash
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

### 4. Optional Web UI

Set `INFERENCE_BACKEND` and the corresponding API key in `.env`, then start the matching backend:

```bash
VENV_PYTHON="$PWD/.venv/bin/python" bash start_bioagent.sh --claude
# Alternatives: --google or --local
```

Open <http://localhost:8000> after the readiness message appears. The local mode also requires the vision model paths documented in [`.env.example`](.env.example).

### Docker status

[`Dockerfile`](Dockerfile) and [`docker-compose.yml`](docker-compose.yml) are included, but the current Compose entrypoint starts the stdio MCP process while the Compose file exposes Web UI and HTTP ports. Until that transport wiring is aligned, `docker compose up` should not be treated as a verified full-stack quick start.

For environment details, alternative embedding providers, and HPC/Singularity notes, continue with [SETUP.md](SETUP.md).

## Architecture

### Three-layer data and query flow

![Evo_PRISM three-layer data and query architecture](docs/images/figure_1_system_arch.png)

Evo_PRISM separates durable data from derived features and fast retrieval:

| Layer | Role | Typical contents |
| :---: | :--- | :--- |
| L3 Bronze | Immutable source data | FASTQ, SpaceRanger outputs, source images |
| L2 Silver | Structured feature store | DuckDB tables and Parquet features |
| L1 Gold | Low-latency retrieval | Exact lookup and HNSW semantic cache |

A request checks reusable results and registered tools before it reaches a cold execution path. New results flow back into the memory layers instead of remaining as disconnected files.

### HELIX tool lifecycle

![HELIX discovery, health, stabilization, and memory lifecycle](docs/images/figure_2_system_arch.png)

HELIX searches the active tool registry before new code is generated. A matching tool can run directly; a miss can enter sandboxed ad-hoc execution. Reused candidates and unhealthy tools enter supervised stabilization, where promotion into `analysis/` requires human approval.

The current design uses a semantic discovery threshold of `0.45` and a promotion signal at `f_promote ≥ 3.0`. Health observations, version changes, and visual snapshots remain available for later diagnosis.

### ENGRAM artifact memory

![ENGRAM artifact registration, retrieval, and lineage architecture](docs/images/figure_3_system_arch_1.png)

ENGRAM registers reports, figures, tables, and other analysis outputs with semantic vectors and tool-version provenance. Retrieval combines structured lookup with semantic search and reciprocal-rank fusion. Artifact relationships form an impact graph that can trace which historical results may become stale after a HELIX tool change.

Together, HELIX and ENGRAM create a closed loop: tool evolution updates provenance, provenance identifies affected artifacts, and accumulated history improves future retrieval and review.

## MCP tool catalog

The server declares **36 tools**. By default it exposes **35**; `bio_execute_code` is hidden unless `MCP_ENABLE_DANGEROUS_TOOLS=true` is set.

| Group | Tools |
| :--- | :--- |
| History and samples | `bio_history_lookup`, `bio_history_timeline`, `bio_history_check`, `bio_history_search`, `bio_lookup_sample`, `bio_register_sample`, `bio_sample_list`, `bio_sample_compare` |
| Memory and artifacts | `bio_memory_query`, `bio_memory_write`, `bio_artifact_search`, `bio_artifact_summary`, `bio_get_artifact`, `bio_get_figure`, `bio_read_report` |
| Discovery and governance | `bio_find_tool`, `bio_tool_health`, `bio_failure_summary`, `bio_impact`, `bio_get_playbook` |
| Core analysis | `bio_check_l2_sufficiency`, `bio_run_spatial_eda`, `bio_run_bulk_eda`, `bio_run_deg`, `bio_run_enrichment`, `bio_run_heatmaps` |
| MCseg and post-processing | `bio_run_mcseg_roi`, `bio_run_mcseg_fullslide`, `bio_run_mcseg_qc`, `bio_compute_crc_metrics`, `bio_get_marker_genes`, `bio_relabel_clusters`, `bio_run_celltypist`, `bio_run_mcseg_merge`, `bio_export_loupe` |
| Opt-in high privilege | `bio_execute_code` |

The MCseg execution tools require an external MCseg backend that is not included in this repository. Post-processing tools also require compatible upstream results; CellTypist support has its own optional dependency.

### Safety and lifecycle defaults

| Control | Default behavior |
| :--- | :--- |
| Dynamic Python execution | Hidden unless explicitly enabled with `MCP_ENABLE_DANGEROUS_TOOLS=true` |
| Tool promotion | Requires human review before a candidate moves into the active analysis library |
| HTTP authentication | Optional bearer token through `MCP_AUTH_TOKEN` |
| Expensive and embedding-backed tools | Protected by the server's request rate limiter |
| Result provenance | Analysis and artifact records retain tool-version relationships |

## Benchmark snapshot

![Evo_PRISM semantic-search flywheel benchmark](docs/images/Figure8_Flywheel_Evolution.png)

The tracked R10 benchmark figure reports semantic-search hit rate increasing from **20% with 2 active tools** to **100% with 25 tools**, while average HNSW lookup latency changes from **1.40 ms** to **1.96 ms** across the same catalog sizes. These are project-reported benchmark results; the paper source and raw benchmark bundle are not included in this public checkout.

## Project structure

```text
Evo_PRISM/
├── analysis/      # Analysis functions, HELIX registry, ENGRAM, and retrieval
├── server/        # MCP server, agent adapters, and Web UI
├── store/         # DuckDB/PostgreSQL storage backends
├── config/        # Settings, paths, and database utilities
├── scripts/       # Schema migrations, ingestion, export, and maintenance
├── scheduler/     # Backup, cleanup, index, and scan jobs
├── playbooks/     # Reusable analysis procedures
├── gene_sets/     # Example pathway definitions
└── docs/guides/   # Setup, integration, transport, and operations guides
```

Runtime databases, raw inputs, feature stores, models, and generated results are local data and are intentionally excluded from version control.

## Documentation

| Guide | Purpose |
| :--- | :--- |
| [SETUP.md](SETUP.md) | Manual installation, environment variables, Singularity, and client setup |
| [MCP JSON setup](docs/guides/MCP_JSON_SETUP.md) | stdio client configuration and path handling |
| [MCP HTTP guide](docs/guides/MCP_HTTP_GUIDE.md) | HTTP transport, headers, initialization, and request examples |
| [Data integration guide](docs/guides/DATA_INTEGRATION_GUIDE.md) | Bringing bulk RNA-seq, proteomics, and other data into the project |
| [L3 data ingest guide](docs/guides/L3_DATA_INGEST_GUIDE.md) | Registering samples and converting L3 sources into L2 features |
| [Scheduled tasks](docs/guides/SCHEDULED_TASKS.md) | Backup, cache cleanup, HNSW rebuild, and launchd examples |
| [Star schema](docs/guides/STAR_SCHEMA.md) | Operational views for throughput and tool stability |
| [Windows setup](docs/guides/WINDOWS_SETUP.md) | Native Windows environment and service setup |

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change.

## License

MIT License — © 2026 Chan Chi Ru. See [LICENSE](LICENSE).
