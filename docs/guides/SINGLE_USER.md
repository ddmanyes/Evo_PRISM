# Personal use on one computer

[README](../../README.md) · [English](SINGLE_USER.md) · [繁體中文](SINGLE_USER_zh.md)

## Minimal setup

Local Python, DuckDB and MCP over stdio, with your existing AI client handling conversation. Basic metadata and CPU EDA require no Tailscale, Postgres, multiuser worker or Clef. Semantic search needs embeddings; MCseg and other analyses have additional model, GPU and dependency requirements.

## Install, initialize and connect

Install uv and place the checkout on a native filesystem such as APFS/ext4, avoiding ExFAT or cloud-synced folders. Run from the checkout root. uv creates the locked dependency environment; network access is needed for packages and DuckDB extensions.

```bash
uv sync --locked --no-install-project --python 3.12
.venv/bin/python scripts/ep_local.py init --data-dir "$PWD/local-data"
.venv/bin/python scripts/ep_local.py doctor --data-dir "$PWD/local-data"
```

Initialization creates an empty main database, L1 cache and lock directory, applies pending migrations and writes local-data/mcp.json with absolute paths. Add its mcpServers.ep entry to your client configuration and connect. No HTTP service or model is started.

Rerunning init preserves existing samples and analyses. A differing existing MCP JSON is never overwritten; choose another file with --config. The generated configuration pins data paths, disables sandbox execution and remote-auth requirements, and ignores .env. This is for local stdio only; use the lab guide for shared/remote deployment.

Initially bio_sample_list is empty and bio_decision_status reports off. Register your own data before analysis.

## Diagnostics and repair

```bash
.venv/bin/python scripts/ep_local.py doctor --data-dir "$PWD/local-data" --check-services
```

doctor reports dependencies, main DB, L1 and optional embedding/Clef status with repair guidance as JSON. Missing required items return exit code 1; an unavailable optional embedding service does not fail metadata readiness. --check-services only performs GET /health, never inference. Clef reads the ledger mode (off/rules/shadow) without writing; use bio_decision_status for live bridge readiness. Health reachability is not semantic-retrieval validation.

| Condition | Action |
| --- | --- |
| Missing packages | `uv sync --locked --no-install-project` |
| Missing DB/L1 or old schema | Stop writers, back up and rerun init; do not delete the DB |
| DB locked | Stop another writer and retry; a personal setup needs one server |
| Embedding offline | Metadata remains available; start the matching model for semantic search |

## Complete reproducible demo

```bash
.venv/bin/python scripts/check_single_user.py
```

Creates independent temporary data and deterministic synthetic counts (40 genes × 6 samples), then runs a real MCP workflow:

1. Initialize empty DB/L1, connect MCP and list tools
2. Register a sample; reject input outside its registered directory
3. Call bio_run_bulk_eda to generate four figures and one report
4. Verify tool_id, artifact files, count statistics and unchanged input hash
5. Disconnect, rerun init and reconnect to retrieve history/report; analysis count stays at one

The printed temporary directory retains result.json, setup/server logs, reports and figures. The demo uses the normal analysis_run/ENGRAM path without research data or generative/embedding model calls. It verifies explicit lookup and reuse, not autonomous model decision quality.

## Analyze your own counts

Register sample_id, data_type=bulk_rnaseq and l3_path (a directory) with bio_register_sample. Use a gene-by-sample TSV with gene IDs in the first column. Optional coldata has sample IDs in the first column and a group column. Then call:

```json
{"sample_id":"my_bulk","counts_path":"counts.tsv","coldata_path":"coldata.tsv"}
```

Call bio_run_bulk_eda with those arguments. Paths are relative to the registered L3 directory, or absolute within it. Omitting counts_path retains the legacy lab default; new users should pass it explicitly.

## Optional capabilities and validation boundaries

Semantic search needs an embedding service such as bge-m3 with matching dimensions (default 1024). Set the endpoint with init --embedding-url or edit the generated MCP JSON. Web chat needs a generative model/API, and Clef shadow needs a bridge and weights. Bulk DEG/MCseg extras and external MSseg are outside the basic EDA validation.

Verification evidence and platform results are recorded in [readiness report](PERSONAL_READINESS_PLAN.md) and GitHub Actions.

[Lab deployment](LAB_DEPLOYMENT.md) · [Advanced installation](INSTALLATION.md) · [Decision advisor](DECISION_ADVISOR.md)
