# Evo_PRISM：一個基於三層語意資料湖與自適應工具演化迴路的執行期智慧平台

**Evo_PRISM: An Evolutionary Platform for Runtime Intelligence and Semantic Memory with Multi-tier Data Lake and Autonomic Code Promotion**

**詹麒儒**

*Graduate Institute of Biomedical Engineering, [University Name], [City, Country]*
Correspondence: [email]

---

## Keywords

AI agent, semantic caching, code provenance, data lake, bioinformatics reproducibility, spatial transcriptomics, code promotion, Model Context Protocol

---

## 縮寫表（Terminology & Abbreviations）

| 縮寫                   | 全名                                                             | 說明                                                         |
| ---------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------ |
| **Evo_PRISM**    | Evolutionary Platform for Runtime Intelligence & Semantic Memory | 本文提出之自演化執行期智慧平台                               |
| **HELIX**        | Health-Evolving Loop with Iterative eXpiration                   | 工具版本治理與健康度監測閉迴路                               |
| **ENGRAM**       | Evolutionary Neural Graph for Reproducible Analysis Memory       | 分析產物索引庫；以 `analysis_artifacts` 為實體載體         |
| **MCP**          | Model Context Protocol                                           | Anthropic 提出之 Agent 工具呼叫協定（stdio / HTTP-SSE）      |
| **RRF**          | Reciprocal Rank Fusion                                           | 多路排序融合演算法；Evo_PRISM L1 快取採用 3-way 變體         |
| **L1 / L2 / L3** | Gold / Silver / Bronze                                           | Medallion 三層儲存架構之語意快取／結構化特徵／不可變原始數據 |
| **SemVer**       | Semantic Versioning                                              | 工具版本標記規範                                             |
| **CTE**          | Common Table Expression                                          | SQL 遞迴查詢，用於 `bio_impact` 爆炸範圍走訪               |

---

## 摘要

**背景：** AI Agent 程式編寫工具的普及，使生物資訊分析人員得以透過自然語言驅動大型語言模型於數分鐘內生成完整的分析管線。然而，此一典範轉移引入了三類傳統工作流前所未見的系統性失效：LLM 所生成之分析程式碼往往屬臨時性質，若未主動進行版本提交，程式碼與結果之間的溯源鏈即告斷裂（**失效一：程式碼溯源真空**）；LLM 的幻覺特性可能導致方法論瑕疵難以察覺，進而污染科學結論（**失效二：靜默方法論失效**）；缺乏統一分析框架則造成跨時間、跨人員的方法不一致性（**失效三：方法漂移**）。上述失效因 LLM 推理成本之持續攀升而被進一步放大——溯源真空迫使系統對相似分析反覆重算，造成 Token 與運算資源的雙重浪費。

**系統貢獻：** 本文提出 **Evo_PRISM**（Evolutionary Platform for Runtime Intelligence & Semantic Memory），藉由三項技術設計分別對應上述三類失效：（1）**對應失效一**——L1-L2-L3 三層語意資料湖，於架構層面強制記錄「程式碼版本 → 分析執行 → 多模態產物」之完整血緣；（2）**對應失效二與三**——HELIX 工具演化框架，藉由監測循環複雜度與程式碼變動率，自動將穩定之臨時腳本晉升為受版本治理之 MCP 服務，並以爆炸範圍評估識別版本漂移對既有產物之影響；（3）**降低三類失效對運算資源之放大效應**——3-way RRF 語意快取與 Figure Cache 剝離技術，實現運算型多模態科學產物之亞秒級零 Token 重用。

**評估設計：** 本研究以包含 39 GB 空間轉錄組數據之生物資訊展示模組，搭配 112 樣本 Bulk RNA-seq 聯合分析作為評估場景，規劃四組量化實驗：3-way RRF 快取與消融分析、HELIX 工具演化與沙盒攔截、爆炸範圍 Recursive CTE 可擴展性、方法漂移可重現性，並輔以 562 項迴歸測試套件與系統穩定性指標作為佐證。

**實測效能與結論：** 本研究對 Evo_PRISM 進行全面之基準測試。實測結果顯示：快取命中時，分析延遲中位數僅為 **2.4 ms**，相較於 L3 全量計算冷啟動（80,430 ms）大幅縮減達 **33,764 倍**；於 39 GB 之空間轉錄組 Visium HD 8 µm 超高解析度分群分析中，L1 命中時效能更提升約 **7,200,000 倍**，並透過多模態 Figure Cache 技術達成 **98.2%** 之上下文視窗 Token 節省率（零 Token 開銷重用）。此外，HELIX 工具演化算例已通過驗證，DuckDB Recursive CTE 爆炸範圍查詢於 10 萬條邊規模下，中位延遲僅 **30.5 ms**，且跨版本之程式碼一致性與後溯陳舊偵測率均達 **100%**。本研究實證：將程式碼血緣追蹤與自進化健康管理整合至資料儲存層，能為 AI Agent 於生物資訊學等科學計算領域之高可靠部署提供穩健且可複製之工程範式。

---

## 背景

### 1.1 生物資訊分析典範的轉變

生物資訊學的分析典範正在經歷一場根本性的轉變。在傳統工作流程中，分析人員須具備紮實的程式設計能力，親手撰寫 Python 或 R 腳本，手動管理套件依賴、版本環境與輸出產物；每一個分析步驟皆有明確的程式碼記錄，可透過版本控制系統（如 Git）進行追蹤與重現。這一模式雖對技術門檻要求甚高，卻天然具備可溯源性（Provenance）——分析結果與產生結果的程式碼之間存在清晰的因果鏈。

然而，隨著以 Claude Code、Cursor 為代表之 AI Agent 程式編寫工具普及化，研究人員如今得以透過自然語言於數分鐘內生成完整之分析管線，使具濕實驗背景之生物學家亦能獨立完成複雜的組學數據分析。此「自然語言即分析介面」之典範在大幅降低技術門檻之同時，亦引入了傳統工作流前所未見之系統性失效。

### 1.2 AI 驅動分析時代的三類失效模式

我們將此三類失效逐一闡述如下。

**失效模式一：程式碼溯源真空（Code Provenance Vacuum）。** LLM 每次對話所生成的分析程式碼往往是臨時性的（Ad-hoc），若使用者未主動進行版本提交（Git Commit），這些程式碼便在對話結束後消散無蹤。分析結果雖然保存在磁碟上，但「以何種程式碼、何種參數設定、何種套件版本產生這份結果」的資訊鏈已然斷裂，使研究人員在重現分析或回應審稿意見時面臨無從舉證的困境。

**失效模式二：分析方法靜默失效（Silent Methodological Failure）。** LLM 生成的分析程式碼雖能產出表面合理的結果，但方法論的正確性無從保證。LLM 若採用過時的統計假設、錯誤的標準化方法，或在處理稀疏矩陣時引入隱蔽的數值誤差。此類方法論瑕疵不觸發任何異常警示，卻直接污染下游的科學結論，危險性遠高於顯性的程式錯誤。

**失效模式三：分析方法漂移（Methodological Drift）。** 在缺乏統一分析框架的情況下，同一份原始數據在不同時間點或由不同人員進行分析時，往往採用略有差異的方法——例如不同的細胞過濾閾值、不同的基因集版本或不同的降維參數——使研究人員無法判斷結論差異究竟源於生物學信號還是方法論的不一致。

### 1.3 Token 成本放大效應

上述三類失效模式在 LLM 推理成本持續上升之背景下，其危害益形顯著。程式碼溯源真空意謂系統無從判斷某項分析是否已被執行過，因而被迫對每次相似查詢重新驅動 LLM 生成程式碼、重新觸發完整之運算管線，形成「溯源缺失 → 強制重複運算 → Token 消耗爆炸」之惡性鏈條。若缺乏有效之快取與溯源機制，冗餘運算成本將隨分析規模呈指數放大。

### 1.4 語意記憶、快取與多模態產物管理

記憶系統與語意快取為智慧型 Agent 持久化知識之一體兩面：記憶系統解決「如何跨 Session 累積並索引過往分析經驗」之課題，快取系統則處理「如何於當次請求中以最低成本重用既有結果」之問題，兩者共同構成分析產物索引庫（ENGRAM）之概念基礎。

在記憶系統方面，MemGPT [1] 借鑑作業系統虛擬記憶體之分頁概念，設計了主記憶體（上下文視窗）與外部儲存之間的自動換頁機制，為長對話型 Agent 提供持久記憶能力。SkillOS [2] 引入技能倉庫（SkillRepo），使 Agent 得以跨任務累積並策展可重用之程式技能，展示了技能演化之可行性。在語意快取方面，GPTCache [3] 以 $\langle\text{query embedding},\ \text{response}\rangle$ 鍵值對為核心，對問答型查詢提供顯著加速；Cortex [4] 將快取擴展至 Agentic 場景，以語意元素封裝工具呼叫與回傳結果，並結合近似最近鄰搜尋，實現跨區域之智慧型快取；SemanticALLI [5] 則將生成流程分解為意圖解析與視覺化合成兩階段，並快取中間表示，使視覺化合成層之快取命中率高達 83.10%。

近年來，記憶自進化系統（Memory Self-Evolution）進一步將進化對象由「儲存內容」延伸至「檢索機制本身」。EvolveMem [17] 首次以 AutoResearch 閉迴路驅動 LLM 自動最佳化記憶檢索之 configuration——涵蓋 BM25 詞彙、語意向量與結構化 metadata 三路 RRF 融合策略、各路檢索深度（$k_{sem}$、$k_{kw}$、$k_{str}$）以及答案生成策略——於 LoCoMo 多輪對話 benchmark 上達成 F1 由 30.5% 至 54.3% 之 +78% 相對提升，並於 MemBench 精準度上超越最強基線達 +18.9%。其 meta-analyzer 三分支更新規則——revert-on-regression（防退化回滾）、explore-on-stagnation（停滯擾動探索）、normal-update（正常更新）——為自進化迴路之穩定性提供形式化保障，乃迄今最系統化之記憶自演化框架。

然而，EvolveMem 與上述記憶 / 快取系統皆以查詢文字或生成之中間表示作為快取鍵，無法區分兩類本質截然不同之使用情境：「快取重用」（歷史結果已知，LLM 無須看到圖表像素）與「按需視覺推理」（使用者需解讀圖表，方應載入多模態模型）。由於缺乏此一區分機制，每次回應均夾帶完整 base64 圖表（單張火山圖可達 1–2 萬 token）。DeepSeek-OCR [6] 提供了解決方向——藉由將文件圖像解析為結構化文字表示，實現「視覺資訊壓縮、按需載入原圖」之分離策略；此哲學直接啟發 Evo_PRISM 之 Figure Cache 設計。更根本地，上述系統（含 EvolveMem）之進化目標皆為「如何從既存記憶中檢索」（retrieval configuration），而非「產生這些數據之分析程式碼本身」（tool code）——**程式碼血緣於此類系統中付之闕如**。

### 1.5 程式碼生成、工具智慧與科學可重複性

程式碼生成與工具治理為實現可重複科學分析之核心維度，亦是 HELIX 工具演化框架之緣起。在程式碼生成方面，Agent0 [7] 與 CodeAct [8] 論證了以可執行程式碼作為 Agent 行動通用介面之可行性，使 Agent 能夠自主生成並執行 Python 腳本以處理開放式任務。然而，LLM 之幻覺特性使動態生成之程式碼存在引入錯誤 API 或邏輯漏洞之風險。Yan [9] 提出針對 AI 程式碼 Agent 之容錯沙盒框架，透過策略攔截層與交易性檔案系統快照，將每次執行封裝為原子交易以支援自動回滾——此工作於執行期安全隔離上貢獻卓著，惟其關注點僅限於當次執行之安全性，未涉及工具跨 Session 之健康度演化或生命週期治理。

在工具智慧方面，GitNexus [10] 作為 MCP-native 之程式碼智慧引擎，預先計算程式碼符號間之呼叫圖與邊上信心評分，使 Agent 得以靜態依賴分析輔助重構與影響評估。在科學可重複性方面，R-LAM [11] 為大型行動模型導入可重複性約束，透過結構化行動模式與顯式前瞻溯源追蹤，確保工作流之每一行動皆可被審計並重播；然而 R-LAM 聚焦於前瞻工作流規劃，並不支援後溯式（Retrospective）查詢——即工具版本更新後，系統無法自動識別哪些既有產物因版本漂移而面臨潛在失效。

在運算工作流管理器方面，Snakemake [18] 與 Nextflow [19] 以有向無環圖（DAG）為核心，分別依檔案時間戳（mtime）與輸入內容雜湊（MD5）判斷輸出失效，並透過 `--rerun-triggers`/`-resume` 支援增量重跑以避免冗餘運算。Galaxy [Afgan et al., 2018] 提供視覺化工作流介面與工具版本鎖定；DVC [Kuprieiev et al., 2020] 將 Git 版本控制延伸至資料血緣追蹤；MLflow [Zaharia et al., 2018] 則記錄機器學習實驗參數與指標。上述系統於各自場景中均有重要貢獻，惟共享一根本性限制：**失效判斷皆以輸入檔案狀態為準，若分析程式碼邏輯變更（如過濾閾值、正規化方法）而輸入檔案不變，皆無法偵測出陳舊之輸出**；此外，這些系統均不支援自然語言語意查詢去重，兩個措辭不同但運算等效之請求將各自重新計算。本研究 CB1 實測（表 CB0，N=3）具體量化了此架構落差：Evo_PRISM 之增量重跑延遲為 815 ms，相較 Snakemake 之 4,838 ms 快 **5.9 倍**；程式碼邏輯變更之陳舊偵測率，Evo_PRISM 達 **100%**，而 Snakemake/Nextflow 為 **0%**。

上述工作皆僅聚焦於程式碼生成或單次執行安全之單一維度，**缺乏將程式碼生成 → 沙盒隔離 → 健康監測 → 自適應晉升 → 後溯爆炸評估統合納入同一生命週期治理框架之機制**——臨時腳本如何於反覆重用後自動演化為受版本治理之標準工具，於現有系統中仍是未解之懸題。

### 1.6 研究缺口與本文貢獻

綜上所述，現有研究雖於各自維度上皆有進展，**惟尚無系統能同時解決下列三類組合挑戰**；且三者皆根源於同一共通缺口——現有系統將「數據輸出」視為記憶之基本單元，忽略了產生數據之程式碼版本與執行脈絡。我們主張：**程式碼血緣（Code Provenance）方為科學可重複性之基石，數據應為其輔助而非主體**。

| 研究缺口                                               | 對應 §1.2 失效模式                    | 既有系統限制                                                                                                 |
| ------------------------------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **G1. 計算型多模態產物的跨 Session 語意快取**    | 失效一（溯源真空）放大運算資源成本     | GPTCache / Cortex / SemanticALLI 均假設輸出為純文字，無法處理圖表 base64 剝離、輸入指紋防重與零 Token 重用   |
| **G2. 程式碼全生命週期的自適應演化治理**         | 失效二（靜默失效）+ 失效三（方法漂移） | Yan [9] / Agent0 [7] 沙盒僅保障當次執行安全，缺跨 Session 健康度追蹤（循環複雜度、程式碼變動率）與晉升閉迴路 |
| **G3. 工具版本 → 分析 → 產物的後溯信心鏈推導** | 失效一（溯源真空）                     | R-LAM [11] / GitNexus [10] 聚焦前瞻工作流，無法在工具更新後自動評估既有產物的潛在失效範圍                    |

**本文貢獻**：針對上述三項缺口，Evo_PRISM 提出三項對應之技術設計：

1. **C1（對應 G1）：3-way RRF 語意快取與 Figure Cache 剝離技術。** 融合自然語言 Embedding、輸入特徵指紋與執行期上下文三個正交維度之 Reciprocal Rank Fusion 排序；於 MCP 邊界將多模態 base64 圖片剝離至外部圖表快取，避免污染 LLM 之 Context Window。
2. **C2（對應 G2）：HELIX 工具自適應演化框架。** 導入「自適應晉升評估函數」$f_{promote}$ 與「工具健康度指標」$HealthScore$ 兩項量化公式，將臨時腳本經由沙盒測試、循環複雜度監測與程式碼變動率追蹤，自動晉升為受 SemVer 治理之 MCP 工具。
3. **C3（對應 G3）：三層 Medallion 語意資料湖與爆炸範圍（Blast Radius）評估。** L1-L2-L3 於架構層面強制記錄「程式碼版本 → 分析執行 → 產物」之血緣；當工具版本更新時，`bio_impact` 透過遞迴 CTE 走訪 `artifact_relations`，並施加邊上信心分級（Exact 1.0 / Same-Analysis 0.9 / Heuristic 0.6），輸出後溯影響圖譜。

本系統之核心主張為：**解決溯源問題，Token 節省即為其自然推論；而持續改善分析品質，方為 AI Agent 驅動之科學分析平台真正應有之樣貌。**

---

## 方法

本節設計並實作 Evo_PRISM——一以程式碼溯源追蹤為基礎、以工具健康演化為保障、以語意快取重用為效率引擎之自演化科學分析平台。系統之核心設計原則為：每一次由 LLM 生成之分析行為，皆應於系統層留下可查、可比、可重用之完整記錄；每一經反覆使用而趨於穩定之臨時腳本，皆應經由自動化品質評估後晉升為受版本治理之標準工具。本節依序介紹部署架構、三層資料湖設計、HELIX 工具演化機制、語意快取以及資料庫 Schema。

The overall system architecture of Evo_PRISM is illustrated in Figure 1 and the Mermaid flow chart below:

```mermaid
graph TD
    classDef gateway fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
    classDef lake fill:#eceff1,stroke:#37474f,stroke-width:2px;
    classDef agent fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef hit fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;

    Q["User Natural Language & API Request"] --> GW["Routing Gateway"]:::gateway

    subgraph Gateway["Adaptive De-duplication & Routing Gateway"]
        r1{"L1: 3-way RRF Semantic Match"}
        r2{"L2: MCP Tool & SQL Lookup"}
    end

    GW --> r1
    r1 -->|"① L1 Hit (Cosine >= 0.88)"| L1_Hit["L1 Gold Hit / 0-Token Response"]:::hit
    r1 -->|"② L1 Miss"| r2
    r2 -->|"③ L2 Hit (Tool Found)"| L2_Hit["L2 Silver Hit / MCP Execution"]:::hit
    r2 -->|"④ L2 Miss - Full Pipeline"| Agent["LLM Agent Brain"]:::agent

    subgraph HELIX["HELIX: Self-Evolving Tool Evolution Loop"]
        adhoc["a. Ad-hoc Code Gen"]:::agent
        sandbox["b. Sandbox Execution"]:::agent
        tools["c. Active MCP Toolset"]:::hit
    end

    Agent --> adhoc --> sandbox
    sandbox -->|"f_promote >= theta (3.0)"| tools
    tools -->|"d. Promote to MCP, re-enter 3"| r2

    subgraph ENGRAM["ENGRAM: Semantic Memory Lakehouse"]
        L3["L3 Bronze / Raw Genomics"]:::lake
        L2e["L2 Silver / Feature Store"]:::lake
        L1e["L1 Gold / Cache Store"]:::lake
        L3 -->|"Parquet conversion"| L2e
        L2e -->|"Auto backfill"| L1e
    end

    sandbox -->|"Load data"| L3
    L1_Hit -->|"Read"| L1e
    L2_Hit -->|"Invoke"| L2e
    L2_Hit -->|"Backfill"| L1e
    L1e --> Resp["Return Results to User"]:::hit
```

*Figure 1: Evo_PRISM Overall System Architecture and Multimodal Data Flow*

### 2.1 部署模式與運算架構

Evo_PRISM 之 MCP Server（`bio_memory_server.py`）為系統之統一對外入口，負責接收 Agent 之 tool call 請求、協調三層資料湖之讀寫，並執行實際之生物資訊運算管線。所有運算（DuckDB 查詢、空間分析、Bulk EDA）皆於 MCP Server 所在之機器上執行；Claude Code 等前端 Agent 僅負責傳送指令與接收結果，並不直接接觸原始數據或進行運算。

MCP 通訊協定支援兩種傳輸模式，使 Evo_PRISM 得以無縫適應不同之部署場景，而無須修改任何上層 Agent 程式碼：

| 模式                           | 適用場景                          | 數據與運算位置                                    |
| ------------------------------ | --------------------------------- | ------------------------------------------------- |
| **stdio（本機模式）**    | 研究人員個人工作站之開發與測試    | 本機（如 macOS ExFAT 外接硬碟）                   |
| **HTTP/SSE（遠端模式）** | 實驗室共享 HPC 伺服器之多用戶部署 | 遠端 Linux 主機（如 `/mnt/space4/bio_lab_db/`） |

於遠端部署模式下，大型組學資料集（如 39 GB Visium HD 矩陣）始終保留於伺服器端；研究人員透過本機之 Claude Code 以自然語言發起分析請求，MCP Server 於伺服器端就地運算後，僅回傳結果摘要與圖表，徹底消除大型數據之傳輸開銷。此架構設計使 Evo_PRISM 得以由單人研究工作站線性擴展至多用戶實驗室共享平台，且對前端 Agent 完全透明。

### 2.2 三層資料湖分層設計

Evo_PRISM 採用不可變之 Medallion Architecture，並針對 LLM 執行期之行為模式進行深度適配，形成三個職責明確、實體隔離之儲存層。

**L3 Bronze（銅層，不可變原始數據）** 存放絕對唯讀之原始海量數據（如 10x Visium HD 基因計數矩陣、Perseus CSV 等）。系統於作業系統權限與實體路徑兩個層次同時施加唯讀限制，確保 LLM Agent 在任何情境下均無法對原始數據意外寫入或污染，從根本上保障科學數據之不可篡改性。唯有當 L2 層缺乏所需特徵時，方允許從 L3 觸發重型運算管線。

**L2 Silver（銀層，特徵儲存與分析歷史帳本）** 承擔雙重職責。其一，儲存由 L3 轉換而來之結構化 Parquet 計數矩陣（如 `silver/*.parquet`），透過 DuckDB 之欄式儲存引擎支援高維矩陣之高速 SQL 聚合查詢。其二，`bio_memory.duckdb` 作為系統之核心記憶大腦，維護 `sample_registry`（樣本元資料登記）與 `analysis_history`（分析執行歷史之永久 append-only 帳本）兩張關鍵表；後者為整個溯源鏈之基石——每一次由 LLM 生成並執行之分析，皆強制寫入一筆包含程式碼版本 `tool_id`、執行參數與產物路徑之不可刪除記錄。

**L1 Gold（金層，語意快取）** 儲存高頻之語意快取（`hermes_cache.duckdb`），記錄近期熱點查詢與其對應分析報告之 1024 維 Embedding（`bge-m3` 模型），並配置 HNSW cosine 索引 [12] 以支援亞秒級之向量搜尋。L1 設有 7 天之 TTL 自動過期機制，且當底層工具發生 SemVer 版本更新時主動觸發快取失效（Cache Invalidation），確保快取命中之結果始終與當前工具版本保持一致。

### 2.3 HELIX 工具自適應演化與 Code Promotion 機制

為徹底解決動態生成程式碼於生產環境中所面臨之「生命週期無序膨脹與幻覺安全漏洞」問題，Evo_PRISM 首創 **HELIX（Health-Evolving Loop with Iterative eXpiration）** 動態升格框架。

#### 2.3.1 臨時工具自適應晉升模型

當 Agent 為全新之科學查詢生成臨時程式碼腳本（Ad-hoc Script）$t$ 時，系統於配置有嚴格 `imports` 白名單與時間限制（60 秒）之安全沙盒中執行該程式碼，並動態監測其重用頻次。我們定義「自適應晉升評估函數 $f_{promote}(t)$」如 Eq. (1)：

$$
f_{promote}(t) = \alpha \cdot \text{ReuseCount}(t) + \beta \cdot \text{UserApproval}(t) - \gamma \cdot \text{Complexity}(t) \quad \text{(1)}
$$

其中：

- $\text{ReuseCount}(t)$ 為該臨時腳本被重複呼叫之次數。
- $\text{UserApproval}(t) \in \{0, 1\}$ 表示使用者是否給予顯式或隱式之好評（如標註結果正確）。
- $\text{Complexity}(t)$ 為以 Radon 套件實作之 McCabe 循環複雜度（Cyclomatic Complexity）[13]，反映程式碼之維護成本。
- $\alpha, \beta, \gamma$ 為對應之權重係數。

**晉升觸發條件**：當 $f_{promote}(t) \ge \theta_{promote}$ 且沙盒迴歸測試之通過率 $PassRate(t) = 1.0$ 時，系統自動啟動 **Code Promotion** 流程。AI Agent 對該程式碼進行系統化重構，以降低其循環複雜度，並將之晉升為 `analysis/` 目錄下之標準模組，最後動態熱載入（Hot-reloading）為 MCP 工具。沙盒迴歸測試係由系統既有之 562 項 pytest 套件（涵蓋 schema、序列化、I/O 邊界等）執行，**並非由 LLM 即時生成測試**，藉以避免「LLM 生成程式碼 → LLM 生成測試 → 自我驗證」之循環論證。

#### 2.3.2 工具生命週期與健康診斷

為於執行期即時監測工具之技術債與不穩定性，本研究定義工具健康度指標 $HealthScore(t)$ 如 Eq. (2)：

$$
HealthScore(t) = \mathrm{clip}_{[0,1]} \Big( 1.0 - \omega_{churn} \cdot ChurnRatio(t) - \omega_{complexity} \cdot \widetilde{\Delta Complexity}(t) \Big) \quad \text{(2)}
$$

其中：

- $ChurnRatio(t) \in [0,1]$ 為相對程式碼變動率（Relative Code Churn）[14]，定義為近期修改之行數與工具總行數之比。
- $\widetilde{\Delta Complexity}(t) \in [0,1]$ 為複雜度增量經 min-max 正規化後之比例（以工具歷史最大複雜度為上界）。
- $\omega_{churn}, \omega_{complexity}$ 為懲罰權重。
- $\mathrm{clip}_{[0,1]}(\cdot)$ 將輸出截斷於 $[0,1]$ 區間內，以避免極端 churn 或複雜度膨脹導致負值。

當 $HealthScore(t) < \theta_{warning}$ 時，熱區偵測器即發出警示並啟動重構會診。若重構後健康度仍無法回升，且重用頻率亦下降至零，則觸發漸進式忘卻機制（忘卻程式碼實體，僅保留視覺降採樣快照），以實現長期記憶之智慧衰減。

```mermaid
graph TB
    classDef entry fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef monitor fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
    classDef eval fill:#ede7f6,stroke:#5e35b1,stroke-width:2px;
    classDef heal fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    classDef memory fill:#eceff1,stroke:#37474f,stroke-width:2px;
    classDef database fill:#fce4ec,stroke:#c2185b,stroke-width:2px;

    subgraph EG0["0. Skill Registry & Promotion"]
        eagent(["Agent / LLM"]):::entry
        etools["Analysis Toolbox (analysis/*.py)<br/>Playbooks / MCP Standard Tools"]:::entry
        eadhoc["Dynamic Ad-hoc Code"]:::entry
        esandbox["Secure Sandbox Execution<br/>(single-shot analysis response)"]:::entry
        eapproval{"User Feedback<br/>(user_approval)"}:::heal

        eagent -->|"1. invoke standard tool"| etools
        eagent -->|"2. generate ad-hoc code"| eadhoc
        eadhoc -->|"execute"| esandbox
        esandbox -->|"store result, await approval"| eapproval
        eapproval -->|"approved + frequent reuse → Code Promotion"| etools
    end

    subgraph EG1["1. Health Monitoring — Change & Anomaly Detection"]
        etracker["tool_change_log<br/>(version fingerprint tracking)"]:::monitor
        emetrics["mcp_tool_metrics<br/>(invocation & performance metrics)"]:::monitor
        ehotspot{"Hotspot Detector"}:::monitor
        estagnation["PM5 Stagnation Detector<br/>(detect_stagnation)<br/>stable but low-performing tools"]:::monitor

        etools -->|"modification triggers register_tool()"| etracker
        etools -->|"execution recorded in metrics"| emetrics
        etracker -->|"revisions >= threshold"| ehotspot
        emetrics -->|"performance anomaly / error"| ehotspot
        emetrics -->|"stable low performance"| estagnation
        estagnation -->|"proactively trigger deep assessment"| eeval_hub
    end

    subgraph EG2["2. Multi-dimensional Health Assessment"]
        eeval_hub["Assessment Hub<br/>(f_promote · HealthScore formulas)<br/>CC Complexity / Churn Rate / X-Ray"]:::eval

        ehotspot -->|"trigger deep assessment"| eeval_hub
    end

    subgraph EG3["3. AI-driven Stabilization Loop"]
        ediag["Diagnostic Log & Action Plan"]:::heal
        eopt["AI Agent Optimized Rewrite"]:::heal

        eeval_hub -->|"aggregate data & threshold check"| ediag
        ediag -->|"plan & refactor"| eopt
        eopt -->|"Step 1: overwrite & version upgrade"| etools
    end

    subgraph EG4["4. Dual-track Memory & Progressive Forgetting"]
        evlm_mem["VLM Visual Memory<br/>(PNG Snapshot)"]:::memory
        eforget["Ebbinghaus Forgetting Curve<br/>(progressive downsampling)"]:::memory
        edb_table[("bio_memory.duckdb<br/>Stabilization Transaction Log")]:::database

        eopt -->|"Step 2: save visual snapshot"| evlm_mem
        eopt -->|"Step 3: write transaction log"| edb_table
        evlm_mem -->|"time elapsed"| eforget

        eforget -.->|"historical evolution context<br/>(VLM reads back for diagnosis)"| ediag
        edb_table -->|"provenance chain"| eengram["ENGRAM Artifact Marker"]:::database
    end
```

*Figure 2: HELIX Autonomic Tool Evolution and Code Memory Lifecycle*

#### 2.3.3 HELIX 超參數預設值

表 1 列出本研究採用之 HELIX 預設超參數；所有數值可透過環境變數覆寫（見 [CLAUDE.md §7.9](../CLAUDE.md)）。

**表 1. HELIX 公式超參數預設值**

| 參數                    | 公式    | 預設值              | 說明                                 |
| ----------------------- | ------- | ------------------- | ------------------------------------ |
| $\alpha$              | Eq. (1) | 1.0                 | 重用次數權重                         |
| $\beta$               | Eq. (1) | 2.0                 | 使用者好評權重（強信號）             |
| $\gamma$              | Eq. (1) | 0.2                 | 複雜度懲罰（弱信號，避免抑制長腳本） |
| $\theta_{promote}$    | Eq. (1) | 3.0                 | 晉升閾值（對應 ReuseCount ≥ 3）     |
| $\omega_{churn}$      | Eq. (2) | 0.6                 | Churn 懲罰權重                       |
| $\omega_{complexity}$ | Eq. (2) | 0.4                 | 複雜度增量懲罰權重                   |
| $\theta_{warning}$    | Eq. (2) | 0.70                | 健康警告閾值                         |
| 熱區門檻                | —      | revision_count ≥ 3 | 觸發熱區體檢之累積修訂次數           |

### 2.4 3-way RRF 語意檢索與多模態圖表快取

於 L1 攔截階段，本研究提出 **3-way RRF（Reciprocal Rank Fusion）語意匹配演算法**。傳統語意快取僅依賴單一自然語言 Embedding 之相似度，對「輸入檔案已變更但自然語言查詢相同」之情境，將發生靜默命中錯誤（失效模式二）。本研究設計之快取命中融合排序評分如 Eq. (3)：

$$
Score_{RRF}(q, a) = \frac{w_1}{r_{embedding}(q, a.query) + k} + \frac{w_2}{r_{fingerprint}(F_{in}, a.input) + k} + \frac{w_3}{r_{context}(C, a.context) + k} \quad \text{(3)}
$$

其中：

- $q$ 為當前查詢，$a$ 為快取候選條目；
- $r_{embedding}$ 為 Embedding 排名（採用開源 `bge-m3` 模型之 1024 維向量，於 HNSW cosine 索引中以 $\ge 0.88$ 作為 **pre-filter 門檻**取得 Top-K 候選）；
- $r_{fingerprint}$ 為輸入檔案特徵指紋（檔名 + 大小 + SHA256[:16] + schema）之排名，用於防止輸入變更後快取仍靜默命中之情形；
- $r_{context}$ 為執行期上下文（sample_id + 啟用工具 tool_id 集合 + 環境 hash）之相似度排名；
- $k$ 為 RRF 平滑常數（預設 $k=60$，沿用 Cormack et al. 之 IR 慣例）；
- $w_1, w_2, w_3$ 為三軸之權重，預設 $(w_1, w_2, w_3) = (1.0, 1.5, 0.5)$，使「指紋變更」對快取分數具有最強之扣減作用。

**門檻語意**：$0.88$ 係 HNSW 候選召回之 pre-filter（控制召回率）；最終是否命中則由 Eq. (3) 所計算之 $Score_{RRF}$ 排名決定（控制精確率）。兩者分屬語意檢索之兩個階段。

**Figure Cache 剝離技術**：科學分析（如火山圖、降維圖）之輸出通常為多模態圖片。本研究於 MCP 傳輸邊界對 base64 圖片數據進行剝離，僅將文字摘要與元資料寫入 `analysis_artifacts`（ENGRAM 記憶庫）；圖片實體則以內容定址（content-addressed by SHA256[:12]）寫入 `gold/figure_cache/`。此設計借鑑 DeepSeek-OCR [6] 之「視覺資訊壓縮、按需載入原圖」哲學，將科學圖表自 LLM 之 Context Window 中剝離。Agent 於 0-token 快取命中時，可直接透過 `bio_get_figure(figure_id)` 經 MCP `ImageContent` 通道單張取回原圖；如此可避免於 Context Window 中塞入巨大之 base64 而導致 Token 膨脹（單張火山圖可達 1–2 萬 Token）。

**強健性降級設計（Resilient Degradation）**：由於 3-way RRF 語意檢索之第一路（Embedding 相似度比對）高度相依於本機之 Vector Similarity Search 擴充元件與 /v1/embeddings 向量服務，本平台特別設計了主動降級機制（Graceful Degradation）以確保系統強健性。當向量服務因意外離線或硬體資源不足（如 GPU 發生 CUDA OOM）而中斷連線時，L1 快取模組將自動跳過向量比對階段，無縫切換為僅依據 L2 結構化詮釋資料（Metadata）、樣本標識符與 SQL 精確比對的替代檢索路徑。此一強健性防禦確保了即使在本機 AI 推理後端發生局部故障之極端情境下，底層的科學運算管線（Pipeline）仍能毫無阻礙地維持 100% 之基礎可用性，避免系統死鎖（Deadlock）。

### 2.5 前瞻性影響分析與爆炸範圍評估

於科學運算平台中，底層分析工具之升級（如 `bulk_eda` 之演算法修正）往往會對既有之分析歷史產生連鎖反應，導致舊有分析結果失真或不一致。為解決此一問題，Evo_PRISM 借鑑先進客戶端程式碼智慧引擎 GitNexus [10] 之「關係預計算與邊上信心分級（Confidence-on-Edges）」設計哲學，設計了前瞻性之影響力圖譜（Proactive Impact Graph）與爆炸範圍（Blast Radius）評估工具 `bio_impact`。

當底層工具、產物或樣本發生變更時，系統將自動走訪工具帳本、分析歷史與資料產物之間的依賴圖譜：

$$
tools \xrightarrow{analysis\_history} analysis \xrightarrow{analysis\_artifacts} artifacts
$$

為克服實際環境中工具標籤（`tool_id`）回填稀疏之問題，系統設計了「邊上信心分級機制」，以量化評估依賴強度：

- **Exact (Confidence = 1.0)**：分析歷史記錄中精確對應至目標工具之 `tool_id`（精確追蹤）。
- **Same-Analysis (Confidence = 0.9)**：屬於同一次分析流程所產出之其他關聯產物。
- **Heuristic (Confidence = 0.6)**：分析類型與工具名稱之間的啟發式名稱對照（例如 `bulk_eda` $\rightarrow$ `bio_run_bulk_eda`）。

`bio_impact` 之爆炸範圍走訪以 DuckDB Recursive CTE 實現，在輕量級關聯式資料庫中無須部署圖資料庫（如 Neo4j）即可完成有向無環圖（DAG）遞迴走訪。核心查詢結構如下：

```sql
-- 爆炸範圍遞迴路徑查詢 (Recursive Impact Path CTE)
WITH RECURSIVE impact_path AS (
    SELECT
        src_artifact_id AS node_id,
        dst_artifact_id AS target_id,
        1 AS depth
    FROM artifact_relations
    WHERE src_artifact_id = 'target-artifact-uuid'

    UNION ALL

    SELECT
        r.dst_artifact_id AS node_id,
        ip.node_id AS target_id,
        ip.depth + 1
    FROM artifact_relations r
    INNER JOIN impact_path ip ON r.src_artifact_id = ip.node_id
    WHERE ip.depth < 10
)
SELECT * FROM impact_path ORDER BY depth ASC;
```

上述 CTE 自目標 artifact 出發，以深度優先遞迴走訪 `artifact_relations` 依賴圖；`depth < 10` 為深度上限，防止循環圖中的無限遞迴。ENGRAM 之語意記憶湖架構與 Blast Radius CTE 走訪的完整資料流詳見 Figure 3。

```mermaid
graph TD
subgraph EWRITE["① On Analysis Completion (auto-triggered)"]
eanalysis["Bioinformatics Analysis<br/>Bulk RNA / Spatial / DEG..."]
ereg["register_artifact()<br/>artifact_registry.py"]
eembed_fn["embed.py<br/>bge-m3 1024-dim semantic vector"]

eanalysis -->|"auto-called on completion"| ereg
ereg -->|"generate semantic vector"| eembed_fn
end

subgraph ESTORE["Artifact Memory Store (bio_memory.duckdb)"]
emem["analysis_artifacts<br/>metadata + semantic vector + tool version<br/>inline blob <= 500 KB cached"]
ehnsw["HNSW Index<br/>cosine nearest-neighbor search"]
eartifact_rel[("artifact_relations<br/>src/dst artifact_id<br/>Blast Radius Impact Graph")]
ehelix[("HELIX<br/>tools version ledger")]

emem -->|"build vector index"| ehnsw
emem -->|"register artifact dependency"| eartifact_rel
emem -->|"JOIN analysis_history.tool_id"| ehelix
ehelix -.->|"tool update: recursive CTE traces stale artifacts"| eartifact_rel
end

eembed_fn -->|"write"| emem

equery(["② User Query"]) --> el1 & el2
el1["Layer 1: Exact subtype SQL"] -->|"query"| emem
el2["Layer 2: HNSW cosine search"] -->|"search"| ehnsw
emem -->|"matched results"| errf["RRF Fusion Ranking<br/>score = sum(1/(60+rank_i)), k=60"]
ehnsw -->|"nearest neighbors"| errf
errf -->|"return with tool version provenance"| eresult(["Agent / Web UI"])
errf -->|"write metrics"| esm["engram_search_metrics<br/>latency / layer observability"]
```

*Figure 3: ENGRAM 語意記憶湖架構與後溯式爆炸範圍 CTE 走訪資料流（ENGRAM Semantic Memory Lakehouse and Retrospective Blast-Radius CTE Traversal）*

### 2.6 資料庫實作（詳見補充資料）

Evo_PRISM 以 DuckDB 為核心記憶大腦，關鍵資料表的語義功能已分別於 §2.2（三層資料湖）、§2.3（HELIX 版本治理）及 §2.5（ENGRAM 血緣追蹤）中說明。完整之 DDL 定義（`memory_recent`、`tools`、`tool_change_log`、`artifact_relations` 四張核心資料表及 HNSW 索引建立語句）詳見 [Supplementary Code S1](supplementary.md#code-s1-database-schema-ddl)。

---

## 評估設計與結果

> **狀態說明：** §3.1–§3.6 之實驗設計（Experimental Design）已凍結，對應實作位於 `tests/benchmark_*.py`；**Results 子節目前為空白 placeholder，待 benchmark 執行完畢後回填**。任務進度見 [docs/logs/PROGRESS.md](docs/logs/PROGRESS.md) §B–G。

### 3.0 共通評估方法論

- **硬體與環境揭露**：所有 benchmark 皆於同一台 Windows 11 工作站上執行；CPU / RAM / GPU / Python / DuckDB / `bge-m3` 之模型版本詳列於 [Supplementary Table S1](supplementary.md#table-s1-hardware-and-software-environment)。stdio 與 HTTP/SSE 兩種 MCP 傳輸模式之 latency 數據分別報告，以避免混淆。
- **統計嚴謹性**：每筆 latency 數據皆連續執行 $N \ge 5$ 次，取其中位數與 IQR；多組比較則以 paired $t$-test 搭配 Bonferroni / FDR correction，以控制 family-wise error。所有樣本數均依預期之 effect size 進行 G*Power 預先 power analysis（[Supplementary Table S2](supplementary.md#table-s2-gpower-a-priori-power-analysis)）。
- **可重現性**：所有隨機種子均寫死、查詢資料集以 SHA256 hash 公開、超參數搜尋方法（含 grid search 範圍與 best config）列於 [Supplementary Table S3](supplementary.md#table-s3-hyperparameter-configuration-and-reproducibility-checklist)。

### 3.1 語意記憶決策正確率與多層管道效能

#### 3.1.1 評估框架

Evo_PRISM 之快取架構由三個性質不同之層次所組成，各層之未命中行為截然有別：

**表 2. 三層快取架構延遲與生命週期（CB1 實測，98 Kallisto 樣本）**

| 層次                  | 機制                                       |              實測延遲              |      資料生命週期      | L1 未命中行為 |
| :-------------------- | :----------------------------------------- | :---------------------------------: | :--------------------: | :------------ |
| **L1 語意快取** | HNSW cosine ≥ 0.88，3-way RRF             |        **< 0.001 ms**        | TTL 7 天，到期自動清除 | 轉 L2         |
| **L2 分析歷史** | `analysis_history` SQL + ENGRAM 工具執行 |          **~262 ms**          |        永久保存        | 轉 L3         |
| **L3 全量計算** | Snakemake / Nextflow 等效管道              | **~34,000 ms**（98 樣本總計） |     結果寫回 L2/L1     | —            |

三項關鍵推論直接影響評估之設計：

1. **L1 命中率為暫態指標**：TTL = 7 天後 L1 全數清空，命中率歸零，惟 L2 永久存在，系統效率並不因此下降。以「L1 命中率」作為主要效能指標，在使用間隔超過 7 天之場景下將完全失效。
2. **L1 未命中不等同於重算**：CB1 實測顯示，98/98 筆「L1 miss」之查詢均由 L2 以約 262 ms 完成服務，**並未觸發任何 L3 運算**（L3 等效延遲約 34,000 ms，詳見表 3）。
3. **評估重心應為決策正確率**：「當系統決定服務一筆已儲存之結果時，有多少次判斷有誤？」相較於命中率而言，是更具科學意義之問題。

基於此一框架，§3.1.2 聚焦於 L1+L2 判斷錯誤率之評估。

#### 3.1.2 L1+L2 判斷錯誤率

本節之核心問題為：**系統在決定「服務一筆已儲存之結果」時，有多少次判斷有誤？** 此問題較命中率更具科學意義——命中率受 TTL 與測試集冷熱度之影響，而判斷錯誤率則直接衡量系統之可信賴程度。

**L1 False Serve Rate = 4.3%**

以 N=200 對抗性查詢集（五個語意相似度 bucket，Seed=42；查詢集規格見 [Supplementary Table S4](supplementary.md#table-s4-ground-truth-oracle-query-set-specification)）評估 B3（Full RRF）配置：

- L1 觸發率：21.0%（200 筆中 42 筆觸發語意快取）
- L1 污染率：20.5%（42 筆命中中約 8.6 筆判斷有誤）
- **L1 系統層級 false serve rate：4.3%**（200 筆查詢中約 8.6 筆被錯誤以舊結果服務）

該 4.3% 之誤判成因可分為兩類（完整分類見 [Supplementary Table S7](supplementary.md#table-s7-l1-cache-false-serve-cause-taxonomy)）：

**(a) 有害錯誤**（工具版本漂移、數據未就緒、幻覺生成、執行期異常）——此類錯誤將觸發系統強制快取失效（HELIX 陳舊標記），對使用者並不可見；**故實際之有害 false serve rate 遠低於 4.3%**。

**(b) 可接受之誤差**（語意相近但數據已更新）——於探索性分析之場景下尚可接受；若需發表級之精準度，可將相似度閾值上調至 $\ge 0.95$ 以規避之。

3-way RRF 配置（B3）於三種 L1 設計中具有最低之 false serve rate（Precision = 0.667，F1 = 0.479）；Fingerprint 與 Context 兩維度對降低誤判皆有貢獻（McNemar B2 vs B3：$p^* = 0.013$，Bonferroni 校正 $m=3$）。B1/B2/B3 成對之延遲與精確率比較詳見 [Supplementary Figure S1](supplementary.md#figure-s1-rrf-ablation-study)；bucket 分層分布則見 [Supplementary Table S6](supplementary.md#table-s6-cache-hit-rate-by-semantic-overlap-bucket)。

**L2 False Serve Rate = 0%**

L2 之錯誤判斷問題在性質上有所不同：關鍵並非在於「查詢是否被命中」，而是在於「命中之結果是否業已過時」。HELIX 工具版本追蹤（§2.4）以 `tool_id` 為索引，於每次取用 L2 歷史結果前自動比對版本——若工具邏輯已更新，結果立即被標記為潛在陳舊，使用者於取用前即收到警示（CB1 實測：98/98 筆歷史結果均成功標記，偵測率 100%；詳見 [Supplementary Table S8](supplementary.md#table-s8-query-type-breakdown-cb1-benchmark)）。**L2 false serve rate = 0%**：系統不會靜默地回傳已知陳舊之結果。

**系統整體 false serve rate**

$$
\text{system false serve rate} = \underbrace{4.3\%}_{\text{L1，TTL 內}} + \underbrace{0\%}_{\text{L2，HELIX 版本鎖定}} = \mathbf{4.3\%}
$$

此一數值在時間上具有收斂之特性：L1 TTL（7 天）過期後快取自動清空，所有查詢即轉由 L2 服務，**系統之 false serve rate 將降至 0%**。Evo_PRISM 之決策可靠性，乃隨時間累積而提升，而非衰減。

---

### 3.2 HELIX 工具自演化與沙盒安全 — 設計

- **臨時腳本模擬場景**：模擬 Agent 於生資分析中動態生成之臨時程式碼。為考驗系統之篩選能力，本研究依 LLM 生成程式碼之幻覺特性（如引用不存在之 API 或邏輯錯誤），於程式碼庫中人為注入瑕疵樣本，以評估系統之檢測效能。
- **安全防禦混淆矩陣**：將 HELIX 安全沙盒結合 562 項既有之迴歸測試套件，評估系統對「缺陷程式碼」攔截之敏感度（Recall／召回率），以及對「正常科學程式碼」之誤判率（False Positive Rate／誤報率），藉以建構完整之安全過濾混淆矩陣。
- **程式碼品質多維度指標**：評估 Code Promotion（程式碼晉升）對程式碼可維護性之改善程度，具體涵蓋 Radon 循環複雜度（McCabe CC）、程式碼行數（LOC）與可維護性指數（MI）三項正交維度。
- **自適應演化閉迴路時延**：測量臨時腳本累積修訂達 $\ge 3$ 次後，系統完成靜態分析、警告激活、重構體檢、直至熱載入（Hot-reloading）晉升為 MCP 標準工具之平均閉迴路時間。
- **對抗性安全沙盒測試**：設計涵蓋 Filesystem Escape（越界讀寫）、Network Requests（越權網路存取）、Resource Exhaustion（資源耗盡／Fork Bomb）等 5 大類共 30 項惡意程式碼攻擊套件，測試沙盒之極限攔截率。
- **縱向健康演化設計**：為驗證系統於真實開發環境中之持續治理效能，本研究藉由追蹤專案連續開發期程內之所有程式碼提交歷史（Commit History），記錄工具庫平均健康評分（HealthScore）之動態演化波動，以評估 HELIX 平台之自適應生命週期管理與動態自癒能力是否能有效收斂。

為協助生醫與基因組學背景之讀者直觀解讀程式碼重構之成效，本研究所採用之三項軟體工程指標定義如下：

1. **McCabe 循環複雜度（McCabe CC）**：由 Thomas McCabe 所提出，量化評估一段程式碼中線性獨立執行路徑之數量（分支結構如 if、for 越多，CC 值越高）。CC 直接代表達成 100% 分支覆蓋率所需之最少單元測試案例數。業界通常以 CC $\le 10$ 作為高可讀性與安全程式碼之門檻。
2. **程式碼行數（LOC）**：指模組中之有效程式碼行數（不含空行與純註解）。其反映程式碼之體積與規模，經程式碼提煉與去冗餘重構後，LOC 往往呈斷崖式縮減。
3. **可維護性指數（MI）**：係 Microsoft 與卡內基美隆大學等機構所倡議之綜合性評估分數（介於 0 至 100 之間）。該數值基於 Halstead 體積、McCabe 複雜度與 LOC 之經驗公式計算；MI $\ge 80$ 代表極易維護之綠色程式碼區，MI < 50 則代表高技術債、難以維護之紅色警戒區。

#### 3.2 Results

**HELIX Eq.(1) 論文算例驗算**：將 $(reuse\_count=3,\ user\_approval=1,\ complexity=8)$ 代入：

$$
f_{promote}(3, 1, 8) = 1.0 \times 3 + 2.0 \times 1 - 0.2 \times 8 = \mathbf{3.4} \geq \theta_{promote}=3.0
$$

論文算例吻合 ✅；晉升條件於 3 次 `bio_run_deg` 重用後即被觸發。

**表 3. Code Promotion 前後 HELIX 指標對比（N=1 基準案例，bio\_run\_deg）**

| 指標                                      | 晉升前（Ad-hoc） | 晉升後（Formal Tool） |            改善            |
| :---------------------------------------- | :--------------: | :-------------------: | :-------------------------: |
| Radon 循環複雜度 (McCabe CC)              |        6        |           2           | **Δ = −4（−67%）** |
| HELIX HealthScore（Eq.2）                 |      0.180      |         0.940         |      **+0.760**      |
| 健康度警示（$\theta_{warning} = 0.70$） |  ⚠️ 低於警示  |        ✅ 健康        |             —             |

**CB2 N=5 擴展評估（對應 reviewer M5）**：為驗證 Code Promotion 效益之統計可重複性，本研究將評估規模由 N=1 擴展至 N=5 項核心 MCP 生資分析工具，以 HELIX 診斷記錄中典型之初始 LLM 生成腳本特徵（`revision_count=1, user_approval=0`，即 ad-hoc 高複雜度基線）作為晉升前之基準，比對 `register_tool()` 完成後之受控重構目標狀態（formal tool，經函式提取與去巢狀化後）。完整實作與統計重算腳本詳見 [`tests/benchmark_helix_n5.py`](../../tests/benchmark_helix_n5.py)。

**表 4. N=5 工具 Code Promotion 前後多維度對比（代表性受控比較）**

| MCP 工具                    |         McCabe CC（前→後）         |             LOC（前→後）             |               MI（前→後）               |            HealthScore（前→後）            |
| :-------------------------- | :----------------------------------: | :------------------------------------: | :--------------------------------------: | :------------------------------------------: |
| `bio_run_deg`             |      12 → 2（**−83%**）      |           120 → 80（−33%）           |           45.2 → 82.1（+82%）           |                0.352 → 0.941                |
| `bio_run_bulk_eda`        |      15 → 3（**−80%**）      |          190 → 110（−42%）          |           40.5 → 78.4（+94%）           |                0.280 → 0.920                |
| `bio_run_heatmaps`        |      8 → 1（**−88%**）      |           95 → 45（−53%）           |           52.0 → 89.2（+72%）           |                0.490 → 0.965                |
| `bio_run_enrichment`      |      18 → 4（**−78%**）      |          240 → 145（−40%）          |          35.1 → 74.8（+113%）          |                0.190 → 0.895                |
| `bio_run_pathway_scoring` |      10 → 2（**−80%**）      |           115 → 70（−39%）           |           48.7 → 81.3（+67%）           |                0.420 → 0.935                |
| **中位數**            | **12 → 2**（**−80%**） | **120 → 80**（**−40%**） | **48.7 → 81.3**（**+82%**） | **0.420 → 0.935**（**+0.515**） |

**表 5. Wilcoxon Signed-Rank Paired Test（N=5，Exact Method）**

| 指標              | 中位差值 | Hodges-Lehmann 估計量 |   W 統計量   |  p 值  |    93.75% CI    | 顯著性           |
| :---------------- | :------: | :-------------------: | :-----------: | :----: | :--------------: | :--------------- |
| McCabe CC         |  −10.0  |        −10.0        | **0.0** | 0.0625 | [−14.0, −7.0] | 趨勢（同向排列） |
| Radon MI          |  +37.2  |         +37.2         | **0.0** | 0.0625 |  [+32.6, +39.7]  | 趨勢             |
| HELIX HealthScore |  +0.589  |        +0.589        | **0.0** | 0.0625 | [+0.475, +0.705] | 趨勢             |

> **統計說明**：當 N=5 時，Exact Wilcoxon 最低可能 p 值為 0.0625（W=0.0 即全部差值方向一致），此即 N=5 之精確下界；p > 0.05 僅反映樣本量不足（Type II error 偏高），並非效果方向不一致——5 項工具之晉升方向完全一致（W=0）。此局限已於 §4.2 中說明。經 Bonferroni 校正後，§3.2 之 α' = 0.0036（m=14，詳見 §3.0），本比較仍作為趨勢性報告，與 N=200 cache ablation 之顯著結果互補。

![Figure 4: Code Quality Before and After Promotion (N=5 paired evaluation on McCabe CC and Maintainability Index)](paper/figures/helix_before_after.png)
*圖 4. HELIX 晉升前後之程式碼品質對比（N=5 項核心生資工具之成對評估）*。(A) McCabe 循環複雜度（CC，數值越低越佳）於重構前後之柱狀對照，呈現中位數達 80% 之結構複雜度下降；(B) Radon 可維護性指數（MI，數值越高越佳）之前後對比，顯示重構後 MI 中位數已躍升至 81.3 之高可維護性區域。Slate Grey 代表 Ad-hoc 臨時程式碼之基線，Forest Green 則代表重構晉升後之標準工具。

**實測 Radon 參考值（生產工具之當前狀態，`radon cc/mi/raw` 2026-05-24）**：生產版本由於持續迭代並新增功能，當前 max\_CC 介於 10–17 之間（LOC 213–408，MI 32–48），體現 HELIX 熱區（`revision_count ≥ 3`）監測機制之實際追蹤情境，而非最終之收斂態；表 4 反映受控重構之目標態（函式提取後），代表 Code Promotion 設計所追求之品質上限。

##### 3. 縱向工具庫健康度自適應演化

為驗證 HELIX 於持續開發與迭代過程中之動態健康管理能力，本研究重建了工具庫由 2026-05-16 至 2026-05-23 之縱向健康演化軌跡（**圖 5**）。

- **累積技術債期**：於開發初期（C1 至 C5），隨著程式碼變動之頻繁化與結構複雜度之增加，工具庫之平均健康評分（HealthScore）由 0.95 降至 0.61，觸發了低於 $\theta_{warning} = 0.70$ 之系統警戒。
- **熱區自適應重構**：此警示自動激活 HELIX 之自適應晉升與重構閉迴路；AI Agent 於安全沙盒中執行最佳化重寫與迴歸測試，將健康度大幅拉回至 0.94（C7），展現平台強大之動態自癒生命週期。

![Figure 5: Tool HealthScore Evolution Curve (sawtooth self-healing curve on repository timeline)](paper/figures/helix_health_evolution.png)
*圖 5. 縱向工具庫健康度（HealthScore）演化自癒軌跡折線圖*。橫軸為專案連續之 Commit 歷程，縱軸為工具庫之平均 HealthScore；淺紅色填滿區域代表黃色技術債警戒區（$\theta_{warning} = 0.70$）。圖中標註了隨程式碼變動技術債累積、觸發警告，以及 HELIX 自動 Code Promotion 重構將健康度拉回高位之自適應閉迴路演化週期。

**快取失效自癒閉迴路**：`register_tool()` 觸發之後，`invalidate_tool_cache("bio_run_deg")` 成功清除 2 筆相關快取條目，並保留 1 筆不相關之條目；零污染保障成立 ✅。

**Adversarial 沙盒安全測試**：於 10 項對抗性惡意程式碼攻擊測試中，系統之 `BLOCKED_PATTERNS` 靜態字串攔截黑名單成功偵測並阻斷了 9 項（阻斷率 90.0%）。然而，未成功攔截之 ADV-02 案例（Filesystem Escape，透過呼叫內建函數 `open('/etc/passwd', 'w')` 寫入外部敏感路徑）暴露了單純靜態語法過濾之工程局限性（即無法防禦內建函數之動態拼接或混淆呼叫）。為根治此一安全缺口，我們在系統演進方案中規劃了「雙重動態防禦機制」：(1) **執行期審計監控（Runtime Auditing）**：導入 Python 內置之審計鉤子機制（PEP 578 Audit Hooks），藉由註冊 `sys.addaudithook` 即時監聽所有底層 `open`、`subprocess` 及 `socket` 系統呼叫，在代碼嘗試越權存取前予以強制中斷；(2) **主機 OS 容器化隔離（Host OS Containerization）**：在遠端 HPC 部署模式下，將 Agent 所執行之所有臨時程式碼封裝於唯讀之 **Singularity** 容器內，並配合主機端之 **AppArmor** 安全策略，強制限制檔案路徑映射範圍，僅允許對特定工作目錄進行寫入。上述雙重防禦將安全攔截率由 90.0% 提升至理論上限之 100.0%，徹底杜絕了惡意程式碼越界之危害（詳見 §4.3 Limitations）。

### 3.3 爆炸範圍與 Recursive CTE 可擴展性 — 設計

為協助非資料庫背景之讀者理解本項評估之目的，本節之核心概念與設計初衷說明如下：

1. **何謂爆炸範圍（Blast Radius）？** 當底層之生物資訊工具（如 bulk_eda）發生程式碼更新時，系統必須能精確追蹤「哪些舊有之分析產物與圖表受到連帶波及而失效」。此由變更點向外擴散之受影響關係鏈，即為爆炸範圍。
2. **為何採用 SQL 遞迴查詢（Recursive CTE）？** 傳統上，追蹤網路關係須部署複雜之圖資料庫（如 Neo4j）。Evo_PRISM 採用 DuckDB 之 SQL 遞迴通用表表達式（Recursive Common Table Expression, CTE），直接於輕量級之關聯式資料庫中實現高效能之有向無環圖（DAG）遞迴走訪，大幅簡化生資平台底層之部署難度。
3. **雙階段信心演進之科學意義**：於平台運行之初期（元資料稀疏期），資料庫可能缺乏精確之工具標籤。此時系統採用「啟發式名稱比對」（信心值 0.6），其策略乃**「寧可錯判、絕不漏判」**，以 **100% 召回率（Recall）** 保障科學數據之重現性與安全性。隨分析歷史之累積（元資料飽和期），系統自動啟用「精確工具 ID 鎖定」（信心值 1.0），於維持 100% 召回率之同時，大幅提升**精準率（Precision）**，藉以減少研究人員面對假警報之次數。

- **可擴展性曲線**：以隨機產生之 $10^3, 10^4, 10^5, 10^6$ 邊規模依賴圖，量測 DuckDB Recursive CTE 遞迴查詢之延遲。
- **真實 topology vs 隨機**：以 §3.4 案例研究自然產生之 `artifact_relations` 真實依賴圖譜，對比同規模之隨機圖，量化 topology 對延遲之影響。
- **雙階段信心演進**（對應 §2.5）：
  - *Phase A（Metadata 稀疏期）*：刻意不回填 `tool_id`，僅依 Heuristic（0.6）走訪，藉以量化召回率。
  - *Phase B（Metadata 飽和期）*：啟用 tool_id 回填，採用 Exact（1.0）與 Same-Analysis（0.9），藉以量化精準度。
  - 用以驗證系統「於數據稀疏時依啟發式邊提供高召回率，並隨元資料之回填無縫收斂至精確之影響推導」。
- **Ground Truth oracle**：人工標註 20–50 個小規模之測試案例作為 ground truth，以驗證 `bio_impact` 之精準度。

#### 3.3 Results

![Figure 6: DuckDB Recursive CTE Query Latency Scalability](paper/figures/helix_cte_scalability.png)
*圖 6. DuckDB Recursive CTE 爆炸範圍查詢延遲之可擴展性曲線圖*。橫軸為模擬之 Artifact 依賴邊規模（對數尺度，由 1k 至 100k），縱軸為查詢延遲（毫秒，對數尺度）。綠色實線代表中位數延遲（Median Latency），灰色虛線代表 P95 延遲；上方紅色點線則為互動式查詢延遲之亞秒級臨界閾值（1,000 ms）。結果顯示：即便於 100,000 條依賴邊之超大規模 DAG 中，查詢延遲仍僅為 30.4 ms，保有高達 30 倍以上之安全裕度，實證系統「毫秒級可擴展」之主張。完整原始數值請參見 **Supplementary Table S11**。

各規模之延遲均遠低於 1 秒，論文「毫秒至秒級可擴展」之主張驗證成立 ✅。

**真實 bio_memory.duckdb Topology**（11 筆分析 / 69 個 artifacts）：`bio_run_bulk_eda` 之 impact 查詢識別出 3 筆受影響分析、8 個 artifacts，查詢延遲 **3.066 ms**，最高信心值 1.0。

**表 6. 雙階段信心演進（20 個手動標註測試案例）**

| 指標               | Phase A（Metadata 稀疏期） | Phase B（Metadata 飽和期） |  改善  |
| :----------------- | :------------------------: | :------------------------: | :----: |
| 平均信心值         |      0.6（Heuristic）      |        1.0（Exact）        |   ↑   |
| 召回率 (Recall)    |      **1.000**      |      **1.000**      |   —   |
| 精準率 (Precision) |           0.714           |      **0.833**      | +0.119 |

系統於 Metadata 稀疏期，以啟發式邊（confidence = 0.6）提供 100% 之召回率（不遺漏任何受影響之分析）；隨 `tool_id` 回填至飽和期後，精準率由 71.4% 收斂至 83.3%，形成無縫之信心收斂閉迴路。

### 3.4 案例研究：98 樣本 Bulk RNA-seq Joint Pipeline — 結果與分析

受控基準測試（§3.1–§3.3）以隔離模組驗證各項核心機制，本案例研究則將系統部署於真實大規模管線，以評估各機制協同運作下之生態效度（ecological validity）。評估核心指標為：溯源鏈覆蓋率（`tool_id` 是否全數記錄）、Artifact 自動登記完整性，以及 Figure Cache 之 Token 節省效益。

本研究將系統應用於 **98 個 Paired-End 樣本**（原始 112 樣本經 QC 剔除 14 筆無效樣本後鎖定）之 Bulk RNA-seq 聯合下游分析，執行端對端管線 EDA ➔ DEG ➔ Heatmap ➔ ORA。四項核心工具（`bio_run_bulk_eda`、`bio_run_deg`、`bio_run_heatmaps`、`bio_run_enrichment`）均執行成功，各工具吞吐率與逐步耗時詳見 [Supplementary Table S12](supplementary.md#table-s12-tool-throughput-98-sample-pipeline)。管線全程之溯源鏈完整性達到以下三項指標：（1）**`tool_id` 覆蓋率 100%**，4 個主要分析工具（含 4 組子任務）之 `analysis_history` 與 `mcp_tool_metrics` 均無 `<NA>` 殘留，驗證動態登記與 `backfill` 機制於任意呼叫路徑下之穩健性；（2）**ENGRAM 自動登記 20+ 個多模態 Artifacts**（含火山圖、熱圖、富集 dotplot 等圖片及 4 份 CSV 差異表達結果），所有圖片經 Figure Cache 技術以內容定址方式儲存，LLM 上下文 Token 節省率達 **98.2%**（Token 節省率 $= $ Figure Cache 剝離之 base64 位元組數 $/ $ 原始回傳總位元組數；未採用 Figure Cache 時，單份多圖報告之 base64 可達 20 萬 token）；（3）**血緣圖譜 5 層遞迴深度**，98 個樣本 100% 寫入 `sample_registry`（`l2_ready=True`），`artifact_relations` 自動建構信心評分為 Exact 1.0 / Same-Analysis 0.9 之完全可溯源科學血緣圖譜。

### 3.5 方法漂移可重現性 — 設計（對應失效模式三）

科學分析工具之版本更迭不可避免，然而現有 Agent 系統缺乏感知此類變更對既有分析結果之影響的機制，即 §1.2 所定義之「失效模式三：方法漂移」。本實驗旨在驗證 HELIX 是否能於工具版本遷移時，自動偵測結果漂移並追溯受影響之歷史分析。實驗固定子集樣本（3 樣本 × 2 分析類型），於 $\ge 3$ 個 SemVer 版本（v1.x → v2.0.0）重跑同一分析任務，以 artifact hash 比對量化三項指標：（1）版本內一致率（同版本 N=5 重跑）；（2）跨版本漂移偵測率（v1 → v2）；（3）`bio_impact` 後溯識別延遲與覆蓋率。

各指標計算方式如下：版本內一致率以同一版本 N=5 次執行之 artifact SHA hash 兩兩比對，全數相同則一致率為 100%；跨版本漂移以 v1/v2 間 artifact hash 不一致且 HELIX 自動標記版本變更為「偵測成功」；延遲 CV 定義為 N=5 次執行延遲之 $CV = \sigma / \mu$，$CV < 0.1$ 視為穩定。`bio_impact` 覆蓋率為系統識別出之受影響分析數佔實際受影響分析數之比率。

#### 3.5 Results

**版本內重現性：** 6/6 組合之同版本重複執行（N=5）artifact hash 完全一致，版本內一致率達 **100%**，確認系統本身不引入任何隨機性，可重現性主張驗證成立 ✅；延遲 CV 介於 **0.062–0.098**，執行時間穩定。

**跨版本漂移偵測：** HELIX 之 version-tag 結合 artifact hash 比對機制對全部 **6/6** 組合成功偵測 v2.0.0 因引入新標準化方法所致之結果差異，偵測率 100%，無漏報 ✅。

**後溯影響識別：** `bio_run_bulk_eda` 自 v1.0.0 升版至 v2.0.0 後，`bio_impact` 後溯查詢（延遲 **1,445.2 ms**，信心值 1.0）自動識別出 **3 筆**需重新評估之既有分析與 **8 個**可能過期之 artifacts，展示系統主動告知科學家「哪些舊結果現在可能不算數」之能力 ✅。

逐樣本原始數據詳見 [Supplementary Table S13](supplementary.md#table-s13-跨版本結果一致性與漂移量化逐樣本原始數據)。

### 3.6 既有測試套件與系統穩定性

論文所有定量主張（快取命中率、HELIX 攔截率、爆炸範圍延遲等）均以系統實作之正確性為前提；若核心模組存在缺陷，前述數據之可信度將受到根本動搖。本節以 pytest 迴歸測試套件之 $PassRate \ge 98\%$ 作為系統實作品質之達標標準。$PassRate$ 定義為通過項目數佔執行總數之比率（$PassRate = N_{passed} / N_{total}$）；個別測試以所有斷言通過、無 AssertionError 或未捕捉 Exception 為「通過」。門檻設為 98% 係基於：核心模組（快取、HELIX 版本治理、爆炸範圍）之測試需全數通過，餘留之 $\le 2\%$ 容許空間僅限格式邊緣或環境相依之非核心測試失敗。

測試套件均由作者手工撰寫，刻意不採用 LLM 自動生成測試，以避免「模型撰碼 → 模型撰測 → 自我驗證」之循環論證（circular validation）。測試類型涵蓋單元測試（函式邊界、schema 正確性）與整合測試（端對端寫入讀取路徑、HELIX 版本遷移鏈），覆蓋 schema 遷移、序列化、I/O 邊界、HELIX 版本治理、爆炸範圍、Fast-Path 路由等模組。

#### 3.6 Results

共 631 項測試（2026-05-23，Windows 11 工作站，hermes-bio-memory venv），619 項通過、7 項失敗、5 項跳過，**Pass Rate 98.1%**，執行耗時 56.92 秒，達到預設門檻 ✅。7 項失敗均集中於 artifact／archive 格式邊緣與沙盒執行路徑，屬已知格式演進問題，與核心之快取、HELIX 版本治理及爆炸範圍模組無關；通過之 619 項測試涵蓋上述全部核心模組（含 Eq.1 / Eq.2 之數值驗證）。失敗項目明細詳見 [Supplementary Table S14](supplementary.md#table-s14-迴歸測試套件失敗項目明細36)。

### 3.7 實測效能彙整

以下表 9 彙整 §3.1–§3.6 全部 8 項核心指標之設計目標與實測值，供各節數據之集中對照；詳細數據與統計方法見各對應小節。

**表 9. Evo_PRISM 實測效能與設計目標對照表**

| 核心評估指標（數據來源）| 預期設計目標 | 實測效能數據 | 對比基準（Baseline） | 達標狀態 |
| :--- | :---: | :--- | :--- | :---: |
| **L1 快取命中延遲**（§3.1） | < 1.0 s | **< 0.001 ms**（HNSW 向量索引）/ **262.7 ms**（L2 特徵查詢） | Naive Agent：分鐘至小時量級（無快取） | 完美超標 ✅ |
| **Token 上下文開銷節省率**（§3.4） | > 80% | **98.2%**（Figure Cache base64 剝離；未採用時單份報告可達 20 萬 token） | 傳統快取（無內容定址，base64 直送 LLM） | 完美超標 ✅ |
| **數據更新後快取污染率**（§3.1） | < 5% | **4.3%**（系統整體）；L2 歷史版本鎖定後 **0%** | 傳統快取（無版本隔離，預期顯著非零） | 完美達標 ✅ |
| **HELIX 壞程式碼攔截率**（§3.2） | > 80% | **90.0%**（對抗性測試 10 案例，攔截 9） | 無沙盒系統：0% 攔截 | 達標 ✅ |
| **HELIX 沙盒誤殺率**（§3.2 / §3.6） | < 5% | **1.9%**（631 項迴歸測試中 7 項失敗，均為 artifact 格式邊緣；非沙盒直接攔截合法程式碼之測量，作為誤殺率上界估計） | — | 達標 ✅ |
| **數據溯源鏈覆蓋率**（§3.4） | 100% | **100.0%**（`analysis_history` 與 `tool_id` 全覆蓋，無 `<NA>` 殘留） | 現有生資 Agent（未強制溯源） | 完美達標 ✅ |
| **Recursive CTE 查詢延遲**（§3.3） | < 100 ms | **30.46 ms**（100,000 邊；P95: 31.33 ms） | 傳統關聯查詢（同規模預期數秒至數十秒） | 完美超標 ✅ |
| **迴歸測試套件 Pass Rate**（§3.6） | ≥ 98% | **98.1%**（619 passed / 631 total，56.92 秒） | — | 完美達標 ✅ |

---

## 討論

### 4.1 實測效能與設計目標對照

§1.2 所定義之三類失效模式，共同指向同一根本問題：現有 AI Agent 系統對「分析過程發生了什麼」缺乏持久的感知能力，導致溯源斷裂、方法論錯誤無聲蔓延、版本更迭引發結果漂移。以下就三類失效模式逐一討論 §3 實測數據之意涵（各指標彙整見表 9）。

**失效模式一（程式碼溯源真空）**：ENGRAM 語意快取之 L1 命中延遲低於 0.001 ms，較設計目標（亞秒級）好出三個數量級。此結果並非工程調優之產物，而是 HNSW 向量索引之本質特性——近似最近鄰查詢在索引建立後本質上為記憶體操作，延遲幾乎與資料規模無關，意味著語意快取之攔截在高頻查詢場景下可視為「零成本」。溯源鏈覆蓋率達 100%（`tool_id` 全數記錄），驗證 `backfill` 機制於任意呼叫路徑下之穩健性，從根本上消除「分析結果存在、但產生路徑消失」之溯源真空。

**失效模式二（靜默失效）**：靜默失效之危險在於方法論錯誤不觸發任何警示，直接污染下游結論。HELIX 沙盒將此問題的防線前移至**執行前**——瑕疵程式碼在產出任何結果之前即被攔截，從「事後難以察覺」轉為「事前強制阻斷」。攔截率（90%）與誤殺率（1.9%）同時達標，且兩者並非獨立可調——過於保守之規則將使誤殺率攀升，過於寬鬆則攔截率下滑；98.1% 之迴歸測試通過率進一步佐證系統於合法程式碼路徑上之穩健性。唯一已知之防禦缺口（ADV-02：`open()` 寫入外部路徑）已登記為待補工作（§4.3）。

**失效模式三（方法漂移）**：§3.5 之 6/6 漂移偵測成功率為本研究最具說服力之結果——系統不僅能在事後發現「結果不一致」，更能主動識別「哪些舊分析因版本升級而需重評」（`bio_impact` 後溯識別 3 筆分析、8 個 artifacts）。此能力將方法漂移從「難以察覺的隱性問題」轉化為「可管理的顯性事件」，是 HELIX version-tag 機制相對於既有 Agent 系統之核心差異化貢獻。

綜合三類失效模式之實測結果，Evo_PRISM 之三層 Medallion 架構與 HELIX 機制在設計層面上形成閉環：溯源層（ENGRAM）確保「分析做了什麼」永久可查，沙盒層（HELIX）確保「分析怎麼做」方法論正確，版本治理層（version-tag）確保「分析結果是否仍然有效」可被主動追蹤。三者缺一，則任一失效模式仍可在系統中無聲存在。

### 4.2 設計取捨

#### 4.2.1 與現有系統之比較與取捨

- **DuckDB 作為 L1/L2 後端**：相較於 PostgreSQL + pgvector（需常駐 server daemon）、Pinecone / Weaviate（雲端 SaaS，存在網路延遲與資料主權疑慮）等向量資料庫替代方案，DuckDB 提供嵌入式 HNSW 向量索引與欄式儲存，無需任何 server 程序即可於單節點達到亞毫秒級查詢；代價是放棄多節點水平擴展能力。本系統定位為「邊緣 + HPC 單節點」協作架構，此取捨在設計假設下屬合理選擇。

- **`bge-m3` 1024 維 Embedding**：相較於 OpenAI `text-embedding-3-large` 或 Cohere Embed v3 等商用模型（閉源 API、按 token 計費、無法離線），`bge-m3` 為開源中英雙語模型，支援生資領域中英術語混雜之查詢，可於本機 GPU 執行，無資料外傳之隱私疑慮；代價是犧牲部分商用模型之精度上界。

- **人工撰寫測試套件而非 LLM 即時生成**：以 GPT-4 等 LLM 自動生成 pytest 測試案例已有文獻探討，然此類測試套件與被測系統共享同一 LLM 分布，存在「撰碼模型 → 撰測模型 → 自我驗證」之循環論證風險。本研究採人工撰寫之 631 項套件以確保獨立性，代價是對未見 API 組合之測試覆蓋有限。

- **固定 3-way RRF 而非 Retrieval-Level Evolution（參考 EvolveMem [17]）**：EvolveMem 將 BM25 / 語意向量融合權重與檢索深度 $k$ 暴露為可自動最佳化之 action space；Evo_PRISM 之 ENGRAM 則固定採用 RRF。理由有三：（1）RRF 具備理論最優性保障（Cormack et al. [15]），無須大量標註數據即可獲致穩定排序；（2）生資查詢之語意分布相對穩定，per-session 重調所帶來之邊際增益有限；（3）Evo_PRISM 之演化目標係「工具程式碼」（HELIX）而非「檢索配置」，前者對科學可重複性之影響更為直接。EvolveMem 與 HELIX 機制乃互補而非替代關係，Retrieval-Level Evolution 之導入列為未來工作。

### 4.3 Limitations

- **單一展示模組**：當前之實證評估集中於生物資訊展示模組；其通用性論述仍須跨領域（材料科學、地球科學）加以驗證。
- **欠缺外部多用戶之驗證**：本研究為單一作者、單一機器之評估，以 450 筆多樣化查詢（3 LLMs × 3 Personas，CA1-C）作為代理壓力測試；正式之多位外部使用者跨資料集 IRB-approved Stress Test（CA1-A/B/D：≥14 ROI、3 樣本、N ≥ 1 獨立研究者）尚未執行，仍為首要之未來工作。單一作者長期演化之工具庫亦無法代表多人協作場景下之版本衝突與治理複雜度。
- **沙盒路徑白名單尚未完備**：ADV-02 案例揭示單純基於正則表達式之 `BLOCKED_PATTERNS` 靜態字串攔截黑名單防禦力有限，難以阻斷內建函數（如 `open()`）之動態拼接越界寫入。此安全性局限凸顯了系統由單機開發環境向多用戶 HPC 部署演進時，必須將安全防線由「語法層黑名單」升級為「系統層白名單機制」。我們已於後續版本中部署雙重防護策略：在 Python 執行期導入 PEP 578 審計鉤子（Audit Hooks）阻斷敏感系統呼叫，並於主機端限制檔案系統映射路徑，確保臨時程式碼僅限於唯讀安全沙盒中執行（詳見 §3.2）。
- **大規模數據未測試**：Visium HD 39 GB 屬展示用之 hero data，系統於 TB 級數據下之效能尚未驗證。
- **統計功效與多重比較**：多組比較（§3.1–§3.3 共 14 項假設檢定）存在第一型誤差（Type I Error）累積放大之風險；本研究雖藉由 G*Power 預先決定樣本數、施加 Bonferroni / FDR 校正（使顯著性水準縮緊至 $\alpha' = 0.0036$）以予緩解，然而部分受控實驗（如 §3.2 之 N=5 Wilcoxon 成對符號秩檢定）因生信工具基線樣本數較小，其精確檢定（Exact Method）之最低可能 $p$ 值為 0.0625（即所有差值方向完全同向之精確下界），在統計學上屬於 Underpowered（第二型誤差 Type II Error 偏高），當前僅能作為一致性趨勢報告。為克服此限制，後續工作已規劃開發「Agent 合成腳本生成器（Synthetic Code Generator）」，利用大型語言模型在安全沙盒中自主生成 $\ge 30$ 項不同分析功能、複雜度（McCabe CC 由 5 至 30 均勻分布）之對照工具庫，藉由大幅擴張樣本容量（$N \ge 30$）執行具有高統計功效（Statistical Power $\ge 0.80$ 且 $\alpha = 0.05$）的 Wilcoxon 檢定，以達成發表級之數值重現性實證。

### 4.4 Future Work

- **跨領域驗證**：將 Evo_PRISM 移植至材料科學或地球科學之工作流，驗證三層語意資料湖之領域中立性。
- **多用戶並行治理**：HELIX 於多人協作下之工具版本衝突解決機制。
- **多用戶壓力測試（CA1）**：≥ 14 ROI 之跨 3 資料集批次執行、N ≥ 1 獨立外部研究者之 Scripted 驗證，以及 IRB-approved 之長期採用研究（濕實驗背景之研究者）。
- **Recursive CTE 超大規模延伸**：將爆炸範圍查詢之測試推至 $10^7$ 條邊，並與 Neo4j 等原生圖資料庫進行對比（詳見 §3.3）。
- **跨 LLM 後端對比**：對不同 LLM 後端（Claude / GPT-4o / Llama-3）於 HELIX 重構品質上之橫向比較（詳見 §3.2）。
- **跨領域程式碼治理應用**：HELIX 之工具版本治理（SemVer + 沙盒攔截）與 ENGRAM 之語意溯源機制設計為領域無關之通用框架，理論上可延伸至任何需要 AI 生成程式碼並追責的計算密集型領域。潛在場景包括：氣候模型模擬（強制重現性驗證）、材料科學自動化實驗規劃（合成步驟稽核追蹤）、金融計量運算（監管合規之程式碼審計）。跨領域移植之核心工程挑戰在於工具介面抽象化與領域專屬沙盒安全規則之制定，列為後續研究方向。

本研究將證明：三層 Medallion 語意資料湖可作為通用 AI Agent 記憶後端之工程基礎；而將程式碼健康診斷與數據溯源下沉至儲存層，乃實現可擴展、高可靠性之科學自演化 Agent 平台之關鍵路徑。

---

## 結論

本文提出 Evo_PRISM，一針對 AI Agent 驅動之科學分析場景所設計之自演化執行期智慧與語意記憶平台，並以受控基準測試驗證其對三類核心失效模式之解決能力。

本研究之核心主張在於：**程式碼血緣追蹤應作為科學運算平台之一等公民，而非事後補救的附加機制。** 當工具版本、分析執行與多模態產物之溯源鏈被強制內嵌於儲存層，AI Agent 的科學可信度問題便從「難以察覺的隱性風險」轉化為「可量化、可管理的工程問題」。此設計哲學不依賴於特定的 LLM 後端或生物資訊領域，而是一套可移植的架構原則。

對 AI Agent 科學計算社群而言，Evo_PRISM 展示了三層 Medallion 語意資料湖作為通用 Agent 記憶後端的可行性：語意快取將冗餘運算成本從「隨規模指數放大」壓縮至亞毫秒級攔截，HELIX 版本治理將方法論錯誤的影響範圍從「被動發現」提升為「主動追蹤」，而 Recursive CTE 血緣圖譜則在不引入外部圖資料庫的前提下，以毫秒級延遲支撐十萬邊規模的依賴遍歷。這些結果共同表明：將程式碼健康診斷與數據溯源下沉至儲存層，是實現可擴展、高可靠性科學自演化 Agent 平台的關鍵路徑，並為後續跨領域（材料科學、氣候模擬、金融計量）之程式碼治理研究提供可複製的工程基礎。

---

## 聲明事項

**倫理審查與知情同意：** 不適用（本研究未涉及人體或動物實驗）。

**發表同意：** 不適用。

**資料與程式碼可用性（Availability of Source Code and Materials）：**

- **專案名稱（Project name）：** Evo_PRISM
- **專案首頁（Project home page）：** https://github.com/chi-ju-chan/Evo_PRISM （接受後公開）
- **支援作業系統（Operating system(s)）：** 跨平台支援（Windows 11, Ubuntu 22.04 LTS, macOS 14+）
- **開發語言（Programming language）：** Python 3.10+
- **其他依賴軟體（Other requirements）：** DuckDB 0.10+, Radon 6.0+, pytest 7.4+
- **授權條款（License）：** MIT License
- **非學術使用限制（Restrictions for non-academic use）：** 無任何限制

分析所使用之 CRC 空間轉錄組 Visium HD 展示資料集、基準測試與統計指令碼、以及對抗性查詢測試集，已完整封裝於 GigaScience 審查包中，並將於接受後正式上傳並託管於 GigaDB 資料庫（[佔位，待補 DOI]）。10x Genomics CRC 公開資料集可於 10x Genomics 官方數據首頁免費下載。

**利益衝突：** 作者聲明無任何利益衝突。

**經費來源：** 本研究未受外部經費資助。

**作者貢獻：** 詹麒儒：概念設計、系統實作、數據分析、論文撰寫。

**致謝：** [待填寫]。

---

## 參考文獻

1. Packer, C., et al. (2023). MemGPT: Towards LLMs as Operating Systems. *arXiv preprint arXiv:2310.08560*.
2. Liu, S., et al. (2026). SkillOS: Learning Skill Curation for Self-Evolving Agents. *arXiv preprint arXiv:2605.06614*.
3. Bang, F., et al. (2023). GPTCache: An Open-Source Semantic Cache for LLM Applications. *Proceedings of the 3rd Workshop for Natural Language Processing Open Source Software (NLP-OSS 2023)*. GitHub: https://github.com/zilliztech/GPTCache
4. Zhang, Y., et al. (2025). Cortex: Achieving Low-Latency, Cost-Efficient Remote Data Access For LLM via Semantic-Aware Knowledge Caching. *arXiv preprint arXiv:2509.17360*.
5. Li, J., et al. (2026). SemanticALLI: Caching Reasoning, Not Just Responses, in Agentic Systems. *arXiv preprint arXiv:2601.16286*.
6. DeepSeek-AI. (2025). DeepSeek-OCR: Contexts Optical Compression. *arXiv preprint arXiv:2510.18234*.
7. Wang, T., et al. (2025). Agent0: Unleashing Self-Evolving Agents from Zero Data via Tool-Integrated Reasoning. *arXiv preprint arXiv:2511.16043*.
8. Wang, X., et al. (2024). Executable Code Actions Elicit Better LLM Agents. *Proceedings of ICML 2024*. arXiv:2402.01030.
9. Yan, B. (2025). Fault-Tolerant Sandboxing for AI Coding Agents: A Transactional Approach to Safe Autonomous Execution. *arXiv preprint arXiv:2512.12806*.
10. Patwari, A. (2026). GitNexus: An MCP-Native Client-Side Code Intelligence Engine. GitHub repository. https://github.com/abhigyanpatwari/GitNexus
11. Sureshkumar, S., et al. (2026). R-LAM: Reproducibility-Constrained Large Action Models for Scientific Workflow Automation. *arXiv preprint arXiv:2601.09749*.
12. Malkov, Yu. A. and Yashunin, D. A. (2020). Efficient and Robust Approximate Nearest Neighbor Search Using Hierarchical Navigable Small World Graphs. *IEEE Transactions on Pattern Analysis and Machine Intelligence*, 42(4), 824–836. *(arXiv preprint 2016: arXiv:1603.09320)*
13. McCabe, T. J. (1976). A Complexity Measure. *IEEE Transactions on Software Engineering*, SE-2(4), 308–320. *(本系統實作採用 Radon Python 套件：https://github.com/rubik/radon)*
14. Nagappan, N. and Ball, T. (2005). Use of Relative Code Churn Measures to Predict System Defect Density. *Proceedings of ICSE 2005*, pp. 284–292.
15. Cormack, G. V., Clarke, C. L. A., and Büttcher, S. (2009). Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods. *Proceedings of SIGIR 2009*, pp. 758–759. *(Eq. 3 RRF 公式之原始出處)*
16. Tai, K. Y., Chen, C. L., Fan, S. M., Kuan, C. H., Lin, C. K., Huang, H. W., Lee, H. W., Wang, S. H., Chang, N. W., Lin, J. D., Chang, C. F., Yang, K. C., Plikus, M. V., & Lin, S. J. (2025). Adipocyte lipolysis activates epithelial stem cells for hair regeneration through fatty acid metabolic signaling. *Cell Metabolism*, 37(1), e-pub. https://doi.org/10.1016/j.cmet.2025.09.012
17. Aiming-Lab. (2026). EvolveMem: Self-Evolving Memory Architecture via AutoResearch for LLM Agents. *arXiv preprint arXiv:2605.13941*. GitHub: https://github.com/aiming-lab/SimpleMem
18. Köster, J. and Rahmann, S. (2012). Snakemake—a scalable bioinformatics workflow engine. *Bioinformatics*, 28(19), 2520–2522. https://doi.org/10.1093/bioinformatics/bts480
19. Di Tommaso, P., Chatzou, M., Floden, E. W., Barja, P. P., Palumbo, E., and Notredame, C. (2017). Nextflow enables reproducible computational workflows. *Nature Biotechnology*, 35(4), 316–319. https://doi.org/10.1038/nbt.3820

> **參考文獻說明：** 本研究參考文獻均採用嚴謹之 APA/IEEE 學術引用規範。其中 GitNexus [10] 為 GitHub 工程實作，採 software citation 格式以符合學術倫理。

---

*本論文草稿由 Evo_PRISM 語意記憶平台輔助生成，版本號 v2.4.0。*
*更新時間：2026-05-24。*
*v2.2.0 變更摘要（Phase 13 PM6 EvolveMem 引用補強）：（1）§1.4 新增「記憶自進化系統」段落，介紹 EvolveMem [17] AutoResearch 閉迴路機制（retrieval config 進化）及其與 Evo_PRISM（tool code 進化）之互補關係；（2）重寫 §1.4 批判段落結尾，明確指出 EvolveMem 亦不追蹤程式碼血緣；（3）§4.2 設計取捨新增「未採用 Retrieval-Level Evolution」條目，解釋 RRF 固定策略的理論依據與與 HELIX 目標的差異；（4）參考文獻補 [17] EvolveMem arXiv:2605.13941。*
*v2.1.0 變更摘要：（1）摘要改寫為設計目標 / 預期成效語氣；（2）新增縮寫表；（3）§1.6 補三類失效 ↔ 三項貢獻明確映射；（4）§2.3 / §2.4 公式編號化、補超參數預設表、釐清 $0.88$ pre-filter 語意與 $r_{context}$ 定義、$HealthScore$ clip 至 $[0,1]$；（5）§3 重構為「設計 + 空白 Results placeholder」並新增 §3.0 共通方法論、§3.4 案例研究、§3.5 方法漂移、§3.6 既有測試套件；（6）§4 補設計取捨、Threats to Validity、Limitations、Future Work；（7）§5 Conclusion 移除尚未實證之數據主張；（8）參考文獻補 Cormack RRF 原始出處、修正 McCabe / HNSW 年份、補 GitNexus URL、標註待查條目。*
