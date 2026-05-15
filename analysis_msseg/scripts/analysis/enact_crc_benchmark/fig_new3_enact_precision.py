"""
fig_new3_enact_precision.py
===========================
新 Figure 3（新 §3.4）— MCseg 在 ENACT CRC 資料集的精準識別策略

Panel A（左半）：
  A1  全局 overview（8× downsample）：MCseg mask 輪廓 + GT centroid
       綠點 = matched（65.1%），紅點 = unmatched（34.9%）
  A2  局部放大（腺體-間質界面，800×800 px）：同色系

Panel B（右半）：
  WBA confusion matrix（covered subset）
  行 = GT，欄 = Predicted；數值為 row-normalised proportion

輸出：
  submission_bioinformatics/figures/fig_new3_enact_precision.png（300 DPI）

執行：
  cd /Volumes/SSD/plan_a
  uv run python submission_bioinformatics/scripts/analysis/enact_crc_benchmark/fig_new3_enact_precision.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import tifffile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from skimage.segmentation import find_boundaries
from scipy.ndimage import binary_dilation
from sklearn.metrics import confusion_matrix
import seaborn as sns
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE     = Path("/Volumes/SSD/plan_a/submission_bioinformatics")
RES_DIR  = BASE / "results" / "enact_crc_f1"
WBA_DIR  = BASE / "results" / "enact_crc_f1_wba"
OUT_PATH = BASE / "figures" / "fig_new3_enact_precision.png"

CROP_X0, CROP_Y0 = 5154, 4635   # ENACT local coord → mask pixel offset

# ── Colours ────────────────────────────────────────────────────────────────────
COL_MATCHED   = "#44CC77"
COL_UNMATCHED = "#FF4444"
CMAP_CONF     = "Blues"
CLASSES       = ["epithelial cells", "stromal cells", "immune cells"]
CLASS_LABELS  = ["Epithelial", "Stromal", "Immune"]

# ── Load data ──────────────────────────────────────────────────────────────────
print("Loading H&E crop…")
he   = tifffile.imread(str(RES_DIR / "he_crop.tif"))          # (H, W, 3) uint8
H, W = he.shape[:2]

print("Loading MCseg mask…")
mask = np.load(str(RES_DIR / "mcseg_mask.npy")).astype(np.int32)

print("Loading GT matched (lookup)…")
gt = pd.read_csv(RES_DIR / "gt_matched.csv")
gt = gt.dropna(subset=["cell_x", "cell_y"]).copy()
gt["col_local"] = (gt["cell_x"] - CROP_X0).astype(int)
gt["row_local"] = (gt["cell_y"] - CROP_Y0).astype(int)
valid = (
    (gt["col_local"] >= 0) & (gt["col_local"] < W) &
    (gt["row_local"] >= 0) & (gt["row_local"] < H)
)
gt       = gt[valid].copy()
matched  = gt[gt["mcseg_cell_id"] > 0]
unmatched= gt[gt["mcseg_cell_id"] == 0]
print(f"  GT in bounds: {len(gt):,}  matched: {len(matched):,}  unmatched: {len(unmatched):,}")

print("Loading WBA gt_matched for confusion matrix…")
wba         = pd.read_csv(WBA_DIR / "gt_matched_wba.csv")
wba_covered = wba[wba["mcseg_cell_id"] > 0].dropna(subset=["pred_label"])

# ── Overview: 8× downsample + boundary overlay ────────────────────────────────
print("Building overview (8× downsample)…")
STEP  = 8
he_ov = he[::STEP, ::STEP].copy()

print("  computing mask boundaries…")
boundary = find_boundaries(mask, mode="outer")
boundary_ds = binary_dilation(boundary, iterations=1)[::STEP, ::STEP]
he_ov[boundary_ds] = [255, 255, 200]   # pale-yellow boundary (visible on pink H&E)

# ── Zoom region: upper-centre of crop (gland–stroma interface) ─────────────────
ZOOM_R0   = 1800
ZOOM_C0   = 8100
ZOOM_SIZE = 900   # pixels full-res ≈ 450 µm at 0.5 µm/px

he_zoom    = he[ZOOM_R0:ZOOM_R0+ZOOM_SIZE, ZOOM_C0:ZOOM_C0+ZOOM_SIZE].copy()
mask_zoom  = mask[ZOOM_R0:ZOOM_R0+ZOOM_SIZE, ZOOM_C0:ZOOM_C0+ZOOM_SIZE]
bound_zoom = binary_dilation(find_boundaries(mask_zoom, mode="outer"), iterations=1)
he_zoom[bound_zoom] = [255, 255, 180]   # pale-yellow

def in_zoom(df):
    return df[
        (df["row_local"] >= ZOOM_R0) & (df["row_local"] < ZOOM_R0 + ZOOM_SIZE) &
        (df["col_local"] >= ZOOM_C0) & (df["col_local"] < ZOOM_C0 + ZOOM_SIZE)
    ].copy()

zm_m = in_zoom(matched)
zm_u = in_zoom(unmatched)
print(f"  zoom matched: {len(zm_m):,}  unmatched: {len(zm_u):,}")

# ── Confusion matrix (WBA covered subset) ─────────────────────────────────────
y_true  = wba_covered["gt_label"]
y_pred  = wba_covered["pred_label"]
cm_raw  = confusion_matrix(y_true, y_pred, labels=CLASSES)
cm_norm = cm_raw.astype(float) / cm_raw.sum(axis=1, keepdims=True)

# ── Figure layout ──────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(18, 8), dpi=300)
gs  = GridSpec(
    2, 3, figure=fig,
    left=0.03, right=0.97, top=0.92, bottom=0.07,
    wspace=0.38, hspace=0.32,
    width_ratios=[2.4, 1.5, 1.5],
    height_ratios=[3, 1],
)

ax_ov   = fig.add_subplot(gs[0, 0])   # overview (spans row 0)
ax_zoom = fig.add_subplot(gs[0, 1])   # zoom
ax_conf = fig.add_subplot(gs[0, 2])   # confusion matrix
ax_leg  = fig.add_subplot(gs[1, :])   # legend + stats (full width)

# ── A1: Overview ───────────────────────────────────────────────────────────────
ax_ov.imshow(he_ov, origin="upper", interpolation="nearest")

um_c_ds = unmatched["col_local"].values / STEP
um_r_ds = unmatched["row_local"].values / STEP
m_c_ds  = matched["col_local"].values   / STEP
m_r_ds  = matched["row_local"].values   / STEP

ax_ov.scatter(um_c_ds, um_r_ds, s=1.5, c=COL_UNMATCHED, alpha=0.65,
              linewidths=0, rasterized=True, zorder=2)
ax_ov.scatter(m_c_ds,  m_r_ds,  s=1.5, c=COL_MATCHED,   alpha=0.65,
              linewidths=0, rasterized=True, zorder=3)

# zoom rectangle
zr0 = ZOOM_R0 / STEP;  zc0 = ZOOM_C0 / STEP
zr1 = (ZOOM_R0 + ZOOM_SIZE) / STEP;  zc1 = (ZOOM_C0 + ZOOM_SIZE) / STEP
ax_ov.add_patch(mpatches.Rectangle(
    (zc0, zr0), zc1 - zc0, zr1 - zr0,
    linewidth=1.5, edgecolor="yellow", facecolor="none", zorder=4
))

# scale bar: 1000 ds-px = 1000 × 8 × 0.5 µm = 4000 µm — use 250 ds-px = 1000 µm
SB_DS = 250
sb_x0 = he_ov.shape[1] * 0.04
sb_y  = he_ov.shape[0] * 0.965
ax_ov.plot([sb_x0, sb_x0 + SB_DS], [sb_y, sb_y], "w-", lw=2, zorder=5)
ax_ov.text(sb_x0 + SB_DS / 2, sb_y * 0.97, "1 mm",
           color="white", fontsize=7, ha="center", va="top")

ax_ov.set_title("A   ENACT CRC region: MCseg coverage of GT centroids  (n = 20,991)",
                fontsize=9.5, fontweight="bold", loc="left", pad=4)
ax_ov.axis("off")

# ── A2: Zoom ───────────────────────────────────────────────────────────────────
ax_zoom.imshow(he_zoom, origin="upper", interpolation="nearest")
ax_zoom.scatter(
    zm_m["col_local"] - ZOOM_C0, zm_m["row_local"] - ZOOM_R0,
    s=9, c=COL_MATCHED,   alpha=0.75, linewidths=0, rasterized=True, zorder=2
)
ax_zoom.scatter(
    zm_u["col_local"] - ZOOM_C0, zm_u["row_local"] - ZOOM_R0,
    s=12, c=COL_UNMATCHED, alpha=0.90, linewidths=0.4,
    edgecolors="white", rasterized=True, zorder=3
)

# scale bar: 200 px = 100 µm
ax_zoom.plot([30, 230], [ZOOM_SIZE * 0.94, ZOOM_SIZE * 0.94], "w-", lw=1.5)
ax_zoom.text(130, ZOOM_SIZE * 0.91, "100 µm",
             color="white", fontsize=7, ha="center", va="bottom")
ax_zoom.set_title("Zoom (gland–stroma interface)", fontsize=8.5, pad=3)
ax_zoom.axis("off")

# ── B: Confusion matrix ────────────────────────────────────────────────────────
annot = np.array([[f"{v:.2f}" for v in row] for row in cm_norm])
sns.heatmap(
    cm_norm, annot=annot, fmt="", cmap=CMAP_CONF,
    xticklabels=CLASS_LABELS, yticklabels=CLASS_LABELS,
    vmin=0, vmax=1, linewidths=0.5, linecolor="white",
    cbar_kws={"shrink": 0.75, "label": "Proportion"},
    ax=ax_conf, annot_kws={"size": 10, "weight": "bold"},
)
ax_conf.set_xlabel("Predicted", fontsize=9)
ax_conf.set_ylabel("True (GT)", fontsize=9)
ax_conf.set_title(
    f"B   Cell-type annotation accuracy\n    covered subset (n = {len(wba_covered):,}, +WBA)",
    fontsize=9.5, fontweight="bold", loc="left", pad=4
)
ax_conf.tick_params(axis="both", labelsize=8)

# ── Legend + stats row ─────────────────────────────────────────────────────────
ax_leg.axis("off")

match_rate = len(matched) / len(gt) * 100

legend_elements = [
    mpatches.Patch(facecolor=COL_MATCHED,
                   label=f"GT matched — covered by MCseg  ({match_rate:.1f}%,  n = {len(matched):,})"),
    mpatches.Patch(facecolor=COL_UNMATCHED,
                   label=f"GT unmatched — centroid in gap  ({100-match_rate:.1f}%, n = {len(unmatched):,})"),
    mpatches.Patch(facecolor="#DDDDDD", edgecolor="gray",
                   label="MCseg cell boundary"),
]

type_stats = []
for lbl, short in zip(CLASSES, CLASS_LABELS):
    n  = (gt["gt_label"] == lbl).sum()
    nu = ((gt["gt_label"] == lbl) & (gt["mcseg_cell_id"] == 0)).sum()
    type_stats.append(f"{short} {nu/n*100:.0f}%")

stats_txt = (
    f"Unmatched rate by cell type: {' · '.join(type_stats)}\n"
    f"Covered-subset precision (+WBA): Epithelial 0.90 · Stromal 0.77 · Immune 0.43  "
    f"│  Weighted F1 = 0.738  ·  Micro F1 = 0.802"
)
ax_leg.text(0.01, 0.82, stats_txt, transform=ax_leg.transAxes,
            fontsize=8.5, va="top", family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#F7F7F7",
                      edgecolor="#CCCCCC", alpha=0.9))
ax_leg.legend(handles=legend_elements, loc="lower left",
              fontsize=8.5, frameon=True, ncol=3,
              bbox_to_anchor=(0.0, 0.0))

# ── Save ───────────────────────────────────────────────────────────────────────
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(str(OUT_PATH), dpi=300, bbox_inches="tight")
print(f"Saved → {OUT_PATH}")
