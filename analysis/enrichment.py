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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import duckdb
import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.db_utils import safe_write
from config.settings import DUCKDB_PATH
from analysis.path_utils import results_dir
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md

logger = logging.getLogger(__name__)

_SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

# 預設 library 集合（與參考 pipeline 對齊；可由呼叫端覆蓋）
DEFAULT_LIBRARIES: tuple[str, ...] = (
    "GO_Biological_Process_2023",
    "KEGG_2021_Human",
    "Reactome_2022",
)

_LIBRARY_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_sample_id(sample_id: str) -> None:
    if not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(f"無效的 sample_id：{sample_id!r}")


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


# ── 主流程：DEG → ORA → 報告 ─────────────────────────────────────────────────

_REPORT_TEMPLATE = """# Bulk 富集分析報告（ORA）

- **樣本登記 ID**：{sample_id}
- **執行時間**：{timestamp}
- **DEG 來源**：`{deg_source}`
- **Gene set libraries**：{libraries}
- **閾值**：|log2FC| > {fc_thr}, qvalue < {pval_thr}

## 命中通路統計

{summary_table}

## Dot plots

{dotplot_figs}
"""


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
    con: Optional[duckdb.DuckDBPyConnection] = None,
) -> tuple[str, str]:
    """對一張 DEG 表跑 ORA（up / down × N 個 library），產出彙整報告。

    Args:
        deg_table_path: ``run_deg_analysis`` 產出的 DEG_<a>_vs_<b>.csv
        libraries:      gseapy Enrichr 支援的 library 名稱清單

    Returns:
        (analysis_id, report_path)
    """
    _validate_sample_id(sample_id)
    if not libraries:
        raise ValueError("libraries 不可為空")
    for lib in libraries:
        _validate_library(lib)

    deg_table_path = Path(deg_table_path)
    if not deg_table_path.exists():
        raise FileNotFoundError(f"找不到 DEG 表：{deg_table_path}")

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps(
        {
            "deg_table_path": str(deg_table_path),
            "libraries": list(libraries),
            "organism": organism,
            "fc_threshold": fc_threshold,
            "pval_threshold": pval_threshold,
            "top_term": top_term,
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
               VALUES (?, ?, 'bulk_enrichment', ?, 'running', ?, ?)""",
            [analysis_id, sample_id, params_json, requested_by, started_at],
        )

        deg = pd.read_csv(deg_table_path, index_col=0)
        directions = split_deg_genes(
            deg,
            fc_threshold=fc_threshold,
            pval_threshold=pval_threshold,
        )

        out_dir = results_dir(sample_id, "bulk_enrichment")
        ts = started_at.strftime("%Y%m%d_%H%M%S")
        prefix = deg_table_path.stem  # 例：DEG_pw24hr_vs_ctrl_20260521_093045

        summary_rows: list[dict] = []
        figs_md_parts: list[str] = []
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
                    png_path = out_dir / f"{tag}_{ts}.png"
                    if dotplot_from_enrichr(
                        res, output_path=png_path, top_term=top_term, title=tag
                    ):
                        figs_md_parts.append(_file_to_b64_md(png_path, tag))
                        artifact_files.append(
                            (
                                png_path,
                                "figure",
                                f"ORA dot plot {direction} / {lib}",
                                "enrichment_dotplot",
                            )
                        )
                    n_sig = int(
                        (res.get("Adjusted P-value", pd.Series(dtype=float)) < pval_threshold).sum()
                    )
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
                sample_id=sample_id,
                timestamp=started_at.isoformat(),
                deg_source=deg_table_path.name,
                libraries=", ".join(libraries),
                fc_thr=fc_threshold,
                pval_thr=pval_threshold,
                summary_table=summary_df.to_markdown(index=False),
                dotplot_figs="\n".join(figs_md_parts) or "（無顯著富集 → 無 dot plot）",
            ),
            encoding="utf-8",
        )
        artifact_files.append((report_path, "report", "Bulk 富集分析報告", "enrichment_report"))

        total_sig = int(summary_df["n_terms_sig"].sum())
        summary = (
            f"Bulk ORA {sample_id}：{len(libraries)} library × up/down，共 {total_sig} 顯著通路。"
        )[:80]

        completed_at = datetime.now(timezone.utc)
        safe_write(
            con,
            """UPDATE analysis_history
                  SET status='completed', result_path=?, completed_at=?, summary=?
                WHERE analysis_id=?""",
            [str(report_path), completed_at, summary, analysis_id],
        )
        # HELIX §7.3：任何呼叫路徑都回填 tool_id（best-effort）
        try:
            from analysis.tool_registry import backfill_tool_id

            backfill_tool_id(con, "bio_run_enrichment", analysis_id)
        except Exception as _exc:
            logger.warning("ora: backfill_tool_id 失敗（非致命）: %s", _exc)

        try:
            from analysis.artifact_registry import register_artifact

            for path, atype, label, subtype in artifact_files:
                if path.exists():
                    register_artifact(
                        con, analysis_id, path, atype, label, artifact_subtype=subtype
                    )
        except Exception as _exc:
            logger.warning("ora: register_artifact 失敗（非致命）: %s", _exc)

    except Exception:
        logger.exception("bulk_enrichment 失敗  analysis_id=%s", analysis_id)
        try:
            safe_write(
                con,
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
        finally:
            if _own_con:
                con.close()
        raise

    if _own_con:
        con.close()
    logger.info("bulk_enrichment 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
