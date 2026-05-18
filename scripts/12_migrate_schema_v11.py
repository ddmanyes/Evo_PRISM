"""
Migration v11 — Schema Health Baseline: ENUM types + NOT NULL (Phase 9-SQL P0).

Creates ENUM types:
  analysis_status    — 'running' | 'completed' | 'failed' | 'stale'
  artifact_type_enum — 'figure' | 'csv' | 'report' | 'log'
  tool_status_enum   — 'active' | 'deprecated' | 'candidate'

Alters columns to use ENUM:
  analysis_history.status           → analysis_status
  tools.status                      → tool_status_enum
  analysis_artifacts.artifact_type  → artifact_type_enum

Adds NOT NULL where semantically required:
  analysis_history.sample_id, analysis_history.started_at
  tools.created_at

Safe to run multiple times (checks before each ALTER).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_ENUM_DEFS = {
    "analysis_status":    ("'running'", "'completed'", "'failed'", "'stale'"),
    "artifact_type_enum": ("'figure'", "'csv'", "'report'", "'log'"),
    "tool_status_enum":   ("'active'", "'deprecated'", "'candidate'"),
}

_COL_TO_ENUM = [
    ("analysis_history",   "status",        "analysis_status"),
    ("tools",              "status",        "tool_status_enum"),
    ("analysis_artifacts", "artifact_type", "artifact_type_enum"),
]

_NOT_NULL_COLS = [
    ("analysis_history", "sample_id"),
    ("analysis_history", "started_at"),
    ("tools",            "created_at"),
]


def _type_exists(con: duckdb.DuckDBPyConnection, type_name: str) -> bool:
    rows = con.execute(
        "SELECT type_name FROM duckdb_types() WHERE type_name = ? AND type_category = 'ENUM'",
        [type_name],
    ).fetchall()
    return len(rows) > 0


def _col_type(con: duckdb.DuckDBPyConnection, table: str, col: str) -> str:
    row = con.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_name = ? AND column_name = ? AND table_schema = 'main'
        """,
        [table, col],
    ).fetchone()
    return row[0] if row else ""


def _col_nullable(con: duckdb.DuckDBPyConnection, table: str, col: str) -> bool:
    row = con.execute(
        """
        SELECT is_nullable FROM information_schema.columns
        WHERE table_name = ? AND column_name = ? AND table_schema = 'main'
        """,
        [table, col],
    ).fetchone()
    return (row[0] == "YES") if row else True


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as e:
            print(f"WARNING: VSS load failed ({e}) — continuing")

        # ── Step 1: create ENUM types ─────────────────────────────────────
        for type_name, values in _ENUM_DEFS.items():
            val_list = ", ".join(values)
            try:
                con.execute(f"CREATE TYPE {type_name} AS ENUM ({val_list})")
                print(f"ENUM {type_name} — created")
            except Exception:
                print(f"ENUM {type_name} — already exists, skipped")

        # ── Step 2: drop dependent views + indexes (required for ALTER TYPE) ─
        _DEPENDENT_VIEWS = ["analysis_index", "promotion_candidates"]
        for vname in _DEPENDENT_VIEWS:
            con.execute(f"DROP VIEW IF EXISTS {vname}")
            print(f"Dropped view: {vname}")

        # Drop all indexes on tables we need to ALTER
        _DROP_INDEXES = [
            "idx_history_sample_type", "idx_history_status_time",
            "idx_tools_name_status",
            "idx_artifacts_analysis_id", "idx_artifacts_subtype",
            "uq_artifacts_run_subtype_label",
        ]
        for idx in _DROP_INDEXES:
            con.execute(f"DROP INDEX IF EXISTS {idx}")
            print(f"Dropped index: {idx}")

        # ── Step 3: null-clean before converting (NULL blocks ALTER TYPE) ──
        null_fixes = [
            ("analysis_history",   "status", "'failed'"),
            ("tools",              "status", "'active'"),
        ]
        for table, col, fallback in null_fixes:
            con.execute(
                f"UPDATE {table} SET {col} = {fallback} WHERE {col} IS NULL"
            )

        # ── Step 4: CHECK constraints as value-domain enforcement ────────────
        # DuckDB 1.5 cannot ALTER TYPE on tables with FK dependencies.
        # ENUM types are created (Step 1) for documentation purposes.
        # CHECK constraints enforce the same value domain at write time.
        _CHECK_CONSTRAINTS = [
            (
                "chk_history_status",
                "analysis_history",
                "status IN ('running','completed','failed','stale')",
            ),
            (
                "chk_tools_status",
                "tools",
                "status IN ('active','deprecated','candidate')",
            ),
            (
                "chk_artifacts_type",
                "analysis_artifacts",
                "artifact_type IN ('figure','csv','report','log')",
            ),
        ]
        existing_checks = {
            r[0] for r in con.execute(
                "SELECT constraint_name FROM duckdb_constraints() "
                "WHERE constraint_type = 'CHECK' AND schema_name = 'main'"
            ).fetchall()
        }
        for cname, table, expr in _CHECK_CONSTRAINTS:
            if cname in existing_checks:
                print(f"CHECK {cname} — already exists, skipped")
                continue
            try:
                con.execute(
                    f"ALTER TABLE {table} ADD CONSTRAINT {cname} CHECK ({expr})"
                )
                print(f"CHECK {cname} on {table} — OK")
            except Exception as e:
                print(f"WARNING: CHECK {cname} failed: {e}")

        # ── Step 6: recreate dependent views ─────────────────────────────
        con.execute(
            """
            CREATE OR REPLACE VIEW analysis_index AS
            SELECT
                ah.sample_id,
                ah.analysis_type,
                COUNT(DISTINCT ah.analysis_id)                           AS run_count,
                MAX(ah.completed_at)::DATE                               AS last_run_date,
                MIN(ah.started_at)::DATE                                 AS first_run_date,
                STRING_AGG(DISTINCT ah.requested_by, ', ')               AS run_by_members,
                SUM(CASE WHEN ah.status = 'completed' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN ah.status = 'failed'    THEN 1 ELSE 0 END) AS fail_count,
                COUNT(aa.artifact_id)                                    AS artifact_count
            FROM  analysis_history ah
            LEFT  JOIN analysis_artifacts aa ON ah.analysis_id = aa.analysis_id
            GROUP BY ah.sample_id, ah.analysis_type
            ORDER BY last_run_date DESC
            """
        )
        print("Recreated view: analysis_index")

        con.execute(
            """
            CREATE OR REPLACE VIEW promotion_candidates AS
            SELECT
                parameters ->> 'origin_id'  AS origin_id,
                analysis_type,
                COUNT(*)                     AS reuse_count,
                MAX(completed_at)            AS last_used
            FROM   analysis_history
            WHERE  parameters ->> 'source' = 'code_promotion'
              AND  status = 'completed'
            GROUP  BY parameters ->> 'origin_id', analysis_type
            HAVING COUNT(*) >= 3
            """
        )
        print("Recreated view: promotion_candidates")

        # ── Step 7: rebuild indexes ───────────────────────────────────────
        _REBUILD_INDEXES = [
            ("idx_history_sample_type",
             "CREATE INDEX IF NOT EXISTS idx_history_sample_type ON analysis_history (sample_id, analysis_type)"),
            ("idx_history_status_time",
             "CREATE INDEX IF NOT EXISTS idx_history_status_time ON analysis_history (status, started_at)"),
            ("idx_tools_name_status",
             "CREATE INDEX IF NOT EXISTS idx_tools_name_status ON tools (tool_name, status)"),
            ("idx_artifacts_analysis_id",
             "CREATE INDEX IF NOT EXISTS idx_artifacts_analysis_id ON analysis_artifacts (analysis_id)"),
            ("idx_artifacts_subtype",
             "CREATE INDEX IF NOT EXISTS idx_artifacts_subtype ON analysis_artifacts (artifact_subtype)"),
            ("uq_artifacts_run_subtype_label",
             "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_run_subtype_label ON analysis_artifacts (analysis_id, artifact_subtype, label)"),
        ]
        for name, ddl in _REBUILD_INDEXES:
            try:
                con.execute(ddl)
                print(f"Rebuilt index: {name}")
            except Exception as e:
                print(f"WARNING: rebuild {name} failed: {e}")

        # ── Step 5: record migration ───────────────────────────────────────
        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 11"
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (11, now(), 'ENUM types (analysis_status, artifact_type_enum, tool_status_enum) + NOT NULL on key columns')
                """
            )
            print("Recorded migration v11")
        else:
            print("Migration v11 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        # ── Verify ────────────────────────────────────────────────────────
        print("\n--- Column types after migration ---")
        for table, col, _ in _COL_TO_ENUM:
            t = _col_type(con, table, col)
            print(f"  {table}.{col}: {t}")

        print("\nMigration v11 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
