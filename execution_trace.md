# 智慧生資分析平台 — 執行日誌

---

## 2026-05-15 — Phase 1 執行記錄

### 1.0 venv 建置（APFS 繞道）
- **結果**：✅ 成功
- **路徑**：`~/.venvs/bioagent` (Python 3.11.14)
- **Symlink**：`/Volumes/NO NAME/bio_DB/.venv → ~/.venvs/bioagent`
- **套件數**：81 packages installed
- **修正**：pyproject.toml 移除不存在的 `readme = "README.md"`；改用 `--no-install-project`

### 1.1 init_db.py 執行
- **結果**：✅ 成功
- **DuckDB 版本**：1.5.2
- **VSS extension**：載入成功（HNSW 可用）
- **bio_memory.duckdb**：780 KB，位於 `/Volumes/NO NAME/bio_DB/`
- **樣本登記**：4 筆（crc_official_v4, MQ250428-D1-D2, MQ250428-A1-M2, Kallisto_v1）
- **修正**：`init_db()` 新增支援傳入 DuckDB 連線物件（原只接受 Path）

### 1.2 測試套件
- **結果**：✅ 4/4 PASSED
- **修正**：`test_init_db.py` 改用 `importlib.util.spec_from_file_location` 載入 `00_init_db.py`（數字開頭無法直接 import）

### 1.3 L3 數據驗證
- **結果**：✅ 成功
- **檔案**：`square_008um/filtered_feature_bc_matrix_agg.h5ad` (1691 MB)
- **Shape**：(516,880 bins × 18,132 genes)
- **讀取方式**：`anndata.read_h5ad(backed='r')` — 不載入全部記憶體

---

## Commit 記錄

| Hash | 內容 |
|------|------|
| `bc84ef9` | feat: Phase 1 complete — venv, DuckDB schema, tests all passing |
| `3b6043c` | docs(plan): add dual-mode code generation + code promotion design |

---

---

## 2026-05-15 — Phase 2A 執行記錄

### 2A.0 02_spatial_to_parquet.py 建立 + 執行
- **結果**：✅ 成功
- **輸入**：`crc_visium_data/official_v4/.../square_008um/filtered_feature_bc_matrix_agg.h5ad` (1691 MB)
- **輸出**：`silver/crc_official_v4/`
  - `obs_metadata.parquet` (16 MB, 516K barcodes + spatial coords)
  - `var_metadata.parquet` (204 KB, 18K genes)
  - `expression/` 104 parts × ~4 MB = 416 MB (215M nonzero entries)
- **耗時**：~103 秒
- **DuckDB 驗證**：PTPRC 3,351 bins / CD8A 1,864 bins ✅
- **修正**：macOS `._*` resource fork 清理；`json.dumps` 修正 dict→JSON

### 2A 完成摘要
- `sample_registry.l2_ready = TRUE` for crc_official_v4
- `analysis_history` 已寫入 l2_convert 記錄

---

## Commit 記錄

| Hash | 內容 |
|------|------|
| `ce6fab8` | feat: 02_spatial_to_parquet.py Phase 2A |
| `bc84ef9` | feat: Phase 1 complete |
| `3b6043c` | docs: code generation design |

---

---

## 2026-05-15 — Phase 2B 執行記錄

### 2B.1 analysis/spatial_eda.py
- **結果**：✅ 成功
- **函數**：`gene_spatial_map()` / `qc_stats()` / `top_genes()` / `gene_coexpression()`
- **Smoke test**：crc_official_v4 top_genes 10筆 PASSED；qc_stats 516K bins PASSED
- **輸出路徑**：`results/{sample_id}/spatial_eda/`

### 2B.2 analysis/history_query.py
- **結果**：✅ 成功
- **特色**：所有查詢 0-token（純 DuckDB SQL），read_only=True 平行安全
- **測試**：7/7 unit tests PASSED（含空結果、missing sample、search_summaries）

### 2B.3 analysis/report_generator.py
- **結果**：✅ 成功
- **真實數據驗證**：
  - n_bins = 516,880（51.7萬）
  - n_genes = 18,118
  - 摘要（50 字）：`crc_official_v4 EDA：51.7萬bins，18,118基因，中位329基因/bi…`
  - 報告儲存：`results/crc_official_v4/report/eda_report_20260515_*.md`
  - analysis_id 寫入 bio_memory.duckdb ✅
- **修正**：`to_markdown()` 需要 `tabulate` 套件 → 改用手工 Markdown 表格格式

### 2B.4 tests/test_phase2b.py
- **結果**：✅ 14/14 PASSED（2.08 秒）
  - 7 TestHistoryQuery
  - 5 TestReportGenerator
  - 2 TestSpatialEdaSmoke（真實 CRC 數據）

---

## Commit 記錄（更新）

| Hash | 內容 |
|------|------|
| _(待 commit)_ | feat: Phase 2B complete — analysis layer |
| `ce6fab8` | feat: 02_spatial_to_parquet.py Phase 2A |
| `bc84ef9` | feat: Phase 1 complete |
| `3b6043c` | docs: code generation design |

---

---

## 2026-05-15 — Phase 3 執行記錄

### 3.0 launchd 排程啟用
- **結果**：✅ com.bioagent.backup 已 `launchctl load`（每日 02:00）
- **備妥**：com.bioagent.cleanup_l1（每日 03:30）、com.bioagent.rebuild_hnsw（每週日 03:00）plist 已放置於 docs/，待個別 load

### 3.1 scripts/03_init_l1_cache.py
- **結果**：✅ 成功
- **輸出**：`gold/hermes_cache.duckdb`，memory_recent 9 欄，HNSW 索引 idx_memory_hnsw（cosine）
- **關鍵修正**：
  - `hnsw_enable_experimental_persistence = true`（檔案型 DB 建 HNSW 必須）
  - 每次新連線都需 `LOAD vss` 才能操作有 HNSW 索引的表
  - `information_schema.columns` 用 `data_type`（非 `column_type`）
  - `init_l1_cache()` 新增 `cache_path` 參數供測試注入

### 3.2 scheduler/cleanup_l1_cache.py
- **結果**：✅ 成功
- **功能**：DELETE expires_at < now()、--dry-run 模式、stats()

### 3.3 scheduler/rebuild_hnsw.py
- **結果**：✅ 成功
- **功能**：DROP + CREATE HNSW、--force 強制重建、index_exists() 驗證

### 3.4 tests/test_phase3.py
- **結果**：✅ 15/15 PASSED（0.88 秒）
  - 4 TestInitL1Cache
  - 6 TestCleanupL1Cache
  - 5 TestRebuildHnsw
- **全套測試**：35/36（test_crc_8um_exists 為既有路徑問題，與本 Phase 無關）

---

## Commit 記錄（更新）

| Hash | 內容 |
|------|------|
| _(待 commit)_ | feat: Phase 3 L1 cache infra |
| `f761800` | docs/chore: reinforce project constitution |
| `277dd9a` | feat: Phase 2B complete |
| `ce6fab8` | feat: Phase 2A |
| `bc84ef9` | feat: Phase 1 complete |

---

---

## 2026-05-15 — Phase 3.5 執行記錄（本機 Embedding 接入）

### 決策變更
- 原計畫：Google gemini-embedding-001（1536-dim）
- **最終採用**：本機 llama.cpp bge-m3-Q8_0（1024-dim）
  - 理由：使用者已有 `~/llama.cpp/`，免費、無 API 費用、離線可用
  - 影響：`gold/hermes_cache.duckdb` schema 改為 `FLOAT[1024]`

### analysis/embed.py
- **結果**：✅ 成功
- **Provider**：llamacpp / openai / google 三路由，由 `.env` EMBEDDING_PROVIDER 控制
- **預設**：llamacpp（llama-server port 8081，OpenAI-compatible `/v1/embeddings`）
- **驗證**：`bge-m3-Q8_0.gguf`（605MB）下載完成，1024-dim 輸出驗證 ✅

### analysis/l1_cache.py
- **結果**：✅ 成功
- **write_to_l1_cache()**：embed_text → INSERT → CHECKPOINT
- **semantic_search()**：array_cosine_similarity(embedding, ?::FLOAT[1024])
- **E2E 驗證**：寫入 PTPRC 記錄，搜尋 "CD8A T cell" → score=0.63（語意相關）✅
- **關鍵修正**：SQL cast 必須 `::FLOAT[{_DIM}]`，不能用 `::FLOAT[]`

### 文件補充
- `docs/launchd_embedding_server.plist.example`：launchd 開機自動啟動 llama-server
- `docs/launchd_cleanup_l1.plist.example`、`docs/launchd_rebuild_hnsw.plist.example`
- `CLAUDE.md §4`：Embedding Server 啟動說明

---

## 2026-05-15 — Phase 4 執行記錄（MCP Server）

### 4.0 mcp 套件安裝
- **結果**：✅ `mcp` 安裝至 `~/.venvs/bioagent`（Python SDK）

### 4.1 server/bio_memory_server.py
- **結果**：✅ 成功，7 個 MCP 工具：

| 工具 | Token | 說明 |
|------|-------|------|
| `bio_history_lookup` | 0 | 樣本分析歷史表 |
| `bio_history_timeline` | 0 | 最近 N 天時間軸 |
| `bio_history_check` | 0 | 是否已有完成存檔 |
| `bio_history_search` | 少量 | L1 HNSW 語意搜尋（只傳 summary） |
| `bio_memory_query` | 少量 | L1 完整報告查詢 |
| `bio_memory_write` | 0 | 寫入 L1 快取 |
| `bio_register_sample` | 0 | 樣本登記 |

- **Bug 修正**：`history_query.recent_analyses()` 回傳 pandas DataFrame（非 list），需用 `.empty` 和 `.to_dict("records")`

### 4.2 tests/test_phase4.py
- **結果**：✅ 19/19 PASSED（0.97 秒）
- **修正**：`analysis.history_query.DUCKDB_PATH` 需同時 monkeypatch（模組層級綁定）
- **全套測試**：54/55（test_crc_8um_exists 既有問題，不相關）

### 4.3 .mcp.json 設定
- **位置**：`bio_DB/.mcp.json`（gitignore 排除，含本機絕對路徑）
- **Command**：`~/.venvs/bioagent/bin/python server/bio_memory_server.py`
- **PYTHONPATH**：`/Volumes/NO NAME/bio_DB`

---

## Commit 記錄（完整）

| Hash | 內容 |
|------|------|
| _(待 commit)_ | feat: Phase 5 WIP — code_executor + agent loop |
| `e8b8e1c` | feat: Phase 4 complete — BioAgent MCP Server |
| `8ae83d1` | feat: Phase 3 L1 cache + Phase 2B analysis layer |
| `f761800` | docs/chore: reinforce project constitution |
| `277dd9a` | feat: Phase 2B complete |
| `ce6fab8` | feat: Phase 2A |
| `bc84ef9` | feat: Phase 1 complete |

---

---

## 2026-05-19 — Phase 10 執行記錄（MCP HTTP Transport）

### 10.1 server/bio_memory_server.py — HTTP transport 接入

- **結果**：✅ 成功
- **新增**：`create_http_app()` — `StreamableHTTPSessionManager(stateless=True)` + `_MCPApp` ASGI class（lifespan 委託 Starlette，HTTP 請求直接交 `session_manager.handle_request`）
- **新增**：`_run_http(port)` — 綁定 `MCP_BIND_HOST`（預設 `127.0.0.1`）+ `uvicorn.run`
- **新增**：`__main__` argparse — `--transport stdio|http --port 8082`
- **保留**：stdio 行為完全不變，`.mcp.json` 無需修改

### 10.2 server/web_app.py — 掛載 /mcp

- **結果**：✅ 成功
- `app.mount("/mcp", create_http_app())` — Web UI 啟動即暴露 MCP HTTP endpoint
- 非致命掛載：mount 失敗僅 warning，不阻止 web server 啟動

### 10.3 start_bioagent.sh — venv 路徑修正

- **結果**：✅ 成功
- `VENV` 路徑從 `~/.venvs/bioagent` 修正為 `~/.venvs/hermes-bio-memory`

### 10.4 tests/test_phase10.py

- **結果**：✅ 15/15 PASSED
  - TestCreateHttpApp（3）：ASGI callable、`__call__` 簽名、idempotent
  - TestMCPInitialize（3）：HTTP 200、server name、protocol version
  - TestMCPToolsList（3）：HTTP 200、7 工具全出現、tool count == 7
  - TestMCPInvalidRequest（2）：unknown method / malformed JSON 均 < 500
  - TestWebAppMCPMount（2）：`/mcp` 路由存在、app 非 None
  - TestStartScript（2）：venv 路徑含 `hermes-bio-memory`、不含舊路徑
- **關鍵修正**：
  - `httpx.AsyncClient(app=...)` 在 httpx 0.28.1 已移除 → 改用 `starlette.testclient.TestClient`
  - `StreamableHTTPSessionManager.run()` 只能呼叫一次 → fixture 改 function scope，每測試建新實例
  - preamble 含 `savefig(` 被 BLOCKED_PATTERNS 攔截 → `sandbox_exec` 新增 `preamble=` kwarg

### 10.5 全套測試

總測試數：228/228 PASSED，3 skipped

---

## 2026-05-19 — 安全性審查修復記錄（第一輪）

受影響檔案：`server/code_executor.py`、`server/bio_memory_server.py`、`server/web_app.py`、`server/agent.py`

| 項目 | 說明 |
|------|------|
| CRITICAL-C1 | `duckdb`/`config` 從 `ALLOWED_IMPORTS` 移除 |
| CRITICAL-C2 | `savefig(`/`to_csv(`/`to_parquet(`/`to_excel(`/`COPY`/`EXPORT`/`.write(` 加入 `BLOCKED_PATTERNS` |
| CRITICAL-C3 | CORS 改讀 `CORS_ORIGINS` env var，部署時設定 |
| HIGH-H1 | `_run_http` 改綁 `127.0.0.1`，`MCP_BIND_HOST` env 覆蓋 |
| HIGH-H2 | `sandbox_exec` 新增 `preamble=` kwarg，`is_safe()` 只驗 LLM code |
| HIGH-H3 | `session_id` 加 regex 驗證 + `_MAX_SESSIONS=200` 上限，超限回 503 |
| HIGH-H5 | `@app.on_event("startup")` 改為 `asynccontextmanager` lifespan |
| MEDIUM-M3 | `_cleanup_old_sessions` 改用 timezone-aware 比較 |

---

## 2026-05-19 — 安全性審查修復記錄（第二輪）

受影響檔案：`server/code_executor.py`、`server/web_app.py`、`analysis/spatial_eda.py`、`server/agent.py`

| 項目 | 說明 |
|------|------|
| CRITICAL-NC1 | `BLOCKED_PATTERNS` 新增 20+ pandas/numpy/scanpy/anndata 隱性 I/O 函式；`analysis.*` 限縮為安全子模組白名單；`glob` 移出白名單 |
| CRITICAL-NC2 | `result_path` 讀取前加 `BIO_DB_ROOT.resolve()` 前綴檢查（`report_page` + `result_images`） |
| CRITICAL-NC3 | `download_csv` 加 `sample_id` 格式驗證；`_l2_expr_glob`/`_l2_obs_path`/`_results_dir` 加路徑遍歷斷言 |
| CRITICAL-NC4 | `engram_compare` 對每個 `analysis_id` 呼叫 `_require_analysis_id()` |
| HIGH-NH1 | 加 `_sessions_dict_lock = threading.Lock()`；清理函數分為 `_unsafe`（持鎖）與公開版本 |
| HIGH-NH2 | `glob.glob(`/`glob.iglob(` 加入 `BLOCKED_PATTERNS` |
| HIGH-NH3 | `write_to_l1_cache(`/`safe_write(`/`register_tool(` 加入 `BLOCKED_PATTERNS` |
| HIGH-NH4 | Google 多輪：`_google_native` loop 前預先建立，loop 內始終傳 `native_history=_google_native` |
| MEDIUM-NM1 | Claude tool result 截斷至 800 字（與 google/local 一致） |
| MEDIUM-NM2 | `_exec_bio_check_l2_sufficiency` venv 路徑 `bioagent` → `hermes-bio-memory` |
| MEDIUM-NM5 | 正則修正含空格路徑；加 `BIO_DB_ROOT` 路徑限制 |

總測試數：228/228 PASSED，3 skipped（全程不 regress）
