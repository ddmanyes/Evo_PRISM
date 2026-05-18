"""
HELIX-Core unit tests — analysis/tool_registry.py

Covers:
  - register_tool: idempotent, hash-change deprecation, revision_count
  - get_active_tool_id: present / absent
  - check_tool_drift: no drift / drifted / unregistered
  - get_hot_tools: threshold filtering
  - prune_deprecated: stable keep=2, provenance guard
  - open_stabilization: happy path, duplicate-ongoing guard, tool-not-found
  - close_stabilization: outcome / closed_at
  - get_open_stabilizations: filter by tool_name
  - mark_stable / is_marked_stable
  - auto_revert_stale_stabilizations: recent vs old
  - tool_health_report: required keys, recommendation string
  - helix_self_health: required keys, empty-db zeros
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Schema fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def helix_con(tmp_path):
    """Fresh DuckDB with full HELIX schema — never touches real bio_memory.duckdb."""
    db = tmp_path / "helix_test.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE IF NOT EXISTS sample_registry (
            sample_id     VARCHAR PRIMARY KEY,
            project       VARCHAR, data_type VARCHAR, platform VARCHAR,
            species       VARCHAR, tissue VARCHAR, l3_path VARCHAR,
            l2_ready      BOOLEAN DEFAULT FALSE,
            analysis_done BOOLEAN DEFAULT FALSE,
            added_by VARCHAR, notes VARCHAR,
            last_updated  TIMESTAMP DEFAULT now()
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tools (
            tool_id        UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            tool_name      VARCHAR NOT NULL,
            version        VARCHAR NOT NULL,
            content_hash   VARCHAR(16) NOT NULL,
            module_path    VARCHAR NOT NULL,
            function_name  VARCHAR NOT NULL,
            description    VARCHAR,
            parameters     JSON,
            status         VARCHAR DEFAULT 'active',
            revision_count INTEGER DEFAULT 0,
            stability_note VARCHAR,
            created_at     TIMESTAMP DEFAULT now(),
            deprecated_at  TIMESTAMP,
            UNIQUE (tool_name, content_hash)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tool_change_log (
            log_id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            tool_name       VARCHAR NOT NULL,
            old_hash        VARCHAR(16),
            new_hash        VARCHAR(16) NOT NULL,
            new_tool_id     UUID REFERENCES tools(tool_id),
            revision_number INTEGER NOT NULL,
            change_reason   VARCHAR,
            changed_at      TIMESTAMP DEFAULT now()
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tool_stabilization_log (
            log_id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            tool_name         VARCHAR NOT NULL,
            trigger_revision  INTEGER NOT NULL,
            diagnosis         VARCHAR,
            action_taken      VARCHAR,
            outcome           VARCHAR,
            revision_before   INTEGER NOT NULL,
            revision_after    INTEGER,
            created_at        TIMESTAMP DEFAULT now(),
            closed_at         TIMESTAMP,
            complexity_before INTEGER,
            complexity_after  INTEGER,
            diagnosis_img     VARCHAR,
            after_img         VARCHAR,
            loc               INTEGER,
            halstead_volume   DOUBLE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            analysis_id   UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            sample_id     VARCHAR REFERENCES sample_registry(sample_id),
            analysis_type VARCHAR,
            parameters    JSON,
            status        VARCHAR DEFAULT 'running',
            result_path   VARCHAR,
            l1_cache_id   UUID,
            requested_by  VARCHAR,
            started_at    TIMESTAMP DEFAULT now(),
            completed_at  TIMESTAMP,
            summary       VARCHAR,
            tool_id       UUID REFERENCES tools(tool_id)
        )
    """)
    yield con
    con.close()


# ---------------------------------------------------------------------------
# Module-level tool stubs — inspect.getsource() works on these
# Each pair (v1/v2) has distinct source so their hashes differ.
# ---------------------------------------------------------------------------

def _reg_v1(): return 1          # noqa: E704
def _reg_v2(): return 2          # noqa: E704

def _drift_v1(): return 10       # noqa: E704
def _drift_v2(): return 20       # noqa: E704

def _hot_v1(): return 100        # noqa: E704
def _hot_v2(): return 200        # noqa: E704
def _hot_v3(): return 300        # noqa: E704

def _prune_v1(): return 1000     # noqa: E704
def _prune_v2(): return 2000     # noqa: E704
def _prune_v3(): return 3000     # noqa: E704
def _prune_v4(): return 4000     # noqa: E704
def _prune_v5(): return 5000     # noqa: E704

def _prov_v1(): return 9001      # noqa: E704
def _prov_v2(): return 9002      # noqa: E704

def _stab_v1(): return 111       # noqa: E704
def _stab_v2(): return 222       # noqa: E704
def _stab_v3(): return 333       # noqa: E704

def _other_v1(): return 11       # noqa: E704
def _other_v2(): return 22       # noqa: E704
def _other_v3(): return 33       # noqa: E704

def _ms_v1(): return 55          # noqa: E704

def _ar_new_v1(): return 61      # noqa: E704
def _ar_new_v2(): return 62      # noqa: E704
def _ar_new_v3(): return 63      # noqa: E704

def _ar_old_v1(): return 71      # noqa: E704
def _ar_old_v2(): return 72      # noqa: E704
def _ar_old_v3(): return 73      # noqa: E704


# ---------------------------------------------------------------------------
# register_tool
# ---------------------------------------------------------------------------

class TestRegisterTool:
    def test_first_registration_returns_uuid(self, helix_con):
        from analysis.tool_registry import register_tool
        tid = register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        assert len(tid) == 36

    def test_idempotent_same_hash(self, helix_con):
        from analysis.tool_registry import register_tool
        tid1 = register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        tid2 = register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        assert tid1 == tid2

    def test_new_hash_new_version(self, helix_con):
        from analysis.tool_registry import register_tool
        tid1 = register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        tid2 = register_tool(helix_con, "t", _reg_v2, "1.1.0", "d")
        assert tid1 != tid2

    def test_old_version_deprecated(self, helix_con):
        from analysis.tool_registry import register_tool
        tid1 = register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        register_tool(helix_con, "t", _reg_v2, "1.1.0", "d")
        row = helix_con.execute(
            "SELECT status FROM tools WHERE tool_id = ?", [tid1]
        ).fetchone()
        assert row[0] == "deprecated"

    def test_revision_count_increments(self, helix_con):
        from analysis.tool_registry import register_tool
        register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        register_tool(helix_con, "t", _reg_v2, "1.1.0", "d")
        row = helix_con.execute(
            "SELECT revision_count FROM tools WHERE tool_name='t' AND status='active'"
        ).fetchone()
        assert row[0] == 2

    def test_change_log_row_per_registration(self, helix_con):
        from analysis.tool_registry import register_tool
        register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        register_tool(helix_con, "t", _reg_v2, "1.1.0", "d")
        n = helix_con.execute(
            "SELECT count(*) FROM tool_change_log WHERE tool_name='t'"
        ).fetchone()[0]
        assert n == 2


# ---------------------------------------------------------------------------
# get_active_tool_id
# ---------------------------------------------------------------------------

class TestGetActiveToolId:
    def test_returns_id(self, helix_con):
        from analysis.tool_registry import register_tool, get_active_tool_id
        tid = register_tool(helix_con, "t", _reg_v1, "1.0.0", "d")
        assert get_active_tool_id(helix_con, "t") == tid

    def test_none_when_absent(self, helix_con):
        from analysis.tool_registry import get_active_tool_id
        assert get_active_tool_id(helix_con, "ghost") is None


# ---------------------------------------------------------------------------
# check_tool_drift
# ---------------------------------------------------------------------------

class TestCheckToolDrift:
    def test_no_drift(self, helix_con):
        from analysis.tool_registry import register_tool, check_tool_drift
        register_tool(helix_con, "t", _drift_v1, "1.0.0", "d")
        assert check_tool_drift(helix_con, "t", _drift_v1)["drifted"] is False

    def test_drift_detected(self, helix_con):
        from analysis.tool_registry import register_tool, check_tool_drift
        register_tool(helix_con, "t", _drift_v1, "1.0.0", "d")
        assert check_tool_drift(helix_con, "t", _drift_v2)["drifted"] is True

    def test_unregistered_no_stored_hash(self, helix_con):
        from analysis.tool_registry import check_tool_drift
        result = check_tool_drift(helix_con, "ghost", _drift_v1)
        assert result["stored_hash"] is None


# ---------------------------------------------------------------------------
# get_hot_tools
# ---------------------------------------------------------------------------

class TestGetHotTools:
    def test_below_threshold_excluded(self, helix_con):
        from analysis.tool_registry import register_tool, get_hot_tools
        register_tool(helix_con, "cold", _reg_v1, "1.0.0", "d")
        assert all(t["tool_name"] != "cold" for t in get_hot_tools(helix_con, min_revisions=3))

    def test_at_threshold_included(self, helix_con):
        from analysis.tool_registry import register_tool, get_hot_tools
        register_tool(helix_con, "hot3", _hot_v1, "1.0.0", "d")
        register_tool(helix_con, "hot3", _hot_v2, "1.1.0", "d")
        register_tool(helix_con, "hot3", _hot_v3, "1.2.0", "d")
        names = [t["tool_name"] for t in get_hot_tools(helix_con, min_revisions=3)]
        assert "hot3" in names


# ---------------------------------------------------------------------------
# prune_deprecated
# ---------------------------------------------------------------------------

class TestPruneDeprecated:
    def test_stable_keeps_2(self, helix_con):
        from analysis.tool_registry import register_tool, prune_deprecated
        for fn, ver in [(_prune_v1,"1.0"),(_prune_v2,"1.1"),(_prune_v3,"1.2"),
                        (_prune_v4,"1.3"),(_prune_v5,"1.4")]:
            register_tool(helix_con, "pt", fn, ver, "d")
        deleted = prune_deprecated(helix_con, "pt", keep_stable=2, hot_threshold=10)
        assert deleted == 2  # 4 deprecated - keep 2 = delete 2

    def test_provenance_guard(self, helix_con):
        from analysis.tool_registry import register_tool, prune_deprecated
        tid1 = register_tool(helix_con, "prt", _prov_v1, "1.0.0", "d")
        register_tool(helix_con, "prt", _prov_v2, "2.0.0", "d")
        helix_con.execute(
            "INSERT INTO sample_registry(sample_id,project,data_type,platform,species,tissue,l3_path) "
            "VALUES ('s1','p','bulk_rnaseq','kallisto','human','colon','/tmp')"
        )
        helix_con.execute(
            "INSERT INTO analysis_history(sample_id,analysis_type,status,tool_id) "
            "VALUES ('s1','eda','completed',?)", [tid1]
        )
        assert prune_deprecated(helix_con, "prt") == 0


# ---------------------------------------------------------------------------
# open_stabilization / close_stabilization
# ---------------------------------------------------------------------------

class TestStabilization:
    def _hot(self, con, name: str = "st"):
        from analysis.tool_registry import register_tool
        fns = [_stab_v1, _stab_v2, _stab_v3]
        for i, fn in enumerate(fns):
            register_tool(con, name, fn, f"1.{i}.0", "d")

    def test_open_returns_log_id(self, helix_con):
        from analysis.tool_registry import open_stabilization
        self._hot(helix_con)
        lid = open_stabilization(helix_con, "st", "diag", "action")
        assert len(lid) == 36

    def test_duplicate_ongoing_raises(self, helix_con):
        from analysis.tool_registry import open_stabilization
        self._hot(helix_con)
        open_stabilization(helix_con, "st", "diag", "action")
        with pytest.raises(ValueError, match="already has an open stabilization"):
            open_stabilization(helix_con, "st", "diag2", "action2")

    def test_tool_not_found_raises(self, helix_con):
        from analysis.tool_registry import open_stabilization
        with pytest.raises(ValueError, match="not found"):
            open_stabilization(helix_con, "ghost", "d", "a")

    def test_close_sets_outcome_and_closed_at(self, helix_con):
        from analysis.tool_registry import open_stabilization, close_stabilization
        self._hot(helix_con)
        lid = open_stabilization(helix_con, "st", "d", "a")
        close_stabilization(helix_con, lid, outcome="stabilized")
        row = helix_con.execute(
            "SELECT outcome, closed_at FROM tool_stabilization_log WHERE log_id=?", [lid]
        ).fetchone()
        assert row[0] == "stabilized"
        assert row[1] is not None

    def test_close_invalid_outcome_raises(self, helix_con):
        from analysis.tool_registry import open_stabilization, close_stabilization
        self._hot(helix_con)
        lid = open_stabilization(helix_con, "st", "d", "a")
        with pytest.raises(ValueError):
            close_stabilization(helix_con, lid, outcome="bad")

    def test_close_nonexistent_raises(self, helix_con):
        from analysis.tool_registry import close_stabilization
        with pytest.raises(ValueError, match="No stabilization log"):
            close_stabilization(helix_con, "00000000-0000-0000-0000-000000000000", "stabilized")

    def test_get_open_filter_by_name(self, helix_con):
        from analysis.tool_registry import open_stabilization, get_open_stabilizations, register_tool
        self._hot(helix_con, "st")
        for i, fn in enumerate([_other_v1, _other_v2, _other_v3]):
            register_tool(helix_con, "other", fn, f"2.{i}.0", "d")
        open_stabilization(helix_con, "st", "d", "a")
        open_stabilization(helix_con, "other", "d2", "a2")
        results = get_open_stabilizations(helix_con, tool_name="st")
        assert len(results) == 1 and results[0]["tool_name"] == "st"


# ---------------------------------------------------------------------------
# mark_stable / is_marked_stable
# ---------------------------------------------------------------------------

class TestMarkStable:
    def test_unmarked_by_default(self, helix_con):
        from analysis.tool_registry import register_tool, is_marked_stable
        register_tool(helix_con, "ms", _ms_v1, "1.0.0", "d")
        assert is_marked_stable(helix_con, "ms") is False

    def test_marked_after_call(self, helix_con):
        from analysis.tool_registry import register_tool, mark_stable, is_marked_stable
        register_tool(helix_con, "ms", _ms_v1, "1.0.0", "d")
        mark_stable(helix_con, "ms", "reason")
        assert is_marked_stable(helix_con, "ms") is True

    def test_sentinel_prefix(self, helix_con):
        from analysis.tool_registry import register_tool, mark_stable
        register_tool(helix_con, "ms", _ms_v1, "1.0.0", "d")
        mark_stable(helix_con, "ms", "完整測試")
        note = helix_con.execute(
            "SELECT stability_note FROM tools WHERE tool_name='ms' AND status='active'"
        ).fetchone()[0]
        assert note.startswith("[STABLE]")


# ---------------------------------------------------------------------------
# auto_revert_stale_stabilizations
# ---------------------------------------------------------------------------

class TestAutoRevert:
    def _hot_new(self, con):
        from analysis.tool_registry import register_tool
        for i, fn in enumerate([_ar_new_v1, _ar_new_v2, _ar_new_v3]):
            register_tool(con, "ar_new", fn, f"1.{i}.0", "d")

    def _hot_old(self, con):
        from analysis.tool_registry import register_tool
        for i, fn in enumerate([_ar_old_v1, _ar_old_v2, _ar_old_v3]):
            register_tool(con, "ar_old", fn, f"1.{i}.0", "d")

    def test_recent_not_reverted(self, helix_con):
        from analysis.tool_registry import open_stabilization, auto_revert_stale_stabilizations
        self._hot_new(helix_con)
        open_stabilization(helix_con, "ar_new", "d", "a")
        assert auto_revert_stale_stabilizations(helix_con, days=30) == []

    def test_old_iteration_reverted(self, helix_con):
        from analysis.tool_registry import open_stabilization, auto_revert_stale_stabilizations
        self._hot_old(helix_con)
        open_stabilization(helix_con, "ar_old", "d", "a")
        cutoff = datetime.now(timezone.utc) - timedelta(days=40)
        helix_con.execute(
            "UPDATE tool_stabilization_log SET created_at=? WHERE tool_name='ar_old'",
            [cutoff],
        )
        reverted = auto_revert_stale_stabilizations(helix_con, days=30)
        assert len(reverted) == 1
        row = helix_con.execute(
            "SELECT outcome FROM tool_stabilization_log WHERE tool_name='ar_old'"
        ).fetchone()
        assert row[0] == "reverted"


# ---------------------------------------------------------------------------
# tool_health_report
# ---------------------------------------------------------------------------

class TestToolHealthReport:
    def test_required_keys(self, helix_con):
        from analysis.tool_registry import tool_health_report
        report = tool_health_report(helix_con)
        for key in (
            "total_active", "total_deprecated", "hot_zones",
            "open_stabilizations", "stale_analyses", "prune_candidates",
            "regression_zones", "helix_self_health", "recommendation",
        ):
            assert key in report, f"missing key: {key}"

    def test_empty_db_healthy_recommendation(self, helix_con):
        from analysis.tool_registry import tool_health_report
        report = tool_health_report(helix_con)
        assert isinstance(report["recommendation"], str)
        assert "健康" in report["recommendation"]

    def test_counts_zero_on_empty_db(self, helix_con):
        from analysis.tool_registry import tool_health_report
        report = tool_health_report(helix_con)
        assert report["total_active"] == 0
        assert report["total_deprecated"] == 0


# ---------------------------------------------------------------------------
# helix_self_health
# ---------------------------------------------------------------------------

class TestHelixSelfHealth:
    def test_required_keys(self, helix_con):
        from analysis.tool_registry import helix_self_health
        h = helix_self_health(helix_con)
        for key in (
            "tools_table_rows", "stabilization_log_rows",
            "change_log_rows", "orphan_iterations", "downsample_coverage_pct",
        ):
            assert key in h, f"missing key: {key}"

    def test_empty_zeros(self, helix_con):
        from analysis.tool_registry import helix_self_health
        h = helix_self_health(helix_con)
        assert h["tools_table_rows"] == 0
        assert h["orphan_iterations"] == 0
        assert h["downsample_coverage_pct"] == 0.0
