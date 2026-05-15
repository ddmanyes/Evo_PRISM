"""
每日 03:30 清理 L1 快取過期記錄（memory_recent.expires_at < now()）。

設計原則：
    - TTL 7 天（L1_TTL_DAYS，可由 settings 調整）
    - 只刪 expires_at 已過的資料；analysis_history 永遠不刪
    - 刪除後執行 CHECKPOINT，避免 WAL 殘留

排程（macOS launchd）：
    見 docs/launchd_cleanup_l1.plist.example（Phase 3 建立）

執行：
    python scheduler/cleanup_l1_cache.py
    python scheduler/cleanup_l1_cache.py --dry-run   # 只印不刪
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import L1_CACHE_PATH


def cleanup_expired(
    *,
    dry_run: bool = False,
    cache_path: Path | None = None,
) -> int:
    """
    刪除 expires_at < now() 的 memory_recent 記錄。

    Args:
        dry_run:    True 時只回報筆數，不實際刪除
        cache_path: 覆蓋預設路徑（測試用）

    Returns:
        刪除（或預計刪除）的筆數
    """
    path = cache_path or L1_CACHE_PATH

    if not path.exists():
        print(f"[cleanup_l1] Cache not found: {path}")
        return 0

    with duckdb.connect(str(path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception:
            pass  # VSS 不影響刪除的邏輯，但需要 LOAD 才能操作有 HNSW 索引的表

        expired_count = con.execute(
            "SELECT COUNT(*) FROM memory_recent WHERE expires_at < now()"
        ).fetchone()[0]

        if expired_count == 0:
            print(f"[cleanup_l1] Nothing to clean ({datetime.now(timezone.utc).date()})")
            return 0

        if dry_run:
            print(f"[cleanup_l1] DRY-RUN: would delete {expired_count} expired records")
            return expired_count

        con.execute("DELETE FROM memory_recent WHERE expires_at < now()")
        con.execute("CHECKPOINT")
        print(
            f"[cleanup_l1] Deleted {expired_count} expired records "
            f"({datetime.now(timezone.utc).date()})"
        )
        return expired_count


def stats(cache_path: Path | None = None) -> dict:
    """回傳快取目前狀態（筆數、最早 / 最晚 expires_at）。"""
    path = cache_path or L1_CACHE_PATH

    if not path.exists():
        return {"exists": False}

    with duckdb.connect(str(path), read_only=True) as con:
        row = con.execute(
            """
            SELECT COUNT(*),
                   MIN(expires_at),
                   MAX(expires_at)
            FROM memory_recent
            """
        ).fetchone()

    return {
        "exists": True,
        "total_records": row[0],
        "earliest_expires": row[1],
        "latest_expires": row[2],
    }


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    s = stats()
    print(f"[cleanup_l1] cache stats: {s}")
    cleanup_expired(dry_run=dry_run)
