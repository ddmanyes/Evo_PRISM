"""
Evo_PRISM diversified queries generator (CA1-C).
=================================================

Generates a diversified scientific query dataset (N=450) representing:
- 3 Personas: Pathologist, Computational Biologist, Wet-lab PI
- 3 LLM Styles: Claude-3.5-Sonnet (academic/precise), GPT-4o (direct/code-like), Gemini-1.5-Pro (exploratory/context-rich)
- 50 queries per combination (3 x 3 x 50 = 450 queries)

Saves the query dataset as a structured JSON at: i:/Evo_PRISM/tests/fixtures/diversified_queries_450.json
"""

import os
import sys
import json
import hashlib
import random
from pathlib import Path

ROOT = Path("I:/Evo_PRISM")
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "tests" / "fixtures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "diversified_queries_450.json"

RANDOM_SEED = 42

# Define templates for the 3 Personas
PERSONA_TEMPLATES = {
    "Pathologist": [
        "評估 {sample} 樣品的 H&E 染色品質與細胞邊界 Conformity 吻合度",
        "分析 {sample} 空間轉錄組切片的 Hellinger NED 空間邊界銳利度指標",
        "計算 {sample} 蘇木精通道(Hematoxylin)提取後的細胞核分割 mask 與 Voronoi 擴張",
        "分析 {sample} 真皮成纖維細胞與 epidermal 邊界的多細胞解剖構造",
        "計算 {sample} 上皮角質形成細胞與真皮成纖維細胞的雙陽性 doublet rate 比例",
        "分析 {sample} 組織邊緣的 acellular 區域並測定背景雜訊",
        "比對 {sample} 的 H&E 高清裁切影像與 Cellpose 細胞分割遮罩的重疊率",
        "定量 {sample} 樣品毛囊 bulbs 結構的細胞質與細胞核面積分布",
    ],
    "Computational Biologist": [
        "對 {sample} 進行 Scanpy 標準化、Log1p、PCA 降維與 UMAP 嵌入繪圖",
        "計算 {sample} 單細胞空間矩陣的 Leiden 聚類並設定 resolution 為 {res}",
        "分析 {sample} 細胞的 total_counts 與 n_genes_by_counts p10 分布與 QC 過濾",
        "執行 {sample} 與對照組的 Wilcoxon signed-rank 差異基因統計檢定",
        "對 {sample} 的空間 Cellular Niches 進行 11-NearestNeighbors 二次聚類分析",
        "檢索 {sample} L2 銀層結構化特徵表的 HNSW 向量近似最近鄰相似度",
        "分析 {sample} 依賴關係圖譜的 bio_impact 爆炸範圍與遞迴 CTE 查詢延遲",
        "比對 {sample} 在不同 SemVer 版本工具下的一致性變異係數 CV",
    ],
    "Wet-lab PI": [
        "鑑定 {sample} 的 Krt14、Col1a1、Lgr5 與 Sox9 關鍵標誌基因在單細胞上的表達量",
        "對 {sample} 上的顯著上調與下調基因進行 GO BP 與 KEGG 生物通路富集分析",
        "分析 {sample} 毛囊幹細胞 Lgr5+ 與周圍真皮鞘細胞的空間最近鄰排他性 Permutation 檢定",
        "查詢 {sample} 樣品中富集上調的反應物通路 Reactome pathway",
        "比較 {sample} 與對照組之間富集的分子功能與生物學過程差異",
        "檢索 {sample} 中毛囊 bulbs 細胞群的特異 marker genes 的 dotplot 與 violin 圖片",
        "評估 {sample} 空間細胞微環境對於外部藥物刺激的潛在反應標記",
        "分析 {sample} 鑑定出的 Melanocytes 與 Endothelial Cells 空間共定位特徵",
    ]
}

# Define phrasing styles for the 3 LLMs
LLM_STYLES = {
    "Claude-3.5-Sonnet": [
        "請協助針對 {query}。務必使用學術級正式語彙，提供詳盡的統計顯著性分析與結構化報告。",
        "進行精確的科學計量分析以 {query}，並對其空間血緣關係進行嚴密推導與記錄。",
        "依據科學可重複性標準，對 {query}，並輸出內容定址的多模態產物與血緣圖譜。"
    ],
    "GPT-4o": [
        "執行分析：{query}。要求直接給出運行結果、關鍵指標與 markdown 對比表格，強調 execution throughput。",
        "撰寫 Python 腳本來 {query}。優化 code 循環複雜度，並在 safe 沙盒內熱加載運行。",
        "快速檢索快取：{query}。使用 3-way RRF 融合排序評分進行 L1 級零 Token 命中攔截。"
    ],
    "Gemini-1.5-Pro": [
        "我想了解 {query} 的背景生物學機制，請為我進行關聯分析並給出綜合解釋。",
        "探索性分析：結合 context hash 與 input fingerprint 評估 {query}，並說明方法漂移風險。",
        "從 L3 不可變原始數據開始，對 {query} 進行逐步 Medallion 分層轉換與樣本登記。"
    ]
}

def generate_queries():
    rng = random.Random(RANDOM_SEED)
    samples = [
        "ctrl_1_upper_bulge", "ctrl_2_upper_bulge", "pw24hr_1_upper_bulge",
        "pw48hr_1_upper_bulge", "ctrl_3_lower_bulge", "d3_roi1", "d3_roi3",
        "crc_roi1", "crc_roi2", "crc_roi3", "acellular_zone", "skin_follicle_showcase"
    ]
    resolutions = ["0.5", "0.8", "1.2"]

    records = []
    qid = 0

    # 3 Personas x 3 LLMs x 50 queries each = 450 queries
    for persona, p_templates in PERSONA_TEMPLATES.items():
        for llm, l_styles in LLM_STYLES.items():
            for i in range(50):
                # Sample templates and styles
                p_temp = rng.choice(p_templates)
                l_style = rng.choice(l_styles)
                sample = rng.choice(samples)
                res = rng.choice(resolutions)

                # Format the query
                base_query = p_temp.format(sample=sample, res=res)
                final_query = l_style.format(query=base_query)

                # Assign semantic bucket based on repetition or index
                semantic_bucket = qid % 5
                
                # Setup fingerprint and context
                # Generate SHA256 fingerprint representing input files
                fp_content = f"input-file-{sample}-v{i % 3}-hash"
                input_fingerprint = hashlib.sha256(fp_content.encode()).hexdigest()[:16]
                
                # Context hash
                context_content = f"session-llm-{llm}-qid-{qid // 10}"
                context_hash = hashlib.sha256(context_content.encode()).hexdigest()[:8]

                # Determine ground truth (80% overlap for high bucket, 10% for low, minus simulated fingerprint change)
                overlap_prob = (semantic_bucket * 20 + 10) / 100.0
                ground_truth_hit = rng.random() < overlap_prob
                
                # Fingerprint changed (simulated cache pollution: 20% probability)
                fingerprint_changed = rng.random() < 0.20
                if fingerprint_changed:
                    ground_truth_hit = False

                records.append({
                    "query_id": f"q{qid:04d}",
                    "query_text": final_query,
                    "persona": persona,
                    "llm_backend": llm,
                    "input_fingerprint": input_fingerprint,
                    "context_hash": context_hash,
                    "semantic_bucket": semantic_bucket,
                    "ground_truth_hit": ground_truth_hit,
                    "fingerprint_changed": fingerprint_changed
                })
                qid += 1

    # Save to file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"Successfully generated {len(records)} diversified queries.")
    print(f"Saved to: {OUTPUT_FILE}")
    
    # Compute SHA256 of the output dataset
    hasher = hashlib.sha256()
    with open(OUTPUT_FILE, "rb") as f:
        hasher.update(f.read())
    print(f"Dataset SHA256: {hasher.hexdigest()}")

if __name__ == "__main__":
    generate_queries()
