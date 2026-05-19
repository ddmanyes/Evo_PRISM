"""Migration v19 — HELIX / ENGRAM Star Schema Views (P1-C).

Creates two read-only views over existing fact/dim tables for analytical
drill-down. No base-table schema changes.

Views
-----
v_analysis_throughput_by_sample_type
    Fact:  analysis_history (events)
    Dim:   sample_registry
    Time:  weekly bucket via date_trunc('week', completed_at)
    Use:   "Visium HD 每週分析吞吐量 / 失敗率趨勢"

v_tool_stability_signal
    Fact:  tool_change_log (revisions), tool_stabilization_log (iterations)
    Dim:   tools
    Use:   "active 工具的綜合穩定性訊號 (OK / WATCH / HOT / IN_PROGRESS / STALE_ITERATION)"
    Integrates revision_count, 30-day churn, open iteration age, last complexity.

Not in this migration
---------------------
v_tool_perf_30d requires an `mcp_tool_metrics` fact table that does not yet
exist in the schema. See P1-D in PROGRESS.md for its prerequisites.

Idempotent: uses CREATE OR REPLACE VIEW. Safe to re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


VIEW_NAMES = (
    "v_analysis_throughput_by_sample_type",
    "v_tool_stability_signal",
)


def _view_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.views "
        "WHERE table_name = ? AND table_schema = 'main'",
        [name],
    ).fetchone()
    return row is not None


def _ddl_throughput() -> str:
    return """
        CREATE OR REPLACE VIEW v_analysis_throughput_by_sample_type AS
        SELECT
            s.data_type,
            s.platform,
            ah.analysis_type,
            date_trunc('week', ah.completed_at) AS week,
            COUNT(*)                            AS n_runs,
            AVG(epoch(ah.completed_at - ah.started_at)) AS avg_seconds,
            SUM(CASE WHEN ah.status = 'completed' THEN 1 ELSE 0 END) AS n_completed,
            SUM(CASE WHEN ah.status = 'failed'    THEN 1 ELSE 0 END) AS n_failed,
            SUM(CASE WHEN ah.status = 'stale'     THEN 1 ELSE 0 END) AS n_stale
        FROM   analysis_history ah
        LEFT JOIN sample_registry s ON s.sample_id = ah.sample_id
        WHERE  ah.completed_at IS NOT NULL
        GROUP  BY s.data_type, s.platform, ah.analysis_type, week
    """


def _ddl_stability_signal() -> str:
    return """
        CREATE OR REPLACE VIEW v_tool_stability_signal AS
        WITH change_30d AS (
            SELECT tool_name,
                   COUNT(*)         AS changes_30d,
                   AVG(churn_ratio) AS avg_churn
            FROM   tool_change_log
            WHERE  changed_at >= now() - INTERVAL 30 DAY
            GROUP  BY tool_name
        ),
        open_iter AS (
            SELECT tool_name,
                   COUNT(*)        AS open_iterations,
                   MIN(created_at) AS oldest_open_at
            FROM   tool_stabilization_log
            WHERE  closed_at IS NULL
            GROUP  BY tool_name
        ),
        last_closed AS (
            SELECT DISTINCT ON (tool_name)
                   tool_name, complexity_after, closed_at
            FROM   tool_stabilization_log
            WHERE  closed_at IS NOT NULL AND complexity_after IS NOT NULL
            ORDER  BY tool_name, closed_at DESC
        )
        SELECT
            t.tool_name,
            t.version,
            t.status,
            t.revision_count,
            COALESCE(c.changes_30d,     0) AS changes_30d,
            COALESCE(c.avg_churn,       0) AS avg_churn,
            COALESCE(o.open_iterations, 0) AS open_iterations,
            o.oldest_open_at,
            lc.complexity_after            AS last_closed_complexity,
            CASE
              WHEN o.open_iterations > 0
                   AND date_diff('day', o.oldest_open_at, now()) > 30 THEN 'STALE_ITERATION'
              WHEN t.revision_count >= 3 AND COALESCE(c.changes_30d, 0) >= 3 THEN 'HOT'
              WHEN t.revision_count >= 3                                     THEN 'WATCH'
              WHEN o.open_iterations > 0                                     THEN 'IN_PROGRESS'
              ELSE 'OK'
            END AS signal
        FROM   tools t
        LEFT JOIN change_30d  c  ON c.tool_name  = t.tool_name
        LEFT JOIN open_iter   o  ON o.tool_name  = t.tool_name
        LEFT JOIN last_closed lc ON lc.tool_name = t.tool_name
        WHERE  t.status = 'active'
    """


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail")

        con.execute(_ddl_throughput())
        if _view_exists(con, "v_analysis_throughput_by_sample_type"):
            print("View: v_analysis_throughput_by_sample_type — OK")
        else:
            raise RuntimeError("v_analysis_throughput_by_sample_type not created")

        con.execute(_ddl_stability_signal())
        if _view_exists(con, "v_tool_stability_signal"):
            print("View: v_tool_stability_signal — OK")
        else:
            raise RuntimeError("v_tool_stability_signal not created")

        row = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 19"
        ).fetchone()
        if not row:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (19, now(),
                    'P1-C: Star schema views — '
                    'v_analysis_throughput_by_sample_type + v_tool_stability_signal')
                """
            )
            print("Recorded migration v19")
        else:
            print("Migration v19 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")
        print("\nMigration v19 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
