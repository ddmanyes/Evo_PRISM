"""CellTypist automated cell type annotation for MCseg ROI results.

Annotates an existing umap_computed.h5ad using a pretrained CellTypist model,
writes celltypist_cell_type column, generates dotplot.

NOTE: CellTypist expects log1p-normalized data at ~10,000 counts/cell.
umap_computed.h5ad already has normalized+log1p X from Stage 3.

NOTE: Most pretrained models are human-derived. For mouse data, gene symbols
(e.g. Krt14 vs KRT14) may not match — check the 'genes_matched' diagnostic
in the report. Use a mouse-compatible model or provide custom markers.

Main function:
    run_celltypist(sample_id, roi_name, ...) -> (analysis_id, report_path)
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
    tool_name="bio_run_celltypist",
    version="1.0.0",
    description="CellTypist 自動細胞類型標注（基於預訓練模型），寫入 celltypist_cell_type 欄位",
)
def run_celltypist(
    sample_id: str,
    roi_name: str,
    roi_dir: Optional[Path] = None,
    model: str = "Immune_All_Low.pkl",
    majority_voting: bool = True,
    requested_by: str = "agent",
) -> tuple[str, str]:
    """Annotate cells using CellTypist pretrained model.

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

    try:
        import celltypist  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "缺少 celltypist，請執行：uv add celltypist"
        ) from exc

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps({
        "roi_name": roi_name, "model": model, "majority_voting": majority_voting,
    })

    from store.factory import get_store as _get_store
    _get_store().insert_history(
        analysis_id, sample_id, "celltypist", params_json, "running", requested_by, started_at
    )

    try:
        import celltypist
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import scanpy as sc

        adata = sc.read_h5ad(str(h5ad_path))

        # Download model if not cached
        logger.info("CellTypist: downloading/loading model %s", model)
        celltypist.models.download_models(model=model, force_update=False)
        ct_model = celltypist.models.Model.load(model=model)

        # Diagnostic: gene overlap
        adata_genes = set(adata.var_names)
        model_features = set(ct_model.features) if hasattr(ct_model, "features") else set()
        n_overlap = len(adata_genes & model_features) if model_features else None
        n_model = len(model_features) if model_features else None
        overlap_msg = (
            f"{n_overlap}/{n_model} 基因匹配" if n_overlap is not None
            else "（無法計算基因匹配數）"
        )

        logger.info("CellTypist: annotating %d cells, gene overlap=%s", adata.n_obs, overlap_msg)
        predictions = celltypist.annotate(adata, model=ct_model, majority_voting=majority_voting)

        pred_df = predictions.predicted_labels
        if majority_voting and "majority_voting" in pred_df.columns:
            label_col = "majority_voting"
        else:
            label_col = "predicted_labels"
        adata.obs["celltypist_cell_type"] = pred_df[label_col].values

        # Write back h5ad (additive column) — atomic rename to survive crashes
        import shutil
        tmp_path = h5ad_path.with_suffix(".h5ad.tmp")
        adata.write_h5ad(str(tmp_path))
        tmp_path.replace(h5ad_path)  # POSIX atomic rename

        out_dir = results_dir(sample_id, "celltypist")
        ts = started_at.strftime("%Y%m%d_%H%M%S")

        # UMAP colored by celltypist label
        umap_path = out_dir / f"umap_celltypist_{sample_id}_{roi_name}_{ts}.png"
        fig, ax = plt.subplots(figsize=(8, 6))
        sc.pl.umap(adata, color="celltypist_cell_type", ax=ax, show=False)
        fig.tight_layout()
        fig.savefig(umap_path, dpi=150, bbox_inches="tight")
        umap_b64 = fig_to_b64_md(fig, "UMAP（celltypist_cell_type）")
        plt.close(fig)

        label_counts = adata.obs["celltypist_cell_type"].value_counts().to_string()
        report_text = (
            f"# CellTypist 標注 — {sample_id} / {roi_name}\n\n"
            f"**生成時間**：{started_at.isoformat()}\n"
            f"**模型**：`{model}`  **majority_voting**：{majority_voting}\n"
            f"**基因匹配**：{overlap_msg}\n\n"
            f"> ⚠️ 大多數預訓練模型為人類資料。"
            f"若為小鼠樣本請確認基因匹配數是否充足（建議 > 50%）。\n\n"
            f"## CellTypist Cell Type 分布\n\n```\n{label_counts}\n```\n\n"
            f"## UMAP（celltypist_cell_type）\n\n{umap_b64}\n\n"
            f"---\n*由 BioAgent analysis/celltypist_annotate.py 自動生成*\n"
        )
        report_path = out_dir / f"celltypist_{sample_id}_{roi_name}_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        top_type = adata.obs["celltypist_cell_type"].value_counts().index[0]
        summary = f"{sample_id}/{roi_name} CellTypist({model[:20]})：top={top_type}"[:50]
        completed_at = datetime.now(timezone.utc)

        with _get_store().write_conn() as con:
            from analysis.tool_registry import get_active_tool_id
            tool_id = get_active_tool_id(con, "bio_run_celltypist")
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
                register_artifact(con, analysis_id, umap_path, "figure",
                                  "UMAP（celltypist_cell_type）", artifact_subtype="umap_celltypist")
                register_artifact(con, analysis_id, h5ad_path, "data",
                                  "umap_computed.h5ad（含 celltypist_cell_type）",
                                  artifact_subtype="h5ad")
                register_artifact(con, analysis_id, report_path, "report",
                                  "CellTypist 報告", artifact_subtype="celltypist_report")
            except Exception as _exc:
                logger.warning("celltypist: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc:
        logger.exception("celltypist 失敗  analysis_id=%s", analysis_id)
        with _get_store().write_conn() as con:
            con.execute(
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id])
            from analysis.failure_diagnosis import classify_exception, write_diagnosis
            write_diagnosis(con, analysis_id, classify_exception(_exc))
        raise

    logger.info("celltypist 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
