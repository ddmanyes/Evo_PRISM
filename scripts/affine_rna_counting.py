#!/usr/bin/env python
"""
仿射版 RNA counting：把 40X 解析度的細胞遮罩投影回 VFR 座標再做 bin 歸屬。

為什麼不能用 analysis/mcseg_wrapper.py 的 downscale_mask_to_vfr()：
那支假設遮罩與 VFR 之間是純縮放（sk_resize 整張等比壓縮）。這份 NDPI 與
CytAssist 之間實際是【水平翻轉 + 縮放 20.2618/20.2387 + 旋轉 −0.247°】
（見 ndpi_vfr_registration.json，12/12 held-out 點驗證，殘差 1.76 µm）。
純縮放會讓整批 bin 歸屬錯位，而且錯得很安靜 —— bins/cell 與 UMI/cell
照樣落在合理範圍，只是細胞配到錯的轉錄體。

做法刻意比照既有流程：同樣是 nearest-neighbour 把遮罩降到 VFR 解析度
（bins 與遮罩必須同座標系），只是把「等比壓縮」換成「仿射反映射取樣」，
這樣結果與 ROI 那輪可直接比較。
"""
import argparse, json, logging, sys
from pathlib import Path
import numpy as np, cv2, pandas as pd

sys.path.insert(0, "/Volumes/KINGSTON/Evo_PRISM/MSseg")

EP = Path("/Volumes/KINGSTON/Evo_PRISM")
SAMPLE = "MQ01_VH_20250428_A1D1"
SR = Path("/Volumes/KINGSTON/Bioinfo_Projects/01_Spatial_Transcriptomics/"
          "20251125_TGIA_VisiumHD/20250612_MQ250428/MQ250428-A1-D1/outs")
VFR_UM_PX = 4.67057234364261          # scalefactors_json.json: microns_per_pixel

log = logging.getLogger("affine_count")


def mask_to_vfr(mask, origin_xy, A, W0, pad=4):
    """
    40X 遮罩（翻轉後 NDPI 座標、原點 origin_xy）-> VFR 解析度遮罩。

    走反映射：對每個 VFR 像素算出它在遮罩裡的位置再取樣（nearest），
    而不是把遮罩 resize 過去 —— 仿射含旋轉，等比壓縮做不到。
    """
    X0, Y0 = origin_xy
    H, W = mask.shape
    Ai = cv2.invertAffineTransform(A)

    # 遮罩四角 -> VFR，決定要涵蓋的 VFR 範圍
    corners = np.array([[X0, Y0, 1], [X0 + W, Y0, 1],
                        [X0, Y0 + H, 1], [X0 + W, Y0 + H, 1]], float)
    V = (Ai @ corners.T).T
    c0 = int(np.floor(V[:, 0].min())) - pad
    r0 = int(np.floor(V[:, 1].min())) - pad
    c1 = int(np.ceil(V[:, 0].max())) + pad
    r1 = int(np.ceil(V[:, 1].max())) + pad
    vw, vh = c1 - c0, r1 - r0
    log.info(f"  VFR 範圍 col {c0}-{c1} row {r0}-{r1}  ({vw}×{vh} px)")

    cols, rows = np.meshgrid(np.arange(c0, c1, dtype=np.float64),
                             np.arange(r0, r1, dtype=np.float64))
    ones = np.ones(cols.size)
    P = A @ np.stack([cols.ravel(), rows.ravel(), ones])     # -> 翻轉後 NDPI
    mx = np.rint(P[0] - X0).astype(np.int64)
    my = np.rint(P[1] - Y0).astype(np.int64)

    out = np.zeros(cols.size, dtype=np.uint32)
    ok = (mx >= 0) & (mx < W) & (my >= 0) & (my < H)
    out[ok] = mask[my[ok], mx[ok]]
    out = out.reshape(vh, vw)
    log.info(f"  投影後 VFR 遮罩：{out.shape}，涵蓋 {len(np.unique(out)) - 1:,} 個細胞 ID "
             f"(原遮罩 {int(mask.max()):,})")
    return out, c0, r0


def composition_sanity_check(bins_adata, assigned_mask, top_n=50, tol=1.15):
    """
    三方組成比較：細胞內 bins vs 細胞外 bins vs 全域。

    為什麼需要這道檢查：2026-09-16 曾發生 square_002um 的 barcodes.tsv.gz 被外部
    工具改寫成排序清單，導致矩陣每一欄錯貼 bin。這種損壞【不會拋任何錯誤】——
    bins/cell、UMI/cell、細胞數全都落在合理範圍，圖也畫得出來，連續數小時的分析
    都建立在假訊號上。

    唯一能早期抓到它的訊號是：表現量被打散後，任意子集合的組成都會收斂到全域平均。
    細胞坐落在有核、轉錄本豐富的位置，其組成【本來就該】與細胞外的 bins 不同；
    若三者幾乎相等，資料的 barcode↔表現量對應已經壞掉。

    tol=1.15 表示：若前 top_n 個基因的「細胞內/細胞外」比值全部落在 0.87~1.15 之間，
    視為異常並拋出。健康資料中至少會有幾個基因明顯偏向某一側。
    """
    import numpy as np
    X = bins_adata.X
    tot = np.asarray(X.sum(0)).ravel()
    top = np.argsort(tot)[::-1][:top_n]
    inside = np.asarray(X[assigned_mask][:, top].sum(0)).ravel()
    outside = np.asarray(X[~assigned_mask][:, top].sum(0)).ravel()
    fi = inside / max(inside.sum(), 1)
    fo = outside / max(outside.sum(), 1)
    ratio = np.divide(fi, fo, out=np.ones_like(fi), where=fo > 0)
    spread = float(np.max(ratio) / max(np.min(ratio[ratio > 0]), 1e-9))
    log.info(f"組成健檢：細胞內 {assigned_mask.sum():,} bins / 細胞外 {(~assigned_mask).sum():,} bins")
    log.info(f"  前 {top_n} 大基因的 內/外 比值範圍 {ratio.min():.3f}–{ratio.max():.3f}（離散度 {spread:.2f}x）")
    if ratio.max() < tol and ratio.min() > 1 / tol:
        raise RuntimeError(
            f"組成健檢失敗：細胞內與細胞外 bins 的組成幾乎相同"
            f"（前 {top_n} 大基因比值全在 {1/tol:.2f}–{tol:.2f} 之間）。"
            f"這是 barcode↔表現量對應損壞的典型徵兆，表現量可能被打散到錯誤的 bins。"
            f"請先比對 filtered_feature_bc_matrix/barcodes.tsv.gz 的日期與排序狀態"
            f"（Space Ranger 的 filtered barcodes 不應是排序的），"
            f"再檢查把 2µm 聚合回 8µm 與官方矩陣的相關性是否接近 1。"
        )
    log.info("  ✓ 通過（細胞內外組成有差異，符合預期）")
    return {"ratio_min": float(ratio.min()), "ratio_max": float(ratio.max()), "spread": spread}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mask", required=True, help="40X 解析度遮罩 .npy")
    ap.add_argument("--origin", required=True, help="遮罩原點（翻轉後 NDPI 座標）'x,y'")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dilation-px", type=int, default=0,
                    help="VFR 像素的擴張量。ROI 那輪實測為 0（4.67 µm/px 下 6px=28µm 過大）")
    ap.add_argument("--tag", default="capture40x")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    R = json.loads((EP / "results/mcseg" / SAMPLE / "ndpi_vfr_registration.json").read_text())
    A = np.array(R["affine_vfr_to_ndpi_flipped"]); W0, _ = R["slide_width_height"]
    X0, Y0 = (int(v) for v in a.origin.split(","))

    log.info(f"載入遮罩 {a.mask}")
    mask = np.load(a.mask, mmap_mode="r")
    log.info(f"  {mask.shape}  max_id={int(np.asarray(mask).max()):,}")

    vfr_mask, c0, r0 = mask_to_vfr(np.asarray(mask), (X0, Y0), A, W0)
    mask_vfr_path = out / f"segmentation_masks_vfr_{a.tag}.npy"
    np.save(mask_vfr_path, vfr_mask)

    # bins：只取 in_tissue，並裁到這塊 VFR 範圍
    from backend.src.roi.extractor import load_visium_adata
    log.info("載入 2µm bins AnnData")
    ad2 = load_visium_adata(SR / "binned_outputs/square_002um", bin_size="002")
    xy = (ad2.obsm["spatial"] if "spatial" in ad2.obsm else
          np.stack([ad2.obs["pxl_col_in_fullres"].values,
                    ad2.obs["pxl_row_in_fullres"].values], 1)).astype(float)
    keep = ((xy[:, 0] >= c0) & (xy[:, 0] < c0 + vfr_mask.shape[1]) &
            (xy[:, 1] >= r0) & (xy[:, 1] < r0 + vfr_mask.shape[0]))
    log.info(f"  範圍內 bins {keep.sum():,} / {len(keep):,}")
    sub = ad2[keep].copy()
    sub_path = out / f"adata_002um_{a.tag}.h5ad"
    sub.write_h5ad(str(sub_path))
    del ad2

    # 在做任何聚合之前先驗資料完整性（見 composition_sanity_check 的說明）
    import numpy as _np
    _m = _np.zeros(sub.n_obs, bool)
    _xy = sub.obsm["spatial"]
    _c = (_xy[:, 0] - c0).round().astype(int)
    _r = (_xy[:, 1] - r0).round().astype(int)
    _ok = (_c >= 0) & (_c < vfr_mask.shape[1]) & (_r >= 0) & (_r < vfr_mask.shape[0])
    _m[_ok] = vfr_mask[_r[_ok], _c[_ok]] > 0
    sanity = composition_sanity_check(sub, _m)

    from backend.src.cellpose_counter.counter import count_rna_per_cell
    cells = count_rna_per_cell(adata_path=sub_path, mask_path=mask_vfr_path,
                               roi_x_px=c0, roi_y_px=r0,
                               pixel_size_um=VFR_UM_PX, dilation_px=a.dilation_px)
    cells_path = out / f"cellpose_cells_{a.tag}.h5ad"
    cells.write_h5ad(str(cells_path))

    nb = cells.obs["n_bins"].values
    X = cells.X; umi = np.asarray(X.sum(1)).ravel()
    log.info(f"\n=== 結果 ===")
    log.info(f"cells={cells.n_obs:,}  bins_assigned={int(nb.sum()):,}  "
             f"bins/cell mean={nb.mean():.2f}  UMI/cell mean={umi.mean():.2f} median={np.median(umi):.0f}")
    (out / f"counting_meta_{a.tag}.json").write_text(json.dumps(
        {"mask": a.mask, "origin_flipped_ndpi": [X0, Y0], "vfr_origin": [c0, r0],
         "vfr_mask_shape": list(vfr_mask.shape), "dilation_px": a.dilation_px,
         "composition_sanity": sanity,
         "n_cells": int(cells.n_obs), "n_bins_assigned": int(nb.sum()),
         "bins_per_cell_mean": float(nb.mean()), "umi_per_cell_mean": float(umi.mean())},
        indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"已寫出 {cells_path}")


if __name__ == "__main__":
    main()
