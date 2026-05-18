# REF-3：Hybrid Retrieval with Reciprocal Rank Fusion (RRF)

**來源**：Microsoft Research (2024) — "Hybrid Retrieval for RAG"; 原始 RRF 論文 Cormack et al. (SIGIR 2009)

---

## 核心概念

**RRF（Reciprocal Rank Fusion）** 將多個排序結果合併為單一排序，公式：

```
RRF_score(doc) = Σ_r  1 / (k + rank_r(doc))
```

- `k = 60`（Cormack 建議預設值，平滑低排名文件的貢獻）
- `rank_r(doc)`：文件在第 r 個 retriever 中的排名（1-based）
- 多個 retriever 分數加總，未出現的文件排名視為 ∞（貢獻 0）

---

## 為何 RRF 優於單一分數加權

| 方法 | 問題 |
|------|------|
| Vector only | 無法精確命中關鍵字 |
| Exact match only | 無語意排序能力 |
| 線性加權 (α·exact + β·vec) | α/β 需調參，對域外查詢不穩定 |
| **RRF** | 無需調參；排名歸一化消除分數尺度差異；實測比加權平均穩定 |

Microsoft 2024 實驗：RRF hybrid 在 BEIR benchmark 上比純 vector 搜尋 NDCG@10 高 4–8%。

---

## 對應本系統設計決策（9A-2）

**現況問題**：`search_artifacts()` Layer 1（exact subtype）命中即 return，Layer 2（HNSW）完全不跑。
若查詢是「padj < 0.01 的 volcano」，Layer 1 回傳所有 volcano，無語意排序。

**改法**：

```python
# Layer 1：exact subtype match → 依 created_at DESC 給 rank_exact
# Layer 2：HNSW cosine      → 依 distance ASC 給 rank_vector
# 合併：RRF_score = 1/(60 + rank_exact) + 1/(60 + rank_vector)
# 按 RRF_score DESC 回傳 top-N
```

**k 值建議**：artifact 數量通常 < 1000，k=60 適用；若日後 > 10k 可調至 k=30。

---

## 實作注意事項

- 文件只出現在一層時，另一層貢獻為 0（缺席，非懲罰）
- `threshold` 仍可保留作最終過濾（RRF_score 低於閾值不回傳）
- RRF 排名從 1 開始（1-based），0 會造成除以 k 的分母縮小

---

## 對應 Phase

**9A-2**，P0，與 9A-1 blob 拆表同步進行。
