# Evo_PRISM

**Evolutionary Platform for Runtime Intelligence & Semantic Memory**

> **Language:** English · [繁體中文版](README_zh.md)

[![CI](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/ddmanyes/Evo_PRISM/releases/tag/v.0.1)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

[Why Evo_PRISM](#why-evo_prism) · [Quick start](#quick-start) · [Architecture](#architecture) · [MCP tools](#mcp-tools) · [Benchmark](#benchmark-snapshot) · [Docs](#documentation)

Evo_PRISM is a local-first runtime that connects natural-language requests to versioned analysis tools and searchable, provenance-aware memory through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

Two subsystems keep the runtime accountable over time: **HELIX** tracks tool discovery, versions, health, and human-gated promotion; **ENGRAM** archives analysis artifacts and links them back to the tool version that produced them. The repository's flagship bioinformatics showcase covers spatial transcriptomics, bulk RNA-seq, scRNA-seq, and MCseg-assisted spatial workflows.

## Why Evo_PRISM?

LLM-driven analysis often fails after the first successful run: generated code disappears, methods drift, artifacts become hard to find, and results lose their connection to the tool version that produced them. Evo_PRISM addresses those failures at the runtime level.

| Pillar | What it provides |
| :--- | :--- |
| **HELIX** | Tool discovery, version tracking, health diagnostics, and human-reviewed promotion |
| **ENGRAM** | Provenance-aware artifact storage with exact, HNSW, BM25, and RRF retrieval |
| **MCP runtime** | Natural-language access over stdio or HTTP, backed by reusable analysis history and cache |

## Quick Start

The verified path for this public checkout is a manual Python environment. Python 3.11 is recommended.

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

### 2. Start the default embedding service

Install [llama.cpp](https://github.com/ggml-org/llama.cpp), download [`bge-m3-Q8_0.gguf`](https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF), then run:

```bash
~/llama.cpp/build/bin/llama-server \
  --model ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding \
  --port 8081 \
  --ctx-size 8192 \
  --n-gpu-layers 99
```

### 3. Connect an MCP client

```bash
cp .mcp.json.example .mcp.json
# Replace the absolute-path placeholders, then add this config to your MCP client.
```

For standalone HTTP transport:

```bash
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

For the optional Web UI, set the matching API key in `.env` and run:

```bash
VENV_PYTHON="$PWD/.venv/bin/python" bash start_bioagent.sh --claude
# Alternatives: --google or --local
```

Open <http://localhost:8000> after the readiness message appears. The local mode also requires the vision model paths documented in [`.env.example`](.env.example).

> **Docker note:** the current Compose entrypoint starts stdio MCP while the file exposes Web UI and HTTP ports, so `docker compose up` is not yet a verified full-stack path.

See [SETUP.md](SETUP.md) for ExFAT/synchronized-folder environments, Google or OpenAI embeddings, Web UI backends, and HPC/Singularity setup.

## Architecture

<picture>
  <source srcset="docs/images/figure_1_system_arch.svg" type="image/svg+xml">
  <img src="docs/images/figure_1_system_arch.png" alt="Evo_PRISM request-to-memory architecture" width="100%">
</picture>

Requests move from L3 immutable sources to L2 structured features and L1 low-latency retrieval. Reusable results and registered tools are checked before cold execution; new results flow back into provenance-aware memory.

<details>
<summary><strong>HELIX tool lifecycle</strong></summary>

<picture>
  <source srcset="docs/images/figure_2_system_arch.svg" type="image/svg+xml">
  <img src="docs/images/figure_2_system_arch.png" alt="HELIX top-to-bottom discovery, health, stabilization, and memory lifecycle" width="100%">
</picture>

HELIX searches the active registry before generating code. Reused candidates and unhealthy tools enter supervised stabilization, and promotion into `analysis/` requires human approval.

</details>

<details>
<summary><strong>ENGRAM artifact memory</strong></summary>

<picture>
  <source srcset="docs/images/figure_3_system_arch_1.svg" type="image/svg+xml">
  <img src="docs/images/figure_3_system_arch_1.png" alt="ENGRAM artifact registration, retrieval, ranking, and lineage architecture" width="100%">
</picture>

ENGRAM stores reports, figures, and tables with semantic vectors and tool-version provenance. Its impact graph can identify historical results affected by a HELIX tool change.

</details>

## MCP tools

The server declares **37 tools** and exposes **36 by default**; `bio_execute_code` remains hidden unless explicitly enabled.

| Group | Count | Examples |
| :--- | :---: | :--- |
| History and samples | 8 | lookup, timeline, registration, comparison |
| Memory and artifacts | 8 | semantic search, reports, figures, artifact retrieval and explicit delivery |
| Discovery and governance | 5 | tool search, health, failure diagnosis, impact |
| Core analysis | 6 | spatial/bulk EDA, DEG, enrichment, heatmaps |
| MCseg and post-processing | 9 | ROI/full-slide runs, QC, annotation, Loupe export |
| Opt-in high privilege | 1 | sandboxed Python execution |

See the canonical declarations in [`server/bio_memory_server.py`](server/bio_memory_server.py). MCseg execution requires an external backend not included here. Tool promotion is human-reviewed, HTTP bearer authentication is optional, and high-cost tools are rate-limited.

## Benchmark snapshot

![Evo_PRISM semantic-search flywheel benchmark](docs/images/Figure8_Flywheel_Evolution.png)

The tracked R10 figure reports hit rate increasing from **20% with 2 active tools** to **100% with 25**, while average HNSW lookup latency changes from **1.40 ms** to **1.96 ms**. These are project-reported results; the raw benchmark bundle is not included here.

## Documentation

- [Setup and deployment](SETUP.md) · [Windows setup](docs/guides/WINDOWS_SETUP.md)
- [MCP stdio](docs/guides/MCP_JSON_SETUP.md) · [MCP HTTP](docs/guides/MCP_HTTP_GUIDE.md)
- [Data integration](docs/guides/DATA_INTEGRATION_GUIDE.md) · [L3 ingestion](docs/guides/L3_DATA_INGEST_GUIDE.md)
- [Scheduled tasks](docs/guides/SCHEDULED_TASKS.md) · [Star schema](docs/guides/STAR_SCHEMA.md)

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change.

## License

MIT License — © 2026 Chan Chi Ru. See [LICENSE](LICENSE).
