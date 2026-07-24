"""
Phase 3 — Standard baseline analysis + HTML report for a Visium HD sample.

Generates the standardized figure suite defined in lcdda note
"Visium HD標準基礎分析與報告產出設計" (30-resources/), designed from lessons
learned in the dpcp01/HF01_VH_SDS case (see that note's "設計動機" section
for the specific incidents each check guards against).

Usage:
    uv run python scripts/03_generate_baseline_report.py --sample-id <id> \
        --mcseg-h5ad <path to clustered h5ad with leiden> \
        [--multi-timepoint --day-cutpoints 900,1600,2300 --day-labels Day3,Day2,Day1,Day0] \
        [--roi-2um-x 4758 --roi-2um-y 5424 --roi-2um-w 256 --roi-2um-h 4554]

Only wires up what's needed to run end-to-end on already-clustered mcseg h5ad
output; raw TIFF/tissue_positions inputs are optional (sections are skipped
with a clear placeholder if not provided, never fabricated).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.spatial import cKDTree
from scipy.stats import mannwhitneyu

import scipy.ndimage as ndi
from analysis.mcseg_quality import celltype_overlay_plot

# Qualitative palette for cluster-label overlays; "Other"/unlabeled always gray (see mcseg_quality.celltype_overlay_plot)
_OVERLAY_PALETTE_HEX = ["#E74C3C", "#2ECC71", "#3498DB", "#9B59B6", "#F39C12", "#1ABC9C", "#E91E8C", "#34495E"]


def _load_mask_or_image(path: str):
    """讀 .npy(整數標籤遮罩或已存好的影像陣列)或一般圖片格式(png/tif)。"""
    if path.endswith(".npy"):
        return np.load(path, mmap_mode="r")
    return plt.imread(path)


def _resolve_mask_px_scale(mask: np.ndarray, adata, n_sample: int = 50) -> float:
    """先驗證mask是否直接(scale=1)對應obs['centroid_x_px']/['centroid_y_px'](用cell_id
    精確比對，不是猜)；不match才退回 _compute_mask_px_scale() 的經驗縮放係數估計。

    Guards against: dpcp01案例裡，同一個樣本先後產出過4種不同像素空間的mask檔案
    (raw TIFF px全解析度、virtual_fullres px、對其中一版"CORRECTED"重跑、再降解析度
    做「for_overlay」)，且 centroid_x_px 這個欄位名稱雖然暗示"raw TIFF px"，實際上
    在_CORRECTED版本的h5ad裡其實是virtual_fullres px——同名欄位在不同處理版本下語意
    不同，唯一可靠的做法是直接拿cell_id去mask裡驗證對不對得上，不能只看欄位名稱或
    陣列shape就假設座標系統。"""
    cell_ids = adata.obs["cell_id"].astype(int).values
    rng = np.random.default_rng(0)
    sample = rng.choice(cell_ids, size=min(n_sample, len(cell_ids)), replace=False)
    obs_px = adata.obs.set_index(adata.obs["cell_id"].astype(int))[["centroid_x_px", "centroid_y_px"]]
    hits = 0
    for cid in sample:
        px_x, px_y = obs_px.loc[cid, "centroid_x_px"], obs_px.loc[cid, "centroid_y_px"]
        yy, xx = int(round(px_y)), int(round(px_x))
        if 0 <= yy < mask.shape[0] and 0 <= xx < mask.shape[1] and mask[yy, xx] == cid:
            hits += 1
    match_rate = hits / len(sample)
    if match_rate > 0.8:
        return 1.0
    scale = _compute_mask_px_scale(mask, adata, n_sample=300)
    logger_msg = (f"_resolve_mask_px_scale: scale=1 match_rate={match_rate:.0%}，"
                  f"改用經驗估計 scale={scale:.4f}")
    print(logger_msg)
    return scale


def _build_cell_id_to_label(adata, cluster_labels: dict[str, str]) -> tuple[dict[int, str], dict[str, tuple[str, float]]]:
    """cell_id (mask 標籤值) -> 人工判讀的cluster標籤，供 celltype_overlay_plot() 使用。"""
    leiden = adata.obs["leiden"].astype(str)
    labels = leiden.map(cluster_labels)
    cell_ids = adata.obs["cell_id"].astype(int)
    mapping = {int(cid): lbl for cid, lbl in zip(cell_ids, labels) if pd.notna(lbl)}
    distinct = sorted(set(cluster_labels.values()))
    palette = {lbl: (_OVERLAY_PALETTE_HEX[i % len(_OVERLAY_PALETTE_HEX)], 0.75) for i, lbl in enumerate(distinct)}
    return mapping, palette


# ── Section 0: alignment check ──────────────────────────────────────────
def fig_alignment_check(tiff_path: str | None, hires_path: str | None, out_path: Path) -> dict:
    """Side-by-side TIFF vs tissue_hires_image thumbnail for visual alignment sanity check.

    Guards against: dpcp01 case where binned_outputs from a stale path had
    tissue_hires_scalef=1.0 (a default value, not a real registration) and
    didn't match the TIFF at all -- only caught after an 80.7h full-slide
    segmentation run had already completed on the wrong coordinate space.
    """
    if not tiff_path or not hires_path:
        return {"status": "skipped", "reason": "tiff_path/hires_path not provided"}
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    hi = Image.open(hires_path)
    axes[0].imshow(hi)
    axes[0].set_title(f"tissue_hires_image.png\n{hi.size}")
    axes[0].axis("off")
    tf = Image.open(tiff_path)
    tf.thumbnail((2000, 2000))
    axes[1].imshow(tf)
    axes[1].set_title(f"raw TIFF (thumbnail)\noriginal size differs, check tissue outline matches")
    axes[1].axis("off")
    fig.suptitle("Alignment check: does the tissue outline match between the two images?")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return {"status": "generated", "path": str(out_path)}


def check_scalef_suspicious(scalefactors_json_path: str | None) -> dict:
    """Flag tissue_hires_scalef == 1.0 as a known red flag (default value, not real registration)."""
    if not scalefactors_json_path or not Path(scalefactors_json_path).exists():
        return {"status": "skipped"}
    with open(scalefactors_json_path) as f:
        sf = json.load(f)
    val = sf.get("tissue_hires_scalef")
    suspicious = val is not None and abs(val - 1.0) < 1e-9
    return {"status": "checked", "tissue_hires_scalef": val, "suspicious_default_value": suspicious}


# ── Section 1-2: QC overview + QC spatial ───────────────────────────────
def fig_qc_overview(adata, out_path: Path, min_counts: int, min_genes: int, n_raw: int) -> dict:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].hist(adata.obs["total_counts"], bins=60, color="#4c72b0", edgecolor="none")
    axes[0].axvline(min_counts, color="red", linestyle="--", label=f"min_counts={min_counts}")
    axes[0].set_xlabel("total_counts per cell")
    axes[0].set_title("total_counts distribution")
    axes[0].legend(fontsize=8)
    axes[0].set_xlim(0, np.percentile(adata.obs["total_counts"], 99))

    gene_col = "n_genes_by_counts" if "n_genes_by_counts" in adata.obs else "n_genes"
    axes[1].hist(adata.obs[gene_col], bins=60, color="#dd8452", edgecolor="none")
    axes[1].axvline(min_genes, color="red", linestyle="--", label=f"min_genes={min_genes}")
    axes[1].set_xlabel("n_genes per cell")
    axes[1].set_title("n_genes distribution")
    axes[1].legend(fontsize=8)
    axes[1].set_xlim(0, np.percentile(adata.obs[gene_col], 99))
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)

    n_post = adata.n_obs
    retention = n_post / n_raw if n_raw else float("nan")
    return {"n_raw": n_raw, "n_post_qc": n_post, "retention_pct": round(retention * 100, 1)}


def fig_qc_spatial(adata, out_path: Path) -> dict:
    xy = adata.obsm["spatial"]
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(xy[:, 0], xy[:, 1], s=1, alpha=0.4, color="#55a868")
    ax.set_title(f"QC-passed cells spatial distribution (n={adata.n_obs:,})")
    ax.set_xlabel("x"); ax.set_ylabel("y")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return {"status": "generated"}


# ── Section 4: clustering (UMAP + dotplot) ──────────────────────────────
def fig_clustering(adata, out_umap: Path, out_dotplot: Path, marker_genes: dict[str, list[str]]) -> dict:
    import scanpy as sc
    if "X_umap" not in adata.obsm:
        a2 = adata.copy()
        sc.pp.normalize_total(a2, target_sum=1e4)
        sc.pp.log1p(a2)
        sc.pp.highly_variable_genes(a2, n_top_genes=2000)
        a2 = a2[:, a2.var.highly_variable].copy()
        sc.pp.scale(a2, max_value=10)
        sc.tl.pca(a2, n_comps=min(50, a2.n_obs - 1))
        sc.pp.neighbors(a2)
        sc.tl.umap(a2)
        adata.obsm["X_umap"] = a2.obsm["X_umap"]

    fig = sc.pl.umap(adata, color="leiden", show=False, return_fig=True)
    fig.savefig(out_umap, dpi=140, bbox_inches="tight")
    plt.close(fig)

    genes_present = {k: [g for g in v if g in adata.var_names] for k, v in marker_genes.items()}
    flat = [g for genes in genes_present.values() for g in genes]
    if flat:
        a3 = adata.copy()
        sc.pp.normalize_total(a3, target_sum=1e4)
        sc.pp.log1p(a3)
        fig2 = sc.pl.dotplot(a3, genes_present, groupby="leiden", show=False, return_fig=True)
        fig2.savefig(out_dotplot, dpi=140, bbox_inches="tight")
        plt.close("all")
    return {"n_clusters": adata.obs["leiden"].nunique()}


# ── Section 5: cell-type composition (manual marker-based, not argmax) ──
def fig_celltype_composition(adata, cluster_labels: dict[str, str], out_path: Path) -> dict:
    """cluster_labels: {leiden_cluster_id: manual_label}. Never trust argmax alone
    for rare populations -- this is meant to be filled in by a human after
    reviewing wilcoxon top markers (see 4.6 lesson in the origin case)."""
    leiden = adata.obs["leiden"].astype(str)
    labels = leiden.map(cluster_labels).fillna("unassigned")
    counts = labels.value_counts()
    fig, ax = plt.subplots(figsize=(7, 4))
    counts.plot.bar(ax=ax, color="#4c72b0")
    ax.set_ylabel("n cells")
    ax.set_title("Cell-type composition (manual marker-based labels)")
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return dict(counts)


# ── Section 6: key gene spatial maps (single-cell) ──────────────────────
def fig_gene_spatial_maps(adata, genes: list[str], out_path: Path) -> dict:
    xy = adata.obsm["spatial"]
    genes_present = [g for g in genes if g in adata.var_names]
    n = len(genes_present)
    if n == 0:
        return {"status": "no genes found"}
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4))
    if n == 1:
        axes = [axes]
    for ax, g in zip(axes, genes_present):
        v = adata[:, g].X
        v = np.asarray(v.todense()).flatten() if hasattr(v, "todense") else np.asarray(v).flatten()
        sc_plot = ax.scatter(xy[:, 0], xy[:, 1], c=v, s=2, cmap="RdYlBu_r")
        ax.set_title(g)
        ax.invert_yaxis()
        ax.axis("off")
        plt.colorbar(sc_plot, ax=ax, fraction=0.04)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return {"genes_plotted": genes_present}


# ── Section: pooled vs within-group statistical test (dual display, mandatory) ──
def stat_pooled_vs_within_group(coords: np.ndarray, group_a: np.ndarray, group_b: np.ndarray,
                                 target: np.ndarray, group_labels: pd.Series) -> pd.DataFrame:
    """Guards against the dpcp01 case: pooled p=4.4e-16 was almost entirely
    driven by a single day; within-day tests showed no effect in 2 of 3 days.
    NEVER report only the pooled result for a cross-group comparison."""
    rows = []
    tree_all = cKDTree(coords[target])
    da, _ = tree_all.query(coords[group_a])
    db, _ = tree_all.query(coords[group_b])
    try:
        _, p_pooled = mannwhitneyu(da, db, alternative="two-sided")
    except ValueError:
        p_pooled = float("nan")
    rows.append({"scope": "POOLED (all groups combined)", "n_a": group_a.sum(), "n_b": group_b.sum(),
                 "mean_a": da.mean(), "mean_b": db.mean(), "p_value": p_pooled})

    for g in sorted(group_labels.unique()):
        gm = (group_labels == g).values
        ta = coords[gm & target]
        if len(ta) < 5:
            rows.append({"scope": g, "n_a": np.nan, "n_b": np.nan, "mean_a": np.nan, "mean_b": np.nan,
                         "p_value": np.nan})
            continue
        tree = cKDTree(ta)
        ga = coords[gm & group_a]
        gb = coords[gm & group_b]
        if len(ga) < 3 or len(gb) < 3:
            continue
        da_g, _ = tree.query(ga)
        db_g, _ = tree.query(gb)
        _, p = mannwhitneyu(da_g, db_g, alternative="two-sided")
        rows.append({"scope": g, "n_a": len(ga), "n_b": len(gb), "mean_a": da_g.mean(),
                     "mean_b": db_g.mean(), "p_value": p})
    return pd.DataFrame(rows)


# ── Section 3: real mcseg annotation overlay (H&E + mask, filled by cell type) ──
def fig_annotation_overlay(mask_path: str | None, he_path: str | None, adata,
                            cluster_labels: dict[str, str], out_path: Path,
                            um_per_px: float | None = None, max_dim: int = 3000) -> dict:
    """真正的分割+annotation疊圖：H&E背景 + 依cluster標籤填色的細胞區域 + 邊界線。

    需要 --mcseg-mask-path(整數標籤陣列 .npy，0=背景) 與 --mcseg-he-path(同像素空間
    對齊的H&E，.npy或一般圖片皆可)。兩者對不對得上**用cell_id實際比對驗證**(見
    _resolve_mask_px_scale)，不能只看檔名或shape就假設——dpcp01案例第一版疊圖(用
    tissue_hires_image_CORRECT.png)shape雖然跟mask一致，實際內容卻完全沒對齊，
    要重新從raw TIFF用_compute_tiff_scale()的已知係數resample才修好。未提供路徑
    則明確標註略過，不臆測或用邊界線代替。

    全片版解析度可能高達2萬x1萬px以上，畫圖前依max_dim等比例downsample(只影響
    這張全片總覽圖的顯示精細度，不影響下面逐時間點的原始解析度裁切)。
    """
    if not mask_path or not he_path:
        return {"status": "skipped", "reason": "mcseg_mask_path/mcseg_he_path not provided"}
    mask = _load_mask_or_image(mask_path)
    stride = max(1, max(mask.shape[:2]) // max_dim)
    mask_ds = np.array(mask[::stride, ::stride])
    image = _load_mask_or_image(he_path)
    image_ds = np.array(image[::stride, ::stride])
    cell_id_to_label, palette = _build_cell_id_to_label(adata, cluster_labels)
    result = celltype_overlay_plot(
        mask_ds, image_ds, cell_id_to_label, out_path, palette=palette,
        title=f"Full-slide annotation overlay (manual marker-based cluster labels, downsampled {stride}x for display)",
        scale_um_per_px=(um_per_px * stride) if um_per_px else None,
    )
    return {"status": "generated", "display_downsample_stride": stride, **result}


# ── Multi-timepoint per-day comparison (item 8-9) ───────────────────────
def _day_pixel_bounds(xy: np.ndarray, day_mask: np.ndarray, margin_frac: float,
                       max_row: int, max_col: int) -> tuple[int, int, int, int]:
    x, y = xy[day_mask, 0], xy[day_mask, 1]
    x0, x1 = x.min(), x.max(); y0, y1 = y.min(), y.max()
    mx, my = (x1 - x0) * margin_frac, (y1 - y0) * margin_frac
    return (max(0, int(y0 - my)), min(max_row, int(y1 + my)),
            max(0, int(x0 - mx)), min(max_col, int(x1 + mx)))


def _zoom_bounds_from_full(full_bounds: tuple[int, int, int, int], zoom_factor: float = 2.0) -> tuple[int, int, int, int]:
    """Zoom window centered on the full view, sized full/zoom_factor (zoom_factor=2 -> 2x magnification).

    Derived from the FULL VIEW's own bounds (not the day's raw cell extent independently) so the
    zoom is always a fixed, predictable ratio of what's already on screen. Guards against: the
    earlier zoom_frac-of-raw-extent approach producing a huge crop for a sparse/anomalous day
    (dpcp01 Day0's 380 scattered cells span a large area, so a fraction of THAT extent was still huge)."""
    r0, r1, c0, c1 = full_bounds
    cr, cc = (r0 + r1) / 2, (c0 + c1) / 2
    hr, hc = (r1 - r0) / (2 * zoom_factor), (c1 - c0) / (2 * zoom_factor)
    return (int(cr - hr), int(cr + hr), int(cc - hc), int(cc + hc))


def _compute_mask_px_scale(mask: np.ndarray, adata, n_sample: int = 300) -> float:
    """經驗計算「mask陣列像素空間」相對於 obs['centroid_x_px']/['centroid_y_px']
    (raw TIFF像素空間)的縮放係數，僅用於裁切時間點視窗的粗略邊界估計——真正的
    細胞類型著色是直接用cell_id索引mask，不經過這個轉換，不受此估計誤差影響。
    這個mask陣列(*_hires_for_overlay.npy)是raw TIFF mask的降解析度版本，縮放
    係數未必等於 _compute_tiff_scale() 算出的 virtual_fullres↔raw_tiff 係數
    (那是另一組座標系統轉換，見 coordinate_system_table)，故用抽樣centroid比對
    直接算出，不假設。"""
    cell_ids = adata.obs["cell_id"].astype(int).values
    rng = np.random.default_rng(0)
    sample = rng.choice(cell_ids, size=min(n_sample, len(cell_ids)), replace=False)
    centroids = ndi.center_of_mass(mask, labels=mask, index=sample)
    obs_px = adata.obs.set_index(adata.obs["cell_id"].astype(int))[["centroid_x_px", "centroid_y_px"]]
    ratios = []
    for cid, (row, col) in zip(sample, centroids):
        if col > 1 and row > 1 and cid in obs_px.index:
            px_x, px_y = obs_px.loc[cid, "centroid_x_px"], obs_px.loc[cid, "centroid_y_px"]
            ratios.append(px_x / col)
            ratios.append(px_y / row)
    return float(np.median(ratios))


def fig_multitimepoint_annotation_overlay(adata, day_col: str, mask_path: str, he_path: str,
                                           cluster_labels: dict[str, str], out_full: Path,
                                           out_zoom: Path, zoom_factor: float = 2.0,
                                           um_per_px: float | None = None) -> dict:
    """逐日「真正的」H&E+mask疊圖版本(取代cell-level scatter proxy)：每天各裁切
    其細胞所在的像素窗格，套用 celltype_overlay_plot() 依cluster標籤著色。

    裁切視窗座標用 _resolve_mask_px_scale() 決定的縮放係數(優先驗證scale=1是否
    直接對得上，見該函數docstring)換算，不假設 obs['centroid_x_px']/['centroid_y_px']
    跟mask是同一還是不同座標系統。
    """
    mask_full = _load_mask_or_image(mask_path)
    image_full = _load_mask_or_image(he_path)
    max_row, max_col = mask_full.shape
    cell_id_to_label, palette = _build_cell_id_to_label(adata, cluster_labels)

    scale = _resolve_mask_px_scale(mask_full, adata)
    xy = np.column_stack([
        adata.obs["centroid_x_px"].values / scale,
        adata.obs["centroid_y_px"].values / scale,
    ])

    days = sorted(adata.obs[day_col].astype(str).unique())
    day_masks = {d: (adata.obs[day_col].astype(str) == d).values for d in days}
    counts_per_day = {d: int(m.sum()) for d, m in day_masks.items()}

    for d, m in day_masks.items():
        full_bounds = _day_pixel_bounds(xy, m, 0.05, max_row, max_col)
        r0, r1, c0, c1 = full_bounds
        celltype_overlay_plot(
            mask_full[r0:r1, c0:c1], image_full[r0:r1, c0:c1], cell_id_to_label,
            out_full.parent / f"{out_full.stem}_{d}.png", palette=palette,
            title=f"{d} full (n={counts_per_day[d]:,})", scale_um_per_px=um_per_px,
        )
        zr0, zr1, zc0, zc1 = _zoom_bounds_from_full(full_bounds, zoom_factor)
        celltype_overlay_plot(
            mask_full[zr0:zr1, zc0:zc1], image_full[zr0:zr1, zc0:zc1], cell_id_to_label,
            out_zoom.parent / f"{out_zoom.stem}_{d}.png", palette=palette,
            title=f"{d} zoom ({zoom_factor:.0f}x)", scale_um_per_px=um_per_px,
        )

    return {"days": days, "n_cells_per_day": counts_per_day,
            "anomaly_flag": [d for d, n in counts_per_day.items() if n < 0.1 * max(counts_per_day.values())],
            "per_day_full": [str(out_full.parent / f"{out_full.stem}_{d}.png") for d in days],
            "per_day_zoom": [str(out_zoom.parent / f"{out_zoom.stem}_{d}.png") for d in days],
            "mask_px_scale_factor": scale}


def fig_multitimepoint_comparison(adata, day_col: str, out_full: Path, out_zoom: Path,
                                   zoom_factor: float = 2.0) -> dict:
    """Per-day cell-level scatter (colored by leiden), full view + local zoom, side by side.
    zoom_factor=2 -> zoom window is half the linear extent of the full view (2x magnification),
    centered on the day's median -- consistent with fig_multitimepoint_annotation_overlay().

    Guards against: dpcp01's Day0 mcseg gap (380 cells, almost all one keratinocyte
    cluster) sitting undetected for a long time because there was no standard
    per-timepoint visual comparison -- a panel like this would have made the
    anomaly (one day looking wildly different/sparser than its neighbors)
    visually obvious immediately.

    This is a cell-level scatter proxy for a true H&E+mask overlay. If
    --mcseg-mask-path/--mcseg-he-path are supplied, use
    fig_multitimepoint_annotation_overlay() instead -- this function stays as
    the no-mask-available fallback (e.g. report generated before raw masks
    were exported, or mask files no longer on disk).
    """
    days = sorted(adata.obs[day_col].astype(str).unique())
    n = len(days)
    xy = adata.obsm["spatial"]
    leiden = adata.obs["leiden"].astype(str)
    cmap = plt.get_cmap("tab20")
    clusters = sorted(leiden.unique(), key=lambda c: int(c) if c.isdigit() else 0)
    color_map = {c: cmap(i % 20) for i, c in enumerate(clusters)}

    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4))
    if n == 1:
        axes = [axes]
    counts_per_day = {}
    for ax, d in zip(axes, days):
        m = (adata.obs[day_col].astype(str) == d).values
        counts_per_day[d] = int(m.sum())
        colors = [color_map[c] for c in leiden[m]]
        ax.scatter(xy[m, 0], xy[m, 1], s=2, c=colors)
        ax.set_title(f"{d} (n={m.sum():,})")
        ax.invert_yaxis()
        ax.axis("off")
    fig.suptitle("Per-timepoint full view (colored by leiden cluster) — check for anomalous days")
    fig.tight_layout()
    fig.savefig(out_full, dpi=140)
    plt.close(fig)

    fig2, axes2 = plt.subplots(1, n, figsize=(4 * n, 4))
    if n == 1:
        axes2 = [axes2]
    for ax, d in zip(axes2, days):
        m = (adata.obs[day_col].astype(str) == d).values
        if m.sum() == 0:
            ax.axis("off")
            continue
        x_d, y_d = xy[m, 0], xy[m, 1]
        cx, cy = np.median(x_d), np.median(y_d)
        halfw = (x_d.max() - x_d.min()) / (2 * zoom_factor)
        halfh = (y_d.max() - y_d.min()) / (2 * zoom_factor)
        zoom_m = m & (np.abs(xy[:, 0] - cx) < halfw) & (np.abs(xy[:, 1] - cy) < halfh)
        colors = [color_map[c] for c in leiden[zoom_m]]
        ax.scatter(xy[zoom_m, 0], xy[zoom_m, 1], s=8, c=colors)
        ax.set_title(f"{d} zoom (n={zoom_m.sum():,})")
        ax.invert_yaxis()
        ax.axis("off")
    fig2.suptitle(f"Per-timepoint local zoom ({zoom_factor:.0f}x magnification of full view, centered)")
    fig2.tight_layout()
    fig2.savefig(out_zoom, dpi=140)
    plt.close(fig2)

    return {"days": days, "n_cells_per_day": counts_per_day,
            "anomaly_flag": [d for d, n in counts_per_day.items() if n < 0.1 * max(counts_per_day.values())]}


# ── HTML assembly ────────────────────────────────────────────────────────
_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>{sample_id} — Visium HD 標準基礎分析報告</title>
<style>
  :root {{ --bg:#fff; --fg:#1a1a1a; --muted:#666; --accent:#8b3a3a; --card:#f7f5f2; --border:#ddd; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#1a1a1a; --fg:#eee; --muted:#aaa; --accent:#d98a8a; --card:#262220; --border:#444; }}
  }}
  body {{ background:var(--bg); color:var(--fg); font-family:-apple-system,"PingFang TC",sans-serif;
          max-width:980px; margin:0 auto; padding:2rem 1.5rem 6rem; line-height:1.6; }}
  h1 {{ border-bottom:3px solid var(--accent); padding-bottom:.5rem; }}
  h2 {{ margin:0; border-left:5px solid var(--accent); padding-left:.6rem; display:inline-block; }}
  .cards {{ display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }}
  .card {{ background:var(--card); border-radius:8px; padding:1rem 1.4rem; min-width:140px; }}
  .card .num {{ font-size:1.6rem; font-weight:700; color:var(--accent); }}
  .card .lbl {{ font-size:.8rem; color:var(--muted); }}
  .warn {{ background:var(--card); border-left:4px solid #c0392b; padding:.8rem 1rem; border-radius:4px; margin:1rem 0; }}
  table {{ border-collapse:collapse; width:100%; margin:1rem 0; font-size:.9rem; }}
  th,td {{ border:1px solid var(--border); padding:.4rem .6rem; text-align:left; }}
  th {{ background:var(--card); }}
  .figure {{ margin:1.2rem 0; text-align:center; }}
  .figure img {{ max-width:100%; border:1px solid var(--border); border-radius:4px; background:#fff; }}
  .figure figcaption {{ font-size:.85rem; color:var(--muted); margin-top:.3rem; }}
  details {{ margin-top:1.6rem; border:1px solid var(--border); border-radius:8px; padding:0 1rem; background:var(--bg); }}
  details[open] {{ padding-bottom:1rem; }}
  summary {{ cursor:pointer; padding:.9rem 0; list-style:none; }}
  summary::-webkit-details-marker {{ display:none; }}
  summary::before {{ content:"▶ "; color:var(--accent); font-size:.8em; }}
  details[open] > summary::before {{ content:"▼ "; }}
  details > summary h2 {{ border-left:none; padding-left:0; }}
</style></head><body>
<h1>{sample_id} — Visium HD 標準基礎分析報告</h1>
<p style="color:var(--muted)">自動產出於標準基礎分析套件（見 lcdda「Visium HD標準基礎分析與報告產出設計」）。各節可點擊標題收合/展開。</p>

<h2>QC 摘要</h2>
<div class="cards">
  <div class="card"><div class="num">{n_raw:,}</div><div class="lbl">原始分割細胞數</div></div>
  <div class="card"><div class="num">{n_post_qc:,}</div><div class="lbl">QC通過細胞數</div></div>
  <div class="card"><div class="num">{retention_pct}%</div><div class="lbl">保留率</div></div>
  <div class="card"><div class="num">{n_clusters}</div><div class="lbl">Leiden clusters</div></div>
</div>

{alignment_section}

<details>
<summary><h2>QC 分布與空間圖</h2></summary>
<div class="figure"><img src="figures/01_qc_overview.png" loading="lazy"><figcaption>total_counts / n_genes 分布，紅線=QC門檻</figcaption></div>
<div class="figure"><img src="figures/02_qc_spatial.png" loading="lazy"><figcaption>QC通過細胞的空間分布</figcaption></div>
</details>

<details>
<summary><h2>Clustering</h2></summary>
<div class="figure"><img src="figures/04_umap.png" loading="lazy"><figcaption>UMAP，依leiden cluster著色</figcaption></div>
<div class="figure"><img src="figures/04_dotplot.png" loading="lazy"><figcaption>Marker基因dot plot（人工判讀用，非argmax自動標籤）</figcaption></div>
<div class="figure"><img src="figures/05_composition.png" loading="lazy"><figcaption>細胞型別組成（人工marker判讀標籤）</figcaption></div>
</details>

<details>
<summary><h2>關鍵基因空間表現</h2></summary>
<div class="figure"><img src="figures/06_gene_spatial.png" loading="lazy"><figcaption>代表性基因的單細胞空間分布</figcaption></div>
</details>

{annotation_overlay_section}

{multitimepoint_section}

<details>
<summary><h2>座標系統對照表</h2></summary>
<table><tr><th>座標系統</th><th>使用位置</th><th>備註</th></tr>
{coord_table_rows}
</table>
</details>

</body></html>
"""


def build_html_report(manifest: dict, out_path: Path) -> None:
    alignment = manifest.get("alignment_check", {})
    if alignment.get("status") == "generated":
        align_body = (
            '<div class="figure"><img src="figures/00_alignment_check.png" loading="lazy">'
            '<figcaption>TIFF vs tissue_hires_image 並排比對，確認組織輪廓對得上</figcaption></div>'
        )
        scalef = manifest.get("scalef_check", {})
        if scalef.get("suspicious_default_value"):
            align_body += ('<div class="warn">⚠️ tissue_hires_scalef剛好等於1.0——'
                            '這通常是程式預設值，不是真正算出來的對齊比例，需人工確認</div>')
        align_section = f'<details><summary><h2>0. 對齊檢查（跑分割前必看）</h2></summary>{align_body}</details>'
    else:
        align_section = ('<div class="warn">⚠️ 未提供TIFF/tissue_hires_image路徑，'
                          '對齊檢查已略過——正式分析前應補做</div>')

    overlay = manifest.get("annotation_overlay", {})
    if overlay.get("status") == "generated":
        annotation_overlay_section = (
            '<details><summary><h2>3. Annotation 疊圖（真正的H&E+mask+細胞類型著色）</h2></summary>'
            '<div class="figure"><img src="figures/07_annotation_overlay.png" loading="lazy">'
            '<figcaption>依人工marker判讀的cluster標籤填色（非argmax），灰色=未分類/QC濾除細胞</figcaption></div>'
            '</details>'
        )
    else:
        annotation_overlay_section = (
            '<div class="warn">⚠️ 未提供 --mcseg-mask-path/--mcseg-he-path，annotation疊圖已略過'
            '（只有分割邊界不足以回答「這裡是什麼細胞」，正式報告應補做）</div>'
        )

    mt = manifest.get("multitimepoint")
    if mt:
        anomaly = mt.get("anomaly_flag", [])
        anomaly_html = (f'<div class="warn">⚠️ 偵測到可能異常的時間點（細胞數遠低於其他天）：'
                         f'{", ".join(anomaly)}——建議人工確認該時間點的mcseg分割是否有缺口</div>') if anomaly else ""
        if mt.get("mode") == "annotation_overlay":
            # 每天各自收合，一次只看一天，不會一次載入全部大圖
            day_figs = "".join(
                f'<details><summary>{d}（n={mt["n_cells_per_day"].get(d, 0):,}）'
                f'{" ⚠️" if d in anomaly else ""}</summary>'
                f'<div class="figure"><img src="figures/08_multitimepoint_full_{d}.png" loading="lazy">'
                f'<figcaption>{d} 全圖（annotation疊圖）</figcaption></div>'
                f'<div class="figure"><img src="figures/09_multitimepoint_zoom_{d}.png" loading="lazy">'
                f'<figcaption>{d} 局部放大 2x（annotation疊圖）</figcaption></div>'
                f'</details>'
                for d in mt.get("days", [])
            )
            mt_section = (
                '<details><summary><h2>多時間點逐日比較（真實H&E+mask疊圖）</h2></summary>'
                + anomaly_html + day_figs + '</details>'
            )
        else:
            mt_body = (
                anomaly_html +
                '<div class="figure"><img src="figures/08_multitimepoint_full.png" loading="lazy">'
                '<figcaption>各時間點全圖（依cluster著色，cell-level scatter proxy——'
                '未提供mask/H&E路徑時的替代方案）</figcaption></div>'
                '<div class="figure"><img src="figures/09_multitimepoint_zoom.png" loading="lazy">'
                '<figcaption>各時間點局部放大 2x</figcaption></div>'
            )
            mt_section = f'<details><summary><h2>多時間點逐日比較</h2></summary>{mt_body}</details>'
    else:
        mt_section = ""

    coord_rows = "".join(
        f"<tr><td>{r['coordinate_system']}</td><td>{r['used_by']}</td><td>{r['notes']}</td></tr>"
        for r in manifest.get("coordinate_systems", [])
    )

    html = _HTML_TEMPLATE.format(
        sample_id=manifest["sample_id"],
        n_raw=manifest.get("qc_overview", {}).get("n_raw", 0),
        n_post_qc=manifest.get("qc_overview", {}).get("n_post_qc", 0),
        retention_pct=manifest.get("qc_overview", {}).get("retention_pct", "?"),
        n_clusters=manifest.get("clustering", {}).get("n_clusters", "?"),
        alignment_section=align_section,
        annotation_overlay_section=annotation_overlay_section,
        multitimepoint_section=mt_section,
        coord_table_rows=coord_rows,
    )
    out_path.write_text(html, encoding="utf-8")


# ── Coordinate system reference table ───────────────────────────────────
def coordinate_system_table(sample_id: str, tiff_scale: float | None = None) -> pd.DataFrame:
    """Guards against repeatedly confusing raw-TIFF-px / virtual_fullres-px / L2-um coords.

    ⚠️ 2026-07-23更新：原本這裡寫「obs['centroid_x_px']是raw TIFF px」，結果在dpcp01
    的annotation疊圖實作中發現對這個樣本(用的是'_CORRECTED'重跑過的h5ad)其實是
    virtual_fullres px，跟segmentation_masks_fullslide_vfr_CORRECTED.npy(scale=1)
    直接對得上，而非raw TIFF全解析度那份mask——**同一個欄位名稱在不同處理版本下
    語意不同**，不能只看欄位名字或mask檔名/shape就假設，一定要像
    _resolve_mask_px_scale()那樣拿實際cell_id去mask裡驗證。"""
    rows = [
        {"coordinate_system": "raw TIFF pixel", "used_by": "mcseg fullslide segmentation, read_btf_crop()",
         "notes": "segmentation_masks_fullslide.npy(未加_CORRECTED)在這個空間；⚠️不要只憑欄位名稱假設obs['centroid_x_px']在這裡——需驗證"},
        {"coordinate_system": "virtual_fullres pixel", "used_by": "bio_run_mcseg_roi roi_x/roi_y params, tissue_positions.parquet pxl_col/row_in_fullres",
         "notes": f"conversion: raw_tiff_px = virtual_fullres_px * tiff_scale (tiff_scale={tiff_scale if tiff_scale else 'run _compute_tiff_scale() for this sample'})；"
                  f"dpcp01案例裡'_CORRECTED'版本的obs['centroid_x_px']實際落在這個空間(用cell_id比對驗證過)"},
        {"coordinate_system": "L2 8um bin (micron)", "used_by": "obs_metadata.parquet spatial_x/y, day_section derivation",
         "notes": "independent scale from the above two, do not mix cutpoints across coordinate systems"},
    ]
    return pd.DataFrame(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sample-id", required=True)
    p.add_argument("--mcseg-h5ad", required=True, help="path to clustered h5ad with .obs['leiden'] and .obsm['spatial']")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--tiff-path", default=None)
    p.add_argument("--hires-path", default=None)
    p.add_argument("--scalefactors-json", default=None)
    p.add_argument("--min-counts", type=int, default=50)
    p.add_argument("--min-genes", type=int, default=20)
    p.add_argument("--n-raw-cells", type=int, default=None, help="raw segmented cell count before QC, for retention rate card")
    p.add_argument("--marker-genes-json", default=None,
                    help='JSON dict {"celltype": ["gene1","gene2"]} for dotplot; e.g. '
                         '\'{"Mac4":["Cd74","H2-Aa"],"Mac5":["Cd14","Spp1"]}\'')
    p.add_argument("--cluster-labels-json", default=None,
                    help='JSON dict {"leiden_id": "manual_label"} for composition plot, e.g. '
                         '\'{"5":"Mac4","15":"Mac5","10":"Bulge"}\' — fill in AFTER reviewing '
                         'the dotplot/wilcoxon markers, never trust argmax alone')
    p.add_argument("--spatial-genes", default=None, help="comma-separated gene list for spatial maps")
    p.add_argument("--day-cutpoints", default=None, help="comma-separated x-coordinate cutpoints, e.g. 900,1600,2300")
    p.add_argument("--day-labels", default=None, help="comma-separated labels in ascending-x order, e.g. Day3,Day2,Day1,Day0 "
                                                        "— direction must be confirmed with the user, never assumed")
    p.add_argument("--mcseg-mask-path", default=None,
                    help="path to the fullslide segmentation label mask .npy (0=background), in the SAME "
                         "pixel space as --mcseg-he-path and .obsm['spatial'] (typically the "
                         "*_hires_for_overlay.npy variant from mcseg fullslide output, NOT the raw-TIFF-scale mask)")
    p.add_argument("--mcseg-he-path", default=None,
                    help="H&E image aligned pixel-for-pixel to --mcseg-mask-path (same shape)")
    p.add_argument("--um-per-px", type=float, default=None, help="micron-per-pixel scale for the overlay scale bar (optional)")
    args = p.parse_args()

    import anndata as ad
    a = ad.read_h5ad(args.mcseg_h5ad)

    out_dir = Path(args.out_dir or f"/data/bio_db/results/reports/{args.sample_id}_baseline_report")
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"sample_id": args.sample_id}

    manifest["alignment_check"] = fig_alignment_check(args.tiff_path, args.hires_path, fig_dir / "00_alignment_check.png")
    manifest["scalef_check"] = check_scalef_suspicious(args.scalefactors_json)
    manifest["qc_overview"] = fig_qc_overview(a, fig_dir / "01_qc_overview.png", args.min_counts, args.min_genes,
                                               args.n_raw_cells or a.n_obs)
    manifest["qc_spatial"] = fig_qc_spatial(a, fig_dir / "02_qc_spatial.png")

    marker_genes = json.loads(args.marker_genes_json) if args.marker_genes_json else {}
    manifest["clustering"] = fig_clustering(a, fig_dir / "04_umap.png", fig_dir / "04_dotplot.png", marker_genes)

    cluster_labels = json.loads(args.cluster_labels_json) if args.cluster_labels_json else {}
    manifest["composition"] = fig_celltype_composition(a, cluster_labels, fig_dir / "05_composition.png")

    manifest["annotation_overlay"] = fig_annotation_overlay(
        args.mcseg_mask_path, args.mcseg_he_path, a, cluster_labels,
        fig_dir / "07_annotation_overlay.png", um_per_px=args.um_per_px,
    )

    spatial_genes = [g.strip() for g in args.spatial_genes.split(",")] if args.spatial_genes else []
    manifest["gene_spatial"] = fig_gene_spatial_maps(a, spatial_genes, fig_dir / "06_gene_spatial.png")

    if args.day_cutpoints and args.day_labels:
        cutpoints = [float(c) for c in args.day_cutpoints.split(",")]
        day_labels = [d.strip() for d in args.day_labels.split(",")]
        x = a.obsm["spatial"][:, 0]
        bins = [0] + cutpoints + [x.max() + 1]
        a.obs["day_section"] = pd.cut(x, bins=bins, labels=day_labels).astype(str)
        if args.mcseg_mask_path and args.mcseg_he_path:
            manifest["multitimepoint"] = fig_multitimepoint_annotation_overlay(
                a, "day_section", args.mcseg_mask_path, args.mcseg_he_path, cluster_labels,
                fig_dir / "08_multitimepoint_full.png", fig_dir / "09_multitimepoint_zoom.png",
                um_per_px=args.um_per_px,
            )
            manifest["multitimepoint"]["mode"] = "annotation_overlay"
        else:
            manifest["multitimepoint"] = fig_multitimepoint_comparison(
                a, "day_section", fig_dir / "08_multitimepoint_full.png", fig_dir / "09_multitimepoint_zoom.png"
            )
            manifest["multitimepoint"]["mode"] = "scatter_proxy"

    manifest["coordinate_systems"] = coordinate_system_table(args.sample_id).to_dict("records")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    build_html_report(manifest, out_dir / "report.html")
    print(json.dumps(manifest, indent=2, default=str))
    print(f"\nDone. Figures + manifest + report.html written to {out_dir}")


if __name__ == "__main__":
    main()
