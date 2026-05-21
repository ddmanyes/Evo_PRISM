"""MCseg 分割品質視覺化（analysis/mcseg_quality.py）測試。

無真實分割數據 → 用合成標籤遮罩驗證量化、邊界、繪圖與 ROI 探索。
generate_mcseg_qc_report 走真實 DUCKDB，僅測「無數據」graceful 路徑。
"""
from __future__ import annotations

import numpy as np
import pytest

from analysis import mcseg_quality as mq


def _synthetic_mask(n_cells: int = 4, size: int = 40) -> np.ndarray:
    """切成棋盤格區塊，每塊一個標籤（0=背景留邊）。"""
    mask = np.zeros((size, size), dtype=np.int32)
    per = size // (n_cells + 1)
    for i in range(n_cells):
        lo = (i + 1) * per - per // 2
        hi = lo + per // 2
        mask[lo:hi, lo:hi] = i + 1
    return mask


# ── 量化 ──────────────────────────────────────────────────────────────────────

def test_cell_metrics_counts():
    mask = _synthetic_mask(n_cells=4)
    m = mq.cell_metrics(mask)
    assert m["n_cells"] == 4
    assert m["mean_area"] > 0
    assert 0 < m["foreground_frac"] < 1


def test_cell_metrics_empty_mask():
    m = mq.cell_metrics(np.zeros((10, 10), dtype=np.int32))
    assert m["n_cells"] == 0
    assert m["mean_area"] == 0.0
    assert m["foreground_frac"] == 0.0


def test_cell_size_distribution_length():
    mask = _synthetic_mask(n_cells=5)
    areas = mq.cell_size_distribution(mask)
    assert areas.size == 5
    assert (areas > 0).all()


def test_boundaries_detects_edges():
    mask = _synthetic_mask(n_cells=3)
    bnd = mq._boundaries(mask)
    assert bnd.any()  # 有細胞 → 必有邊界
    assert bnd.shape == mask.shape


# ── 繪圖 ──────────────────────────────────────────────────────────────────────

def test_mask_overlay_writes_png(tmp_path):
    out = mq.mask_overlay_plot(_synthetic_mask(), tmp_path / "ov.png", title="t")
    assert out.exists() and out.stat().st_size > 0


def test_comparison_plot_writes_png(tmp_path):
    out = mq.comparison_plot(_synthetic_mask(3), _synthetic_mask(5),
                             tmp_path / "cmp.png", roi_name="ROI1")
    assert out.exists() and out.stat().st_size > 0


def test_size_distribution_plot_writes_png(tmp_path):
    masks = {"nuc": _synthetic_mask(4), "mcseg": _synthetic_mask(6)}
    out = mq.size_distribution_plot(masks, tmp_path / "dist.png")
    assert out.exists() and out.stat().st_size > 0


# ── ROI 探索 ──────────────────────────────────────────────────────────────────

def test_discover_roi_pairs(tmp_path):
    np.save(tmp_path / "roiA_nuc.npy", _synthetic_mask(3))
    np.save(tmp_path / "roiA_mcseg.npy", _synthetic_mask(4))
    np.save(tmp_path / "roiB_nuc.npy", _synthetic_mask(2))  # 無 mcseg 配對 → 略過
    pairs = mq.discover_roi_pairs(tmp_path)
    assert [p[0] for p in pairs] == ["roiA"]


# ── 報告 graceful 路徑 ────────────────────────────────────────────────────────

def test_report_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        mq.generate_mcseg_qc_report("test_sample", qc_dir=tmp_path / "nonexistent")


def test_report_no_pairs_raises(tmp_path):
    (tmp_path / "lonely_nuc.npy").write_bytes(b"")  # 無配對
    with pytest.raises(FileNotFoundError, match="成對"):
        mq.generate_mcseg_qc_report("test_sample", qc_dir=tmp_path)
