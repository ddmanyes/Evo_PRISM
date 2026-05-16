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
├── IMPLEMENTATION_PLAN.md  ← Phase 執行計畫（/sp-executing-plans 維護）
├── execution_trace.md     ← 各 Phase 執行紀錄（含驗收結果與 commit hash）
│
├── config/                 ← 集中設定（路徑、常數、寫入工具）
│   ├── settings.py
│   └── db_utils.py             ← ✅ safe_write() / cleanup_stale_runs() / db_health_check()
│
├── scripts/                ← 一次性資料轉換工具（每個樣本跑一次）
│   ├── 00_init_db.py           ← ✅ 建立 Schema（已完成）
│   ├── 01_register_sample.py   ← 自動掃描 + 登記 L3 樣本
│   ├── 02_spatial_to_parquet.py← ✅ L3 Visium HD → L2 Parquet（已驗證：416 MB / 215M nonzero）
│   └── msseg/                  ← MSseg 相關工具腳本
│
├── analysis/               ← 可重複使用的分析函數（Agent 呼叫）
│   ├── spatial_eda.py          ← ✅ 基因空間圖、QC 統計
│   ├── history_query.py        ← ✅ 0-token 歷史查詢
│   ├── report_generator.py     ← ✅ EDA 報告 + ≤50 字摘要
│   ├── embed.py                ← ✅ Embedding 封裝（llamacpp/openai/google）
│   ├── l1_cache.py             ← ✅ L1 快取寫入 + 語意搜尋
│   ├── bulk_eda.py             ← ✅ 整批 QC / PCA / EDA 報告
│   ├── bulk_timeseries.py      ← ✅ 時間序列均值 + log2 FC
│   ├── pathway_scoring.py      ← ✅ ssGSEA / Z-score 路徑評分（讀 gene_sets/ YAML）
│   └── multiomics_integration.py ← ✅ RNA-Protein 時序整合 + Spearman 相關 + 滯後分析
│
├── server/                 ← MCP Server（Phase 5，尚未實作）
│   └── bio_memory_server.py
│
├── scheduler/              ← 排程任務
│   ├── backup_db.py            ← ✅ 每日 02:00 EXPORT DATABASE 備份（已啟用 launchd）
│   ├── cleanup_l1_cache.py     ← ✅ 每日 03:30 清理 L1 TTL 過期記錄
│   ├── rebuild_hnsw.py         ← ✅ 每週日 03:00 重建 HNSW 索引
│   └── scan_new_samples.py     ← ✅ 每 30 分鐘掃描並登記新 Kallisto 樣本
│
├── gene_sets/              ← 路徑基因集 YAML 配置（供 pathway_scoring.py 讀取）
│   └── hair_follicle.yaml      ← ✅ OxPhos / TCA / FAO / Glycolysis / Cell_Cycle
│
├── proteome_data/          ← Proteomics 數據（L2 ready）
│   └── sHG_timeseries/         ← sHG Perseus log2 intensity（0/24/48/72/96h）
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

### ExFAT 限制與 venv 安排

`/Volumes/NO NAME/` 為 ExFAT，**不能直接在此建立 venv**（symlink/權限會壞）。
正確做法：venv 建在 APFS（家目錄），再以 symlink 接回專案目錄。

```bash
# 一次性建置（首次使用）
python3 -m venv ~/.venvs/hermes-bio-memory
ln -s ~/.venvs/hermes-bio-memory "/Volumes/NO NAME/bio_DB/.venv"

# 直接以 venv 的 python 執行（不需 uv run）
~/.venvs/hermes-bio-memory/bin/python scripts/00_init_db.py
```

### uv 指令

```bash
# 安裝依賴（無 package 目錄，必須加 --no-install-project，否則 hatchling build 失敗）
uv sync --no-install-project
uv add <package>                  # 新增套件

# 初始化資料庫（第一次使用）
uv run python scripts/00_init_db.py

# 執行測試
uv run pytest tests/ -v

# 環境變數
cp .env.example .env  # 填入 API keys
```

### Embedding Server（分析前必須啟動）

**所有呼叫 `analysis/embed.py`、`analysis/l1_cache.py` 的操作都需要 embedding server 在線。**
Server 提供本機 OpenAI-compatible `/v1/embeddings` API（port 8081）。

```bash
# 手動啟動（背景執行）
~/llama.cpp/build/bin/llama-server \
  -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99 &

# 確認在線
curl http://localhost:8081/health          # → {"status":"ok"}
~/.venvs/hermes-bio-memory/bin/python -c "
from analysis.embed import server_health; print(server_health())"

# 停止
pkill -f "llama-server.*8081"
```

**自動啟動（推薦）**：安裝 launchd plist，開機自動啟動、自動重啟：
```bash
cp docs/launchd_embedding_server.plist.example \
   ~/Library/LaunchAgents/com.hermes.embedding_server.plist
launchctl load ~/Library/LaunchAgents/com.hermes.embedding_server.plist
```

模型：`~/llama.cpp/models/bge-m3-Q8_0.gguf`（605 MB，1024-dim，BAAI bge-m3，多語含中文）

---

### Multimodal Server (Gemma 4 Vision)

**支援影像輸入與多模態推理（port 8080）。**

```bash
# 手動啟動
~/llama.cpp/build/bin/llama-server \
  -m /Users/zhanqiru/gemma-4-26B-A4B-it-UD-IQ2_M.gguf \
  --mmproj /Users/zhanqiru/mmproj-BF16.gguf \
  --port 8080 --ctx-size 8192 --n-gpu-layers 99 \
  --flash-attn -ctk q8_0 -ctv q8_0

# 自動啟動
cp docs/launchd_multimodal_server.plist.example \
   ~/Library/LaunchAgents/com.hermes.multimodal_server.plist
launchctl load ~/Library/LaunchAgents/com.hermes.multimodal_server.plist
```

---

### 備份與健檢

```bash
# 手動備份（launchd 每日 02:00 自動跑，見 docs/launchd_backup.plist.example）
uv run python scheduler/backup_db.py

# 緊急還原（從最新備份）
uv run python scheduler/backup_db.py --restore

# 健檢（顯示 sample / history / stale / l2_ready 數量）
uv run python config/db_utils.py
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

### 寫入關鍵表必須走 safe_write()
`analysis_history` 與 `sample_registry` 的所有寫入都應透過 `config.db_utils.safe_write()`，
它會在寫入後立即 `CHECKPOINT`，把 WAL 刷入主檔——ExFAT 無日誌，這是縮小斷電損壞視窗的關鍵。
L1 `memory_recent` 等快取寫入因頻率高、丟失可重建，不需呼叫（效能考量）。

### Agent 啟動時清理殭屍狀態
任何長駐程序（Agent / MCP Server / Telegram Bot）啟動時呼叫 `cleanup_stale_runs(con)`，
把 > 24 小時仍為 `running` 的紀錄標為 `stale`。

### 禁止在腳本內硬編碼路徑
所有路徑從 `config/settings.py` 的 `Settings` class 取得。

### 跨專案數據整合規則

將其他專案的數據或分析方法併入 bio_DB 時，遵循以下優先順序：

1. **數據**：複製到對應目錄（`bulk_rna_data/`、`proteome_data/`）→ 登記到 `sample_registry`
2. **通用分析方法**：抽象化後放入 `analysis/`，去除硬編碼路徑與生物特化常數
3. **生物特化邏輯**（特定基因清單、TF 網絡）：放入 `gene_sets/*.yaml` 配置檔，不硬編碼
4. **高度特化方法**：保留在原專案，透過 `sys.path.insert` 呼叫 bio_DB 共用函數

詳細決策流程見 [docs/DATA_INTEGRATION_GUIDE.md](docs/DATA_INTEGRATION_GUIDE.md)。

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
                 requested_by, started_at, completed_at, summary VARCHAR,
                 tool_id UUID)  -- 預留：未來 tools 表 FK，目前皆為 NULL

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
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 當前 Phase 執行計畫（含驗收標準） |
| [execution_trace.md](execution_trace.md) | Phase 執行紀錄（結果與 commit hash） |
| [plan_zh.md](plan_zh.md) | 完整七階段系統設計（中文） |
| [plan.md](plan.md) | 完整系統設計（英文） |
| [docs/L3_DATA_INGEST_GUIDE.md](docs/L3_DATA_INGEST_GUIDE.md) | 新增樣本到 L3 的操作指南 |
| [docs/TEST_DATABASE_INDEX.md](docs/TEST_DATABASE_INDEX.md) | 測試資料庫總覽（數據位置、大小） |
| [docs/DATA_INTEGRATION_GUIDE.md](docs/DATA_INTEGRATION_GUIDE.md) | 跨專案數據與程式碼整合決策指南 |
| [docs/launchd_backup.plist.example](docs/launchd_backup.plist.example) | macOS 每日備份排程範本 |
| [docs/launchd_scan_samples.plist.example](docs/launchd_scan_samples.plist.example) | macOS 每 30 分鐘掃描新樣本排程範本 |
| [msseg_docs/CLAUDE.md](msseg_docs/CLAUDE.md) | MSseg 子專案開發規範 |
