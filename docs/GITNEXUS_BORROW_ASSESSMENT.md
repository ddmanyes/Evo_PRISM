# GitNexus 借鏡項評估

> 對象：[abhigyanpatwari/GitNexus](https://github.com/abhigyanpatwari/GitNexus)
> 評估日期：2026-05-21
> 結論先講：**3 個候選設計只有 1 個現在值得做（impact 分析）**；另 2 個記錄為「條件成熟再做」。

## 為何兩專案可互相借鏡

GitNexus 與 bio_DB 架構血緣高度重疊：

| 維度 | GitNexus | bio_DB |
|------|----------|--------|
| 對外介面 | MCP server（16 tools） | MCP server（20/21 tools） |
| 混合搜尋 | BM25 + 向量 + **RRF K=60** | BM25 + HNSW + **RRF K=60** |
| 增量更新 | SHA1 content hash | HELIX AST-normalized hash |
| 知識結構 | 代碼知識圖（44 節點 / 21 邊） | ENGRAM artifact + lineage + HELIX 工具帳本 |
| 核心哲學 | **預計算關係智能，查詢時單次回傳** | Fast-Path 跳過 LLM、L1/L2/L3 分層 |

兩者都在做「把昂貴的關係計算搬到寫入時，讓查詢便宜」。差別：GitNexus 圖是**代碼**，bio_DB 圖是**分析產物 + 工具版本**。

---

## 候選 1：邊上的 confidence tier + reason（confidence-on-edges）

### GitNexus 怎麼做
知識圖每條邊帶 `(confidence ∈ 0.5–1.0, reason)`，例如 `CALLS` 邊標 `import-resolved`（高信心）vs `global`（啟發式）。Agent 因此能區分「確定的依賴」與「猜測的依賴」，排序與風險評估都據此加權。

### 對應 bio_DB 的位置
`artifact_relations(src, dst, relation_type)` —— 目前只有關係類型，無信心度。
provenance hash（`input_data_hash` / `code_hash` / `env_hash`）已具備計算信心的材料。

### 效益 / 成本 / 現實
| 項目 | 評估 |
|------|------|
| 成本 | **低** — ALTER ADD COLUMN + 改 `link_artifacts` / `get_lineage` + 測試 |
| 效益 | **目前近零** |
| **數據現實** | `artifact_relations` **目前 0 筆** — 整個 lineage 邊功能（9B）尚未被任何流程實際填充 |

### 決策：**暫不實作（document only）**
理由：對「0 筆資料」的表加信心欄是潤飾未使用的功能。**等 lineage 真正被寫入後再補**——而且最自然的方式是讓本次的 `impact` 分析在推導關係時順手寫 confidence（見候選 3），而非獨立加欄。

---

## 候選 2：預計算物化視圖（pre-computed materialized views）

### GitNexus 怎麼做
聚類（Leiden）、影響評分、信心分級全在**索引時**算完並落地，查詢時零動態計算 → 單次呼叫回傳完整 context，連小模型都能可靠運作。

### 對應 bio_DB 的位置
已有 on-read views：`v_analysis_throughput_by_sample_type`、`v_tool_stability_signal`、`analysis_index`、`promotion_candidates`。Fast-Path（本週完成）也是同一哲學的另一切面（簡單查詢預先攔截、不進 LLM）。

### 效益 / 成本 / 現實
| 項目 | 評估 |
|------|------|
| 成本 | **中** — DuckDB **無原生 MATERIALIZED VIEW**；要自建「表 + 刷新排程（launchd）+ staleness 標記」 |
| 效益 | **目前低** |
| **數據現實** | 全庫 324 analyses / 22 artifacts / 9 tools；現有 view 在這個量級**毫秒回傳**，無預計算需求 |

### 決策：**暫不實作（document only）**
理由：典型的 premature optimization。現有 view on-read 已即時；Fast-Path 已覆蓋互動熱路徑。**觸發條件**：當 `analysis_artifacts > 數萬筆` 或某個聚合 query 實測 > 1s 時，再把該 view 物化成表 + 加 launchd 刷新（範本可參考 `scheduler/rebuild_hnsw.py`）。

---

## 候選 3：影響分析 / 爆炸範圍（impact / blast-radius）✅ **實作**

### GitNexus 怎麼做
`impact` tool 對目標符號走六階段：定位 → 上游 caller → 下游 callee → 跨流程 → 群組邊界 → 風險彙總，回傳「受影響符號排序清單 + 各邊信心」。讓 AI 改函數前先看炸到誰。

### 對應 bio_DB 的位置
HELIX 已有「stale analyses」概念（工具更新後舊分析過時），但**只在 `tool_health_report` 被動呈現，沒有前瞻、可查詢的「改這個工具會影響哪些分析/產物」入口**。

bio_DB 的影響圖天然存在：
```
tools(tool_id, tool_name, version)
   ↓ analysis_history.tool_id（精確）  /  analysis_type↔tool_name（啟發式）
analysis_history(analysis_id, sample_id)
   ↓ analysis_artifacts.analysis_id
analysis_artifacts(artifact_id)
```

### 效益 / 成本 / 現實
| 項目 | 評估 |
|------|------|
| 成本 | **中** — 純 SQL 走圖，無需 migration（用既有 schema） |
| 效益 | **高** — HELIX §7 版本治理的關鍵缺口：deprecate/改版工具前先看炸到哪些 sample 的哪些分析 |
| **數據現實** | tool_id 僅 4/324 已回填（299 是 ad-hoc dynamic_code 本就無工具）→ **這正是要用 confidence tier 的場景** |

### 設計：把候選 1 的 confidence 精神吸收進來
影響邊的信心分級（解決 tool_id 稀疏問題，degrade gracefully）：

| 來源 | confidence | reason |
|------|-----------|--------|
| `analysis_history.tool_id == 目標 tool_id` | **1.0** | `tool_id-exact` |
| `analysis_type` 對應到 `tool_name`（如 `bulk_eda`→`bio_run_bulk_eda`）但 tool_id 為 NULL | **0.6** | `analysis_type-heuristic` |
| 同一 analysis 的其他 artifacts | **0.9** | `same-analysis` |
| `artifact_relations` 既有邊 | 該邊未來的 confidence（現 0 筆） | `explicit-lineage` |

如此 impact 工具**今天就能在現有資料上運作**，且隨 tool_id 覆蓋率提升而更精準——同時順帶把「tool_id 未回填」的治理缺口直接暴露給使用者。

### 實作落地
- `analysis/impact.py`：`tool_impact()` / `artifact_impact()` / `sample_impact()` + `_RiskTier`
- MCP tool `bio_impact`（agent + bio_memory_server 雙端註冊）
- `register_tool()` 入 HELIX 帳本
- `tests/test_impact.py`

---

## 總結

| 候選 | 成本 | 效益 | 數據現實 | 決策 |
|------|------|------|---------|------|
| 1. confidence-on-edges | 低 | 目前近零 | relations 0 筆 | 📋 文件記錄，併入候選 3 的邊推導 |
| 2. 預計算物化視圖 | 中 | 目前低 | 量級太小、view 已即時 | 📋 文件記錄，量級門檻觸發再做 |
| 3. impact / blast-radius | 中 | **高** | 現有 schema 可跑，confidence 解稀疏 | ✅ **本次實作** |

**核心心法（兩專案共通）**：把昂貴的關係計算搬到寫入時、查詢便宜化；對不確定的關係用 confidence tier 誠實標記，而非假裝精確。

## 後續觸發條件（何時回頭做候選 1 / 2）

- **候選 1**：當 `bio_impact` 或其他流程開始實際寫 `artifact_relations`（> 數百筆）→ 把推導出的 confidence 落到邊上。
- **候選 2**：當某聚合 query 實測 > 1s 或 `analysis_artifacts > 5 萬筆`→ 將該 view 物化為表 + launchd 刷新。
- **共通**：tool_id 回填覆蓋率應提升（目前工具產出分析僅 ~17% 有 tool_id）——這是 impact / stale 兩個治理功能的共同前置；建議所有 `bio_run_*` 經 MCP `_exec_*` 呼叫時確實走 `_backfill_tool_id`。
