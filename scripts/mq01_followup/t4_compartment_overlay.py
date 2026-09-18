#!/usr/bin/env python
"""
T4: compartment (rod/lens/cornea/rpe) 著色 H&E 疊圖。

為什麼需要這支：現有的 fullslide_qc_overlay / fullslide_he_mask_overlay /
fullslide_qc_pass_only 全部依 QC 類別（pass/no_bin/low_signal/too_large/too_small）
分色，整個樣本沒有一張是依【生物身分】著色疊在 H&E 上。phase234_compartments.png
是純散點沒有 H&E 底圖，看不出身分指派跟組織結構對不對得上。這支就是要補這張圖。

座標系與遮罩掃描邏輯【完全重用】 overlay_fullslide.py（同目錄上一層 scripts/）：
X0,Y0,WR,HR,MASK_Y_OFF,SLIDE_W,DS,MPP,N_LABELS,CHUNK 全部從那支 import，
label_stats()／label_edges()／edge_maps()／paint()／legend()／crop_panel()／
grid_of()／density() 也直接重用，不重新推導座標。edge_maps/paint/legend/
crop_panel 內部吃的是模組全域的 CLASSES/DRAW_ORDER，所以這支用「換上自己的
COMPARTMENT_CLASSES/DRAW_ORDER 再呼叫」的方式重用（見 _use_compartment_palette()），
不是重寫那些函式。

分類來源：cells_with_identity.h5ad 的 obs['compartment']（cornea/lens/rod/rpe/
unassigned，40,524 顆）用 cell_id 對回 segmentation_masks_raw40x.npy 的 label id
（PLAN.md 已驗證 40,524/40,524 100% 對得起來）。h5ad 自己的 centroid_x_px/y_px
【不能用】——那是 P0-0 記的 VFR 粗遮罩座標（4.6706 µm/px），跟 raw40x label 遮罩
是不同座標系；本腳本一律用 overlay_fullslide.label_stats() 掃 raw40x 遮罩重新算
的質心（region-local L0 px，跟 crop_panel/paint 用的是同一個座標系）。

著色範圍的決定（在 --out 的 meta json 裡也會記）：
  - 96,272 個 label 裡只有 40,524 顆在 h5ad 有 compartment。其餘 55,748 顆
    【不畫】（不是淡灰）：H&E 本身已經看得到組織與核的密度，額外疊一層淡灰
    邊界只會讓畫面变得跟既有的 fullslide_he_mask_overlay 系列重複，反而稀釋
    這張圖唯一要凸顯的訊號（身分是否落在對的組織層）。
  - unassigned（QC 有過但沒有 compartment 比對上，13,675 顆）畫中灰，
    當作「這裡有 QC-pass 細胞但身分不明」的背景參照，不是完全略過。

Insets 的選點邏輯：h5ad 的 obs['section']（S1-S4，四片組織）分別找 rod 與 rpe
兩個類別質心【同時】密度高的位置（用 overlay_fullslide.density() 分別算
rod-only／rpe-only 的顆數密度，box-filter 後取 min(rod,rpe) 的 argmax），
這樣找到的位置保證是 ONL(rod)/RPE 真的相鄰的交界帶，而不是隨便哪裡 rod 多。
挑完之後再檢查該 inset 框內是否有 lens 細胞落入，寫進 meta 供覆核。
"""
import argparse, json, logging, sys, time
from pathlib import Path
import numpy as np, cv2, h5py

SCRIPTS_DIR = Path("/Volumes/KINGSTON/Evo_PRISM/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
import overlay_fullslide as ovf  # noqa: E402  -- 座標系/遮罩掃描邏輯全部重用

sys.path.insert(0, "/Volumes/KINGSTON/Evo_PRISM/MSseg")
from backend.src.utils.slide_reader import PyramidSlideReader  # noqa: E402  (crop_panel 內部用)

EP = ovf.EP
SAMPLE = ovf.SAMPLE
NDPI = ovf.NDPI
X0, Y0, WR, HR = ovf.X0, ovf.Y0, ovf.WR, ovf.HR
MASK_Y_OFF, SLIDE_W, DS, MPP = ovf.MASK_Y_OFF, ovf.SLIDE_W, ovf.DS, ovf.MPP
N_LABELS, CHUNK = ovf.N_LABELS, ovf.CHUNK

OUT = EP / "results/mcseg" / SAMPLE / "capture_area_40x"
H5AD = OUT / "cells_with_identity.h5ad"
MASK_NPY = OUT / "segmentation_masks_raw40x.npy"

# 類別碼刻意跟 overlay_fullslide.CLASSES 的 1-5 錯開（那是 QC 類別，不共用同一個碼域），
# 0 = 不畫（背景 + 沒有 compartment 的 55,748 顆）。
COMPARTMENT_CLASSES = {
    2: ("unassigned", (150, 150, 150)),
    3: ("cornea", (40, 180, 95)),
    4: ("lens", (255, 205, 40)),
    5: ("rod (ONL)", (225, 35, 35)),
    6: ("rpe", (120, 60, 20)),
}
NAME_TO_CODE = {"unassigned": 2, "cornea": 3, "lens": 4, "rod": 5, "rpe": 6}
DRAW_ORDER = [2, 5, 4, 3, 6]  # unassigned 底、rod 其次、lens/cornea 再上、rpe 壓最上層（最稀少最要看清界線）

log = logging.getLogger("t4_compartment_overlay")


def _maxpool_bool(a, k):
    """布林陣列做 k×k max-pool。

    max-pool 具結合性：對 DS=4 的邊界圖再 pool k 次，結果等同直接對原遮罩做
    DS=4k —— 所以交付版總覽不需要第二趟 3.1 GB 全掃描，直接從既有 masks 導出。
    用 any() 而非取樣，理由同 edge_maps 的 docstring：取樣會讓整條邊界消失。
    """
    h, w = a.shape
    h2, w2 = h // k, w // k
    return a[: h2 * k, : w2 * k].reshape(h2, k, w2, k).any(axis=(1, 3))


def _fit_to(a, h, w):
    """把布林圖裁切／補零到剛好 (h, w)，吸收 WR/HR 不整除造成的 1 px 誤差。"""
    out = np.zeros((h, w), bool)
    hh, ww = min(h, a.shape[0]), min(w, a.shape[1])
    out[:hh, :ww] = a[:hh, :ww]
    return out


def _legend_small(img, counts, keys, scale=0.38):
    """ovf.legend 的等比縮小版。

    原版固定畫 820 px 寬的框，疊在 1792 px 的交付版總覽上會吃掉 46% 畫面。
    這裡按 scale 縮，資訊與配色完全相同。
    """
    row = max(1, int(42 * scale))
    box_w = int(820 * scale)
    box_h = row * len(keys) + int(36 * scale)
    x0, y0 = int(40 * scale), int(40 * scale)
    cv2.rectangle(img, (x0, y0), (x0 + box_w, y0 + box_h), (255, 255, 255), -1)
    cv2.rectangle(img, (x0, y0), (x0 + box_w, y0 + box_h), (60, 60, 60), max(1, int(3 * scale)))
    for i, k in enumerate(keys):
        y = y0 + int(36 * scale) + i * row
        sw = int(46 * scale)
        cv2.rectangle(img, (x0 + int(22 * scale), y - int(22 * scale)),
                      (x0 + int(22 * scale) + sw, y + int(6 * scale)),
                      COMPARTMENT_CLASSES[k][1], -1)
        cv2.putText(img, f"{COMPARTMENT_CLASSES[k][0]:<11s} {counts[k]:>7,}",
                    (x0 + int(124 * scale), y + int(2 * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.95 * scale, (20, 20, 20),
                    max(1, int(2 * scale)))


def _use_compartment_palette():
    """把 overlay_fullslide 模組的全域 CLASSES/DRAW_ORDER 換成本腳本的配色，
    這樣 edge_maps/paint/legend/crop_panel 這些吃全域變數的函式就能原封不動重用。"""
    ovf.CLASSES = COMPARTMENT_CLASSES
    ovf.DRAW_ORDER = DRAW_ORDER


def load_compartment_cls():
    """回傳 (cls, cell_id, compartment_name_per_cell, section_code_per_label, section_cats)。
    cls: uint8 陣列，index=label id，值=COMPARTMENT_CLASSES 的碼，0=不畫。"""
    with h5py.File(H5AD, "r") as f:
        obs = f["obs"]
        cell_id = obs["cell_id"][:].astype(np.int64)
        comp_cats = obs["compartment"]["categories"][:].astype(str)
        comp_codes = obs["compartment"]["codes"][:]
        sec_cats = obs["section"]["categories"][:].astype(str)
        sec_codes = obs["section"]["codes"][:]

    cat_to_class = np.array([NAME_TO_CODE[c] for c in comp_cats], dtype=np.uint8)
    cell_class = cat_to_class[comp_codes]
    comp_name = comp_cats[comp_codes]

    cls = np.zeros(N_LABELS + 1, dtype=np.uint8)
    cls[cell_id] = cell_class

    section_of_label = np.full(N_LABELS + 1, -1, dtype=np.int8)
    section_of_label[cell_id] = sec_codes

    return cls, cell_id, comp_name, section_of_label, sec_cats


def pick_section_inset(section_ids, cx, cy, cls, box_px):
    """在某一切片（section_ids = 該片所有有 compartment 的 label id）裡找
    能同時看到 rod/RPE/lens 交界的位置。

    兩階段，先確定「這是真的 rod/RPE 交界」，再在夠格的交界候選裡挑離 lens
    最近的一個——單純取 rod/RPE 聯合密度最大值（第一版做法）會選到 rod/RPE
    真的相鄰但離 lens 動輒 350-1300 um 的地方（實測過，見腳本開發紀錄），
    box 開多大都框不進 lens；反過來若單純找離 lens 最近的點，又可能根本
    不在 rod/RPE 交界上。所以：
      1. 用固定 900px（~207 um，不隨 box_px 縮放，這樣候選集合的「多緊」
         判準不會因為 box 選多大而跟著鬆）的核算 rod-only、rpe-only 密度，
         取 min(rod,rpe) 當作交界強度；保留強度贏過中位數的格點【當交界候選】。
      2. 在候選裡挑「離最近 lens 細胞的距離」最小的一格，當作 inset 中心。
    回傳 (px, py, lens_visible, n_rod_in_box, n_rpe_in_box, n_lens_in_box)。"""
    D = 16
    d_rod = ovf.density(section_ids, cx, cy, cls, [5], D=D)
    d_rpe = ovf.density(section_ids, cx, cy, cls, [6], D=D)
    k = max(1, 900 // D)
    br = cv2.boxFilter(d_rod, -1, (k, k), normalize=True)
    bp = cv2.boxFilter(d_rpe, -1, (k, k), normalize=True)
    junction = np.minimum(br, bp)

    x, y, c = cx[section_ids], cy[section_ids], cls[section_ids]
    lens_x, lens_y = x[c == 4], y[c == 4]

    if (junction > 0).any() and lens_x.size:
        thresh = np.percentile(junction[junction > 0], 50)
        ys, xs = np.nonzero(junction > thresh)
        cand_x = xs * D + D / 2
        cand_y = ys * D + D / 2
        # 逐候選找最近 lens 距離；候選數量是萬級（D=16 網格），向量化距離矩陣
        # 對單一切片一次跑沒問題（label_stats/edge_maps 才是真正吃記憶體的兩趟）
        dmin = np.array([np.hypot(lens_x - a, lens_y - b).min()
                          for a, b in zip(cand_x, cand_y)])
        idx = int(np.argmin(dmin))
        bx, by = cand_x[idx], cand_y[idx]
        nearest_lens_px = float(dmin[idx])
    else:
        log.warning("    此切片找不到 rod/rpe 交界候選或沒有 lens 細胞，改用交界強度最大值")
        score = junction if (junction > 0).any() else bp
        i, j = np.unravel_index(np.argmax(score), score.shape)
        bx, by = j * D + D / 2, i * D + D / 2
        nearest_lens_px = float(np.hypot(lens_x - bx, lens_y - by).min()) if lens_x.size else float("nan")

    px = int(np.clip(bx - box_px / 2, 0, WR - box_px))
    py = int(np.clip(by - box_px / 2, 0, HR - box_px))

    in_box = (x >= px) & (x < px + box_px) & (y >= py) & (y < py + box_px)
    n_rod = int(((c == 5) & in_box).sum())
    n_rpe = int(((c == 6) & in_box).sum())
    n_lens = int(((c == 4) & in_box).sum())
    return px, py, n_lens > 0, n_rod, n_rpe, n_lens, nearest_lens_px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--box", type=int, default=2400, help="inset 邊長 @L0 px（比 QC inset 的 1200 大不少，"
                     "因為要同時框進 lens/rod/rpe 三者；實測 rod-rpe 交界離最近 lens 細胞常有"
                     "數百 um，1800 在部分切片仍框不進 lens，2400（~554 um）四片都夠）")
    ap.add_argument("--delivery-ds", type=int, default=16,
                    help="交付版總覽的 downsample（必須是 2 的冪次且為 DS 的倍數）。"
                         "16 -> 1792x1830，客戶端再縮到 1568 只有 1.14 倍，"
                         "max-pool 的邊界撐得住；DS=4 的原圖 7170x7322 會被縮到 21%，線全沒。")
    ap.add_argument("--no-delivery", action="store_true",
                    help="只產既有輸出，不產交付版總覽與逐塊 inset。")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    out = Path(a.out)
    t0 = time.time()
    _use_compartment_palette()

    log.info("讀 compartment 標籤...")
    cls, cell_id, comp_name, section_of_label, sec_cats = load_compartment_cls()
    drawn_counts = {k: int((cls == k).sum()) for k in COMPARTMENT_CLASSES}
    n_drawn = sum(drawn_counts.values())
    n_not_drawn = N_LABELS - n_drawn
    for k in DRAW_ORDER:
        log.info(f"  {COMPARTMENT_CLASSES[k][0]:<12s} {drawn_counts[k]:7,}")
    log.info(f"  not drawn (no h5ad row) {n_not_drawn:7,}")
    assert n_drawn == 40524, f"預期 40,524 顆有 compartment，實際 {n_drawn}"

    log.info("mmap 讀 raw40x label 遮罩...")
    m = np.load(MASK_NPY, mmap_mode="r")
    log.info(f"  mask shape {m.shape} dtype {m.dtype}")

    log.info("第一趟掃描：label_stats（面積＋質心，全部 label）...")
    area, cx, cy = ovf.label_stats(m)
    log.info(f"  完成，{time.time()-t0:.0f}s")

    # ---- 全圖疊圖（level 2 / DS 4） ----
    log.info("讀 HE（level 2）...")
    he = ovf.read_he(DS)
    Hs, Ws = he.shape[:2]
    log.info(f"  HE {he.shape}")

    log.info("第二趟掃描：edge_maps（依 compartment 分類的邊界）...")
    masks = ovf.edge_maps(m, cls, (Hs, Ws))
    log.info(f"  完成，{time.time()-t0:.0f}s")

    ov = ovf.paint(he, masks, DRAW_ORDER)
    ovf.legend(ov, drawn_counts, DRAW_ORDER)
    full_path = out / "fullslide_compartment_overlay.png"
    cv2.imwrite(str(full_path), cv2.cvtColor(ov, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])
    log.info(f"寫出 {full_path.name}")

    # ---- 交付版總覽（DS = a.delivery_ds，預設 16）----
    # 為什麼要另外出一張：7170x7322 這張送進對話後會被客戶端降採樣到長邊 ~1568
    # （每軸只剩 21%），1 px 的細胞邊界整條消失，看起來就只是一張普通 H&E。
    # 直接在這裡出 1792x1830，客戶端只再縮 1.14 倍，max-pool 的邊界留得住。
    delivery_paths = {}
    if not a.no_delivery:
        ds_d = int(a.delivery_ds)
        if ds_d % DS != 0 or (ds_d & (ds_d - 1)) != 0:
            log.warning(f"--delivery-ds={ds_d} 不是 2 的冪次或非 DS={DS} 的倍數，跳過交付版總覽")
        else:
            k = ds_d // DS
            he_d = ovf.read_he(ds_d)
            Hd, Wd = he_d.shape[:2]
            masks_d = {kk: _fit_to(_maxpool_bool(v, k), Hd, Wd) for kk, v in masks.items()}
            ov_d = ovf.paint(he_d, masks_d, DRAW_ORDER)
            _legend_small(ov_d, drawn_counts, DRAW_ORDER)
            dpath = out / f"fullslide_compartment_overview_ds{ds_d}.png"
            cv2.imwrite(str(dpath), cv2.cvtColor(ov_d, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_PNG_COMPRESSION, 6])
            delivery_paths["overview"] = {
                "path": str(dpath), "downsample": ds_d, "size": [Wd, Hd],
                "size_mb": round(dpath.stat().st_size / 1048576, 2),
            }
            log.info(f"寫出 {dpath.name}（{Wd}x{Hd}，"
                     f"{delivery_paths['overview']['size_mb']} MB）")

    # ---- insets：四切片各一塊，找 rod/rpe 交界 ----
    log.info("挑 insets 位置（每片找 rod/rpe 密度交界）...")
    box = a.box
    r = PyramidSlideReader(NDPI)
    r.__enter__()
    panels = []
    inset_meta = {}
    for i, sec in enumerate(sec_cats):
        if sec == "unassigned":
            continue
        ids_here = cell_id[section_of_label[cell_id] == i]
        if ids_here.size == 0:
            log.warning(f"  {sec}: 沒有標到 compartment 的細胞，略過")
            continue
        px, py, lens_vis, n_rod, n_rpe, n_lens, nearest_lens_px = pick_section_inset(
            ids_here, cx, cy, cls, box)
        title = f"{sec}  rod/RPE/lens junction"
        panel = ovf.crop_panel(r, m, cls, px, py, box, title)
        panels.append(panel)
        if not a.no_delivery:
            # 逐塊各自存檔：現有 compartment_insets_40x.png 是 grid_of 把四塊拼成
            # 4824x5222 / 36.7 MB，整張超過 inline 上限而交付不進來。單塊 2400x2400
            # 約 9 MB，低於上限可直接內嵌，且降採樣到 1568 仍有 65%，輪廓看得見。
            ppath = out / f"compartment_inset_{sec}.png"
            cv2.imwrite(str(ppath), cv2.cvtColor(panel, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_PNG_COMPRESSION, 6])
            delivery_paths.setdefault("insets", {})[sec] = {
                "path": str(ppath), "size": [box, box],
                "size_mb": round(ppath.stat().st_size / 1048576, 2),
            }
            log.info(f"  寫出 {ppath.name}（{delivery_paths['insets'][sec]['size_mb']} MB）")
        inset_meta[sec] = {
            "origin_L0_px": [px, py], "box_L0_px": box,
            "n_rod_in_box": n_rod, "n_rpe_in_box": n_rpe, "n_lens_in_box": n_lens,
            "lens_visible": lens_vis,
            "nearest_lens_cell_um_from_junction_candidate": (
                round(nearest_lens_px * MPP, 1) if nearest_lens_px == nearest_lens_px else None),
        }
        log.info(f"  {sec}: origin=({px},{py}) rod={n_rod} rpe={n_rpe} lens={n_lens} "
                 f"lens_visible={lens_vis} nearest_lens={nearest_lens_px*MPP:.0f}um")
    r.close()

    grid = ovf.grid_of(panels, cols=2)
    # 在拼圖上方加一條白色圖例列，跟全圖疊圖用同一份配色/計數
    leg_h = 42 * len(DRAW_ORDER) + 76
    canvas = np.full((grid.shape[0] + leg_h, grid.shape[1], 3), 255, np.uint8)
    canvas[leg_h:] = grid
    ovf.legend(canvas, drawn_counts, DRAW_ORDER)
    insets_path = out / "compartment_insets_40x.png"
    cv2.imwrite(str(insets_path), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 6])
    log.info(f"寫出 {insets_path.name}")

    # ---- meta json ----
    meta = {
        "sample": SAMPLE,
        "ndpi": str(NDPI),
        "coordinate_system": {
            "note": "沿用 overlay_fullslide.py 的座標處理，未重新推導；"
                    "遮罩原點對應水平翻轉後 NDPI 的 (43992,6050)，"
                    "實際套用 X0,Y0=(43992,6052)、WR,HR=(28680,29288) 以整除 DS=4。",
            "origin_flipped_ndpi": [X0, Y0],
            "region_size_L0": [WR, HR],
            "mask_y_offset": MASK_Y_OFF,
            "mpp_um_per_px_L0": MPP,
            "full_overlay_level": int(np.log2(DS)),
            "full_overlay_downsample": DS,
            "full_overlay_out_size": [Ws, Hs],
        },
        "centroid_source": "overlay_fullslide.label_stats() 直接掃 segmentation_masks_raw40x.npy 算出，"
                            "region-local L0 px；h5ad 自身的 centroid_x_px/y_px 是 P0-0 記載的 VFR 粗遮罩座標"
                            "（4.6706 um/px），跟這張圖用的 raw40x label 遮罩不是同一個座標系，未使用。",
        "compartment_source": "cells_with_identity.h5ad obs['compartment']，經 cell_id 對回 "
                               "segmentation_masks_raw40x.npy 的 label id。",
        "n_labels_total": int(N_LABELS),
        "n_labels_with_compartment": int(n_drawn),
        "n_labels_not_drawn": int(n_not_drawn),
        "not_drawn_policy": "55,748 顆沒有 h5ad compartment 的 label 完全不畫（不是淡灰）："
                             "H&E 底圖本身已顯示核密度，多畫一層淡灰邊界只會跟既有 "
                             "fullslide_he_mask_overlay 系列重複，稀釋這張圖唯一要看的訊號。"
                             "13,675 顆 QC-pass 但 compartment=unassigned 的細胞畫中灰"
                             "（COMPARTMENT_CLASSES[2]），當作『有細胞但身分不明』的背景參照。",
        "colored_cell_counts": {COMPARTMENT_CLASSES[k][0]: drawn_counts[k] for k in DRAW_ORDER},
        "colors_rgb": {COMPARTMENT_CLASSES[k][0]: list(COMPARTMENT_CLASSES[k][1]) for k in DRAW_ORDER},
        "draw_order_bottom_to_top": [COMPARTMENT_CLASSES[k][0] for k in DRAW_ORDER],
        "files": {
            "fullslide_compartment_overlay.png": str(full_path),
            "compartment_insets_40x.png": str(insets_path),
        },
        "delivery_renders": {
            "why": "存檔版（上面 files）是 L0/DS=4 全解析度，超過 MCP 的 25 MB "
                   "inline 上限，且送進對話會被客戶端降採樣到長邊 ~1568 使細線消失。"
                   "這裡另外產一組專供交付：總覽降到 DS=16（邊界仍走 max-pool，"
                   "區室呈色帶）、inset 逐塊不拼圖（單塊即可內嵌）。",
            "path_note": "下方各項的 path 是【本次產出當下】的位置。一旦用 "
                         "register_artifact 登記，analysis/artifact_registry.py 對 base64 "
                         "超過 512,000 bytes（原檔約 384 KB）的檔案會複製到 "
                         "results/overflow/overflow_<artifact_id>.<ext> 並【刪除原檔】，"
                         "屆時這裡記的 path 會變成死路徑，overflow 路徑才是正式位置。"
                         "登記後請改以 artifact_id 取用，並把 artifact_id / canonical_path "
                         "回填到本區塊（腳本產出當下還拿不到 artifact_id）。",
            **delivery_paths,
        },
        "insets": {
            "box_L0_px": box, "box_um": round(box * MPP, 1),
            "selection_method": "兩階段：(1) 每片分別算 rod-only、rpe-only 顆數密度"
                                 "（overlay_fullslide.density()，16px grid，固定 900px 核 box-filter），"
                                 "取 min(rod,rpe) 贏過中位數的格點當『真的是交界』的候選集合；"
                                 "(2) 在候選集合裡挑離最近 lens 細胞距離最小的一格當 inset 中心。"
                                 "先驗過純取 min(rod,rpe) 最大值（不管 lens）的做法：四片的交界都離"
                                 "最近 lens 細胞 350-1300 um，box 開多大都框不進，所以改成這個兩階段版本。",
            "sections": inset_meta,
        },
    }
    meta_path = out / "compartment_overlay_meta.json"
    json.dump(meta, open(meta_path, "w"), indent=2)
    log.info(f"寫出 {meta_path.name}")
    log.info(f"完成，共 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
