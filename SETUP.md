# Hermes Bio-Memory — 環境建置指南

本文件說明如何在新機器上從零開始建置 Hermes Bio-Memory 執行環境。

---

## 前置需求

| 項目 | 版本 / 說明 |
| ---- | ----------- |
| Python | 3.11 以上 |
| [uv](https://github.com/astral-sh/uv) | 套件管理工具 |
| llama.cpp | 已編譯的 `llama-server` 執行檔 |
| bge-m3-Q8_0.gguf | 605 MB，本機 embedding 模型 |
| Gemma 4 Vision 模型 | `gemma-4-26B-A4B-it-UD-IQ2_M.gguf` + `mmproj-BF16.gguf` |

---

## 步驟一：複製專案資料夾

將完整的 `bio_DB/` 資料夾複製到目標機器，**必須包含以下數據目錄**：

```
bio_DB/
├── silver/          ← L2 Parquet（CRC 空間轉錄體，416 MB）
├── gold/            ← L1 語意快取
├── bulk_rna_data/   ← Bulk RNA Kallisto 輸出
└── proteome_data/   ← Proteomics 數據
```

> 若以上目錄缺失，分析功能仍可啟動，但無法執行 demo 查詢。

---

## 步驟二：建立 Python 虛擬環境

專案目錄位於 ExFAT 磁碟，**venv 必須建在 APFS**（家目錄），再以 symlink 接回：

```bash
# 建立 venv（只需執行一次）
python3 -m venv ~/.venvs/hermes-bio-memory

# 建立 symlink（路徑請換成你的實際路徑）
ln -s ~/.venvs/hermes-bio-memory "/path/to/bio_DB/.venv"

# 安裝依賴
cd /path/to/bio_DB
uv sync --no-install-project
```

---

## 步驟三：設定環境變數

```bash
cp .env.example .env
```

用文字編輯器開啟 `.env`，填入以下欄位：

```bash
ANTHROPIC_API_KEY=sk-ant-...      # 使用 Claude 後端時需要
GOOGLE_API_KEY=AIza...            # 使用 Google Gemini 後端時需要
INFERENCE_BACKEND=local           # 預設本機推理，改 claude 或 google 切換雲端
CLAUDE_MODEL=claude-sonnet-4-6    # Claude 模型版本
GOOGLE_MODEL=gemini-2.0-flash     # Google Gemini 模型版本
```

---

## 步驟四：初始化資料庫

若已有複製過來的 `bio_memory.duckdb` 可跳過此步驟。
若要全新建立（例如磁碟路徑不同），執行：

```bash
~/.venvs/hermes-bio-memory/bin/python scripts/00_init_db.py
~/.venvs/hermes-bio-memory/bin/python scripts/01_register_sample.py
```

> **注意**：`sample_registry` 內的 `l3_path` 存放絕對路徑，換機器後若磁碟掛載點不同需重新登記。

---

## 步驟五：確認模型路徑

開啟 `config/settings.py`，確認以下路徑與你的機器一致：

```python
LLAMA_SERVER_BIN   = "~/llama.cpp/build/bin/llama-server"
EMBEDDING_MODEL    = "~/llama.cpp/models/bge-m3-Q8_0.gguf"
MULTIMODAL_MODEL   = "~/gemma-4-26B-A4B-it-UD-IQ2_M.gguf"
MULTIMODAL_MMPROJ  = "~/mmproj-BF16.gguf"
```

---

## 步驟六：啟動 Embedding Server

所有語意搜尋與 L1 快取操作都需要 embedding server 在線，**建議在啟動主系統前先執行**：

```bash
~/llama.cpp/build/bin/llama-server \
  -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99 &

# 確認在線
curl http://localhost:8081/health
# 預期回應：{"status":"ok"}
```

---

## 步驟七：啟動系統

```bash
bash start_hermes.sh
```

腳本會自動：
1. 啟動 llama-server（Gemma 4 Vision，port 8080）
2. 等待模型載入完畢（最多 120 秒）
3. 啟動 FastAPI Web UI（port 8000）

開啟瀏覽器前往 [http://localhost:8000](http://localhost:8000)

---

## 健檢

```bash
~/.venvs/hermes-bio-memory/bin/python config/db_utils.py
```

正常輸出示例：

```
{'sample_count': 4, 'history_count': 1, 'stale_count': 0, 'l2_ready_count': 1}
```

---

## 常見問題

| 問題 | 解法 |
| ---- | ---- |
| `symlink` 建立失敗 | 確認目標路徑的父目錄存在 |
| `uv sync` 失敗 | 確認已安裝 uv：`pip install uv` |
| llama-server 啟動超時 | 確認模型路徑正確，查看 `logs/llama_server.log` |
| DuckDB 鎖住 | 關閉所有 Python 程序後重新啟動 |
| `l2_ready_count: 0` | 執行 `scripts/01_register_sample.py` 重新登記樣本 |

---

## 相關文件

| 文件 | 說明 |
| ---- | ---- |
| [CLAUDE.md](CLAUDE.md) | 專案憲法（規範、架構、路徑） |
| [plan_zh.md](plan_zh.md) | 完整系統設計 |
| [PROGRESS.md](PROGRESS.md) | 當前進度與待辦事項 |
