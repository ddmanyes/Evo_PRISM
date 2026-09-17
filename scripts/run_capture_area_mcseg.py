#!/usr/bin/env python
"""
MQ01_VH_20250428_A1D1 capture-area 40X 分割。

為什麼不用 bio_run_mcseg_fullslide：該路徑走 tifffile.memmap(btf_path) + VFR->TIFF
純縮放，三件事都不成立 ——
  1. 這份 NDPI 是 (3, 41472, 119040, 3) 三焦平面，memmap 失敗會退回 imread() 全解碼 14.8 GB
  2. NDPI 相對 CytAssist 需【水平翻轉】，且有 -0.247° 旋轉（見 ndpi_vfr_registration.json）
  3. 玻片上有 3 對切片，Space Ranger 只拍中間那對；另兩對分割出來配不到任何 bin

改用 run_tiled_mcseg_v2 的 tile_reader 串流路徑：不落地 2.52 GB 中間檔、
work_dir memmap 讓 3.36 GB int32 標籤圖走磁碟、resume 支援中斷續跑。
空白 tile 由 cfg["min_tissue_frac"] 擋掉（461/841 塊含組織，省 45%）。
"""
import argparse, json, logging, sys, time
from pathlib import Path
import numpy as np, cv2, pandas as pd

sys.path.insert(0, "/Volumes/KINGSTON/Evo_PRISM/MSseg")
from backend.src.utils.slide_reader import PyramidSlideReader
from backend.src.segmentation.cellpose_runner import run_tiled_mcseg_v2

EP = Path("/Volumes/KINGSTON/Evo_PRISM")
SAMPLE = "MQ01_VH_20250428_A1D1"
SR = Path("/Volumes/KINGSTON/Bioinfo_Projects/01_Spatial_Transcriptomics/"
          "20251125_TGIA_VisiumHD/20250612_MQ250428/MQ250428-A1-D1/outs")
NDPI = EP / "lab_exchange/MQ250428/D1-2_40X_3_10.53.23.ndpi"

# 與 2026-09-16 ROI 測試（1,837 cells）逐項相同的 7-pass 設定
CFG = dict(use_gpu=True, batch_size=4, voronoi_distance=9, flow_threshold=0.4,
           min_size=20, max_size=6000, use_transcript_rescue=False,
           clahe_clip_limit=3.0, dia_small=13.0, dia_mid=17.0, dia_large=22.0,
           use_hematoxylin=True, cellprob_threshold=-2.0, use_cpsam=True,
           dia_cpsam_auto=0.0, dia_cpsam_small=16.0, cellprob_cpsam_auto=-1.0,
           cellprob_cpsam_small=-3.0, cellprob_cpsam_hema=-1.0)


def capture_area_bbox(A, W0):
    tp = pd.read_parquet(SR / "binned_outputs/square_002um/spatial/tissue_positions.parquet")
    it = tp[tp.in_tissue == 1]
    r0, r1 = it.pxl_row_in_fullres.min(), it.pxl_row_in_fullres.max()
    c0, c1 = it.pxl_col_in_fullres.min(), it.pxl_col_in_fullres.max()
    P = (A @ np.array([[c0, r0, 1], [c1, r0, 1], [c0, r1, 1], [c1, r1, 1]], float).T).T
    x0, y0 = float(P[:, 0].min()), float(P[:, 1].min())
    return int(x0), int(y0), int(np.ceil(P[:, 0].max() - x0)), int(np.ceil(P[:, 1].max() - y0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EP / "results/mcseg" / SAMPLE / "capture_area_40x"))
    ap.add_argument("--tile-size", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--min-tissue-frac", type=float, default=0.01)
    ap.add_argument("--limit-tiles", type=int, default=0,
                    help=">0 時只跑左上角這麼多塊，用於冒煙測試")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    R = json.loads((EP / "results/mcseg" / SAMPLE / "ndpi_vfr_registration.json").read_text())
    A = np.array(R["affine_vfr_to_ndpi_flipped"]); W0, H0 = R["slide_width_height"]
    X0, Y0, W, H = capture_area_bbox(A, W0)
    if a.limit_tiles:
        n = a.limit_tiles * a.tile_size
        W = H = min(n, W, H)
    logging.info(f"capture area @flipped NDPI: origin=({X0},{Y0}) size={W}x{H} "
                 f"({W*H/1e6:.0f} Mpx)")

    reader = PyramidSlideReader(NDPI); reader.__enter__()

    def tile_reader(x, y, w, h):
        """region-local (翻轉後) 座標 -> NDPI 實際讀取 + 翻回來。"""
        fx = X0 + x
        xn = W0 - (fx + w)                      # 翻轉後 x 對應的未翻轉讀取起點
        xn = max(0, min(xn, W0 - w))
        yn = max(0, min(Y0 + y, H0 - h))
        return cv2.flip(reader.read_region(int(xn), int(yn), int(w), int(h), level=0), 1)

    cfg = dict(CFG, sample_id=f"{SAMPLE}_capture40x", min_tissue_frac=a.min_tissue_frac)
    t0 = time.time()
    mask = run_tiled_mcseg_v2(cfg=cfg, tile_size=a.tile_size, overlap=a.overlap,
                              tile_reader=tile_reader, full_shape=(H, W),
                              work_dir=str(out / "work"), resume=True,
                              progress_callback=lambda p, m: logging.info(f"  {p*100:5.1f}% {m}"))
    reader.__exit__(None, None, None)
    n = int(mask.max())
    logging.info(f"分割完成 {n:,} cells，耗時 {(time.time()-t0)/3600:.2f} h")
    np.save(out / "segmentation_masks_raw40x.npy", mask)
    (out / "run_meta.json").write_text(json.dumps(
        {"origin_flipped_ndpi": [X0, Y0], "size": [W, H], "n_cells": n,
         "tile_size": a.tile_size, "overlap": a.overlap,
         "min_tissue_frac": a.min_tissue_frac, "cfg": cfg,
         "registration": "ndpi_vfr_registration.json",
         "elapsed_h": (time.time() - t0) / 3600}, indent=2, ensure_ascii=False), encoding="utf-8")
    logging.info(f"已寫出 {out}")


if __name__ == "__main__":
    main()
