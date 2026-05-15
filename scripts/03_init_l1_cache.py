"""
Phase 3 — 初始化 L1 語意快取資料庫（gold/hermes_cache.duckdb）。

建立：
    memory_recent  — 分析報告快取（含 1536-dim embedding）
    HNSW 索引      — cosine 相似度，由 DuckDB VSS 提供

設計考量：
    - HNSW 索引建在 embedding 欄位（FLOAT[1536]），metric = cosine
    - DuckDB VSS 不支援 incremental update，所以寫入時不更新索引；
      定期重建由 scheduler/rebuild_hnsw.py 負責（每週日 03:00）
    - expires_at 欄位由 scheduler/cleanup_l1_cache.py 每日清理（03:30）

執行：
    python scripts/03_init_l1_cache.py
    python scripts/03_init_l1_cache.py --reset   # 刪除並重建（危險！）
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import EMBEDDING_DIM, L1_CACHE_PATH


def init_l1_cache(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    cache_path: Path | None = None,
    reset: bool = False,
) -> duckdb.DuckDBPyConnection:
    """
    建立 L1 快取 schema。

    Args:
        con:        已存在的連線（測試用）；None 時依 cache_path 開啟
        cache_path: 覆蓋預設的 L1_CACHE_PATH（測試用）
        reset:      True 時先 DROP TABLE 再重建（⚠️ 會清空所有快取）

    Returns:
        已初始化的 DuckDB 連線
    """
    own_con = con is None
    if own_con:
        target = cache_path or L1_CACHE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(target))

    # 載入 VSS 擴充（HNSW 需要）
    try:
        con.execute("LOAD vss")
        print("VSS extension loaded")
    except Exception:
        con.execute("INSTALL vss; LOAD vss")
        print("VSS extension installed and loaded")

    if reset:
        con.execute("DROP TABLE IF EXISTS memory_recent")
        print("[init_l1] Existing table dropped (--reset)")

    # memory_recent：每筆對應一次分析結果的語意快取
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS memory_recent (
            id           UUID    PRIMARY KEY,
            sample_id    VARCHAR NOT NULL,
            query_text   VARCHAR NOT NULL,    -- 查詢語句或分析參數描述
            report_text  VARCHAR NOT NULL,    -- 完整 Markdown 報告（或摘要展開版）
            summary      VARCHAR NOT NULL,    -- ≤50 字摘要（由 report_generator 生成）
            embedding    FLOAT[{EMBEDDING_DIM}],   -- Google gemini-embedding-001 向量
            analysis_id  UUID,               -- FK → bio_memory.analysis_history.analysis_id
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ NOT NULL  -- TTL：created_at + 7 天
        )
        """
    )
    print(f"Table: memory_recent — OK (embedding dim={EMBEDDING_DIM})")

    # HNSW 索引（cosine 相似度）
    # DuckDB VSS 1.5+ 需要開啟實驗性持久化才能在檔案型 DB 建 HNSW 索引。
    # 此設定僅影響索引持久化，不影響查詢正確性。
    try:
        con.execute("SET hnsw_enable_experimental_persistence = true")
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_hnsw
            ON memory_recent
            USING HNSW (embedding)
            WITH (metric = 'cosine')
            """
        )
        print("HNSW index: idx_memory_hnsw — OK (cosine)")
    except Exception as e:
        print(f"WARNING: HNSW index creation failed (will retry after data load): {e}")

    con.execute("CHECKPOINT")
    shown_path = cache_path or L1_CACHE_PATH
    print(f"L1 cache initialized: {shown_path}")

    if own_con:
        return con
    return con


def verify_schema(con: duckdb.DuckDBPyConnection) -> dict:
    """驗證 schema 完整性，回傳各項狀態。"""
    result = {}

    cols = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'memory_recent'"
        ).fetchall()
    }
    required = {"id", "sample_id", "query_text", "report_text", "summary",
                "embedding", "analysis_id", "created_at", "expires_at"}
    result["columns_ok"] = required.issubset(cols)
    result["missing_cols"] = sorted(required - cols)

    result["row_count"] = con.execute(
        "SELECT COUNT(*) FROM memory_recent"
    ).fetchone()[0]

    # 確認 embedding 維度
    dim_row = con.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'memory_recent' AND column_name = 'embedding'"
    ).fetchone()
    result["embedding_type"] = dim_row[0] if dim_row else "NOT FOUND"

    return result


if __name__ == "__main__":
    reset = "--reset" in sys.argv

    if reset:
        confirm = input("⚠️  --reset will DROP memory_recent. Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    con = init_l1_cache(reset=reset)
    v = verify_schema(con)
    print("\n[verify]", v)
    con.close()
