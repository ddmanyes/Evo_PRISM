"""Tests for P1-C Star Schema views (migration v19).

Verifies that v_analysis_throughput_by_sample_type and v_tool_stability_signal
DDL works against an in-memory schema mirroring the production tables, and
that aggregations / signal classification produce expected values.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest


def _load_v19_module():
    """Load scripts/20_migrate_schema_v19.py — filename starts with a digit
    so standard import syntax does not apply.
    """
    path = Path(__file__).resolve().parent.parent / "scripts" / "20_migrate_schema_v19.py"
    spec = importlib.util.spec_from_file_location("migrate_v19", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_v21_module():
    """Load scripts/22_migrate_schema_v21_mcp_metrics.py — filename starts with a digit."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "22_migrate_schema_v21_mcp_metrics.py"
    spec = importlib.util.spec_from_file_location("migrate_v21", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def star_con():
    """In-memory DuckDB with the minimal tables needed for star schema views."""
    con = duckdb.connect(":memory:")

    con.execute("""
        CREATE TABLE sample_registry (
            sample_id   VARCHAR PRIMARY KEY,
            data_type   VARCHAR,
            platform    VARCHAR
        )
    """)
    con.execute(
        "INSERT INTO sample_registry VALUES "
        "('s1', 'visium_hd',   '10x_visium_hd'), "
        "('s2', 'bulk_rnaseq', 'kallisto')"
    )

    con.execute("""
        CREATE TABLE tools (
            tool_id        UUID PRIMARY KEY,
            tool_name      VARCHAR,
            version        VARCHAR,
            status         VARCHAR DEFAULT 'active',
            revision_count INTEGER DEFAULT 0
        )
    """)

    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id   UUID PRIMARY KEY,
            sample_id     VARCHAR REFERENCES sample_registry(sample_id),
            analysis_type VARCHAR,
            status        VARCHAR DEFAULT 'completed',
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ
        )
    """)

    con.execute("""
        CREATE TABLE tool_change_log (
            log_id       UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
            tool_name    VARCHAR,
            changed_at   TIMESTAMPTZ,
            churn_ratio  DOUBLE
        )
    """)

    con.execute("""
        CREATE TABLE tool_stabilization_log (
            log_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tool_name         VARCHAR,
            created_at        TIMESTAMPTZ,
            closed_at         TIMESTAMPTZ,
            complexity_after  INTEGER
        )
    """)

    yield con
    con.close()


# ---------------------------------------------------------------------------
# v_analysis_throughput_by_sample_type
# ---------------------------------------------------------------------------

class TestThroughputView:
    def test_view_created_and_empty_table_yields_no_rows(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_throughput())
        row = star_con.execute(
            "SELECT 1 FROM information_schema.views "
            "WHERE table_name='v_analysis_throughput_by_sample_type'"
        ).fetchone()
        assert row is not None
        assert star_con.execute(
            "SELECT COUNT(*) FROM v_analysis_throughput_by_sample_type"
        ).fetchone()[0] == 0

    def test_aggregates_runs_per_sample_type_and_week(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_throughput())

        base = datetime(2026, 5, 11, 10, 0, 0, tzinfo=timezone.utc)
        for offset_days, sample in [(0, "s1"), (1, "s1"), (2, "s1"), (10, "s1")]:
            start = base + timedelta(days=offset_days)
            star_con.execute(
                "INSERT INTO analysis_history VALUES (?, ?, ?, 'completed', ?, ?)",
                [str(uuid.uuid4()), sample, "bulk_eda", start, start + timedelta(seconds=5)],
            )

        rows = star_con.execute(
            "SELECT week, n_runs, n_completed, avg_seconds "
            "FROM v_analysis_throughput_by_sample_type "
            "WHERE data_type = 'visium_hd' "
            "ORDER BY week"
        ).fetchall()
        assert len(rows) == 2
        n_runs_sorted = sorted([r[1] for r in rows])
        assert n_runs_sorted == [1, 3]
        assert all(abs(r[3] - 5.0) < 0.01 for r in rows)

    def test_status_buckets(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_throughput())

        now = datetime(2026, 5, 18, tzinfo=timezone.utc)
        for status in ["completed", "completed", "failed", "stale"]:
            star_con.execute(
                "INSERT INTO analysis_history VALUES (?, 's2', 'bulk_eda', ?, ?, ?)",
                [str(uuid.uuid4()), status, now, now + timedelta(seconds=1)],
            )

        row = star_con.execute(
            "SELECT n_runs, n_completed, n_failed, n_stale "
            "FROM v_analysis_throughput_by_sample_type "
            "WHERE data_type = 'bulk_rnaseq'"
        ).fetchone()
        assert row is not None
        assert row == (4, 2, 1, 1)

    def test_null_completed_at_excluded(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_throughput())
        star_con.execute(
            "INSERT INTO analysis_history VALUES (?, 's1', 'bulk_eda', 'running', ?, NULL)",
            [str(uuid.uuid4()), datetime(2026, 5, 18, tzinfo=timezone.utc)],
        )
        n = star_con.execute(
            "SELECT COUNT(*) FROM v_analysis_throughput_by_sample_type"
        ).fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# v_tool_stability_signal
# ---------------------------------------------------------------------------

class TestStabilitySignalView:
    def test_ok_signal_for_quiet_tool(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_stability_signal())
        star_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version, status, revision_count) "
            "VALUES (gen_random_uuid(), 'bio_quiet', '1.0.0', 'active', 1)"
        )
        row = star_con.execute(
            "SELECT signal, changes_30d, open_iterations "
            "FROM v_tool_stability_signal WHERE tool_name = 'bio_quiet'"
        ).fetchone()
        assert row == ("OK", 0, 0)

    def test_watch_signal_when_revision_high_but_no_recent_churn(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_stability_signal())
        star_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version, status, revision_count) "
            "VALUES (gen_random_uuid(), 'bio_watch', '1.0.0', 'active', 5)"
        )
        row = star_con.execute(
            "SELECT signal FROM v_tool_stability_signal WHERE tool_name = 'bio_watch'"
        ).fetchone()
        assert row == ("WATCH",)

    def test_hot_signal_when_revision_high_and_recent_churn(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_stability_signal())
        star_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version, status, revision_count) "
            "VALUES (gen_random_uuid(), 'bio_hot', '1.0.0', 'active', 4)"
        )
        for _ in range(3):
            star_con.execute(
                "INSERT INTO tool_change_log (tool_name, changed_at, churn_ratio) "
                "VALUES ('bio_hot', now() - INTERVAL 5 DAY, 0.4)"
            )
        row = star_con.execute(
            "SELECT signal, changes_30d FROM v_tool_stability_signal "
            "WHERE tool_name = 'bio_hot'"
        ).fetchone()
        assert row == ("HOT", 3)

    def test_in_progress_signal_when_open_iteration_exists(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_stability_signal())
        star_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version, status, revision_count) "
            "VALUES (gen_random_uuid(), 'bio_iter', '1.0.0', 'active', 1)"
        )
        star_con.execute(
            "INSERT INTO tool_stabilization_log (tool_name, created_at, closed_at) "
            "VALUES ('bio_iter', now() - INTERVAL 3 DAY, NULL)"
        )
        row = star_con.execute(
            "SELECT signal, open_iterations FROM v_tool_stability_signal "
            "WHERE tool_name = 'bio_iter'"
        ).fetchone()
        assert row == ("IN_PROGRESS", 1)

    def test_stale_iteration_signal_when_open_too_long(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_stability_signal())
        star_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version, status, revision_count) "
            "VALUES (gen_random_uuid(), 'bio_stale', '1.0.0', 'active', 1)"
        )
        star_con.execute(
            "INSERT INTO tool_stabilization_log (tool_name, created_at, closed_at) "
            "VALUES ('bio_stale', now() - INTERVAL 60 DAY, NULL)"
        )
        row = star_con.execute(
            "SELECT signal FROM v_tool_stability_signal "
            "WHERE tool_name = 'bio_stale'"
        ).fetchone()
        assert row == ("STALE_ITERATION",)

    def test_deprecated_tools_excluded(self, star_con):
        mod = _load_v19_module()
        star_con.execute(mod._ddl_stability_signal())
        star_con.execute(
            "INSERT INTO tools (tool_id, tool_name, version, status, revision_count) "
            "VALUES (gen_random_uuid(), 'bio_dep', '0.9.0', 'deprecated', 7)"
        )
        n = star_con.execute(
            "SELECT COUNT(*) FROM v_tool_stability_signal WHERE tool_name = 'bio_dep'"
        ).fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# v_tool_perf_30d
# ---------------------------------------------------------------------------

class TestToolPerfView:
    def test_view_created_and_empty_yields_no_rows(self, star_con):
        mod = _load_v21_module()
        star_con.execute(mod._ddl_mcp_metrics_table())
        star_con.execute(mod._ddl_tool_perf_view())
        
        row = star_con.execute(
            "SELECT 1 FROM information_schema.views "
            "WHERE table_name='v_tool_perf_30d'"
        ).fetchone()
        assert row is not None
        assert star_con.execute(
            "SELECT COUNT(*) FROM v_tool_perf_30d"
        ).fetchone()[0] == 0

    def test_aggregates_perf_metrics_correctly(self, star_con):
        mod = _load_v21_module()
        star_con.execute(mod._ddl_mcp_metrics_table())
        star_con.execute(mod._ddl_tool_perf_view())

        # 寫入測試數據：3 次呼叫
        # 1. ok, 100ms
        # 2. user_error, 200ms
        # 3. rate_limited, 300ms
        now = datetime.now(timezone.utc)
        star_con.execute(
            "INSERT INTO mcp_tool_metrics (tool_name, duration_ms, status, recorded_at) "
            "VALUES "
            "('bio_test_tool', 100, 'ok', ?), "
            "('bio_test_tool', 200, 'user_error', ?), "
            "('bio_test_tool', 300, 'rate_limited', ?)",
            [now, now, now]
        )

        row = star_con.execute(
            "SELECT tool_name, n_calls, avg_duration_ms, p95_duration_ms, error_rate, n_rate_limited "
            "FROM v_tool_perf_30d WHERE tool_name = 'bio_test_tool'"
        ).fetchone()

        assert row is not None
        assert row[0] == "bio_test_tool"
        assert row[1] == 3 # n_calls
        assert abs(row[2] - 200.0) < 0.01 # avg_duration_ms
        # p95: 100, 200, 300 之間的 95th quantile 預計會在 290 左右
        assert row[3] > 200.0 and row[3] <= 300.0
        # error_rate: status 不為 'ok' 的比例 = 2 / 3 * 100 = 66.67%
        assert abs(row[4] - 66.67) < 0.01
        assert row[5] == 1 # n_rate_limited

    def test_excludes_records_older_than_30_days(self, star_con):
        mod = _load_v21_module()
        star_con.execute(mod._ddl_mcp_metrics_table())
        star_con.execute(mod._ddl_tool_perf_view())

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=31)

        # 寫入一筆新的和一筆過期的
        star_con.execute(
            "INSERT INTO mcp_tool_metrics (tool_name, duration_ms, status, recorded_at) VALUES "
            "('bio_filtered', 100, 'ok', ?), "
            "('bio_filtered', 500, 'ok', ?)",
            [now, old]
        )

        row = star_con.execute(
            "SELECT n_calls, avg_duration_ms FROM v_tool_perf_30d "
            "WHERE tool_name = 'bio_filtered'"
        ).fetchone()

        assert row is not None
        assert row[0] == 1
        assert abs(row[1] - 100.0) < 0.01

