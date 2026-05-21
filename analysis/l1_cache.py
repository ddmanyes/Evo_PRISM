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


def _setup_vss(con: duckdb.DuckDBPyConnection, *, read_only: bool = False) -> None:
    """載入 VSS extension（失敗時只記 warning，不中斷）。"""
    try:
        con.execute("LOAD vss")
        if not read_only:
            con.execute("SET hnsw_enable_experimental_persistence = true")
    except Exception as e:
        logger.warning("VSS load warning: %s", e)


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

    with duckdb.connect(str(path)) as con:
        _setup_vss(con)
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

    logger.info("L1 cache written: %s (sample=%s)", rec_id, sample_id)
    return rec_id


# ── 搜尋 ──────────────────────────────────────────────────────────────────────


def semantic_search(
    query: str,
    *,
    n: int = 5,
    threshold: float = L1_COSINE_THRESHOLD,
    sample_id: Optional[str] = None,
    analysis_type: Optional[str] = None,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> list[dict]:
    """
    語意搜尋 L1 快取（HNSW cosine similarity）。

    Args:
        query:          查詢字串（自然語言或基因名稱）
        n:              回傳筆數上限
        threshold:      相似度門檻（0~1），低於此值不回傳
        sample_id:      若指定則只搜尋該樣本的記錄
        analysis_type:  若指定則只搜尋該分析類型的記錄（需 memory_recent 有
                        analysis_type 欄位；若欄位不存在則自動降級、記 warning）
        cache_path:     覆蓋預設路徑（測試用）
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

    with duckdb.connect(str(path)) as con:  # VSS search needs write mode for index access
        _setup_vss(con)
        row_count = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
        if row_count == 0:
            return []

        # 確認 memory_recent 是否有 analysis_type 欄位（migration 可能尚未執行）
        has_analysis_type_col = False
        if analysis_type:
            try:
                col_row = con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'memory_recent' AND column_name = 'analysis_type'"
                ).fetchone()
                has_analysis_type_col = col_row is not None
            except Exception:
                has_analysis_type_col = False

        if analysis_type and not has_analysis_type_col:
            logger.warning(
                "semantic_search: analysis_type=%r filter requested but column does not "
                "exist in memory_recent; ignoring filter (run migration to add column)",
                analysis_type,
            )

        # 組裝動態 WHERE 子句與參數列表
        extra_filters: list[str] = []
        extra_params: list = []

        if sample_id:
            extra_filters.append("AND sample_id = ?")
            extra_params.append(sample_id)

        if analysis_type and has_analysis_type_col:
            extra_filters.append("AND analysis_type = ?")
            extra_params.append(analysis_type)

        filter_clause = " ".join(extra_filters)
        params: list = [query_vec] + extra_params + [n]

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
                   {filter_clause}
            ORDER BY score DESC
            LIMIT ?
        """
        rows = con.execute(sql, params).fetchall()

    result_cols = [
        "id",
        "sample_id",
        "query_text",
        "summary",
        "report_text",
        "created_at",
        "expires_at",
        "score",
    ]
    results = [dict(zip(result_cols, row)) for row in rows if row[-1] >= threshold]
    return results


def invalidate_tool_cache(
    tool_name: str,
    *,
    cache_path: Optional[Path] = None,
) -> int:
    """Delete all L1 cache entries whose query_text contains *tool_name*.

    Called automatically by register_tool() when a tool's source changes, so
    that stale results from the previous version are not served to users.

    Returns the number of rows deleted (0 if cache file does not exist).
    """
    path = cache_path or L1_CACHE_PATH
    if not path.exists():
        return 0
    with duckdb.connect(str(path)) as con:
        deleted = con.execute(
            "DELETE FROM memory_recent WHERE query_text LIKE ? RETURNING id",
            [f"%{tool_name}%"],
        ).fetchall()
        con.execute("CHECKPOINT")
    count = len(deleted)
    if count:
        logger.info("invalidate_tool_cache: removed %d entries for tool %r", count, tool_name)
    return count


def cache_stats(cache_path: Optional[Path] = None) -> dict:
    """回傳 L1 快取統計（不需要 embedding server）。"""
    from scheduler.cleanup_l1_cache import stats

    return stats(cache_path=cache_path or L1_CACHE_PATH)


if __name__ == "__main__":
    from analysis.embed import server_health

    h = server_health()
    if not h["ok"]:
        print(f"[l1_cache] Server not available: {h['error']}")
        print(
            "  Start: ~/llama.cpp/build/bin/llama-server -m ~/llama.cpp/models/bge-m3-Q8_0.gguf --embedding --port 8081"
        )
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
