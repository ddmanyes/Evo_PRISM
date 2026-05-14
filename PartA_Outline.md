# Part A 提案大綱：Hermes 分層式生物資訊快取中樞

## 1. 專案標題與背景 (Title & Background)
*   **專案名稱**：Hermes - 基於資料湖倉架構的生資代理人分層記憶系統 (A Hierarchical Agentic Memory System for Bioinformatics using Data Lakehouse Architecture)
*   **學生資訊**：[您的姓名] | [學號] | [系級]
*   **核心目標**：解決自主化 Agent 在處理高成本生物資訊數據（如 Bulk RNA-seq, Visium HD）時，因無法重用過去運算結果而導致的極大計算資源浪費。

## 2. 動機與痛點分析 (Motivation)
*   **重運算成本極高**：生資原始資料解析（如特徵提取、空間聚類）耗時極長，Agent 不應在每次問答中都從頭執行。
*   **Token 消耗與 Context 爆炸 (Context Explosion)**：生資數據與報告動輒數萬字，每次整包餵給大語言模型 (LLM) 會導致極高昂的 API 費用，且超出上下文長度限制。
*   **分析視角的多樣性**：相同的 Level 2 資料（標準化矩陣），可能因為不同的 Skill 分析出不同的 Level 1 結論。現有系統無法有效管理這種「一對多」的衍生關係。

## 3. 現有系統調研：DuckDB 在分層架構中的應用 (System Investigation)
*   **為什麼選擇 DuckDB 建立資料湖倉 (Data Lakehouse)？**
    *   **無縫讀取底層檔案**：能直接以極高速度 Query 本地的 Parquet/CSV 檔案（Level 2），而無需將數據搬入資料庫。
    *   **向量擴充支援**：能透過 `duckdb_vss` 同時管理 Level 1 的語意分析結論。
    *   **Agent 友好**：作為進程內 (In-process) 引擎，完美配合 Python 分析腳本與 Agent 工作流。

## 4. Hermes 系統架構：三層智能回退機制 (System Architecture)
*   **Level 1 (Gold 層：多解析度語意快取 Hierarchical Semantic Cache)**：
    *   **近期記憶 (工作區)**：保留剛分析完的完整生資報告文字（高精度）。
    *   **中期記憶 (摘要區)**：將舊報告使用 LLMLingua 進行文字降維，刪除不影響語義的冗詞，保留關鍵結論（中精度，省 20 倍 Token）。
    *   **長期記憶 (歸檔區)**：轉化為低解析度的視覺特徵 (Visual Encoding) 或純向量 (Embedding)，僅在被喚醒時才回溯展開（極低 Token 成本）。
    *   Agent 第一優先檢索區，透過 **KV Cache 命中**機制實現 0.1 秒即時且廉價的回答。
*   **Level 2 (Silver 層：特徵數據 Feature Store)**：
    *   存儲清洗、對齊後的標準化數據（如 `.h5ad` 的 Parquet 表達）。
    *   當 L1 無答案時，Agent 直接提取此層數據餵給 Analysis Skills，免去底層解析。
*   **Level 3 (Bronze 層：不可變原始湖 Immutable Lake)**：
    *   存儲 FASTQ, 原始影像等。只有在 L2 缺乏數據時才觸發耗時的底層 Pipeline。

## 5. 關鍵創新：成本感知的代理人路由演算法 (Cost-aware Agentic Routing)
*   **脈絡壓縮與降維 (Context Compression & LLMLingua)**：在將 L2 的龐大生資特徵表餵給 Agent 前，透過小模型過濾或轉換為精簡自定義語法（如 JSON 轉為極簡字串），捨棄不必要的精度以換取極大的上下文空間。
*   **延遲執行 (Lazy Execution) 與快取命中 (Cache Hit)**：系統具有「能不重算就不重算」的惰性機制，自動向下層尋找最近的可重用檢查點 (Checkpoint)，最大化伺服器端 KV Cache 的利用率。
*   **非破壞性疊加分析**：新的分析指令會調用 Skills 在 L2 上運算，並將新結果「附加」至 L1，完美保護底層資料。

## 6. 預期效益與評估 (Evaluation)
*   **計算時間對比**：設計實驗比較 Hermes 架構在「重複提問」、「改變分析參數」等情境下，對比傳統「一條龍重跑」所節省的運算時間（預期達 90% 以上）。

## 7. 結論與專題展望 (Conclusion)
*   Hermes 架構示範了資料庫技術如何將生資分析 Pipeline 轉換為 Agent 可以即時調用的動態知識庫，為未來的 Final Project 奠定基礎。
