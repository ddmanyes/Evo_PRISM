---
name: mcseg
version: 1.0.0
data_type: imaging
when_to_use: H&E 影像 MCseg 細胞分割的品質評估與視覺化。使用者要求「看分割品質」「NUC vs MCseg 比較」「細胞數/大小分布」時。
agent_tool: bio_run_mcseg_qc
---

# MCseg 分割品質說明書

定義 MCseg 細胞分割的**品質評估流程**與**每步該產出的圖**。
本說明書處理的是**既有分割遮罩的視覺化與量化**，不即時重跑分割。

## 重要前提：本機只做視覺化，不重跑分割

- 即時分割（`run_mcseg_v2`）依賴 MSseg 原專案的 cellpose + GPU，**本平台不執行**
- 本說明書假設分割遮罩**已產生**並放在 QC 目錄

## 資料契約

- 分割遮罩 = 整數標籤 `.npy`：0 = 背景，1..N = 各細胞，`mask.max()` = 細胞數
- 每個 ROI 一對：`{roi}_nuc.npy`（NUC 基準）+ `{roi}_mcseg.npy`（MCseg 完整流程）
- 預設目錄 `results/mcseg_qc/`（可由 `qc_dir` 參數覆蓋）
- H&E ROI 影像（RGB uint8）為選填；缺則以標籤著色代替底圖

## 標準步驟

### 步驟 1 — 探索 ROI 對
- **目的**：確認有哪些 ROI 同時有 NUC 與 MCseg 遮罩
- **函數**：`analysis.mcseg_quality.discover_roi_pairs`
- **品質關卡**：找不到成對 `.npy` → 明確回報「無分割輸出可評估」，不得編造結果

### 步驟 2 — 細胞量化
- **目的**：每個 ROI 的細胞數、平均/中位面積、前景占比
- **函數**：`analysis.mcseg_quality.cell_metrics` / `cell_size_distribution`
- **品質關卡**：MCseg 細胞數遠少於 NUC → 提示過度合併；面積中位數異常大 → 提示分割不足

### 步驟 3 — NUC vs MCseg 對比圖
- **目的**：並排檢視兩法分割邊界差異
- **函數**：`analysis.mcseg_quality.comparison_plot`（紅色邊界疊在 H&E 或標籤底圖）
- **產出圖**：每 ROI 一張並排對比

### 步驟 4 — 細胞面積分布
- **目的**：跨 ROI 比較細胞大小分布是否合理
- **函數**：`analysis.mcseg_quality.size_distribution_plot`
- **產出圖**：面積直方圖（多 ROI 疊圖）

> 步驟 1–4 由 `bio_run_mcseg_qc(sample_id, qc_dir=...)` 一次完成
> （內部 `analysis.mcseg_quality.generate_mcseg_qc_report`），報告含 inline base64 系列圖，
> 並登記 artifact（subtype：mcseg_compare / mcseg_sizedist / mcseg_report）。

## 完成後

- 確認 `analysis_history` 寫入（completed，analysis_type=mcseg_qc）且 `tool_id` 回填
- 繁中摘要：ROI 數、各法細胞數對比、可疑分割（若有）
- 指出 `result_path`
