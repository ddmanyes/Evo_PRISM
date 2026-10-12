#!/usr/bin/env python
"""
Phase 1 QC：細胞級品質分布 + crystallin 汙染性質判別。

最關鍵的一題是 crystallin（水晶體蛋白）訊號的來源。ROI 試跑顯示它佔總 UMI
18.5%、散布在 67.5% 的細胞裡。兩種可能後續處置完全不同：

  (a) ambient RNA 汙染 -> 空間上均勻，全樣本一致移除即可
  (b) 切片真的帶到水晶體組織 -> 空間上聚集，必須在空間上把那塊劃掉

用 Moran's I 空間自相關 + 置換檢定分辨：ambient 的 I 應接近 0，
真組織會有顯著正自相關。
"""
import argparse, json, logging, sys
from pathlib import Path
import numpy as np

log = logging.getLogger("phase1_qc")

RETINA_MARKERS = {
    "Rod":       ["Rho", "Nrl", "Nr2e3", "Gnat1", "Pde6b", "Rom1"],
    "Cone":      ["Opn1sw", "Opn1mw", "Arr3", "Gnat2", "Pde6h"],
    "Bipolar":   ["Vsx2", "Grm6", "Prkca", "Cabp5"],
    "Horizontal":["Lhx1", "Calb1", "Onecut1"],
    "Amacrine":  ["Pax6", "Gad1", "Slc6a9", "Tfap2b"],
    "Muller":    ["Rlbp1", "Glul", "Slc1a3", "Apoe"],
    "RGC":       ["Rbpms", "Sncg", "Pou4f1", "Nefl"],
    "Microglia": ["Cx3cr1", "C1qa", "P2ry12"],
    "RPE":       ["Rpe65", "Ttr", "Best1"],
}


def morans_I(values, xy, k=8, n_perm=999, seed=0):
    """
    k-最近鄰權重下的 Moran's I + 置換檢定。

    用 kNN 而非固定距離，因為細胞密度在視網膜各層差異極大
    （ONL 極密、IPL 幾乎無核），固定半徑會讓稀疏層沒有鄰居。
    """
    from scipy.spatial import cKDTree
    n = len(values)
    if n < k + 1:
        return None
    tree = cKDTree(xy)
    _, idx = tree.query(xy, k=k + 1)
    nbr = idx[:, 1:]                                   # 去掉自己
    z = values - values.mean()
    denom = (z ** 2).sum()
    if denom == 0:
        return None

    def _I(zz):
        return (n / (n * k)) * (zz[:, None] * zz[nbr]).sum() / (zz ** 2).sum()

    I = _I(z)
    rng = np.random.default_rng(seed)
    null = np.array([_I(rng.permutation(z)) for _ in range(n_perm)])
    p = (np.sum(np.abs(null) >= abs(I)) + 1) / (n_perm + 1)
    return {"I": float(I), "p_perm": float(p),
            "null_mean": float(null.mean()), "null_sd": float(null.std()),
            "z_score": float((I - null.mean()) / null.std()) if null.std() > 0 else None,
            "k": k, "n_perm": n_perm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True, help="cellpose_cells*.h5ad")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="qc")
    ap.add_argument("--grid", type=int, default=24, help="空間網格邊長（格數）")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    import anndata as ad
    A = ad.read_h5ad(a.cells)
    X = A.X
    umi = np.asarray(X.sum(1)).ravel()
    ngene = np.asarray((X > 0).sum(1)).ravel()
    obs = A.obs
    log.info(f"{A.shape[0]:,} cells × {A.shape[1]:,} genes")

    rep = {"input": a.cells, "n_cells": int(A.n_obs), "n_genes": int(A.n_vars)}

    # ── 1. 基本分布 ───────────────────────────────────────────
    def dist(v, name):
        d = {"median": float(np.median(v)), "mean": float(v.mean()),
             "p10": float(np.percentile(v, 10)), "p90": float(np.percentile(v, 90))}
        log.info(f"  {name:14s} median={d['median']:8.1f} mean={d['mean']:8.1f} "
                 f"p10={d['p10']:7.1f} p90={d['p90']:8.1f}")
        return d
    log.info("基本分布：")
    rep["umi_per_cell"] = dist(umi, "UMI/cell")
    rep["genes_per_cell"] = dist(ngene, "genes/cell")
    for col in ("n_bins", "cell_area_um2"):
        if col in obs:
            rep[col] = dist(obs[col].values.astype(float), col)

    # ── 2. 過濾門檻 ───────────────────────────────────────────
    log.info("過濾門檻保留率：")
    rep["thresholds"] = {}
    for t in (0, 25, 50, 100, 200, 500):
        keep = umi >= t
        if "n_bins" in obs:
            keep = keep & (obs["n_bins"].values >= 3)
        rep["thresholds"][f"umi>={t}_and_bins>=3"] = {
            "n": int(keep.sum()), "pct": float(keep.mean() * 100)}
        log.info(f"  UMI>={t:4d} & bins>=3 : {keep.sum():7,} ({keep.mean()*100:5.1f}%)")

    # ── 3. crystallin ────────────────────────────────────────
    cry = [g for g in A.var_names if g.lower().startswith("cry")]
    ci = [A.var_names.get_loc(g) for g in cry]
    cs = np.asarray(X[:, ci].sum(1)).ravel()
    frac = np.divide(cs, umi, out=np.zeros_like(cs, dtype=float), where=umi > 0)
    rep["crystallin"] = {
        "n_genes": len(cry), "pct_of_total_umi": float(cs.sum() / umi.sum() * 100),
        "per_cell_frac_median": float(np.median(frac[umi > 0])),
        "cells_over_50pct": int((frac > 0.5).sum()),
        "cells_expressing": int((cs > 0).sum()),
        "pct_cells_expressing": float((cs > 0).mean() * 100)}
    log.info(f"crystallin：{len(cry)} 基因、佔總 UMI {rep['crystallin']['pct_of_total_umi']:.1f}%、"
             f"{rep['crystallin']['pct_cells_expressing']:.1f}% 細胞有表現")

    # ── 4. 空間性質判別 ───────────────────────────────────────
    xc = "centroid_x_um" if "centroid_x_um" in obs else "centroid_x_px"
    yc = "centroid_y_um" if "centroid_y_um" in obs else "centroid_y_px"
    xy = np.stack([obs[xc].values.astype(float), obs[yc].values.astype(float)], 1)
    ok = umi >= 25                        # 太淺的細胞比例不穩定，會稀釋訊號
    log.info(f"空間自相關（Moran's I，n={ok.sum():,} 顆 UMI>=25 的細胞）...")
    mi = morans_I(frac[ok], xy[ok])
    rep["crystallin_spatial"] = mi
    if mi:
        verdict = ("聚集 → 疑似真有水晶體組織，需在空間上劃掉"
                   if (mi["I"] > 0.15 and mi["p_perm"] < 0.05) else
                   "接近隨機 → 符合 ambient RNA 汙染，可全樣本移除")
        rep["crystallin_verdict"] = verdict
        log.info(f"  Moran's I = {mi['I']:+.4f}  p={mi['p_perm']:.4f}  z={mi['z_score']:+.1f}")
        log.info(f"  判讀：{verdict}")

    # ── 5. 網格圖 ────────────────────────────────────────────
    g = a.grid
    gx = np.clip(((xy[:, 0] - xy[:, 0].min()) / (np.ptp(xy[:, 0]) + 1e-9) * g).astype(int), 0, g - 1)
    gy = np.clip(((xy[:, 1] - xy[:, 1].min()) / (np.ptp(xy[:, 1]) + 1e-9) * g).astype(int), 0, g - 1)
    heat = np.full((g, g), np.nan); cnt = np.zeros((g, g))
    for i in range(g):
        for j in range(g):
            m = (gx == j) & (gy == i) & ok
            cnt[i, j] = m.sum()
            if m.sum() >= 5:
                heat[i, j] = frac[m].mean()
    rep["crystallin_grid"] = {
        "grid": g,
        "cell_frac_range": [float(np.nanmin(heat)), float(np.nanmax(heat))],
        "cell_frac_sd_across_grid": float(np.nanstd(heat))}
    log.info(f"  網格間 crystallin 佔比 SD = {rep['crystallin_grid']['cell_frac_sd_across_grid']:.4f} "
             f"(範圍 {np.nanmin(heat):.3f}–{np.nanmax(heat):.3f})")

    # ── 6. 視網膜標記概覽 ─────────────────────────────────────
    log.info("視網膜標記：")
    rep["markers"] = {}
    for ct, gs in RETINA_MARKERS.items():
        gs2 = [x for x in gs if x in A.var_names]
        if not gs2:
            continue
        idx = [A.var_names.get_loc(x) for x in gs2]
        s = np.asarray(X[:, idx].sum(1)).ravel()
        rep["markers"][ct] = {"genes_found": gs2, "total_umi": float(s.sum()),
                              "pct_cells_expressing": float((s > 0).mean() * 100)}
        log.info(f"  {ct:11s} UMI={s.sum():9,.0f}  表現細胞 {(s>0).mean()*100:5.1f}%")

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(16, 4.4))
        ax[0].hist(np.log10(umi[umi > 0]), bins=60, color="#4C72B0")
        ax[0].set_xlabel("log10 UMI/cell"); ax[0].set_ylabel("cells"); ax[0].set_title("UMI per cell")
        ax[1].hist(frac[umi > 0], bins=60, color="#C44E52")
        ax[1].set_xlabel("crystallin fraction of cell UMI"); ax[1].set_title("Crystallin contamination")
        im = ax[2].imshow(heat, origin="lower", cmap="magma")
        ax[2].set_title("Crystallin fraction (spatial)"); plt.colorbar(im, ax=ax[2])
        plt.tight_layout(); plt.savefig(out / f"qc_{a.tag}.png", dpi=130)
        log.info(f"圖：{out / f'qc_{a.tag}.png'}")
    except Exception as e:
        log.warning(f"繪圖略過：{e}")

    (out / f"qc_report_{a.tag}.json").write_text(
        json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"報告：{out / f'qc_report_{a.tag}.json'}")


if __name__ == "__main__":
    main()
