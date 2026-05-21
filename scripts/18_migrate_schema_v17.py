"""
Migration v17 — Matryoshka dual-layer HNSW index (Phase 9D-1/9D-2).

Adds to analysis_artifacts:
  embedding_256  FLOAT[256]  — first 256 dims of the 1024-dim bge-m3 vector
                               (Matryoshka sub-vector, ~95% recall at 1/4 cost)

Creates:
  idx_artifacts_hnsw_256  — HNSW cosine index on embedding_256 for fast coarse scan

search_artifacts() uses this when MATRYOSHKA_ENABLED=true (9D-3):
  Phase 1: embedding_256 HNSW → top-50 candidates
  Phase 2: full 1024-dim re-rank → top-N

DuckDB 1.5.x ADD COLUMN is blocked when FKs reference the table.
Strategy: same recreate-table pattern as v14/v16.

Safe to run multiple times (idempotent via information_schema check).
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
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? AND table_schema = 'main'",
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
        # Step 1: Add embedding_256 column via table rebuild (9D-1)
        # ------------------------------------------------------------------
        if not _col_exists(con, "analysis_artifacts", "embedding_256"):
            print("Adding embedding_256 column via table rebuild...")

            for idx in [
                "idx_artifacts_analysis_id",
                "idx_artifacts_subtype",
                "idx_artifacts_hnsw",
                "idx_artifacts_hnsw_256",
                "uq_artifacts_run_subtype_label",
            ]:
                con.execute(f"DROP INDEX IF EXISTS {idx}")

            # Backup blob table (FK blocks RENAME).
            # Persistent table (not TEMP) so data survives session interruption on ExFAT.
            con.execute("DROP TABLE IF EXISTS _blob_backup_v17_v17")
            con.execute(
                """
                CREATE TABLE _blob_backup_v17_v17 AS
                SELECT artifact_id, inline_data FROM analysis_artifact_blobs
                """
            )
            backed_up = con.execute("SELECT COUNT(*) FROM _blob_backup_v17_v17").fetchone()[0]
            print(f"  Backed up {backed_up} blob rows")

            # Backup artifact_relations if it exists (persistent, same reason)
            rel_backup = _table_exists(con, "artifact_relations")
            if rel_backup:
                con.execute("DROP TABLE IF EXISTS _rel_backup_v17_v17")
                con.execute("CREATE TABLE _rel_backup_v17_v17 AS SELECT * FROM artifact_relations")

            con.execute("DROP TABLE IF EXISTS artifact_relations")
            con.execute("DROP TABLE IF EXISTS analysis_artifact_blobs")
            con.execute("ALTER TABLE analysis_artifacts RENAME TO analysis_artifacts_old")

            # Detect which optional columns exist (provenance from v16)
            old_cols = {
                r[0]
                for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'analysis_artifacts_old' AND table_schema = 'main'"
                ).fetchall()
            }
            has_provenance = "input_data_hash" in old_cols

            provenance_ddl = (
                "input_data_hash  VARCHAR,\n"
                "                    code_hash        VARCHAR,\n"
                "                    env_hash         VARCHAR,"
                if has_provenance
                else ""
            )
            provenance_cols = "input_data_hash, code_hash, env_hash," if has_provenance else ""

            con.execute(
                f"""
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
                    embedding_256    FLOAT[256],
                    {provenance_ddl}
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            con.execute(
                f"""
                INSERT INTO analysis_artifacts
                    (artifact_id, analysis_id, artifact_type, artifact_subtype,
                     label, file_path, file_size_kb, mime_type,
                     embedding, embedding_256,
                     {provenance_cols}
                     created_at)
                SELECT artifact_id, analysis_id, artifact_type, artifact_subtype,
                       label, file_path, file_size_kb, mime_type,
                       embedding,
                       CASE WHEN embedding IS NOT NULL
                            THEN embedding[1:256]
                            ELSE NULL
                       END,
                       {provenance_cols}
                       created_at
                FROM   analysis_artifacts_old
                """
            )
            copied = con.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()[0]
            print(f"  Rebuilt analysis_artifacts with embedding_256: {copied} rows")
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
                FROM   _blob_backup_v17 b
                WHERE  b.artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                """
            )
            restored = con.execute("SELECT COUNT(*) FROM analysis_artifact_blobs").fetchone()[0]
            con.execute("DROP TABLE _blob_backup_v17")
            print(f"  Restored {restored} blob rows")

            # Restore artifact_relations
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
                    FROM   _rel_backup_v17
                    WHERE  src_artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                      AND  dst_artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                    """
                )
                con.execute("DROP TABLE _rel_backup_v17")
                print("  Restored artifact_relations from backup")

            print("embedding_256 column added — OK")
        else:
            print("embedding_256 column already present — skipped")

        # ------------------------------------------------------------------
        # Step 2: Rebuild all indexes including 256-dim coarse HNSW (9D-2)
        # ------------------------------------------------------------------
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
            print("Index: idx_artifacts_hnsw (1024-dim) — OK")
        except Exception as e:
            print(f"WARNING: idx_artifacts_hnsw failed: {e}")

        try:
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_artifacts_hnsw_256
                ON analysis_artifacts USING HNSW (embedding_256)
                WITH (metric = 'cosine')
                """
            )
            print("Index: idx_artifacts_hnsw_256 (256-dim coarse) — OK")
        except Exception as e:
            print(f"WARNING: idx_artifacts_hnsw_256 failed: {e}")

        # ------------------------------------------------------------------
        # Step 3: Recreate tool_artifact_lineage view (references analysis_artifacts)
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
        print("View: tool_artifact_lineage rebuilt — OK")

        # ------------------------------------------------------------------
        # Step 4: record migration
        # ------------------------------------------------------------------
        existing = con.execute("SELECT 1 FROM schema_migrations WHERE version = 17").fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (17, now(),
                    'Matryoshka dual-layer HNSW: embedding_256 + idx_artifacts_hnsw_256 (9D)')
                """
            )
            print("Recorded migration v17")
        else:
            print("Migration v17 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        art_cols = [
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'analysis_artifacts' AND table_schema = 'main' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
        print(f"\nanalysis_artifacts columns : {', '.join(art_cols)}")
        assert "embedding_256" in art_cols
        print("\nMigration v17 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
