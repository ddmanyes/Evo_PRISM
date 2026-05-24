"""Tests for PM4 HELIX Revert-on-Regression Guard.

Covers:
  1. compute_version_success_rate() — correct rate, None on insufficient data
  2. check_and_revert_regressions() — no action when new version is fine
  3. check_and_revert_regressions() — reverts when regression exceeds tau
  4. check_and_revert_regressions() — respects min_runs threshold
  5. check_and_revert_regressions() — logs revert to tool_change_log
  6. check_and_revert_regressions() — no prev version → skip gracefully
  7. check_and_revert_regressions() — custom tau override
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import duckdb
import pytest

from analysis.tool_registry import compute_version_success_rate


# ── DB bootstrap helpers ──────────────────────────────────────────────────────

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bootstrap(con: duckdb.DuckDBPyConnection) -> None:
    """Minimal schema: tools + analysis_history + tool_change_log."""
    con.execute("""
        CREATE TABLE tools (
            tool_id       UUID PRIMARY KEY,
            tool_name     VARCHAR NOT NULL,
            version       VARCHAR NOT NULL,
            content_hash  VARCHAR(16) DEFAULT 'deadbeef00000000',
            module_path   VARCHAR DEFAULT 'analysis.test',
            function_name VARCHAR DEFAULT 'fn',
            status        VARCHAR DEFAULT 'active',
            created_at    TIMESTAMP DEFAULT now(),
            deprecated_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id  UUID PRIMARY KEY,
            sample_id    VARCHAR,
            analysis_type VARCHAR,
            status       VARCHAR,
            tool_id      UUID
        )
    """)
    con.execute("""
        CREATE TABLE tool_change_log (
            log_id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            tool_name       VARCHAR,
            old_hash        VARCHAR,
            new_hash        VARCHAR,
            revision_number INTEGER,
            change_reason   VARCHAR,
            changed_at      TIMESTAMP
        )
    """)


def _register(con, tool_name: str, version: str, status: str = "active") -> str:
    tid = _uuid()
    con.execute(
        "INSERT INTO tools (tool_id, tool_name, version, status) VALUES (?, ?, ?, ?)",
        [tid, tool_name, version, status],
    )
    return tid


def _add_runs(con, tool_id: str, completed: int, failed: int) -> None:
    for _ in range(completed):
        con.execute(
            "INSERT INTO analysis_history (analysis_id, status, tool_id) VALUES (?, 'completed', ?)",
            [_uuid(), tool_id],
        )
    for _ in range(failed):
        con.execute(
            "INSERT INTO analysis_history (analysis_id, status, tool_id) VALUES (?, 'failed', ?)",
            [_uuid(), tool_id],
        )


# ── compute_version_success_rate tests ───────────────────────────────────────

class TestComputeVersionSuccessRate:

    def test_correct_rate(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        tid = _register(con, "bio_test", "1.0.0")
        _add_runs(con, tid, completed=8, failed=2)  # 80%
        rate = compute_version_success_rate(con, tid, min_runs=3)
        assert rate == pytest.approx(0.80)

    def test_perfect_rate(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        tid = _register(con, "bio_test", "1.0.0")
        _add_runs(con, tid, completed=5, failed=0)
        rate = compute_version_success_rate(con, tid, min_runs=3)
        assert rate == pytest.approx(1.0)

    def test_zero_rate(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        tid = _register(con, "bio_test", "1.0.0")
        _add_runs(con, tid, completed=0, failed=5)
        rate = compute_version_success_rate(con, tid, min_runs=3)
        assert rate == pytest.approx(0.0)

    def test_none_when_insufficient_runs(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        tid = _register(con, "bio_test", "1.0.0")
        _add_runs(con, tid, completed=2, failed=0)  # only 2 runs, min_runs=3
        rate = compute_version_success_rate(con, tid, min_runs=3)
        assert rate is None

    def test_none_when_no_runs(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        tid = _register(con, "bio_test", "1.0.0")
        rate = compute_version_success_rate(con, tid, min_runs=1)
        assert rate is None


# ── check_and_revert_regressions tests ───────────────────────────────────────

class TestCheckAndRevertRegressions:

    def _run(self, con, **kwargs):
        """Patch DUCKDB_PATH so the function uses our in-memory con."""
        from unittest.mock import MagicMock
        import analysis.code_promoter as cp

        original_connect = duckdb.connect

        def _fake_connect(path, **kw):
            return con

        with patch.object(duckdb, "connect", side_effect=_fake_connect):
            return cp.check_and_revert_regressions(**kwargs)

    def test_no_revert_when_improved(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        prev_id = _register(con, "bio_eda", "1.0.0", status="deprecated")
        new_id  = _register(con, "bio_eda", "2.0.0", status="active")
        _add_runs(con, prev_id, completed=7, failed=3)   # 70%
        _add_runs(con, new_id,  completed=9, failed=1)   # 90% — improvement
        result = self._run(con, min_runs=3, tau=0.10)
        assert result == []

    def test_no_revert_within_tau(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        prev_id = _register(con, "bio_eda", "1.0.0", status="deprecated")
        new_id  = _register(con, "bio_eda", "2.0.0", status="active")
        _add_runs(con, prev_id, completed=8, failed=2)   # 80%
        _add_runs(con, new_id,  completed=7, failed=3)   # 70% — Δ=−0.10, tau=0.10: border
        result = self._run(con, min_runs=3, tau=0.10)
        assert result == []  # not < -tau, exactly equal

    def test_reverts_on_regression(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        prev_id = _register(con, "bio_eda", "1.0.0", status="deprecated")
        new_id  = _register(con, "bio_eda", "2.0.0", status="active")
        _add_runs(con, prev_id, completed=9, failed=1)   # 90%
        _add_runs(con, new_id,  completed=5, failed=5)   # 50% — Δ=−0.40 >> tau=0.10
        result = self._run(con, min_runs=3, tau=0.10)
        assert len(result) == 1
        r = result[0]
        assert r["tool_name"] == "bio_eda"
        assert r["delta"] == pytest.approx(-0.40, abs=0.01)
        # DB: new version should now be deprecated, prev re-activated
        active_row = con.execute(
            "SELECT version FROM tools WHERE tool_name='bio_eda' AND status='active'"
        ).fetchone()
        assert active_row is not None
        assert active_row[0] == "1.0.0"

    def test_revert_logs_to_tool_change_log(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        prev_id = _register(con, "bio_deg", "1.0.0", status="deprecated")
        new_id  = _register(con, "bio_deg", "2.0.0", status="active")
        _add_runs(con, prev_id, completed=9, failed=1)   # 90%
        _add_runs(con, new_id,  completed=4, failed=6)   # 40%
        self._run(con, min_runs=3, tau=0.10)
        log_row = con.execute(
            "SELECT change_reason FROM tool_change_log WHERE tool_name='bio_deg'"
        ).fetchone()
        assert log_row is not None
        assert "[AUTO-REVERT]" in log_row[0]

    def test_skips_when_insufficient_new_data(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        prev_id = _register(con, "bio_eda", "1.0.0", status="deprecated")
        new_id  = _register(con, "bio_eda", "2.0.0", status="active")
        _add_runs(con, prev_id, completed=8, failed=2)   # 80%
        _add_runs(con, new_id,  completed=1, failed=1)   # only 2 runs — skip
        result = self._run(con, min_runs=3, tau=0.10)
        assert result == []

    def test_skips_when_no_previous_version(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        new_id = _register(con, "bio_new_tool", "1.0.0", status="active")
        _add_runs(con, new_id, completed=3, failed=7)   # 30% — bad, but no prev to compare
        result = self._run(con, min_runs=3, tau=0.10)
        assert result == []

    def test_custom_tau_override(self):
        con = duckdb.connect(":memory:")
        _bootstrap(con)
        prev_id = _register(con, "bio_eda", "1.0.0", status="deprecated")
        new_id  = _register(con, "bio_eda", "2.0.0", status="active")
        _add_runs(con, prev_id, completed=8, failed=2)   # 80%
        _add_runs(con, new_id,  completed=7, failed=3)   # 70% — Δ=−0.10
        # With tau=0.05: should revert (delta < -0.05)
        result_strict = self._run(con, min_runs=3, tau=0.05)
        assert len(result_strict) == 1
