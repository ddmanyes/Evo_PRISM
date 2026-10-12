# 個人單機使用

[README](../../README_zh.md) · [English](SINGLE_USER.md) · [繁體中文](SINGLE_USER_zh.md)

## 最小配置

本機 Python + DuckDB + stdio MCP，由現有 AI 客戶端負責對話。基本 metadata 與 CPU EDA 不需 Tailscale、Postgres、多人 worker 或 Clef。語意搜尋另需 embedding；MCseg 等分析仍有模型、GPU 與依賴需求。

## 安裝、初始化與連線

先安裝 uv，checkout 放在 APFS／ext4 等本機原生檔案系統；避免 ExFAT 或雲端同步目錄。以下在 checkout 根目錄執行。uv 會依鎖定檔建立依賴環境；需網路下載套件及 DuckDB 擴充。

```bash
uv sync --locked --no-install-project --python 3.12
.venv/bin/python scripts/ep_local.py init --data-dir "$PWD/local-data"
.venv/bin/python scripts/ep_local.py doctor --data-dir "$PWD/local-data"
```

初始化會建立空白主資料庫、L1、鎖定目錄、執行 pending migrations，並產生含絕對路徑的 local-data/mcp.json。把其中 mcpServers.ep 加入客戶端設定，再連接 EP。它不启动任何 HTTP 服務或模型。

重跑 init 保留既有樣本與分析；若 MCP JSON 已存在但內容不同，會停止而不覆寫。可用 --config 選擇新檔案。generated config 明確固定資料路徑、關閉遠端驗證需求／沙盒，並忽略 .env；這只適用本機 stdio。正式／遠端部署使用實驗室指南。

首次呼叫 bio_sample_list 應為空清單；bio_decision_status 為 off。登記自己的資料後才執行分析。

## 診斷與修復

```bash
.venv/bin/python scripts/ep_local.py doctor --data-dir "$PWD/local-data" --check-services
```

doctor 以 JSON 顯示依賴、主 DB、L1、可選 embedding/Clef 狀態與修復建議。必要項缺失時 exit code 為 1；可選 embedding 不在線不會把 metadata 模式判為失敗。--check-services 只 GET /health，不執行推理。Clef 以唯讀方式讀取 ledger 的 off/rules/shadow 狀態；bridge 即時狀態以 bio_decision_status 為準。健康端點可達不等於語意檢索已驗證。

| 情況 | 處理 |
| --- | --- |
| 缺套件 | `uv sync --locked --no-install-project` |
| 缺 DB／L1／schema 過舊 | 停止寫入程序，備份後重跑 init；勿刪 DB |
| DB 被鎖定 | 停止另一個寫入程序後重試；單機不需啟多個 server |
| embedding 不在線 | 基礎 metadata 可用；啟動相符模型再用語意搜尋 |

## 完整可重現示範

```bash
.venv/bin/python scripts/check_single_user.py
```

自動建立獨立暫存資料與 40 genes × 6 samples 合成 counts，執行真正的 MCP 流程：

1. 初始化空白 DB/L1，建立 MCP 連線與列舉工具
2. 登記樣本；拒絕越出已登記資料目錄的輸入
3. 呼叫 bio_run_bulk_eda，產生 4 張圖與 1 份報告
4. 驗證 tool_id、產物檔案、counts 統計與原始檔 hash
5. 關閉連線，重跑 init，再次連線查歷史與報告；分析筆數仍為 1

結果保留於輸出的暫存路徑，含 result.json、初始化與 server logs、報告及圖片。示範使用正常的 analysis_run/ENGRAM 流程，不依賴真實研究資料，也不呼叫生成／embedding 模型。這證明明確查詢後重用既有成果；不是模型自動決策品質驗收。

## 分析自己的 counts

先用 bio_register_sample 登記 sample_id、data_type=bulk_rnaseq 與 l3_path（目錄）。counts TSV 為 gene × sample，第一欄是 gene ID。coldata 可選，第一欄 sample ID、group 欄為分組。接著呼叫：

```json
{"sample_id":"my_bulk","counts_path":"counts.tsv","coldata_path":"coldata.tsv"}
```

工具為 bio_run_bulk_eda。路徑相對於該樣本已登記 L3 目錄，也可用目錄內的絕對路徑。省略 counts_path 仍沿用舊版實驗室預設，因此新使用者應明確傳入。

## 可選能力與驗證界限

語意搜尋需要 bge-m3 等 embedding 服務及相符維度（初始化預設 1024）；可用 init --embedding-url 設定端點，或修改產生的 MCP JSON。Web 對話另需生成模型/API；Clef shadow 另需 bridge 與權重。Bulk DEG/MCseg extras 和外部 MSseg 不屬本次基本 EDA 驗證。

驗證證據與各平台結果記於 [readiness report](PERSONAL_READINESS_PLAN.md) 及 GitHub Actions。

[Lab deployment](LAB_DEPLOYMENT_zh.md) · [Advanced installation](INSTALLATION_zh.md) · [Decision advisor](DECISION_ADVISOR.md)
