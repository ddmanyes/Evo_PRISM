# Star Schema Views (P1-C)

**Migration**: v19 ([scripts/20_migrate_schema_v19.py](../scripts/20_migrate_schema_v19.py))
**Tests**: [tests/test_star_schema.py](../tests/test_star_schema.py) — 10 passed
**Status**: 2 views shipped; `v_tool_perf_30d` deferred to P1-D

---

## Why Star Schema

Module 11 of DB114 (Analytical Modeling & Systems) describes the **dimensional model**: separate the high-volume *event* tables (facts) from the low-cardinality *describing* tables (dimensions), then expose pre-joined aggregations as views.

bio_DB already has this structure implicitly; v19 just makes it explicit and queryable in one statement instead of repeating the JOIN every time.

```
                  ┌─────────────────┐
                  │ sample_registry │  (Dim)
                  └────────┬────────┘
                           │ sample_id
                           ▼
                  ┌─────────────────┐
                  │ analysis_history│  (Fact)
                  └────────┬────────┘
                           │ tool_id
                           ▼
                  ┌─────────────────┐         ┌──────────────────────┐
                  │      tools      │◄────────│   tool_change_log    │ (Fact)
                  │      (Dim)      │         └──────────────────────┘
                  │                 │         ┌──────────────────────┐
                  │                 │◄────────│tool_stabilization_log│ (Fact)
                  └─────────────────┘         └──────────────────────┘
```

---

## View 1 — `v_analysis_throughput_by_sample_type`

**Source**: `analysis_history` × `sample_registry`
**Grain**: one row per (`data_type`, `platform`, `analysis_type`, week)
**Use cases**:

- "Visium HD 每週分析數變化趨勢"
- "kallisto bulk_eda 平均耗時是不是在退化"
- "上週 failed 跟 stale 各多少筆"

### Columns

| Column | Type | Note |
|---|---|---|
| `data_type` | VARCHAR | from `sample_registry.data_type` |
| `platform` | VARCHAR | from `sample_registry.platform` |
| `analysis_type` | VARCHAR | from `analysis_history.analysis_type` |
| `week` | TIMESTAMPTZ | `date_trunc('week', completed_at)` |
| `n_runs` | BIGINT | total rows in this bucket |
| `avg_seconds` | DOUBLE | mean of `epoch(completed_at - started_at)` |
| `n_completed` | BIGINT | rows where `status='completed'` |
| `n_failed` | BIGINT | rows where `status='failed'` |
| `n_stale` | BIGINT | rows where `status='stale'` |

**Filter**: `WHERE completed_at IS NOT NULL` — in-flight runs excluded.

### Example queries

```sql
-- Throughput of the last 4 weeks per data_type
SELECT data_type, week, SUM(n_runs) AS total
FROM   v_analysis_throughput_by_sample_type
WHERE  week >= date_trunc('week', now()) - INTERVAL 4 WEEK
GROUP  BY data_type, week
ORDER  BY week DESC, total DESC;

-- Latest week's failure rate per analysis_type
SELECT analysis_type,
       SUM(n_failed)::DOUBLE / NULLIF(SUM(n_runs), 0) AS failure_rate
FROM   v_analysis_throughput_by_sample_type
WHERE  week = date_trunc('week', now())
GROUP  BY analysis_type;
```

---

## View 2 — `v_tool_stability_signal`

**Source**: `tools` × `tool_change_log` × `tool_stabilization_log`
**Grain**: one row per active tool
**Use cases**:

- "Which tools should I worry about right now?" — one SELECT
- Replaces the inline JOIN logic inside `tool_health_report()` for ad-hoc SQL access

### Columns

| Column | Type | Note |
|---|---|---|
| `tool_name` | VARCHAR | from `tools` |
| `version` | VARCHAR | current active version |
| `status` | VARCHAR | always `'active'` (deprecated filtered out) |
| `revision_count` | INTEGER | cumulative HELIX revisions |
| `changes_30d` | BIGINT | rows in `tool_change_log` within 30 days |
| `avg_churn` | DOUBLE | mean `churn_ratio` over 30-day window |
| `open_iterations` | BIGINT | rows in `tool_stabilization_log` with `closed_at IS NULL` |
| `oldest_open_at` | TIMESTAMPTZ | earliest open iteration timestamp |
| `last_closed_complexity` | INTEGER | `complexity_after` of latest closed iteration |
| `signal` | VARCHAR | classification: see below |

### `signal` classification

Priority order (first match wins):

| Signal | Condition | Meaning |
|---|---|---|
| `STALE_ITERATION` | `open_iterations > 0` AND open > 30 days | iteration forgotten, close or revert |
| `HOT` | `revision_count ≥ 3` AND `changes_30d ≥ 3` | active hotspot, needs intervention |
| `WATCH` | `revision_count ≥ 3` | historically churned, currently quiet |
| `IN_PROGRESS` | open iteration exists (< 30 days) | working as designed |
| `OK` | else | nothing to do |

### Example queries

```sql
-- Anything that needs attention right now
SELECT tool_name, signal, revision_count, changes_30d, open_iterations
FROM   v_tool_stability_signal
WHERE  signal IN ('HOT', 'STALE_ITERATION')
ORDER  BY revision_count DESC;

-- Count by signal — quick health pulse
SELECT signal, COUNT(*)
FROM   v_tool_stability_signal
GROUP  BY signal;
```

---

## Deferred: `v_tool_perf_30d`

Original DB114 review proposed this third view (call count / p95 latency / error rate per tool per 30-day window). It requires an `mcp_tool_metrics` fact table that is **not yet in the schema**.

Why we did not work around it:

- `analysis_history` records only full analysis runs, not query-class MCP calls (`bio_artifact_search`, `bio_history_lookup`, `bio_tool_health`…). About 80% of MCP traffic would be invisible.
- `analysis_history.status='stale'` is not the same as a real failure, so error-rate would be biased.

See **P1-D** in [PROGRESS.md](../PROGRESS.md):

1. Migration v20 — `mcp_tool_metrics(metric_id, tool_name, tool_id, called_at, duration_ms, status, error_class, requested_by)`
2. `server/bio_memory_server.py::call_tool()` instrumentation wrapper
3. Accumulate ≥ 1 week of real calls
4. Then build `v_tool_perf_30d` as a follow-up migration

---

## Why no `bio_tool_health` rewrite

The original plan proposed switching `bio_tool_health` MCP tool to `SELECT FROM v_tool_stability_signal`. After reading [analysis/tool_registry.py:914](../analysis/tool_registry.py#L914), the existing `tool_health_report()` already returns a richer payload (`hot_zones`, `open_stabilizations`, `stale_analyses`, `prune_candidates`, `regression_zones`) than the view exposes.

The view is for **ad-hoc SQL / future dashboards**, not a drop-in replacement. Both surface the same fact tables in complementary shapes.

---

## Migration & verification

```bash
# Apply
PYTHONPATH=. .venv/bin/python scripts/20_migrate_schema_v19.py

# Verify
PYTHONPATH=. .venv/bin/python -c "
import duckdb
con = duckdb.connect('bio_memory.duckdb', read_only=True)
con.execute('LOAD vss')
print(con.execute('SELECT COUNT(*) FROM v_analysis_throughput_by_sample_type').fetchone())
print(con.execute('SELECT COUNT(*) FROM v_tool_stability_signal').fetchone())
"

# Test
PYTHONPATH=. .venv/bin/python -m pytest tests/test_star_schema.py
```

Idempotent (`CREATE OR REPLACE VIEW`); safe to re-run.
