"""Marker gene ranking for MCseg ROI results.

Runs sc.tl.rank_genes_groups on an existing umap_computed.h5ad and exports
a CSV table + inline Markdown summary.

Main function:
    run_marker_genes(sample_id, roi_name, ...) -> (analysis_id, report_path)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import MCSEG_RESULTS_ROOT  # noqa: E402
from analysis.path_utils import results_dir  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402

logger = logging.getLogger(__name__)


@register_tool_on_import(
    tool_name="bio_get_marker_genes",
    version="1.0.0",
    description="對 MCseg ROI 結果執行 rank_genes_groups，匯出 marker genes CSV + 摘要表",
)
def run_marker_genes(
    sample_id: str,
    roi_name: str,
    roi_dir: Optional[Path] = None,
    groupby: str = "leiden",
    n_genes: int = 20,
    method: str = "wilcoxon",
    requested_by: str = "agent",
) -> tuple[str, str]:
    """Rank marker genes per cluster/cell-type for an existing MCseg ROI result.

    Returns (analysis_id, report_path).
    """
    validate_sample_id(sample_id)
    roi_dir = Path(roi_dir) if roi_dir else MCSEG_RESULTS_ROOT / sample_id / "roi" / roi_name
    h5ad_path = roi_dir / "umap_computed.h5ad"

    if not h5ad_path.exists():
        raise FileNotFoundError(
            f"找不到 umap_computed.h5ad：{h5ad_path}\n"
            "請先執行 bio_run_mcseg_roi 完成 Stage 3–6。"
        )

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps({
        "roi_name": roi_name, "groupby": groupby,
        "n_genes": n_genes, "method": method,
    })

    from store.factory import get_store as _get_store
    _get_store().insert_history(
        analysis_id, sample_id, "marker_genes", params_json, "running", requested_by, started_at
    )

    try:
        import scanpy as sc

        adata = sc.read_h5ad(str(h5ad_path))

        if groupby not in adata.obs.columns:
            available = list(adata.obs.columns)
            raise ValueError(
                f"groupby='{groupby}' 不在 obs 欄位中。\n"
                f"可用欄位：{available}"
            )
        if adata.obs[groupby].nunique() < 2:
            raise ValueError(
                f"'{groupby}' 只有 {adata.obs[groupby].nunique()} 個群組，"
                "至少需要 2 個才能執行 rank_genes_groups。"
            )

        sc.tl.rank_genes_groups(adata, groupby=groupby, method=method, n_genes=n_genes)
        df = sc.get.rank_genes_groups_df(adata, group=None)
        df = df.rename(columns={
            "group": "cluster",
            "names": "gene",
            "scores": "score",
            "logfoldchanges": "logfoldchange",
            "pvals_adj": "pval_adj",
        })[["cluster", "gene", "score", "logfoldchange", "pval_adj"]]

        out_dir = results_dir(sample_id, "marker_genes")
        ts = started_at.strftime("%Y%m%d_%H%M%S")
        csv_path = out_dir / f"marker_genes_{sample_id}_{roi_name}_{ts}.csv"
        df.to_csv(csv_path, index=False)

        # Inline top-5/cluster Markdown table
        top5 = df.groupby("cluster", sort=False).head(5)
        table_md = top5.to_markdown(index=False, floatfmt=".4f")

        clusters = sorted(df["cluster"].unique())
        report_text = (
            f"# Marker Genes — {sample_id} / {roi_name}\n\n"
            f"**生成時間**：{started_at.isoformat()}\n"
            f"**groupby**：`{groupby}`（{len(clusters)} 個群組）\n"
            f"**方法**：{method}  **每群 top-N**：{n_genes}\n\n"
            f"完整 CSV：`{csv_path}`\n\n"
            f"## Top 5 Marker Genes per Cluster\n\n"
            f"{table_md}\n\n"
            f"---\n*由 BioAgent analysis/marker_genes.py 自動生成*\n"
        )
        report_path = out_dir / f"marker_genes_{sample_id}_{roi_name}_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        summary = f"{sample_id}/{roi_name} markers：{len(clusters)} 群，top gene={df['gene'].iloc[0]}"[:50]
        completed_at = datetime.now(timezone.utc)

        with _get_store().write_conn() as con:
            from analysis.tool_registry import get_active_tool_id
            tool_id = get_active_tool_id(con, "bio_get_marker_genes")
            con.execute(
                """UPDATE analysis_history
                      SET status='completed', result_path=?, completed_at=?, summary=?, tool_id=?
                    WHERE analysis_id=?""",
                [str(report_path), completed_at, summary, tool_id, analysis_id],
            )
            from analysis.failure_diagnosis import success_diagnosis, write_diagnosis
            write_diagnosis(con, analysis_id, success_diagnosis())
            try:
                from analysis.artifact_registry import register_artifact
                register_artifact(con, analysis_id, csv_path, "table", "Marker genes CSV",
                                  artifact_subtype="marker_genes")
                register_artifact(con, analysis_id, report_path, "report", "Marker genes 報告",
                                  artifact_subtype="marker_genes_report")
            except Exception as _exc:
                logger.warning("marker_genes: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc:
        logger.exception("marker_genes 失敗  analysis_id=%s", analysis_id)
        with _get_store().write_conn() as con:
            con.execute(
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id])
            from analysis.failure_diagnosis import classify_exception, write_diagnosis
            write_diagnosis(con, analysis_id, classify_exception(_exc))
        raise

    logger.info("marker_genes 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
