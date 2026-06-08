"""
Spatial transcriptomics segmentation quality metrics for Evo_PRISM.

Implements the independent CRC pipeline metrics originally developed for
the MCseg paper (plan_a/MSseg/analysis/scripts/analysis/).  Designed to
accept the output of bio_run_mcseg_roi directly.

Public API
----------
compute_ftc()          — Transcript capture rate (A1)
compute_umi_density()  — Median UMI / µm² per cell
compute_ned()          — Neighbor Expression Divergence (Hellinger)
compute_c1_coexpr()    — Lineage-exclusivity co-expression rate
generate_crc_metrics_report() — Full pipeline → analysis_history
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import scipy.sparse as sp

logger = logging.getLogger("evo_prism.spatial_metrics")

# Default CRC impossible gene pairs (human symbols; override for mouse).
# Source: MCseg paper §2.3 (co-expression impurity metric).
CRC_IMPOSSIBLE_PAIRS: list[list[str]] = [
    ["EPCAM", "CD3E"],
    ["MUC2",  "NKG7"],
    ["ACTA2", "CD3E"],
    ["PECAM1", "EPCAM"],
]

PIXEL_SIZE_UM = 0.2738   # µm / pixel (Visium HD 2µm bin grid)
AREA_SCALE    = PIXEL_SIZE_UM ** 2   # µm² per pixel


# ── Metric helpers ────────────────────────────────────────────────────────

def compute_ftc(
    mask: np.ndarray,
    tp_parquet_path: str | Path,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
) -> float:
    """
    Tissue capture fraction (FTC / A1).

    Counts the fraction of in-tissue 2µm bins inside the ROI whose center
    pixel falls within any cell mask.

    Parameters
    ----------
    mask          : uint32 label array (H, W) in virtual_fullres ROI coords.
    tp_parquet_path : tissue_positions.parquet from Space Ranger output.
    roi_x/y/w/h   : ROI bounding box in virtual_fullres pixels (top-left origin).
    """
    tp_path = Path(tp_parquet_path)
    if not tp_path.exists():
        logger.warning("tissue_positions not found: %s", tp_path)
        return float("nan")

    tp = pd.read_parquet(
        tp_path,
        columns=["barcode", "in_tissue", "pxl_row_in_fullres", "pxl_col_in_fullres"],
    )
    tp = tp[tp["in_tissue"] == 1]

    x0, y0, x1, y1 = roi_x, roi_y, roi_x + roi_w, roi_y + roi_h
    in_roi = (
        (tp["pxl_col_in_fullres"] >= x0) & (tp["pxl_col_in_fullres"] < x1) &
        (tp["pxl_row_in_fullres"] >= y0) & (tp["pxl_row_in_fullres"] < y1)
    )
    tp_roi = tp[in_roi]
    if tp_roi.empty:
        return float("nan")

    row_local = (tp_roi["pxl_row_in_fullres"].values - y0).clip(0, mask.shape[0] - 1)
    col_local = (tp_roi["pxl_col_in_fullres"].values - x0).clip(0, mask.shape[1] - 1)
    attributed = (mask[row_local.astype(int), col_local.astype(int)] > 0).sum()
    return float(attributed / len(tp_roi))


def compute_umi_density(mask: np.ndarray, adata) -> float:
    """
    Median UMI / µm² across all cells.

    Normalises by cell mask area (in µm²) so that large-boundary methods
    (e.g. Space Ranger) do not gain an artificial advantage.
    """
    if mask is None or adata is None or adata.n_obs == 0:
        return float("nan")

    cell_ids = _get_cell_ids(adata)
    umis = _get_umi_counts(adata)

    max_id = int(mask.max())
    if max_id < 1:
        return float("nan")

    pixel_counts = np.bincount(mask.ravel(), minlength=max_id + 1)
    areas_um2 = np.where(
        cell_ids <= max_id,
        pixel_counts[cell_ids.clip(0, max_id)],
        0,
    ).astype(float) * AREA_SCALE

    valid = (areas_um2 > 0) & (umis > 0)
    if valid.sum() == 0:
        return float("nan")

    return float(np.median(umis[valid].astype(float) / areas_um2[valid]))


def compute_ned(mask: np.ndarray, adata, n_hvgs: int = 1000) -> float:
    """
    Neighbor Expression Divergence (NED) — mean Hellinger distance between
    spatially adjacent cell pairs.

    High NED → sharp boundaries (good).
    Low NED  → transcripts leaking across borders (bad).
    """
    if mask is None or adata is None or adata.n_obs < 5 or int(mask.max()) < 2:
        return float("nan")

    X = adata.X
    if sp.issparse(X):
        X = np.asarray(X.todense(), dtype=np.float32)
    else:
        X = np.array(X, dtype=np.float32)

    if X.shape[1] > n_hvgs:
        gene_var = X.var(axis=0)
        hvg_idx = np.argpartition(gene_var, -n_hvgs)[-n_hvgs:]
        X = X[:, hvg_idx]

    row_sums = X.sum(axis=1, keepdims=True)
    X_prob = (X / np.maximum(row_sums, 1e-10)).astype(np.float32)

    cell_ids = _get_cell_ids(adata)
    cid_to_row = {int(c): i for i, c in enumerate(cell_ids)}

    # Find adjacent cell pairs via mask dilation boundary detection
    from scipy.ndimage import grey_dilation
    struct = np.ones((3, 3), dtype=np.int32)
    dilated = grey_dilation(mask.astype(np.int32), footprint=struct)
    bnd = (mask > 0) & (dilated != mask)
    ci = mask[bnd].astype(np.int32)
    cj = dilated[bnd].astype(np.int32)
    valid = (cj > 0) & (cj != ci)
    ci, cj = ci[valid], cj[valid]

    if len(ci) == 0:
        return float("nan")

    pairs = np.unique(np.sort(np.stack([ci, cj], axis=1), axis=1), axis=0)
    known = set(cid_to_row.keys())
    keep = np.array([(int(a) in known and int(b) in known) for a, b in pairs])
    pairs = pairs[keep]

    if len(pairs) < 5:
        return float("nan")

    if len(pairs) > 3000:
        rng = np.random.default_rng(42)
        pairs = pairs[rng.choice(len(pairs), 3000, replace=False)]

    i_idx = np.array([cid_to_row[int(a)] for a in pairs[:, 0]])
    j_idx = np.array([cid_to_row[int(b)] for b in pairs[:, 1]])
    sqrt_i = np.sqrt(np.maximum(X_prob[i_idx], 0))
    sqrt_j = np.sqrt(np.maximum(X_prob[j_idx], 0))
    hell = np.sqrt(np.sum((sqrt_i - sqrt_j) ** 2, axis=1) / 2)
    return float(np.clip(np.mean(hell), 0, 1))


def compute_c1_coexpr(
    adata,
    impossible_pairs: list[list[str]] | None = None,
) -> float:
    """
    Lineage-exclusivity co-expression rate (C1 in MSseg paper).

    Fraction of cells where both genes of a biologically impossible pair
    have raw UMI > 0.  Averaged across all valid pairs.
    """
    if impossible_pairs is None:
        impossible_pairs = CRC_IMPOSSIBLE_PAIRS

    rates = []
    X = adata.X
    for gene_a, gene_b in impossible_pairs:
        if gene_a not in adata.var_names or gene_b not in adata.var_names:
            continue
        idx_a = adata.var_names.get_loc(gene_a)
        idx_b = adata.var_names.get_loc(gene_b)
        col_a = _get_col(X, idx_a)
        col_b = _get_col(X, idx_b)
        coexpr = float(((col_a > 0) & (col_b > 0)).sum() / adata.n_obs)
        rates.append(coexpr)

    return float(np.mean(rates)) if rates else float("nan")


# ── ENACT benchmark (optional) ───────────────────────────────────────────

def compute_enact_precision(
    mask: np.ndarray,
    gt_centroids_csv: str | Path,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
) -> dict:
    """
    ENACT CRC benchmark precision (§3.6 of MCseg paper).

    Checks what fraction of expert-annotated GT centroids (from
    Lotfollahi et al. 2025) fall inside any MCseg cell mask.

    Parameters
    ----------
    gt_centroids_csv : CSV with columns x_centroid, y_centroid (virtual_fullres)
    roi_*            : ROI bounding box in virtual_fullres pixels

    Returns
    -------
    dict with keys: gt_total, gt_in_roi, gt_matched, precision
    """
    gt_path = Path(gt_centroids_csv)
    if not gt_path.exists():
        logger.warning("ENACT GT not found: %s", gt_path)
        return {}

    gt = pd.read_csv(gt_path)
    x_col = next((c for c in gt.columns if "x" in c.lower() and "centroid" in c.lower()), None)
    y_col = next((c for c in gt.columns if "y" in c.lower() and "centroid" in c.lower()), None)
    if x_col is None or y_col is None:
        logger.warning("ENACT CSV missing centroid columns: %s", list(gt.columns))
        return {}

    x0, y0, x1, y1 = roi_x, roi_y, roi_x + roi_w, roi_y + roi_h
    in_roi = (
        (gt[x_col] >= x0) & (gt[x_col] < x1) &
        (gt[y_col] >= y0) & (gt[y_col] < y1)
    )
    gt_roi = gt[in_roi]
    if gt_roi.empty:
        return {"gt_total": len(gt), "gt_in_roi": 0, "gt_matched": 0, "precision": float("nan")}

    row_local = (gt_roi[y_col].values - y0).astype(int).clip(0, mask.shape[0] - 1)
    col_local = (gt_roi[x_col].values - x0).astype(int).clip(0, mask.shape[1] - 1)
    matched = int((mask[row_local, col_local] > 0).sum())
    precision = float(matched / len(gt_roi))

    return {
        "gt_total": len(gt),
        "gt_in_roi": len(gt_roi),
        "gt_matched": matched,
        "precision": precision,
    }


# ── Main report generator ─────────────────────────────────────────────────

def generate_crc_metrics_report(
    sample_id: str,
    roi_name: str,
    mask_path: str | Path,
    adata_cells_path: str | Path,
    tp_parquet_path: str | Path,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
    impossible_pairs: list[list[str]] | None = None,
    enact_gt_csv: str | Path | None = None,
    n_hvgs: int = 1000,
    requested_by: str = "agent",
) -> tuple[str, Path]:
    """
    Compute FTC / UMI_density / NED / C1 from existing MCseg outputs and
    write a result record to analysis_history.

    Returns (analysis_id, report_path).
    """
    from config.settings import DUCKDB_PATH
    from config.db_utils import safe_write
    import scanpy as sc

    analysis_id = str(uuid.uuid4())
    started_at  = datetime.now(timezone.utc).isoformat()
    params = {
        "sample_id": sample_id,
        "roi_name": roi_name,
        "roi_x": roi_x, "roi_y": roi_y,
        "roi_w": roi_w, "roi_h": roi_h,
        "n_hvgs": n_hvgs,
    }

    con = duckdb.connect(str(DUCKDB_PATH))
    safe_write(
        con,
        """INSERT INTO analysis_history
               (analysis_id, sample_id, analysis_type, parameters, status,
                requested_by, started_at)
           VALUES (?, ?, 'crc_metrics', ?, 'running', ?, ?)""",
        [analysis_id, sample_id, json.dumps(params), requested_by, started_at],
    )

    try:
        # Load inputs
        mask = np.load(str(mask_path)).astype(np.uint32)
        adata = sc.read_h5ad(str(adata_cells_path))

        # Compute metrics
        ftc         = compute_ftc(mask, tp_parquet_path, roi_x, roi_y, roi_w, roi_h)
        umi_density = compute_umi_density(mask, adata)
        ned         = compute_ned(mask, adata, n_hvgs=n_hvgs)
        c1          = compute_c1_coexpr(adata, impossible_pairs)
        n_cells     = adata.n_obs

        enact_info: dict = {}
        if enact_gt_csv is not None:
            enact_info = compute_enact_precision(
                mask, enact_gt_csv, roi_x, roi_y, roi_w, roi_h
            )

        # Build markdown report
        lines = [
            f"# CRC Metrics Report — {sample_id} / {roi_name}",
            "",
            f"**analysis_id:** `{analysis_id}`  ",
            f"**generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## RNA Metrics",
            "",
            "| Metric | Value | Paper MCseg (CRC 15-ROI mean) |",
            "|--------|-------|-------------------------------|",
            f"| FTC (Tissue Capture Fraction) | {ftc:.3f} | 0.737 |",
            f"| UMI Density (UMI/µm²) | {umi_density:.3f} | 11.6 |",
            f"| NED (Neighbor Expression Divergence) | {ned:.3f} | 0.727 |",
            f"| C1 Co-expression Rate | {c1:.4f} | 0.0049 |",
            f"| Cells (raw) | {n_cells:,} | — |",
            "",
            "**NED scale:** 0 = no boundary discrimination, 1 = perfect boundary.  ",
            "**C1 lower is better** (fewer biologically impossible co-expressions).",
        ]

        if enact_info:
            prec = enact_info.get("precision", float("nan"))
            lines += [
                "",
                "## ENACT Benchmark (GT Precision)",
                "",
                f"- GT total: {enact_info.get('gt_total', '—')}",
                f"- GT in ROI: {enact_info.get('gt_in_roi', '—')}",
                f"- GT matched (inside mask): {enact_info.get('gt_matched', '—')}",
                f"- **Precision: {prec:.3f}** (paper MCseg WBA: ~0.76)",
            ]

        lines += [
            "",
            "## Configuration",
            "",
            f"- mask: `{mask_path}`",
            f"- adata: `{adata_cells_path}`",
            f"- tissue_positions: `{tp_parquet_path}`",
            f"- ROI bbox: x={roi_x}, y={roi_y}, w={roi_w}, h={roi_h}",
            f"- impossible_pairs: {impossible_pairs or 'CRC defaults'}",
        ]

        report_text = "\n".join(lines)
        summary = (
            f"CRC metrics {roi_name}: FTC={ftc:.3f} UMI_dens={umi_density:.3f} "
            f"NED={ned:.3f} C1={c1:.4f} cells={n_cells}"
        )

        # Save report file
        out_dir = Path(mask_path).parent
        report_path = out_dir / "crc_metrics_report.md"
        report_path.write_text(report_text, encoding="utf-8")

        completed_at = datetime.now(timezone.utc).isoformat()
        safe_write(
            con,
            """UPDATE analysis_history
               SET status='completed', result_path=?, completed_at=?, summary=?
               WHERE analysis_id=?""",
            [str(report_path), completed_at, summary, analysis_id],
        )
        from analysis.failure_diagnosis import success_diagnosis, write_diagnosis
        write_diagnosis(con, analysis_id, success_diagnosis())

        # Persist metric values as artifact JSON for programmatic reuse
        metrics_json = {
            "ftc": ftc, "umi_density": umi_density,
            "ned": ned, "c1_coexpr": c1,
            "n_cells": n_cells,
        }
        if enact_info:
            metrics_json.update(enact_info)

        metrics_path = out_dir / "crc_metrics.json"
        metrics_path.write_text(json.dumps(metrics_json, indent=2), encoding="utf-8")

        logger.info("CRC metrics complete  analysis_id=%s  %s", analysis_id, summary)

    except Exception as _exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception("CRC metrics failed  analysis_id=%s", analysis_id)
        safe_write(
            con,
            "UPDATE analysis_history SET status='failed', summary=? WHERE analysis_id=?",
            [tb[-500:], analysis_id],
        )
        from analysis.failure_diagnosis import classify_exception, write_diagnosis
        write_diagnosis(con, analysis_id, classify_exception(_exc))
        con.close()
        raise

    con.close()
    return analysis_id, report_path


# ── Utilities ─────────────────────────────────────────────────────────────

def _get_cell_ids(adata) -> np.ndarray:
    """Return integer cell IDs from adata.obs['cell_id'] or obs_names."""
    if "cell_id" in adata.obs.columns:
        return adata.obs["cell_id"].values.astype(int)
    try:
        return adata.obs_names.astype(int).values
    except (ValueError, TypeError):
        return np.arange(1, adata.n_obs + 1)


def _get_umi_counts(adata) -> np.ndarray:
    """Return per-cell total UMI counts."""
    for col in ("n_umis", "total_counts", "n_counts"):
        if col in adata.obs.columns:
            return adata.obs[col].values.astype(float)
    X = adata.X
    if sp.issparse(X):
        return np.asarray(X.sum(axis=1)).ravel().astype(float)
    return X.sum(axis=1).astype(float)


def _get_col(X, idx: int) -> np.ndarray:
    col = X[:, idx]
    if sp.issparse(col):
        return np.asarray(col.todense()).ravel()
    return np.asarray(col).ravel()
