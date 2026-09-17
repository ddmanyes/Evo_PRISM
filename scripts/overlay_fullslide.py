#!/usr/bin/env python
"""
capture-area 40X：細胞遮罩 QC + HE 疊圖。

為什麼需要這支：Phase 1 QC 只看轉錄體分布（UMI/bins/Moran's I），看不出遮罩
畫在哪裡。2026-09-17 產出 96,272 顆細胞，數字全落在合理範圍，但把遮罩疊回 HE
才看得出大量輪廓落在空白玻片上。影像層的檢查不能用數字代替。

QC 判準（互斥，依序命中）——門檻由分布決定，不是憑印象：
  no_bin      無任何 in_tissue bin（不在 counting h5ad 內，或在其中但 n_bins=0）。
              面積中位數 6.1 µm²
              （有 bin 者為 26.2）。背景假陽性主要落在這類。
  low_signal  有 bin 但 bins<3 或 UMI<50。沿用 Phase 1 建議門檻。
  too_large   面積 > 150 µm²（直徑 >13.9 µm，p99）。這類 UMI 中位數 700、
              bins 中位數 45，是【轉錄體門檻抓不到】的多核沾黏 —— 訊號越強
              越穩穩通過，卻違反下游的單細胞假設。形態學上限專門補這個洞。
  too_small   面積 < 7 µm²（直徑 <3.0 µm，小於任何真實細胞核）。此門檻
              98.6% 與 UMI<50 重疊，幾近冗餘，保留只為攔下那 1.4%。
  pass        其餘。

座標系（踩過坑，寫下來）：
  遮罩 (0,0) 對應【水平翻轉後】NDPI 的 (43992, 6050)；NDPI 實際讀取要翻回去
  x_read = W0 - (x_flipped + w)。y 起點 6050 不是 DS 的倍數，直接除會讓 HE 與
  遮罩差半格，改由 6052 起、高度 29288（皆整除 4），兩邊套同一組數字。

遮罩掃兩趟是必要的：第一趟算每個 label 的面積（才能分類），第二趟才能依分類
把邊界上色。單趟做不到。

輸出（皆在 results/mcseg/<SAMPLE>/capture_area_40x/）：
  fullslide_qc_overlay.png        全圖，五類分色
  fullslide_qc_pass_only.png      全圖，只畫通過 QC 的細胞
  fullslide_qc_insets_40x.png     四切片各一塊 L0 原生解析度，五類分色
  fullslide_qc_reject_insets.png  四種剔除情境的特寫對照（空白玻片／水晶體
                                  內部／組織邊緣／沾黏）
  fullslide_qc_report.json        各類計數與依據
  fullslide_qc_labels.csv         逐 label 的面積／bins／UMI／QC 分類，
                                  下游要取 pass 子集直接讀這份
"""
import argparse, json, logging, sys, time
from pathlib import Path
import numpy as np, cv2, h5py

sys.path.insert(0, "/Volumes/KINGSTON/Evo_PRISM/MSseg")
from backend.src.utils.slide_reader import PyramidSlideReader

EP = Path("/Volumes/KINGSTON/Evo_PRISM")
SAMPLE = "MQ01_VH_20250428_A1D1"
NDPI = EP / "lab_exchange/MQ250428/D1-2_40X_3_10.53.23.ndpi"

X0, Y0 = 43992, 6052
WR, HR = 28680, 29288
MASK_Y_OFF, SLIDE_W = 2, 119040
DS, MPP, N_LABELS, CHUNK = 4, 0.23047316140035493, 96272, 2048

MIN_BINS, MIN_UMI = 3, 50
MAX_AREA_UM2, MIN_AREA_UM2 = 150.0, 7.0

# 類別碼 → (名稱, RGB)。pass 最後畫，壓在被剔除的上面。
CLASSES = {
    1: ("pass",       (45, 220, 60)),
    2: ("no_bin",     (245, 90, 30)),
    3: ("low_signal", (205, 60, 200)),
    4: ("too_large",  (40, 110, 250)),
    5: ("too_small",  (250, 205, 40)),
}
DRAW_ORDER = [2, 3, 5, 4, 1]

log = logging.getLogger("qc_overlay")


def read_he(ds):
    r = PyramidSlideReader(NDPI); r.__enter__()
    he = r.read_region((SLIDE_W - (X0 + WR)) // ds, Y0 // ds,
                       WR // ds, HR // ds, level=int(np.log2(ds)))
    r.close()
    return cv2.flip(he, 1)


def label_stats(m):
    """每個 label 的像素數與質心（bincount 分塊累加，比 ndimage 快兩個數量級）。"""
    N = N_LABELS + 1
    area = np.zeros(N, np.int64)
    sx = np.zeros(N, np.float64)
    sy = np.zeros(N, np.float64)
    xs = np.tile(np.arange(WR, dtype=np.float64), CHUNK)
    for r0 in range(0, HR, CHUNK):
        r1 = min(r0 + CHUNK, HR)
        lab = np.asarray(m[MASK_Y_OFF + r0: MASK_Y_OFF + r1, :WR]).ravel()
        area += np.bincount(lab, minlength=N)
        sx += np.bincount(lab, weights=xs[:lab.size], minlength=N)
        sy += np.bincount(lab, weights=np.repeat(
            np.arange(r0, r1, dtype=np.float64), WR), minlength=N)
    with np.errstate(invalid="ignore", divide="ignore"):
        return area, sx / area, sy / area


def read_transcriptome():
    """從 counting 的 h5ad 取每顆細胞的 bins 與 UMI。只有拿到 in_tissue bin
    的細胞會在這裡出現，沒出現的就是 no_bin。"""
    p = EP / "results/mcseg" / SAMPLE / "capture_area_40x" / "cellpose_cells_capture40x.h5ad"
    with h5py.File(p, "r") as f:
        g = f["obs"]["cell_id"]
        cid = (g["categories"][:].astype(str)[g["codes"][:]]
               if isinstance(g, h5py.Group) else g[:])
        cid = np.array([int(c) for c in cid])
        nb = f["obs"]["n_bins"][:].astype(float)
        ip = f["X"]["indptr"][:]
        # cumsum 差分而非 add.reduceat：後者遇到空列會出界
        cs = np.concatenate([[0.0], np.cumsum(f["X"]["data"][:].astype(np.float64))])
        umi = cs[ip[1:]] - cs[ip[:-1]]
    bins_all = np.zeros(N_LABELS + 1)
    umi_all = np.zeros(N_LABELS + 1)
    has = np.zeros(N_LABELS + 1, bool)
    bins_all[cid], umi_all[cid], has[cid] = nb, umi, True
    return bins_all, umi_all, has


def classify(area_um2, bins_all, umi_all, has, ids):
    cls = np.zeros(N_LABELS + 1, np.uint8)
    A, B, U, H = area_um2[ids], bins_all[ids], umi_all[ids], has[ids]
    c = np.full(len(ids), 1, np.uint8)
    c[(H) & ((B < MIN_BINS) | (U < MIN_UMI))] = 3
    # h5ad 裡有列但 n_bins=0 的細胞（counting 的 p10 就是 0）實質等同沒有 bin，
    # 歸 no_bin 而非 low_signal，否則類別名稱與內容不符
    c[(~H) | (B == 0)] = 2
    c[(c == 1) & (A > MAX_AREA_UM2)] = 4
    c[(c == 1) & (A < MIN_AREA_UM2)] = 5
    cls[ids] = c
    return cls


def edge_maps(m, cls, shape):
    """邊界圖依類別分開後各自 max-pool 降採樣。max-pool 而非取樣：DS=4 下
    細胞只有 5 px 寬，取樣會讓整條邊界消失。"""
    Hs, Ws = shape
    out = {k: np.zeros((Hs, Ws), bool) for k in CLASSES}
    for r0 in range(0, HR, CHUNK):
        r1 = min(r0 + CHUNK, HR)
        blk = np.asarray(m[MASK_Y_OFF + r0: MASK_Y_OFF + r1 + 1, :WR])
        cur = blk[: r1 - r0]
        e = np.zeros(cur.shape, bool)
        e |= (blk[1: r1 - r0 + 1] != cur)
        e[:, :-1] |= (cur[:, 1:] != cur[:, :-1])
        h, kc = (r1 - r0) // DS, cls[cur]
        for k, tgt in out.items():
            tgt[r0 // DS: r0 // DS + h] = (e & (kc == k)).reshape(
                h, DS, Ws, DS).max(axis=(1, 3))
    return out


def paint(he, masks, keys, alpha=0.72):
    out = he.astype(np.float32)
    for k in keys:
        col = np.array(CLASSES[k][1], np.float32)
        out[masks[k]] = out[masks[k]] * (1 - alpha) + col * alpha
    return out.astype(np.uint8)


def legend(img, counts, keys):
    h = 42 * len(keys) + 36
    cv2.rectangle(img, (40, 40), (860, 40 + h), (255, 255, 255), -1)
    cv2.rectangle(img, (40, 40), (860, 40 + h), (60, 60, 60), 3)
    for i, k in enumerate(keys):
        y = 76 + i * 42
        cv2.rectangle(img, (62, y - 22), (108, y + 6), CLASSES[k][1], -1)
        cv2.putText(img, f"{CLASSES[k][0]:<11s} {counts[k]:>7,}", (124, y + 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.95, (20, 20, 20), 2)


def crop_panel(reader, m, cls, px, py, box, title):
    he = cv2.flip(reader.read_region(int(SLIDE_W - (X0 + px + box)),
                                     int(Y0 + py), box, box, level=0), 1)
    blk = np.asarray(m[MASK_Y_OFF + py: MASK_Y_OFF + py + box + 1,
                       px: px + box + 1]).astype(np.int64)
    cur = blk[:box, :box]
    e = (blk[1:box + 1, :box] != cur) | (blk[:box, 1:box + 1] != cur)
    kc = cls[cur]
    ov = he.astype(np.float32)
    for k in DRAW_ORDER:
        sel = e & (kc == k)
        if sel.any():
            ov[sel] = ov[sel] * 0.25 + np.array(CLASSES[k][1], np.float32) * 0.75
    ov = ov.astype(np.uint8)
    bar = int(50 / MPP)
    cv2.rectangle(ov, (40, box - 60), (40 + bar, box - 48), (30, 30, 30), -1)
    cv2.putText(ov, "50 um", (40, box - 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)
    pan = np.full((box + 56, box, 3), 255, np.uint8)
    pan[56:] = ov
    cv2.putText(pan, title, (12, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    return pan


def grid_of(panels, cols=2):
    g = 24
    H, W = panels[0].shape[:2]
    rows = (len(panels) + cols - 1) // cols
    out = np.full((H * rows + g * (rows - 1), W * cols + g * (cols - 1), 3), 255, np.uint8)
    for i, p in enumerate(panels):
        out[(i // cols) * (H + g):(i // cols) * (H + g) + H,
            (i % cols) * (W + g):(i % cols) * (W + g) + W] = p
    return out


def density(ids, cx, cy, cls, want, D=16):
    """某類細胞的【顆數】密度（質心 2D 直方圖）。用顆數而非面積覆蓋率：
    背景假陽性又小又散，在覆蓋率上永遠被水晶體內的大片假輪廓蓋過去。"""
    sel = np.isin(cls[ids], want)
    h = np.histogram2d(cy[ids][sel], cx[ids][sel],
                       bins=[HR // D, WR // D], range=[[0, HR], [0, WR]])[0]
    return h.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EP / "results/mcseg" / SAMPLE / "capture_area_40x"))
    ap.add_argument("--box", type=int, default=1200, help="inset 邊長 @L0 px")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out)
    t0 = time.time()

    he = read_he(DS)
    Hs, Ws = he.shape[:2]
    log.info(f"HE {he.shape} (level {int(np.log2(DS))})")

    m = np.load(out / "segmentation_masks_raw40x.npy", mmap_mode="r")
    area, cx, cy = label_stats(m)
    ids = np.nonzero(area[1:])[0] + 1
    area_um2 = area * MPP ** 2
    bins_all, umi_all, has = read_transcriptome()
    cls = classify(area_um2, bins_all, umi_all, has, ids)

    counts = {k: int((cls[ids] == k).sum()) for k in CLASSES}
    for k in DRAW_ORDER:
        sel = cls[ids] == k
        if sel.sum():
            log.info(f"  {CLASSES[k][0]:<11s} {counts[k]:7,} ({counts[k]/len(ids)*100:5.1f}%)  "
                     f"面積 med {np.median(area_um2[ids][sel]):6.1f} µm²  "
                     f"UMI med {np.median(umi_all[ids][sel]):6.0f}")

    masks = edge_maps(m, cls, (Hs, Ws))
    log.info("邊界分類完成")

    ov = paint(he, masks, DRAW_ORDER)
    legend(ov, counts, DRAW_ORDER)
    cv2.imwrite(str(out / "fullslide_qc_overlay.png"),
                cv2.cvtColor(ov, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])

    ovp = paint(he, masks, [1])
    legend(ovp, counts, [1])
    cv2.imwrite(str(out / "fullslide_qc_pass_only.png"),
                cv2.cvtColor(ovp, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])
    log.info("全圖兩張已寫出")

    # inset：四象限取 pass 最密處；另取被剔除最密處一塊
    r = PyramidSlideReader(NDPI); r.__enter__()
    box = a.box
    dp = density(ids, cx, cy, cls, [1])
    k = box // 16
    hs, ws = dp.shape
    panels, picks = [], {}
    for name, (y0, y1, x0, x1) in [("S1", (0, hs // 2, 0, ws // 2)),
                                   ("S2", (0, hs // 2, ws // 2, ws)),
                                   ("S3", (hs // 2, hs, 0, ws // 2)),
                                   ("S4", (hs // 2, hs, ws // 2, ws))]:
        d = cv2.boxFilter(dp, -1, (k, k), normalize=True)[y0 + k // 2: y1 - k // 2,
                                                          x0 + k // 2: x1 - k // 2]
        i, j = np.unravel_index(np.argmax(d), d.shape)
        px, py = (x0 + k // 2 + j) * 16 - box // 2, (y0 + k // 2 + i) * 16 - box // 2
        picks[name] = [int(px), int(py)]
        panels.append(crop_panel(r, m, cls, px, py, box, f"{name}  pass-dense"))
        log.info(f"  inset {name}: ({px},{py})")
    cv2.imwrite(str(out / "fullslide_qc_insets_40x.png"),
                cv2.cvtColor(grid_of(panels), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])

    # 被剔除的細胞不是同一回事，分四種情境各取一塊看清楚剔掉了什麼
    dr = cv2.boxFilter(density(ids, cx, cy, cls, [2, 3]), -1, (k, k), normalize=True)
    dpb = cv2.boxFilter(dp, -1, (k, k), normalize=True)
    dl = cv2.boxFilter(density(ids, cx, cy, cls, [4]), -1, (k, k), normalize=True)
    he16 = cv2.resize(cv2.cvtColor(he, cv2.COLOR_RGB2HSV)[:, :, 1],
                      (dr.shape[1], dr.shape[0]), interpolation=cv2.INTER_AREA)
    blank = he16 < 40          # 空白玻片：低飽和度
    # 門檻用分位數而非硬編值：顆數密度的絕對尺度隨 box 與網格大小改變，
    # 寫死的數字換個參數就全部落空（曾讓 tissue edge 選到 (0,0)）。
    q = lambda a, p: float(np.percentile(a[a > 0], p)) if (a > 0).any() else 0.0
    p_hi, r_hi, p_lo = q(dpb, 70), q(dr, 70), q(dpb, 20)
    scen = [("blank slide",    np.where(blank, dr, 0)),
            ("lens interior",  np.where((~blank) & (dpb < p_lo), dr, 0)),
            ("tissue edge",    np.where((dpb > p_hi) & (dr > r_hi), dr * dpb, 0)),
            ("clumped >150um2", dl)]
    rpanels = []
    for name, score in scen:
        if not score.any():
            log.warning(f"  reject inset [{name}]: 無符合條件的區域，略過")
            continue
        i, j = np.unravel_index(np.argmax(score), score.shape)
        px = max(0, min(j * 16 - box // 2, WR - box))
        py = max(0, min(i * 16 - box // 2, HR - box))
        picks[name] = [int(px), int(py)]
        rpanels.append(crop_panel(r, m, cls, px, py, box, f"reject: {name}"))
        log.info(f"  reject inset [{name}]: ({px},{py})")
    cv2.imwrite(str(out / "fullslide_qc_reject_insets.png"),
                cv2.cvtColor(grid_of(rpanels), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_PNG_COMPRESSION, 6])
    r.close()

    import csv
    with open(out / "fullslide_qc_labels.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["label_id", "area_um2", "centroid_x_px", "centroid_y_px",
                    "n_bins", "umi", "qc_class"])   # 質心為 region-local L0 px
        for lid, ar, x, y in zip(ids, area_um2[ids], cx[ids], cy[ids]):
            w.writerow([int(lid), round(float(ar), 2), round(float(x), 1),
                        round(float(y), 1), int(bins_all[lid]), int(umi_all[lid]),
                        CLASSES[cls[lid]][0]])
    log.info(f"寫出 fullslide_qc_labels.csv（{len(ids):,} 列）")

    json.dump({"thresholds": {"min_bins": MIN_BINS, "min_umi": MIN_UMI,
                              "max_area_um2": MAX_AREA_UM2, "min_area_um2": MIN_AREA_UM2},
               "n_labels": int(len(ids)),
               "counts": {CLASSES[k][0]: counts[k] for k in CLASSES},
               "pass_frac": counts[1] / len(ids),
               "median_area_um2": {CLASSES[k][0]: float(np.median(area_um2[ids][cls[ids] == k]))
                                   for k in CLASSES if counts[k]},
               "median_umi": {CLASSES[k][0]: float(np.median(umi_all[ids][cls[ids] == k]))
                              for k in CLASSES if counts[k]},
               "inset_origins_L0": picks},
              open(out / "fullslide_qc_report.json", "w"), indent=2)
    log.info(f"完成，{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
