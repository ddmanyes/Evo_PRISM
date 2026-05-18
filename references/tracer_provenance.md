---
title: "TRACER: Verifiable Generative Provenance for Multimodal Tool-Using Agents"
arxiv: 2605.09934
year: 2025
downloaded: pdfs/tracer_2605.09934.pdf
relevance: Tool provenance tracking, linking agent tool calls to stored results, stale cache detection
---

## 核心問題

當 AI agent 呼叫多個工具後給出最終答案，無法判斷每一句結論來自哪個工具的輸出——還是來自模型的預訓練記憶或幻覺。這個問題稱為 **provenance gap（來源缺口）**。在生資分析場景下，這意味著 agent 無法區分「這個結論是上次 spatial_eda 工具的輸出支持的」還是「模型自己推論的」，也無法偵測工具程式碼改變後快取結果是否仍然可信。

## 方法

TRACER 強制 agent 在輸出每一句結論的同時，附上結構化的 provenance 記錄：

```json
{
  "tool_call_id": "spatial_eda_run_003",
  "quoted_text": "CD45+ cells concentrated in tumor margin, score=0.91",
  "relation": "Compression"
}
```

三種 relation 類型：
- **Quotation** — 直接引用工具輸出原文
- **Compression** — 摘要自工具輸出
- **Inference** — 從工具輸出推導的結論

每筆 provenance 記錄自動驗證：tool_call_id 存在嗎？引用文字真的在該工具輸出中？Provenance 正確率作為 RL 訓練的 reward signal。

## 對本系統（bio_DB）的啟示

`analysis_history` 目前只記錄「做了什麼分析、結果在哪」，但缺少「這個結論由哪次工具呼叫的哪段輸出支持」。

可在 `analysis_history` 加入 `provenance JSON` 欄位：

```json
{
  "tool_call_id": "bio_run_spatial_eda_<uuid>",
  "tool_hash": "a3f8c2d1",
  "supporting_text": "top gene: CD45, mean_expr=3.2",
  "relation": "Compression"
}
```

結合 `tools` 表的 `content_hash`，agent 查詢歷史時可判斷：
1. 這個結論由哪次工具呼叫產生 ✓
2. 當時的工具版本（hash）是什麼 ✓
3. 工具程式碼後來有沒有改變 ✓
4. 若改變，此結論是否仍可信，是否需要重跑 ✓

## 關鍵數字

- 8B 模型超越最強大型閉源工具使用 baseline **約 24 個百分點**
- 多餘工具呼叫減少 **~30%**（4,949 → 3,486 次）
- 發布 TRACE-Bench：多工具推理的 sentence-level provenance 評測基準

## 引用

```
@article{tracer2025,
  title   = {TRACER: Verifiable Generative Provenance for Multimodal Tool-Using Agents},
  year    = {2025},
  journal = {arXiv preprint arXiv:2605.09934},
  url     = {https://arxiv.org/abs/2605.09934}
}
```

## 相關文獻

- MemGPT (Packer et al., 2023) — 分層記憶架構，本系統 L1/L2 設計參考
- DVC — DAG 依賴追蹤，工具版本 cascade invalidation 參考
- Mirascope `@ops.version` — Python 函數 content-hash 版本管理參考實作
