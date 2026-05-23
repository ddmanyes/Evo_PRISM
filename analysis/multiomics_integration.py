"""
RNA-Protein 多組學時序整合。

主要函數：
    load_proteome()           — 載入 Perseus log2 intensity 蛋白質矩陣
    align_rna_protein()       — 找交集基因、對齊共有時間點
    rna_protein_correlation() — 計算 Spearman 相關（基因層級）
    lag_analysis()            — 偵測 RNA→Protein 時間滯後
    run_integration()         — 整合入口：執行完整流程並存檔

蛋白質數據格式（sHG_log2intensity_0804.csv）：
    欄位：0_1, 0_2, ..., 96_4（時間點_rep），Protein name，T: Gene name，...
    值：Perseus log2 intensity（已標準化）
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT
from analysis.bulk_timeseries import mean_by_timepoint, parse_timepoint_cols, tpm_to_log2
from analysis.pathway_scoring import score_pathways
from analysis.tool_registry import register_tool_on_import

logger = logging.getLogger(__name__)

PROTEOME_DEFAULT = BIO_DB_ROOT / "proteome_data" / "sHG_timeseries" / "sHG_log2intensity_0804.csv"
RESULTS_DIR = BIO_DB_ROOT / "results" / "multiomics"

_PROT_COL_RE = re.compile(r"^(?P<tp>\d+)_(?P<rep>\d+)$")


# ── 蛋白質數據載入 ────────────────────────────────────────────────────────────


def load_proteome(
    path: Optional[Path] = None,
    gene_col: str = "T: Gene name",
) -> pd.DataFrame:
    """載入 Perseus log2 intensity 矩陣，回傳 Gene × Sample DataFrame。

    Parameters
    ----------
    path:
        CSV 路徑；None 使用預設 sHG_log2intensity_0804.csv。
    gene_col:
        基因名稱欄位（Perseus 慣例為 "T: Gene name"）。

    Returns
    -------
    Gene × Sample DataFrame（index=gene symbol，columns="0_1","24_2"...）。
    重複基因名（isoform）取均值合併。
    """
    path = path or PROTEOME_DEFAULT
    if not path.exists():
        raise FileNotFoundError(f"找不到蛋白質數據：{path}")

    df = pd.read_csv(path, index_col=0)
    if gene_col not in df.columns:
        raise ValueError(f"找不到基因名稱欄位 {gene_col!r}，現有欄位：{list(df.columns)[:10]}")

    value_cols = [c for c in df.columns if _PROT_COL_RE.match(str(c))]
    gene_names = df[gene_col].astype(str).str.strip()
    valid = gene_names.notna() & (gene_names != "") & (gene_names != "nan")

    result = df.loc[valid, value_cols].copy()
    result.index = gene_names[valid]
    result = result.groupby(result.index).mean()

    logger.info(
        "載入蛋白質矩陣：%d 蛋白 × %d 樣本（來源：%s）",
        result.shape[0],
        result.shape[1],
        path.name,
    )
    return result


def _parse_protein_timepoints(columns: list[str]) -> dict[str, list[str]]:
    """解析蛋白質欄名 → {時間點標籤: [欄名列表]}，如 {"0h": ["0_1","0_2"]}。"""
    tp_map: dict[str, list[str]] = {}
    for col in columns:
        m = _PROT_COL_RE.match(str(col))
        if not m:
            continue
        label = f"{m.group('tp')}h"
        tp_map.setdefault(label, []).append(col)
    return dict(sorted(tp_map.items(), key=lambda x: int(x[0].rstrip("h"))))


# ── 對齊 RNA / Protein ───────────────────────────────────────────────────────


def align_rna_protein(
    rna_counts: pd.DataFrame,
    prot_df: pd.DataFrame,
    rna_tissue: str = "Hair_germ",
    overlap_timepoints: Optional[list[str]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """找交集基因，計算各時間點均值，對齊共有時間點。

    Returns
    -------
    (rna_mean, prot_mean, overlap_genes)
        rna_mean:      Gene × Timepoint（log2(TPM+1)）
        prot_mean:     Gene × Timepoint（log2 intensity）
        overlap_genes: 交集基因名稱列表
    """
    rna_tp_map = parse_timepoint_cols(list(rna_counts.columns), tissue=rna_tissue)
    rna_mean = tpm_to_log2(mean_by_timepoint(rna_counts, rna_tp_map))

    prot_tp_map = _parse_protein_timepoints(list(prot_df.columns))
    prot_mean = pd.DataFrame(
        {
            tp: prot_df[[c for c in cols if c in prot_df.columns]].mean(axis=1)
            for tp, cols in prot_tp_map.items()
        },
        index=prot_df.index,
    )

    overlap_genes = sorted(set(rna_mean.index) & set(prot_mean.index))
    logger.info(
        "交集基因：%d（RNA %d，Protein %d）",
        len(overlap_genes),
        len(rna_mean),
        len(prot_mean),
    )

    if overlap_timepoints is None:
        overlap_timepoints = sorted(
            set(rna_mean.columns) & set(prot_mean.columns),
            key=lambda tp: int(tp.rstrip("h")),
        )
    logger.info("共有時間點：%s", overlap_timepoints)

    return (
        rna_mean.loc[overlap_genes, overlap_timepoints],
        prot_mean.loc[overlap_genes, overlap_timepoints],
        overlap_genes,
    )


# ── 相關與滯後分析 ────────────────────────────────────────────────────────────


def rna_protein_correlation(
    rna_mean: pd.DataFrame,
    prot_mean: pd.DataFrame,
) -> pd.DataFrame:
    """計算每個基因的 RNA-Protein Spearman 相關（跨時間點）。

    Returns
    -------
    DataFrame，index=gene，欄位：spearman_r、p_value、n_timepoints。
    """
    records = []
    for gene in rna_mean.index:
        rna_vals = rna_mean.loc[gene].values
        prot_vals = prot_mean.loc[gene].values
        mask = ~(np.isnan(rna_vals) | np.isnan(prot_vals))
        n = int(mask.sum())
        if n < 3:
            records.append(
                {"gene": gene, "spearman_r": np.nan, "p_value": np.nan, "n_timepoints": n}
            )
            continue
        r, p = spearmanr(rna_vals[mask], prot_vals[mask])
        records.append(
            {"gene": gene, "spearman_r": float(r), "p_value": float(p), "n_timepoints": n}
        )

    result = pd.DataFrame(records).set_index("gene")
    logger.info(
        "Spearman 相關完成：%d 基因，中位數 r=%.3f",
        len(result),
        result["spearman_r"].median(),
    )
    return result


def lag_analysis(
    rna_mean: pd.DataFrame,
    prot_mean: pd.DataFrame,
    timepoints: list[str],
) -> pd.DataFrame:
    """偵測 RNA→Protein 時間滯後：比較各基因 RNA/Protein 峰值時間點。

    Returns
    -------
    DataFrame，index=gene，欄位：rna_peak_h、prot_peak_h、lag_h（整數小時）。
    """
    tp_vals = [int(tp.rstrip("h")) for tp in timepoints]
    records = []
    for gene in rna_mean.index:
        rna_vals = rna_mean.loc[gene, timepoints].values
        prot_vals = prot_mean.loc[gene, timepoints].values
        if np.all(np.isnan(rna_vals)) or np.all(np.isnan(prot_vals)):
            continue
        rna_peak = tp_vals[int(np.nanargmax(rna_vals))]
        prot_peak = tp_vals[int(np.nanargmax(prot_vals))]
        records.append(
            {
                "gene": gene,
                "rna_peak_h": rna_peak,
                "prot_peak_h": prot_peak,
                "lag_h": prot_peak - rna_peak,
            }
        )

    result = pd.DataFrame(records).set_index("gene")
    if len(result):
        logger.info("滯後分析完成：%s", dict(result["lag_h"].value_counts().sort_index()))
    return result


# ── 整合入口 ──────────────────────────────────────────────────────────────────


@register_tool_on_import(
    tool_name="bio_run_multiomics_integration",
    version="1.0.0",
    description="執行 RNA-Protein 多組學時序整合分析與相關性滯後分析"
)
def run_integration(
    rna_counts: pd.DataFrame,
    proteome_path: Optional[Path] = None,
    rna_tissue: str = "Hair_germ",
    gene_sets_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> dict[str, pd.DataFrame]:
    """執行完整 RNA-Protein 整合流程並存檔。

    Returns
    -------
    dict，keys: rna_mean, prot_mean, correlation, lag, pathway_rna, pathway_prot。
    """
    out_dir = Path(output_dir) if output_dir else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    prot_df = load_proteome(proteome_path)
    rna_mean, prot_mean, _ = align_rna_protein(rna_counts, prot_df, rna_tissue=rna_tissue)
    timepoints = list(rna_mean.columns)

    corr = rna_protein_correlation(rna_mean, prot_mean)
    lag = lag_analysis(rna_mean, prot_mean, timepoints)

    pw_rna = score_pathways(
        rna_mean,
        gene_sets_path,
        method="zscore",
        output_dir=out_dir,
        label=f"rna_{rna_tissue}",
    )
    pw_prot = score_pathways(
        prot_mean,
        gene_sets_path,
        method="zscore",
        output_dir=out_dir,
        label=f"prot_{rna_tissue}",
    )

    rna_mean.to_csv(out_dir / f"rna_mean_{rna_tissue}.tsv", sep="\t")
    prot_mean.to_csv(out_dir / f"prot_mean_{rna_tissue}.tsv", sep="\t")
    corr.to_csv(out_dir / f"rna_prot_correlation_{rna_tissue}.tsv", sep="\t")
    lag.to_csv(out_dir / f"lag_analysis_{rna_tissue}.tsv", sep="\t")
    logger.info("整合結果已儲存至 %s", out_dir)

    return {
        "rna_mean": rna_mean,
        "prot_mean": prot_mean,
        "correlation": corr,
        "lag": lag,
        "pathway_rna": pw_rna,
        "pathway_prot": pw_prot,
    }
