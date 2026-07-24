"""Bulk RNA-seq 富集分析（ORA / GSEA）— gseapy 封裝。

對齊參考實作 ddmanyes/bulk-rnaseq-pipeline：
    - ORA：``gseapy.enrichr``（線上）對 GO / KEGG / Reactome
    - dot plot：``gseapy.plot.dotplot``
    - GSEA prerank（按需）：``gseapy.prerank``

主要對外函數：
    run_ora(sample_id, deg_table_path, libraries, ...)
        → (analysis_id, report_path)
        對 DEG 表的 up / down 兩個方向各自跑 ORA，每個資料庫 × 方向 → CSV + dot plot

設計取捨：
    gseapy.enrichr 需網路（Enrichr API）；無網或被防火牆擋時直接 raise，由 agent
    fallback 提示用戶。GSEA prerank 走 offline GMT，較重，本檔暫只實作 ORA。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT
from analysis.path_utils import results_dir
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md
from analysis.tool_registry import register_tool_on_import
from analysis.run_context import analysis_run

logger = logging.getLogger(__name__)

from analysis.validators import validate_sample_id

# 預設 library 集合（與參考 pipeline 對齊；可由呼叫端覆蓋）
DEFAULT_LIBRARIES: tuple[str, ...] = (
    "GO_Biological_Process_2023",
    "KEGG_2021_Human",
    "Reactome_2022",
)

# 擴充 library 集合（含 GO 三子本體；API 呼叫較多，按需使用）
EXTENDED_LIBRARIES: tuple[str, ...] = (
    "GO_Biological_Process_2023",
    "GO_Molecular_Function_2023",
    "GO_Cellular_Component_2023",
    "KEGG_2021_Human",
    "Reactome_2022",
)

_LIBRARY_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_library(name: str) -> None:
    if not _LIBRARY_RE.match(name):
        raise ValueError(f"無效的 gene set library 名稱：{name!r}")


# ── 從 DEG 表抽 up/down gene list ────────────────────────────────────────────


def split_deg_genes(
    deg: pd.DataFrame,
    *,
    fc_col: str = "log2FC",
    pval_col: str = "qvalue",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
) -> dict[str, list[str]]:
    """從 DEG 表抽出 up / down 方向的基因清單。

    使用 DataFrame.index 作為基因符號（與 omicverse pyDEG 輸出對齊）。
    """
    missing = {fc_col, pval_col} - set(deg.columns)
    if missing:
        raise ValueError(f"deg 缺少欄位：{sorted(missing)}")
    sig = deg[deg[pval_col] < pval_threshold]
    up = sig.index[sig[fc_col] > fc_threshold].astype(str).tolist()
    dn = sig.index[sig[fc_col] < -fc_threshold].astype(str).tolist()
    return {"up": up, "down": dn}


# ── ORA via gseapy.enrichr ───────────────────────────────────────────────────


def run_enrichr_single(
    gene_list: Sequence[str],
    library: str,
    *,
    organism: str = "human",
    cutoff: float = 0.05,
) -> pd.DataFrame:
    """對一個 gene_list × 一個 library 跑 Enrichr ORA。

    回傳 gseapy 的 res2d DataFrame（含 Term / Overlap / P-value / Adjusted P-value / Genes 等欄）。
    沒有命中時回空 DataFrame（不 raise）。
    """
    _validate_library(library)
    if not gene_list:
        return pd.DataFrame()

    import gseapy as gp

    try:
        enr = gp.enrichr(
            gene_list=list(gene_list),
            gene_sets=library,
            organism=organism,
            outdir=None,
            cutoff=cutoff,
            no_plot=True,
            verbose=False,
        )
    except Exception as exc:
        logger.warning("Enrichr %s 失敗：%s", library, exc)
        return pd.DataFrame()
    return enr.res2d if enr is not None and enr.res2d is not None else pd.DataFrame()


def dotplot_from_enrichr(
    res: pd.DataFrame,
    *,
    output_path: Path,
    top_term: int = 10,
    title: str = "",
    figsize: tuple[float, float] = (6.0, 5.5),
) -> Optional[Path]:
    """為一張 Enrichr 結果畫 dot plot；無結果時回 None。"""
    if res is None or res.empty:
        return None
    import gseapy as gp

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ax = gp.dotplot(
            res,
            column="Adjusted P-value",
            top_term=top_term,
            figsize=figsize,
            title=title,
            cmap=plt.cm.viridis,
            show_ring=False,
        )
        fig = ax.figure if hasattr(ax, "figure") else plt.gcf()
        fig.tight_layout()
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output_path
    except Exception as exc:
        logger.warning("dotplot 失敗（%s）：%s", title, exc)
        plt.close("all")
        return None


def bar_plot_from_enrichr(
    res: pd.DataFrame,
    *,
    output_path: Path,
    top_term: int = 10,
    title: str = "",
    figsize: tuple[float, float] = (7.0, 5.0),
) -> Optional[Path]:
    """Enrichr ORA 結果長條圖（-log10 padj）；補充 dot plot 使用。"""
    if res is None or res.empty:
        return None
    pval_col = "Adjusted P-value" if "Adjusted P-value" in res.columns else "P-value"
    term_col = "Term" if "Term" in res.columns else res.columns[0]
    df = res.nsmallest(top_term, pval_col).copy()
    if df.empty:
        return None
    df["_neglog10p"] = -np.log10(df[pval_col].clip(lower=1e-300))
    df = df.sort_values("_neglog10p", ascending=True)
    terms = df[term_col].str[:55].tolist()
    values = df["_neglog10p"].tolist()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=figsize)
    ax.barh(terms, values, color="#4C72B0", alpha=0.8)
    ax.axvline(-np.log10(0.05), color="grey", ls="--", lw=0.8, label="padj=0.05")
    ax.set_xlabel("-log10(Adjusted P-value)", fontsize=10)
    ax.set_title(title or "Enrichment bar plot", fontsize=11)
    ax.legend(fontsize=8)
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _version_block() -> str:
    import importlib as _il, sys as _sys
    pkgs = [("pandas", "pandas"), ("numpy", "numpy"), ("gseapy", "gseapy"),
            ("matplotlib", "matplotlib")]
    rows = [f"| Python | {_sys.version.split()[0]} |"]
    for label, pkg in pkgs:
        try:
            ver = getattr(_il.import_module(pkg), "__version__", "?")
        except ImportError:
            ver = "—"
        rows.append(f"| `{label}` | {ver} |")
    return "| 套件 | 版本 |\n| --- | --- |\n" + "\n".join(rows)


# ── 主流程：DEG → ORA → 報告 ─────────────────────────────────────────────────

_REPORT_TEMPLATE = """# Bulk 富集分析報告（ORA）

| 欄位 | 值 |
| --- | --- |
| **analysis_id** | `{analysis_id}` |
| **樣本登記 ID** | {sample_id} |
| **執行時間** | {timestamp} |
| **DEG 來源** | `{deg_source}` |
| **物種** | {organism} |
| **Gene set libraries** | {libraries} |
| **閾值** | \\|log2FC\\| > {fc_thr}, qvalue < {pval_thr} |
| **ORA background** | Enrichr 全資料庫基因（gseapy 預設；限縮 background 建議改用 clusterProfiler） |

## 命中通路統計

{summary_table}

## 長條圖（Bar plots，-log10 padj）

{barplot_figs}

## 點圖（Dot plots）

{dotplot_figs}

---

## 套件版本（reproducibility）

{version_block}

*由 Evo_PRISM analysis/enrichment.py 自動生成*
"""


@register_tool_on_import(
    tool_name="bio_run_enrichment",
    version="1.0.0",
    description="對 DEG 基因進行 ORA 富集分析 (Enrichr)",
)
def run_ora(
    sample_id: str,
    *,
    deg_table_path: Path,
    libraries: Sequence[str] = DEFAULT_LIBRARIES,
    organism: str = "human",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
    top_term: int = 10,
    requested_by: str = "agent",
    parent_analysis_id: Optional[str] = None,
) -> tuple[str, str]:
    """對一張 DEG 表跑 ORA（up / down × N 個 library），產出彙整報告。

    Args:
        deg_table_path: ``run_deg_analysis`` 產出的 DEG_<a>_vs_<b>.csv
        libraries:      gseapy Enrichr 支援的 library 名稱清單

    Returns:
        (analysis_id, report_path)
    """
    validate_sample_id(sample_id)
    if not libraries:
        raise ValueError("libraries 不可為空")
    for lib in libraries:
        _validate_library(lib)

    deg_table_path = Path(deg_table_path)
    if not deg_table_path.exists():
        raise FileNotFoundError(f"找不到 DEG 表：{deg_table_path}")

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(BIO_DB_ROOT))
        except ValueError:
            return str(p)

    _params = {
        "deg_table_path": _rel(deg_table_path),
        "libraries": list(libraries),
        "organism": organism,
        "fc_threshold": fc_threshold,
        "pval_threshold": pval_threshold,
        "top_term": top_term,
    }

    with analysis_run(
        sample_id, "bulk_enrichment",
        params=_params,
        requested_by=requested_by,
        parent_analysis_id=parent_analysis_id,
        tool_name="bio_run_enrichment",
        canonical=True,
    ) as run:
        deg = pd.read_csv(deg_table_path, index_col=0)
        directions = split_deg_genes(
            deg,
            fc_threshold=fc_threshold,
            pval_threshold=pval_threshold,
        )

        out_dir = results_dir(sample_id, "bulk_enrichment")
        ts = run.started_at.strftime("%Y%m%d_%H%M%S")
        prefix = deg_table_path.stem  # 例：DEG_pw24hr_vs_ctrl_20260521_093045

        summary_rows: list[dict] = []
        barplot_md_parts: list[str] = []
        dotplot_md_parts: list[str] = []
        artifact_files: list[tuple[Path, str, str, str]] = []

        for direction, gene_list in directions.items():
            for lib in libraries:
                tag = f"{prefix}__{direction}__{lib}"
                res = run_enrichr_single(
                    gene_list,
                    lib,
                    organism=organism,
                    cutoff=pval_threshold,
                )
                csv_path = out_dir / f"{tag}_{ts}.csv"
                if res is not None and not res.empty:
                    res.to_csv(csv_path, index=False)
                    artifact_files.append(
                        (csv_path, "csv", f"ORA {direction} / {lib}", "enrichment_table")
                    )
                    # 共用 pval 欄與 caption（bar + dot plot 都用）
                    _pval_col = "Adjusted P-value" if "Adjusted P-value" in res.columns else "P-value"
                    _term_col = "Term" if "Term" in res.columns else res.columns[0]
                    n_sig = int((res[_pval_col] < pval_threshold).sum())
                    _top_row = res.nsmallest(1, _pval_col)
                    _top_term = str(_top_row[_term_col].iloc[0])[:40] if not _top_row.empty else "—"
                    _top_p = float(_top_row[_pval_col].iloc[0]) if not _top_row.empty else 1.0
                    _caption = f"top: {_top_term}（padj={_top_p:.1e}）；顯著 {n_sig} term"
                    # bar plot
                    bar_path = out_dir / f"{tag}_bar_{ts}.png"
                    bar_file = bar_plot_from_enrichr(
                        res, output_path=bar_path, top_term=top_term,
                        title=f"{direction} / {lib}"
                    )
                    if bar_file:
                        barplot_md_parts.append(_file_to_b64_md(bar_path, f"Bar {direction}/{lib}"))
                        artifact_files.append((
                            bar_path, "figure",
                            f"ORA bar plot {direction} / {lib} — {_caption}",
                            "enrichment_barplot",
                        ))
                    # dot plot
                    png_path = out_dir / f"{tag}_{ts}.png"
                    if dotplot_from_enrichr(
                        res, output_path=png_path, top_term=top_term, title=tag
                    ):
                        dotplot_md_parts.append(_file_to_b64_md(png_path, tag))
                        artifact_files.append((
                            png_path, "figure",
                            f"ORA dot plot {direction} / {lib} — {_caption}",
                            "enrichment_dotplot",
                        ))
                else:
                    n_sig = 0

                summary_rows.append(
                    {
                        "direction": direction,
                        "library": lib,
                        "n_genes_input": len(gene_list),
                        "n_terms_sig": n_sig,
                    }
                )

        summary_df = pd.DataFrame(summary_rows)
        report_path = out_dir / f"bulk_enrichment_{sample_id}_{ts}.md"
        report_path.write_text(
            _REPORT_TEMPLATE.format(
                analysis_id=run.analysis_id,
                sample_id=sample_id,
                timestamp=run.started_at.isoformat(),
                deg_source=deg_table_path.name,
                organism=organism,
                libraries=", ".join(libraries),
                fc_thr=fc_threshold,
                pval_thr=pval_threshold,
                summary_table=summary_df.to_markdown(index=False),
                barplot_figs="\n".join(barplot_md_parts) or "（無顯著富集 → 無長條圖）",
                dotplot_figs="\n".join(dotplot_md_parts) or "（無顯著富集 → 無 dot plot）",
                version_block=_version_block(),
            ),
            encoding="utf-8",
        )
        artifact_files.append((report_path, "report", "Bulk 富集分析報告", "enrichment_report"))

        total_sig = int(summary_df["n_terms_sig"].sum())
        n_lib = len(libraries)
        summary = (
            f"[libs={n_lib}|sig={total_sig}] "
            f"Bulk ORA {sample_id}：{n_lib} library × up/down，共 {total_sig} 顯著通路。"
        )[:80]
        n_directions = int(summary_df["direction"].nunique())
        summary_metrics = json.dumps({
            "n_directions": n_directions,
            "n_libraries": n_lib,
            "n_sig_pathways": total_sig,
        })

        # 生命週期收尾（complete/canonical/diagnosis/artifact flush/snapshot）由 seam 統一處理。
        for path, atype, label, subtype in artifact_files:
            run.artifact(path, atype, label, subtype)
        run.complete(
            report_path, summary,
            summary_metrics=json.loads(summary_metrics),
        )

    logger.info("bulk_enrichment 完成  analysis_id=%s", run.analysis_id)
    return run.analysis_id, str(report_path)
