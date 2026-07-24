"""
Phase 5 — BioAgent Agent Loop。

推理引擎：llama.cpp OpenAI-compatible API（port 8080，本機 Gemma 4 Vision）
工具呼叫格式：OpenAI function calling

    使用者查詢
        │
        ├─[Step 1] bio_history_check   ← 0 token，確認是否已存檔
        ├─[Step 2] bio_history_search  ← L1 語意快取命中
        ├─[Step 3] bio_memory_query    ← L1 完整報告
        ├─[Step 4] 分析工具（spatial_eda 等）← 實際執行分析
        ├─[Step 5] bio_find_tool       ← 寫碼前先搜既有可重用函數（0 token）
        └─[Step 6] bio_execute_code    ← 全 miss 才動態寫碼（非標準分析）

公開函數：
    handle_message(user_msg, history=[]) → AgentResponse
    run_cli()                            → 互動式 CLI

────────────────────────────────────────────────────────────────────────────
agent_*.py 家族的分檔規則（2026-07-24 架構審查候選 6：文件化現有切分，不重切）
────────────────────────────────────────────────────────────────────────────
Web UI 的工具實作（`_exec_bio_*` handler）依「執行性質」分散在四個檔案；
每個工具實際落在哪個 module+func，權威來源是 `server/tool_catalog.py`（可直接查），
本檔只是說明切分原則，避免再靠 grep 猜：

    agent.py         ← 本檔。Agent 主迴圈、LLM client、dispatch 組裝（_TOOL_HANDLERS
                       由 tool_catalog 自動生成 + 決策 8-B override）、execute_tool 安全閘。
                       不放具體分析 handler。
    agent_bulk.py    ← 「計算密集型」分析 handler：mcseg 分割、DEG、enrichment、heatmap、
                       clustering、celltypist、geneset score、空間鄰距、CRC metrics、
                       loupe 匯出、外部結果登記等。
    agent_history.py ← 「metadata／內省／沙盒」handler：歷史查詢、L1 記憶、樣本清單、
                       tool health、playbook、impact、find_tool、register_sample、
                       以及動態 code executor（bio_execute_code）。
    agent_spatial.py ← 「空間專屬」handler：L2 充足性檢查、空間 EDA。

新增工具時：依上述性質選檔放 `_exec_bio_<name>`，並在 tool_catalog.py 補 ToolSpec
（module+func 指向它，或 mcp_handler 指向 MCP 端 async handler）——兩者同步由
tests/test_tool_catalog_parity.py 守住。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


# ── 系統 Prompt ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是「智慧生資分析平台」AI Agent，專為實驗室生物資訊分析設計。

## 圖片顯示能力（重要）

本系統前端**完整支援圖片渲染**。分析工具（bio_run_spatial_eda、bio_run_bulk_eda、bio_execute_code）
執行後，報告檔案中已嵌入 inline base64 圖片（格式：`![alt](data:image/png;base64,...)`），
前端會自動解析並在對話視窗中直接顯示圖表。
**請勿告知使用者「系統不支援圖片顯示」——這是錯誤的。** 圖片會自動呈現，無需額外說明。

## 工具使用策略（依序執行，節省運算資源）

1. **bio_history_check**（優先）：每次分析前先確認是否已有完成存檔，避免重複運算。
2. **bio_history_search**：語意搜尋 L1 快取，確認是否有相似分析結果。
3. **bio_memory_query**：從 L1 快取取回完整報告。
3-PB. **bio_get_playbook（領域分析方法學，重要）**：執行任何領域分析（bulk / 空間 / mcseg）前，
   先以 bio_get_playbook(domain) 取得該領域的「技能說明書」——它定義標準步驟順序、每步該呼叫的
   既有函數、該產出的圖、以及品質關卡。**依說明書分步進行，確保每一步都產出對應圖、不可跳步**。
   省略 domain 可列出所有可用說明書。未來新分析領域也以新增 playbook 的方式擴充。
3A. **bio_check_l2_sufficiency**：執行 bio_run_spatial_eda 前必須先確認 l2_ready=true；若 false，回傳轉換命令，不得繼續執行分析。
3B. **bio_run_spatial_eda**：L2 Parquet 讀取，生成空間轉錄體 EDA 報告（含 QC 圖嵌入 + 摘要寫入 L1 快取）。需 l2_ready=true。
3C. **bio_run_bulk_eda**：Bulk RNA-seq EDA（QC + top genes + PCA 圖），需先執行 pipeline 腳本產生計數矩陣。
4. **bio_find_tool**（寫碼前必經）：要做非標準分析、準備用 bio_execute_code 前，
   先以 bio_find_tool 語意搜尋既有可重用函數。命中 → 在動態碼中 `import` 重用該函數，
   不要從零重寫（既有函數已測試、已去硬編碼）。0 LLM token 的本地搜尋。
5. **bio_execute_code**：僅當 bio_find_tool 全 miss（無夠相似的既有工具）時，才動態生成並沙盒執行 Python 程式碼（plt.show() 產生的圖會自動擷取並嵌入結果）。仍可 import 白名單的 analysis.* 函數作基礎。

## 快取命中行為（Cache Hit Protocol）

**觸發條件**：
- Step 1 命中：工具回傳以 `exists: true` 開頭
- Step 2 命中：工具回傳以 `語意搜尋命中` 開頭（非 `語意搜尋 cache miss`）

命中時**必須**依序執行以下步驟，不可直接跳到新分析，也**不需要**再呼叫 `bio_memory_query`：

1. **告知命中**：說明「已找到 {sample_id} 的分析記錄，完成於 {completed_at}」
2. **列出使用參數**：顯示回傳的 `parameters` 欄位內容，讓使用者確認當時的分析條件
3. **列出可用輸出**（條件）：
   - 若 `result_path` 為「（未記錄）」→ 說明「結果路徑未記錄，請參考上方摘要」，跳過此步驟
   - 否則 → 告知 `{result_path}` 下可能有 report.md / *.png / *.csv
4. **詢問使用者**：「此結果是否符合您的需求？或需要調整參數重新執行？」
5. **等待確認後再決定**：
   - 若使用者確認足夠 → 提供摘要，結束（**不需呼叫 bio_memory_query**）
   - 若使用者需要不同參數 → 繼續執行新分析（Step 3A 以後）

## 工具庫管理（bio_tool_health）

呼叫 `bio_tool_health` 的時機與流程：

1. 使用者詢問工具穩定性 → `action=report`（顯示熱區 + 進行中迭代 + VLM 視覺快照）
2. report 回傳未處理熱區時 → 已附上可直接呼叫的 stabilize 參數，立即跟進開啟迭代
3. 重構完成後 → `action=close_stabilize` 記錄結果（stabilized/ongoing/reverted）
4. 查看改善歷程 → `action=trend`（可選 tool_name；顯示跨迭代 CC delta 趨勢）
5. 使用者要求清理 → `action=prune`（只刪無分析引用的 deprecated，有引用的永遠保留）

**穩定化迭代原則**：
- 熱區工具（revision ≥ 3）+ 尚無迭代 → report 已附完整 stabilize 參數，應立即開啟
- 已有進行中迭代 → report 顯示 VLM 視覺快照（640x640 PNG），可直接參考上次診斷記憶
- 穩定工具（revision < 3）可積極 prune（保留 2 個版本）
- 不穩定工具保留更多歷史（保留 10 個版本），供追溯
- `revision_after` 在 close 時自動回填；`complexity_before/after` delta 是客觀改善指標
- `action=trend` 查看累積 CC 改善，評估整體工具健康走向

## 讀檔請求處理（絕對規則，禁止幻覺）

使用者問「某報告/檔案/分析裡寫了什麼」「打開 xxx.md」「裡面有沒有 X」「結果是多少」
等檔案內容問題時，**必須**依下列順序嘗試工具，全部 0 結果才能回「找不到」：

1. `bio_history_lookup(sample_id)` 或 `bio_history_check(sample_id, analysis_type)`
   — 取得該樣本最近一次分析的 `result_path`（這是真實 .md 路徑）
2. `bio_read_report(result_path=<上一步取得的 path>)`
   — **真正讀取 .md 原文**，回傳 head + tail（含真實數字、表格、結論）
3. `bio_artifact_summary(sample_id)`、`bio_memory_query(...)` — 補充 metadata

**絕對禁止**的行為：
- 看到 `bio_execute_code` 禁用 `open()` 就推論「無法讀取」並放棄
  — 那只是沙盒限制執行任意程式碼，**讀報告請走 `bio_read_report` 工具**
- 用「根據標準流程」「通常會包含」「應該有」「無法直接讀取，但…」等句式
  推測檔案內容。沒呼叫 `bio_read_report` 拿到 head/tail 之前，
  **任何描述檔案內容的句子都是幻覺，禁止輸出**
- 自編檔案內容後加「請以實際檔案為準」式免責聲明
- 引用具體數字（樣本數、基因數、p-value）卻沒附「來自 bio_read_report 第幾行」

**正確的「讀不到」回覆格式**（必須提供下一步，不可只說失敗）：
```
無法讀取 {檔名/artifact}。
已嘗試：
  - bio_history_lookup(sample_id=X)    → 0 hits / result_path 為空
  - bio_read_report(result_path=Y)     → ReportReadError: file not found
可能原因：
  (a) 分析未執行 → 可跑 bio_run_bulk_eda(sample_id=X)
  (b) sample_id 拼寫不同 → 你是指 ... 嗎？
  (c) 報告檔案已被搬移或刪除
```

## 回答原則（非常重要）

- **每次工具呼叫完成後，必須用繁體中文輸出總結給使用者**，不可沉默結束
- 若工具回傳數字/列表結果，直接在回答中列出，不要只說「已完成」
- 分析結果簡潔摘要，不複製整份報告
- 明確指出結果路徑（result_path）供使用者自行查閱完整報告
- 若需新分析，先說明預計步驟再執行
- **禁止回傳空白回覆**：即使工具已執行，也必須用文字說明結果
- **禁止憑檔名或工具名稱推測檔案內容** — 任何「報告包含 X」陳述都必須來自
  `bio_read_report` 工具回傳的 head/tail，或其他工具的具體欄位

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
#
# 2026-07-24 架構審查（候選 1+3）：description/schema 從 server/tool_catalog.py 生成，
# 不再手刻第二份（原本這裡只有 21/44 個工具，且同工具的 description 跟 MCP 端各自漂移）。
#
# 危險工具過濾在「模組載入時」讀一次 env（跟本檔其餘 env 設定如 LLAMA_BASE_URL 同風格），
# 不像 MCP 的 list_tools() 每次請求動態算——因為 BIO_TOOLS 是三個推理後端（Claude/本機
# llama.cpp/Gemini）共用的模組級常數，沒有天然的「每請求重算」掛勾點，而
# MCP_ENABLE_DANGEROUS_TOOLS 本質是部署期設定，不會在 process 存活期間變動。
# 真正的安全防護在 execute_tool() 的執行期 gate（每次呼叫都動態檢查，見下方），
# 就算 BIO_TOOLS 沒即時反映 env 變動，呼叫仍會被正確擋下。
from server.tool_catalog import TOOL_CATALOG as _TOOL_CATALOG_FOR_BIO_TOOLS
from server.bio_memory_server import _dangerous_tools_enabled as _dangerous_tools_enabled_at_import

BIO_TOOLS = [
    {"name": name, "description": spec.description, "input_schema": spec.json_schema}
    for name, spec in _TOOL_CATALOG_FOR_BIO_TOOLS.items()
    if not spec.dangerous or _dangerous_tools_enabled_at_import()
]


# ── 工具執行 ─────────────────────────────────────────────────────────────────


# 決策 8-B（2026-07-24 sp-brainstorming；2026-07-24 收斂複審）：以下 6 個工具 Web UI 早就
# 有自己獨立的實作（跟 MCP 端的內嵌邏輯已經分岔——例如 bio_history_check 這邊用
# store.get_history()、MCP 端是原生 SQL + format=json 模式），統一 catalog 時**不**調解
# 兩邊差異，維持 Web UI 現有實作不變，避免對兩端引入未知的行為變更。
#
# 分岔性質已逐一查證（見 tests/test_agent_tool_divergence.py 的 KNOWN_DIVERGENCES）：
#   - history_check/lookup/timeline/search：MCP 端是功能超集或格式不同，屬「可調解但有回歸
#     風險」，暫留現狀。
#   - register_sample：僅 added_by провенанс 標籤不同（"agent" vs "mcp_server"）——**刻意**
#     分岔，非 bug（見兩端 handler 內註解）。
#   - memory_query：Web UI 刻意把報告截到 2000 字（保護本機 LLM context）——**刻意**分岔。
# 原本第 7 個 bio_read_report 兩端邏輯**逐字相同**（純重複），已移除 Web UI override，改讓它
# 走 catalog 自動生成的 MCP bridge handler（零行為變更）。
from server.agent_history import (
    _exec_bio_history_check,
    _exec_bio_history_lookup,
    _exec_bio_history_timeline,
    _exec_bio_history_search,
    _exec_bio_memory_query,
    _exec_bio_register_sample,
)


def _make_sync_delegate_handler(module_path: str, func_name: str):
    """為一個 delegate 工具產生 Web UI sync handler：動態 import + 直接呼叫（同一份實作，
    MCP 端用 asyncio.to_thread 包一層，這裡直接同步呼叫，執行模型維持現有的 sync 風格）。"""
    import importlib

    def _handler(args: dict) -> str:
        fn = getattr(importlib.import_module(module_path), func_name)
        return fn(args)

    return _handler


def _make_mcp_bridge_handler(mcp_handler_name: str):
    """為一個「只有 MCP 內嵌邏輯、Web UI 從未實作過」的工具產生橋接 handler：
    透過 asyncio.run() 直接重用 bio_memory_server.py 既有的 async handler，不搬動/
    不複製任何業務邏輯（sp-brainstorming 決策 7-A）。

    MCP handler 可能回傳 `list[ImageContent]`（僅 bio_get_figure）而非純文字——Web UI
    需要文字，故轉成 inline base64 markdown（與 Web UI 既有的圖片顯示慣例一致）。
    """
    import asyncio

    def _handler(args: dict) -> str:
        from server import bio_memory_server as _bms

        mcp_handler = getattr(_bms, mcp_handler_name)
        result = asyncio.run(mcp_handler(args))
        if isinstance(result, list):
            parts = []
            for item in result:
                data = getattr(item, "data", None)
                mime = getattr(item, "mimeType", None)
                if data and mime:
                    parts.append(f"![figure](data:{mime};base64,{data})")
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return result

    return _handler


_TOOL_HANDLERS: dict = {}
for _name, _spec in _TOOL_CATALOG_FOR_BIO_TOOLS.items():
    if _spec.module is not None and _spec.func is not None:
        _TOOL_HANDLERS[_name] = _make_sync_delegate_handler(_spec.module, _spec.func)
    elif _spec.mcp_handler is not None:
        _TOOL_HANDLERS[_name] = _make_mcp_bridge_handler(_spec.mcp_handler)
del _name, _spec

# 決策 8-B 例外：覆寫成 Web UI 現有的獨立實作（見上方 import 與說明）。
# bio_read_report 不在此列——它兩端邏輯逐字相同，已改走 catalog 的 MCP bridge。
_TOOL_HANDLERS.update(
    {
        "bio_history_check": _exec_bio_history_check,
        "bio_history_lookup": _exec_bio_history_lookup,
        "bio_history_timeline": _exec_bio_history_timeline,
        "bio_history_search": _exec_bio_history_search,
        "bio_memory_query": _exec_bio_memory_query,
        "bio_register_sample": _exec_bio_register_sample,
    }
)

assert set(_TOOL_HANDLERS) == set(_TOOL_CATALOG_FOR_BIO_TOOLS), (
    f"_TOOL_HANDLERS/TOOL_CATALOG 工具集不一致：只在一邊的有 "
    f"{set(_TOOL_HANDLERS) ^ set(_TOOL_CATALOG_FOR_BIO_TOOLS)}"
)


def execute_tool(name: str, tool_input: dict) -> str:
    """執行工具並回傳字串結果（含錯誤訊息）。

    2026-07-24 架構審查（決策 2/4）：跟 MCP 端的 call_tool() 對等補上 dangerous gate、
    rate limit、metric 記錄——原本這裡完全沒有這些防護，`bio_execute_code` 等高權限工具
    無條件開放，是本次審查發現的安全落差。
    """
    import time as _time

    from server.bio_memory_server import (
        _dangerous_tools_enabled,
        _rate_limit_check,
        _record_metric,
    )

    spec = _TOOL_CATALOG_FOR_BIO_TOOLS.get(name)
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return f"[Error] 未知工具：{name!r}"

    if spec is not None and spec.dangerous and not _dangerous_tools_enabled():
        return (
            f"[Error] {name} 為高權限工具，目前未啟用。"
            "設定 env MCP_ENABLE_DANGEROUS_TOOLS=true 並重啟才可呼叫。"
        )

    if spec is not None and spec.rate_limited and not _rate_limit_check(f"tool:{name}"):
        return f"[Error] {name} 已達速率上限，請稍後再試。"

    t0 = _time.monotonic()
    try:
        result = handler(tool_input)
    except Exception as e:
        logger.exception("Tool %r failed", name)
        _record_metric(
            name,
            int((_time.monotonic() - t0) * 1000),
            "system_error",
            error_class=e.__class__.__name__,
            requested_by="web_ui",
        )
        return f"[Error] {name} 執行失敗：{e}"
    _record_metric(name, int((_time.monotonic() - t0) * 1000), "ok", requested_by="web_ui")
    return result


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
# Was hardcoded to localhost:8080 (start_bioagent.sh's own dedicated Gemma4 Vision
# 26B), unlike every other backend setting in this file which reads from env. Made
# configurable so deployments can point at an already-running OpenAI-compatible
# Gemma endpoint instead (e.g. lcdda's shared llama-server) rather than standing up
# a second local model.
import os as _os

LLAMA_BASE_URL = _os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1")
LLAMA_MODEL = _os.getenv("LLAMA_MODEL", "gemma-4")
# Gemma "thinking" template: without disabling it, replies come back slow / empty
# (same failure mode documented in lcdda's mcp_second_brain/llm_cli.py). Off by
# default only when explicitly opted out.
LLAMA_NO_THINK = _os.getenv("LLAMA_NO_THINK", "1") not in ("0", "false", "False", "")

_local_client = None
_claude_client = None


def _get_local_client():
    global _local_client
    if _local_client is None:
        from openai import OpenAI as _OpenAI

        _local_client = _OpenAI(base_url=LLAMA_BASE_URL, api_key="not-needed")
    return _local_client


def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        import anthropic
        from config.settings import ANTHROPIC_API_KEY, validate_inference_backend

        validate_inference_backend("claude")  # 缺 key 立即 raise，不讓 SDK 收到空 key
        _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _claude_client


_HISTORY_ROLES = {"user", "assistant", "tool", "system"}


def _make_claude_call(messages: list[dict], max_tokens: int) -> tuple[str, list, int, int]:
    """呼叫 Claude API，回傳 (stop_reason, content_blocks, input_tokens, output_tokens)。"""
    from config.settings import CLAUDE_MODEL

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
                    out.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        }
                    )
                else:
                    out.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                out.append(block)
        return out

    system_msg = next((m["content"] for m in messages if m["role"] == "system"), SYSTEM_PROMPT)
    non_system = [m for m in messages if m["role"] != "system"]
    converted = [{**m, "content": _convert_content(m["content"])} for m in non_system]

    # Prompt Cache：system prompt + tools 標記為可快取，降低重複請求的 TTFT
    cached_system = [{"type": "text", "text": system_msg, "cache_control": {"type": "ephemeral"}}]
    cached_tools = [
        {**t, "cache_control": {"type": "ephemeral"}} if i == len(BIO_TOOLS) - 1 else t
        for i, t in enumerate(BIO_TOOLS)
    ]

    resp = _get_claude_client().beta.messages.create(  # type: ignore[call-overload]
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=cached_system,  # type: ignore[arg-type]
        tools=cached_tools,  # type: ignore[arg-type]
        messages=converted,  # type: ignore[arg-type]
        betas=["prompt-caching-2024-07-31"],
    )
    return resp.stop_reason, resp.content, resp.usage.input_tokens, resp.usage.output_tokens  # type: ignore[union-attr]


def _make_local_call(messages: list[dict], model: str, max_tokens: int):
    """呼叫本機 llama.cpp，回傳 chat completion response。"""
    extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if LLAMA_NO_THINK else None
    return _get_local_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        tools=_OPENAI_TOOLS,
        tool_choice="auto",
        messages=messages,
        extra_body=extra_body,
    )


_google_client = None


def _get_google_client():
    global _google_client
    if _google_client is None:
        from config.settings import GOOGLE_API_KEY, validate_inference_backend

        validate_inference_backend("google")  # 缺 key 立即 raise，不讓 SDK 收到空 key
        from google import genai

        _google_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _google_client


def _strip_schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    """遞迴移除 types.Schema 不接受的 'default' 欄位。"""
    schema = {k: v for k, v in schema.items() if k != "default"}
    if "properties" in schema:
        schema["properties"] = {
            k: _strip_schema_defaults(v) for k, v in schema["properties"].items()
        }
    return schema


def _make_google_call(
    messages: list[dict],
    model: str,
    max_tokens: int,
    native_history: list | None = None,
) -> tuple:
    """呼叫 Google Gemini API，回傳 (finish_reason, response, input_tokens, output_tokens, history_contents)。

    native_history: 若提供，直接使用（含 FunctionCall/FunctionResponse parts）；
                    否則從 OpenAI-format messages 重建。
    """
    from google.genai import types

    client = _get_google_client()

    # BIO_TOOLS（Anthropic schema）→ Gemini FunctionDeclaration
    gemini_tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=types.Schema(**_strip_schema_defaults(dict(t["input_schema"]))),  # type: ignore[arg-type]
                )
                for t in BIO_TOOLS
            ]
        )
    ]

    system_instruction = next(
        (m["content"] for m in messages if m["role"] == "system"), SYSTEM_PROMPT
    )

    if native_history is not None:
        history_contents = native_history
    else:
        history_contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "model" if m["role"] == "assistant" else "user"
            content = m["content"]
            if isinstance(content, str):
                history_contents.append(types.Content(role=role, parts=[types.Part(text=content)]))
            elif isinstance(content, list):
                parts = []
                for b in content:
                    if b.get("type") == "text":
                        parts.append(types.Part(text=b["text"]))
                    elif b.get("type") == "image_url":
                        # data URI → inline_data
                        url = b.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            header, data = url.split(",", 1)
                            mime = header.split(";")[0].replace("data:", "")
                            parts.append(
                                types.Part(inline_data=types.Blob(mime_type=mime, data=data))
                            )
                if parts:
                    history_contents.append(types.Content(role=role, parts=parts))

    resp = client.models.generate_content(
        model=model,
        contents=history_contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=gemini_tools,
            max_output_tokens=max_tokens,
        ),
    )
    in_tok = getattr(resp.usage_metadata, "prompt_token_count", 0) or 0
    out_tok = getattr(resp.usage_metadata, "candidates_token_count", 0) or 0
    finish = resp.candidates[0].finish_reason.name if resp.candidates else "STOP"
    return finish, resp, in_tok, out_tok, history_contents


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
        backend:         "local" | "claude" | "google"（空字串則讀 INFERENCE_BACKEND env）
        model:           模型名稱（空字串則依 backend 自動選擇）
        max_tokens:      最大回覆 token 數
        max_tool_rounds: 最多幾輪工具呼叫（防無限迴圈）

    Returns:
        AgentResponse(text, tool_calls, input_tokens, output_tokens, messages)
    """
    from config.settings import INFERENCE_BACKEND, CLAUDE_MODEL, GOOGLE_MODEL

    resolved_backend = backend or INFERENCE_BACKEND
    if model:
        resolved_model = model
    elif resolved_backend == "claude":
        resolved_model = CLAUDE_MODEL
    elif resolved_backend == "google":
        resolved_model = GOOGLE_MODEL
    else:
        resolved_model = LLAMA_MODEL

    # 組裝 messages：system + history（完整結構，含 tool 輪次）+ 新訊息
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history or []:
        if m.get("role") in _HISTORY_ROLES and m.get("role") != "system":
            messages.append(m)

    # ── Fast-Path 攔截 ──────────────────────────────────────────────────────
    # 簡單唯讀查詢（最近 N 筆/時間軸/樣本列表）直接呼叫工具，跳過 LLM。
    # 多模態訊息（image_base64）一律不走 fast-path，留給 VLM。
    if not image_base64:
        from server.fast_path import try_fast_path, render_header

        hit = try_fast_path(user_msg)
        if hit is not None:
            try:
                tool_result = execute_tool(hit.tool_name, hit.args)
            except Exception as exc:  # noqa: BLE001 — 任何錯誤都 fallback 給 LLM
                logger.warning(
                    "fast_path intent=%s tool=%s failed, fallback to LLM: %s",
                    hit.intent,
                    hit.tool_name,
                    exc,
                )
            else:
                text = render_header(hit) + tool_result
                messages.append({"role": "user", "content": user_msg})
                messages.append({"role": "assistant", "content": text})
                logger.info(
                    "fast_path hit intent=%s tool=%s (bypassed LLM)", hit.intent, hit.tool_name
                )
                return AgentResponse(
                    text=text,
                    tool_calls=[
                        {
                            "name": hit.tool_name,
                            "input": hit.args,
                            "result": tool_result,
                            "fast_path": True,
                        }
                    ],
                    input_tokens=0,
                    output_tokens=0,
                    messages=messages,
                )

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

    # Pre-build Google native history from messages once before the loop.
    # This ensures prior tool-call turns in `history` are not silently dropped
    # by the round-0 conversion path inside _make_google_call.
    _google_native: list = []
    if resolved_backend == "google":
        _google_native = _make_google_call(
            messages, resolved_model, max_tokens, native_history=None
        )[4]  # index 4 = history_contents built from messages

    for _round in range(max_tool_rounds):
        if resolved_backend == "claude":
            stop_reason, content_blocks, in_tok, out_tok = _make_claude_call(messages, max_tokens)
            total_input += in_tok
            total_output += out_tok

            if stop_reason != "tool_use":
                text = next(
                    (b.text for b in content_blocks if hasattr(b, "text")), "（無文字回覆）"
                )
                messages.append({"role": "assistant", "content": text})
                return AgentResponse(
                    text=text,
                    tool_calls=all_tool_calls,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    messages=messages,
                )

            tool_results = []
            for block in content_blocks:
                if block.type != "tool_use":
                    continue
                tool_result = execute_tool(block.name, block.input)
                logger.info("Tool %r called: %s…", block.name, str(tool_result)[:60])
                all_tool_calls.append(
                    {"name": block.name, "input": block.input, "result": tool_result}
                )
                truncated = (
                    tool_result
                    if len(tool_result) <= 800
                    else tool_result[:800] + "\n…（已截斷，完整內容見 result_path）"
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": truncated}
                )
            serializable_blocks = [
                b.model_dump() if hasattr(b, "model_dump") else b for b in content_blocks
            ]
            messages.append({"role": "assistant", "content": serializable_blocks})
            messages.append({"role": "user", "content": tool_results})
            continue

        # ── google backend (Gemini API) ───────────────────────────────────────
        if resolved_backend == "google":
            from google.genai import types as _gtypes

            # Always pass accumulated native history (pre-built before loop).
            finish, resp, in_tok, out_tok, _google_native = _make_google_call(
                messages,
                resolved_model,
                max_tokens,
                native_history=_google_native,
            )
            total_input += in_tok
            total_output += out_tok

            candidate = resp.candidates[0] if resp.candidates else None
            candidate_parts = candidate.content.parts if (candidate and candidate.content) else []
            fn_calls = [
                p.function_call
                for p in candidate_parts
                if hasattr(p, "function_call") and p.function_call
            ]

            if fn_calls:
                # Preserve the model turn with its FunctionCall parts in native history
                _google_native.append(_gtypes.Content(role="model", parts=candidate_parts))
                # Batch all tool results into a single user turn (Gemini requires alternating roles)
                response_parts = []
                for fc in fn_calls:
                    fn_args = dict(fc.args) if fc.args else {}
                    tool_result = execute_tool(fc.name, fn_args)
                    logger.info("Tool %r called: %s…", fc.name, str(tool_result)[:60])
                    all_tool_calls.append(
                        {"name": fc.name, "input": fn_args, "result": tool_result}
                    )
                    response_parts.append(
                        _gtypes.Part(
                            function_response=_gtypes.FunctionResponse(
                                name=fc.name,
                                response={"result": tool_result[:800]},
                            )
                        )
                    )
                _google_native.append(_gtypes.Content(role="user", parts=response_parts))
                continue

            # Handle blocked / truncated responses before accessing resp.text
            if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                text = "（回應被安全過濾器封鎖）"
            elif finish == "MAX_TOKENS":
                text = ((resp.text or "").strip() + "…（已截斷）") or "（已截斷）"
            else:
                text = (resp.text or "").strip() or "（無文字回覆）"

            messages.append({"role": "assistant", "content": text})
            return AgentResponse(
                text=text,
                tool_calls=all_tool_calls,
                input_tokens=total_input,
                output_tokens=total_output,
                messages=messages,
            )

        # ── local backend (llama.cpp OpenAI-compatible) ───────────────────────
        response = _make_local_call(messages, resolved_model, max_tokens)
        usage = response.usage
        if usage:
            total_input += usage.prompt_tokens or 0
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
                tool_msg = (
                    tool_result
                    if len(tool_result) <= 800
                    else tool_result[:800] + "\n…（已截斷，完整內容見 result_path）"
                )
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


def _startup_cleanup() -> None:
    """Agent 啟動時清理殭屍 running 狀態。

    hours=0 表示清理所有 running 記錄——server 重啟本身就代表之前的進程已終止，
    任何殘留的 running 狀態都是殭屍。
    """
    try:
        from config.db_utils import cleanup_stale_runs, open_db

        with open_db() as con:
            cleaned = cleanup_stale_runs(con, hours=0)
            if cleaned:
                logger.info("啟動清理：%d 筆殭屍 running → stale", cleaned)
    except Exception as e:
        logger.warning("startup cleanup 失敗（不影響啟動）：%s", e)


def run_cli() -> None:
    """互動式 CLI（用於本機測試）。"""
    logging.basicConfig(level=logging.INFO)
    _startup_cleanup()
    print("BioAgent Agent（輸入 'exit' 離開）")
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
        print(f"\nBioAgent：{result.text}")
        print(
            f"  [tokens: in={result.input_tokens} out={result.output_tokens} | tools={len(result.tool_calls)}]"
        )

        # 使用 handle_message 回傳的完整 messages（含 tool 輪次），確保 API 合規
        if result.text:
            history = result.messages[-12:]


if __name__ == "__main__":
    run_cli()
