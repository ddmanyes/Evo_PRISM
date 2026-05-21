"""Tests for analysis/enrichment.py — ORA via gseapy.enrichr。

策略:
  - gseapy.enrichr 需網路,絕不在 CI 跑;monkeypatch 取代為 fake function。
  - gseapy.dotplot 也 monkeypatch,避免畫圖時實際呼叫 gseapy 內部。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from analysis.enrichment import (
    DEFAULT_LIBRARIES,
    run_enrichr_single,
    run_ora,
    split_deg_genes,
)


@pytest.fixture
def fake_deg_df() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "log2FC": rng.normal(0, 1.5, size=100),
            "qvalue": rng.uniform(0, 1, size=100),
        },
        index=[f"GENE_{i:04d}" for i in range(100)],
    )


@pytest.fixture
def fake_enrichr_res() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Term": [f"Term_{i}" for i in range(8)],
            "Overlap": ["5/20"] * 8,
            "P-value": [0.001 * (i + 1) for i in range(8)],
            "Adjusted P-value": [0.01 * (i + 1) for i in range(8)],
            "Genes": ["A;B;C"] * 8,
        }
    )


@pytest.fixture
def fake_gseapy(fake_enrichr_res):
    """Monkeypatch gseapy.enrichr + gseapy.dotplot 不打網路、不畫圖。"""
    import matplotlib.pyplot as _plt

    def _fake_enrichr(**kw):
        return SimpleNamespace(res2d=fake_enrichr_res.copy())

    def _fake_dotplot(res, **kw):
        fig, ax = _plt.subplots(figsize=(4, 3))
        ax.plot([0, 1], [0, 1])
        return ax

    fake = SimpleNamespace(
        enrichr=_fake_enrichr, dotplot=_fake_dotplot, plot=SimpleNamespace(dotplot=_fake_dotplot)
    )
    with patch.dict("sys.modules", {"gseapy": fake}):
        yield fake


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE sample_registry (sample_id VARCHAR PRIMARY KEY)")
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id UUID PRIMARY KEY, sample_id VARCHAR, analysis_type VARCHAR,
            parameters JSON, status VARCHAR, result_path VARCHAR, l1_cache_id UUID,
            requested_by VARCHAR, started_at TIMESTAMP, completed_at TIMESTAMP,
            summary VARCHAR, tool_id UUID
        )
    """)
    con.execute("INSERT INTO sample_registry VALUES ('test_sid')")
    con.close()
    monkeypatch.setattr("analysis.enrichment.DUCKDB_PATH", db_path)
    return db_path


# ── split_deg_genes ─────────────────────────────────────────────────────────


class TestSplitDegGenes:
    def test_splits_up_down(self, fake_deg_df):
        out = split_deg_genes(fake_deg_df, fc_threshold=0.5, pval_threshold=0.5)
        assert "up" in out and "down" in out
        assert all(isinstance(g, str) for g in out["up"] + out["down"])

    def test_no_intersection_with_up_down(self, fake_deg_df):
        out = split_deg_genes(fake_deg_df, fc_threshold=0.5, pval_threshold=0.5)
        assert not set(out["up"]) & set(out["down"])

    def test_raises_when_columns_missing(self):
        bad = pd.DataFrame({"x": [1]}, index=["g"])
        with pytest.raises(ValueError, match="缺少欄位"):
            split_deg_genes(bad)


# ── run_enrichr_single ─────────────────────────────────────────────────────


class TestRunEnrichrSingle:
    def test_returns_df(self, fake_gseapy, fake_enrichr_res):
        res = run_enrichr_single(["A", "B"], "GO_Biological_Process_2023")
        assert isinstance(res, pd.DataFrame) and len(res) == len(fake_enrichr_res)

    def test_empty_input_returns_empty(self, fake_gseapy):
        assert run_enrichr_single([], "GO_X").empty

    def test_validates_library_name(self):
        with pytest.raises(ValueError, match="無效"):
            run_enrichr_single(["A"], "bad library!")


# ── run_ora full flow ──────────────────────────────────────────────────────


class TestRunOra:
    def test_full_flow(self, tmp_path, fake_deg_df, fake_gseapy, isolated_db):
        deg_path = tmp_path / "DEG_treat_vs_ctrl.csv"
        fake_deg_df.to_csv(deg_path)

        import analysis.path_utils as pu

        with patch.object(pu, "BIO_DB_ROOT", tmp_path):
            aid, rpath = run_ora(
                "test_sid",
                deg_table_path=deg_path,
                libraries=("GO_BP",),
                fc_threshold=0.5,
                pval_threshold=0.5,
            )
        assert aid and Path(rpath).exists()
        con = duckdb.connect(str(isolated_db), read_only=True)
        status = con.execute(
            "SELECT status FROM analysis_history WHERE analysis_id=?", [aid]
        ).fetchone()[0]
        con.close()
        assert status == "completed"

    def test_rejects_missing_deg_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_ora("test_sid", deg_table_path=tmp_path / "nope.csv")

    def test_rejects_empty_libraries(self, tmp_path, fake_deg_df):
        deg = tmp_path / "d.csv"
        fake_deg_df.to_csv(deg)
        with pytest.raises(ValueError, match="不可為空"):
            run_ora("test_sid", deg_table_path=deg, libraries=())


def test_default_libraries_constant():
    assert isinstance(DEFAULT_LIBRARIES, tuple) and len(DEFAULT_LIBRARIES) >= 3
