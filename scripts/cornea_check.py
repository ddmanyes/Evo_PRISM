#!/usr/bin/env python
"""
角膜的分割品質與身分指派檢查（承接 overlay_fullslide.py 的逐 label QC）。

為什麼角膜要單獨看：角膜是分層鱗狀上皮 + 稀疏基質，核的形狀與密度都跟視網膜
的 rod 差很多（rod 核是高度緻密的特化異染色質核，~5 µm；角膜上皮核明顯更大），
全樣本的面積分布看不出它有沒有被切對。

四個踩過的坑，寫下來避免重蹈：

1. **不要用象限切切片**。S1 的角膜 x 最遠到 15616，越過 capture area 中線
   （WR/2=14340），象限法會把它算成 S2。一律讀 h5ad 自己的 `section` 欄位。

2. **判斷基質在上皮的哪一側，不能只看「遮罩覆蓋率低」**。角膜外側是空白玻片，
   覆蓋率更低（0.3% vs 51.3%），會選錯邊。基質的定義是【HE 有染色（有組織）
   且細胞稀疏】，兩個條件缺一不可。

3. **keratocyte 標記要用 Kera，不能用 Col1a1/Lum/Dcn**。後者是泛結締組織標記，
   鞏膜同樣富含，實測 rpe 那群也佔了 13.2%，會支持錯誤的結論。換成角膜基質特異
   的 Kera（keratocan）後結論就變了——全樣本只有 109/40,524 顆有訊號。

4. `var/_index` 是 `nullable-string-array` 編碼（values+mask），不是 categorical，
   也不是純 dataset。AnnData 的字串欄三種編碼都要能讀。

輸出（results/mcseg/<SAMPLE>/capture_area_40x/）：
  cornea_segmentation_check.png   各切片角膜最密處 + 沾黏處，cornea 另外上色
  cornea_stroma_zoom.png          上皮 vs 基質不同深度，畫出所有 label
  cornea_check_report.json        逐切片、逐區室的量化與 Kera 檢定
"""
import argparse, csv, json, logging, sys, time
from collections import Counter
from pathlib import Path
import numpy as np, cv2, h5py
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree
from scipy.stats import mannwhitneyu

sys.path.insert(0, "/Volumes/KINGSTON/Evo_PRISM/MSseg")
sys.path.insert(0, "/Volumes/KINGSTON/Evo_PRISM/scripts")
from backend.src.utils.slide_reader import PyramidSlideReader
from overlay_fullslide import (label_edges, X0, Y0, WR, HR, MASK_Y_OFF,
                               SLIDE_W, MPP, EP, SAMPLE, NDPI)

EPI_MARKERS = ["Aldh3a1", "Krt12", "Krt5"]      # 全是【上皮】標記，見報告
STROMA_MARKER = ["Kera"]                         # 角膜基質特異
COL = {"pass": (45, 220, 60), "no_bin": (245, 90, 30), "low_signal": (205, 60, 200),
       "too_small": (250, 205, 40), "too_large": (40, 110, 250),
       "cornea": (0, 205, 255), "cornea_big": (255, 60, 60)}
log = logging.getLogger("cornea")


def h5col(o):
    """AnnData 字串欄三種編碼：純 dataset / categorical / nullable-string-array。"""
    if not isinstance(o, h5py.Group):
        return o[:] if o.dtype.kind not in "SO" else o[:].astype(str)
    if "categories" in o:
        return o["categories"][:].astype(str)[o["codes"][:]]
    return o["values"][:].astype(str)


def load(out):
    qc = {}
    with open(out / "fullslide_qc_labels.csv") as fh:
        for r in csv.DictReader(fh):
            qc[int(r["label_id"])] = (r["qc_class"], float(r["area_um2"]),
                                      float(r["centroid_x_px"]), float(r["centroid_y_px"]))
    with h5py.File(out / "cells_with_identity.h5ad", "r") as f:
        cid = np.array([int(c) for c in h5col(f["obs"]["cell_id"])])
        comp = h5col(f["obs"]["compartment"])
        sect = h5col(f["obs"]["section"])
        xu = f["obs"]["centroid_x_um"][:].astype(float)
        yu = f["obs"]["centroid_y_um"][:].astype(float)
        genes = h5col(f["var"]["_index"])
        X = csr_matrix((f["X"]["data"][:], f["X"]["indices"][:], f["X"]["indptr"][:]),
                       shape=(len(comp), len(genes)))
    return qc, cid, comp, sect, xu, yu, genes, X


def gene_frac(X, genes, names):
    tot = np.maximum(np.asarray(X.sum(1)).ravel(), 1)
    idx = [i for i, g in enumerate(genes) if g in set(names)]
    return np.asarray(X[:, idx].sum(1)).ravel() / tot, [genes[i] for i in idx]


def panel(reader, m, colmap, px, py, box, title, extra=""):
    px = max(0, min(px, WR - box)); py = max(0, min(py, HR - box))
    he = cv2.flip(reader.read_region(int(SLIDE_W - (X0 + px + box)), int(Y0 + py),
                                     box, box, level=0), 1)
    y0, y1 = max(0, py - 1), min(HR, py + box + 1)
    x0, x1 = max(0, px - 1), min(WR, px + box + 1)
    blk = np.asarray(m[MASK_Y_OFF + y0: MASK_Y_OFF + y1, x0: x1]).astype(np.int64)
    oy, ox = py - y0, px - x0
    cur = blk[oy: oy + box, ox: ox + box]
    e = label_edges(cur,
                    up=blk[oy - 1, ox: ox + box] if oy else None,
                    down=blk[oy + box, ox: ox + box] if y1 > py + box else None,
                    left=blk[oy: oy + box, ox - 1] if ox else None,
                    right=blk[oy: oy + box, ox + box] if x1 > px + box else None)
    kc = colmap[cur]
    ov = he.astype(np.float32)
    keys = list(COL)
    for i, k in enumerate(keys, start=1):
        sel = e & (kc == i)
        if sel.any():
            ov[sel] = ov[sel] * 0.2 + np.array(COL[k], np.float32) * 0.8
    ov = ov.astype(np.uint8)
    bar = int(50 / MPP)
    cv2.rectangle(ov, (36, box - 54), (36 + bar, box - 42), (30, 30, 30), -1)
    cv2.putText(ov, "50 um", (36, box - 66), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (30, 30, 30), 2)
    pan = np.full((box + 52, box, 3), 255, np.uint8); pan[52:] = ov
    cv2.putText(pan, f"{title}{extra}", (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (20, 20, 20), 2)
    return pan


def grid_of(panels, cols):
    g = 22
    H, W = panels[0].shape[:2]
    rows = (len(panels) + cols - 1) // cols
    out = np.full((H * rows + g * (rows - 1), W * cols + g * (cols - 1), 3), 255, np.uint8)
    for i, p in enumerate(panels):
        out[(i // cols) * (H + g):(i // cols) * (H + g) + H,
            (i % cols) * (W + g):(i % cols) * (W + g) + W] = p
    return out


def legend(img, keys, x=34, y=52, w=520):
    h = 36 * len(keys) + 26
    cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 255), -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (60, 60, 60), 2)
    for i, (k, t) in enumerate(keys):
        yy = y + 32 + i * 36
        cv2.rectangle(img, (x + 16, yy - 19), (x + 58, yy + 3), COL[k], -1)
        cv2.putText(img, t, (x + 72, yy), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EP / "results/mcseg" / SAMPLE / "capture_area_40x"))
    ap.add_argument("--box", type=int, default=1600, help="角膜總覽 inset 邊長 @L0 px")
    ap.add_argument("--zoom-box", type=int, default=900,
                help="角膜全層特寫邊長 @L0 px（900 px = 207 µm，角膜全厚 ~100 µm）")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out); t0 = time.time()

    qc, cid, comp, sect, xu, yu, genes, X = load(out)
    epi, g_epi = gene_frac(X, genes, EPI_MARKERS)
    ker, g_ker = gene_frac(X, genes, STROMA_MARKER)
    A = np.array([qc[c][1] for c in cid]); dia = 2 * np.sqrt(A / np.pi)
    U = np.array([int(qc[c][0] == "pass") for c in cid])
    CL = np.array([qc[c][0] for c in cid])
    cx = np.array([qc[c][2] for c in cid]); cy = np.array([qc[c][3] for c in cid])
    log.info(f"上皮標記 {g_epi}；基質標記 {g_ker}")

    rep = {"n_cells": int(len(cid)), "epi_markers": g_epi, "stroma_marker": g_ker,
           "by_compartment": {}, "cornea_by_section": {}, "kera": {}, "insets": {}}
    log.info(f"{'區室':<12}{'n':>7}{'面積med':>9}{'直徑med':>9}{'QCpass':>9}{'too_large':>10}")
    for c in sorted(set(comp)):
        s = comp == c
        d = dict(n=int(s.sum()), area_med=float(np.median(A[s])),
                 dia_med=float(np.median(dia[s])), area_p95=float(np.percentile(A[s], 95)),
                 pass_rate=float((CL[s] == "pass").mean()),
                 too_large_rate=float((CL[s] == "too_large").mean()),
                 epi_score_med=float(np.median(epi[s])))
        rep["by_compartment"][c] = d
        log.info(f"{c:<12}{d['n']:>7,}{d['area_med']:>9.1f}{d['dia_med']:>9.2f}"
                 f"{d['pass_rate']*100:>8.1f}%{d['too_large_rate']*100:>9.1f}%")

    cor = comp == "cornea"
    for s in sorted(set(sect)):
        m_ = cor & (sect == s)
        rep["cornea_by_section"][str(s)] = dict(
            n=int(m_.sum()),
            area_med=float(np.median(A[m_])) if m_.sum() else None,
            pass_rate=float((CL[m_] == "pass").mean()) if m_.sum() else None)
        log.info(f"  cornea {s}: n={m_.sum()}")

    # Kera 檢定：keratocyte 到底測不測得到
    hi = ker > 0
    rep["kera"] = {"n_expressing": int(hi.sum()), "frac": float(hi.mean()),
                   "by_compartment": {c: int(((comp == c) & hi).sum()) for c in sorted(set(comp))}}
    log.info(f"Kera>0 僅 {hi.sum():,}/{len(cid):,} ({hi.mean()*100:.2f}%) "
             f"→ 基質 keratocyte 在此深度測不到，與 Muller/Bipolar 同一類限制")
    near = cKDTree(np.c_[xu[cor], yu[cor]]).query(np.c_[xu, yu], k=1)[0] <= 60
    nu, fu = near & (comp == "unassigned"), (~near) & (comp == "unassigned")
    if nu.sum() > 10 and fu.sum() > 10:
        p = mannwhitneyu(ker[nu], ker[fu], alternative="greater").pvalue
        rep["kera"]["near_vs_far_unassigned_p"] = float(p)
        log.info(f"  角膜鄰域 unassigned({nu.sum()}) vs 遠處({fu.sum()}) Kera: p={p:.2e}")

    # ---- 圖 1：各切片角膜最密處 + 沾黏處 ----
    N = max(qc) + 1
    keys = list(COL)
    cmap = np.zeros(N, np.uint8)
    for lid, (cl, *_rest) in qc.items():
        cmap[lid] = keys.index(cl) + 1
    cor_ids = set(cid[cor].tolist())
    for lid in cor_ids:
        cmap[lid] = keys.index("cornea_big" if qc[lid][0] == "too_large" else "cornea") + 1

    r = PyramidSlideReader(NDPI); r.__enter__()
    box = a.box
    def densest(sel, G=400):
        c = Counter((int(y // G) * G, int(x // G) * G) for x, y in zip(cx[sel], cy[sel]))
        (gy, gx), n = c.most_common(1)[0]
        return gx + G // 2 - box // 2, gy + G // 2 - box // 2, n

    panels = []
    for s in ["S1", "S2", "S3", "S4"]:
        sel = cor & (sect == s)
        if sel.sum() < 5:
            log.info(f"  {s}: 無角膜（n={sel.sum()}），略過"); continue
        px, py, _ = densest(sel)
        rep["insets"][f"cornea_{s}"] = [int(px), int(py)]
        panels.append(panel(r, np.load(out / "segmentation_masks_raw40x.npy", mmap_mode="r"),
                            cmap, px, py, box, f"{s} cornea densest"))
    big = cor & (CL == "too_large")
    if big.sum() >= 3:
        px, py, _ = densest(big)
        rep["insets"]["cornea_too_large"] = [int(px), int(py)]
        panels.append(panel(r, np.load(out / "segmentation_masks_raw40x.npy", mmap_mode="r"),
                            cmap, px, py, box, f"cornea too_large (n={int(big.sum())})"))
    grid = grid_of(panels, 2)
    legend(grid, [("cornea", "cornea epithelium (labeled)"), ("cornea_big", "cornea too_large"),
                  ("pass", "other pass"), ("no_bin", "rejected")], w=640)
    cv2.imwrite(str(out / "cornea_segmentation_check.png"),
                cv2.cvtColor(grid, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])
    log.info("寫出 cornea_segmentation_check.png")

    # ---- 圖 2：上皮 vs 基質，畫出所有 label（不分身分）----
    m = np.load(out / "segmentation_masks_raw40x.npy", mmap_mode="r")
    plain = np.zeros(N, np.uint8)
    for lid, (cl, *_rest) in qc.items():
        plain[lid] = keys.index(cl) + 1
    zb = a.zoom_box
    s1 = cor & (sect == "S1")
    G = 300
    gy, gx = Counter((int(b // G) * G, int(aa // G) * G)
                     for aa, b in zip(cx[s1], cy[s1])).most_common(1)[0][0]
    loc = (np.abs(cx[s1] - gx) < 400) & (np.abs(cy[s1] - gy) < 400)
    P = np.c_[cx[s1][loc], cy[s1][loc]]; P = P - P.mean(0)
    v = np.linalg.svd(P, full_matrices=False)[2][0]
    nrm = np.array([-v[1], v[0]])
    cen = np.array([gx, gy], float)
    probe = {}
    for sgn in (+1, -1):
        pt = cen + nrm * sgn * 260
        yy, xx = int(pt[1]), int(pt[0])
        sub = np.asarray(m[MASK_Y_OFF + max(0, yy - 150): MASK_Y_OFF + yy + 150,
                           max(0, xx - 150): xx + 150])
        hep = cv2.flip(r.read_region(int(SLIDE_W - (X0 + xx + 150)), int(Y0 + yy - 150),
                                     300, 300, level=0), 1)
        probe[sgn] = ((sub > 0).mean(), float(cv2.cvtColor(hep, cv2.COLOR_RGB2HSV)[:, :, 1].mean()))
        log.info(f"  法線 {sgn:+d} 側：覆蓋 {probe[sgn][0]*100:.1f}%  HE 飽和 {probe[sgn][1]:.1f}")
    tis = {k: v_ for k, v_ in probe.items() if v_[1] > 45}
    side = min(tis, key=lambda k: tis[k][0]) if tis else min(probe, key=lambda k: probe[k][0])
    log.info(f"  基質在 {side:+d} 側")
    # 沿【切線】取三段，不是沿法線取不同深度：小鼠角膜全厚才 ~100 µm（434 px），
    # 而視野是 900 px，往法線平移只會得到三張幾乎重疊、卻標著不同深度的圖。
    # 沿切線移動則每格都含完整的上皮 + 基質，看的是不同位置的一致性。
    # 取樣點用角膜細胞【沿主軸的分位數】，不用固定平移距離：角膜弧長有限，
    # 固定 ±zb 會讓最後一格跑出角膜、落到虹膜與色素上（實際發生過），
    # 標題卻還寫著 cornea。分位數保證三格都落在角膜自己身上。
    proj = np.c_[cx[s1], cy[s1]] @ v
    zp = []
    for i, q in enumerate((25, 50, 75)):
        k = int(np.argmin(np.abs(proj - np.percentile(proj, q))))
        px_ = int(cx[s1][k] - zb // 2); py_ = int(cy[s1][k] - zb // 2)
        rep["insets"][f"cornea_span_p{q}"] = [px_, py_]
        zp.append(panel(r, m, plain, px_, py_, zb,
                        f"S1 cornea, arc p{q} (epi + stroma)"))
    r.close()
    zg = grid_of(zp, 3)
    legend(zg, [(k, k) for k in ["pass", "no_bin", "low_signal", "too_small", "too_large"]], w=380)
    cv2.imwrite(str(out / "cornea_stroma_zoom.png"),
                cv2.cvtColor(zg, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])
    log.info("寫出 cornea_stroma_zoom.png")

    json.dump(rep, open(out / "cornea_check_report.json", "w"), indent=2, ensure_ascii=False)
    log.info(f"完成，{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
