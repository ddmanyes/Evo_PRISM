# 數據與程式碼整合指南

本文件說明如何將新的數據集或分析程式碼併入 bio_DB，以及判斷是否需要整合的決策流程。

---

## 一、整合前判斷清單

在開始整合前，先回答以下問題：

| 問題 | 是 → | 否 → |
|------|------|------|
| 數據是否與現有樣本共用相同批次？ | 只複製數據，不需重跑 | 確認格式相容後再複製 |
| 分析方法是否與現有腳本功能重疊？ | 整合為共用函數，移除重複 | 直接新增為獨立模組 |
| 分析是否只適用於特定生物問題？ | 保留在原專案，不移植 | 抽象為通用模組後移植 |
| 結果是否需要被 Agent 查詢？ | 寫入 `analysis_history` | 存 TSV 即可 |

---

## 二、數據整合步驟

### 2-A Bulk RNA-seq（Kallisto 輸出）

```
raw/BulkRNA/ 或 results_kallisto/
       ↓
1. 確認 abundance.tsv 存在於每個樣本資料夾
2. 執行 scripts/bulk_rna/ 管道（若尚未產生 gene_counts_mapped_symbol.tsv）
3. 複製 L2 數據到 bio_DB/bulk_rna_data/{ProjectName}/results_kallisto/
4. 登記到 sample_registry：
       python scripts/01_register_sample.py --scan-bulk-rna
   （或）每 30 分鐘由 launchd 自動掃描（scheduler/scan_new_samples.py）
```

**已支援樣本命名規則**（`parse_timepoint_cols()` 自動解析）：
```
{condition}_{replicate}_{tissue}
例：ctrl_1_Hair_germ, pw6hr_2_lower_bulge
```

若命名規則不同，需更新 `analysis/bulk_timeseries.py` 中的 `_TIMEPOINT_RE` 正規式。

---

### 2-B Proteomics（Perseus 輸出）

```
原始 .csv（Perseus MaxQuant 輸出）
       ↓
1. 複製到 bio_DB/proteome_data/{ProjectName}/{filename}.csv
2. 登記到 sample_registry（data_type=proteomics, platform=maxquant）：
       python scripts/01_register_sample.py \
           --sample-id {sample_id} \
           --data-type proteomics \
           --platform maxquant \
           --l3-path proteome_data/{ProjectName}/{filename}.csv
3. 確認欄位格式（load_proteome() 預設讀取 "T: Gene name" 欄）
```

**支援的 Perseus 欄位格式**：
- 強度欄：`{timepoint_h}_{replicate}`（如 `0_1`、`24_2`、`96_4`）
- 基因名稱欄：`T: Gene name`（可在 `load_proteome(gene_col=...)` 指定）

若欄位命名不同（如 `LFQ intensity 0h_1`），需在呼叫前先 rename 或自訂 `_PROT_COL_RE`。

---

### 2-C 其他數據類型

| 類型 | 推薦目錄 | platform 值 | 備註 |
|------|---------|------------|------|
| 10x Visium HD | `crc_visium_data/` 或 `visium_data/` | `10x_visium_hd` | L2 轉換用 `scripts/02_spatial_to_parquet.py` |
| scRNA-seq | `scrna_data/` | `cellranger` | 尚未實作 L2 pipeline |
| ATAC-seq | `atac_data/` | `snapatac2` | 尚未實作 |
| 臨床數據 | `clinical_data/` | `other` | 手動登記，不自動掃描 |

---

## 三、分析程式碼整合步驟

### 判斷移植優先度

```
新分析方法
    ├─ 與現有模組功能 >50% 重疊？
    │      ├─ 是 → 提取差異部分，合併到現有模組
    │      └─ 否 → 繼續判斷
    │
    ├─ 高度特化於特定生物問題？（硬編碼基因清單、實驗設計）
    │      ├─ 是 → 保留在原專案，透過 import 呼叫 bio_DB 共用函數
    │      └─ 否 → 繼續判斷
    │
    └─ 可被 Agent 呼叫或多個專案共用？
           ├─ 是 → 移植為 analysis/ 下的獨立模組
           └─ 否 → 放入 scripts/ 作為一次性工具
```

### 移植程序

1. **提取純函數**：去除硬編碼路徑（改用 `BIO_DB_ROOT`）、去除專案特化常數（改為參數）
2. **加上類型標註**：所有函數參數與回傳值加 type hints
3. **替換 print 為 logging**：`import logging; logger = logging.getLogger(__name__)`
4. **建立配置檔**（若有基因清單）：放入 `gene_sets/{project}.yaml`
5. **更新 CLAUDE.md 目錄結構區塊**，保持文件同步

### 移植後檢查

```bash
# 確認 import 正常
~/.venvs/hermes-bio-memory/bin/python -c "from analysis.{module} import {function}"

# 確認沒有硬編碼路徑
grep -n "\/mnt\/\|\/Users\/\|\/Volumes\/" analysis/{module}.py

# 確認使用 logging 而非 print
grep -n "^print\|[^#]print(" analysis/{module}.py
```

---

## 四、現有可重用模組一覽

| 模組 | 主要函數 | 適用場景 |
|------|---------|---------|
| `analysis/bulk_eda.py` | `generate_bulk_report()` | 整批樣本 QC + PCA 報告 |
| `analysis/bulk_timeseries.py` | `timeseries_summary()`, `log2fc()` | 時間序列 FC 計算 |
| `analysis/pathway_scoring.py` | `score_pathways()`, `load_gene_sets()` | ssGSEA / Z-score 路徑評分 |
| `analysis/multiomics_integration.py` | `run_integration()` | RNA-Protein 時序整合 |
| `analysis/spatial_eda.py` | `plot_spatial()`, `top_genes()` | Visium HD 空間分析 |
| `analysis/embed.py` | `embed_text()` | 文字向量化（bge-m3） |
| `analysis/l1_cache.py` | `search_cache()`, `write_cache()` | L1 語意快取 |
| `analysis/history_query.py` | `query_history()` | 分析歷史查詢（0 token） |
| `config/db_utils.py` | `safe_write()`, `db_health_check()` | DuckDB 安全寫入 |

---

## 五、基因集擴充

新增路徑基因集時，在 `gene_sets/` 下新增或編輯 YAML：

```yaml
# gene_sets/{project}.yaml
NewPathway:
  description: 路徑說明（中英文均可）
  genes:
    - Gene1
    - Gene2
```

呼叫時傳入路徑：

```python
from analysis.pathway_scoring import score_pathways
scores = score_pathways(expr, gene_sets_path=Path("gene_sets/my_project.yaml"))
```

**命名建議**：

- `hair_follicle.yaml`：毛囊相關路徑（OxPhos、TCA、FAO）
- `cancer.yaml`：腫瘤相關路徑（未來）
- `immune.yaml`：免疫相關路徑（未來）

---

## 六、跨專案共用原則

| 情境 | 做法 |
|------|------|
| 兩個專案用同一批 RNA-seq | 數據只存一份（`bulk_rna_data/`），各專案 import bio_DB 分析函數 |
| 專案 A 的方法對專案 B 有用 | 抽象為通用模組移入 `analysis/`，兩個專案都 import |
| 方法高度特化（特定 TF、特定組織） | 保留在原專案，透過 `sys.path.insert` 呼叫 bio_DB 工具 |
| 需要歷史追蹤與 Agent 查詢 | 分析完成後呼叫 `safe_write()` 寫入 `analysis_history` |
