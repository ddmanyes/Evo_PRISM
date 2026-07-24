"""Multi-ROI AnnData merge + integrated Scanpy pipeline.

Concatenates cellpose_cells.h5ad from multiple ROIs, normalizes, runs
HVG/PCA/integration/UMAP, outputs merged.h5ad + two UMAP plots.

Integration strategies (tried in order if integrate="auto"):
  harmony  → sc.external.pp.harmony_integrate (requires harmonypy)
  bbknn    → sc.external.pp.bbknn            (requires bbknn)
  none     → plain sc.pp.neighbors on PCA

Main function:
    run_mcseg_merge(sample_id, roi_names, merged_name, ...) -> (analysis_id, report_path)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import MCSEG_RESULTS_ROOT  # noqa: E402
from analysis.path_utils import results_dir  # noqa: E402
from analysis.viz_utils import fig_to_b64_md  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402
from analysis.run_context import analysis_run  # noqa: E402

logger = logging.getLogger(__name__)


def _try_integrate(adata, batch_key: str, strategy: str) -> str:
    """Apply batch integration, return the strategy actually used."""
    if strategy in ("auto", "harmony"):
        try:
            import scanpy as sc
            sc.external.pp.harmony_integrate(adata, key=batch_key)
            adata.obsm["X_pca_integrated"] = adata.obsm["X_pca_harmony"]
            sc.pp.neighbors(adata, use_rep="X_pca_harmony")
            return "harmony"
        except Exception as exc:
            if strategy == "harmony":
                raise
            logger.warning("harmony 失敗，嘗試 bbknn：%s", exc)

    if strategy in ("auto", "bbknn"):
        try:
            import scanpy as sc
            sc.external.pp.bbknn(adata, batch_key=batch_key)
            return "bbknn"
        except Exception as exc:
            if strategy == "bbknn":
                raise
            logger.warning("bbknn 失敗，使用無 batch correction：%s", exc)

    import scanpy as sc
    sc.pp.neighbors(adata, use_rep="X_pca")
    return "none"


@register_tool_on_import(
    tool_name="bio_run_mcseg_merge",
    version="1.0.0",
    description="合併多個 MCseg ROI 的 cellpose_cells.h5ad，執行整合 Scanpy 管線（harmony/bbknn/none）",
)
def run_mcseg_merge(
    sample_id: str,
    roi_names: list[str],
    merged_name: str,
    output_base: Optional[Path] = None,
    integrate: str = "auto",
    requested_by: str = "agent",
) -> tuple[str, str]:
    """Merge multiple ROI cellpose_cells.h5ad and run integrated Scanpy pipeline.

    Returns (analysis_id, report_path).
    """
    validate_sample_id(sample_id)
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9_\-]+$', merged_name):
        raise ValueError(
            f"merged_name 只允許英數字、底線、連字號，收到：{merged_name!r}"
        )
    if len(roi_names) < 2:
        raise ValueError("至少需要 2 個 ROI 才能執行合併分析。")
    if integrate not in ("auto", "harmony", "bbknn", "none"):
        raise ValueError(f"integrate 必須是 auto/harmony/bbknn/none，收到：{integrate!r}")

    base = Path(output_base) if output_base else MCSEG_RESULTS_ROOT / sample_id

    # Validate all input files exist first
    h5ad_paths: list[Path] = []
    missing: list[str] = []
    for rn in roi_names:
        p = base / "roi" / rn / "cellpose_cells.h5ad"
        if p.exists():
            h5ad_paths.append(p)
        else:
            missing.append(str(p))
    if missing:
        raise FileNotFoundError(
            f"以下 cellpose_cells.h5ad 不存在（請先執行 bio_run_mcseg_roi）：\n"
            + "\n".join(missing)
        )

    _params = {
        "roi_names": roi_names, "merged_name": merged_name, "integrate": integrate,
    }

    with analysis_run(
        sample_id, "mcseg_merge",
        params=_params,
        requested_by=requested_by,
        tool_name="bio_run_mcseg_merge",
    ) as run:
        import anndata
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import scanpy as sc

        adatas = [sc.read_h5ad(str(p)) for p in h5ad_paths]
        logger.info("Merging %d ROIs: %s", len(roi_names), roi_names)

        merged = anndata.concat(
            adatas,
            label="roi_name",
            keys=roi_names,
            join="outer",
            index_unique="-",
        )
        merged.obs["roi_name"] = merged.obs["roi_name"].astype("category")

        # Scanpy pipeline on raw counts
        sc.pp.filter_genes(merged, min_cells=3)
        sc.pp.normalize_total(merged, target_sum=1e4)
        sc.pp.log1p(merged)
        sc.pp.highly_variable_genes(merged, n_top_genes=2000, batch_key="roi_name")
        merged_hvg = merged[:, merged.var["highly_variable"]].copy()
        sc.pp.scale(merged_hvg)
        sc.tl.pca(merged_hvg, n_comps=30)
        merged.obsm["X_pca"] = merged_hvg.obsm["X_pca"]

        integration_used = _try_integrate(merged, "roi_name", integrate)
        logger.info("Integration strategy used: %s", integration_used)

        sc.tl.leiden(merged)
        sc.tl.umap(merged)

        out_dir = results_dir(sample_id, "mcseg_merge")
        merged_dir = out_dir / merged_name
        merged_dir.mkdir(parents=True, exist_ok=True)
        ts = run.started_at.strftime("%Y%m%d_%H%M%S")

        h5ad_out = merged_dir / "merged.h5ad"
        merged.write_h5ad(str(h5ad_out))

        # UMAP by roi_name
        fig1, ax1 = plt.subplots(figsize=(8, 6))
        sc.pl.umap(merged, color="roi_name", ax=ax1, show=False)
        fig1.tight_layout()
        umap_roi_path = merged_dir / f"umap_by_roi_{ts}.png"
        fig1.savefig(umap_roi_path, dpi=150, bbox_inches="tight")
        umap_roi_b64 = fig_to_b64_md(fig1, "UMAP（by ROI）")
        plt.close(fig1)

        # UMAP by leiden
        fig2, ax2 = plt.subplots(figsize=(8, 6))
        sc.pl.umap(merged, color="leiden", ax=ax2, show=False)
        fig2.tight_layout()
        umap_leiden_path = merged_dir / f"umap_by_leiden_{ts}.png"
        fig2.savefig(umap_leiden_path, dpi=150, bbox_inches="tight")
        umap_leiden_b64 = fig_to_b64_md(fig2, "UMAP（Leiden clustering）")
        plt.close(fig2)

        cell_counts = merged.obs["roi_name"].value_counts().to_string()
        report_text = (
            f"# Multi-ROI Merge — {sample_id} / {merged_name}\n\n"
            f"**生成時間**：{run.started_at.isoformat()}\n"
            f"**ROI**：{roi_names}\n"
            f"**整合策略**：{integration_used}"
            + (" ⚠️（fallback，無 batch correction）" if integration_used == "none" else "")
            + f"\n**合併細胞數**：{merged.n_obs}  **基因數**：{merged.n_vars}\n\n"
            f"## 各 ROI 細胞數\n\n```\n{cell_counts}\n```\n\n"
            f"## UMAP（by ROI）\n\n{umap_roi_b64}\n\n"
            f"## UMAP（Leiden Clustering）\n\n{umap_leiden_b64}\n\n"
            f"---\n*由 BioAgent analysis/mcseg_merge.py 自動生成*\n"
        )
        report_path = merged_dir / f"merge_report_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        summary = (
            f"{sample_id} merge {len(roi_names)} ROIs→{merged.n_obs} cells [{integration_used}]"
        )[:50]
        run.artifact(h5ad_out, "data", "merged.h5ad", "h5ad_merged")
        run.artifact(umap_roi_path, "figure", "UMAP（by ROI）", "umap_roi")
        run.artifact(umap_leiden_path, "figure", "UMAP（Leiden）", "umap_leiden")
        run.artifact(report_path, "report", "Merge 報告", "merge_report")
        run.complete(report_path, summary)

    logger.info("mcseg_merge 完成  analysis_id=%s", run.analysis_id)
    return run.analysis_id, str(report_path)
