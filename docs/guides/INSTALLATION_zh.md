# 進階安裝與既有部署

這是既有部署與進階安裝參考。**個人單機請先看 [單機指南](SINGLE_USER_zh.md)**。目前根目錄 Compose 使用實驗室的 build 路徑、Tailscale 位址、共用模型端點及 worker；需依部署調整，不能直接視為通用的一鍵安裝。

本頁保留的測試資料範例依賴本機 fixture；公開 checkout 不一定含有這些檔案。


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
- `bge-m3-Q8_0.gguf`（605 MB 語意搜尋模型，從 [HuggingFace ggml-org/bge-m3-Q8_0-GGUF](https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF) 下載 Q8_0 量化版）

```bash
# 1. 把下載好的模型放入專案的 models/ 資料夾
mkdir -p models
# 將 bge-m3-Q8_0.gguf 移入 models/

# 2. 複製環境設定（MCP 模式不需填入 API Key）
cp .env.example .env

# 3. 以核心模式啟動（只依賴 KINGSTON；首次會下載映像）
docker compose up -d

# 4. 驗證；資料庫初始化與待執行 migration 會在容器啟動時自動完成
curl -s "http://${EP_HOST}:8000/health"  # set EP_HOST to the configured bind address
```

預設核心模式的程式、DuckDB 與分析結果都來自 KINGSTON，且不依賴
`/Volumes/SSD`。只有 MCseg 需要讀取 SSD 上的唯讀原始 L3 資料時，才切換成
SSD 模式：

```bash
test -d /Volumes/SSD
docker compose -f docker-compose.yml -f docker-compose.ssd.yml \
  up -d --force-recreate
```

拔除 SSD 前，先重建回核心模式：

```bash
docker compose up -d --force-recreate
```

完整驗證與故障復原方式請見
[可選 SSD 掛載指南](../../docs/guides/OPTIONAL_SSD_MOUNT.md)。

完成！Web UI 與 MCP（`/mcp/`）共用 **the configured host on port 8000**。

> 或直接使用預建映像：`docker pull ddmann375000/evo-prism:0.1.0`
>
> HPC / Singularity 叢集用戶請見 [SETUP.md](../../SETUP.md#singularity)。

---

### 💡 路徑二：手動安裝 + MCP（連接 IDE / CLI）

> 此路徑讓您在 **Claude Code CLI** 或 **Antigravity IDE** 中，直接以自然語言呼叫 分析工具，不需要另外開瀏覽器。

**事前準備：**

- Python ≥ 3.10（建議 3.11）
- [uv](https://github.com/astral-sh/uv) 套件管理器（`pip install uv`）
- [llama.cpp](https://github.com/ggml-org/llama.cpp) 已編譯（執行本機 embedding 服務）
- `bge-m3-Q8_0.gguf`（605 MB，放於 `~/llama.cpp/models/`）
- An API key only if using the corresponding Web Agent backend

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
.venv/bin/python scripts/03_init_l1_cache.py

# 步驟 5：執行所有資料庫版本更新（只需執行一次）
.venv/bin/python scripts/run_pending_migrations.py
.venv/bin/python scripts/04_migrate_l1_rrf.py
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
- **Antigravity IDE**：Settings → MCP Servers 新增條目，詳見 [MCP_JSON_SETUP.md](../../docs/guides/MCP_JSON_SETUP.md)
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
