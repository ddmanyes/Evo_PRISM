"""
Phase 2B — 0-token 分析歷史查詢。

0-token 的意思：所有查詢走 DuckDB SQL，不呼叫任何 LLM API。
Agent 可直接調用這些函數取得結構化資料，再決定是否需要語意搜尋（L1）。

主要函數：
    recent_analyses()   — 最近 N 筆分析記錄
    sample_summary()    — 指定樣本的分析概覽
    find_by_type()      — 按 analysis_type 篩選
    get_analysis()      — 依 analysis_id 取得完整記錄
    analysis_index()    — 從 analysis_index view 取得彙總統計
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

logger = logging.getLogger(__name__)


def _con(db_path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


# ── 查詢函數 ──────────────────────────────────────────────────────────────────


def recent_analyses(
    n: int = 20,
    *,
    sample_id: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    返回最近 N 筆分析記錄，按 completed_at 降冪排列。

    Args:
        n:         筆數上限（預設 20）
        sample_id: 若指定則只查該樣本
        status:    若指定則只查該狀態（completed / running / stale / failed）

    Returns:
        DataFrame: analysis_id, sample_id, analysis_type, status,
                   completed_at, summary, result_path
    """
    db_path = db_path or DUCKDB_PATH
    filters = []
    params: list = []

    if sample_id:
        filters.append("sample_id = ?")
        params.append(sample_id)
    if status:
        filters.append("status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(n)

    with _con(db_path) as con:
        df = con.execute(
            f"""
            SELECT analysis_id,
                   sample_id,
                   analysis_type,
                   status,
                   completed_at,
                   summary,
                   result_path
            FROM   analysis_history
            {where}
            ORDER BY completed_at DESC NULLS LAST
            LIMIT ?
            """,
            params,
        ).fetchdf()

    return df


def sample_summary(
    sample_id: str,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """
    返回指定樣本的快速概覽。

    Returns:
        dict 含：sample_info (dict), analysis_counts (dict), last_analyses (DataFrame)
    """
    db_path = db_path or DUCKDB_PATH

    with _con(db_path) as con:
        sample_rows = con.execute(
            "SELECT * FROM sample_registry WHERE sample_id = ?",
            [sample_id],
        ).fetchdf()

        if sample_rows.empty:
            raise ValueError(f"Sample '{sample_id}' not found in sample_registry")

        counts = con.execute(
            """
            SELECT analysis_type,
                   COUNT(*)                             AS run_count,
                   MAX(completed_at)                   AS last_run,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed
            FROM   analysis_history
            WHERE  sample_id = ?
            GROUP BY analysis_type
            ORDER BY last_run DESC
            """,
            [sample_id],
        ).fetchdf()

        last = con.execute(
            """
            SELECT analysis_type, completed_at, summary
            FROM   analysis_history
            WHERE  sample_id = ? AND status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 5
            """,
            [sample_id],
        ).fetchdf()

    return {
        "sample_info": sample_rows.iloc[0].to_dict(),
        "analysis_counts": counts,
        "last_analyses": last,
    }


def find_by_type(
    analysis_type: str,
    *,
    sample_id: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    按 analysis_type 篩選歷史記錄。

    Returns:
        DataFrame: analysis_id, sample_id, completed_at, summary, result_path, parameters
    """
    db_path = db_path or DUCKDB_PATH
    filters = ["analysis_type = ?"]
    params: list = [analysis_type]

    if sample_id:
        filters.append("sample_id = ?")
        params.append(sample_id)

    params.append(limit)
    where = "WHERE " + " AND ".join(filters)

    with _con(db_path) as con:
        df = con.execute(
            f"""
            SELECT analysis_id,
                   sample_id,
                   completed_at,
                   summary,
                   result_path,
                   parameters
            FROM   analysis_history
            {where}
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            params,
        ).fetchdf()

    return df


def get_analysis(
    analysis_id: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """
    依 analysis_id 取得單筆完整記錄。

    Returns:
        dict（所有欄位）或 None（不存在）
    """
    db_path = db_path or DUCKDB_PATH

    with _con(db_path) as con:
        row = con.execute(
            "SELECT * FROM analysis_history WHERE analysis_id = ?",
            [analysis_id],
        ).fetchdf()

    if row.empty:
        return None
    return row.iloc[0].to_dict()


def analysis_index(
    *,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    從 analysis_index view 取得彙總統計（按樣本 + 分析類型分組）。

    此 view 設計為 0-token 入口點：Agent 可先看這個快照，
    決定是否要進一步查詢詳細記錄或觸發新分析。

    Returns:
        DataFrame: sample_id, analysis_type, run_count, last_run_date, last_summary
    """
    db_path = db_path or DUCKDB_PATH

    with _con(db_path) as con:
        # analysis_index 是 00_init_db.py 建立的 view
        df = con.execute(
            """
            SELECT *
            FROM   analysis_index
            ORDER BY sample_id, last_run_date DESC
            """
        ).fetchdf()

    return df


def search_summaries(
    keyword: str,
    *,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    在 summary 欄位做 LIKE 關鍵字搜尋（非語意搜尋，0-token）。
    語意搜尋請使用 L1 HNSW（Phase 3 實作）。

    Returns:
        DataFrame: analysis_id, sample_id, analysis_type, completed_at, summary
    """
    db_path = db_path or DUCKDB_PATH

    with _con(db_path) as con:
        df = con.execute(
            """
            SELECT analysis_id, sample_id, analysis_type, completed_at, summary
            FROM   analysis_history
            WHERE  summary ILIKE ?
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            [f"%{keyword}%", limit],
        ).fetchdf()

    return df


if __name__ == "__main__":
    print("[history_query] analysis_index:")
    print(analysis_index().to_string(index=False))

    print("\n[history_query] recent (all):")
    print(recent_analyses(n=10).to_string(index=False))

    print("\n[history_query] sample_summary crc_official_v4:")
    s = sample_summary("crc_official_v4")
    print("  sample_info:", {k: v for k, v in s["sample_info"].items() if k in ["sample_id", "data_type", "l2_ready"]})
    print("  analysis_counts:\n", s["analysis_counts"].to_string(index=False))
