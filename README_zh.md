# Evo_PRISM (Evo-PRISM)

**自進化執行期智慧與語意記憶平台**

> **語言：** [English](README.md) · 中文版

Evo_PRISM 是一個通用、自進化（Self-Evolving）的 LLM-Agent 工具鏈與執行期智慧語意記憶系統。系統基於 **LLM Agent + MCP** 設計，採用創新的三層架構（L3 原始數據 → L2 結構化特徵 → L1 語意快取），搭配 **HELIX**（工具健康進化與版本追蹤迴路）與 **ENGRAM**（分析產出與執行脈絡永久記憶），能讓任何人在無需寫程式的情況下透過自然語言與複雜的分析工具鏈和資料庫互動。每份分析結果永久歸檔、可語意搜尋，並強制關聯至產生它的軟體版本，實現無痛的自適應優化與代碼自動晉升（Promotion）。

為了展示本平台在處理高難度資料溯源、超高複雜度分析工具管理以及大數據特徵提煉時的真實實戰威力，本專案提供了一個**首屈一指的垂直領域旗艦展示：生物資訊分析模組 (Bioinformatics Showcase Module)**。該模組完整接入了空間轉錄組 (Spatial Transcriptomics)、Bulk RNA-seq、scRNA-seq、蛋白質組學 (Proteomics) 等前沿生命科學數據的分析能力。

[![CI](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.5.2-yellow)](https://duckdb.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![Docker](https://img.shields.io/badge/Docker-ddmann375000%2Fevo--prism%3A0.1.0-blue)](https://hub.docker.com/r/ddmann375000/evo-prism)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

---

## 為什麼是 Evo_PRISM？

Evo_PRISM 克服了現代 LLM 應用與工具開發的核心痛點，使其在複雜任務分析（如生物資訊工作流）中具備自我進化的能力。

現代 LLM 驅動的分析工作流面臨三大命名失效模式，傳統工具均無法偵測：

- **程式碼溯源真空（Code Provenance Vacuum）** — AI 生成的分析腳本在對話結束後即消失；磁碟上留有結果，卻無法追溯到產生它的程式碼、參數或套件版本。
- **分析方法靜默失效（Silent Methodological Failure）** — 錯誤的正規化方式、過時的統計假設或稀疏矩陣數值誤差能產出表面合理的輸出，但不觸發任何警示，直接污染下游科學結論。
- **分析方法漂移（Methodological Drift）** — 同一份原始數據在不同時間或不同人員分析時，因閾值或工具版本不一致而產生微妙差異，令可重現性稽核無從著手。

Evo_PRISM 從架構層面系統性解決三者：

| 痛點                                | Evo_PRISM 的解法                                                                    |
| :---------------------------------- | :---------------------------------------------------------------------------------- |
| 🔁 每次提問都要重複執行昂貴工具     | **L1 語意快取**：相似問題毫秒級自動回答，極大節省資源                         |
| 📂 工具輸出結果散落各地、無法回溯   | **ENGRAM 永久記憶**：每次產出自動歸檔，支援 Hybrid 3-way RRF 語意搜尋         |
| 🐛 程式修改後不知道是哪版產出結果   | **HELIX 版本追蹤**：工具版本與分析歷史強制關聯，拒絕版本漂移                  |
| 🧑‍💻 非工程師無法直接調用複雜工具 | **自然語言 Agent**：支援 Web UI、MCP、Telegram，以純自然語言呼叫工具          |
| 🛠️ 靜態工具鏈無法自動適應新需求   | **自進化 Code Promotion**：自動偵測熱點，重用代碼達三次即可自動升格為永久工具 |

---

## 系統架構

### 三層資料架構

![Evo_PRISM 三層架構](docs/images/architecture_three_layer.png)

| 層 |  名稱  | 說明                                         |
| :-: | :----: | :------------------------------------------- |
| L3 | Bronze | 不可變原始數據（FASTQ, SpaceRanger outs）    |
| L2 | Silver | DuckDB + Parquet 結構化特徵（30B → 416 MB） |
| L1 |  Gold  | HNSW 語意快取，TTL 7 天                      |

### HELIX — 工具健康進化迴路

![HELIX 簡單流程圖](docs/images/helix_flow_simple.png)

![HELIX 架構迴路](docs/images/helix_architecture_loop.png)

**HELIX** 負責追蹤所有分析工具的版本、熱區偵測、複雜度量測（Radon CC）與穩定化重構，確保 Agent 呼叫的永遠是健康版本。

**Code Promotion 刻意保留人工確認關卡。** 當臨時腳本達到晉升閾值（重用 ≥ 3 次，`fpromote ≥ 3.0`），LLM 審查後產出草稿，但需由管理員執行 `approve_candidate()` 確認後才搬入正式 `analysis/` 目錄。這個設計刻意避免「LLM 生成 → LLM 審核 → 自我驗證」的封閉循環。`UserApproval` 訊號（`+1` / `0` / `-1`）讓操作者能主動壓制高重用但方法論有誤的腳本，阻止其晉升。

#### HELIX 記憶系統

HELIX 採用**雙軌記憶機制**，讓 Agent 在每次重新診斷時都能回溯完整的工具演化脈絡。

**① HELIX-Vision — VLM 視覺記憶**

每次穩定化迭代（Stabilization Iteration）完成後，系統自動渲染一張 **640×640 PNG 快照**，以四象限圖像編碼當次診斷的完整脈絡，並以 base64 寫入 `tool_stabilization_log.diagnosis_img`：

| 象限         | 內容                              |
| :----------- | :-------------------------------- |
| 原始碼熱圖   | 逐行 token 計數，視覺化複雜度熱區 |
| 複雜度儀表板 | Radon 循環複雜度（CC）當前量測值  |
| 修訂時間軸   | `tool_change_log` 版本指紋歷程  |
| 診斷文字     | AI 評估摘要與行動計畫             |

每張快照約佔 **~100 VLM vision tokens**，比等效純文字壓縮約 10×，讓 VLM 能以極低 token 成本跨 session 回讀歷史快照。

**② Ebbinghaus 遺忘曲線**

`scheduler/helix_expire_snapshots.py`（每週日 04:00）對舊快照進行漸進降採樣，模擬生物記憶衰減——空間佈局保留、細部文字隨時間淡出：

```text
迭代關閉後 0–180 天 ：640×640  ~100 VLM tokens  完整解析度
迭代關閉後 180–365 天：320×320   ~25 VLM tokens  50% 降採樣
迭代關閉後 > 365 天  ：160×160    ~6 VLM tokens  25% 降採樣
```

確保近期診斷精準，歷史記憶則以最低成本保留空間結構，供 Agent 跨 session 快速重構工具演化全貌。

### ENGRAM — 分析產出永久記憶

![ENGRAM 架構](docs/images/engram_architecture.png)

**ENGRAM** 將每次分析產出（CSV、Parquet、圖片、報告）永久歸檔，支援 Hybrid 3-way RRF 語意搜尋（Exact SQL + HNSW + BM25 FTS），並與 HELIX 工具帳本關聯防止版本漂移。

### HELIX × ENGRAM 閉迴路 — 爆炸範圍評估（Blast Radius）

兩個子系統形成**閉合回路**：

```text
HELIX 偵測到工具版本更新
        ↓
爆炸範圍查詢走訪 ENGRAM 血緣圖
        ↓
系統識別哪些歷史分析結果由舊版工具產出、可能已失效
        ↓
隨 ENGRAM 積累更豐富的血緣元資料，精準率自動提升（71.4% → 83.3%）
```

這正是 Snakemake、Nextflow、DVC 等傳統工作流管理工具做不到的事：它們只能偵測「輸入檔案是否變動」，**無法偵測「程式碼邏輯是否改變」**。當某工具的正規化方法被修正，Evo_PRISM 能回溯標記所有由舊版產出的歷史結果——無需重新執行任何分析。呼叫 `bio_impact` 即可查詢任意工具變更的當前爆炸範圍。

### Agent 決策流程

```text
提問
 ├─ Step 1  SQL 精確比對（0 token，< 1 秒）         ← 做過？直接回傳
 ├─ Step 2  HNSW 語意搜尋（cosine ≥ 0.88）         ← 問過類似？快取回傳
 ├─ Step 3A 標準分析工具（L2 Parquet 就緒）
 │           └─ bio_find_tool：0 LLM Token 工具語意探索 → 生成新代碼前先重用既有函數
 ├─ Step 3B Code Promotion 重用（曾生成過？）
 └─ Step 3C 全新程式碼生成（沙盒執行 + 失敗重試）
               └─ 執行成功 → 存入歷史 → 下次可被 3B 查詢
               └─ 重用 ≥ 3 次 → 升格為 3A 永久工具（人工確認）
```

---

## 實證效能與基準測試

Evo_PRISM 已在多種高複雜度生命科學計算場景中通過嚴格的定量驗證，確立了其高可擴展性、數據可靠性與卓越的 Token 經濟效益。

### 1. 工具庫自強化飛輪（R10 飛輪）
隨著臨時程式碼經由沙盒驗證與 Code Promotion 自動晉升並積累至工具庫，系統對後續新分析意圖之語意命中率呈現**顯著之單調躍升**，同時保持極低之亞毫秒級檢索延遲：
- **語意命中率之單調收斂：** 語意搜尋命中率（Cosine 相似度 $\ge 0.45$）自早期階段之 **20.0%**（2 項工具）單調遞增至 **100.0%**（25 項完整工具），實證了「工具晉升 $\rightarrow$ 記憶積累 $\rightarrow$ 重用 $\rightarrow$ 再晉升」之自強化飛輪效應。
- **低延遲高擴展性：** 得益於 DuckDB 本機 HNSW 向量索引，平均查詢延遲由 2 項工具下之 **1.40 ms** 極緩上升至 25 項工具下之 **1.96 ms**，P95 延遲恆低於 **2.0 ms**（參見論文 **[圖 8](docs/paper/figures/figure_r10_flywheel.png)**）。

### 2. 高通量空間組學數據攝入（R5 alt 基準測試）
針對 10x Genomics Visium HD 空間轉錄組數據，執行端對端 Ingestion 工作流（Stages 0–7：含 Cellpose 細胞分割、轉錄本空間歸屬計數、下游 Scanpy 聚類與 GeoJSON 導出），於邊緣工作站展現極高之計算效率（參見補充資料 **[Table S16](docs/paper/supplementary.md#table-s16-visium-hd-ingestion-throughput-and-resource-profiling-benchmark-r5-alt)**）：
* **攝入通量：** 於 **104.5 秒**內完成 4.79 GB 影像、1,493 個高精度細胞與 13.4k 基因之完整處理，平均通量達 **14.3 cells/sec**。
* **極低存儲開銷：** 細胞級高精度 H5AD 矩陣與 GeoJSON 邊界文件之磁碟佔用被壓縮至 **2.08 MB 至 5.85 MB** 之間，便於在邊緣端就地進行大規模數據重現。

### 3. 自進化精準度與健康度自癒
* **爆炸範圍精準收斂（ENGRAM 飛輪）：** 藉由 Recursive SQL CTE 遞迴血緣走訪，影響精準率自中繼元資料稀疏期之 **71.4%** 自主收斂至飽和期之 **83.3%**，且全程維持 **100% 召回率**與零人工干預（參見補充資料 **[Table S15](docs/paper/supplementary.md#table-s15-ground-truth-oracle-query-set-construction-and-annotation-protocol)**）。
* **程式碼健康度自癒（HELIX 飛輪）：** 當頻繁修改導致技術債累積時，工具庫平均 `HealthScore` 自 **0.61** 之警告狀態自動重構回升至 **0.94**，成對工具之 McCabe 循環複雜度中位數同步下降 **80%**。

### 4. Token 經濟與系統穩健性
* **Context Token 節省：** Figure Cache 於 MCP 邊界自動剝離高體積之多模態 base64 圖片，達成 **98.2%** 之傳遞 Token 節省率，免於污染 LLM 之上下文視窗。
* **100% 測試通過率：** 系統內建之 **679 項自動化測試**（涵蓋 HELIX 版本管理、ENGRAM 語意搜尋與沙盒安全等 49 個測試檔）達成 **100.0% 通過率**（674 passed, 5 skipped），確保系統之工業級可靠度。

---

## 快速開始

先選擇您的使用路徑，再依對應步驟操作：

| 我想要…                                | 建議路徑                                                            |  難度  |
| :-------------------------------------- | :------------------------------------------------------------------ | :----: |
| 第一次嘗試，想最快跑起來                | 🐳[Docker Compose（推薦新使用者）](#-路徑一docker-compose推薦新使用者) | ★☆☆ |
| 在 Claude Code / IDE 裡直接呼叫分析工具 | 💡[手動安裝 + MCP](#-路徑二手動安裝--mcp連接-ide--cli)                 | ★★☆ |
| 需要完整網頁對話介面或完全離線推理      | 🌐[手動安裝 + Web UI](#-路徑三手動安裝--web-ui對話介面)                | ★★★ |

---

### 🐳 路徑一：Docker Compose（推薦新使用者）

無需手動建立 Python 環境——所有服務都在容器內執行。

**事前準備：**

- [Docker Engine ≥ 24 + Docker Compose v2](https://docs.docker.com/get-docker/)
- `bge-m3-Q8_0.gguf`（605 MB 語意搜尋模型，從 [HuggingFace BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) 下載 Q8_0 量化版）

```bash
# 1. 把下載好的模型放入專案的 models/ 資料夾
mkdir -p models
# 將 bge-m3-Q8_0.gguf 移入 models/

# 2. 複製環境設定（MCP 模式不需填入 API Key）
cp .env.example .env

# 3. 啟動所有服務（首次會下載映像，約 343 MB）
docker compose up -d

# 4. 初始化資料庫（只需執行一次）
docker compose exec evo-prism python scripts/00_init_db.py
```

完成！MCP HTTP 就緒：**<http://localhost:8080>** · Web UI：<http://localhost:8000>

> 或直接使用預建映像：`docker pull ddmann375000/evo-prism:0.1.0`
>
> HPC / Singularity 叢集用戶請見 [SETUP.md](SETUP.md#singularity)。

---

### 💡 路徑二：手動安裝 + MCP（連接 IDE / CLI）

> 此路徑讓您在 **Claude Code CLI** 或 **Antigravity IDE** 中，直接以自然語言呼叫 25 個分析工具，不需要另外開瀏覽器。

**事前準備：**

- Python ≥ 3.10（建議 3.11）
- [uv](https://github.com/astral-sh/uv) 套件管理器（`pip install uv`）
- [llama.cpp](https://github.com/ggml-org/llama.cpp) 已編譯（執行本機 embedding 服務）
- `bge-m3-Q8_0.gguf`（605 MB，放於 `~/llama.cpp/models/`）
- Anthropic API Key

**安裝步驟：**

```bash
# 步驟 1：建立虛擬環境
# 注意：若專案放在 ExFAT 外接硬碟或 Google Drive 同步資料夾，
# Python venv 必須建在本機（APFS/ext4）才能正常運作，再用 symlink 連回來
python3 -m venv ~/.venvs/hermes-bio-memory
ln -s ~/.venvs/hermes-bio-memory .venv

# 步驟 2：安裝所有 Python 套件
uv sync --no-install-project

# 步驟 3：設定環境變數
cp .env.example .env
# 開啟 .env 填入：ANTHROPIC_API_KEY=sk-ant-...

# 步驟 4：建立資料庫（只需執行一次）
.venv/bin/python scripts/00_init_db.py

# 步驟 5：執行所有資料庫版本更新（只需執行一次）
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    .venv/bin/python "$script"
done
```

**啟動 Embedding Server（必要）：**

```bash
# Embedding Server 負責將查詢轉為向量，提供語意搜尋能力
# & 表示在背景執行，終端機可繼續使用
~/llama.cpp/build/bin/llama-server \
  -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99 &

# 確認已成功啟動（回應 {"status":"ok"} 表示正常）
curl http://localhost:8081/health
```

**連線 MCP 客戶端：**

- **Claude Code CLI**：專案根目錄已有 `.mcp.json`，在此目錄執行 `claude` 即自動載入所有工具
- **Antigravity IDE**：Settings → MCP Servers 新增條目，詳見 [MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md)
- **外部 HTTP 客戶端**：`python server/bio_memory_server.py --transport http --port 8082`

---

### 🌐 路徑三：手動安裝 + Web UI（對話介面）

> 完成路徑二的安裝步驟後，直接執行：

```bash
bash start_bioagent.sh --claude  # 使用 Claude API（推薦，需 ANTHROPIC_API_KEY）
bash start_bioagent.sh --google  # 使用 Gemini API（需 GOOGLE_API_KEY）
bash start_bioagent.sh --local   # 完全本機推理（需額外下載 Gemma 4 26B 模型，~16 GB RAM）
```

開啟瀏覽器：**[http://localhost:8000](http://localhost:8000)**

---

### 🧪 驗證安裝是否成功

專案內建測試數據集，無需下載任何外部數據就能執行：

```python
from analysis.bulk_eda import run_deg_analysis

result = run_deg_analysis(
    counts_path="tests/fixtures/bulk_rna/deseq2_counts_top1000.csv",
    coldata_path="tests/fixtures/bulk_rna/deseq2_coldata.tsv",
    condition_col="group",
    ref_level="ctrl",
)
print(result["summary"])   # 印出差異表現基因數量摘要
```

或在 Claude Code CLI 內直接下指令：

```
bio_run_deg counts_path=tests/fixtures/bulk_rna/deseq2_counts_top1000.csv
            coldata_path=tests/fixtures/bulk_rna/deseq2_coldata.tsv
            condition_col=group ref_level=ctrl
```

---

## 測試資料

`tests/fixtures/bulk_rna/` 內含開箱即用的 Bulk RNA-seq 示範數據集，無需額外下載，包含 `deseq2_counts_top1000.csv`（~400 KB，1000 基因 × 84 樣本）、`deseq2_coldata.tsv`（樣本 metadata）與 `gene_sets/hair_follicle.yaml`（路徑基因集範例），可直接用於 `bio_run_deg`、`bio_run_enrichment`、`bio_run_heatmaps` demo。

```bash
# 跑所有自動化測試
.venv/bin/python -m pytest tests/ -v --tb=short
# 679 tests collected
```

---

## LLM + MCP Server 整合

Evo_PRISM 以 **MCP（Model Context Protocol）** 作為 LLM 與工具箱之間的標準橋樑。任何支援 MCP 的 AI 客戶端（Claude Code、Antigravity IDE、Web UI）都能直接呼叫 25 個工具（啟用沙盒後共 26 個），讓 LLM 自主決定何時查詢歷史、何時觸發分析、何時讀回報告——無需人工介入。

同時提供 **stdio** 與 **HTTP** 兩種 transport。

```bash
# HTTP 獨立啟動
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

### 可用工具（預設 25 個）

| 工具 | 說明 |
| :--- | :--- |
| `bio_history_lookup` | 樣本分析歷史 |
| `bio_history_timeline` | 最近 N 天時間軸 |
| `bio_history_check` | 確認分析是否完成 |
| `bio_history_search` | L1 HNSW 語意搜尋 |
| `bio_memory_query` | L1 快取完整報告 |
| `bio_memory_write` | 寫入 L1 快取 |
| `bio_register_sample` | 登記新樣本 |
| `bio_read_report` | 讀取分析報告原文 |
| `bio_artifact_search` | ENGRAM 3-way RRF 語意搜尋 |
| `bio_artifact_summary` | ENGRAM artifact 摘要 |
| `bio_get_artifact` | 取得分析輸出檔案 handle（路徑 + 下載 URL + 預覽） |
| `bio_get_figure` | 依 ID 取回單張圖片（MCP ImageContent，供 VLM 按需載入） |
| `bio_check_l2_sufficiency` | 檢查 L2 就緒狀態 |
| `bio_find_tool` | 語意搜尋既有可重用分析函數（撰寫代碼前先探索） |
| `bio_run_spatial_eda` | 空間 EDA 分析 |
| `bio_run_bulk_eda` | Bulk RNA EDA 分析 |
| `bio_run_deg` | 差異表達分析（DEG）+ 火山圖 |
| `bio_run_enrichment` | ORA 富集分析 + dot plot |
| `bio_run_heatmaps` | 基因表達熱圖生成 |
| `bio_tool_health` | HELIX 工具健檢 |
| `bio_failure_summary` | 分析失敗診斷統計（HELIX PM1 自診斷） |
| `bio_impact` | 變更爆炸範圍評估 |
| `bio_run_mcseg_roi` † | Visium HD ROI 多尺度細胞分割（GPU，30–90 分鐘） |
| `bio_run_mcseg_fullslide` † | 全切片 tiled 細胞分割（GPU，數小時） |
| `bio_compute_crc_metrics` † | CRC Visium HD 空間指標計算 |
| `bio_execute_code` ⚠️ | 沙盒 Python 執行（需 `MCP_ENABLE_DANGEROUS_TOOLS=true`） |

> † 需要 MCseg 後端（`scripts/msseg/`），本倉庫未包含。工具仍會出現在列表中，但呼叫時若未安裝後端將回傳 import 錯誤。

詳細設定見 [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md) 與 [docs/guides/MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md)。

---


## 排程任務

六個背景排程器分別負責資料庫備份、L1 / Figure Cache TTL 清理、HNSW 索引重建、新樣本掃描與 HELIX 快照降採樣。完整任務表與 launchd 安裝說明見 [docs/guides/SCHEDULED_TASKS.md](docs/guides/SCHEDULED_TASKS.md)。

---

## 專案結構

```text
Evo_PRISM/                      ← 專案根目錄
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
│   ├── tests/                  ← 測試套件（49 files, 679 tests）
│   ├── gene_sets/              ← 路徑基因集 YAML
│   └── start_bioagent.sh       ← 一鍵啟動腳本
│
├── 文件（git 追蹤）
│   └── docs/
│       ├── images/             ← 架構圖（三層架構 / HELIX / ENGRAM）
│       ├── guides/             ← 操作指南（MCP / L3 Ingest / Data Integration）
│       ├── launchd/            ← macOS launchd plist 範本
│       └── logs/               ← 開發日誌（PROGRESS / execution_trace）
│
└── 本地數據目錄（.gitignore 排除）
    ├── bio_memory.duckdb       ← 主資料庫
    ├── silver/                 ← L2 Parquet 特徵存儲
    ├── gold/                   ← L1 語意快取 DuckDB
    ├── crc_visium_data/        ← L3 原始數據（~39 GB）
    ├── bulk_rna_data/          ← Bulk RNA 原始數據
    └── proteome_data/          ← Proteomics 數據
```

---

## 測試

```bash
cd "$BIO_DB_ROOT"
.venv/bin/python -m pytest tests/ -v --tb=short
```

預期：**679 tests collected**（100.0% 通過率），涵蓋 49 個測試檔，範圍包含 HELIX 版本管理、ENGRAM 語意搜尋、MCP stdio/HTTP transport、沙盒安全、Code Promotion 及生物資訊分析管道。

完整每檔明細見 [docs/guides/TESTING.md](docs/guides/TESTING.md)。

---

## 文件索引

| 文件                                                                        | 說明                                        |
| :-------------------------------------------------------------------------- | :------------------------------------------ |
| [CLAUDE.md](CLAUDE.md)                                                         | 專案憲法（開發規範 + Schema + 路徑）        |
| [SETUP.md](SETUP.md)                                                           | 詳細環境安裝手冊                            |
| [TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md)                                 | 技術概覽：架構說明、HELIX/ENGRAM 公式、Benchmark 結果 |
| [docs/logs/PROGRESS.md](docs/logs/PROGRESS.md)                                 | 實作進度封存                                |
| [docs/guides/DATA_INTEGRATION_GUIDE.md](docs/guides/DATA_INTEGRATION_GUIDE.md) | 跨專案數據整合指南                          |
| [docs/guides/L3_DATA_INGEST_GUIDE.md](docs/guides/L3_DATA_INGEST_GUIDE.md)     | 新增 L3 樣本操作指南                        |
| [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md)                 | MCP stdio 設定（Claude Code / Antigravity） |
| [docs/guides/MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md)                 | MCP HTTP transport 說明                     |
| [docs/guides/STAR_SCHEMA.md](docs/guides/STAR_SCHEMA.md)                       | Star Schema views 設計與使用範例            |
| [docs/guides/TESTING.md](docs/guides/TESTING.md)                               | 測試套件明細（49 files, 679 tests）        |
| [docs/guides/SCHEDULED_TASKS.md](docs/guides/SCHEDULED_TASKS.md)               | 排程任務表與 launchd 安裝說明              |

---

## 用 LLM 擴充工具箱

Evo_PRISM 最核心的設計理念：**讓 LLM 自己擴充自己（Self-Evolution）**。

`CLAUDE.md` 是整個平台的「憲法」，完整定義了每一條規範與擴充流程。LLM 讀懂它之後，只需要你的一句話就能自主完成所有修改，並實現工具的自主演化升格：

#### 情境一：新增全新分析領域

```
幫我新增 scRNA-seq 分析工具，支援 clustering、marker gene 偵測、UMAP 視覺化
```

LLM 自動產出：playbook → 分析函數 → MCP 接線 → HELIX 版本登記，全新工具立即可用。

#### 情境二：擴充既有工具的能力

```
幫 bio_run_deg 加入互動式 volcano plot，並支援批次校正（ComBat）
```

LLM 自動修改既有函數、更新 playbook 步驟、bump 版本號、重新呼叫 `register_tool()`，HELIX 自動記錄版本差異。

---

兩種情境 LLM 都會完成以下四步，不需要人工介入：

| 步驟                    | 新增工具                            | 擴充既有工具                    |
| ----------------------- | ----------------------------------- | ------------------------------- |
| **1. Playbook**   | 建立 `playbooks/<domain>.md`      | 更新既有 playbook 步驟          |
| **2. 分析函數**   | 建立 `analysis/<module>.py`       | 修改既有函數，新增參數或圖表    |
| **3. MCP 接線**   | 新增工具至 `bio_memory_server.py` | 更新工具的 `inputSchema` 描述 |
| **4. HELIX 登記** | `register_tool()` 版本 1.0.0      | `register_tool()` bump 版本號 |

完成後，工具立即可被任何 MCP 客戶端（Claude Code / Antigravity / Web UI）呼叫，所有分析結果自動歸入 ENGRAM 永久記憶。

### 現有工具參考

| MCP 工具                | Playbook                        | 分析模組                    |
| ----------------------- | ------------------------------- | --------------------------- |
| `bio_run_bulk_eda`    | `playbooks/bulk_rnaseq.md`    | `analysis/bulk_eda.py`    |
| `bio_run_deg`         | `playbooks/bulk_rnaseq.md`    | `analysis/bulk_eda.py`    |
| `bio_run_spatial_eda` | `playbooks/spatial_visium.md` | `analysis/spatial_eda.py` |

---

## 貢獻

歡迎 PR 與 Issue！請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 授權

MIT License — © 2026 詹麒儒 (Chan Chi Ru). See [LICENSE](LICENSE).
