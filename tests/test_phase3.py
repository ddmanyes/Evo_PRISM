"""
Tests for Phase 3 — L1 semantic cache infrastructure.

Tests run on isolated tmp_path databases; never touch the real hermes_cache.duckdb.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def l1_db(tmp_path) -> Path:
    """Initialize a fresh L1 cache DB in tmp_path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "init_l1_cache",
        Path(__file__).parent.parent / "scripts" / "03_init_l1_cache.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_path = tmp_path / "test_l1_cache.duckdb"
    con = mod.init_l1_cache(cache_path=db_path)
    con.close()
    return db_path


def _insert_record(db_path: Path, *, expired: bool = False) -> str:
    """Insert a synthetic memory_recent record."""
    rec_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(days=1) if expired else now + timedelta(days=7)

    # Fake embedding matching current EMBEDDING_DIM
    from config.settings import EMBEDDING_DIM
    embedding = [0.0] * EMBEDDING_DIM

    con = duckdb.connect(str(db_path))
    # Must load VSS before modifying tables with HNSW index
    try:
        con.execute("LOAD vss")
    except Exception:
        pass
    con.execute("SET hnsw_enable_experimental_persistence = true")
    con.execute(
        """
        INSERT INTO memory_recent
            (id, sample_id, query_text, report_text, summary,
             embedding, analysis_id, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            rec_id,
            "test_sample",
            "PTPRC spatial map",
            "# Test Report",
            "測試摘要",
            embedding,
            str(uuid.uuid4()),
            now,
            expires_at,
        ],
    )
    con.execute("CHECKPOINT")
    con.close()
    return rec_id


# ── 03_init_l1_cache tests ────────────────────────────────────────────────────


class TestInitL1Cache:
    def test_schema_columns_complete(self, l1_db):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "init_l1_cache",
            Path(__file__).parent.parent / "scripts" / "03_init_l1_cache.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        con = duckdb.connect(str(l1_db))
        try:
            con.execute("LOAD vss")
        except Exception:
            pass
        v = mod.verify_schema(con)
        con.close()

        assert v["columns_ok"], f"Missing columns: {v['missing_cols']}"
        assert v["row_count"] == 0

    def test_embedding_dim(self, l1_db):
        from config.settings import EMBEDDING_DIM
        con = duckdb.connect(str(l1_db))
        row = con.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'memory_recent' AND column_name = 'embedding'"
        ).fetchone()
        con.close()
        assert row is not None
        assert str(EMBEDDING_DIM) in row[0]

    def test_idempotent_reinit(self, l1_db):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "init_l1_cache",
            Path(__file__).parent.parent / "scripts" / "03_init_l1_cache.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        con = duckdb.connect(str(l1_db))
        # Running init_l1_cache twice on same con should not raise
        mod.init_l1_cache(con=con)
        v = mod.verify_schema(con)
        con.close()
        assert v["columns_ok"]

    def test_insert_and_read(self, l1_db):
        rec_id = _insert_record(l1_db)
        con = duckdb.connect(str(l1_db), read_only=True)
        row = con.execute(
            "SELECT id, sample_id, summary FROM memory_recent WHERE id = ?",
            [rec_id],
        ).fetchone()
        con.close()
        assert row is not None
        assert row[1] == "test_sample"
        assert row[2] == "測試摘要"


# ── cleanup_l1_cache tests ────────────────────────────────────────────────────


class TestCleanupL1Cache:
    def test_cleanup_empty_cache(self, l1_db):
        from scheduler.cleanup_l1_cache import cleanup_expired

        count = cleanup_expired(cache_path=l1_db)
        assert count == 0

    def test_cleanup_no_expired(self, l1_db):
        _insert_record(l1_db, expired=False)
        from scheduler.cleanup_l1_cache import cleanup_expired

        count = cleanup_expired(cache_path=l1_db)
        assert count == 0

        # Record should still exist
        con = duckdb.connect(str(l1_db), read_only=True)
        n = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
        con.close()
        assert n == 1

    def test_cleanup_expired_record(self, l1_db):
        _insert_record(l1_db, expired=True)
        _insert_record(l1_db, expired=False)

        from scheduler.cleanup_l1_cache import cleanup_expired

        deleted = cleanup_expired(cache_path=l1_db)
        assert deleted == 1

        con = duckdb.connect(str(l1_db), read_only=True)
        n = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
        con.close()
        assert n == 1  # only non-expired remains

    def test_cleanup_dry_run(self, l1_db):
        _insert_record(l1_db, expired=True)

        from scheduler.cleanup_l1_cache import cleanup_expired

        count = cleanup_expired(dry_run=True, cache_path=l1_db)
        assert count == 1

        # Record should still exist (dry-run)
        con = duckdb.connect(str(l1_db), read_only=True)
        n = con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]
        con.close()
        assert n == 1

    def test_stats(self, l1_db):
        from scheduler.cleanup_l1_cache import stats

        _insert_record(l1_db, expired=False)
        s = stats(cache_path=l1_db)
        assert s["exists"] is True
        assert s["total_records"] == 1

    def test_missing_cache(self, tmp_path):
        from scheduler.cleanup_l1_cache import cleanup_expired

        count = cleanup_expired(cache_path=tmp_path / "nonexistent.duckdb")
        assert count == 0


# ── rebuild_hnsw tests ────────────────────────────────────────────────────────


class TestRebuildHnsw:
    def test_rebuild_empty_skips(self, l1_db):
        from scheduler.rebuild_hnsw import rebuild_hnsw

        result = rebuild_hnsw(cache_path=l1_db)
        assert result["status"] == "skipped"

    def test_rebuild_force_empty(self, l1_db):
        from scheduler.rebuild_hnsw import rebuild_hnsw

        result = rebuild_hnsw(force=True, cache_path=l1_db)
        # Empty table HNSW rebuild may succeed or warn; should not crash
        assert result["status"] in ("ok", "error", "skipped")

    def test_rebuild_with_data(self, l1_db):
        _insert_record(l1_db, expired=False)

        from scheduler.rebuild_hnsw import rebuild_hnsw, index_exists

        result = rebuild_hnsw(cache_path=l1_db)
        assert result["status"] == "ok"
        assert result["row_count"] == 1
        assert index_exists(l1_db)

    def test_index_exists_after_init(self, l1_db):
        from scheduler.rebuild_hnsw import index_exists

        # Index created during init (even on empty table)
        assert index_exists(l1_db)

    def test_missing_cache(self, tmp_path):
        from scheduler.rebuild_hnsw import rebuild_hnsw

        result = rebuild_hnsw(cache_path=tmp_path / "nonexistent.duckdb")
        assert result["status"] == "skipped"
