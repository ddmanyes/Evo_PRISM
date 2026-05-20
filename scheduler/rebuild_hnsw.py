"""
每週日 03:00 重建 HNSW 索引 + ENGRAM FTS 索引（DROP + CREATE）。

背景：
    DuckDB VSS 不支援 incremental index update——每次 INSERT 不會自動更新索引。
    DuckDB FTS 同樣是 snapshot 模式，新增 artifact 後不會被既有 BM25 索引涵蓋。
    兩者都需要定期 DROP + CREATE 來讓新資料進入索引。

執行內容：
    1. rebuild_hnsw()           — L1 cache `memory_recent` 的 idx_memory_hnsw
    2. rebuild_artifact_fts()   — Main DB `analysis_artifacts` 的 FTS BM25 (P0-B)

安全策略：
    1. 先確認來源表有資料（空表不需要重建）
    2. PRAGMA create_fts_index(..., overwrite=1) 是 DuckDB 內建 idempotent 操作
    3. 重建後 CHECKPOINT 寫入主檔

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
from config.settings import DUCKDB_PATH, L1_CACHE_PATH

_FTS_SCHEMA_ARTIFACTS = "fts_main_analysis_artifacts"


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

    with duckdb.connect(str(path)) as con:
        try:
            con.execute("LOAD vss")
        except Exception:
            try:
                con.execute("INSTALL vss; LOAD vss")
            except Exception as e:
                return {"status": "error", "error": f"Cannot load VSS: {e}"}

        try:
            row_count = con.execute(
                "SELECT COUNT(*) FROM memory_recent"
            ).fetchone()[0]

            if row_count == 0 and not force:
                print("[rebuild_hnsw] Skipped — memory_recent is empty (use --force to override)")
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


def rebuild_artifact_fts(
    *,
    force: bool = False,
    db_path: Path | None = None,
) -> dict:
    """重建 analysis_artifacts 的 DuckDB FTS BM25 索引 (P0-B)。

    Args:
        force:   True 時即使 row_count=0 也執行重建（測試用）
        db_path: 覆蓋預設主 DB 路徑（測試用）

    Returns:
        dict 含 status, row_count, elapsed_sec, error
    """
    path = db_path or DUCKDB_PATH

    if not path.exists():
        return {"status": "skipped", "reason": f"Main DB not found: {path}"}

    with duckdb.connect(str(path)) as con:
        # VSS must be loaded so that CHECKPOINT can rebind HNSW indexes on
        # analysis_artifacts (these exist regardless of FTS).
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            return {"status": "error", "error": f"Cannot load VSS: {exc}"}

        try:
            con.execute("LOAD fts")
        except Exception:
            try:
                con.execute("INSTALL fts; LOAD fts")
            except Exception as exc:
                return {"status": "error", "error": f"Cannot load FTS: {exc}"}

        try:
            row_count = con.execute(
                "SELECT COUNT(*) FROM analysis_artifacts"
            ).fetchone()[0]

            if row_count == 0 and not force:
                print("[rebuild_artifact_fts] Skipped — analysis_artifacts is empty (use --force to override)")
                return {"status": "skipped", "reason": "empty table", "row_count": 0}

            print(f"[rebuild_artifact_fts] Rebuilding FTS index ({row_count:,} rows)...")
            t0 = time.time()

            # PRAGMA with overwrite=1 is idempotent — drops + recreates atomically.
            con.execute(
                "PRAGMA create_fts_index("
                "'analysis_artifacts', 'artifact_id', "
                "'label', 'artifact_subtype', 'artifact_type', "
                "overwrite=1)"
            )
            con.execute("CHECKPOINT")

            elapsed = time.time() - t0
            ts = datetime.now(timezone.utc).isoformat()
            print(f"[rebuild_artifact_fts] Done in {elapsed:.1f}s  [{ts}]")

            return {
                "status": "ok",
                "row_count": row_count,
                "elapsed_sec": round(elapsed, 2),
                "timestamp": ts,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


def fts_index_exists(db_path: Path | None = None) -> bool:
    """Check whether the FTS sidecar schema for analysis_artifacts is present."""
    path = db_path or DUCKDB_PATH
    if not path.exists():
        return False
    with duckdb.connect(str(path), read_only=True) as con:
        try:
            con.execute("LOAD fts")
        except Exception:
            pass
        try:
            row = con.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?",
                [_FTS_SCHEMA_ARTIFACTS],
            ).fetchone()
            return row is not None
        except Exception:
            return False


def index_exists(cache_path: Path | None = None) -> bool:
    """確認 idx_memory_hnsw 是否存在。"""
    path = cache_path or L1_CACHE_PATH
    if not path.exists():
        return False
    with duckdb.connect(str(path)) as con:
        try:
            con.execute("LOAD vss")
        except Exception:
            pass
        try:
            rows = con.execute(
                "SELECT index_name FROM duckdb_indexes() WHERE index_name = 'idx_memory_hnsw'"
            ).fetchall()
            return len(rows) > 0
        except Exception:
            return False


if __name__ == "__main__":
    force = "--force" in sys.argv

    result_hnsw = rebuild_hnsw(force=force)
    print("[rebuild_hnsw] result:", result_hnsw)
    print("[rebuild_hnsw] index_exists:", index_exists())

    result_fts = rebuild_artifact_fts(force=force)
    print("[rebuild_artifact_fts] result:", result_fts)
    print("[rebuild_artifact_fts] fts_index_exists:", fts_index_exists())
