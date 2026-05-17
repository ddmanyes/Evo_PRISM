# Hermes Bio-Memory — 實驗室生資智慧分析系統

---

## 一、系統定位與動機

### 問題

現代生物資訊實驗室每日產出大量高維度數據（Visium HD、Bulk RNA-seq、Proteomics），卻面臨四個核心痛點：

1. **重複運算浪費**：不同成員針對相同樣本提出類似問題，各自重跑相同耗時 Pipeline（SpaceRanger 單次 ~4 小時）
2. **數據孤島**：分析結果散落於各人電腦，缺乏統一查詢與比較機制
3. **無分析記錄**：無從得知某樣本是否已分析過、結果在哪、由誰完成
4. **使用門檻高**：不熟悉命令列的成員難以自助取得結果

### 解決方案

以 **AI Agent + 三層數據倉儲** 建立實驗室智慧分析平台：

- 成員透過 **Web UI 或 Telegram** 自然語言查詢，無需任何程式能力
- 每次分析自動寫入時間軸資料庫，可隨時追溯
- **多層防線**：SQL 精確查（0 token）→ 語意搜尋（少量 token），避免重複運算
- 所有樣本、分析、報告統一累積，形成可持續增值的**實驗室知識資產**
- **多模態**：支援圖片上傳讓 Gemma 4 Vision 視覺分析，分析結果圖直接顯示於聊天框

---

## 二、技術選型說明

### 設計依據概覽

本系統的核心架構均參考已發表的工程實踐，而非憑空設計。**三層 Bronze / Silver / Gold 架構**源自 Databricks Medallion Architecture 與結構感知資料湖設計（Hai et al., 2023），核心概念是原始數據唯讀不可改、分析結果只計算一次，避免重複運算。**語意搜尋**採用 DuckDB VSS 的 HNSW 實作（Malkov & Yashunin, 2018），讓「問法不同但意思相同」的查詢直接命中快取，不重跑分析。**Agent-First 查詢策略**參考 Trummer（2025）與 MemGPT 的分層記憶模型（Packer et al., 2023），讓資料庫處理結構化問題（0 token），LLM 只負責剩下無法用 SQL 回答的部分，大幅壓低 API 費用。**兩階段寫入**借鑑資料庫 WAL 與 Saga pattern（Garcia-Molina & Salem, 1987），確保長時間分析任務（~4 小時）即使中途崩潰也不遺失記錄。**Code Promotion 框架**則是原創設計，讓動態生成的程式碼在重用三次後自動升格為永久工具。詳細文獻論述見**附錄 A**。

### 元件選型總覽

| 元件 | 選型 | 主要理由 |
| ---- | ---- | -------- |
| 分析資料庫 | DuckDB（Raasveldt & Mühleisen, 2019） | 嵌入式、列式向量化、原生 Parquet、內建 HNSW，無需另起 DB 程序 |
| L2 儲存格式 | Apache Parquet | 列式壓縮（~95%）、型別嚴格、跨語言、與 DuckDB 零轉換整合 |
| 向量搜尋 | DuckDB VSS — HNSW（Müller et al., 2024） | 免部署 Pinecone / Weaviate，cosine 搜尋嵌入主 DB |
| 推理引擎 | 自製輕量 Agent + 雙後端 | 工具數量少（≤ 10），無需 LangChain 等重型框架 |
| 本機 LLM | Gemma 4 Vision 26B（llama.cpp） | 離線、零費用、多模態，敏感實驗數據不上傳雲端 |
| 雲端 LLM | Claude Sonnet（Anthropic） | 推理更強時切換，Prompt Cache 壓低費用 |
| Embedding | bge-m3 Q8（llama.cpp，port 8081） | 1024-dim、中英混雜表現佳、本機推理零費用 |
| 前端介面 | FastAPI Web UI（port 8000）+ Telegram Bot 骨架 | Web UI 已驗證，Telegram 為擴充選項 |

### 推理引擎：LLM 與 Python 的職責分工

本系統採用自製輕量 Agent，明確劃分 LLM 與 Python 的工作邊界：

| 工作項目 | 負責方 |
| -------- | ------ |
| 理解使用者意圖、決定呼叫哪個工具 | LLM |
| 資料寫入（DuckDB、Parquet） | Python（LLM 決定呼叫，Python 實際執行） |
| 檔案分析（.h5ad、.parquet） | Python 讀取計算，LLM 處理摘要 |
| 圖表生成（matplotlib） | Python 畫圖，`plt.show()` hook 自動捕獲回傳 |
| 視覺分析（圖片輸入） | Gemma 4 Vision / Claude 直接處理 |
| 分析歷史查詢 | LLM 呼叫 `bio_history_*`，Python 執行 SQL |

雙後端切換方式：Web UI sidebar「本機 / Claude」按鈕，即時生效，選擇存 `localStorage`。工具呼叫格式於 `agent.py` 自動轉換（Anthropic `input_schema` ↔ OpenAI function calling）。

### DuckDB 選型理由

DuckDB（Raasveldt & Mühleisen, 2019）是**列式嵌入式分析資料庫**，針對生資規模的 OLAP 查詢具備以下優勢：

- **列式儲存與向量化執行**：每次只讀需要的欄位，CPU SIMD 批次處理，略過基因計數矩陣中數億個零值。
- **零依賴嵌入式**：`import duckdb` 即用，無需維護獨立 DB Server，macOS 開發與 Linux 部署行為完全一致。
- **原生讀取 Parquet**：直接 `FROM 'silver/*.parquet'` 查詢，L2 的 416 MB Parquet 免載入記憶體，SQL 聚合後才傳給 LLM。
- **內建 HNSW 向量搜尋**：DuckDB VSS 擴充提供嵌入式近似最近鄰搜尋（Müller et al., 2024），免部署 Pinecone / Weaviate，L1 語意快取 cosine 搜尋 < 1 秒命中。
- **生資規模實測**：Visium HD 2µm 全圖 2.15 億 bins，8µm 聚合後約 500 萬列，DuckDB SQL 聚合 20 行結果傳給 LLM，節省 99%+ token。

### Parquet 選型理由

Apache Parquet 是**列式壓縮二進位格式**，為大型數字矩陣的標準儲存方案：

- **列式壓縮**：RLE + Dictionary encoding 對稀疏矩陣壓縮率極高。CRC Visium HD 原始約 30 億數字 → 416 MB Parquet（~95% 壓縮）。
- **型別嚴格**：欄位型別固定（`float32` / `int32`），基因計數直接以 `float32` 存，DuckDB 無需型別推斷。
- **分區儲存**：依樣本分區（`silver/spatial_counts_{sample_id}_8um/`），查詢時只讀相關分區。
- **跨語言**：Python pandas / R arrow / DuckDB 原生支援，分析結果可直接用 R 讀取，與濕實驗室人員共用。

### 沙盒執行策略

動態生成程式碼（Code Generation Loop）的執行隔離依部署階段遞進：

| 階段 | 隔離方式 |
| ---- | -------- |
| macOS 測試（現階段） | `subprocess.run` + ALLOWED\_IMPORTS 白名單 + timeout=60s |
| Linux 部署（第十一階段） | Docker container（`python:3.11-slim` + bind-mount `silver/`） |

---

## 三、三層架構

```
L3 銅層（Bronze）── 不可變原始數據
    FASTQ、BAM、SpaceRanger outs/、Perseus CSV
    規則：絕對唯讀，任何腳本嚴禁修改
         │
         │  scripts/ 一次性轉換
         ▼
L2 銀層（Silver）── 結構化特徵儲存 + 分析歷史
    silver/*.parquet        ← 空間 / Bulk RNA 計數矩陣
    bio_memory.duckdb       ← sample_registry + analysis_history + Views
    規則：只有 scripts/ 可寫入；分析函數只讀
         │
         │  分析完成後自動寫入
         ▼
L1 金層（Gold）── 語意快取（近期記憶）
    gold/hermes_cache.duckdb  ← memory_recent + HNSW 索引（TTL 7 天）
    規則：analysis/ 函數寫入；TTL 到期自動清除；可重建
```

| 層級 | 觸發時機 | 回應時間 | Token 消耗 |
|------|---------|---------|-----------|
| L1 快取命中（cosine ≥ 0.88） | L1 語意搜尋命中 | < 1 秒 | 0（直接回傳） |
| L2 SQL / Parquet 查詢 | L1 未命中 | ~30 秒 | 極少（SQL 壓縮後） |
| L3 Pipeline | L2 無 Parquet | ~4 小時 | 正常 |

---

## 四、資料庫 Schema 總覽

本章依架構層級由上至下說明各儲存單元，對應三層架構中的 L1 Gold → L2 Silver。

---

### L1 Gold：`memory_recent`

**用途：語意去重快取。** 每次分析完成後，將查詢文字、報告內容與其向量 embedding 一起存入。下次收到語意相似的問題（cosine ≥ 0.88）時，直接回傳快取結果，不重新執行分析、不消耗 LLM token。TTL 7 天到期自動清除——過期快取可能對應已更新的分析結果，過期即失效是刻意設計，確保不回傳過時答案。此表可完整重建，丟失不影響資料完整性。

| 欄位 | 說明 |
| --- | --- |
| `query_text` | 使用者原始問題文字 |
| `report_text` | 上次分析產生的完整報告 |
| `embedding` | 問題的 1024 維向量（bge-m3），供 HNSW 近似最近鄰搜尋 |
| `expires_at` | 建立時間 + 7 天，到期由排程自動刪除 |

```sql
CREATE TABLE memory_recent (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    sample_id   VARCHAR,
    query_text  VARCHAR,
    report_text VARCHAR,
    embedding   FLOAT[1024],  -- bge-m3 本機 1024-dim
    created_at  TIMESTAMP DEFAULT now(),
    expires_at  TIMESTAMP     -- TTL 7 天
);
CREATE INDEX memory_recent_emb_idx ON memory_recent
    USING HNSW (embedding) WITH (metric = 'cosine');
```

---

### L2 Silver：Parquet 計數矩陣

**用途：壓縮後的空間轉錄體特徵矩陣。** 由 `scripts/02_spatial_to_parquet.py` 從 L3 原始 SpaceRanger 輸出（`.h5ad`）一次性轉換而來，存於 `silver/<sample_id>/`。DuckDB 可直接查詢 Parquet，不需匯入記憶體，支援生資規模的 SQL 聚合。

每個樣本包含三類檔案：

| 檔案 | 欄位 | 說明 |
| --- | --- | --- |
| `obs_metadata.parquet` | `barcode`, `spatial_x`, `spatial_y`, `in_tissue`, … | 每個 bin 的空間座標與 QC 指標 |
| `var_metadata.parquet` | `gene_name`, `gene_id`, `genome` | 基因註解 |
| `expression/part-*.parquet` | `barcode`, `gene_name`, `count` | 長格式稀疏矩陣，僅儲存非零值（float32） |

> Visium HD 8µm 解析度：約 21 萬 bins × 最多 3 萬基因；非零值約 2.1 億筆，壓縮後約 416 MB（zstd）。

---

### L2 Silver：`sample_registry`

**用途：實驗室樣本名冊。** 每個生物樣本登記一筆，記錄它是什麼資料類型、原始檔案在哪、是否已轉換為 L2 Parquet、是否已完成分析。新樣本進來就新增一筆，之後不再修改。

```sql
CREATE TABLE sample_registry (
    sample_id      VARCHAR PRIMARY KEY,
    project        VARCHAR,
    data_type      VARCHAR,  -- visium_hd|visium|scrna|bulk_rnaseq|proteomics|other
    platform       VARCHAR,  -- 10x_visium_hd|kallisto|maxquant|...
    species        VARCHAR,  -- 'mouse'|'human'
    tissue         VARCHAR,
    l3_path        VARCHAR,
    l2_ready       BOOLEAN DEFAULT FALSE,
    analysis_done  BOOLEAN DEFAULT FALSE,
    added_by       VARCHAR,
    notes          VARCHAR,
    last_updated   TIMESTAMP DEFAULT now()
);
```

---

### L2 Silver：`analysis_history`

**用途：分析操作永久帳本。** 每次執行分析就新增一筆，**永遠不刪除**。記錄對哪個樣本、做了什麼分析、用了哪些參數、結果存在哪裡。這是系統追責與重現的唯一依據，也是 `analysis_index` View 的資料來源。

| 欄位 | 說明 |
| --- | --- |
| `analysis_type` | 分析種類，如 `qc` / `spatial_gene` / `clustering` |
| `parameters` | JSON 格式的完整參數，含可選的 `generated_code`（動態程式碼升格用） |
| `status` | `running` → `completed` / `failed` / `stale`（超過 24 小時未完成自動標記） |
| `summary` | ≤ 50 字的結果摘要，供 Agent 0-token 快速瀏覽 |
| `result_path` | 完整報告或圖檔的存放路徑 |

```sql
CREATE TABLE analysis_history (
    analysis_id   UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    sample_id     VARCHAR REFERENCES sample_registry(sample_id),
    analysis_type VARCHAR,    -- 'bulk_eda', 'eda_report', 'spatial_heatmap' ...
    parameters    JSON,       -- 分析參數 + 可選 generated_code / source / origin_id
    status        VARCHAR,    -- 'running' | 'completed' | 'failed' | 'stale'
    result_path   VARCHAR,
    l1_cache_id   UUID,
    requested_by  VARCHAR,
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    summary       VARCHAR,    -- ≤50 字結果摘要（語意搜尋品質上限）
    tool_id       UUID        -- 預留：未來 tools 表 FK，現為 NULL
);
```

---

### L2 Silver：Views

Views 是從 `analysis_history` 自動聚合的虛擬表，不佔額外儲存空間，查詢結果即時反映最新資料。

| View | 用途 | Token |
| --- | --- | --- |
| `analysis_index` | 依樣本 × 分析類型彙總執行次數、最後執行日期、成功／失敗數，Agent 每輪掃一眼即可掌握全局 | 0 |
| `promotion_candidates` | 列出 `reuse_count ≥ 3` 的動態程式碼，供 Code Promotion 流程評估是否升格為永久工具 | 0 |

---

## 五、完整查詢決策流程

```
使用者提問（Web UI / Telegram）
    │
    ├─[Step 1] bio_history_check()
    │   SQL 精確比對 analysis_history
    │   └─ 命中 → [Cache Hit Protocol]
    │       ① 告知命中（樣本、類型、完成時間）
    │       ② 顯示 parameters（分析條件 JSON）
    │       ③ 列出可用輸出（result_path 下的 .md / .png / .csv）
    │       ④ 詢問：「是否足夠？或需要調整參數重新執行？」
    │       ⑤ 使用者確認 → 結束 ｜ 需重跑 → Step 3
    │   └─ 未命中 → Step 2
    │
    ├─[Step 2] bio_history_search()
    │   HNSW cosine 語意搜尋 L1 快取
    │   └─ 相似度 ≥ 0.88 → [Cache Hit Protocol]（同 Step 1 命中流程）
    │   └─ 未命中 → Step 3
    │
    ├─[Step 3] 判斷分析路徑
    │   │
    │   ├─[3A] 標準分析（QC / 空間基因圖 / EDA）
    │   │       ├─ bio_check_l2_sufficiency()  ← 確認 l2_ready=true
    │   │       │   └─ false → 回傳轉換命令，停止
    │   │       └─ bio_run_spatial_eda / bio_run_bulk_eda
    │   │           → INSERT running → 分析 → UPDATE completed / failed
    │   │           → 結果寫入 L1 → 回傳
    │   │
    │   ├─[3B] 曾生成過類似程式碼？（Code Promotion 重用路徑）
    │   │       SQL: SELECT parameters->>'generated_code'
    │   │            FROM analysis_history
    │   │            WHERE analysis_type LIKE ? AND status='completed'
    │   │            ORDER BY completed_at DESC LIMIT 1
    │   │       → 找到 → 直接重用
    │   │           INSERT source='code_promotion', origin_id=首次 ID
    │   │       → 重用 ≥ 3 次 → 觸發 scan_candidates() 評估升格
    │   │
    │   └─[3C] 全新分析（Code Generation Loop）
    │           LLM 生成程式碼
    │           → 安全檢查（ALLOWED_IMPORTS / BLOCKED_PATTERNS）
    │           → 沙盒執行（sandbox_exec，timeout=60s）
    │           → plt.show() hook 自動捕獲 matplotlib 圖 → base64 回傳聊天框
    │           → 失敗 → 餵 traceback 給 LLM 修正（≤ 3 次）
    │           → 成功 → 存入 analysis_history.parameters["generated_code"]
    │           → 結果寫入 L1 → 回傳
    │
    └─[Step 4] L3 Pipeline 排程（~4 小時）
        有原始數據？
        └─ 是 → 排程 SpaceRanger / Kallisto → 完成後 L3→L2→L1
        └─ 否 → 通知使用者需上傳原始數據
```

> **工具生命週期**：3C 生成 → 存入 3B 可重用 → 重用 ≥3 次後升格回 3A 永久工具

---

## 六、分析歷史：兩階段寫入與狀態機

> Schema 詳見四章「資料庫 Schema 總覽」。

### 狀態機

```
分析開始
    │
    ▼
INSERT status='running'        ← 立刻寫入（程序崩潰也留下紀錄）
    │
    ├─ 分析成功 → UPDATE status='completed'
    │               result_path, summary, completed_at 同步更新
    └─ 分析失敗 → UPDATE status='failed'
                      completed_at 更新
                          ↑
              > 24h 未更新 → cleanup_stale_runs() 標為 'stale'
```

**為何開始時就要寫**：L3 Pipeline 約 4 小時，若只在完成時寫，中途崩潰這筆記錄消失。`running` 狀態確保「嘗試過」不會消失，也讓 `cleanup_stale_runs()` 能偵測殭屍任務。

### 核心寫入規則

| 規則 | 說明 |
|------|------|
| `analysis_history` 只 INSERT，永不 UPDATE 已完成記錄 | 每次重跑產生新紀錄，歷史完整保留 |
| 所有關鍵表寫入必須走 `safe_write()` | 寫入後立即 CHECKPOINT，縮小 ExFAT 斷電損壞視窗 |
| Agent 啟動時呼叫 `cleanup_stale_runs()` | 把 > 24h running 標為 stale |
| UPDATE failed / stale 也必須走 `safe_write()` | 確保異常路徑同樣受 CHECKPOINT 保護 |

### 三種 0-token 查詢模式

```sql
-- 模式 1：某樣本所有分析狀態
SELECT analysis_type, last_run_date, success_count, fail_count
FROM analysis_index WHERE sample_id = 'crc_official_v4';

-- 模式 2：確認特定分析是否已成功完成
SELECT COUNT(*) > 0 AS already_done
FROM analysis_history
WHERE sample_id = 'crc_official_v4'
  AND analysis_type = 'spatial_heatmap'
  AND status = 'completed';

-- 模式 3：本週時間軸
SELECT DATE_TRUNC('day', completed_at) AS date, COUNT(*) AS n
FROM analysis_history
WHERE completed_at >= NOW() - INTERVAL '7 days' AND status = 'completed'
GROUP BY 1 ORDER BY 1 DESC;
```

---

## 七、省 Token 搜尋策略

### 設計原則

**讓資料庫回答結構化問題，LLM 只處理剩下無法用 SQL 答的部分。**

```
問題類型                      處理方式                   Token 消耗
──────────────────────────────────────────────────────────────────
「XX 樣本做過什麼分析？」     SQL 查 analysis_index       0 token
「這週完成幾個分析？」         SQL GROUP BY date           0 token
「有沒有問過 CD45 分布？」    HNSW 語意搜尋（只傳摘要）    少量 token
「幫我解讀這份 QC 報告」      傳完整報告給 LLM            正常 token
```

### analysis_history vs. memory_recent

| 比較項目 | `analysis_history`（SQL，L2） | `memory_recent`（VSS，L1） |
|---------|-------------------------------|---------------------------|
| 查的是 | 「有沒有**做過**這件事」 | 「有沒有**問過類似**問題」 |
| 比對方式 | 精確（sample_id + analysis_type） | 語意相似度（cosine ≥ 0.88） |
| 時間紀錄 | ✅ started_at / completed_at | ❌ 只有 TTL |
| Token 消耗 | **0 token** | 少量（embedding API） |
| 壽命 | **永久** | TTL 7 天 |

> `analysis_history` 是**永久帳本**（SQL 精確查，0 token）。  
> `memory_recent` 是**語意去重器**，攔截「問法不同但意思相同」的重複查詢。

### L2 Parquet 如何壓縮 Token

Visium HD 原始矩陣：100,000 bins × 30,000 genes = 30 億數字，不可能傳給 LLM。

```sql
-- DuckDB SQL 先聚合，只傳 20 行結果給 LLM
SELECT gene_name, AVG(count) AS avg_expr
FROM 'silver/spatial_counts_crc_official_v4_8um/*.parquet'
WHERE in_tissue = TRUE
GROUP BY gene_name ORDER BY avg_expr DESC LIMIT 20
```

| 方式 | LLM 看到的資料量 | Token 消耗 |
|------|----------------|-----------|
| 原始矩陣直接傳 | 30 億數字 | 不可能 |
| pandas 讀入後傳 | 需 ~12 GB RAM | 正常 |
| **DuckDB SQL 壓縮** | **20 行摘要** | **極少** |

**Token 節省來源是 SQL 聚合**；DuckDB + Parquet 讓這個聚合在生資規模下不需匯入、不爆記憶體、直接可用。

---

## 八、Code Promotion 框架

動態程式碼（3C 路徑生成）在被重用 ≥ 3 次後自動評估升格為永久工具。

### 完整生命週期

```
3C：Claude 生成程式碼（沙盒執行成功）
    │
    ├── 存入 analysis_history.parameters["generated_code"]
    │
    │   [下次重用]
    ├── INSERT analysis_history
    │       source='code_promotion', origin_id=首次 analysis_id
    │
    │   [promotion_candidates VIEW 偵測 reuse_count ≥ 3]
    ├── code_promoter.review_candidate()
    │       LLM 審查：通用性 / 介面清晰 / 安全性
    │       程式碼以 <untrusted_code> 標籤隔離（防 prompt injection）
    │       └─ 不通過 → 繼續存在 analysis_history 供重用
    │       └─ 通過 →
    │           ├── code_promoter.write_draft()
    │           │       → analysis/candidates/<name>.py
    │           ├── 通知管理員（Web UI / Telegram）
    │           └── 管理員 /approve
    │                   → code_promoter.approve_candidate()
    │                       ├── candidates/<name>.py → analysis/<name>.py
    │                       ├── 寫入 tools/registry.json
    │                       └── Agent hot-reload → 正式加入 BIO_TOOLS ✅（3A）
    │
    └── [/reject] → code_promoter.reject_candidate()（刪除草稿）
```

### 資料庫追蹤

```sql
-- 重用時每次 INSERT 新紀錄
INSERT INTO analysis_history (..., parameters, status) VALUES (...,
    json_object('source',         'code_promotion',
                'origin_id',      '<首次 analysis_id>',
                'generated_code', '<程式碼文字>'),
    'completed');

-- promotion_candidates VIEW（已建立於 bio_memory.duckdb）
CREATE OR REPLACE VIEW promotion_candidates AS
SELECT parameters->>'origin_id' AS origin_id,
       analysis_type,
       COUNT(*)                  AS reuse_count,
       MAX(completed_at)         AS last_used
FROM analysis_history
WHERE parameters->>'source' = 'code_promotion' AND status = 'completed'
GROUP BY parameters->>'origin_id', analysis_type
HAVING COUNT(*) >= 3;
```

### 三層程式碼狀態

| 層級 | 位置 | 狀態 |
|------|------|------|
| 可重用 | `analysis_history.parameters["generated_code"]` | 已執行、可重用 |
| 候選 | `analysis/candidates/<name>.py` | 待管理員審核 |
| 正式 | `analysis/<name>.py` + `tools/registry.json` | 永久工具（3A） |

### 工具版本管理路徑

**現階段（工具 < 20 個）**：`tools/registry.json` 記錄 name / module / function / version / status，夠用。

**規模擴展後（工具 ≥ 20 個）**：在 `bio_memory.duckdb` 新增 `tools` 資料表：

```sql
CREATE TABLE tools (
    tool_id       UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name     VARCHAR NOT NULL,
    version       VARCHAR NOT NULL,         -- semver '1.2.0'
    module_path   VARCHAR NOT NULL,
    function_name VARCHAR NOT NULL,
    description   VARCHAR,
    parameters    JSON,
    status        VARCHAR DEFAULT 'active', -- 'candidate'|'active'|'deprecated'
    origin_id     UUID,                     -- FK → analysis_history
    git_commit    VARCHAR,
    created_at    TIMESTAMP DEFAULT now(),
    deprecated_at TIMESTAMP,
    UNIQUE (tool_name, version)
);
```

`analysis_history.tool_id` 外鍵直接指向 `tools`，解鎖跨版本查詢（「哪些分析用了已 deprecated 的工具？」）。

---

## 九、資料庫安全

### 風險對策

| 風險 | 來源 | 對策 |
|------|------|------|
| ExFAT 斷電損壞 | `/Volumes/NO NAME/` 無日誌 | `safe_write()` 每次寫入後立即 CHECKPOINT |
| `.wal` 殘留鎖住 DB | Python 程序被 kill | Agent 啟動時 `cleanup_stale_runs()` |
| `running` 殭屍狀態 | 程序中途中斷 | > 24h → 標為 `stale` |
| 多程序寫入衝突 | 多人同時查詢 | `asyncio.Lock` 序列化所有寫入 |
| Session 記憶體洩漏 | Web UI 長期運行 | TTL 24h 自動清理，每小時執行 |

### safe_write()

```python
def safe_write(con, sql, params=None):
    """寫入關鍵表後立即 CHECKPOINT，縮小 ExFAT 斷電損壞視窗。"""
    con.execute(sql, params or [])
    con.execute("CHECKPOINT")
```

**使用範圍**：`analysis_history`、`sample_registry` 的所有寫入（含 INSERT running / UPDATE completed / UPDATE failed / UPDATE stale）。L1 `memory_recent` 因頻率高且可重建，不需呼叫。

### 備份排程

```
scheduler/
├── backup_db.py          每日 02:00   EXPORT DATABASE → ~/bio_db_backups/（APFS，保留 7 天）
├── cleanup_l1_cache.py   每日 03:30   DELETE memory_recent WHERE expires_at < now()
├── rebuild_hnsw.py       每週日 03:00 DROP + CREATE INDEX（HNSW 不支援 incremental update）
└── scan_new_samples.py   每 30 分鐘  掃描 results_kallisto/ 登記新樣本至 sample_registry
```

---

## 十、推理引擎架構

### 雙後端設計

- **local**（預設）：Gemma 4 26B Vision IQ2_M，port 8080，離線/隱私/多模態圖片分析
- **claude**：claude-sonnet-4-6（可設定），需 `ANTHROPIC_API_KEY`，更強推理時切換

切換方式：Web UI sidebar「本機 / Claude」按鈕，即時生效，存 localStorage。

### 工具呼叫格式轉換

```
BIO_TOOLS（Anthropic input_schema 格式）
    │
    ├─ local backend → _to_openai_tools() → OpenAI function calling
    │                   → llama.cpp /v1/chat/completions
    │
    └─ claude backend → 直接使用 BIO_TOOLS + _convert_content()（image_url → Anthropic base64）
                        → anthropic.Anthropic().messages.create()
```

### 視覺分析（多模態）

```
用戶貼圖（附件按鈕 / Ctrl+V 貼上）
    → base64 data URI → ChatRequest.image_base64
    → handle_message() 組裝 image_url content block
    → Gemma 4 Vision 分析 → 工具呼叫
    → 分析圖（matplotlib）plt.show() hook 捕獲
    → SSE images[] 回傳聊天框 → img-card + ⬇ 下載
```

---

## 十一、Agent 工具清單（BIO_TOOLS）

| 工具 | 用途 | Token |
|------|------|-------|
| `bio_history_check` | 確認是否已有存檔（SQL 精確） | **0** |
| `bio_history_lookup` | 查詢分析歷史記錄 | **0** |
| `bio_history_timeline` | 近 N 天時間軸 | **0** |
| `bio_history_search` | 語意搜尋 L1 快取（只傳 summary） | 少量 |
| `bio_memory_query` | 從 L1 取回完整報告 | 少量 |
| `bio_check_l2_sufficiency` | 確認 l2_ready=true（spatial_eda 前必呼叫） | **0** |
| `bio_run_spatial_eda` | 執行空間轉錄體 EDA（需 l2_ready，含 QC 圖） | 正常 |
| `bio_run_bulk_eda` | 執行 Bulk RNA-seq EDA | 正常 |
| `bio_register_sample` | 登記新樣本至 sample_registry | **0** |
| `bio_execute_code` | 沙盒執行動態生成 Python（plt.show() 自動捕獲圖） | 正常 |

**呼叫順序原則**：
`bio_history_check` → `bio_history_search` → `bio_memory_query` → `bio_check_l2_sufficiency`（需 spatial 時）→ 分析工具 → `bio_execute_code`（最後手段）

---

## 十二、Web UI 架構

```
瀏覽器
    ├── GET  /                        → index.html（聊天介面）
    ├── GET  /history                 → history.html（分析歷史 + 縮圖預覽）
    ├── GET  /results/{id}            → 報告 HTML（含 base64 圖）
    ├── POST /api/chat                → SSE 串流（text/event-stream）
    │       events: status/ping/tool_calls/tokens/message/error/done
    │       message: { text, report_link, images[] }
    ├── GET  /api/history             → 歷史查詢 JSON
    ├── GET  /api/results/{id}/csv    → 下載 top_genes CSV
    ├── GET  /api/results/{id}/images → 取回報告圖片清單
    ├── GET  /api/backend             → 查詢推理後端狀態
    └── GET  /health                  → DB 健檢
```

### 圖片流向

```
分析工具執行
    └─ report_generator → QC 圖 base64 嵌入 .md 報告（result_path）
    └─ bio_execute_code → plt.show() hook → PNG → base64 嵌入工具結果
         │
         ▼ （executor thread，不阻塞 event loop）
web_app._extract_images_from_tool_calls()
    → 從 result_path .md 抽出 base64（regex: [A-Za-z0-9+/=\r\n]）
    → SSE message event images[]
         │
         ▼
前端 appendMsg() → img-card（圖片 + 檔名 + ⬇ 下載按鈕）
history.html → GET /api/results/{id}/images → 縮圖預覽列
```

### Session 管理

- 每個 tab UUID session_id，存 localStorage
- 每個 session 保留最近 12 輪（24 messages）含 tool 輪次完整歷史
- TTL 24h：每小時自動清理非活躍 session

---

## 十三、分析函式庫（analysis/）

| 模組 | 主要函數 | 狀態 |
|------|---------|------|
| `spatial_eda.py` | `plot_spatial()`, `top_genes()`, `cluster_summary()` | ✅ |
| `bulk_eda.py` | `generate_bulk_report()` — 兩階段寫入（running→completed/failed） | ✅ |
| `bulk_timeseries.py` | `timeseries_summary()`, `log2fc()`, `parse_timepoint_cols()` | ✅ |
| `pathway_scoring.py` | `score_pathways()`, `zscore_aggregate()`, `ssgsea_score()` | ✅ |
| `multiomics_integration.py` | `run_integration()`, `rna_protein_correlation()`, `lag_analysis()` | ✅ |
| `report_generator.py` | `run_full_eda_report()`, `write_report_to_history()` — 兩階段寫入 | ✅ |
| `history_query.py` | `query_history()` — 0 token SQL 介面 | ✅ |
| `embed.py` | `embed_text()` — bge-m3 本機 embedding | ✅ |
| `l1_cache.py` | `write_to_l1_cache()`, `search_cache()` | ✅ |
| `code_promoter.py` | `scan_candidates()`, `review_candidate()`, `approve_candidate()`, `reject_candidate()` | ✅ |

### 報告品質原則

每個分析函數產圖時，**同步生成精準文字描述**——這段文字的 embedding 品質決定了 L1 語意搜尋的天花板：

```
❌ 壞的摘要（搜尋命中率差）：
   "spatial heatmap saved"

✅ 好的摘要（搜尋命中率高）：
   "基因：CD8A，樣本：crc_official_v4，解析度：8µm
    CD8A 高表現集中於腫瘤邊緣，佔組織面積 18%
    圖檔：results/crc_official_v4/spatial_heatmap/20260515_CD8A.png"
```

---

## 十四、目錄結構

```
/Volumes/NO NAME/bio_DB/
│
├── CLAUDE.md                       ← 專案憲法（規範 + 架構 + 路徑）
├── PROGRESS.md                     ← 進度封存
├── plan_zh.md                      ← 本文件
├── pyproject.toml                  ← 依賴管理（uv）
├── pyrightconfig.json              ← ✅ Pyright 靜態分析設定
├── .env.example                    ← 環境變數範本
├── bio_memory.duckdb               ← 主 DuckDB（sample_registry + analysis_history + Views）
├── start_hermes.sh                 ← ✅ 一鍵啟動 llama-server（port 8080）+ FastAPI
│
├── config/
│   ├── settings.py                 ← 集中路徑與 API key（含 INFERENCE_BACKEND / CLAUDE_MODEL）
│   └── db_utils.py                 ← ✅ safe_write / cleanup_stale_runs / db_health_check
│
├── scripts/                        ← 一次性 L3→L2 轉換工具
│   ├── 00_init_db.py               ← ✅ 建立所有 Schema + Views
│   ├── 01_register_sample.py       ← ✅ 自動掃描登記樣本至 sample_registry
│   ├── 02_spatial_to_parquet.py    ← ✅ Visium HD → L2 Parquet（已驗證：416 MB）
│   └── bulk_rna/                   ← ✅ Kallisto → gene_counts TSV（5 支腳本）
│
├── analysis/                       ← 分析函式庫（Agent 呼叫）
│   ├── spatial_eda.py              ← ✅
│   ├── bulk_eda.py                 ← ✅ 兩階段寫入
│   ├── bulk_timeseries.py          ← ✅ 時序均值 + log2FC
│   ├── pathway_scoring.py          ← ✅ ssGSEA / Z-score（YAML 驅動）
│   ├── multiomics_integration.py   ← ✅ RNA-Protein 整合 + Spearman + 滯後
│   ├── report_generator.py         ← ✅ 兩階段寫入 + ≤50 字摘要 + QC 圖 base64 嵌入
│   ├── history_query.py            ← ✅ 0-token SQL 查詢
│   ├── embed.py                    ← ✅ bge-m3 本機 embedding
│   ├── l1_cache.py                 ← ✅ L1 快取讀寫
│   ├── code_promoter.py            ← ✅ Code Promotion：掃描/審查/升格/拒絕
│   └── candidates/                 ← 升格候選草稿暫存區
│
├── tools/
│   └── registry.json               ← ✅ 已上線工具清單（name/module/version/status）
│
├── gene_sets/
│   └── hair_follicle.yaml          ← ✅ OxPhos/TCA/FAO/Glycolysis/Cell_Cycle（小鼠）
│
├── server/
│   ├── agent.py                    ← ✅ Agent Loop + 10 個 BIO_TOOLS + 雙後端 + 視覺分析
│   ├── web_app.py                  ← ✅ FastAPI SSE + session TTL + 圖片 API
│   ├── code_executor.py            ← ✅ 沙盒執行（sandbox_exec + SecurityError）
│   ├── telegram_bot.py             ← 骨架已建（待 Telegram Token 正式啟用）
│   ├── bio_memory_server.py        ← MCP Server 骨架（Phase 9+）
│   └── static/
│       ├── index.html              ← ✅ 聊天介面（圖片上傳/回傳/下載）
│       └── history.html            ← ✅ 分析歷史 + 縮圖預覽
│
├── scheduler/
│   ├── backup_db.py                ← ✅ 每日 02:00
│   ├── cleanup_l1_cache.py         ← ✅ 每日 03:30
│   ├── rebuild_hnsw.py             ← ✅ 每週日 03:00
│   └── scan_new_samples.py         ← ✅ 每 30 分鐘
│
├── docs/
│   ├── DATA_INTEGRATION_GUIDE.md   ← ✅ 跨專案整合決策指南
│   ├── L3_DATA_INGEST_GUIDE.md
│   ├── TEST_DATABASE_INDEX.md
│   └── launchd_*.plist.example     ← 各排程 launchd 範本
│
├── tests/
│   ├── conftest.py
│   ├── test_init_db.py             ← 4 tests ✅
│   ├── test_phase2b.py             ← 14 tests ✅
│   ├── test_phase3.py              ← 15 tests ✅
│   ├── test_phase4.py              ← 19 tests ✅
│   ├── test_phase5.py              ← 28 tests ✅（openai SDK mock）
│   └── test_phase6.py              ← 23 tests ✅
│
├── results/                        ← 分析結果（含 .md 報告 + base64 QC 圖）
├── silver/                         ← L2 Parquet（scripts/ 寫入，analysis/ 唯讀）
├── gold/                           ← L1 快取 DuckDB（analysis/ 寫入）
│   └── hermes_cache.duckdb
├── proteome_data/
│   └── sHG_timeseries/             ← ✅ Perseus log2 intensity（0/24/48/72/96h）
├── bulk_rna_data/                  ← Bulk RNA Kallisto 輸出（84 樣本）
├── crc_visium_data/                ← ✅ CRC Visium HD L3（~39 GB，唯讀）
└── references/                     ← 技術文獻摘要（.md）
```

---

## 十五、跨專案整合規則

將其他專案的數據或分析方法併入 bio_DB 時，依下列優先順序：

1. **數據**：複製到對應目錄（`bulk_rna_data/`、`proteome_data/`）→ 登記至 `sample_registry`
2. **通用分析方法**：去除硬編碼路徑與生物特化常數後放入 `analysis/`
3. **生物特化邏輯**（特定基因清單、TF 網絡）：放入 `gene_sets/*.yaml`，不硬編碼
4. **高度特化方法**：保留在原專案，透過 `sys.path.insert` 呼叫 bio_DB 共用函數

詳細決策流程見 [docs/DATA_INTEGRATION_GUIDE.md](docs/DATA_INTEGRATION_GUIDE.md)。

---

## 十六、實作階段進度

| 階段 | 名稱 | 狀態 |
|------|------|------|
| 第一階段 | 環境建置 + Schema | ✅ 完成 |
| 第二階段 A | Visium HD → L2 Parquet | ✅ 完成（CRC 測試集，416 MB） |
| 第二階段 B | Bulk RNA-seq → L2 | ✅ TSV 完成（84 樣本）；Parquet 轉換待補 |
| 第二階段 C | Proteomics 整合 | ✅ 完成（sHG Perseus log2） |
| 第三階段 | 分析工具層 + 報告產生 | ✅ 完成（10 個 Agent 工具 + report_generator QC 圖） |
| 第三階段＋ | Code Promotion 框架 | ✅ 完成 |
| 第四階段 | L1 語意快取 | ✅ 完成（bge-m3 本機，1024-dim） |
| 第五階段 | Agent + 測試套件 | ✅ 完成（105/106 PASSED，openai SDK mock） |
| 第六階段 | 排程系統 | ✅ 4 個排程，launchd plist 範本齊備 |
| 第七階段 | 推理引擎雙後端 | ✅ 完成（local llama.cpp + Claude API 可切換） |
| 第八階段 | Web UI + 多模態 | ✅ 完成（圖片上傳/回傳/下載，SSE 串流，session TTL） |
| 第九階段 | 端對端驗證 | ⏳ 進行中（Claude API 切換驗證、launchd 排程啟用） |
| 第十階段 | Telegram Bot 正式啟用 | ⏳ 待 Telegram Token（骨架已完成） |
| 第十一階段 | Linux 部署 | ⏳ 待伺服器（路徑遷移、Docker 沙盒替換） |

---

## 十七、關鍵路徑對照

| 項目 | macOS 測試 | Linux 生產 |
|------|-----------|-----------|
| 主資料夾 | `/Volumes/NO NAME/bio_DB/` | `/mnt/space4/bio_lab_db/` |
| 主 DuckDB | `bio_DB/bio_memory.duckdb` | `bio_lab_db/bio_memory.duckdb` |
| L2 Parquet | `bio_DB/silver/` | `bio_lab_db/silver/` |
| L1 快取 | `bio_DB/gold/hermes_cache.duckdb` | `bio_lab_db/gold/` |
| 備份目標 | `~/bio_db_backups/`（APFS） | `/mnt/backup/bio_lab_db/` |

> 所有路徑集中於 `config/settings.py`，腳本內嚴禁硬編碼。

---

## 十八、參考文獻索引

| 文件 | 內容 | 對應章節 |
|------|------|---------|
| `references/duckdb.md` | DuckDB 引擎設計（SIGMOD 2019） | 三、六 |
| `references/duckdb_vss.md` | HNSW 向量搜尋 | 四、六 |
| `references/lakeharbor_icde2024.md` | 結構感知資料湖（ICDE 2024） | 三 |
| `references/agent_first_data_systems.md` | Agent-First 資料系統（2025） | 全章節 |
| `references/mcp_protocol.md` | MCP Server 骨架 | 十 |
| `references/anndata_scanpy.md` | 讀取 Visium HD | 十二、十三 |
| `references/memgpt.md` | 分層記憶模型（概念參考） | 三 |

---

## 附錄 A：設計決策與文獻依據

每個核心設計決策均有明確的文獻或技術來源，避免「憑感覺設計」。

### A1. 三層 Bronze / Silver / Gold 架構

**來源**：Medallion Architecture（Databricks Lake House）概念；結構感知資料湖 LakeHarbor（Hai et al., 2023）

**截取想法**：

- 原始數據不可變（Bronze），確保可重現性
- Silver 層做結構化轉換，集中計算一次而非每次查詢時重算
- Gold 層作為熱快取，用於低延遲存取

**本系統的調整**：Gold 層改用 HNSW 向量索引做語意搜尋（非傳統 BI Cube），適應自然語言查詢場景。

---

### A2. HNSW 向量語意搜尋

**來源**：DuckDB VSS 擴充（Müller et al., 2024）；HNSW 演算法（Malkov & Yashunin, 2018）

**截取想法**：

- HNSW 在高維向量（1024-dim）的 ANN 搜尋中兼顧速度（O(log N)）與精度
- cosine similarity 比 L2 distance 更適合語意相似度比較
- DuckDB 原生整合免去外部向量資料庫（Pinecone、Weaviate）的部署成本

**本系統的調整**：TTL 7 天 + 每週日完整重建索引（HNSW 不支援增量更新），以防索引碎片化。

---

### A3. Agent-First 查詢架構 + Token 省策

**來源**：Agent-First Data Systems（Trummer, 2025）；MemGPT 分層記憶模型（Packer et al., 2023）

**截取想法**：

- Agent 不應每次都「暴力傳全量資料給 LLM」，應先讓資料庫回答結構化問題
- MemGPT 的「主記憶 / 外部儲存 / 歸檔」分層概念 → 對應本系統的 L1 / L2 / L3

**本系統的調整**：把 MemGPT 的「記憶分頁換入換出」簡化為「SQL 精確查（0 token）→ 語意搜尋（少量 token）→ 完整報告（正常 token）」三段防線，更適合生資批次分析場景（非對話連續性場景）。

---

### A4. 兩階段寫入 + 狀態機

**來源**：資料庫可靠性設計通例（WAL / crash recovery）；長時間批次作業的 Saga pattern（Garcia-Molina & Salem, 1987）

**截取想法**：

- 長時間任務（SpaceRanger ~4 小時）若只在完成時寫入，崩潰後記錄消失
- 「先寫 running，完成再更新」確保任何崩潰都留下痕跡

**本系統的調整**：加入 `stale` 狀態（> 24h running 自動標記），並以 `safe_write()` 在 ExFAT 無日誌環境下保護每次寫入。

---

### A5. Code Promotion 自動升格框架

**來源**：無直接文獻對應；靈感來自 A/B 測試逐步推廣（progressive rollout）與函數式程式設計中的 memoization

**截取想法**：

- 動態生成的程式碼不應永遠停留在「不可信任」狀態
- 重用 ≥ 3 次代表社群驗證（類似 GitHub star 的隱性信號）
- LLM 審查 + 管理員人工核准 = 雙重把關，確保自動化不失控

**本系統的原創設計**：`promotion_candidates` VIEW 自動偵測重用次數，觸發升格流程，無需人工追蹤。

---

### A6. 多模態視覺分析

**來源**：Gemma 4 Vision（Google DeepMind, 2025）；llama.cpp OpenAI-compatible API（Gerganov et al., 2023）

**截取想法**：

- 本機 Vision LLM 可在不上傳敏感實驗圖到雲端的前提下做視覺分析
- OpenAI `image_url` content block 格式已成事實標準，llama.cpp 原生支援

**本系統的調整**：`plt.show()` hook 自動捕獲 matplotlib 圖並回傳聊天框，解決「分析結果圖無法直接顯示於對話」的問題。

---

## 附錄 B：驗收標準與驗證方法

系統設計有四個核心目標（源自第二章「問題」）。每個目標對應可量測的驗收指標。

### B1. 消除重複運算

**設計目標**：相同樣本的相同分析不重複執行。

- **L1 命中率 ≥ 80%**（穩定使用後）：查詢 `SELECT COUNT(*) FROM memory_recent WHERE created_at > NOW() - INTERVAL '7 days'`
- **`bio_history_check` 正確攔截**：`tests/test_phase5.py::test_history_check_returns_done`
- **重複查詢不觸發新 running**：查詢後確認 `analysis_history` 筆數未增加

---

### B2. Token 消耗可控

**設計目標**：結構化問題由 SQL 回答（0 token），LLM 只處理無法 SQL 化的部分。

- **`bio_history_check/lookup/timeline` 不呼叫 LLM**：單元測試確認工具函數直接回傳 SQL 結果
- **傳給 LLM 的資料 ≤ 50 行**：`report_generator.py` 輸出格式審查
- **月 Token 消耗在預算內**：部署後 Anthropic Dashboard 監控

---

### B3. 分析可追溯

**設計目標**：每次分析都有完整記錄，可追溯由誰、何時、對哪個樣本做了什麼。

- **每次分析後 `analysis_history` 有記錄**：`tests/test_phase5.py::test_history_written_after_analysis`
- **`analysis_index` VIEW 正確彙總**：`tests/test_init_db.py` View 正確性測試
- **崩潰後 running → stale**：`tests/test_phase2b.py::test_cleanup_stale_runs`

---

### B4. 使用門檻低

**設計目標**：實驗室成員不需懂命令列，透過自然語言即可取得分析結果。

- **全程無需 CLI**：端對端手動測試（index.html → SSE → img-card 收到結果圖）
- **圖片上傳可觸發視覺分析**：上傳 QC 圖，確認 Gemma 4 Vision 回應（附件按鈕 / Ctrl+V 貼上）
- **歷史縮圖預覽**：history.html → 點擊「預覽」按鈕顯示縮圖列
- **5 位成員可自行使用**：部署後使用者調查（定性）

---

### B5. 數據安全（ExFAT 環境）

**設計目標**：斷電或程序崩潰後資料庫不損壞，分析歷史不遺失。

- **`safe_write()` 每次寫入後 CHECKPOINT**：`config/db_utils.py` 程式碼審查
- **每日備份成功**：執行後確認 `~/bio_db_backups/` 有新檔（APFS 分區）
- **備份還原後筆數一致**：`python scheduler/backup_db.py --restore` 後查詢比對

---

## 附錄 C：未來擴充（暫不在主計畫內）

### LLMLingua — 中期記憶壓縮

L1 TTL 7 天到期後，若報告仍有查詢價值，可透過 LLMLingua 壓縮至 1/20 大小，延伸為「中期記憶」（TTL 90 天）。引入時機：L1 快取量超過閾值，或 Token 費用明顯上升。

### tools 資料表（工具 ≥ 20 個時升級）

詳見第八章「工具版本管理路徑」。觸發時機：工具庫超過 20 個，或有多人 `/approve` 並發需求。

### 圖表語意搜尋

不採用 CLIP 圖像向量搜尋（生資圖表訓練資料不足，無法區分同類熱圖）。正確做法：產圖時同步呼叫 `report_generator` 寫入精準文字描述，文字 embedding 比 CLIP 更準確。

---

## 附錄 D：當前狀態快照（2026-05-17）

> 此為建置時期的進度快照，詳細完成項目與待辦事項見 [PROGRESS.md](PROGRESS.md)。

### 系統環境

| 項目 | 內容 |
| ---- | ---- |
| 測試平台 | macOS `/Volumes/NO NAME/bio_DB/`（ExFAT 外接硬碟） |
| 目標平台 | Linux `/mnt/space4/bio_lab_db/`（生產部署） |
| Demo 數據 | CRC Visium HD L2 Parquet（416 MB）+ Bulk RNA Kallisto（84 樣本）+ sHG Proteomics |
| 推理引擎 | 本機 llama.cpp Gemma 4 Vision（port 8080）/ Claude API（可切換） |
| 前端介面 | FastAPI Web UI（`server/web_app.py`，port 8000） |
| 測試覆蓋 | 105/106 PASSED（1 個既有路徑問題，非程式碼問題） |

### 實作階段摘要

Phase 1–8 已全數完成，涵蓋 Schema 建置、L2 Parquet 轉換、分析函式庫、L1 語意快取、Agent Loop、排程系統、雙推理後端、Web UI 多模態。詳細各階段狀態見**十六章**。

### 下一步優先順序

#### 現階段（本機可做）

- 端對端測試：填入 `ANTHROPIC_API_KEY`，驗證 Claude API 切換
- 啟用 `launchd_scan_samples.plist` 排程

#### 接著（需 Telegram Token）

- Telegram Bot 正式啟用（骨架已完成於 `server/telegram_bot.py`）

#### 之後（需 Linux 伺服器）

- 路徑設定遷移（`config/settings.py`）
- Docker 沙盒替換 `code_executor.py`
- FASTQ 自動 Kallisto 觸發
- 5 位實驗室成員實際使用驗證

---

## References

Garcia-Molina, H., & Salem, K. (1987). Sagas. *ACM SIGMOD Record*, 16(3), 249–259. [https://doi.org/10.1145/38714.38742](https://doi.org/10.1145/38714.38742)

Gerganov, G., et al. (2023). *llama.cpp: Efficient LLM inference in C/C++*. GitHub. [https://github.com/ggerganov/llama.cpp](https://github.com/ggerganov/llama.cpp)

Google DeepMind. (2025). *Gemma 4 Technical Report*. Google DeepMind.

Hai, R., Quix, C., & Jarke, M. (2023). LakeHarbor: A structure-aware data lake architecture. *Proceedings of the 39th IEEE International Conference on Data Engineering (ICDE)*.

Malkov, Y. A., & Yashunin, D. A. (2018). Efficient and robust approximate nearest neighbor search using hierarchical navigable small world graphs. *IEEE Transactions on Pattern Analysis and Machine Intelligence*, 42(4), 824–836. [https://doi.org/10.1109/TPAMI.2018.2889473](https://doi.org/10.1109/TPAMI.2018.2889473)

Müller, L., Giceva, J., & Raasveldt, M. (2024). *DuckDB-VSS: Vector similarity search in DuckDB*. Proceedings of the VLDB Endowment.

Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., & Gonzalez, J. E. (2023). MemGPT: Towards LLMs as operating systems. *arXiv preprint arXiv:2310.08560*.

Raasveldt, M., & Mühleisen, H. (2019). DuckDB: An embeddable analytical database. *Proceedings of the 2019 ACM SIGMOD International Conference on Management of Data*, 1981–1984. [https://doi.org/10.1145/3299869.3320212](https://doi.org/10.1145/3299869.3320212)

Trummer, I. (2025). From databases to agent-first data systems. *arXiv preprint arXiv:2502.08902*.
