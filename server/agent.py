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
3A. **bio_check_l2_sufficiency**：執行 bio_run_spatial_eda 前必須先確認 l2_ready=true；若 false，回傳轉換命令，不得繼續執行分析。
3B. **bio_run_spatial_eda**：L2 Parquet 讀取，生成空間轉錄體 EDA 報告（含 QC 圖嵌入 + 摘要寫入 L1 快取）。需 l2_ready=true。
3C. **bio_run_bulk_eda**：Bulk RNA-seq EDA（QC + top genes + PCA 圖），需先執行 pipeline 腳本產生計數矩陣。
4. **bio_execute_code**：非標準分析，動態生成並沙盒執行 Python 程式碼（plt.show() 產生的圖會自動擷取並嵌入結果）。

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

1. 使用者詢問工具穩定性 → `action=report`（顯示熱區 + 進行中迭代）
2. 發現熱區（revision ≥ 3）→ 先 `action=diagnose` 寫入診斷，再 `action=stabilize` 開啟迭代
3. 重構完成後 → `action=close_stabilize` 記錄結果（stabilized/ongoing/reverted）
4. 使用者要求清理 → `action=prune`（只刪無分析引用的 deprecated，有引用的永遠保留）

**穩定化迭代原則**：
- 熱區工具（revision ≥ 3）+ 尚無迭代 → 主動建議開啟
- 已有進行中迭代 → report 標示 ✓，不重複開啟
- 穩定工具（revision < 3）可積極 prune（保留 2 個版本）
- 不穩定工具保留更多歷史（保留 10 個版本），供追溯
- `revision_after` 在 close 時自動回填，作為效果驗收依據

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
            "以自然語言語意搜尋 L1 快取（HNSW cosine ≥ 0.88）。"
            "只回傳 50 字 summary，節省 token。需要 embedding server 在線（port 8081）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然語言查詢"},
                "n": {"type": "integer", "description": "回傳筆數上限（預設 5）", "default": 5},
                "threshold": {"type": "number", "description": "相似度門檻（預設 0.88）", "default": 0.88},
                "sample_id": {"type": "string", "description": "限定樣本 ID（可選）"},
                "analysis_type": {
                    "type": "string",
                    "description": "限定分析類型（可選，如 spatial_eda / bulk_eda），避免跨類型命中",
                },
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
                "sample_id":  {"type": "string", "description": "唯一樣本 ID（全小寫底線）"},
                "data_type":  {"type": "string", "description": "資料類型：visium_hd | visium | scrna | bulk_rnaseq | ..."},
                "l3_path":    {"type": "string", "description": "L3 原始數據絕對路徑（唯讀）"},
                "project":    {"type": "string", "description": "專案代號（可選）"},
                "platform":   {"type": "string", "description": "平台（可選）"},
                "species":    {"type": "string", "description": "物種（預設 human）", "default": "human"},
                "tissue":     {"type": "string", "description": "組織類型（可選）"},
                "notes":      {"type": "string", "description": "備註（可選）"},
                "condition":  {"type": "string", "description": "實驗條件（可選）：control/tumor/treated/..."},
                "time_point": {"type": "string", "description": "時間點（可選）：0h/24h/day3/..."},
                "batch":      {"type": "string", "description": "測序批次（可選）：batch_1/batch_2/..."},
                "donor_id":   {"type": "string", "description": "供體 ID（可選），連結同一個體的多個樣本"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "標籤陣列（可選）：paper_figure/key_result/qc_only/...",
                },
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
        "name": "bio_sample_list",
        "description": (
            "列出 sample_registry 中已登記的樣本（0 token，純 SQL）。"
            "支援 data_type / tissue / condition 過濾，方便快速瀏覽現有資料集。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data_type": {
                    "type": "string",
                    "description": "資料類型篩選（可選，如 visium_hd / bulk_rnaseq）",
                },
                "tissue": {"type": "string", "description": "組織類型篩選（可選，模糊比對）"},
                "condition": {"type": "string", "description": "樣本條件篩選（可選，對應 notes 欄位模糊比對）"},
                "limit": {"type": "integer", "description": "最多回傳筆數（預設 50）", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "bio_sample_compare",
        "description": (
            "比較兩個或多個樣本的分析歷史摘要，回傳各樣本最新各類型分析的摘要對照表。"
            "協助判斷不同樣本的分析狀態差異，無需閱讀完整報告。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要比較的樣本 ID 列表（2 個以上）",
                },
            },
            "required": ["sample_ids"],
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
    {
        "name": "bio_tool_health",
        "description": (
            "工具庫健康報告與穩定化迭代管理。支援五個 action：\n"
            "  'report'          — 健康狀態總覽（active/deprecated/熱區/進行中迭代）\n"
            "  'diagnose'        — 寫入 stability_note（需 tool_name + note）\n"
            "  'stabilize'       — 開啟穩定化迭代，記錄診斷與行動計畫（需 tool_name + diagnosis + action_taken）\n"
            "  'close_stabilize' — 關閉迭代，記錄結果（需 log_id + outcome；outcome: stabilized/ongoing/reverted）\n"
            "  'prune'           — 清理未被引用的 deprecated 紀錄（需 tool_name）"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["report", "diagnose", "stabilize", "close_stabilize", "prune"],
                    "description": "操作類型",
                },
                "tool_name": {
                    "type": "string",
                    "description": "diagnose/stabilize/prune 時必填",
                },
                "note": {
                    "type": "string",
                    "description": "diagnose 時必填：說明為何頻繁變動及穩定化方向",
                },
                "diagnosis": {
                    "type": "string",
                    "description": "stabilize 時必填：問題診斷描述",
                },
                "action_taken": {
                    "type": "string",
                    "description": "stabilize 時必填：計畫採取的行動（重構/抽 helper/加測試...）",
                },
                "log_id": {
                    "type": "string",
                    "description": "close_stabilize 時必填：open_stabilization 回傳的 UUID",
                },
                "outcome": {
                    "type": "string",
                    "enum": ["stabilized", "ongoing", "reverted"],
                    "description": "close_stabilize 時必填：迭代結果",
                },
            },
            "required": ["action"],
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
            SELECT analysis_id, completed_at, result_path, summary, parameters
            FROM   analysis_history
            WHERE  sample_id = ? AND analysis_type = ? AND status = 'completed'
            ORDER  BY completed_at DESC LIMIT 1
            """,
            [sample_id, analysis_type],
        ).fetchone()
    if row:
        analysis_id, completed_at, result_path, summary, parameters = row
        params_str = parameters if parameters else "{}"
        return (
            f"exists: true\nanalysis_id: {analysis_id}\n"
            f"completed_at: {str(completed_at)[:16]}\n"
            f"result_path: {result_path or '（未記錄）'}\n"
            f"parameters: {params_str}\n"
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
    import duckdb
    from analysis.l1_cache import semantic_search
    from config.settings import DUCKDB_PATH
    results = semantic_search(
        args["query"],
        n=int(args.get("n", 5)),
        threshold=float(args.get("threshold", 0.88)),
        sample_id=args.get("sample_id"),
        analysis_type=args.get("analysis_type"),
    )
    if not results:
        return f"語意搜尋 cache miss（query={args['query']!r}）"
    # Enrich each L1 hit with parameters + result_path via l1_cache_id join
    l1_ids = [str(r["id"]) for r in results]  # 統一轉 str，避免 DuckDB UUID 物件型別不一致
    placeholders = ", ".join("?" * len(l1_ids))
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        enrichment_rows = con.execute(
            f"""
            SELECT l1_cache_id, parameters, result_path
            FROM   analysis_history
            WHERE  l1_cache_id IN ({placeholders}) AND status = 'completed'
            """,
            l1_ids,
        ).fetchall()
    enrichment = {str(row[0]): (row[1], row[2]) for row in enrichment_rows}
    if not enrichment:
        logger.warning("bio_history_search: enrichment 查詢無結果，l1_ids=%s", l1_ids)
    for r in results:
        params_raw, path_raw = enrichment.get(str(r["id"]), (None, None))
        r["parameters"] = params_raw if params_raw is not None else "{}"
        r["result_path"] = path_raw if path_raw is not None else "（未記錄）"
    lines = [f"語意搜尋命中 {len(results)} 筆"]
    for r in results:
        lines.append(
            f"  [{r['score']:.3f}] {r['sample_id']}\n"
            f"    摘要: {r['summary']}\n"
            f"    參數: {r['parameters']}\n"
            f"    結果路徑: {r['result_path']}"
        )
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
    total_chars = len(report)
    if total_chars > 2000:
        report = report[:2000] + f"\n…（完整報告共 {total_chars} 字，截斷於 2000 字，完整內容見 result_path）"
    return (
        f"L1 cache hit（score={r['score']:.4f}）\n"
        f"summary: {r['summary']}\ncreated_at: {str(r['created_at'])[:16]}\n\n"
        f"--- 完整報告 ---\n{report}"
    )


def _exec_bio_sample_list(args: dict) -> str:
    """列出 sample_registry 中的樣本，支援 data_type / tissue / condition 過濾。"""
    import duckdb
    from config.settings import DUCKDB_PATH

    data_type: Optional[str] = args.get("data_type")
    tissue: Optional[str] = args.get("tissue")
    condition: Optional[str] = args.get("condition")
    limit: int = int(args.get("limit", 50))

    where_clauses: list[str] = []
    params: list = []

    if data_type:
        where_clauses.append("data_type = ?")
        params.append(data_type)
    if tissue:
        where_clauses.append("tissue ILIKE ?")
        params.append(f"%{tissue}%")
    if condition:
        # condition 對應 notes 欄位模糊比對
        where_clauses.append("notes ILIKE ?")
        params.append(f"%{condition}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            f"""
            SELECT sample_id, data_type, tissue, notes AS condition,
                   l2_ready, analysis_done
            FROM   sample_registry
            {where_sql}
            ORDER  BY last_updated DESC
            LIMIT  ?
            """,
            params,
        ).fetchall()

    if not rows:
        return "sample_registry 中無符合條件的樣本。"

    header = f"樣本清單（共 {len(rows)} 筆）\n{'─' * 60}"
    col_header = f"{'sample_id':<30} {'data_type':<15} {'tissue':<12} {'condition':<15} {'l2_ready':<9} {'done'}"
    lines = [header, col_header]
    for r in rows:
        sid, dtype, tis, cond, l2, done = r
        lines.append(
            f"{str(sid):<30} {str(dtype or ''):<15} {str(tis or ''):<12} "
            f"{str(cond or '')[:14]:<15} {'✓' if l2 else '✗':<9} {'✓' if done else '✗'}"
        )
    return "\n".join(lines)


def _exec_bio_sample_compare(args: dict) -> str:
    """比較多個樣本的最新各類型分析摘要，回傳對照表。"""
    import duckdb
    from config.settings import DUCKDB_PATH

    sample_ids: list[str] = args.get("sample_ids", [])
    if len(sample_ids) < 2:
        return "[Error] bio_sample_compare 需要至少 2 個 sample_id。"

    placeholders = ", ".join("?" * len(sample_ids))

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        # 取每個樣本每種分析類型的最新 completed 紀錄
        rows = con.execute(
            f"""
            WITH ranked AS (
                SELECT ah.sample_id,
                       ah.analysis_type,
                       ah.completed_at,
                       ah.summary,
                       ah.result_path,
                       ROW_NUMBER() OVER (
                           PARTITION BY ah.sample_id, ah.analysis_type
                           ORDER BY ah.completed_at DESC
                       ) AS rn
                FROM   analysis_history ah
                WHERE  ah.sample_id IN ({placeholders})
                  AND  ah.status = 'completed'
            )
            SELECT sample_id, analysis_type,
                   strftime(completed_at, '%Y-%m-%d %H:%M') AS completed_at,
                   summary, result_path
            FROM   ranked
            WHERE  rn = 1
            ORDER  BY sample_id, analysis_type
            """,
            sample_ids,
        ).fetchall()

    if not rows:
        return f"指定樣本（{', '.join(sample_ids)}）均無 completed 分析記錄。"

    # 組裝對照表：以 analysis_type 為欄、sample_id 為列
    from collections import defaultdict

    table: dict[str, dict[str, str]] = defaultdict(dict)
    all_types: list[str] = []
    for sample_id, analysis_type, completed_at, summary, result_path in rows:
        entry = f"{(summary or '').strip()[:60]} [{completed_at}]"
        table[sample_id][analysis_type] = entry
        if analysis_type not in all_types:
            all_types.append(analysis_type)

    lines = [f"樣本比較對照表（{len(sample_ids)} 個樣本 × {len(all_types)} 種分析）"]
    lines.append(f"{'分析類型':<20} " + "  ".join(f"{sid[:20]:<22}" for sid in sample_ids))
    lines.append("─" * (22 + 24 * len(sample_ids)))
    for atype in all_types:
        row_parts = [f"{atype:<20}"]
        for sid in sample_ids:
            cell = table.get(sid, {}).get(atype, "（尚無記錄）")
            row_parts.append(f"{cell[:22]:<22}")
        lines.append("  ".join(row_parts))

    return "\n".join(lines)


def _exec_bio_tool_health(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.tool_registry import (
        tool_health_report, set_stability_note, prune_deprecated,
        open_stabilization, close_stabilization,
    )

    action = args.get("action", "report")

    if action == "report":
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            report = tool_health_report(con)
        lines = [
            "工具庫健康報告",
            f"  active 工具：{report['total_active']} 個",
            f"  deprecated 版本：{report['total_deprecated']} 個",
        ]
        if report["open_stabilizations"]:
            lines.append(f"\n進行中穩定化迭代（{len(report['open_stabilizations'])} 筆）：")
            for s in report["open_stabilizations"]:
                lines.append(
                    f"  [{s['log_id'][:8]}…] {s['tool_name']}  "
                    f"開始於 {s['created_at'][:16]}  "
                    f"行動：{(s['action_taken'] or '—')[:50]}"
                )
        if report["hot_zones"]:
            open_names = {s["tool_name"] for s in report["open_stabilizations"]}
            lines.append("\n熱區工具（revision_count ≥ 3）：")
            for t in report["hot_zones"]:
                tag = " ✓迭代中" if t["tool_name"] in open_names else " ⚠️ 尚無迭代"
                note = t["stability_note"] or "（尚無診斷）"
                lines.append(f"  {t['tool_name']}  revision={t['revision_count']}{tag}  診斷：{note}")
                for entry in t["change_log"][:3]:
                    reason = entry["reason"] or "—"
                    lines.append(
                        f"    [{entry['revision']}] {entry['old_hash'] or 'init'} → "
                        f"{entry['new_hash']}  {entry['changed_at'][:16]}  {reason}"
                    )
        else:
            lines.append("  無熱區工具（所有工具 revision < 3）")
        if report["stale_analyses"]:
            lines.append("\n過期分析結果（由舊版工具產生）：")
            for name, n in report["stale_analyses"].items():
                lines.append(f"  {name}: {n} 筆")
        if report["prune_candidates"]:
            lines.append("\n可安全清理的 deprecated 紀錄：")
            for name, n in report["prune_candidates"].items():
                lines.append(f"  {name}: {n} 筆")
        lines.append(f"\n建議：{report['recommendation']}")
        return "\n".join(lines)

    elif action == "diagnose":
        tool_name = args.get("tool_name")
        note = args.get("note")
        if not tool_name or not note:
            return "[Error] diagnose 需要提供 tool_name 和 note。"
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            set_stability_note(con, tool_name, note)
            con.execute("CHECKPOINT")
        return (
            f"已寫入 stability_note for {tool_name!r}：\n{note}\n\n"
            "建議接著呼叫 action=stabilize 開啟正式穩定化迭代，記錄行動計畫。"
        )

    elif action == "stabilize":
        tool_name = args.get("tool_name")
        diagnosis = args.get("diagnosis")
        action_taken = args.get("action_taken")
        if not tool_name or not diagnosis or not action_taken:
            return "[Error] stabilize 需要提供 tool_name、diagnosis、action_taken。"
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            log_id = open_stabilization(con, tool_name, diagnosis, action_taken)
            con.execute("CHECKPOINT")
        return (
            f"已開啟穩定化迭代 for {tool_name!r}\n"
            f"  log_id: {log_id}\n"
            f"  診斷：{diagnosis}\n"
            f"  行動：{action_taken}\n\n"
            f"完成後請呼叫 action=close_stabilize  log_id={log_id}  outcome=stabilized/reverted。"
        )

    elif action == "close_stabilize":
        log_id = args.get("log_id")
        outcome = args.get("outcome")
        if not log_id or not outcome:
            return "[Error] close_stabilize 需要提供 log_id 和 outcome。"
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            try:
                close_stabilization(con, log_id, outcome, args.get("action_taken"))
                con.execute("CHECKPOINT")
            except ValueError as e:
                return f"[Error] {e}"
        outcome_zh = {"stabilized": "已穩定化 ✓", "ongoing": "仍在進行", "reverted": "已回退"}.get(outcome, outcome)
        return f"迭代 {log_id[:8]}… 已關閉。結果：{outcome_zh}"

    elif action == "prune":
        tool_name = args.get("tool_name")
        if not tool_name:
            return "[Error] prune 需要提供 tool_name。"
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            deleted = prune_deprecated(con, tool_name)
            con.execute("CHECKPOINT")
        if deleted == 0:
            return f"{tool_name!r} 無可清理的 deprecated 紀錄（所有舊版本均有分析引用，已保留）。"
        return f"已清理 {tool_name!r} 的 {deleted} 筆 deprecated 紀錄（無分析引用的版本）。"

    return f"[Error] 未知 action: {action!r}，請使用 report/diagnose/stabilize/close_stabilize/prune。"


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
        f"~/.venvs/bioagent/bin/python scripts/02_spatial_to_parquet.py --sample-id {sample_id}"
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

    # run_full_eda_report 內部已實作完整兩階段寫入（INSERT running → UPDATE completed/failed）
    try:
        result = run_full_eda_report(sample_id, requested_by=requested_by)
        summary = result.get('summary', '')
        report_path = result.get('report_path', '(無)')
        # 讀取完整報告（含 inline base64 圖片），讓 web_app 解析並顯示
        report_text = ""
        if report_path and report_path != '(無)':
            try:
                from pathlib import Path as _Path
                report_text = _Path(report_path).read_text(encoding="utf-8")
            except Exception:
                pass
        analysis_id = result.get('analysis_id', '')
        # Track which tool version produced this result
        try:
            from analysis.tool_registry import get_active_tool_id
            with duckdb.connect(str(DUCKDB_PATH)) as _con:
                _tool_id = get_active_tool_id(_con, "bio_run_spatial_eda")
                if _tool_id and analysis_id:
                    _con.execute(
                        "UPDATE analysis_history SET tool_id = ? WHERE analysis_id = ?",
                        [_tool_id, analysis_id],
                    )
        except Exception as _te:
            logger.warning("bio_run_spatial_eda: tool_id tracking failed: %s", _te)

        header = (
            f"EDA 完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"summary: {summary}\n"
            f"report_path: {report_path}\n\n"
        )
        return header + report_text
    except Exception as e:
        logger.exception("bio_run_spatial_eda failed for %r", sample_id)
        return f"EDA 執行失敗：{e}"


def _exec_bio_register_sample(args: dict) -> str:
    import re
    from config.db_utils import safe_write, get_connection
    from datetime import datetime, timezone
    sample_id = args["sample_id"]
    if not re.match(r'^[a-z0-9_-]+$', sample_id):
        return f"樣本 ID {sample_id!r} 格式錯誤：只允許小寫英數字、底線和連字號。"
    con = get_connection()
    if con.execute("SELECT 1 FROM sample_registry WHERE sample_id=?", [sample_id]).fetchone():
        return f"樣本 {sample_id!r} 已存在，跳過。"
    safe_write(
        con,
        """INSERT INTO sample_registry
               (sample_id, project, data_type, platform, species, tissue,
                l3_path, l2_ready, analysis_done, added_by, notes, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, false, false, ?, ?, ?)""",
        [
            sample_id, args.get("project", ""), args["data_type"],
            args.get("platform", ""), args.get("species", "human"),
            args.get("tissue", ""), args["l3_path"],
            "agent", args.get("notes", ""), datetime.now(timezone.utc),
        ],
    )
    return f"樣本 {sample_id!r} 已登記。data_type={args['data_type']!r}"


def _exec_bio_run_bulk_eda(args: dict) -> str:
    import duckdb
    from analysis.bulk_eda import generate_bulk_report
    from config.settings import DUCKDB_PATH
    sample_id    = args["sample_id"]
    requested_by = args.get("requested_by", "agent")
    try:
        analysis_id, report_path = generate_bulk_report(sample_id, requested_by=requested_by)
        # 讀取完整報告（含 inline base64 圖片），讓 web_app 解析並顯示
        report_text = ""
        if report_path:
            try:
                from pathlib import Path as _Path
                report_text = _Path(report_path).read_text(encoding="utf-8")
            except Exception:
                pass
        # Track which tool version produced this result
        try:
            from analysis.tool_registry import get_active_tool_id
            with duckdb.connect(str(DUCKDB_PATH)) as _con:
                _tool_id = get_active_tool_id(_con, "bio_run_bulk_eda")
                if _tool_id and analysis_id:
                    _con.execute(
                        "UPDATE analysis_history SET tool_id = ? WHERE analysis_id = ?",
                        [_tool_id, analysis_id],
                    )
        except Exception as _te:
            logger.warning("bio_run_bulk_eda: tool_id tracking failed: %s", _te)

        header = (
            f"Bulk EDA 完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
        )
        return header + report_text
    except Exception as e:
        return f"Bulk EDA 執行失敗：{e}"


def _exec_bio_execute_code(args: dict) -> str:
    from server.code_executor import sandbox_exec, SecurityError
    import tempfile, base64, json
    from pathlib import Path as _Path

    code = args["code"]
    description = args.get("description", "")
    timeout = int(args.get("timeout", 60))

    # SecurityError 在 sandbox_exec 內部檢查，preamble 為系統注入，不經 LLM 生成
    with tempfile.TemporaryDirectory(prefix="hermes_fig_") as fig_dir:
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

    if not result.success:
        return f"執行失敗（{result.duration_sec}s）\n{result.traceback[:1000]}"

    # 寫入 analysis_history，讓 Code Promotion 框架可掃描重用紀錄
    try:
        import duckdb, uuid as _uuid
        from datetime import datetime, timezone
        from config.settings import DUCKDB_PATH
        from config.db_utils import safe_write
        now = datetime.now(timezone.utc)
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            safe_write(
                con,
                """INSERT INTO analysis_history
                       (analysis_id, sample_id, analysis_type, parameters, status,
                        requested_by, started_at, completed_at, summary)
                   VALUES (?, ?, 'dynamic_code', ?, 'completed', 'agent', ?, ?, ?)""",
                [
                    str(_uuid.uuid4()),
                    args.get("sample_id", "unknown"),
                    json.dumps({"generated_code": code, "description": description}),
                    now, now,
                    description[:50] or "dynamic code execution",
                ],
            )
    except Exception:
        logger.warning("bio_execute_code: 寫入 analysis_history 失敗（不影響結果）", exc_info=True)

    out = result.output[:2000] if len(result.output) > 2000 else result.output
    return f"執行成功（{result.duration_sec}s）\n{out}{fig_md}"


_TOOL_HANDLERS = {
    "bio_history_check": _exec_bio_history_check,
    "bio_history_lookup": _exec_bio_history_lookup,
    "bio_history_timeline": _exec_bio_history_timeline,
    "bio_history_search": _exec_bio_history_search,
    "bio_memory_query": _exec_bio_memory_query,
    "bio_sample_list": _exec_bio_sample_list,
    "bio_sample_compare": _exec_bio_sample_compare,
    "bio_check_l2_sufficiency": _exec_bio_check_l2_sufficiency,
    "bio_tool_health":          _exec_bio_tool_health,
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
        from config.settings import ANTHROPIC_API_KEY
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
                    out.append({"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": b64,
                    }})
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
        tools=cached_tools,    # type: ignore[arg-type]
        messages=converted,    # type: ignore[arg-type]
        betas=["prompt-caching-2024-07-31"],
    )
    return resp.stop_reason, resp.content, resp.usage.input_tokens, resp.usage.output_tokens  # type: ignore[union-attr]


def _make_local_call(messages: list[dict], model: str, max_tokens: int):
    """呼叫本機 llama.cpp，回傳 chat completion response。"""
    return _get_local_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        tools=_OPENAI_TOOLS,
        tool_choice="auto",
        messages=messages,
    )


_google_client = None


def _get_google_client():
    global _google_client
    if _google_client is None:
        from google import genai
        from config.settings import GOOGLE_API_KEY
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
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=types.Schema(**_strip_schema_defaults(dict(t["input_schema"]))),  # type: ignore[arg-type]
            )
            for t in BIO_TOOLS
        ])
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
                history_contents.append(
                    types.Content(role=role, parts=[types.Part(text=content)])
                )
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
                            parts.append(types.Part(
                                inline_data=types.Blob(mime_type=mime, data=data)
                            ))
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
    in_tok  = getattr(resp.usage_metadata, "prompt_token_count", 0) or 0
    out_tok = getattr(resp.usage_metadata, "candidates_token_count", 0) or 0
    finish  = resp.candidates[0].finish_reason.name if resp.candidates else "STOP"
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
    _google_native: list = []  # accumulates native Gemini Content objects across tool rounds

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
            serializable_blocks = [
                b.model_dump() if hasattr(b, "model_dump") else b
                for b in content_blocks
            ]
            messages.append({"role": "assistant", "content": serializable_blocks})
            messages.append({"role": "user", "content": tool_results})
            continue

        # ── google backend (Gemini API) ───────────────────────────────────────
        if resolved_backend == "google":
            from google.genai import types as _gtypes

            # First iteration builds history from messages; subsequent iterations
            # pass the accumulated native_history so FunctionCall parts are preserved.
            finish, resp, in_tok, out_tok, _google_native = _make_google_call(
                messages, resolved_model, max_tokens,
                native_history=_google_native if _round > 0 else None,
            )
            total_input  += in_tok
            total_output += out_tok

            candidate = resp.candidates[0] if resp.candidates else None
            candidate_parts = candidate.content.parts if (candidate and candidate.content) else []
            fn_calls = [p.function_call for p in candidate_parts
                        if hasattr(p, "function_call") and p.function_call]

            if fn_calls:
                # Preserve the model turn with its FunctionCall parts in native history
                _google_native.append(
                    _gtypes.Content(role="model", parts=candidate_parts)
                )
                # Batch all tool results into a single user turn (Gemini requires alternating roles)
                response_parts = []
                for fc in fn_calls:
                    fn_args = dict(fc.args) if fc.args else {}
                    tool_result = execute_tool(fc.name, fn_args)
                    logger.info("Tool %r called: %s…", fc.name, str(tool_result)[:60])
                    all_tool_calls.append({"name": fc.name, "input": fn_args, "result": tool_result})
                    response_parts.append(
                        _gtypes.Part(function_response=_gtypes.FunctionResponse(
                            name=fc.name,
                            response={"result": tool_result[:800]},
                        ))
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
            return AgentResponse(text=text, tool_calls=all_tool_calls,
                                 input_tokens=total_input, output_tokens=total_output,
                                 messages=messages)

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


def _startup_cleanup() -> None:
    """Agent 啟動時清理殭屍 running 狀態（> 24h 標為 stale）。"""
    try:
        from config.db_utils import cleanup_stale_runs, get_connection
        con = get_connection()
        cleaned = cleanup_stale_runs(con)
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
        print(f"  [tokens: in={result.input_tokens} out={result.output_tokens} | tools={len(result.tool_calls)}]")

        # 使用 handle_message 回傳的完整 messages（含 tool 輪次），確保 API 合規
        if result.text:
            history = result.messages[-12:]


if __name__ == "__main__":
    run_cli()
