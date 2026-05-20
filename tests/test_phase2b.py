"""
Tests for Phase 2B — analysis layer functions.

Uses an in-memory DuckDB with a small synthetic L2 Parquet fixture
so tests run fast without requiring the full 416 MB CRC dataset.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH, L2_ROOT  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_ID = "test_sample"


@pytest.fixture
def synthetic_l2(tmp_path) -> tuple[Path, Path, Path]:
    """
    Create a minimal L2 fixture:
      - obs_metadata.parquet  (10 bins in a 5x2 grid)
      - expression/part-0000.parquet (30 nonzero entries: 3 genes × 10 bins)

    Returns (obs_path, expr_dir, db_path).
    """
    silver_dir = tmp_path / "silver" / SAMPLE_ID
    expr_dir = silver_dir / "expression"
    expr_dir.mkdir(parents=True)

    # obs_metadata: 10 bins, 2 rows × 5 cols
    obs_rows = []
    for r in range(2):
        for c in range(5):
            obs_rows.append(
                {
                    "barcode": f"{r}x{c}",
                    "array_row_8um": r,
                    "array_col_8um": c,
                    "pxl_row_in_fullres": float(r * 100),
                    "pxl_col_in_fullres": float(c * 100),
                    "n_bins_2um": 16,
                    "spatial_x": float(c * 100),
                    "spatial_y": float(r * 100),
                }
            )
    obs_df = pd.DataFrame(obs_rows)
    obs_path = silver_dir / "obs_metadata.parquet"
    obs_df.to_parquet(obs_path, index=False)

    # expression: GENE_A expressed everywhere, GENE_B in first 5 bins only
    genes = []
    for r in range(2):
        for c in range(5):
            barcode = f"{r}x{c}"
            genes.append({"barcode": barcode, "gene_name": "GENE_A", "count": float(r + c + 1)})
            if r == 0:
                genes.append({"barcode": barcode, "gene_name": "GENE_B", "count": 2.0})
    expr_df = pd.DataFrame(genes)
    expr_path = expr_dir / "part-0000.parquet"
    expr_df.to_parquet(expr_path, index=False)

    # DuckDB with schema
    import importlib.util

    init_script = Path(__file__).parent.parent / "scripts" / "00_init_db.py"
    spec = importlib.util.spec_from_file_location("init_db_module", init_script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    mod.init_db(con)
    con.execute(
        """
        INSERT INTO sample_registry
            (sample_id, project, data_type, platform, species, tissue,
             l3_path, l2_ready, analysis_done, added_by)
        VALUES (?, 'test_project', 'visium_hd', '10x_visium_hd', 'human', 'colon',
                '/dev/null', TRUE, FALSE, 'pytest')
        """,
        [SAMPLE_ID],
    )
    con.execute("CHECKPOINT")
    con.close()

    return obs_path, expr_dir, db_path


# ── history_query tests ───────────────────────────────────────────────────────


class TestHistoryQuery:
    def test_analysis_index_empty(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import analysis_index

        df = analysis_index(db_path=db_path)
        assert isinstance(df, pd.DataFrame)

    def test_recent_analyses_empty(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import recent_analyses

        df = recent_analyses(n=5, db_path=db_path)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_sample_summary_found(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import sample_summary

        result = sample_summary(SAMPLE_ID, db_path=db_path)
        assert result["sample_info"]["sample_id"] == SAMPLE_ID
        assert isinstance(result["analysis_counts"], pd.DataFrame)

    def test_sample_summary_missing(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import sample_summary

        with pytest.raises(ValueError, match="not found"):
            sample_summary("nonexistent_sample", db_path=db_path)

    def test_find_by_type_empty(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import find_by_type

        df = find_by_type("qc_stats", db_path=db_path)
        assert isinstance(df, pd.DataFrame)

    def test_get_analysis_none(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import get_analysis

        result = get_analysis(str(uuid.uuid4()), db_path=db_path)
        assert result is None

    def test_search_summaries_no_match(self, synthetic_l2):
        _, _, db_path = synthetic_l2
        from analysis.history_query import search_summaries

        df = search_summaries("不存在的關鍵字", db_path=db_path)
        assert len(df) == 0


# ── report_generator tests ────────────────────────────────────────────────────


class TestReportGenerator:
    def _patch_l2_paths(self, monkeypatch, obs_path: Path, expr_dir: Path):
        """Redirect L2 path helpers to synthetic fixture paths."""
        import analysis.report_generator as rg

        monkeypatch.setattr(
            rg,
            "_l2_obs_path",
            lambda sid: str(obs_path),
        )
        monkeypatch.setattr(
            rg,
            "_l2_expr_glob",
            lambda sid: str(expr_dir / "*.parquet"),
        )

    def test_generate_eda_report_structure(self, synthetic_l2, monkeypatch):
        obs_path, expr_dir, db_path = synthetic_l2
        self._patch_l2_paths(monkeypatch, obs_path, expr_dir)

        from analysis.report_generator import generate_eda_report

        report, summary, stats = generate_eda_report(SAMPLE_ID, db_path=db_path)

        assert isinstance(report, str)
        assert SAMPLE_ID in report
        assert "## 1. 資料概覽" in report
        assert "## 3. 前 20 高表達基因" in report

    def test_generate_summary_length(self, synthetic_l2, monkeypatch):
        obs_path, expr_dir, db_path = synthetic_l2
        self._patch_l2_paths(monkeypatch, obs_path, expr_dir)

        from analysis.report_generator import generate_eda_report

        _, summary, _ = generate_eda_report(SAMPLE_ID, db_path=db_path)

        assert len(summary) <= 50, f"Summary too long: {len(summary)} chars — '{summary}'"
        assert SAMPLE_ID in summary

    def test_summary_contains_key_fields(self, synthetic_l2, monkeypatch):
        obs_path, expr_dir, db_path = synthetic_l2
        self._patch_l2_paths(monkeypatch, obs_path, expr_dir)

        from analysis.report_generator import generate_eda_report

        _, summary, stats = generate_eda_report(SAMPLE_ID, db_path=db_path)

        # summary must include sample_id and be non-empty
        assert SAMPLE_ID in summary
        assert len(summary) > 5
        # top gene appears in summary OR summary was truncated (small synthetic data)
        top_gene = stats["top_genes"]["gene_name"].iloc[0]
        assert top_gene in summary or len(summary) == 50

    def test_write_report_to_history(self, synthetic_l2, monkeypatch, tmp_path):
        obs_path, expr_dir, db_path = synthetic_l2
        self._patch_l2_paths(monkeypatch, obs_path, expr_dir)

        # Patch results dir to use tmp_path
        import analysis.report_generator as rg

        monkeypatch.setattr(rg, "_results_dir", lambda sid: tmp_path)

        from analysis.report_generator import write_report_to_history

        analysis_id, result_path = write_report_to_history(
            SAMPLE_ID,
            "# Test Report",
            "測試摘要：10bins，2基因。",
            db_path=db_path,
            save_file=True,
        )

        assert analysis_id  # non-empty UUID

        # Verify written to DB
        from analysis.history_query import get_analysis

        record = get_analysis(analysis_id, db_path=db_path)
        assert record is not None
        assert record["sample_id"] == SAMPLE_ID
        assert record["analysis_type"] == "eda_report"
        assert record["status"] == "completed"
        assert record["summary"] == "測試摘要：10bins，2基因。"

    def test_run_full_eda_report(self, synthetic_l2, monkeypatch, tmp_path):
        obs_path, expr_dir, db_path = synthetic_l2
        self._patch_l2_paths(monkeypatch, obs_path, expr_dir)

        import analysis.report_generator as rg

        monkeypatch.setattr(rg, "_results_dir", lambda sid: tmp_path)

        from analysis.report_generator import run_full_eda_report

        result = run_full_eda_report(SAMPLE_ID, db_path=db_path, save_file=True)

        assert "analysis_id" in result
        assert "summary" in result
        assert len(result["summary"]) <= 50
        assert result["stats"]["n_bins"] == 10
        assert result["stats"]["n_genes"] == 2


# ── spatial_eda smoke tests (requires real L2 data) ──────────────────────────


@pytest.mark.skipif(
    not (L2_ROOT / "crc_official_v4" / "obs_metadata.parquet").exists(),
    reason="CRC L2 silver data not available",
)
class TestSpatialEdaSmoke:
    """Smoke tests against real CRC L2 data — skipped if silver/ not present."""

    REAL_SAMPLE = "crc_official_v4"
    REAL_DB = DUCKDB_PATH

    def test_top_genes_returns_dataframe(self):
        from analysis.spatial_eda import top_genes

        df = top_genes(self.REAL_SAMPLE, n=10, db_path=self.REAL_DB)
        assert len(df) == 10
        assert "gene_name" in df.columns
        assert "total_counts" in df.columns

    def test_qc_stats_shape(self):
        from analysis.spatial_eda import qc_stats

        df = qc_stats(self.REAL_SAMPLE, save=False, db_path=self.REAL_DB)
        assert len(df) > 0
        assert "n_genes" in df.columns
        assert "total_counts" in df.columns
