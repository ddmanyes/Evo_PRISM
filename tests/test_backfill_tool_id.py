"""Tests for analysis.tool_registry.backfill_tool_id — 統一 tool_id 回填出口。

驗證 HELIX §7.3 的單一實作出口：
  - tools 表 + 已 register 的 active 工具 → 回填成功
  - 工具未 register（無 active 版本）→ 靜默 no-op
  - tools 表不存在（隔離 DB）→ 靜默 no-op，不 raise
  - analysis_id 為空 → no-op
並驗證直接呼叫 run_deg_analysis（不經 MCP）也會回填 tool_id。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd

from analysis.tool_registry import backfill_tool_id


def _make_history_db(con: duckdb.DuckDBPyConnection, *, with_tools: bool) -> None:
    con.execute("CREATE TABLE sample_registry (sample_id VARCHAR PRIMARY KEY)")
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id UUID PRIMARY KEY, sample_id VARCHAR, analysis_type VARCHAR,
            parameters JSON, status VARCHAR, result_path VARCHAR, l1_cache_id UUID,
            requested_by VARCHAR, started_at TIMESTAMP, completed_at TIMESTAMP,
            summary VARCHAR, tool_id UUID
        )
    """)
    con.execute("INSERT INTO sample_registry VALUES ('S1')")
    con.execute("""
        INSERT INTO analysis_history (analysis_id, sample_id, analysis_type, status)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001','S1','bulk_eda','completed')
    """)
    if with_tools:
        con.execute("""
            CREATE TABLE tools (
                tool_id UUID PRIMARY KEY, tool_name VARCHAR, version VARCHAR, status VARCHAR
            )
        """)


class TestBackfillToolId:
    def test_backfills_when_tool_active(self):
        con = duckdb.connect(":memory:")
        _make_history_db(con, with_tools=True)
        con.execute("""
            INSERT INTO tools VALUES
              ('11111111-1111-1111-1111-111111111111','bio_run_bulk_eda','1.0.0','active')
        """)
        ok = backfill_tool_id(con, "bio_run_bulk_eda", "aaaaaaaa-0000-0000-0000-000000000001")
        assert ok is True
        tid = con.execute(
            "SELECT tool_id FROM analysis_history WHERE analysis_id='aaaaaaaa-0000-0000-0000-000000000001'"
        ).fetchone()[0]
        assert str(tid) == "11111111-1111-1111-1111-111111111111"
        con.close()

    def test_noop_when_tool_not_registered(self):
        con = duckdb.connect(":memory:")
        _make_history_db(con, with_tools=True)  # tools 表存在但無此工具
        ok = backfill_tool_id(con, "bio_run_bulk_eda", "aaaaaaaa-0000-0000-0000-000000000001")
        assert ok is False
        con.close()

    def test_noop_when_tools_table_absent(self):
        con = duckdb.connect(":memory:")
        _make_history_db(con, with_tools=False)  # 無 tools 表
        # 不可 raise
        ok = backfill_tool_id(con, "bio_run_bulk_eda", "aaaaaaaa-0000-0000-0000-000000000001")
        assert ok is False
        con.close()

    def test_noop_when_analysis_id_empty(self):
        con = duckdb.connect(":memory:")
        _make_history_db(con, with_tools=True)
        assert backfill_tool_id(con, "bio_run_bulk_eda", None) is False
        assert backfill_tool_id(con, "bio_run_bulk_eda", "") is False
        con.close()

    def test_only_latest_active_used(self):
        """有 deprecated + active 兩版時，回填 active 版。"""
        con = duckdb.connect(":memory:")
        _make_history_db(con, with_tools=True)
        con.execute("""
            INSERT INTO tools VALUES
              ('11111111-1111-1111-1111-111111111111','bio_run_bulk_eda','1.0.0','deprecated'),
              ('22222222-2222-2222-2222-222222222222','bio_run_bulk_eda','1.1.0','active')
        """)
        backfill_tool_id(con, "bio_run_bulk_eda", "aaaaaaaa-0000-0000-0000-000000000001")
        tid = con.execute(
            "SELECT tool_id FROM analysis_history WHERE analysis_id='aaaaaaaa-0000-0000-0000-000000000001'"
        ).fetchone()[0]
        assert str(tid) == "22222222-2222-2222-2222-222222222222"
        con.close()


# ── 端對端：直接呼叫分析函數（不經 MCP）也回填 tool_id ──────────────────────


class TestDirectCallBackfills:
    def test_run_deg_analysis_backfills_tool_id(self, tmp_path, monkeypatch):
        """直接 import run_deg_analysis 呼叫（模擬 script/scheduler）也填 tool_id。"""
        # 隔離 DB，含 tools 表 + 已註冊 active 的 bio_run_deg
        db_path = tmp_path / "t.duckdb"
        con = duckdb.connect(str(db_path))
        _make_history_db(con, with_tools=True)
        con.execute("""
            INSERT INTO tools VALUES
              ('33333333-3333-3333-3333-333333333333','bio_run_deg','1.0.0','active')
        """)
        con.close()
        monkeypatch.setattr("analysis.bulk_deg.DUCKDB_PATH", db_path)

        # 合成輸入
        rng = np.random.default_rng(0)
        genes = [f"G{i:03d}" for i in range(50)]
        samples = [f"t_{i}" for i in range(3)] + [f"c_{i}" for i in range(3)]
        counts = pd.DataFrame(rng.poisson(50, (50, 6)), index=genes, columns=samples)
        coldata = pd.DataFrame({"group": ["t"] * 3 + ["c"] * 3}, index=samples)
        cp = tmp_path / "counts.csv"
        counts.to_csv(cp)
        dp = tmp_path / "coldata.tsv"
        coldata.to_csv(dp, sep="\t")

        # mock omicverse pyDEG（避免實跑 DESeq2）
        fake_deg = pd.DataFrame(
            {"log2FC": rng.normal(0, 1, 50), "qvalue": rng.uniform(0, 1, 50)},
            index=genes,
        )

        class _FakePyDEG:
            def __init__(self, c):
                pass

            def drop_duplicates_index(self):
                pass

            def deg_analysis(self, a, b, method="DEseq2", alpha=0.05):
                return fake_deg.copy()

            def foldchange_set(self, **k):
                pass

        fake_ov = SimpleNamespace(bulk=SimpleNamespace(pyDEG=_FakePyDEG))

        from analysis.bulk_deg import run_deg_analysis
        import analysis.path_utils as pu

        with (
            patch.dict("sys.modules", {"omicverse": fake_ov, "omicverse.bulk": fake_ov.bulk}),
            patch.object(pu, "BIO_DB_ROOT", tmp_path),
        ):
            aid, _ = run_deg_analysis(
                "S1",
                counts_path=cp,
                coldata_path=dp,
                comparisons=[("t", "c")],
            )

        # 直接呼叫即應回填 tool_id（不靠 MCP wrapper）
        con = duckdb.connect(str(db_path), read_only=True)
        tid = con.execute(
            "SELECT tool_id FROM analysis_history WHERE analysis_id=?", [aid]
        ).fetchone()[0]
        con.close()
        assert str(tid) == "33333333-3333-3333-3333-333333333333"
