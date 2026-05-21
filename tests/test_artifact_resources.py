"""MCP Resources：artifact 列舉 + 取回的單元測試。"""

import duckdb
import pytest

import analysis.artifact_resources as ar


@pytest.fixture
def con(tmp_path, monkeypatch):
    """建一個最小 analysis_artifacts 表 + 隔離的 BIO_DB_ROOT。"""
    # artifact 檔案放在 tmp_path（當作 BIO_DB_ROOT）
    monkeypatch.setattr(ar, "BIO_DB_ROOT", tmp_path)
    # resolve_artifact_path 以模組匯入的參照為準，需一併導向 tmp_path
    monkeypatch.setattr(ar, "resolve_artifact_path", lambda p: tmp_path / p)

    results = tmp_path / "results"
    results.mkdir()
    (results / "qc.csv").write_text("gene,count\nKRT5,12\nEPCAM,8\n", encoding="utf-8")
    (results / "feat.parquet").write_bytes(b"PAR1\x00binarydata\xff")

    db = tmp_path / "t.duckdb"
    c = duckdb.connect(str(db))
    c.execute(
        """
        CREATE TABLE analysis_artifacts (
            artifact_id UUID, analysis_id UUID, artifact_type VARCHAR,
            artifact_subtype VARCHAR, label VARCHAR, file_path VARCHAR,
            file_size_kb INTEGER, mime_type VARCHAR,
            created_at TIMESTAMP
        )
        """
    )
    c.execute(
        "INSERT INTO analysis_artifacts VALUES "
        "('11111111-1111-1111-1111-111111111111', gen_random_uuid(), 'table', "
        " 'qc', 'QC table', 'results/qc.csv', 1, 'text/csv', now()),"
        "('22222222-2222-2222-2222-222222222222', gen_random_uuid(), 'table', "
        " 'features', 'Feature matrix', 'results/feat.parquet', 1, "
        " 'application/octet-stream', now() - INTERVAL 1 DAY)"
    )
    yield c
    c.close()


def test_parse_uri_ok():
    assert (
        ar.parse_artifact_uri("artifact://11111111-1111-1111-1111-111111111111")
        == "11111111-1111-1111-1111-111111111111"
    )


def test_parse_uri_strips_trailing_slash():
    assert (
        ar.parse_artifact_uri("artifact://11111111-1111-1111-1111-111111111111/")
        == "11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.parametrize("bad", ["http://x", "artifact://not-a-uuid", "artifact://"])
def test_parse_uri_rejects_bad(bad):
    with pytest.raises(ar.ArtifactResourceError):
        ar.parse_artifact_uri(bad)


def test_list_resources_newest_first(con):
    items = ar.list_artifact_resources(con)
    assert [i["name"] for i in items] == ["QC table", "Feature matrix"]
    assert items[0]["uri"] == "artifact://11111111-1111-1111-1111-111111111111"
    assert items[0]["mime_type"] == "text/csv"


def test_read_text_artifact_returns_str(con):
    content, mime = ar.read_artifact_resource(
        con, "artifact://11111111-1111-1111-1111-111111111111"
    )
    assert isinstance(content, str)
    assert "KRT5,12" in content
    assert mime == "text/csv"


def test_read_binary_artifact_returns_bytes(con):
    content, mime = ar.read_artifact_resource(
        con, "artifact://22222222-2222-2222-2222-222222222222"
    )
    assert isinstance(content, bytes)
    assert content.startswith(b"PAR1")


def test_read_missing_artifact_raises(con):
    with pytest.raises(ar.ArtifactResourceError):
        ar.read_artifact_resource(con, "artifact://33333333-3333-3333-3333-333333333333")


def test_read_oversize_rejected(con, monkeypatch):
    monkeypatch.setattr(ar, "ARTIFACT_RESOURCE_MAX_MB", 0.0)  # 任何檔都超標
    with pytest.raises(ar.ArtifactResourceError, match="超過 inline 上限"):
        ar.read_artifact_resource(con, "artifact://11111111-1111-1111-1111-111111111111")


def test_get_handle_text_includes_preview_and_urls(con):
    h = ar.get_artifact_handle(con, "11111111-1111-1111-1111-111111111111", preview_lines=2)
    assert h["found"] is True
    assert h["label"] == "QC table"
    assert h["local_path"].endswith("results/qc.csv")
    assert h["web_url"].endswith("/api/engram/artifact/11111111-1111-1111-1111-111111111111/inline")
    assert "gene,count" in h["preview"]
    assert "EPCAM" not in h["preview"]  # 只取前 2 行


def test_get_handle_binary_has_no_preview(con):
    h = ar.get_artifact_handle(con, "22222222-2222-2222-2222-222222222222")
    assert h["found"] is True
    assert h["preview"] is None  # 二進位不預覽


def test_get_handle_missing_returns_not_found(con):
    h = ar.get_artifact_handle(con, "33333333-3333-3333-3333-333333333333")
    assert h == {"found": False, "artifact_id": "33333333-3333-3333-3333-333333333333"}


def test_get_handle_rejects_bad_id(con):
    with pytest.raises(ar.ArtifactResourceError):
        ar.get_artifact_handle(con, "not-a-uuid")


def test_read_path_escape_rejected(con, tmp_path):
    # 注入一筆 file_path 指向 BIO_DB_ROOT 之外
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    con.execute(
        "INSERT INTO analysis_artifacts VALUES "
        "('44444444-4444-4444-4444-444444444444', gen_random_uuid(), 'table', "
        " 'x', 'escape', ?, 1, 'text/csv', now())",
        [str(outside)],
    )
    # resolve_artifact_path 對絕對路徑會原樣回傳，落在 root 外 → 應拒絕
    with pytest.raises(ar.ArtifactResourceError, match="越界"):
        ar.read_artifact_resource(con, "artifact://44444444-4444-4444-4444-444444444444")
