# Hermes Bio-Memory — 專案憲法

## 1. 專案定位

**Hermes Bio-Memory** 是以 AI Agent 為核心的實驗室智慧生資分析平台。
核心目標：讓實驗室成員透過 Telegram 自然語言查詢空間轉錄體與 Bulk RNA 分析結果，消除重複運算，建立實驗室知識資產。

**當前階段**：本機測試建置（macOS `/Volumes/NO NAME/bio_DB/`），以 CRC Visium HD + MSseg 程式碼為測試資料，驗證 L1/L2/L3 三層架構後再部署至 Linux 伺服器。

---

## 2. 三層架構（神聖不可侵犯）

```
L3 Bronze ← 不可變原始數據（crc_visium_data/ + 未來伺服器數據）
L2 Silver ← DuckDB + Parquet 結構化特徵存儲（silver/）
L1 Gold   ← HNSW 語意快取 + 分析歷史（gold/hermes_cache.duckdb）
```

**鐵律**：
- **L3 唯讀**：任何腳本嚴禁修改 `crc_visium_data/` 下的原始數據
- **L2 由腳本寫入**：只有 `scripts/` 下的工具可以寫入 `silver/` 和 `bio_memory.duckdb`
- **L1 由分析函數寫入**：分析完成後自動寫入快取，TTL 7 天
- **analysis_history 永久保存**：分析歷史絕不刪除

---

## 3. 目錄結構

```
bio_DB/
├── CLAUDE.md               ← 本文件（專案憲法）
├── PROGRESS.md             ← 進度封存（每次完成里程碑更新）
├── plan_zh.md              ← 完整設計計畫（中文）
├── plan.md                 ← 完整設計計畫（英文）
├── pyproject.toml          ← Python 依賴管理（uv）
├── .env.example            ← 環境變數範本
├── bio_memory.duckdb       ← 主 DuckDB（sample_registry + analysis_history）
│
├── config/                 ← 集中設定（路徑、常數）
│   └── settings.py
│
├── scripts/                ← 一次性資料轉換工具（每個樣本跑一次）
│   ├── 00_init_db.py           ← 建立 Schema（已完成）
│   ├── 01_register_sample.py   ← 自動掃描 + 登記 L3 樣本
│   ├── 02_spatial_to_parquet.py← L3 Visium HD → L2 Parquet
│   └── msseg/                  ← MSseg 相關工具腳本
│
├── analysis/               ← 可重複使用的分析函數（Agent 呼叫）
│   ├── spatial_eda.py
│   ├── bulk_eda.py
│   ├── report_generator.py
│   └── history_query.py
│
├── server/                 ← MCP Server（Phase 5，尚未實作）
│   └── bio_memory_server.py
│
├── scheduler/              ← 排程任務（Phase 6，尚未實作）
│
├── tests/                  ← 測試套件
│   ├── conftest.py
│   ├── test_init_db.py
│   └── test_spatial_ingest.py
│
├── silver/                 ← L2：Parquet 特徵存儲（由腳本寫入）
├── gold/                   ← L1：語意快取（由分析函數寫入）
│
├── docs/                   ← 專案操作文件（指南、索引）
├── reports/                ← 學術作業報告（PartA/PartB，gitignored .pdf/.docx）
│
├── crc_visium_data/        ← L3 測試數據（~39GB，唯讀）
├── data_ana/               ← 參考分析中間數據（唯讀，來自 MSseg 專案）
├── results_ana/            ← 參考分析結果（唯讀，來自 MSseg 專案）
├── analysis_msseg/         ← MSseg 分析程式碼（參考用）
├── backend_msseg/          ← MSseg FastAPI 後端（參考用）
├── msseg_docs/             ← MSseg 文件（參考用）
│
└── references/             ← 技術論文摘要（.md）+ pdfs/（PDF 原文，gitignored）
```

---

## 4. 開發環境

```bash
# Python 環境（使用 uv，禁用 pip）
uv sync                           # 安裝依賴
uv add <package>                  # 新增套件
uv run python scripts/00_init_db.py  # 執行腳本

# 初始化資料庫（第一次使用）
uv run python scripts/00_init_db.py

# 執行測試
uv run pytest tests/ -v

# 環境變數
cp .env.example .env  # 填入 API keys
```

---

## 5. 資料路徑

| 項目 | 本機測試路徑 | 未來 Linux 路徑 |
|------|------------|---------------|
| 主資料夾 | `/Volumes/NO NAME/bio_DB/` | `/mnt/space4/bio_lab_db/` |
| CRC 測試數據 | `bio_DB/crc_visium_data/official_v4/` | TBD |
| 主 DuckDB | `bio_DB/bio_memory.duckdb` | `/mnt/space4/bio_lab_db/bio_memory.duckdb` |
| L2 Parquet | `bio_DB/silver/` | `/mnt/space4/bio_lab_db/silver/` |
| L1 快取 | `bio_DB/gold/hermes_cache.duckdb` | `/mnt/space4/bio_lab_db/gold/` |

> 所有路徑設定集中在 `config/settings.py`，腳本內不允許硬編碼路徑。

---

## 6. 關鍵規範

### 分析函數必須寫入歷史
每次分析完成，必須：
1. 將結果存至 `result_path`
2. INSERT 一筆至 `analysis_history`（status = 'completed'）
3. 若完整報告需快取 → 同時寫入 `memory_recent`

### 禁止在腳本內硬編碼路徑
所有路徑從 `config/settings.py` 的 `Settings` class 取得。

### 大型檔案操作
- **禁止** `cat` 或直接讀入 `.h5ad`、`.btf`、`.h5` 大型生信檔案
- Visium HD 2µm 全圖 (>100 萬 bins) 必須使用 backed mode 或先裁切
- L2 只儲存 8µm bins；2µm 按需從 L3 讀取

### macOS 清理
```bash
find . -name "._*" -delete && find . -name ".DS_Store" -delete
```

---

## 7. 核心資料庫 Schema

```sql
-- 樣本登記（bio_memory.duckdb）
-- data_type 大類: visium_hd | visium | scrna | bulk_rnaseq | multiome | atac | proteomics | imaging | other
-- platform  具體工具: 10x_visium_hd | cellranger | kallisto | salmon | cellranger_arc | ...
sample_registry(sample_id PK, project, data_type, platform, species, tissue, l3_path,
                l2_ready BOOL, analysis_done BOOL, added_by, notes, last_updated)

-- 分析歷史（永久保存）
analysis_history(analysis_id UUID PK, sample_id FK, analysis_type,
                 parameters JSON, status, result_path, l1_cache_id UUID,
                 requested_by, started_at, completed_at, summary VARCHAR)

-- 精簡索引 View（0 token 查詢）
analysis_index VIEW: GROUP BY sample_id + analysis_type, 顯示 run_count、last_run_date

-- L1 語意快取（gold/hermes_cache.duckdb）
memory_recent(id UUID, sample_id, query_text, report_text,
              embedding FLOAT[1536], created_at, expires_at)
-- + HNSW 索引（cosine metric）
```

---

## 8. 測試資料說明

| 資料集 | 路徑 | 用途 |
|--------|------|------|
| CRC Visium HD (官方) | `crc_visium_data/official_v4/` | L2 轉換測試、管道驗證 |
| MSseg 分析結果 | `results_ana/` | 分析輸出格式參考 |
| MSseg 中間數據 | `data_ana/` | AnnData 格式參考 |
| MSseg 程式碼 | `analysis_msseg/` `backend_msseg/` | 細胞分割、API 實作參考 |

---

## 9. 相關文件

| 文件 | 內容 |
|------|------|
| [PROGRESS.md](PROGRESS.md) | 當前進度、完成里程碑、待辦事項 |
| [plan_zh.md](plan_zh.md) | 完整七階段系統設計（中文） |
| [plan.md](plan.md) | 完整系統設計（英文） |
| [L3_DATA_INGEST_GUIDE.md](L3_DATA_INGEST_GUIDE.md) | 新增樣本到 L3 的操作指南 |
| [TEST_DATABASE_INDEX.md](TEST_DATABASE_INDEX.md) | 測試資料庫總覽（數據位置、大小） |
| [msseg_docs/CLAUDE.md](msseg_docs/CLAUDE.md) | MSseg 子專案開發規範 |
