"""
Phase 5 — Hermes Bio-Memory Agent Loop。

推理引擎：llama.cpp OpenAI-compatible API（port 8080，本機 Gemma 4 Vision）
工具呼叫格式：OpenAI function calling

    使用者查詢
        │
        ├─[Step 1] bio_history_check   ← 0 token，確認是否已存檔
        ├─[Step 2] bio_history_search  ← L1 語意快取命中
        ├─[Step 3] bio_memory_query    ← L1 完整報告
        ├─[Step 4] 分析工具（spatial_eda 等）← 實際執行分析
        └─[Step 5] bio_execute_code    ← 非標準分析，動態程式碼

公開函數：
    handle_message(user_msg, history=[]) → AgentResponse
    run_cli()                            → 互動式 CLI
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


# ── 系統 Prompt ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是 Hermes Bio-Memory，一個專為實驗室生物資訊分析設計的 AI Agent。

## 工具使用策略（依序執行，節省運算資源）

1. **bio_history_check**（優先）：每次分析前先確認是否已有完成存檔，避免重複運算。
2. **bio_history_search**：語意搜尋 L1 快取，確認是否有相似分析結果。
3. **bio_memory_query**：從 L1 快取取回完整報告。
3A. **bio_check_l2_sufficiency**：執行 bio_run_spatial_eda 前必須先確認 l2_ready=true；若 false，回傳轉換命令，不得繼續執行分析。
3B. **bio_run_spatial_eda**：L2 Parquet 讀取，生成空間轉錄體 EDA 報告（含摘要寫入 L1 快取）。需 l2_ready=true。
3C. **bio_run_bulk_eda**：Bulk RNA-seq EDA（QC + top genes + PCA），需先執行 pipeline 腳本產生計數矩陣。
4. **bio_execute_code**：非標準分析，動態生成並沙盒執行 Python 程式碼。

## 回答原則（非常重要）

- **每次工具呼叫完成後，必須用繁體中文輸出總結給使用者**，不可沉默結束
- 若工具回傳數字/列表結果，直接在回答中列出，不要只說「已完成」
- 分析結果簡潔摘要，不複製整份報告
- 明確指出結果路徑（result_path）供使用者自行查閱完整報告
- 若需新分析，先說明預計步驟再執行
- **禁止回傳空白回覆**：即使工具已執行，也必須用文字說明結果

## 資料說明

- L3 Bronze：原始數據（唯讀），路徑記錄於 sample_registry
- L2 Silver：DuckDB + Parquet 特徵存儲（silver/ 目錄）
- L1 Gold：語意快取（gold/hermes_cache.duckdb，TTL 7 天）

## 注意事項

- L3 原始數據絕不修改
- 大型 .h5ad 必須用 backed mode 讀取
- 分析歷史永久保存，請善用 bio_history_lookup 查詢
"""


# ── BIO_TOOLS 定義 ────────────────────────────────────────────────────────────

BIO_TOOLS = [
    {
        "name": "bio_history_check",
        "description": (
            "確認某樣本的某分析類型是否已有完成存檔（0 token，純 SQL）。"
            "每次執行分析前必須先呼叫此工具，避免重複運算。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "樣本 ID，例如 crc_official_v4"},
                "analysis_type": {"type": "string", "description": "分析類型，例如 spatial_eda"},
            },
            "required": ["sample_id", "analysis_type"],
        },
    },
    {
        "name": "bio_history_lookup",
        "description": "查詢樣本分析歷史記錄（0 token，純 SQL）。回傳分析類型、狀態、完成時間、摘要。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "樣本 ID（可選，省略則查全部）"},
                "analysis_type": {"type": "string", "description": "分析類型篩選（可選）"},
                "limit": {"type": "integer", "description": "最多回傳筆數（預設 20）", "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "bio_history_timeline",
        "description": "回傳最近 N 天的分析時間軸（0 token，純 SQL）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "n_days": {"type": "integer", "description": "往回查幾天（預設 7）", "default": 7},
            },
            "required": [],
        },
    },
    {
        "name": "bio_history_search",
        "description": (
            "以自然語言語意搜尋 L1 快取（HNSW cosine）。"
            "只回傳 50 字 summary，節省 token。需要 embedding server 在線（port 8081）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然語言查詢"},
                "n": {"type": "integer", "description": "回傳筆數上限（預設 5）", "default": 5},
                "threshold": {"type": "number", "description": "相似度門檻（預設 0.5）", "default": 0.5},
                "sample_id": {"type": "string", "description": "限定樣本 ID（可選）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "bio_memory_query",
        "description": (
            "從 L1 語意快取取回完整報告（HNSW cosine ≥ threshold 命中）。"
            "cache miss 時回傳空，需呼叫 bio_run_spatial_eda 生成新報告。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然語言查詢"},
                "sample_id": {"type": "string", "description": "限定樣本 ID（可選）"},
                "threshold": {"type": "number", "description": "相似度門檻（預設 0.88）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "bio_run_spatial_eda",
        "description": (
            "對指定樣本執行空間轉錄體 EDA（QC 統計 + top genes + 報告生成）。"
            "完成後自動寫入 analysis_history + L1 快取。"
            "需要 L2 Parquet 已轉換（l2_ready = true）。耗時約 10–30 秒。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "樣本 ID，例如 crc_official_v4"},
                "requested_by": {"type": "string", "description": "請求者（預設 agent）", "default": "agent"},
            },
            "required": ["sample_id"],
        },
    },
    {
        "name": "bio_register_sample",
        "description": "登記新樣本至 sample_registry。每個樣本只需登記一次。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "唯一樣本 ID（全小寫底線）"},
                "data_type": {"type": "string", "description": "資料類型：visium_hd | visium | scrna | bulk_rnaseq | ..."},
                "l3_path": {"type": "string", "description": "L3 原始數據絕對路徑（唯讀）"},
                "project": {"type": "string", "description": "專案代號（可選）"},
                "platform": {"type": "string", "description": "平台（可選）"},
                "species": {"type": "string", "description": "物種（預設 human）", "default": "human"},
                "tissue": {"type": "string", "description": "組織類型（可選）"},
                "notes": {"type": "string", "description": "備註（可選）"},
            },
            "required": ["sample_id", "data_type", "l3_path"],
        },
    },
    {
        "name": "bio_run_bulk_eda",
        "description": (
            "對 Bulk RNA-seq 樣本集執行 EDA（QC 統計 + top genes + 樣本相關 + PCA）。"
            "完成後自動寫入 analysis_history。"
            "需要先執行 scripts/bulk_rna/ pipeline 產生 gene_counts.tsv。耗時約 10–60 秒。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "樣本集 ID，例如 Kallisto_v1"},
                "requested_by": {"type": "string", "description": "請求者（預設 agent）", "default": "agent"},
            },
            "required": ["sample_id"],
        },
    },
    {
        "name": "bio_execute_code",
        "description": (
            "沙盒執行動態生成的 Python 程式碼（用於非標準分析）。"
            "只允許白名單 import（duckdb, pandas, numpy, scipy, anndata, scanpy 等）。"
            "禁止 os.system, subprocess, open(), eval, exec 等危險操作。"
            "timeout=60 秒。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要執行的 Python 程式碼"},
                "description": {"type": "string", "description": "此程式碼的分析目的（用於記錄）"},
                "timeout": {"type": "integer", "description": "執行超時秒數（預設 60）", "default": 60},
            },
            "required": ["code", "description"],
        },
    },
    {
        "name": "bio_check_l2_sufficiency",
        "description": (
            "確認樣本的 L2 Parquet 是否已就緒（l2_ready = true）。"
            "在執行 bio_run_spatial_eda 之前必須先呼叫，確認 L2 準備好才能繼續。"
            "若 l2_ready=false，回傳需要執行的轉換命令。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "樣本 ID，例如 crc_official_v4"},
            },
            "required": ["sample_id"],
        },
    },
]


# ── 工具執行 ─────────────────────────────────────────────────────────────────


def _exec_bio_history_check(args: dict) -> str:
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
            ORDER  BY completed_at DESC LIMIT 1
            """,
            [sample_id, analysis_type],
        ).fetchone()
    if row:
        analysis_id, completed_at, result_path, summary = row
        return (
            f"exists: true\nanalysis_id: {analysis_id}\n"
            f"completed_at: {str(completed_at)[:16]}\n"
            f"result_path: {result_path or '（未記錄）'}\n"
            f"summary: {(summary or '')[:80]}"
        )
    return f"exists: false\n{sample_id!r} × {analysis_type!r} 尚無完成存檔。"


def _exec_bio_history_lookup(args: dict) -> str:
    from analysis.history_query import recent_analyses, find_by_type
    sample_id = args.get("sample_id")
    analysis_type = args.get("analysis_type")
    limit = int(args.get("limit", 20))
    if analysis_type:
        df = find_by_type(analysis_type, sample_id=sample_id, limit=limit)
    else:
        df = recent_analyses(n=limit, sample_id=sample_id)
    if df.empty:
        return f"無分析記錄（sample_id={sample_id!r}）"
    rows = df[["sample_id", "analysis_type", "status", "completed_at", "summary"]].to_dict("records")
    lines = [f"分析歷史（共 {len(rows)} 筆）"]
    for r in rows:
        lines.append(
            f"• {r['sample_id']} / {r['analysis_type']} / {r['status']} "
            f"/ {str(r.get('completed_at', ''))[:16]} / {(r.get('summary') or '')[:40]}"
        )
    return "\n".join(lines)


def _exec_bio_history_timeline(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH
    n_days = int(args.get("n_days", 7))
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            """
            SELECT sample_id, analysis_type, status,
                   strftime(completed_at,'%Y-%m-%d %H:%M') AS completed_at, summary
            FROM   analysis_history
            WHERE  completed_at >= now() - (? * INTERVAL '1 day')
            ORDER  BY completed_at DESC LIMIT 30
            """,
            [n_days],
        ).fetchall()
    if not rows:
        return f"最近 {n_days} 天無分析記錄。"
    lines = [f"最近 {n_days} 天時間軸（{len(rows)} 筆）"]
    for r in rows:
        lines.append(f"• {r[3]} {r[0]} / {r[1]} / {r[2]} — {(r[4] or '')[:40]}")
    return "\n".join(lines)


def _exec_bio_history_search(args: dict) -> str:
    from analysis.l1_cache import semantic_search
    results = semantic_search(
        args["query"],
        n=int(args.get("n", 5)),
        threshold=float(args.get("threshold", 0.5)),
        sample_id=args.get("sample_id"),
    )
    if not results:
        return f"語意搜尋 cache miss（query={args['query']!r}）"
    lines = [f"語意搜尋命中 {len(results)} 筆"]
    for r in results:
        lines.append(f"  [{r['score']:.3f}] {r['sample_id']} — {r['summary']}")
    return "\n".join(lines)


def _exec_bio_memory_query(args: dict) -> str:
    from analysis.l1_cache import semantic_search
    from config.settings import L1_COSINE_THRESHOLD
    results = semantic_search(
        args["query"],
        n=1,
        threshold=float(args.get("threshold", L1_COSINE_THRESHOLD)),
        sample_id=args.get("sample_id"),
    )
    if not results:
        return f"L1 cache miss（threshold={args.get('threshold', L1_COSINE_THRESHOLD)}）。建議執行 bio_run_spatial_eda。"
    r = results[0]
    report = r["report_text"]
    if len(report) > 2000:
        report = report[:2000] + "\n…（報告過長，已截斷，完整內容見 result_path）"
    return (
        f"L1 cache hit（score={r['score']:.4f}）\n"
        f"summary: {r['summary']}\ncreated_at: {str(r['created_at'])[:16]}\n\n"
        f"--- 完整報告 ---\n{report}"
    )


def _exec_bio_check_l2_sufficiency(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH
    sample_id = args["sample_id"]
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT l2_ready, l3_path, data_type FROM sample_registry WHERE sample_id=?",
            [sample_id],
        ).fetchone()
    if row is None:
        return f"樣本 {sample_id!r} 不存在於 sample_registry，請先執行 bio_register_sample。"
    l2_ready, l3_path, data_type = row
    if l2_ready:
        return f"l2_ready=true。樣本 {sample_id!r} 的 L2 Parquet 已就緒，可直接執行分析。"
    cmd = (
        f"~/.venvs/hermes-bio-memory/bin/python scripts/02_spatial_to_parquet.py --sample-id {sample_id}"
        if data_type in ("visium_hd", "visium")
        else f"# data_type={data_type!r}，請手動執行對應的 L2 轉換腳本。"
    )
    return (
        f"l2_ready=false。樣本 {sample_id!r} 尚未完成 L2 轉換。\n"
        f"l3_path: {l3_path}\n"
        f"執行以下命令完成轉換後再重試：\n{cmd}"
    )


def _exec_bio_run_spatial_eda(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.report_generator import run_full_eda_report
    sample_id = args["sample_id"]
    requested_by = args.get("requested_by", "agent")

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT l2_ready FROM sample_registry WHERE sample_id=?", [sample_id]
        ).fetchone()
    if row is None:
        return f"樣本 {sample_id!r} 不存在於 sample_registry，請先執行 bio_register_sample。"
    if not row[0]:
        return (
            f"樣本 {sample_id!r} 的 L2 Parquet 尚未就緒（l2_ready=false）。\n"
            f"請先呼叫 bio_check_l2_sufficiency 確認並執行轉換命令。"
        )

    try:
        result = run_full_eda_report(sample_id, requested_by=requested_by)
        return (
            f"EDA 完成。\n"
            f"analysis_id: {result.get('analysis_id', '(無)')}\n"
            f"summary: {result.get('summary', '')}\n"
            f"report_path: {result.get('report_path', '(無)')}"
        )
    except Exception as e:
        return f"EDA 執行失敗：{e}"


def _exec_bio_register_sample(args: dict) -> str:
    import duckdb
    from config.db_utils import safe_write
    from config.settings import DUCKDB_PATH
    from datetime import datetime, timezone
    import re
    sample_id = args["sample_id"]
    if not re.match(r'^[a-z0-9_-]+$', sample_id):
        return f"樣本 ID {sample_id!r} 格式錯誤：只允許小寫英數字、底線和連字號。"
    with duckdb.connect(str(DUCKDB_PATH)) as con:
        if con.execute("SELECT 1 FROM sample_registry WHERE sample_id=?", [sample_id]).fetchone():
            return f"樣本 {sample_id!r} 已存在，跳過。"
        safe_write(
            con,
            """
            INSERT INTO sample_registry
                (sample_id,project,data_type,platform,species,tissue,
                 l3_path,l2_ready,analysis_done,added_by,notes,last_updated)
            VALUES (?,?,?,?,?,?,?,false,false,?,?,?)
            """,
            [
                sample_id, args.get("project",""), args["data_type"],
                args.get("platform",""), args.get("species","human"),
                args.get("tissue",""), args["l3_path"],
                "agent", args.get("notes",""), datetime.now(timezone.utc),
            ],
        )
    return f"樣本 {sample_id!r} 已登記。data_type={args['data_type']!r}"


def _exec_bio_run_bulk_eda(args: dict) -> str:
    from analysis.bulk_eda import generate_bulk_report
    sample_id    = args["sample_id"]
    requested_by = args.get("requested_by", "agent")
    try:
        analysis_id, report_path = generate_bulk_report(sample_id, requested_by=requested_by)
        return (
            f"Bulk EDA 完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}"
        )
    except Exception as e:
        return f"Bulk EDA 執行失敗：{e}"


def _exec_bio_execute_code(args: dict) -> str:
    from server.code_executor import sandbox_exec, SecurityError
    import tempfile, os, base64
    from pathlib import Path as _Path

    code = args["code"]
    timeout = int(args.get("timeout", 60))

    # Inject figure-save hook: any plt.show() → save to temp file
    fig_dir = tempfile.mkdtemp(prefix="hermes_fig_")
    preamble = f"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as _plt_orig
_hermes_fig_dir = {fig_dir!r}
_hermes_fig_idx = [0]
_orig_show = _plt_orig.show
def _hermes_show(*a, **kw):
    idx = _hermes_fig_idx[0]
    _plt_orig.savefig(f"{{_hermes_fig_dir}}/fig_{{idx:02d}}.png", dpi=120, bbox_inches="tight")
    _hermes_fig_idx[0] += 1
    _plt_orig.close("all")
_plt_orig.show = _hermes_show
"""
    augmented_code = preamble + "\n" + code

    try:
        result = sandbox_exec(augmented_code, timeout=timeout)
    except SecurityError as e:
        return f"[SecurityError] 程式碼違反安全規則：{e}"

    # Collect any saved figures as base64
    fig_paths = sorted(_Path(fig_dir).glob("fig_*.png"))
    fig_md = ""
    for fp in fig_paths:
        b64 = base64.b64encode(fp.read_bytes()).decode()
        fig_md += f"\n![figure](data:image/png;base64,{b64})\n"
        try:
            fp.unlink()
        except Exception:
            pass
    try:
        os.rmdir(fig_dir)
    except Exception:
        pass

    if result.success:
        out = result.output[:2000] if len(result.output) > 2000 else result.output
        return f"執行成功（{result.duration_sec}s）\n{out}{fig_md}"
    return f"執行失敗（{result.duration_sec}s）\n{result.traceback[:1000]}"


_TOOL_HANDLERS = {
    "bio_history_check": _exec_bio_history_check,
    "bio_history_lookup": _exec_bio_history_lookup,
    "bio_history_timeline": _exec_bio_history_timeline,
    "bio_history_search": _exec_bio_history_search,
    "bio_memory_query": _exec_bio_memory_query,
    "bio_check_l2_sufficiency": _exec_bio_check_l2_sufficiency,
    "bio_run_spatial_eda": _exec_bio_run_spatial_eda,
    "bio_run_bulk_eda":    _exec_bio_run_bulk_eda,
    "bio_register_sample": _exec_bio_register_sample,
    "bio_execute_code": _exec_bio_execute_code,
}


def execute_tool(name: str, tool_input: dict) -> str:
    """執行工具並回傳字串結果（含錯誤訊息）。"""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return f"[Error] 未知工具：{name!r}"
    try:
        return handler(tool_input)
    except Exception as e:
        logger.exception("Tool %r failed", name)
        return f"[Error] {name} 執行失敗：{e}"


# ── Agent Response ────────────────────────────────────────────────────────────


@dataclass
class AgentResponse:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    messages: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ── BIO_TOOLS → OpenAI function calling 格式 ─────────────────────────────────

def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """將 Anthropic tool schema 轉為 OpenAI function calling 格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


_OPENAI_TOOLS = _to_openai_tools(BIO_TOOLS)


# ── 推理後端 ─────────────────────────────────────────────────────────────────

LLAMA_BASE_URL = "http://localhost:8080/v1"
LLAMA_MODEL    = "gemma-4"

from openai import OpenAI as _OpenAI
_local_client: Optional[_OpenAI] = None

def _get_local_client() -> _OpenAI:
    global _local_client
    if _local_client is None:
        _local_client = _OpenAI(base_url=LLAMA_BASE_URL, api_key="not-needed")
    return _local_client

_HISTORY_ROLES = {"user", "assistant", "tool", "system"}


def _make_claude_call(messages: list[dict], max_tokens: int) -> tuple[str, list, int, int]:
    """呼叫 Claude API，回傳 (stop_reason, content_blocks, input_tokens, output_tokens)。"""
    import anthropic
    from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Claude API 的 system 從 messages 中分離
    system_msg = next((m["content"] for m in messages if m["role"] == "system"), SYSTEM_PROMPT)
    non_system = [m for m in messages if m["role"] != "system"]

    # 將 openai image_url content 轉為 Anthropic base64 image block
    def _convert_content(content):
        if not isinstance(content, list):
            return content
        out = []
        for block in content:
            if block.get("type") == "image_url":
                url = block["image_url"]["url"]
                if url.startswith("data:"):
                    media, b64 = url.split(",", 1)
                    media_type = media.split(";")[0].replace("data:", "")
                    out.append({"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": b64,
                    }})
                else:
                    out.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                out.append(block)
        return out

    converted = [
        {**m, "content": _convert_content(m["content"])} for m in non_system
    ]

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system_msg,
        tools=BIO_TOOLS,
        messages=converted,
    )
    return resp.stop_reason, resp.content, resp.usage.input_tokens, resp.usage.output_tokens


def _make_local_call(messages: list[dict], model: str, max_tokens: int):
    """呼叫本機 llama.cpp，回傳 chat completion response。"""
    return _get_local_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        tools=_OPENAI_TOOLS,
        tool_choice="auto",
        messages=messages,
    )


# ── 核心 Agent Loop ───────────────────────────────────────────────────────────


def handle_message(
    user_msg: str,
    history: Optional[list[dict]] = None,
    *,
    backend: str = "",
    model: str = "",
    max_tokens: int = 8192,
    max_tool_rounds: int = 15,
    image_base64: str = "",
) -> AgentResponse:
    """
    處理一則使用者訊息，支援本機 llama.cpp 或 Claude API 兩種推理後端。

    Args:
        user_msg:        使用者自然語言訊息
        history:         對話歷史（AgentResponse.messages 格式，含 tool 輪次）
        backend:         "local" | "claude"（空字串則讀 INFERENCE_BACKEND env）
        model:           模型名稱（空字串則依 backend 自動選擇）
        max_tokens:      最大回覆 token 數
        max_tool_rounds: 最多幾輪工具呼叫（防無限迴圈）

    Returns:
        AgentResponse(text, tool_calls, input_tokens, output_tokens, messages)
    """
    from config.settings import INFERENCE_BACKEND, CLAUDE_MODEL
    resolved_backend = backend or INFERENCE_BACKEND
    resolved_model   = model or (CLAUDE_MODEL if resolved_backend == "claude" else LLAMA_MODEL)

    # 組裝 messages：system + history（完整結構，含 tool 輪次）+ 新訊息
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in (history or []):
        if m.get("role") in _HISTORY_ROLES and m.get("role") != "system":
            messages.append(m)

    if image_base64:
        # 確保帶 data URI prefix（llama.cpp openai-compatible 格式）
        if not image_base64.startswith("data:"):
            image_base64 = "data:image/png;base64," + image_base64
        user_content: list[dict] = [
            {"type": "text", "text": user_msg or "請描述並分析這張圖片。"},
            {"type": "image_url", "image_url": {"url": image_base64}},
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": user_msg})

    all_tool_calls: list[dict] = []
    total_input = 0
    total_output = 0

    for _round in range(max_tool_rounds):
        if resolved_backend == "claude":
            stop_reason, content_blocks, in_tok, out_tok = _make_claude_call(messages, max_tokens)
            total_input  += in_tok
            total_output += out_tok

            if stop_reason != "tool_use":
                text = next((b.text for b in content_blocks if hasattr(b, "text")), "（無文字回覆）")
                messages.append({"role": "assistant", "content": text})
                return AgentResponse(text=text, tool_calls=all_tool_calls,
                                     input_tokens=total_input, output_tokens=total_output,
                                     messages=messages)

            tool_results = []
            for block in content_blocks:
                if block.type != "tool_use":
                    continue
                tool_result = execute_tool(block.name, block.input)
                logger.info("Tool %r called: %s…", block.name, str(tool_result)[:60])
                all_tool_calls.append({"name": block.name, "input": block.input, "result": tool_result})
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": tool_result})
            messages.append({"role": "assistant", "content": content_blocks})
            messages.append({"role": "user", "content": tool_results})
            continue

        # ── local backend (llama.cpp OpenAI-compatible) ───────────────────────
        response = _make_local_call(messages, resolved_model, max_tokens)
        usage = response.usage
        if usage:
            total_input  += usage.prompt_tokens or 0
            total_output += usage.completion_tokens or 0

        choice = response.choices[0]
        msg = choice.message

        if msg.tool_calls:
            # 明確建構 assistant 訊息，確保 tool_calls 不因 exclude_unset 被丟棄
            assistant_msg: dict = {"role": "assistant", "content": msg.content}
            assistant_msg["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            messages.append(assistant_msg)

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    logger.warning("Tool %r: malformed arguments JSON: %s", fn_name, exc)
                    tool_result = f"[Error] JSON decode failed for {fn_name}: {exc}"
                    all_tool_calls.append({"name": fn_name, "input": {}, "result": tool_result})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
                    continue

                tool_result = execute_tool(fn_name, fn_args)
                logger.info("Tool %r called: %s…", fn_name, str(tool_result)[:60])
                all_tool_calls.append({"name": fn_name, "input": fn_args, "result": tool_result})
                # 截斷過長的工具結果，避免撐爆 8192 context window
                tool_msg = tool_result if len(tool_result) <= 800 else tool_result[:800] + "\n…（已截斷，完整內容見 result_path）"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_msg})

            continue

        text = (msg.content or "").strip()

        # Gemma 4 有時工具呼叫結束後不輸出文字；若有工具結果則自動彙整
        if not text and all_tool_calls:
            last_result = all_tool_calls[-1]["result"]
            text = last_result if len(last_result) <= 2000 else last_result[:2000] + "\n…（已截斷）"

        if not text:
            text = "（無文字回覆）"

        messages.append({"role": "assistant", "content": text})
        return AgentResponse(
            text=text,
            tool_calls=all_tool_calls,
            input_tokens=total_input,
            output_tokens=total_output,
            messages=messages,
        )

    # 超過 max_tool_rounds — 補上 closing assistant 訊息避免下一輪 messages 序列不合法
    executed = ", ".join(c["name"] for c in all_tool_calls) or "（無）"
    exhaustion_text = (
        f"[警告] 分析步驟較多，已執行 {len(all_tool_calls)} 個工具仍未完成。\n"
        f"已呼叫：{executed}\n"
        "請嘗試拆分查詢，例如先問「樣本基本資訊」再問「前 20 高表達基因」。"
    )
    messages.append({"role": "assistant", "content": exhaustion_text})
    return AgentResponse(
        text=exhaustion_text,
        tool_calls=all_tool_calls,
        input_tokens=total_input,
        output_tokens=total_output,
        messages=messages,
    )


# ── CLI 介面 ─────────────────────────────────────────────────────────────────


def run_cli() -> None:
    """互動式 CLI（用於本機測試）。"""
    logging.basicConfig(level=logging.INFO)
    print("Hermes Bio-Memory Agent（輸入 'exit' 離開）")
    print("─" * 50)
    history: list[dict] = []
    while True:
        try:
            user_msg = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break
        if not user_msg:
            continue
        if user_msg.lower() in ("exit", "quit", "bye"):
            print("再見！")
            break

        result = handle_message(user_msg, history)
        print(f"\nHermes：{result.text}")
        print(f"  [tokens: in={result.input_tokens} out={result.output_tokens} | tools={len(result.tool_calls)}]")

        # 使用 handle_message 回傳的完整 messages（含 tool 輪次），確保 API 合規
        if result.text:
            history = result.messages[-12:]


if __name__ == "__main__":
    run_cli()
