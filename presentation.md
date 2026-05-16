# Hermes Bio-Memory
## 實驗室生資智慧分析系統

---

## slide 1 — 系統定位

**Hermes Bio-Memory** 是一套以 AI Agent 為核心的實驗室智慧分析平台。

> 讓實驗室成員用**自然語言**查詢空間轉錄體與 Bulk RNA 分析結果，
> 無需任何程式能力，無需重複運算。

**測試數據規模**

- CRC Visium HD 官方數據（~39 GB，L3 Bronze 唯讀）
- Bulk RNA-seq Kallisto（84 樣本）
- sHG Proteomics（Perseus log2，5 個時間點）

---

## slide 2 — 問題：四個實驗室痛點

1. **重複運算浪費**
   - 不同成員對同一樣本提相似問題，各自重跑相同 Pipeline
   - SpaceRanger 單次耗時 ~4 小時

2. **數據孤島**
   - 分析結果散落各人電腦，無統一查詢機制

3. **無分析記錄**
   - 無從得知某樣本是否已分析過、由誰完成、結果在哪

4. **使用門檻高**
   - 不熟悉命令列的成員無法自助取得結果

---

## slide 3 — 解法：三層架構 + Agent 決策樹

### 三層數據倉儲

```
L3 Bronze  ── 不可變原始數據（FASTQ、SpaceRanger outs/）
     │  scripts/ 一次性轉換
     ▼
L2 Silver  ── DuckDB + Parquet（sample_registry、analysis_history）
     │  分析完成後自動寫入
     ▼
L1 Gold    ── HNSW 語意快取（memory_recent，TTL 7 天）
```

### Agent 五段決策防線

```
提問
 ├─ Step 1  SQL 精確比對（0 token，< 1 秒）        ← 已做過？直接回傳
 ├─ Step 2  HNSW 語意搜尋（cosine >= 0.88）        ← 問過類似的？快取回傳
 ├─ Step 3A 標準分析工具（L2 Parquet 已就緒）
 ├─ Step 3B Code Promotion 重用（曾生成過？）
 └─ Step 3C 全新程式碼生成（沙盒執行 + 失敗重試）
```

---

## slide 4 — 核心設計來自哪些文獻

### 三層架構

- **來源**：Medallion Architecture（Databricks）；LakeHarbor ICDE 2024
- **截取**：原始數據不可變；Silver 集中計算一次而非每次查詢時重算
- **調整**：Gold 層改用 HNSW 向量索引，適應自然語言查詢場景

### HNSW 語意搜尋

- **來源**：DuckDB VSS；Malkov & Yashunin 2018
- **截取**：ANN 搜尋兼顧速度 O(log N) 與精度；cosine 比 L2 更適合語意比較
- **調整**：TTL 7 天 + 每週完整重建索引（HNSW 不支援增量更新）

### Agent-First + Token 省策

- **來源**：Agent-First Data Systems 2025；MemGPT 分層記憶模型
- **截取**：資料庫先回答結構化問題，LLM 只處理剩下無法 SQL 化的部分
- **調整**：三段防線（SQL → 語意 → 完整報告）取代 MemGPT 分頁換入換出，適合批次分析場景

### 兩階段寫入狀態機

- **來源**：WAL / crash recovery 通例；saga pattern
- **截取**：長任務崩潰也能留下記錄
- **調整**：加入 `stale` 狀態（>24h running 自動標記）+ ExFAT `safe_write()` CHECKPOINT

### Code Promotion 自動升格

- **來源**：靈感來自 progressive rollout 與 memoization
- **截取**：重用 ≥ 3 次 = 隱性社群驗證
- **調整**：`promotion_candidates` VIEW 自動偵測重用次數，觸發升格流程，無需人工追蹤

### 多模態視覺分析

- **來源**：Gemma 4 Vision（Google DeepMind 2025）；llama.cpp OpenAI-compatible API
- **截取**：本機 Vision LLM 可在不上傳敏感實驗圖至雲端的前提下做視覺分析
- **調整**：`plt.show()` hook 自動捕獲 matplotlib 圖並回傳聊天框

---

## slide 5 — Demo：Web UI 功能展示

### 聊天介面（index.html）

- 自然語言提問 → SSE 串流回覆
- 圖片上傳（附件按鈕 / Ctrl+V 貼上）→ Gemma 4 Vision 視覺分析
- 分析結果圖（matplotlib QC 圖）直接顯示於聊天框，支援下載
- 推理後端即時切換：本機 Gemma 4 / Claude API

### 歷史頁面（history.html）

- 所有分析記錄一覽（樣本、類型、狀態、時間）
- 點擊「預覽」展開結果縮圖列

### 報告頁面（/results/{id}）

- 含 base64 嵌入 QC 圖的完整分析報告 HTML

---

## slide 6 — 系統數字

- 測試數據總量：~39 GB（Visium HD）+ 84 Bulk RNA 樣本
- L2 Parquet 大小：416 MB（CRC Visium HD 8µm bins，215M nonzero）
- Agent 工具數量：10 個 BIO_TOOLS
- 測試通過率：105 / 106 PASSED（6 個測試檔，openai SDK mock）
- 推理引擎：Gemma 4 26B Vision IQ2_M（本機，port 8080）
- Embedding 模型：bge-m3 Q8（1024-dim，多語含中文，本機，port 8081）
- L1 快取 TTL：7 天（每週日自動重建 HNSW 索引）
- Session TTL：24 小時（每小時自動清理非活躍 session）
- 排程任務數：4 個（備份 / L1 清理 / HNSW 重建 / 新樣本掃描）

---

## slide 7 — 驗收標準：達成了哪些？

### 已驗證（本機測試階段）

- **消除重複運算**：`bio_history_check` 正確攔截已完成分析，單元測試通過
- **分析可追溯**：每次分析後 `analysis_history` 有記錄，`analysis_index` VIEW 正確彙總
- **Token 省策有效**：`bio_history_check/lookup/timeline` 三個工具不呼叫 LLM（SQL 直接回傳）
- **數據安全**：`safe_write()` 每次寫入後 CHECKPOINT，每日備份腳本可執行還原

### 待驗證（需部署後）

- **使用門檻低**：5 位成員實際使用後的定性調查
- **L1 命中率 >= 80%**：穩定使用一週後統計
- **月 Token 消耗在預算內**：Anthropic Dashboard 監控
- **Claude API 切換**：填入 `ANTHROPIC_API_KEY` 後端對端驗證

---

## slide 8 — 下一步

```
現在可做（本機）
    ├── 端對端測試：填入 ANTHROPIC_API_KEY，驗證 Claude 後端切換
    └── 啟用 launchd_scan_samples.plist 自動掃描新樣本

接著（需 Telegram Token）
    └── Telegram Bot 正式啟用（server/telegram_bot.py 骨架已完成）

之後（需 Linux 伺服器）
    ├── 路徑設定遷移（config/settings.py）
    ├── Docker 沙盒替換 code_executor.py（生產安全隔離）
    ├── FASTQ 自動 Kallisto 觸發
    └── 5 位實驗室成員實際使用驗證
```

---

## 附：系統架構圖（文字版）

```
使用者（Web UI / Telegram）
         │ 自然語言提問
         ▼
    server/agent.py
    ├─ BIO_TOOLS x 10（SQL / Parquet / 沙盒執行）
    ├─ 雙推理後端（local llama.cpp / Claude API）
    └─ plt.show() hook → 分析圖回傳聊天框
         │
    ┌────┴─────────────────────┐
    │                          │
    ▼                          ▼
L2 bio_memory.duckdb       L1 hermes_cache.duckdb
sample_registry            memory_recent (HNSW)
analysis_history           TTL 7 天
analysis_index VIEW
         │
         ▼
L3 原始數據（唯讀）
crc_visium_data/  bulk_rna_data/  proteome_data/
```
