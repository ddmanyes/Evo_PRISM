# Evo_PRISM (Evo-PRISM) — 環境建置指南 / Setup Guide

本文件說明如何在新機器上從零開始建置 Evo_PRISM 執行環境。

*This guide walks you through setting up Evo_PRISM on a new machine from scratch.*

> [!NOTE]
> **API 與實體命名相容性說明**：
> 雖然本平台已被重塑為通用的 `Evo_PRISM` 自進化平台，但為保持現有代碼、自動化測試以及生物資訊旗艦模組的百分之百相容性，本機底層的 Python 虛擬環境名稱 `hermes-bio-memory`、實體資料庫名稱 `bio_memory.duckdb` 與以 `bio_*` 為首的命令行指令將維持不變，請安心依照以下指南進行設置。

> [!TIP]
> **最快速的方式是 Docker Compose**（見下方 Option A）。若您需要手動安裝，或在 Windows 上開發，請見 [docs/guides/WINDOWS_SETUP.md](docs/guides/WINDOWS_SETUP.md)。

---

## 前置需求 / Prerequisites

Evo_PRISM 採用 **MCP-First（MCP 伺服器優先）** 的設計。若您主要是在 IDE（如 Antigravity）或 CLI（如 Claude Code）中透過雲端 LLM 客戶端來調用工具，**本機的 26B Gemma 模型為選配（Optional）**。

| 項目 | 類型 | 版本 / 說明 |
| ---- | ---- | ----------- |
| Python | 核心 | 3.10 以上（建議 3.11+）|
| [uv](https://github.com/astral-sh/uv) | 核心 | 套件管理工具 |
| llama.cpp | 核心 | 已編譯的 `llama-server` 執行檔（用於本地跑輕量 embedding）|
| bge-m3-Q8_0.gguf | 核心 | **605 MB 輕量模型**，本機 embedding 用於 L1 語意快取與檢索 |
| Gemma 4 Vision 模型 | **選配** | `gemma-4-26B-A4B-it-UD-IQ2_M.gguf` + `mmproj-F16.gguf`（僅在完全離線/完全本機 Web UI 模式下需要）|

---

## Option A：Docker Compose（推薦新使用者）· *Recommended for New Users*

預建映像已包含所有 Python 依賴，無需手動建立 venv。

*The pre-built image bundles all Python dependencies — no manual venv setup needed.*

### 前置
- Docker Engine ≥ 24 + Docker Compose v2
- `bge-m3-Q8_0.gguf`（605 MB）放入 `./models/`

### 步驟

```bash
# 1. 設定環境變數
cp .env.example .env
# 填入至少 ANTHROPIC_API_KEY=sk-ant-...

# 2. 啟動服務（首次會拉取映像 ~343 MB）
docker compose up -d

# 3. 初始化 DB（首次，一次性）
docker compose exec evo-prism python scripts/00_init_db.py
for s in $(docker compose exec evo-prism ls scripts/ | grep '^[0-9][0-9]_migrate' | sort -V); do
    docker compose exec evo-prism python "scripts/$s"
done

# 4. 驗證
docker compose exec evo-prism python config/db_utils.py
# 預期：{'sample_count': 0, 'history_count': 0, 'stale_count': 0, 'l2_ready_count': 0}
```

| 服務 | Port | 說明 |
|------|------|------|
| Web UI | 8000 | FastAPI 對話介面 |
| MCP HTTP | 8080 | 外部 AI 客戶端接入 |
| Embedding | 8081 | bge-m3 sidecar（docker compose 內部）|

或自行 build：`docker build -t evo-prism .`

---

## Option B：Singularity / Apptainer（HPC 叢集）

```bash
singularity build evo-prism.sif Singularity.def
singularity run --bind /mnt/data:/data/bio_db evo-prism.sif server
```

`Singularity.def` 在專案根目錄，可自行 build。

---

## Option C：手動安裝（macOS / Linux）

*以下步驟適用於需要直接存取 GPU、開發模式或特殊磁碟配置的情境。*

---

## 步驟一：複製專案資料夾

將完整的 `Evo_PRISM/` 資料夾複製到目標機器，**必須包含以下數據目錄**：

```
Evo_PRISM/
├── silver/          ← L2 Parquet（CRC 空間轉錄體，416 MB）
├── gold/            ← L1 語意快取
├── bulk_rna_data/   ← Bulk RNA Kallisto 輸出
└── proteome_data/   ← Proteomics 數據
```

> 若以上目錄缺失，分析功能仍可啟動，但無法執行 demo 查詢。

---

## 步驟二：建立 Python 虛擬環境

若專案目錄位於 ExFAT 磁碟，**venv 必須建在 APFS**（家目錄），再以 symlink 接回；一般 Linux/APFS 目錄可直接在專案內建 venv：

```bash
# ExFAT 磁碟（macOS）：建立 venv 於家目錄並 symlink 回來
python3 -m venv ~/.venvs/evo-prism
ln -s ~/.venvs/evo-prism "/path/to/Evo_PRISM/.venv"

# 一般路徑（Linux / macOS APFS）：直接建在專案內
cd /path/to/Evo_PRISM
uv venv

# 安裝依賴
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
# 1) 建立基本 Schema
~/.venvs/hermes-bio-memory/bin/python scripts/00_init_db.py

# 2) 初始化 L1 快取
~/.venvs/hermes-bio-memory/bin/python scripts/03_init_l1_cache.py

# 3) 套用所有後續 migration（v2 → v20）
#    包含 ENGRAM artifact 表、HELIX 工具版本表、BM25 FTS 索引、Star Schema views
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    ~/.venvs/hermes-bio-memory/bin/python "$script"
done

# 3) 登記樣本
~/.venvs/hermes-bio-memory/bin/python scripts/01_register_sample.py
```

> **注意**：`sample_registry` 內的 `l3_path` 存放絕對路徑，換機器後若磁碟掛載點不同需重新登記。
> 跳過 step 2 會造成 ENGRAM 搜尋、HELIX 版本追蹤、Star Schema views 全部不可用。

---

## 步驟五：確認模型與伺服器路徑

開啟 `start_bioagent.sh`，確認開頭定義的 `llama.cpp` 與模型路徑與您的本機相符：

```bash
LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"
EMBED_MODEL="$HOME/llama.cpp/models/bge-m3-Q8_0.gguf"

# 以下為選配（僅在 --local 完全本機模式下需要，純 MCP / 雲端模式可忽略不存在）
VISION_MODEL="$HOME/gemma-4-26B-A4B-it-UD-IQ2_M.gguf"
MMPROJ="$HOME/mmproj-F16.gguf"
```

如果您使用 L1 HNSW 語意快取，本機的 embedding 模型環境變數會在 `config/settings.py` 中被讀取，預設路徑為 `~/llama.cpp/models/bge-m3-Q8_0.gguf`：

```python
LLAMACPP_MODEL_PATH = os.path.expanduser("~/llama.cpp/models/bge-m3-Q8_0.gguf")
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
bash start_bioagent.sh
```

腳本會自動：
1. 啟動 llama-server（Gemma 4 Vision，port 8080）
2. 等待模型載入完畢（最多 120 秒）
3. 啟動 FastAPI Web UI（port 8000）

開啟瀏覽器前往 [http://localhost:8000](http://localhost:8000)

---

## 步驟八：設定 MCP Server（讓 Claude Code / Antigravity / Web UI 共用）

MCP（Model Context Protocol）讓 IDE 端 AI 直接呼叫 bio_DB 的 17 個工具（啟用沙盒後 18 個），跳過 web_app 雙輪 Agent，回應更快、不會發生列表截斷。同一份 `bio_memory_server.py` 同時支援三種客戶端，差別在 transport 與設定檔位置。

### 共通前置：建 symlink 避開含空格 / 中文路徑（macOS Google Drive 必做）

```bash
ln -sfn "/path/to/bio_DB" ~/bio_DB
# 後續所有 MCP 設定都用 /Users/<you>/bio_DB/... 純 ASCII 路徑
```

> Linux / 本機目錄純 ASCII 路徑可略過。

### A. Web UI（HTTP transport，自動掛載）

不需額外設定。`bash start_bioagent.sh` 啟動 FastAPI 時，MCP server 自動掛載於 `http://localhost:8000/mcp`。

驗證：

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -c 200
```

預期：JSON 內含 `bio_history_lookup` 等工具名稱。

### B. Claude Code CLI（stdio transport）

```bash
cd ~/bio_DB
cp .mcp.json.example .mcp.json
```

編輯 `.mcp.json`：

```json
{
  "mcpServers": {
    "bio-memory": {
      "command": "/Users/<you>/bio_DB/.venv/bin/python",
      "args": ["/Users/<you>/bio_DB/server/bio_memory_server.py"],
      "env": {
        "PYTHONPATH": "/Users/<you>/bio_DB",
        "MCP_AUTH_TOKEN": "",
        "MCP_BIND_HOST": "127.0.0.1",
        "MCP_RATE_LIMIT_PER_MIN": "30",
        "MCP_ENABLE_DANGEROUS_TOOLS": "false"
      }
    }
  }
}
```

下次在 `~/bio_DB` 啟動 `claude` CLI 時自動連 MCP server。CLI 內輸入 `/mcp` 驗證連線狀態與工具列表。

### C. Antigravity IDE（stdio transport）

開啟 Antigravity，**Settings → MCP Servers**，新增條目（或直接編輯 `~/Library/Application Support/Antigravity/User/settings.json`）：

```json
{
  "mcpServers": {
    "bio-memory": {
      "command": "/Users/<you>/bio_DB/.venv/bin/python",
      "args": ["/Users/<you>/bio_DB/server/bio_memory_server.py"],
      "env": {
        "PYTHONPATH": "/Users/<you>/bio_DB",
        "MCP_BIND_HOST": "127.0.0.1",
        "MCP_ENABLE_DANGEROUS_TOOLS": "false"
      }
    }
  }
}
```

存檔後 **重啟 Antigravity**。Tool palette 應出現 `bio_*` 工具列表（預設 25 個，啟用 dangerous tools 後 26 個）。

### 想啟用 `bio_execute_code` 沙盒（dangerous）

預設關閉。要讓 MCP client 跑沙盒 Python（產出自動歸檔到 `results/dynamic_code/`）：

```json
"MCP_ENABLE_DANGEROUS_TOOLS": "true"
```

僅在以下條件全部成立時開啟：

- 客戶端是本機（`MCP_BIND_HOST=127.0.0.1`）
- 對外暴露時搭配 `MCP_AUTH_TOKEN`
- 已 review `server/code_executor.py` 的 `BLOCKED_PATTERNS` 白名單

詳細安全建議見 [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md)。

---

## 健檢

```bash
~/.venvs/hermes-bio-memory/bin/python config/db_utils.py
```

正常輸出示例：

```text
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
| [README.md](README.md) | 專案概覽與快速入門 |
| [docs/guides/STAR_SCHEMA.md](docs/guides/STAR_SCHEMA.md) | Star Schema views 設計與使用範例 |
| [docs/guides/MCP_JSON_SETUP.md](docs/guides/MCP_JSON_SETUP.md) | MCP stdio 設定（Claude Code / IDE）詳細說明 |
| [docs/guides/MCP_HTTP_GUIDE.md](docs/guides/MCP_HTTP_GUIDE.md) | MCP HTTP transport + curl 範例 |
| [docs/guides/WINDOWS_SETUP.md](docs/guides/WINDOWS_SETUP.md) | Windows 專用安裝注意事項 |
