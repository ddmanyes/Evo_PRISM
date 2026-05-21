"""
Migration v9 — ENGRAM: Analysis Artifact Registry.

Creates:
  analysis_artifacts   — one row per file produced by an analysis run
  idx_artifacts_hnsw   — HNSW cosine index on embedding (requires VSS extension)

Also updates analysis_index view to include artifact_count.

Safe to run multiple times (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH, EMBEDDING_DIM


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        # Load VSS for HNSW index creation
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
            print("VSS loaded")
        except Exception as e:
            print(f"WARNING: VSS load failed ({e}) — HNSW index will be skipped")

        # Create analysis_artifacts table
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS analysis_artifacts (
                artifact_id      UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
                analysis_id      UUID    NOT NULL
                                         REFERENCES analysis_history(analysis_id),
                artifact_type    VARCHAR NOT NULL,  -- 'figure'|'csv'|'report'|'log'
                artifact_subtype VARCHAR,           -- 'volcano'|'pca'|'heatmap'|'deg_list'|'eda_report'
                label            VARCHAR NOT NULL,  -- human-readable description
                file_path        VARCHAR,           -- absolute path on disk
                inline_data      TEXT,              -- base64 content (≤ 500 KB only)
                file_size_kb     INTEGER,           -- file size in KB
                mime_type        VARCHAR,           -- 'image/png'|'text/csv'|'text/markdown'
                embedding        FLOAT[{EMBEDDING_DIM}],  -- semantic search vector
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        print("Table: analysis_artifacts — OK")

        # HNSW index on embedding
        try:
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_artifacts_hnsw
                ON analysis_artifacts
                USING HNSW (embedding)
                WITH (metric = 'cosine')
                """
            )
            print("Index: idx_artifacts_hnsw (HNSW cosine) — OK")
        except Exception as e:
            print(f"WARNING: HNSW index creation skipped: {e}")

        # Indexes for common query patterns
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_analysis_id
            ON analysis_artifacts (analysis_id)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_subtype
            ON analysis_artifacts (artifact_subtype)
            """
        )
        print("Indexes: analysis_id, artifact_subtype — OK")

        # Update analysis_index view to include artifact_count
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
        print("View: analysis_index (with artifact_count) — OK")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        # Verify
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert "analysis_artifacts" in tables, "analysis_artifacts table missing"

        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'analysis_artifacts' AND table_schema = 'main'"
            ).fetchall()
        }
        required = {
            "artifact_id",
            "analysis_id",
            "artifact_type",
            "artifact_subtype",
            "label",
            "file_path",
            "inline_data",
            "file_size_kb",
            "mime_type",
            "embedding",
            "created_at",
        }
        missing = required - cols
        if missing:
            print(f"\nERROR: missing columns: {missing}", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v9 summary ---")
        print("  analysis_artifacts table : present")
        print(f"  embedding dim            : FLOAT[{EMBEDDING_DIM}]")
        print("  HNSW index               : idx_artifacts_hnsw (cosine)")
        print("  analysis_index view      : updated with artifact_count")
        print("\nENGRAM artifact registry ready.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
