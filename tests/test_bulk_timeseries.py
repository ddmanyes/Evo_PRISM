"""Bulk RNA-seq 時序模組（analysis/bulk_timeseries.py）測試。

欄名格式：{condition}_{replicate}_{tissue}，例 ctrl_1_HG → 0h、pw6hr_2_HG → 6h。

策略：
  - parse_timepoint_cols：解析 / 雜質跳過 / 組織過濾 / **數值排序**（24h 不可排在 6h 前）。
  - mean_by_timepoint：手算 replicate 均值 + 自動解析 + 缺欄跳過。
  - tpm_to_log2 / log2fc：log2(x+1) 精確值 + baseline 歸零 + 缺 baseline 報錯。
  - timeseries_summary：端到端、baseline 欄恆為 0、TSV round-trip、組織過濾。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis import bulk_timeseries as ts


# ── parse_timepoint_cols ──────────────────────────────────────────────────────


def test_parse_basic_grouping():
    cols = ["ctrl_1_HG", "ctrl_2_HG", "pw6hr_1_HG", "pw24hr_1_HG"]
    out = ts.parse_timepoint_cols(cols)
    assert out == {
        "0h": ["ctrl_1_HG", "ctrl_2_HG"],
        "6h": ["pw6hr_1_HG"],
        "24h": ["pw24hr_1_HG"],
    }


def test_parse_numeric_sort_not_lexicographic():
    """6h 必須排在 24h 前（數值序），而非字典序的 '24h' < '6h'。"""
    cols = ["pw24hr_1_HG", "pw6hr_1_HG", "ctrl_1_HG"]
    assert list(ts.parse_timepoint_cols(cols).keys()) == ["0h", "6h", "24h"]


def test_parse_skips_non_matching():
    cols = ["ctrl_1_HG", "gene_id", "random_column", "pw6hr_1_HG"]
    out = ts.parse_timepoint_cols(cols)
    assert set(out.keys()) == {"0h", "6h"}


def test_parse_tissue_filter():
    cols = ["ctrl_1_HG", "ctrl_1_lower_bulge", "pw6hr_1_HG", "pw6hr_1_lower_bulge"]
    out = ts.parse_timepoint_cols(cols, tissue="HG")
    assert out == {"0h": ["ctrl_1_HG"], "6h": ["pw6hr_1_HG"]}


# ── mean_by_timepoint ─────────────────────────────────────────────────────────


@pytest.fixture
def counts():
    """g1/g2 × 4 樣本（HG 組織，0h ×2 replicate、6h ×2 replicate）。"""
    return pd.DataFrame(
        {
            "ctrl_1_HG": [1, 10],
            "ctrl_2_HG": [3, 30],
            "pw6hr_1_HG": [7, 70],
            "pw6hr_2_HG": [9, 90],
        },
        index=["g1", "g2"],
    )


def test_mean_by_timepoint_auto_parse(counts):
    out = ts.mean_by_timepoint(counts)  # tp_map=None → 自動解析
    assert list(out.columns) == ["0h", "6h"]
    # 0h: g1=(1+3)/2=2, g2=(10+30)/2=20；6h: g1=(7+9)/2=8, g2=80
    np.testing.assert_allclose(out["0h"].values, [2, 20])
    np.testing.assert_allclose(out["6h"].values, [8, 80])


def test_mean_by_timepoint_skips_missing_cols(counts):
    """tp_map 指向不存在的欄 → 該時間點跳過，不崩潰。"""
    out = ts.mean_by_timepoint(counts, tp_map={"0h": ["ctrl_1_HG"], "ghost": ["nope"]})
    assert "0h" in out.columns
    assert "ghost" not in out.columns


# ── tpm_to_log2 / log2fc ──────────────────────────────────────────────────────


def test_tpm_to_log2_exact():
    df = pd.DataFrame({"a": [0, 1, 3, 7]}, index=list("wxyz"))
    out = ts.tpm_to_log2(df)  # log2(x+1)
    np.testing.assert_allclose(out["a"].values, [0, 1, 2, 3])


def test_log2fc_already_log_transformed():
    expr = pd.DataFrame({"0h": [1, 2], "6h": [3, 5]}, index=["g1", "g2"])
    fc = ts.log2fc(expr, baseline="0h", log_transformed=True)
    np.testing.assert_allclose(fc["0h"].values, [0, 0])  # baseline 恆 0
    np.testing.assert_allclose(fc["6h"].values, [2, 3])  # 3-1, 5-2


def test_log2fc_raw_applies_log_first():
    expr = pd.DataFrame({"0h": [1, 1], "6h": [3, 7]}, index=["g1", "g2"])
    fc = ts.log2fc(expr, baseline="0h", log_transformed=False)
    # log2(x+1): 0h→[1,1], 6h→[2,3]；fc 6h = [1,2]
    np.testing.assert_allclose(fc["0h"].values, [0, 0])
    np.testing.assert_allclose(fc["6h"].values, [1, 2])


def test_log2fc_missing_baseline_raises():
    expr = pd.DataFrame({"6h": [3, 5]}, index=["g1", "g2"])
    with pytest.raises(ValueError, match="baseline"):
        ts.log2fc(expr, baseline="0h", log_transformed=True)


# ── timeseries_summary（端到端）───────────────────────────────────────────────


def test_timeseries_summary_baseline_zero(counts):
    log2_mean, fc = ts.timeseries_summary(counts, baseline="0h")
    assert list(log2_mean.columns) == ["0h", "6h"]
    # log2_mean = log2(mean+1)：0h g1=log2(3), 6h g1=log2(9)
    np.testing.assert_allclose(log2_mean["0h"].values, np.log2([3, 21]))
    np.testing.assert_allclose(fc["0h"].values, [0, 0])
    # fc 6h = log2(9)-log2(3) 等
    np.testing.assert_allclose(fc["6h"].values, np.log2([9, 81]) - np.log2([3, 21]))


def test_timeseries_summary_tissue_filter(counts):
    extra = counts.copy()
    extra["ctrl_1_lower_bulge"] = [100, 100]
    log2_mean, _ = ts.timeseries_summary(extra, tissue="HG")
    # lower_bulge 欄被濾掉 → 結果只含 HG 推導的時間點
    assert list(log2_mean.columns) == ["0h", "6h"]


def test_timeseries_summary_writes_tsv(counts, tmp_path):
    out_dir = tmp_path / "out"
    log2_mean, fc = ts.timeseries_summary(counts, tissue="HG", output_dir=out_dir)
    mean_tsv = out_dir / "log2_mean_HG.tsv"
    fc_tsv = out_dir / "log2fc_HG.tsv"
    assert mean_tsv.exists() and fc_tsv.exists()
    back = pd.read_csv(fc_tsv, sep="\t", index_col=0)
    np.testing.assert_allclose(back["6h"].values, fc["6h"].values)
