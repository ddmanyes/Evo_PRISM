"""Manual cluster relabeling for MCseg ROI AnnData.

Applies a user-supplied label_map to an existing umap_computed.h5ad,
writes cell_type_manual column, regenerates UMAP plot.

Main function:
    run_relabel_clusters(sample_id, roi_name, label_map, ...) -> (analysis_id, report_path)
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
from analysis.viz_utils import fig_to_b64_md  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402

logger = logging.getLogger(__name__)


@register_tool_on_import(
    tool_name="bio_relabel_clusters",
    version="1.0.0",
    description="依 label_map 手動重標 cluster → cell_type_manual，並重新繪製 UMAP",
)
def run_relabel_clusters(
    sample_id: str,
    roi_name: str,
    label_map: dict[str, str],
    roi_dir: Optional[Path] = None,
    groupby: str = "leiden",
    requested_by: str = "agent",
) -> tuple[str, str]:
    """Apply label_map to groupby column, write cell_type_manual, regenerate UMAP.

    Returns (analysis_id, report_path).
    """
    validate_sample_id(sample_id)
    roi_dir = Path(roi_dir) if roi_dir else MCSEG_RESULTS_ROOT / sample_id / "roi" / roi_name
    h5ad_path = roi_dir / "umap_computed.h5ad"

    if not h5ad_path.exists():
        raise FileNotFoundError(
            f"找不到 umap_computed.h5ad：{h5ad_path}\n請先執行 bio_run_mcseg_roi 完成 Stage 3–6。"
        )
    if not label_map:
        raise ValueError("label_map 不得為空。")

    # Coerce keys to str defensively
    label_map = {str(k): str(v) for k, v in label_map.items()}

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps({"roi_name": roi_name, "groupby": groupby, "label_map": label_map})

    from store.factory import get_store as _get_store

    _get_store().insert_history(
        analysis_id, sample_id, "relabel_clusters", params_json, "running", requested_by, started_at
    )

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import scanpy as sc

        adata = sc.read_h5ad(str(h5ad_path))

        if groupby not in adata.obs.columns:
            raise ValueError(
                f"groupby='{groupby}' 不在 obs 欄位中。可用：{list(adata.obs.columns)}"
            )

        existing_labels = set(adata.obs[groupby].astype(str).unique())
        unknown_keys = set(label_map.keys()) - existing_labels
        warnings: list[str] = []
        if unknown_keys:
            warnings.append(
                f"⚠️ label_map 中以下 key 在 '{groupby}' 不存在（已忽略）：{sorted(unknown_keys)}"
            )

        adata.obs["cell_type_manual"] = (
            adata.obs[groupby].astype(str).map(lambda v: label_map.get(v, v)).astype("category")
        )

        # Write back h5ad (additive column only) — atomic rename to survive crashes
        tmp_path = h5ad_path.with_suffix(".h5ad.tmp")
        adata.write_h5ad(str(tmp_path))
        tmp_path.replace(h5ad_path)  # POSIX atomic rename

        # Regenerate UMAP with new labels
        out_dir = results_dir(sample_id, "relabel_clusters")
        ts = started_at.strftime("%Y%m%d_%H%M%S")
        umap_path = out_dir / f"umap_manual_{sample_id}_{roi_name}_{ts}.png"

        fig, ax = plt.subplots(figsize=(8, 6))
        sc.pl.umap(adata, color="cell_type_manual", ax=ax, show=False)
        fig.tight_layout()
        fig.savefig(umap_path, dpi=150, bbox_inches="tight")
        umap_b64 = fig_to_b64_md(fig, "UMAP（cell_type_manual）")
        plt.close(fig)

        label_counts = adata.obs["cell_type_manual"].value_counts().to_string()
        warn_block = ("\n\n" + "\n".join(warnings)) if warnings else ""
        report_text = (
            f"# Cluster Relabeling — {sample_id} / {roi_name}\n\n"
            f"**生成時間**：{started_at.isoformat()}\n"
            f"**groupby**：`{groupby}`  →  新欄位：`cell_type_manual`\n"
            f"**label_map**：`{label_map}`{warn_block}\n\n"
            f"## Cell Type Manual 分布\n\n```\n{label_counts}\n```\n\n"
            f"## UMAP（cell_type_manual）\n\n{umap_b64}\n\n"
            f"---\n*由 BioAgent analysis/relabel_clusters.py 自動生成*\n"
        )
        report_path = out_dir / f"relabel_{sample_id}_{roi_name}_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        summary = (
            f"{sample_id}/{roi_name} relabel：{len(label_map)} 個 label，共 {adata.n_obs} 細胞"
        )[:50]
        completed_at = datetime.now(timezone.utc)

        with _get_store().write_conn() as con:
            from analysis.tool_registry import get_active_tool_id

            tool_id = get_active_tool_id(con, "bio_relabel_clusters")
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

                register_artifact(
                    con,
                    analysis_id,
                    umap_path,
                    "figure",
                    "UMAP（cell_type_manual）",
                    artifact_subtype="umap_manual",
                )
                register_artifact(
                    con,
                    analysis_id,
                    h5ad_path,
                    "data",
                    "umap_computed.h5ad（含 cell_type_manual）",
                    artifact_subtype="h5ad",
                )
                register_artifact(
                    con,
                    analysis_id,
                    report_path,
                    "report",
                    "Relabel 報告",
                    artifact_subtype="relabel_report",
                )
            except Exception as _exc:
                logger.warning("relabel_clusters: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc:
        logger.exception("relabel_clusters 失敗  analysis_id=%s", analysis_id)
        with _get_store().write_conn() as con:
            con.execute(
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
            from analysis.failure_diagnosis import classify_exception, write_diagnosis

            write_diagnosis(con, analysis_id, classify_exception(_exc))
        raise

    logger.info("relabel_clusters 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
