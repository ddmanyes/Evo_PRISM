# Hermes Bio-Memory — 實作計畫書

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
uv venv ~/.venvs/hermes-bio-memory --python 3.11
ln -sf ~/.venvs/hermes-bio-memory "/Volumes/NO NAME/bio_DB/.venv"
UV_PROJECT_ENVIRONMENT=~/.venvs/hermes-bio-memory uv sync --directory "/Volumes/NO NAME/bio_DB"
```
**驗收**：`uv run python -c "import duckdb; print(duckdb.__version__)"` 輸出版本號  

---

### [ ] 1.1 執行 init_db.py — 驗證 DuckDB + VSS

**目標**：實際建立 bio_memory.duckdb 並驗證 schema  
**指令**：
```bash
cd "/Volumes/NO NAME/bio_DB"
UV_PROJECT_ENVIRONMENT=~/.venvs/hermes-bio-memory uv run python scripts/00_init_db.py
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
UV_PROJECT_ENVIRONMENT=~/.venvs/hermes-bio-memory uv run pytest tests/test_init_db.py -v
```
**驗收**：所有測試 PASSED（至少 4 個測試通過）

---

### [ ] 1.3 驗證 L3 測試數據可讀取

**目標**：確認 CRC 官方數據結構正確，anndata 可讀取 H5 檔  
**指令**：
```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/hermes-bio-memory uv run python -c "
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

## Phase 2A — L2 Parquet 轉換

### [ ] 2.0 建立 scripts/02_spatial_to_parquet.py

**目標**：CRC 8µm binned → silver/crc_official_v4.parquet  
**依賴**：Phase 1.1, 1.3 完成  

---

## 執行日誌 → execution_trace.md
