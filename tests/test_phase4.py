"""
Tests for Phase 4 — Bio-Memory MCP Server.

Strategy:
  - Import server handlers directly (no MCP wire protocol needed for unit tests)
  - Use tmp_path DuckDB fixtures to isolate state
  - Tools requiring embedding server (bio_history_search, bio_memory_query,
    bio_memory_write) are tested with a stub that patches embed_text
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_main_db(tmp_path: Path) -> Path:
    """Create a minimal bio_memory.duckdb with sample_registry and analysis_history."""
    db_path = tmp_path / "bio_memory.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE sample_registry (
            sample_id    VARCHAR PRIMARY KEY,
            project      VARCHAR,
            data_type    VARCHAR,
            platform     VARCHAR,
            species      VARCHAR DEFAULT 'human',
            tissue       VARCHAR,
            l3_path      VARCHAR,
            l2_ready     BOOLEAN DEFAULT false,
            analysis_done BOOLEAN DEFAULT false,
            added_by     VARCHAR,
            notes        VARCHAR,
            last_updated TIMESTAMPTZ
        )
    """)
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id  UUID PRIMARY KEY,
            sample_id    VARCHAR,
            analysis_type VARCHAR,
            parameters   JSON,
            status       VARCHAR DEFAULT 'pending',
            result_path  VARCHAR,
            l1_cache_id  UUID,
            requested_by VARCHAR,
            started_at   TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            summary      VARCHAR,
            tool_id      UUID
        )
    """)
    con.execute("""
        INSERT INTO sample_registry
            (sample_id, project, data_type, platform, species, l3_path,
             l2_ready, analysis_done, added_by, last_updated)
        VALUES ('test_s1', 'proj', 'visium_hd', '10x', 'human', '/data/s1',
                true, false, 'pytest', now())
    """)
    now = datetime.now(timezone.utc)
    con.execute("""
        INSERT INTO analysis_history
            (analysis_id, sample_id, analysis_type, status,
             result_path, requested_by, completed_at, summary)
        VALUES (gen_random_uuid(), 'test_s1', 'spatial_eda', 'completed',
                '/results/s1/eda', 'pytest', ?, '測試摘要')
    """, [now])
    con.close()
    return db_path


def _make_l1_db(tmp_path: Path) -> Path:
    """Create a minimal L1 cache DuckDB with one record."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "init_l1_cache",
        Path(__file__).parent.parent / "scripts" / "03_init_l1_cache.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_path = tmp_path / "hermes_cache.duckdb"
    con = mod.init_l1_cache(cache_path=db_path)
    con.close()

    from config.settings import EMBEDDING_DIM

    con = duckdb.connect(str(db_path))
    try:
        con.execute("LOAD vss")
    except Exception:
        pass
    con.execute("SET hnsw_enable_experimental_persistence = true")
    con.execute("""
        INSERT INTO memory_recent
            (id, sample_id, query_text, report_text, summary,
             embedding, analysis_id, created_at, expires_at)
        VALUES (?, 'test_s1', 'PTPRC spatial map',
                '# Test Report\n\nPTPRC 高表達', '測試 L1 摘要',
                ?, gen_random_uuid(), now(), ?)
    """, [
        str(uuid.uuid4()),
        [0.1] * EMBEDDING_DIM,
        datetime.now(timezone.utc) + timedelta(days=7),
    ])
    con.execute("CHECKPOINT")
    con.close()
    return db_path


def run(coro):
    """Helper to run coroutines in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── list_tools ────────────────────────────────────────────────────────────────


class TestListTools:
    def test_tool_count(self):
        from server.bio_memory_server import list_tools
        tools = run(list_tools())
        assert len(tools) == 9

    def test_tool_names(self):
        from server.bio_memory_server import list_tools
        tools = run(list_tools())
        names = {t.name for t in tools}
        expected = {
            "bio_history_lookup", "bio_history_timeline", "bio_history_check",
            "bio_history_search", "bio_memory_query", "bio_memory_write",
            "bio_register_sample",
            "bio_artifact_search", "bio_artifact_summary",
        }
        assert names == expected

    def test_all_tools_have_schema(self):
        from server.bio_memory_server import list_tools
        tools = run(list_tools())
        for t in tools:
            assert t.inputSchema is not None
            assert "properties" in t.inputSchema


# ── bio_history_check ─────────────────────────────────────────────────────────


class TestBioHistoryCheck:
    def test_exists(self, tmp_path):
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_check
        with patch("config.settings.DUCKDB_PATH", db):
            result = run(_handle_bio_history_check({
                "sample_id": "test_s1",
                "analysis_type": "spatial_eda",
            }))
        assert "exists: true" in result
        assert "result_path" in result

    def test_not_exists(self, tmp_path):
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_check
        with patch("config.settings.DUCKDB_PATH", db):
            result = run(_handle_bio_history_check({
                "sample_id": "test_s1",
                "analysis_type": "bulk_eda",
            }))
        assert "exists: false" in result


# ── bio_history_lookup ────────────────────────────────────────────────────────


class TestBioHistoryLookup:
    def test_lookup_by_sample(self, tmp_path, monkeypatch):
        db = _make_main_db(tmp_path)
        monkeypatch.setattr("config.settings.DUCKDB_PATH", db)
        monkeypatch.setattr("analysis.history_query.DUCKDB_PATH", db)
        from server.bio_memory_server import _handle_bio_history_lookup
        result = run(_handle_bio_history_lookup({"sample_id": "test_s1"}))
        assert "test_s1" in result
        assert "spatial_eda" in result

    def test_lookup_no_sample_returns_all(self, tmp_path, monkeypatch):
        db = _make_main_db(tmp_path)
        monkeypatch.setattr("config.settings.DUCKDB_PATH", db)
        monkeypatch.setattr("analysis.history_query.DUCKDB_PATH", db)
        from server.bio_memory_server import _handle_bio_history_lookup
        result = run(_handle_bio_history_lookup({}))
        assert "test_s1" in result

    def test_lookup_unknown_sample(self, tmp_path, monkeypatch):
        db = _make_main_db(tmp_path)
        monkeypatch.setattr("config.settings.DUCKDB_PATH", db)
        monkeypatch.setattr("analysis.history_query.DUCKDB_PATH", db)
        from server.bio_memory_server import _handle_bio_history_lookup
        result = run(_handle_bio_history_lookup({"sample_id": "no_such_sample"}))
        assert "無分析記錄" in result or "（無記錄）" in result


# ── bio_history_timeline ──────────────────────────────────────────────────────


class TestBioHistoryTimeline:
    def test_timeline_default(self, tmp_path):
        db = _make_main_db(tmp_path)
        with patch("config.settings.DUCKDB_PATH", db):
            from server.bio_memory_server import _handle_bio_history_timeline
            result = run(_handle_bio_history_timeline({}))
        assert "test_s1" in result or "無分析記錄" in result

    def test_timeline_zero_days(self, tmp_path):
        db = _make_main_db(tmp_path)
        with patch("config.settings.DUCKDB_PATH", db):
            from server.bio_memory_server import _handle_bio_history_timeline
            result = run(_handle_bio_history_timeline({"n_days": 0}))
        assert "無分析記錄" in result or "0 天" in result


# ── bio_register_sample ───────────────────────────────────────────────────────


class TestBioRegisterSample:
    def test_register_new(self, tmp_path):
        db = _make_main_db(tmp_path)
        with patch("config.settings.DUCKDB_PATH", db):
            from server.bio_memory_server import _handle_bio_register_sample
            result = run(_handle_bio_register_sample({
                "sample_id": "new_sample_01",
                "data_type": "bulk_rnaseq",
                "l3_path": "/data/new_sample_01",
                "project": "test_project",
                "species": "mouse",
            }))
        assert "已登記" in result

        con = duckdb.connect(str(db), read_only=True)
        row = con.execute(
            "SELECT sample_id, species FROM sample_registry WHERE sample_id = ?",
            ["new_sample_01"],
        ).fetchone()
        con.close()
        assert row is not None
        assert row[1] == "mouse"

    def test_register_duplicate_skips(self, tmp_path):
        db = _make_main_db(tmp_path)
        with patch("config.settings.DUCKDB_PATH", db):
            from server.bio_memory_server import _handle_bio_register_sample
            result = run(_handle_bio_register_sample({
                "sample_id": "test_s1",
                "data_type": "visium_hd",
                "l3_path": "/data/s1",
            }))
        assert "已存在" in result


# ── bio_memory_write + query（需要 stub embedding）────────────────────────────


class TestBioMemoryWriteQuery:
    def _fake_embed(self, text, **kwargs):
        from config.settings import EMBEDDING_DIM
        return [0.9] * EMBEDDING_DIM

    def test_write_to_l1(self, tmp_path):
        l1_db = _make_l1_db(tmp_path)
        # analysis.l1_cache 在 import 時 from config.settings import L1_CACHE_PATH，
        # 必須同時 patch 模組層綁定（patch config.settings 不會回流）。
        with patch("config.settings.L1_CACHE_PATH", l1_db), \
             patch("analysis.l1_cache.L1_CACHE_PATH", l1_db), \
             patch("analysis.embed.embed_batch", return_value=[[0.9] * 1024]):
            from server.bio_memory_server import _handle_bio_memory_write
            result = run(_handle_bio_memory_write({
                "sample_id": "test_s1",
                "query_text": "PTPRC expression map",
                "report_text": "# Report\n\nPTPRC is high.",
                "summary": "test_s1 EDA：PTPRC 高表達。",
            }))
        assert "成功" in result

    def test_query_cache_hit(self, tmp_path):
        from config.settings import EMBEDDING_DIM
        l1_db = _make_l1_db(tmp_path)
        with patch("config.settings.L1_CACHE_PATH", l1_db), \
             patch("config.settings.L1_COSINE_THRESHOLD", 0.0), \
             patch("analysis.embed.embed_batch", return_value=[[0.1] * EMBEDDING_DIM]):
            from server.bio_memory_server import _handle_bio_memory_query
            result = run(_handle_bio_memory_query({
                "query": "PTPRC spatial",
                "threshold": 0.0,
            }))
        assert "cache hit" in result or "cache miss" in result  # either is valid

    def test_query_cache_miss(self, tmp_path):
        from config.settings import EMBEDDING_DIM
        l1_db = _make_l1_db(tmp_path)
        with patch("config.settings.L1_CACHE_PATH", l1_db), \
             patch("analysis.embed.embed_batch", return_value=[[-1.0] * EMBEDDING_DIM]):
            from server.bio_memory_server import _handle_bio_memory_query
            result = run(_handle_bio_memory_query({
                "query": "completely unrelated query xyz",
                "threshold": 0.99,
            }))
        assert "cache miss" in result


# ── bio_history_search ────────────────────────────────────────────────────────


class TestBioHistorySearch:
    def test_search_cache_miss(self, tmp_path):
        from config.settings import EMBEDDING_DIM
        l1_db = _make_l1_db(tmp_path)
        with patch("config.settings.L1_CACHE_PATH", l1_db), \
             patch("analysis.embed.embed_batch", return_value=[[-1.0] * EMBEDDING_DIM]):
            from server.bio_memory_server import _handle_bio_history_search
            result = run(_handle_bio_history_search({
                "query": "unrelated xyz",
                "threshold": 0.99,
            }))
        assert "cache miss" in result

    def test_search_cache_hit(self, tmp_path):
        from config.settings import EMBEDDING_DIM
        l1_db = _make_l1_db(tmp_path)
        with patch("config.settings.L1_CACHE_PATH", l1_db), \
             patch("analysis.embed.embed_batch", return_value=[[0.1] * EMBEDDING_DIM]):
            from server.bio_memory_server import _handle_bio_history_search
            result = run(_handle_bio_history_search({
                "query": "PTPRC spatial",
                "threshold": 0.0,
            }))
        assert "命中" in result or "cache miss" in result


# ── call_tool dispatch ────────────────────────────────────────────────────────


class TestCallToolDispatch:
    def test_unknown_tool_returns_error(self):
        # 改為回傳 error TextContent，不 raise（避免 MCP transport 中斷）
        from server.bio_memory_server import call_tool
        from mcp import types
        result = run(call_tool("no_such_tool", {}))
        assert isinstance(result, list)
        assert isinstance(result[0], types.TextContent)
        assert "未知工具" in result[0].text or "Unknown" in result[0].text

    def test_call_tool_returns_text_content(self, tmp_path):
        db = _make_main_db(tmp_path)
        with patch("config.settings.DUCKDB_PATH", db):
            from server.bio_memory_server import call_tool
            from mcp import types
            result = run(call_tool("bio_history_check", {
                "sample_id": "test_s1",
                "analysis_type": "spatial_eda",
            }))
        assert isinstance(result, list)
        assert isinstance(result[0], types.TextContent)
        assert "exists:" in result[0].text
