"""Bulk RNA-seq EDA 模組（analysis/bulk_eda.py）測試。

聚焦 Phase 11.1 新增的「系列圖」：qc_barplot / correlation_heatmap / pca_plot /
_file_to_b64_md，外加既有純函數 qc_stats / top_genes / sample_correlation。

策略：用合成 count 矩陣，不碰真實 L3 數據或 DUCKDB——圖檔寫入 tmp_path。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis import bulk_eda as be


@pytest.fixture
def counts() -> pd.DataFrame:
    """6 樣本 × 50 基因合成 count 矩陣，兩組各 3 重複（組名為欄名前綴）。"""
    rng = np.random.default_rng(42)
    genes = [f"GENE{i}" for i in range(50)]
    samples = ["ctrl_1", "ctrl_2", "ctrl_3", "treat_1", "treat_2", "treat_3"]
    data = rng.poisson(lam=100, size=(50, 6))
    return pd.DataFrame(data, index=genes, columns=samples)


# ── 純函數 ────────────────────────────────────────────────────────────────────

def test_qc_stats_columns(counts):
    qc = be.qc_stats(counts)
    assert {"total_counts", "n_genes", "median_counts_per_gene"} <= set(qc.columns)
    assert list(qc.index) == sorted(counts.columns, key=lambda s: -counts[s].sum())
    assert (qc["n_genes"] <= counts.shape[0]).all()


def test_top_genes_sorted_desc(counts):
    top = be.top_genes(counts, n=10)
    assert len(top) == 10
    assert top["mean_counts"].is_monotonic_decreasing
    assert (top["present_in_n_samples"] <= counts.shape[1]).all()


def test_sample_correlation_square_and_unit_diagonal(counts):
    corr = be.sample_correlation(counts)
    assert corr.shape == (6, 6)
    assert np.allclose(np.diag(corr.values), 1.0)


# ── 系列圖（Phase 11.1 核心）────────────────────────────────────────────────────

def test_qc_barplot_writes_png(counts, tmp_path):
    out = tmp_path / "qc.png"
    result = be.qc_barplot(be.qc_stats(counts), output_path=out)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_correlation_heatmap_writes_png(counts, tmp_path):
    out = tmp_path / "corr.png"
    result = be.correlation_heatmap(be.sample_correlation(counts), output_path=out)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_pca_plot_writes_png(counts, tmp_path):
    pytest.importorskip("sklearn")
    out = tmp_path / "pca.png"
    result = be.pca_plot(counts, output_path=out, n_top_genes=40)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


# ── inline base64 helper ──────────────────────────────────────────────────────

def test_file_to_b64_md_is_data_uri(counts, tmp_path):
    out = be.qc_barplot(be.qc_stats(counts), output_path=tmp_path / "qc.png")
    md = be._file_to_b64_md(out, alt="QC barplot")
    assert md.strip().startswith("![QC barplot](data:image/png;base64,")
    assert md.strip().endswith(")")
    # base64 段非空且可解碼
    import base64
    b64 = md.split("base64,", 1)[1].rstrip(")\n")
    assert len(base64.b64decode(b64)) > 0
