"""
Phase 4 — BioAgent MCP Server

公開 7 個 MCP 工具供 Claude Code / Hermes Agent 呼叫：

歷史查詢（0 token，SQL 直接回傳）：
    bio_history_lookup   — 查詢樣本分析歷史表
    bio_history_timeline — 時間軸摘要（最近 N 天）
    bio_history_check    — 是否已有完成存檔（True/False）

語意搜尋（少量 token，只傳 summary）：
    bio_history_search   — L1 HNSW cosine 搜尋，回傳 summary 列表

記憶讀寫：
    bio_memory_query     — L1 語意快取查詢（報告全文）
    bio_memory_write     — 寫入 L1 語意快取
    bio_register_sample  — 登記新樣本至 sample_registry

啟動方式（stdio）：
    python server/bio_memory_server.py

或透過 .claude/settings.json mcpServers 設定自動啟動。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

server = Server("bio-memory")


# ── Tool 定義 ────────────────────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="bio_history_lookup",
            description=(
                "查詢樣本分析歷史（0 token，純 SQL）。"
                "回傳指定樣本的所有分析記錄，含分析類型、狀態、完成時間、摘要、結果路徑。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本 ID，例如 crc_official_v4。若省略則回傳所有樣本。",
                    },
                    "analysis_type": {
                        "type": "string",
                        "description": "分析類型篩選，例如 spatial_eda。省略則回傳所有類型。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多回傳筆數（預設 20）。",
                        "default": 20,
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="bio_history_timeline",
            description=(
                "回傳最近 N 天的分析時間軸（0 token，純 SQL）。"
                "顯示誰在何時對哪個樣本做了什麼分析。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "n_days": {
                        "type": "integer",
                        "description": "往回查幾天（預設 7）。",
                        "default": 7,
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="bio_history_check",
            description=(
                "確認某樣本的某分析類型是否已有完成存檔（0 token，純 SQL）。"
                "回傳 True/False 及最新完成時間與結果路徑（若存在）。"
                "Agent 應在每次分析前呼叫此工具避免重複運算。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本 ID，例如 crc_official_v4。",
                    },
                    "analysis_type": {
                        "type": "string",
                        "description": "分析類型，例如 spatial_eda。",
                    },
                },
                "required": ["sample_id", "analysis_type"],
            },
        ),
        types.Tool(
            name="bio_history_search",
            description=(
                "以自然語言語意搜尋 L1 語意快取（HNSW cosine）。"
                "只回傳 50 字 summary，不回傳完整報告，節省 token。"
                "需要 embedding server 在線（port 8081）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然語言查詢，例如「PTPRC 在腫瘤微環境的空間分佈」。",
                    },
                    "n": {
                        "type": "integer",
                        "description": "回傳筆數上限（預設 5）。",
                        "default": 5,
                    },
                    "threshold": {
                        "type": "number",
                        "description": "相似度門檻 0~1（預設 0.5）。低於此值不回傳。",
                        "default": 0.5,
                    },
                    "sample_id": {
                        "type": "string",
                        "description": "限定樣本 ID（可選）。",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="bio_memory_query",
            description=(
                "從 L1 語意快取取回完整報告（HNSW cosine ≥ 0.88 命中）。"
                "cache miss 時回傳空結果，Agent 應繼續呼叫分析工具。"
                "需要 embedding server 在線（port 8081）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然語言查詢或分析參數描述。",
                    },
                    "sample_id": {
                        "type": "string",
                        "description": "限定樣本 ID（可選，可縮小搜尋範圍）。",
                    },
                    "threshold": {
                        "type": "number",
                        "description": "相似度門檻（預設使用 L1_COSINE_THRESHOLD = 0.88）。",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="bio_memory_write",
            description=(
                "將分析報告寫入 L1 語意快取（TTL 7 天）。"
                "分析完成後呼叫此工具，讓後續相似查詢可以直接命中快取。"
                "需要 embedding server 在線（port 8081）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本 ID，例如 crc_official_v4。",
                    },
                    "query_text": {
                        "type": "string",
                        "description": "代表此分析的查詢文字（用於 embedding，供語意搜尋命中）。",
                    },
                    "report_text": {
                        "type": "string",
                        "description": "完整報告 Markdown 文字。",
                    },
                    "summary": {
                        "type": "string",
                        "description": "≤50 字中文摘要（語意搜尋時展示）。",
                    },
                    "analysis_id": {
                        "type": "string",
                        "description": "對應 analysis_history 的 UUID（可選）。",
                    },
                },
                "required": ["sample_id", "query_text", "report_text", "summary"],
            },
        ),
        types.Tool(
            name="bio_register_sample",
            description=(
                "登記新樣本至 sample_registry（L3 Bronze 目錄）。"
                "每個樣本只需登記一次。若 sample_id 已存在則回報並跳過。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "唯一樣本 ID，格式 {project}_{sample}（全小寫底線），例如 crc_official_v4。",
                    },
                    "data_type": {
                        "type": "string",
                        "description": "資料大類：visium_hd | visium | scrna | bulk_rnaseq | multiome | atac | proteomics | imaging | other",
                    },
                    "l3_path": {
                        "type": "string",
                        "description": "L3 原始數據絕對路徑（唯讀）。",
                    },
                    "project": {
                        "type": "string",
                        "description": "專案代號（可選），例如 crc_visium。",
                    },
                    "platform": {
                        "type": "string",
                        "description": "分析平台，例如 10x_visium_hd | cellranger（可選）。",
                    },
                    "species": {
                        "type": "string",
                        "description": "物種，例如 human | mouse（可選，預設 human）。",
                        "default": "human",
                    },
                    "tissue": {
                        "type": "string",
                        "description": "組織類型，例如 colon | liver（可選）。",
                    },
                    "notes": {
                        "type": "string",
                        "description": "備註（可選）。",
                    },
                },
                "required": ["sample_id", "data_type", "l3_path"],
            },
        ),
    ]


# ── Tool 實作 ─────────────────────────────────────────────────────────────────


def _fmt_table(rows: list[dict]) -> str:
    """將 list[dict] 格式化為 Markdown 表格字串。"""
    if not rows:
        return "（無記錄）"
    headers = list(rows[0].keys())
    sep = " | ".join("---" for _ in headers)
    head = " | ".join(str(h) for h in headers)
    lines = [f"| {head} |", f"| {sep} |"]
    for row in rows:
        line = " | ".join(str(row.get(h, "")) for h in headers)
        lines.append(f"| {line} |")
    return "\n".join(lines)


async def _handle_bio_history_lookup(args: dict) -> str:
    from analysis.history_query import recent_analyses, find_by_type

    sample_id = args.get("sample_id")
    analysis_type = args.get("analysis_type")
    limit = int(args.get("limit", 20))

    if analysis_type:
        rows = find_by_type(analysis_type, sample_id=sample_id, limit=limit)
    else:
        rows = recent_analyses(n=limit, sample_id=sample_id)

    # rows is a pandas DataFrame
    if rows.empty:
        return f"無分析記錄（sample_id={sample_id!r}, analysis_type={analysis_type!r}）"

    table_rows = [
        {
            "analysis_id": str(r.get("analysis_id", ""))[:8] + "…",
            "sample_id": r.get("sample_id", ""),
            "type": r.get("analysis_type", ""),
            "status": r.get("status", ""),
            "completed_at": str(r.get("completed_at", ""))[:16],
            "summary": (str(r.get("summary", "")) or "")[:40],
            "result_path": str(r.get("result_path", "") or ""),
        }
        for r in rows.to_dict("records")
    ]
    return f"分析歷史（共 {len(rows)} 筆）\n\n" + _fmt_table(table_rows)


async def _handle_bio_history_timeline(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH

    n_days = int(args.get("n_days", 7))
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            """
            SELECT sample_id,
                   analysis_type,
                   status,
                   requested_by,
                   strftime(completed_at, '%Y-%m-%d %H:%M') AS completed_at,
                   summary
            FROM   analysis_history
            WHERE  completed_at >= now() - (? * INTERVAL '1 day')
            ORDER  BY completed_at DESC
            LIMIT  50
            """,
            [n_days],
        ).fetchall()
        cols = ["sample_id", "analysis_type", "status", "requested_by", "completed_at", "summary"]
        result_rows = [dict(zip(cols, r)) for r in rows]

    if not result_rows:
        return f"最近 {n_days} 天無分析記錄。"

    table_rows = [
        {
            "sample_id": r["sample_id"],
            "type": r["analysis_type"],
            "status": r["status"],
            "by": r["requested_by"] or "",
            "completed_at": r["completed_at"] or "",
            "summary": (r["summary"] or "")[:40],
        }
        for r in result_rows
    ]
    return f"最近 {n_days} 天分析時間軸（共 {len(result_rows)} 筆）\n\n" + _fmt_table(table_rows)


async def _handle_bio_history_check(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH

    sample_id = args["sample_id"]
    analysis_type = args["analysis_type"]
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            """
            SELECT analysis_id, completed_at, result_path, summary
            FROM   analysis_history
            WHERE  sample_id = ? AND analysis_type = ? AND status = 'completed'
            ORDER  BY completed_at DESC
            LIMIT  1
            """,
            [sample_id, analysis_type],
        ).fetchone()

    if row:
        analysis_id, completed_at, result_path, summary = row
        return (
            f"exists: true\n"
            f"analysis_id: {analysis_id}\n"
            f"completed_at: {str(completed_at)[:16]}\n"
            f"result_path: {result_path or '（未記錄）'}\n"
            f"summary: {(summary or '')[:80]}"
        )
    return f"exists: false\nsample_id={sample_id!r}, analysis_type={analysis_type!r} 尚無完成存檔。"


async def _handle_bio_history_search(args: dict) -> str:
    from analysis.l1_cache import semantic_search

    query = args["query"]
    n = int(args.get("n", 5))
    threshold = float(args.get("threshold", 0.5))
    sample_id = args.get("sample_id")

    results = semantic_search(query, n=n, threshold=threshold, sample_id=sample_id)
    if not results:
        return f"語意搜尋 cache miss（query={query!r}, threshold={threshold}）"

    lines = [f"語意搜尋命中 {len(results)} 筆（threshold={threshold}）\n"]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. [{r['score']:.3f}] {r['sample_id']} — {r['summary']}\n"
            f"   query_text: {r['query_text'][:60]}\n"
            f"   created_at: {str(r['created_at'])[:16]}"
        )
    return "\n".join(lines)


async def _handle_bio_memory_query(args: dict) -> str:
    from analysis.l1_cache import semantic_search
    from config.settings import L1_COSINE_THRESHOLD

    query = args["query"]
    sample_id = args.get("sample_id")
    threshold = float(args.get("threshold", L1_COSINE_THRESHOLD))

    results = semantic_search(query, n=1, threshold=threshold, sample_id=sample_id)
    if not results:
        return f"L1 cache miss（threshold={threshold}）。建議呼叫分析工具生成新報告。"

    r = results[0]
    return (
        f"L1 cache hit（score={r['score']:.4f}）\n"
        f"sample_id: {r['sample_id']}\n"
        f"summary: {r['summary']}\n"
        f"created_at: {str(r['created_at'])[:16]}\n"
        f"expires_at: {str(r['expires_at'])[:16]}\n\n"
        f"--- 完整報告 ---\n{r['report_text']}"
    )


async def _handle_bio_memory_write(args: dict) -> str:
    from analysis.l1_cache import write_to_l1_cache

    rec_id = write_to_l1_cache(
        sample_id=args["sample_id"],
        query_text=args["query_text"],
        report_text=args["report_text"],
        summary=args["summary"],
        analysis_id=args.get("analysis_id"),
    )
    return f"L1 快取寫入成功。\nid: {rec_id}\nsample_id: {args['sample_id']}"


async def _handle_bio_register_sample(args: dict) -> str:
    import duckdb
    from config.db_utils import safe_write
    from config.settings import DUCKDB_PATH
    from datetime import datetime, timezone

    import re
    sample_id = args["sample_id"]
    if not re.match(r'^[a-z0-9_-]+$', sample_id):
        return f"樣本 ID {sample_id!r} 格式錯誤：只允許小寫英數字、底線和連字號。"

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        existing = con.execute(
            "SELECT sample_id FROM sample_registry WHERE sample_id = ?", [sample_id]
        ).fetchone()
        if existing:
            return f"樣本 {sample_id!r} 已存在於 sample_registry，跳過登記。"

        safe_write(
            con,
            """
            INSERT INTO sample_registry
                (sample_id, project, data_type, platform, species, tissue,
                 l3_path, l2_ready, analysis_done, added_by, notes, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, false, false, ?, ?, ?)
            """,
            [
                sample_id,
                args.get("project", ""),
                args["data_type"],
                args.get("platform", ""),
                args.get("species", "human"),
                args.get("tissue", ""),
                args["l3_path"],
                "mcp_server",
                args.get("notes", ""),
                datetime.now(timezone.utc),
            ],
        )

    return f"樣本 {sample_id!r} 已登記至 sample_registry。\ndata_type: {args['data_type']}\nl3_path: {args['l3_path']}"


# ── call_tool 分發 ────────────────────────────────────────────────────────────

_HANDLERS = {
    "bio_history_lookup": _handle_bio_history_lookup,
    "bio_history_timeline": _handle_bio_history_timeline,
    "bio_history_check": _handle_bio_history_check,
    "bio_history_search": _handle_bio_history_search,
    "bio_memory_query": _handle_bio_memory_query,
    "bio_memory_write": _handle_bio_memory_write,
    "bio_register_sample": _handle_bio_register_sample,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name!r}")
    try:
        result = await handler(arguments)
    except Exception as exc:
        logger.exception("Tool %r failed: %s", name, exc)
        result = f"[ERROR] {name} 執行失敗：{exc}"
    return [types.TextContent(type="text", text=result)]


# ── 啟動 ─────────────────────────────────────────────────────────────────────


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
