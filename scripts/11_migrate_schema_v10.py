"""
Migration v10 — Schema Health Baseline (Phase 9-SQL P0).

Creates:
  schema_migrations   — version registry for all applied migrations

Backfills:
  v1–v9 migration records

Safe to run multiple times (CREATE TABLE IF NOT EXISTS / ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_HISTORY = [
    (1,  "2026-05-15", "init_db: sample_registry, analysis_history, tools, tool_dependencies, analysis_index view"),
    (2,  "2026-05-15", "sample_registry metadata fields: condition, time_point, batch, donor_id, tags"),
    (3,  "2026-05-15", "analysis_history tool_id FK + tools/tool_dependencies tables"),
    (4,  "2026-05-16", "tool_change_log: append-only tool change history"),
    (5,  "2026-05-16", "tools: revision_count, stability_note, deprecated_at; HELIX-Core"),
    (6,  "2026-05-16", "tool_stabilization_log: HELIX stabilization iteration tracking"),
    (7,  "2026-05-17", "tool_stabilization_log: loc, halstead_volume, after_img columns"),
    (8,  "2026-05-17", "tool_change_log: source_snapshot, changed_lines, churn_ratio (line-level hotspot)"),
    (9,  "2026-05-18", "analysis_artifacts: ENGRAM artifact registry + HNSW cosine index + analysis_index artifact_count"),
]


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    with duckdb.connect(str(db_path)) as con:

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER     PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL,
                description VARCHAR     NOT NULL
            )
            """
        )
        print("Table: schema_migrations — OK")

        inserted = 0
        for version, date_str, description in _HISTORY:
            existing = con.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", [version]
            ).fetchone()
            if existing:
                continue
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                [version, f"{date_str} 00:00:00+00", description],
            )
            inserted += 1

        print(f"Backfilled {inserted} historical records (skipped already-present)")

        existing_v10 = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 10"
        ).fetchone()
        if not existing_v10:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (10, now(), 'schema_migrations table + v1-v9 backfill (Phase 9-SQL P0)')
                """
            )
            print("Recorded migration v10")
        else:
            print("Migration v10 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        rows = con.execute(
            "SELECT version, applied_at::DATE, description FROM schema_migrations ORDER BY version"
        ).fetchall()
        print("\n--- schema_migrations ---")
        for r in rows:
            print(f"  v{r[0]:>2}  {r[1]}  {r[2]}")

        print("\nMigration v10 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
