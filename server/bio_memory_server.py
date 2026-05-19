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

啟動方式：
    # stdio（Claude Code CLI，.mcp.json 設定）
    python server/bio_memory_server.py

    # HTTP（Web UI / 外部客戶端，port 8082）
    python server/bio_memory_server.py --transport http --port 8082

掛載至現有 FastAPI app（由 web_app.py 呼叫）：
    from server.bio_memory_server import create_http_app
    app.mount("/mcp", create_http_app())
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
import uuid
from collections import deque
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

server = Server("bio-memory")

# sample_id 驗證規則，與 _handle_bio_register_sample 對齊
_SAMPLE_ID_RE = re.compile(r"^[a-z0-9_-]+$")

# Rate limit：每 IP/process token bucket。embedding/search 路徑特別保護 llama-server
_RATE_LIMIT_WINDOW_SEC = 60.0
_RATE_LIMIT_MAX_CALLS = int(os.environ.get("MCP_RATE_LIMIT_PER_MIN", "30"))
_rate_buckets: dict[str, deque[float]] = {}


def _rate_limit_check(key: str) -> bool:
    """Return True if request allowed; False if rate limit exceeded."""
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(key, deque())
    cutoff = now - _RATE_LIMIT_WINDOW_SEC
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX_CALLS:
        return False
    bucket.append(now)
    return True


class RateLimitExceeded(RuntimeError):
    """Embedding/search 路徑被 rate limit 拒絕；call_tool 視為使用者錯誤回傳。"""


# 需要 rate limit 保護的工具（會打 embedding server 或耗用 llama 資源）
_RATE_LIMITED_TOOLS = frozenset(
    {"bio_history_search", "bio_memory_query", "bio_memory_write"}
)

_METRICS_SCHEMA_READY = False


def _ensure_metrics_table() -> None:
    """首次寫入時建立 mcp_tool_metrics 表（lazy, idempotent）。"""
    global _METRICS_SCHEMA_READY
    if _METRICS_SCHEMA_READY:
        return
    import duckdb
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_tool_metrics (
                metric_id    UUID    PRIMARY KEY DEFAULT uuid(),
                tool_name    VARCHAR NOT NULL,
                duration_ms  INTEGER NOT NULL,
                status       VARCHAR NOT NULL,  -- ok | user_error | system_error | rate_limited
                recorded_at  TIMESTAMP NOT NULL DEFAULT now()
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcp_metrics_tool_time "
            "ON mcp_tool_metrics(tool_name, recorded_at)"
        )
    _METRICS_SCHEMA_READY = True


def _record_metric(tool_name: str, duration_ms: int, status: str) -> None:
    """Best-effort metric write; never raise to caller."""
    try:
        _ensure_metrics_table()
        import duckdb
        from config.settings import DUCKDB_PATH

        with duckdb.connect(str(DUCKDB_PATH)) as con:
            con.execute(
                "INSERT INTO mcp_tool_metrics (tool_name, duration_ms, status) VALUES (?, ?, ?)",
                [tool_name, int(duration_ms), status],
            )
    except Exception as exc:  # pragma: no cover
        logger.debug("metric write failed (%s): %s", tool_name, exc)


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
                    "limit": {
                        "type": "integer",
                        "description": "回傳筆數上限（預設 50，最大 500）。n_days 大時可調高避免漏掉早期紀錄。",
                        "default": 50,
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
                        "description": "相似度門檻 0~1（預設 0.88，對齊 agent.py Cache Hit Protocol L1_COSINE_THRESHOLD）。",
                        "default": 0.88,
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


def _pipe_safe(s: str, max_len: int = 60) -> str:
    """Escape pipe chars and truncate; protects Markdown table columns from breakage.

    含空格的 ExFAT 路徑（例如 `/Volumes/NO NAME/...`）會破壞表格欄位對齊；
    `|` 會被當成欄位分隔符 — 一律轉成 `\\|` 並截斷。
    """
    s = str(s).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _fmt_table(rows: list[dict]) -> str:
    """將 list[dict] 格式化為 Markdown 表格字串（每格 pipe-safe + 截斷）。"""
    if not rows:
        return "（無記錄）"
    headers = list(rows[0].keys())
    sep = " | ".join("---" for _ in headers)
    head = " | ".join(_pipe_safe(h, 40) for h in headers)
    lines = [f"| {head} |", f"| {sep} |"]
    for row in rows:
        line = " | ".join(_pipe_safe(row.get(h, ""), 60) for h in headers)
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
    limit = max(1, min(int(args.get("limit", 50)), 500))
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            f"""
            SELECT sample_id,
                   analysis_type,
                   status,
                   requested_by,
                   strftime(completed_at, '%Y-%m-%d %H:%M') AS completed_at,
                   summary
            FROM   analysis_history
            WHERE  completed_at >= now() - (? * INTERVAL '1 day')
            ORDER  BY completed_at DESC
            LIMIT  {limit}
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
    from config.settings import L1_COSINE_THRESHOLD

    query = args["query"]
    n = int(args.get("n", 5))
    threshold = float(args.get("threshold", L1_COSINE_THRESHOLD))
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

    sample_id = args["sample_id"]
    if not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(
            f"sample_id {sample_id!r} 格式錯誤：只允許小寫英數字、底線和連字號（對齊 bio_register_sample）"
        )

    rec_id = write_to_l1_cache(
        sample_id=sample_id,
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
        _record_metric(name, 0, "user_error")
        return [types.TextContent(type="text", text=f"[ERROR] 未知工具：{name!r}")]

    # Rate limit gate（僅針對打 embedding server 的工具）
    if name in _RATE_LIMITED_TOOLS and not _rate_limit_check(f"tool:{name}"):
        logger.warning("Rate limit exceeded for tool %r", name)
        _record_metric(name, 0, "rate_limited")
        return [
            types.TextContent(
                type="text",
                text=(
                    f"[ERROR] {name} 已達速率上限"
                    f"（{_RATE_LIMIT_MAX_CALLS} calls / {int(_RATE_LIMIT_WINDOW_SEC)}s）。"
                    "請稍後再試，或調整 MCP_RATE_LIMIT_PER_MIN env。"
                ),
            )
        ]

    t0 = time.monotonic()
    try:
        result = await handler(arguments)
    except RateLimitExceeded as exc:
        _record_metric(name, int((time.monotonic() - t0) * 1000), "rate_limited")
        logger.warning("Tool %r rate limited: %s", name, exc)
        return [types.TextContent(type="text", text=f"[ERROR] {name}: {exc}")]
    except (ValueError, KeyError, TypeError) as exc:
        _record_metric(name, int((time.monotonic() - t0) * 1000), "user_error")
        # 使用者錯誤：參數驗證失敗、缺欄位、型別錯
        logger.info("Tool %r user error: %s", name, exc)
        return [
            types.TextContent(type="text", text=f"[ERROR] {name} 參數錯誤：{exc}")
        ]
    except Exception as exc:
        _record_metric(name, int((time.monotonic() - t0) * 1000), "system_error")
        corr_id = uuid.uuid4().hex[:8]
        logger.exception("Tool %r system error [corr=%s]: %s", name, corr_id, exc)
        return [
            types.TextContent(
                type="text",
                text=(
                    f"[ERROR] {name} 系統錯誤（correlation_id={corr_id}）。"
                    "請聯絡管理員並提供此 ID 對照 server log。"
                ),
            )
        ]
    _record_metric(name, int((time.monotonic() - t0) * 1000), "ok")
    return [types.TextContent(type="text", text=result)]


# ── HTTP transport ────────────────────────────────────────────────────────────


async def _send_auth_error(send, status: int, msg: str) -> None:
    body = msg.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _extract_bearer_token(scope: dict) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            text = value.decode("latin-1", errors="ignore").strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
            return text
    return None


def create_http_app():
    """回傳 (asgi_handler, lifespan_cm)，供父 ASGI app 掛載並驅動 lifespan。

    session_manager 以 stateless=True 運行，每次請求獨立，不需要 session affinity。

    認證：若 env `MCP_AUTH_TOKEN` 已設定，所有非 lifespan 請求必須帶
    `Authorization: Bearer <token>`；缺失或不符回 401。未設定 token 時 auth 關閉，
    維持向後相容（Web UI mount /mcp 時可不啟用）。

    用法（FastAPI）：
        mcp_handler, mcp_lifespan = create_http_app()
        # FastAPI 不會傳遞 lifespan 到 mount 的子 app，必須在父 lifespan 中驅動
        @contextlib.asynccontextmanager
        async def app_lifespan(_):
            async with mcp_lifespan():
                yield
        app = FastAPI(lifespan=app_lifespan)
        app.mount("/mcp", mcp_handler)
    """
    import contextlib

    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
    )
    auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip() or None

    @contextlib.asynccontextmanager
    async def _mcp_lifespan():
        # Agent 啟動時清理殭屍狀態（CLAUDE.md §6）
        try:
            _startup_cleanup_stale_runs()
        except Exception as exc:  # pragma: no cover - non-fatal best effort
            logger.warning("startup cleanup_stale_runs failed: %s", exc)
        async with session_manager.run():
            yield

    async def _asgi_handler(scope, receive, send):
        # lifespan 由父 app 透過 _mcp_lifespan 驅動，這裡只處理 HTTP 請求
        if scope["type"] == "lifespan":
            # 父 app 已在自己的 lifespan 中驅動 _mcp_lifespan，子 app 收到時直接回 ack
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            if auth_token is not None:
                presented = _extract_bearer_token(scope)
                if not presented:
                    await _send_auth_error(
                        send, 401, "Unauthorized: missing Bearer token"
                    )
                    return
                if presented != auth_token:
                    await _send_auth_error(
                        send, 401, "Unauthorized: invalid token"
                    )
                    return
            await session_manager.handle_request(scope, receive, send)

    return _asgi_handler, _mcp_lifespan


def _startup_cleanup_stale_runs() -> None:
    """MCP server 為長駐程序，啟動時清理 > 24h 仍為 running 的紀錄（CLAUDE.md §6）。"""
    import duckdb
    from config.db_utils import cleanup_stale_runs
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        n = cleanup_stale_runs(con)
        if n:
            logger.info("Startup cleanup: marked %d stale running rows", n)


# ── 啟動 ─────────────────────────────────────────────────────────────────────


async def _run_stdio() -> None:
    try:
        _startup_cleanup_stale_runs()
    except Exception as exc:  # pragma: no cover - non-fatal best effort
        logger.warning("startup cleanup_stale_runs failed: %s", exc)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _run_http(port: int) -> None:
    import contextlib
    import os
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    host = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
    handler, mcp_lifespan = create_http_app()

    @contextlib.asynccontextmanager
    async def _lifespan(_app: Starlette):
        async with mcp_lifespan():
            yield

    app = Starlette(routes=[Mount("/", app=handler)], lifespan=_lifespan)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="BioAgent MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args()

    if args.transport == "http":
        _run_http(args.port)
    else:
        asyncio.run(_run_stdio())
