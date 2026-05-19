# DB114 Module 11 / 12 對 bio_DB 的適用性評估

**日期**：2026-05-19
**對照講義**：
- Module 11 — Analytical Modeling & Systems（Data Lakehouse、Medallion、Dimensional Modeling）
- Module 12 — Vector Database Systems（HNSW、PQ、Hybrid Search、Vector Aggregation、Metadata Filtering）

**目的**：把講義中的架構原則對齊 bio_DB 現有實作（DuckDB + Parquet、HNSW、Matryoshka 雙層嵌入、RRF 混合搜尋、HELIX、ENGRAM），判斷哪些建議能落地、ROI 多高、什麼時候做。

---

## 1. 總結（按 ROI 排序）

| # | 建議 | ROI | 建置成本 | 優先級 |
|---|------|-----|---------|-------|
| 1 | DuckDB FTS (BM25) 加入 RRF 第三條 ranker | ⭐⭐⭐⭐⭐ | 低（1–2 天） | **P0** |
| 2 | Metadata Pre-filter 是否下推到 HNSW（驗證） | ⭐⭐⭐⭐ | 極低（半天） | **P0** |
| 3 | HELIX / ENGRAM Star Schema View | ⭐⭐⭐ | 低（1 天） | **P1-C（已排入 Sprint）** |
| 4 | Analysis-level summary embedding（非 centroid） | ⭐⭐⭐ | 中（2–3 天） | P1 |
| 5 | PQ 量化冷資料 | ⭐⭐ | 高 | **P3 暫緩** |
| 6 | Bronze schema-on-read lineage 強化 | ⭐⭐ | 中 | P2 |

---

## 2. 逐項評估

### 建議 1：DuckDB FTS (BM25) + RRF — P0，強烈推薦

**為什麼適合 bio_DB**
- ENGRAM 的 Layer 1 目前是 exact/LIKE 級別 boost，對生資專有名詞（`EPCAM`、`HALLMARK_OXPHOS`、`KRT14+`）這類形態學精確但語意稀疏的詞，BM25 比 dense embedding 強。
- bge-m3 在「中文混雜短英文 gene symbol」場景容易誤配，BM25 提供互補訊號。
- DuckDB FTS 是內建擴充，索引存在同一個 `.duckdb` 檔，與現有 L1/L2 架構零摩擦。
- RRF 已實作，新增第三條 ranker `fts_score` 是局部改動。

**風險 / 待驗證**
- DuckDB FTS 是 snapshot：新增 artifact 後需 `PRAGMA drop_fts_index` + 重建，建議併入 `rebuild_hnsw.py` 週日 03:00 batch。
- 預設 stemmer 是英文 porter，中文 query 需先做 jieba 斷詞再丟入。

**驗收標準**
- 對 20–30 條生資專有名詞 / 中英混雜 query 跑 A/B，`recall@10` 較純 dense 提升 ≥ 10%。

---

### 建議 2：Metadata Pre-filter 下推驗證 — P0，必做

**為什麼必驗證**
- 這不是新功能，是檢查既有實作是否最佳。若 `search_artifacts()` 是「先 HNSW top-k 再 Python filter `sample_id`」，HNSW 召回可能根本不含目標樣本，等於性能地雷。
- DuckDB VSS 對 WHERE 子句的 pre-filter 支援度視版本不同；確認後決定是否需要兩階段查詢或加大 `LIMIT` 緩衝。

**動作**
- `EXPLAIN ANALYZE` 一次 `search_artifacts()` 的實際 query plan。
- 確認 `WHERE sample_id = ?` / `artifact_subtype = ?` 是 pre-filter 還是 post-filter。
- 若是 post-filter：改寫為兩階段查詢，或在 query 包裝層自動放大 `LIMIT`。

**驗收標準**
- query plan 文件化於 `docs/`，pre-filter 路徑明確。

---

### 建議 3：HELIX / ENGRAM Star Schema View — P1

**為什麼適合**
- 搭配 `tools` / `sample_registry` / `analysis_history` 作 dim/fact，能直接出「過去 30 天 × 樣本類型 × 平均耗時」這類 drill-down。
- 純 SQL View，不動底層儲存，回滾零成本。
- 整合 `tool_health_report()` 散落的 ad-hoc SQL 成可重用 view。

**2026-05-19 實作前發現（重要修正）**
原先評估假設 Phase 10 已建立 `mcp_tool_metrics` fact table，schema 檢查發現**該表不存在**。當前可用的 fact 來源僅 `analysis_history` / `tool_change_log` / `tool_stabilization_log`。
- `analysis_history` 粒度過粗（只記錄完整分析跑，不含查詢類 MCP 工具），無法替代 `mcp_tool_metrics` 做工具效能 view。
- **決策**：P1-C 縮減為 2 個 view（移除 `v_tool_perf_30d`），新增 **P1-D**（在 PROGRESS.md）追蹤 `mcp_tool_metrics` 表建立與 MCP server 寫入鉤子。
- `v_tool_perf_30d` 等 P1-D 完成、累積 1 週實際呼叫資料後再回頭補。

**建議產出（修正後）**
- `docs/STAR_SCHEMA.md`：列出 fact / dim、每個 view 的 SQL、未來 `v_tool_perf_30d` 上線的條件。
- 2 個 view：
  - `v_analysis_throughput_by_sample_type` — `analysis_history` × `sample_registry`，weekly 分析吞吐量
  - `v_tool_stability_signal` — `tools` × `tool_change_log` × `tool_stabilization_log`，整合熱區/迭代/穩定性訊號

---

### 建議 4：Analysis-level summary embedding（修正版） — P1

**對原建議的修正**
原建議是「對 analysis 下所有 artifact 向量取平均（centroid）」，**不建議直接這樣做**：
- bge-m3 即使 normalized，mean pooling 也會稀釋語意（PCA 圖 + 文字報告 + spatial heatmap 的 centroid 不像任何一個）。

**修正方案**
- 利用 `report_generator.py` 已產生的 ≤50 字摘要，直接 embed 一份 1024-dim 向量。
- 存到 `analysis_history.summary_embedding`，建一條 HNSW 索引。
- `bio_history_search` 直接走 summary embedding，語意更乾淨、儲存成本相同。

---

### 建議 5：PQ 量化冷資料 — P3 暫緩

- HNSW 1024-dim、100 萬筆 ≈ 6 GB，本機 Mac 完全扛得住。
- Matryoshka 256-dim coarse 已是準 PQ 優化，再上 PQ 是過度工程。
- **觸發條件**：`analysis_artifacts > 5M` 或 embedding server 載入時間 > 30s 時再評估。

---

### 建議 6：Bronze schema-on-read lineage — P2

- L3 `crc_visium_data/` 已等同 Bronze，唯讀規則已寫入 CLAUDE.md。
- 可加強：`sample_registry` 補 `ingested_at` / `source_hash` / `raw_schema_snapshot` 三欄，作為未來 lineage 重跑驗證的依據。
- 屬 nice-to-have，非當前瓶頸。

---

## 3. 下一個 Sprint 建議納入的工作項

依序執行：

1. **[P0-A] Metadata pre-filter 驗證**（半天）
2. **[P0-B] DuckDB FTS + RRF PoC**（2 天）
3. **[P1-C] HELIX / ENGRAM Star Schema View**（1 天）

P1 剩餘的 summary embedding、P2 的 Bronze lineage、P3 的 PQ 量化維持原優先級。
