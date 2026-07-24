"""
Phase 4 — BioAgent MCP Server

公開 14 個 MCP 工具供 Claude Code / Hermes Agent 呼叫：

歷史查詢（0 token，SQL 直接回傳）：
    bio_history_lookup        — 查詢樣本分析歷史表
    bio_history_timeline      — 時間軸摘要（最近 N 天）
    bio_history_check         — 是否已有完成存檔（True/False）
    bio_check_l2_sufficiency  — L2 Parquet 是否就緒

語意搜尋（少量 token，只傳 summary）：
    bio_history_search        — L1 HNSW cosine 搜尋，回傳 summary 列表
    bio_artifact_search       — ENGRAM artifact RRF hybrid 搜尋
    bio_artifact_summary      — 0-token artifact 概覽

記憶讀寫：
    bio_memory_query          — L1 語意快取查詢（報告全文）
    bio_memory_write          — 寫入 L1 語意快取
    bio_register_sample       — 登記新樣本至 sample_registry
    bio_lookup_sample         — 查詢樣本 ID（新/舊 alias 互查、模糊搜尋、依 project 列出）

分析執行（重量級，會寫 DB / 跑沙盒）：
    bio_run_spatial_eda       — 空間轉錄體 EDA（10–30 秒，需 l2_ready=true）
    bio_run_bulk_eda          — Bulk RNA EDA（10–60 秒）
    bio_run_mcseg_roi         — Visium HD 單 ROI MCseg 分割＋RNA 計數＋Scanpy＋Xenium 匯出（GPU，30–90 分鐘）
    bio_run_mcseg_fullslide   — Visium HD 全片 tiled MCseg 分割（GPU，數小時）
    bio_execute_code          — 沙盒執行 Python 程式碼（白名單 import，timeout=60s）
    bio_tool_health           — HELIX 工具庫健康報告與穩定化迭代管理
    bio_failure_summary       — PM1 診斷彙整：failure_diagnosis 類型分佈統計（EvolveMem 啟發）

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
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types

from analysis.figure_cache import strip_base64_for_llm

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

server = Server("bio-memory")

# sample_id 驗證規則，與 _handle_bio_register_sample 對齊
_SAMPLE_ID_RE = re.compile(r"^[a-z0-9_-]+$")

# Rate limit：每 IP/process token bucket。embedding/search 路徑特別保護 llama-server
#
# Sync（threading.Lock，非 asyncio.Lock）：內部沒有真正 I/O，只是操作記憶體內 deque，
# 用 asyncio.Lock 純粹是跟著檔案風格、非必要。2026-07-24 統一 tool catalog 後，Web UI 端
# （server/agent.py，同步 + run_in_executor 背景執行緒）也要呼叫同一份實作，sync 版讓
# 兩端直接共用同一組計數器狀態，不需跨 event loop 橋接（sp-brainstorming 決策 5-A）。
_RATE_LIMIT_WINDOW_SEC = 60.0
_RATE_LIMIT_MAX_CALLS = int(os.environ.get("MCP_RATE_LIMIT_PER_MIN", "30"))
_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = threading.Lock()


def _rate_limit_check(key: str) -> bool:
    """Return True if request allowed; False if rate limit exceeded."""
    with _rate_lock:
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


# 需要 rate limit 保護、以及高權限（可執行任意 Python）的工具清單，
# 從 tool_catalog.py 的單一真相來源衍生（2026-07-24 統一 catalog，不再手刻兩份）。
# 高權限工具預設不對 MCP 客戶端暴露；設定 env MCP_ENABLE_DANGEROUS_TOOLS=true 才會出現在
# list_tools 並可被呼叫（defense in depth — 即使 MCP_AUTH_TOKEN 未設，也不會意外洩漏沙盒執行入口）。
from server.tool_catalog import TOOL_CATALOG as _TOOL_CATALOG  # noqa: E402

_RATE_LIMITED_TOOLS = frozenset(n for n, s in _TOOL_CATALOG.items() if s.rate_limited)
_DANGEROUS_TOOLS = frozenset(n for n, s in _TOOL_CATALOG.items() if s.dangerous)


def _dangerous_tools_enabled() -> bool:
    """Read MCP_ENABLE_DANGEROUS_TOOLS env at runtime (no caching, by design).

    Caching this value would break test isolation: pytest's monkeypatch.setenv
    flips the env per-test, and every call site expects to see the fresh value.
    Cost is one os.environ.get() lookup per list_tools / call_tool — negligible.
    """
    return os.environ.get("MCP_ENABLE_DANGEROUS_TOOLS", "").lower() in ("1", "true", "yes")


def _record_metric(
    tool_name: str,
    duration_ms: int,
    status: str,
    error_class: str | None = None,
    requested_by: str | None = None,
) -> None:
    """Best-effort metric write; never raise to caller."""
    try:
        from store.factory import get_store

        get_store().record_metric(
            tool_name, duration_ms, status,
            error_class=error_class,
            requested_by=requested_by,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("metric write failed (%s): %s", tool_name, exc)


# ── Tool 定義 ────────────────────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = _build_all_tools()
    if not _dangerous_tools_enabled():
        tools = [t for t in tools if t.name not in _DANGEROUS_TOOLS]
    return tools


# ── MCP Resources：分析後數據檔交付（artifact:// URI）────────────────────────────


@server.list_resources()
async def list_resources() -> list[types.Resource]:
    """列出可用 resource：分析 artifact（artifact://）+ 樣本登記快照（registry://snapshot）。"""
    from analysis.artifact_resources import list_artifact_resources
    from config.settings import BIO_DB_ROOT
    from pathlib import Path

    from store.factory import get_store

    def _sync() -> list[dict]:
        with get_store().read_conn() as con:
            return list_artifact_resources(con)

    items = await asyncio.to_thread(_sync)
    resources = [
        types.Resource(
            uri=it["uri"],
            name=it["name"],
            description=it["description"],
            mimeType=it["mime_type"],
            size=(it["size_kb"] * 1024 if it["size_kb"] is not None else None),
        )
        for it in items
    ]

    # registry://snapshot — 樣本清單 + 分析狀態 + alias 對照表靜態快照
    snapshot_path = Path(BIO_DB_ROOT) / "docs" / "registry_snapshot.md"
    if snapshot_path.exists():
        resources.append(
            types.Resource(
                uri="registry://snapshot",
                name="Registry Snapshot",
                description=(
                    "樣本登記快照（auto-generated）。含：① 樣本清單 ② 分析 canonical 狀態 "
                    "③ pipeline gap 待辦 ④ 新舊 sample_id 對照表（alias）。"
                    "查詢樣本清單或 ID 對照時優先使用此 resource，省去 DB 查詢。"
                ),
                mimeType="text/markdown",
                size=snapshot_path.stat().st_size,
            )
        )
    return resources


@server.read_resource()
async def read_resource(uri):  # uri: pydantic AnyUrl
    """依 URI 取回 resource 內容：artifact:// 或 registry://snapshot。"""
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    from analysis.artifact_resources import read_artifact_resource, ArtifactResourceError
    from config.settings import BIO_DB_ROOT
    from pathlib import Path

    uri_str = str(uri)

    # registry://snapshot
    if uri_str == "registry://snapshot":
        snapshot_path = Path(BIO_DB_ROOT) / "docs" / "registry_snapshot.md"
        if not snapshot_path.exists():
            return [ReadResourceContents(
                content="[ERROR] registry_snapshot.md 尚未產生，請先執行 scripts/export_registry.py",
                mime_type="text/plain",
            )]
        return [ReadResourceContents(
            content=snapshot_path.read_text(encoding="utf-8"),
            mime_type="text/markdown",
        )]

    # artifact://
    from store.factory import get_store as _get_store

    def _sync():
        with _get_store().read_conn() as con:
            return read_artifact_resource(con, uri_str)

    try:
        content, mime = await asyncio.to_thread(_sync)
    except ArtifactResourceError as exc:
        return [ReadResourceContents(content=f"[ERROR] {exc}", mime_type="text/plain")]

    return [ReadResourceContents(content=content, mime_type=mime)]


def _build_all_tools() -> list[types.Tool]:
    """Build full tool list from TOOL_CATALOG. Dangerous tools are included here; filtering is in list_tools()."""
    from server.tool_catalog import TOOL_CATALOG

    return [
        types.Tool(name=name, description=spec.description, inputSchema=spec.json_schema)
        for name, spec in TOOL_CATALOG.items()
    ]


# ── Tool 實作 ─────────────────────────────────────────────────────────────────


def _resolve_format_mode(args: dict) -> str:
    """Pick output format mode for response serialization.

    Returns one of {'text', 'json'}. Unknown / missing values fall back to 'text'
    for safety so older clients that omit the field keep their previous behavior.
    """
    fmt = str(args.get("format") or "text").lower()
    return "json" if fmt == "json" else "text"


def _json_dump(payload: dict | list) -> str:
    """JSON dump with stable ordering and non-ASCII preserved for Chinese summaries."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


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
    fmt = _resolve_format_mode(args)

    if analysis_type:
        rows = find_by_type(analysis_type, sample_id=sample_id, limit=limit)
    else:
        rows = recent_analyses(n=limit, sample_id=sample_id)

    # rows is a pandas DataFrame
    if rows.empty:
        if fmt == "json":
            return _json_dump(
                {"count": 0, "records": [], "sample_id": sample_id, "analysis_type": analysis_type}
            )
        return f"無分析記錄（sample_id={sample_id!r}, analysis_type={analysis_type!r}）"

    records = rows.to_dict("records")
    if fmt == "json":
        return _json_dump(
            {
                "count": len(records),
                "records": [
                    {
                        "analysis_id": str(r.get("analysis_id", "")),
                        "sample_id": r.get("sample_id", ""),
                        "analysis_type": r.get("analysis_type", ""),
                        "status": r.get("status", ""),
                        "completed_at": str(r.get("completed_at", "")),
                        "summary": str(r.get("summary", "") or ""),
                        "result_path": str(r.get("result_path", "") or ""),
                    }
                    for r in records
                ],
            }
        )

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
        for r in records
    ]
    return f"分析歷史（共 {len(rows)} 筆）\n\n" + _fmt_table(table_rows)


async def _handle_bio_history_timeline(args: dict) -> str:
    from store.factory import get_store

    n_days = int(args.get("n_days", 7))
    limit = max(1, min(int(args.get("limit", 50)), 500))
    fmt = _resolve_format_mode(args)
    with get_store().read_conn() as con:
        rows = con.execute(
            """
            SELECT sample_id,
                   analysis_type,
                   status,
                   requested_by,
                   completed_at,
                   summary
            FROM   analysis_history
            WHERE  completed_at >= now() - (? * INTERVAL '1 day')
            ORDER  BY completed_at DESC
            LIMIT  ?
            """,
            [n_days, limit],
        ).fetchall()
        cols = ["sample_id", "analysis_type", "status", "requested_by", "completed_at", "summary"]
        result_rows = [
            {**dict(zip(cols, r)), "completed_at": str(r[4])[:16] if r[4] else ""}
            for r in rows
        ]

    if not result_rows:
        if fmt == "json":
            return _json_dump({"count": 0, "n_days": n_days, "records": []})
        return f"最近 {n_days} 天無分析記錄。"

    if fmt == "json":
        return _json_dump(
            {
                "count": len(result_rows),
                "n_days": n_days,
                "records": [
                    {
                        "sample_id": r["sample_id"],
                        "analysis_type": r["analysis_type"],
                        "status": r["status"],
                        "requested_by": r["requested_by"] or "",
                        "completed_at": r["completed_at"] or "",
                        "summary": r["summary"] or "",
                    }
                    for r in result_rows
                ],
            }
        )

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
    from store.factory import get_store

    sample_id = args["sample_id"]
    analysis_type = args["analysis_type"]
    fmt = _resolve_format_mode(args)
    with get_store().read_conn() as con:
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
        if fmt == "json":
            return _json_dump(
                {
                    "exists": True,
                    "sample_id": sample_id,
                    "analysis_type": analysis_type,
                    "analysis_id": str(analysis_id),
                    "completed_at": str(completed_at),
                    "result_path": result_path or "",
                    "summary": summary or "",
                }
            )
        return (
            f"exists: true\n"
            f"analysis_id: {analysis_id}\n"
            f"completed_at: {str(completed_at)[:16]}\n"
            f"result_path: {result_path or '（未記錄）'}\n"
            f"summary: {(summary or '')[:80]}"
        )
    if fmt == "json":
        return _json_dump(
            {
                "exists": False,
                "sample_id": sample_id,
                "analysis_type": analysis_type,
            }
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
    from analysis.l1_cache import SPATIAL_EDA_TOOL_NAME, compute_current_context, semantic_search
    from config.settings import L1_COSINE_THRESHOLD

    query = args["query"]
    sample_id = args.get("sample_id")
    threshold = float(args.get("threshold", L1_COSINE_THRESHOLD))

    input_fingerprint = context_hash = None
    if sample_id:
        input_fingerprint, context_hash = compute_current_context(
            sample_id, SPATIAL_EDA_TOOL_NAME
        )

    results = semantic_search(
        query,
        n=1,
        threshold=threshold,
        sample_id=sample_id,
        input_fingerprint=input_fingerprint,
        context_hash=context_hash,
    )
    if not results:
        return f"L1 cache miss（threshold={threshold}）。建議呼叫分析工具生成新報告。"

    r = results[0]
    # 【刻意分岔，非 bug】MCP 端回傳完整報告 + expires_at；Web UI 端
    # agent_history._exec_bio_memory_query 會截到 2000 字保護本機 LLM context。目標不同，不統一。
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
    import re
    from store.factory import get_store

    sample_id = args["sample_id"]
    if not re.match(r"^[a-z0-9_-]+$", sample_id):
        return f"樣本 ID {sample_id!r} 格式錯誤：只允許小寫英數字、底線和連字號。"

    store = get_store()
    existing = store.get_sample(sample_id)
    if existing:
        return f"樣本 {sample_id!r} 已存在於 sample_registry，跳過登記。"

    store.register_sample(
        sample_id,
        args.get("project", ""),
        args["data_type"],
        args.get("platform", ""),
        args.get("species", "human"),
        args.get("tissue", ""),
        args["l3_path"],
        "mcp_server",  # ← provenance 標籤：MCP client 發起（Web UI 端為 "agent"，刻意分岔）
        args.get("notes", ""),
    )
    return f"樣本 {sample_id!r} 已登記至 sample_registry。\ndata_type: {args['data_type']}\nl3_path: {args['l3_path']}"


async def _handle_bio_artifact_search(args: dict) -> str:
    from analysis.artifact_registry import search_artifacts
    from store.factory import get_store

    query = args["query"]
    n = int(args.get("n", 5))
    threshold = float(args.get("threshold", 0.01))
    artifact_subtype = args.get("artifact_subtype")
    sample_id = args.get("sample_id")

    with get_store().read_conn() as con:
        results = search_artifacts(
            con,
            query,
            n=n,
            threshold=threshold,
            artifact_subtype=artifact_subtype,
            sample_id=sample_id,
        )

    if not results:
        return (
            f"ENGRAM 搜尋無命中（query={query!r}, threshold={threshold}, "
            f"subtype={artifact_subtype!r}）。"
        )

    lines = [f"ENGRAM 命中 {len(results)} 筆（threshold={threshold}）\n"]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. [{r['score']:.4f}] {r.get('artifact_subtype', '')} — "
            f"{r.get('label', '')}\n"
            f"   artifact_id: {r['artifact_id']}\n"
            f"   analysis_id: {r['analysis_id']}\n"
            f"   file_path:   {r.get('file_path', '')}\n"
            f"   layer:       {r.get('search_layer', '')}"
        )
    return "\n".join(lines)


async def _handle_bio_artifact_summary(args: dict) -> str:
    from analysis.artifact_registry import artifact_summary
    from store.factory import get_store

    sample_id = args["sample_id"]
    with get_store().read_conn() as con:
        summary = artifact_summary(con, sample_id)

    if summary["total_runs"] == 0:
        return f"樣本 {sample_id!r} 尚無已完成分析或 artifact 記錄。"

    by_subtype_lines = (
        "\n".join(f"  - {st}: {ct}" for st, ct in sorted(summary["by_subtype"].items()))
        or "  （無 subtype 記錄）"
    )
    latest = summary["latest_run"] or {}
    return (
        f"sample_id: {summary['sample_id']}\n"
        f"total_runs: {summary['total_runs']}\n"
        f"total_artifacts: {summary['total_artifacts']}\n"
        f"by_subtype:\n{by_subtype_lines}\n"
        f"latest_run:\n"
        f"  analysis_id:   {latest.get('analysis_id', '')}\n"
        f"  analysis_type: {latest.get('analysis_type', '')}\n"
        f"  completed_at:  {latest.get('completed_at', '')}\n"
        f"  artifact_count: {latest.get('artifact_count', 0)}"
    )


# ── 分析工具委派至 agent.py（避免雙份維護；長時間 I/O 走 to_thread） ───────────
#
# 這些 _exec_* 函數本身是同步、回傳 str 的純 Python 函數（不需 LLM），
# 因此可由 MCP server 直接呼叫，與 agent.py / web_app.py 共用同一份實作。
#
# 安全性備註：`server.agent` 模組本身沒有 import-time 副作用 —— Anthropic / Google /
# OpenAI SDK 都包在 `_get_*_client()` 內部 lazy import；module-level 僅定義常數、
# BIO_TOOLS schema 與函數。因此 stdio 啟動的冷啟動成本不會被連累。
# 若未來重構 agent.py 時違反此契約，需同步調整本 wrapper（例如改成 subprocess）。


async def _handle_bio_lookup_sample(args: dict) -> str:
    from analysis.sample_lookup import lookup_sample, list_samples

    def _sync() -> str:
        if args.get("list_all"):
            return list_samples(
                project=args.get("project"),
                data_type=args.get("data_type"),
            )
        query = args.get("query", "").strip()
        if not query:
            return "請提供 query 或設定 list_all=true。"
        return lookup_sample(
            query,
            fuzzy=bool(args.get("fuzzy", False)),
            project=args.get("project"),
            data_type=args.get("data_type"),
        )

    return await asyncio.to_thread(_sync)


async def _handle_bio_cascade_impact(args: dict) -> str:
    analysis_id = args.get("analysis_id", "").strip()
    if not analysis_id:
        return "錯誤：analysis_id 為必填參數。"

    def _sync() -> str:
        from store.factory import get_store
        from analysis.impact import cascade_impact, render_impact_md

        with get_store().read_conn() as con:
            report = cascade_impact(con, analysis_id)
        return render_impact_md(report)

    return await asyncio.to_thread(_sync)


async def _handle_bio_compare_versions(args: dict) -> str:
    tool_name = args.get("tool_name", "").strip()
    version_a = args.get("version_a", "").strip()
    version_b = args.get("version_b", "").strip()
    sample_id = args.get("sample_id", "").strip() or None
    analysis_type = args.get("analysis_type", "").strip() or None

    if not tool_name or not version_a or not version_b:
        return "錯誤：tool_name、version_a、version_b 為必填參數。"

    def _sync() -> str:
        from store.factory import get_store
        from analysis.version_compare import bio_compare_versions, render_comparison_md

        with get_store().read_conn() as con:
            report = bio_compare_versions(
                con,
                tool_name=tool_name,
                version_a=version_a,
                version_b=version_b,
                sample_id=sample_id,
                analysis_type=analysis_type,
                land_report=True,
            )
        return render_comparison_md(report)

    return await asyncio.to_thread(_sync)


async def _handle_bio_failure_summary(args: dict) -> str:
    """PM1: Aggregate failure_diagnosis from analysis_history (EvolveMem-inspired)."""
    from store.factory import get_store

    sample_id = args.get("sample_id", "").strip() or None
    analysis_type = args.get("analysis_type", "").strip() or None
    since_days = int(args.get("since_days", 30))
    top_n = int(args.get("top_n", 5))

    def _sync() -> str:
        with get_store().read_conn() as con:
            # Build WHERE clause
            conditions = [
                "failure_diagnosis IS NOT NULL",
                "started_at >= now() - (? * INTERVAL '1 day')",
            ]
            params: list = [since_days]
            if sample_id:
                conditions.append("sample_id = ?")
                params.append(sample_id)
            if analysis_type:
                conditions.append("analysis_type = ?")
                params.append(analysis_type)
            where = " AND ".join(conditions)

            # 1. Type distribution
            type_rows = con.execute(
                f"""
                SELECT
                    json_extract_string(failure_diagnosis, '$.type') AS diag_type,
                    COUNT(*) AS cnt
                FROM analysis_history
                WHERE {where}
                GROUP BY diag_type
                ORDER BY cnt DESC
                """,
                params,
            ).fetchall()

            if not type_rows:
                return (
                    f"[bio_failure_summary] 最近 {since_days} 天內無帶有 failure_diagnosis 的記錄。\n"
                    "提示：請先執行 scripts/25_migrate_schema_v24_failure_diagnosis.py 並待分析工具寫入診斷資料。"
                )

            total = sum(r[1] for r in type_rows)
            success_cnt = next((r[1] for r in type_rows if r[0] == "success"), 0)
            failure_cnt = total - success_cnt

            lines = [
                f"=== bio_failure_summary（最近 {since_days} 天）===",
                f"總計 {total} 筆  |  成功 {success_cnt}  |  失敗 {failure_cnt}",
                "",
                "【類型分佈】",
            ]
            for diag_type, cnt in type_rows:
                pct = cnt / total * 100
                lines.append(f"  {diag_type:<30} {cnt:>5} 筆  ({pct:.1f}%)")

            # 2. Top-N failure details (non-success only)
            detail_rows = con.execute(
                f"""
                SELECT
                    json_extract_string(failure_diagnosis, '$.type')   AS diag_type,
                    json_extract_string(failure_diagnosis, '$.detail') AS detail,
                    analysis_type,
                    sample_id,
                    started_at::DATE AS run_date
                FROM analysis_history
                WHERE {where}
                  AND json_extract_string(failure_diagnosis, '$.type') != 'success'
                ORDER BY started_at DESC
                LIMIT ?
                """,
                params + [top_n],
            ).fetchall()

            if detail_rows:
                lines += ["", f"【最近 {top_n} 筆失敗樣本】"]
                for diag_type, detail, atype, sid, run_date in detail_rows:
                    lines.append(
                        f"  [{run_date}] {sid} / {atype} → {diag_type}: {(detail or '')[:120]}"
                    )

            return "\n".join(lines)

    try:
        return await asyncio.to_thread(_sync)
    except Exception as exc:
        return f"[ERROR] bio_failure_summary 失敗：{exc}"


async def _handle_bio_read_report(args: dict) -> str:
    from analysis.report_reader import read_report, ReportReadError

    def _sync() -> str:
        try:
            r = read_report(
                args["result_path"],
                max_chars=int(args.get("max_chars", 8000)),
                head_fraction=float(args.get("head_fraction", 0.75)),
            )
        except ReportReadError as exc:
            return f"[ERROR] bio_read_report 失敗：{exc}"
        meta = (
            f"path: {r.path}\n"
            f"total_chars: {r.total_chars} | truncated: {r.truncated}\n"
            f"note: {r.note}\n"
        )
        if r.tail:
            return f"{meta}--- HEAD ---\n{r.head}\n--- TAIL ---\n{r.tail}"
        return f"{meta}--- CONTENT ---\n{r.head}"

    return await asyncio.to_thread(_sync)


async def _handle_bio_get_figure(args: dict) -> list[types.ImageContent]:
    """依 figure_id 取回快取圖片，回傳 MCP ImageContent（多模態通道，不進文字 context）。"""
    from analysis.figure_cache import load_figure_b64

    figure_id = args["figure_id"]

    def _sync() -> tuple[str, str]:
        return load_figure_b64(figure_id)

    b64, mime = await asyncio.to_thread(_sync)
    return [types.ImageContent(type="image", data=b64, mimeType=mime)]


async def _handle_bio_get_artifact(args: dict) -> str:
    """回傳分析數據檔的取用 handle（路徑 + 下載 URL + 預覽）；任何 client 皆可用。"""
    from analysis.artifact_resources import get_artifact_handle, ArtifactResourceError
    from store.factory import get_store

    artifact_id = args["artifact_id"]
    preview_lines = int(args.get("preview_lines", 20))

    def _sync() -> dict:
        with get_store().read_conn() as con:
            return get_artifact_handle(con, artifact_id, preview_lines=preview_lines)

    try:
        h = await asyncio.to_thread(_sync)
    except ArtifactResourceError as exc:
        return f"[ERROR] bio_get_artifact: {exc}"

    if not h.get("found"):
        return f"artifact_id={artifact_id!r} 不存在於 analysis_artifacts。"

    lines = [
        f"label: {h['label']}",
        f"subtype: {h['subtype']} | mime: {h['mime_type']} | size: {h['size_kb']} KB",
        f"local_path: {h['local_path']}",
        f"web_url: {h['web_url']}",
    ]
    if h.get("preview"):
        lines.append(f"\n--- 預覽（前 {preview_lines} 行）---\n{h['preview']}")
    return "\n".join(lines)


# ── call_tool 分發 ────────────────────────────────────────────────────────────
#
# 兩類工具：
#   - delegate（TOOL_CATALOG 有 module+func）：業務邏輯在獨立 sync 函式，通用
#     _make_delegate_handler() 產生 `asyncio.to_thread(fn, args)` 包裝，不再手刻。
#   - mcp_only（TOOL_CATALOG 只有 mcp_handler）：邏輯內嵌在上面的 `_handle_bio_*`
#     函式裡（客製前後處理，不適合塞進通用查表），維持顯式函式。


def _make_delegate_handler(module_path: str, func_name: str):
    """為一個 delegate 工具產生 MCP async handler：動態 import + asyncio.to_thread。"""
    import importlib

    async def _handler(args: dict) -> str:
        fn = getattr(importlib.import_module(module_path), func_name)
        return await asyncio.to_thread(fn, args)

    return _handler


_HANDLERS: dict[str, "Callable[[dict], Any]"] = {}
for _name, _spec in _TOOL_CATALOG.items():
    if _spec.module is not None and _spec.func is not None:
        _HANDLERS[_name] = _make_delegate_handler(_spec.module, _spec.func)
del _name, _spec

_HANDLERS.update(
    {
        "bio_history_lookup": _handle_bio_history_lookup,
        "bio_history_timeline": _handle_bio_history_timeline,
        "bio_history_check": _handle_bio_history_check,
        "bio_history_search": _handle_bio_history_search,
        "bio_memory_query": _handle_bio_memory_query,
        "bio_memory_write": _handle_bio_memory_write,
        "bio_register_sample": _handle_bio_register_sample,
        "bio_artifact_search": _handle_bio_artifact_search,
        "bio_artifact_summary": _handle_bio_artifact_summary,
        "bio_lookup_sample": _handle_bio_lookup_sample,
        "bio_cascade_impact": _handle_bio_cascade_impact,
        "bio_compare_versions": _handle_bio_compare_versions,
        "bio_failure_summary": _handle_bio_failure_summary,
        "bio_read_report": _handle_bio_read_report,
        "bio_get_figure": _handle_bio_get_figure,
        "bio_get_artifact": _handle_bio_get_artifact,
    }
)

assert set(_HANDLERS) == set(_TOOL_CATALOG), (
    f"_HANDLERS/TOOL_CATALOG 工具集不一致：只在一邊的有 "
    f"{set(_HANDLERS) ^ set(_TOOL_CATALOG)}"
)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent | types.ImageContent]:
    requested_by = None
    if isinstance(arguments, dict):
        requested_by = arguments.get("requested_by")
    if not requested_by:
        requested_by = "mcp_client"

    # ── Fast-Path 攔截（AA3）──────────────────────────────────────────────────
    # bio_history_search 接受自然語言查詢，遇到簡單意圖（最近N筆/時間軸/樣本列表）
    # 可直接走 SQL 結構化工具，繞過 embedding server，降低 latency 並節省 token。
    # 兩種 transport（stdio / HTTP-SSE）共用同一個 call_tool 入口，均生效。
    if name == "bio_history_search" and isinstance(arguments, dict):
        _query = arguments.get("query", "")
        if _query:
            try:
                from server.fast_path import try_fast_path, render_header

                _hit = try_fast_path(_query)
                if _hit is not None:
                    _fp_handler = _HANDLERS.get(_hit.tool_name)
                    if _fp_handler is not None:
                        t0_fp = time.monotonic()
                        try:
                            _fp_result = await _fp_handler(_hit.args)
                        except Exception as _fp_exc:
                            logger.warning(
                                "fast_path MCP intent=%s tool=%s failed, fallback: %s",
                                _hit.intent,
                                _hit.tool_name,
                                _fp_exc,
                            )
                        else:
                            _record_metric(
                                name,
                                int((time.monotonic() - t0_fp) * 1000),
                                "ok",
                                requested_by=requested_by,
                            )
                            logger.info(
                                "fast_path MCP hit intent=%s → %s (bypassed embedding)",
                                _hit.intent,
                                _hit.tool_name,
                            )
                            _fp_text = render_header(_hit) + (
                                _fp_result if isinstance(_fp_result, str) else str(_fp_result)
                            )
                            return [types.TextContent(type="text", text=_fp_text)]
            except ImportError:
                pass  # fast_path module not available; continue normal dispatch

    handler = _HANDLERS.get(name)
    if handler is None:
        _record_metric(name, 0, "user_error", requested_by=requested_by)
        return [types.TextContent(type="text", text=f"[ERROR] 未知工具：{name!r}")]

    # Dangerous tool gate：即使 handler 存在，未開 env flag 也拒絕（defense in depth）
    if name in _DANGEROUS_TOOLS and not _dangerous_tools_enabled():
        _record_metric(name, 0, "user_error", requested_by=requested_by)
        logger.warning("Dangerous tool %r called but MCP_ENABLE_DANGEROUS_TOOLS not set", name)
        return [
            types.TextContent(
                type="text",
                text=(
                    f"[ERROR] {name} 為高權限工具，目前未啟用。"
                    "設定 env MCP_ENABLE_DANGEROUS_TOOLS=true 並重啟 server 才可呼叫。"
                ),
            )
        ]

    # Rate limit gate（僅針對打 embedding server 的工具）
    if name in _RATE_LIMITED_TOOLS and not _rate_limit_check(f"tool:{name}"):
        logger.warning("Rate limit exceeded for tool %r", name)
        _record_metric(
            name, 0, "rate_limited", error_class="RateLimitExceeded", requested_by=requested_by
        )
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
        _record_metric(
            name,
            int((time.monotonic() - t0) * 1000),
            "rate_limited",
            error_class="RateLimitExceeded",
            requested_by=requested_by,
        )
        logger.warning("Tool %r rate limited: %s", name, exc)
        return [types.TextContent(type="text", text=f"[ERROR] {name}: {exc}")]
    except (ValueError, KeyError, TypeError) as exc:
        _record_metric(
            name,
            int((time.monotonic() - t0) * 1000),
            "user_error",
            error_class=exc.__class__.__name__,
            requested_by=requested_by,
        )
        # 使用者錯誤：參數驗證失敗、缺欄位、型別錯
        logger.info("Tool %r user error: %s", name, exc)
        return [types.TextContent(type="text", text=f"[ERROR] {name} 參數錯誤：{exc}")]
    except Exception as exc:
        _record_metric(
            name,
            int((time.monotonic() - t0) * 1000),
            "system_error",
            error_class=exc.__class__.__name__,
            requested_by=requested_by,
        )
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
    _record_metric(name, int((time.monotonic() - t0) * 1000), "ok", requested_by=requested_by)
    # 圖片類工具回傳 content block list（如 bio_get_figure 的 ImageContent）→ 直接送出
    if isinstance(result, list):
        return list(result)  # type: ignore[return-value]
    # 文字結果統一出口：剝除 inline base64 圖片，避免爆 LLM context（換成 bio_get_figure 佔位符）
    return [types.TextContent(type="text", text=strip_base64_for_llm(result))]


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
            auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip() or None
            if auth_token is not None:
                presented = _extract_bearer_token(scope)
                if not presented:
                    await _send_auth_error(send, 401, "Unauthorized: missing Bearer token")
                    return
                import hmac

                if not hmac.compare_digest(presented, auth_token):
                    await _send_auth_error(send, 401, "Unauthorized: invalid token")
                    return
            await session_manager.handle_request(scope, receive, send)

    return _asgi_handler, _mcp_lifespan


def _startup_cleanup_stale_runs() -> None:
    """MCP server 為長駐程序，啟動時清理 > 24h 仍為 running 的紀錄（CLAUDE.md §6），並自動註冊所有 Lazy Tools (AB4)。"""
    from store.factory import get_store

    store = get_store()

    # 1. 清理過期運行紀錄
    n = store.cleanup_stale_runs()
    if n:
        logger.info("Startup cleanup: marked %d stale running rows", n)

    # 2. 自動註冊 @register_tool_on_import 的 Lazy Tools (AB4)
    try:
        # 導入分析模組以激活裝飾器 lazy append
        import analysis.bulk_eda  # noqa: F401
        import analysis.bulk_deg  # noqa: F401
        import analysis.bulk_heatmap  # noqa: F401
        import analysis.enrichment  # noqa: F401
        import analysis.marker_genes  # noqa: F401
        import analysis.image_conversion  # noqa: F401
        import analysis.relabel_clusters  # noqa: F401
        import analysis.celltypist_annotate  # noqa: F401
        import analysis.mcseg_merge  # noqa: F401
        import analysis.loupe_export  # noqa: F401
        import analysis.sc_spatial_tools  # noqa: F401
        import analysis.sc_clustering  # noqa: F401
        # 2026-07-24 架構審查（候選 5）：以下 6 個模組也有 @register_tool_on_import，
        # 但先前沒被列在這裡——裝飾器只在模組被 import 時執行，這 6 個工具因此從未真的
        # 寫進 tools 表。`tests/test_lazy_tool_registration_coverage.py` 守住不再漏。
        import analysis.mcseg_wrapper  # noqa: F401
        import analysis.mcseg_quality  # noqa: F401
        import analysis.report_generator  # noqa: F401
        import analysis.pathway_scoring  # noqa: F401
        import analysis.multiomics_integration  # noqa: F401
        import analysis.external_import  # noqa: F401

        from analysis.tool_registry import register_all_lazy_tools

        with store.write_conn() as con:
            n_lazy = register_all_lazy_tools(con)
        if n_lazy:
            logger.info("Startup lazy registry: registered %d active tools in DuckDB", n_lazy)
    except Exception as lazy_exc:
        logger.warning("Startup lazy registry failed: %s", lazy_exc)


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
