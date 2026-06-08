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
                "limit": {
                    "type": "integer",
                    "description": "最多回傳筆數（預設 20）",
                    "default": 20,
                },
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
                "threshold": {
                    "type": "number",
                    "description": "相似度門檻（預設 0.88）",
                    "default": 0.88,
                },
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
                "requested_by": {
                    "type": "string",
                    "description": "請求者（預設 agent）",
                    "default": "agent",
                },
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
                "data_type": {
                    "type": "string",
                    "description": "資料類型：visium_hd | visium | scrna | bulk_rnaseq | ...",
                },
                "l3_path": {"type": "string", "description": "L3 原始數據絕對路徑（唯讀）"},
                "project": {"type": "string", "description": "專案代號（可選）"},
                "platform": {"type": "string", "description": "平台（可選）"},
                "species": {
                    "type": "string",
                    "description": "物種（預設 human）",
                    "default": "human",
                },
                "tissue": {"type": "string", "description": "組織類型（可選）"},
                "notes": {"type": "string", "description": "備註（可選）"},
                "condition": {
                    "type": "string",
                    "description": "實驗條件（可選）：control/tumor/treated/...",
                },
                "time_point": {"type": "string", "description": "時間點（可選）：0h/24h/day3/..."},
                "batch": {"type": "string", "description": "測序批次（可選）：batch_1/batch_2/..."},
                "donor_id": {
                    "type": "string",
                    "description": "供體 ID（可選），連結同一個體的多個樣本",
                },
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
                "sample_id": {"type": "string", "description": "樣本集 ID,例如 Kallisto_v1"},
                "requested_by": {
                    "type": "string",
                    "description": "請求者(預設 agent)",
                    "default": "agent",
                },
            },
            "required": ["sample_id"],
        },
    },
    {
        "name": "bio_run_deg",
        "description": (
            "Bulk RNA-seq 差異表達分析(DESeq2 via omicverse.pyDEG)+ 火山圖。"
            "對多組對照逐一跑 DEG,每組產出 DEG_<a>_vs_<b>.csv + Volcano_<a>_vs_<b>.png,"
            "彙整報告寫入 analysis_history(analysis_type=bulk_deg)。"
            "**對齊 ddmanyes/bulk-rnaseq-pipeline 的 DESeq2 流程**。先 bio_get_playbook(bulk_rnaseq)。"
            "耗時依樣本數而定(84 樣本 × 1 對照 ≈ 1–3 分鐘)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "已登記的樣本 ID"},
                "counts_path": {
                    "type": "string",
                    "description": "gene × sample counts CSV(如 bulk_rna_data/.../deseq2_counts.csv)",
                },
                "coldata_path": {
                    "type": "string",
                    "description": "sample × group 設計表(TSV/CSV,需 'group' 欄)",
                },
                "comparisons": {
                    "type": "array",
                    "description": "對照組清單,每筆 [treat, ctrl] 兩個 group 名,如 [['pw24hr','ctrl'],['pw48hr','ctrl']]",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "minItems": 1,
                },
                "method": {
                    "type": "string",
                    "description": "DEseq2 / ttest / wilcox",
                    "default": "DEseq2",
                },
                "fc_threshold": {
                    "type": "number",
                    "description": "|log2FC| 顯著閾值",
                    "default": 1.0,
                },
                "pval_threshold": {
                    "type": "number",
                    "description": "qvalue 顯著閾值",
                    "default": 0.05,
                },
                "requested_by": {"type": "string", "default": "agent"},
            },
            "required": ["sample_id", "counts_path", "coldata_path", "comparisons"],
        },
    },
    {
        "name": "bio_run_enrichment",
        "description": (
            "對 DEG 表跑 ORA 富集分析(gseapy.enrichr 線上 API)。"
            "up/down 兩方向 × N 個 library(預設 GO_BP / KEGG / Reactome)各自命中通路 + dot plot。"
            "寫入 analysis_history(analysis_type=bulk_enrichment)。"
            "**需網路連線 Enrichr API**;deg_table_path 需指向 bio_run_deg 產出的 CSV。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string"},
                "deg_table_path": {
                    "type": "string",
                    "description": "bio_run_deg 產出的 DEG_<a>_vs_<b>.csv 絕對或相對路徑",
                },
                "libraries": {
                    "type": "array",
                    "description": "Enrichr gene set library 名稱清單;省略則用預設 GO/KEGG/Reactome",
                    "items": {"type": "string"},
                },
                "organism": {"type": "string", "default": "human"},
                "fc_threshold": {"type": "number", "default": 1.0},
                "pval_threshold": {"type": "number", "default": 0.05},
                "top_term": {
                    "type": "integer",
                    "description": "dot plot 顯示前 N 條 term",
                    "default": 10,
                },
                "requested_by": {"type": "string", "default": "agent"},
            },
            "required": ["sample_id", "deg_table_path"],
        },
    },
    {
        "name": "bio_run_heatmaps",
        "description": (
            "為 Bulk RNA-seq 產出兩張熱圖:(1) 顯著基因熱圖(union of DEG 顯著基因),"
            "(2) Top N 變異基因熱圖(預設 top 50)。皆 z-score normalized,含階層聚類(sns.clustermap)。"
            "寫入 analysis_history(analysis_type=bulk_heatmap)。"
            "對齊 ddmanyes/bulk-rnaseq-pipeline 的 Heatmap_Significant_Genes / Heatmap_Top50_Variable_Genes。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string"},
                "counts_path": {"type": "string", "description": "gene × sample counts CSV"},
                "deg_tables": {
                    "type": "array",
                    "description": "一張或多張 DEG CSV 路徑;會 union 後抽顯著基因",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "top_n": {"type": "integer", "default": 50},
                "fc_threshold": {"type": "number", "default": 1.0},
                "pval_threshold": {"type": "number", "default": 0.05},
                "requested_by": {"type": "string", "default": "agent"},
            },
            "required": ["sample_id", "counts_path", "deg_tables"],
        },
    },
    {
        "name": "bio_impact",
        "description": (
            "影響分析 / 爆炸範圍(blast radius)。回答『改版/deprecate 某工具,或重跑/撤回某樣本,"
            "會影響哪些分析與產物』。借鏡 GitNexus 的 impact tool,每條影響邊帶 confidence:"
            "tool_id 精確=1.0 / 同分析=0.9 / analysis_type 啟發式=0.6。"
            "恰好給一個目標:tool_name 或 artifact_id 或 sample_id。0 LLM token 純 SQL。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "工具名(如 bio_run_bulk_eda)→ 該工具改版會影響哪些分析",
                },
                "artifact_id": {
                    "type": "string",
                    "description": "產物 ID → 下游受影響的 artifacts",
                },
                "sample_id": {"type": "string", "description": "樣本 ID → 該樣本所有分析與產物"},
            },
            "required": [],
        },
    },
    {
        "name": "bio_find_tool",
        "description": (
            "語意搜尋既有可重用的分析函數（tool discovery）。"
            "**寫 bio_execute_code 前務必先呼叫**：描述你要做的分析意圖，"
            "回傳最相關的既有函數 + 簽名 + import 方式。命中就在動態碼中 import 重用，"
            "勿從零重寫。0 LLM token 的本地語意搜尋；全 miss 才表示需自行撰寫。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要做的分析意圖（自然語言，如『時間序列 log2 fold change』）",
                },
                "n": {"type": "integer", "description": "回傳候選數上限（預設 5）", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "bio_run_mcseg_qc",
        "description": (
            "MCseg 細胞分割品質視覺化（讀既有 .npy 遮罩，**不**即時重跑分割）。"
            "掃 qc_dir 內成對的 *_nuc.npy / *_mcseg.npy，產出 NUC vs MCseg 對比圖 + "
            "細胞面積分布 + 量化表，寫入 analysis_history（analysis_type=mcseg_qc）。"
            "先 bio_get_playbook(mcseg) 取方法學。需先有分割輸出檔。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {"type": "string", "description": "樣本 ID"},
                "qc_dir": {
                    "type": "string",
                    "description": "分割遮罩目錄（省略則用預設 results/mcseg_qc/）",
                },
            },
            "required": ["sample_id"],
        },
    },
    {
        "name": "bio_get_playbook",
        "description": (
            "取得某分析領域的『技能說明書』（標準步驟順序 + 每步該呼叫的函數 + 該產出的圖 + 品質關卡）。"
            "**執行任何領域分析（bulk / 空間 / mcseg）前先呼叫**，依說明書分步進行，確保每步出圖、不漏步。"
            "省略 domain 則列出所有可用說明書。0 LLM token 的本地讀取。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "說明書名稱或 data_type，如 bulk_rnaseq / spatial_visium / visium_hd（省略則列出全部）",
                },
            },
            "required": [],
        },
    },
    {
        "name": "bio_execute_code",
        "description": (
            "沙盒執行動態生成的 Python 程式碼（用於非標準分析）。"
            "**呼叫前先用 bio_find_tool 找既有函數**，命中則在此 import 重用，勿重造輪子。"
            "只允許白名單 import（pandas, numpy, scipy, anndata, scanpy，以及 "
            "analysis.spatial_eda / bulk_eda / pathway_scoring / multiomics_integration / "
            "bulk_timeseries / report_generator 等既有分析函數）。"
            "禁止 os.system, subprocess, open(), eval, exec 等危險操作。"
            "timeout=60 秒。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要執行的 Python 程式碼"},
                "description": {"type": "string", "description": "此程式碼的分析目的（用於記錄）"},
                "timeout": {
                    "type": "integer",
                    "description": "執行超時秒數（預設 60）",
                    "default": 60,
                },
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
                "condition": {
                    "type": "string",
                    "description": "樣本條件篩選（可選，對應 notes 欄位模糊比對）",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多回傳筆數（預設 50）",
                    "default": 50,
                },
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
        "name": "bio_read_report",
        "description": (
            "讀取分析報告（.md/.txt/.log）原文。路徑必須位於 results/ 或 results_ana/ 內，"
            "其他路徑會被沙盒拒絕。超過 max_chars 時自動截斷為 head+tail 兩段。"
            "用於：使用者問「報告裡寫了什麼」「打開 xxx.md」等需要原文佐證的請求。"
            "禁止憑檔名推測內容——務必呼叫此工具取得真實文字。"
        ),
        "input_schema": {
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
    },
    {
        "name": "bio_tool_health",
        "description": (
            "工具庫健康報告與穩定化迭代管理。支援六個 action：\n"
            "  'report'          — 健康狀態總覽（active/deprecated/熱區/進行中迭代/VLM快照）\n"
            "  'diagnose'        — 寫入 stability_note（需 tool_name + note）\n"
            "  'stabilize'       — 開啟穩定化迭代，記錄診斷與行動計畫（需 tool_name + diagnosis + action_taken）\n"
            "  'close_stabilize' — 關閉迭代，記錄結果（需 log_id + outcome；outcome: stabilized/ongoing/reverted）\n"
            "  'trend'           — 複雜度改善趨勢（可選 tool_name 過濾；查看跨迭代 CC delta）\n"
            "  'prune'           — 清理未被引用的 deprecated 紀錄（需 tool_name）"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "report",
                        "diagnose",
                        "stabilize",
                        "close_stabilize",
                        "trend",
                        "prune",
                    ],
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


from server.agent_spatial import (
    _exec_bio_check_l2_sufficiency,
    _exec_bio_run_spatial_eda,
)
from server.agent_bulk import (
    _exec_bio_run_bulk_eda,
    _exec_bio_run_mcseg_qc,
    _exec_bio_run_deg,
    _exec_bio_run_enrichment,
    _exec_bio_run_heatmaps,
)
from server.agent_history import (
    _exec_bio_history_check,
    _exec_bio_history_lookup,
    _exec_bio_history_timeline,
    _exec_bio_history_search,
    _exec_bio_memory_query,
    _exec_bio_sample_list,
    _exec_bio_sample_compare,
    _exec_bio_tool_health,
    _exec_bio_read_report,
    _exec_bio_find_tool,
    _exec_bio_get_playbook,
    _exec_bio_impact,
    _exec_bio_register_sample,
    _exec_bio_execute_code,
)


_TOOL_HANDLERS = {
    "bio_history_check": _exec_bio_history_check,
    "bio_find_tool": _exec_bio_find_tool,
    "bio_get_playbook": _exec_bio_get_playbook,
    "bio_history_lookup": _exec_bio_history_lookup,
    "bio_history_timeline": _exec_bio_history_timeline,
    "bio_history_search": _exec_bio_history_search,
    "bio_memory_query": _exec_bio_memory_query,
    "bio_sample_list": _exec_bio_sample_list,
    "bio_sample_compare": _exec_bio_sample_compare,
    "bio_check_l2_sufficiency": _exec_bio_check_l2_sufficiency,
    "bio_tool_health": _exec_bio_tool_health,
    "bio_run_spatial_eda": _exec_bio_run_spatial_eda,
    "bio_run_bulk_eda": _exec_bio_run_bulk_eda,
    "bio_run_deg": _exec_bio_run_deg,
    "bio_run_enrichment": _exec_bio_run_enrichment,
    "bio_run_heatmaps": _exec_bio_run_heatmaps,
    "bio_impact": _exec_bio_impact,
    "bio_run_mcseg_qc": _exec_bio_run_mcseg_qc,
    "bio_register_sample": _exec_bio_register_sample,
    "bio_execute_code": _exec_bio_execute_code,
    "bio_read_report": _exec_bio_read_report,
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
LLAMA_MODEL = "gemma-4"

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
