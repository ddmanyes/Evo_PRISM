"""
Migration v14 — ENGRAM blob table split (Phase 9A-1).

Splits analysis_artifacts into two tables:
  analysis_artifacts      — metadata only (no inline_data column)
  analysis_artifact_blobs — 1:0..1 blob table (artifact_id PK FK, inline_data TEXT)

This eliminates wide-row penalty during HNSW scans.
artifact_registry.py get_artifacts() / compare_analyses() updated to JOIN blob table.

Safe to run multiple times (CREATE TABLE IF NOT EXISTS / checks before ALTER).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


def _col_exists(con: duckdb.DuckDBPyConnection, table: str, col: str) -> bool:
    row = con.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = ? AND column_name = ? AND table_schema = 'main'
        """,
        [table, col],
    ).fetchone()
    return row is not None


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as e:
            print(f"WARNING: VSS load failed ({e}) — continuing")

        # Step 1: create blob table
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_artifact_blobs (
                artifact_id  UUID PRIMARY KEY
                             REFERENCES analysis_artifacts(artifact_id),
                inline_data  TEXT NOT NULL
            )
            """
        )
        print("Table: analysis_artifact_blobs — OK")

        # Step 2: migrate existing inline_data rows (only if column still exists)
        if _col_exists(con, "analysis_artifacts", "inline_data"):
            con.execute(
                """
                INSERT INTO analysis_artifact_blobs (artifact_id, inline_data)
                SELECT artifact_id, inline_data
                FROM   analysis_artifacts
                WHERE  inline_data IS NOT NULL
                  AND  artifact_id NOT IN (SELECT artifact_id FROM analysis_artifact_blobs)
                """
            )
            migrated = con.execute(
                "SELECT COUNT(*) FROM analysis_artifact_blobs"
            ).fetchone()[0]
            print(f"Migrated inline_data rows to blob table: {migrated}")

            # Step 3: recreate analysis_artifacts without inline_data column.
            # DuckDB 1.5 cannot ALTER TABLE DROP COLUMN when FKs reference the table.
            # Strategy: rename original → _old, recreate without inline_data,
            # copy data, drop _old. FK from analysis_artifact_blobs is also recreated.
            con.execute("DROP INDEX IF EXISTS idx_artifacts_analysis_id")
            con.execute("DROP INDEX IF EXISTS idx_artifacts_subtype")
            con.execute("DROP INDEX IF EXISTS idx_artifacts_hnsw")
            con.execute("DROP INDEX IF EXISTS uq_artifacts_run_subtype_label")

            # Preserve blob data in a temp table before dropping the blob table
            # (required because the FK on blob table blocks RENAME of analysis_artifacts)
            con.execute(
                """
                CREATE TEMP TABLE _blob_backup AS
                SELECT artifact_id, inline_data FROM analysis_artifact_blobs
                """
            )
            backed_up = con.execute("SELECT COUNT(*) FROM _blob_backup").fetchone()[0]
            print(f"Backed up {backed_up} blob rows to _blob_backup")

            con.execute("DROP TABLE IF EXISTS analysis_artifact_blobs")
            print("Temporarily dropped analysis_artifact_blobs for table rebuild")

            con.execute(
                "ALTER TABLE analysis_artifacts RENAME TO analysis_artifacts_old"
            )
            con.execute(
                """
                CREATE TABLE analysis_artifacts (
                    artifact_id      UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
                    analysis_id      UUID    NOT NULL
                                             REFERENCES analysis_history(analysis_id),
                    artifact_type    VARCHAR NOT NULL,
                    artifact_subtype VARCHAR,
                    label            VARCHAR NOT NULL,
                    file_path        VARCHAR,
                    file_size_kb     INTEGER,
                    mime_type        VARCHAR,
                    embedding        FLOAT[1024],
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            con.execute(
                """
                INSERT INTO analysis_artifacts
                    (artifact_id, analysis_id, artifact_type, artifact_subtype,
                     label, file_path, file_size_kb, mime_type, embedding, created_at)
                SELECT artifact_id, analysis_id, artifact_type, artifact_subtype,
                       label, file_path, file_size_kb, mime_type, embedding, created_at
                FROM   analysis_artifacts_old
                """
            )
            copied = con.execute(
                "SELECT COUNT(*) FROM analysis_artifacts"
            ).fetchone()[0]
            print(f"Rebuilt analysis_artifacts (no inline_data): {copied} rows")
            con.execute("DROP TABLE analysis_artifacts_old")
            print("Dropped analysis_artifacts_old")

            # Recreate blob table with FK
            con.execute(
                """
                CREATE TABLE analysis_artifact_blobs (
                    artifact_id  UUID PRIMARY KEY
                                 REFERENCES analysis_artifacts(artifact_id),
                    inline_data  TEXT NOT NULL
                )
                """
            )
            # Restore blobs from backup (only rows whose artifact_id survived the rebuild)
            con.execute(
                """
                INSERT INTO analysis_artifact_blobs (artifact_id, inline_data)
                SELECT b.artifact_id, b.inline_data
                FROM   _blob_backup b
                WHERE  b.artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                """
            )
            restored = con.execute(
                "SELECT COUNT(*) FROM analysis_artifact_blobs"
            ).fetchone()[0]
            con.execute("DROP TABLE _blob_backup")
            print(f"Recreated analysis_artifact_blobs with FK — restored {restored} blob rows")

            # Rebuild indexes
            for name, ddl in [
                ("idx_artifacts_analysis_id",
                 "CREATE INDEX IF NOT EXISTS idx_artifacts_analysis_id ON analysis_artifacts (analysis_id)"),
                ("idx_artifacts_subtype",
                 "CREATE INDEX IF NOT EXISTS idx_artifacts_subtype ON analysis_artifacts (artifact_subtype)"),
                ("uq_artifacts_run_subtype_label",
                 "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_run_subtype_label ON analysis_artifacts (analysis_id, artifact_subtype, label)"),
            ]:
                con.execute(ddl)
                print(f"Rebuilt index: {name}")
            try:
                con.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_artifacts_hnsw
                    ON analysis_artifacts USING HNSW (embedding)
                    WITH (metric = 'cosine')
                    """
                )
                print("Rebuilt idx_artifacts_hnsw (HNSW) — OK")
            except Exception as e:
                print(f"WARNING: idx_artifacts_hnsw rebuild failed: {e}")
        else:
            print("inline_data column already absent — migration already applied")

        # Step 4: (artifact_id is PK on blob table — no separate index needed)

        # Step 5: record migration
        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 14"
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (14, now(), 'ENGRAM blob split: inline_data → analysis_artifact_blobs (9A-1)')
                """
            )
            print("Recorded migration v14")
        else:
            print("Migration v14 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        # Verify
        blob_count = con.execute(
            "SELECT COUNT(*) FROM analysis_artifact_blobs"
        ).fetchone()[0]
        art_cols = [
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'analysis_artifacts' AND table_schema = 'main'"
            ).fetchall()
        ]
        print(f"\nanalysis_artifact_blobs rows : {blob_count}")
        print(f"analysis_artifacts columns   : {', '.join(sorted(art_cols))}")
        assert "inline_data" not in art_cols, "inline_data should have been dropped"

        print("\nMigration v14 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
