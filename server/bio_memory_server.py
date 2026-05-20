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

分析執行（重量級，會寫 DB / 跑沙盒）：
    bio_run_spatial_eda       — 空間轉錄體 EDA（10–30 秒，需 l2_ready=true）
    bio_run_bulk_eda          — Bulk RNA EDA（10–60 秒）
    bio_execute_code          — 沙盒執行 Python 程式碼（白名單 import，timeout=60s）
    bio_tool_health           — HELIX 工具庫健康報告與穩定化迭代管理

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
import time
import uuid
from collections import deque
from pathlib import Path

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


# 需要 rate limit 保護的工具（會打 embedding server 或耗用大量資源）
_RATE_LIMITED_TOOLS = frozenset(
    {
        "bio_history_search",
        "bio_memory_query",
        "bio_memory_write",
        "bio_artifact_search",
        # 工具語意搜尋：打 embedding server
        "bio_find_tool",
        # 分析執行工具：寫 L1 cache（打 embedding server）+ 重量級運算
        "bio_run_spatial_eda",
        "bio_run_bulk_eda",
        # 沙盒執行：CPU/I/O 重量級
        "bio_execute_code",
    }
)

# 高權限工具：可執行任意 Python（即使沙盒）；預設不對 MCP 客戶端暴露。
# 設定 env MCP_ENABLE_DANGEROUS_TOOLS=true 才會出現在 list_tools 並可被呼叫。
# 此 flag 為 defense in depth — 即使 MCP_AUTH_TOKEN 未設，也不會意外洩漏沙盒執行入口。
_DANGEROUS_TOOLS = frozenset({"bio_execute_code"})


def _dangerous_tools_enabled() -> bool:
    """Read MCP_ENABLE_DANGEROUS_TOOLS env at runtime (no caching, by design).

    Caching this value would break test isolation: pytest's monkeypatch.setenv
    flips the env per-test, and every call site expects to see the fresh value.
    Cost is one os.environ.get() lookup per list_tools / call_tool — negligible.
    """
    return os.environ.get("MCP_ENABLE_DANGEROUS_TOOLS", "").lower() in ("1", "true", "yes")

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
    tools = _build_all_tools()
    if not _dangerous_tools_enabled():
        tools = [t for t in tools if t.name not in _DANGEROUS_TOOLS]
    return tools


# ── MCP Resources：分析後數據檔交付（artifact:// URI）────────────────────────────

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    """列出已登記的分析 artifact 供客戶端取用（resources/list）。"""
    import duckdb
    from analysis.artifact_resources import list_artifact_resources
    from config.settings import DUCKDB_PATH

    def _sync() -> list[dict]:
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            return list_artifact_resources(con)

    items = await asyncio.to_thread(_sync)
    return [
        types.Resource(
            uri=it["uri"],
            name=it["name"],
            description=it["description"],
            mimeType=it["mime_type"],
            size=(it["size_kb"] * 1024 if it["size_kb"] is not None else None),
        )
        for it in items
    ]


@server.read_resource()
async def read_resource(uri):  # uri: pydantic AnyUrl
    """依 artifact:// URI 取回數據檔內容（resources/read）。"""
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    import duckdb
    from analysis.artifact_resources import read_artifact_resource, ArtifactResourceError
    from config.settings import DUCKDB_PATH

    def _sync():
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            return read_artifact_resource(con, str(uri))

    try:
        content, mime = await asyncio.to_thread(_sync)
    except ArtifactResourceError as exc:
        # 以文字內容回報錯誤，讓客戶端/使用者看到原因與下載備援
        return [ReadResourceContents(content=f"[ERROR] {exc}", mime_type="text/plain")]

    return [ReadResourceContents(content=content, mime_type=mime)]


def _build_all_tools() -> list[types.Tool]:
    """Build full tool list. Dangerous tools are included here; filtering is in list_tools."""
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
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "回傳格式：text（Markdown 表格，預設）或 json（結構化字串，供客戶端解析）。",
                        "default": "text",
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
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "回傳格式：text（Markdown 表格，預設）或 json。",
                        "default": "text",
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
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "回傳格式：text（YAML-like，預設）或 json。",
                        "default": "text",
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
        types.Tool(
            name="bio_artifact_search",
            description=(
                "搜尋 ENGRAM 分析產出（圖、CSV、報告）— RRF hybrid（exact subtype + HNSW cosine）。"
                "回傳 artifact 列表含 score、file_path、artifact_subtype、analysis_id；不含檔案內容。"
                "需要 embedding server 在線（port 8081）；artifact_subtype 提供時走 Layer 1 + Layer 2 融合。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然語言查詢，例如「腫瘤微環境細胞密度圖」。",
                    },
                    "n": {
                        "type": "integer",
                        "description": "回傳筆數上限（預設 5）。",
                        "default": 5,
                    },
                    "threshold": {
                        "type": "number",
                        "description": "RRF 分數門檻（預設 0.01；範圍約 0.008–0.033）。",
                        "default": 0.01,
                    },
                    "artifact_subtype": {
                        "type": "string",
                        "description": "限定 subtype（Layer 1 exact match），例如 gene_spatial_map | qc_stats。",
                    },
                    "sample_id": {
                        "type": "string",
                        "description": "限定樣本 ID（可選，透過 JOIN analysis_history 過濾）。",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="bio_artifact_summary",
            description=(
                "回傳指定樣本的 ENGRAM artifact 概覽（0 token，純 SQL）。"
                "顯示總執行次數、總 artifact 數、各 subtype 分佈、最新一次執行資訊。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本 ID，例如 crc_official_v4。",
                    },
                },
                "required": ["sample_id"],
            },
        ),
        types.Tool(
            name="bio_read_report",
            description=(
                "讀取分析報告（.md/.txt/.log）原文。路徑必須位於 results/ 或 results_ana/ 內，"
                "其他路徑會被沙盒拒絕。超過 max_chars 時自動截斷為 head+tail 兩段。"
                "用於：使用者問「報告裡寫了什麼」「打開 xxx.md」等需要原文佐證的請求。"
                "禁止憑檔名推測內容——務必呼叫此工具取得真實文字。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "result_path": {
                        "type": "string",
                        "description": (
                            "報告路徑。可絕對路徑或 BIO_DB_ROOT-relative，"
                            "例如 results/bulk_eda/bulk_eda_xxx.md。"
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "回傳字元數上限（預設 8000）。",
                        "default": 8000,
                    },
                    "head_fraction": {
                        "type": "number",
                        "description": "head 比例（預設 0.75，其餘為 tail）。",
                        "default": 0.75,
                    },
                },
                "required": ["result_path"],
            },
        ),
        types.Tool(
            name="bio_check_l2_sufficiency",
            description=(
                "確認樣本的 L2 Parquet 是否已就緒（l2_ready = true）。"
                "在執行 bio_run_spatial_eda 之前必須先呼叫；l2_ready=false 時回傳需要執行的轉換命令。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本 ID，例如 crc_official_v4。",
                    },
                },
                "required": ["sample_id"],
            },
        ),
        types.Tool(
            name="bio_run_spatial_eda",
            description=(
                "對指定樣本執行空間轉錄體 EDA（QC 統計 + top genes + 報告生成）。"
                "完成後自動寫入 analysis_history + L1 快取。需要 L2 Parquet 已轉換（l2_ready = true）。"
                "耗時約 10–30 秒；rate-limited（會寫 embedding）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本 ID，例如 crc_official_v4。",
                    },
                    "requested_by": {
                        "type": "string",
                        "description": "請求者（預設 mcp_client）。",
                        "default": "mcp_client",
                    },
                },
                "required": ["sample_id"],
            },
        ),
        types.Tool(
            name="bio_run_bulk_eda",
            description=(
                "對 Bulk RNA-seq 樣本集執行 EDA（QC 統計 + top genes + 樣本相關 + PCA）。"
                "完成後自動寫入 analysis_history。需要先執行 scripts/bulk_rna/ pipeline 產生 gene_counts.tsv。"
                "耗時約 10–60 秒；rate-limited（會寫 embedding）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_id": {
                        "type": "string",
                        "description": "樣本集 ID，例如 Kallisto_v1。",
                    },
                    "requested_by": {
                        "type": "string",
                        "description": "請求者（預設 mcp_client）。",
                        "default": "mcp_client",
                    },
                },
                "required": ["sample_id"],
            },
        ),
        types.Tool(
            name="bio_find_tool",
            description=(
                "語意搜尋既有可重用的分析函數（tool discovery）。"
                "寫 bio_execute_code 前務必先呼叫：描述分析意圖，回傳最相關的既有函數 "
                "+ 簽名 + import 方式。命中就 import 重用，勿從零重寫。"
                "本地 embedding + HNSW，0 LLM token；全 miss 才需自行撰寫。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要做的分析意圖（自然語言）。",
                    },
                    "n": {
                        "type": "integer",
                        "description": "回傳候選數上限（預設 5）。",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="bio_execute_code",
            description=(
                "沙盒執行動態生成的 Python 程式碼（用於非標準分析）。"
                "只允許白名單 import（duckdb 除外，pandas/numpy/scipy/anndata/scanpy 等）。"
                "禁止 os.system, subprocess, open(), eval, exec, glob.glob 等危險操作。"
                "timeout 預設 60 秒，最大 300 秒；rate-limited。"
                "⚠️ 高權限工具：預設**不對外暴露**。必須設定 env `MCP_ENABLE_DANGEROUS_TOOLS=true` 才會出現在 tools/list；"
                "同時建議搭配 `MCP_AUTH_TOKEN` 與 `MCP_BIND_HOST=127.0.0.1`。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要執行的 Python 程式碼。",
                    },
                    "description": {
                        "type": "string",
                        "description": "此程式碼的分析目的（用於 analysis_history 記錄）。",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "執行超時秒數（預設 60，最大 300）。",
                        "default": 60,
                    },
                },
                "required": ["code", "description"],
            },
        ),
        types.Tool(
            name="bio_tool_health",
            description=(
                "HELIX 工具庫健康報告與穩定化迭代管理。支援六個 action：\n"
                "  'report'          — 健康狀態總覽（active/deprecated/熱區/進行中迭代/VLM 快照）\n"
                "  'diagnose'        — 寫入 stability_note（需 tool_name + note）\n"
                "  'stabilize'       — 開啟穩定化迭代（需 tool_name + diagnosis + action_taken）\n"
                "  'close_stabilize' — 關閉迭代（需 log_id + outcome；outcome: stabilized/ongoing/reverted）\n"
                "  'trend'           — 複雜度改善趨勢（可選 tool_name 過濾）\n"
                "  'prune'           — 清理未被引用的 deprecated 紀錄（需 tool_name）"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["report", "diagnose", "stabilize", "close_stabilize", "trend", "prune"],
                        "description": "操作類型。",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "diagnose/stabilize/prune 時必填。",
                    },
                    "note": {
                        "type": "string",
                        "description": "diagnose 時必填：說明為何頻繁變動及穩定化方向。",
                    },
                    "diagnosis": {
                        "type": "string",
                        "description": "stabilize 時必填：問題診斷描述。",
                    },
                    "action_taken": {
                        "type": "string",
                        "description": "stabilize 時必填：計畫採取的行動。",
                    },
                    "log_id": {
                        "type": "string",
                        "description": "close_stabilize 時必填：open_stabilization 回傳的 UUID。",
                    },
                    "outcome": {
                        "type": "string",
                        "enum": ["stabilized", "ongoing", "reverted"],
                        "description": "close_stabilize 時必填：迭代結果。",
                    },
                },
                "required": ["action"],
            },
        ),
        types.Tool(
            name="bio_get_figure",
            description=(
                "依 figure_id 取回單張圖片（MCP image content，供多模態模型視覺推理）。"
                "報告類工具回傳的文字裡，圖片以佔位符 [圖片:... | id=<figure_id> | 用 bio_get_figure 索取] 呈現——"
                "base64 已從文字 context 剝除以節省 token。需要看某張圖時，用該 figure_id 呼叫此工具單張取回。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "figure_id": {
                        "type": "string",
                        "description": "佔位符中的 id（hex），例如 a1b2c3d4e5f6。",
                    },
                },
                "required": ["figure_id"],
            },
        ),
        types.Tool(
            name="bio_get_artifact",
            description=(
                "取得分析數據檔的取用 handle（任何 client 皆可用，含不支援 MCP resources 者）。"
                "回傳檔案 metadata + 本地絕對路徑 + web_app 下載 URL + 文字檔的前幾行預覽。"
                "用於：使用者想下載/取得分析產出的 csv/parquet/報告等數據檔。"
                "artifact_id 由 bio_artifact_search 取得。"
                "（支援 resources 的 client 可改用 resources/read artifact://<id> 直接取回內容。）"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "artifact 的 UUID（來自 bio_artifact_search）。",
                    },
                    "preview_lines": {
                        "type": "integer",
                        "description": "文字檔預覽行數（預設 20）。",
                        "default": 20,
                    },
                },
                "required": ["artifact_id"],
            },
        ),
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
            return _json_dump({"count": 0, "records": [], "sample_id": sample_id, "analysis_type": analysis_type})
        return f"無分析記錄（sample_id={sample_id!r}, analysis_type={analysis_type!r}）"

    records = rows.to_dict("records")
    if fmt == "json":
        return _json_dump({
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
        })

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
    import duckdb
    from config.settings import DUCKDB_PATH

    n_days = int(args.get("n_days", 7))
    limit = max(1, min(int(args.get("limit", 50)), 500))
    fmt = _resolve_format_mode(args)
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
        if fmt == "json":
            return _json_dump({"count": 0, "n_days": n_days, "records": []})
        return f"最近 {n_days} 天無分析記錄。"

    if fmt == "json":
        return _json_dump({
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
        })

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
    fmt = _resolve_format_mode(args)
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
        if fmt == "json":
            return _json_dump({
                "exists": True,
                "sample_id": sample_id,
                "analysis_type": analysis_type,
                "analysis_id": str(analysis_id),
                "completed_at": str(completed_at),
                "result_path": result_path or "",
                "summary": summary or "",
            })
        return (
            f"exists: true\n"
            f"analysis_id: {analysis_id}\n"
            f"completed_at: {str(completed_at)[:16]}\n"
            f"result_path: {result_path or '（未記錄）'}\n"
            f"summary: {(summary or '')[:80]}"
        )
    if fmt == "json":
        return _json_dump({
            "exists": False,
            "sample_id": sample_id,
            "analysis_type": analysis_type,
        })
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


async def _handle_bio_artifact_search(args: dict) -> str:
    import duckdb
    from analysis.artifact_registry import search_artifacts
    from config.settings import DUCKDB_PATH

    query = args["query"]
    n = int(args.get("n", 5))
    threshold = float(args.get("threshold", 0.01))
    artifact_subtype = args.get("artifact_subtype")
    sample_id = args.get("sample_id")

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
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
    import duckdb
    from analysis.artifact_registry import artifact_summary
    from config.settings import DUCKDB_PATH

    sample_id = args["sample_id"]
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        summary = artifact_summary(con, sample_id)

    if summary["total_runs"] == 0:
        return f"樣本 {sample_id!r} 尚無已完成分析或 artifact 記錄。"

    by_subtype_lines = "\n".join(
        f"  - {st}: {ct}" for st, ct in sorted(summary["by_subtype"].items())
    ) or "  （無 subtype 記錄）"
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


async def _handle_bio_check_l2_sufficiency(args: dict) -> str:
    from server.agent import _exec_bio_check_l2_sufficiency
    return await asyncio.to_thread(_exec_bio_check_l2_sufficiency, args)


async def _handle_bio_run_spatial_eda(args: dict) -> str:
    from server.agent import _exec_bio_run_spatial_eda
    return await asyncio.to_thread(_exec_bio_run_spatial_eda, args)


async def _handle_bio_run_bulk_eda(args: dict) -> str:
    from server.agent import _exec_bio_run_bulk_eda
    return await asyncio.to_thread(_exec_bio_run_bulk_eda, args)


async def _handle_bio_execute_code(args: dict) -> str:
    from server.agent import _exec_bio_execute_code
    # timeout clamp（防 MCP 客戶端傳大數）
    t = args.get("timeout", 60)
    try:
        t = max(1, min(int(t), 300))
    except (TypeError, ValueError):
        t = 60
    args = {**args, "timeout": t}
    return await asyncio.to_thread(_exec_bio_execute_code, args)


async def _handle_bio_tool_health(args: dict) -> str:
    from server.agent import _exec_bio_tool_health
    return await asyncio.to_thread(_exec_bio_tool_health, args)


async def _handle_bio_find_tool(args: dict) -> str:
    from server.agent import _exec_bio_find_tool
    return await asyncio.to_thread(_exec_bio_find_tool, args)


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
    import duckdb
    from analysis.artifact_resources import get_artifact_handle, ArtifactResourceError
    from config.settings import DUCKDB_PATH

    artifact_id = args["artifact_id"]
    preview_lines = int(args.get("preview_lines", 20))

    def _sync() -> dict:
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
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

_HANDLERS = {
    "bio_history_lookup": _handle_bio_history_lookup,
    "bio_history_timeline": _handle_bio_history_timeline,
    "bio_history_check": _handle_bio_history_check,
    "bio_history_search": _handle_bio_history_search,
    "bio_memory_query": _handle_bio_memory_query,
    "bio_memory_write": _handle_bio_memory_write,
    "bio_register_sample": _handle_bio_register_sample,
    "bio_artifact_search": _handle_bio_artifact_search,
    "bio_artifact_summary": _handle_bio_artifact_summary,
    "bio_check_l2_sufficiency": _handle_bio_check_l2_sufficiency,
    "bio_run_spatial_eda": _handle_bio_run_spatial_eda,
    "bio_run_bulk_eda": _handle_bio_run_bulk_eda,
    "bio_execute_code": _handle_bio_execute_code,
    "bio_find_tool": _handle_bio_find_tool,
    "bio_tool_health": _handle_bio_tool_health,
    "bio_read_report": _handle_bio_read_report,
    "bio_get_figure": _handle_bio_get_figure,
    "bio_get_artifact": _handle_bio_get_artifact,
}


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent]:
    handler = _HANDLERS.get(name)
    if handler is None:
        _record_metric(name, 0, "user_error")
        return [types.TextContent(type="text", text=f"[ERROR] 未知工具：{name!r}")]

    # Dangerous tool gate：即使 handler 存在，未開 env flag 也拒絕（defense in depth）
    if name in _DANGEROUS_TOOLS and not _dangerous_tools_enabled():
        _record_metric(name, 0, "user_error")
        logger.warning("Dangerous tool %r called but MCP_ENABLE_DANGEROUS_TOOLS not set", name)
        return [types.TextContent(
            type="text",
            text=(
                f"[ERROR] {name} 為高權限工具，目前未啟用。"
                "設定 env MCP_ENABLE_DANGEROUS_TOOLS=true 並重啟 server 才可呼叫。"
            ),
        )]

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
