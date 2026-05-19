# 智慧生資分析平台 — 實作計畫書

> 由 `/sp-executing-plans` 生成於 2026-05-15。  
> 狀態：`[ ] 待辦` / `[>] 進行中` / `[x] 完成` / `[!] 阻礙`

---

## 環境前提（已確認）

| 項目 | 狀態 | 備註 |
|------|------|------|
| uv 安裝 | ✅ | v0.10.0 |
| pyproject.toml | ✅ | 完整依賴定義 |
| SSD 掛載 | ❌ 未掛載 | 改用 `~/.venvs/` (APFS，支援 symlink) |
| .venv | ❌ 不存在 | Phase 1.0 建立 |

---

## Phase 1 — 環境建置與 Schema 驗證

### [>] 1.0 建立 Python 虛擬環境（ExFAT 繞道方案）

**目標**：在 APFS 建立 venv，symlink 至 bio_DB/  
**指令**：
```bash
mkdir -p ~/.venvs
uv venv ~/.venvs/bioagent --python 3.11
ln -sf ~/.venvs/bioagent "/Volumes/NO NAME/bio_DB/.venv"
UV_PROJECT_ENVIRONMENT=~/.venvs/bioagent uv sync --directory "/Volumes/NO NAME/bio_DB"
```
**驗收**：`uv run python -c "import duckdb; print(duckdb.__version__)"` 輸出版本號  

---

### [ ] 1.1 執行 init_db.py — 驗證 DuckDB + VSS

**目標**：實際建立 bio_memory.duckdb 並驗證 schema  
**指令**：
```bash
cd "/Volumes/NO NAME/bio_DB"
UV_PROJECT_ENVIRONMENT=~/.venvs/bioagent uv run python scripts/00_init_db.py
```
**驗收**：
- 輸出 `VSS extension loaded` 或 `WARNING: VSS extension failed`（後者可接受，繼續）
- 輸出 `sample_registry — OK`、`analysis_history — OK`、`analysis_index — OK`
- `bio_memory.duckdb` 檔案存在且 > 0 bytes

---

### [ ] 1.2 執行測試套件

**目標**：自動驗證 schema 結構正確  
**指令**：
```bash
cd "/Volumes/NO NAME/bio_DB"
UV_PROJECT_ENVIRONMENT=~/.venvs/bioagent uv run pytest tests/test_init_db.py -v
```
**驗收**：所有測試 PASSED（至少 4 個測試通過）

---

### [ ] 1.3 驗證 L3 測試數據可讀取

**目標**：確認 CRC 官方數據結構正確，anndata 可讀取 H5 檔  
**指令**：
```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/bioagent uv run python -c "
import anndata as ad
import pathlib
p = pathlib.Path('/Volumes/NO NAME/bio_DB/crc_visium_data/official_v4')
h5 = list(p.rglob('*.h5'))[:1]
print('Found:', h5[0] if h5 else 'NO H5 FILES')
if h5:
    adata = ad.read_h5ad(str(h5[0]), backed='r')
    print('Shape:', adata.shape)
    adata.file.close()
"
```
**驗收**：輸出 `Shape: (rows, cols)`，無 Exception

---

## Phase 2A — L2 Parquet 轉換（完成）

### [x] 2.0 建立 scripts/02_spatial_to_parquet.py（✅ 完成）

**結果**：silver/crc_official_v4/（104 parts, 416 MB, 215M nonzero, 103 秒）

---

## Phase 2B — 分析層

### [x] 2B.1 analysis/spatial_eda.py（✅ 完成）

**目標**：基因空間圖、QC 統計、top_genes、共表達散點圖  
**函數**：`gene_spatial_map()` / `qc_stats()` / `top_genes()` / `gene_coexpression()`  
**驗收**：smoke test on crc_official_v4 PASSED  

---

### [x] 2B.2 analysis/history_query.py（✅ 完成）

**目標**：0-token DuckDB 查詢，不呼叫 LLM  
**函數**：`recent_analyses()` / `sample_summary()` / `find_by_type()` / `get_analysis()` / `analysis_index()` / `search_summaries()`  
**驗收**：7/7 unit tests PASSED  

---

### [x] 2B.3 analysis/report_generator.py（✅ 完成）

**目標**：生成 Markdown EDA 報告 + ≤50 字中文摘要（語意搜尋語料核心）  
**函數**：`generate_eda_report()` / `generate_summary()` / `write_report_to_history()` / `run_full_eda_report()`  
**驗收**：7/7 unit tests PASSED；真實數據 crc_official_v4 生成摘要 50 字 ✅  

---

### [x] 2B.4 tests/test_phase2b.py（✅ 完成）

**驗收**：14/14 PASSED（7 history_query + 7 report_generator + 2 smoke tests）

---

## Phase 3 — L1 語意快取（基礎設施完成，embedding 待接入）

### [x] 3.0 啟用 launchd 排程（✅ 完成）
- com.bioagent.backup：每日 02:00（已 load）
- com.bioagent.cleanup_l1：每日 03:30（plist 備妥，待 load）
- com.bioagent.rebuild_hnsw：每週日 03:00（plist 備妥，待 load）

### [x] 3.1 scripts/03_init_l1_cache.py（✅ 完成）
- gold/hermes_cache.duckdb 建立，memory_recent schema + HNSW 索引（cosine）
- 修正：需要 hnsw_enable_experimental_persistence=true 才能持久化索引
- 修正：每次新連線都需要 LOAD vss 才能操作有 HNSW 索引的表

### [x] 3.2 scheduler/cleanup_l1_cache.py（✅ 完成）
- 每日刪除 expires_at < now() 的記錄，支援 --dry-run

### [x] 3.3 scheduler/rebuild_hnsw.py（✅ 完成）
- DROP + CREATE HNSW 索引，支援 --force

### [x] 3.4 tests/test_phase3.py（✅ 完成）
- 15/15 PASSED

### [x] 3.5 本機 Embedding 接入（✅ 完成）
- 決策改為 llamacpp + bge-m3-Q8_0（1024-dim），免費離線
- `analysis/embed.py`：llamacpp / openai / google 三 provider
- `analysis/l1_cache.py`：write_to_l1_cache() + semantic_search()
- `docs/launchd_embedding_server.plist.example`：開機自動啟動 llama-server
- E2E 驗證：寫入 PTPRC 記錄，搜尋 "CD8A T cell" → score=0.63 ✅

---

## Phase 4 — MCP Server（完成）

### [x] 4.0 mcp 套件安裝（✅ 完成）
- `uv add mcp --no-install-project`

### [x] 4.1 server/bio_memory_server.py（✅ 完成）
**7 個 MCP 工具**：bio_history_lookup / bio_history_timeline / bio_history_check /
bio_history_search / bio_memory_query / bio_memory_write / bio_register_sample

### [x] 4.2 tests/test_phase4.py（✅ 完成）
- 19/19 PASSED（0.97 秒）

### [x] 4.3 .mcp.json 設定（✅ 完成）
- `bio_DB/.mcp.json`（.gitignore 排除）
- Claude Code 重新啟動後可呼叫 bio-memory MCP 工具

---

## Phase 10 — MCP HTTP Transport（完成）

### [x] 10.1 server/bio_memory_server.py（✅ 完成）

- `create_http_app()` — `StreamableHTTPSessionManager(stateless=True)` + `_MCPApp` ASGI class
- `_run_http(port)` — 綁定 `MCP_BIND_HOST`（預設 `127.0.0.1`）
- `--transport stdio|http --port` CLI 參數；stdio 行為完全不變

### [x] 10.2 server/web_app.py — 掛載 /mcp（✅ 完成）

- `app.mount("/mcp", create_http_app())` — Web UI 啟動即暴露 MCP HTTP endpoint
- `asynccontextmanager` lifespan 取代廢棄的 `@app.on_event("startup")`

### [x] 10.3 start_bioagent.sh — venv 路徑修正（✅ 完成）

- `VENV` 從 `~/.venvs/bioagent` 修正為 `~/.venvs/hermes-bio-memory`

### [x] 10.4 tests/test_phase10.py — 15/15 PASSED（✅ 完成）

---

## 安全性審查修復（第一輪 + 第二輪，2026-05-19，完成）

### [x] 第一輪：8 項修復（✅ 完成）

| 項目 | 檔案 |
|------|------|
| CRITICAL-C1 `duckdb`/`config` 移出白名單 | `code_executor.py` |
| CRITICAL-C2 隱性寫入函式加入黑名單 | `code_executor.py` |
| CRITICAL-C3 CORS 改讀 `CORS_ORIGINS` env | `web_app.py` |
| HIGH-H1 MCP HTTP 改綁 `127.0.0.1` | `bio_memory_server.py` |
| HIGH-H2 `preamble=` kwarg 隔離安全檢查 | `code_executor.py` / `agent.py` |
| HIGH-H3 session_id 驗證 + MAX_SESSIONS=200 | `web_app.py` |
| HIGH-H5 lifespan context manager | `web_app.py` |
| MEDIUM-M3 timezone-aware session 清理 | `web_app.py` |

### [x] 第二輪：11 項修復（✅ 完成）

| 項目 | 檔案 |
|------|------|
| CRITICAL-NC1 20+ pandas/numpy/scanpy I/O 封鎖；analysis 限縮子模組；glob 移除 | `code_executor.py` |
| CRITICAL-NC2 result_path 路徑遍歷防護 | `web_app.py` |
| CRITICAL-NC3 sample_id 格式驗證；路徑遍歷斷言 | `web_app.py` / `spatial_eda.py` |
| CRITICAL-NC4 engram_compare analysis_ids 驗證 | `web_app.py` |
| HIGH-NH1 session 字典加 threading.Lock | `web_app.py` |
| HIGH-NH2 glob.glob/iglob 加入黑名單 | `code_executor.py` |
| HIGH-NH3 L1/DB 寫入函式加入黑名單 | `code_executor.py` |
| HIGH-NH4 Google 多輪 tool history 修復 | `agent.py` |
| MEDIUM-NM1 Claude tool result 截斷至 800 字 | `agent.py` |
| MEDIUM-NM2 venv 路徑 bioagent → hermes-bio-memory | `agent.py` |
| MEDIUM-NM5 含空格路徑正則修正 + 路徑限制 | `web_app.py` |

總測試數：228/228 PASSED，3 skipped

---

## 執行日誌 → execution_trace.md
