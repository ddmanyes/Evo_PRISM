"""Tests for analysis/bulk_deg.py — DEG + volcano plot。

策略:
  - 真實跑 DESeq2 對 6 樣本 × 200 基因合成資料雖然可行,但匯入 omicverse 需 ~10s
    且結果有隨機性。改 monkeypatch ``omicverse.bulk.pyDEG`` 為輕量假類別,
    回傳固定 DEG DataFrame。
  - volcano_plot 對 fake DEG 直接畫圖驗證檔案產生。
  - run_deg_analysis 用 monkeypatch 隔離 DUCKDB_PATH 至 tmp,測試完整流程。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from analysis.bulk_deg import (
    deg_single_comparison,
    load_deg_inputs,
    run_deg_analysis,
    volcano_plot,
)


# ── 共用 fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def synthetic_counts() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    genes = [f"GENE_{i:04d}" for i in range(200)]
    samples = [f"treat_{i}" for i in range(3)] + [f"ctrl_{i}" for i in range(3)]
    data = rng.poisson(50, size=(200, 6)).astype(int)
    return pd.DataFrame(data, index=genes, columns=samples)


@pytest.fixture
def synthetic_coldata() -> pd.DataFrame:
    samples = [f"treat_{i}" for i in range(3)] + [f"ctrl_{i}" for i in range(3)]
    return pd.DataFrame({"group": ["treat"] * 3 + ["ctrl"] * 3}, index=samples)


@pytest.fixture
def fake_deg_df() -> pd.DataFrame:
    """模擬 omicverse pyDEG.deg_analysis 的輸出。"""
    genes = [f"GENE_{i:04d}" for i in range(200)]
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "log2FC": rng.normal(0, 1.5, size=200),
            "qvalue": rng.uniform(0, 1, size=200),
            "BaseMean": rng.uniform(10, 1000, size=200),
        },
        index=genes,
    )


@pytest.fixture
def fake_pydeg(fake_deg_df):
    """Monkeypatch omicverse.bulk.pyDEG to return fake_deg_df without running DESeq2."""

    class _FakePyDEG:
        def __init__(self, counts):
            self.raw_data = counts

        def drop_duplicates_index(self):
            pass

        def deg_analysis(self, a, b, method="DEseq2", alpha=0.05):
            return fake_deg_df.copy()

        def foldchange_set(self, **k):
            pass

    fake_ov = SimpleNamespace(bulk=SimpleNamespace(pyDEG=_FakePyDEG))
    with patch.dict("sys.modules", {"omicverse": fake_ov, "omicverse.bulk": fake_ov.bulk}):
        yield fake_ov


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """初始化 analysis_history schema 並 patch DUCKDB_PATH。"""
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE sample_registry (sample_id VARCHAR PRIMARY KEY)
    """)
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id   UUID PRIMARY KEY,
            sample_id     VARCHAR,
            analysis_type VARCHAR,
            parameters    JSON,
            status        VARCHAR,
            result_path   VARCHAR,
            l1_cache_id   UUID,
            requested_by  VARCHAR,
            started_at    TIMESTAMP,
            completed_at  TIMESTAMP,
            summary       VARCHAR,
            tool_id       UUID
        )
    """)
    con.execute("INSERT INTO sample_registry VALUES ('test_sid')")
    con.close()

    monkeypatch.setattr("analysis.bulk_deg.DUCKDB_PATH", db_path)
    return db_path


# ── load_deg_inputs ─────────────────────────────────────────────────────────


class TestLoadDegInputs:
    def test_loads_aligned(self, tmp_path, synthetic_counts, synthetic_coldata):
        cp = tmp_path / "counts.csv"
        synthetic_counts.to_csv(cp)
        dp = tmp_path / "coldata.tsv"
        synthetic_coldata.to_csv(dp, sep="\t")
        counts, coldata = load_deg_inputs(cp, dp)
        assert list(counts.columns) == list(coldata.index)
        assert "group" in coldata.columns

    def test_raises_when_coldata_missing_group(self, tmp_path, synthetic_counts):
        cp = tmp_path / "counts.csv"
        synthetic_counts.to_csv(cp)
        dp = tmp_path / "coldata.tsv"
        pd.DataFrame({"foo": ["a"] * 6}, index=synthetic_counts.columns).to_csv(dp, sep="\t")
        with pytest.raises(ValueError, match="group"):
            load_deg_inputs(cp, dp)

    def test_raises_when_no_overlap(self, tmp_path, synthetic_counts):
        cp = tmp_path / "counts.csv"
        synthetic_counts.to_csv(cp)
        dp = tmp_path / "coldata.tsv"
        pd.DataFrame({"group": ["a"]}, index=["unknown_sample"]).to_csv(dp, sep="\t")
        with pytest.raises(ValueError, match="重疊"):
            load_deg_inputs(cp, dp)


# ── deg_single_comparison ───────────────────────────────────────────────────


class TestDegSingleComparison:
    def test_calls_pydeg(self, synthetic_counts, synthetic_coldata, fake_pydeg, fake_deg_df):
        res = deg_single_comparison(synthetic_counts, synthetic_coldata, "treat", "ctrl")
        assert len(res) == len(fake_deg_df)
        assert "log2FC" in res.columns

    def test_raises_when_group_missing(self, synthetic_counts, synthetic_coldata, fake_pydeg):
        with pytest.raises(ValueError, match="找不到對照"):
            deg_single_comparison(synthetic_counts, synthetic_coldata, "treat", "absent_group")

    def test_rejects_bad_group_name(self, synthetic_counts, synthetic_coldata):
        with pytest.raises(ValueError, match="無效"):
            deg_single_comparison(synthetic_counts, synthetic_coldata, "bad group!", "ctrl")


# ── volcano_plot ─────────────────────────────────────────────────────────────


class TestVolcanoPlot:
    def test_produces_png(self, tmp_path, fake_deg_df):
        out = tmp_path / "volcano.png"
        result = volcano_plot(fake_deg_df, output_path=out, title="test")
        assert result == out and out.exists() and out.stat().st_size > 1000

    def test_raises_when_columns_missing(self, tmp_path):
        bad = pd.DataFrame({"foo": [1, 2]}, index=["a", "b"])
        with pytest.raises(ValueError, match="缺少欄位"):
            volcano_plot(bad, output_path=tmp_path / "x.png")


# ── run_deg_analysis ────────────────────────────────────────────────────────


class TestRunDegAnalysis:
    def test_full_flow(
        self, tmp_path, synthetic_counts, synthetic_coldata, fake_pydeg, isolated_db
    ):
        cp = tmp_path / "counts.csv"
        synthetic_counts.to_csv(cp)
        dp = tmp_path / "coldata.tsv"
        synthetic_coldata.to_csv(dp, sep="\t")

        # results_dir 會寫到 BIO_DB_ROOT/results/... — patch 至 tmp_path
        import analysis.path_utils as pu

        with patch.object(pu, "BIO_DB_ROOT", tmp_path):
            aid, rpath = run_deg_analysis(
                "test_sid",
                counts_path=cp,
                coldata_path=dp,
                comparisons=[("treat", "ctrl")],
            )

        assert aid and Path(rpath).exists()
        # Confirm DB row
        con = duckdb.connect(str(isolated_db), read_only=True)
        row = con.execute(
            "SELECT status, analysis_type FROM analysis_history WHERE analysis_id=?",
            [aid],
        ).fetchone()
        con.close()
        assert row == ("completed", "bulk_deg")

    def test_rejects_empty_comparisons(self, tmp_path, synthetic_counts, synthetic_coldata):
        cp = tmp_path / "counts.csv"
        synthetic_counts.to_csv(cp)
        dp = tmp_path / "coldata.tsv"
        synthetic_coldata.to_csv(dp, sep="\t")
        with pytest.raises(ValueError, match="不可為空"):
            run_deg_analysis("test_sid", counts_path=cp, coldata_path=dp, comparisons=[])

    def test_rejects_bad_sample_id(self):
        with pytest.raises(ValueError, match="無效"):
            run_deg_analysis(
                "bad sample!",
                counts_path=Path("x.csv"),
                coldata_path=Path("x.tsv"),
                comparisons=[("a", "b")],
            )
