"""
每週日 03:00 重建 HNSW 索引（DROP + CREATE）。

背景：
    DuckDB VSS 不支援 incremental index update——每次 INSERT 不會自動更新索引。
    因此需要定期 DROP + CREATE 來讓新資料進入索引，確保語意搜尋的召回率。

安全策略：
    1. 先確認 memory_recent 有資料（空表不需要重建）
    2. DROP INDEX → CREATE INDEX（中間有一段時間索引不存在）
    3. 重建後驗證索引存在
    4. 整個過程不影響 memory_recent 的讀寫（僅索引層操作）

排程（macOS launchd）：
    見 docs/launchd_rebuild_hnsw.plist.example

執行：
    python scheduler/rebuild_hnsw.py
    python scheduler/rebuild_hnsw.py --force   # 不管有無資料，強制重建
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import L1_CACHE_PATH


def rebuild_hnsw(
    *,
    force: bool = False,
    cache_path: Path | None = None,
) -> dict:
    """
    重建 memory_recent 的 HNSW 索引。

    Args:
        force:      True 時即使 row_count=0 也執行重建
        cache_path: 覆蓋預設路徑（測試用）

    Returns:
        dict 含 status, row_count, elapsed_sec, error
    """
    path = cache_path or L1_CACHE_PATH

    if not path.exists():
        return {"status": "skipped", "reason": f"Cache not found: {path}"}

    con = duckdb.connect(str(path))
    try:
        con.execute("LOAD vss")
    except Exception:
        try:
            con.execute("INSTALL vss; LOAD vss")
        except Exception as e:
            con.close()
            return {"status": "error", "error": f"Cannot load VSS: {e}"}

    try:
        row_count = con.execute(
            "SELECT COUNT(*) FROM memory_recent"
        ).fetchone()[0]

        if row_count == 0 and not force:
            con.close()
            print(f"[rebuild_hnsw] Skipped — memory_recent is empty (use --force to override)")
            return {"status": "skipped", "reason": "empty table", "row_count": 0}

        print(f"[rebuild_hnsw] Rebuilding HNSW index ({row_count:,} rows)...")
        t0 = time.time()

        con.execute("SET hnsw_enable_experimental_persistence = true")
        con.execute("DROP INDEX IF EXISTS idx_memory_hnsw")
        con.execute(
            """
            CREATE INDEX idx_memory_hnsw
            ON memory_recent
            USING HNSW (embedding)
            WITH (metric = 'cosine')
            """
        )
        con.execute("CHECKPOINT")

        elapsed = time.time() - t0
        ts = datetime.now(timezone.utc).isoformat()
        print(f"[rebuild_hnsw] Done in {elapsed:.1f}s  [{ts}]")

        return {
            "status": "ok",
            "row_count": row_count,
            "elapsed_sec": round(elapsed, 2),
            "timestamp": ts,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        con.close()


def index_exists(cache_path: Path | None = None) -> bool:
    """確認 idx_memory_hnsw 是否存在。"""
    path = cache_path or L1_CACHE_PATH
    if not path.exists():
        return False
    con = duckdb.connect(str(path))
    try:
        con.execute("LOAD vss")
    except Exception:
        pass
    try:
        # DuckDB 1.5 可從 duckdb_indexes() 查詢
        rows = con.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE index_name = 'idx_memory_hnsw'"
        ).fetchall()
        return len(rows) > 0
    except Exception:
        return False
    finally:
        con.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    result = rebuild_hnsw(force=force)
    print("[rebuild_hnsw] result:", result)
    print("[rebuild_hnsw] index_exists:", index_exists())
