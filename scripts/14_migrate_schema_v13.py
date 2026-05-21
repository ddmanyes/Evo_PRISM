"""
Migration v13 — Composite indexes + UNIQUE constraints (Phase 9-SQL P1).

Adds indexes:
  idx_history_sample_type         — analysis_history(sample_id, analysis_type)
  idx_history_status_time         — analysis_history(status, started_at)
  idx_tools_name_status           — tools(tool_name, status)

Adds UNIQUE index:
  uq_artifacts_run_subtype_label  — analysis_artifacts(analysis_id, artifact_subtype, label)
  (prevents duplicate artifact registration for the same run)

FK strategy documented (DuckDB 1.x does not enforce ON DELETE;
application-layer guards are noted inline):
  analysis_history.sample_id  → sample_registry  : RESTRICT
  analysis_history.tool_id    → tools             : RESTRICT
  analysis_artifacts.analysis_id → analysis_history : CASCADE

Safe to run multiple times (CREATE INDEX IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_INDEXES = [
    (
        "idx_history_sample_type",
        "CREATE INDEX IF NOT EXISTS idx_history_sample_type "
        "ON analysis_history (sample_id, analysis_type)",
    ),
    (
        "idx_history_status_time",
        "CREATE INDEX IF NOT EXISTS idx_history_status_time "
        "ON analysis_history (status, started_at)",
    ),
    (
        "idx_tools_name_status",
        "CREATE INDEX IF NOT EXISTS idx_tools_name_status ON tools (tool_name, status)",
    ),
]

_UNIQUE_INDEXES = [
    (
        "uq_artifacts_run_subtype_label",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_run_subtype_label
        ON analysis_artifacts (analysis_id, artifact_subtype, label)
        """,
    ),
]

_FK_POLICY = (
    "FK ON DELETE policy (app-layer enforced — DuckDB 1.x does not support ON DELETE): "
    "sample→history RESTRICT | tool→history RESTRICT | history→artifacts CASCADE"
)


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as e:
            print(f"WARNING: VSS load failed ({e}) — continuing")

        for name, ddl in _INDEXES:
            try:
                con.execute(ddl)
                print(f"Index: {name} — OK")
            except Exception as e:
                print(f"WARNING: {name} failed: {e}")

        for name, ddl in _UNIQUE_INDEXES:
            try:
                con.execute(ddl)
                print(f"Unique index: {name} — OK")
            except Exception as e:
                print(f"WARNING: {name} failed: {e}")

        print(f"\nFK policy: {_FK_POLICY}")

        existing = con.execute("SELECT 1 FROM schema_migrations WHERE version = 13").fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (13, now(), ?)
                """,
                [
                    "Composite indexes on analysis_history + tools; "
                    "UNIQUE index on artifacts(analysis_id,subtype,label); "
                    "FK ON DELETE policy documented"
                ],
            )
            print("Recorded migration v13")
        else:
            print("Migration v13 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        idx_rows = con.execute(
            """
            SELECT index_name, table_name
            FROM   duckdb_indexes()
            WHERE  index_name IN (
                'idx_history_sample_type', 'idx_history_status_time',
                'idx_tools_name_status', 'uq_artifacts_run_subtype_label'
            )
            ORDER  BY table_name, index_name
            """
        ).fetchall()
        print("\n--- New indexes ---")
        for r in idx_rows:
            print(f"  {r[1]}.{r[0]}")

        print("\nMigration v13 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
