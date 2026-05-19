"""Migration v18 — DuckDB FTS (BM25) on analysis_artifacts (P0-B).

Adds a BM25 full-text index over `label`, `artifact_subtype`, `artifact_type`
columns of `analysis_artifacts`. Enables Layer 3 of the hybrid RRF retrieval
inside `search_artifacts()`.

Why BM25 alongside HNSW:
  - bge-m3 dense embeddings tend to miss exact bio-symbol tokens
    (e.g. EPCAM, KRT14, HALLMARK_OXPHOS) in CJK/EN mixed queries.
  - BM25 provides a complementary signal for keyword-exact matches.
  - Combined via existing Reciprocal Rank Fusion (k=60).

No schema change on `analysis_artifacts`. The FTS extension creates a
sidecar schema `fts_main_analysis_artifacts` with auxiliary tables.

Idempotent: re-running drops + recreates the FTS index (overwrite=1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


FTS_SCHEMA_NAME = "fts_main_analysis_artifacts"


def _fts_schema_exists(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?",
        [FTS_SCHEMA_NAME],
    ).fetchone()
    return row is not None


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        # VSS must be loaded before any write/CHECKPOINT on analysis_artifacts
        # because the table holds HNSW indexes that need rebinding at commit time.
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail")

        try:
            con.execute("INSTALL fts")
            con.execute("LOAD fts")
        except Exception as exc:
            print(f"ERROR: FTS extension load failed: {exc}")
            raise

        con.execute(
            "PRAGMA create_fts_index("
            "'analysis_artifacts', 'artifact_id', "
            "'label', 'artifact_subtype', 'artifact_type', "
            "overwrite=1)"
        )
        if _fts_schema_exists(con):
            print(f"FTS index created in schema `{FTS_SCHEMA_NAME}` — OK")
        else:
            raise RuntimeError(
                f"FTS index reported created but schema `{FTS_SCHEMA_NAME}` not found"
            )

        row = con.execute(
            f"SELECT COUNT(*) FROM {FTS_SCHEMA_NAME}.docs"
        ).fetchone()
        row_count = row[0] if row else 0
        print(f"FTS indexed rows: {row_count}")

        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 18"
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (18, now(),
                    'P0-B: DuckDB FTS BM25 index on analysis_artifacts '
                    '(label + artifact_subtype + artifact_type) for hybrid RRF Layer 3')
                """
            )
            print("Recorded migration v18")
        else:
            print("Migration v18 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")
        print("\nMigration v18 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
