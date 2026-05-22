# 🧊 SESSION RESUME — Evo_PRISM
> 封存時間：2026-05-22 | Commit：`4a8ebef`

---

## 當前任務核心

**Pre-Benchmark 架構補強（AA 階段）全數完成。** 下個階段為 Benchmark B–G 執行與論文數據回填。但 Code Review 在已提交的 AA1 程式碼中發現 3 個高風險問題，**必須在 Benchmark 2（HELIX）啟動前修正**，否則跑出來的 HealthScore 數據無效。

---

## 進度百分比

| 階段 | 狀態 | 完成度 |
|------|------|--------|
| AA Pre-Benchmark 架構補強（5項 Critical） | ✅ 全數完成 | 100% |
| AB Pre-Benchmark 補強（10項 High） | ⬜ 尚未開始 | 0% |
| AA2 部分（Web UI 👍/👎 端點） | 🔲 待續 | 50%（DB層完成） |
| Benchmark B-G | ⬜ 未解鎖 | 0% |
| 論文數據回填（I 段） | ⬜ 未開始 | 0% |

**整體進度：~35%**（AA 完成但 AB + Benchmarks 全未動）

---

## 🚨 中斷點 — Code Review 發現的未修 Bug（AA1 提交後遺留）

### 🔴 Bug 1：HELIX 7.1 違規 — `register_tool()` 未呼叫
- **位置**：提交 `4a8ebef` 修改了 `analysis/code_promoter.py::scan_candidates()` 與 `analysis/tool_registry.py::tool_health_report()`，但**未呼叫 `register_tool()`**
- **影響**：`tool_change_log` 空白，`revision_count` 不累積，Benchmark 2 的 HELIX 演化曲線跑出來是假數據
- **修正方式**：
  ```python
  from analysis.tool_registry import register_tool
  with duckdb.connect(str(DUCKDB_PATH)) as con:
      register_tool(con, tool_name="bio_scan_promotion_candidates",
                    fn=scan_candidates, version="1.1.0",
                    module_path="analysis.code_promoter",
                    function_name="scan_candidates",
                    change_reason="改用 HELIX Eq.(1) f_promote 公式取代 reuse_count 啟發式")
      register_tool(con, tool_name="bio_tool_health",
                    fn=tool_health_report, version="...",  # 查 tools table 現有版本
                    module_path="analysis.tool_registry",
                    function_name="tool_health_report",
                    change_reason="新增 tool_health_scores + HealthScore 警示 recommendation")
  ```

### 🔴 Bug 2：`churn_ratio` 查詢無 Migration Guard
- **位置**：`analysis/tool_registry.py::tool_health_report()`，新增的 `churn_rows = con.execute("...WHERE churn_ratio IS NOT NULL...")` 無 `try/except`
- **影響**：舊 schema（`churn_ratio` 欄位不存在）會導致整個 `bio_tool_health` MCP 工具崩潰，連 open_stabilizations 等既有欄位也無法回傳
- **修正**：照 `scan_candidates()` 的 `user_approval` 查詢加 `try/except + fallback {}`

### 🟡 Bug 3：`delta_cc_norm` 無歷史資料時超出 `[0,1]` 定義域
- **位置**：`analysis/tool_registry.py::tool_health_report()`
- **情境**：`tool_stabilization_log` 為空時 `max_cc_historical = 1`；若某工具 `regression=5`，`delta_cc_norm = 5.0`（論文宣稱此值 ∈ [0,1]）
- **影響**：`compute_health_score()` 的 `clip` 會救住最終分數，但論文的 normalization 語義有誤，需在 §2.5 補說明或改用 self-normalization

### 🟡 潛在 Bug 4：`regression_zones["regression"]` key 需驗證
- **位置**：`tool_health_report()` 新增段落 `delta_cc = reg["regression"] if reg else 0`
- **待確認**：`regression_zones` 的 dict key 是否確實為 `"regression"`（未在 diff 中確認建構邏輯）

---

## 下一步行動（優先序）

### 立即（Benchmark 前必做）

1. **修正 Bug 1–2**（上方說明），commit 後標記 `fix(HELIX): post-AA1 code review corrections`
2. **AA2 Web UI 端點**：`server/web_app.py` 加 `POST /analysis/{id}/feedback`（body: `{"approval": 1/-1}`）→ `UPDATE analysis_history SET user_approval=? WHERE analysis_id=?`

### 接著（可並行 Benchmark）

3. **AB3（去硬編碼 L3 路徑）**：`scripts/00_init_db.py:155`、`scripts/01_register_sample.py:38`、`scripts/02_spatial_to_parquet.py:202` 改用 `config.settings.L3_ROOT`
4. **AB7（pyrightconfig 去 macOS 硬編碼）**：`/Users/zhanqiru/.venvs` 改相對路徑

### 然後

5. **Benchmark D**（Cache + RRF，依賴 AA3 ✅ AA4 ✅）
6. **Benchmark E**（HELIX，依賴 AA1 Bug 1/2 修正後 ✅ AA2 ✅）
7. **Benchmark C**（112 樣本，等 TS260410004 WSL 管線跑完）

---

## 環境狀態

| 項目 | 狀態 |
|------|------|
| 工作目錄 | `i:/Evo_PRISM/` |
| Python venv | `C:\Users\User\.venvs\hermes-bio-memory` |
| 執行前綴 | `uv run` |
| DuckDB | 1.5.2（`bio_memory.duckdb` 已跑通 v21 全部 migration） |
| WSL 背景管線 | TS260410004（28 paired-end 樣本）Kallisto 定量中（狀態未知，需確認） |
| Embedding server | port 8081（llama.cpp bge-m3-Q8_0，需手動確認是否在線） |
| 最新 commit | `4a8ebef` — feat(AA): implement HELIX Eq.(1)(2)... |

---

## 下次啟動指令（直接複製給 AI）

```
你好，我是 Evo_PRISM 專案負責人。請先讀取 SESSION_RESUME.md 與 CLAUDE.md 了解專案背景。

當前狀態：AA 架構補強已全數提交（commit 4a8ebef），但 Code Review 後發現 3 個未修 Bug。

**本次任務**：修正 SESSION_RESUME.md「中斷點」章節列出的 Bug 1（HELIX register_tool 未呼叫）與 Bug 2（churn_ratio query 無 migration guard），完成後 commit，然後繼續 AA2 的 Web UI 👍/👎 端點實作（server/web_app.py）。

工作目錄：i:/Evo_PRISM/
執行環境：uv run（uv.lock 存在）
```

---

## 技術債快照

| 代號 | 描述 | 優先度 |
|------|------|--------|
| BUG-CR-1 | register_tool() 未呼叫（AA1 遺留） | 🔴 立即 |
| BUG-CR-2 | churn_ratio 無 migration guard | 🔴 立即 |
| BUG-CR-3 | delta_cc_norm [0,1] 語義問題 | 🟡 Benchmark 前 |
| AB1 | artifact_relations.confidence CHECK constraint | 🟡 Benchmark F 前 |
| AB3 | scripts/ 去 L3 硬編碼路徑 | 🟡 中 |
| AB6 | agent.py 2435 行拆三檔 | 🟢 低 |
| AB7 | pyrightconfig 去 macOS 硬編碼 | 🟢 低 |
