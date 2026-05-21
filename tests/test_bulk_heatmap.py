"""Tests for analysis/bulk_heatmap.py — DEG / top variable heatmaps。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from analysis.bulk_heatmap import (
    collect_sig_genes,
    deg_heatmap,
    run_bulk_heatmaps,
    top_var_heatmap,
)


@pytest.fixture
def synthetic_counts() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        rng.poisson(50, size=(80, 6)),
        index=[f"G{i:03d}" for i in range(80)],
        columns=[f"S{i}" for i in range(6)],
    )


@pytest.fixture
def fake_deg_table(tmp_path) -> Path:
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "log2FC": rng.normal(0, 2, size=80),
            "qvalue": rng.uniform(0, 1, size=80),
        },
        index=[f"G{i:03d}" for i in range(80)],
    )
    p = tmp_path / "DEG_a_vs_b.csv"
    df.to_csv(p)
    return p


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
    monkeypatch.setattr("analysis.bulk_heatmap.DUCKDB_PATH", db_path)
    return db_path


# ── deg_heatmap / top_var_heatmap pure plotting ─────────────────────────────


class TestDegHeatmap:
    def test_produces_png(self, tmp_path, synthetic_counts):
        sig = [f"G{i:03d}" for i in range(15)]
        out = tmp_path / "h.png"
        result = deg_heatmap(synthetic_counts, sig, output_path=out)
        assert result == out and out.exists() and out.stat().st_size > 1000

    def test_no_overlap_returns_none(self, tmp_path, synthetic_counts):
        assert (
            deg_heatmap(synthetic_counts, ["nope1", "nope2"], output_path=tmp_path / "h.png")
            is None
        )

    def test_normalize_false_still_runs(self, tmp_path, synthetic_counts):
        out = tmp_path / "h.png"
        deg_heatmap(synthetic_counts, ["G000", "G001", "G002"], output_path=out, normalize=False)
        assert out.exists()


class TestTopVarHeatmap:
    def test_produces_png(self, tmp_path, synthetic_counts):
        out = tmp_path / "v.png"
        result = top_var_heatmap(synthetic_counts, output_path=out, top_n=20)
        assert result == out and out.exists()

    def test_empty_returns_none(self, tmp_path):
        assert top_var_heatmap(pd.DataFrame(), output_path=tmp_path / "x.png") is None


# ── collect_sig_genes ──────────────────────────────────────────────────────


class TestCollectSigGenes:
    def test_unions_across_tables(self, tmp_path):
        for tag, seed in [("a", 1), ("b", 2)]:
            rng = np.random.default_rng(seed)
            pd.DataFrame(
                {
                    "log2FC": rng.normal(0, 2, 30),
                    "qvalue": rng.uniform(0, 0.1, 30),  # 全顯著
                },
                index=[f"{tag}_G{i}" for i in range(30)],
            ).to_csv(tmp_path / f"DEG_{tag}.csv")
        sig = collect_sig_genes(
            [tmp_path / "DEG_a.csv", tmp_path / "DEG_b.csv"],
            fc_threshold=0.0,
            pval_threshold=0.5,
        )
        # 同種子下大部分基因應顯著;至少跨兩表 union > 30
        assert len(sig) > 30

    def test_skips_missing_files(self, tmp_path):
        assert collect_sig_genes([tmp_path / "ghost.csv"]) == []


# ── run_bulk_heatmaps full flow ─────────────────────────────────────────────


class TestRunBulkHeatmaps:
    def test_full_flow(self, tmp_path, synthetic_counts, fake_deg_table, isolated_db):
        cp = tmp_path / "counts.csv"
        synthetic_counts.to_csv(cp)

        import analysis.path_utils as pu

        with patch.object(pu, "BIO_DB_ROOT", tmp_path):
            aid, rpath = run_bulk_heatmaps(
                "test_sid",
                counts_path=cp,
                deg_tables=[fake_deg_table],
                top_n=20,
                fc_threshold=0.0,
                pval_threshold=0.5,
            )

        assert aid and Path(rpath).exists()
        con = duckdb.connect(str(isolated_db), read_only=True)
        row = con.execute(
            "SELECT status, analysis_type FROM analysis_history WHERE analysis_id=?",
            [aid],
        ).fetchone()
        con.close()
        assert row == ("completed", "bulk_heatmap")
