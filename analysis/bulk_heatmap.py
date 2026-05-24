"""Bulk RNA-seq 熱圖（顯著基因 + top 變異基因）。

對齊參考實作 ddmanyes/bulk-rnaseq-pipeline：
    - Heatmap_Significant_Genes：union DEG 顯著基因，z-score 後 sns.clustermap
    - Heatmap_Top50_Variable_Genes：跨樣本 variance top N，z-score 後 sns.clustermap

主要對外函數：
    run_bulk_heatmaps(sample_id, counts_path, deg_tables, ...) → (analysis_id, report_path)
        產出兩張 PNG + Markdown 報告，登記到 analysis_history (analysis_type='bulk_heatmap')。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import duckdb
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.db_utils import safe_write
from config.settings import DUCKDB_PATH
from analysis.path_utils import results_dir
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md
from analysis.tool_registry import register_tool_on_import

logger = logging.getLogger(__name__)

from analysis.validators import validate_sample_id


# ── 純畫圖（無 DB I/O，供測試與獨立使用）──────────────────────────────────


def deg_heatmap(
    counts: pd.DataFrame,
    sig_genes: Sequence[str],
    *,
    output_path: Path,
    normalize: bool = True,
    title: str = "Significant genes (z-score)",
    figsize: tuple[float, float] = (8.0, 8.0),
    cmap: str = "RdBu_r",
) -> Optional[Path]:
    """顯著基因 z-score heatmap（行：基因，欄：樣本，含階層聚類）。

    Args:
        counts:    gene × sample 計數矩陣
        sig_genes: 要畫的基因清單（會自動取與 counts.index 的交集）
        normalize: True → log1p 後 row z-score；False → 直接用原值
    """
    overlap = [g for g in sig_genes if g in counts.index]
    if not overlap:
        logger.warning("deg_heatmap：sig_genes 與 counts 無交集，跳過")
        return None
    sub = counts.loc[overlap].astype(float)
    if normalize:
        sub = np.log1p(sub)
        # row z-score；std=0 的列用 0 取代避免 NaN
        mean = sub.mean(axis=1)
        std = sub.std(axis=1).replace(0, 1.0)
        sub = sub.sub(mean, axis=0).div(std, axis=0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    g = sns.clustermap(
        sub,
        cmap=cmap,
        figsize=figsize,
        center=0,
        xticklabels=True,
        yticklabels=(len(overlap) <= 60),
        cbar_kws={"label": "z-score" if normalize else "value"},
    )
    g.figure.suptitle(f"{title}  (n={len(overlap)})", y=1.02)
    g.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(g.figure)
    return output_path


def top_var_heatmap(
    counts: pd.DataFrame,
    *,
    output_path: Path,
    top_n: int = 50,
    normalize: bool = True,
    title: str = "Top variable genes (z-score)",
    figsize: tuple[float, float] = (8.0, 9.0),
    cmap: str = "RdBu_r",
) -> Optional[Path]:
    """跨樣本 variance top-N 基因熱圖。"""
    if counts.empty:
        return None
    log_counts = np.log1p(counts.astype(float))
    var = log_counts.var(axis=1).sort_values(ascending=False)
    top = var.head(top_n).index
    sub = log_counts.loc[top]
    if normalize:
        mean = sub.mean(axis=1)
        std = sub.std(axis=1).replace(0, 1.0)
        sub = sub.sub(mean, axis=0).div(std, axis=0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    g = sns.clustermap(
        sub,
        cmap=cmap,
        figsize=figsize,
        center=0,
        xticklabels=True,
        yticklabels=(top_n <= 60),
        cbar_kws={"label": "z-score" if normalize else "log1p"},
    )
    g.figure.suptitle(f"{title}  (top {top_n})", y=1.02)
    g.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(g.figure)
    return output_path


# ── 主流程：讀 counts + 多張 DEG 表 → 兩張 heatmap ──────────────────────────


def collect_sig_genes(
    deg_tables: Sequence[Path],
    *,
    fc_col: str = "log2FC",
    pval_col: str = "qvalue",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
) -> list[str]:
    """合併多張 DEG 表，取所有對照 union 的顯著基因（依 |log2FC| + qvalue）。"""
    all_sig: set[str] = set()
    for p in deg_tables:
        path = Path(p)
        if not path.exists():
            logger.warning("collect_sig_genes：跳過不存在的 %s", path)
            continue
        df = pd.read_csv(path, index_col=0)
        if {fc_col, pval_col} - set(df.columns):
            logger.warning("%s 缺欄位 (%s, %s)，跳過", path.name, fc_col, pval_col)
            continue
        sig = df[(df[fc_col].abs() > fc_threshold) & (df[pval_col] < pval_threshold)]
        all_sig.update(sig.index.astype(str))
    return sorted(all_sig)


_REPORT_TEMPLATE = """# Bulk Heatmap 報告

- **樣本登記 ID**：{sample_id}
- **執行時間**：{timestamp}
- **顯著基因數**：{n_sig}（union of {n_deg} DEG 表）
- **Top variable**：top {top_n}

## 顯著基因熱圖

{sig_fig}

## Top 變異基因熱圖

{var_fig}
"""


@register_tool_on_import(
    tool_name="bio_run_heatmaps",
    version="1.0.0",
    description="基於 DEG 顯著基因 Union 繪製全樣本表達量熱圖"
)
def run_bulk_heatmaps(
    sample_id: str,
    *,
    counts_path: Path,
    deg_tables: Sequence[Path],
    top_n: int = 50,
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
    requested_by: str = "agent",
    con: Optional[duckdb.DuckDBPyConnection] = None,
) -> tuple[str, str]:
    """產出顯著基因 heatmap + top variable heatmap，寫入 analysis_history。"""
    validate_sample_id(sample_id)
    counts_path = Path(counts_path)

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps(
        {
            "counts_path": str(counts_path),
            "deg_tables": [str(p) for p in deg_tables],
            "top_n": top_n,
            "fc_threshold": fc_threshold,
            "pval_threshold": pval_threshold,
        }
    )

    _own_con = con is None
    if con is None:
        con = duckdb.connect(str(DUCKDB_PATH))

    try:
        safe_write(
            con,
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    requested_by, started_at)
               VALUES (?, ?, 'bulk_heatmap', ?, 'running', ?, ?)""",
            [analysis_id, sample_id, params_json, requested_by, started_at],
        )

        counts = pd.read_csv(counts_path, index_col=0)
        sig_genes = collect_sig_genes(
            deg_tables,
            fc_threshold=fc_threshold,
            pval_threshold=pval_threshold,
        )

        out_dir = results_dir(sample_id, "bulk_heatmap")
        ts = started_at.strftime("%Y%m%d_%H%M%S")
        sig_png = out_dir / f"Heatmap_Significant_Genes_{ts}.png"
        var_png = out_dir / f"Heatmap_Top{top_n}_Variable_Genes_{ts}.png"

        sig_file = deg_heatmap(counts, sig_genes, output_path=sig_png) if sig_genes else None
        var_file = top_var_heatmap(counts, output_path=var_png, top_n=top_n)

        sig_fig_md = (
            _file_to_b64_md(sig_file, "顯著基因熱圖") if sig_file else "（無顯著基因 → 跳過）"
        )
        var_fig_md = (
            _file_to_b64_md(var_file, f"Top {top_n} 變異基因熱圖")
            if var_file
            else "（counts 為空 → 跳過）"
        )

        report_path = out_dir / f"bulk_heatmap_{sample_id}_{ts}.md"
        report_path.write_text(
            _REPORT_TEMPLATE.format(
                sample_id=sample_id,
                timestamp=started_at.isoformat(),
                n_sig=len(sig_genes),
                n_deg=len(deg_tables),
                top_n=top_n,
                sig_fig=sig_fig_md,
                var_fig=var_fig_md,
            ),
            encoding="utf-8",
        )

        summary = (f"Bulk Heatmap {sample_id}：{len(sig_genes)} 顯著基因 + top {top_n} 變異基因。")[
            :80
        ]

        completed_at = datetime.now(timezone.utc)
        safe_write(
            con,
            """UPDATE analysis_history
                  SET status='completed', result_path=?, completed_at=?, summary=?
                WHERE analysis_id=?""",
            [str(report_path), completed_at, summary, analysis_id],
        )
        from analysis.failure_diagnosis import success_diagnosis, write_diagnosis
        write_diagnosis(con, analysis_id, success_diagnosis())
        try:
            from analysis.artifact_registry import register_artifact

            artifact_files: list[tuple[Path, str, str, str]] = []
            if sig_file:
                artifact_files.append((sig_file, "figure", "顯著基因熱圖", "heatmap_sig"))
            if var_file:
                artifact_files.append(
                    (var_file, "figure", f"Top {top_n} 變異基因熱圖", "heatmap_var")
                )
            artifact_files.append((report_path, "report", "Bulk Heatmap 報告", "heatmap_report"))
            for path, atype, label, subtype in artifact_files:
                if path.exists():
                    register_artifact(
                        con, analysis_id, path, atype, label, artifact_subtype=subtype
                    )
        except Exception as _exc:
            logger.warning("bulk_heatmap: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc_outer:
        logger.exception("bulk_heatmap 失敗  analysis_id=%s", analysis_id)
        from analysis.failure_diagnosis import classify_exception, write_diagnosis
        try:
            safe_write(
                con,
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
            write_diagnosis(con, analysis_id, classify_exception(_exc_outer))
        finally:
            if _own_con:
                con.close()
        raise

    if _own_con:
        con.close()
    logger.info("bulk_heatmap 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
