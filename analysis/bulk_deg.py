"""Bulk RNA-seq 差異表達分析（DEG）+ 火山圖。

對齊參考實作 ddmanyes/bulk-rnaseq-pipeline：
    - DESeq2 統計透過 ``omicverse.bulk.pyDEG`` 呼叫
    - 火山圖以 matplotlib + adjustText 手繪（與參考 pipeline 一致）

主要對外函數：
    run_deg_analysis(sample_id, counts_path, coldata_path, comparisons, ...)
        → (analysis_id, report_path)
        每組對照產出 DEG_<a>_vs_<b>.csv + Volcano_<a>_vs_<b>.png，
        並登記到 analysis_history (analysis_type='bulk_deg') + analysis_artifacts。

設計取捨：
    omicverse / adjustText 都是重套件，採延遲匯入；測試以 monkeypatch 取代真實 DESeq2 呼叫
    （DESeq2 對 84 樣本實跑數分鐘，不適合放 CI）。
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

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.db_utils import connect_db
from store.factory import get_store
from config.settings import BIO_DB_ROOT, DUCKDB_PATH, SUMMARY_MAX_CHARS
from analysis.path_utils import results_dir
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md
from analysis.tool_registry import register_tool_on_import

logger = logging.getLogger(__name__)

from analysis.validators import validate_sample_id

_GROUP_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_group(name: str) -> None:
    if not _GROUP_RE.match(name):
        raise ValueError(f"無效的 group 名稱：{name!r}")


# ── 資料載入 ─────────────────────────────────────────────────────────────────


def load_deg_inputs(
    counts_path: Path,
    coldata_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """載入 DESeq2 風格的 counts.csv + coldata.tsv。

    counts: gene index、樣本為欄；coldata: sample index、`group` 欄位必填。
    回傳 (counts, coldata)，已對齊樣本順序。
    """
    counts = pd.read_csv(counts_path, index_col=0)
    coldata = (
        pd.read_csv(coldata_path, sep="\t", index_col=0)
        if coldata_path.suffix in {".tsv", ".txt"}
        else pd.read_csv(coldata_path, index_col=0)
    )
    if "group" not in coldata.columns:
        raise ValueError(f"coldata 缺 'group' 欄：{coldata_path}")
    shared = [s for s in counts.columns if s in coldata.index]
    if not shared:
        raise ValueError("counts 與 coldata 樣本完全不重疊")
    return counts[shared], coldata.loc[shared]


# ── 低表達基因過濾 ────────────────────────────────────────────────────────────


def filter_low_expression(
    counts: pd.DataFrame,
    *,
    min_cpm: float = 1.0,
    min_samples: int | None = None,
) -> pd.DataFrame:
    """移除低表達基因（CPM < min_cpm 在多數樣本中）。

    保留條件：至少 min_samples 個樣本的 CPM >= min_cpm。
    min_samples 預設為樣本數的 1/4（至少 2），確保即使只有少數 replicate 也能保留有效基因。
    """
    n_samples = counts.shape[1]
    if min_samples is None:
        min_samples = max(2, n_samples // 4)

    lib_sizes = counts.sum(axis=0)
    # 避免除以零
    lib_sizes = lib_sizes.replace(0, 1)
    cpm = counts.divide(lib_sizes, axis=1) * 1e6
    keep = (cpm >= min_cpm).sum(axis=1) >= min_samples
    n_before = len(counts)
    filtered = counts.loc[keep]
    n_after = len(filtered)
    logger.info(
        "低表達過濾：%d → %d 基因（移除 %d，min_cpm=%.1f, min_samples=%d）",
        n_before, n_after, n_before - n_after, min_cpm, min_samples,
    )
    return filtered


# ── DEG（pyDEG/DESeq2 wrapper）──────────────────────────────────────────────


def deg_single_comparison(
    counts: pd.DataFrame,
    coldata: pd.DataFrame,
    group_a: str,
    group_b: str,
    *,
    method: str = "DEseq2",
    alpha: float = 0.05,
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
) -> pd.DataFrame:
    """跑單組對照的 DEG（A vs B；A 為 treat，B 為 ctrl）。

    回傳 omicverse DEG DataFrame，含 log2FC / qvalue / sig（up/down/normal）欄。
    """
    _validate_group(group_a)
    _validate_group(group_b)

    if "group" not in coldata.columns:
        raise ValueError("coldata 缺 'group' 欄")
    a_samples = coldata.index[coldata["group"] == group_a].tolist()
    b_samples = coldata.index[coldata["group"] == group_b].tolist()
    if not a_samples or not b_samples:
        raise ValueError(
            f"找不到對照樣本：group_a={group_a!r}({len(a_samples)}) "
            f"group_b={group_b!r}({len(b_samples)})"
        )

    # 延遲匯入 omicverse（重套件、~10s 載入）
    import omicverse as ov

    dds = ov.bulk.pyDEG(counts)
    dds.drop_duplicates_index()
    res = dds.deg_analysis(a_samples, b_samples, method=method, alpha=alpha)
    dds.foldchange_set(fc_threshold=fc_threshold, pval_threshold=pval_threshold)
    return res


# ── 火山圖（手繪 matplotlib + adjustText）─────────────────────────────────────


def volcano_plot(
    deg: pd.DataFrame,
    *,
    output_path: Path,
    title: str = "",
    fc_col: str = "log2FC",
    pval_col: str = "qvalue",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
    top_n_labels: int = 10,
    figsize: tuple[float, float] = (5.5, 5.0),
) -> Path:
    """為一張 DEG 表畫火山圖，並存檔。

    對齊參考 pipeline 的火山圖風格：
      - 上調 #e25d5d / 下調 #7388c1 / ns #d7d7d7
      - 加 fc/pval 閾值虛線；以 adjustText 標 top_n 顯著基因
    """
    required = {fc_col, pval_col}
    if missing := required - set(deg.columns):
        raise ValueError(f"deg 缺少欄位：{sorted(missing)}")
    df = deg.copy()
    df["-log10p"] = -np.log10(df[pval_col].clip(lower=1e-300))
    up = (df[fc_col] > fc_threshold) & (df[pval_col] < pval_threshold)
    dn = (df[fc_col] < -fc_threshold) & (df[pval_col] < pval_threshold)
    colors = np.where(up, "#e25d5d", np.where(dn, "#7388c1", "#d7d7d7"))

    fig, ax = plt.subplots(figsize=figsize)
    ax.scatter(df[fc_col], df["-log10p"], c=colors, s=8, alpha=0.7, edgecolors="none")
    ax.axhline(-np.log10(pval_threshold), color="grey", ls="--", lw=0.8)
    ax.axvline(fc_threshold, color="grey", ls="--", lw=0.8)
    ax.axvline(-fc_threshold, color="grey", ls="--", lw=0.8)
    ax.set_xlabel(f"{fc_col}")
    ax.set_ylabel(f"-log10({pval_col})")
    ax.set_title(title or "Volcano")

    # Top-N labels via adjustText（缺套件時降級為前 N 直接 annotate）
    sig = df[up | dn].nlargest(top_n_labels, "-log10p")
    if not sig.empty:
        texts = [ax.text(r[fc_col], r["-log10p"], str(g), fontsize=8) for g, r in sig.iterrows()]
        try:
            from adjustText import adjust_text

            adjust_text(texts, ax=ax, arrowprops=dict(arrowstyle="-", color="grey", lw=0.4))
        except ImportError:
            logger.debug("adjustText 未安裝；跳過標籤避撞")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


# ── 報告生成輔助 ─────────────────────────────────────────────────────────────


def _caption_mv(counts: pd.DataFrame) -> str:
    try:
        if counts.empty:
            return "Mean-Variance 不可用"
        log_c = np.log1p(counts.values.astype(float))
        mean_e = log_c.mean(axis=1)
        var_e = log_c.var(axis=1)
        n_high = int(((mean_e < 2.0) & (var_e > 0.5)).sum())
        return f"低表達高 variance 基因（log1p mean<2，var>0.5）共 {n_high} 個"
    except Exception:
        return "Mean-Variance 不可用"


def _caption_volcano(
    deg: pd.DataFrame, comparison: str, fc_thr: float, pval_thr: float
) -> str:
    try:
        fc = deg.get("log2FC", pd.Series(dtype=float))
        q = deg.get("qvalue", pd.Series(dtype=float))
        up = int(((fc > fc_thr) & (q < pval_thr)).sum())
        dn = int(((fc < -fc_thr) & (q < pval_thr)).sum())
        ns = len(deg) - up - dn
        if len(q) > 0 and q.min() < pval_thr:
            top_row = deg[q == q.min()].iloc[[0]]  # iloc 避免重複 index 問題
            top_gene = top_row.index[0]
            fc_val = top_row["log2FC"].iloc[0] if "log2FC" in top_row.columns else float("nan")
            top_str = f"；最顯著 {top_gene}（FC={fc_val:.1f}，q={q.min():.1e}）"
        else:
            top_str = ""
        return f"{comparison}：up={up}，down={dn}，ns={ns}{top_str}"
    except Exception:
        return f"{comparison}：up/down 不可用"


def _caption_ma(
    deg: pd.DataFrame, comparison: str, fc_thr: float, pval_thr: float
) -> str:
    try:
        fc = deg.get("log2FC", pd.Series(dtype=float))
        q = deg.get("qvalue", pd.Series(dtype=float))
        up = int(((fc > fc_thr) & (q < pval_thr)).sum())
        dn = int(((fc < -fc_thr) & (q < pval_thr)).sum())
        sig = (fc.abs() > fc_thr) & (q < pval_thr)
        bm_col = next((c for c in ["BaseMean", "baseMean", "AveExpr"] if c in deg.columns), None)
        if bm_col and sig.any():
            bm = deg.loc[sig, bm_col]
            bm_str = f"；{bm_col} 範圍 {bm.min():.0f}–{bm.max():.0f}"
        else:
            bm_str = "；無 BaseMean 欄" if bm_col is None else ""
        return f"{comparison}：up={up}，down={dn}{bm_str}"
    except Exception:
        return f"{comparison}：MA caption 不可用"


def _version_block() -> str:
    import importlib as _il, sys as _sys
    pkgs = [("pandas", "pandas"), ("numpy", "numpy"), ("omicverse", "omicverse"),
            ("scipy", "scipy"), ("matplotlib", "matplotlib")]
    rows = [f"| Python | {_sys.version.split()[0]} |"]
    for label, pkg in pkgs:
        try:
            ver = getattr(_il.import_module(pkg), "__version__", "?")
        except ImportError:
            ver = "—"
        rows.append(f"| `{label}` | {ver} |")
    return "| 套件 | 版本 |\n| --- | --- |\n" + "\n".join(rows)


def mean_variance_plot(
    counts: pd.DataFrame,
    *,
    output_path: Path,
) -> Optional[Path]:
    """Mean-variance plot（log1p counts）— 近似 DESeq2 dispersion 診斷圖。"""
    if counts.empty:
        return None
    log_c = np.log1p(counts.astype(float))
    mean_expr = log_c.mean(axis=1)
    variance = log_c.var(axis=1)

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(mean_expr, variance, s=3, alpha=0.25, color="#4C72B0", edgecolors="none")
    ax.set_xlabel("log1p mean expression", fontsize=11)
    ax.set_ylabel("log1p variance", fontsize=11)
    ax.set_title("Mean-Variance (filtered counts)", fontsize=12)
    ax.grid(True, linestyle="--", alpha=0.3)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def ma_plot(
    deg: pd.DataFrame,
    *,
    output_path: Path,
    title: str = "",
    fc_col: str = "log2FC",
    pval_col: str = "qvalue",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
) -> Optional[Path]:
    """MA plot：x = log2 mean expression（BaseMean 優先，否則排序代理），y = log2FC。"""
    if fc_col not in deg.columns:
        return None
    df = deg.copy()
    for candidate in ("BaseMean", "baseMean", "AveExpr"):
        if candidate in df.columns:
            df["_x"] = np.log2(df[candidate].clip(lower=0.01))
            x_label = f"log2({candidate})"
            break
    else:
        df["_x"] = np.arange(len(df), dtype=float)
        x_label = "gene rank (no BaseMean found)"

    up = (df[fc_col] > fc_threshold) & (df[pval_col] < pval_threshold)
    dn = (df[fc_col] < -fc_threshold) & (df[pval_col] < pval_threshold)
    colors = np.where(up, "#e25d5d", np.where(dn, "#7388c1", "#cccccc"))

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(df["_x"], df[fc_col], c=colors, s=4, alpha=0.5, edgecolors="none")
    ax.axhline(0, color="black", lw=0.8)
    ax.axhline(fc_threshold, color="grey", ls="--", lw=0.6)
    ax.axhline(-fc_threshold, color="grey", ls="--", lw=0.6)
    ax.set_xlabel(x_label, fontsize=10)
    ax.set_ylabel(fc_col, fontsize=10)
    ax.set_title(f"MA plot: {title}" if title else "MA plot", fontsize=12)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _deg_threshold_summary(deg: pd.DataFrame, comparison: str) -> pd.DataFrame:
    """多閾值 DEG 統計表（lfc × padj 組合）。"""
    thresholds = [(0.5, 0.05), (1.0, 0.05), (1.5, 0.05), (2.0, 0.05), (1.0, 0.01)]
    fc = deg.get("log2FC", pd.Series(dtype=float))
    pv = deg.get("qvalue", pd.Series(dtype=float))
    rows = []
    for lfc_thr, padj_thr in thresholds:
        up = int(((fc > lfc_thr) & (pv < padj_thr)).sum())
        dn = int(((fc < -lfc_thr) & (pv < padj_thr)).sum())
        rows.append({
            "comparison": comparison,
            "|log2FC|>": lfc_thr,
            "padj<": padj_thr,
            "up": up,
            "down": dn,
            "total": up + dn,
        })
    return pd.DataFrame(rows)


# ── 主流程：多組對照 + 報告 ──────────────────────────────────────────────────

_REPORT_TEMPLATE = """# Bulk DEG 分析報告

| 欄位 | 值 |
| --- | --- |
| **analysis_id** | `{analysis_id}` |
| **樣本登記 ID** | {sample_id} |
| **執行時間** | {timestamp} |
| **方法** | {method}（pyDEG / omicverse 封裝） |
| **design formula** | `~ group`（對照組以 comparisons 指定） |
| **LFC shrinkage** | omicverse pyDEG 內建（詳見 omicverse 版本說明） |
| **對照清單** | {comparisons_str} |
| **閾值** | \\|log2FC\\| > {fc_thr}, qvalue < {pval_thr} |
| **對照組數** | {n_comparisons} |
| **基因數（過濾前）** | {n_genes_before:,} |
| **基因數（過濾後）** | {n_genes_after:,} |

{quality_warnings}

## 1. Mean-Variance 圖（過濾後 counts）

{mv_fig}

## 2. 結果摘要

{summary_table}

## 3. 多閾值 DEG 統計

{threshold_table}

## 4. 火山圖

{volcano_figs}

## 5. MA 圖

{ma_figs}

---

## 套件版本（reproducibility）

{version_block}

*由 Evo_PRISM analysis/bulk_deg.py 自動生成*
"""


def _assess_deg_flags(summary_df: pd.DataFrame) -> list[str]:
    """回傳 DEG 數量品質旗標清單。"""
    flags: list[str] = []
    for _, row in summary_df.iterrows():
        if row.get("status", "ok") != "ok":
            continue
        n_sig = int(row.get("n_sig_up", 0)) + int(row.get("n_sig_down", 0))
        cmp = row["comparison"]
        if n_sig > 10000:
            flags.append(f"excess_deg:{cmp}(n={n_sig},可能未過濾低表達基因)")
        elif n_sig > 5000:
            flags.append(f"high_deg:{cmp}(n={n_sig})")
        elif n_sig < 50:
            flags.append(f"few_deg:{cmp}(n={n_sig},組間差異弱或樣本數不足)")
    return flags


@register_tool_on_import(
    tool_name="bio_run_deg",
    version="1.0.0",
    description="對指定對照組別進行 bulk RNA-seq 差異表達分析 (DESeq2)",
)
def run_deg_analysis(
    sample_id: str,
    *,
    counts_path: Path,
    coldata_path: Path,
    comparisons: Sequence[tuple[str, str]],
    method: str = "DEseq2",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
    requested_by: str = "agent",
    parent_analysis_id: Optional[str] = None,
) -> tuple[str, str]:
    """跑多組對照的 DEG，產出每組 DEG CSV + 火山圖 + 彙整報告。

    成功後自動標記為 canonical，舊 canonical 降為 superseded。

    Args:
        sample_id:       已登記的樣本 ID
        counts_path:     gene × sample 計數矩陣（CSV）
        coldata_path:    sample × group 設計表（TSV 或 CSV，需 'group' 欄）
        comparisons:     [(group_a, group_b), ...] —— A vs B（A=treat, B=ctrl）
        method:          'DEseq2'（pyDEG 預設）/ 'ttest' / 'wilcox'（依 omicverse 版本支援）
        fc_threshold:    |log2FC| 顯著閾值
        pval_threshold:  qvalue 顯著閾值
        con:             可傳入既有連線供測試 monkeypatch；None 則開新連線

    Returns:
        (analysis_id, report_path)
    """
    validate_sample_id(sample_id)
    if not comparisons:
        raise ValueError("comparisons 不可為空")
    for a, b in comparisons:
        _validate_group(a)
        _validate_group(b)

    counts_path = Path(counts_path)
    coldata_path = Path(coldata_path)

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(BIO_DB_ROOT))
        except ValueError:
            return str(p)

    _params = {
        "counts_path": _rel(counts_path),
        "coldata_path": _rel(coldata_path),
        "comparisons": [list(c) for c in comparisons],
        "method": method,
        "fc_threshold": fc_threshold,
        "pval_threshold": pval_threshold,
    }
    params_json = json.dumps(_params)

    from config.db_utils import param_hash

    store = get_store()

    try:
        if parent_analysis_id is None:
            parent_analysis_id = store.get_canonical_id(sample_id, "bulk_deg")

        store.insert_history(
            analysis_id, sample_id, "bulk_deg", params_json, "running",
            requested_by, started_at,
            parameter_hash=param_hash(_params),
        )

        counts, coldata = load_deg_inputs(counts_path, coldata_path)
        n_genes_before = len(counts)
        counts = filter_low_expression(counts)
        n_genes_after = len(counts)
        out_dir = results_dir(sample_id, "bulk_deg")
        ts = started_at.strftime("%Y%m%d_%H%M%S")

        # Mean-Variance plot（對所有過濾後 counts 只畫一次）
        mv_png = out_dir / f"MeanVariance_{sample_id}_{ts}.png"
        artifact_files_pre: list[tuple[Path, str, str, str]] = []
        try:
            mv_file = mean_variance_plot(counts, output_path=mv_png)
            if mv_file:
                mv_fig = _file_to_b64_md(mv_file, "Mean-Variance")
                artifact_files_pre = [
                    (mv_png, "figure", f"Mean-Variance 圖 — {_caption_mv(counts)}", "mean_variance")
                ]
            else:
                mv_fig = "（counts 為空，跳過）"
        except Exception:
            logger.warning("MeanVariance plot 失敗，跳過", exc_info=True)
            mv_fig = "（生成失敗）"

        summary_rows: list[dict] = []
        volcano_md_parts: list[str] = []
        ma_md_parts: list[str] = []
        threshold_dfs: list[pd.DataFrame] = []
        artifact_files: list[tuple[Path, str, str, str]] = list(artifact_files_pre)
        # 結構：(path, artifact_type, label, subtype)

        for a, b in comparisons:
            try:
                deg = deg_single_comparison(
                    counts,
                    coldata,
                    a,
                    b,
                    method=method,
                    fc_threshold=fc_threshold,
                    pval_threshold=pval_threshold,
                )
            except Exception as exc:
                logger.warning("DEG %s_vs_%s 失敗：%s", a, b, exc, exc_info=True)
                summary_rows.append(
                    {
                        "comparison": f"{a}_vs_{b}",
                        "n_sig_up": 0,
                        "n_sig_down": 0,
                        "n_total": 0,
                        "status": f"failed: {type(exc).__name__}",
                    }
                )
                continue

            deg_csv = out_dir / f"DEG_{a}_vs_{b}_{ts}.csv"
            deg.to_csv(deg_csv)
            artifact_files.append((deg_csv, "csv", f"DEG {a} vs {b}", "deg_table"))

            # 火山圖
            volcano_png = out_dir / f"Volcano_{a}_vs_{b}_{ts}.png"
            try:
                volcano_plot(
                    deg,
                    output_path=volcano_png,
                    title=f"{a} vs {b}",
                    fc_threshold=fc_threshold,
                    pval_threshold=pval_threshold,
                )
                volcano_md_parts.append(_file_to_b64_md(volcano_png, f"Volcano {a} vs {b}"))
                artifact_files.append((
                    volcano_png, "figure",
                    f"火山圖 {a} vs {b} — {_caption_volcano(deg, f'{a}_vs_{b}', fc_threshold, pval_threshold)}",
                    "volcano",
                ))
            except Exception:
                logger.warning("火山圖 %s_vs_%s 失敗，跳過", a, b, exc_info=True)
                volcano_md_parts.append(f"\n（{a} vs {b} 火山圖生成失敗）\n")

            # MA 圖
            ma_png = out_dir / f"MA_{a}_vs_{b}_{ts}.png"
            try:
                ma_file = ma_plot(
                    deg,
                    output_path=ma_png,
                    title=f"{a} vs {b}",
                    fc_threshold=fc_threshold,
                    pval_threshold=pval_threshold,
                )
                if ma_file:
                    ma_md_parts.append(_file_to_b64_md(ma_png, f"MA {a} vs {b}"))
                    artifact_files.append((
                        ma_png, "figure",
                        f"MA 圖 {a} vs {b} — {_caption_ma(deg, f'{a}_vs_{b}', fc_threshold, pval_threshold)}",
                        "ma_plot",
                    ))
                else:
                    ma_md_parts.append(f"\n（{a} vs {b} MA 圖：缺 log2FC 欄）\n")
            except Exception:
                logger.warning("MA plot %s_vs_%s 失敗，跳過", a, b, exc_info=True)
                ma_md_parts.append(f"\n（{a} vs {b} MA 圖生成失敗）\n")

            # 多閾值統計
            threshold_dfs.append(_deg_threshold_summary(deg, f"{a}_vs_{b}"))

            up = (
                (deg.get("log2FC", pd.Series(dtype=float)) > fc_threshold)
                & (deg.get("qvalue", pd.Series(dtype=float)) < pval_threshold)
            ).sum()
            dn = (
                (deg.get("log2FC", pd.Series(dtype=float)) < -fc_threshold)
                & (deg.get("qvalue", pd.Series(dtype=float)) < pval_threshold)
            ).sum()
            summary_rows.append(
                {
                    "comparison": f"{a}_vs_{b}",
                    "n_sig_up": int(up),
                    "n_sig_down": int(dn),
                    "n_total": int(len(deg)),
                    "status": "ok",
                }
            )

        summary_df = pd.DataFrame(summary_rows)
        deg_quality_flags = _assess_deg_flags(summary_df)

        threshold_table = (
            pd.concat(threshold_dfs, ignore_index=True).to_markdown(index=False)
            if threshold_dfs
            else "（無 DEG 結果）"
        )

        if deg_quality_flags:
            warnings_md = (
                "## ⚠️ 品質警告\n\n"
                + "\n".join(f"- `{f}`" for f in deg_quality_flags)
                + "\n\n"
            )
        else:
            warnings_md = ""

        comparisons_str = ", ".join(f"{a} vs {b}" for a, b in comparisons)

        report_path = out_dir / f"bulk_deg_{sample_id}_{ts}.md"
        report_path.write_text(
            _REPORT_TEMPLATE.format(
                analysis_id=analysis_id,
                sample_id=sample_id,
                timestamp=started_at.isoformat(),
                method=method,
                comparisons_str=comparisons_str,
                fc_thr=fc_threshold,
                pval_thr=pval_threshold,
                n_comparisons=len(comparisons),
                n_genes_before=n_genes_before,
                n_genes_after=n_genes_after,
                quality_warnings=warnings_md,
                mv_fig=mv_fig,
                summary_table=summary_df.to_markdown(index=False),
                threshold_table=threshold_table,
                volcano_figs="\n".join(volcano_md_parts) or "（無火山圖）",
                ma_figs="\n".join(ma_md_parts) or "（無 MA 圖）",
                version_block=_version_block(),
            ),
            encoding="utf-8",
        )
        artifact_files.append((report_path, "report", "Bulk DEG 分析報告", "deg_report"))

        total_sig = int(summary_df[["n_sig_up", "n_sig_down"]].to_numpy().sum())
        n_cmp = len(comparisons)
        full_summary = (
            f"[n_cmp={n_cmp}|sig={total_sig}] "
            f"Bulk DEG {sample_id}：{n_cmp} 對照，共 {total_sig} 顯著基因。"
        )
        summary = full_summary[:SUMMARY_MAX_CHARS]
        summary_metrics = json.dumps({
            "n_comparisons": n_cmp,
            "n_sig_total": total_sig,
            "n_sig_up": int(summary_df["n_sig_up"].sum()),
            "n_sig_down": int(summary_df["n_sig_down"].sum()),
            "n_genes_before_filter": n_genes_before,
            "n_genes_after_filter": n_genes_after,
            "quality_flags": deg_quality_flags,
        })

        completed_at = datetime.now(timezone.utc)
        store.complete_history(
            analysis_id, str(report_path), summary, completed_at,
            summary_metrics=json.loads(summary_metrics),
        )
        store.mark_canonical(analysis_id, sample_id, "bulk_deg")

        from analysis.failure_diagnosis import success_diagnosis

        store.update_history(
            analysis_id, failure_diagnosis=json.dumps(success_diagnosis())
        )
        # register_artifact still uses DuckDB VSS/HNSW — not yet migrated to RegistryStore.
        try:
            from analysis.artifact_registry import register_artifact

            _artifact_con = connect_db(DUCKDB_PATH)
            try:
                for path, atype, label, subtype in artifact_files:
                    if path.exists():
                        register_artifact(
                            _artifact_con, analysis_id, path, atype, label,
                            artifact_subtype=subtype,
                        )
            finally:
                _artifact_con.close()
        except Exception as _exc:
            logger.warning("bulk_deg: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc_outer:
        logger.exception("bulk_deg 分析失敗  analysis_id=%s", analysis_id)
        from analysis.failure_diagnosis import classify_exception

        try:
            store.fail_history(
                analysis_id, datetime.now(timezone.utc),
                failure_diagnosis=json.dumps(classify_exception(_exc_outer)),
            )
        except Exception:
            pass
        raise

    # store 不需要 close() — 連線由 store 自行管理
    try:
        from scripts.export_registry import export_snapshot
        export_snapshot()
    except Exception as _exp_exc:
        logger.warning("export_registry 失敗（非致命）: %s", _exp_exc)

    logger.info("bulk_deg 完成  analysis_id=%s  report=%s", analysis_id, report_path)
    return analysis_id, str(report_path)
