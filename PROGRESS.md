# 智慧生資分析平台 — 進度封存

> 每次完成一個里程碑後更新本文件。
> 詳細設計見 [plan_zh.md](plan_zh.md)，專案規範見 [CLAUDE.md](CLAUDE.md)。

---

## 📍 當前里程碑

**里程碑**：Phase 10 完成 + WAL crash 後穩定性整備 + MCP server 審查 + 穩定性 P0/P1/P2 全清 + MCP P1/P2 全清
**平台**：macOS `/Volumes/NO NAME/bio_DB/`（ExFAT）
**最後更新**：2026-05-19
**commit**：（待回填）

---

## ✅ 2026-05-19 Session MCP P2 全清

- [x] **`bio_history_timeline` 加 `limit` 參數**：schema 補 `limit`（default 50, max 500）；handler 用 `max(1, min(int(args.get("limit", 50)), 500))` clamp 後直接拼進 SQL；`n_days` 大時可調高避免漏掉早期紀錄
- [x] **`_fmt_table` 防破表格**：新增 `_pipe_safe(s, max_len)` helper，將 `|`/`\n`/`\r` escape 並截斷（header 40 字、data cell 60 字）；ExFAT `/Volumes/NO NAME/` 含空格與 `|` 路徑不再破壞 Markdown 表格欄位對齊
- [x] **`mcp_tool_metrics` 表 + observability hook**：新增 `(metric_id UUID PK, tool_name, duration_ms INTEGER, status VARCHAR, recorded_at TIMESTAMP)` 表（lazy `CREATE TABLE IF NOT EXISTS` + `idx_mcp_metrics_tool_time` composite index）；`call_tool` 在 4 個 return path（`ok` / `user_error` / `system_error` / `rate_limited`）皆呼叫 `_record_metric()`，best-effort 寫入不阻擋回傳
- [x] **`test_phase10.py` e2e 工具呼叫補強**：新增 5 個 class（`TestE2EToolCalls`、`TestAuthMiddleware`、`TestRateLimitGate`、`TestMetricsRecording`）共 11 tests，涵蓋：
  - `bio_history_lookup` / `bio_history_timeline` / `bio_history_check` true/false 端對端讀真 DB
  - `MCP_AUTH_TOKEN` 缺/錯 token → 401，未設定 env → auth 關閉
  - rate limit 第 3 次呼叫被擋（`MCP_RATE_LIMIT_PER_MIN=2`）
  - `mcp_tool_metrics` `ok` + `user_error` 兩類 status 確實寫入
- [x] **`test_phase4.py::test_write_to_l1` 順序穩定化**：補 `patch("analysis.l1_cache.L1_CACHE_PATH", l1_db)`，避免 `analysis.l1_cache` 已被 import 時模組層 binding 仍指向真實 `/Volumes/NO NAME/...gold/hermes_cache.duckdb`；測試現可任意順序執行
- [x] **驗證**：`tests/test_phase10.py`（26 tests）+ `tests/test_phase4.py`（19 tests）= 45/45 PASS

---

## ✅ 2026-05-19 Session MCP P1 全清

- [x] **`call_tool` 例外重構**：未知工具改為回 `[ERROR] 未知工具：...`（不再 raise，避免 MCP transport 中斷）；新增 `(ValueError, KeyError, TypeError) → [ERROR] {name} 參數錯誤：...`（log level info）與 `Exception → [ERROR] ... 系統錯誤（correlation_id=<8-char hex>）`（log level error，server-side stack trace 對照）；`RateLimitExceeded` 自定例外類別預留 handler 內部使用
- [x] **`_rate_limit_check` 接入**：模組層 `_RATE_LIMITED_TOOLS = {bio_history_search, bio_memory_query, bio_memory_write}`；`call_tool` 進 handler 前 gate，超限回 `[ERROR] {name} 已達速率上限（N calls / 60s）`；env `MCP_RATE_LIMIT_PER_MIN` 可調（預設 30）
- [x] **`MCP_AUTH_TOKEN` HTTP 認證**：`create_http_app()` 內檢查 `Authorization: Bearer <token>` header，缺失/不符回 401 plain-text；env 未設定時自動關閉（向後相容 web_app 內部 mount）；新增 `_send_auth_error()` / `_extract_bearer_token()` helper；smoke 測試 no-auth/bad-token 雙路徑 → 401
- [x] **`cleanup_stale_runs` 啟動時呼叫**：新增 `_startup_cleanup_stale_runs()`；stdio/http 兩條入口（`_run_stdio` + `_mcp_lifespan`）皆呼叫；DB 不存在或失敗為 non-fatal warning（不阻擋 server 啟動）
- [x] **`test_phase10.py` 更新**：套用 `create_http_app() → (handler, lifespan_cm)` tuple API；新增 `_build_starlette_app()` helper 用 Starlette 父 app 驅動 lifespan；3 個既有失敗測試（`test_returns_asgi_callable` / `test_has_asgi_call_signature` / `test_idempotent_creation`）改為 tuple-aware 並全部通過；15/15 PASS（先前 3 fail + 8 error → 0 fail）
- [x] **`test_phase4.py` 更新**：`test_unknown_tool_raises` 改名 `test_unknown_tool_returns_error`，斷言 TextContent 包含 `未知工具`；17/17 PASS（先前 1 fail → 0 fail）
- [x] **驗證**：rate-limit smoke（`MCP_RATE_LIMIT_PER_MIN=2` 第 3 次呼叫即被擋）+ auth smoke（401 雙路徑）+ phase4/phase10 共 34/34 tests PASS

### 上一輪 MCP P1 部分完成（封存於前一 commit d548573）

- [x] **`bio_memory_write` sample_id 驗證**：`_SAMPLE_ID_RE = ^[a-z0-9_-]+$`，與 `bio_register_sample` 對齊；格式不符 raise ValueError
- [x] **rate limit / correlation ID 基礎設施**：模組層 `_rate_limit_check(key)` token bucket（預設 30 calls/min，env `MCP_RATE_LIMIT_PER_MIN` 可調）、`uuid` import 預留

---

## ✅ 2026-05-19 Session 穩定性 P2 全清

- [x] **WAL pre-flight check**：`config/db_utils.wal_preflight_check()` 在 `web_app._lifespan` 最早期執行；read-only 試開失敗時 rename `.wal → .wal.corrupt.<ts>`，狀態寫 `logs/wal_preflight_status.json`，並上報 `/health.wal_preflight`；驗證 `wal_preflight.ok=true checked_at=2026-05-19T10:49:57`
- [x] **每週 round-trip 還原測試**：新增 `scheduler/weekly_restore_test.py`（INSTALL/LOAD vss + `hnsw_enable_experimental_persistence`）+ launchd 範本 + `com.hermes.weekly_restore_test`（週日 05:00）；手動執行 91 samples / 16 history 與主庫一致
- [x] **agent.py safe_write 合規審查**：發現 `bio_run_spatial_eda` / `bio_run_bulk_eda` 兩處 `analysis_history UPDATE tool_id` 繞過 `safe_write`，已改走 `safe_write`（含 CHECKPOINT）；`bio_execute_code` INSERT 早已合規；全檔僅剩 SELECT 直接 `con.execute`

---

## ✅ 2026-05-19 Session 穩定性 P1 全清

- [x] **launchd 排程批次安裝**：6 個 plist 全部 load 成功（cleanup_l1 / rebuild_hnsw / scan_samples / helix_expire / embedding_server / multimodal_server）；連同原有 webserver + backup 共 8 個 hermes job
- [x] **plist Label 命名正規化**：`launchd_helix_expire.plist.example` 與 `launchd_multimodal_server.plist.example` 範本 Label 從舊 `com.bioagent.*` 改為 `com.hermes.*` 命名一致；Log 目錄 `~/Library/Logs/bioagent/` 補建
- [x] **embedding/multimodal server 自動拉起**：兩個 llama-server 由 launchd KeepAlive 接管，crash 後自動 restart；驗證 8081 `{"status":"ok"}`、8080 `200`
- [x] **`/health` 端點擴充**：新增 `embedding_server_ok` / `multimodal_server_ok` / `backup.{last_success_at, last_success_age_hours, last_size_bytes, last_error, fresh}` / `disk_free_gb`；`ok` 總判定 = DB ok + embedding ok + 備份 < 36h 新鮮度；觀測閉環完成

---

## ✅ 2026-05-19 Session 後續修復（P0 全清）

- [x] **歷史資料遺失盤點**（穩定性 P0-1）：比對 `~/bio_db_backups/20260515_1253/analysis_history.csv` 僅 1 筆（最早 l2_convert），現行 DB 16 筆，**無資料遺失**。先前推測「30+ → 16」不成立；5/15 之後從未有完整備份，但 DB 主檔本身未掉資料
- [x] **`scheduler/backup_db.py` 加固**（穩定性 P0-2 + P0-3）：新增 `MIN_BACKUP_BYTES=100KB` 門檻、失敗自動刪空目錄、`logs/backup_status.json` 記錄 `last_success_at`/`last_failure_at`/`last_size_bytes`/`last_error`、失敗時 `sys.exit(1)`；新增 `--prune-empty` 子命令一次清掉 6 個歷史 0-byte 目錄；驗證 0.8 MB 成功備份寫入 status JSON
- [x] **MCP HTTP 500 修復**（MCP P0-1）：根因為 `FastAPI.mount()` 不傳遞 lifespan 給子 ASGI app，`session_manager.run()` 從未啟動；改 `create_http_app()` 回傳 `(handler, lifespan_cm)` tuple，由 `web_app._lifespan` 統一驅動。重啟後 `/mcp/` initialize + tools/list 皆 200，7 工具完整列出
- [x] **`docs/MCP_HTTP_GUIDE.md`**（Phase 10 P10-5）：curl 與 httpx 範例、Accept header 規範、7 工具表、6 類常見錯誤排查、部署注意（綁定/認證/rate limit）
- [x] **`bio_history_search` threshold 統一**（MCP P0-3）：schema 預設 `0.5 → 0.88`、實作 fallback 改讀 `L1_COSINE_THRESHOLD`；MCP/Agent 雙端 Cache Hit Protocol 對齊

---

## ✅ 2026-05-19 Session 封存

### WAL crash 緊急修復與穩定性建置

- [x] **DB 重建**：write-mode 開啟時 C++ FatalException duplicate key `372b4182`（WAL replay 失敗，無法在 Python catch）；以 read-only EXPORT → 刪除 → 重建 schema → reimport（FK ordering：samples→history）；最終 91 samples / 15 history / 2 tools 完整還原
- [x] **`scheduler/backup_db.py` 修復**：`EXPORT DATABASE ?` placeholder 不被 DuckDB parser 接受 → 改 f-string；同步修正 `IMPORT DATABASE` 與 restore 段 pre-backup；5/16–5/18 連續四日備份失敗根因解除
- [x] **`scripts/17_migrate_schema_v16.py` / `scripts/18_migrate_schema_v17.py`**：`tool_artifact_lineage` VIEW 內 `t.source_hash` → `t.content_hash`（tools 表正確欄位名）
- [x] **`server/web_app.py` `_deferred_cleanup`**：改 read-only pre-check（先查 zombie 數，只在需要時開 write 連線觸發 WAL replay），降低 WAL 損壞風險
- [x] **launchd 自動重啟**：建立 `~/bin/hermes_webserver.sh`（APFS，ExFAT 無法執行 launchd 腳本）+ `com.hermes.webserver.plist`（`KeepAlive=true`、`ThrottleInterval=5`）；kill→restart < 6s 驗證
- [x] **`com.hermes.backup`**：已 load 且測試成功，`20260519_0938  0.8 MB`

### Code Review 審查（兩份 PROGRESS.md 待辦清單）

- [x] **穩定性審查**：14 項分 P0/P1/P2/P3 記錄（資料完整性、launchd 排程未完整安裝、`/health` 擴充、WAL pre-flight、每週還原驗證）
- [x] **MCP server 審查**：15 項分 P0/P1/P2/P3 記錄（HTTP endpoint 500 bug、工具覆蓋不完整、threshold 不一致、雙份維護、ENGRAM 未暴露）

---

## ✅ 已完成

### 計畫與設計
- [x] `plan_zh.md` — 完整七階段系統設計（中文），含 Code Promotion、tools 表擴展、資料庫安全、HNSW 維護、Linux 遷移 checklist
- [x] `plan.md` — 英文版設計計畫
- [x] `CLAUDE.md` — 專案憲法（規範、架構、路徑、ExFAT 限制）
- [x] `docs/L3_DATA_INGEST_GUIDE.md` — L3 新增樣本操作指南
- [x] `docs/TEST_DATABASE_INDEX.md` — 測試資料庫索引文件
- [x] `docs/launchd_backup.plist.example` — macOS 排程範本
- [x] `IMPLEMENTATION_PLAN.md` + `execution_trace.md` — Phase 執行追蹤

### 測試數據準備
- [x] CRC Visium HD 官方數據 (`crc_visium_data/official_v4/`, ~39GB)
- [x] MSseg 分析程式碼複製至 `analysis_msseg/`, `backend_msseg/`, `msseg_docs/`
- [x] 分析中間結果複製至 `data_ana/` (1.6GB), `results_ana/` (3.9GB)
- [x] `.gitignore` 設定（含 `results/`、`bio_db_backups/`）

### Phase 1：環境與 Schema（完成）
- [x] `pyproject.toml` + `uv sync --no-install-project`
- [x] venv 建於 APFS（`~/.venvs/bioagent`）+ symlink 至 `.venv`
- [x] `config/settings.py` — 集中路徑設定
- [x] `scripts/00_init_db.py` — sample_registry + analysis_history + analysis_index view
- [x] `analysis_history.tool_id UUID` 預留欄位（未來 tools 表 FK）
- [x] DuckDB VSS 擴充驗證可載入
- [x] `tests/test_init_db.py` — 4/4 PASSED
- [x] sample_registry 填入 4 筆樣本（`crc_official_v4` 等）

### Phase 2A：L2 空間轉錄體（完成）
- [x] `scripts/02_spatial_to_parquet.py` — chunked long-format 轉換
- [x] 輸出 `silver/spatial_counts_crc_official_v4_8um/`（104 parts, 416 MB）
- [x] 輸出 `silver/spatial_meta_crc_official_v4.parquet`（516,880 bins）
- [x] 215,440,730 nonzero entries，運行時間 103 秒
- [x] DuckDB 可依基因名稱與空間座標查詢驗證

### 資料庫安全（完成）
- [x] `config/db_utils.py` — `safe_write()` / `cleanup_stale_runs()` / `db_health_check()`
- [x] `scheduler/backup_db.py` — EXPORT DATABASE 每日備份 + 7 天保留 + `--restore` 還原
- [x] 備份還原 round-trip 驗證通過（4 樣本 + 1 歷史 + tool_id + view 完整還原）
- [x] 健檢回傳：`{'sample_count': 4, 'history_count': 1, 'stale_count': 0, 'running_count': 0, 'l2_ready_count': 1}`

---

## ✅ Phase 2B 完成（2026-05-15）

- [x] `analysis/spatial_eda.py` — 基因空間圖（`gene_spatial_map`）、QC 統計（`qc_stats`）、`top_genes`、共表達散點圖
- [x] `analysis/history_query.py` — 0-token DuckDB 查詢（`recent_analyses` / `sample_summary` / `find_by_type` / `analysis_index` / `search_summaries`）
- [x] `analysis/report_generator.py` — Markdown EDA 報告 + ≤50 字中文摘要（語意搜尋核心語料）
- [x] `tests/test_phase2b.py` — 14/14 PASSED（7 history_query + 5 report_generator + 2 smoke）
- [x] 真實數據驗證：crc_official_v4 → 摘要 50 字、報告儲存至 `results/`

---

## ✅ Phase 3 + 3.5 完成（2026-05-15）

- [x] launchd 每日備份排程已啟用（com.bioagent.backup）
- [x] `scripts/03_init_l1_cache.py` — gold/hermes_cache.duckdb + memory_recent + HNSW（cosine）
- [x] `scheduler/cleanup_l1_cache.py` — TTL 清理（每日 03:30）
- [x] `scheduler/rebuild_hnsw.py` — HNSW 重建（每週日 03:00）
- [x] `tests/test_phase3.py` — 15/15 PASSED
- [x] Phase 3.5：**本機 embedding 接入**（llamacpp bge-m3-Q8_0，1024-dim）
  - `analysis/embed.py` — llamacpp/openai/google 三 provider
  - `analysis/l1_cache.py` — write_to_l1_cache() + semantic_search()
  - E2E 驗證通過：score=0.63 for CD8A query

---

## ✅ Phase 4 完成（2026-05-15）

- [x] `mcp` 套件安裝至 venv
- [x] `server/bio_memory_server.py` — 7 個 MCP 工具（bio_history_* + bio_memory_* + bio_register_sample）
- [x] `tests/test_phase4.py` — 19/19 PASSED（0.97 秒）
- [x] `bio_DB/.mcp.json` — Claude Code MCP Server 設定（gitignored）
- **總測試數**：54/55 PASSED（test_crc_8um_exists 為既有路徑問題）

---

## ✅ Phase 5 完成（2026-05-15）

- [x] `anthropic` 套件安裝（v0.102.0）
- [x] `server/code_executor.py` — macOS 沙盒執行器
  - ALLOWED_IMPORTS 白名單（duckdb, pandas, numpy, scipy, anndata, scanpy…）
  - BLOCKED_PATTERNS 黑名單（os.system, subprocess, eval, exec, open()…）
  - `is_safe(code)` → (bool, reason)；`sandbox_exec(code, timeout=60)` → ExecResult
- [x] `server/agent.py` — 推理引擎切換至本機 llama.cpp（OpenAI-compatible API）
  - BIO_TOOLS：8 個工具定義（bio_history_* + bio_memory_* + bio_run_* + bio_execute_code）
  - `_to_openai_tools()` 將 Anthropic schema 轉為 OpenAI function calling 格式
  - `handle_message(user_msg, history=[])` → AgentResponse（含 tool_calls + token 統計）
  - `execute_tool(name, input)` → str（分發至 Python 工具執行）
  - `run_cli()` 互動式 CLI（本機測試用）
  - 推理引擎：`openai.OpenAI(base_url="http://localhost:8080/v1")`（Gemma 4 Vision）
- [x] `tests/test_phase5.py` — 28/28 PASSED
  - TestIsSafe（10 tests）：白名單/黑名單安全檢查
  - TestSandboxExec（5 tests）：沙盒執行（含 timeout）
  - TestExecuteToolDispatch（7 tests）：工具分發（mock DB）
  - TestHandleMessage（6 tests）：Agent Loop（mock Claude API）
- **總測試數**：82/83 PASSED（test_crc_8um_exists 為既有路徑問題）

---

## ✅ Phase 6 完成（2026-05-15）

- [x] `server/telegram_bot.py` — Telegram Bot（python-telegram-bot v22）
  - 白名單過濾（`TELEGRAM_ALLOWED_USER_IDS`，空白名單預設全拒）
  - `/start`、`/help`、`/history [sample_id]`、`/status` 指令
  - 自然語言訊息 → `handle_message()`（Agent Loop）
  - per-user 對話歷史（最近 12 輪）
  - 長文字自動分段（4000 字元/段）
  - typing... 狀態提示
- [x] `pytest-asyncio` 安裝 + `pyproject.toml` 加 `asyncio_mode = "auto"`
- [x] `tests/test_phase6.py` — 23/23 PASSED
  - TestIsAllowed（3）：白名單邏輯
  - TestSplitText（4）：訊息分段
  - TestCmdStart/Help/History/Status（8）：指令 handler
  - TestOnMessage（8）：自然語言分派、歷史管理、錯誤處理
- **總測試數**：105/106 PASSED（test_crc_8um_exists 為既有路徑問題）

---

## ✅ Phase 7 完成（2026-05-16）

- [x] `server/agent.py` — 推理引擎雙後端支援
  - `openai` 套件安裝至 venv（v2.37.0）
  - `_to_openai_tools()` 轉換工具格式（Anthropic → OpenAI function calling）
  - `handle_message(backend=)` 支援 `"local"` / `"claude"` 動態切換
  - `_make_local_call()` / `_make_claude_call()` 分離實作
  - 工具結果截斷至 800 字元，防止撐爆 context window
  - max_tokens 預設提升至 8192
  - 修復 5 個 HIGH 問題（history 過濾、tool_calls 序列化、exhaustion path、JSON decode、client 共用）
- [x] `start_bioagent.sh` — 一鍵啟動腳本
  - 自動啟動 llama server（等待模型載入最多 120 秒）+ FastAPI Web UI
  - 偵測已運行 server 並跳過，Ctrl+C 同時停止兩個 server
  - ctx-size 提升至 16384（適合 18GB 記憶體）
  - `--threads $(sysctl -n hw.physicalcpu)` 自動設定 CPU 執行緒
  - Log 寫入 `logs/llama_server.log` / `logs/web_app.log`
- [x] `pyrightconfig.json` — IDE 指向正確 venv，消除假錯誤
- [x] `server/web_app.py` — 後端切換 API
  - `ChatRequest.backend` 欄位（"local" / "claude"）
  - `GET /api/backend` — 查詢預設後端與 llama server 狀態
  - SSE tokens 事件加入工具呼叫數（`tools` 欄位）
- [x] `server/static/index.html` — UI 改善
  - Sidebar 加「本機 / Claude」切換按鈕，選擇存 localStorage
  - `_sending` flag 防止 Enter 重複送出
  - Token 計數：llama.cpp usage=null 時 fallback 顯示工具呼叫數
- [x] `config/settings.py` — 新增 `INFERENCE_BACKEND`、`CLAUDE_MODEL` env var
- [x] `server/code_executor.py` — 白名單加入 `glob`
- [x] `analysis/report_generator.py` — EDA 報告嵌入 QC 圖
  - `_generate_qc_figure_b64()` — genes/bin + UMI/bin 分布圖 base64 內嵌 Markdown
  - `_collect_stats()` 回傳 `obs_df` 供繪圖使用
  - 模板加入 `{qc_figure}` 佔位符

---

## ✅ Phase 8 完成（2026-05-16）

- [x] `server/static/index.html` — 圖片上傳功能
  - 附件按鈕（🖼）+ 剪貼簿 Ctrl+V 貼圖
  - 圖片預覽條（送出前可清除）
  - 用戶訊息泡泡顯示縮圖
- [x] `server/agent.py` — 視覺分析支援
  - `handle_message(image_base64=)` 參數，組裝 openai `image_url` content block
  - Claude backend：自動轉為 Anthropic `base64 image` block
  - 延遲初始化 `_local_client`（`_get_local_client()`），避免 import 時連線
- [x] `server/web_app.py` — 圖片 SSE 傳遞
  - `_extract_images_from_tool_calls()` 從 result_path .md 抽出 base64 圖片
  - `message` SSE event 附帶 `images[]`（filename + data_uri）
  - 圖片讀取移至 executor thread，不阻塞 event loop
  - Session TTL 清理（24h，每小時自動執行）
  - `GET /api/results/{id}/images` 端點供歷史頁使用
- [x] `server/static/index.html` — Bot 回覆圖片卡片
  - `img-card` 樣式：圖片預覽 + 檔名 + ⬇ 下載按鈕
- [x] `server/static/history.html` — 歷史記錄圖片預覽
  - 每筆有報告的分析記錄可展開圖片縮圖列
- [x] `analysis/report_generator.py` — QC 圖嵌入報告（已於 Phase 7 完成）
- [x] `server/agent.py` — `bio_execute_code` matplotlib 圖自動捕獲
  - plt.show() hook → 存 PNG → base64 嵌入工具結果
- [x] `tests/test_phase5.py` — mock 從 anthropic 改為 openai（28/28 PASSED）
- [x] regex 修正：base64 抽取改用字符類 `[A-Za-z0-9+/=]` 避免 `)` 截斷

---

## ✅ 文件完整化完成（2026-05-17）

### plan_zh.md 重構

- [x] 章節重編：修復重複「十一」問題，統一從一到十九，加附錄 A/B/C
- [x] 新增**附錄 A：設計決策與文獻依據**（6 小節）
  - A1 三層 Medallion 架構（Databricks + LakeHarbor ICDE 2024）
  - A2 HNSW 向量語意搜尋（DuckDB VSS + Malkov & Yashunin 2018）
  - A3 Agent-First + Token 省策（Agent-First 2025 + MemGPT）
  - A4 兩階段寫入 + 狀態機（WAL / crash recovery + saga pattern）
  - A5 Code Promotion 自動升格框架（progressive rollout + memoization）
  - A6 多模態視覺分析（Gemma 4 Vision + llama.cpp）
- [x] 新增**附錄 B：驗收標準與驗證方法**（5 小節）
  - B1 消除重複運算（L1 命中率 ≥ 80%）
  - B2 Token 消耗可控（0-token 工具單元測試）
  - B3 分析可追溯（analysis_history + stale 狀態）
  - B4 使用門檻低（端對端手動測試）
  - B5 數據安全（safe_write + 每日備份 + 還原驗證）
- [x] 新增九（推理引擎雙後端）、十一（Web UI 架構）章節
- [x] 修正日期（2026-05-16 → 2026-05-17）
- [x] 修正 anndata_scanpy.md 對應章節（十一 → 十二、十三）
- [x] 修正沙盒策略標記（Phase 5+ → 第十一階段）

### CLAUDE.md 修正

- [x] Schema 說明中 embedding 維度 `FLOAT[1536]` → `FLOAT[1024]`（與實際 bge-m3 一致）

### presentation.md 重構為 Marp 格式

- [x] 加入 Marp frontmatter（theme、paginate、自訂 CSS）
- [x] 重組為標準報告結構：前言 → 問題 → 目標 → 方法 → 結果 → 討論 → 結論 → 下一步
- [x] 拆分為 13 張投影片（含封面 + 附錄架構圖）
- [x] 補充**非本科系聽者**的生物資訊背景說明（Slide 1：空間轉錄體、Bulk RNA、Proteomics 白話解釋）
- [x] Slide 6 補充 HNSW 全名與定義
- [x] 新增 Slide 10 討論（結果意義 + 系統限制）
- [x] 新增 Slide 12 獨立結論頁
- [x] 修正所有 linting 警告（MD022/MD032/MD033/MD040/MD060）

---

## ✅ agent.py 重大修復完成（2026-05-17）

### Cache Hit Protocol

- [x] `bio_history_check`：SELECT 加入 `parameters` 欄位回傳
- [x] `bio_history_search`：enrichment 改用 `l1_cache_id IN (...)` 批次查詢（精準 join），UUID 型別統一轉 `str`
- [x] `bio_history_search`：threshold 預設值 0.5 → 0.88（與規格第五章一致）
- [x] `SYSTEM_PROMPT`：新增 Cache Hit Protocol 段落（觸發條件、條件式 result_path 展示、不需再呼叫 bio_memory_query）

### Code Promotion 框架修復

- [x] `_exec_bio_execute_code`：成功後寫入 `analysis_history`（含 `analysis_id` UUID + `parameters["generated_code"]`），promotion_candidates VIEW 可正常掃描
- [x] `_exec_bio_execute_code`：`tempfile.mkdtemp` → `TemporaryDirectory` context manager，修復 SecurityError 時的 tempfile 洩漏

### 架構合規修復

- [x] `_startup_cleanup()`：新增函數，`run_cli()` 啟動時呼叫 `cleanup_stale_runs()`（第六章規範）
- [x] `_exec_bio_register_sample`：改用 `get_connection()` 單例，避免多程序 DuckDB 寫入鎖衝突
- [x] `_startup_cleanup`：改用 `get_connection()` 單例
- [x] Claude backend：`content_blocks` 存入 messages 前呼叫 `model_dump()` 序列化
- [x] `_get_local_client()`：openai import 改為 lazy（函數內部），避免未安裝時模組無法載入

### 文件更新

- [x] `plan_zh.md`：第二章新增 DuckDB + Parquet 選型理由（技術優勢 + 生資實測數字）
- [x] `presentation.md`：新增 Slide 4B（DuckDB + Parquet 優勢說明，含壓縮流程圖）
- [x] `README.md`：新增專案 README

---

## ✅ ENGRAM 模組完成（2026-05-18）

### 分析產出永久記憶系統

- [x] `scripts/10_migrate_schema_v9.py` — `analysis_artifacts` 表 + HNSW cosine 索引 + `analysis_index` view 加 `artifact_count`
- [x] `analysis/artifact_registry.py` — ENGRAM-Core 五個公開函數
  - `register_artifact()` — 自動讀取 file_size、MIME、inline_data（≤500 KB），生成 embedding，一行寫入 DB
  - `get_artifacts()` — 依 analysis_id 查詢，支援 artifact_type / subtype 篩選、include_inline 控制
  - `compare_analyses()` — 並排回傳 N 個分析的 artifact，含 tool_version/tool_status
  - `artifact_summary()` — 0-token 概覽（total_runs/total_artifacts/by_subtype/latest_run）
  - `search_artifacts()` — 兩層搜尋：Layer 1 精確 subtype（score=1.0）→ Layer 2 HNSW cosine fallback
- [x] `tests/test_artifact_registry.py` — 23/23 PASSED（5 test classes）
  - 修正 `analysis_id` UUID→VARCHAR 型別不符（search 路徑的 `::VARCHAR` 強制轉型）
- [x] `analysis/bulk_eda.py` — 分析完成後自動呼叫 `register_artifact()`（PCA 圖 + EDA 報告，非致命 try/except）
- [x] `server/web_app.py` — 8 個 ENGRAM API 路由
  - `GET /engram` — ENGRAM Web UI 頁面
  - `GET /api/engram/samples` — 所有有 artifact 的樣本統計
  - `GET /api/engram/summary/{sample_id}` — 0-token 概覽
  - `GET /api/engram/analyses/{sample_id}` — 樣本下的分析清單（含 artifact 數）
  - `GET /api/engram/artifacts/{analysis_id}` — 某分析的 artifact 列表
  - `GET /api/engram/artifact/{artifact_id}/inline` — 取得單一 artifact base64
  - `GET /api/engram/compare?ids=...` — 並排比較多分析
  - `GET /api/engram/search?q=...` — 語意搜尋
- [x] `server/static/engram.html` — Web UI
  - 樣本列表側邊欄 + 分析記錄卡片 + artifact 縮圖格狀佈局
  - 圖片 lightbox（點擊放大，ESC 關閉）
  - Lazy-load inline_data（按需 fetch，結果 cache）
  - Subtype 篩選 chips（pca / volcano / heatmap…）
  - 多選並排比較（含工具版本顯示）
  - 語意搜尋（相似度 %）

---

## ✅ plan_zh.md 第一至四章重構（2026-05-18）

### 期刊風格改寫

- [x] **第一章**：核心主張改為三層遞進（去重→比較→推導）；實現方式改為三層協同（人機介面→去重閘道→記憶核心）
- [x] **第二章**：重構為期刊風格，段落驅動取代 bullet/表格；拆為 2.1 架構設計決策 / 2.2 原創模組 / 2.3 技術元件選型；HELIX/ENGRAM 各有完整 contribution 段落；加入 HELIX × ENGRAM 協同段落（provenance hash → 可信度標記）
- [x] **第三章**：移除重複的 HELIX 閉環與雙軌記憶段落（已在第二章說明）；新增寫入路徑 / 查詢路徑兩段；ASCII 架構圖補入 `results/` 目錄與分類標題；加入 Mermaid 靜態架構圖與查詢路徑圖；效能表加入「資料生命週期」欄
- [x] **第四章**：章首加入 Mermaid ER Diagram，涵蓋 10 張資料表的主鍵、外鍵與關聯線

---

## ✅ Code Review HIGH 問題修復（2026-05-18）

### 3 個 HIGH 問題修復

- [x] **Migration 原子性**：`scripts/17_migrate_schema_v16.py` / `scripts/18_migrate_schema_v17.py` — blob backup 從 `TEMP TABLE` 改為 persistent 表（`_blob_backup_v16` / `_blob_backup_v17`），session 中斷後資料可從 persistent 表恢復，不再依賴 session 存活
- [x] **`_bootstrap_vss()` read_only 安全**：`config/db_utils.py` — 新增 `read_only` 參數，`LOAD vss` 兩種連線都執行，`SET hnsw_enable_experimental_persistence` 只在 writable 連線執行，避免 read_only 模式靜默失敗
- [x] **`artifact_relations` 唯一約束**：migration v16/v17 及 restore 段均加入 `uq_rel_src_dst_type` 索引；`link_artifacts()` ON CONFLICT 改用 `(src_artifact_id, dst_artifact_id, relation_type)` 防止重複邊；測試 fixture 同步加入唯一索引
- [x] **總測試數：213/213 PASSED，3 skipped**（與修復前相同，全數通過）

---

## ✅ Phase 9B + 9C + 9D + SQL-7~10 完成（2026-05-18）

### Phase 9B：ENGRAM Provenance & Lineage

- [x] `scripts/17_migrate_schema_v16.py` — migration v16：`analysis_artifacts` 新增 `input_data_hash` / `code_hash` / `env_hash`（recreate-table 策略）
- [x] `artifact_relations` 表 — 有向邊（src, dst, relation_type），relation_type: `derived_from` | `used_by` | `compared_with`
- [x] `tool_artifact_lineage` view — 三表預先 join（artifacts + history + tools）
- [x] `analysis/artifact_registry.py` — `register_artifact()` 自動計算三個 hash；新增 `link_artifacts()` / `get_lineage()`
  - `_hash_input_data(paths)` — SHA256[:16] of (path, mtime, size)
  - `_hash_function_source(fn)` — AST-normalized SHA256[:16]
  - `_hash_env()` — Python version + package versions + env vars
- [x] 9B 測試：**13 個新測試**（TestProvenanceHashes × 6 + TestLinkArtifacts × 3 + TestGetLineage × 4）

### Phase 9C：HELIX AST-normalized hash

- [x] `analysis/tool_registry.py` — `compute_tool_hash()` 改用 `ast.parse` → `ast.dump` 正規化
  - comment-only 修改不觸發 revision（`ast.dump` 不含 comment 節點）
  - 邏輯變更才更新 hash
  - SyntaxError fallback 保留 text-strip normalization
  - `inspect.getsource` 新增捕捉 `TypeError`（built-in 函數）
- [x] 9C 測試：**3 個新測試**（TestAstNormalizedHash）

### Phase 9D：Matryoshka 雙層 HNSW 索引

- [x] `scripts/18_migrate_schema_v17.py` — migration v17：`analysis_artifacts` 新增 `embedding_256 FLOAT[256]`；建立 `idx_artifacts_hnsw_256`
- [x] `config/settings.py` — 新增 `MATRYOSHKA_DIM=256` / `MATRYOSHKA_ENABLED=false`（env var 控制）
- [x] `analysis/artifact_registry.py` — `register_artifact()` 自動截斷 `embedding[:256]` 寫入 `embedding_256`
- [x] `search_artifacts()` — `MATRYOSHKA_ENABLED=true` 時啟動兩階段搜尋（256 粗篩 top-50 → 1024 精排 top-N）
- [x] 9D 測試：**3 個新測試**（TestMatryoshkaEmbedding）

### SQL-9/SQL-10 補強

- [x] SQL-9：`register_tool()` 寫入 `tool_change_log` 後加 `revision_count` 同步 assertion
- [x] SQL-10：`config/db_utils.py` `get_connection()` 加入 `_bootstrap_vss()` — 每次連線自動 LOAD vss + SET hnsw_enable_experimental_persistence（消除分散在各腳本的重複設定）
- [x] **總測試數：213/213 PASSED，3 skipped**（較 Phase 9A 的 194 增加 19 個測試）

---

## ✅ Phase 9-SQL + Phase 9A 完成（2026-05-18）

### Schema 健康基線（Phase 9-SQL P0/P1）

- [x] `scripts/11_migrate_schema_v10.py` — `schema_migrations` 版本追蹤表 + v1–v9 歷史補登
- [x] `scripts/12_migrate_schema_v11.py` — ENUM 型別建立（`analysis_status` / `artifact_type_enum` / `tool_status_enum`）；DuckDB 1.5.x FK 限制下改用 ENUM 文件策略
- [x] `scripts/13_migrate_schema_v12.py` — `analysis_artifacts.file_path` 改相對路徑（BIO_DB_ROOT-relative）
- [x] `config/settings.py` — 新增 `resolve_artifact_path()` 讓絕對路徑可跨平台還原
- [x] `scripts/14_migrate_schema_v13.py` — composite index（`analysis_history(sample_id,analysis_type)`、`(status,started_at)`；`tools(tool_name,status)`）+ UNIQUE index `uq_artifacts_run_subtype_label`；FK ON DELETE 策略文件化
- [x] `references/rrf_hybrid_search_summary.md` — REF-3 RRF Hybrid Search 摘要（≤300 字）

### ENGRAM 搜尋強化（Phase 9A）

- [x] `scripts/15_migrate_schema_v14.py` — `analysis_artifact_blobs` blob 拆表（inline_data 移出主表）；recreate-table 策略解決 DuckDB FK 限制
- [x] `scripts/16_migrate_schema_v15.py` — `engram_search_metrics` 觀測表（query / returned_n / latency_ms / search_layer）
- [x] `analysis/artifact_registry.py` — 全面更新（9A-1~4）：
  - `register_artifact()` blob 拆表寫入 + `_make_embed_text` 強化（CSV schema、report 首段）+ 相對路徑儲存
  - `get_artifacts()` / `compare_analyses()` JOIN blob 表取 inline_data
  - `search_artifacts()` 改 Hybrid RRF（k=60）— Layer 1 exact boost + Layer 2 HNSW，回傳 `score` + `search_layer`
  - `_record_search_metric()` 寫入 `engram_search_metrics`
- [x] `tests/test_artifact_registry.py` — 更新 2 個測試（blob 表查詢、RRF score 驗證）；**194/194 PASSED**

---

## ✅ HELIX 架構全面改善完成（2026-05-18）

### P0 — 閉環缺口

- [x] `open_stabilization()` 加入重複 ongoing 防護斷言（`ValueError` 若同工具已有未關閉迭代）
- [x] `scheduler/helix_expire_snapshots.py` — 遺忘曲線降採樣排程（180d→0.5x、365d→0.25x）

### P1 — 重要改善

- [x] `tool_health_report()` 增加 `regression_zones`（偵測穩定化後複雜度回潮的工具）
- [x] `prune_deprecated()` 連帶清理 1 年以上 `diagnosis_img`（保留文字診斷）
- [x] `tests/test_tool_registry.py` — 32 tests，涵蓋 register/drift/hot/prune/stabilize/mark_stable/auto_revert/health
- [x] `tests/test_tool_visualizer.py` — 15 tests，涵蓋 loc/halstead/CC/render/downsample
- [x] **總計 47/47 HELIX tests PASSED**

### P2 — 體驗與長期維護

- [x] `mark_stable(tool_name, reason)` + `is_marked_stable()` — 穩定工具白名單
- [x] `auto_revert_stale_stabilizations(con, days=30)` — 30 天自動關閉失效迭代
- [x] 熱區閾值改為 `settings.HELIX_HOT_THRESHOLD`（env var 可覆蓋，預設 3）
- [x] `close_stabilization()` 渲染 `after_img`，與 `diagnosis_img` 並列前後對比
- [x] `tool_stabilization_log` 加 `loc`/`halstead_volume`/`after_img` 欄位（migration v7）
- [x] `tool_health_report` 加 `helix_self_health`（表大小、孤兒迭代、降採樣覆蓋率）
- [x] `compute_loc()` / `compute_halstead_volume()` 加入 `tool_visualizer.py`
- [x] `config/settings.py` 加入 HELIX 四個常數（HOT_THRESHOLD、STALE_ITERATION_DAYS、SNAPSHOT_DECAY_DAYS_1/2）
- [x] `CLAUDE.md` §7 更新（§7.5–§7.9 新增排程、mark_stable、auto_revert、閾值設定說明）

---

## ✅ Phase 10 完成（2026-05-19）

- [x] `server/bio_memory_server.py` — 新增 `create_http_app()`（`StreamableHTTPSessionManager` stateless mode）+ `_run_http()` + `--transport http --port` CLI 參數；stdio 行為完全不變
- [x] `server/web_app.py` — 掛載 `app.mount("/mcp", create_http_app())`，Web UI 啟動時自動暴露 MCP HTTP endpoint
- [x] `start_bioagent.sh` — 修正 `VENV` 路徑（`bioagent` → `hermes-bio-memory`）
- [x] `tests/test_phase10.py` — 15/15 PASSED（TestCreateHttpApp × 3 + TestMCPInitialize × 3 + TestMCPToolsList × 3 + TestMCPInvalidRequest × 2 + TestWebAppMCPMount × 2 + TestStartScript × 2）
- **總測試數：228/228 PASSED，3 skipped**

---

## ⏭️ 下一步（按優先順序）

### 🔥 穩定性審查待辦（2026-05-19 補登，WAL crash 事件後）

**P0 — 資料完整性與穩定性**
- [x] **歷史資料遺失復原**：盤點結果為**無遺失** — 5/15 備份僅含 1 筆 l2_convert（當時 DB 起始狀態），現行 16 筆完整；先前「30+ → 16」推測不成立
- [x] **`backup_db.py` 既往失敗清查**：實作 `MIN_BACKUP_BYTES=100KB` size 驗證 + 失敗自動刪空目錄；`--prune-empty` 一次清掉 6 個歷史 0-byte 備份
- [x] **`com.hermes.backup` 監控**：失敗 `sys.exit(1)` + `logs/backup_status.json`（last_success_at / last_failure_at / last_size_bytes / last_error）；健檢端點可後續接讀此檔（/health 擴充見 P1）
- [ ] **`_deferred_cleanup` 仍開 writable**：read-only pre-check 已加，但若有 zombie 記錄仍會開 write 連線觸發 WAL replay；可改用 `ATTACH ... (READ_ONLY)` 然後另開短連線只做 UPDATE，或直接接受此風險（zombie 罕見）

**P1 — 排程與監控**
- [x] **launchd 排程完整安裝**：6 個 plist 全部 `launchctl load` 成功，現共 8 個 hermes job：
  - `com.hermes.cleanup_l1`（每日 03:30）
  - `com.hermes.rebuild_hnsw`（每週日 03:00）
  - `com.hermes.scan_samples`（每 30 min interval）
  - `com.hermes.helix_expire`（每週日 04:00；Label 已從舊 `com.bioagent.*` 改 `com.hermes.*`）
  - `com.hermes.embedding_server`（KeepAlive，已運行 PID 7750）
  - `com.hermes.multimodal_server`（KeepAlive，Label 已正規化；Gemma 4 26B 模型載入 ~30s）
- [x] **embedding/multimodal server 自動重啟**：兩個 llama-server 皆已納入 launchd KeepAlive，crash 後自動拉起
- [x] **multimodal server 啟動**：port 8080 由 launchd 接管（Gemma 4 26B + mmproj BF16）
- [x] **`/health` 端點擴充**：新增 `embedding_server_ok` / `multimodal_server_ok` / `backup.{last_success_at, last_success_age_hours, last_size_bytes, fresh}` / `disk_free_gb`；`ok` 總判定 = DB OK + embedding OK + 備份 < 36 小時新鮮度

**P2 — DB 防護加固**
- [x] **DuckDB safe_write 全面套用**：審查 `agent.py` 全部寫入點，發現 `bio_run_spatial_eda` / `bio_run_bulk_eda` 兩處 `analysis_history UPDATE tool_id` 繞過 `safe_write`，已改走 `safe_write`（含 CHECKPOINT 刷 WAL）；`bio_execute_code` INSERT 早已合規；其餘 `con.execute` 全為 SELECT
- [x] **WAL pre-flight check**：`config/db_utils.wal_preflight_check()` 於 `web_app._lifespan` 最早期執行 — read-only 試開 DB，失敗時自動 rename `.wal → .wal.corrupt.<ts>`，狀態寫至 `logs/wal_preflight_status.json`，並上報 `/health.wal_preflight`
- [x] **每週 round-trip 還原測試**：`scheduler/weekly_restore_test.py` + `docs/launchd_weekly_restore_test.plist.example` + `com.hermes.weekly_restore_test`（週日 05:00）；IMPORT 最新備份至 `/tmp/bio_memory_verify.duckdb`，驗證 sample/history > 0；首次手動執行 91/16 與主庫一致；狀態寫 `logs/restore_test_status.json`

**P3 — 安全性殘留**
- [ ] M4：API key 未設定時改為啟動時早期失敗（`config/settings.py`）
- [ ] NH4 後續驗證：Google backend 多輪 tool history 修復後尚未端對端測試
- [ ] SQL-6 NOT NULL 補齊（待 DuckDB 升級支援有 FK 表的 SET NOT NULL）
- [ ] SQL-7/SQL-8 UNIQUE 約束與 STRUCT/EAV 重構（migration v18+）

### 🔧 MCP Server 改善待辦（2026-05-19 補登，server/bio_memory_server.py review）

**P0 — 功能性 Bug**

- [x] **HTTP endpoint 500 error**：根因為 FastAPI 不傳遞 lifespan 給 mount 子 app，`session_manager.run()` 未啟動；`create_http_app()` 改回傳 `(handler, lifespan_cm)`，由 `web_app._lifespan` 統一驅動。`docs/MCP_HTTP_GUIDE.md` 已建立（含 Accept header 規範與 curl/httpx 範例）
- [ ] **工具覆蓋不完整**：MCP server 只公開 7 個工具（history/memory/register），但 `agent.py BIO_TOOLS` 有 `bio_run_spatial_eda` / `bio_run_bulk_eda` / `bio_execute_code` / `bio_tool_health` / `bio_check_l2_sufficiency` 等核心分析工具未透過 MCP 暴露；外部客戶端（Claude Code CLI、curl）無法觸發分析。需評估是否補齊或記錄此限制為「Agent-only 工具」
- [x] **`bio_history_search` threshold 不一致**：schema 預設 0.5 → 0.88，實作 fallback 改用 `L1_COSINE_THRESHOLD`，MCP/Agent 雙端對齊

**P1 — 健壯性**

- [x] **`call_tool` 例外吞掉 traceback**：改為 `(ValueError/KeyError/TypeError) → 參數錯誤訊息（info log）`；`Exception → 系統錯誤 + correlation_id（exception log）`；未知工具不再 raise
- [x] **`bio_register_sample` 未走 `cleanup_stale_runs`**：`_startup_cleanup_stale_runs()` 已在 `_run_stdio` 與 `_mcp_lifespan` 兩條啟動路徑呼叫
- [x] **HTTP mode 缺認證**：`MCP_AUTH_TOKEN` env 已實作；`create_http_app` 內檢查 `Authorization: Bearer <token>`，缺/不符回 401；未設定 token 時 auth 關閉維持向後相容
- [x] **無 rate limiting**：`_RATE_LIMITED_TOOLS = {bio_history_search, bio_memory_query, bio_memory_write}` 已 gate；`MCP_RATE_LIMIT_PER_MIN` env 可調（預設 30）
- [x] **`bio_memory_write` sample_id 格式驗證**：模組級 `_SAMPLE_ID_RE = re.compile(r"^[a-z0-9_-]+$")` 與 `bio_register_sample` 對齊；格式不符直接 raise ValueError

**P2 — 可觀測性與測試**

- [x] **HTTP transport 缺乏監控指標**：新增 `mcp_tool_metrics(tool_name, duration_ms, status, recorded_at)` + composite index；`call_tool` 4 個 return path 皆 best-effort 寫入
- [x] **`test_phase10.py` 只測 mount 與 initialize**：補 11 個 e2e/auth/rate-limit/metrics tests（`TestE2EToolCalls` + `TestAuthMiddleware` + `TestRateLimitGate` + `TestMetricsRecording`）
- [x] **`fmt_table` 對長 summary 不截斷**：新增 `_pipe_safe()` 將 `|`/換行 escape + 截斷（header 40 / cell 60）；ExFAT 含空格與 pipe 路徑不再破表
- [x] **`bio_history_timeline` SQL 寫死 `LIMIT 50`**：補 `limit` 參數（預設 50，最大 500，clamp 到 [1, 500]）

**P3 — 介面一致性**

- [ ] **MCP / Agent 工具命名重複**：MCP server 的 `bio_history_*` 與 agent.py 的 `bio_history_*` 是兩套獨立實作（agent.py 不透過 MCP 呼叫，直接呼叫 Python 函數）；長期應統一為「agent.py 透過 MCP HTTP 呼叫 server/bio_memory_server.py」，避免雙份維護
- [ ] **回傳格式不一致**：`bio_history_lookup` 回 Markdown 表格、`bio_history_check` 回 YAML-like 純文字、`bio_history_search` 回編號列表；建議統一為結構化 JSON（MCP 規範允許 TextContent 包 JSON 字串，由客戶端解析）
- [ ] **缺 `bio_artifact_search` MCP 工具**：ENGRAM 模組 5 個函數（register/get/compare/summary/search）皆未透過 MCP 暴露；外部客戶端無法存取分析產出記憶
- [ ] **`.mcp.json` 路徑含空格**：`/Volumes/NO NAME/bio_DB/...` 在某些 MCP 客戶端可能解析錯誤；Linux 遷移時路徑改為 `/mnt/space4/bio_lab_db/`，記得同步更新

### 既有待辦（不變）

1. 端對端測試：Claude API 切換驗證（填入 `ANTHROPIC_API_KEY`）
2. Linux 伺服器遷移（見 plan_zh.md checklist）
3. Docker 沙盒替換 `code_executor.py`（Linux 部署用）
4. Telegram Bot token 申請（Phase 0 正式啟用）

---

## 📐 Phase 10：MCP HTTP Transport 規劃

> 目標：將現有 stdio-only MCP Server 升級為同時支援 HTTP transport，讓 Web UI 與非 Python 客戶端可統一透過 MCP 呼叫工具。

### 背景

| 客戶端 | 現況 | Phase 10 後 |
| ------ | ---- | ----------- |
| Claude Code CLI | ✅ stdio MCP | ✅ 維持 stdio |
| Web UI (FastAPI) | 直接 import agent.py | ✅ 可選用 MCP HTTP |
| Telegram Bot | 直接 import agent.py | ✅ 可選用 MCP HTTP |
| 外部工具 / curl | ❌ 無法呼叫 | ✅ HTTP endpoint |

### 實作項目

- [ ] P10-1 `server/bio_memory_server.py` — 加 `streamable-http` transport（保留 stdio，`--transport` 參數切換）
- [ ] P10-2 `start_bioagent.sh` — 以 HTTP mode 啟動 MCP Server（預設 port 8082）
- [ ] P10-3 `server/web_app.py` — 新增 `/mcp` proxy 路由（可選，供前端直接呼叫 MCP 工具）
- [ ] P10-4 `tests/test_phase10.py` — HTTP transport 端對端測試（工具呼叫 + 錯誤處理）
- [ ] P10-5 `docs/MCP_HTTP_GUIDE.md` — 使用說明（curl 範例 + Python client 範例）

---

## 📐 Phase 9：雙軌記憶優化規劃

> 目標：強化 ENGRAM / HELIX 雙軌記憶系統的搜尋品質、可追溯性與長期維運能力。
> 設計依據見 plan_zh.md 附錄 A8（ENGRAM）與 §7（HELIX）；外部技術參考於 9-REF 階段先行下載。

### Phase 9-REF：文獻下載與閱讀（先行）

| 編號 | 文獻／資源 | 用途對應 | 優先 |
|------|-----------|---------|------|
| REF-1 | **A-MEM** (Zettelkasten-inspired agent memory, 2024) | 9B-2 artifact_relations 邊類型設計 | P1 |
| REF-2 | **OpenLineage spec** (openlineage.io) | 9C-3 lineage event emitter | P2 |
| REF-3 | **Microsoft Hybrid Retrieval (2024) — RRF** | 9A-2 Hybrid search 公式驗證 | P0 |
| REF-4 | **Matryoshka Representation Learning** (Kusupati et al., 2022) | 9D 雙層索引設計 | P2 |
| REF-5 | **MemGPT** (Berkeley, 2023) | 對照 HELIX 遺忘曲線與 recall/archival 分層 | P3 |
| REF-6 | **bge-m3 paper** (BAAI, 2024) — Matryoshka 支援確認 | 9D 可行性驗證 | P2 |
| REF-7 | **PROV-O ontology** (W3C) | 9B-1 provenance hash 命名規範 | P2 |
| REF-8 | **ColBERT v2 / PLAID** | 評估是否值得替換單一 cosine（觀察用） | P3 |

- [x] 下載 REF-3 PDF 至 `references/pdfs/`（9A 啟動前必需）
- [x] 撰寫 `references/rrf_hybrid_search_summary.md`（≤ 300 字摘要 + 對應設計決策）
- [ ] 下載 REF-1, REF-7 PDF（9B 啟動前必需）
- [ ] 撰寫 `references/amem_zettelkasten_summary.md`、`references/prov_o_summary.md`
- [ ] 下載 REF-4, REF-6 PDF（9D 啟動前必需）
- [ ] 撰寫 `references/matryoshka_summary.md`、`references/bge_m3_summary.md`
- [ ] 下載 REF-2 規格與 SDK 文件（9C 啟動前必需）
- [ ] 撰寫 `references/openlineage_summary.md`
- [ ] REF-5, REF-8 列入長期閱讀清單（不阻塞實作，視時間補做）

### Phase 9-SQL：Schema 健康基線（P0 — 與 9A/9B 並行）

> 從 SQL 設計原則（約束、正規化、索引、慣例）對現有 schema 補強。
> Linux 遷移前必須完成 P0 項目。

**P0（Linux 遷移前必做）**

- [x] SQL-1 `analysis_artifacts.file_path` 改存相對路徑（相對 project root）— 配 `config/settings.py` 拼回絕對路徑；migration v12 一次轉換既有資料
- [x] SQL-2 `schema_migrations` 表 — 記錄 (version, applied_at, description)；既有 v2–v9 補登一次
- [x] SQL-3 ENUM 型別建立（DuckDB 1.5.x 有 FK 的表不支援 ALTER TYPE，改為 ENUM 文件策略）：
  - `analysis_status` ENUM('running','completed','failed','stale') — 已建立
  - `artifact_type_enum` ENUM('figure','csv','report','log') — 已建立
  - `tool_status_enum` ENUM('active','deprecated','candidate') — 已建立

**P1（9B 啟動前完成）**

- [x] SQL-4 補 composite 索引：
  - `analysis_history(sample_id, analysis_type)` — 已建立 (migration v13)
  - `analysis_history(status, started_at)` — 已建立 (migration v13)
  - `tools(tool_name, status)` — 已建立 (migration v13)
- [x] SQL-5 FK ON DELETE 策略文件化（DuckDB 1.5.x 不支援 ON DELETE，application 層 enforce）
- [ ] SQL-6 NOT NULL 補齊：待 DuckDB 升級後 ALTER（1.5.x 有 FK 的表不支援 SET NOT NULL）

**P2（隨 9B/9C 一併處理）**

- [ ] SQL-7 UNIQUE 約束：
  - `analysis_artifacts(analysis_id, artifact_subtype, label)` — 防同分析重複登記
  - `sample_registry(project, sample_id)` 評估是否需要（跨 project 同 sample_id 政策）
- [ ] SQL-8 `analysis_history.parameters` JSON → STRUCT 或 EAV — 視 9A-3 embedding 強化需求決定
- [ ] SQL-9 `tools.revision_count` derived data 同步保證 — 在 `tool_change_log` 寫入路徑加 assertion
- [ ] SQL-10 HNSW persistence 設定移入 `config/db_utils.py` connection bootstrap，避免遺漏

**P3（長期，不阻塞）**

- [ ] SQL-11 時間戳欄位命名統一規範（`created_at` + `updated_at` 雙標準）— 大重構，風險高
- [ ] SQL-12 audit log 表（trigger-based）— 視實驗室稽核需求啟動

### 預估工時

| 子項 | 工時 | 對應 Migration |
|------|------|----------------|
| SQL-1 file_path 轉相對 | 2h | v12 |
| SQL-2 schema_migrations | 1h | v12 |
| SQL-3 ENUM | 2h | v13 |
| SQL-4 composite index | 1h | 併入 9A-4 |
| SQL-5 FK 策略 | 1h | 文件 + 9B |
| SQL-6 NOT NULL | 1h | 併入 v13 |
| SQL-7 UNIQUE | 2h | v14 |
| SQL-8 STRUCT/EAV | 4h | v14 |
| SQL-9/10 | 2h | code-only |
| SQL-11 | 4h | v15（緩） |
| SQL-12 | 6h | v16（視需求） |

### Phase 9A：ENGRAM 搜尋強化（P0 — 無 schema breaking）

- [x] 9A-1 `analysis_artifact_blobs` 表拆分（migration v14）— inline_data 移出主表，避免 wide-row 影響 HNSW scan
- [x] 9A-2 Hybrid 搜尋（RRF k=60）— `search_artifacts()` Layer 1 exact boost + Layer 2 HNSW，回傳 `score` + `search_layer`
- [x] 9A-3 `_make_embed_text` 強化 — CSV 抽 header schema、report/log 抽首段
- [x] 9A-4 `engram_search_metrics` 表（migration v15）— 記錄 query / returned_n / latency_ms / search_layer
- [x] 9A 測試：**194/194 PASSED**（全套，較原 23 增加 171 個其他模組測試）

### Phase 9B：Provenance & Lineage（P1 — 小幅 schema 變動）

- [ ] 9B-1 `analysis_artifacts` 增 `input_data_hash` / `code_hash` / `env_hash`（migration v11）
- [ ] 9B-2 `artifact_relations(src, dst, relation_type)` 表 — bulk_eda 自動連結（PCA → DEG → volcano）
- [ ] 9B-3 `tool_artifact_lineage` materialized view — 三表預先 join，HELIX↔ENGRAM 反向追溯
- [ ] 9B-4 `register_artifact()` 自動計算三個 hash
- [ ] 9B 測試：覆蓋 hash 計算 + relation insert + lineage view

### Phase 9C：HELIX 精進（P2 — 選做）

- [ ] 9C-1 AST-normalized `source_hash` — `ast.parse` → `ast.dump` 後再 SHA256，消除 whitespace 噪音
- [ ] 9C-2 SVG snapshot 取代部分 PNG（diff-friendly，文字檔可 git track）
- [ ] 9C-3 OpenLineage event emitter — `register_tool()` / `register_artifact()` 同步輸出標準事件

### Phase 9D：Matryoshka 雙層索引（P2 — 中等風險）

- [ ] 9D-1 啟用 bge-m3 Matryoshka 模式 — 同步產生 1024 與 256 維 embedding
- [ ] 9D-2 新建 256 維 HNSW 粗篩索引 `idx_artifacts_hnsw_256`
- [ ] 9D-3 `search_artifacts()` 改兩階段 — 256 粗篩 top-50 → 1024 精排 top-N
- [ ] 9D-4 Benchmark：HNSW 內存下降比例、recall@5 保留率

### 預估工時與優先

| Sub-phase | 工時 | 風險 | 文獻依賴 |
|-----------|------|------|----------|
| 9-REF (REF-3) | 1h | 低 | — |
| 9A | 7h | 低 | REF-3 |
| 9-REF (REF-1, REF-7) | 2h | 低 | — |
| 9B | 11h | 中 | REF-1, REF-7 |
| 9-REF (REF-4, REF-6) | 2h | 低 | — |
| 9D | 6h | 中 | REF-4, REF-6 |
| 9-REF (REF-2) | 1h | 低 | — |
| 9C | 10h | 中 | REF-2 |

**建議執行順序**：REF-3 → 9A → REF-1/REF-7 → 9B → REF-4/REF-6 → 9D → REF-2 → 9C

---

## ⛔ 已知問題 / 阻礙

| 問題 | 狀態 | 說明 |
|------|------|------|
| 訊息平台 | 已決定 | FastAPI Web UI（取代 Telegram），`server/web_app.py` 已完成 |
| launchd cleanup/rebuild 排程 | 待處理 | plist 已在 docs/，待 `launchctl load` × 2 |
| Linux 伺服器權限 | 待確認 | `/mnt/space4/` 空間與寫入權限 |
| MQ250422-A1-D1 缺失 web_summary | 既有問題 | 以 D1-D2 為主要原型 |
| NDPI 配準 | 待處理 | 影響空間圖組織影像疊加 |
| Telegram Bot token | 待申請 | Phase 0 進入時申請 |

---

## 🏁 里程碑歷史

| 日期 | 里程碑 | 備註 |
|------|--------|------|
| 2026-05-11 | 計畫撰寫完成（plan_zh.md + plan.md） | 從 Windows I:\ 設計 |
| 2026-05-15 | 測試數據建置完成（~45GB 複製完畢） | 平台轉移至 macOS ExFAT |
| 2026-05-15 | 專案憲法建立（CLAUDE.md + PROGRESS.md） | 架構文件完整化 |
| 2026-05-15 | Phase 1 完成 | DuckDB schema + venv + VSS 驗證 + test_init_db 4/4 |
| 2026-05-15 | Phase 2A 完成 | CRC 8µm → 416 MB Parquet（215M nonzero, 103 秒） |
| 2026-05-15 | 資料庫安全完成 | 備份還原 round-trip 驗證通過 |
| 2026-05-15 | 設計補強完成 | embedding=Google、沙盒策略、HNSW 維護、Linux 遷移 checklist |
| 2026-05-15 | Phase 2B 完成 | analysis 三模組 + 14/14 tests；CRC EDA 報告 + 50 字摘要生成成功 |
| 2026-05-15 | Phase 3 基礎設施完成 | L1 cache schema + HNSW + cleanup + rebuild + 15/15 tests |
| 2026-05-15 | Phase 3.5 完成 | 本機 embedding（bge-m3-Q8_0）+ l1_cache.py E2E 驗證 |
| 2026-05-15 | Phase 4 完成 | MCP Server 7 工具 + .mcp.json + 19/19 tests，54/55 全套通過 |
| 2026-05-15 | Phase 5 完成 | code_executor + agent loop + 28/28 tests，82/83 全套通過 |
| 2026-05-15 | Phase 6 完成 | Telegram Bot + 23/23 tests，103/104 全套通過 |
| 2026-05-15 | 安全性與正確性全面審查（5 輪）| 修復 17 項問題，詳見下方安全審查記錄 |
| 2026-05-16 | Phase 8 完成 | 圖片上傳/回傳/下載 + session TTL + lazy client + matplotlib 捕獲 |
| 2026-05-17 | 文件完整化 | plan_zh.md 重構（附錄 A 文獻依據 + 附錄 B 驗收標準 + 章節重編）；CLAUDE.md embedding 維度修正（1536→1024）；presentation.md 重構為標準報告格式（11 張→13 張 Marp 投影片） |
| 2026-05-17 | agent.py 重大修復（3C + 8H） | Cache Hit Protocol 實作、enrichment UUID 型別修正、Code Promotion 寫入修復、startup cleanup、tempfile 洩漏修正、Claude backend 序列化、threshold 0.5→0.88、get_connection 統一 |
| 2026-05-18 | ENGRAM 模組完成 | analysis_artifacts + HNSW 索引、5 個 ENGRAM-Core 函數、23/23 tests、bulk_eda 自動登記、8 個 API 路由、engram.html Web UI |
| 2026-05-18 | Phase 9-SQL + 9A 完成 | schema_migrations (v10)、ENUM 型別 (v11)、file_path 相對化 (v12)、composite index + UNIQUE (v13)、blob 拆表 (v14)、search_metrics (v15)；Hybrid RRF 搜尋；194/194 PASSED |
| 2026-05-19 | Phase 10 完成 | MCP HTTP Transport：`bio_memory_server.py` 加 `streamable-http`（stateless）、`create_http_app()` 掛載至 `web_app.py /mcp`、`start_bioagent.sh` venv 路徑修正、15/15 tests；228/228 全套通過 |
| 2026-05-19 | WAL crash 修復 + 穩定性建置 | DB write-mode FatalException 重建（91/15 還原）、`backup_db.py` placeholder bug 修復、`com.hermes.webserver` 自動重啟、`_deferred_cleanup` read-only pre-check、穩定性 14 項 + MCP 15 項待辦清單建立 |

---

## 🔒 安全性與正確性審查記錄（2026-05-19，Code Review）

### 審查範圍
`server/agent.py`、`server/bio_memory_server.py`、`server/web_app.py`、`config/settings.py`、`server/code_executor.py`

### 發現問題（修復狀態更新：2026-05-19）

| 級別 | # | 問題 | 位置 | 狀態 |
| ---- | - | ---- | ---- | ---- |
| CRITICAL | C1 | `config` + `duckdb` 在沙盒白名單，LLM 生成程式碼可 DELETE/DROP 主資料庫 | `code_executor.py` | ✅ 已修 — 兩者從 `ALLOWED_IMPORTS` 移除 |
| CRITICAL | C2 | `plt.savefig`、`to_csv`、`COPY TO` 繞過 `open()` 封鎖，可寫任意路徑 | `code_executor.py` | ✅ 已修 — 加入 `BLOCKED_PATTERNS` |
| CRITICAL | C3 | CORS `allow_origins=["*"]`，部署前必須鎖定 | `web_app.py` | ✅ 已修 — 改讀 `CORS_ORIGINS` env var，預設 `*`（本機開發可接受），部署時設 env |
| HIGH | H1 | MCP HTTP `_run_http` 綁定 `0.0.0.0` 無認證，區網任何主機可寫入 DB | `bio_memory_server.py` | ✅ 已修 — 預設 `127.0.0.1`，可透過 `MCP_BIND_HOST` env 覆蓋 |
| HIGH | H2 | `is_safe()` 同時驗證 preamble 與 LLM 程式碼，架構混亂 | `agent.py` / `code_executor.py` | ✅ 已修 — `sandbox_exec` 新增 `preamble=` kwarg，只對 `code` 執行安全檢查 |
| HIGH | H3 | `session_id` 無長度/格式驗證，可記憶體耗盡攻擊 | `web_app.py` | ✅ 已修 — 加 regex 驗證 + `_MAX_SESSIONS=200` 上限；超限回 503 |
| HIGH | H5 | `@app.on_event("startup")` 已廢棄，與 MCP lifespan 可能衝突 | `web_app.py` | ✅ 已修 — 改用 `@contextlib.asynccontextmanager` lifespan，cleanup task 隨 app 生命週期 |
| MEDIUM | M3 | `_cleanup_old_sessions` timezone 比較冗餘（`.replace(tzinfo=None)` 雙重去除） | `web_app.py` | ✅ 已修 — `_sessions_dict_lock` 重寫時改用 timezone-aware 比較 |
| MEDIUM | M4 | API key 預設空字串，未設定時在首次呼叫才報錯而非啟動時早期失敗 | `settings.py` | ⏳ 待修（低優先） |

**第二輪審查新發現（2026-05-19）：**

| 級別 | # | 問題 | 位置 | 狀態 |
| ---- | - | ---- | ---- | ---- |
| CRITICAL | NC1 | pandas/numpy/anndata/scanpy 隱性 I/O 完全繞過沙盒（`pd.read_csv('/etc/passwd')`、`np.save()` 等） | `code_executor.py` | ✅ 已修 — 加入 20+ 函式名稱至 `BLOCKED_PATTERNS`；`analysis.*` 限縮至安全子模組 |
| CRITICAL | NC2 | `result_path` 從 DB 讀出後直接 `read_text()`，無路徑遍歷防護 | `web_app.py` | ✅ 已修 — 加 `BIO_DB_ROOT.resolve()` 前綴檢查；`result_images` 端點同步修正 |
| CRITICAL | NC3 | `sample_id` 未驗證直接插入 Parquet glob f-string | `web_app.py` / `spatial_eda.py` | ✅ 已修 — `download_csv` 加格式驗證；`_l2_expr_glob`/`_l2_obs_path`/`_results_dir` 加路徑斷言 |
| CRITICAL | NC4 | `engram_compare` 的 `analysis_ids` 無格式驗證 | `web_app.py` | ✅ 已修 — 迴圈呼叫 `_require_analysis_id()` |
| HIGH | NH1 | session 三個字典在清理迴圈與請求之間無互斥鎖，Python 3.11+ 會 `RuntimeError` | `web_app.py` | ✅ 已修 — 加 `_sessions_dict_lock = threading.Lock()`；清理函數分為 `_unsafe`（持鎖呼叫）與 `_cleanup_old_sessions`（公開） |
| HIGH | NH2 | `glob` 在白名單允許目錄列舉 | `code_executor.py` | ✅ 已修 — 從 `ALLOWED_IMPORTS` 移除；`glob.glob(`/`glob.iglob(` 加入 `BLOCKED_PATTERNS` |
| HIGH | NH3 | `analysis.*` 整包可呼叫 `write_to_l1_cache`/`safe_write` 等寫入函數 | `code_executor.py` | ✅ 已修 — 改為明確列出安全子模組白名單；`write_to_l1_cache(`/`safe_write(`/`register_tool(` 加入 `BLOCKED_PATTERNS` |
| HIGH | NH4 | Google backend 多輪 tool history 丟失（OpenAI-format history 中 tool_call 結構被轉換掉） | `agent.py` | ✅ 已修 — `_google_native` 在 loop 前從 `messages` 預先建立，loop 內始終傳入 `native_history=_google_native` |
| MEDIUM | NM1 | Claude backend 工具結果不截斷（三端不一致） | `agent.py` | ✅ 已修 — Claude tool_result 統一截斷至 800 字 |
| MEDIUM | NM2 | `_exec_bio_check_l2_sufficiency` 舊 venv 路徑 `bioagent` | `agent.py` | ✅ 已修 — 改為 `hermes-bio-memory` |
| MEDIUM | NM5 | `\S+` 截斷含空格路徑（ExFAT `/Volumes/NO NAME/`） | `web_app.py` | ✅ 已修 — 改為 `(.+?)(?:\n|$)`；順帶加 `BIO_DB_ROOT` 路徑限制 |

### 後端接入確認（claude / google / local）

- **Claude**：工具呼叫格式正確，但多輪工具結果不截斷（成本較高，M1 待修）
- **Google**：單輪正確；多輪工具歷史丟失（H4 待修）
- **Local（Gemma）**：正常

---

## 🔒 安全性與正確性審查記錄（2026-05-15，5 輪）

### 已修復問題清單

| 檔案 | 問題 | 修復 |
|------|------|------|
| `server/agent.py` | `AgentResponse` 缺少 `messages` 欄位，跨輪工具歷史遺失 | 新增 `messages: list[dict]` 欄位，`handle_message` 回傳完整歷史 |
| `server/agent.py` | `_exec_bio_run_spatial_eda` 使用不存在的 `result_path` 鍵 | 改為 `result.get('report_path')` |
| `server/agent.py` | `sample_id` 無驗證，可注入任意字串 | 加 `^[a-z0-9_-]+$` regex 驗證 |
| `server/agent.py` | `run_cli()` 歷史保留方式錯誤 | 改為 `result.messages[-12:]` |
| `server/telegram_bot.py` | 歷史更新用 `result.text`（字串），非完整 messages | 改為 `result.messages[-_MAX_HISTORY:]` |
| `server/telegram_bot.py` | 空回覆時仍更新歷史（`""` 污染 Claude API） | 加 `if reply:` guard |
| `server/telegram_bot.py` | `server_health()` 回傳值未用 `.get("ok")` | 修正為 `server_health().get("ok")` |
| `server/code_executor.py` | `BLOCKED_PATTERNS` 缺少 dunder 繞過手法 | 新增 `getattr(`, `__builtins__`, `__class__`, `__subclasses__`, `vars(` |
| `analysis/report_generator.py` | `write_report_to_history()` 型別標注為 `-> str`，實際回傳 tuple | 改為 `-> tuple[str, str]` |
| `analysis/report_generator.py` | `sample_id` 無驗證 | 加 `_validate_sample_id()` |
| `analysis/l1_cache.py` | `_open_l1()` 回傳裸連線，需手動 close | 以 `_setup_vss(con)` + `with` context manager 取代 |
| `analysis/spatial_eda.py` | 所有公開函數無輸入驗證 | 加 `_validate_sample_id()` + `_validate_gene_name()` |
| `analysis/spatial_eda.py` | DuckDB 連線未用 `with`，讀寫混用 | 全面改用 `with` + `read_only=True` |
| `config/db_utils.py` | `db_health_check()` 需傳入 con，無法獨立呼叫 | 改為 `con=None`，自動開啟 read-only 連線 |
| `scheduler/backup_db.py` | SQL 參數未參數化（SQL Injection 風險） | 改為 `"EXPORT DATABASE ?"` 參數化形式 |
| `scheduler/cleanup_l1_cache.py` | 裸連線 + 重複 `con.close()` | 全面改用 `with` context manager |
| `scheduler/rebuild_hnsw.py` | 裸連線 + 重複 `con.close()` | 全面改用 `with` context manager |
| `tests/test_phase5.py` | `test_history_passed_to_api` 斷言 `== 3`，實際為 4（live reference） | 修正為 `== 4` |
| `tests/test_phase6.py` | `SimpleNamespace` mock 缺少 `messages` 欄位 | 補全所有 fake_result 的 `messages=[...]` |

### 架構侷限（已記錄，未完全解決）
- **沙盒繞過**：純文字比對無法防止所有 Python introspection 攻擊（`getattr` 鏈、AST 操作）。生產部署建議改用 Docker 容器隔離。

---

## 💡 關鍵決策記錄

| 決策 | 結論 | 理由 |
|------|------|------|
| Embedding 實作 | llamacpp bge-m3-Q8_0（1024-dim）取代 Google | 使用者已有 llama.cpp，免費離線，無 API 費用 |
| L2 解析度 | 8µm bins | 2µm 全圖 >100 萬 bins，L2 儲存成本過高 |
| L2 儲存格式 | Long-format Parquet（nonzero only） | 99.4% 稀疏，dense 會爆炸 |
| 測試數據選擇 | CRC 官方 Visium HD | 含完整 binned + segmented outputs |
| 資料庫引擎 | DuckDB + VSS（HNSW） | 嵌入式、Parquet 原生、0-token SQL |
| Agent 框架 | 自製 Agent + Claude API | 不採 Hermes（GPU 自架成本不符規模） |
| Embedding 模型 | Google `gemini-embedding-001`（1536-dim） | 多語、含中文、有免費額度 |
| 沙盒策略 | macOS 用 `subprocess`，Linux 部署改 Docker | 分階段提升隔離強度 |
| 備份策略 | 每日 02:00 EXPORT DATABASE → `~/bio_db_backups/`，保留 7 天 | APFS 有日誌、避免 ExFAT 風險 |
| ExFAT 防護 | 關鍵寫入後 CHECKPOINT + 殭屍狀態清理 | 縮小斷電損壞視窗 |
| sample_id 命名 | `{project_short}_{sample_short}` 全小寫底線 | 跨腳本一致性（如 `crc_official_v4`） |
| Python 環境 | uv（`--no-install-project`）+ venv on APFS + symlink | ExFAT 無法直接放 venv |
| 訊息平台 | 未定（Telegram 優先評估） | 待確認實驗室成員習慣 |
