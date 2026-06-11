"""樣本 ID 查詢工具（bio_lookup_sample）。

支援：
  - 新 ID 精確查詢（sample_id）
  - 舊 ID 反查（alias）
  - 模糊查詢（LIKE，部分匹配）
  - 列出指定 project / data_type 的所有樣本

回傳結構化文字，供 MCP Agent 直接讀取。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH
from config.db_utils import open_db

logger = logging.getLogger(__name__)


def lookup_sample(
    query: str,
    *,
    fuzzy: bool = False,
    project: str | None = None,
    data_type: str | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> str:
    """查詢樣本 ID。

    Args:
        query:     新 ID、舊 ID（alias）或部分字串（fuzzy=True 時用 LIKE）
        fuzzy:     True 時用 LIKE 模糊匹配，False 時精確比對
        project:   限定 project（可選）
        data_type: 限定 data_type（可選）

    Returns:
        格式化文字結果
    """
    def _query(c: duckdb.DuckDBPyConnection) -> str:
        conditions = []
        params: list = []

        if fuzzy:
            conditions.append("(sample_id LIKE ? OR COALESCE(alias,'') LIKE ?)")
            like_q = f"%{query}%"
            params += [like_q, like_q]
        else:
            conditions.append("(sample_id = ? OR alias = ?)")
            params += [query, query]

        if project:
            conditions.append("project = ?")
            params.append(project)
        if data_type:
            conditions.append("data_type = ?")
            params.append(data_type)

        where = " AND ".join(conditions)
        rows = c.execute(f"""
            SELECT sample_id, alias, data_type, project,
                   condition, time_point, tissue, l2_ready,
                   notes,
                   CASE WHEN l3_path LIKE '/Volumes/%' THEN 'ok'
                        WHEN l3_path LIKE '/mnt/%'     THEN 'linux'
                        WHEN l3_path LIKE 'I:%'        THEN 'win'
                        ELSE 'other' END AS path_status
            FROM sample_registry
            WHERE {where}
            ORDER BY data_type, project, sample_id
        """, params).fetchall()

        if not rows:
            hint = "（提示：使用 fuzzy=true 進行部分匹配）" if not fuzzy else ""
            return f"找不到符合 `{query}` 的樣本{hint}。"

        parts = [f"**查詢：`{query}`** — 找到 {len(rows)} 筆\n"]
        for r in rows:
            sid, al, dtype, proj, cond, tp, tissue, l2, notes, ps = r
            parts.append(f"### `{sid}`")
            if al:
                parts.append(f"- **舊 ID（alias）**：`{al}`")
            parts.append(f"- 類型：{dtype}　專案：{proj}")
            if cond or tp:
                parts.append(f"- 條件：{cond or '—'}　時間點：{tp or '—'}")
            if tissue:
                parts.append(f"- 組織：{tissue}")
            parts.append(f"- L2 就緒：{'✓' if l2 else '✗'}　路徑狀態：{ps}")
            if notes:
                parts.append(f"- 備註：{notes}")
            parts.append("")
        return "\n".join(parts)

    if con is not None:
        return _query(con)
    with open_db(DUCKDB_PATH) as c:
        return _query(c)


def list_samples(
    *,
    project: str | None = None,
    data_type: str | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> str:
    """列出指定 project / data_type 的所有樣本（摘要格式）。"""
    def _query(c: duckdb.DuckDBPyConnection) -> str:
        conditions = []
        params: list = []
        if project:
            conditions.append("project = ?")
            params.append(project)
        if data_type:
            conditions.append("data_type = ?")
            params.append(data_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = c.execute(f"""
            SELECT sample_id, alias, data_type, project,
                   condition, time_point, tissue, l2_ready
            FROM sample_registry
            {where}
            ORDER BY data_type, project, sample_id
        """, params).fetchall()

        if not rows:
            return "找不到符合條件的樣本。"

        lines = [f"**共 {len(rows)} 筆**\n"]
        lines.append("| sample_id | alias | 類型 | 專案 | 條件 | 時間點 | 組織 | L2 |")
        lines.append("|-----------|-------|------|------|------|--------|------|----|")
        for r in rows:
            sid, al, dtype, proj, cond, tp, tissue, l2 = r
            lines.append(
                f"| `{sid}` | {al or ''} | {dtype} | {proj} "
                f"| {cond or ''} | {tp or ''} | {tissue or ''} | {'✓' if l2 else '✗'} |"
            )
        return "\n".join(lines)

    if con is not None:
        return _query(con)
    with open_db(DUCKDB_PATH) as c:
        return _query(c)
