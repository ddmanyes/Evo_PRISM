"""Add owner attribution to the separate L1 semantic cache database.

Existing rows are deliberately marked ``legacy:unattributed``.  Admins retain
the global view; non-admin identities cannot match those legacy rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import L1_CACHE_PATH  # noqa: E402

LEGACY_OWNER = "legacy:unattributed"


def migrate(cache_path: Path = L1_CACHE_PATH) -> dict:
    if not cache_path.exists():
        return {"status": "skipped", "reason": "cache database does not exist"}

    with duckdb.connect(str(cache_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception:
            pass
        columns = {
            row[0]
            for row in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'memory_recent'"
            ).fetchall()
        }
        if "requested_by" not in columns:
            con.execute(
                "ALTER TABLE memory_recent ADD COLUMN requested_by VARCHAR "
                f"DEFAULT '{LEGACY_OWNER}'"
            )
        con.execute(
            "UPDATE memory_recent SET requested_by = ? "
            "WHERE requested_by IS NULL OR requested_by = ''",
            [LEGACY_OWNER],
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_recent_owner "
            "ON memory_recent(requested_by, created_at)"
        )
        con.execute("CHECKPOINT")
        row_count = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
    return {"status": "ok", "row_count": row_count, "legacy_owner": LEGACY_OWNER}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-path", type=Path, default=L1_CACHE_PATH)
    args = parser.parse_args()
    print(migrate(args.cache_path))


if __name__ == "__main__":
    main()
