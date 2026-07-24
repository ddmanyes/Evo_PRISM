"""Generic single-cell QC + clustering + UMAP tool for post-mcseg AnnData.

Naturally follows bio_run_mcseg_fullslide/roi's RNA counting step — that step only
produces a raw counts matrix (cellpose_cells_fullslide.h5ad / cellpose_cells.h5ad),
with no QC filtering, normalization, or clustering. bio_run_mcseg_roi already has
its own Stage 3-7 downstream (via scratch/run_visium_hd_showcase.py), but that
pipeline hardcodes a skin-lineage marker dict — fine for the ROI showcase, but not
reusable across samples/tissues. bio_run_mcseg_fullslide has no downstream at all.

This module is the generic version of that Stage 3 (QC/HVG/PCA/neighbors/leiden/
UMAP is plain scanpy, no dataset-specific assumptions) plus a *parameterized*
version of Stage 4 (marker_gene_sets is caller-supplied, not a hardcoded dict) — see
sb project note "康育 VisiumHD 傷口機轉分析方法建置進 EP 工具箱 — 規劃" Phase 3.
Cluster→cell-type assignment stays semi-automatic: rank_genes_groups always runs
(so the caller gets top marker genes per cluster to inspect/label manually), and
marker_gene_sets-based auto-scoring is opt-in on top of that.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT  # noqa: E402
from analysis.path_utils import results_dir  # noqa: E402
from analysis.viz_utils import fig_to_b64_md  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402
from analysis.run_context import analysis_run  # noqa: E402

logger = logging.getLogger("evo_prism.sc_clustering")


@register_tool_on_import(
    tool_name="run_sc_clustering",
    version="1.0.0",
    description=(
        "任意單細胞 h5ad 的 QC 過濾 + Leiden clustering + UMAP（標準 scanpy 管線："
        "normalize→log1p→HVG→PCA→neighbors→leiden→umap）,並輸出每個 cluster 的"
        "top marker genes（rank_genes_groups）。可選 marker_gene_sets 對每個"
        "cluster 做 score_genes-based 自動細胞型別評分(argmax),沒給就只輸出"
        "marker genes 供人工判讀——細胞型別對照表刻意不寫死,是 bio_run_mcseg_roi "
        "Stage 3-4 的泛用版本(那個版本的細胞型別是寫死的皮膚 lineage,不能跨組織"
        "重用)。天然銜接 bio_run_mcseg_fullslide 的輸出(cellpose_cells_fullslide."
        "h5ad,尚未經過任何 QC/clustering)。"
    ),
)
def run_sc_clustering(
    sample_id: str,
    h5ad_path: str | Path,
    out_dir: str | Path,
    min_counts: int | None = None,
    min_genes: int | None = None,
    qc_percentile: int = 10,
    min_bins: int = 0,
    n_top_genes: int = 1000,
    n_pcs: int = 15,
    n_neighbors: int = 15,
    resolution: float = 0.5,
    n_top_markers: int = 10,
    marker_gene_sets: dict[str, list[str]] | None = None,
    score_threshold: float = 0.05,
    unassigned_label: str = "Unassigned",
    requested_by: str = "agent",
) -> dict:
    """
    QC-filter, cluster (Leiden), and UMAP-embed an arbitrary single-cell h5ad.

    Args:
        h5ad_path: input AnnData (raw counts in .X), must be under BIO_DB_ROOT.
        min_counts / min_genes: hard QC thresholds. If either is None, it's set
            adaptively to max(floor, qc_percentile-th percentile of the observed
            distribution) — same heuristic as bio_run_mcseg_roi's Stage 3, just
            exposed as a parameter instead of hardcoded p10.
        qc_percentile: percentile used for the adaptive threshold above (ignored
            for whichever of min_counts/min_genes was given explicitly).
        min_bins: if the input has an obs['n_bins'] column (true for mcseg
            fullslide/roi RNA-counting output — number of Visium bins mapped to
            each segmented cell), drop cells with n_bins < min_bins. Cells with
            n_bins == 0 have no RNA signal at all (mask-detected but no bin
            overlap) and are typically not analyzable. Default 0 = no filtering
            (opt-in, since not every caller's h5ad has this column).
        n_top_genes: HVG count for PCA input.
        n_pcs / n_neighbors: passed to sc.pp.neighbors.
        resolution: sc.tl.leiden resolution.
        n_top_markers: top marker genes per cluster to report (wilcoxon rank_genes_groups).
        marker_gene_sets: optional {cell_type: [genes...]}. If given, each cluster's
            mean per-module score_genes score is computed and the cluster is
            assigned argmax(module) if that score > score_threshold, else
            unassigned_label. This is cluster-level (not cell-level) assignment —
            deliberately coarser than bio_run_mcseg_roi's per-cell scoring, since
            a caller-supplied marker set for an arbitrary tissue is more reliable
            at resolving "which cluster is this" than "which single cell is this".
        out_dir: must be under BIO_DB_ROOT.

    Returns:
        {"analysis_id", "artifact_ids", "h5ad_out", "n_cells_before", "n_cells_after",
         "n_clusters", "cluster_sizes": {...}, "top_markers": {cluster: [genes...]},
         "cell_type_assignment": {cluster: label} | None}
    """
    import json
    import uuid
    from datetime import datetime

    import anndata
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import scanpy as sc

    # obs index / string columns can end up as pandas nullable StringArray after
    # boolean-mask subsetting (e.g. the min_bins filter below) on newer pandas —
    # anndata 0.11 refuses to write those by default. This is anndata's own
    # documented opt-in for a case it considers still-experimental but stable
    # enough here (plain string data, no actual missing values).
    anndata.settings.allow_write_nullable_strings = True

    from store.factory import get_store

    validate_sample_id(sample_id)
    h5ad_path = Path(h5ad_path)
    out_dir = Path(out_dir)

    store = get_store()
    if not store.get_sample(sample_id):
        raise ValueError(f"sample_id '{sample_id}' 不存在於 sample_registry，請先 bio_register_sample。")
    if not h5ad_path.exists():
        raise FileNotFoundError(f"h5ad_path 不存在：{h5ad_path}")
    for label, p in [("h5ad_path", h5ad_path), ("out_dir", out_dir)]:
        try:
            p.relative_to(BIO_DB_ROOT)
        except ValueError:
            raise ValueError(f"{label} 必須在 BIO_DB_ROOT（{BIO_DB_ROOT}）底下；收到 {p}")
    if not (0 < qc_percentile < 100):
        raise ValueError(f"qc_percentile 必須在 0-100 之間，收到 {qc_percentile}")

    params = {
        "h5ad_path": str(h5ad_path), "min_counts": min_counts, "min_genes": min_genes,
        "qc_percentile": qc_percentile, "min_bins": min_bins, "n_top_genes": n_top_genes,
        "n_pcs": n_pcs, "n_neighbors": n_neighbors, "resolution": resolution,
        "marker_gene_sets": marker_gene_sets, "score_threshold": score_threshold,
    }

    with analysis_run(
        sample_id, "sc_clustering",
        params=params,
        requested_by=requested_by,
        tool_name="run_sc_clustering",
    ) as run:
        adata = sc.read_h5ad(str(h5ad_path))
        n_cells_before = int(adata.n_obs)
        logger.info(f"run_sc_clustering: loaded {n_cells_before} cells x {adata.n_vars} genes")

        if min_bins > 0:
            if "n_bins" not in adata.obs.columns:
                raise ValueError("min_bins > 0 但 adata.obs 沒有 'n_bins' 欄位")
            adata = adata[adata.obs["n_bins"] >= min_bins].copy()
            logger.info(f"run_sc_clustering: after min_bins={min_bins} filter: {adata.n_obs} cells")

        sc.pp.calculate_qc_metrics(adata, inplace=True, percent_top=None)
        counts_arr = np.asarray(adata.obs["total_counts"], dtype=float)
        genes_arr = np.asarray(adata.obs["n_genes_by_counts"], dtype=float)
        eff_min_counts = min_counts if min_counts is not None else max(
            50, int(np.percentile(counts_arr, qc_percentile))
        )
        eff_min_genes = min_genes if min_genes is not None else max(
            20, int(np.percentile(genes_arr, qc_percentile))
        )
        logger.info(f"run_sc_clustering: QC thresholds min_counts={eff_min_counts}, min_genes={eff_min_genes}")

        sc.pp.filter_cells(adata, min_counts=eff_min_counts)
        sc.pp.filter_cells(adata, min_genes=eff_min_genes)
        sc.pp.filter_genes(adata, min_cells=3)
        n_cells_after = int(adata.n_obs)
        logger.info(f"run_sc_clustering: after QC: {n_cells_after} cells x {adata.n_vars} genes")

        if adata.n_obs < 15 or adata.n_vars == 0:
            raise ValueError(
                f"QC 過濾後剩餘細胞數({adata.n_obs})或基因數({adata.n_vars})不足以聚類。"
                f"請放寬 min_counts/min_genes 或 qc_percentile。"
            )

        adata.layers["counts"] = adata.X.copy()
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)

        sc.pp.highly_variable_genes(adata, n_top_genes=min(n_top_genes, adata.n_vars), subset=False)
        adata_hvg = adata[:, adata.var.highly_variable].copy()
        sc.pp.scale(adata_hvg, max_value=10)
        n_pcs_eff = min(n_pcs, adata_hvg.n_obs - 1, adata_hvg.n_vars - 1)
        sc.tl.pca(adata_hvg, n_comps=n_pcs_eff, svd_solver="arpack")
        sc.pp.neighbors(adata_hvg, n_neighbors=n_neighbors, n_pcs=n_pcs_eff)
        sc.tl.umap(adata_hvg, min_dist=0.5)
        sc.tl.leiden(adata_hvg, resolution=resolution)

        adata.obsm["X_pca"] = adata_hvg.obsm["X_pca"]
        adata.obsm["X_umap"] = adata_hvg.obsm["X_umap"]
        adata.obs["leiden"] = adata_hvg.obs["leiden"]

        cluster_sizes = adata.obs["leiden"].value_counts().sort_index()
        n_clusters = int(cluster_sizes.shape[0])
        logger.info(f"run_sc_clustering: {n_clusters} clusters, sizes={cluster_sizes.to_dict()}")

        # Top marker genes per cluster (wilcoxon), always computed — this is the
        # semi-automatic path: caller inspects these to label clusters manually.
        sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
        top_markers: dict[str, list[str]] = {}
        for cl in cluster_sizes.index:
            names = adata.uns["rank_genes_groups"]["names"][cl][:n_top_markers]
            top_markers[str(cl)] = [str(g) for g in names]

        # Optional cluster-level auto cell-type assignment from caller-supplied marker sets.
        cell_type_assignment: dict[str, str] | None = None
        if marker_gene_sets:
            score_cols = []
            for name, genes in marker_gene_sets.items():
                present = [g for g in genes if g in adata.var_names]
                col = f"_score_{name}"
                if present:
                    sc.tl.score_genes(adata, present, score_name=col)
                else:
                    adata.obs[col] = 0.0
                    logger.warning(f"run_sc_clustering: marker set '{name}' 沒有基因出現在 var_names")
                score_cols.append(col)

            module_names = list(marker_gene_sets.keys())
            per_cluster_mean = adata.obs.groupby("leiden", observed=True)[score_cols].mean()
            cell_type_assignment = {}
            for cl, row in per_cluster_mean.iterrows():
                best_i = int(np.argmax(row.values))
                best_score = float(row.values[best_i])
                cell_type_assignment[str(cl)] = (
                    module_names[best_i] if best_score > score_threshold else unassigned_label
                )
            adata.obs["cell_type"] = adata.obs["leiden"].astype(str).map(cell_type_assignment).astype("category")
            adata.obs.drop(columns=score_cols, inplace=True)

        out_dir.mkdir(parents=True, exist_ok=True)
        ts = run.started_at.strftime("%Y%m%d_%H%M%S")

        h5ad_out = out_dir / "clustered.h5ad"
        adata.write_h5ad(str(h5ad_out))

        markers_csv = out_dir / "top_markers_per_cluster.csv"
        pd.DataFrame(
            [(cl, i, g) for cl, genes in top_markers.items() for i, g in enumerate(genes)],
            columns=["leiden", "rank", "gene"],
        ).to_csv(markers_csv, index=False)

        color_key = "cell_type" if cell_type_assignment else "leiden"
        fig, ax = plt.subplots(figsize=(8, 6))
        sc.pl.umap(adata, color=color_key, ax=ax, show=False)
        fig.tight_layout()
        umap_path = out_dir / f"umap_{color_key}_{ts}.png"
        fig.savefig(umap_path, dpi=150, bbox_inches="tight")
        umap_b64 = fig_to_b64_md(fig, f"UMAP（{color_key}）")
        plt.close(fig)

        markers_md = "\n".join(
            f"- **cluster {cl}** ({cluster_sizes[cl]:,} cells)"
            + (f" → `{cell_type_assignment[cl]}`" if cell_type_assignment else "")
            + f": {', '.join(genes)}"
            for cl, genes in top_markers.items()
        )
        report_text = (
            f"# Single-cell Clustering — {sample_id}\n\n"
            f"**生成時間**：{run.started_at.isoformat()}\n"
            f"**輸入**：{h5ad_path}\n"
            f"**QC**：min_counts={eff_min_counts}, min_genes={eff_min_genes}"
            f"（{'adaptive p' + str(qc_percentile) if min_counts is None or min_genes is None else 'explicit'}）\n"
            f"**細胞數**：{n_cells_before:,} → {n_cells_after:,}（QC 後）\n"
            f"**Cluster 數**：{n_clusters}（resolution={resolution}）\n\n"
            f"## Cluster Marker Genes（top {n_top_markers}, wilcoxon）\n\n{markers_md}\n\n"
            f"## UMAP（{color_key}）\n\n{umap_b64}\n\n"
            f"---\n*由 BioAgent analysis/sc_clustering.py 自動生成*\n"
        )
        report_path = out_dir / f"clustering_report_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        summary = f"{sample_id} clustering: {n_cells_after:,} cells → {n_clusters} clusters"[:80]

        # artifacts 走專用 DuckDB con 以收集 artifact_ids 回傳；history 收尾由 seam 處理。
        from analysis.artifact_registry import register_artifact
        from config.db_utils import connect_db
        from config.settings import DUCKDB_PATH

        _acon = connect_db(DUCKDB_PATH)
        try:
            artifact_ids = [
                register_artifact(_acon, run.analysis_id, h5ad_out, "data",
                                  "clustered.h5ad（含 leiden/UMAP）", artifact_subtype="h5ad_clustered"),
                register_artifact(_acon, run.analysis_id, markers_csv, "table",
                                  "Top marker genes per cluster", artifact_subtype="cluster_markers"),
                register_artifact(_acon, run.analysis_id, umap_path, "figure",
                                  f"UMAP（{color_key}）", artifact_subtype="umap_clustering"),
                register_artifact(_acon, run.analysis_id, report_path, "report",
                                  "Clustering 報告", artifact_subtype="clustering_report"),
            ]
        finally:
            _acon.close()

        run.complete(report_path, summary)

    logger.info(f"run_sc_clustering 完成  analysis_id={run.analysis_id}  {summary}")
    return {
        "analysis_id": run.analysis_id,
        "artifact_ids": artifact_ids,
        "h5ad_out": str(h5ad_out),
        "report_path": str(report_path),
        "n_cells_before": n_cells_before,
        "n_cells_after": n_cells_after,
        "n_clusters": n_clusters,
        "cluster_sizes": {str(k): int(v) for k, v in cluster_sizes.items()},
        "top_markers": top_markers,
        "cell_type_assignment": cell_type_assignment,
    }
