"""SQL-7 regression — analysis_artifacts(analysis_id, artifact_subtype, label) UNIQUE。

防止同分析重複登記同 subtype + label 的 artifact，避免 ENGRAM 搜尋結果膨脹與
file_path 衝突。Migration v14 已建立 `uq_artifacts_run_subtype_label`；此測試
確保未來的 schema 改動不會悄悄移除該約束。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _setup_schema(tmp_path: Path) -> tuple[Path, str]:
    db = tmp_path / "bio_memory.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id  UUID PRIMARY KEY,
            sample_id    VARCHAR,
            analysis_type VARCHAR,
            status       VARCHAR DEFAULT 'completed',
            completed_at TIMESTAMPTZ
        )
    """)
    con.execute("""
        CREATE TABLE analysis_artifacts (
            artifact_id      UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
            analysis_id      UUID    NOT NULL REFERENCES analysis_history(analysis_id),
            artifact_type    VARCHAR NOT NULL,
            artifact_subtype VARCHAR,
            label            VARCHAR NOT NULL,
            file_path        VARCHAR,
            file_size_kb     INTEGER,
            mime_type        VARCHAR,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    con.execute(
        "CREATE UNIQUE INDEX uq_artifacts_run_subtype_label "
        "ON analysis_artifacts (analysis_id, artifact_subtype, label)"
    )
    analysis_id = con.execute(
        "INSERT INTO analysis_history (analysis_id, sample_id, analysis_type, completed_at) "
        "VALUES (gen_random_uuid(), 's1', 'spatial_eda', ?) RETURNING analysis_id",
        [datetime.now(timezone.utc)],
    ).fetchone()[0]
    con.close()
    return db, str(analysis_id)


class TestArtifactUniqueConstraint:
    def test_first_insert_succeeds(self, tmp_path):
        db, analysis_id = _setup_schema(tmp_path)
        with duckdb.connect(str(db)) as con:
            con.execute(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                "VALUES (?, 'figure', 'gene_spatial_map', 'PTPRC', '/p/ptprc.png')",
                [analysis_id],
            )
            row = con.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()
        assert row[0] == 1

    def test_duplicate_triple_rejected(self, tmp_path):
        db, analysis_id = _setup_schema(tmp_path)
        with duckdb.connect(str(db)) as con:
            con.execute(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                "VALUES (?, 'figure', 'gene_spatial_map', 'PTPRC', '/p/ptprc.png')",
                [analysis_id],
            )
            # 同 (analysis_id, subtype, label) 第二次必須被 UNIQUE 擋
            # DuckDB 錯誤訊息不含 index 名，比對 violation 關鍵字 + 三欄位即可
            with pytest.raises(
                duckdb.ConstraintException, match="Duplicate key.*gene_spatial_map.*PTPRC"
            ):
                con.execute(
                    "INSERT INTO analysis_artifacts "
                    "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                    "VALUES (?, 'figure', 'gene_spatial_map', 'PTPRC', '/p/ptprc_v2.png')",
                    [analysis_id],
                )

    def test_different_subtype_same_label_ok(self, tmp_path):
        db, analysis_id = _setup_schema(tmp_path)
        with duckdb.connect(str(db)) as con:
            con.execute(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                "VALUES (?, 'figure', 'gene_spatial_map', 'PTPRC', '/a.png')",
                [analysis_id],
            )
            # 不同 subtype，label 相同 — 應允許
            con.execute(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                "VALUES (?, 'csv', 'qc_stats', 'PTPRC', '/a.csv')",
                [analysis_id],
            )
            n = con.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()[0]
        assert n == 2

    def test_different_analysis_same_triple_ok(self, tmp_path):
        db, analysis_id_1 = _setup_schema(tmp_path)
        with duckdb.connect(str(db)) as con:
            analysis_id_2 = con.execute(
                "INSERT INTO analysis_history (analysis_id, sample_id, analysis_type, completed_at) "
                "VALUES (gen_random_uuid(), 's2', 'spatial_eda', ?) RETURNING analysis_id",
                [datetime.now(timezone.utc)],
            ).fetchone()[0]
            con.execute(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                "VALUES (?, 'figure', 'gene_spatial_map', 'PTPRC', '/a.png')",
                [analysis_id_1],
            )
            # 同 (subtype, label) 但不同 analysis — UNIQUE key 包含 analysis_id，應允許
            con.execute(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_type, artifact_subtype, label, file_path) "
                "VALUES (?, 'figure', 'gene_spatial_map', 'PTPRC', '/b.png')",
                [analysis_id_2],
            )
            n = con.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()[0]
        assert n == 2
