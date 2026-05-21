---
name: bulk_rnaseq
version: 3.0.0
data_type: bulk_rnaseq
when_to_use: Bulk RNA-seq 樣本（Kallisto / featureCounts count 矩陣）的標準探索與差異分析。使用者要求「跑 bulk EDA / DEG / 火山圖 / 熱圖 / GO / GSEA」時。
agent_tools: [bio_run_bulk_eda, bio_run_deg, bio_run_enrichment, bio_run_heatmaps]
reference_pipeline: https://github.com/ddmanyes/bulk-rnaseq-pipeline
---

# Bulk RNA-seq 標準分析說明書

對齊參考實作 [ddmanyes/bulk-rnaseq-pipeline](https://github.com/ddmanyes/bulk-rnaseq-pipeline)
（Python + OmicVerse + GSEApy，**DESeq2 統計，不是 edgeR**）。

整條 pipeline 切成「上游：fastq → counts」+「下游：counts → 圖表」兩段：

```
[ 上游（不在 bio_DB 內，需先離線跑完）]
  fastq.gz → FastQC + trim_galore → kallisto quant → kallisto_to_matrix.py
                                                          ↓
                                                    counts.csv + coldata.tsv
                                                          ↓
[ 下游（bio_DB 接手）]                                     ↓
  merge counts → log2CPM → PCA → ComBat 批次校正
              → DESeq2 DEG（按 config 跑多組對照）
              → Volcano（per comparison）
              → Heatmap（significant genes + top 50 variable）
              → ORA（GO / KEGG / Reactome via GSEApy）→ dot plot
              → [optional] K-means time-series + per-cluster 富集
```

**四個原生 MCP tools 涵蓋下游主流程**（HELIX 版本管理 + ENGRAM 自動 artifact 登記）：

| Tool | 對應步驟 | 寫入 analysis_type |
|------|---------|------------------|
| `bio_run_bulk_eda` | 1–4：QC / top genes / 相關 / PCA | `bulk_eda` |
| `bio_run_deg` | 5–6：DEG（DESeq2 via pyDEG）+ 火山圖 | `bulk_deg` |
| `bio_run_heatmaps` | 7：顯著基因 + top variable 熱圖 | `bulk_heatmap` |
| `bio_run_enrichment` | 8：ORA（GO/KEGG/Reactome via gseapy.enrichr） | `bulk_enrichment` |

Time-series K-means（步驟 9）與 ComBat 批次校正仍走 `bio_execute_code` + `analysis.bulk_timeseries`。

## 前置條件

- 樣本已在 `sample_registry`，且 `data_type = bulk_rnaseq`
- 上游 kallisto 已跑完，**counts + coldata** 落在 `bulk_rna_data/<project>/results_kallisto/`：
  - `deseq2_counts.csv`（gene × sample 整數計數矩陣）
  - `deseq2_coldata.tsv`（sample × condition/batch/time 設計表）
- 開分析前先 `bio_history_check(sample_id, "bulk_eda")`，命中就走快取協定，避免重算

## 標準步驟

### 步驟 1 — QC 統計與圖
- **目的**：每樣本 library size、偵測基因數、mapping rate，判斷有無壞樣本
- **函數**：`analysis.bulk_eda.qc_stats` → `analysis.bulk_eda.qc_barplot`
- **產出圖**：library size + 偵測基因數雙 barplot
- **品質關卡**：`mapping_rate_pct < 70%` 或 `n_genes` 明顯偏低 → 標記可疑

### 步驟 2 — 高表達基因檢視
- **目的**：確認 top 基因合理（非 rRNA / 接頭污染主導）
- **函數**：`analysis.bulk_eda.top_genes`
- **產出**：top 20 基因表（mean counts + 出現樣本數）
- **品質關卡**：單一基因佔比異常高 → 可能 ribodepletion 不全

### 步驟 3 — 樣本相關矩陣
- **目的**：確認重複樣本群聚、偵測 outlier / 標籤錯置
- **函數**：`analysis.bulk_eda.sample_correlation` → `analysis.bulk_eda.correlation_heatmap`
- **產出圖**：Pearson（log1p）相關 heatmap
- **品質關卡**：同組重複間 Pearson < 0.9 → 提示批次效應或樣本品質問題

### 步驟 4 — PCA 降維（含 batch correction 對照）
- **目的**：整體結構視覺化、確認分組分離、評估批次效應強度
- **函數**：`analysis.bulk_eda.pca_plot`（高變異 top 2000 基因）
- **產出圖**：PC1–PC2 散點圖（依欄名前綴著色）
- **進階（按需）**：若 `coldata` 含 `batch` 欄、且 PC1 主要由 batch 驅動：
  - 走 `bio_execute_code`，用 `omicverse.bulk.batch_correction` 或 `pycombat` 跑 ComBat
  - 產出「校正前 vs 校正後」並排 PCA 圖（對照參考 pipeline 的 `PCA_Batch_Correction_Comparison.png`）

> 步驟 1–4 由 `bio_run_bulk_eda(sample_id)` 一次完成，報告含四張 inline base64 圖，
> 並各自登記為 artifact（subtype：qc / correlation / pca / eda_report）。

### 步驟 5 — 差異表達分析（DEG，DESeq2）— **原生 tool**
- **目的**：找出每組對照的顯著差異基因
- **Tool**：`bio_run_deg(sample_id, counts_path, coldata_path, comparisons, ...)`
- **底層**：`analysis.bulk_deg.run_deg_analysis` → `omicverse.bulk.pyDEG.deg_analysis(method='DEseq2')`
- **產出**：每組對照一張 `DEG_<a>_vs_<b>_<ts>.csv`（log2FC / qvalue / BaseMean）+ artifact 登記（subtype=`deg_table`）
- **品質關卡**：
  - 顯著基因數（|log2FC|>1, qvalue<0.05）落在 50–5000 之間為合理
  - >10000 → 可能未過濾低表達；<10 → 組間差異太弱或樣本數不足

### 步驟 6 — 火山圖（per comparison）— **原生 tool（與步驟 5 合併）**
- **目的**：視覺化每組對照的 DEG 分布
- **產出**：`bio_run_deg` 同步產出 `Volcano_<a>_vs_<b>_<ts>.png`（紅 up / 藍 down / 灰 ns，含 fc/qvalue 閾值線 + top 10 顯著基因 adjustText 標籤）
- **artifact subtype**：`volcano`
- **品質關卡**：點雲應對稱（無系統性偏移）；極端點需基因名可解釋

### 步驟 7 — 熱圖（顯著基因 + top 變異基因）— **原生 tool**
- **目的**：視覺化 DEG / high-variance 基因在樣本間的表達 pattern
- **Tool**：`bio_run_heatmaps(sample_id, counts_path, deg_tables=[...], top_n=50)`
- **底層**：`analysis.bulk_heatmap` → `seaborn.clustermap` z-score row-wise
- **產出兩張**：
  - `Heatmap_Significant_Genes_<ts>.png` — `deg_tables` union 後的顯著基因（subtype=`heatmap_sig`）
  - `Heatmap_Top<N>_Variable_Genes_<ts>.png` — 跨所有樣本 log1p variance top N（subtype=`heatmap_var`）
- **品質關卡**：同組重複應聚成一支；若 dendrogram 把重複拆散 → 回頭查 batch / 標籤

### 步驟 8 — 富集分析（ORA：GO / KEGG / Reactome）— **原生 tool**
- **目的**：把 DEG list 翻譯成生物功能 / 通路
- **Tool**：`bio_run_enrichment(sample_id, deg_table_path, libraries=[...])`
- **底層**：`analysis.enrichment.run_ora` → `gseapy.enrichr`（線上 Enrichr API）+ `gseapy.dotplot`
- **預設 libraries**：`GO_Biological_Process_2023` / `KEGG_2021_Human` / `Reactome_2022`
- **產出**：每方向（up/down）× 每 library → CSV + dot plot（subtypes：`enrichment_table` / `enrichment_dotplot`）
- **品質關卡**：
  - top pathway 與實驗主題相關（如毛囊樣本應命中 hair follicle / cell cycle / OxPhos）
  - 全部 pathway 在 Adjusted P-value > 0.25 → DEG signal 可能太弱
- **⚠️ 需網路**：Enrichr API；無網時 raise，由 Agent 改走 `analysis.pathway_scoring.score_pathways(method='zscore')` 對 `gene_sets/*.yaml` 的自訂 gene set 評分（離線）

### 步驟 9 — 時序分析（按需，僅有 time 欄位時）
- **目的**：找隨時間共表達的基因模組
- **工具**：
  - K-means clustering（搭配 elbow plot 找最佳 k）→ 對照 pipeline 的 `TimeSeries_Clustering_k*.png`
  - 既有函數：`analysis.bulk_timeseries.mean_by_timepoint` / `log2fc` / `timeseries_summary`
- **產出**：每個 cluster 一條趨勢線 + per-cluster GO 富集
- **品質關卡**：cluster 數合理（k=4–8）；單一 cluster 解釋 > 80% 變異 → 重新評估 k

## 完整一次性分析的範本（建議框架）

當使用者說「跑完整 bulk 分析」時，標準流程（四個 tool 串聯）：

```text
# 1) EDA（步驟 1–4）
bio_run_bulk_eda(sample_id="kallisto_v1")
    → result_path = results/bulk_eda/.../bulk_eda_<sid>_<ts>.md

# 2) DEG + 火山（步驟 5–6）
bio_run_deg(
    sample_id="kallisto_v1",
    counts_path="bulk_rna_data/Kallisto_v1/results_kallisto/deseq2_counts.csv",
    coldata_path="bulk_rna_data/Kallisto_v1/results_kallisto/deseq2_coldata.tsv",
    comparisons=[["pw24hr","ctrl"], ["pw48hr","ctrl"]],
)
    → result_path = results/bulk_deg/.../bulk_deg_<sid>_<ts>.md
    → DEG CSVs：DEG_pw24hr_vs_ctrl_<ts>.csv, DEG_pw48hr_vs_ctrl_<ts>.csv

# 3) 熱圖（步驟 7）— 餵入第 2 步產出的所有 DEG CSV
bio_run_heatmaps(
    sample_id="kallisto_v1",
    counts_path="bulk_rna_data/Kallisto_v1/results_kallisto/deseq2_counts.csv",
    deg_tables=["results/bulk_deg/.../DEG_pw24hr_vs_ctrl_<ts>.csv",
                "results/bulk_deg/.../DEG_pw48hr_vs_ctrl_<ts>.csv"],
    top_n=50,
)

# 4) 富集（步驟 8）— 每張 DEG 各跑一次
bio_run_enrichment(
    sample_id="kallisto_v1",
    deg_table_path="results/bulk_deg/.../DEG_pw24hr_vs_ctrl_<ts>.csv",
)
bio_run_enrichment(
    sample_id="kallisto_v1",
    deg_table_path="results/bulk_deg/.../DEG_pw48hr_vs_ctrl_<ts>.csv",
)
```

## 完成後

- 確認每步 `analysis_history` 都已寫入（status=completed）且 `tool_id` 已回填
- 摘要回報（繁中）：樣本數、可疑樣本（若有）、PCA 分離情況、各對照 DEG 數、top 3 富集通路
- 明確指出 `result_path` 供使用者查完整報告與所有圖檔
- 跨對照需要對照時，呼叫 `compare_analyses(analysis_ids=[...])` 並排兩個 analysis_id 的 artifacts

## 仍走 bio_execute_code（按需）

| 場景 | 工具 |
|------|------|
| ComBat 批次校正 | `omicverse.bulk.batch_correction()`；對照前後 PCA 並排 |
| GSEA prerank（不依賴 padj 閾值） | `gseapy.prerank(ranked_gene_list, gene_sets='gmt_or_name')` |
| Time-series K-means + elbow | `sklearn.cluster.KMeans` + `silhouette_score` + `analysis.bulk_timeseries.mean_by_timepoint` |
| 自訂 gene set 評分（離線） | `analysis.pathway_scoring.score_pathways(counts, 'gene_sets/*.yaml', method='zscore'\|'ssgsea')` |

這些場景重複跑 ≥ 2 次 → 由控制面板 Phase 3 引導畢業成 `analysis/` 函數。
