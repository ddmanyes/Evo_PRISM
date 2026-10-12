# 個人單機使用

[README](../../README_zh.md) · [English](SINGLE_USER.md) · [繁體中文](SINGLE_USER_zh.md)

## 最小配置

使用本機 Python + DuckDB + stdio MCP，由現有 AI 客戶端負責對話。這條路徑不需要開啟網路服務、Postgres、Tailscale 或多人 worker。一般 metadata 工具可先使用；語意搜尋需要 embedding；實際分析依工具而有額外需求。單機並不代表所有分析都只需 CPU 或少量 RAM。

## 安裝與初始化

準備 Python 3.11 以上及 uv，將 checkout 放在本機 APFS／ext4 等原生檔案系統。避免把虛擬環境建在 ExFAT 或雲端同步目錄。以下命令均在 checkout 根目錄執行。

```bash
uv sync --no-install-project
cp .env.example .env
```

編輯 .env：不使用的 API Key 留空；使用新的資料目錄時先建立目錄。以下是單機設定範例，路徑需自行替換：

```dotenv
BIO_DB_ROOT=/absolute/path/to/ep-data
MCP_AUTH_REQUIRED=false
MCP_ENABLE_DANGEROUS_TOOLS=false
EMBEDDING_PROVIDER=llamacpp
LLAMACPP_BASE_URL=http://127.0.0.1:8081/v1
EMBEDDING_MODEL=bge-m3-Q8_0
EMBEDDING_DIM=1024
ANTHROPIC_API_KEY=
```

此處無驗證設定僅適用本機 stdio。遠端或共用 HTTP 服務必須改用實驗室部署的驗證設定。若不設定 BIO_DB_ROOT，預設使用 checkout 根目錄。不要為了試用而指向既有研究資料庫。

```bash
mkdir -p /absolute/path/to/ep-data
.venv/bin/python scripts/00_init_db.py --no-samples
.venv/bin/python scripts/03_init_l1_cache.py
.venv/bin/python scripts/run_pending_migrations.py
.venv/bin/python scripts/04_migrate_l1_rrf.py
```

--no-samples 只建立 schema，不加入既有實驗室樣本。省略此參數會沿用舊版的樣本種子行為。L1 初始化需要 DuckDB VSS 擴充；未快取擴充時需可下載它的網路環境。遷移使用 pending runner，不逐一重跑舊 migration。

## 連接 MCP 客戶端

以下設定交由客戶端啟動 server；所有路徑需替換成絕對路徑。MCP 客戶端自帶的模型不需在 EP 另啟一個生成模型。

```json
{
  "mcpServers": {
    "ep": {
      "command": "/absolute/path/to/Evo_PRISM/.venv/bin/python",
      "args": ["/absolute/path/to/Evo_PRISM/server/bio_memory_server.py"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/Evo_PRISM",
        "BIO_DB_ROOT": "/absolute/path/to/ep-data",
        "MCP_AUTH_REQUIRED": "false"
      }
    }
  }
}
```

初次連線先呼叫 bio_sample_list，應為空清單；bio_decision_status 應顯示 off。用 bio_register_sample 登記自己的原始資料，再依 playbook 使用合適的分析工具。實際工具清單由 MCP tools/list 取得。

## 依需求加裝

| 能力 | 額外條件 |
| --- | --- |
| 語意搜尋／工具向量索引 | 啟動相容 embedding 服務；模型維度需和 L1 schema 相同 |
| 內建 Web 對話 | 另選生成模型／API 後端，見進階安裝 |
| Bulk RNA / MCseg | 依 pyproject.toml 安裝對應 extras；MCseg 另需 MSseg、資料與運算資源 |
| Clef shadow | 另外配置 bridge 與權重；不影響基礎 MCP 使用 |

Embedding 範例：先取得 bge-m3 GGUF 與 llama.cpp，再啟動下列服務。此步驟不包含在無模型 smoke test 中。

```bash
llama-server -m /absolute/path/to/bge-m3-Q8_0.gguf \
  --embedding --host 127.0.0.1 --port 8081 --ctx-size 8192
```

[Advanced installation](INSTALLATION_zh.md) · [Decision advisor](DECISION_ADVISOR.md)

## 可重現的基本驗證

```bash
.venv/bin/python scripts/check_single_user.py
```

此檢查使用新的暫存資料目錄、忽略 .env、不連接模型；保留 log 與 result.json 路徑。會驗證：

- 空白主資料庫與 L1 初始化、全部 pending migrations、重跑 migrations
- v29 混合 FK／軟引用 schema 的索引與 view 保留
- 真實 MCP stdio 握手與工具列舉，沙盒工具預設不暴露
- 樣本登記、清單回讀、無歷史查詢、決策 off 狀態

2026-10-12 已在 macOS Apple Silicon、Python 3.12.13、DuckDB 1.5.3 的既有依賴環境中實測。使用空白資料庫，未使用正式研究資料。第一次驗證發現 v29 重建未刪索引的問題，修正後重新從空白庫驗證。

範圍限制：尚未證明全新作業系統的依賴安裝、embedding 搜尋、Web UI、GPU／MCseg、所有科學分析或多人部署。通過此檢查代表基礎本機 MCP 與資料庫操作可用。沒有 embedding 時工具索引可能記錄警告；不等於 metadata 工具不可用。
