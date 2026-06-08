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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT, DUCKDB_PATH, SUMMARY_MAX_CHARS
from config.db_utils import safe_write
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md
from analysis.path_utils import results_dir
from analysis.tool_registry import register_tool_on_import

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


def pca_plot(
    counts: pd.DataFrame,
    output_path: Optional[Path] = None,
    n_top_genes: int = 2000,
) -> Path:
    """以變異量最高的 n_top_genes 基因做 PCA，儲存圖檔並回傳路徑。"""
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    var = counts.var(axis=1).sort_values(ascending=False)
    top_idx = var.head(n_top_genes).index
    mat = np.log1p(counts.loc[top_idx].T.values)
    mat = StandardScaler().fit_transform(mat)

    pca = PCA(n_components=min(2, mat.shape[1]))
    coords = pca.fit_transform(mat)
    explained = pca.explained_variance_ratio_ * 100

    fig, ax = plt.subplots(figsize=(8, 6))
    samples = counts.columns.tolist()
    groups = [s.split("_")[0] for s in samples]
    palette = {g: plt.cm.tab10(i) for i, g in enumerate(sorted(set(groups)))}

    for sample, group, (x, y) in zip(samples, groups, coords):
        ax.scatter(x, y, color=palette[group], s=60, zorder=3)
        ax.annotate(sample, (x, y), fontsize=6, ha="left", va="bottom")

    from matplotlib.patches import Patch

    handles = [Patch(color=c, label=g) for g, c in sorted(palette.items())]
    ax.legend(handles=handles, fontsize=8, title="condition")
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


# ── 報告生成 ──────────────────────────────────────────────────────────────────

_REPORT_TEMPLATE = """\
# Bulk RNA-seq EDA 報告

**生成時間**：{timestamp}
**樣本集**：{sample_id}
**樣本數**：{n_samples}
**基因數**：{n_genes:,}

---

## 1. QC 統計

{qc_table}
{qc_fig}

---

## 2. Top {n_top} 高表達基因（平均 counts）

{top_table}

---

## 3. 樣本相關矩陣（Pearson, log1p）

{corr_table}
{corr_fig}

---

## 4. PCA 圖
{pca_fig}

---

*由 BioAgent analysis/bulk_eda.py 自動生成*
"""


@register_tool_on_import(
    tool_name="bio_run_bulk_eda",
    version="1.0.0",
    description="執行 98 樣本 Bulk RNA-seq 的 EDA 探索性數據分析並繪製圖表"
)
def generate_bulk_report(
    sample_id: str,
    counts_path: Optional[Path] = None,
    requested_by: str = "agent",
) -> tuple[str, str]:
    """執行完整 Bulk EDA 並將報告 + 摘要寫入 analysis_history。

    回傳 (analysis_id, report_path)。
    """
    validate_sample_id(sample_id)

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps({"counts_path": str(counts_path or "auto")})

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        safe_write(
            con,
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    requested_by, started_at)
               VALUES (?, ?, 'bulk_eda', ?, 'running', ?, ?)""",
            [analysis_id, sample_id, params_json, requested_by, started_at],
        )

        counts = load_counts(counts_path)
        qc = qc_stats(counts)
        top = top_genes(counts, n=20)
        corr = sample_correlation(counts)

        out_dir = results_dir(sample_id, "bulk_eda")
        ts = started_at.strftime("%Y%m%d_%H%M%S")

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

        qc_file, qc_fig = _safe_fig(lambda p: qc_barplot(qc, output_path=p), qc_out, "QC barplot")
        corr_file, corr_fig = _safe_fig(
            lambda p: correlation_heatmap(corr, output_path=p),
            corr_out,
            "Sample correlation heatmap",
        )
        pca_file, pca_fig = _safe_fig(lambda p: pca_plot(counts, output_path=p), pca_out, "PCA")

        report_text = _REPORT_TEMPLATE.format(
            timestamp=started_at.isoformat(),
            sample_id=sample_id,
            n_samples=counts.shape[1],
            n_genes=counts.shape[0],
            qc_table=qc.to_markdown(floatfmt=".1f"),
            qc_fig=qc_fig,
            n_top=20,
            top_table=top.to_markdown(floatfmt=".1f"),
            corr_table=corr.to_markdown(floatfmt=".3f"),
            corr_fig=corr_fig,
            pca_fig=pca_fig,
        )

        report_path = out_dir / f"bulk_eda_{sample_id}_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")
        logger.info("報告儲存至 %s", report_path)

        avg_total = qc["total_counts"].mean()
        avg_genes = int(qc["n_genes"].mean())
        n_samples = counts.shape[1]
        full_summary = (
            f"Bulk RNA {sample_id}：{n_samples} 樣本，"
            f"均 {avg_genes:,} 基因，均 total counts {avg_total:,.0f}。"
        )
        summary = full_summary[:SUMMARY_MAX_CHARS]

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

            if qc_file and qc_file.exists():
                register_artifact(
                    con,
                    analysis_id,
                    qc_file,
                    "figure",
                    "QC barplot（library size + 偵測基因數）",
                    artifact_subtype="qc",
                )
            if corr_file and corr_file.exists():
                register_artifact(
                    con,
                    analysis_id,
                    corr_file,
                    "figure",
                    "樣本相關矩陣 heatmap",
                    artifact_subtype="correlation",
                )
            if pca_file and pca_file.exists():
                register_artifact(
                    con,
                    analysis_id,
                    pca_file,
                    "figure",
                    "PCA 主成分分析圖",
                    artifact_subtype="pca",
                )
            register_artifact(
                con,
                analysis_id,
                report_path,
                "report",
                "Bulk EDA 分析報告",
                artifact_subtype="eda_report",
            )
        except Exception as _exc:
            logger.warning("bulk_eda: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc:
        logger.exception("bulk_eda 分析失敗  analysis_id=%s", analysis_id)
        try:
            safe_write(
                con,
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
            from analysis.failure_diagnosis import classify_exception, write_diagnosis
            write_diagnosis(con, analysis_id, classify_exception(_exc))
        except Exception:
            pass
        raise
    finally:
        con.close()

    logger.info("analysis_history 寫入完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
