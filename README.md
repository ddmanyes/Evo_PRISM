# **實驗室生資智慧分析平台**

讓實驗室成員用自然語言查詢空間轉錄體與 Bulk RNA 分析結果，無需任何程式能力，無需重複運算。

---

## 系統概覽

```text
使用者（Web UI / Telegram）
         │ 自然語言提問
         ▼
    server/agent.py
    ├─ BIO_TOOLS x 10（SQL / Parquet / 沙盒執行）
    ├─ 雙推理後端（本機 Gemma 4 Vision / Claude API）
    └─ plt.show() hook → 分析圖回傳聊天框
         │
    ┌────┴──────────────────────┐
    │                           │
    ▼                           ▼
L2 bio_memory.duckdb        L1 hermes_cache.duckdb
sample_registry             memory_recent (HNSW)
analysis_history            TTL 7 天
analysis_index VIEW
         │
         ▼
L3 原始數據（唯讀）
crc_visium_data/  bulk_rna_data/  proteome_data/
```

**三層架構**：

- **L3 Bronze**：不可變原始數據（FASTQ、SpaceRanger outs/）
- **L2 Silver**：DuckDB + Parquet 結構化特徵（30 億數字 → 416 MB，集中計算一次）
- **L1 Gold**：HNSW 語意快取，TTL 7 天，問過的問題直接回傳

---

## 快速開始

### 前置需求

- macOS（測試平台）或 Linux（生產部署）
- Python ≥ 3.10
- [uv](https://github.com/astral-sh/uv) 套件管理器
- [llama.cpp](https://github.com/ggml-org/llama.cpp) 已編譯（`~/llama.cpp/build/bin/llama-server`）
- 模型檔案（放於 `~/`）：
  - `gemma-4-26B-A4B-it-UD-IQ2_M.gguf`（推理引擎）
  - `mmproj-BF16.gguf`（視覺投影層）
  - `bge-m3-Q8_0.gguf`（Embedding，605 MB）

### 安裝

```bash
# 1. 建立 venv（ExFAT 磁碟不支援 symlink，venv 必須建在 APFS）
python3 -m venv ~/.venvs/hermes-bio-memory
ln -s ~/.venvs/hermes-bio-memory "/Volumes/NO NAME/bio_DB/.venv"

# 2. 安裝依賴
cd "/Volumes/NO NAME/bio_DB"
uv sync --no-install-project

# 3. 設定環境變數
cp .env.example .env
# 填入 ANTHROPIC_API_KEY（使用 Claude 後端時才需要）

# 4. 初始化資料庫 Schema
~/.venvs/hermes-bio-memory/bin/python scripts/00_init_db.py

# 5. 啟動 Embedding Server（分析前必須在線）
~/llama.cpp/build/bin/llama-server \
  -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99 &
```

### 啟動系統

```bash
cd "/Volumes/NO NAME/bio_DB"
bash start_hermes.sh
```

啟動後開啟瀏覽器：**<http://localhost:8000>**

啟動順序：

1. Gemma 4 Vision 推理引擎（port 8080）— 等待模型載入，最多 120 秒
2. Embedding Server bge-m3（port 8081）— 若已透過 launchd 自動啟動則跳過
3. FastAPI Web UI（port 8000）— 等待 scanpy/anndata import，最多 60 秒

### 關閉系統

```bash
# 方法一：在 start_hermes.sh 執行的終端機按 Ctrl+C
# （只停止由該腳本啟動的服務）

# 方法二：強制停止所有服務（含 launchd 或手動啟動的）
cd "/Volumes/NO NAME/bio_DB"
bash stop_hermes.sh
```

`stop_hermes.sh` 依序停止 port 8080（推理引擎）、8081（Embedding）、8000（Web UI）。

---

## 測試

```bash
cd "/Volumes/NO NAME/bio_DB"
~/.venvs/hermes-bio-memory/bin/python -m pytest tests/ -v --tb=short
```

預期結果：**105 / 106 PASSED**（1 筆為既有路徑問題，非程式邏輯錯誤）

| 測試檔              | 測試數 | 涵蓋範圍                             |
| ------------------- | ------ | ------------------------------------ |
| `test_init_db.py` | 4      | Schema + Views 正確性                |
| `test_phase2b.py` | 14     | 歷史查詢 + 報告生成                  |
| `test_phase3.py`  | 15     | L1 快取 + HNSW                       |
| `test_phase4.py`  | 19     | MCP Server 工具                      |
| `test_phase5.py`  | 28     | Agent Loop + 沙盒執行（openai mock） |
| `test_phase6.py`  | 23     | Telegram Bot 指令與訊息分派          |

---

## 功能說明

### Web UI（<http://localhost:8000）>

- **聊天介面**：自然語言提問 → SSE 串流回覆
- **圖片上傳**：附件按鈕或 `Ctrl+V` 貼圖 → Gemma 4 Vision 視覺分析
- **分析結果圖**：matplotlib QC 圖直接顯示於聊天框，支援下載
- **後端切換**：Sidebar 即時切換本機 Gemma 4 / Claude API
- **歷史頁面**（`/history`）：所有分析記錄 + 縮圖預覽
- **報告頁面**（`/results/{id}`）：完整 HTML 分析報告含 QC 圖

### Agent 決策流程

```text
提問
 ├─ Step 1  SQL 精確比對（0 token，< 1 秒）   ← 做過？直接回傳
 ├─ Step 2  HNSW 語意搜尋（cosine ≥ 0.88）   ← 問過類似？快取回傳
 ├─ Step 3A 標準分析工具（L2 Parquet 就緒）
 ├─ Step 3B Code Promotion 重用（曾生成過？）
 └─ Step 3C 全新程式碼生成（沙盒執行 + 失敗重試）
```

### 推理後端

| 後端          | 模型                     | 用途                               |
| ------------- | ------------------------ | ---------------------------------- |
| local（預設） | Gemma 4 26B Vision IQ2_M | 離線、隱私、多模態圖片分析         |
| claude        | claude-sonnet-4-6        | 更強推理，需 `ANTHROPIC_API_KEY` |

---

## 驗證架構與資料庫

```bash
# 健檢（確認 sample / history / stale 數量）
~/.venvs/hermes-bio-memory/bin/python config/db_utils.py

# 確認 L2 Parquet 資料正確
~/.venvs/hermes-bio-memory/bin/python -c "
import duckdb
r = duckdb.execute(\"\"\"
    SELECT COUNT(*) as bins, COUNT(DISTINCT gene_name) as genes
    FROM 'silver/spatial_counts_crc_official_v4_8um/*.parquet'
    WHERE in_tissue = TRUE
\"\"\").fetchone()
print(f'bins={r[0]}, genes={r[1]}')
"
# 預期：bins≈516880, genes≈18000

# 列出資料庫所有表格
~/.venvs/hermes-bio-memory/bin/python -c "
import duckdb
con = duckdb.connect('bio_memory.duckdb', read_only=True)
print(con.execute('SHOW TABLES').fetchall())
"
```

---

## 專案結構

```text
bio_DB/
├── config/           ← 路徑與 API key 集中設定
├── scripts/          ← 一次性 L3→L2 轉換工具
├── analysis/         ← 分析函式庫（Agent 呼叫）
├── server/           ← FastAPI Web UI + Agent + 沙盒執行器
│   └── static/       ← index.html（聊天）、history.html（歷史）
├── scheduler/        ← 4 個排程任務（備份/清理/重建/掃描）
├── tests/            ← 測試套件（6 個測試檔）
├── gene_sets/        ← 路徑基因集 YAML
├── tools/            ← registry.json（已上線工具清單）
├── silver/           ← L2 Parquet（scripts/ 寫入，analysis/ 唯讀）
├── gold/             ← L1 快取 DuckDB（hermes_cache.duckdb）
├── results/          ← 分析結果（.md 報告 + QC 圖）
├── start_hermes.sh   ← 一鍵啟動腳本
└── bio_memory.duckdb ← 主資料庫（sample_registry + analysis_history）
```

---

## 排程任務

| 腳本                              | 時間         | 功能                                                 |
| --------------------------------- | ------------ | ---------------------------------------------------- |
| `scheduler/backup_db.py`        | 每日 02:00   | EXPORT DATABASE →`~/bio_db_backups/`（保留 7 天） |
| `scheduler/cleanup_l1_cache.py` | 每日 03:30   | 清理 L1 TTL 到期快取                                 |
| `scheduler/rebuild_hnsw.py`     | 每週日 03:00 | 重建 HNSW 索引                                       |
| `scheduler/scan_new_samples.py` | 每 30 分鐘   | 掃描並登記新樣本至 sample_registry                   |

手動備份與還原：

```bash
~/.venvs/hermes-bio-memory/bin/python scheduler/backup_db.py            # 備份
~/.venvs/hermes-bio-memory/bin/python scheduler/backup_db.py --restore  # 還原最新備份
```

---

## 測試數據規模

| 資料集              | 大小       | 說明                            |
| ------------------- | ---------- | ------------------------------- |
| CRC Visium HD（L3） | ~39 GB     | 官方測試數據，唯讀              |
| L2 Parquet          | 416 MB     | 8µm bins，215M nonzero entries |
| Bulk RNA-seq        | 84 樣本    | Kallisto 定量輸出               |
| Proteomics          | 5 個時間點 | sHG Perseus log2 intensity      |

---

## 文件

| 文件                                                          | 說明                               |
| ------------------------------------------------------------- | ---------------------------------- |
| [plan_zh.md](plan_zh.md)                                         | 完整系統設計（中文，18 章 + 附錄） |
| [CLAUDE.md](CLAUDE.md)                                           | 專案憲法（開發規範 + 架構 + 路徑） |
| [PROGRESS.md](PROGRESS.md)                                       | 實作進度封存                       |
| [presentation.md](presentation.md)                               | 系統簡報（Marp 格式，13 張）       |
| [docs/DATA_INTEGRATION_GUIDE.md](docs/DATA_INTEGRATION_GUIDE.md) | 跨專案數據整合指南                 |
| [docs/L3_DATA_INGEST_GUIDE.md](docs/L3_DATA_INGEST_GUIDE.md)     | 新增 L3 樣本操作指南               |

---

## 下一步

```text
現在可做（本機）
    ├── 端對端測試：填入 ANTHROPIC_API_KEY，驗證 Claude 後端切換
    └── 啟用 launchd_scan_samples.plist 自動掃描新樣本

接著（需 Telegram Token）
    └── Telegram Bot 正式啟用（server/telegram_bot.py 骨架已完成）

之後（需 Linux 伺服器）
    ├── 路徑設定遷移（config/settings.py）
    ├── Docker 沙盒替換 code_executor.py（生產安全隔離）
    ├── FASTQ 自動 Kallisto 觸發
    └── 5 位實驗室成員實際使用驗證
```
