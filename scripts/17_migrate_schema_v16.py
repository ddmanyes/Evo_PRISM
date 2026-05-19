"""
Migration v16 — ENGRAM Provenance & Lineage (Phase 9B-1/9B-2/9B-3).

Adds to analysis_artifacts:
  input_data_hash  VARCHAR  — SHA256[:16] of input data paths + mtimes
  code_hash        VARCHAR  — SHA256[:16] of the Python function source
  env_hash         VARCHAR  — SHA256[:16] of key env vars + package versions

Creates:
  artifact_relations — directed graph of artifact-to-artifact edges
    (src_artifact_id, dst_artifact_id, relation_type)
    relation_type: 'derived_from' | 'used_by' | 'compared_with'

Creates view:
  tool_artifact_lineage — three-table pre-join (artifacts + history + tools)

DuckDB 1.5.x cannot ADD COLUMN to a table referenced by a FK.
Strategy: recreate analysis_artifacts (same as v14 pattern) with new columns.

Safe to run multiple times (idempotent checks via information_schema).
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


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = ? AND table_schema = 'main'",
        [table],
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

        # ------------------------------------------------------------------
        # Step 1: Add provenance columns to analysis_artifacts (9B-1)
        # DuckDB 1.5.x ADD COLUMN is blocked when a FK references the table.
        # Use the same recreate-table strategy as v14.
        # ------------------------------------------------------------------
        if not _col_exists(con, "analysis_artifacts", "input_data_hash"):
            print("Adding provenance columns via table rebuild...")

            for idx in [
                "idx_artifacts_analysis_id",
                "idx_artifacts_subtype",
                "idx_artifacts_hnsw",
                "uq_artifacts_run_subtype_label",
            ]:
                con.execute(f"DROP INDEX IF EXISTS {idx}")

            # Backup blob table (FK blocks RENAME).
            # Persistent table (not TEMP) so data survives if the session is interrupted
            # mid-migration on ExFAT (no-journal FS — data loss window is real).
            con.execute("DROP TABLE IF EXISTS _blob_backup_v16_v16")
            con.execute(
                """
                CREATE TABLE _blob_backup_v16_v16 AS
                SELECT artifact_id, inline_data FROM analysis_artifact_blobs
                """
            )
            backed_up = con.execute("SELECT COUNT(*) FROM _blob_backup_v16_v16").fetchone()[0]
            print(f"  Backed up {backed_up} blob rows")

            # Backup artifact_relations if it already exists (persistent, same reason)
            rel_backup = _table_exists(con, "artifact_relations")
            if rel_backup:
                con.execute("DROP TABLE IF EXISTS _rel_backup_v16_v16")
                con.execute(
                    "CREATE TABLE _rel_backup_v16_v16 AS SELECT * FROM artifact_relations"
                )

            con.execute("DROP TABLE IF EXISTS artifact_relations")
            con.execute("DROP TABLE IF EXISTS analysis_artifact_blobs")
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
                    input_data_hash  VARCHAR,
                    code_hash        VARCHAR,
                    env_hash         VARCHAR,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            con.execute(
                """
                INSERT INTO analysis_artifacts
                    (artifact_id, analysis_id, artifact_type, artifact_subtype,
                     label, file_path, file_size_kb, mime_type, embedding,
                     input_data_hash, code_hash, env_hash, created_at)
                SELECT artifact_id, analysis_id, artifact_type, artifact_subtype,
                       label, file_path, file_size_kb, mime_type, embedding,
                       NULL, NULL, NULL, created_at
                FROM   analysis_artifacts_old
                """
            )
            copied = con.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()[0]
            print(f"  Rebuilt analysis_artifacts with provenance columns: {copied} rows")
            con.execute("DROP TABLE analysis_artifacts_old")

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
            con.execute(
                """
                INSERT INTO analysis_artifact_blobs (artifact_id, inline_data)
                SELECT b.artifact_id, b.inline_data
                FROM   _blob_backup_v16 b
                WHERE  b.artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                """
            )
            restored = con.execute(
                "SELECT COUNT(*) FROM analysis_artifact_blobs"
            ).fetchone()[0]
            con.execute("DROP TABLE _blob_backup_v16")
            print(f"  Restored {restored} blob rows")

            # Rebuild indexes
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_analysis_id "
                "ON analysis_artifacts (analysis_id)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_subtype "
                "ON analysis_artifacts (artifact_subtype)"
            )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_run_subtype_label "
                "ON analysis_artifacts (analysis_id, artifact_subtype, label)"
            )
            try:
                con.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_artifacts_hnsw
                    ON analysis_artifacts USING HNSW (embedding)
                    WITH (metric = 'cosine')
                    """
                )
                print("  Rebuilt idx_artifacts_hnsw — OK")
            except Exception as e:
                print(f"  WARNING: idx_artifacts_hnsw rebuild failed: {e}")

            # Restore artifact_relations if it existed before
            if rel_backup:
                con.execute(
                    """
                    CREATE TABLE artifact_relations (
                        relation_id     UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
                        src_artifact_id UUID    NOT NULL
                                                REFERENCES analysis_artifacts(artifact_id),
                        dst_artifact_id UUID    NOT NULL
                                                REFERENCES analysis_artifacts(artifact_id),
                        relation_type   VARCHAR NOT NULL,
                        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                con.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_rel_src_dst_type "
                    "ON artifact_relations (src_artifact_id, dst_artifact_id, relation_type)"
                )
                con.execute(
                    """
                    INSERT INTO artifact_relations
                        (relation_id, src_artifact_id, dst_artifact_id,
                         relation_type, created_at)
                    SELECT relation_id, src_artifact_id, dst_artifact_id,
                           relation_type, created_at
                    FROM   _rel_backup_v16
                    WHERE  src_artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                      AND  dst_artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                    """
                )
                con.execute("DROP TABLE _rel_backup_v16")
                print("  Restored artifact_relations from backup")

            print("Provenance columns added — OK")
        else:
            print("Provenance columns already present — skipped")

        # ------------------------------------------------------------------
        # Step 2: Create artifact_relations table (9B-2)
        # ------------------------------------------------------------------
        if not _table_exists(con, "artifact_relations"):
            con.execute(
                """
                CREATE TABLE artifact_relations (
                    relation_id     UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
                    src_artifact_id UUID    NOT NULL
                                            REFERENCES analysis_artifacts(artifact_id),
                    dst_artifact_id UUID    NOT NULL
                                            REFERENCES analysis_artifacts(artifact_id),
                    relation_type   VARCHAR NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_src "
                "ON artifact_relations (src_artifact_id)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_dst "
                "ON artifact_relations (dst_artifact_id)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_type "
                "ON artifact_relations (relation_type)"
            )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_rel_src_dst_type "
                "ON artifact_relations (src_artifact_id, dst_artifact_id, relation_type)"
            )
            print("Table: artifact_relations — OK")
        else:
            print("artifact_relations already exists — skipped")

        # ------------------------------------------------------------------
        # Step 3: tool_artifact_lineage view (9B-3)
        # ------------------------------------------------------------------
        con.execute("DROP VIEW IF EXISTS tool_artifact_lineage")
        con.execute(
            """
            CREATE VIEW tool_artifact_lineage AS
            SELECT
                aa.artifact_id,
                aa.label,
                aa.artifact_subtype,
                aa.input_data_hash,
                aa.code_hash,
                aa.env_hash,
                ah.analysis_id,
                ah.analysis_type,
                ah.sample_id,
                ah.started_at,
                t.tool_id,
                t.tool_name,
                t.version         AS tool_version,
                t.content_hash    AS tool_source_hash,
                t.revision_count
            FROM   analysis_artifacts  aa
            JOIN   analysis_history    ah USING (analysis_id)
            LEFT JOIN tools            t  ON ah.tool_id = t.tool_id
            """
        )
        print("View: tool_artifact_lineage — OK")

        # ------------------------------------------------------------------
        # Step 4: record migration
        # ------------------------------------------------------------------
        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 16"
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (16, now(),
                    'ENGRAM provenance hashes + artifact_relations + lineage view (9B)')
                """
            )
            print("Recorded migration v16")
        else:
            print("Migration v16 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        # Verify
        art_cols = [
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'analysis_artifacts' AND table_schema = 'main' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
        rel_count = con.execute("SELECT COUNT(*) FROM artifact_relations").fetchone()[0]
        print(f"\nanalysis_artifacts columns : {', '.join(art_cols)}")
        print(f"artifact_relations rows    : {rel_count}")
        assert "input_data_hash" in art_cols
        assert "code_hash" in art_cols
        assert "env_hash" in art_cols
        print("\nMigration v16 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
