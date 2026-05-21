# Star Schema Views (P1-C)

**Migration**: v19 ([scripts/20_migrate_schema_v19.py](../scripts/20_migrate_schema_v19.py)) & v21 ([scripts/22_migrate_schema_v21_mcp_metrics.py](../scripts/22_migrate_schema_v21_mcp_metrics.py))
**Tests**: [tests/test_star_schema.py](../tests/test_star_schema.py) — 13 passed
**Status**: 3 views shipped; fully implemented (P1-D completed)

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

## View 3 — `v_tool_perf_30d`

**Source**: `mcp_tool_metrics` (Fact)
**Grain**: one row per `tool_name`
**Use cases**:

- "Which MCP tools are slowest on average or have the highest P95 latency?"
- "What is the error rate for each tool over the last 30 days?"
- "How many rate limit rejections happened on a specific tool?"

### Columns

| Column | Type | Note |
|---|---|---|
| `tool_name` | VARCHAR | name of the MCP tool |
| `n_calls` | BIGINT | total calls recorded for this tool |
| `avg_duration_ms` | DOUBLE | average call duration |
| `p95_duration_ms` | DOUBLE | P95 call duration (`quantile_cont(duration_ms, 0.95)`) |
| `error_rate` | DOUBLE | percentage of calls with status != 'ok' |
| `n_rate_limited` | BIGINT | total calls rejected due to rate limits |

**Filter**: `WHERE recorded_at >= now() - INTERVAL 30 DAY`

### Example queries

```sql
-- Top 5 slowest tools by P95 latency in the last 30 days
SELECT tool_name, n_calls, avg_duration_ms, p95_duration_ms
FROM   v_tool_perf_30d
ORDER  BY p95_duration_ms DESC
LIMIT  5;

-- Tools with error rate exceeding 10%
SELECT tool_name, n_calls, error_rate
FROM   v_tool_perf_30d
WHERE  error_rate > 10.0
ORDER  BY error_rate DESC;
```

---

## Why no `bio_tool_health` rewrite

The original plan proposed switching `bio_tool_health` MCP tool to `SELECT FROM v_tool_stability_signal`. After reading [analysis/tool_registry.py:914](../analysis/tool_registry.py#L914), the existing `tool_health_report()` already returns a richer payload (`hot_zones`, `open_stabilizations`, `stale_analyses`, `prune_candidates`, `regression_zones`) than the view exposes.

The view is for **ad-hoc SQL / future dashboards**, not a drop-in replacement. Both surface the same fact tables in complementary shapes.

---

## Migration & verification

```bash
# Apply v19 and v21
PYTHONPATH=. .venv/bin/python scripts/20_migrate_schema_v19.py
PYTHONPATH=. .venv/bin/python scripts/22_migrate_schema_v21_mcp_metrics.py

# Verify
PYTHONPATH=. .venv/bin/python -c "
import duckdb
con = duckdb.connect('bio_memory.duckdb', read_only=True)
print('throughput views:', con.execute('SELECT COUNT(*) FROM v_analysis_throughput_by_sample_type').fetchone())
print('stability signal views:', con.execute('SELECT COUNT(*) FROM v_tool_stability_signal').fetchone())
print('tool perf views:', con.execute('SELECT COUNT(*) FROM v_tool_perf_30d').fetchone())
"

# Test
PYTHONPATH=. .venv/bin/python -m pytest tests/test_star_schema.py
```

Idempotent (`CREATE OR REPLACE VIEW`); safe to re-run.
