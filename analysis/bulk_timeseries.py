"""
Bulk RNA-seq 時間序列分析。

主要函數：
    parse_timepoint_cols()   — 從樣本欄位名稱解析時間點標籤
    mean_by_timepoint()      — 各時間點跨 replicate 均值
    log2fc()                 — log2 fold change（相對 baseline 時間點）
    tpm_to_log2()            — log2(TPM + 1) 正規化
    timeseries_summary()     — 整合摘要：均值矩陣 + FC 矩陣
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 欄名格式：{condition}_{replicate}_{tissue}
# 例如 ctrl_1_Hair_germ → 0h；pw6hr_2_lower_bulge → 6h
_TIMEPOINT_RE = re.compile(
    r"^(?:ctrl|pw(?P<hrs>\d+)hr)_\d+_(?P<tissue>.+)$"
)
_CTRL_ALIAS = "0h"


def parse_timepoint_cols(
    columns: list[str],
    tissue: Optional[str] = None,
) -> dict[str, list[str]]:
    """解析樣本欄名，回傳 {時間點標籤: [欄名列表]}。

    Parameters
    ----------
    columns:
        DataFrame 的欄名列表（樣本名）。
    tissue:
        若指定（如 "Hair_germ"），只保留該組織的欄。
        None 表示保留所有組織。
    """
    tp_map: dict[str, list[str]] = {}
    for col in columns:
        m = _TIMEPOINT_RE.match(col)
        if not m:
            continue
        if tissue and m.group("tissue") != tissue:
            continue
        hrs = m.group("hrs")
        label = _CTRL_ALIAS if hrs is None else f"{hrs}h"
        tp_map.setdefault(label, []).append(col)

    def _sort_key(tp: str) -> int:
        return 0 if tp == _CTRL_ALIAS else int(tp.rstrip("h"))

    return dict(sorted(tp_map.items(), key=lambda x: _sort_key(x[0])))


def mean_by_timepoint(
    counts: pd.DataFrame,
    tp_map: Optional[dict[str, list[str]]] = None,
    tissue: Optional[str] = None,
) -> pd.DataFrame:
    """計算各時間點的跨 replicate 均值。

    Parameters
    ----------
    counts:
        Gene × Sample 矩陣（index = gene，columns = samples）。
    tp_map:
        由 parse_timepoint_cols() 產生的對應表。None 時自動解析。
    tissue:
        若指定，只計算該組織的時間點均值。

    Returns
    -------
    Gene × Timepoint 均值 DataFrame。
    """
    if tp_map is None:
        tp_map = parse_timepoint_cols(list(counts.columns), tissue=tissue)

    result: dict[str, pd.Series] = {}
    for tp, cols in tp_map.items():
        valid = [c for c in cols if c in counts.columns]
        if not valid:
            logger.warning("時間點 %s 無有效欄位，跳過", tp)
            continue
        result[tp] = counts[valid].mean(axis=1)

    return pd.DataFrame(result, index=counts.index)


def tpm_to_log2(tpm: pd.DataFrame) -> pd.DataFrame:
    """log2(TPM + 1) 正規化，與 Protein log2 intensity 使用相同底數。"""
    return np.log2(tpm + 1)


def log2fc(
    expr: pd.DataFrame,
    baseline: str = "0h",
    log_transformed: bool = False,
) -> pd.DataFrame:
    """計算相對 baseline 時間點的 log2 fold change。

    Parameters
    ----------
    expr:
        Gene × Timepoint 均值矩陣（counts 或 TPM）。
    baseline:
        基準時間點欄名（預設 "0h"）。
    log_transformed:
        True 表示 expr 已為 log2 值（差值即 log2FC）；
        False 表示 expr 為原始值（先做 log2(x+1) 再差值）。
    """
    if baseline not in expr.columns:
        raise ValueError(
            f"baseline 時間點 {baseline!r} 不在欄位中：{list(expr.columns)}"
        )
    mat = expr if log_transformed else np.log2(expr + 1)
    fc = mat.subtract(mat[baseline], axis=0)
    logger.info(
        "log2FC：%d 基因 × %d 時間點，範圍 %.2f ~ %.2f",
        fc.shape[0], fc.shape[1], fc.values.min(), fc.values.max(),
    )
    return fc


def timeseries_summary(
    counts: pd.DataFrame,
    tissue: Optional[str] = None,
    baseline: str = "0h",
    output_dir: Optional[Path] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """整合摘要：回傳 (log2_mean, log2fc) 並選擇性存檔。

    Parameters
    ----------
    counts:
        原始 Gene × Sample count 矩陣。
    tissue:
        只分析指定組織（如 "Hair_germ"）；None 表示全部。
    baseline:
        FC 基準時間點。
    output_dir:
        若指定，將兩個矩陣存為 TSV。

    Returns
    -------
    (log2_mean_per_tp, log2fc_per_tp)
    """
    tp_map = parse_timepoint_cols(list(counts.columns), tissue=tissue)
    mean_mat = mean_by_timepoint(counts, tp_map)
    log2_mean = tpm_to_log2(mean_mat)
    fc_mat = log2fc(log2_mean, baseline=baseline, log_transformed=True)

    logger.info(
        "時序摘要：tissue=%s  %d 時間點  %d 基因",
        tissue or "all", len(tp_map), counts.shape[0],
    )

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{tissue}" if tissue else ""
        log2_mean.to_csv(output_dir / f"log2_mean{suffix}.tsv", sep="\t")
        fc_mat.to_csv(output_dir / f"log2fc{suffix}.tsv", sep="\t")
        logger.info("矩陣已儲存至 %s", output_dir)

    return log2_mean, fc_mat
