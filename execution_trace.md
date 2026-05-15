# Hermes Bio-Memory — 執行日誌

---

## 2026-05-15 — Phase 1 執行記錄

### 1.0 venv 建置（APFS 繞道）
- **結果**：✅ 成功
- **路徑**：`~/.venvs/hermes-bio-memory` (Python 3.11.14)
- **Symlink**：`/Volumes/NO NAME/bio_DB/.venv → ~/.venvs/hermes-bio-memory`
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

## 下一步：Phase 2B — 分析層

- [ ] `analysis/spatial_eda.py` — 基礎探索函數（基因空間圖、QC 統計）
- [ ] `analysis/history_query.py` — 0-token 歷史查詢
- [ ] `analysis/report_generator.py` — 報告 + 50 字摘要（⭐ 語意搜尋品質上限）
