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

## 下一步：Phase 2A — L2 Parquet 轉換

- [ ] 建立 `scripts/02_spatial_to_parquet.py`
- [ ] CRC 8µm h5ad → `silver/crc_official_v4/` Parquet
- [ ] DuckDB 驗證基因查詢可運作
