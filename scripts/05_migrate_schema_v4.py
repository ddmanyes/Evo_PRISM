"""
Migration v4 — Tool stability tracking.

Changes to bio_memory.duckdb:
  tools table:
    + revision_count  INTEGER DEFAULT 0   (incremented each time a new hash is registered)
    + stability_note  VARCHAR              (AI-written diagnosis: why this tool keeps changing)

  New table: tool_change_log
    Records every hash transition with optional change_reason.
    Enables hot-zone identification: tools with high revision_count are unstable.

Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS column).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH


_ADD_REVISION_COUNT = (
    "ALTER TABLE tools ADD COLUMN IF NOT EXISTS revision_count INTEGER DEFAULT 0"
)
_ADD_STABILITY_NOTE = (
    "ALTER TABLE tools ADD COLUMN IF NOT EXISTS stability_note VARCHAR"
)

_TOOL_CHANGE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS tool_change_log (
    log_id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name       VARCHAR NOT NULL,
    old_hash        VARCHAR(16),          -- NULL for first registration
    new_hash        VARCHAR(16) NOT NULL,
    new_tool_id     UUID,                 -- 軟引用：刻意不加 REFERENCES tools(tool_id)，見 migration v20
    revision_number INTEGER NOT NULL,     -- monotonically increasing per tool_name
    change_reason   VARCHAR,              -- optional: why the code changed
    changed_at      TIMESTAMP DEFAULT now()
)
"""


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))

    try:
        print("ALTER tools: add revision_count ...", end=" ")
        con.execute(_ADD_REVISION_COUNT)
        print("OK")

        print("ALTER tools: add stability_note ...", end=" ")
        con.execute(_ADD_STABILITY_NOTE)
        print("OK")

        print("CREATE TABLE tool_change_log ...", end=" ")
        con.execute(_TOOL_CHANGE_LOG_DDL)
        print("OK")

        # Back-fill revision_count=1 for tools that already exist (first registration)
        con.execute("""
            UPDATE tools
            SET    revision_count = 1
            WHERE  revision_count = 0
        """)
        print("Back-fill revision_count=1 for existing tools ... OK")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        # Verify
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tools' AND table_schema = 'main'"
            ).fetchall()
        }
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }

        missing_cols = {"revision_count", "stability_note"} - cols
        missing_tables = {"tool_change_log"} - tables
        if missing_cols or missing_tables:
            print(f"\nERROR: missing_cols={missing_cols}, missing_tables={missing_tables}",
                  file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v4 summary ---")
        print("  tools.revision_count : present")
        print("  tools.stability_note : present")
        print("  tool_change_log      : present")

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
