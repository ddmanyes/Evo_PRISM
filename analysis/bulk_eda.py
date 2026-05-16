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
import re
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
from config.settings import BIO_DB_ROOT, DUCKDB_PATH
from config.db_utils import safe_write

logger = logging.getLogger(__name__)

BULK_RESULTS_DIR = BIO_DB_ROOT / "bulk_rna_data" / "Kallisto_v1" / "results_kallisto"
REPORTS_DIR      = BIO_DB_ROOT / "results" / "bulk_eda"

_SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_sample_id(sample_id: str) -> None:
    if not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(f"無效的 sample_id：{sample_id!r}（只允許英數字、底線、連字號）")


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
    stats = pd.DataFrame({
        "total_counts":          counts.sum(axis=0),
        "n_genes":               (counts > 0).sum(axis=0),
        "median_counts_per_gene": counts.replace(0, np.nan).median(axis=0),
    })

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

    var     = counts.var(axis=1).sort_values(ascending=False)
    top_idx = var.head(n_top_genes).index
    mat     = np.log1p(counts.loc[top_idx].T.values)
    mat     = StandardScaler().fit_transform(mat)

    pca     = PCA(n_components=min(2, mat.shape[1]))
    coords  = pca.fit_transform(mat)
    explained = pca.explained_variance_ratio_ * 100

    fig, ax = plt.subplots(figsize=(8, 6))
    samples = counts.columns.tolist()
    groups  = [s.split("_")[0] for s in samples]
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

---

## 2. Top {n_top} 高表達基因（平均 counts）

{top_table}

---

## 3. 樣本相關矩陣（Pearson, log1p）

{corr_table}

---

## 4. PCA 圖

![PCA]({pca_path})

---

*由 Hermes Bio-Memory analysis/bulk_eda.py 自動生成*
"""


def generate_bulk_report(
    sample_id: str,
    counts_path: Optional[Path] = None,
    requested_by: str = "agent",
) -> tuple[str, str]:
    """執行完整 Bulk EDA 並將報告 + 摘要寫入 analysis_history。

    回傳 (analysis_id, report_path)。
    """
    _validate_sample_id(sample_id)

    counts = load_counts(counts_path)
    qc     = qc_stats(counts)
    top    = top_genes(counts, n=20)
    corr   = sample_correlation(counts)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    pca_out = REPORTS_DIR / f"pca_{sample_id}_{ts}.png"

    try:
        pca_path = str(pca_plot(counts, output_path=pca_out))
    except Exception:
        logger.warning("PCA 生成失敗（可能缺 scikit-learn），跳過", exc_info=True)
        pca_path = "(PCA 圖生成失敗)"

    report_text = _REPORT_TEMPLATE.format(
        timestamp=datetime.now(timezone.utc).isoformat(),
        sample_id=sample_id,
        n_samples=counts.shape[1],
        n_genes=counts.shape[0],
        qc_table=qc.to_markdown(floatfmt=".1f"),
        n_top=20,
        top_table=top.to_markdown(floatfmt=".1f"),
        corr_table=corr.to_markdown(floatfmt=".3f"),
        pca_path=pca_path,
    )

    report_path = REPORTS_DIR / f"bulk_eda_{sample_id}_{ts}.md"
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("報告儲存至 %s", report_path)

    avg_total = qc["total_counts"].mean()
    avg_genes = int(qc["n_genes"].mean())
    n_samples = counts.shape[1]
    summary   = (
        f"Bulk RNA {sample_id}：{n_samples} 樣本，"
        f"均 {avg_genes:,} 基因，均 total counts {avg_total:,.0f}。"
    )[:50]

    analysis_id = str(uuid.uuid4())
    now         = datetime.now(timezone.utc)

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        safe_write(
            con,
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    result_path, requested_by, started_at, completed_at, summary)
               VALUES (?, ?, 'bulk_eda', ?, 'completed', ?, ?, ?, ?, ?)""",
            [analysis_id, sample_id,
             json.dumps({"counts_path": str(counts_path or "auto")}),
             str(report_path), requested_by, now, now, summary],
        )

    logger.info("analysis_history 寫入完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
