# Evo_PRISM

**Evolutionary Platform for Runtime Intelligence & Semantic Memory**

> **語言：** [English](README.md) · 繁體中文

[![CI](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/ddmanyes/Evo_PRISM/releases/tag/v.0.1)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

[為什麼選擇 Evo_PRISM](#為什麼選擇-evo_prism) · [快速開始](#快速開始) · [系統架構](#系統架構) · [MCP 工具](#mcp-工具目錄) · [基準測試](#基準測試摘要) · [文件](#文件索引)

Evo_PRISM 是一套 local-first 執行環境，透過 [Model Context Protocol（MCP）](https://modelcontextprotocol.io/)把自然語言需求連接到具版本管理的分析工具與可搜尋、可追溯的永久記憶。

兩個核心子系統讓執行環境能長期維持可信度：**HELIX** 管理工具探索、版本、健康度與人工審核後的晉升；**ENGRAM** 歸檔分析產物，並保留產物與工具版本之間的來源關係。此 repo 以生物資訊作為旗艦展示，涵蓋空間轉錄體、bulk RNA-seq、scRNA-seq 與 MCseg 輔助空間分析流程。

## 為什麼選擇 Evo_PRISM？

LLM 分析流程常在第一次成功執行後開始失控：生成的程式碼消失、方法逐漸漂移、產物難以搜尋，最後也無法確認結果究竟由哪一版工具產生。Evo_PRISM 把這些問題視為執行環境與記憶系統的責任，而不是只靠 prompt 約束。

| 失效模式 | Evo_PRISM 的處理方式 |
| :--- | :--- |
| 對話結束後，生成的分析程式碼隨即消失 | HELIX 登記可重用工具並記錄版本血緣 |
| 方法或實作錯誤產生外觀合理的結果 | 健康度與失敗診斷讓工具行為可被檢查 |
| 不同人或不同時間執行同一分析卻得到不同結果 | 分析歷史把結果、參數與工具版本相互連結 |
| 分析產物散落在不同檔案與目錄 | ENGRAM 登記產物，提供精確與語意檢索 |
| 相似需求反覆執行昂貴計算 | L1 快取與歷史查詢重用已驗證結果 |

## 核心能力

- **MCP 原生介面**：以 stdio 或 HTTP 連接相容的 Agent 與 IDE。
- **三層資料路徑**：從不可變原始資料（L3），到結構化特徵（L2）與語意快取（L1）。
- **HELIX 工具生命週期**：支援語意探索、版本追蹤、健康監測與人工審核晉升。
- **ENGRAM 產物記憶**：結合 Exact SQL、HNSW 向量搜尋、BM25 全文搜尋與 RRF 排序融合。
- **版本感知影響分析**：找出工具變更可能影響的歷史產物。
- **生物資訊工作流**：涵蓋樣本歷史、空間與 bulk RNA 分析、差異表現、富集分析、熱圖、細胞註解及 MCseg 整合。

## 快速開始

此公開 checkout 目前可驗證的路徑是手動建立 Python 環境。建議使用 Python 3.11；專案支援 Python 3.10 以上版本。

### 1. 安裝

```bash
git clone https://github.com/ddmanyes/Evo_PRISM.git
cd Evo_PRISM

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install "uv>=0.4,<1"
uv sync --no-install-project

cp .env.example .env
python scripts/00_init_db.py
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    python "$script"
done
```

若 repo 位於 ExFAT 或同步資料夾，請把虛擬環境建立在本機 APFS/ext4 磁碟，再以 `.venv` symlink 連回專案。

### 2. 設定 Embedding

預設 provider 是本機 [llama.cpp](https://github.com/ggml-org/llama.cpp) server，並使用 [`bge-m3-Q8_0.gguf`](https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF)：

```bash
~/llama.cpp/build/bin/llama-server \
  --model ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding \
  --port 8081 \
  --ctx-size 8192 \
  --n-gpu-layers 99
```

確認服務已經就緒：

```bash
curl http://localhost:8081/health
```

系統也支援 Google 與 OpenAI embedding provider。請在 `.env` 設定對應 provider 與 API key；可用變數請見 [`.env.example`](.env.example)。

### 3. 連接 MCP 客戶端

使用 stdio transport 時，先複製設定範本，再替換其中所有絕對路徑佔位文字：

```bash
cp .mcp.json.example .mcp.json
```

各客戶端設定方式請見 [MCP JSON 設定指南](docs/guides/MCP_JSON_SETUP.md)。若要獨立提供 HTTP transport：

```bash
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

### 4. 選用 Web UI

在 `.env` 設定 `INFERENCE_BACKEND` 與對應 API key，再啟動相符的後端：

```bash
VENV_PYTHON="$PWD/.venv/bin/python" bash start_bioagent.sh --claude
# 其他選項：--google 或 --local
```

看到 readiness 訊息後，開啟 <http://localhost:8000>。本機模式還需要設定 [`.env.example`](.env.example) 列出的視覺模型路徑。

### Docker 目前狀態

Repo 內包含 [`Dockerfile`](Dockerfile) 與 [`docker-compose.yml`](docker-compose.yml)，但目前 Compose entrypoint 會啟動 stdio MCP，而 Compose 檔案暴露的是 Web UI 與 HTTP ports。在 transport wiring 完成對齊前，不應把 `docker compose up` 視為已驗證的完整服務快速啟動方式。

更多環境細節、其他 embedding provider 與 HPC／Singularity 說明，請繼續閱讀 [SETUP.md](SETUP.md)。

## 系統架構

### 三層資料與查詢流程

![Evo_PRISM 三層資料與查詢架構](docs/images/figure_1_system_arch.png)

Evo_PRISM 把持久資料、衍生特徵與快速檢索分成三層：

| 層級 | 角色 | 常見內容 |
| :---: | :--- | :--- |
| L3 Bronze | 不可變來源資料 | FASTQ、SpaceRanger 輸出、來源影像 |
| L2 Silver | 結構化特徵庫 | DuckDB tables 與 Parquet features |
| L1 Gold | 低延遲檢索 | 精確查詢與 HNSW 語意快取 |

每次請求會先檢查可重用結果與已登記工具，只有未命中的需求才進入冷啟動執行路徑。新結果會回流到記憶層，而不是留在不相連的檔案中。

### HELIX 工具生命週期

![HELIX 工具探索、健康監測、穩定化與記憶生命週期](docs/images/figure_2_system_arch.png)

HELIX 會在生成新程式碼前先搜尋 active tool registry。命中的工具可直接執行；未命中時才進入沙盒中的 ad-hoc execution。被重複使用的候選工具與不健康工具會進入 supervised stabilization，而晉升至 `analysis/` 前必須通過人工審核。

目前設計使用 `0.45` 作為語意探索門檻，並以 `f_promote ≥ 3.0` 作為晉升訊號。健康觀測、版本變更與視覺快照都會保留，供後續診斷使用。

### ENGRAM 產物記憶

![ENGRAM 產物登記、檢索與血緣架構](docs/images/figure_3_system_arch_1.png)

ENGRAM 為報告、圖表、資料表及其他分析輸出登記語意向量與工具版本來源。檢索會結合結構化查詢、語意搜尋及 RRF 排序融合。產物之間的關係形成 impact graph，可在 HELIX 工具變更後追蹤哪些歷史結果可能已經過時。

HELIX 與 ENGRAM 因此形成閉合迴路：工具演化更新 provenance，provenance 找出受影響產物，累積的歷史則改善後續檢索與審查。

## MCP 工具目錄

Server 共宣告 **36 個工具**，預設對外提供 **35 個**；只有設定 `MCP_ENABLE_DANGEROUS_TOOLS=true` 後，才會顯示 `bio_execute_code`。

| 分組 | 工具 |
| :--- | :--- |
| 歷史與樣本 | `bio_history_lookup`、`bio_history_timeline`、`bio_history_check`、`bio_history_search`、`bio_lookup_sample`、`bio_register_sample`、`bio_sample_list`、`bio_sample_compare` |
| 記憶與產物 | `bio_memory_query`、`bio_memory_write`、`bio_artifact_search`、`bio_artifact_summary`、`bio_get_artifact`、`bio_get_figure`、`bio_read_report` |
| 探索與治理 | `bio_find_tool`、`bio_tool_health`、`bio_failure_summary`、`bio_impact`、`bio_get_playbook` |
| 核心分析 | `bio_check_l2_sufficiency`、`bio_run_spatial_eda`、`bio_run_bulk_eda`、`bio_run_deg`、`bio_run_enrichment`、`bio_run_heatmaps` |
| MCseg 與後處理 | `bio_run_mcseg_roi`、`bio_run_mcseg_fullslide`、`bio_run_mcseg_qc`、`bio_compute_crc_metrics`、`bio_get_marker_genes`、`bio_relabel_clusters`、`bio_run_celltypist`、`bio_run_mcseg_merge`、`bio_export_loupe` |
| 選用高權限 | `bio_execute_code` |

MCseg 執行工具需要此 repo 未包含的外部 MCseg backend。後處理工具也需要相容的上游結果；CellTypist 支援另有選用 dependency。

### 安全與生命週期預設值

| 控制項 | 預設行為 |
| :--- | :--- |
| 動態執行 Python | 除非設定 `MCP_ENABLE_DANGEROUS_TOOLS=true`，否則不會對外顯示 |
| 工具晉升 | 候選工具移入正式 analysis library 前必須通過人工審核 |
| HTTP 驗證 | 可透過 `MCP_AUTH_TOKEN` 設定 bearer token |
| 高成本及使用 embedding 的工具 | 受 server request rate limiter 保護 |
| 結果溯源 | Analysis 與 artifact records 保留工具版本關係 |

## 基準測試摘要

![Evo_PRISM 語意搜尋飛輪基準測試](docs/images/Figure8_Flywheel_Evolution.png)

Repo 追蹤的 R10 圖表顯示：語意搜尋命中率由 **2 個 active tools 時的 20%**，提升至 **25 個工具時的 100%**；相同 catalog sizes 下，HNSW 平均查詢延遲由 **1.40 ms** 變為 **1.96 ms**。這些數值是專案回報的 benchmark 結果；論文來源與原始 benchmark bundle 並未包含在此公開 checkout 中。

## 專案結構

```text
Evo_PRISM/
├── analysis/      # 分析函式、HELIX registry、ENGRAM 與檢索
├── server/        # MCP server、agent adapters 與 Web UI
├── store/         # DuckDB／PostgreSQL 儲存後端
├── config/        # 設定、路徑與資料庫 utilities
├── scripts/       # Schema migration、ingestion、export 與維護工具
├── scheduler/     # 備份、清理、index 與掃描工作
├── playbooks/     # 可重用分析程序
├── gene_sets/     # 範例 pathway 定義
└── docs/guides/   # 安裝、整合、transport 與維運指南
```

Runtime database、原始輸入、feature stores、models 與生成結果都屬於本機資料，因此刻意排除在版本控制之外。

## 文件索引

| 指南 | 用途 |
| :--- | :--- |
| [SETUP.md](SETUP.md) | 手動安裝、環境變數、Singularity 與 client 設定 |
| [MCP JSON 設定](docs/guides/MCP_JSON_SETUP.md) | stdio client 設定與路徑處理 |
| [MCP HTTP 指南](docs/guides/MCP_HTTP_GUIDE.md) | HTTP transport、headers、初始化與 request 範例 |
| [資料整合指南](docs/guides/DATA_INTEGRATION_GUIDE.md) | 匯入 bulk RNA-seq、proteomics 與其他資料 |
| [L3 資料匯入指南](docs/guides/L3_DATA_INGEST_GUIDE.md) | 登記 sample 並把 L3 source 轉成 L2 features |
| [排程任務](docs/guides/SCHEDULED_TASKS.md) | 備份、cache 清理、HNSW 重建與 launchd 範例 |
| [Star schema](docs/guides/STAR_SCHEMA.md) | Throughput 與工具穩定度 operational views |
| [Windows 安裝](docs/guides/WINDOWS_SETUP.md) | Windows 原生環境與服務設定 |

## 參與貢獻

歡迎提出 issue 與 pull request。送出變更前，請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 授權

MIT License — © 2026 詹麒儒（Chan Chi Ru）。詳見 [LICENSE](LICENSE)。
