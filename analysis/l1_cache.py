"""
Phase 3.5 — L1 語意快取讀寫介面。

寫入：分析完成後呼叫 write_to_l1_cache()，把報告 + embedding 存入 memory_recent。
搜尋：Agent 收到查詢後呼叫 semantic_search()，回傳相似度前 N 筆結果。

設計：
    - embedding 由 analysis/embed.py 負責（provider 無關）
    - HNSW 索引由 DuckDB VSS 提供，每次連線需 LOAD vss
    - TTL 7 天（L1_TTL_DAYS），由 scheduler/cleanup_l1_cache.py 清理
    - 相似度門檻 L1_COSINE_THRESHOLD（預設 0.88），低於此值視為 cache miss
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import duckdb

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import L1_CACHE_PATH, L1_COSINE_THRESHOLD, L1_TTL_DAYS

logger = logging.getLogger(__name__)


# ── 連線工具 ──────────────────────────────────────────────────────────────────


def _open_l1(cache_path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """開啟 L1 cache 並載入 VSS extension。"""
    con = duckdb.connect(str(cache_path), read_only=read_only)
    try:
        con.execute("LOAD vss")
        if not read_only:
            con.execute("SET hnsw_enable_experimental_persistence = true")
    except Exception as e:
        logger.warning("VSS load warning: %s", e)
    return con


# ── 寫入 ──────────────────────────────────────────────────────────────────────


def write_to_l1_cache(
    sample_id: str,
    query_text: str,
    report_text: str,
    summary: str,
    *,
    analysis_id: Optional[str] = None,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> str:
    """
    將分析結果寫入 L1 語意快取。

    Args:
        sample_id:    樣本 ID（如 'crc_official_v4'）
        query_text:   查詢語句或分析參數描述（用於 embedding）
        report_text:  完整報告文字
        summary:      ≤50 字摘要（由 report_generator 產生）
        analysis_id:  對應 bio_memory.analysis_history 的 UUID（可選）
        cache_path:   覆蓋預設路徑（測試用）
        embedding_provider: 覆蓋 settings（測試用）

    Returns:
        寫入記錄的 UUID (str)
    """
    from analysis.embed import embed_text

    path = cache_path or L1_CACHE_PATH

    # 取得 embedding（可能需要 llama-server 在線）
    embedding = embed_text(query_text, provider=embedding_provider)

    rec_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=L1_TTL_DAYS)

    con = _open_l1(path)
    try:
        con.execute(
            """
            INSERT INTO memory_recent
                (id, sample_id, query_text, report_text, summary,
                 embedding, analysis_id, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rec_id,
                sample_id,
                query_text,
                report_text,
                summary,
                embedding,
                analysis_id,
                now,
                expires_at,
            ],
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    logger.info("L1 cache written: %s (sample=%s)", rec_id, sample_id)
    return rec_id


# ── 搜尋 ──────────────────────────────────────────────────────────────────────


def semantic_search(
    query: str,
    *,
    n: int = 5,
    threshold: float = L1_COSINE_THRESHOLD,
    sample_id: Optional[str] = None,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> list[dict]:
    """
    語意搜尋 L1 快取（HNSW cosine similarity）。

    Args:
        query:      查詢字串（自然語言或基因名稱）
        n:          回傳筆數上限
        threshold:  相似度門檻（0~1），低於此值不回傳
        sample_id:  若指定則只搜尋該樣本的記錄
        cache_path: 覆蓋預設路徑（測試用）
        embedding_provider: 覆蓋 settings（測試用）

    Returns:
        list of dict，每筆含：id, sample_id, summary, score, report_text,
                               query_text, created_at, expires_at
        依 score 降冪排列。空列表表示 cache miss。
    """
    from analysis.embed import embed_text

    path = cache_path or L1_CACHE_PATH

    if not path.exists():
        logger.warning("L1 cache not found: %s", path)
        return []

    query_vec = embed_text(query, provider=embedding_provider)

    con = _open_l1(path, read_only=False)  # VSS search needs write mode for index access
    try:
        row_count = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
        if row_count == 0:
            return []

        sample_filter = "AND sample_id = ?" if sample_id else ""
        params: list = [query_vec, n]
        if sample_id:
            params.insert(1, sample_id)

        # DuckDB VSS array_cosine_similarity：1 = identical, 0 = orthogonal
        # HNSW ORDER BY + LIMIT 觸發近似最近鄰搜尋
        from config.settings import EMBEDDING_DIM as _DIM

        sql = f"""
            SELECT id,
                   sample_id,
                   query_text,
                   summary,
                   report_text,
                   created_at,
                   expires_at,
                   array_cosine_similarity(embedding, ?::FLOAT[{_DIM}]) AS score
            FROM   memory_recent
            WHERE  expires_at > now()
                   {sample_filter}
            ORDER BY score DESC
            LIMIT ?
        """
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    cols = ["id", "sample_id", "query_text", "summary", "report_text",
            "created_at", "expires_at", "score"]
    results = [dict(zip(cols, row)) for row in rows if row[-1] >= threshold]
    return results


def cache_stats(cache_path: Optional[Path] = None) -> dict:
    """回傳 L1 快取統計（不需要 embedding server）。"""
    from scheduler.cleanup_l1_cache import stats
    return stats(cache_path=cache_path or L1_CACHE_PATH)


if __name__ == "__main__":
    from analysis.embed import server_health

    h = server_health()
    if not h["ok"]:
        print(f"[l1_cache] Server not available: {h['error']}")
        print("  Start: ~/llama.cpp/build/bin/llama-server -m ~/llama.cpp/models/bge-m3-Q8_0.gguf --embedding --port 8081")
        raise SystemExit(1)

    print("[l1_cache] Writing test record...")
    rec_id = write_to_l1_cache(
        sample_id="crc_official_v4",
        query_text="PTPRC spatial expression in CRC tumor microenvironment",
        report_text="# Test EDA Report\n\nPTPRC shows high expression in immune cell-rich regions.",
        summary="crc_official_v4 EDA：PTPRC 在腫瘤免疫細胞區高表達。",
    )
    print(f"  Written: {rec_id}")

    print("\n[l1_cache] Searching: 'CD8 T cell expression spatial'...")
    results = semantic_search("CD8 T cell expression spatial", n=3, threshold=0.5)
    for r in results:
        print(f"  score={r['score']:.4f}  summary={r['summary'][:40]}")

    print("\n[l1_cache] Stats:", cache_stats())
