---
name: bulk_rnaseq
version: 1.0.0
data_type: bulk_rnaseq
when_to_use: Bulk RNA-seq 樣本（Kallisto / Salmon count 矩陣）的標準探索分析。使用者要求「跑 bulk EDA」「看 QC / PCA / 樣本相關」時。
agent_tool: bio_run_bulk_eda
---

# Bulk RNA-seq 標準分析說明書

這份說明書定義 bulk RNA-seq 的**標準分析順序**與**每步該產出的圖**。
`bio_run_bulk_eda` 已將步驟 1–4 封裝成單次呼叫，會一次產出整個系列圖（inline base64）。
非標準需求（差異表達、火山圖、富集分析）走步驟 5 的擴充流程。

## 前置條件

- 樣本已在 `sample_registry`，且 `data_type = bulk_rnaseq`
- gene count 矩陣已由 pipeline 產生（`bulk_rna_data/.../results_kallisto/gene_counts*.tsv`）
- 開分析前先 `bio_history_check(sample_id, "bulk_eda")`，命中就走快取協定，避免重算

## 標準步驟

### 步驟 1 — QC 統計與圖
- **目的**：每樣本 library size、偵測基因數、mapping rate，判斷有無壞樣本
- **函數**：`analysis.bulk_eda.qc_stats` → `analysis.bulk_eda.qc_barplot`
- **產出圖**：library size + 偵測基因數雙 barplot
- **品質關卡**：某樣本 `mapping_rate_pct < 70%` 或 `n_genes` 明顯偏低 → 標記為可疑，於摘要點名

### 步驟 2 — 高表達基因檢視
- **目的**：確認 top 基因合理（非 rRNA / 接頭污染主導）
- **函數**：`analysis.bulk_eda.top_genes`
- **產出**：top 20 基因表（mean counts + 出現樣本數）
- **品質關卡**：單一基因佔比異常高 → 提示可能 ribodepletion 不全

### 步驟 3 — 樣本相關矩陣
- **目的**：確認重複樣本群聚、偵測 outlier / 標籤錯置
- **函數**：`analysis.bulk_eda.sample_correlation` → `analysis.bulk_eda.correlation_heatmap`
- **產出圖**：Pearson（log1p）相關 heatmap
- **品質關卡**：同組重複間相關 < 0.9 → 提示批次效應或樣本品質問題

### 步驟 4 — PCA 降維
- **目的**：整體結構視覺化，確認分組分離度
- **函數**：`analysis.bulk_eda.pca_plot`（高變異 top 2000 基因）
- **產出圖**：PC1–PC2 散點圖（依欄名前綴著色）
- **品質關卡**：PC1 主要由單一 outlier 樣本驅動 → 考慮剔除後重跑

> 步驟 1–4 由 `bio_run_bulk_eda(sample_id)` 一次完成，報告含四張 inline base64 圖，
> 並各自登記為 artifact（subtype：qc / correlation / pca / eda_report）。

### 步驟 5 — 擴充分析（按需，非標準）
- **時機**：使用者要差異表達、時序、路徑評分、火山圖等
- **流程**：先 `bio_find_tool(<需求描述>)` 找既有可重用函數
  - 命中 → 在 `bio_execute_code` 動態碼中 `import` 重用（如 `analysis.bulk_timeseries`、`analysis.pathway_scoring`）
  - 全 miss → 才從零寫碼（`plt.show()` 的圖會自動擷取嵌入）

## 完成後

- 確認 `analysis_history` 已寫入（status=completed）且 `tool_id` 已回填
- 用繁中摘要回報：樣本數、平均基因數、可疑樣本（若有）、PCA 分離情況
- 明確指出 `result_path` 供使用者查完整報告
