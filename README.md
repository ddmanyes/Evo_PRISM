# Bio_PRISM

**Bioinformatics Platform for Research Intelligence, Semantic Memory**

Bio_PRISM 是以 **LLM Agent + MCP** 為核心的實驗室生資知識管理平台。系統採三層架構（L3 原始數據 → L2 結構化特徵 → L1 語意快取），搭配 **HELIX**（工具版本健康追蹤）與 **ENGRAM**（分析產出永久記憶），讓任何實驗室成員透過自然語言查詢空間轉錄體、Bulk RNA、scRNA、蛋白質體等任意組學數據。每份分析結果永久歸檔、可語意搜尋，並強制關聯至產生它的程式版本，不再重複運算、不再遺失脈絡。

*Bio_PRISM is a lab knowledge management platform powered by **LLM Agent + MCP (Model Context Protocol)**. It connects language models to 15 bioinformatics tools through a three-layer data architecture (L3 raw → L2 structured features → L1 semantic cache), with **HELIX** for tool versioning and health tracking, and **ENGRAM** for permanent artifact memory. Any lab member can query spatial transcriptomics, bulk RNA-seq, scRNA-seq, proteomics, and more in plain language — every result is archived, semantically searchable, and traceable to the exact tool version that produced it.*

[![CI](https://github.com/ddmanyes/Bio_PRISM/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ddmanyes/Bio_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.5-yellow)](https://duckdb.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

---

## 為什麼是 Bio_PRISM？ / Why Bio_PRISM?

傳統生資分析的五個痛點，Bio_PRISM 逐一解決。

*Bio_PRISM addresses five common pain points in bioinformatics workflows.*

| 痛點 / Problem | Bio_PRISM 的解法 / Solution |
|:---|:---|
| 🔁 每次提問都要重跑程式 / Re-running the same analysis repeatedly | **L1 語意快取**：相似問題毫秒回答，節省 GPU / 記憶體 · *Semantic cache answers similar queries in milliseconds* |
| 📂 分析結果散落各地、無法搜尋 / Results scattered and unsearchable | **ENGRAM 永久記憶**：每次產出自動歸檔，支援語意搜尋 · *Every artifact auto-archived with hybrid semantic search* |
| 🐛 不知道結果是哪版程式跑出來的 / No traceability between results and code | **HELIX 版本追蹤**：工具版本與分析歷史強制關聯 · *Tool versions hard-linked to analysis history* |
| 🧑‍💻 非工程師無法查詢數據 / Non-coders locked out of data | **自然語言 Agent**：Web UI + Telegram，無需寫程式 · *Plain-language queries via Web UI or Telegram* |
| 📊 只支援特定數據類型 / Platform locked to one data type | **可擴充三層架構**：空間轉錄體、Bulk RNA、scRNA、蛋白質體、ATAC 等皆可接入 · *Extensible to any omics: spatial Tx, bulk RNA, scRNA, proteomics, ATAC, and more* |

---

## 系統架構 / Architecture

### 三層資料架構 / Three-Layer Data Architecture

![Bio_PRISM 三層架構](docs/images/三層架構.png)

| 層 / Layer | 名稱 / Name | 說明 / Description |
|:---:|:---:|:---|
| L3 | Bronze | 不可變原始數據 / Immutable raw data (FASTQ, SpaceRanger outs) |
| L2 | Silver | DuckDB + Parquet 結構化特徵 / Structured features (30B → 416 MB) |
| L1 | Gold | HNSW 語意快取，TTL 7 天 / Semantic cache, TTL 7 days |

### HELIX — 工具健康進化迴路 / Tool Health-Evolving Loop

![HELIX 架構](docs/images/HELIX_架構圖.png)

**HELIX** 負責追蹤所有分析工具的版本、熱區偵測、複雜度量測與穩定化重構，確保 Agent 呼叫的永遠是健康版本。

*HELIX tracks every analysis tool's version, detects hot-spots, measures complexity (Radon CC), and drives stabilization refactors — ensuring Agent always calls a healthy version.*

### ENGRAM — 分析產出永久記憶 / Permanent Artifact Memory

![ENGRAM 架構](docs/images/engram_架構圖1.png)

**ENGRAM** 將每次分析產出（CSV、Parquet、圖片、報告）永久歸檔，支援 Hybrid 3-way RRF 語意搜尋（Exact SQL + HNSW + BM25 FTS），並與 HELIX 工具帳本關聯防止版本漂移。

*ENGRAM permanently archives every analysis artifact and enables Hybrid 3-way RRF semantic search (Exact SQL + HNSW + BM25 FTS), linked to HELIX for version provenance.*

### Agent 決策流程 / Agent Decision Flow

```text
提問 / Query
 ├─ Step 1  SQL 精確比對（0 token，< 1 秒）  ← 做過？直接回傳
 ├─ Step 2  HNSW 語意搜尋（cosine ≥ 0.88）   ← 問過類似？快取回傳
 ├─ Step 3A 標準分析工具（L2 Parquet 就緒）
 ├─ Step 3B Code Promotion 重用（曾生成過？）
 └─ Step 3C 全新程式碼生成（沙盒執行 + 失敗重試）
               └─ 執行成功 → 存入歷史 → 下次可被 3B 查詢
               └─ 重用 ≥ 3 次 → 升格為 3A 永久工具
```

---

## 快速開始 / Quick Start

Bio_PRISM 採用 **MCP-First（以 MCP 伺服器為核心）** 的設計。如果您主要是在 IDE（如 Antigravity）或 CLI（如 Claude Code）中使用 AI 客戶端直接呼叫工具，**您完全不需要下載或運行 26B 的本機 Gemma 模型**，只需啟動極其輕量的本機 Embedding 伺服器即可！

*Bio_PRISM is designed as an **MCP-First** platform. If you primarily use AI clients in IDEs (like Antigravity) or CLI (like Claude Code) to call tools, **you do not need the heavy 26B local Gemma model at all** — only a lightweight local embedding server is needed.*

### 前置需求 / Prerequisites

#### 1. 核心模式（推薦：MCP 優先 / 雲端 LLM 模式）· *Core Mode (Recommended)*
*   macOS（開發測試）或 Linux（生產部署）
*   Python ≥ 3.10 與 [uv](https://github.com/astral-sh/uv) 套件管理器
*   [llama.cpp](https://github.com/ggml-org/llama.cpp) 已編譯（`~/llama.cpp/build/bin/llama-server`）
*   **輕量模型**（放於 `~/`）：
    *   `bge-m3-Q8_0.gguf` — Embedding 模型（僅 605 MB，用於 L1 語意快取與 ENGRAM 混合檢索）

#### 2. 完全離線模式（選配：本機 Web UI 運作）· *Offline Mode (Optional)*
如果您需要 100% 離線或在本地跑完全部推理流程，才需要額外下載：
*   `gemma-4-26B-A4B-it-UD-IQ2_M.gguf` — 本機推理引擎（需 ~16 GB RAM）
*   `mmproj-F16.gguf` — 視覺投影層 / Vision projector

---

### 安裝 / Installation

```bash
# 設定專案根目錄
export BIO_DB_ROOT="$(pwd)"

# 1. 建立 venv（若位於 ExFAT 或雲端同步資料夾，venv 必須建在 APFS 本機）
python3 -m venv ~/.venvs/hermes-bio-memory
ln -s ~/.venvs/hermes-bio-memory "$BIO_DB_ROOT/.venv"

# 2. 安裝依賴 / Install dependencies
cd "$BIO_DB_ROOT"
uv sync --no-install-project

# 3. 設定環境變數 / Configure environment
cp .env.example .env
# 填入您的 API keys (若使用 Claude/Gemini 等雲端 LLM 後端)

# 4. 初始化資料庫 / Initialize database
.venv/bin/python scripts/00_init_db.py

# 5. 初始化 L1 快取 / Initialize L1 cache
.venv/bin/python scripts/03_init_l1_cache.py

# 6. 執行所有 Schema migration（v2 → v20）
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    .venv/bin/python "$script"
done
```

---

### 啟動與使用 / Launch & Execution

#### 💡 模式 A：MCP 優先模式（推薦 - 直接連接 IDE/CLI 客戶端）
此模式不需要啟動主 Web 伺服器，直接透過 AI 客戶端連線。

1. **啟動 Embedding Server**（提供 L1 語意快取支援，極輕量且必要）：
   ```bash
   ~/llama.cpp/build/bin/llama-server \
     -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \
     --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99 &
   ```
2. **連線至您的 MCP 客戶端**：
   *   **Claude Code CLI**：已在專案根目錄配置 `.mcp.json`，直接啟動 `claude` 即可自動載入工具。
   *   **Antigravity IDE**：在 Settings 中設定本機 MCP 伺服器（詳見下文 MCP 整合說明）。
   *   **啟動獨立 HTTP 傳輸**（若外部客戶端需要）：
       ```bash
       .venv/bin/python server/bio_memory_server.py --transport http --port 8082
       ```

#### 🧪 快速驗證：用內建 Demo 數據測試 DEG 分析 · *Quick Validation with Bundled Demo Data*

Repo 內附 `tests/fixtures/bulk_rna/` 小型數據集（無需下載完整數據即可執行）：

*The repo ships with `tests/fixtures/bulk_rna/` — no extra downloads needed:*

```python
# 直接在 Python 中呼叫 / Call directly from Python
from analysis.bulk_eda import run_deg_analysis

result = run_deg_analysis(
    counts_path="tests/fixtures/bulk_rna/deseq2_counts_top1000.csv",
    coldata_path="tests/fixtures/bulk_rna/deseq2_coldata.tsv",
    condition_col="group",
    ref_level="ctrl",
)
print(result["summary"])   # DEG 數量摘要
```

或透過 MCP 工具呼叫（Claude Code CLI 內）：

*Or call via MCP tool (inside Claude Code CLI):*

```
bio_run_deg counts_path=tests/fixtures/bulk_rna/deseq2_counts_top1000.csv
            coldata_path=tests/fixtures/bulk_rna/deseq2_coldata.tsv
            condition_col=group ref_level=ctrl
```

---

#### 🌐 模式 B：啟動 Web UI 互動介面
如果您想使用網頁儀表板，可使用雲端 API (推薦) 或完全本機推理：

```bash
bash start_bioagent.sh --claude  # 使用 Claude API（推薦，需 ANTHROPIC_API_KEY）
bash start_bioagent.sh --google  # 使用 Gemini API（推薦，需 GOOGLE_API_KEY）
bash start_bioagent.sh --local   # 完全本機運作（需 gemma-4-26B 模型與 ~16 GB RAM）
```
啟動後開啟瀏覽器：**<http://localhost:8000>**

---

## 測試資料 / Test Data

### 隨 repo 附帶（可直接使用）/ Included in Repo

`tests/fixtures/bulk_rna/` 內含開箱即用的 Bulk RNA-seq 示範數據集，無需額外下載：

*`tests/fixtures/bulk_rna/` ships with the repo — no extra downloads needed:*

| 檔案 | 大小 | 說明 |
|------|------|------|
| `deseq2_counts_top1000.csv` | ~400KB | Count matrix（1000 個最高變異基因 × 84 樣本，DESeq2 格式）|
| `deseq2_coldata.tsv` | 2KB | 樣本 metadata（group 欄位，DESeq2 必需）|
| `gene_sets/hair_follicle.yaml` | < 1KB | 路徑基因集範例（OxPhos / TCA / FAO 等）|

可直接用於 `bio_run_deg`、`bio_run_enrichment`、`bio_run_heatmaps` 工具 demo（範例見快速開始章節）。

*Sufficient to demo `bio_run_deg`, `bio_run_enrichment`, and `bio_run_heatmaps` (see Quick Start above).*

```bash
# 跑所有自動化測試 / Run all automated tests
.venv/bin/python -m pytest tests/ -v --tb=short
# 562 tests collected
```

---

## LLM + MCP Server 整合 / LLM + MCP Integration

Bio_PRISM 以 **MCP（Model Context Protocol）** 作為 LLM 與生資工具之間的標準橋樑。任何支援 MCP 的 AI 客戶端（Claude Code、Antigravity IDE、Web UI）都能直接呼叫 15 個生資工具，讓 LLM 決定何時查詢歷史、何時觸發分析、何時讀回報告——無需人工介入。

*Bio_PRISM uses **MCP (Model Context Protocol)** as the standard bridge between LLMs and bioinformatics tools. Any MCP-compatible client (Claude Code, Antigravity IDE, Web UI) can invoke 15 bio tools directly — the LLM autonomously decides when to query history, trigger analysis, or retrieve reports.*

同時提供 **stdio** 與 **HTTP** 兩種 transport，供外部 AI 客戶端呼叫。

*Supports both **stdio** and **HTTP** transport for external AI client integration.*

```bash
# HTTP 獨立啟動 / Standalone HTTP
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

### 可用工具 / Available Tools（預設 15 個）

| 工具 / Tool | 說明 / Description | Token |
|:---|:---|:---:|
| `bio_history_lookup` | 樣本分析歷史 / Sample analysis history | 0 |
| `bio_history_timeline` | 最近 N 天時間軸 / Recent N-day timeline | 0 |
| `bio_history_check` | 確認分析是否完成 / Check analysis completion | 0 |
| `bio_history_search` | L1 HNSW 語意搜尋 / L1 semantic search | 少量 |
| `bio_memory_query` | L1 快取完整報告 / L1 full report query | 少量 |
| `bio_memory_write` | 寫入 L1 快取 / Write L1 cache | 少量 |
| `bio_register_sample` | 登記新樣本 / Register new sample | 0 |
| `bio_read_report` | 讀取分析報告原文 / Read analysis report | 0 |
| `bio_artifact_search` | ENGRAM 3-way RRF 語意搜尋 | 少量 |
| `bio_artifact_summary` | ENGRAM artifact 摘要 / Artifact summary | 0 |
| `bio_check_l2_sufficiency` | 檢查 L2 就緒狀態 / Check L2 readiness | 0 |
| `bio_run_spatial_eda` | 空間 EDA 分析 / Spatial EDA analysis | 高 |
| `bio_run_bulk_eda` | Bulk RNA EDA 分析 | 高 |
| `bio_tool_health` | HELIX 工具健檢 / HELIX health report | 0 |
| `bio_impact` | 變更爆炸範圍評估 / Change blast radius | 0 |
| `bio_execute_code` ⚠️ | 沙盒 Python 執行（需 `MCP_ENABLE_DANGEROUS_TOOLS=true`） | 高 |

詳細設定見 [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md) 與 [docs/guides/MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md)。

*For detailed configuration, see [MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md) and [MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md).*

---

## 推理後端 / Inference Backends

| 後端 / Backend | 模型 / Model | 用途 / Use |
|:---|:---|:---|
| `--local`（預設） | Gemma 4 26B Vision IQ2_M | 離線、隱私、多模態圖片分析 |
| `--claude` | claude-sonnet-4-6 | 更強推理，需 ANTHROPIC_API_KEY |
| `--google` | gemini-2.0-flash | 需 GOOGLE_API_KEY |

---

## 排程任務 / Scheduled Tasks

| 腳本 | 時間 | 功能 |
|:---|:---|:---|
| `scheduler/backup_db.py` | 每日 02:00 | EXPORT DATABASE → `~/bio_db_backups/`（保留 7 天） |
| `scheduler/cleanup_l1_cache.py` | 每日 03:30 | 清理 L1 TTL 到期快取 |
| `scheduler/rebuild_hnsw.py` | 每週日 03:00 | 重建 HNSW + ENGRAM BM25 FTS 索引 |
| `scheduler/scan_new_samples.py` | 每 30 分鐘 | 掃描並登記新樣本 |
| `scheduler/helix_expire_snapshots.py` | 每週日 04:00 | HELIX 視覺快照遺忘曲線降採樣 |

launchd 範本在 [docs/launchd/](docs/launchd/)。

---

## 專案結構 / Project Structure

```text
bio_DB/                         ← Bio_PRISM 專案根目錄
│
├── 核心程式碼（git 追蹤）
│   ├── config/                 ← 集中設定（路徑、safe_write、db_utils）
│   ├── scripts/                ← 一次性 L3→L2 轉換 + Schema migration（v0–v19）
│   ├── analysis/               ← 分析函式庫（HELIX / ENGRAM / EDA / 快取）
│   │   ├── tool_registry.py    ← HELIX-Core（版本管理）
│   │   ├── artifact_registry.py← ENGRAM-Core（永久記憶）
│   │   └── tool_visualizer.py  ← HELIX-Vision（視覺快照）
│   ├── server/                 ← FastAPI Web UI + Agent + MCP Server
│   ├── scheduler/              ← 排程任務（備份/清理/重建/掃描）
│   ├── tests/                  ← 測試套件（35 files, 562 tests）
│   ├── gene_sets/              ← 路徑基因集 YAML
│   └── start_bioagent.sh       ← 一鍵啟動腳本
│
├── 文件（git 追蹤）
│   └── docs/
│       ├── images/             ← 架構圖（三層架構 / HELIX / ENGRAM）
│       ├── guides/             ← 操作指南（MCP / L3 Ingest / Data Integration）
│       ├── plans/              ← 設計計畫（plan_zh / plan / IMPLEMENTATION_PLAN）
│       ├── launchd/            ← macOS launchd plist 範本
│       └── logs/               ← 開發日誌（PROGRESS / execution_trace）
│
└── 本地數據目錄（.gitignore 排除）
    ├── bio_memory.duckdb       ← 主資料庫（*.duckdb）
    ├── silver/                 ← L2 Parquet 特徵存儲
    ├── gold/                   ← L1 語意快取 DuckDB
    ├── crc_visium_data/        ← L3 原始數據（~39 GB）
    ├── bulk_rna_data/          ← Bulk RNA 原始數據
    └── proteome_data/          ← Proteomics 數據
```

---

## 測試 / Testing

```bash
cd "$BIO_DB_ROOT"
.venv/bin/python -m pytest tests/ -v --tb=short
```

預期 / Expected: **562 tests collected**（少數 sandbox `FileNotFoundError` 為環境依賴，非邏輯失敗）

| 測試檔 | 數量 | 涵蓋範圍 |
|:---|:---:|:---|
| `test_tool_registry.py` | 56 | HELIX 版本管理、穩定化、churn |
| `test_fast_path.py` | 46 | Agent 快速路徑（SQL / timeline / sample list）|
| `test_artifact_registry.py` | 44 | ENGRAM 3-way RRF + Provenance |
| `test_phase4.py` | 35 | MCP Server stdio 工具 |
| `test_phase5.py` | 31 | Agent Loop + 沙盒執行 |
| `test_phase10.py` | 31 | MCP HTTP transport |
| `test_phase6.py` | 23 | Telegram Bot 指令與訊息分派 |
| `test_dashboard_actions.py` | 19 | 控制面板操作層 |
| `test_graduation.py` | 18 | Code Promotion 升格機制 |
| `test_impact.py` | 16 | HELIX blast radius 評估 |
| `test_artifact_resources.py` | 15 | MCP Resources artifact 交付 |
| `test_phase3.py` | 15 | L1 快取 + HNSW |
| `test_tool_visualizer.py` | 15 | HELIX 視覺快照 + 降採樣 |
| `test_phase2b.py` | 14 | 歷史查詢 + 報告生成 |
| `test_bulk_timeseries.py` | 13 | 時間序列均值 + log2 FC |
| `test_figure_cache.py` | 13 | MCP 圖片快取 + base64 剝離 |
| `test_report_reader.py` | 13 | 報告讀取 + 路徑沙盒 |
| `test_star_schema.py` | 13 | Star Schema views（throughput / stability）|
| `test_pathway_scoring.py` | 14 | ssGSEA / Z-score 路徑評分 |
| `test_bulk_deg.py` | 11 | DEG + Volcano plot |
| `test_validate_inference_backend.py` | 10 | 推理後端 fail-fast 驗證 |
| `test_tool_search.py` | 10 | 工具語意搜尋 |
| `test_enrichment.py` | 10 | ORA 富集分析 |
| `test_mcseg_quality.py` | 10 | MCseg 品質評估 |
| `test_playbook.py` | 10 | Playbook 工具展開 |
| 其他 | 57 | report_page (9) / bulk_heatmap (8) / dashboard (8) / bulk_eda (7) / backfill_tool_id (6) / handle_message_fast_path (6) / unique_constraint (4) / init_db (4) / spatial_ingest (3) / google_backend_multi_round (2) |

---

## 文件索引 / Documentation

| 文件 | 說明 |
|:---|:---|
| [CLAUDE.md](CLAUDE.md) | 專案憲法（開發規範 + Schema + 路徑） |
| [SETUP.md](SETUP.md) | 詳細環境安裝手冊 |
| [docs/plans/plan_zh.md](docs/plans/plan_zh.md) | 完整系統設計（中文，18 章） |
| [docs/plans/plan.md](docs/plans/plan.md) | 完整系統設計（英文） |
| [docs/logs/PROGRESS.md](docs/logs/PROGRESS.md) | 實作進度封存 |
| [docs/guides/DATA_INTEGRATION_GUIDE.md](docs/guides/DATA_INTEGRATION_GUIDE.md) | 跨專案數據整合指南 |
| [docs/guides/L3_DATA_INGEST_GUIDE.md](docs/guides/L3_DATA_INGEST_GUIDE.md) | 新增 L3 樣本操作指南 |
| [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md) | MCP stdio 設定（Claude Code / Antigravity） |
| [docs/guides/MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md) | MCP HTTP transport 說明 |
| [docs/guides/STAR_SCHEMA.md](docs/guides/STAR_SCHEMA.md) | Star Schema views 設計與範例 |

---

## 下一步 / Roadmap

```text
本機可做 / Local
    ├── 端對端測試：填入 ANTHROPIC_API_KEY，驗證 Claude 後端切換
    └── launchd 排程安裝（launchctl load × 5，plist 範本在 docs/launchd/）

接著 / Next（需 Linux 伺服器）
    ├── 路徑設定遷移（config/settings.py）
    └── Docker 沙盒替換 code_executor.py（生產安全隔離）

之後 / Later
    └── 5 位實驗室成員實際使用驗證
```

---

## 用 LLM 擴充工具箱 / Extend the Toolbox with LLM

Bio_PRISM 最核心的設計理念：**讓 LLM 自己擴充自己**。

`CLAUDE.md` 是整個平台的「憲法」，完整定義了每一條規範與擴充流程。LLM 讀懂它之後，只需要你的一句話就能自主完成所有修改：

*The core design principle of Bio_PRISM: **the LLM extends itself**. `CLAUDE.md` is the project constitution. Once the LLM reads it, one sentence from you is enough to make it do all the work:*

#### 情境一：新增全新分析領域 / Add a brand-new analysis domain

```
幫我新增 scRNA-seq 分析工具，支援 clustering、marker gene 偵測、UMAP 視覺化
```

LLM 自動產出：playbook → 分析函數 → MCP 接線 → HELIX 版本登記，全新工具立即可用。

*LLM outputs: playbook → analysis function → MCP wiring → HELIX registration. New tool ready immediately.*

#### 情境二：擴充既有工具的能力 / Extend an existing tool

```
幫 bio_run_deg 加入互動式 volcano plot，並支援批次校正（ComBat）
```

LLM 自動修改既有函數、更新 playbook 步驟、bumping 版本號、重新呼叫 `register_tool()`，HELIX 自動記錄版本差異。

*LLM modifies the existing function, updates the playbook, bumps the version, re-registers with HELIX. Version delta is automatically tracked.*

---

兩種情境 LLM 都會完成以下四步，不需要人工介入：

*Both scenarios follow the same four steps — no manual intervention needed:*

| 步驟 | 新增工具 | 擴充既有工具 |
|------|---------|------------|
| **1. Playbook** | 建立 `playbooks/<domain>.md` | 更新既有 playbook 步驟 |
| **2. 分析函數** | 建立 `analysis/<module>.py` | 修改既有函數，新增參數或圖表 |
| **3. MCP 接線** | 新增工具至 `bio_memory_server.py` | 更新工具的 `inputSchema` 描述 |
| **4. HELIX 登記** | `register_tool()` 版本 1.0.0 | `register_tool()` bump 版本號 |

完成後，工具立即可被任何 MCP 客戶端（Claude Code / Antigravity / Web UI）呼叫，所有分析結果自動歸入 ENGRAM 永久記憶。

*Once done, the tool is immediately callable from any MCP client, and all results are automatically archived into ENGRAM.*

### 現有工具參考 / Reference Implementations

| MCP 工具 | Playbook | 分析模組 |
|---------|----------|---------|
| `bio_run_bulk_eda` | `playbooks/bulk_rnaseq.md` | `analysis/bulk_eda.py` |
| `bio_run_deg` | `playbooks/bulk_rnaseq.md` | `analysis/bulk_eda.py` |
| `bio_run_spatial_eda` | `playbooks/spatial_visium.md` | `analysis/spatial_eda.py` |

---

## 貢獻 / Contributing

歡迎 PR 與 Issue！請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。

*PRs and Issues welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.*

---

## 授權 / License

MIT License — © 2026 詹麒儒 (Chan Chi Ru). See [LICENSE](LICENSE).
