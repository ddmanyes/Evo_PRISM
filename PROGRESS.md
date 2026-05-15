# Hermes Bio-Memory — 進度封存

> 每次完成一個里程碑後更新本文件。
> 詳細設計見 [plan_zh.md](plan_zh.md)，專案規範見 [CLAUDE.md](CLAUDE.md)。

---

## 📍 當前里程碑

**里程碑**：測試建置階段 — 以本機測試數據驗證 L2/L1/L3 架構  
**平台**：macOS `/Volumes/NO NAME/bio_DB/`（ExFAT）  
**最後更新**：2026-05-15

---

## ✅ 已完成

### 計畫與設計
- [x] `plan_zh.md` — 完整七階段系統設計（中文）
- [x] `plan.md` — 英文版設計計畫
- [x] `CLAUDE.md` — 專案憲法（規範、架構、路徑）
- [x] `L3_DATA_INGEST_GUIDE.md` — L3 新增樣本操作指南
- [x] `TEST_DATABASE_INDEX.md` — 測試資料庫索引文件

### 測試數據準備
- [x] CRC Visium HD 官方數據 (`crc_visium_data/official_v4/`, ~39GB)
  - 含 2µm / 8µm / 16µm 三個解析度
  - 含 segmented_outputs（細胞分割結果）
  - 含 spatial/ 座標文件
- [x] MSseg 分析程式碼複製至 `analysis_msseg/`, `backend_msseg/`, `msseg_docs/`
- [x] 分析中間結果複製至 `data_ana/` (1.6GB), `results_ana/` (3.9GB)
- [x] `.gitignore` 設定（排除大型數據文件）

### 腳本基礎
- [x] `scripts/00_init_db.py` — DuckDB schema 初始化（sample_registry + analysis_history）

---

## 🔄 進行中

- [ ] 環境建置：`pyproject.toml` + `uv sync`
- [ ] `config/settings.py` — 集中路徑設定
- [ ] 驗證 `00_init_db.py` 實際可執行

---

## ⏭️ 下一步（按優先順序）

### Phase 1：環境驗證（本週）
1. 執行 `uv run python scripts/00_init_db.py` — 確認 DuckDB + VSS 可用
2. 確認 CRC 官方數據結構（`official_v4/` 的 H5 檔案可被 anndata 讀取）
3. 建立 `tests/test_init_db.py` — 自動驗證 schema 建立成功

### Phase 2A：L2 轉換（下週）
4. 完成 `scripts/02_spatial_to_parquet.py`（CRC 官方數據 → Parquet）
5. 執行轉換，輸出至 `silver/`
6. 用 DuckDB 驗證基因查詢可運作

### Phase 2B：分析層
7. 建立 `analysis/spatial_eda.py`（基礎探索函數）
8. 建立 `analysis/history_query.py`（0-token 歷史查詢）
9. 建立 `analysis/report_generator.py`（報告 + 50 字摘要）

### Phase 3：L1 快取
10. 確認 embedding 方案（OpenAI vs 本地 nomic-embed）
11. 建立 `gold/hermes_cache.duckdb` + HNSW 索引

### Phase 4：MCP Server
12. 實作 `server/bio_memory_server.py`
13. 設定 `.claude/settings.json` 的 mcpServers

---

## ⛔ 已知問題 / 阻礙

| 問題 | 狀態 | 說明 |
|------|------|------|
| ExFAT 不支援 symlink | 待處理 | `.venv` 需建在 SSD，再 symlink 到此 |
| DuckDB VSS 是否可用 | 待驗證 | `INSTALL vss` 需要網路，初次執行確認 |
| Embedding 模型未決定 | 待決定 | OpenAI ($) vs nomic-embed (本地 ~2GB) |
| Linux 伺服器路徑未確認 | 待確認 | `/mnt/space4/` 空間與寫入權限 |
| Telegram Bot token | 待申請 | 確認實驗室使用平台後申請 |

---

## 🏁 里程碑歷史

| 日期 | 里程碑 | 備註 |
|------|--------|------|
| 2026-05-11 | 計畫撰寫完成（plan_zh.md + plan.md） | 從 Windows I:\ 設計 |
| 2026-05-15 | 測試數據建置完成（~45GB 複製完畢） | 平台轉移至 macOS ExFAT |
| 2026-05-15 | 專案憲法建立（CLAUDE.md + PROGRESS.md） | 架構文件完整化 |

---

## 💡 關鍵決策記錄

| 決策 | 結論 | 理由 |
|------|------|------|
| L2 解析度 | 使用 8µm bins（非 2µm） | 2µm 全圖 >100 萬 bins，L2 儲存成本過高 |
| 測試數據選擇 | CRC 官方 Visium HD | 有完整 binned + segmented outputs，最接近真實場景 |
| 資料庫引擎 | DuckDB | 嵌入式、Parquet 原生支援、0-token SQL 查詢 |
| 訊息平台 | 未定（Telegram 優先評估） | 待確認實驗室成員習慣 |
| Python 環境 | uv（待確認 ExFAT 問題） | 與 MSseg 專案一致 |
