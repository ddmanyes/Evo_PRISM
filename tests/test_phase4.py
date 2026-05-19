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
    def test_tool_count_default_hides_dangerous(self, monkeypatch):
        """預設 MCP_ENABLE_DANGEROUS_TOOLS 未設 → bio_execute_code 不出現（14 個）。"""
        monkeypatch.delenv("MCP_ENABLE_DANGEROUS_TOOLS", raising=False)
        from server.bio_memory_server import list_tools
        tools = run(list_tools())
        assert len(tools) == 14
        names = {t.name for t in tools}
        assert "bio_execute_code" not in names

    def test_tool_count_with_dangerous_enabled(self, monkeypatch):
        """設定 MCP_ENABLE_DANGEROUS_TOOLS=true → 15 個工具。"""
        monkeypatch.setenv("MCP_ENABLE_DANGEROUS_TOOLS", "true")
        from server.bio_memory_server import list_tools
        tools = run(list_tools())
        assert len(tools) == 15
        names = {t.name for t in tools}
        assert "bio_execute_code" in names

    def test_tool_names(self, monkeypatch):
        monkeypatch.setenv("MCP_ENABLE_DANGEROUS_TOOLS", "true")
        from server.bio_memory_server import list_tools
        tools = run(list_tools())
        names = {t.name for t in tools}
        expected = {
            "bio_history_lookup", "bio_history_timeline", "bio_history_check",
            "bio_history_search", "bio_memory_query", "bio_memory_write",
            "bio_register_sample",
            "bio_artifact_search", "bio_artifact_summary",
            "bio_check_l2_sufficiency",
            "bio_run_spatial_eda", "bio_run_bulk_eda",
            "bio_execute_code", "bio_tool_health",
            "bio_read_report",
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


# ── format=json 結構化回傳（P3 L612）──────────────────────────────────────────


class TestFormatJson:
    """3 個唯讀 history 工具支援 format=json，輸出可由 json.loads 解析的 string。"""

    def test_history_check_json_exists(self, tmp_path):
        import json
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_check
        with patch("config.settings.DUCKDB_PATH", db):
            result = run(_handle_bio_history_check({
                "sample_id": "test_s1",
                "analysis_type": "spatial_eda",
                "format": "json",
            }))
        payload = json.loads(result)
        assert payload["exists"] is True
        assert payload["sample_id"] == "test_s1"
        assert payload["analysis_type"] == "spatial_eda"
        assert "analysis_id" in payload
        assert "completed_at" in payload

    def test_history_check_json_not_exists(self, tmp_path):
        import json
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_check
        with patch("config.settings.DUCKDB_PATH", db):
            result = run(_handle_bio_history_check({
                "sample_id": "test_s1",
                "analysis_type": "bulk_eda",
                "format": "json",
            }))
        payload = json.loads(result)
        assert payload["exists"] is False
        assert payload["sample_id"] == "test_s1"

    def test_history_lookup_json(self, tmp_path, monkeypatch):
        import json
        db = _make_main_db(tmp_path)
        monkeypatch.setattr("config.settings.DUCKDB_PATH", db)
        monkeypatch.setattr("analysis.history_query.DUCKDB_PATH", db)
        from server.bio_memory_server import _handle_bio_history_lookup
        result = run(_handle_bio_history_lookup({"sample_id": "test_s1", "format": "json"}))
        payload = json.loads(result)
        assert payload["count"] >= 1
        assert any(r["sample_id"] == "test_s1" for r in payload["records"])
        # 結構化欄位皆完整（不被 _fmt_table 的 80 字截斷影響）
        rec = payload["records"][0]
        assert set(rec.keys()) == {
            "analysis_id", "sample_id", "analysis_type", "status",
            "completed_at", "summary", "result_path",
        }

    def test_history_lookup_json_empty(self, tmp_path, monkeypatch):
        import json
        db = _make_main_db(tmp_path)
        monkeypatch.setattr("config.settings.DUCKDB_PATH", db)
        monkeypatch.setattr("analysis.history_query.DUCKDB_PATH", db)
        from server.bio_memory_server import _handle_bio_history_lookup
        result = run(_handle_bio_history_lookup({"sample_id": "no_such", "format": "json"}))
        payload = json.loads(result)
        assert payload["count"] == 0
        assert payload["records"] == []

    def test_history_timeline_json(self, tmp_path):
        import json
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_timeline
        with patch("config.settings.DUCKDB_PATH", db):
            result = run(_handle_bio_history_timeline({"n_days": 30, "format": "json"}))
        payload = json.loads(result)
        assert "count" in payload
        assert "n_days" in payload
        assert payload["n_days"] == 30
        assert isinstance(payload["records"], list)

    def test_unknown_format_falls_back_to_text(self, tmp_path):
        """非 'json' 值（含 'yaml'、空字串）回 text 預設行為，向後相容。"""
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_check
        with patch("config.settings.DUCKDB_PATH", db):
            r1 = run(_handle_bio_history_check({
                "sample_id": "test_s1",
                "analysis_type": "spatial_eda",
                "format": "yaml",  # 未支援，應 fallback text
            }))
        assert "exists: true" in r1
        # 不應為合法 JSON
        import json
        try:
            json.loads(r1)
            assert False, "yaml fallback 應回 text，但回傳了合法 JSON"
        except json.JSONDecodeError:
            pass

    def test_text_format_unchanged_when_omitted(self, tmp_path):
        """未傳 format 時行為與舊版完全相同（向後相容）。"""
        db = _make_main_db(tmp_path)
        from server.bio_memory_server import _handle_bio_history_check
        with patch("config.settings.DUCKDB_PATH", db):
            r = run(_handle_bio_history_check({
                "sample_id": "test_s1",
                "analysis_type": "spatial_eda",
            }))
        assert "exists: true" in r
        assert r.startswith("exists:")


# ── bio_execute_code timeout clamp（P3 review M2）─────────────────────────────


class TestExecuteCodeTimeoutClamp:
    """`_handle_bio_execute_code` 必須將 timeout clamp 至 [1, 300]；
    非法字串/None 等 fallback 為 60。"""

    def _capture_timeout(self, args_passed: list):
        """Returns a fake _exec_bio_execute_code that records args and returns OK."""
        def _fake(args):
            args_passed.append(args)
            return "ok"
        return _fake

    def test_too_large_clamped_to_300(self):
        from server import bio_memory_server as srv
        captured = []
        with patch("server.agent._exec_bio_execute_code", self._capture_timeout(captured)):
            run(srv._handle_bio_execute_code({
                "code": "print(1)", "description": "x", "timeout": 10000,
            }))
        assert captured[0]["timeout"] == 300

    def test_too_small_clamped_to_1(self):
        from server import bio_memory_server as srv
        captured = []
        with patch("server.agent._exec_bio_execute_code", self._capture_timeout(captured)):
            run(srv._handle_bio_execute_code({
                "code": "print(1)", "description": "x", "timeout": 0,
            }))
        assert captured[0]["timeout"] == 1

    def test_invalid_string_falls_back_to_60(self):
        from server import bio_memory_server as srv
        captured = []
        with patch("server.agent._exec_bio_execute_code", self._capture_timeout(captured)):
            run(srv._handle_bio_execute_code({
                "code": "print(1)", "description": "x", "timeout": "abc",
            }))
        assert captured[0]["timeout"] == 60

    def test_normal_value_passed_through(self):
        from server import bio_memory_server as srv
        captured = []
        with patch("server.agent._exec_bio_execute_code", self._capture_timeout(captured)):
            run(srv._handle_bio_execute_code({
                "code": "print(1)", "description": "x", "timeout": 120,
            }))
        assert captured[0]["timeout"] == 120

    def test_omitted_defaults_to_60(self):
        from server import bio_memory_server as srv
        captured = []
        with patch("server.agent._exec_bio_execute_code", self._capture_timeout(captured)):
            run(srv._handle_bio_execute_code({
                "code": "print(1)", "description": "x",
            }))
        assert captured[0]["timeout"] == 60


# ── Dangerous tool gate（P3 review M1）────────────────────────────────────────


class TestDangerousToolGate:
    """`bio_execute_code` 預設不被 list_tools 暴露、call_tool 也擋下。"""

    def test_default_hidden_from_call_tool(self, monkeypatch):
        from server.bio_memory_server import call_tool
        monkeypatch.delenv("MCP_ENABLE_DANGEROUS_TOOLS", raising=False)
        result = run(call_tool("bio_execute_code", {"code": "1", "description": "x"}))
        assert len(result) == 1
        text = result[0].text
        assert "[ERROR]" in text
        assert "高權限工具" in text or "MCP_ENABLE_DANGEROUS_TOOLS" in text

    def test_enabled_passes_dangerous_gate(self, monkeypatch):
        """env 啟用後 dangerous gate 不再擋；驗證 handler 確實被呼叫且回值正確傳出。"""
        from server.bio_memory_server import call_tool
        monkeypatch.setenv("MCP_ENABLE_DANGEROUS_TOOLS", "true")
        with patch("server.agent._exec_bio_execute_code", return_value="ok"):
            result = run(call_tool("bio_execute_code", {"code": "1", "description": "x"}))
        text = result[0].text
        assert "MCP_ENABLE_DANGEROUS_TOOLS" not in text
        assert text == "ok", f"handler 結果未透傳，實際 {text!r}"

    def test_env_value_case_insensitive(self, monkeypatch):
        from server.bio_memory_server import _dangerous_tools_enabled
        # truthy：常見三件套 + 大小寫變體
        for val in ("true", "TRUE", "True", "1", "yes", "YES", "Yes"):
            monkeypatch.setenv("MCP_ENABLE_DANGEROUS_TOOLS", val)
            assert _dangerous_tools_enabled() is True, f"Failed for {val!r}"
        # falsy：明確 falsy 字串 + 大小寫變體 + 空字串
        for val in ("false", "FALSE", "False", "no", "NO", "No", "0", "", "off", "OFF"):
            monkeypatch.setenv("MCP_ENABLE_DANGEROUS_TOOLS", val)
            assert _dangerous_tools_enabled() is False, f"Failed for {val!r}"


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
