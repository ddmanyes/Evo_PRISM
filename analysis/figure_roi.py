"""
figure_roi — 對「大到交付不進來」的圖做原生解析度 ROI 切塊。

動機：超過 ARTIFACT_RESOURCE_MAX_MB 的圖無法 inline；但把整張縮小交付同樣沒用，
因為客戶端會再把長邊降到約 1568 px。對「H&E 連續底圖 + 1 px 標註細線」這類圖，
任何整張縮放都會讓線消失（實測：16 倍縮小後 max-pool 保留 40.1% 的邊界格子，
直接取樣只剩 0.2%）。

唯一有效的槓桿是【最終那張 ≤1568 px 畫面裡的每單位面積像素數】，所以要切塊：
以原生解析度取一小塊，該塊縮到 1568 時幾乎 1:1，線就留得住。

本模組刻意只做通用的事——它不懂解剖學，只看得到像素：
  - grid：把圖切成標號格（A1、B2…），讓呼叫端指名要哪一格
  - hotspot：用飽和度找「標註墨水」最密的格，H&E 粉紫與空白背景飽和度低，
    標註色（紅/綠/黃/棕）高，所以不需要知道標註的語意也能定位
懂語意的 ROI（「第 2 片的 RPE」）屬於產圖層，不該塞進這裡。
"""

from __future__ import annotations

import io
import string
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# 大圖是正常輸入，不是攻擊；關掉 decompression bomb 警告上限
Image.MAX_IMAGE_PIXELS = None

# 客戶端視覺管線的長邊上限。切塊邊長預設對齊它，讓該塊幾乎 1:1 呈現。
CLIENT_MAX_EDGE = 1568

# 飽和度門檻：H&E 粉紫約 0.1-0.2，標註色（紅/綠/黃/棕）約 0.8
_SAT_THRESHOLD = 0.45


class FigureROIError(ValueError):
    """ROI 參數錯誤或圖檔無法處理。"""


def _col_label(i: int) -> str:
    """0->A, 25->Z, 26->AA。格數可能超過 26，不能只用單字母。"""
    out = ""
    while True:
        out = string.ascii_uppercase[i % 26] + out
        i = i // 26 - 1
        if i < 0:
            return out


def grid_geometry(w: int, h: int, tile: int) -> dict:
    """回傳格子幾何。最後一排／最後一列可能不滿一格，照樣算一格。"""
    if tile <= 0:
        raise FigureROIError(f"tile 必須為正整數，收到 {tile}")
    cols = max(1, -(-w // tile))
    rows = max(1, -(-h // tile))
    return {
        "image_size": [w, h],
        "tile": tile,
        "cols": cols,
        "rows": rows,
        "n_tiles": cols * rows,
        "labels": [f"{_col_label(c)}{r + 1}" for r in range(rows) for c in range(cols)],
    }


def parse_tile_label(label: str, geo: dict) -> tuple[int, int]:
    """'C4' -> (col_index, row_index)，並檢查落在格子範圍內。"""
    s = str(label or "").strip().upper()
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    if i == 0 or i == len(s) or not s[i:].isdigit():
        raise FigureROIError(f"格子標號格式錯誤：{label!r}（預期像 A1、C4、AB12）")
    col = 0
    for ch in s[:i]:
        col = col * 26 + (ord(ch) - 64)
    col -= 1
    row = int(s[i:]) - 1
    if not (0 <= col < geo["cols"] and 0 <= row < geo["rows"]):
        raise FigureROIError(
            f"格子 {label!r} 超出範圍（此圖為 {geo['cols']} 欄 × {geo['rows']} 列）"
        )
    return col, row


def tile_box(col: int, row: int, geo: dict) -> tuple[int, int, int, int]:
    """格子索引 -> (x0, y0, x1, y1)，邊界格自動夾到圖邊。"""
    t = geo["tile"]
    w, h = geo["image_size"]
    x0, y0 = col * t, row * t
    return x0, y0, min(x0 + t, w), min(y0 + t, h)


def _saturation(arr: np.ndarray) -> np.ndarray:
    """(H,W,3) uint8 -> (H,W) float32 飽和度。全黑像素定義為 0，避免除以 0。"""
    a = arr[:, :, :3].astype(np.float32)
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    return np.divide(mx - mn, np.maximum(mx, 1.0))


def annotation_hotspots(arr: np.ndarray, geo: dict, top_n: int = 5, stride: int = 4) -> list[dict]:
    """依「標註墨水」密度排序格子。

    stride 取樣只為了省時間——這裡要的是哪一格值得看，不是精確計數，
    取樣造成的偏差對排序影響可忽略（跟 edge_maps 的情況不同：那裡取樣會
    讓邊界整條消失，這裡只是少數幾個像素的統計）。
    """
    sat = _saturation(arr[::stride, ::stride])
    ink = sat > _SAT_THRESHOLD
    t = max(1, geo["tile"] // stride)
    out = []
    for row in range(geo["rows"]):
        for col in range(geo["cols"]):
            blk = ink[row * t : (row + 1) * t, col * t : (col + 1) * t]
            if blk.size == 0:
                continue
            out.append(
                {
                    "tile": f"{_col_label(col)}{row + 1}",
                    "ink_fraction": round(float(blk.mean()), 4),
                    "box": list(tile_box(col, row, geo)),
                }
            )
    out.sort(key=lambda d: -d["ink_fraction"])
    return out[:top_n]


def render_overview(arr: np.ndarray, geo: dict, max_edge: int = 1400) -> bytes:
    """降採樣總覽 + 疊上標號格線，讓呼叫端能指名要哪一格。

    這張總覽【只用來導航】。它跟原圖一樣會失去細線，不能拿來判讀——
    要判讀請取原生解析度的格子。
    """
    im = Image.fromarray(arr[:, :, :3])
    w, h = im.size
    scale = min(1.0, max_edge / max(w, h))
    im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    t = geo["tile"] * scale
    for c in range(1, geo["cols"]):
        d.line([(c * t, 0), (c * t, im.size[1])], fill=(0, 120, 255), width=1)
    for r in range(1, geo["rows"]):
        d.line([(0, r * t), (im.size[0], r * t)], fill=(0, 120, 255), width=1)
    for r in range(geo["rows"]):
        for c in range(geo["cols"]):
            d.text((c * t + 3, r * t + 2), f"{_col_label(c)}{r + 1}", fill=(0, 90, 200))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def crop_png(arr: np.ndarray, box: tuple[int, int, int, int]) -> bytes:
    """原生解析度裁切，不做任何縮放——縮放正是本模組要避免的事。"""
    x0, y0, x1, y1 = box
    sub = arr[y0:y1, x0:x1, :3]
    if sub.size == 0:
        raise FigureROIError(f"裁切範圍為空：{box}")
    buf = io.BytesIO()
    Image.fromarray(sub).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def load_image(path: Path) -> np.ndarray:
    """讀進記憶體一次，後續 hotspot 與 crop 共用同一份，避免重複解碼大圖。"""
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))
