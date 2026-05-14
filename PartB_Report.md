# Part B: 學術論文研究與提案 (Research Proposal)

> [!tip] 格式提醒
> 以下內容請填入 ACM Conference Proceedings Template 對應章節。需要英文版請告知。

---

## 1. Title & Author

**標題：** 為 Hermes Agent 擴充生物資訊分層記憶後端：以 LakeHarbor 結構感知架構為基礎
*(Extending Hermes Agent with a Hierarchical Bioinformatics Memory Backend Built on LakeHarbor's Structure-Aware Architecture)*

**Name:** 詹麒儒  | **Student ID:** d12528018

---

## 2. Abstract

Hermes Agent（NousResearch）是一個以閉合學習迴路為核心的通用自主 AI Agent，其現有記憶後端（SQLite FTS5 + Honcho）在生物資訊垂直領域面臨計算冗餘與 Context 爆炸兩大瓶頸。本研究以 *LakeHarbor*（ICDE 2024）的結構感知資料湖架構為理論基礎，提出以 MCP Server 插件形式為 Hermes Agent 新增一個三層式分層記憶後端，整合 DuckDB 混合查詢引擎、LLMLingua 純文字脈絡壓縮（中期記憶），以及 DeepSeek-OCR 圖表光學壓縮（長期記憶），兩種壓縮技術各司其職、互不重疊，在不修改 Hermes 核心的前提下，共同覆蓋生資輸出的文字與圖像兩種形態，大幅降低 API Token 成本與重複運算開銷。

---

## 3. Problem Definition

**Hermes Agent** 採用 SQLite FTS5 全文搜尋作為跨 Session 記憶索引，以 Honcho 維護使用者行為模型。這套設計對通用任務運作良好，但在**生物資訊（Bioinformatics）**場景中暴露出兩個根本性缺陷：

1. **計算冗餘（Computational Redundancy）**：生資 Pipeline（STARsolo 對齊、Squidpy 空間聚類）處理單一 Visium HD 樣本（\~50 GB）需數小時。Hermes 的 FTS5 記憶層僅儲存純文字摘要，無法快取中間特徵矩陣，導致語意相似的查詢反覆觸發重型運算。
2. **Context 爆炸與 Token 成本（Context Explosion）**：生資分析報告動輒數十萬字。Hermes skill 系統回傳全文本時，以 GPT-4o（\$5/1M input tokens）計算，成本極高且易超出 Context Window。

現有資料湖系統（如 LakeHarbor）雖已將結構化 schema 提升為一等公民，但以**靜態、同質解析度**處理所有資料，未解決 Agent 場景中「以何種精度記憶」的核心問題。Hermes 也尚無針對生資垂直領域的專屬記憶後端。

---

## 4. Prior Work & Research Gap

**主要參考文獻：** *LakeHarbor: Making Structures First-Class Citizens in Data Lakes* (ICDE 2024, pp. 5583–5592). Hiroyuki Yamada, Masaru Kitsuregawa, Kazuo Goda. University of Tokyo.

LakeHarbor 提出將結構化 schema 提升為資料湖的一等公民，使資料湖在保留 schema-on-read 彈性的同時，享有結構化 warehouse 的查詢效能。其核心貢獻是建立統一的結構感知儲存層，消弭資料湖與資料倉儲之間的鴻溝。

**研究缺口（The Gap）：** LakeHarbor 假設同一份資料對所有查詢者以相同解析度呈現。在 Hermes Agent 驅動的生資分析場景中，這造成兩個未解決的問題：

- 無法根據查詢時間距離或重要性，自動將資料**降級壓縮**（完整文本 → 摘要 → 純向量）
- 缺乏將脈絡壓縮內建於儲存引擎層的機制，使 Hermes 的 Token 成本控制完全依賴應用層

此外，Hermes Agent 本身尚無與任何資料湖倉架構整合的插件，存在明確的工程實作缺口。

---

## 5. Proposed Solution

我們提出以 **MCP Server** 插件形式為 Hermes Agent 新增生資專屬記憶後端，在 LakeHarbor 的結構感知儲存設計基礎上，加入**多解析度語意快取（Multi-Resolution Semantic Cache）**。Hermes 的 `mcp_serve.py` 機制允許此插件無縫接入，Agent 透過 MCP Tool Call 存取新後端，無需修改核心程式碼。

系統採用三層式 Medallion 架構：

```text
Hermes Agent Query
    │  (MCP Tool Call: bio_memory_query)
    ▼
[L1 Gold]  Multi-Res Semantic Cache ──hit──→ Return compressed context (cost ≈ 0)
    │ miss
    ▼
[L2 Silver] DuckDB Feature Store ──hit──→ LLMLingua compress → Append to L1
    │ miss
    ▼
[L3 Bronze] Immutable Raw Lake → STARsolo/Squidpy Pipeline → Write to L2 → L1
```

- **Bronze 層（L3）— 不可變原始湖**：儲存原始 FASTQ / 影像矩陣，絕對不可變更。僅在 L2 缺乏所需特徵時觸發重型 Pipeline，依序回填 L2、L1。
- **Silver 層（L2）— DuckDB 結構化特徵儲存**：延伸 LakeHarbor 的結構感知設計，以 schema-aware 方式儲存 `.h5ad` 提取的 count 矩陣（Parquet 格式）。透過 DuckDB `vss` 擴充支援「基因名稱結構化過濾 + 語意向量搜尋」混合查詢，補足 Hermes 現有 SQLite FTS5 後端的不足。
- **Gold 層（L1）— 多解析度語意快取**：超越 LakeHarbor 靜態儲存模型的核心創新。根據 TTL 與存取頻率跨三個解析度自動管理 Hermes 的生資記憶，替換 Honcho + FTS5 成為生資專屬記憶層：

  1. *近期記憶（高解析，TTL = 7 天）*：完整純文字分析報告，供 Agent 完整推理
  2. *中期記憶（中解析，TTL = 90 天）*：**LLMLingua 語意壓縮**至 1/20，刪除冗詞但保留完整語意結構（DEG 列表、統計結論），供 Agent 仍需推理但不必讀取全文時使用
  3. *長期記憶（低解析，永久）*：**DeepSeek-OCR 光學壓縮**——將遠期報告（含文字與圖表）編碼為極少量 vision token（100 tokens 可重建 800+ 文字 token，精度 96.8%），以視覺模態作為壓縮中介，僅作索引而非推理用途；語意命中（cosine similarity ≥ 0.88）時才展開完整報告。未來可探索以 DeepSeek-OCR 全面取代 LLMLingua 的可行性

---

## 6. Experiments & Verification

使用 10x Genomics 公開的 PBMC Visium HD 資料集（\~45 GB）實作原型，以 DuckDB（`vss` 擴充）與 LLMLingua 建構 MCP Server，接入真實的 Hermes Agent 實例。

- **實驗設定**：模擬 Hermes Agent 針對腫瘤微環境執行 100 次連續探索性查詢
- **評估指標**：

  1. *Token 成本分析*：對比 Hermes 原生 FTS5 記憶 vs. 本擴充模組 L1 命中的 API Token 總消耗
  2. *延遲基準測試*：L1 命中（<1 s）vs. L2 提取（\~30 s）vs. L3 重算（\~4 h），以 paired t-test（α = 0.05）驗證顯著性
  3. *語意保留率*：以 BERTScore F1 衡量壓縮後結論與全量讀取的一致性，目標 ≥ 0.92

---

## 7. Expected Results

基於 LLMLingua 20× 壓縮率與 Prefix KV Cache \~90% Token 節省的理論推算，預期本擴充模組在冗餘生資查詢中達到 **≥ 80% 的 API Token 節省率**，並使 Hermes Agent 避免超過 70% 的後續查詢觸及 L3 重型運算。本研究將證明：LakeHarbor 的結構感知架構可作為通用 AI Agent 記憶後端的工程基礎，而脈絡壓縮下沉至儲存層是實現可擴展 Agent 驅動生資系統的關鍵路徑。

---

## 8. References

1. **[Primary]** Yamada, H., Kitsuregawa, M., and Goda, K. (2024). LakeHarbor: Making Structures First-Class Citizens in Data Lakes. In *Proceedings of the 40th IEEE International Conference on Data Engineering (ICDE '24)*, pp. 5583–5592.
2. **[Supporting]** NousResearch. (2025). *Hermes Agent: The agent that grows with you*. GitHub. [github.com/nousresearch/hermes-agent](https://github.com/nousresearch/hermes-agent)
3. **[Supporting]** Liu, S., et al. (2025). Supporting Our AI Overlords: Redesigning Data Systems to be Agent-First. *arXiv preprint arXiv:2509.00997*.
4. **[Supporting]** Jiang, H., Wu, Q., Lin, C.-Y., Yang, Y., and Qiu, L. (2023). LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models. In *Proceedings of EMNLP 2023*, pp. 13358–13376.
5. **[Supporting]** DeepSeek-AI. (2025). *DeepSeek-OCR: Contexts Optical Compression*. *arXiv:2510.18234*. GitHub. [github.com/deepseek-ai/DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR)
