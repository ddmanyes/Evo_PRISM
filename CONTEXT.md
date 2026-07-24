# Evo-PRISM

生物資訊分析平台的通用語言（ubiquitous language）。此檔僅收錄本專案特有的領域概念，
不含實作或架構細節——那些歸 `CLAUDE.md` 與 `docs/`。

## Language

**Analysis Run**:
在某個 Sample 上執行一次分析所產生的單位；帶有生命週期（running → completed / failed），
在 `analysis_history` 中恰記一列，並可回收 Artifact 與 Diagnosis。
_Avoid_: analysis, job, task, run（單獨使用時語意過寬）

**Analysis Type**:
分析的種類（如 `bulk_deg`、`bulk_enrichment`）；與 Sample 共同構成識別鍵，
用來分群同類 Run 並指認 Canonical Run。
_Avoid_: analysis kind, tool name（tool name 是 HELIX 的版本身分，另屬他義）

**Canonical Run**:
同一組 (Sample, Analysis Type) 的多次 Run 中，當前被標為權威的那一次；
標定新的 Canonical Run 會把前一個降級為 superseded。
_Avoid_: latest run, primary run

**Sample**:
已登記於 `sample_registry` 的一筆生物數據集（Visium HD / bulk RNA / scRNA / …），
以 `sample_id` 識別。
_Avoid_: dataset, specimen

**Artifact**:
一次 Analysis Run 產出的檔案（figure / csv / report / …），登記後可供語意檢索。
_Avoid_: output file, result file

**Diagnosis**:
附加在一次 Analysis Run 上的成功／失敗分類結果。
_Avoid_: error info, status detail
