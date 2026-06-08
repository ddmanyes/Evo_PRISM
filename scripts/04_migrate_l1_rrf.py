"""
Migration: 為 memory_recent 新增 3-way RRF 所需欄位。

新增：
    input_fingerprint VARCHAR  — SHA256[:16] of 輸入檔案（r_fingerprint）
    context_hash      VARCHAR  — SHA256[:16] of sample_id+tool_ids+env（r_context）

設計：
    - 使用 ALTER TABLE ADD COLUMN IF NOT EXISTS（DuckDB ≥ 0.9 idempotent）
    - 現有記錄的兩欄為 NULL，等同舊版純 cosine 行為（semantic_search 會跳過比對）
    - 不影響現有快取資料；可安全在任何時間點執行

執行：
    python scripts/04_migrate_l1_rrf.py
    python scripts/04_migrate_l1_rrf.py --dry-run   # 只顯示計畫，不修改
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import L1_CACHE_PATH

NEW_COLS = [
    ("input_fingerprint", "VARCHAR"),
    ("context_hash",      "VARCHAR"),
]


def migrate(db_path: Path, *, dry_run: bool = False) -> dict:
    if not db_path.exists():
        return {"status": "skipped", "reason": f"{db_path} not found"}

    results = {}
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("INSTALL vss; LOAD vss")
        except Exception:
            pass

        existing = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'memory_recent'"
            ).fetchall()
        }

        for col, dtype in NEW_COLS:
            if col in existing:
                results[col] = "already exists"
                continue
            if dry_run:
                results[col] = f"would add {dtype}"
            else:
                con.execute(
                    f"ALTER TABLE memory_recent ADD COLUMN IF NOT EXISTS {col} {dtype}"
                )
                results[col] = f"added {dtype}"

        if not dry_run:
            con.execute("CHECKPOINT")

    return {"status": "ok", "columns": results, "db": str(db_path)}


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    result = migrate(L1_CACHE_PATH, dry_run=dry_run)
    tag = "[DRY-RUN] " if dry_run else ""
    print(f"{tag}Migration result: {result}")
