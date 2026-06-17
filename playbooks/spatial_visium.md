---
name: spatial_visium
version: 1.2.0
data_type: visium_hd
when_to_use: Visium / Visium HD 空間轉錄體樣本的標準探索分析。使用者要求「跑空間 EDA」「畫某基因的空間分布」「看空間 QC」時。
agent_tool: bio_run_spatial_eda
---

# 空間轉錄體（Visium HD）標準分析說明書

定義空間轉錄體的**標準分析順序**與**每步該產出的圖**。
`bio_run_spatial_eda` 已將 QC + top genes + 代表基因空間圖封裝成單次呼叫，
產出完整 Markdown 報告（inline base64）。
單一基因 / 共表達等聚焦查詢走步驟 3–4 的個別函數。

## 資料模型（必讀）

### L2 Silver 是什麼

L2 由 `scripts/02_spatial_to_parquet.py` 從 L3 轉換而來，**只讀 8µm 那層**：

```text
L3/outs/binned_outputs/square_008um/filtered_feature_bc_matrix_agg.h5ad  ← 來源
         ↓ 轉換
L2/silver/{sample_id}/
    obs_metadata.parquet     barcode + array_row_8um + array_col_8um + spatial_x/y
    var_metadata.parquet     gene_name + gene_id
    expression/
        part-0000.parquet    長格式 (barcode, gene_name, count)，只含非零值
        part-0001.parquet    ...（每 5000 barcodes 一個 part）
```

10x Visium HD 原始資料同時有 2µm / 8µm / 16µm 三個解析度，L2 選 8µm 的理由：

- 2µm（>100 萬 bins）在記憶體做 pivot 會爆，且現階段分析不需要細胞級精度
- 16µm 空間解析度太粗，失去局部結構資訊
- 8µm ≈ 單細胞直徑，兼顧解析度與計算效能

### 8µm bin 的座標含義

`array_row_8um` / `array_col_8um` 是**格子 index**（第幾格），不是物理距離。

- `array_row_8um = 42` 表示第 42 行格子，物理距離 = 42 × 8µm = 336µm
- 空間圖的 X/Y 軸顯示的是 index 值，軸標題標注「(8µm)」提示單位換算
- 報告中 `n_bins`、`sparsity`、`valid_density` 全以 8µm bin 為計算單位

### 各分析函數的資料存取方式

所有分析函數透過 **DuckDB** 直接查 Parquet，以 `barcode` 為 JOIN key：

```sql
-- 典型查詢模式（gene_spatial_map / qc_stats / gene_coexpression）
SELECT o.array_row_8um, o.array_col_8um, e.count
FROM   read_parquet('obs_metadata.parquet') AS o
LEFT JOIN read_parquet('expression/*.parquet') AS e USING (barcode)
WHERE  e.gene_name = ?
```

`expression/` 是長格式（每個非零的 barcode × gene 一列），不含座標，座標只在 obs。

### 2µm 精度的取得方式

目前分析工具**不支援 2µm**。若需要次細胞解析度：

1. 必須另開流程從 L3 `square_002um/` 讀取
2. 需要 backed mode（`anndata.read_h5ad(..., backed="r")`）並先裁切 ROI
3. 詳見 `scripts/02_spatial_to_parquet.py` 的轉換邏輯，可仿照擴充

## 前置條件

- 樣本已在 `sample_registry`，`data_type = visium_hd`（或 visium）
- **L2 必須就緒**：執行前先 `bio_check_l2_sufficiency(sample_id)` 確認 `l2_ready = true`
  - 若 false → 回傳轉換命令給使用者，**不得繼續分析**
- 開分析前先 `bio_history_check(sample_id, "eda_report")`，命中走快取協定

## 標準步驟

### 步驟 1 — 空間 QC 統計與圖

- **目的**：每 bin 的 total counts、偵測基因數分布，判斷組織覆蓋與品質
- **函數**：`analysis.spatial_eda.qc_stats`
- **產出**：
  - `qc_stats.parquet`（per-bin 統計，寫入 `analysis_artifacts`）
  - `qc_distributions.png`（n_genes + total_counts 分布直方圖，寫入 `analysis_artifacts`）
- **品質關卡**：大量低 counts bins → 提示組織外背景，考慮過濾門檻

### 步驟 2 — 高表達基因檢視

- **目的**：確認整體表達輪廓合理
- **函數**：`analysis.spatial_eda.top_genes`
- **產出**：top genes DataFrame（`save=True` 時存 CSV 並寫 history）

### 步驟 3 — 代表基因空間分布圖（EDA 自動執行）

- **目的**：前 3 高表達基因在組織切片上的空間表達模式
- **函數**：`analysis.spatial_eda.gene_spatial_map`
- **產出圖**：單基因空間 heatmap（array_row/col grid，正確座標範圍，cmap=RdYlBu_r）
- **自動執行**：`bio_run_spatial_eda` 會對 top-3 基因自動呼叫，嵌入報告第 4 節
- **聚焦查詢**：使用者點名特定基因（如 EPCAM、PTPRC）時個別呼叫
- **品質關卡**：基因不在 L2 矩陣 → 明確回報「基因未偵測」，不得編造分布

### 步驟 4 — 基因共表達（按需）

- **目的**：兩基因空間共定位關係與相關性
- **函數**：`analysis.spatial_eda.gene_coexpression`
- **產出圖**：3-panel 圖
  - Panel 1：gene_a 空間分布（Blues cmap）
  - Panel 2：gene_b 空間分布（Reds cmap）
  - Panel 3：共表達散點（gene_a UMI vs gene_b UMI per bin）
- **空白保護**：兩基因均未偵測時拋出 ValueError，不產生空白圖

> 步驟 1–3 由 `bio_run_spatial_eda(sample_id)` 一次完成
> （內部走 `analysis.report_generator.run_full_eda_report`），
> 報告含 inline base64 圖，節次：
> 1. 資料概覽  2. QC 統計  3. 前 20 高表達基因  4. **代表基因空間分布**  5. 空間覆蓋率  6. 結論摘要
>
> 每張 artifact label 內嵌統計數字（bins 有表達、vmax 等），
> agent 可透過 `bio_artifact_search` 搜尋而不需重新載圖。

### 步驟 5 — 擴充分析（按需，非標準）

- SVGs、neighborhood enrichment、spatial domain detection 等：先 `bio_find_tool` 找既有函數，命中重用，全 miss 才寫碼。
- 詳見 mcseg 改善計畫（Phase 1 待實作）。

## 大型檔案鐵律

- L3 原始 `.h5ad` / `.btf` **禁止**全圖載入或 `cat`
- Visium HD 2µm 全圖（>100 萬 bins）須 backed mode 或先裁切；L2 只存 8µm bins

## 完成後

- 確認 `analysis_history` 寫入（completed）且 `tool_id` 回填（HELIX 自動）
- 確認圖檔已寫入 `analysis_artifacts`（可用 `bio_artifact_search` 驗證）
- 繁中摘要：bin 數、品質概況、代表基因觀察
- 指出 `result_path`
