"""
Migration v5 — Tool stabilization iteration tracking.

New table: tool_stabilization_log
  Records each stabilization attempt for a hot-zone tool:
    - What triggered it (which revision)
    - What was diagnosed and what action was taken
    - Whether it worked (revision_after filled in later)
    - Open items (closed_at = NULL) signal ongoing iterations

Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


_STABILIZATION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS tool_stabilization_log (
    log_id           UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name        VARCHAR NOT NULL,
    trigger_revision INTEGER NOT NULL,   -- revision_count that triggered this iteration
    diagnosis        VARCHAR,            -- problem description (may expand on stability_note)
    action_taken     VARCHAR,            -- what was done: refactor / extract helper / add tests / ...
    outcome          VARCHAR,            -- 'stabilized' | 'ongoing' | 'reverted'
    revision_before  INTEGER NOT NULL,   -- revision_count at iteration start
    revision_after   INTEGER,            -- filled in later; NULL = still open
    created_at       TIMESTAMP DEFAULT now(),
    closed_at        TIMESTAMP           -- NULL = iteration in progress
)
"""


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))

    try:
        print("CREATE TABLE tool_stabilization_log ...", end=" ")
        con.execute(_STABILIZATION_LOG_DDL)
        print("OK")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        if "tool_stabilization_log" not in tables:
            print("\nERROR: tool_stabilization_log not found", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v5 summary ---")
        print("  tool_stabilization_log : present")
        print("\nNext: use bio_tool_health action=stabilize to open an iteration,")
        print("      and action=close_stabilize to record the outcome.")

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
