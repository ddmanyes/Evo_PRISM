"""Verify fresh setup, real MCP CPU EDA, artifact persistence and cross-session reuse.

No research data or model inference: deterministic synthetic counts are generated
in a new temporary directory. All logs and results are retained there.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ep_local import ROOT, doctor, init, local_env, process_env


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="ep-single-user-")).resolve()
    data = work / "data"
    print(f"Isolated verification logs: {work}", flush=True)
    setup = init(data, embedding_url="http://127.0.0.1:1/v1")
    assert doctor(data)["ready"]
    import duckdb

    with duckdb.connect(str(data / "bio_memory.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM sample_registry").fetchone()[0] == 0
        assert (
            con.execute(
                "SELECT count(*) FROM duckdb_indexes() WHERE index_name='idx_history_sample_type'"
            ).fetchone()[0]
            == 1
        )
        con.execute("SELECT * FROM analysis_index LIMIT 0")
        version = con.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        assert version >= 30
    raw = data / "demo_raw"
    raw.mkdir()
    counts = raw / "counts.tsv"
    counts.write_text(
        "gene\tctrl_1\tctrl_2\tctrl_3\ttreat_1\ttreat_2\ttreat_3\n"
        + "".join(
            f"synthetic_gene_{i}\t"
            + "\t".join(str(10 + i * 3 + j * 2 + (i * j) % 17) for j in range(6))
            + "\n"
            for i in range(1, 41)
        )
    )
    coldata = raw / "coldata.tsv"
    coldata.write_text(
        "sample\tgroup\n"
        + "".join(f"{group}_{i}\t{group}\n" for group in ("ctrl", "treat") for i in range(1, 4))
    )
    results = {
        "schema_version": version,
        "initial_samples": 0,
        "checks": [],
        "input_sha256": hashlib.sha256(counts.read_bytes()).hexdigest(),
        "setup": setup,
    }
    env = process_env(local_env(data, "http://127.0.0.1:1/v1"))
    env["EP_CLEF_BASE_URL"] = "http://127.0.0.1:1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    async def session_run(reuse: bool) -> None:
        with (work / ("reuse.log" if reuse else "server.log")).open("w") as log:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(ROOT / "server/bio_memory_server.py")],
                env=env,
                cwd=str(work),
            )
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert "bio_execute_code" not in names
                    results["tool_count"] = len(names)

                    async def call(name, args):
                        response = await session.call_tool(name, args)
                        assert not response.isError, (name, response)
                        text = "\n".join(c.text for c in response.content if hasattr(c, "text"))
                        assert "[ERROR]" not in text, (name, text[:500])
                        results["checks"].append(("reuse:" if reuse else "") + name)
                        return text

                    status = json.loads(await call("bio_decision_status", {}))
                    assert status["mode"] == "off" and not status["native_model_enabled"]
                    if not reuse:
                        await call(
                            "bio_register_sample",
                            {
                                "sample_id": "docs_smoke",
                                "data_type": "bulk_rnaseq",
                                "l3_path": str(raw),
                                "project": "synthetic_smoke",
                                "notes": "Synthetic demo; no scientific claims",
                            },
                        )
                        assert "docs_smoke" in await call("bio_sample_list", {})
                        assert "exists: false" in await call(
                            "bio_history_check",
                            {"sample_id": "docs_smoke", "analysis_type": "bulk_eda"},
                        )
                        # New explicit input paths must stay in the registered raw directory.
                        denied = await call(
                            "bio_run_bulk_eda",
                            {"sample_id": "docs_smoke", "counts_path": "../bio_memory.duckdb"},
                        )
                        assert "執行失敗" in denied and "registered L3" in denied
                        text = await call(
                            "bio_run_bulk_eda",
                            {
                                "sample_id": "docs_smoke",
                                "counts_path": "counts.tsv",
                                "coldata_path": "coldata.tsv",
                            },
                        )
                        assert "Bulk EDA 完成" in text, text[:1000]
                        results["analysis_id"] = re.search(r"analysis_id: ([0-9a-f-]+)", text)[1]
                        results["report_path"] = re.search(r"report_path: ([^\n]+)", text)[1]
                    history = await call(
                        "bio_history_check",
                        {"sample_id": "docs_smoke", "analysis_type": "bulk_eda"},
                    )
                    assert "exists: true" in history.lower(), history
                    report = await call(
                        "bio_read_report",
                        {"result_path": results["report_path"], "max_chars": 4000},
                    )
                    assert "docs_smoke" in report and results["analysis_id"] in report
                    summary = await call("bio_artifact_summary", {"sample_id": "docs_smoke"})
                    assert "total_runs: 1" in summary, summary

    asyncio.run(asyncio.wait_for(session_run(False), timeout=240))
    # Reinitialization must preserve the completed run and the generated config.
    config_before = Path(setup["mcp_config"]).read_bytes()
    init(data, embedding_url="http://127.0.0.1:1/v1")
    assert Path(setup["mcp_config"]).read_bytes() == config_before
    assert doctor(data, True, "http://127.0.0.1:1/v1")["ready"]
    asyncio.run(asyncio.wait_for(session_run(True), timeout=120))
    with duckdb.connect(str(data / "bio_memory.duckdb"), read_only=True) as con:
        rows = con.execute(
            "SELECT status, tool_id, summary_metrics FROM analysis_history WHERE sample_id='docs_smoke'"
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == "completed" and rows[0][1]
        metrics = json.loads(rows[0][2])
        assert metrics["n_samples"] == 6 and metrics["n_genes_matrix"] == 40
        artifacts = con.execute(
            "SELECT artifact_type, file_path FROM analysis_artifacts WHERE analysis_id=?",
            [results["analysis_id"]],
        ).fetchall()
        assert len(artifacts) == 5, artifacts
        assert sum(t == "figure" for t, _ in artifacts) == 4
        for _, path in artifacts:
            target = Path(path)
            assert (target if target.is_absolute() else data / target).is_file()
    assert hashlib.sha256(counts.read_bytes()).hexdigest() == results["input_sha256"]
    results.update(
        status="PASS",
        artifacts=5,
        figures=4,
        runs_after_reuse=1,
        report_sha256=hashlib.sha256(Path(results["report_path"]).read_bytes()).hexdigest(),
        scope="Synthetic CPU Bulk EDA, metadata/artifacts and explicit cross-session reuse; no embeddings, model or scientific quality claims",
    )
    (work / "result.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
