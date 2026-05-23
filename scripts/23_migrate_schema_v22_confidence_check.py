"""Migration v22 — Add confidence column & CHECK constraint to artifact_relations table (AB1).

Adds a `confidence` column to the `artifact_relations` table representing edge weight/certainty.
Enforces the constraint: `confidence IN (0.6, 0.9, 1.0)`.

Idempotent: Re-runnable, safe to run.
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


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to database: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail")

        # Check if confidence column already exists
        cols = con.execute("PRAGMA table_info(artifact_relations)").fetchall()
        has_confidence = any(c[1] == "confidence" for c in cols)

        if not has_confidence:
            print("Migrating artifact_relations to include confidence with CHECK constraint...")
            
            # Backup existing relations
            has_table = _table_exists(con, "artifact_relations")
            if has_table:
                con.execute("CREATE TABLE _rel_backup_v22 AS SELECT * FROM artifact_relations")
                con.execute("DROP TABLE artifact_relations CASCADE")
            
            # Recreate table with confidence and CHECK constraint
            con.execute(
                """
                CREATE TABLE artifact_relations (
                    relation_id     UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
                    src_artifact_id UUID        NOT NULL,
                    dst_artifact_id UUID        NOT NULL,
                    relation_type   VARCHAR     NOT NULL,
                    confidence      DOUBLE      NOT NULL DEFAULT 1.0 CHECK (confidence IN (0.6, 0.9, 1.0)),
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_rel_src_dst_type "
                "ON artifact_relations (src_artifact_id, dst_artifact_id, relation_type)"
            )
            
            # Restore from backup
            if has_table:
                con.execute(
                    """
                    INSERT INTO artifact_relations
                        (relation_id, src_artifact_id, dst_artifact_id, relation_type, confidence, created_at)
                    SELECT relation_id, src_artifact_id, dst_artifact_id, relation_type, 1.0, created_at
                    FROM _rel_backup_v22
                    """
                )
                con.execute("DROP TABLE _rel_backup_v22")
                print("  Restored artifact_relations from backup (default confidence=1.0)")
            
            print("Successfully added confidence column with CHECK constraint.")
        else:
            print("Column 'confidence' already exists — skipped.")

        # Record migration
        row = con.execute("SELECT 1 FROM schema_migrations WHERE version = 22").fetchone()
        if not row:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (22, now(), 'AB1: Add confidence column and CHECK constraint to artifact_relations')
                """
            )
            print("Recorded migration v22")
        else:
            print("Migration v22 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")
        print("\nMigration v22 complete.")


if __name__ == "__main__":
    migrate()
