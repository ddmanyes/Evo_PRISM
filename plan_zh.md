# Hermes Bio-Memory — 實驗室生資智慧分析系統

---

## 一、當前狀態（2026-05-16）

| 項目 | 內容 |
|------|------|
| 測試平台 | macOS `/Volumes/NO NAME/bio_DB/`（ExFAT 外接硬碟） |
| 目標平台 | Linux `/mnt/space4/bio_lab_db/`（生產部署） |
| 測試數據 | CRC Visium HD 官方數據（~39 GB）+ Bulk RNA Kallisto（84 樣本）+ sHG Proteomics |

### 已完成項目

| 類別 | 完成項目 |
|------|---------|
| 基礎建設 | `00_init_db.py`（Schema）、`config/settings.py`、`config/db_utils.py`（safe_write / cleanup_stale_runs）、`.env.example` |
| L3 → L2 | `02_spatial_to_parquet.py`（CRC Visium HD → 416 MB Parquet）、Bulk RNA L2 pipeline（5 支腳本）、`01_register_sample.py`（自動掃描登記） |
| 分析函式庫 | `analysis/bulk_eda.py`（兩階段寫入）、`analysis/report_generator.py`（兩階段寫入）、`analysis/bulk_timeseries.py`、`analysis/pathway_scoring.py`（ssGSEA/Z-score）、`analysis/multiomics_integration.py`（RNA-Protein）、`analysis/history_query.py` |
| 多組學 | Proteomics 數據整合（sHG Perseus log2）、`gene_sets/hair_follicle.yaml`（OxPhos/TCA/FAO/Glycolysis/Cell_Cycle） |
| Agent | `server/agent.py`（10 個工具，含 `bio_check_l2_sufficiency`） |
| Code Promotion | `analysis/code_promoter.py`、`promotion_candidates` VIEW、`tools/registry.json`、`analysis/candidates/` |
| 排程 | `scheduler/backup_db.py`（每日 02:00）、`scheduler/cleanup_l1_cache.py`（每日 03:30）、`scheduler/rebuild_hnsw.py`（每週日）、`scheduler/scan_new_samples.py`（每 30 分鐘） |
| 文件 | `CLAUDE.md`、`docs/DATA_INTEGRATION_GUIDE.md`、所有 launchd plist 範本 |

### 下一步優先順序

```
現在可做（本機）
    ├── 啟用 launchd_scan_samples.plist 排程（5 分鐘）
    ├── 跑 run_integration() 驗證 RNA-Protein 整合結果
    └── Agent CLI 整合測試（server/agent.py）

接著（需 Telegram Token）
    └── 第零階段：Telegram Bot 骨架

之後（需 Linux 伺服器）
    ├── Phase 6-B：FASTQ 自動 Kallisto 觸發
    ├── Phase 6-C：分析完成 Telegram 推送
    └── 第七階段：5 位成員實際使用驗證
```

> 操作規範見 [CLAUDE.md](CLAUDE.md)，進度封存見 [PROGRESS.md](PROGRESS.md)，整合決策見 [docs/DATA_INTEGRATION_GUIDE.md](docs/DATA_INTEGRATION_GUIDE.md)。

---

## 二、系統定位與動機

### 問題

現代生物資訊實驗室每日產出大量高維度數據（Visium HD、Bulk RNA-seq、Proteomics），卻面臨四個核心痛點：

1. **重複運算浪費**：不同成員針對相同樣本提出類似問題，各自重跑相同耗時 Pipeline（SpaceRanger 單次 ~4 小時）
2. **數據孤島**：分析結果散落於各人電腦，缺乏統一查詢與比較機制
3. **無分析記錄**：無從得知某樣本是否已分析過、結果在哪、由誰完成
4. **使用門檻高**：不熟悉命令列的成員難以自助取得結果

### 解決方案

以 **AI Agent + 三層數據倉儲** 建立實驗室智慧分析平台：

- 成員透過 **Telegram** 自然語言查詢，無需任何程式能力
- 每次分析自動寫入時間軸資料庫，可隨時追溯
- **多層防線**：SQL 精確查（0 token）→ 語意搜尋（少量 token），避免重複運算
- 所有樣本、分析、報告統一累積，形成可持續增值的**實驗室知識資產**

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

## 四、完整查詢決策流程

```
使用者提問（Telegram）
    │
    ├─[Step 1] bio_history_check()
    │   SQL 精確比對 analysis_history
    │   └─ 命中 → 回傳存檔路徑 / 摘要（0 token，< 1 秒）
    │   └─ 未命中 → Step 2
    │
    ├─[Step 2] bio_history_search()
    │   HNSW cosine 語意搜尋 L1 快取
    │   └─ 相似度 ≥ 0.88 → 回傳快取報告（< 1 秒）
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
    │           Claude 生成程式碼
    │           → 安全檢查（ALLOWED_IMPORTS / BLOCKED_PATTERNS）
    │           → 沙盒執行（sandbox_exec，timeout=60s）
    │           → 失敗 → 餵 traceback 給 Claude 修正（≤ 3 次）
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

## 五、分析歷史：兩階段寫入與狀態機

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

### SQL Schema

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

-- 精簡索引 View（0 token，Agent 每輪掃一眼）
CREATE VIEW analysis_index AS
SELECT sample_id, analysis_type,
       COUNT(*)                                         AS run_count,
       MAX(completed_at)::DATE                          AS last_run_date,
       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS success_count,
       SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS fail_count,
       STRING_AGG(DISTINCT requested_by, ', ')          AS run_by
FROM analysis_history
GROUP BY sample_id, analysis_type
ORDER BY last_run_date DESC;
```

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

## 六、省 Token 搜尋策略

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

## 七、Code Promotion 框架

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
    │       Claude Haiku 審查：通用性 / 介面清晰 / 安全性
    │       程式碼以 <untrusted_code> 標籤隔離（防 prompt injection）
    │       └─ 不通過 → 繼續存在 analysis_history 供重用
    │       └─ 通過 →
    │           ├── code_promoter.write_draft()
    │           │       → analysis/candidates/<name>.py
    │           ├── Telegram 通知管理員
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

## 八、資料庫安全

### 風險對策

| 風險 | 來源 | 對策 |
|------|------|------|
| ExFAT 斷電損壞 | `/Volumes/NO NAME/` 無日誌 | `safe_write()` 每次寫入後立即 CHECKPOINT |
| `.wal` 殘留鎖住 DB | Python 程序被 kill | Agent 啟動時 `cleanup_stale_runs()` |
| `running` 殭屍狀態 | 程序中途中斷 | > 24h → 標為 `stale` |
| 多程序寫入衝突 | 多人 Telegram Bot | `asyncio.Lock` 序列化所有寫入 |

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

## 九、Agent 工具清單（BIO_TOOLS）

| 工具 | 用途 | Token |
|------|------|-------|
| `bio_history_check` | 確認是否已有存檔（SQL 精確） | **0** |
| `bio_history_lookup` | 查詢分析歷史記錄 | **0** |
| `bio_history_timeline` | 近 N 天時間軸 | **0** |
| `bio_history_search` | 語意搜尋 L1 快取（只傳 summary） | 少量 |
| `bio_memory_query` | 從 L1 取回完整報告 | 少量 |
| `bio_check_l2_sufficiency` | 確認 l2_ready=true（spatial_eda 前必呼叫） | **0** |
| `bio_run_spatial_eda` | 執行空間轉錄體 EDA（需 l2_ready） | 正常 |
| `bio_run_bulk_eda` | 執行 Bulk RNA-seq EDA | 正常 |
| `bio_register_sample` | 登記新樣本至 sample_registry | **0** |
| `bio_execute_code` | 沙盒執行動態生成 Python（3C 路徑） | 正常 |

**呼叫順序原則**：
`bio_history_check` → `bio_history_search` → `bio_memory_query` → `bio_check_l2_sufficiency`（需 spatial 時）→ 分析工具 → `bio_execute_code`（最後手段）

---

## 十、分析函式庫（analysis/）

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

## 十一、目錄結構

```
/Volumes/NO NAME/bio_DB/
│
├── CLAUDE.md                       ← 專案憲法（規範 + 架構 + 路徑）
├── PROGRESS.md                     ← 進度封存
├── plan_zh.md                      ← 本文件
├── pyproject.toml                  ← 依賴管理（uv）
├── .env.example                    ← 環境變數範本
├── bio_memory.duckdb               ← 主 DuckDB（sample_registry + analysis_history + Views）
│
├── config/
│   ├── settings.py                 ← 集中路徑與 API key（BIO_DB_ROOT、DUCKDB_PATH 等）
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
│   ├── report_generator.py         ← ✅ 兩階段寫入 + ≤50 字摘要
│   ├── history_query.py            ← ✅ 0-token SQL 查詢
│   ├── embed.py                    ← ✅ bge-m3 本機 embedding
│   ├── l1_cache.py                 ← ✅ L1 快取讀寫
│   ├── code_promoter.py            ← ✅ Code Promotion 框架
│   └── candidates/                 ← 升格候選草稿暫存區
│
├── tools/
│   └── registry.json               ← ✅ 已上線工具清單（name/module/version/status）
│
├── gene_sets/
│   └── hair_follicle.yaml          ← ✅ OxPhos/TCA/FAO/Glycolysis/Cell_Cycle（小鼠）
│
├── server/
│   ├── agent.py                    ← ✅ Agent Loop + 10 個 BIO_TOOLS
│   └── code_executor.py            ← 沙盒執行（sandbox_exec + SecurityError）
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
│   ├── test_init_db.py
│   ├── test_spatial_ingest.py
│   └── test_phase2b.py
│
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

## 十二、資料庫 Schema 總覽

### sample_registry

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

### memory_recent（L1 Gold）

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

### Views

| View | 用途 | Token |
|------|------|-------|
| `analysis_index` | 精簡索引，Agent 每輪掃一眼 | 0 |
| `promotion_candidates` | reuse_count ≥ 3 的升格候選清單 | 0 |

---

## 十三、跨專案整合規則

將其他專案的數據或分析方法併入 bio_DB 時，依下列優先順序：

1. **數據**：複製到對應目錄（`bulk_rna_data/`、`proteome_data/`）→ 登記至 `sample_registry`
2. **通用分析方法**：去除硬編碼路徑與生物特化常數後放入 `analysis/`
3. **生物特化邏輯**（特定基因清單、TF 網絡）：放入 `gene_sets/*.yaml`，不硬編碼
4. **高度特化方法**：保留在原專案，透過 `sys.path.insert` 呼叫 bio_DB 共用函數

詳細決策流程見 [docs/DATA_INTEGRATION_GUIDE.md](docs/DATA_INTEGRATION_GUIDE.md)。

---

## 十四、實作階段進度

| 階段 | 名稱 | 狀態 |
|------|------|------|
| 第零階段 | Telegram Bot 骨架 | ⏳ 待實作 |
| 第一階段 | 環境建置 + Schema | ✅ 完成 |
| 第二階段 A | Visium HD → L2 Parquet | ✅ 完成（CRC 測試集） |
| 第二階段 B | Bulk RNA-seq → L2 | ✅ TSV 完成；Parquet 轉換待補 |
| 第二階段 C | Proteomics 整合 | ✅ 完成（sHG Perseus log2） |
| 第三階段 | 分析工具層 + 報告產生 | ✅ 完成（10 個 Agent 工具） |
| 第三階段＋ | Code Promotion 框架 | ✅ 完成 |
| 第四階段 | L1 語意快取 | ✅ 完成（bge-m3 本機） |
| 第五階段 | MCP Server + Telegram 整合 | ⏳ 待實作 |
| 第六階段 | 排程系統 | ✅ 4 個排程（Phase B/C 待實作） |
| 第七階段 | 驗證與調校 | ⏳ 待實作 |

---

## 十五、技術選型說明

### Agent 框架：自製輕量 Agent + Claude API

不採用 Hermes（需 ~40 GB VRAM GPU 自架）。實驗室規模（月百次查詢）下 Claude API 費用極低，Prompt Cache 後更省，推理與工具呼叫能力更強。

| 任務 | Claude API | Python 工具 |
|------|-----------|------------|
| 理解使用者意圖 | ✅ | — |
| 資料寫入（DuckDB、Parquet） | 決定呼叫哪個工具 | ✅ 實際寫入 |
| 檔案分析（.h5ad、.parquet） | 處理 Python 回傳的摘要 | ✅ 讀取計算 |
| 圖表生成（matplotlib） | 決定畫什麼 | ✅ 畫圖存檔 |
| 分析歷史管理 | 呼叫 `bio_history_*` | ✅ 執行 SQL |

### Embedding：bge-m3 本機（llama.cpp）

| 屬性 | 值 |
|------|------|
| 模型 | `bge-m3-Q8_0.gguf`（605 MB） |
| 維度 | 1024-dim |
| 多語 | ✅ 中英混雜表現佳 |
| 啟動 | `llama-server --embedding --port 8081 --ctx-size 8192` |
| 費用 | 零（本機推理） |

### 沙盒執行策略

| 階段 | 隔離方式 |
|------|---------|
| macOS 測試（現階段） | `subprocess.run` + ALLOWED_IMPORTS 白名單 + timeout=60s |
| Linux 部署（Phase 5+） | Docker container（`python:3.11-slim` + bind-mount silver/） |

---

## 十六、關鍵路徑對照

| 項目 | macOS 測試 | Linux 生產 |
|------|-----------|-----------|
| 主資料夾 | `/Volumes/NO NAME/bio_DB/` | `/mnt/space4/bio_lab_db/` |
| 主 DuckDB | `bio_DB/bio_memory.duckdb` | `bio_lab_db/bio_memory.duckdb` |
| L2 Parquet | `bio_DB/silver/` | `bio_lab_db/silver/` |
| L1 快取 | `bio_DB/gold/hermes_cache.duckdb` | `bio_lab_db/gold/` |
| 備份目標 | `~/bio_db_backups/`（APFS） | `/mnt/backup/bio_lab_db/` |

> 所有路徑集中於 `config/settings.py`，腳本內嚴禁硬編碼。

---

## 十七、參考文獻索引

| 文件 | 內容 | 對應章節 |
|------|------|---------|
| `references/duckdb.md` | DuckDB 引擎設計（SIGMOD 2019） | 三、六 |
| `references/duckdb_vss.md` | HNSW 向量搜尋 | 四、六 |
| `references/lakeharbor_icde2024.md` | 結構感知資料湖（ICDE 2024） | 三 |
| `references/agent_first_data_systems.md` | Agent-First 資料系統（2025） | 全章節 |
| `references/mcp_protocol.md` | MCP Server 骨架 | 九 |
| `references/anndata_scanpy.md` | 讀取 Visium HD | 十一 |
| `references/memgpt.md` | 分層記憶模型（概念參考） | 三 |

---

## 附錄 A：未來擴充（暫不在主計畫內）

### LLMLingua — 中期記憶壓縮

L1 TTL 7 天到期後，若報告仍有查詢價值，可透過 LLMLingua 壓縮至 1/20 大小，延伸為「中期記憶」（TTL 90 天）。引入時機：L1 快取量超過閾值，或 Token 費用明顯上升。

### tools 資料表（工具 ≥ 20 個時升級）

詳見第七章「工具版本管理路徑」。觸發時機：工具庫超過 20 個，或有多人 `/approve` 並發需求。

### 圖表語意搜尋

不採用 CLIP 圖像向量搜尋（生資圖表訓練資料不足，無法區分同類熱圖）。正確做法：產圖時同步呼叫 `report_generator` 寫入精準文字描述，文字 embedding 比 CLIP 更準確。
