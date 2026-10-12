# Personal use on one computer

[README](../../README.md) · [English](SINGLE_USER.md) · [繁體中文](SINGLE_USER_zh.md)

## Minimal setup

Use local Python, DuckDB and MCP over stdio, with your existing AI client handling conversation. This path needs no network listener, Postgres, Tailscale or multiuser workers. Metadata tools can be used first; semantic search needs embeddings and individual analyses have additional requirements. Single-user does not imply CPU-only or low-memory analysis.

## Install and initialize

Use Python 3.11 or newer and uv. Keep the checkout on a native filesystem such as APFS/ext4; avoid virtual environments on ExFAT or cloud-synced storage. Run these commands from the checkout root.

```bash
uv sync --no-install-project
cp .env.example .env
```

Edit .env: leave unused API keys empty and create your data directory first. Replace the example absolute path below:

```dotenv
BIO_DB_ROOT=/absolute/path/to/ep-data
MCP_AUTH_REQUIRED=false
MCP_ENABLE_DANGEROUS_TOOLS=false
EMBEDDING_PROVIDER=llamacpp
LLAMACPP_BASE_URL=http://127.0.0.1:8081/v1
EMBEDDING_MODEL=bge-m3-Q8_0
EMBEDDING_DIM=1024
ANTHROPIC_API_KEY=
```

The unauthenticated configuration here is for local stdio only. Remote/shared HTTP services require the lab authentication setup. Without BIO_DB_ROOT, data defaults to the checkout root. Use a new directory when trying EP rather than an existing research database.

```bash
mkdir -p /absolute/path/to/ep-data
.venv/bin/python scripts/00_init_db.py --no-samples
.venv/bin/python scripts/03_init_l1_cache.py
.venv/bin/python scripts/run_pending_migrations.py
.venv/bin/python scripts/04_migrate_l1_rrf.py
```

--no-samples creates the schema without legacy lab sample entries; omitting it preserves the legacy seeding behavior. L1 initialization requires the DuckDB VSS extension and network access to download it if not cached. Use the pending runner instead of manually rerunning old migrations.

## Connect an MCP client

Let your MCP client launch the server with the following configuration, replacing all absolute paths. An external client provides its own conversational model; EP does not need another generative model for this path.

```json
{
  "mcpServers": {
    "ep": {
      "command": "/absolute/path/to/Evo_PRISM/.venv/bin/python",
      "args": ["/absolute/path/to/Evo_PRISM/server/bio_memory_server.py"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/Evo_PRISM",
        "BIO_DB_ROOT": "/absolute/path/to/ep-data",
        "MCP_AUTH_REQUIRED": "false"
      }
    }
  }
}
```

On first connection, bio_sample_list should be empty and bio_decision_status should report off. Register your raw inputs with bio_register_sample and follow the relevant playbook. Discover the actual catalog through MCP tools/list.

## Add capabilities as needed

| Capability | Additional requirements |
| --- | --- |
| Semantic search / tool vector index | A compatible embedding service; dimensions must match the L1 schema |
| Built-in Web chat | A configured generative model/API backend; see advanced installation |
| Bulk RNA / MCseg | Relevant pyproject.toml extras; MCseg also needs MSseg, data and compute resources |
| Clef shadow | Separate bridge and weights; not required for basic MCP use |

Embedding example: obtain bge-m3 GGUF and llama.cpp, then start the following service. This is outside the no-model smoke test.

```bash
llama-server -m /absolute/path/to/bge-m3-Q8_0.gguf \
  --embedding --host 127.0.0.1 --port 8081 --ctx-size 8192
```

[Advanced installation](INSTALLATION.md) · [Decision advisor](DECISION_ADVISOR.md)

## Repeatable basic verification

```bash
.venv/bin/python scripts/check_single_user.py
```

This check uses a fresh temporary data directory, ignores .env and does not connect to models. It preserves logs and result.json and verifies:

- Empty main/L1 initialization, pending migrations and a repeat migration run
- Index/view preservation for the mixed FK/soft-reference schema at v29
- Real MCP stdio handshake/catalog, with sandbox tools hidden by default
- Sample registration/readback, missing-history lookup and advisor-off status

Verified on 2026-10-12 with macOS Apple Silicon, Python 3.12.13 and DuckDB 1.5.3 in an existing dependency environment, using fresh databases and no production research data. The first attempt exposed duplicate index recreation in v29; after correction, verification restarted from a fresh database.

Limits: this does not verify dependency installation on a clean OS, embedding search, Web UI, GPU/MCseg, every scientific analysis or multiuser deployment. Passing establishes basic local MCP/database operation. Without embeddings, tool indexing may log warnings while metadata tools remain usable.
