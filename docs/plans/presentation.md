---
marp: true
theme: default
paginate: true
style: |
  section {
    font-family: "Inter", "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    font-size: 22px;
    padding: 50px;
    background: #ffffff;
  }
  h1 { font-size: 2.2em; color: #0d47a1; margin-bottom: 0.2em; }
  h2 { font-size: 1.6em; color: #1565c0; border-bottom: 3px solid #1565c0; padding-bottom: 6px; margin-top: 0; }
  h3 { font-size: 1.2em; color: #1e88e5; margin-top: 0.5em; margin-bottom: 0.3em; }
  code { background: #eef2f7; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; color: #0d47a1; font-family: "Fira Code", monospace; }
  pre { background: #f5f7fa; padding: 10px; border-radius: 6px; border: 1px solid #e4e7eb; font-size: 0.75em; }
  strong { color: #0d47a1; }
  .grid-2 {
    display: flex;
    gap: 30px;
    align-items: center;
  }
  .col {
    flex: 1;
  }
  .card {
    background: #f8fafc;
    border-left: 5px solid #1565c0;
    padding: 12px 18px;
    border-radius: 4px;
    margin-bottom: 12px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.04);
  }
  .card h3 {
    margin-top: 0;
    color: #1565c0;
  }
  .highlight {
    background: #fff8e1;
    border-left: 5px solid #ffb300;
  }
  .highlight h3 {
    color: #ef6c00;
  }
  .success {
    background: #e8f5e9;
    border-left: 5px solid #2e7d32;
  }
  .success h3 {
    color: #2e7d32;
  }
  .title-slide {
    background: linear-gradient(135deg, #0d47a1 0%, #1565c0 100%);
    color: white;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
  }
  .title-slide h1 { color: #ffffff; font-size: 3.2em; margin-bottom: 0.1em; text-shadow: 0 2px 4px rgba(0,0,0,0.2); }
  .title-slide p { font-size: 1.3em; opacity: 0.9; margin-top: 10px; }
  .footer {
    position: absolute;
    bottom: 20px;
    right: 40px;
    font-size: 0.6em;
    color: #94a3b8;
  }
---

<!-- _class: title-slide -->

# 🧬 智慧生資分析平台

### 實驗室專屬的自然語言生資智慧分析系統

無需程式能力 ‧ 0-Token 快速檢索 ‧ 自主健康演化

<div class="footer">Speaker: Bio-DB Project Team</div>

---

## 💡 實驗室現狀與三大痛點

<div class="grid-2">
  <div class="col">
    <h3>背景：多組學數據海量興起</h3>
    <ul>
      <li><strong>空間轉錄體（Visium HD）</strong>
        <ul>
          <li>8µm × 8µm 解析度，組織切片全覆蓋</li>
          <li>單片切片高達 <strong>30 億個數據點</strong></li>
        </ul>
      </li>
      <li><strong>龐大的運算開銷</strong>
        <ul>
          <li>原始前處理 Pipeline 單次耗時 <strong>~4 小時</strong></li>
          <li>佔用極大記憶體（~12 GB RAM）與 CPU 資源</li>
        </ul>
      </li>
    </ul>
  </div>
  <div class="col">
    <div class="card highlight">
      <h3>1. 重複運算與資源浪費</h3>
      <p>不同成員對同一樣本提出相似提問，被迫重頭分析，耗時費力。</p>
    </div>
    <div class="card highlight">
      <h3>2. 分析結果孤島化</h3>
      <p>分析成果散落在個人電腦，沒有歷史備忘，亦無軌跡留存。</p>
    </div>
    <div class="card highlight">
      <h3>3. 濕實驗室成員的高門檻</h3>
      <p>非資工背景成員無法自行靈活取數，高度依賴寫程式的人手動服務。</p>
    </div>
  </div>
</div>

---

## 🎯 系統設計與四大核心目標

<div class="grid-2">
  <div class="col">
    <div class="card success">
      <h3>✅ 不重複做一樣的事</h3>
      <p>同一樣本的同種分析僅算一次，之後全自動秒級取用。</p>
    </div>
    <div class="card success">
      <h3>✅ 每次分析必留痕跡</h3>
      <p>建立歷史完整帳本，誰做了什麼、結果在哪，一清二楚。</p>
    </div>
  </div>
  <div class="col">
    <div class="card success">
      <h3>✅ 0-Token 智慧檢索</h3>
      <p>問過的問題 0-token 緩存秒回；未問過的透過 SQL 與 AI 精準執行。</p>
    </div>
    <div class="card success">
      <h3>✅ 任何人都能自主科研</h3>
      <p>自然語言對話提問，直接在對話框內取得互動圖表與報告。</p>
    </div>
  </div>
</div>

---

## 📊 三層數據倉儲架構 (Medallion Architecture)

<div class="grid-2">
  <div class="col" style="flex: 1.1;">
    <h3>分層數據模型與查詢效能</h3>
    <ul>
      <li><strong>L3 Bronze（銅層）：不可變原始數據</strong>
        <ul>
          <li>FASTQ、SpaceRanger 原始產出</li>
          <li>絕對唯讀，保障實驗室資產安全性</li>
        </ul>
      </li>
      <li><strong>L2 Silver（銀層）：列式高壓縮數據</strong>
        <ul>
          <li>DuckDB + Parquet 結構化多表</li>
          <li>基因稀疏矩陣壓縮 95%，即查即用</li>
        </ul>
      </li>
      <li><strong>L1 Gold（金層）：HNSW 語意快取</strong>
        <ul>
          <li>以 0-token 精準快取高頻提問結果</li>
        </ul>
      </li>
    </ul>
  </div>
  <div class="col" style="flex: 0.9; text-align: center;">
    <img src="docs/三層架構.png" alt="三層架構" style="width: 100%; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);" />
  </div>
</div>

---

## 🔄 HELIX 代碼自適應演化閉環

<div class="grid-2">
  <div class="col" style="flex: 1.1;">
    <h3>讓生資工具隨時間自我健康進化</h3>
    <ul>
      <li><strong>主動監測 (Monitor)</strong>
        <ul>
          <li>修改自動觸發 <code>register_tool()</code></li>
          <li>頻繁修訂或出錯自動標記為 <strong>熱區工具</strong></li>
        </ul>
      </li>
      <li><strong>多維度體檢 (Assessment)</strong>
        <ul>
          <li>同時並行計算 <strong>Radon CC (複雜度)</strong>、<strong>Churn Ratio (變動率)</strong> 與 <strong>行級 X-Ray</strong></li>
        </ul>
      </li>
      <li><strong>AI 診療重構 (Stabilization)</strong>
        <ul>
          <li>擬定重構計畫，自動改寫並覆蓋升級</li>
        </ul>
      </li>
      <li><strong>雙軌記憶與衰減 (Decay)</strong>
        <ul>
          <li>優化快照隨時間<strong>漸進式降採樣 (320p ➔ 160p)</strong>，大幅節省 90% 視覺 Token</li>
        </ul>
      </li>
    </ul>
  </div>
  <div class="col" style="flex: 0.9; text-align: center;">
    <img src="docs/HELIX_架構圖.png" alt="HELIX 架構圖" style="width: 100%; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);" />
  </div>
</div>

---

## 🧠 ENGRAM 產出物索引與記憶檢索

<div class="grid-2">
  <div class="col" style="flex: 1.1;">
    <h3>將龐大圖表與報告精準歸檔與召回</h3>
    <ul>
      <li><strong>雙軌混合搜尋 (Hybrid Search)</strong>
        <ul>
          <li><strong>Layer 1</strong>：精確 SQL 參數硬核對</li>
          <li><strong>Layer 2</strong>：<code>BGE-M3</code> 向量 + HNSW 索引進行語意 Cosine 近鄰檢索</li>
          <li>以 <strong>RRF 混合排名</strong> 實現精準召回</li>
        </ul>
      </li>
      <li><strong>Blob 物理儲存優化</strong>
        <ul>
          <li>小於 500KB 的圖表與二進位 HTML 報告，以 <code>inline blob</code> 直接落庫快取</li>
        </ul>
      </li>
      <li><strong>版本溯源綁定</strong>
        <ul>
          <li>產出物與 <code>tools.content_hash</code> 精確 JOIN</li>
          <li>明確區分分析差異源於「樣本」還是「工具代碼版本漂移」</li>
        </ul>
      </li>
    </ul>
  </div>
  <div class="col" style="flex: 0.9; text-align: center;">
    <img src="docs/engram_架構圖1" alt="ENGRAM 架構圖" style="width: 100%; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);" />
  </div>
</div>

---

## 🌿 Agent 智慧查詢決策路徑

系統採用「優先走高效率路徑，能在前面解決就不往後走」的三段式決策防線：

```text
[ 收到自然語言提問 ]
        │
        ├──> Step 1: L1 SQL 精確比對 (0-token, < 1 秒)
        │             「這個樣本的這個分析，之前做過嗎？」 ➔ ✓ 命中直接回傳
        │
        ├──> Step 2: L1 HNSW 語意搜尋 (0-token, < 1 秒, cosine ≥ 0.88)
        │             「問法不同但意思相同的問題，問過嗎？」 ➔ ✓ 命中快取
        │
        ├──> Step 3A: L2 Parquet 標準分析工具 (極少 token, ~30 秒)
        │             意圖辨識 ➔ 生成 SQL ➔ 資料庫列式計算 ➔ AI 格式化回答
        │
        └──> Step 3B: 動態程式碼生成與沙盒執行 (全新分析, ~數分鐘)
                      LLM 生成 Python ➔ 沙盒安全執行 ➔ 產物落盤落庫與 HELIX 登記
```

---

## ⚡ 為什麼選擇 DuckDB + Parquet？

<div class="grid-2">
  <div class="col">
    <div class="card">
      <h3>🦆 DuckDB (嵌入式列式分析)</h3>
      <ul>
        <li><strong>向量化執行</strong>：每次僅讀取所需欄位，使用 SIMD 批次運算，略過大量基因稀疏零值。</li>
        <li><strong>零外部依賴</strong>：無需部署伺服器，<code>import duckdb</code> 即開即用。</li>
        <li><strong>原生向量檢索</strong>：內建 HNSW 向量索引，無縫實作語意快取。</li>
      </ul>
    </div>
  </div>
  <div class="col">
    <div class="card">
      <h3>📦 Parquet (列式高壓縮存儲)</h3>
      <ul>
        <li><strong>極致壓縮比</strong>：空間轉錄體 30 億非零值，壓縮率達 95%，落盤僅 <strong>416 MB</strong>！</li>
        <li><strong>跨語言兼容</strong>：Python/R 原生支援，濕實驗室成員可用 R 語言無縫直接讀取分析。</li>
        <li><strong>免讀入記憶體</strong>：直接以 SQL 聚合查詢，省去 ~12GB RAM 讀取開銷。</li>
      </ul>
    </div>
  </div>
</div>

---

## 💻 Demo：生資分析平台系統功能演示

<div class="grid-2">
  <div class="col">
    <ul>
      <li><strong>自然語言對話框</strong>
        <ul>
          <li>輸入大白話即可查詢複雜數據，採用 SSE 串流提供毫秒級打字響應。</li>
        </ul>
      </li>
      <li><strong>本機圖片多模態分析</strong>
        <ul>
          <li>支援 Ctrl+V 直接貼上組織切片或實驗圖。</li>
          <li>Gemma 4 Vision 本機執行，資料 100% 安全不上雲。</li>
        </ul>
      </li>
    </ul>
  </div>
  <div class="col">
    <ul>
      <li><strong>分析結果圖整合呈現</strong>
        <ul>
          <li>火山圖、QC 散點圖直接嵌入對話，支援點擊放大與高解析度下載。</li>
        </ul>
      </li>
      <li><strong>歷史與報告頁面</strong>
        <ul>
          <li>所有分析項目（樣本、類型、狀態、完成時間）完整留痕。</li>
          <li>生成獨立 HTML 報告檔案，供瀏覽器直接瀏覽與轉存。</li>
        </ul>
      </li>
    </ul>
  </div>
</div>

---

## 📈 系統實測數據與結果

* **測試數據總量**：**~39 GB** 原始空間轉錄體 (Visium HD) + **84** 個 Bulk RNA 臨床樣本。
* **物理足跡優化**：稀疏矩陣經 Parquet 列式壓縮為 **416 MB**，DuckDB 查詢免除 12GB RAM 讀入開銷。
* **評測指標**：
  
  | 評測項目 | 測試用例 | 通過率 / 配置 | 核心成效 |
  | :--- | :--- | :--- | :--- |
  | 單元與整合測試 | 6 個測試套件 | **105 / 106 PASSED** | 系統高穩定性，邏輯無死角 |
  | 向量相似度檢索 | HNSW 索引 | cosine $\ge$ 0.88 (TTL 7 天) | 相同/相似提問完全 0-token 秒回 |
  | 本機推理後端 | Gemma 4 26B | Vision IQ2_M 量化 (Port 8080) | 隱私數據本機安全執行 |
  | 語意嵌入模型 | BGE-M3 | Q8 多語向量 (1024-dim, Port 8081) | 精準識別中英文複雜學術術語 |

---

## 💬 討論：技術價值與現有限制

<div class="grid-2">
  <div class="col">
    <div class="card success">
      <h3>💎 核心技術價值</h3>
      <ul>
        <li><strong>大數據輕量化</strong>：讓龐大的空間組學與臨床 RNA 數據，在普通商用筆電即可實現即時交互與探索。</li>
        <li><strong>低門檻與高防護</strong>：大幅降低濕實驗室成員使用數據的阻礙，同時實現原始數據的絕對唯讀保護。</li>
        <li><strong>高性價比</strong>：90% 常規提問在 L1 緩存攔截，近乎零 token 帳單開銷。</li>
      </ul>
    </div>
  </div>
  <div class="col">
    <div class="card highlight">
      <h3>⚠️ 系統現有限制</h3>
      <ul>
        <li><strong>本機量化模型瓶頸</strong>：2-bit 量化 Gemma 4 在處理非常高難度的生資邏輯時，推理精準度有所損耗。</li>
        <li><strong>高並發限制</strong>：目前採用 <code>asyncio.Lock</code> 進行序列化寫入，未進行大規模多用戶壓力測試。</li>
        <li><strong>ExFAT 環境限制</strong>：在沒有日誌的檔案系統上，需依賴強制 CHECKPOINT 縮小損壞機率。</li>
      </ul>
    </div>
  </div>
</div>

---

## 🗺️ 未來展望與下一步演進

```text
  現在 (本機優化與自動化)
     ├── 端對端測試：填入生產環境 API Key，驗證 Claude 3.5 / Gemini 1.5 Pro 後端無縫切換
     └── 啟用新樣本自動定時掃描排程 (launchd_scan_samples.plist)
  
  接著 (團隊協同渠道)
     └── Telegram Bot 渠道正式啟用，生資平台隨身攜帶 (server/telegram_bot.py 骨架已完成)
  
  之後 (Linux 生產環境部署)
     ├── 使用 Docker 容器替換本機程式碼沙盒，確保物理層面的系統安全性隔離
     ├── 對接 Kallisto/Salmon，實現 FASTQ 原始數據自動化輕量定量 Pipeline 觸發
     └── 邀請 5 位實驗室臨床科研成員進行實地 Beta 測試：
         → 精確統計 L1 快取命中率，並進行定量滿意度評估 (預期命中率 ≥ 80%)
```
