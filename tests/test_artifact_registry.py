"""Tests for ENGRAM-Core (analysis/artifact_registry.py)."""

from __future__ import annotations

import json
import uuid

import duckdb
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def eng_con():
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
            tool_id        UUID PRIMARY KEY,
            tool_name      VARCHAR,
            version        VARCHAR,
            status         VARCHAR DEFAULT 'active',
            source_hash    VARCHAR,
            revision_count INTEGER DEFAULT 0
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
            file_size_kb     INTEGER,
            mime_type        VARCHAR,
            embedding        FLOAT[1024],
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # migration v14: blob table split
    con.execute("""
        CREATE TABLE analysis_artifact_blobs (
            artifact_id  UUID PRIMARY KEY
                         REFERENCES analysis_artifacts(artifact_id),
            inline_data  TEXT NOT NULL
        )
    """)

    # migration v15: search metrics table
    con.execute("""
        CREATE TABLE engram_search_metrics (
            metric_id    UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            query        VARCHAR NOT NULL,
            returned_n   INTEGER NOT NULL,
            latency_ms   INTEGER NOT NULL,
            search_layer VARCHAR NOT NULL,
            threshold    DOUBLE,
            sample_id    VARCHAR,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # migration v16: provenance columns + artifact_relations + lineage view
    con.execute("""
        ALTER TABLE analysis_artifacts
        ADD COLUMN input_data_hash VARCHAR
    """)
    con.execute("""
        ALTER TABLE analysis_artifacts
        ADD COLUMN code_hash VARCHAR
    """)
    con.execute("""
        ALTER TABLE analysis_artifacts
        ADD COLUMN env_hash VARCHAR
    """)

    # migration v17: Matryoshka 256-dim sub-vector
    con.execute("""
        ALTER TABLE analysis_artifacts
        ADD COLUMN embedding_256 FLOAT[256]
    """)

    con.execute("""
        CREATE TABLE artifact_relations (
            relation_id     UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            src_artifact_id UUID NOT NULL
                            REFERENCES analysis_artifacts(artifact_id),
            dst_artifact_id UUID NOT NULL
                            REFERENCES analysis_artifacts(artifact_id),
            relation_type   VARCHAR NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    con.execute(
        "CREATE UNIQUE INDEX uq_rel_src_dst_type "
        "ON artifact_relations (src_artifact_id, dst_artifact_id, relation_type)"
    )

    con.execute("""
        CREATE VIEW tool_artifact_lineage AS
        SELECT
            aa.artifact_id,
            aa.label,
            aa.artifact_subtype,
            aa.input_data_hash,
            aa.code_hash,
            aa.env_hash,
            ah.analysis_id,
            ah.analysis_type,
            ah.sample_id,
            ah.started_at,
            t.tool_id,
            t.tool_name,
            t.version         AS tool_version,
            t.source_hash     AS tool_source_hash,
            t.revision_count
        FROM   analysis_artifacts  aa
        JOIN   analysis_history    ah USING (analysis_id)
        LEFT JOIN tools            t  ON ah.tool_id = t.tool_id
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

        art_id = register_artifact(eng_con, aid, f, "figure", "PCA 圖", artifact_subtype="pca")
        assert len(art_id) == 36

    def test_row_written(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact

        aid = _insert_analysis(eng_con)
        f = tmp_path / "volcano.png"
        f.write_bytes(b"PNG")

        register_artifact(eng_con, aid, f, "figure", "火山圖", artifact_subtype="volcano")

        row = eng_con.execute(
            "SELECT artifact_type, artifact_subtype, label, file_path "
            "FROM analysis_artifacts WHERE analysis_id = ?",
            [aid],
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

        art_id = register_artifact(eng_con, aid, f, "figure", "小圖")

        # Since migration v14, inline_data lives in analysis_artifact_blobs
        row = eng_con.execute(
            "SELECT inline_data FROM analysis_artifact_blobs WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row is not None and row[0] is not None

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
            register_artifact(eng_con, aid, f, "figure", "火山圖", artifact_subtype="volcano")

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
            register_artifact(eng_con, aid1, f, "figure", subtype, artifact_subtype=subtype)

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
                register_artifact(eng_con, aid, f, "figure", f"圖{j}", artifact_subtype="pca")

        result = artifact_summary(eng_con, "s1")
        assert result["total_runs"] == 2
        assert result["total_artifacts"] == 6

    def test_by_subtype_counts(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, artifact_summary

        aid = _insert_analysis(eng_con)
        for subtype in ["pca", "pca", "volcano"]:
            f = tmp_path / f"{subtype}_{uuid.uuid4().hex[:4]}.png"
            f.write_bytes(b"PNG")
            register_artifact(eng_con, aid, f, "figure", subtype, artifact_subtype=subtype)

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
        register_artifact(eng_con, aid, f, "figure", "火山圖", artifact_subtype="volcano")

        results = search_artifacts(eng_con, "差異表現圖", artifact_subtype="volcano")
        assert len(results) == 1
        assert results[0]["artifact_subtype"] == "volcano"
        # RRF score: 1/(60+1) ≈ 0.0164 for single exact-layer hit
        assert results[0]["score"] > 0
        assert "search_layer" in results[0]

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
            register_artifact(eng_con, aid, f, "figure", "火山圖", artifact_subtype="volcano")

        results = search_artifacts(eng_con, "volcano", artifact_subtype="volcano", sample_id="s1")
        assert len(results) == 1
        assert results[0]["analysis_id"] == aid_s1


# ---------------------------------------------------------------------------
# Provenance & Lineage (9B)
# ---------------------------------------------------------------------------


class TestProvenanceHashes:
    def test_env_hash_written_on_register(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact

        aid = _insert_analysis(eng_con)
        f = tmp_path / "pca.png"
        f.write_bytes(b"PNG")

        art_id = register_artifact(eng_con, aid, f, "figure", "PCA")

        row = eng_con.execute(
            "SELECT env_hash FROM analysis_artifacts WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert len(row[0]) == 16  # SHA256[:16]

    def test_input_data_hash_from_paths(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact

        aid = _insert_analysis(eng_con)
        inp = tmp_path / "counts.parquet"
        inp.write_bytes(b"fake parquet")
        out = tmp_path / "pca.png"
        out.write_bytes(b"PNG")

        art_id = register_artifact(
            eng_con,
            aid,
            out,
            "figure",
            "PCA",
            input_paths=[inp],
        )

        row = eng_con.execute(
            "SELECT input_data_hash FROM analysis_artifacts WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row[0] is not None
        assert len(row[0]) == 16

    def test_code_hash_from_function(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact

        aid = _insert_analysis(eng_con)
        f = tmp_path / "out.csv"
        f.write_text("a,b\n1,2")

        def dummy_fn():
            pass

        art_id = register_artifact(
            eng_con,
            aid,
            f,
            "csv",
            "表",
            producing_fn=dummy_fn,
        )

        row = eng_con.execute(
            "SELECT code_hash FROM analysis_artifacts WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row[0] is not None
        assert len(row[0]) == 16

    def test_input_data_hash_none_when_not_provided(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact

        aid = _insert_analysis(eng_con)
        f = tmp_path / "fig.png"
        f.write_bytes(b"PNG")

        art_id = register_artifact(eng_con, aid, f, "figure", "圖")

        row = eng_con.execute(
            "SELECT input_data_hash FROM analysis_artifacts WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row[0] is None  # not provided → NULL

    def test_same_input_same_hash(self, tmp_path):
        from analysis.artifact_registry import _hash_input_data

        inp = tmp_path / "data.parquet"
        inp.write_bytes(b"x" * 1000)

        h1 = _hash_input_data([inp])
        h2 = _hash_input_data([inp])
        assert h1 == h2

    def test_different_input_different_hash(self, tmp_path):
        from analysis.artifact_registry import _hash_input_data

        inp1 = tmp_path / "a.parquet"
        inp1.write_bytes(b"aaa")
        inp2 = tmp_path / "b.parquet"
        inp2.write_bytes(b"bbb")

        assert _hash_input_data([inp1]) != _hash_input_data([inp2])


class TestLinkArtifacts:
    def test_link_creates_relation(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, link_artifacts

        aid = _insert_analysis(eng_con)
        f1 = tmp_path / "pca.png"
        f1.write_bytes(b"PNG")
        f2 = tmp_path / "volcano.png"
        f2.write_bytes(b"PNG")

        art1 = register_artifact(eng_con, aid, f1, "figure", "PCA", artifact_subtype="pca")
        art2 = register_artifact(eng_con, aid, f2, "figure", "火山圖", artifact_subtype="volcano")

        rel_id = link_artifacts(eng_con, art1, art2, "derived_from")
        assert rel_id is not None

        row = eng_con.execute(
            "SELECT relation_type FROM artifact_relations WHERE relation_id = ?",
            [rel_id],
        ).fetchone()
        assert row[0] == "derived_from"

    def test_invalid_relation_type_raises(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, link_artifacts

        aid = _insert_analysis(eng_con)
        f = tmp_path / "fig.png"
        f.write_bytes(b"PNG")
        art_id = register_artifact(eng_con, aid, f, "figure", "圖")

        with pytest.raises(ValueError):
            link_artifacts(eng_con, art_id, art_id, "invalid_type")

    def test_compared_with_relation(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, link_artifacts

        aid1 = _insert_analysis(eng_con, sample_id="s1")
        aid2 = _insert_analysis(eng_con, sample_id="s2")
        f1 = tmp_path / "fig1.png"
        f1.write_bytes(b"PNG")
        f2 = tmp_path / "fig2.png"
        f2.write_bytes(b"PNG")

        art1 = register_artifact(eng_con, aid1, f1, "figure", "圖1")
        art2 = register_artifact(eng_con, aid2, f2, "figure", "圖2")
        rel_id = link_artifacts(eng_con, art1, art2, "compared_with")
        assert rel_id is not None


class TestGetLineage:
    def test_upstream_returns_source(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, link_artifacts, get_lineage

        aid = _insert_analysis(eng_con)
        f_src = tmp_path / "counts.png"
        f_src.write_bytes(b"PNG")
        f_dst = tmp_path / "pca.png"
        f_dst.write_bytes(b"PNG")

        src_id = register_artifact(eng_con, aid, f_src, "csv", "counts")
        dst_id = register_artifact(eng_con, aid, f_dst, "figure", "PCA")
        link_artifacts(eng_con, src_id, dst_id, "derived_from")

        lineage = get_lineage(eng_con, dst_id, direction="upstream")
        assert len(lineage) == 1
        assert lineage[0]["artifact_id"] == src_id
        assert lineage[0]["relation_type"] == "derived_from"

    def test_downstream_returns_consumer(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, link_artifacts, get_lineage

        aid = _insert_analysis(eng_con)
        f_src = tmp_path / "deg.csv"
        f_src.write_text("gene,lfc\nCD8A,2.1")
        f_dst = tmp_path / "volcano.png"
        f_dst.write_bytes(b"PNG")

        src_id = register_artifact(eng_con, aid, f_src, "csv", "DEG")
        dst_id = register_artifact(eng_con, aid, f_dst, "figure", "火山圖")
        link_artifacts(eng_con, src_id, dst_id, "derived_from")

        lineage = get_lineage(eng_con, src_id, direction="downstream")
        assert len(lineage) == 1
        assert lineage[0]["artifact_id"] == dst_id

    def test_empty_when_no_relations(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, get_lineage

        aid = _insert_analysis(eng_con)
        f = tmp_path / "orphan.png"
        f.write_bytes(b"PNG")
        art_id = register_artifact(eng_con, aid, f, "figure", "孤兒圖")

        assert get_lineage(eng_con, art_id) == []

    def test_lineage_includes_provenance_hashes(self, eng_con, tmp_path):
        from analysis.artifact_registry import register_artifact, link_artifacts, get_lineage

        aid = _insert_analysis(eng_con)
        inp = tmp_path / "data.parquet"
        inp.write_bytes(b"fake")
        f_src = tmp_path / "src.png"
        f_src.write_bytes(b"PNG")
        f_dst = tmp_path / "dst.png"
        f_dst.write_bytes(b"PNG")

        src_id = register_artifact(
            eng_con,
            aid,
            f_src,
            "figure",
            "來源圖",
            input_paths=[inp],
        )
        dst_id = register_artifact(eng_con, aid, f_dst, "figure", "下游圖")
        link_artifacts(eng_con, src_id, dst_id, "derived_from")

        lineage = get_lineage(eng_con, dst_id, direction="upstream")
        assert len(lineage) == 1
        assert lineage[0]["input_data_hash"] is not None
        assert lineage[0]["env_hash"] is not None


# ---------------------------------------------------------------------------
# Matryoshka dual-layer embedding (9D)
# ---------------------------------------------------------------------------


class TestMatryoshkaEmbedding:
    def test_embedding_256_written_when_embedding_available(self, eng_con, tmp_path, monkeypatch):
        from analysis.artifact_registry import register_artifact

        fake_emb = [0.1] * 1024
        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: fake_emb)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "pca.png"
        f.write_bytes(b"PNG")

        art_id = register_artifact(eng_con, aid, f, "figure", "PCA")

        row = eng_con.execute(
            "SELECT embedding_256 FROM analysis_artifacts WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row is not None
        emb256 = row[0]
        assert emb256 is not None
        assert len(emb256) == 256
        # FLOAT[256] is 32-bit; compare with tolerance
        assert list(emb256) == pytest.approx(fake_emb[:256], rel=1e-5)

    def test_embedding_256_null_when_no_embedding(self, eng_con, tmp_path, monkeypatch):
        from analysis.artifact_registry import register_artifact

        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "fig.png"
        f.write_bytes(b"PNG")

        art_id = register_artifact(eng_con, aid, f, "figure", "圖")

        row = eng_con.execute(
            "SELECT embedding_256 FROM analysis_artifacts WHERE artifact_id = ?",
            [art_id],
        ).fetchone()
        assert row[0] is None

    def test_matryoshka_search_disabled_by_default(self, eng_con, tmp_path, monkeypatch):
        """With MATRYOSHKA_ENABLED=false (default), search uses standard 1024-dim path."""
        from analysis.artifact_registry import register_artifact, search_artifacts

        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)
        # Ensure env default is false
        monkeypatch.setattr("config.settings.MATRYOSHKA_ENABLED", False)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "volcano.png"
        f.write_bytes(b"PNG")
        register_artifact(eng_con, aid, f, "figure", "火山圖", artifact_subtype="volcano")

        # Should still return via exact layer (no embedding needed)
        results = search_artifacts(eng_con, "volcano", artifact_subtype="volcano")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# P0-B: FTS BM25 Layer 3 in search_artifacts()
# ---------------------------------------------------------------------------


class TestFtsLayer:
    """Layer 3 BM25 ranker — verifies 3-way RRF fusion (P0-B)."""

    @staticmethod
    def _create_fts_index(con):
        """Apply migration v18 equivalent on the in-memory fixture."""
        con.execute("INSTALL fts")
        con.execute("LOAD fts")
        con.execute(
            "PRAGMA create_fts_index("
            "'analysis_artifacts', 'artifact_id', "
            "'label', 'artifact_subtype', 'artifact_type', "
            "overwrite=1)"
        )

    def test_fts_availability_detection(self, eng_con):
        """_fts_artifacts_available returns False before FTS index, True after."""
        from analysis.artifact_registry import _fts_artifacts_available

        assert _fts_artifacts_available(eng_con) is False
        self._create_fts_index(eng_con)
        assert _fts_artifacts_available(eng_con) is True

    def test_fts_layer_returns_keyword_hit(self, eng_con, tmp_path, monkeypatch):
        """BM25 finds a label match even when embedding is unavailable."""
        from analysis.artifact_registry import register_artifact, search_artifacts

        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "pca.png"
        f.write_bytes(b"PNG")
        register_artifact(
            eng_con, aid, f, "figure", "PCA principal component plot", artifact_subtype="pca"
        )

        # Build FTS index AFTER registering artifacts (snapshot semantics).
        self._create_fts_index(eng_con)

        # Search by keyword that lives in label but not in artifact_subtype param —
        # would miss Layer 1; only FTS can rescue it.
        results = search_artifacts(eng_con, "principal", n=5, threshold=0.0)
        assert len(results) == 1
        assert results[0]["artifact_subtype"] == "pca"
        assert results[0]["search_layer"] == "fts"

    def test_rrf_combines_exact_and_fts(self, eng_con, tmp_path, monkeypatch):
        """Artifact hit by both exact-subtype and FTS keyword scores as 'rrf'."""
        from analysis.artifact_registry import register_artifact, search_artifacts

        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "volcano.png"
        f.write_bytes(b"PNG")
        register_artifact(
            eng_con, aid, f, "figure", "volcano differential plot", artifact_subtype="volcano"
        )
        self._create_fts_index(eng_con)

        results = search_artifacts(
            eng_con, "volcano", n=5, threshold=0.0, artifact_subtype="volcano"
        )
        assert len(results) == 1
        # Both layers contributed → search_layer should be "rrf"
        assert results[0]["search_layer"] == "rrf"
        # RRF score ≥ 2/(60+1) ≈ 0.0328
        assert results[0]["score"] >= 1 / 61

    def test_fts_silently_skipped_when_index_absent(self, eng_con, tmp_path, monkeypatch):
        """Without migration v18, Layer 3 is silently skipped; 2-layer flow works."""
        from analysis.artifact_registry import register_artifact, search_artifacts

        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid = _insert_analysis(eng_con)
        f = tmp_path / "v.png"
        f.write_bytes(b"PNG")
        register_artifact(eng_con, aid, f, "figure", "火山圖", artifact_subtype="volcano")
        # NOTE: no _create_fts_index() call.

        results = search_artifacts(eng_con, "火山圖", artifact_subtype="volcano")
        assert len(results) == 1
        assert results[0]["search_layer"] == "exact"

    def test_fts_layer_respects_sample_id_filter(self, eng_con, tmp_path, monkeypatch):
        """sample_id filter must apply to the FTS query path."""
        from analysis.artifact_registry import register_artifact, search_artifacts

        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda t: None)

        aid_s1 = _insert_analysis(eng_con, sample_id="s1")
        aid_s2 = _insert_analysis(eng_con, sample_id="s2")
        for aid, name in [(aid_s1, "p1.png"), (aid_s2, "p2.png")]:
            f = tmp_path / name
            f.write_bytes(b"PNG")
            register_artifact(
                eng_con, aid, f, "figure", "principal component plot", artifact_subtype="pca"
            )
        self._create_fts_index(eng_con)

        results = search_artifacts(eng_con, "principal", n=10, threshold=0.0, sample_id="s1")
        assert len(results) == 1
        assert results[0]["analysis_id"] == aid_s1
