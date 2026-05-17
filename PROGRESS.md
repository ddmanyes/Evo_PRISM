# 智慧生資分析平台 — 進度封存

> 每次完成一個里程碑後更新本文件。
> 詳細設計見 [plan_zh.md](plan_zh.md)，專案規範見 [CLAUDE.md](CLAUDE.md)。

---

## 📍 當前里程碑

**里程碑**：agent.py Cache Hit Protocol + Code Promotion 修復（3 CRITICAL + 8 HIGH 問題解決）
**平台**：macOS `/Volumes/NO NAME/bio_DB/`（ExFAT）
**最後更新**：2026-05-17
**commit**：(latest)

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
- [x] venv 建於 APFS（`~/.venvs/hermes-bio-memory`）+ symlink 至 `.venv`
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

- [x] launchd 每日備份排程已啟用（com.hermes.backup）
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
- [x] `start_hermes.sh` — 一鍵啟動腳本
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

## ⏭️ 下一步（按優先順序）

1. 端對端測試：Claude API 切換驗證（填入 `ANTHROPIC_API_KEY`）
2. Linux 伺服器遷移（見 plan_zh.md checklist）
3. Docker 沙盒替換 `code_executor.py`（Linux 部署用）
4. Telegram Bot token 申請（Phase 0 正式啟用）

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
