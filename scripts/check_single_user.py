"""Smoke-test a new local database and real MCP stdio session without model calls.

Run with the installed environment: python scripts/check_single_user.py
Uses an isolated temporary directory; preserves logs on success and failure.
Existing databases, .env secrets, and deployed services are not used.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def main() -> None:
    import inspect
    import dotenv.main
    if "PYTHON_DOTENV_DISABLED" not in inspect.getsource(dotenv.main):
        raise RuntimeError("Update python-dotenv before running: isolation requires PYTHON_DOTENV_DISABLED")
    root = Path(__file__).resolve().parents[1]
    work = Path(tempfile.mkdtemp(prefix="ep-single-user-"))
    data = work / "data"
    data.mkdir()
    # Explicit environment avoids inheriting deployment settings or credentials.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if k in os.environ}
    env.update({
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(root), "BIO_DB_ROOT": str(data),
        "DUCKDB_PATH": str(data / "bio_memory.duckdb"),
        "L1_CACHE_PATH": str(data / "gold/hermes_cache.duckdb"),
        "MPLCONFIGDIR": str(work / "matplotlib"), "ER_DB_BACKEND": "duckdb",
        "MCP_AUTH_REQUIRED": "false", "MCP_AUTH_TOKEN": "", "MCP_AUTH_TOKENS": "",
        "MCP_ENABLE_DANGEROUS_TOOLS": "false",
        "LLAMACPP_BASE_URL": "http://127.0.0.1:1/v1",
        "EP_CLEF_BASE_URL": "http://127.0.0.1:1",
    })
    print(f"Isolated verification logs: {work}", flush=True)
    commands = [
        ["00_init_db.py", "--no-samples"], ["03_init_l1_cache.py"],
        ["run_pending_migrations.py"], ["04_migrate_l1_rrf.py"],
        ["run_pending_migrations.py"],
    ]
    with (work / "initialization.log").open("w") as log:
        for script, *args in commands:
            log.write(f"\n>>> {script} {args}\n")
            log.flush()
            subprocess.run([sys.executable, str(root / "scripts" / script), *args],
                           cwd=work, env=env, stdout=log, stderr=log, check=True, timeout=180)
    import duckdb
    with duckdb.connect(str(data / "bio_memory.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM sample_registry").fetchone()[0] == 0
        # Regression: mixed FK/soft-reference schemas must retain existing indexes
        # and restore dependent views after migration v29.
        assert con.execute("SELECT count(*) FROM duckdb_indexes() WHERE index_name = 'idx_history_sample_type'").fetchone()[0] == 1
        con.execute("SELECT * FROM analysis_index LIMIT 0")
        version = con.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        assert version >= 29
    results = {"schema_version": version, "initial_samples": 0, "checks": []}

    async def check_mcp() -> None:
        with (work / "server.log").open("w") as log:
            params = StdioServerParameters(command=sys.executable,
                args=[str(root / "server/bio_memory_server.py")], env=env, cwd=str(work))
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert "bio_execute_code" not in names
                    results["tool_count"] = len(names)
                    calls = [
                        ("bio_decision_status", {}),
                        ("bio_register_sample", {"sample_id": "docs_smoke", "data_type": "other",
                            "l3_path": str(data), "project": "synthetic_smoke"}),
                        ("bio_sample_list", {}),
                        ("bio_history_check", {"sample_id": "docs_smoke", "analysis_type": "bulk_eda"}),
                    ]
                    for name, arguments in calls:
                        response = await session.call_tool(name, arguments)
                        assert not response.isError, (name, response)
                        text = "\n".join(c.text for c in response.content if hasattr(c, "text"))
                        if name == "bio_decision_status":
                            status = json.loads(text)
                            assert status["mode"] == "off" and not status["native_model_enabled"]
                        elif name == "bio_sample_list":
                            assert "docs_smoke" in text
                        elif name == "bio_history_check":
                            assert "exists: false" in text
                        results["checks"].append(name)
    asyncio.run(asyncio.wait_for(check_mcp(), timeout=120))
    with duckdb.connect(str(data / "bio_memory.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM sample_registry WHERE sample_id='docs_smoke'").fetchone()[0] == 1
    results["status"] = "PASS"
    results["scope"] = "Initialization, repeat migrations, MCP metadata read/write; no embeddings, models or scientific analysis"
    (work / "result.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
