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

import hashlib
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


# ── 4-way RRF 常數（論文 §2.4.1 + B5 BM25）──────────────────────────────────
# 權重總和保持 1.0，確保 perfect score = 1/(_RRF_K+1) 不變（向後相容）。
# BM25 解決基因別名漂移（PTPRC↔CD45、MS4A1↔CD20）；FTS 不可用時退化為 3-way。

_RRF_K: int = 60       # 標準 RRF 平滑常數
_W1: float = 0.4       # cosine similarity 權重（原 0.5）
_W_BM25: float = 0.3   # BM25 全文搜尋權重（新增）
_W2: float = 0.2       # input fingerprint 匹配權重（原 0.3）
_W3: float = 0.1       # context hash 匹配權重（原 0.2）
_MISMATCH_RANK: int = 9999  # 不匹配時的懲罰 rank

# FTS sidecar schema name（PRAGMA create_fts_index 建立）
_FTS_SCHEMA_L1 = "fts_main_memory_recent"


def _rrf_score(
    rank_cosine: int,
    rank_fp: int,
    rank_ctx: int,
    rank_bm25: int = 1,
) -> float:
    """4-way Reciprocal Rank Fusion 分數。

    rank_bm25=1（預設）代表 FTS 不可用或視為完美命中，不影響排序。
    FTS 可用時傳入實際 BM25 rank；未命中傳入 _MISMATCH_RANK。
    """
    return (
        _W1 / (rank_cosine + _RRF_K)
        + _W_BM25 / (rank_bm25 + _RRF_K)
        + _W2 / (rank_fp + _RRF_K)
        + _W3 / (rank_ctx + _RRF_K)
    )


def _rrf_hit_threshold(
    *, has_fp: bool = False, has_ctx: bool = False, has_bm25: bool = False
) -> float:
    """計算 RRF 命中門檻（完美分與最差失配分的中點）。"""
    perfect = _rrf_score(1, 1, 1, 1)
    worst_miss = perfect
    if has_fp:
        worst_miss = min(worst_miss, _rrf_score(1, _MISMATCH_RANK, 1, 1))
    if has_ctx:
        worst_miss = min(worst_miss, _rrf_score(1, 1, _MISMATCH_RANK, 1))
    if has_bm25:
        worst_miss = min(worst_miss, _rrf_score(1, 1, 1, _MISMATCH_RANK))
    return (perfect + worst_miss) / 2


# ── FTS 工具 ──────────────────────────────────────────────────────────────────


def _setup_fts(con: duckdb.DuckDBPyConnection) -> bool:
    """Try to load FTS extension. Returns True on success."""
    try:
        con.execute("LOAD fts")
        return True
    except Exception:
        return False


def _fts_l1_available(con: duckdb.DuckDBPyConnection) -> bool:
    """Return True if FTS index on memory_recent exists and FTS is loadable."""
    try:
        con.execute("LOAD fts")
        row = con.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?",
            [_FTS_SCHEMA_L1],
        ).fetchone()
        return row is not None
    except Exception:
        return False


def rebuild_fts_index(cache_path: Optional[Path] = None) -> dict:
    """Create or rebuild the BM25 FTS index on memory_recent.

    Should be called by the daily cleanup scheduler after expired records are removed.
    PRAGMA create_fts_index with overwrite=1 is idempotent (drops + recreates atomically).

    Returns status dict with row_count and elapsed_sec.
    """
    import time as _time

    path = cache_path or L1_CACHE_PATH
    if not path.exists():
        return {"status": "skipped", "reason": "cache file does not exist"}

    try:
        with duckdb.connect(str(path)) as con:
            _setup_vss(con)
            if not _setup_fts(con):
                return {"status": "skipped", "reason": "FTS extension unavailable"}

            row_count = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
            if row_count == 0:
                return {"status": "skipped", "reason": "empty table", "row_count": 0}

            logger.info("[rebuild_fts_index] Rebuilding FTS index (%d rows)...", row_count)
            t0 = _time.time()
            con.execute(
                "PRAGMA create_fts_index("
                "'memory_recent', 'id', 'query_text', 'report_text', overwrite=1)"
            )
            con.execute("CHECKPOINT")
            elapsed = _time.time() - t0
            logger.info("[rebuild_fts_index] Done in %.1fs", elapsed)
            return {"status": "ok", "row_count": row_count, "elapsed_sec": round(elapsed, 2)}
    except Exception as exc:
        logger.warning("[rebuild_fts_index] Failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ── 輸入指紋 / 上下文雜湊 ────────────────────────────────────────────────────


def compute_input_fingerprint(
    *,
    raw_content: Optional[str] = None,
    file_paths: Optional[list] = None,
) -> str:
    """計算輸入數據的 16 字元 SHA-256 指紋。

    raw_content 與 file_paths 可同時提供，會一起納入雜湊。
    file_paths 按字母排序後依序讀入（保持跨平台穩定性）。
    """
    h = hashlib.sha256()
    if raw_content is not None:
        h.update(raw_content.encode())
    if file_paths:
        for fp in sorted(str(p) for p in file_paths):
            h.update(Path(fp).read_bytes())
    return h.hexdigest()[:16]


def compute_context_hash(
    sample_id: str,
    *,
    tool_ids: Optional[list[str]] = None,
    tool_versions: Optional[dict[str, str]] = None,
    env_info: Optional[str] = None,
) -> str:
    """計算分析上下文的雜湊（tool_ids 順序無關）。

    tool_ids 排序後納入；tool_versions 依 tool_id 字母排序後附加 source_hash。
    """
    h = hashlib.sha256()
    h.update(sample_id.encode())
    for tid in sorted(tool_ids or []):
        h.update(tid.encode())
        if tool_versions and tid in tool_versions:
            h.update(tool_versions[tid].encode())
    if env_info:
        h.update(env_info.encode())
    return h.hexdigest()[:17]


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
    input_fingerprint: Optional[str] = None,
    context_hash: Optional[str] = None,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> str:
    """將分析結果寫入 L1 語意快取。

    Args:
        sample_id:          樣本 ID（如 'crc_official_v4'）
        query_text:         查詢語句或分析參數描述（用於 embedding）
        report_text:        完整報告文字
        summary:            ≤50 字摘要（由 report_generator 產生）
        analysis_id:        對應 bio_memory.analysis_history 的 UUID（可選）
        input_fingerprint:  輸入數據指紋（由 compute_input_fingerprint() 產生）
        context_hash:       分析上下文雜湊（由 compute_context_hash() 產生）
        cache_path:         覆蓋預設路徑（測試用）
        embedding_provider: 覆蓋 settings（測試用）

    Returns:
        寫入記錄的 UUID (str)
    """
    from analysis.embed import embed_text

    path = cache_path or L1_CACHE_PATH
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
                 embedding, analysis_id, input_fingerprint, context_hash,
                 created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rec_id,
                sample_id,
                query_text,
                report_text,
                summary,
                embedding,
                analysis_id,
                input_fingerprint,
                context_hash,
                now,
                expires_at,
            ],
        )
        con.execute("CHECKPOINT")

    logger.info("L1 cache written: %s (sample=%s)", rec_id, sample_id)
    return rec_id


# ── 搜尋 ──────────────────────────────────────────────────────────────────────


def _has_analysis_type_col(con: duckdb.DuckDBPyConnection) -> bool:
    try:
        row = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'memory_recent' AND column_name = 'analysis_type'"
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _build_semantic_filters(
    sample_id: Optional[str],
    analysis_type: Optional[str],
    has_analysis_type_col: bool,
) -> tuple[str, list]:
    filters: list[str] = []
    params: list = []
    if sample_id:
        filters.append("AND sample_id = ?")
        params.append(sample_id)
    if analysis_type and has_analysis_type_col:
        filters.append("AND analysis_type = ?")
        params.append(analysis_type)
    return " ".join(filters), params


def semantic_search(
    query: str,
    *,
    n: int = 5,
    threshold: float = L1_COSINE_THRESHOLD,
    sample_id: Optional[str] = None,
    analysis_type: Optional[str] = None,
    input_fingerprint: Optional[str] = None,
    context_hash: Optional[str] = None,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> list[dict]:
    """語意搜尋 L1 快取（HNSW cosine similarity，可選 4-way RRF）。

    若提供 input_fingerprint 或 context_hash，啟用 RRF 模式：
      - cosine + BM25（FTS 可用時）+ fingerprint + context_hash 四路融合
      - FTS 不可用時自動退化為 3-way（cosine + fingerprint + context_hash）
      - 任一指標不符（數據或上下文已變更）→ cache miss
    否則退化為純 cosine 模式（向後相容）。

    Args:
        query:              查詢字串
        n:                  回傳筆數上限
        threshold:          cosine 相似度門檻（純 cosine 模式用）
        sample_id:          若指定則只搜尋該樣本的記錄
        analysis_type:      若指定則只搜尋該分析類型（欄位不存在時自動降級）
        input_fingerprint:  輸入數據指紋，提供時啟用 RRF
        context_hash:       上下文雜湊，提供時啟用 RRF
        cache_path:         覆蓋預設路徑（測試用）
        embedding_provider: 覆蓋 settings（測試用）

    Returns:
        list of dict，每筆含 id, sample_id, summary, score, report_text,
        query_text, created_at, expires_at。RRF 模式額外附帶 rrf_score。
    """
    from analysis.embed import embed_text
    from config.settings import EMBEDDING_DIM as _DIM

    path = cache_path or L1_CACHE_PATH
    if not path.exists():
        logger.warning("L1 cache not found: %s", path)
        return []

    query_vec = embed_text(query, provider=embedding_provider)
    use_rrf = input_fingerprint is not None or context_hash is not None
    bm25_rank: dict[str, int] = {}  # populated in RRF branch when FTS is available

    with duckdb.connect(str(path)) as con:
        _setup_vss(con)
        _setup_fts(con)
        if con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0] == 0:  # type: ignore[index]
            return []

        has_col = _has_analysis_type_col(con) if analysis_type else False
        if analysis_type and not has_col:
            logger.warning(
                "semantic_search: analysis_type=%r filter requested but column does not "
                "exist in memory_recent; ignoring filter (run migration to add column)",
                analysis_type,
            )

        filter_clause, extra_params = _build_semantic_filters(sample_id, analysis_type, has_col)

        if use_rrf:
            params: list = [query_vec] + extra_params + [n * 4]
            sql = f"""
                SELECT id, sample_id, query_text, summary, report_text,
                       created_at, expires_at,
                       array_cosine_similarity(embedding, ?::FLOAT[{_DIM}]) AS score,
                       input_fingerprint, context_hash
                FROM   memory_recent
                WHERE  expires_at > now()
                       {filter_clause}
                ORDER BY score DESC
                LIMIT ?
            """
            rows = con.execute(sql, params).fetchall()

            # ── BM25 第四路：FTS 可用時執行 ──────────────────────────────────
            if _fts_l1_available(con):
                try:
                    bm25_sql = f"""
                        SELECT id::VARCHAR,
                               {_FTS_SCHEMA_L1}.match_bm25(id, ?) AS bm25_score
                        FROM   memory_recent
                        WHERE  expires_at > now()
                               AND {_FTS_SCHEMA_L1}.match_bm25(id, ?) IS NOT NULL
                               {filter_clause}
                        ORDER  BY bm25_score DESC
                        LIMIT  ?
                    """
                    bm25_rows = con.execute(
                        bm25_sql, [query, query] + extra_params + [n * 4]
                    ).fetchall()
                    bm25_rank = {str(r[0]): rank for rank, r in enumerate(bm25_rows, start=1)}
                except Exception as _bm25_exc:
                    logger.warning("L1 BM25 search failed (degrading to 3-way): %s", _bm25_exc)
        else:
            params = [query_vec] + extra_params + [n]
            sql = f"""
                SELECT id, sample_id, query_text, summary, report_text,
                       created_at, expires_at,
                       array_cosine_similarity(embedding, ?::FLOAT[{_DIM}]) AS score
                FROM   memory_recent
                WHERE  expires_at > now()
                       {filter_clause}
                ORDER BY score DESC
                LIMIT ?
            """
            rows = con.execute(sql, params).fetchall()

    base_cols = [
        "id",
        "sample_id",
        "query_text",
        "summary",
        "report_text",
        "created_at",
        "expires_at",
        "score",
    ]

    if not use_rrf:
        return [dict(zip(base_cols, row)) for row in rows if row[-1] >= threshold]

    # ── 4-way RRF 模式（BM25 可用時）/ 3-way（FTS 不可用時降級）────────────────
    has_bm25 = bool(bm25_rank)
    rrf_threshold = _rrf_hit_threshold(
        has_fp=input_fingerprint is not None,
        has_ctx=context_hash is not None,
        has_bm25=has_bm25,
    )
    results: list[dict] = []
    for rank_cosine, row in enumerate(rows, start=1):
        rec = dict(zip(base_cols + ["_fp", "_ctx"], row))
        if rec["score"] < threshold:
            continue

        stored_fp: Optional[str] = rec.pop("_fp")
        stored_ctx: Optional[str] = rec.pop("_ctx")

        rank_fp = (
            1 if (input_fingerprint is None or stored_fp == input_fingerprint) else _MISMATCH_RANK
        )
        rank_ctx = 1 if (context_hash is None or stored_ctx == context_hash) else _MISMATCH_RANK
        # rank_bm25=1 when FTS unavailable → BM25 component is neutral (no penalty)
        rank_bm25 = bm25_rank.get(str(rec["id"]), _MISMATCH_RANK) if has_bm25 else 1

        rrf = _rrf_score(rank_cosine, rank_fp, rank_ctx, rank_bm25)
        if rrf < rrf_threshold:
            continue

        rec["rrf_score"] = rrf
        results.append(rec)
        if len(results) >= n:
            break

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
