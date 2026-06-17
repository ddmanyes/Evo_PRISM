---
# ── 必填欄位 ────────────────────────────────────────────────────────────────
name: <kebab-case 唯一名稱，如 scrna_basic>
version: 1.0.0
data_type: <對應 sample_registry.data_type，如 scrna>
when_to_use: <一句話：何時用這份說明書，供 bio_get_playbook 列表時顯示>

# ── 選填欄位 ────────────────────────────────────────────────────────────────
agent_tools: [bio_run_xxx, bio_execute_code]      # 此分析用到的 MCP tools（陣列；單一工具可用 agent_tool: bio_run_xxx）
reference_pipeline: https://github.com/...        # 對齊的參考實作（若有）
---

# <分析名稱> 標準分析說明書

<!-- section: overview -->
## 概覽

（一段文字 + pipeline 示意 ASCII 圖）

```text
[ 上游：... ]
  input → step1 → step2
                     ↓
[ 下游（bio_DB 接手）]
  step3 → step4 → output
```

## 分析決策流程

```mermaid
flowchart TD
    START([開始]) --> STEP1[...]
    STEP1 --> END([結束])
```

## Tool 對照表

| Tool | 對應步驟 | 寫入 analysis_type |
| --- | --- | --- |
| `bio_run_xxx` | 1–N：... | `<analysis_type>` |
<!-- /section -->

<!-- section: prerequisites -->
## 前置條件

- 條件 1
- 條件 2（路徑、格式要求）

## 標準分析前必呼叫

```python
bio_history_check(sample_id="<sid>", analysis_type="<type>")
```
<!-- /section -->

<!-- section: steps -->
## 標準步驟

### 步驟 1 — <名稱>

- **目的**：...
- **函數**：`analysis.<module>.<function>`
- **產出圖**：...
- **品質關卡**：條件 → 自動寫入 `quality_flags`（`flag_name:<detail>`）

### 步驟 2 — <名稱>

- **目的**：...
- **Tool**：`bio_run_xxx(sample_id, param1, ...)`
- **底層**：`analysis.<module>` → 第三方套件
- **產出**：檔案名稱規則 + artifact subtype
- **品質關卡**：...
- **回傳格式**：

  ```text
  <分析名稱> 完成。
  analysis_id: <uuid>
  report_path: <絕對路徑>

  <報告正文>
  ```

<!-- （繼續加步驟...） -->
<!-- /section -->

<!-- section: template -->
## 完整一次性分析的範本

```python
# 0) 先確認快取
bio_history_check(sample_id="<sid>", analysis_type="<type>")

# 1) 步驟 1
bio_run_xxx(sample_id="<sid>", ...)

# 2) 步驟 2（依賴步驟 1 artifact）
# 查 artifact 路徑（不要猜時間戳）：
# SELECT file_path FROM analysis_artifacts
# WHERE analysis_id = '<上一步 analysis_id>'
#   AND artifact_subtype = '<subtype>'
bio_run_yyy(sample_id="<sid>", input_path=<從 artifact 查到的路徑>)
```
<!-- /section -->

<!-- section: appendix -->
## 完成後

- 確認每步 `analysis_history` 都已寫入（status=completed）且 `tool_id` 不為 NULL

  ```sql
  SELECT analysis_id, status, tool_id, summary
  FROM   analysis_history
  WHERE  analysis_id = '<回傳的 analysis_id>'
  ```

  `tool_id = NULL` 代表工具版本未回填，HELIX stale analyses 將忽略此筆記錄（見 CLAUDE.md 7.3）

- 確認工具輸出的 summary 是否包含品質告警；如有需向使用者說明
- 摘要回報（繁中）：...
- 明確指出 `result_path` 供使用者查完整報告

## 仍走 bio_execute_code（按需）

| 場景 | 工具 / 模組 |
| --- | --- |
| 場景 1 | `analysis.<module>.<function>()` |
<!-- /section -->

---

<!-- 說明書撰寫規範（開發備忘，不影響 agent 讀取）

## Section 標記規則

Section 標記是**選填的**（`Playbook.as_markdown()` 有 graceful fallback：無標記時回傳完整正文）。
建議在正文超過 ~2000 token 的大型說明書中使用，以支援省 token 的分段載入。

五個標準 section（名稱固定）：

  overview      — pipeline 概覽 + 決策流程 + tool 對照表（目標 ~400 token）
  prerequisites — 前置條件 + 分析前必呼叫（目標 ~200 token）
  steps         — 標準步驟（含品質關卡、回傳格式）（目標 ~1500 token）
  template      — 完整分析範本程式碼（目標 ~600 token）
  appendix      — 完成後確認事項 + bio_execute_code 場景（目標 ~300 token）

格式：
  <!-- section: <name> -->
  ...
  <!-- /section -->

Agent 呼叫方式：

  ```text
  bio_get_playbook(domain="<name>")                  → 完整說明書
  bio_get_playbook(domain="<name>", section="steps") → 只取 steps section（需先加標記）
  ```

注意：mcseg.md / spatial_visium.md 目前尚未加入 section 標記，
      呼叫 section= 參數會回傳「尚未劃分 sections」提示，不會出錯。

-->
