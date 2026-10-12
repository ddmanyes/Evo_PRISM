# Evo_PRISM

**Runtime intelligence, tool evolution and traceable analysis memory**

[English](README.md) · [繁體中文](README_zh.md)

EP lets AI agents discover tools, run analyses and retrieve previous results through MCP, with artifact and tool-version tracking. HELIX manages tool lifecycles; ENGRAM manages registered artifacts and provenance. Bioinformatics tools cover bulk RNA, single-cell and spatial analysis; availability depends on installed dependencies and data.

## Choose your setup

| Use case | Start here | Requirements |
| --- | --- | --- |
| Personal, one computer | [Single-user guide](docs/guides/SINGLE_USER.md) | Local Python, DuckDB, MCP client; embeddings for semantic search |
| Lab, multiple users or remote access | [Lab deployment](docs/guides/LAB_DEPLOYMENT.md) | Authentication, member permissions, shared storage and workers |

**Personal use does not require Clef, Tailscale or a multiuser queue.** Root Compose is a lab deployment configuration, not a portable single-user default.

## Quick start: personal use

In a checkout on a native local filesystem, install Python 3.11 and uv, then run:

```bash
uv sync --locked --no-install-project --python 3.12
.venv/bin/python scripts/ep_local.py init --data-dir "$PWD/local-data"
.venv/bin/python scripts/ep_local.py doctor --data-dir "$PWD/local-data"
```

Add `mcpServers.ep` from the generated `local-data/mcp.json` to your MCP client and call `bio_sample_list`. Run the complete isolated demo with:

```bash
.venv/bin/python scripts/check_single_user.py
```

[Personal setup, diagnostics and validation scope](docs/guides/SINGLE_USER.md).
## Architecture

![EP architecture overview](docs/images/ep_overview.svg)

| Component | Responsibility and boundary |
| --- | --- |
| Agent / MCP | Planning and tool calls, subject to authorization and input checks |
| HELIX | Discovery, versions and health tracking; promotion requires tests and human review |
| ENGRAM | Storage, hybrid retrieval and lineage for registered artifacts; not automatic chat archiving |
| L3 / L2 / L1 | Raw data / structured features / expiring semantic cache; permanent artifacts are separate |
| Clef shadow | Optional comparison advice; rules remain primary, with no tool execution or promotion authority |

[HELIX / ENGRAM diagrams and target workflows](docs/guides/ARCHITECTURE.md) · [Clef configuration and logging scope](docs/guides/DECISION_ADVISOR.md)

## Main capabilities

| Task | Tool entry points |
| --- | --- |
| Check existing results | `bio_history_check`, `bio_history_search`, `bio_read_report` |
| Find tools and methods | `bio_find_tool`, `bio_get_playbook`, `bio_get_analysis_workflow` |
| Analyze and retrieve artifacts | `bio_run_*`, `bio_artifact_search`, `bio_get_artifact`, `bio_get_figure` |
| Track versions and impact | `bio_tool_health`, `bio_impact`, `bio_compare_versions` |
| Decision advice and feedback | `bio_decision_status`, `bio_decision_assess`, `bio_decision_feedback` |

Use the live MCP catalog for available tools. Sandbox execution is disabled by default; MCseg and other optional capabilities have additional model, GPU and dependency requirements.

## Validation and limits

The [single-user guide](docs/guides/SINGLE_USER.md) records clean-environment installation, MCP, synthetic CPU EDA and cross-session result reuse. See the [testing guide](docs/guides/TESTING.md) for analysis tests and historical validation; these do not guarantee every analysis capability or scientific correctness in this revision.

A new decision ledger starts off. Clef shadow requires a separately configured bridge and model; the repository includes the client, not weights or an automatically started MLX service. Successful shadow operation is not independent quality acceptance.

## Documentation

- [Personal setup](docs/guides/SINGLE_USER.md) · [Lab deployment](docs/guides/LAB_DEPLOYMENT.md) · [Advanced installation](docs/guides/INSTALLATION.md)
- [Architecture](docs/guides/ARCHITECTURE.md) · [Decision advisor](docs/guides/DECISION_ADVISOR.md)
- [MCP stdio](docs/guides/MCP_JSON_SETUP.md) · [MCP HTTP](docs/guides/MCP_HTTP_GUIDE.md)
- [Analysis workflows](docs/guides/ANALYSIS_WORKFLOW_GUIDE.md) · [Data ingestion](docs/guides/L3_DATA_INGEST_GUIDE.md)
- [Tests](docs/guides/TESTING.md) · [Scheduling and backups](docs/guides/SCHEDULED_TASKS.md)

## Contributing and license

Issues and PRs welcome; read [CONTRIBUTING.md](CONTRIBUTING.md).
MIT License — © 2026 Chan Chi Ru. [LICENSE](LICENSE).
