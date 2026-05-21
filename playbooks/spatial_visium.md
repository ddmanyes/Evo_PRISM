---
name: spatial_visium
version: 1.0.0
data_type: visium_hd
when_to_use: Visium / Visium HD 空間轉錄體樣本的標準探索分析。使用者要求「跑空間 EDA」「畫某基因的空間分布」「看空間 QC」時。
agent_tool: bio_run_spatial_eda
---

# 空間轉錄體（Visium HD）標準分析說明書

定義空間轉錄體的**標準分析順序**與**每步該產出的圖**。
`bio_run_spatial_eda` 已將 QC + 代表基因空間圖封裝成單次呼叫，產出報告（inline base64）。
單一基因 / 共表達等聚焦查詢走步驟 3–4 的個別函數。

## 前置條件

- 樣本已在 `sample_registry`，`data_type = visium_hd`（或 visium）
- **L2 必須就緒**：執行前先 `bio_check_l2_sufficiency(sample_id)` 確認 `l2_ready = true`
  - 若 false → 回傳轉換命令給使用者，**不得繼續分析**
- 開分析前先 `bio_history_check(sample_id, "spatial_eda")`，命中走快取協定

## 標準步驟

### 步驟 1 — 空間 QC 統計與圖
- **目的**：每 bin 的 total counts、偵測基因數分布，判斷組織覆蓋與品質
- **函數**：`analysis.spatial_eda.qc_stats`
- **產出圖**：QC 分布雙圖（counts / n_genes）
- **品質關卡**：大量低 counts bins → 提示組織外背景，考慮過濾門檻

### 步驟 2 — 高表達基因檢視
- **目的**：確認整體表達輪廓合理
- **函數**：`analysis.spatial_eda.top_genes`
- **產出**：top 基因表

### 步驟 3 — 基因空間分布圖（聚焦查詢）
- **目的**：特定基因在組織切片上的空間表達模式
- **函數**：`analysis.spatial_eda.gene_spatial_map`
- **產出圖**：單基因空間 heatmap（座標 + 表達量著色）
- **用法**：使用者點名基因（如 EPCAM、PTPRC）時呼叫；可連續對多基因產生系列圖
- **品質關卡**：基因不在 L2 矩陣 → 明確回報「基因未偵測」，不得編造分布

### 步驟 4 — 基因共表達（按需）
- **目的**：兩基因空間共定位關係
- **函數**：`analysis.spatial_eda.gene_coexpression`
- **產出圖**：兩基因共表達散點 / 空間疊圖

> 步驟 1–2 + 代表基因圖由 `bio_run_spatial_eda(sample_id)` 一次完成
> （內部走 `analysis.report_generator.run_full_eda_report`），報告含 inline base64 圖。
> 步驟 3–4 為使用者指定基因時的個別呼叫。

### 步驟 5 — 擴充分析（按需，非標準）
- 區域比較、cluster、空間鄰域分析等：先 `bio_find_tool` 找既有函數，命中重用，全 miss 才寫碼。

## 大型檔案鐵律

- L3 原始 `.h5ad` / `.btf` **禁止**全圖載入或 `cat`
- Visium HD 2µm 全圖（>100 萬 bins）須 backed mode 或先裁切；L2 只存 8µm bins

## 完成後

- 確認 `analysis_history` 寫入（completed）且 `tool_id` 回填
- 繁中摘要：bin 數、品質概況、代表基因觀察
- 指出 `result_path`
