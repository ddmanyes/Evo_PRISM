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

### 前置需求 / Prerequisites

- macOS（測試平台）或 Linux（生產部署）
- Python ≥ 3.10
- [uv](https://github.com/astral-sh/uv) 套件管理器
- [llama.cpp](https://github.com/ggml-org/llama.cpp) 已編譯（`~/llama.cpp/build/bin/llama-server`）
- 模型檔案（放於 `~/`）：
  - `gemma-4-26B-A4B-it-UD-IQ2_M.gguf` — 推理引擎 / Inference engine
  - `mmproj-F16.gguf` — 視覺投影層 / Vision projector
  - `bge-m3-Q8_0.gguf` — Embedding（605 MB）

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
# 填入 API keys / Fill in API keys

# 4. 初始化資料庫 / Initialize database
.venv/bin/python scripts/00_init_db.py

# 5. 執行所有 Schema migration（v9 → v19）
for script in scripts/[12][0-9]_migrate_schema_*.py; do
    .venv/bin/python "$script"
done
```

### 啟動 / Launch

```bash
bash start_bioagent.sh           # 互動式選擇後端 / Interactive backend select
bash start_bioagent.sh --claude  # Claude API（需 ANTHROPIC_API_KEY）
bash start_bioagent.sh --google  # Google Gemini API（需 GOOGLE_API_KEY）
bash start_bioagent.sh --local   # 本機 Gemma 4 Vision（需 ~16 GB RAM）
```

啟動後開啟瀏覽器：**<http://localhost:8000>**

---

## 測試資料庫 / Test Database

本專案的測試資料（L2 Parquet + DuckDB + 基因集）**不包含在 git repository** 中，因為原始生信資料體積龐大（~39 GB）且含實驗室內部數據。

*The test dataset (L2 Parquet + DuckDB + gene sets) is **not included** in this repository due to large file sizes (~39 GB) and proprietary lab data.*

如需取得測試資料壓縮包以在本機驗證系統，請聯絡作者：

*To obtain the test data archive for local evaluation, please contact the author:*

> **✉️ 請聯絡作者取得下載連結 / Ask the author for download link**
>
> 詹麒儒 (Chan Chi Ru) — u9013039@gmail.com

測試資料包含 / Test archive includes:
- `bio_memory.duckdb` — 已初始化的主資料庫（Schema v19 + 範例 sample 登記）/ Pre-initialized database with sample registry
- `silver/spatial_counts_crc_official_v4_8um/` — CRC Visium HD 8µm L2 Parquet（416 MB，215M nonzero）
- `gene_sets/hair_follicle.yaml` — 路徑基因集範例 / Example pathway gene set

預期測試結果 / Expected test results:
```bash
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
│   ├── tests/                  ← 測試套件（293 tests）
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
| `test_phase2b.py` | 14 | 歷史查詢 + 報告生成 |
| `test_pathway_scoring.py` | 14 | ssGSEA / Z-score 路徑評分 |
| `test_bulk_deg.py` | 11 | DEG + Volcano plot |
| `test_validate_inference_backend.py` | 10 | 推理後端 fail-fast 驗證 |
| `test_tool_search.py` | 10 | 工具語意搜尋 |
| `test_enrichment.py` | 10 | ORA 富集分析 |
| `test_mcseg_quality.py` | 10 | MCseg 品質評估 |
| `test_playbook.py` | 10 | Playbook 工具展開 |
| 其他 | ~69 | bulk_eda / heatmap / spatial / init / dashboard / migration |

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

## 貢獻 / Contributing

歡迎 PR 與 Issue！請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。

*PRs and Issues welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.*

---

## 授權 / License

MIT License — © 2026 詹麒儒 (Chan Chi Ru). See [LICENSE](LICENSE).
