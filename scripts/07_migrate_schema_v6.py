"""
Migration v6 — Visual memory for tool stabilization iterations.

Adds to tool_stabilization_log:
  diagnosis_img     VARCHAR  — base64 PNG snapshot rendered at iteration open
                               (complexity heatmap + revision timeline + diagnosis text)
                               Stored as data URI: "data:image/png;base64,..."
                               VLM agents read the image to recover iteration context
                               without re-reading source code (~100 vision tokens at 640x640,
                               ~10x compression per DeepSeek-OCR arXiv:2510.18234).
  complexity_before INTEGER  — cyclomatic complexity at iteration start (via radon)
  complexity_after  INTEGER  — filled at close_stabilization(); delta = improvement metric

Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_NEW_COLS: list[tuple[str, str]] = [
    ("diagnosis_img", "VARCHAR"),  # base64 data URI PNG
    ("complexity_before", "INTEGER"),  # cyclomatic complexity at open
    ("complexity_after", "INTEGER"),  # cyclomatic complexity at close
]


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))

    try:
        for col_name, col_type in _NEW_COLS:
            sql = (
                f"ALTER TABLE tool_stabilization_log ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
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

        print("\n--- Migration v6 summary ---")
        for col_name, _ in _NEW_COLS:
            print(f"  tool_stabilization_log.{col_name:<20} : present")
        print("\nVLM agents can now view diagnosis_img to recover iteration context.")

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
