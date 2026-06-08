"""Migration v21 — MCP metrics fact table & v_tool_perf_30d view (P1-D).

Recreates the `mcp_tool_metrics` table with complete analytical columns
(tool_id, error_class, requested_by) and builds a 30-day performance view.

Views
-----
v_tool_perf_30d
    Fact:  mcp_tool_metrics (calls)
    Dim:   tools (via tool_id軟引用)
    Use:   "MCP 工具 30 天內效能分析（P95 Latency、錯誤率、Rate limit 統計）"

Idempotent: uses DROP TABLE CASCADE and CREATE OR REPLACE VIEW. Safe to re-run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? AND table_schema = 'main'",
        [name],
    ).fetchone()
    return row is not None


def _view_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.views WHERE table_name = ? AND table_schema = 'main'",
        [name],
    ).fetchone()
    return row is not None


def _ddl_mcp_metrics_table() -> str:
    return """
        CREATE TABLE mcp_tool_metrics (
            metric_id    UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            tool_name    VARCHAR NOT NULL,
            tool_id      UUID,                 -- 軟外鍵對照 tools(tool_id)
            duration_ms  INTEGER NOT NULL,
            status       VARCHAR NOT NULL,     -- 'ok' | 'user_error' | 'system_error' | 'rate_limited'
            error_class  VARCHAR,              -- 異常類別名稱，如 'ValueError'
            requested_by VARCHAR NOT NULL DEFAULT 'mcp_client',
            recorded_at  TIMESTAMP DEFAULT now()
        )
    """


def _ddl_mcp_metrics_index() -> str:
    return """
        CREATE INDEX IF NOT EXISTS idx_mcp_metrics_tool_time 
        ON mcp_tool_metrics(tool_name, recorded_at)
    """


def _ddl_tool_perf_view() -> str:
    return """
        CREATE OR REPLACE VIEW v_tool_perf_30d AS
        SELECT
            tool_name,
            COUNT(*) AS n_calls,
            ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95), 2) AS p95_duration_ms,
            ROUND(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS error_rate,
            SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END) AS n_rate_limited
        FROM mcp_tool_metrics
        WHERE recorded_at >= now() - INTERVAL 30 DAY
        GROUP BY tool_name
    """


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail")

        # 1. 建立或重建 mcp_tool_metrics 表（保護已有歷史資料）
        print("Creating/verifying table: mcp_tool_metrics")
        if _table_exists(con, "mcp_tool_metrics"):
            row = con.execute("SELECT COUNT(*) FROM mcp_tool_metrics").fetchone()
            count = row[0] if row else 0
            if count > 0:
                print(f"[v21] mcp_tool_metrics already has {count} rows — skipping DROP")
            else:
                con.execute("DROP TABLE IF EXISTS mcp_tool_metrics CASCADE")
                con.execute(_ddl_mcp_metrics_table())
                con.execute(_ddl_mcp_metrics_index())
        else:
            con.execute(_ddl_mcp_metrics_table())
            con.execute(_ddl_mcp_metrics_index())
        if _table_exists(con, "mcp_tool_metrics"):
            print("Table: mcp_tool_metrics — OK")
        else:
            raise RuntimeError("mcp_tool_metrics not created")

        # 2. 建立 v_tool_perf_30d 視圖
        print("Creating view: v_tool_perf_30d")
        con.execute(_ddl_tool_perf_view())
        if _view_exists(con, "v_tool_perf_30d"):
            print("View: v_tool_perf_30d — OK")
        else:
            raise RuntimeError("v_tool_perf_30d not created")

        # 3. 註冊 migration version 21
        row = con.execute("SELECT 1 FROM schema_migrations WHERE version = 21").fetchone()
        if not row:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (21, now(),
                    'P1-D: MCP metrics fact table + v_tool_perf_30d view')
                """
            )
            print("Recorded migration v21")
        else:
            print("Migration v21 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")
        print("\nMigration v21 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
