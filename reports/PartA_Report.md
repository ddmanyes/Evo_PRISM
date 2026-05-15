# Final Project Research Proposal (Part A)

**Title:** Extending Hermes Agent with a Token-Efficient Hierarchical Bioinformatics Memory Backend using Data Lakehouse Architecture
**Name:** 詹麒儒  | **Student ID:** d12528018

---

## 1. Introduction & Problem Definition

**Hermes Agent**（NousResearch, MIT License）是一個以 Python 建構的通用自主 AI Agent 框架，其核心設計哲學為「the agent that grows with you」——透過閉合學習迴路（closed learning loop）讓 Agent 從自身經驗中持續自我改善，並以 SQLite FTS5 全文搜尋與 Honcho 使用者建模維持跨 Session 的記憶連貫性。Hermes Agent 已支援 40+ 工具、MCP 協定整合、多平台訊息閘道（Telegram、Slack、Discord）以及 HPC Singularity 部署，架構上具備高度的可擴充性。

然而，當我們嘗試將 Hermes Agent 部署於**生物資訊（Bioinformatics）**垂直領域，執行 Bulk RNA-seq 或 Visium HD 空間轉錄體學分析時，其現有記憶後端面臨兩個極為嚴峻的系統瓶頸：

1. **重運算成本極大化**：Visium HD 單一樣本的 gene expression matrix 可達 \~50 GB，底層 Pipeline（STARsolo 對齊、定量、Squidpy 空間聚類）耗時數小時。Hermes 現有的 SQLite FTS5 記憶層僅儲存純文字摘要，無法快取中間特徵矩陣，導致 Agent 在語意相似的查詢下重複觸發昂貴的重型運算。
2. **Context 爆炸與 Token 消耗**：生資分析報告動輒數十萬字。Hermes 的 skill 系統與 FTS5 搜尋回傳全文本區塊，以 GPT-4o 為例（\$5/1M input tokens），這會迅速超出 Context Window 並產生高昂的 API Token 費用，使系統難以規模化。

本提案旨在為 Hermes Agent 設計並實作一個**專屬的生資記憶後端模組**，透過整合 **Data Lakehouse（資料湖倉）** 架構與 **Context Compression（脈絡壓縮）** 機制，在不修改 Hermes 核心的前提下，以插件形式（Plugin / MCP Server）最大化運算重用率並極小化 Token 消耗。

---

## 2. System Investigation: Hermes Agent + DuckDB + LLMLingua + DeepSeek-OCR

本研究調研了四項關鍵技術：

- **Hermes Agent 擴充機制**：Hermes 透過 `toolsets.py` 與 `optional-skills/` 支援模組化工具擴充，並以 `mcp_serve.py` 將自身暴露為 MCP Server。這意味著我們可以在不 fork 主專案的情況下，以 MCP Tool 的形式注入一個新的生資記憶後端，Hermes 的路由邏輯會自動決定何時呼叫它 [1]。
- **DuckDB 與資料湖倉架構**：DuckDB 作為進程內（In-process）OLAP 引擎，在 TPC-H benchmark 中查詢速度比 PostgreSQL 快 10–100×，並可直接查詢 Parquet 巨型檔案，無需搬移資料。其 `vss` 擴充同時支援**結構化基因名稱過濾**與**語意向量搜尋**，完美補足 Hermes 現有 SQLite FTS5 後端在生資混合查詢場景下的不足 [2]。
- **LLMLingua 語意壓縮（中期記憶）**：LLMLingua（Jiang et al., 2023）以小型語言模型理解語意後刪除冗詞，在 GSM8K 等基準測試中以 **20× 壓縮率**僅犧牲 <2% 的答案準確度 [3]。結合 Prefix KV Cache 可使重複查詢 Token 成本降低 \~90%。負責壓縮 **L1 摘要區（中期記憶）** 中仍需被讀懂的生資文字報告（DEG 列表、統計結論），保留完整語意結構供 Agent 推理使用。
- **DeepSeek-OCR 光學壓縮（長期記憶）**：DeepSeek-OCR（arXiv:2510.18234）的核心機制是將文字/圖表內容編碼為極少量的 vision token，以視覺模態作為壓縮中介。論文實測以 **100 vision tokens** 重建 800–900 個文字 token 可達 96.8% 精度（8.5× 壓縮率），並支援 chart、化學式等複雜圖形的 parsing。負責壓縮 **L1 歸檔區（長期記憶）** 中僅需作為索引、無需逐字推理的遠期報告與生資圖表，以極致壓縮換取儲存效率 [6]。兩種技術本質互補——LLMLingua 是語意層壓縮（保留結構供推理），DeepSeek-OCR 是視覺層壓縮（極致壓縮供索引）——共同覆蓋中期與長期記憶的不同需求。未來可進一步探索以 DeepSeek-OCR 全面取代 LLMLingua 的可行性。

---

## 3. Proposed Extension: Bio-Memory MCP Backend

我們提出以 **MCP Server** 形式為 Hermes Agent 新增一個生資專屬記憶後端，遵循 Medallion Architecture（Databricks, 2022）[4] 設計三層式延遲執行（Lazy Execution）機制：

```text
Hermes Agent Query (MCP Tool Call)
    |
    v
[L1 Gold]   Semantic Cache  --hit-->  Return (cost ~ 0)
    | miss
    v
[L2 Silver] DuckDB Store    --hit-->  LLMLingua -> Append L1
    | miss
    v
[L3 Bronze] Raw Lake  -->  Pipeline  -->  Write L2 -> L1
```

- **Level 1（Gold）— 語意快取層**：以 `text-embedding-3-small`（1536-dim）向量化歷史結論，cosine similarity ≥ 0.88 為命中閾值。依時間敏感度分三區：*工作區*（完整報告，TTL = 7 天）、*摘要區*（LLMLingua 壓縮至 1/20，TTL = 90 天）、*歸檔區*（**DeepSeek-OCR 視覺壓縮摘要**，永久保留）——生資圖表（UMAP、熱圖）經 DeepSeek-OCR 轉為自然語言描述後儲存為輕量索引，語意命中時才展開完整報告。設計參照 MemGPT [5] 的分層記憶模型，替換 Hermes 現有的 Honcho + FTS5 後端。
- **Level 2（Silver）— DuckDB 特徵儲存**：以 schema-aware 方式儲存 `.h5ad` 提取的 count 矩陣（Parquet 格式），透過 DuckDB `vss` 支援「基因名稱過濾 + 語意查詢」混合檢索。餵給 LLM 前以 JIDN（JSON Integer-Dense Notation）精簡稀疏矩陣，體積縮減約 40%。
- **Level 3（Bronze）— 不可變原始湖**：儲存原始 FASTQ / 影像矩陣，絕對不可變更。僅在 L2 缺乏特定特徵時才調用重型 Pipeline，依序回填 L2、L1。

---

## 4. Innovation & Comparison

| 維度         | Hermes 原生記憶（FTS5） | Naive RAG | **本提案擴充後**                |
| ------------ | ----------------------- | --------- | ------------------------------------- |
| 生資特徵快取 | 無                      | 無        | **有（L2 Parquet）**            |
| Token 消耗   | 高（全文回傳）          | 中        | **極低（LLMLingua 壓縮）**      |
| 混合查詢     | 純文字 FTS              | 純語意    | **結構化 + 語意（DuckDB vss）** |
| 重運算避免   | 無                      | 無        | **有（三層 Lazy Execution）**   |
| 圖表記憶索引 | 無                      | 無        | **有（DeepSeek-OCR 視覺壓縮）** |

---

## 5. Experiments & Verification Plan

使用 10x Genomics 公開的 PBMC Visium HD 資料集（\~45 GB）驗證三個面向：

1. **Token 消耗對比**：50 個語意重疊的空間聚類問答，對比 Hermes 原生 FTS5 vs. 本擴充模組的 API Token 總消耗（預期節省 ≥ 80%）。
2. **響應延遲評估**：L3 重算（\~4 h）→ L2 提取（\~30 s）→ L1 命中（<1 s），以 paired t-test（α = 0.05）驗證顯著性。
3. **語意準確度保留率**：以 BERTScore F1 衡量壓縮後結論與全量讀取一致性，目標 ≥ 0.92。

---

## References

[1] NousResearch. (2025). *Hermes Agent: The agent that grows with you*. GitHub. [github.com/nousresearch/hermes-agent](https://github.com/nousresearch/hermes-agent)
[2] Raasveldt, M., & Mühleisen, H. (2019). DuckDB: an embeddable analytical database. *SIGMOD*, 1981–1984.
[3] Jiang, H., et al. (2023). LLMLingua: Compressing prompts for accelerated inference of large language models. *EMNLP*, 13358–13376.
[4] Databricks. (2022). *Medallion Architecture*. Databricks Documentation.
[5] Packer, C., et al. (2023). MemGPT: Towards LLMs as operating systems. *arXiv:2310.08560*.
[6] DeepSeek-AI. (2025). *DeepSeek-OCR: Contexts Optical Compression*. *arXiv:2510.18234*. GitHub. [github.com/deepseek-ai/DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR)
