"""
Migration v7 — Extended metrics for tool stabilization iterations.

Adds to tool_stabilization_log:
  loc              INTEGER  — non-blank, non-comment source lines at iteration open
  halstead_volume  DOUBLE   — Halstead volume at iteration open (radon)
  after_img        VARCHAR  — base64 PNG snapshot rendered at close_stabilization()
                              (same format as diagnosis_img but reflects post-refactor state)

Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_NEW_COLS: list[tuple[str, str]] = [
    ("loc",             "INTEGER"),   # lines of code at open
    ("halstead_volume", "DOUBLE"),    # Halstead volume at open
    ("after_img",       "VARCHAR"),   # base64 PNG at close
]


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))

    try:
        for col_name, col_type in _NEW_COLS:
            sql = (
                f"ALTER TABLE tool_stabilization_log "
                f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            )
            print(f"ALTER tool_stabilization_log: add {col_name} ...", end=" ")
            con.execute(sql)
            print("OK")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tool_stabilization_log' AND table_schema = 'main'"
            ).fetchall()
        }
        missing = {c for c, _ in _NEW_COLS} - cols
        if missing:
            print(f"\nERROR: missing columns: {missing}", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v7 summary ---")
        for col_name, col_type in _NEW_COLS:
            print(f"  tool_stabilization_log.{col_name:<20} ({col_type}): present")
        print("\nloc + halstead_volume enable complexity trend analysis per iteration.")
        print("after_img enables before/after visual comparison in HELIX-Vision.")

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
