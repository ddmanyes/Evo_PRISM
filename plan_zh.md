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
| AI Agent 框架 | **自製輕量 Agent + Claude API** | 接收訊息、工具呼叫、管理排程；不用 Hermes（需 GPU 自架，本專案規模不值得） |
| 使用者介面 | Telegram Bot（或 LINE / Slack） | 依實驗室實際使用習慣決定 |
| 數據倉儲引擎 | DuckDB + Apache Parquet | L2 特徵儲存；SQL 直接查詢 Parquet，把百萬行矩陣壓縮成摘要後才傳給 LLM，節省 token 的關鍵機制 |
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
    ├─[Step 1] bio_history_check()              ← 0 token，SQL 精確比對
    │   analysis_history 已有相同 sample+type？
    │   └─ 是 → 直接回傳存檔路徑 / 摘要（< 1 秒，0 token）
    │   └─ 否 → 繼續 Step 2
    │
    ├─[Step 2] L1 語意搜尋                      ← HNSW cosine ≥ 0.88
    │   memory_recent 有語意相似的過去報告？
    │   └─ 是 → 回傳快取報告（< 1 秒）
    │   └─ 否 → 繼續 Step 3
    │
    ├─[Step 3] L2 特徵查詢 + 工具選擇           ← ~30 秒
    │   silver/ 有對應 Parquet？
    │   └─ 否 → 繼續 Step 4
    │   └─ 是 → 判斷工具路徑：
    │       │
    │       ├─ [3A] 標準分析（QC / 空間基因圖 / clustering）？
    │       │         → Mode 1：呼叫 analysis/ 預定義工具
    │       │         → 結果寫入 L1 + analysis_history → 回傳
    │       │
    │       ├─ [3B] 曾生成過類似程式碼？        ← Code Promotion 重用路徑
    │       │         SQL: SELECT parameters->>'generated_code'
    │       │              FROM analysis_history
    │       │              WHERE analysis_type LIKE ? AND status = 'completed'
    │       │              ORDER BY completed_at DESC LIMIT 1
    │       │         → 找到 → 直接重用（節省生成 token）
    │       │         → 重用次數 ≥ 3 → 評估是否升格為 analysis/ 正式工具
    │       │         → 結果寫入 L1 + analysis_history（reuse_count +1）→ 回傳
    │       │
    │       └─ [3C] 全新分析，無既有程式碼？    ← Code Generation Loop
    │                 Mode 2：Claude 生成程式碼
    │                 → 安全檢查（ALLOWED_IMPORTS / BLOCKED_PATTERNS）
    │                 → 沙盒執行 → 失敗則餵 traceback 給 Claude 修正（≤ 3 次）
    │                 → 成功 → 程式碼存入 analysis_history.parameters["generated_code"]
    │                 → 結果寫入 L1 → 回傳
    │
    └─[Step 4] L3 Pipeline 排程                 ← ~4 小時
        有原始數據（crc_visium_data/ 或 /mnt/space4/）？
        └─ 是 → 排程 SpaceRanger / Kallisto → 完成後依序寫入 L2 → L1 → 回傳
        └─ 否 → 通知使用者需上傳原始數據
```

> **工具生命週期小結**：3A（永久）→ 3C 生成後存入 3B（可重用）→ 重用 ≥3 次後升格回 3A。

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

### analysis_history vs. memory_recent — 兩個工具，兩個用途

> ⚠️ **常見誤解**：VSS（向量搜尋）不是用來查時間紀錄的。時間紀錄由 `analysis_history` + SQL 負責，兩者解決完全不同的問題。

#### 核心差異

| 比較項目 | `analysis_history` + SQL（L2） | `memory_recent` + VSS（L1） |
|---------|-------------------------------|----------------------------|
| **查的是** | 「有沒有**做過**這件事」 | 「有沒有**問過類似**的問題」 |
| **比對方式** | 精確比對（sample_id、analysis_type） | 語意相似度（向量距離 cosine ≥ 0.88） |
| **時間紀錄** | ✅ 有（started_at、completed_at） | ❌ 無（只有 TTL 到期時間） |
| **token 消耗** | **0 token**（純 SQL） | 少量（需 embedding API） |
| **儲存內容** | 元數據（樣本、類型、時間、狀態、路徑、50字摘要） | 完整報告文字 + 向量嵌入（FLOAT[1536]） |
| **壽命** | **永久**（不刪除） | **TTL 7 天**（到期自動清除） |
| **回答的問題** | 「CRC 樣本做過 QC 嗎？誰做的？什麼時候？」 | 「有沒有問過和免疫細胞分布有關的問題？」 |

#### VSS 真正解決的問題：語意去重

SQL 精確比對無法處理「同一個意思，不同文字」的情況：

```
使用者 A 問：「CRC 樣本裡 CD8+ T 細胞在哪裡？」
使用者 B 問：「腸癌組織的細胞毒性 T 淋巴球分布？」

→ 意思一樣，文字完全不同
→ SQL 精確比對：找不到重複 ✗
→ VSS cosine 相似度：0.91 ≥ 0.88，命中快取 ✅  不需重跑分析
```

**VSS 做的事**：把過去的分析報告轉成向量存起來，新問題進來時用向量距離判斷「這個問題的答案我之前算過了嗎？」—— 即使問法完全不同。

#### 時間紀錄是誰的責任？

時間紀錄完全在 `analysis_history`（SQL，0 token），與 VSS 無關：

```sql
-- 「這週做了哪些分析？」→ SQL 直接回答，完全不需要 VSS
SELECT sample_id, analysis_type, completed_at, requested_by
FROM analysis_history
WHERE completed_at >= NOW() - INTERVAL '7 days'
  AND status = 'completed'
ORDER BY completed_at DESC
```

#### 一句話總結

> `analysis_history` 是**永久帳本**，記下「誰、何時、做了什麼」的事實（SQL 精確查，0 token）。  
> `memory_recent` + VSS 是**語意去重器**，攔截「問法不同但意思相同」的重複查詢（向量距離判斷，少量 token）。  
> 兩者互補，缺一不可。

### 時間紀錄管理

#### 狀態機：三個狀態

```
分析開始
    │
    ▼
 running          ← 立刻寫入，started_at = now()
    │
    ├─ 成功 → completed   completed_at、result_path、summary 同步更新
    └─ 失敗 → failed      completed_at、錯誤原因寫入 parameters
```

**開始時就寫入的原因**：若只在完成時寫，程式崩潰或 L3 pipeline 中斷（~4 小時），這筆分析會完全沒有紀錄。`running` 狀態確保「曾經嘗試過」不會消失。

#### 完整寫入流程

```python
def run_analysis(sample_id, analysis_type, params):
    # Step 1：分析開始，立刻寫入 running
    analysis_id = insert_history(
        sample_id=sample_id,
        analysis_type=analysis_type,
        parameters=params,
        status="running",
        started_at=now()
    )

    try:
        # Step 2：執行分析、生成圖表
        result = compute(...)
        path = save_figure(result)

        # Step 3：生成文字描述（語意搜尋的基礎）
        report_text = make_report_text(result, path)
        summary = make_summary(result)  # 50 字以內

        # Step 4：更新為 completed
        update_history(analysis_id,
            status="completed",
            completed_at=now(),
            result_path=path,
            summary=summary
        )

        # Step 5：寫入 L1 快取
        cache_id = insert_l1_cache(sample_id, report_text)
        update_history(analysis_id, l1_cache_id=cache_id)

    except Exception as e:
        # 失敗也要記錄，不留盲點
        update_history(analysis_id,
            status="failed",
            completed_at=now(),
            parameters={**params, "error": str(e)}
        )
        raise
```

#### 重跑原則：永遠新增，不覆蓋

```python
# ❌ 錯誤：UPDATE 舊紀錄（會抹去歷史）
UPDATE analysis_history SET status='completed' WHERE sample_id=X

# ✅ 正確：永遠 INSERT 新紀錄
INSERT INTO analysis_history (...) VALUES (...)
# analysis_index VIEW 用 MAX(completed_at) 自動顯示最新一次
```

重跑後，`analysis_history` 保留完整歷史：

```
analysis_id  analysis_type    status     completed_at
──────────────────────────────────────────────────────
uuid-001     spatial_heatmap  failed    2026-05-10   ← 保留
uuid-002     spatial_heatmap  completed 2026-05-15   ← 最新（index 顯示此筆）
```

#### 三種查詢模式（全部 0 token）

```sql
-- 模式 1：某樣本的分析狀態總覽
SELECT analysis_type, last_run_date, success_count, fail_count
FROM analysis_index
WHERE sample_id = 'official_v4';

-- 模式 2：確認特定分析是否已成功完成（Agent 呼叫前先確認，避免重跑）
SELECT COUNT(*) > 0 AS already_done
FROM analysis_history
WHERE sample_id = 'official_v4'
  AND analysis_type = 'spatial_heatmap'
  AND parameters->>'gene' = 'CD8A'
  AND status = 'completed';

-- 模式 3：本週時間軸
SELECT DATE_TRUNC('day', completed_at) AS date,
       COUNT(*) AS analyses_done,
       STRING_AGG(sample_id || '/' || analysis_type, ', ') AS detail
FROM analysis_history
WHERE completed_at >= NOW() - INTERVAL '7 days'
  AND status = 'completed'
GROUP BY 1 ORDER BY 1 DESC;
```

#### 清理原則

| 狀態 | 清理策略 | 原因 |
|------|---------|------|
| `completed` | **永不刪除** | 永久帳本，歷史不可改寫 |
| `failed` | **永不刪除** | 失敗紀錄是診斷依據 |
| `running` 超過 24 小時 | 標記為 `stale`，定期清理 | 代表程式已崩潰，不會自行完成 |

```sql
-- 每日排程：清理殭屍任務
UPDATE analysis_history
SET status = 'stale'
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '24 hours';
```

#### L3 長時間任務（~4 小時）的進度追蹤

```sql
-- pipeline 執行中，定期更新進度百分比（寫入 parameters JSON）
UPDATE analysis_history
SET parameters = json_merge(parameters, '{"progress": 0.68, "stage": "alignment"}')
WHERE analysis_id = ? AND status = 'running';

-- Telegram 查詢進度 → 0 token
SELECT parameters->>'progress' AS progress,
       parameters->>'stage'    AS stage,
       started_at,
       AGE(NOW(), started_at)  AS elapsed
FROM analysis_history
WHERE analysis_id = ?;
```

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

### ✅ Agent 框架決策：自製輕量 Agent + Claude API

**不採用 Hermes（NousResearch）**，原因：
- Hermes 70B 需要 ~40GB VRAM GPU 自架 inference server，本專案伺服器無此資源
- 實驗室規模（月百次查詢）下 Claude API 費用極低，Prompt Cache 後更省
- Claude 推理能力與工具呼叫遠優於 Hermes，生資領域問題理解更準確

### Claude API 的角色分工

> **Claude = 大腦（推理決策）；Python 工具 = 雙手（實際執行）**
> Claude API 本身不直接碰檔案或資料庫，所有 I/O 都透過工具呼叫由 Python 完成。

| 任務 | Claude API | Python 工具 |
|------|-----------|------------|
| 理解使用者自然語言意圖 | ✅ 直接處理 | — |
| **資料建立**（寫 DuckDB、Parquet） | 決定呼叫哪個工具 | ✅ 實際寫入 |
| **檔案分析**（.h5ad、.parquet） | 處理 Python 回傳的摘要數字 | ✅ 讀取並計算 |
| **圖表生成**（matplotlib 熱圖） | 決定要畫什麼基因 | ✅ 畫圖、存檔 |
| **分析歷史管理**（DuckDB SQL） | 呼叫 `bio_history_*` 工具 | ✅ 執行 SQL |
| 讀取文字報告、Markdown | ✅ Files API 直接讀取 | — |
| 整理結果回傳 Telegram | ✅ 直接生成自然語言 | — |

### 核心 Agent Loop（約 50 行）

```python
# server/agent.py
async def handle_message(user_msg: str) -> str:
    response = anthropic.messages.create(
        model="claude-sonnet-4-6",
        system=SYSTEM_PROMPT,        # Prompt Cache：只算第一次
        messages=[{"role": "user", "content": user_msg}],
        tools=BIO_TOOLS              # bio_history_check、plot_spatial 等工具定義
    )

    # Claude 決定呼叫工具
    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input)  # Python 執行
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result)
                })
        # 把工具結果還給 Claude 繼續推理
        response = anthropic.messages.create(
            model="claude-sonnet-4-6",
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user",      "content": user_msg},
                {"role": "assistant", "content": response.content},
                {"role": "user",      "content": tool_results}
            ],
            tools=BIO_TOOLS
        )

    return response.content[0].text

# Telegram Bot 接收訊息 → agent loop → 回傳
async def telegram_handler(update, context):
    reply = await handle_message(update.message.text)
    await update.message.reply_text(reply)
```

### Prompt Cache 節省費用

系統 prompt（工具定義 + 分析規則）約 2,000 token，每次對話都需要。
Anthropic Prompt Cache 讓重複的 prefix 只計算一次：

```
未快取：每次對話 2,000 token × $3/百萬 = $0.006/次
已快取：每次對話 2,000 token × $0.30/百萬 = $0.0006/次
→ 節省 90% 的系統 prompt 費用
```

---

### 雙模式分析：預定義工具 vs 動態程式碼生成

Agent 面對兩類需求，採用不同執行路徑：

```
使用者需求
    │
    ├─► 標準分析（QC、基因表達、clustering）
    │       └─► Mode 1：呼叫預定義工具（`analysis/` 模組）
    │               → 穩定、可重現、速度快
    │
    └─► 非標準分析（「幫我算 D1 和 D2 樣本的 Moran's I 空間自相關」）
            └─► Mode 2：動態程式碼生成 + 沙盒執行
                    → Claude 寫程式碼 → Python 執行 → 錯誤回饋 → 修正重試
```

#### Mode 2：動態 Code Generation Loop

```python
# server/code_executor.py

ALLOWED_IMPORTS = {
    "duckdb", "pandas", "numpy", "scipy", "scanpy",
    "anndata", "matplotlib", "seaborn", "squidpy"
}
BLOCKED_PATTERNS = ["os.system", "subprocess", "eval(", "exec(", "__import__", "open("]

async def code_generation_loop(task: str, context: dict, max_retries: int = 3) -> dict:
    """
    Claude 寫程式碼 → 沙盒執行 → 如果有錯 → Claude 讀 traceback → 修正
    成功後：結果存入 analysis_history，程式碼存入 parameters
    """
    messages = [{"role": "user", "content": CODE_GEN_PROMPT.format(task=task, context=context)}]

    for attempt in range(max_retries):
        response = claude.messages.create(model="claude-sonnet-4-6", messages=messages)
        code = extract_code(response)

        if not is_safe(code):          # 安全檢查：禁止危險 import / pattern
            raise SecurityError(code)

        result = sandbox_exec(code)    # 受限 Python 環境執行

        if result.success:
            return {"code": code, "output": result.output, "attempt": attempt + 1}

        # 失敗：把 traceback 餵回 Claude 讓它自己修正
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user",      "content": f"執行錯誤：\n{result.traceback}\n請修正程式碼。"}
        ]

    raise MaxRetriesExceeded(task)
```

#### ⭐ Code Promotion：程式碼升格為永久工具

**問題**：相同分析邏輯若每次都重新生成，不只浪費 token，還可能產出不一致的結果。

**解法**：成功的程式碼存入 `analysis_history.parameters`，Claude 在下次接到類似需求時判斷是否可重用。當某段程式碼被重複呼叫 N 次後，由 Claude 評估是否值得升格為正式工具。

```
一次性分析成功
    │
    ├─► 程式碼存入 analysis_history.parameters["generated_code"]
    │
    ├─► 下次類似需求：Claude 先查 analysis_history → 找到舊程式碼 → 直接重用
    │       SQL：SELECT parameters->>'generated_code'
    │            FROM analysis_history
    │            WHERE analysis_type = ? AND status = 'completed'
    │            ORDER BY completed_at DESC LIMIT 1
    │
    └─► 重用次數 ≥ 3 次後：Claude 評估是否升格
            評估標準：
            ① 邏輯通用（不依賴特定樣本 ID）
            ② 有明確輸入/輸出介面
            ③ 本人（管理員）確認
            → 通過 → 移入 analysis/ 成為正式工具
            → 不通過 → 繼續存在 analysis_history 供重用
```

**三層程式碼生命週期**：

| 層級 | 位置 | 狀態 | 描述 |
|------|------|------|------|
| L0 草稿 | 記憶體（當次對話） | 一次性 | Claude 剛生成、未驗證 |
| L1 歷史 | `analysis_history.parameters` | 可重用 | 執行成功、有 traceback 記錄 |
| L2 工具 | `analysis/` 模組 | 永久工具 | 泛化後移入，加入 `BIO_TOOLS` |

> 這個設計讓系統的工具庫隨使用自然成長——不需要人工預設所有分析場景，實際用到的才會被固化。

---

#### Code Promotion 工程實作細節

##### Step 1 — 資料庫追蹤重用次數

重用舊程式碼時，每次都 INSERT 一筆新記錄並帶上來源標記，讓 SQL 統計次數：

```sql
-- 重用時寫入（source = code_promotion，origin_id 指向首次生成那筆）
INSERT INTO analysis_history
    (sample_id, analysis_type, parameters, status, requested_by, started_at, completed_at, summary)
VALUES (
    ?, ?,
    json_object(
        'source',         'code_promotion',
        'origin_id',      '<首次生成的 analysis_id>',
        'generated_code', '<程式碼文字>'
    ),
    'completed', ?, now(), now(), ?
);
```

用 View 自動彙總候選清單（0-token，agent loop 開始時掃一眼）：

```sql
CREATE OR REPLACE VIEW promotion_candidates AS
SELECT
    parameters->>'origin_id'   AS origin_id,
    analysis_type,
    COUNT(*)                    AS reuse_count,
    MAX(completed_at)           AS last_used
FROM analysis_history
WHERE parameters->>'source' = 'code_promotion'
  AND status = 'completed'
GROUP BY parameters->>'origin_id', analysis_type
HAVING COUNT(*) >= 3;
```

##### Step 2 — Claude 審查通用性

偵測到 `reuse_count ≥ 3` 後，把程式碼送給 Claude 做三項審查：

```python
PROMOTION_PROMPT = """
以下程式碼已被重用 {reuse_count} 次，評估是否適合升格為永久工具：

{code}

請判斷：
① 邏輯通用？（無硬編碼的 sample_id / 路徑）
② 有清楚的輸入/輸出介面？（可包裝成 def func(sample_id, **kwargs) -> dict）
③ 有無安全疑慮？

回答 JSON：{{"promote": true/false, "reason": "...", "suggested_name": "snake_case"}}
"""
```

不通過 → 什麼都不做，繼續留在 `analysis_history` 供重用。

##### Step 3 — 生成標準化函數，存入 candidates/

審查通過後，Claude 將生成程式碼重構為正式函數格式，寫入暫存區：

```
analysis/
├── spatial_eda.py          ← 正式工具（已上線）
├── history_query.py        ← 正式工具（已上線）
└── candidates/             ← 待審區（Claude 自動生成，管理員確認後搬移）
    └── morans_i_spatial.py ← 範例：Moran's I 升格草稿
```

生成的草稿格式範例：

```python
# analysis/candidates/morans_i_spatial.py
# [AUTO-GENERATED] reuse_count=4, origin_id=abc-123, promoted_at=2026-05-15
# [PENDING REVIEW] 管理員確認後執行 /approve morans_i_spatial

def run_morans_i(sample_id: str, gene: str, **kwargs) -> dict:
    """計算指定基因的 Moran's I 空間自相關係數。"""
    import duckdb, numpy as np
    # ... Claude 重構後的程式碼 ...
    return {"gene": gene, "morans_i": value, "p_value": p}
```

Telegram 同時通知管理員：

```
[Hermes] 🔔 升格候選：morans_i_spatial
已重用 4 次，Claude 審查通過。
草稿已存入 analysis/candidates/morans_i_spatial.py
回覆 /approve morans_i_spatial 或 /reject morans_i_spatial
```

##### Step 4 — 管理員確認，寫入 registry.json

`BIO_TOOLS` 不寫死在程式碼中，改從 `tools/registry.json` 動態載入：

```json
[
  {
    "name": "plot_spatial_gene",
    "module": "analysis.spatial_eda",
    "function": "plot_spatial_gene",
    "description": "繪製基因的空間表達熱圖",
    "parameters": {"sample_id": "str", "gene": "str"}
  },
  {
    "name": "morans_i_spatial",
    "module": "analysis.morans_i_spatial",
    "function": "run_morans_i",
    "description": "計算基因的 Moran's I 空間自相關係數",
    "parameters": {"sample_id": "str", "gene": "str"}
  }
]
```

`/approve morans_i_spatial` 執行三個動作：
1. `candidates/morans_i_spatial.py` → `analysis/morans_i_spatial.py`
2. 在 `registry.json` 新增一筆記錄
3. Agent 重啟（或 hot-reload）後新工具立即可用

##### 完整升格流程圖

```
Mode 2 Code Gen 成功
    │
    ├── 程式碼 → analysis_history.parameters["generated_code"]
    │
    │   [每次重用]
    ├── INSERT analysis_history（source=code_promotion, origin_id=首次 ID）
    │
    │   [promotion_candidates VIEW 偵測 reuse_count ≥ 3]
    ├── Claude 審查（通用性 / 介面 / 安全）
    │       └─ 不通過 → 繼續重用，不升格
    │       └─ 通過 →
    │           ├── Claude 重構 → analysis/candidates/<name>.py
    │           ├── Telegram 通知管理員
    │           └── 管理員 /approve
    │                   ├── 搬移至 analysis/<name>.py
    │                   ├── 寫入 tools/registry.json
    │                   └── Agent hot-reload → 正式加入 BIO_TOOLS ✅
    │
    └── [不通過或 /reject] → 繼續存在 analysis_history 供重用
```

---

#### 工具版本管理：規模擴展路徑

##### 現階段（工具 < 20 個）：registry.json

`tools/registry.json` 記錄當前版本與 metadata，`analysis_history.parameters` 內嵌版本字串：

```json
{ "tool_name": "morans_i_spatial", "tool_version": "1.0.0", "gene": "PTPRC" }
```

簡單夠用，無需額外基礎設施。

> **現在就該做的一件事**：`analysis_history` 預留 `tool_id UUID` 欄位（允許 NULL），等規模到了直接填入，不需改 schema。

---

##### 規模擴展後（工具 ≥ 20 個）：tools 資料表

`registry.json` 在工具增多後面臨三個無法解決的問題：
- 無法跨版本查詢（「哪些分析用了 deprecated 的舊版？」）
- 無法做影響分析（「改 morans_i v1.0 會影響幾筆歷史？」）
- 多人 `/approve` 並發時 JSON 檔案互相覆蓋

此時在 `bio_memory.duckdb` 新增 `tools` 表：

```sql
CREATE TABLE tools (
    tool_id       UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name     VARCHAR NOT NULL,           -- 'morans_i_spatial'
    version       VARCHAR NOT NULL,           -- semver '1.2.0'
    module_path   VARCHAR NOT NULL,           -- 'analysis.morans_i_spatial'
    function_name VARCHAR NOT NULL,           -- 'run_morans_i'
    description   VARCHAR,
    parameters    JSON,                       -- 輸入 schema
    status        VARCHAR DEFAULT 'active',   -- 'candidate' | 'active' | 'deprecated'
    origin_id     UUID,                       -- FK → analysis_history（從哪次生成升格）
    promoted_by   VARCHAR,
    git_commit    VARCHAR,                    -- 升格當下的 git commit hash（重現性）
    created_at    TIMESTAMP DEFAULT now(),
    deprecated_at TIMESTAMP,

    UNIQUE (tool_name, version)
);
```

`analysis_history.tool_id` 外鍵直接指向 `tools`，解鎖跨表查詢：

```sql
-- 哪些分析用了已 deprecated 的工具版本？
SELECT h.analysis_id, h.sample_id, h.completed_at,
       t.tool_name, t.version, t.deprecated_at
FROM analysis_history h
JOIN tools t USING (tool_id)
WHERE t.status = 'deprecated';

-- 改 morans_i 前先確認影響範圍
SELECT t.version, COUNT(*) AS run_count
FROM analysis_history h
JOIN tools t USING (tool_id)
WHERE t.tool_name = 'morans_i_spatial'
GROUP BY t.version;

-- 時間旅行查詢：某日期當下哪些工具可用？
SELECT * FROM tools
WHERE created_at <= '2026-03-01'
  AND (deprecated_at IS NULL OR deprecated_at > '2026-03-01')
  AND status = 'active';
```

##### 工具版本生命週期

```
candidate   ← /approve 前，Claude 生成的草稿（存於 analysis/candidates/）
    │
    └─► active      ← 管理員 /approve，寫入 tools 表，加入 BIO_TOOLS
            │
            └─► deprecated   ← 發現 bug 或新版取代
                    ├── 舊 analysis_history 記錄完整保留（tool_id 仍有效）
                    └── 新分析自動路由到最新 active 版本
```

##### BIO_TOOLS 動態載入（取代 registry.json）

```python
def load_bio_tools(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute(
        "SELECT tool_name, module_path, function_name, description, parameters "
        "FROM tools WHERE status = 'active' ORDER BY tool_name"
    ).fetchall()
    tools = []
    for name, module, func, desc, params in rows:
        mod = importlib.import_module(module)
        tools.append({
            "name": name,
            "function": getattr(mod, func),
            "description": desc,
            "input_schema": json.loads(params or "{}"),
        })
    return tools
```

##### 擴展時機判斷

| 工具數量 | 方案 | 說明 |
|---------|------|------|
| < 20 個 | `registry.json` + `parameters->>'tool_version'` | 目前階段，夠用 |
| 20–100 個 | `tools` 表 + `tool_id` 外鍵（同一個 `bio_memory.duckdb`） | 多人協作時升級 |
| > 100 個 | 獨立 `tools.duckdb` 或搬至 PostgreSQL | 實驗室規模不太會到這層 |

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

    -- 資料類型（兩層分類）
    data_type      VARCHAR,       -- 大類：見下方對照表
    platform       VARCHAR,       -- 具體平台/工具：'10x_visium_hd'、'kallisto'、'cellranger'、'salmon' 等

    species        VARCHAR,       -- 'mouse'、'human'、'rat'
    tissue         VARCHAR,       -- 'colon'、'lung'、'pancreas' 等（方便跨樣本查詢）
    l3_path        VARCHAR,       -- 原始數據路徑
    l2_ready       BOOLEAN DEFAULT FALSE,
    analysis_done  BOOLEAN DEFAULT FALSE,
    added_by       VARCHAR,
    notes          VARCHAR,
    last_updated   TIMESTAMP DEFAULT now()
);
```

**data_type 對照表：**

| data_type | 說明 | 常見 platform |
|-----------|------|--------------|
| `visium_hd` | 10x Visium HD 空間轉錄體 | `10x_visium_hd` |
| `visium` | 10x Visium 標準版 | `10x_visium` |
| `scrna` | 單細胞 RNA-seq | `cellranger`、`starsolo`、`kallisto_kb` |
| `bulk_rnaseq` | Bulk RNA-seq | `kallisto`、`salmon`、`star_rsem` |
| `multiome` | 同時測 RNA + ATAC | `cellranger_arc` |
| `atac` | ATAC-seq / scATAC-seq | `cellranger_atac`、`snapatac2` |
| `proteomics` | 蛋白質體學 | `maxquant`、`fragpipe` |
| `imaging` | 純影像（無定序） | `cellpose`、`stardist` |
| `other` | 不屬於以上類型 | 自由填寫 platform |

> **設計原則**：`data_type` 是固定的大類（方便 SQL GROUP BY），`platform` 記錄具體工具（方便追蹤版本差異）。新資料類型直接加入對照表，不需修改 schema。

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

> ⭐ **這是整個系統裡最需要用心設計的模組。**  
> 每個分析函數生成圖表時，`report_generator` 同步產出的文字描述品質，決定了未來所有語意搜尋的天花板——描述越精準，`memory_recent` 的命中率越高。

**每個產圖函數必須同時完成兩件事：**

```python
def plot_spatial(sample_id, gene, df):
    # ✅ 第一件事：畫圖存檔（一般都有做）
    path = f"results/{sample_id}/spatial_heatmap/{timestamp}_{gene}.png"
    fig.savefig(path)

    # ⭐ 第二件事：生成文字描述（這才是搜尋的基礎，經常被忽略）
    report_text = make_spatial_report(sample_id, gene, df, path)
    #   → 含：基因名、樣本、高表現區域、覆蓋面積、圖檔路徑
    #   → 這段文字的 embedding 寫入 memory_recent
    #   → 未來「免疫細胞浸潤相關分析？」能命中，靠的就是這段文字
```

**壞的描述 vs 好的描述：**

```
❌ 壞（搜尋命中率極差）：
   "spatial heatmap saved"

✅ 好（搜尋命中率高）：
   "基因：CD8A，樣本：official_v4，解析度：8µm
    結果：CD8A 高表現集中於腫瘤邊緣，佔組織面積 18%
    模式：腫瘤核心低表現（藍），邊緣高表現（紅）
    圖檔：results/official_v4/spatial_heatmap/20260515_CD8A.png"
```

**函數列表：**
- `make_spatial_report(sample_id, gene, df, path)` — ⭐ 空間熱圖完整描述（含定量結果）
- `make_clustering_report(sample_id, adata, path)` — ⭐ 聚類結果描述（含群集數、代表基因）
- `make_text_report(result)` — 產生 Markdown 摘要（適合 Telegram 訊息）
- `make_image_report(fig)` — matplotlib 圖表 → PNG（供 Telegram 傳送）
- `make_qc_report(sample_id)` — 標準 QC 報告（reads 數、基因數、組織覆蓋率）
- `make_summary(result, max_chars=50)` — 50 字摘要 → 寫入 `analysis_history.summary`
- `make_report_text(result, path)` — 完整文字描述 → 寫入 `memory_recent.report_text`（語意搜尋的核心輸入）

### history_query.py（分析歷史查詢，全部 0 token）
- `what_analyzed(sample_id)` — 查詢某樣本所有歷史分析（精確，0 token）
- `weekly_timeline(n_days=7)` — 近 N 天的分析時間軸（0 token）
- `find_archived(sample_id, analysis_type)` — 確認特定分析是否已有存檔（0 token）
- `semantic_search(query_text, top_k=5)` — 語意搜尋 L1 快取，只傳摘要給 LLM（少量 token）

---

## 分析圖表的儲存與搜尋設計

### 儲存原則：圖檔存磁碟，資料庫只存路徑

圖表（PNG/SVG）不存入 DuckDB，存在 `results/` 目錄，資料庫記錄路徑：

```
bio_DB/
└── results/
    └── {sample_id}/
        └── {analysis_type}/
            ├── 20260515_143022_spatial_CD8A.png
            └── 20260515_143022_spatial_CD8A_params.json
```

`analysis_history.result_path` 永久指向圖檔位置，圖檔不會因為 L1 TTL 到期而消失。

### 圖表語意搜尋：文字 embedding，不用 CLIP

> ⚠️ **設計決策：不採用 CLIP 圖像向量搜尋**

CLIP 對生資圖表效果差，原因：
- 訓練資料以自然照片為主，幾乎未見空間轉錄體熱圖、UMAP、Violin plot
- 能識別「這是有顏色的圖」，但無法區分「CD8 熱圖」vs「CD45 熱圖」（視覺相似）
- 無法連結「免疫細胞浸潤」→ 特定空間表現模式

**正確做法：產圖時同步呼叫 `report_generator`，寫入精準文字描述**

```python
# analysis/spatial_eda.py 內，每個產圖函數的標準結構
def plot_spatial(sample_id, gene, df):
    # 1. 畫圖
    fig, ax = plt.subplots()
    ...
    path = f"results/{sample_id}/spatial_heatmap/{timestamp}_{gene}.png"
    fig.savefig(path)

    # 2. ⭐ 生成文字描述（語意搜尋的基礎）
    report_text = make_report_text(
        sample_id=sample_id,
        gene=gene,
        high_expr_region=compute_high_region(df),   # 定量：高表現在哪
        coverage_pct=compute_coverage(df),           # 定量：面積百分比
        path=path
    )
    # report_text 範例：
    # "基因：CD8A，樣本：official_v4，解析度：8µm
    #  結果：CD8A 高表現集中於腫瘤邊緣，佔組織面積 18%
    #  圖檔：results/official_v4/spatial_heatmap/20260515_CD8A.png"

    # 3. 同步寫入 analysis_history + memory_recent
    insert_history(sample_id, "spatial_heatmap", path, make_summary(report_text))
    insert_l1_cache(sample_id, f"{gene} 空間分布", report_text)
```

> **關鍵**：`report_text` 含有定量結果（面積、區域、模式），未來搜尋「免疫細胞浸潤」能命中，靠的就是這段文字——而不是圖片本身。

### 搜尋精確度比較

| 搜尋方式 | 精確度 | token | 適用情境 |
|---------|--------|-------|---------|
| SQL 精確查（gene + analysis_type） | ⭐⭐⭐⭐⭐ | 0 | 「CD8A 的空間圖在哪？」 |
| 文字 embedding（report_text） | ⭐⭐⭐⭐ | 少量 | 「免疫細胞浸潤相關的分析」 |
| CLIP 圖像向量 | ⭐⭐ | 少量 | **不採用**，生資圖語意理解弱 |

---

## DuckDB + VSS 技術選型說明

> 本節說明為何選擇 DuckDB + VSS 作為 L1 Gold 層的核心引擎，以及它在系統中扮演的角色。

### 三大核心特色

| 特色 | 說明 |
|------|------|
| **輕量進程內資料庫** | 無需獨立伺服器（不同於 PostgreSQL/pgvector 或 Milvus），直接以函式庫形式嵌入 Python，零基礎建設成本 |
| **HNSW 高速向量索引** | VSS 擴充底層使用 Hierarchical Navigable Small World 演算法，多維度向量檢索延遲 < 1 秒，適合即時語意相似度比對 |
| **Parquet 原生整合 → L2 節省 token 的核心** | DuckDB 直接對 `silver/` 的 Parquet 執行 SQL 聚合，把「100K bins × 30K genes」的巨大矩陣壓縮成幾十行摘要後才傳給 LLM。**節省 token 的不是 Parquet 格式本身，而是 SQL 先過濾這個動作；DuckDB + Parquet 讓這件事在生資規模下變得實際可行**（傳統 DB 需先匯入耗時數小時，pandas 直接讀則記憶體爆炸）|

### 在 Hermes 中的角色：系統的第二道防線

查詢決策流程中，L1 語意快取是**繼 0-token SQL 歷史查詢之後的第二道防線**：

```
使用者查詢
    │
    ├─ [第一道] bio_history_check()  ← 0 token，SQL 精確比對 analysis_history
    │   命中 → 直接回傳存檔路徑（< 1 秒，0 token）
    │   未命中 ↓
    │
    ├─ [第二道] L1 HNSW 語意搜尋    ← 本層（DuckDB VSS）
    │   Cosine ≥ 0.88 命中 → 回傳快取報告（< 1 秒）
    │   未命中 ↓
    │
    ├─ [第三道] L2 Parquet 特徵查詢  ← ~30 秒
    └─ [第四道] L3 Pipeline 排程    ← ~4 小時
```

### L2 Parquet 查詢如何節省 token

**問題根源**：Visium HD 原始矩陣根本無法傳給 LLM：

```
raw Parquet（L2 silver）
→ 100,000 bins × 30,000 genes = 30 億個數字
→ 不可能傳給 LLM
```

**解法：DuckDB SQL 先壓縮，LLM 只看結果**：

```sql
-- 只傳這個 SQL 的結果（20 行）給 LLM，而非原始矩陣
SELECT gene_name, AVG(count) AS avg_expr
FROM 'silver/spatial_counts_crc_8um.parquet'
WHERE sample_id = 'official_v4'
  AND in_tissue = TRUE
GROUP BY gene_name
ORDER BY avg_expr DESC
LIMIT 20
```

| 方式 | LLM 看到的資料量 | Token 消耗 |
|------|----------------|-----------|
| 原始矩陣直接傳 | 30 億數字 | 不可能 |
| pandas 處理後傳 | 可行，但讀入 RAM 需 ~12 GB | 正常 |
| **DuckDB SQL 壓縮** | **20 行摘要** | **極少** |

> **關鍵理解**：token 節省的來源是「SQL 聚合」，DuckDB + Parquet 的貢獻是讓這個聚合在生資規模下**不需匯入、不爆記憶體、直接可用**。

---

### 具體運作機制

**當使用者送出查詢（例如：「整理 CRC 樣本的免疫細胞分布差異」）：**

1. **向量化**：將查詢文字送入 Embedding API，轉為 `FLOAT[1536]` 向量
2. **HNSW 搜尋**：對 `memory_recent` 表執行 Cosine Similarity 比對
3. **命中閾值**：相似度 ≥ 0.88 → 視為「過去已分析過相同意圖」
4. **快取命中**：直接回傳 L1 存放的完整文字報告，**完全不觸及 L2/L3**

**快取寫入（分析完成後）：**
- 分析結果報告 + 向量嵌入 → 寫入 `memory_recent`
- TTL 設定 7 天（近期記憶）
- 同時將 50 字摘要 + `l1_cache_id` 寫回 `analysis_history`

### 效益總結

| 指標 | 數值 | 機制 |
|------|------|------|
| 快取命中回應時間 | < 1 秒 | HNSW 索引直接查向量 |
| 重複查詢節省率 | ≥ 70% | L1 命中不觸及 L2/L3 |
| Token 消耗（L1 命中） | 0（報告直接回傳）| 不經過 LLM |
| Token 消耗（語意搜尋本身） | 少量（僅 embedding API）| 只傳 50 字 summary 給 LLM |

> **類比**：DuckDB + VSS 構成了 Hermes AI 的「海馬迴（近期語意記憶區）」——高成本的生資查詢在此被攔截，實現「用自然語言找過往分析報告」的超快速捷徑。

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
| `references/deepseek_ocr.md` | ~~長期記憶壓縮~~（對本專案不適用，見未來擴充討論區） | 不適用 |

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

### B. DeepSeek-OCR — 對本專案幾乎無用，暫不考慮

> **結論：此工具對本專案的問題不成立，不建議引入。**

DeepSeek-OCR 設計用來把舊報告 + 圖表壓縮成極少量 vision token 作為永久索引。但本架構已經把它試圖解決的問題覆蓋了：

| DeepSeek-OCR 想解決的 | 本架構的實際情況 |
|----------------------|----------------|
| 舊報告過期後找不回來 | `analysis_history` 永久保存 `result_path`，圖檔永遠在磁碟 |
| 無法語意搜尋舊圖表 | 文字 embedding（report_text）比 CLIP 更準——生資圖的意義在文字描述，不在像素 |
| 重新分析成本高 | L2 Parquet 永久存著，~30 秒可重生成任何圖表 |
| 大量報告需壓縮 | 實驗室規模一年幾百筆，遠低於需要壓縮的門檻 |

額外代價：需要 GPU 伺服器 + 重量級模型，維護成本高。

**唯一值得重新評估的條件**：系統運行 > 3 年、分析筆數 > 10,000 筆、且伺服器儲存空間成為瓶頸。目前不成立。論文參考見 `references/deepseek_ocr.md`。

---

## 待解決問題

1. **訊息平台確認**：Telegram / LINE / Slack — 依實驗室成員習慣決定，影響第零階段實作
2. ~~**Agent 框架**~~：✅ **已決定** — 自製輕量 Agent + Claude API，不採用 Hermes（需 GPU 自架，規模不符）
3. **嵌入模型**：OpenAI `text-embedding-3-small`（付費）vs. 本地 `nomic-embed-text`（免費）— 第四階段前決定
4. **Linux 伺服器權限**：確認 `/mnt/space4/` 有足夠磁碟空間與寫入權限
5. **MQ250422-A1-D1 缺失檔案**：web_summary 等檔案不存在（SpaceRanger 既有問題）— 以 D1-D2 為主要原型
6. **NDPI 配準**：兩個 MQ250428 樣本的高解析影像尚未對齊 — 完成前空間圖無組織影像疊加
7. **2µm vs 8µm**：L2 儲存 8µm；2µm 按需從 L3 載入，不做持久化
