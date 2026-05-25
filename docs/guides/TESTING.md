# Evo_PRISM — Test Suite

## Running Tests

```bash
cd "$BIO_DB_ROOT"
.venv/bin/python -m pytest tests/ -v --tb=short
```

Expected: **631+ tests collected** across 49 test files.
A small number of sandbox `FileNotFoundError` failures are environment-dependent (llama-server not running) and are not logic failures.

## Test File Index

| Test file | Count | Coverage |
|:---|:---:|:---|
| `test_tool_registry.py` | 56 | HELIX version management, stabilization, churn |
| `test_fast_path.py` | 46 | Agent fast-path (SQL / timeline / sample list) |
| `test_artifact_registry.py` | 44 | ENGRAM 3-way RRF + provenance |
| `test_code_executor.py` | 40+ | Sandbox Python execution security boundaries |
| `test_sandbox_adversarial.py` | 33 | N=30 adversarial inputs (FPR=0%, Recall=100%) |
| `test_phase4.py` | 35 | MCP Server stdio tools |
| `test_phase5.py` | 31 | Agent loop + sandbox execution |
| `test_phase10.py` | 31 | MCP HTTP transport |
| `test_phase6.py` | 23 | Telegram Bot commands and message dispatch |
| `test_dashboard_actions.py` | 19 | Control panel action layer |
| `test_helix_formulas.py` | 18 | HELIX Eq.(1)(2) f_promote + HealthScore (full boundary coverage) |
| `test_graduation.py` | 18 | Code Promotion mechanism |
| `test_impact.py` | 16 | HELIX blast-radius assessment |
| `test_artifact_resources.py` | 15 | MCP Resources artifact delivery |
| `test_phase3.py` | 15 | L1 cache + HNSW |
| `test_tool_visualizer.py` | 15 | HELIX visual snapshots + downsampling |
| `test_phase2b.py` | 14 | History query + report generation |
| `test_pathway_scoring.py` | 14 | ssGSEA / Z-score pathway scoring |
| `test_bulk_timeseries.py` | 13 | Time-series means + log2 FC |
| `test_figure_cache.py` | 13 | MCP figure cache + base64 stripping |
| `test_report_reader.py` | 13 | Report reading + path sandboxing |
| `test_star_schema.py` | 13 | Star Schema views (throughput / stability) |
| `test_bulk_deg.py` | 11 | DEG + volcano plot |
| `test_validate_inference_backend.py` | 10 | Inference backend fail-fast validation |
| `test_tool_search.py` | 10 | Tool semantic search |
| `test_enrichment.py` | 10 | ORA enrichment analysis |
| `test_mcseg_quality.py` | 10 | MCseg quality assessment |
| `test_playbook.py` | 10 | Playbook tool expansion |
| Other (21 files) | 57+ | report_page / bulk_heatmap / dashboard / bulk_eda / backfill_tool_id / handle_message_fast_path / unique_constraint / init_db / spatial_ingest / google_backend / cte_scalability, etc. |

## Key Test Groups

**Security / sandbox** — `test_code_executor.py` + `test_sandbox_adversarial.py`
Tests that the sandboxed code executor blocks all malicious patterns (N=30 adversarial inputs, FPR=0%, Recall=100%).

**HELIX correctness** — `test_helix_formulas.py` + `test_tool_registry.py`
Validates Eq.(1) `f_promote` and Eq.(2) `HealthScore` against all boundary conditions, plus version lifecycle (register → deprecate → prune).

**ENGRAM retrieval** — `test_artifact_registry.py`
Covers the 3-way RRF ranking (Exact SQL + HNSW + BM25 FTS) and artifact provenance linking to HELIX tool versions.

**MCP transport** — `test_phase4.py` + `test_phase10.py`
End-to-end stdio and HTTP transport for all 17 built-in tools.
