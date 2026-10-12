#!/usr/bin/env python
"""
Phase 2-4：區室劃分 → 細胞身分 → 分層驗證。

Phase 2 不刪 crystallin 基因（原計畫如此，建立在損壞資料上）。修復後 crystallin
的 Moran's I=0.98，是真實的水晶體組織訊號，刪掉會毀掉水晶體本身的生物學。改成
在空間上把水晶體劃出來。

只對過度離散 >>1 的高豐度標記定身分（lens 125x / RPE 8.5x / Rod 7.9x）。
Müller/Bipolar/Amacrine/RGC 全部 ≈1x —— 標記佔 0.04-0.08%，在 median 116 UMI/cell
下每顆細胞期望值 <0.1 counts，物理上測不到，不納入。

kNN 平滑（k=15）：單細胞比例噪聲大，但 crystallin Moran's I=0.98 表示鄰近細胞
高度一致，平滑提升訊噪比而不破壞空間結構。
"""
import argparse, json, logging, sys
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree

log = logging.getLogger("phase234")

MARKERS = {
    "lens":   None,   # 執行期填入所有 Cry* + Mip
    "rod":    ["Rho", "Nrl", "Nr2e3", "Gnat1", "Pde6b", "Rom1"],
    "rpe":    ["Rpe65", "Ttr", "Best1"],
    "cornea": ["Aldh3a1", "Krt12", "Krt5"],
}


def marker_frac(A, genes, umi):
    gs = [g for g in genes if g in A.var_names]
    v = np.asarray(A.X[:, [A.var_names.get_loc(g) for g in gs]].sum(1)).ravel()
    return np.divide(v, umi, out=np.zeros(len(umi)), where=umi > 0), gs


def knn_smooth(vals, xy, k=15):
    """鄰域平均。回傳每顆細胞含自己在內的 k 個最近鄰平均值。"""
    t = cKDTree(xy)
    _, idx = t.query(xy, k=k)
    return vals[idx].mean(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-umi", type=int, default=50)
    ap.add_argument("--min-bins", type=int, default=3)
    ap.add_argument("--knn", type=int, default=15)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    import anndata as ad
    A = ad.read_h5ad(a.cells)
    umi = np.asarray(A.X.sum(1)).ravel()
    keep = (umi >= a.min_umi) & (A.obs["n_bins"].values >= a.min_bins)
    log.info(f"{A.n_obs:,} cells → 過濾後 {keep.sum():,}（UMI>={a.min_umi} & bins>={a.min_bins}）")
    A = A[keep].copy(); umi = umi[keep]
    xy = np.stack([A.obs.centroid_x_um.values, A.obs.centroid_y_um.values], 1).astype(float)
    sec = A.obs["section"].values if "section" in A.obs else np.array(["all"] * A.n_obs)

    MARKERS["lens"] = [g for g in A.var_names if g.lower().startswith("cry")] + \
                      [g for g in ["Mip"] if g in A.var_names]

    # ── Phase 2/3：標記分數 + kNN 平滑 ──────────────────────────
    raw, sm = {}, {}
    log.info("標記分數（原始 → kNN 平滑）：")
    for k, genes in MARKERS.items():
        f, gs = marker_frac(A, genes, umi)
        raw[k] = f; sm[k] = knn_smooth(f, xy, a.knn)
        log.info(f"  {k:7s} {len(gs):2d} 基因  平均 {f.mean()*100:6.3f}%  "
                 f"平滑後 p99 {np.percentile(sm[k],99)*100:6.2f}%")

    # 階層式絕對門檻，不做跨標記 z-score 比較。
    # 各標記的分布尺度差 10-100 倍（lens 平滑後 p95=61%、rod p99 僅 6.5%），
    # 跨標記比 z-score 會讓尺度大的永遠獲勝 —— 實測 rod 只分到 63 顆。
    #
    # 且 rod 的分布是【單峰】的（p50 就有 2.36%）：rod 佔視網膜細胞 70-80%，
    # 神經視網膜整體都帶 rod 訊號，它不是要偵測的少數族群，而是扣掉其他
    # 組織後的主體。lens/cornea 則是明顯雙峰（空間上獨立的組織）。
    #
    # 因此改為：先切出空間獨立的組織，剩下的視為神經視網膜。
    TH = {"lens": 0.20, "cornea": 0.03, "rpe": 0.005, "rod": 0.02}
    names = list(MARKERS)
    label = np.full(len(umi), "unassigned", dtype=object)
    for k in ["lens", "cornea", "rpe", "rod"]:        # 順序即優先序
        label[(label == "unassigned") & (sm[k] > TH[k])] = k
    log.info(f"階層門檻：{TH}")
    A.obs["compartment"] = label
    for k in MARKERS:
        A.obs[f"score_{k}"] = sm[k]

    import collections
    log.info("\nPhase 2/3 區室與身分指派：")
    cnt = collections.Counter(label)
    for k in names + ["unassigned"]:
        log.info(f"  {k:11s} {cnt.get(k,0):7,d} ({cnt.get(k,0)/len(label)*100:5.1f}%)")

    # ── Phase 4：逐切片分層驗證 ────────────────────────────────
    log.info("\nPhase 4 逐切片徑向排序（眼球切面：水晶體在中心，"
             "由內而外應為 lens < rod < rpe）：")
    res = {}
    for s in sorted(set(sec)):
        m = sec == s
        if m.sum() < 500: continue
        lm = m & (label == "lens")
        if lm.sum() < 50:
            log.info(f"  {s}: 水晶體細胞不足（{lm.sum()}），跳過"); continue
        cx, cy = xy[lm, 0].mean(), xy[lm, 1].mean()      # 水晶體質心 = 眼球中心
        r = np.hypot(xy[:, 0] - cx, xy[:, 1] - cy)
        row = {}
        for k in names:
            kk = m & (label == k)
            row[k] = (int(kk.sum()), float(np.median(r[kk])) if kk.sum() >= 30 else None)
        order = [k for k in ["lens", "rod", "rpe"] if row[k][1] is not None]
        rads = [row[k][1] for k in order]
        ok = all(rads[i] < rads[i+1] for i in range(len(rads)-1))
        txt = "  ".join(f"{k}={row[k][0]:,d}@{row[k][1]:.0f}µm" if row[k][1] else f"{k}=n/a"
                        for k in names)
        log.info(f"  {s}: {txt}   徑向序 {'✓ 正確' if ok else '✗ 不符'}")
        res[s] = {"center_um": [float(cx), float(cy)], "ok": bool(ok),
                  "counts_radius": {k: row[k] for k in names}}

    A.write_h5ad(out / "cells_with_identity.h5ad")
    (out / "phase234_report.json").write_text(json.dumps(
        {"n_cells": int(A.n_obs), "assignment": dict(cnt),
         "markers": {k: [g for g in v if g in A.var_names] for k, v in MARKERS.items()},
         "knn": a.knn, "thresholds": TH, "sections": res}, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"\n已寫出 {out/'cells_with_identity.h5ad'}")


if __name__ == "__main__":
    main()
