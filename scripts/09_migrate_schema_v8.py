"""
Migration v8 — Intra-tool hotspot tracking (line-level churn).

Adds to tool_change_log:
  source_snapshot  TEXT     — full inspect.getsource() at registration time
  changed_lines    VARCHAR  — JSON array of [start, end] line ranges changed vs prev revision
  churn_ratio      DOUBLE   — changed_lines_count / loc  (Nagappan relative churn)

Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_NEW_COLS: list[tuple[str, str]] = [
    ("source_snapshot", "TEXT"),
    ("changed_lines", "VARCHAR"),
    ("churn_ratio", "DOUBLE"),
]


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))

    try:
        for col_name, col_type in _NEW_COLS:
            sql = f"ALTER TABLE tool_change_log ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            print(f"ALTER tool_change_log: add {col_name} ...", end=" ")
            con.execute(sql)
            print("OK")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tool_change_log' AND table_schema = 'main'"
            ).fetchall()
        }
        missing = {c for c, _ in _NEW_COLS} - cols
        if missing:
            print(f"\nERROR: missing columns: {missing}", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v8 summary ---")
        for col_name, col_type in _NEW_COLS:
            print(f"  tool_change_log.{col_name:<20} ({col_type}): present")
        print("\nsource_snapshot enables line-level diff between revisions.")
        print("changed_lines + churn_ratio enable Tornhill-style hotspot scoring.")

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
