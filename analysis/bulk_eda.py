"""
Bulk RNA-seq 基礎探索分析（EDA）。

主要函數：
    load_counts()          — 載入 gene count 矩陣（TSV）
    qc_stats()             — 每樣本 QC 統計（total_counts, n_genes, mapping_rate）
    top_genes()            — 依平均表達量排序的前 N 基因
    sample_correlation()   — 樣本間 Pearson 相關矩陣（log1p counts）
    pca_plot()             — PCA 降維圖（matplotlib）
    generate_bulk_report() — 彙整報告 + 摘要，寫入 analysis_history
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT, SUMMARY_MAX_CHARS
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md
from analysis.path_utils import results_dir
from analysis.tool_registry import register_tool_on_import
from analysis.run_context import analysis_run

logger = logging.getLogger(__name__)

BULK_RESULTS_DIR = BIO_DB_ROOT / "bulk_rna_data" / "Kallisto_v1" / "results_kallisto"
REPORTS_DIR = BIO_DB_ROOT / "results" / "bulk_eda"

from analysis.validators import validate_sample_id


# ── 資料載入 ──────────────────────────────────────────────────────────────────


def load_counts(counts_path: Optional[Path] = None) -> pd.DataFrame:
    """載入 gene count 矩陣，回傳 DataFrame（index=gene, columns=samples）。

    預設依序嘗試：
      gene_counts_mapped_symbol.tsv → gene_counts_ensembl.tsv → gene_counts.tsv
    """
    defaults = [
        BULK_RESULTS_DIR / "gene_counts_mapped_symbol.tsv",
        BULK_RESULTS_DIR / "gene_counts_ensembl.tsv",
        BULK_RESULTS_DIR / "gene_counts.tsv",
    ]
    path = counts_path or next((p for p in defaults if p.exists()), None)
    if path is None or not path.exists():
        raise FileNotFoundError(
            "找不到 gene count 矩陣，請先執行 scripts/bulk_rna/ 下的 pipeline 腳本"
        )
    df = pd.read_csv(path, sep="\t", index_col=0)
    logger.info("載入 count 矩陣：%s  shape=%s", path.name, df.shape)
    return df


# ── 分析函數 ──────────────────────────────────────────────────────────────────


def qc_stats(
    counts: pd.DataFrame,
    run_info_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """每樣本 QC 統計：total_counts、n_genes（>0）、mapping_rate（若 run_info 存在）。"""
    stats = pd.DataFrame(
        {
            "total_counts": counts.sum(axis=0),
            "n_genes": (counts > 0).sum(axis=0),
            "median_counts_per_gene": counts.replace(0, np.nan).median(axis=0),
        }
    )

    info_dir = run_info_dir or BULK_RESULTS_DIR
    mapping_rates: dict[str, float] = {}
    for sample in counts.columns:
        info_path = info_dir / sample / "run_info.json"
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                mapping_rates[sample] = float(info.get("p_pseudoaligned", 0))
            except Exception:
                logger.warning("無法讀取 run_info.json：%s", info_path)
    if mapping_rates:
        stats["mapping_rate_pct"] = pd.Series(mapping_rates)

    return stats.sort_values("total_counts", ascending=False)


def top_genes(counts: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """依所有樣本平均表達量排序的前 N 基因。"""
    mean_expr = counts.mean(axis=1).sort_values(ascending=False)
    top = mean_expr.head(n).to_frame("mean_counts")
    top["present_in_n_samples"] = (counts.loc[top.index] > 0).sum(axis=1)
    return top


def sample_correlation(counts: pd.DataFrame) -> pd.DataFrame:
    """樣本間 Pearson 相關矩陣（log1p 正規化後計算）。"""
    return np.log1p(counts).corr(method="pearson")


def assess_qc_flags(
    qc: pd.DataFrame,
    corr: pd.DataFrame,
    *,
    mapping_rate_threshold: float = 70.0,
    pearson_threshold: float = 0.9,
) -> list[str]:
    """回傳品質警告旗標清單（空列表 = 全部通過）。

    旗標格式：
        low_mapping_rate:<sample>(<rate>%)
        low_correlation:<sample>(mean_r=<r>)
    """
    flags: list[str] = []

    if "mapping_rate_pct" in qc.columns:
        bad = qc[qc["mapping_rate_pct"] < mapping_rate_threshold]["mapping_rate_pct"]
        for sample, rate in bad.items():
            flags.append(f"low_mapping_rate:{sample}({rate:.1f}%)")

    # 平均相關性：排除自身（對角線）
    corr_copy = corr.copy().astype(float)
    np.fill_diagonal(corr_copy.values, np.nan)
    mean_r = corr_copy.mean(axis=1, skipna=True)
    bad_corr = mean_r[mean_r < pearson_threshold]
    for sample, r in bad_corr.items():
        flags.append(f"low_correlation:{sample}(mean_r={r:.3f})")

    return flags


def pca_plot(
    counts: pd.DataFrame,
    output_path: Optional[Path] = None,
    n_top_genes: int = 2000,
    coldata: Optional[pd.DataFrame] = None,
    color_by: str = "group",
) -> Path:
    """以變異量最高的 n_top_genes 基因做 PCA，儲存圖檔並回傳路徑。

    coldata 若提供，依 color_by 欄位著色（優先於 sample name 前綴推斷）。
    """
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    var = counts.var(axis=1).sort_values(ascending=False)
    top_idx = var.head(n_top_genes).index
    mat = np.log1p(counts.loc[top_idx].T.values)
    mat = StandardScaler().fit_transform(mat)

    pca = PCA(n_components=min(2, mat.shape[1]))
    coords = pca.fit_transform(mat)
    explained = pca.explained_variance_ratio_ * 100

    samples = counts.columns.tolist()
    if coldata is not None and color_by in coldata.columns:
        groups = [str(coldata.loc[s, color_by]) if s in coldata.index else "unknown" for s in samples]
        legend_title = color_by
    else:
        groups = [s.split("_")[0] for s in samples]
        legend_title = "condition"

    fig, ax = plt.subplots(figsize=(8, 6))
    palette = {g: plt.cm.tab10(i) for i, g in enumerate(sorted(set(groups)))}

    for sample, group, (x, y) in zip(samples, groups, coords):
        ax.scatter(x, y, color=palette[group], s=60, zorder=3)
        ax.annotate(sample, (x, y), fontsize=6, ha="left", va="bottom")

    from matplotlib.patches import Patch

    handles = [Patch(color=c, label=g) for g, c in sorted(palette.items())]
    ax.legend(handles=handles, fontsize=8, title=legend_title)
    ax.set_xlabel(f"PC1 ({explained[0]:.1f}%)", fontsize=11)
    ax.set_ylabel(f"PC2 ({explained[1]:.1f}%)" if len(explained) > 1 else "PC2", fontsize=11)
    ax.set_title("Bulk RNA-seq PCA (log1p counts)", fontsize=13)
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()

    out = output_path or (REPORTS_DIR / f"pca_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    logger.info("PCA 圖儲存至 %s", out)
    return out


def qc_barplot(
    qc: pd.DataFrame,
    output_path: Optional[Path] = None,
) -> Path:
    """每樣本 library size 與偵測基因數雙 barplot，儲存圖檔並回傳路徑。"""
    samples = qc.index.tolist()
    fig, axes = plt.subplots(1, 2, figsize=(max(8, len(samples) * 0.6), 5))

    axes[0].bar(samples, qc["total_counts"], color="#4C72B0")
    axes[0].set_ylabel("total counts", fontsize=11)
    axes[0].set_title("Library size", fontsize=12)

    axes[1].bar(samples, qc["n_genes"], color="#55A868")
    axes[1].set_ylabel("n genes (>0)", fontsize=11)
    axes[1].set_title("Detected genes", fontsize=12)

    for ax in axes:
        ax.tick_params(axis="x", labelrotation=90, labelsize=7)
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()

    out = output_path or (REPORTS_DIR / f"qc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    logger.info("QC barplot 儲存至 %s", out)
    return out


def correlation_heatmap(
    corr: pd.DataFrame,
    output_path: Optional[Path] = None,
) -> Path:
    """樣本間相關矩陣 heatmap，儲存圖檔並回傳路徑。"""
    n = corr.shape[0]
    fig, ax = plt.subplots(figsize=(max(6, n * 0.5), max(5, n * 0.5)))
    im = ax.imshow(corr.values, cmap="viridis", vmin=corr.values.min(), vmax=1.0)

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(corr.columns, rotation=90, fontsize=7)
    ax.set_yticklabels(corr.index, fontsize=7)
    ax.set_title("Sample correlation (Pearson, log1p)", fontsize=12)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()

    out = output_path or (REPORTS_DIR / f"corr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    logger.info("相關矩陣 heatmap 儲存至 %s", out)
    return out


# ── 報告生成輔助 ─────────────────────────────────────────────────────────────


def _caption_qc(qc: pd.DataFrame) -> str:
    try:
        if qc.empty or qc["total_counts"].isna().all():
            return "QC 統計不可用"
        lo, hi = int(qc["total_counts"].min()), int(qc["total_counts"].max())
        g_lo, g_hi = int(qc["n_genes"].min()), int(qc["n_genes"].max())
        return f"lib.size {lo:,}–{hi:,}；偵測基因 {g_lo:,}–{g_hi:,}"
    except Exception:
        return "QC 統計不可用"


def _caption_dist(counts: pd.DataFrame) -> str:
    try:
        if counts.empty:
            return "count 分布不可用"
        vals = np.log1p(counts.values.astype(float))
        medians = np.median(vals, axis=0)
        return f"各樣本 log1p 中位數 {medians.min():.2f}–{medians.max():.2f}"
    except Exception:
        return "count 分布不可用"


def _caption_corr(corr: pd.DataFrame) -> str:
    try:
        vals = corr.values.copy().astype(float)
        np.fill_diagonal(vals, np.nan)
        off = vals[~np.isnan(vals)]
        if off.size == 0:
            return "Pearson r N/A（樣本數不足）"
        return f"Pearson r {off.min():.2f}–{off.max():.2f}（不含對角線）"
    except Exception:
        return "相關矩陣不可用"


def _caption_pca(counts: pd.DataFrame) -> str:
    try:
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler
        top_idx = counts.var(axis=1).sort_values(ascending=False).head(2000).index
        mat = StandardScaler().fit_transform(np.log1p(counts.loc[top_idx].T.values))
        ev = PCA(n_components=min(2, mat.shape[1])).fit(mat).explained_variance_ratio_ * 100
        return f"PC1={ev[0]:.1f}%，PC2={ev[1]:.1f}%" if len(ev) > 1 else f"PC1={ev[0]:.1f}%"
    except Exception:
        return "PC% 不可用"


def _version_block() -> str:
    """Markdown table of key package versions for reproducibility."""
    import importlib as _il
    import sys as _sys
    pkgs = [("pandas", "pandas"), ("numpy", "numpy"), ("scipy", "scipy"),
            ("omicverse", "omicverse"), ("seaborn", "seaborn"), ("sklearn", "sklearn")]
    rows = [f"| Python | {_sys.version.split()[0]} |"]
    for label, pkg in pkgs:
        try:
            ver = getattr(_il.import_module(pkg), "__version__", "?")
        except ImportError:
            ver = "—"
        rows.append(f"| `{label}` | {ver} |")
    return "| 套件 | 版本 |\n| --- | --- |\n" + "\n".join(rows)


def count_dist_boxplot(
    counts: pd.DataFrame,
    *,
    output_path: Path,
) -> Path:
    """每樣本 log1p counts 分布 boxplot（正規化前）。"""
    n = counts.shape[1]
    fig, ax = plt.subplots(figsize=(max(8, n * 0.55), 5))
    log_data = [np.log1p(counts[col].values) for col in counts.columns]
    bp = ax.boxplot(
        log_data,
        labels=counts.columns,
        showfliers=False,
        patch_artist=True,
        medianprops=dict(color="white", lw=1.5),
    )
    for patch in bp["boxes"]:
        patch.set_facecolor("#4C72B0")
        patch.set_alpha(0.75)
    ax.tick_params(axis="x", labelrotation=90, labelsize=7)
    ax.set_ylabel("log1p(counts)", fontsize=11)
    ax.set_title("Count distribution per sample (pre-normalization)", fontsize=12)
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


# ── 報告生成 ──────────────────────────────────────────────────────────────────

_REPORT_TEMPLATE = """\
# Bulk RNA-seq EDA 報告

| 欄位 | 值 |
| --- | --- |
| **analysis_id** | `{analysis_id}` |
| **生成時間** | {timestamp} |
| **樣本集** | {sample_id} |
| **樣本數** | {n_samples} |
| **基因數** | {n_genes:,} |

---

{quality_warnings}

## 0. 實驗設計（coldata）

{design_table}

---

## 1. QC 統計

{qc_table}
{qc_fig}

---

## 2. Count 分布（log1p，正規化前）

{dist_fig}

---

## 3. Top {n_top} 高表達基因（平均 counts）

{top_table}

---

## 4. 樣本相關矩陣（Pearson, log1p）

{corr_table}
{corr_fig}

---

## 5. PCA 圖

{pca_fig}

---

## 套件版本（reproducibility）

{version_block}

*由 Evo_PRISM analysis/bulk_eda.py 自動生成*
"""


@register_tool_on_import(
    tool_name="bio_run_bulk_eda",
    version="1.1.0",
    description="對 Bulk RNA-seq 樣本執行 EDA（QC / top genes / 相關矩陣 / PCA），支援 coldata 著色",
)
def generate_bulk_report(
    sample_id: str,
    counts_path: Optional[Path] = None,
    coldata_path: Optional[Path] = None,
    requested_by: str = "agent",
    parent_analysis_id: Optional[str] = None,
) -> tuple[str, str]:
    """執行完整 Bulk EDA 並將報告 + 摘要寫入 analysis_history。

    coldata_path 若提供，PCA 圖依 'group' 欄著色（否則以 sample name 前綴推斷）。
    成功完成後自動標記為 canonical，舊 canonical 降為 superseded。
    parent_analysis_id 若未指定則自動查詢當前 canonical 作為父節點。

    回傳 (analysis_id, report_path)。
    """
    from config.db_utils import param_hash

    validate_sample_id(sample_id)

    def _rel(p: Optional[Path]) -> str:
        if p is None:
            return "auto"
        try:
            return str(p.relative_to(BIO_DB_ROOT))
        except ValueError:
            return str(p)

    _params = {"counts_path": _rel(counts_path), "coldata_path": _rel(coldata_path)}
    report_path: Optional[Path] = None

    with analysis_run(
        sample_id, "bulk_eda",
        params=_params,
        requested_by=requested_by,
        parent_analysis_id=parent_analysis_id,
        tool_name="bio_run_bulk_eda",
        canonical=True,
    ) as run:
        counts = load_counts(counts_path)
        qc = qc_stats(counts)
        top = top_genes(counts, n=20)
        corr = sample_correlation(counts)
        quality_flags = assess_qc_flags(qc, corr)

        coldata: Optional[pd.DataFrame] = None
        if coldata_path is not None:
            _cp = Path(coldata_path)
            if _cp.exists():
                coldata = (
                    pd.read_csv(_cp, sep="\t", index_col=0)
                    if _cp.suffix in {".tsv", ".txt"}
                    else pd.read_csv(_cp, index_col=0)
                )
            else:
                logger.warning("coldata_path 不存在，PCA 改用 sample name 前綴著色：%s", _cp)

        out_dir = results_dir(sample_id, "bulk_eda")
        ts = run.started_at.strftime("%Y%m%d_%H%M%S")

        # 系列圖：QC barplot、相關矩陣 heatmap、PCA。任一失敗不致命，記警告續行。
        qc_out = out_dir / f"qc_{sample_id}_{ts}.png"
        corr_out = out_dir / f"corr_{sample_id}_{ts}.png"
        pca_out = out_dir / f"pca_{sample_id}_{ts}.png"

        def _safe_fig(plot_fn, out_path: Path, alt: str) -> tuple[Optional[Path], str]:
            try:
                f = plot_fn(out_path)
                return f, _file_to_b64_md(f, alt)
            except Exception:
                logger.warning("%s 生成失敗，跳過", alt, exc_info=True)
                return None, f"\n（{alt} 生成失敗）\n"

        dist_out = out_dir / f"dist_{sample_id}_{ts}.png"

        qc_file, qc_fig = _safe_fig(lambda p: qc_barplot(qc, output_path=p), qc_out, "QC barplot")
        dist_file, dist_fig = _safe_fig(
            lambda p: count_dist_boxplot(counts, output_path=p), dist_out, "Count distribution"
        )
        corr_file, corr_fig = _safe_fig(
            lambda p: correlation_heatmap(corr, output_path=p),
            corr_out,
            "Sample correlation heatmap",
        )
        pca_file, pca_fig = _safe_fig(
            lambda p: pca_plot(counts, output_path=p, coldata=coldata), pca_out, "PCA"
        )

        if quality_flags:
            warnings_md = (
                "## ⚠️ 品質警告\n\n"
                + "\n".join(f"- `{f}`" for f in quality_flags)
                + "\n\n---\n"
            )
        else:
            warnings_md = ""

        if coldata is not None:
            design_table = coldata.to_markdown()
        else:
            design_table = "（未提供 coldata，請傳入 `coldata_path` 以顯示實驗設計）"

        report_text = _REPORT_TEMPLATE.format(
            analysis_id=run.analysis_id,
            timestamp=run.started_at.isoformat(),
            sample_id=sample_id,
            n_samples=counts.shape[1],
            n_genes=counts.shape[0],
            quality_warnings=warnings_md,
            design_table=design_table,
            qc_table=qc.to_markdown(floatfmt=".1f"),
            qc_fig=qc_fig,
            dist_fig=dist_fig,
            n_top=20,
            top_table=top.to_markdown(floatfmt=".1f"),
            corr_table=corr.to_markdown(floatfmt=".3f"),
            corr_fig=corr_fig,
            pca_fig=pca_fig,
            version_block=_version_block(),
        )

        report_path = out_dir / f"bulk_eda_{sample_id}_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")
        logger.info("報告儲存至 %s", report_path)

        avg_total = qc["total_counts"].mean()
        avg_genes = int(qc["n_genes"].mean())
        n_samples = counts.shape[1]
        # 結構化摘要：前綴含關鍵數字供 SQL 過濾，後接自然語言供語意搜尋
        full_summary = (
            f"[n={n_samples}|genes={avg_genes}] "
            f"Bulk RNA {sample_id}：{n_samples} 樣本，"
            f"均 {avg_genes:,} 基因，avg_total={avg_total:,.0f}。"
        )
        summary = full_summary[:SUMMARY_MAX_CHARS]
        summary_metrics = json.dumps({
            "n_samples": n_samples,
            "avg_detected_genes": avg_genes,
            "avg_total_counts": int(avg_total),
            "quality_flags": quality_flags,
        })

        # 生命週期收尾（complete/canonical/diagnosis/artifact flush/snapshot）由 seam 統一處理。
        if qc_file:
            run.artifact(qc_file, "figure",
                         f"QC barplot（library size + 偵測基因數）— {_caption_qc(qc)}", "qc")
        if dist_file:
            run.artifact(dist_file, "figure",
                         f"Count 分布 boxplot（log1p）— {_caption_dist(counts)}", "count_dist")
        if corr_file:
            run.artifact(corr_file, "figure",
                         f"樣本相關矩陣 heatmap — {_caption_corr(corr)}", "correlation")
        if pca_file:
            run.artifact(pca_file, "figure",
                         f"PCA 主成分分析圖 — {_caption_pca(counts)}", "pca")
        run.artifact(report_path, "report", "Bulk EDA 分析報告", "eda_report")
        run.complete(
            report_path, summary,
            summary_metrics=json.loads(summary_metrics),
        )

    logger.info("analysis_history 寫入完成  analysis_id=%s", run.analysis_id)
    return run.analysis_id, str(report_path)
