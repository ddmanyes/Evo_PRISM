"""Tests for analysis/impact.py — blast-radius + confidence tier。

用 in-memory DuckDB 建小型圖（tools / analysis_history / analysis_artifacts），
驗證三種 impact 入口的邊推導與 confidence 分級。
"""
from __future__ import annotations

import duckdb
import pytest

from analysis.impact import (
    CONF_SAME_ANALYSIS,
    CONF_TOOL_ID_EXACT,
    CONF_TYPE_HEURISTIC,
    artifact_impact,
    compute_impact,
    render_impact_md,
    sample_impact,
    tool_impact,
)


@pytest.fixture
def graph_con():
    """小型影響圖：1 tool(2 版) + 4 analyses(混 tool_id) + artifacts。"""
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE tools (
            tool_id UUID PRIMARY KEY, tool_name VARCHAR, version VARCHAR, status VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id UUID PRIMARY KEY, sample_id VARCHAR, analysis_type VARCHAR,
            status VARCHAR, tool_id UUID, started_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE analysis_artifacts (
            artifact_id UUID PRIMARY KEY, analysis_id UUID, label VARCHAR
        )
    """)
    # 兩版工具
    con.execute("""
        INSERT INTO tools VALUES
          ('11111111-1111-1111-1111-111111111111','bio_run_bulk_eda','1.0.0','deprecated'),
          ('22222222-2222-2222-2222-222222222222','bio_run_bulk_eda','1.1.0','active')
    """)
    # 4 analyses：2 有 tool_id（精確）、1 同 type 無 tool_id（啟發式）、1 無關
    con.execute("""
        INSERT INTO analysis_history VALUES
          ('aaaaaaaa-0000-0000-0000-000000000001','S1','bulk_eda','completed',
           '22222222-2222-2222-2222-222222222222','2026-01-01'),
          ('aaaaaaaa-0000-0000-0000-000000000002','S2','bulk_eda','completed',
           '11111111-1111-1111-1111-111111111111','2026-01-02'),
          ('aaaaaaaa-0000-0000-0000-000000000003','S3','bulk_eda','stale',
           NULL,'2026-01-03'),
          ('aaaaaaaa-0000-0000-0000-000000000004','S4','dynamic_code','completed',
           NULL,'2026-01-04')
    """)
    # artifacts：analysis 1 有兩個、analysis 3 有一個
    con.execute("""
        INSERT INTO analysis_artifacts VALUES
          ('bbbbbbbb-0000-0000-0000-000000000001','aaaaaaaa-0000-0000-0000-000000000001','pca'),
          ('bbbbbbbb-0000-0000-0000-000000000002','aaaaaaaa-0000-0000-0000-000000000001','report'),
          ('bbbbbbbb-0000-0000-0000-000000000003','aaaaaaaa-0000-0000-0000-000000000003','qc')
    """)
    yield con
    con.close()


# ── tool_impact ─────────────────────────────────────────────────────────────

class TestToolImpact:
    def test_exact_and_heuristic_edges(self, graph_con):
        rep = tool_impact(graph_con, "bio_run_bulk_eda")
        # 2 exact（兩版各一）+ 1 heuristic（analysis 3）= 3
        assert rep.n_analyses == 3
        # 受影響分析應為 analysis 1/2/3，不含 dynamic_code 的 analysis 4
        ids = {a.analysis_id[-1] for a in rep.affected_analyses}  # 末碼 1/2/3
        assert ids == {"1", "2", "3"}

    def test_confidence_tiers(self, graph_con):
        rep = tool_impact(graph_con, "bio_run_bulk_eda")
        reasons = {a.reason for a in rep.affected_analyses}
        assert "tool_id-exact" in reasons
        assert "analysis_type-heuristic" in reasons
        exact = [a for a in rep.affected_analyses if a.reason == "tool_id-exact"]
        heur = [a for a in rep.affected_analyses if a.reason == "analysis_type-heuristic"]
        assert len(exact) == 2 and all(a.confidence == CONF_TOOL_ID_EXACT for a in exact)
        assert len(heur) == 1 and heur[0].confidence == CONF_TYPE_HEURISTIC

    def test_artifacts_expanded(self, graph_con):
        rep = tool_impact(graph_con, "bio_run_bulk_eda")
        # analysis 1 (2 artifacts) + analysis 3 (1 artifact) = 3；analysis 2 無 artifact
        assert rep.n_artifacts == 3

    def test_untracked_note_present(self, graph_con):
        rep = tool_impact(graph_con, "bio_run_bulk_eda")
        assert "啟發式" in rep.untracked_note

    def test_unknown_tool_empty(self, graph_con):
        rep = tool_impact(graph_con, "bio_nonexistent")
        assert rep.n_analyses == 0

    def test_rejects_bad_name(self, graph_con):
        with pytest.raises(ValueError, match="無效"):
            tool_impact(graph_con, "bad name!")


# ── sample_impact ───────────────────────────────────────────────────────────

class TestSampleImpact:
    def test_lists_sample_analyses(self, graph_con):
        rep = sample_impact(graph_con, "S1")
        assert rep.n_analyses == 1
        assert rep.affected_analyses[0].reason == "sample-direct"
        assert rep.affected_analyses[0].confidence == CONF_TOOL_ID_EXACT
        assert rep.n_artifacts == 2  # analysis 1 的兩個 artifact

    def test_sample_no_analyses(self, graph_con):
        rep = sample_impact(graph_con, "S_none")
        assert rep.n_analyses == 0


# ── artifact_impact ─────────────────────────────────────────────────────────

class TestArtifactImpact:
    def test_same_analysis_siblings(self, graph_con):
        # artifact 1 與 artifact 2 同屬 analysis 1
        rep = artifact_impact(graph_con, "bbbbbbbb-0000-0000-0000-000000000001")
        assert "bbbbbbbb-0000-0000-0000-000000000002" in rep.affected_artifact_ids
        assert rep.affected_analyses[0].reason == "same-analysis"
        assert rep.affected_analyses[0].confidence == CONF_SAME_ANALYSIS

    def test_unknown_artifact_empty(self, graph_con):
        rep = artifact_impact(graph_con, "cccccccc-0000-0000-0000-000000000099")
        assert rep.n_analyses == 0

    def test_rejects_bad_id(self, graph_con):
        with pytest.raises(ValueError, match="無效"):
            artifact_impact(graph_con, "not-a-valid-id-with-symbols!@#")


# ── compute_impact dispatch + render ────────────────────────────────────────

class TestComputeImpactDispatch:
    def test_requires_exactly_one_target(self, graph_con):
        with pytest.raises(ValueError, match="恰好一個"):
            compute_impact(con=graph_con)
        with pytest.raises(ValueError, match="恰好一個"):
            compute_impact(tool_name="x", sample_id="y", con=graph_con)

    def test_dispatches_tool(self, graph_con):
        rep = compute_impact(tool_name="bio_run_bulk_eda", con=graph_con)
        assert rep.target_kind == "tool"

    def test_dispatches_sample(self, graph_con):
        rep = compute_impact(sample_id="S1", con=graph_con)
        assert rep.target_kind == "sample"


class TestRenderImpactMd:
    def test_renders_table(self, graph_con):
        md = render_impact_md(tool_impact(graph_con, "bio_run_bulk_eda"))
        assert "影響分析" in md
        assert "confidence" in md
        assert "| 1.0 |" in md and "| 0.6 |" in md

    def test_empty_render(self, graph_con):
        md = render_impact_md(tool_impact(graph_con, "bio_nonexistent"))
        assert "無受影響分析" in md
