> **Source:** Model Context Protocol (MCP) — Anthropic Official Specification
> https://modelcontextprotocol.io/
> https://github.com/modelcontextprotocol/python-sdk

---

# Model Context Protocol (MCP)

**Author:** Anthropic
**License:** MIT
**Python SDK:** `pip install mcp`
**Version:** 1.x (2024–2025)

---

## Overview

MCP is an open protocol that standardizes how applications provide context and tools to LLMs. It defines a client–server architecture:

- **MCP Server** — exposes Tools, Resources, and Prompts
- **MCP Client** — an LLM host (Claude Desktop, Claude Code, Hermes Agent) that calls the server
- **Transport** — stdio (local process) or SSE (HTTP streaming)

In the Hermes Bio-Memory plan, `bio_memory_server.py` is an MCP Server. Claude Code or Hermes Agent acts as the MCP Client.

---

## Core Concepts

### Tools
Functions the LLM can call. Each tool has a name, description, and JSON schema for inputs:

```python
@server.tool()
async def bio_memory_query(question: str, sample_id: str) -> str:
    """Query the bioinformatics memory backend (L1 → L2 fallback)."""
    ...
```

### Resources
Read-only data sources the LLM can access (files, DB results). Optional for this project.

### Prompts
Reusable prompt templates. Optional for this project.

---

## Python SDK — Server Skeleton

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
import asyncio

server = Server("bio-memory")

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="bio_memory_query",
            description="Query bioinformatics memory: L1 semantic cache → L2 DuckDB feature store",
            inputSchema={
                "type": "object",
                "properties": {
                    "question":  {"type": "string", "description": "Natural language question"},
                    "sample_id": {"type": "string", "description": "Sample ID, e.g. MQ250428-D1-D2"}
                },
                "required": ["question"]
            }
        ),
        types.Tool(
            name="bio_memory_write",
            description="Write analysis report to L1 semantic cache",
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id":     {"type": "string"},
                    "analysis_type": {"type": "string"},
                    "report_text":   {"type": "string"}
                },
                "required": ["sample_id", "analysis_type", "report_text"]
            }
        ),
        types.Tool(
            name="bio_register_sample",
            description="Register a new sample in the L3 registry",
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {"type": "string"},
                    "data_type": {"type": "string"},
                    "l3_path":   {"type": "string"}
                },
                "required": ["sample_id", "data_type", "l3_path"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "bio_memory_query":
        result = await handle_query(arguments["question"], arguments.get("sample_id"))
        return [types.TextContent(type="text", text=result)]
    elif name == "bio_memory_write":
        ...
    elif name == "bio_register_sample":
        ...
    raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Registering with Claude Code

Add to `.claude/settings.json` (or Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bio-memory": {
      "command": "python",
      "args": ["I:/bio_DB/server/bio_memory_server.py"]
    }
  }
}
```

---

## Transport Options

| Transport | Use case |
|-----------|----------|
| `stdio` | Local process — simplest, use for development |
| `sse` (HTTP) | Remote server or multi-client — use for production |

For this project: use **stdio** (local Windows process).

---

## Relevance to Hermes Bio-Memory

| Plan Phase | MCP Role |
|-----------|----------|
| Phase 5 | `bio_memory_server.py` implements MCP Server |
| L1 query routing | `bio_memory_query` tool triggers L1 → L2 fallback logic |
| L1 write | `bio_memory_write` tool inserts embeddings into hermes_cache.duckdb |
| Sample registration | `bio_register_sample` tool updates sample_registry |

---

## References

- MCP Specification: https://modelcontextprotocol.io/
- Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Claude Code MCP integration: https://docs.anthropic.com/claude/docs/mcp
