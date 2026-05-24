"""Migration v24 — Add failure_diagnosis column to analysis_history (PM1, Phase 13).

Adds a nullable TEXT column `failure_diagnosis` to `analysis_history` to store
a lightweight per-run diagnostic JSON produced after each tool invocation.

Expected JSON structure:
  {
    "type": "cache_miss_semantic | wrong_tool_version | insufficient_context |
             L3_not_ready | hallucination | success",
    "detail": "<human-readable explanation, ≤ 200 chars>",
    "diagnosed_at": "<ISO8601 timestamp>"
  }

Rationale (EvolveMem PM1): EvolveMem [arXiv:2605.13941] drives its AutoResearch
self-evolution loop by reading per-question failure logs.  Evo_PRISM adopts the
same principle for HELIX/ENGRAM diagnosis: every tool run records its failure
category (or "success"), enabling bio_failure_summary (PM1-C) to aggregate root
causes and guide future HELIX promotion decisions.

Idempotent: safe to run multiple times.
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
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc})")

        # 1. Add failure_diagnosis column (idempotent)
        try:
            con.execute(
                "ALTER TABLE analysis_history "
                "ADD COLUMN IF NOT EXISTS failure_diagnosis TEXT DEFAULT NULL"
            )
            print("Column: analysis_history.failure_diagnosis — OK")
        except Exception as exc:
            print(f"WARNING: could not add failure_diagnosis column: {exc}")

        # 2. Record in schema_migrations (idempotent via INSERT OR IGNORE)
        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 24"
        ).fetchone()
        if existing:
            print("Migration v24 already recorded — skipped")
        else:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (24, now(),
                        'PM1/Phase13: Add failure_diagnosis TEXT to analysis_history '
                        '(EvolveMem-inspired per-run diagnostic log)')
                """
            )
            print("Recorded migration v24 in schema_migrations")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")
        print("\nMigration v24 complete.")


if __name__ == "__main__":
    migrate()
