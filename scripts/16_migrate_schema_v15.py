"""
Migration v15 — ENGRAM search observability (Phase 9A-4).

Creates:
  engram_search_metrics — one row per search_artifacts() call

Used to tune threshold / k values and monitor search quality over time.
artifact_registry.search_artifacts() writes to this table after each call.

Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as e:
            print(f"WARNING: VSS load failed ({e}) — continuing")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS engram_search_metrics (
                metric_id    UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
                query        VARCHAR     NOT NULL,
                returned_n   INTEGER     NOT NULL,
                latency_ms   INTEGER     NOT NULL,
                search_layer VARCHAR     NOT NULL,  -- 'exact' | 'hnsw' | 'rrf'
                threshold    DOUBLE,
                sample_id    VARCHAR,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        print("Table: engram_search_metrics — OK")

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_search_metrics_created
            ON engram_search_metrics (created_at)
            """
        )
        print("Index: idx_search_metrics_created — OK")

        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 15"
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (15, now(), 'engram_search_metrics table for search observability (9A-4)')
                """
            )
            print("Recorded migration v15")
        else:
            print("Migration v15 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        cols = [
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'engram_search_metrics' AND table_schema = 'main' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
        print(f"\nengram_search_metrics columns: {', '.join(cols)}")
        print("\nMigration v15 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
