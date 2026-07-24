"""Migration v25 — Add affected_params JSON column to tools table (P3 diff-level tagging).

Adds `affected_params JSON` to `tools` to track which parameter keys are affected
by each version's code change.  When present, `bio_impact` narrows impact reporting
to analyses that actually used those parameters, reducing version-level false positives.

Schema change:
  ALTER TABLE tools ADD COLUMN IF NOT EXISTS affected_params JSON DEFAULT NULL

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

        try:
            con.execute(
                "ALTER TABLE tools "
                "ADD COLUMN IF NOT EXISTS affected_params JSON DEFAULT NULL"
            )
            print("Column: tools.affected_params — OK")
        except Exception as exc:
            print(f"WARNING: tools.affected_params migration failed: {exc}")

        # Verify
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tools' AND table_schema = 'main'"
            ).fetchall()
        }
        if "affected_params" in cols:
            print("Verification: tools.affected_params — PRESENT ✓")
        else:
            print("Verification: tools.affected_params — MISSING (may need manual ALTER)")

        con.execute("CHECKPOINT")
        print("Migration v25 complete.")


if __name__ == "__main__":
    migrate()
