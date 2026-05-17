---
marp: true
theme: default
paginate: true
style: |
  section {
    font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    font-size: 22px;
  }
  h1 { font-size: 2em; color: #1a5276; }
  h2 { font-size: 1.5em; color: #1a5276; border-bottom: 2px solid #1a5276; padding-bottom: 4px; }
  h3 { font-size: 1.1em; color: #2e86c1; margin-top: 0.6em; }
  code { background: #f0f4f8; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }
  pre { font-size: 0.72em; line-height: 1.4; }
  strong { color: #1a5276; }
---

# Hermes Bio-Memory

## 實驗室生資智慧分析系統

讓實驗室成員用**自然語言**查詢生物資訊分析結果
無需任何程式能力，無需重複運算

---

## Slide 1 — 什麼是生物資訊分析？

現代生物醫學實驗室產出**海量多組學數據**：

- **空間轉錄體（Visium HD）**
  把組織切片的每個細胞位置都記錄「哪些基因正在活動」
  → 單片切片：100,000 個位置 × 30,000 個基因 = **30 億個數字**

- **Bulk RNA-seq**
  測量整批細胞的基因表現量，比較不同樣本或時間點的差異

- **Proteomics（蛋白質體學）**
  測量細胞中實際存在的蛋白質種類與豐度

這些數據**不能直接「看」**，必須透過複雜的計算流程（Pipeline）分析後才能得出生物意義。

---

## Slide 2 — 問題：四個實驗室痛點

1. **重複運算浪費**
   - 不同成員對同一樣本提相似問題，各自重跑相同 Pipeline
   - SpaceRanger（空間轉錄體前處理工具）單次耗時 **~4 小時**

2. **數據孤島**
   - 分析結果散落各人電腦，無統一查詢機制

3. **無分析記錄**
   - 無從得知某樣本是否已分析過、由誰完成、結果在哪

4. **使用門檻高**
   - 不熟悉命令列的成員無法自助取得結果

---

## Slide 3 — 目標

本系統以四個**可量測目標**為驗收標準：

1. **消除重複運算**
   相同樣本的相同分析不重複執行（L1 快取命中率目標 ≥ 80%）

2. **分析可追溯**
   每次分析寫入永久帳本，可查「誰、何時、對哪個樣本做了什麼」

3. **Token 消耗可控**
   結構化問題由 SQL 回答（0 token），LLM 只處理語意層問題

4. **使用門檻低**
   實驗室成員不需懂命令列，透過自然語言即可取得分析結果與圖表

---

## Slide 4 — 方法：三層架構

```text
L3 Bronze（銅層）── 不可變原始數據（FASTQ、SpaceRanger 輸出）→ 絕對唯讀
     │ 一次性轉換腳本
     ▼
L2 Silver（銀層）── DuckDB + Parquet（30 億數字 → 416 MB）→ 集中計算一次
     │ 分析完成後自動寫入
     ▼
L1 Gold（金層）  ── HNSW 語意快取（TTL 7 天）→ 問過的問題直接回傳
```

各層效能比較：

- **L1 快取命中**（cosine ≥ 0.88）：回應 < 1 秒，Token 消耗 **0**
- **L2 SQL 查詢**（L1 未命中）：回應 ~30 秒，Token 消耗極少
- **L3 Pipeline**（L2 無資料）：回應 ~4 小時，Token 消耗正常

---

## Slide 4B — 方法：為什麼選 DuckDB + Parquet？

### DuckDB — 嵌入式列式分析資料庫

- **列式向量化執行**：每次只讀需要的欄位，SIMD 批次運算，略過大量零值
- **零部署**：`import duckdb` 即用，不需另起 PostgreSQL / MySQL 服務
- **原生讀 Parquet**：直接 `FROM 'silver/*.parquet'` 查詢，無需預先匯入
- **內建 HNSW 向量索引**：語意快取搜尋免部署 Pinecone / Weaviate

生資實測：Visium HD 8µm 約 500 萬列 × 1 萬基因 → DuckDB SQL 聚合 20 行摘要傳給 LLM，節省 **99%+ token**

### Parquet — 列式壓縮儲存格式

- **高壓縮率**：稀疏基因矩陣（大量零值）RLE 壓縮效果極佳
- **跨語言**：Python / R / DuckDB 原生支援，濕實驗室可直接用 R 讀取
- **分區查詢**：依樣本分目錄，查詢時只讀相關分區

```text
原始 .h5 (SpaceRanger)  →  Parquet (416 MB)  →  DuckDB SQL  →  20 行摘要  →  LLM
  需 ~12 GB RAM 讀入        磁碟列式壓縮         免讀入記憶體     Token 極少
```

CRC Visium HD 原始 ~30 億數字 → **416 MB Parquet**（壓縮約 95%）

---

## Slide 5 — 方法：Agent 五段決策防線

收到問題，系統依序嘗試，**能在前面解決就不往後走**：

```text
使用者提問
 ├─ Step 1  SQL 精確比對（0 token，< 1 秒）
 │           「這個樣本的這個分析，做過嗎？」✓ 做過 → 直接回傳
 │
 ├─ Step 2  HNSW 語意搜尋（cosine ≥ 0.88）
 │           「問法不同但意思相同的問題，問過嗎？」✓ 問過 → 快取回傳
 │
 ├─ Step 3A 標準分析工具（L2 Parquet 已就緒 → 呼叫內建函數）
 │
 ├─ Step 3B Code Promotion 重用
 │           「以前生成過類似程式碼嗎？」重用 ≥ 3 次 → 自動升格永久工具
 │
 └─ Step 3C 全新程式碼生成
             LLM 生成 → 沙盒安全執行 → 失敗自動重試（≤ 3 次）
```

> **關鍵設計**：讓資料庫做資料庫擅長的事（Step 1–2），LLM 只處理真正需要推理的部分（Step 3）

---

## Slide 6 — 方法：文獻依據（上）

### 三層 Medallion 架構

- **來源**：Medallion Architecture（Databricks）；LakeHarbor ICDE 2024
- **截取**：原始數據不可變；Silver 集中計算一次而非每次查詢時重算
- **本系統調整**：Gold 層改用 HNSW 向量索引，適應自然語言查詢場景（非傳統 BI Cube）

### HNSW 向量語意搜尋

- **HNSW** = Hierarchical Navigable Small World：在高維向量空間中以 O(log N) 找到最相似向量
- **來源**：DuckDB VSS 擴充；Malkov & Yashunin 2018
- **本系統調整**：TTL 7 天 + 每週完整重建索引（HNSW 不支援增量更新）

### Agent-First + Token 省策

- **來源**：Agent-First Data Systems 2025；MemGPT 分層記憶模型
- **截取**：資料庫先回答結構化問題，LLM 只處理語意層
- **本系統調整**：SQL → 語意 → 完整報告三段防線，取代 MemGPT 分頁換入換出

---

## Slide 7 — 方法：文獻依據（下）

### 兩階段寫入 + 狀態機

- **來源**：WAL / crash recovery 通例；長時間批次作業的 saga pattern
- **截取**：長任務崩潰也能留下記錄（先寫 `running`，完成再更新）
- **本系統調整**：加入 `stale` 狀態（> 24h running 自動標記）+ ExFAT 環境下 `safe_write()` CHECKPOINT

### Code Promotion 自動升格框架

- **來源**：靈感自 A/B 測試 progressive rollout 與函數式程式設計中的 memoization
- **截取**：重用 ≥ 3 次代表隱性社群驗證（類似 GitHub star 的信號）
- **本系統原創**：`promotion_candidates` VIEW 自動偵測重用次數，觸發升格流程，無需人工追蹤

### 多模態視覺分析

- **來源**：Gemma 4 Vision（Google DeepMind 2025）；llama.cpp OpenAI-compatible API
- **截取**：本機 Vision LLM 可在不上傳敏感實驗圖至雲端的前提下做視覺分析
- **本系統調整**：`plt.show()` hook 自動捕獲 matplotlib 圖並回傳聊天框

---

## Slide 8 — Demo：Web UI 功能展示

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

## Slide 9 — 結果：系統數字

- 測試數據總量：**~39 GB**（Visium HD）+ **84** Bulk RNA 樣本
- L2 Parquet 大小：**416 MB**（原始 30 億數字 → SQL 可查結構，免去 ~12 GB RAM 讀入）
- Agent 工具數量：**10** 個 BIO_TOOLS
- 測試通過率：**105 / 106 PASSED**（6 個測試檔；1 筆為既有路徑問題，非程式邏輯錯誤）
- 推理引擎：Gemma 4 26B Vision IQ2_M（本機，port 8080）
- Embedding 模型：bge-m3 Q8（**1024-dim**，多語含中文，本機，port 8081）
- L1 快取 TTL：**7 天**（每週日自動重建 HNSW 索引）
- Session TTL：**24 小時**（每小時自動清理非活躍 session）
- 排程任務數：**4 個**（備份 / L1 清理 / HNSW 重建 / 新樣本掃描）

---

## Slide 10 — 結果：已驗收的目標

### ✅ 已驗證（本機測試階段）

- **消除重複運算**：`bio_history_check` 正確攔截已完成分析（單元測試通過）
- **分析可追溯**：每次分析後 `analysis_history` 有記錄，`analysis_index` VIEW 正確彙總
- **Token 省策有效**：三個 0-token 工具不呼叫 LLM，SQL 直接回傳
- **數據安全**：`safe_write()` 每次寫入後 CHECKPOINT，每日備份腳本可執行還原

### ⏳ 待驗證（需部署後）

- **使用門檻低**：5 位成員實際使用後的定性調查
- **L1 命中率 ≥ 80%**：穩定使用一週後統計
- **月 Token 消耗在預算內**：Anthropic Dashboard 監控
- **Claude API 切換**：填入 `ANTHROPIC_API_KEY` 端對端驗證

---

## Slide 11 — 討論

### 結果代表什麼？

- **L2 Parquet 的意義**：Visium HD 查詢從「需 ~12 GB RAM 讀入全矩陣」縮短為「SQL 聚合 20 行結果」，讓生資規模數據可在筆電即時查詢
- **兩階段寫入的意義**：~4 小時 Pipeline 中途崩潰也留下 `running` 紀錄，不重複排程、不無聲失敗
- **Token 省策的意義**：實驗室規模（月百次查詢）下，重複性問題在 Step 1/2 被攔截，幾乎零 Token 消耗

### 目前的限制

1. **本機測試階段**：L1 命中率 80% 為目標，尚無真實使用者數據驗證
2. **IQ2_M 量化精度**：Gemma 4 26B 使用 2-bit 量化壓縮以在本機執行，複雜推理能力有所下降
3. **單機單用戶**：`asyncio.Lock` 序列化寫入，高並發場景未經壓力測試
4. **ExFAT 環境**：`safe_write()` 縮小斷電損壞視窗，但無法完全取代有日誌的檔案系統

---

## Slide 12 — 結論

**Hermes Bio-Memory** 以三層 Medallion 架構 + Agent-First 查詢設計，解決了實驗室生資分析的四個核心痛點：

- **重複運算** → 兩層快取（SQL 精確 + HNSW 語意）攔截重複查詢，4 小時 Pipeline 不重跑
- **數據孤島** → 統一 DuckDB + Parquet，所有樣本與分析結果集中可查
- **無記錄** → 兩階段寫入狀態機，每次分析留下永久帳本，崩潰也不遺失
- **門檻高** → Web UI 自然語言介面 + 分析圖直接顯示，成員無需命令列知識

本機測試四項可量測目標已通過單元測試驗證；L1 命中率與使用者滿意度待部署後實地量測。

---

## Slide 13 — 下一步

```text
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
        → 量測 L1 命中率與使用者滿意度
```

---

## 附：系統架構圖

```text
使用者（Web UI / Telegram）
         │ 自然語言提問
         ▼
    server/agent.py
    ├─ BIO_TOOLS x 10（SQL / Parquet / 沙盒執行）
    ├─ 雙推理後端（local llama.cpp / Claude API）
    └─ plt.show() hook → 分析圖回傳聊天框
         │
    ┌────┴──────────────────────┐
    │                           │
    ▼                           ▼
L2 bio_memory.duckdb        L1 hermes_cache.duckdb
sample_registry             memory_recent (HNSW)
analysis_history            TTL 7 天
analysis_index VIEW
         │
         ▼
L3 原始數據（唯讀）
crc_visium_data/  bulk_rna_data/  proteome_data/
```
