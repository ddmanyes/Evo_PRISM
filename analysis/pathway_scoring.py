"""
通用路徑評分模組（ssGSEA / Z-score 聚合）。

主要函數：
    load_gene_sets()     — 從 YAML 檔載入基因集字典
    zscore_aggregate()   — Z-score 聚合法（快速，適合時序探索）
    ssgsea_score()       — ssGSEA AUC 法（統計嚴謹，適合最終報告）
    score_pathways()     — 整合入口：自動選擇方法並存檔

YAML 格式（見 gene_sets/hair_follicle.yaml）：
    PathwayName:
      description: 路徑說明
      genes: [Gene1, Gene2, ...]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from analysis.tool_registry import register_tool_on_import

logger = logging.getLogger(__name__)

GENE_SETS_DIR = Path(__file__).parent.parent / "gene_sets"


# ── 基因集載入 ────────────────────────────────────────────────────────────────


def load_gene_sets(
    yaml_path: Optional[Path] = None,
) -> dict[str, list[str]]:
    """從 YAML 檔載入基因集，回傳 {pathway_name: [gene, ...]}。

    Parameters
    ----------
    yaml_path:
        YAML 檔路徑。None 時使用 gene_sets/hair_follicle.yaml。
    """
    path = yaml_path or (GENE_SETS_DIR / "hair_follicle.yaml")
    if not path.exists():
        raise FileNotFoundError(f"找不到基因集檔案：{path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    gene_sets: dict[str, list[str]] = {}
    for name, body in raw.items():
        if isinstance(body, dict):
            genes = body.get("genes", [])
        elif isinstance(body, list):
            genes = body
        else:
            logger.warning("跳過無法解析的路徑：%s", name)
            continue
        gene_sets[name] = [g for g in genes if isinstance(g, str)]

    logger.info("載入 %d 條路徑基因集（來源：%s）", len(gene_sets), path.name)
    return gene_sets


# ── 評分方法 ──────────────────────────────────────────────────────────────────


def zscore_aggregate(
    expr: pd.DataFrame,
    gene_sets: dict[str, list[str]],
) -> pd.DataFrame:
    """Z-score 聚合法：對基因集內基因的 Z-score 取平均。

    快速、適合時序探索與視覺化；不輸出統計顯著性。

    Parameters
    ----------
    expr:
        Gene × Sample（或 Gene × Timepoint）矩陣，值為任意尺度。
    gene_sets:
        {pathway: [gene, ...]} 字典。

    Returns
    -------
    Pathway × Sample 評分 DataFrame。
    """
    z = expr.subtract(expr.mean(axis=1), axis=0).divide(expr.std(axis=1).replace(0, np.nan), axis=0)

    scores: dict[str, pd.Series] = {}
    for pathway, genes in gene_sets.items():
        overlap = [g for g in genes if g in z.index]
        if not overlap:
            logger.warning("路徑 %s：基因集無交集，跳過", pathway)
            continue
        scores[pathway] = z.loc[overlap].mean(axis=0)
        logger.debug("路徑 %s：%d/%d 基因命中", pathway, len(overlap), len(genes))

    result = pd.DataFrame(scores).T  # Pathway × Sample
    logger.info("Z-score 聚合完成：%d 路徑 × %d 樣本", *result.shape)
    return result


def ssgsea_score(
    expr: pd.DataFrame,
    gene_sets: dict[str, list[str]],
    alpha: float = 0.25,
) -> pd.DataFrame:
    """ssGSEA（single-sample GSEA）AUC 評分法。

    對每個樣本獨立計算路徑富集分數，適合跨樣本比較與統計報告。

    Parameters
    ----------
    expr:
        Gene × Sample 矩陣（counts、TPM 或 log2 均可）。
    gene_sets:
        {pathway: [gene, ...]} 字典。
    alpha:
        加權指數（0 = 無加權；0.25 = ssGSEA 預設）。

    Returns
    -------
    Pathway × Sample 評分 DataFrame。
    """
    n_genes = expr.shape[0]
    scores: dict[str, dict[str, float]] = {pw: {} for pw in gene_sets}

    for sample in expr.columns:
        col = expr[sample].dropna()
        ranked = col.rank(ascending=False, method="average")
        rank_arr = ranked.values
        gene_idx = {g: i for i, g in enumerate(col.index)}

        for pathway, genes in gene_sets.items():
            hit_idx = [gene_idx[g] for g in genes if g in gene_idx]
            if not hit_idx:
                scores[pathway][sample] = np.nan
                continue

            hit_set = set(hit_idx)
            n_miss = n_genes - len(hit_set)

            hit_weights = np.array(
                [rank_arr[i] ** alpha if i in hit_set else 0.0 for i in range(n_genes)]
            )
            miss_weights = np.array([0.0 if i in hit_set else 1.0 for i in range(n_genes)])

            hit_norm = hit_weights.sum() or 1.0
            miss_norm = n_miss or 1.0

            cumsum_hit = np.cumsum(hit_weights) / hit_norm
            cumsum_miss = np.cumsum(miss_weights) / miss_norm
            scores[pathway][sample] = float((cumsum_hit - cumsum_miss).sum())

    result = pd.DataFrame(scores).T  # Pathway × Sample
    logger.info("ssGSEA 評分完成：%d 路徑 × %d 樣本", *result.shape)
    return result


# ── 整合入口 ──────────────────────────────────────────────────────────────────


@register_tool_on_import(
    tool_name="bio_run_pathway_scoring",
    version="1.0.0",
    description="執行 ssGSEA 或 Z-score 對基因表現矩陣進行路徑活性評分",
)
def score_pathways(
    expr: pd.DataFrame,
    gene_sets_path: Optional[Path] = None,
    method: str = "zscore",
    output_dir: Optional[Path] = None,
    label: str = "",
) -> pd.DataFrame:
    """整合入口：載入基因集 → 評分 → 選擇性存檔。

    Parameters
    ----------
    expr:
        Gene × Sample（或 Gene × Timepoint）表現矩陣。
    gene_sets_path:
        YAML 路徑；None 使用預設 hair_follicle.yaml。
    method:
        "zscore"（快速探索）或 "ssgsea"（統計報告）。
    output_dir:
        若指定，將評分矩陣存為 TSV。
    label:
        輸出檔名標籤（如 "Hair_germ_timeseries"）。

    Returns
    -------
    Pathway × Sample 評分 DataFrame。
    """
    # TODO H11: write to analysis_history after analysis completes
    gene_sets = load_gene_sets(gene_sets_path)

    if method == "zscore":
        scores = zscore_aggregate(expr, gene_sets)
    elif method == "ssgsea":
        scores = ssgsea_score(expr, gene_sets)
    else:
        raise ValueError(f"未知評分方法：{method!r}，請選擇 'zscore' 或 'ssgsea'")

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{label}" if label else ""
        out_path = output_dir / f"pathway_scores_{method}{suffix}.tsv"
        scores.to_csv(out_path, sep="\t")
        logger.info("路徑評分已儲存至 %s", out_path)

    return scores
