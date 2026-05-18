"""Tests for ENGRAM-Core (analysis/artifact_registry.py)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import duckdb
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def eng_con(tmp_path):
    """In-memory DuckDB with minimal schema for ENGRAM tests."""
    con = duckdb.connect(":memory:")

    con.execute("""
        CREATE TABLE sample_registry (
            sample_id VARCHAR PRIMARY KEY
        )
    """)
    con.execute("INSERT INTO sample_registry VALUES ('s1'), ('s2')")

    con.execute("""
        CREATE TABLE tools (
            tool_id   UUID PRIMARY KEY,
            tool_name VARCHAR,
            version   VARCHAR,
            status    VARCHAR DEFAULT 'active'
        )
    """)

    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id   UUID PRIMARY KEY,
            sample_id     VARCHAR REFERENCES sample_registry(sample_id),
            analysis_type VARCHAR,
            parameters    JSON,
            status        VARCHAR DEFAULT 'completed',
            result_path   VARCHAR,
            requested_by  VARCHAR,
            started_at    TIMESTAMPTZ DEFAULT now(),
            completed_at  TIMESTAMPTZ DEFAULT now(),
            summary       VARCHAR,
            tool_id       UUID REFERENCES tools(tool_id)
        )
    """)

    con.execute("""
        CREATE TABLE analysis_artifacts (
            artifact_id      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            analysis_id      UUID NOT NULL REFERENCES analysis_history(analysis_id),
            artifact_type    VARCHAR NOT NULL,
            artifact_subtype VARCHAR,
            label            VARCHAR NOT NULL,
            file_path        VARCHAR,
            inline_data      TEXT,
            file_size_kb     INTEGER,
            mime_type        VARCHAR,
            embedding        FLOAT[4],
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    yield con
    con.close()


def _insert_analysis(con, sample_id="s1", analysis_type="bulk_eda", params=None):
    aid = str(uuid.uuid4())
    con.execute(
        """INSERT INTO analysis_history
               (analysis_id, sample_id, analysis_type, parameters, status)
           VALUES (?, ?, ?, ?, 'completed')""",
        [aid, sample_id, analysis_type, json.dumps(params or {})],
    )
    return aid


# ---------------------------------------------------------------------------
# register_artifact
# ---------------------------------------------------------------------------

class TestRegisterArtifact:
    def test_returns_uuid(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact
        aid = _insert_analysis(eng_con)
        f = tmp_path / "pca.png"
        f.write_bytes(b"\x89PNG")

        art_id = register_artifact(eng_con, aid, f, "figure", "PCA 圖",
                                   artifact_subtype="pca")
        assert len(art_id) == 36

    def test_row_written(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact
        aid = _insert_analysis(eng_con)
        f = tmp_path / "volcano.png"
        f.write_bytes(b"PNG")

        register_artifact(eng_con, aid, f, "figure", "火山圖",
                          artifact_subtype="volcano")

        row = eng_con.execute(
            "SELECT artifact_type, artifact_subtype, label, file_path "
            "FROM analysis_artifacts WHERE analysis_id = ?", [aid]
        ).fetchone()
        assert row[0] == "figure"
        assert row[1] == "volcano"
        assert row[2] == "火山圖"
        assert row[3] == str(f)

    def test_inline_data_stored_for_small_file(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact
        aid = _insert_analysis(eng_con)
        f = tmp_path / "small.png"
        f.write_bytes(b"x" * 100)

        register_artifact(eng_con, aid, f, "figure", "小圖")

        row = eng_con.execute(
            "SELECT inline_data FROM analysis_artifacts WHERE analysis_id = ?", [aid]
        ).fetchone()
        assert row[0] is not None

    def test_missing_file_does_not_raise(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact
        aid = _insert_analysis(eng_con)
        missing = tmp_path / "ghost.png"

        art_id = register_artifact(eng_con, aid, missing, "figure", "幽靈圖")
        assert art_id

    def test_mime_type_inferred(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact
        aid = _insert_analysis(eng_con)
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2")

        register_artifact(eng_con, aid, f, "csv", "QC 表", artifact_subtype="qc_csv")

        mime = eng_con.execute(
            "SELECT mime_type FROM analysis_artifacts WHERE analysis_id = ?", [aid]
        ).fetchone()[0]
        assert mime == "text/csv"

    def test_file_size_kb_recorded(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact
        aid = _insert_analysis(eng_con)
        f = tmp_path / "report.md"
        f.write_text("# Report\n" * 100)

        register_artifact(eng_con, aid, f, "report", "報告")

        size = eng_con.execute(
            "SELECT file_size_kb FROM analysis_artifacts WHERE analysis_id = ?", [aid]
        ).fetchone()[0]
        assert size is not None and size >= 0


# ---------------------------------------------------------------------------
# get_artifacts
# ---------------------------------------------------------------------------

class TestGetArtifacts:
    def test_returns_all_artifacts(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, get_artifacts
        aid = _insert_analysis(eng_con)
        for name, subtype in [("pca.png", "pca"), ("volcano.png", "volcano")]:
            f = tmp_path / name
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", name, artifact_subtype=subtype)

        results = get_artifacts(eng_con, aid)
        assert len(results) == 2

    def test_filter_by_type(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, get_artifacts
        aid = _insert_analysis(eng_con)
        f_fig = tmp_path / "pca.png"
        f_fig.write_bytes(b"PNG")
        f_csv = tmp_path / "deg.csv"
        f_csv.write_text("gene,lfc")
        register_artifact(eng_con, aid, f_fig, "figure", "PCA")
        register_artifact(eng_con, aid, f_csv, "csv", "DEG list")

        results = get_artifacts(eng_con, aid, artifact_type="csv")
        assert len(results) == 1
        assert results[0]["artifact_type"] == "csv"

    def test_filter_by_subtype(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, get_artifacts
        aid = _insert_analysis(eng_con)
        for name, subtype in [("pca.png", "pca"), ("volcano.png", "volcano")]:
            f = tmp_path / name
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", name, artifact_subtype=subtype)

        results = get_artifacts(eng_con, aid, artifact_subtype="volcano")
        assert len(results) == 1
        assert results[0]["artifact_subtype"] == "volcano"

    def test_include_inline_false_omits_data(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, get_artifacts
        aid = _insert_analysis(eng_con)
        f = tmp_path / "fig.png"
        f.write_bytes(b"PNG data")
        register_artifact(eng_con, aid, f, "figure", "圖")

        results = get_artifacts(eng_con, aid, include_inline=False)
        assert results[0]["inline_data"] is None

    def test_empty_when_no_artifacts(self, eng_con):
        from analysis.artifact_registry import get_artifacts
        aid = _insert_analysis(eng_con)
        assert get_artifacts(eng_con, aid) == []


# ---------------------------------------------------------------------------
# compare_analyses
# ---------------------------------------------------------------------------

class TestCompareAnalyses:
    def test_groups_by_analysis_id(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, compare_analyses
        aid1 = _insert_analysis(eng_con)
        aid2 = _insert_analysis(eng_con)
        for aid in [aid1, aid2]:
            f = tmp_path / f"volcano_{aid[:8]}.png"
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", "火山圖",
                               artifact_subtype="volcano")

        result = compare_analyses(eng_con, [aid1, aid2])
        assert aid1 in result and aid2 in result
        assert len(result[aid1]) == 1
        assert len(result[aid2]) == 1

    def test_filter_by_subtype(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, compare_analyses
        aid1 = _insert_analysis(eng_con)
        for subtype in ["pca", "volcano"]:
            f = tmp_path / f"{subtype}.png"
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid1, f, "figure", subtype,
                               artifact_subtype=subtype)

        result = compare_analyses(eng_con, [aid1], artifact_subtype="pca")
        assert len(result[aid1]) == 1
        assert result[aid1][0]["artifact_subtype"] == "pca"

    def test_empty_list_returns_empty(self, eng_con):
        from analysis.artifact_registry import compare_analyses
        assert compare_analyses(eng_con, []) == {}

    def test_includes_tool_version(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, compare_analyses
        tool_id = str(uuid.uuid4())
        eng_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version) VALUES (?, 'test_tool', '1.0.0')",
            [tool_id],
        )
        aid = str(uuid.uuid4())
        eng_con.execute(
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, status, tool_id)
               VALUES (?, 's1', 'bulk_eda', 'completed', ?)""",
            [aid, tool_id],
        )
        f = tmp_path / "fig.png"
        f.write_bytes(b"PNG")
        register_artifact(eng_con, aid, f, "figure", "圖")

        result = compare_analyses(eng_con, [aid])
        assert result[aid][0]["tool_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# artifact_summary
# ---------------------------------------------------------------------------

class TestArtifactSummary:
    def test_empty_sample_returns_zeros(self, eng_con):
        from analysis.artifact_registry import artifact_summary
        result = artifact_summary(eng_con, "s1")
        assert result["total_runs"] == 0
        assert result["total_artifacts"] == 0
        assert result["latest_run"] is None

    def test_counts_runs_and_artifacts(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, artifact_summary
        for i in range(2):
            aid = _insert_analysis(eng_con)
            for j in range(3):
                f = tmp_path / f"fig_{i}_{j}.png"
                f.write_bytes(b"PNG")
                register_artifact(eng_con, aid, f, "figure", f"圖{j}",
                                   artifact_subtype="pca")

        result = artifact_summary(eng_con, "s1")
        assert result["total_runs"] == 2
        assert result["total_artifacts"] == 6

    def test_by_subtype_counts(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, artifact_summary
        aid = _insert_analysis(eng_con)
        for subtype in ["pca", "pca", "volcano"]:
            f = tmp_path / f"{subtype}_{uuid.uuid4().hex[:4]}.png"
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", subtype,
                               artifact_subtype=subtype)

        result = artifact_summary(eng_con, "s1")
        assert result["by_subtype"]["pca"] == 2
        assert result["by_subtype"]["volcano"] == 1

    def test_latest_run_is_most_recent(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, artifact_summary
        last_aid = None
        for i in range(3):
            aid = _insert_analysis(eng_con)
            f = tmp_path / f"fig_{i}.png"
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", "圖")
            last_aid = aid

        result = artifact_summary(eng_con, "s1")
        assert result["latest_run"]["analysis_id"] == last_aid


# ---------------------------------------------------------------------------
# search_artifacts (no embedding server — layer 1 only)
# ---------------------------------------------------------------------------

class TestSearchArtifacts:
    def test_exact_subtype_match_returns_results(self, eng_con, tmp_path, monkeypatch):
        from analysis.artifact_registry import register_artifact, search_artifacts
        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "volcano.png"
        f.write_bytes(b"PNG")
        register_artifact(eng_con, aid, f, "figure", "火山圖",
                           artifact_subtype="volcano")

        results = search_artifacts(eng_con, "差異表現圖", artifact_subtype="volcano")
        assert len(results) == 1
        assert results[0]["artifact_subtype"] == "volcano"
        assert results[0]["score"] == 1.0

    def test_no_match_returns_empty(self, eng_con, monkeypatch):
        from analysis.artifact_registry import search_artifacts
        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        results = search_artifacts(eng_con, "query", artifact_subtype="volcano")
        assert results == []

    def test_embedding_unavailable_returns_empty_for_hnsw(self, eng_con, monkeypatch):
        from analysis.artifact_registry import search_artifacts
        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        results = search_artifacts(eng_con, "some query")
        assert results == []

    def test_sample_id_filter(self, eng_con, tmp_path, monkeypatch):
        from analysis.artifact_registry import register_artifact, search_artifacts
        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid_s1 = _insert_analysis(eng_con, sample_id="s1")
        aid_s2 = _insert_analysis(eng_con, sample_id="s2")
        for aid, name in [(aid_s1, "v1.png"), (aid_s2, "v2.png")]:
            f = tmp_path / name
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", "火山圖",
                               artifact_subtype="volcano")

        results = search_artifacts(eng_con, "volcano",
                                   artifact_subtype="volcano", sample_id="s1")
        assert len(results) == 1
        assert results[0]["analysis_id"] == aid_s1
