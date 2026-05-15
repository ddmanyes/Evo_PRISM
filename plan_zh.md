# Hermes Bio-Memory — 實驗室生資智慧分析系統

---

## 🗺️ 當前狀態（2026-05-15）

| 項目 | 狀態 |
|------|------|
| 平台 | macOS `/Volumes/NO NAME/bio_DB/`（ExFAT，測試階段） |
| 目標平台 | Linux `/mnt/space4/bio_lab_db/`（生產部署） |
| 測試數據 | CRC Visium HD 官方數據（~39GB）+ MSseg 分析結果（5.5GB） |
| 完成項目 | 計畫文件、00_init_db.py、L3 數據就位、CLAUDE.md |
| 當前進行 | 環境建置（pyproject.toml + uv sync） |
| 下一步 | 執行 00_init_db.py → 02_spatial_to_parquet.py（CRC 數據） |

> 詳細進度見 [PROGRESS.md](PROGRESS.md)，操作規範見 [CLAUDE.md](CLAUDE.md)。

---

## 計畫摘要

### 背景與動機

現代生物資訊實驗室每日產出大量高維度數據——空間轉錄體（Visium HD）、單細胞 RNA-seq、Bulk RNA-seq——而每次重新分析往往耗費數小時乃至數天的運算資源。傳統工作流程存在四個核心痛點：

1. **重複運算浪費**：不同實驗室成員針對相同樣本提出類似問題，卻各自重跑相同的耗時 Pipeline（SpaceRanger 單次執行約 4 小時），造成伺服器資源嚴重浪費。
2. **數據孤島問題**：分析結果散落於各人電腦或伺服器資料夾，缺乏統一的查詢與比較機制，跨樣本分析困難。
3. **無分析記錄可查**：實驗室缺乏系統性的分析歷史追蹤，成員無從得知某樣本是否已被分析過、結果存放在哪、由誰在何時完成——導致重工或遺漏，也難以對外呈現實驗室的研究進度。
4. **使用門檻過高**：實驗室成員若不熟悉 Python / 命令列，難以自助取得分析結果，造成對特定人員的過度依賴。

### 解決方案

本計畫建立一套以 **AI Agent** 為核心的**實驗室智慧生資分析平台**，整合三層式數據倉儲、分析歷史追蹤、與對話式介面，實現：

- **對話式查詢**：實驗室成員透過 **Telegram**（或其他訊息平台）以自然語言提出分析需求，無需任何程式設計能力
- **分析歷史追蹤**：每次分析自動記錄至時間軸資料庫，成員可隨時查詢「某樣本分析了什麼、誰做的、結果在哪」，並以此為依據決定是否需要重新分析
- **智慧快取與省 Token 搜尋**：透過結構化 SQL（0 token）與語意向量搜尋（少量 token）兩階段機制，在最小 API 費用下確認是否有已存檔的分析結果，避免重複運算
- **自動排程分析**：系統每日定時執行 QC 報告、新樣本偵測，並主動推送報告給相關成員
- **數據持續累積**：所有樣本、分析、報告統一儲存，形成實驗室可持續增值的**知識資產**

### 核心技術棧

| 角色 | 技術 | 說明 |
|------|------|------|
| AI Agent 框架 | 輕量自製 Agent 或 Hermes Agent | 接收訊息、呼叫工具、管理排程 |
| 使用者介面 | Telegram Bot（或 LINE / Slack） | 依實驗室實際使用習慣決定 |
| 數據倉儲引擎 | DuckDB + Apache Parquet | L2 特徵儲存與所有結構化查詢的核心 |
| 向量語意搜尋 | DuckDB VSS（HNSW 索引） | L1 語意快取的命中機制 |
| 分析歷史追蹤 | DuckDB（`analysis_history` + `analysis_index`） | 帶時間軸的分析紀錄，0 token 可查 |
| 系統介面協定 | MCP（Model Context Protocol） | Agent 與資料庫之間的標準工具介面 |
| 部署環境 | Linux 伺服器（`/mnt/space4/`） | 全天候運行；現階段先在 macOS 本機測試 |

### 預期效益

| 指標 | 目標 | 達成機制 |
|------|------|---------|
| 重複查詢回應時間 | < 1 秒 | L1 語意快取命中 |
| 重複運算節省率 | ≥ 70% 後續查詢不觸及 L3 | analysis_history 先行確認 + L1 快取 |
| 歷史查詢 Token 消耗 | 0 token（精確查詢）/ 少量（語意搜尋） | SQL 直接回傳，不經 LLM |
| 使用者技術門檻 | Telegram 訊息即可完成查詢與查閱歷史 | 對話式 AI 介面 |
| 跨樣本比較 | SQL 直接查詢，不需重跑分析 | L2 Parquet 統一儲存 |

---

## 系統架構總覽

```
實驗室成員
    │
    │  Telegram 訊息（自然語言需求 / 查詢歷史 / 索取報告）
    ▼
┌──────────────────────────────────────────────────┐
│                    AI Agent                      │
│          （Linux 伺服器 /mnt/space4/）            │
│                                                  │
│  • 解析使用者意圖（分析需求 / 歷史查詢 / 排程）   │
│  • 優先呼叫 0-token 歷史查詢工具確認是否已存檔    │
│  • 視需要呼叫分析工具或快取查詢                   │
│  • 管理每日排程任務                               │
│  • 將報告 / 圖表 / 歷史摘要推送回 Telegram        │
└─────────────────────┬────────────────────────────┘
                      │ MCP Tool Call
                      ▼
┌──────────────────────────────────────────────────┐
│              Bio-Memory MCP Server               │
│                                                  │
│  ┌─ L1 Gold ── 語意快取（近期記憶）               │
│  │   完整報告文字 + HNSW 向量索引                 │
│  │   TTL 7 天；cosine ≥ 0.88 命中               │
│  │                                               │
│  ├─ L2 Silver ─ DuckDB 結構化特徵儲存            │
│  │   ├─ 空間轉錄體 Parquet（8µm bins）           │
│  │   ├─ Bulk RNA-seq Parquet                    │
│  │   ├─ sample_registry（樣本登記）               │
│  │   ├─ analysis_history（分析歷史，帶時間軸）    │
│  │   └─ analysis_index（精簡索引 View，0 token） │
│  │                                               │
│  └─ L3 Bronze ─ 不可變原始數據                   │
│      FASTQ、SpaceRanger outs/、BAM、原始影像      │
│      （現有資料，絕不修改）                        │
└──────────────────────────────────────────────────┘
```

**完整查詢決策流程：**
```
使用者提問
    │
    ├─[Step 1] bio_history_check()         ← 0 token，SQL 確認是否已有存檔
    │   已存檔且結果有效？
    │   └─ 是 → 直接回傳存檔路徑或摘要（< 1 秒，0 token）
    │   └─ 否 → 繼續 Step 2
    │
    ├─[Step 2] L1 語意搜尋                 ← 少量 token，HNSW cosine ≥ 0.88
    │   快取命中？
    │   └─ 是 → 回傳快取報告（< 1 秒）
    │   └─ 否 → 繼續 Step 3
    │
    ├─[Step 3] L2 特徵查詢                 ← 有特徵數據則輕量分析（~30 秒）
    │   有 Parquet 數據？
    │   └─ 是 → 分析 → 寫入 L1 + analysis_history → 回傳報告
    │   └─ 否 → 繼續 Step 4
    │
    └─[Step 4] L3 Pipeline 排程            ← 耗時（~4 小時）
        有原始數據？
        └─ 是 → 排程 SpaceRanger / Kallisto → 完成後寫 L2 → L1 → 回傳
        └─ 否 → 通知使用者需上傳原始數據
```

---

## 專案目錄結構

```
現階段（macOS 本機測試）
/Volumes/NO NAME/bio_DB/
├── CLAUDE.md                     ← 專案憲法（規範 + 架構 + 路徑）
├── PROGRESS.md                   ← 進度封存（每次里程碑更新）
├── plan_zh.md                    ← 本計畫文件
├── plan.md                       ← 英文版
├── pyproject.toml                ← Python 依賴管理（uv）
├── .env.example                  ← 環境變數範本
├── bio_memory.duckdb             ← DuckDB 主入口（含所有結構化表）
│
├── silver\                       ← L2：Parquet 特徵儲存
│   ├── spatial_counts_*.parquet  ← 8µm bin 空間轉錄體計數
│   ├── spatial_meta_*.parquet    ← barcode 元數據（座標、群集）
│   └── bulk_counts_*.parquet     ← Bulk RNA-seq 計數
│
├── gold\                         ← L1：語意快取
│   └── hermes_cache.duckdb       ← memory_recent + HNSW 索引
│
├── config/                       ← 集中路徑與設定
│   └── settings.py
│
├── scripts/                      ← 一次性資料轉換工具
│   ├── 00_init_db.py             ← ✅ 建立所有表（含 analysis_history）
│   ├── 01_register_sample.py     ← 自動掃描 + 登記 L3 樣本
│   ├── 02_spatial_to_parquet.py  ← L3 Visium HD → L2 Parquet
│   └── msseg/                    ← MSseg 工具腳本
│
├── analysis/                     ← 分析函式庫（持續擴充）
│   ├── spatial_eda.py
│   ├── bulk_eda.py
│   ├── report_generator.py       ← 報告格式化 + 50 字摘要產生
│   └── history_query.py          ← 分析歷史查詢（0 token SQL 介面）
│
├── server/                       ← MCP Server
│   └── bio_memory_server.py      ← 含 bio_history_* 工具
│
├── scheduler/                    ← 排程任務（cron 驅動）
│   ├── daily_qc.py
│   └── sample_watcher.py
│
├── tests/                        ← 測試套件
│   ├── conftest.py
│   ├── test_init_db.py
│   └── test_spatial_ingest.py
│
├── crc_visium_data/              ← ✅ L3 測試數據（~39GB，唯讀）
├── data_ana/                     ← ✅ 參考分析中間數據（唯讀）
├── results_ana/                  ← ✅ 參考分析結果（唯讀）
├── analysis_msseg/               ← ✅ MSseg 分析程式碼（參考用）
├── backend_msseg/                ← ✅ MSseg FastAPI 後端（參考用）
├── msseg_docs/                   ← ✅ MSseg 文件（參考用）
│
└── references/                   ← 技術參考文獻與論文

未來轉移目標（Linux 伺服器，架構完全相同）
/mnt/space4/bio_lab_db/
```

---

## 三層架構對照

| 層級 | 角色 | 儲存內容 | 觸發時機 |
|------|------|---------|---------|
| **L3 銅層**（Bronze） | 不可變原始數據，唯一真實來源 | FASTQ、BAM、SpaceRanger outs/、原始影像 | 僅在 L2 缺乏所需特徵時觸發重型 Pipeline |
| **L2 銀層**（Silver） | 結構化特徵儲存 + 分析歷史 | Parquet 計數矩陣、sample_registry、**analysis_history**、analysis_index | L1 未命中 且 history_check 確認無存檔時 |
| **L1 金層**（Gold） | 語意快取，最快回應層 | memory_recent（完整報告 + 向量嵌入，TTL 7天） | 每次查詢的第一道防線（cosine ≥ 0.88） |

**各表所在位置：**

| 資料表 / 檔案 | 位置 | 說明 |
|-------------|------|------|
| `spatial_counts_*.parquet` | `silver/` | 空間轉錄體基因計數（L2） |
| `bulk_counts_*.parquet` | `silver/` | Bulk RNA-seq 計數（L2） |
| `sample_registry` | `bio_memory.duckdb` | 所有樣本登記，含路徑與狀態 |
| `analysis_history` | `bio_memory.duckdb` | 分析歷史主表，含時間軸 |
| `analysis_index` | `bio_memory.duckdb`（View） | 精簡彙整索引，供 0 token 快速查閱 |
| `memory_recent` | `gold/hermes_cache.duckdb` | L1 語意快取，含 HNSW 索引 |

---

## 分析歷史設計與省 Token 搜尋策略

### 設計原則：SQL 過濾優先，LLM 只看結果

最省 token 的方法是「**讓資料庫回答結構化問題，LLM 只處理剩下沒辦法用 SQL 答的部分**」。

```
問題類型                    處理方式                       Token 消耗
────────────────────────────────────────────────────────────────────
「XX 樣本分析了什麼？」     SQL 查 analysis_history         0 token
「這週做了幾個分析？」      SQL GROUP BY date               0 token  
「有沒有分析過 CD45？」     HNSW 語意搜尋 L1 cache          少量 token（只傳摘要）
「幫我解讀這份 QC 報告」    傳完整報告給 LLM                正常 token
```

關鍵設計：**不把完整報告傳給 LLM 來篩選；先用 SQL / 向量搜尋縮範圍，只把命中的精簡摘要傳給 LLM。**

### analysis_history — 分析歷史主表

```sql
CREATE TABLE analysis_history (
    analysis_id   UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    sample_id     VARCHAR REFERENCES sample_registry(sample_id),
    analysis_type VARCHAR,     -- 'qc'、'spatial_gene'、'clustering'、'diff_expr'
    parameters    JSON,        -- 分析參數（基因名稱、解析度、group 等）
    status        VARCHAR,     -- 'running'、'completed'、'failed'
    result_path   VARCHAR,     -- 輸出檔案路徑（圖表、報告）
    l1_cache_id   UUID,        -- 對應的 memory_recent 快取 ID（若有）
    requested_by  VARCHAR,     -- 哪位成員發起
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    summary       VARCHAR      -- 50 字以內的結果摘要（供快速搜尋用）
);
```

### analysis_index — 省 Token 搜尋用的精簡索引視圖

```sql
-- 這個 View 是「展示給 Agent 看」的精簡格式
-- 不含完整報告，只有結構化事實，大幅節省 token
CREATE VIEW analysis_index AS
SELECT
    sample_id,
    analysis_type,
    COUNT(*)                            AS run_count,
    MAX(completed_at)::DATE             AS last_run_date,
    MIN(started_at)::DATE               AS first_run_date,
    STRING_AGG(DISTINCT requested_by, ', ')  AS run_by_members,
    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS success_count,
    SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS fail_count
FROM analysis_history
GROUP BY sample_id, analysis_type
ORDER BY last_run_date DESC;
```

### analysis_history vs. memory_recent — 兩者的差異

| 比較項目 | `analysis_history`（L2） | `memory_recent`（L1） |
|---------|------------------------|----------------------|
| **用途** | 追蹤「做了什麼」——誰在何時做了哪種分析 | 快速回答「這個問題的答案是什麼」 |
| **儲存內容** | 元數據（樣本、類型、時間、狀態、路徑、50字摘要） | 完整報告文字 + 向量嵌入（FLOAT[1536]） |
| **壽命** | **永久**（不刪除） | **TTL 7 天**（到期自動清除） |
| **查詢方式** | SQL（0 token） | HNSW 向量搜尋（少量 token） |
| **回答的問題** | 「有沒有做過？誰做的？結果在哪？」 | 「這個分析問題有沒有現成答案？」 |

> `analysis_history` 是**永久帳本**，記下每次分析的事實。  
> `memory_recent` 是**短期答案庫**，加速重複語意問題的回應。  
> 兩者互補：帳本確認「有做過」，答案庫直接給「答案內容」。

### 歷史紀錄寫入時機

```
分析函式執行完成
    │
    ├─ 1. 將結果存成 PNG / CSV / MD → result_path
    ├─ 2. 產生 50 字摘要 → summary（例：「CD45+ 佔 23%，主要在邊緣區」）
    ├─ 3. INSERT 一筆至 analysis_history（status = 'completed'）
    └─ 4. 若完整報告需快取 → 寫入 memory_recent，ID 存回 analysis_history.l1_cache_id
```

分析開始時也應先寫一筆 `status = 'running'`，以便在失敗時仍有紀錄（`status = 'failed'`），不留盲點。

### 搜尋流程設計

**模式 A：精確查詢（0 token）**

使用者問「MQ250428-D1-D2 有做過哪些分析？」

```python
# Agent 直接呼叫 SQL，不經過 LLM
result = duckdb.query("""
    SELECT analysis_type, last_run_date, run_count, run_by_members
    FROM analysis_index
    WHERE sample_id = 'MQ250428-D1-D2'
    ORDER BY last_run_date DESC
""").df()
# 格式化成 Markdown 表格直接回傳 Telegram
```

**模式 B：語意搜尋（少量 token）**

使用者問「有沒有分析過 CD45 分布？」

```python
# Step 1：向量化問題（呼叫 embedding API，消耗少量 token）
query_embedding = embed("CD45 distribution analysis")

# Step 2：HNSW 搜尋 L1 快取（不消耗 LLM token）
hits = duckdb.query("""
    SELECT sample_id, summary, completed_at,
           array_cosine_similarity(embedding, ?::FLOAT[1536]) AS score
    FROM memory_recent
    WHERE array_cosine_similarity(embedding, ?::FLOAT[1536]) >= 0.88
    ORDER BY score DESC LIMIT 5
""", [query_embedding, query_embedding]).df()

# Step 3：只把 summary 欄位（50字）傳給 LLM，不傳完整報告
# Token 消耗 = 5 筆 × 50 字 ≈ 350 tokens，而非 5 份完整報告
```

**模式 C：時間軸瀏覽（0 token）**

使用者問「這週的分析進度？」

```python
result = duckdb.query("""
    SELECT DATE_TRUNC('day', completed_at) AS date,
           COUNT(*) AS analyses_done,
           STRING_AGG(sample_id || '/' || analysis_type, ', ') AS details
    FROM analysis_history
    WHERE completed_at >= NOW() - INTERVAL '7 days'
      AND status = 'completed'
    GROUP BY 1 ORDER BY 1 DESC
""").df()
```

---

## 第零階段 — 訊息介面建置

*先決條件：確認實驗室成員實際使用的訊息平台。*

- [ ] 確認平台選擇：Telegram / LINE / Slack（依實驗室習慣）
- [ ] 建立 Bot（以 Telegram 為例：向 @BotFather 申請 Token）
- [ ] 設定白名單：只允許實驗室成員的使用者 ID 存取
- [ ] 建立基本 Bot 骨架（`python-telegram-bot` 或對應 SDK）
- [ ] 測試：成員發送訊息 → Bot 回應確認

> **Agent 框架選擇（待決定）：**
> - **Hermes Agent**（NousResearch）：功能完整，內建多平台支援，但為外部依賴
> - **自製輕量 Agent**（Claude API + python-telegram-bot）：更簡單，完全掌控
> 建議先用自製方式驗證流程，確認需求後再評估是否引入 Hermes Agent

---

## 第一階段 — 環境建置與 Schema 設計

- [x] 安裝 Python 套件：`duckdb`、`anndata`、`pandas`、`pyarrow`、`scipy`（pyproject.toml 完成）
- [x] 建立目錄骨架：`silver/`、`gold/`、`scripts/`、`analysis/`、`server/`、`scheduler/`、`config/`、`tests/`
- [x] 撰寫 `scripts/00_init_db.py`：
  - 初始化 `bio_memory.duckdb`
  - 驗證 VSS 擴充可載入（`INSTALL vss; LOAD vss;`）
  - 建立 `sample_registry` 資料表
- [ ] **執行** `uv run python scripts/00_init_db.py` 驗證 Schema 實際可建立
- [ ] 填入 CRC 官方數據至 `sample_registry`（測試樣本：`official_v4`）
- [x] 建立 `analysis_history` 主表與 `analysis_index` 視圖（在 00_init_db.py 中）

**sample_registry 欄位設計：**
```sql
CREATE TABLE sample_registry (
    sample_id      VARCHAR PRIMARY KEY,
    project        VARCHAR,       -- 'MQ250428'、'Kallisto_v1'
    data_type      VARCHAR,       -- 'visium_hd'、'bulk_rnaseq'、'scrna'
    species        VARCHAR,       -- 'mouse'、'human'
    l3_path        VARCHAR,       -- 原始數據路徑（Linux 伺服器）
    l2_ready       BOOLEAN DEFAULT FALSE,
    analysis_done  BOOLEAN DEFAULT FALSE,
    added_by       VARCHAR,       -- 新增者
    notes          VARCHAR,
    last_updated   TIMESTAMP
);
```

> `analysis_history` 與 `analysis_index` 的詳細設計見上方「分析歷史設計與省 Token 搜尋策略」章節。

---

## 第二階段 — L2 銀層：特徵儲存庫建置

### 2-A  空間轉錄體（Visium HD）

**測試原型（本機）**：CRC 官方 Visium HD 數據（`crc_visium_data/official_v4/`）  
**實驗室原型（伺服器）**：MQ250428-D1-D2（待部署 Linux 後處理）

L3 來源（測試）：
```
crc_visium_data/official_v4/
    binned_outputs/square_008um/    ← 主要（8µm，分析解析度）
    binned_outputs/square_016um/    ← 次要（16µm，總覽）
    segmented_outputs/              ← 細胞分割結果（此數據獨有）
    spatial/                        ← 空間座標
```

- [ ] 撰寫 `scripts/02_spatial_to_parquet.py`（使用 CRC 官方數據測試）
- [ ] 輸出：`silver/spatial_counts_crc_official_v4_8um.parquet`
- [ ] 輸出：`silver/spatial_meta_crc_official_v4.parquet`
- [ ] 驗證 DuckDB 可依基因名稱與空間座標查詢
- [ ] 測試通過後，相同腳本套用至 MQ250428-D1-D2（伺服器部署後）

**spatial_counts 欄位：**
```sql
sample_id VARCHAR, barcode VARCHAR, gene_name VARCHAR,
count INTEGER, x_um FLOAT, y_um FLOAT, bin_size_um INTEGER
```

**spatial_meta 欄位：**
```sql
sample_id VARCHAR, barcode VARCHAR,
n_genes INTEGER, n_counts INTEGER,
x_um FLOAT, y_um FLOAT, in_tissue BOOLEAN,
cluster_id INTEGER, cell_type VARCHAR
```

### 2-B  Bulk RNA-seq（Kallisto）

L3 來源：`results_kallisto/`

- [ ] 撰寫 `scripts/02_kallisto_to_parquet.py`
- [ ] 彙整所有 `abundance.tsv`；透過 t2g 表對應至基因名稱
- [ ] 輸出：`silver/bulk_counts_kallisto_v1.parquet`

**bulk_counts 欄位：**
```sql
sample_id VARCHAR, gene_name VARCHAR,
est_counts FLOAT, tpm FLOAT,
condition VARCHAR, replicate INTEGER
```

---

## 第三階段 — 分析工具層與報告產生

持續擴充的分析函式庫，由 Agent 呼叫後產出報告推送給使用者。

### spatial_eda.py
- `load_sample(sample_id)` — 查詢 L2，回傳 DataFrame
- `plot_spatial(df, gene)` — 基因表現空間圖（輸出 PNG）
- `top_genes(sample_id, n)` — 前 N 高表現基因
- `cluster_summary(sample_id)` — 各群集細胞數與代表基因
- `compare_samples(id_a, id_b, gene)` — 跨樣本基因表現比較

### bulk_eda.py
- `load_bulk(condition=None)` — 查詢 bulk_counts
- `diff_expr(cond_a, cond_b)` — 差異表現分析（fold-change）
- `plot_pca(df)` — 跨樣本 PCA
- `gene_query(gene_name)` — 特定基因在所有樣本的表現

### report_generator.py
- `make_text_report(result)` — 產生 Markdown 摘要（適合 Telegram 訊息）
- `make_image_report(fig)` — matplotlib 圖表 → PNG（供 Telegram 傳送）
- `make_qc_report(sample_id)` — 標準 QC 報告（reads 數、基因數、組織覆蓋率）
- `make_summary(result, max_chars=50)` — 產生 50 字以內的結果摘要（寫入 analysis_history.summary，供省 token 搜尋）

### history_query.py（分析歷史查詢，全部 0 token）
- `what_analyzed(sample_id)` — 查詢某樣本所有歷史分析（精確，0 token）
- `weekly_timeline(n_days=7)` — 近 N 天的分析時間軸（0 token）
- `find_archived(sample_id, analysis_type)` — 確認特定分析是否已有存檔（0 token）
- `semantic_search(query_text, top_k=5)` — 語意搜尋 L1 快取，只傳摘要給 LLM（少量 token）

---

## 第四階段 — L1 金層：語意快取

*在第二、三階段穩定後建立。*

僅建立**近期記憶層**，以 HNSW 向量索引實現語意命中，避免重複分析。

- [ ] 建立 `gold/hermes_cache.duckdb`
- [ ] 建立 `memory_recent` 資料表 + HNSW 索引
- [ ] 撰寫 `scripts/cache_write.py`：分析完成後自動寫入快取
- [ ] 撰寫 `scripts/cache_query.py`：cosine ≥ 0.88 語意搜尋

**memory_recent 資料表：**
```sql
CREATE TABLE memory_recent (
    id          UUID DEFAULT gen_random_uuid(),
    sample_id   VARCHAR,
    query_text  VARCHAR,
    report_text VARCHAR,         -- 完整分析報告文字
    embedding   FLOAT[1536],     -- 向量嵌入
    created_at  TIMESTAMP DEFAULT now(),
    expires_at  TIMESTAMP        -- TTL 7 天
);

-- HNSW 向量索引
CREATE INDEX ON memory_recent USING HNSW (embedding)
WITH (metric = 'cosine');
```

**嵌入模型（待決定）：**
- OpenAI `text-embedding-3-small`（雲端，1536 維，$0.02/百萬 tokens）
- 本地 `nomic-embed-text`（免費，需 ~2 GB RAM）

> 詳見 `references/duckdb_vss.md`。

---

## 第五階段 — MCP Server

檔案：`server/bio_memory_server.py`
安裝：`pip install mcp`

**分析查詢工具（主動呼叫 LLM，有 token 消耗）：**

| MCP 工具 | 輸入 | 輸出 | Token |
|---------|------|------|-------|
| `bio_memory_query` | 問題、sample_id | L1 快取報告 或 L2 分析結果 | 有 |
| `bio_memory_write` | sample_id、分析類型、報告 | 快取寫入確認 | 無 |
| `bio_register_sample` | sample_id、資料類型、路徑 | 樣本登記確認 | 無 |

**歷史查詢工具（SQL 直接回傳，0 token）：**

| MCP 工具 | 輸入 | 輸出 | Token |
|---------|------|------|-------|
| `bio_history_lookup` | sample_id、analysis_type（可選）| 精簡分析歷史表格 | **0** |
| `bio_history_timeline` | n_days（預設 7） | 時間軸摘要表 | **0** |
| `bio_history_check` | sample_id、analysis_type | 是否已有完成存檔（True/False） | **0** |
| `bio_history_search` | 自然語言查詢 | L1 語意搜尋命中（只傳 summary） | 少量 |

**設計說明：** `bio_history_*` 工具在 MCP Server 內直接執行 SQL，回傳格式化字串給 Agent，**完全不經過 LLM**。只有 `bio_history_search` 需要 embedding API（查向量），但也只傳 50 字 summary 給 LLM，不傳完整報告。

> 詳見 `references/mcp_protocol.md`。

---

## 第六階段 — 排程系統

以 Linux cron 驅動，不依賴複雜框架。

### 每日任務（`scheduler/daily_qc.py`）
- 每日 08:00：偵測新樣本，自動加入 `sample_registry` 並通知管理員
- 每日 09:00：對新完成的樣本產生 QC 報告，推送至實驗室群組

### 新樣本監控（`scheduler/sample_watcher.py`）
- 掃描指定目錄是否有新的 SpaceRanger `outs/` 或 Kallisto 結果
- 發現新樣本 → 自動登記 + 通知 → 觸發 L2 轉換排程

**Linux cron 設定範例：**
```bash
0 8 * * * python /mnt/space4/bio_lab_db/scheduler/sample_watcher.py
0 9 * * * python /mnt/space4/bio_lab_db/scheduler/daily_qc.py
0 9 * * 1 python /mnt/space4/bio_lab_db/scheduler/weekly_report.py
```

---

## 第七階段 — 驗證與調校

以 MQ250428-D1-D2 為基準：

- [ ] 5 位實驗室成員實際使用，回饋操作體驗
- [ ] 確認 L1 快取命中率（測量重複查詢比例）
- [ ] 測量各層回應時間：L1（< 1秒）/ L2（~ 30秒）/ L3（~ 4小時）
- [ ] 根據回饋調整報告格式、查詢意圖解析、排程頻率

---

## 實作順序

```
第零階段（訊息介面 + Bot）
    │
    ▼
第一階段（DuckDB 環境 + Schema）
    │
    ├──→ 第二階段 2-A（Visium HD → L2）──┐
    ├──→ 第二階段 2-B（Bulk RNA → L2）   ─┤
    │                                     ▼
    │                          第三階段（分析工具 + 報告）
    │                                     │
    │                                     ▼
    │                          第四階段（L1 語意快取）
    │                                     │
    │                                     ▼
    │                          第五階段（MCP Server）
    │                                     │
    └─────────────────────────────────────▼
                               第六階段（排程系統）
                                          │
                                          ▼
                               第七階段（驗證調校）
                                          │
                                          ▼
                               【伺服器部署】macOS 本機 → Linux
```

---

## 關鍵檔案路徑

| 項目 | 現階段（macOS 測試） | 未來（Linux） |
|------|----------------|-------------|
| 測試原型（CRC） | `/Volumes/NO NAME/bio_DB/crc_visium_data/official_v4/` | `/mnt/space4/raw_data/crc_official/` |
| 實驗室原型 | TBD（伺服器上的 MQ250428 等） | `/mnt/space4/.../MQ250428-D1-D2/outs/` |
| 主 DuckDB | `/Volumes/NO NAME/bio_DB/bio_memory.duckdb` | `/mnt/space4/bio_lab_db/bio_memory.duckdb` |
| L1 快取 | `/Volumes/NO NAME/bio_DB/gold/hermes_cache.duckdb` | `/mnt/space4/bio_lab_db/gold/` |
| L2 Parquet | `/Volumes/NO NAME/bio_DB/silver/` | `/mnt/space4/bio_lab_db/silver/` |

---

## 參考文獻索引

| 文件 | 內容 | 對應階段 |
|------|------|---------|
| `references/duckdb.md` | DuckDB 引擎設計（SIGMOD 2019） | 第 1、2 階段 |
| `references/duckdb_vss.md` | HNSW 向量搜尋 | 第 4 階段 |
| `references/lakeharbor_icde2024.md` | 結構感知資料湖（ICDE 2024） | 第 2 階段 |
| `references/agent_first_data_systems.md` | Agent-First 資料系統（2025） | 全階段 |
| `references/mcp_protocol.md` | MCP Server 骨架 | 第 5 階段 |
| `references/anndata_scanpy.md` | 讀取 Visium HD 數據 | 第 2 階段 |
| `references/memgpt.md` | 分層記憶模型（概念參考） | 第 4 階段 |

---

## 未來擴充討論區

*以下元件目前不在主要開發計畫內，但技術上可行，待系統穩定後再評估是否引入。*

### A. LLMLingua — 中期記憶壓縮
**論文：** Jiang et al., EMNLP 2023（見 `references/llmlingua.md`）

當 L1 近期記憶（TTL 7天）到期後，若該報告仍有被查詢的價值，可透過 LLMLingua 將完整報告壓縮至 1/20 大小，延伸儲存為「中期記憶」（TTL 90天）。

- **壓縮率：** 20×，語意準確度損失 < 2%
- **適用情境：** 實驗室累積大量報告、Token 費用開始成為顯著成本時
- **安裝成本：** `pip install llmlingua`，首次使用需下載 ~3 GB 模型
- **引入時機：** 當 L1 快取資料量超過設定閾值，或 API Token 費用明顯上升時

```sql
-- 引入後新增的資料表
memory_midterm (TTL 90天, LLMLingua 壓縮文字, embedding FLOAT[1536])
```

---

### B. DeepSeek-OCR — 長期記憶視覺壓縮
**論文：** DeepSeek-AI, arXiv:2510.18234（見 `references/deepseek_ocr.md`）

針對超過 90 天的歷史報告與圖表，可透過 DeepSeek-OCR 將文字與圖表共同編碼為極少量的 vision token（100 tokens 可重建 800+ 文字 token，精度 96.8%），作為永久索引層。

- **壓縮率：** 8.5×，適合圖表密集的生資報告
- **適用情境：** 實驗室長期運行、歷史報告累積量龐大、需要索引但不需要完整內容時
- **安裝成本：** 需要 GPU 伺服器，模型較重
- **引入時機：** 系統運行超過 1 年，歷史報告量超過數千份，且有索引搜尋需求時

```sql
-- 引入後新增的資料表
memory_longterm (永久, DeepSeek-OCR 視覺摘要, embedding FLOAT[1536])
```

---

## 待解決問題

1. **訊息平台確認**：Telegram / LINE / Slack — 依實驗室成員習慣決定，影響第零階段實作
2. **Agent 框架**：自製輕量 Agent vs. Hermes Agent — 建議先自製，驗證後再評估
3. **嵌入模型**：OpenAI `text-embedding-3-small`（付費）vs. 本地 `nomic-embed-text`（免費）— 第四階段前決定
4. **Linux 伺服器權限**：確認 `/mnt/space4/` 有足夠磁碟空間與寫入權限
5. **MQ250422-A1-D1 缺失檔案**：web_summary 等檔案不存在（SpaceRanger 既有問題）— 以 D1-D2 為主要原型
6. **NDPI 配準**：兩個 MQ250428 樣本的高解析影像尚未對齊 — 完成前空間圖無組織影像疊加
7. **2µm vs 8µm**：L2 儲存 8µm；2µm 按需從 L3 載入，不做持久化
