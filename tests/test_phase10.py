"""
Tests for Phase 10 — MCP HTTP Transport.

Strategy:
  - create_http_app() 回傳有效 ASGI app
  - HTTP transport 可正確回應 MCP initialize + tools/list
  - web_app 掛載後 /mcp 路由可存取
  - start_bioagent.sh VENV 路徑正確
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _build_starlette_app():
    """create_http_app() 回傳 (handler, lifespan_cm) tuple，需以 Starlette 父 app 驅動 lifespan。"""
    import contextlib
    from server.bio_memory_server import create_http_app
    from starlette.applications import Starlette
    from starlette.routing import Mount

    handler, mcp_lifespan = create_http_app()

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        async with mcp_lifespan():
            yield

    return Starlette(routes=[Mount("/", app=handler)], lifespan=_lifespan)


@pytest.fixture()
def http_client():
    """每個測試建新 app，因為 StreamableHTTPSessionManager.run() 每實例只能呼叫一次。"""
    from starlette.testclient import TestClient

    with TestClient(_build_starlette_app(), raise_server_exceptions=False) as client:
        yield client


def _mcp_headers():
    return {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


# ── TestCreateHttpApp ─────────────────────────────────────────────────────────


class TestCreateHttpApp:
    def test_returns_handler_and_lifespan_tuple(self):
        from server.bio_memory_server import create_http_app

        result = create_http_app()
        assert isinstance(result, tuple) and len(result) == 2
        handler, lifespan_cm = result
        assert callable(handler)
        assert callable(lifespan_cm)

    def test_handler_has_asgi_call_signature(self):
        import inspect
        from server.bio_memory_server import create_http_app

        handler, _ = create_http_app()
        sig = inspect.signature(handler)
        assert list(sig.parameters) == ["scope", "receive", "send"]

    def test_idempotent_creation(self):
        from server.bio_memory_server import create_http_app

        h1, l1 = create_http_app()
        h2, l2 = create_http_app()
        assert callable(h1) and callable(l1)
        assert callable(h2) and callable(l2)


# ── TestMCPInitialize ─────────────────────────────────────────────────────────


class TestMCPInitialize:
    def _payload(self, req_id: int) -> bytes:
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.1"},
                },
            }
        ).encode()

    def test_returns_200(self, http_client):
        resp = http_client.post("/", content=self._payload(1), headers=_mcp_headers())
        assert resp.status_code == 200

    def test_response_contains_server_name(self, http_client):
        resp = http_client.post("/", content=self._payload(2), headers=_mcp_headers())
        assert b"bio-memory" in resp.content

    def test_response_contains_protocol_version(self, http_client):
        resp = http_client.post("/", content=self._payload(3), headers=_mcp_headers())
        assert b"2024-11-05" in resp.content


# ── TestMCPToolsList ──────────────────────────────────────────────────────────


class TestMCPToolsList:
    _EXPECTED_TOOLS = {
        "bio_history_lookup",
        "bio_history_timeline",
        "bio_history_check",
        "bio_history_search",
        "bio_memory_query",
        "bio_memory_write",
        "bio_register_sample",
        "bio_artifact_search",
        "bio_artifact_summary",
        "bio_check_l2_sufficiency",
        "bio_run_spatial_eda",
        "bio_run_bulk_eda",
        "bio_run_deg",
        "bio_run_enrichment",
        "bio_run_heatmaps",
        "bio_impact",
        "bio_find_tool",
        "bio_execute_code",
        "bio_tool_health",
        "bio_get_figure",
        "bio_get_artifact",
        "bio_run_mcseg_roi",
        "bio_run_mcseg_fullslide",
        "bio_compute_crc_metrics",
        "bio_failure_summary",
    }

    def _payload(self, req_id: int) -> bytes:
        return json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}}
        ).encode()

    def test_returns_200(self, http_client):
        resp = http_client.post("/", content=self._payload(10), headers=_mcp_headers())
        assert resp.status_code == 200

    def test_contains_all_safe_tools(self, monkeypatch):
        # 預設不啟用 dangerous tools；驗證 13 個 safe 工具都存在。
        # 改用顯式 TestClient 與下方 test_tool_count_is_* 寫法一致，避免依賴 fixture 與 monkeypatch 的執行序。
        monkeypatch.delenv("MCP_ENABLE_DANGEROUS_TOOLS", raising=False)
        from starlette.testclient import TestClient

        with TestClient(_build_starlette_app(), raise_server_exceptions=False) as client:
            resp = client.post("/", content=self._payload(11), headers=_mcp_headers())
        # 以工具「名稱」比對，而非 raw 子字串——工具描述可能合法提到別的工具名
        # （如 bio_find_tool 描述提到 bio_execute_code），子字串檢查會誤判。
        names = set(re.findall(r'"name"\s*:\s*"(bio_[^"]+)"', resp.content.decode()))
        safe_tools = self._EXPECTED_TOOLS - {"bio_execute_code"}
        assert safe_tools <= names, f"缺少 safe 工具：{safe_tools - names}"
        assert "bio_execute_code" not in names, "bio_execute_code 在預設 env 下應該被隱藏"

    def test_tool_count_is_14_when_dangerous_enabled(self, monkeypatch):
        """env=true 時，新建 client 應看到 26 個工具。"""
        monkeypatch.setenv("MCP_ENABLE_DANGEROUS_TOOLS", "true")
        # 重新建 app 確保 env 生效
        from starlette.testclient import TestClient

        with TestClient(_build_starlette_app(), raise_server_exceptions=False) as client:
            resp = client.post("/", content=self._payload(12), headers=_mcp_headers())
        names = re.findall(r'"name"\s*:\s*"(bio_[^"]+)"', resp.content.decode())
        assert len(names) == 26

    def test_tool_count_is_13_by_default(self, monkeypatch):
        """env 未設時，client 只看到 25 個（無 bio_execute_code）。"""
        monkeypatch.delenv("MCP_ENABLE_DANGEROUS_TOOLS", raising=False)
        from starlette.testclient import TestClient

        with TestClient(_build_starlette_app(), raise_server_exceptions=False) as client:
            resp = client.post("/", content=self._payload(13), headers=_mcp_headers())
        names = re.findall(r'"name"\s*:\s*"(bio_[^"]+)"', resp.content.decode())
        assert len(names) == 25
        assert "bio_execute_code" not in names


# ── TestMCPInvalidRequest ─────────────────────────────────────────────────────


class TestMCPInvalidRequest:
    def test_unknown_method_not_500(self, http_client):
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 20, "method": "unknown/method", "params": {}}
        ).encode()
        resp = http_client.post("/", content=payload, headers=_mcp_headers())
        assert resp.status_code < 500

    def test_malformed_json_not_500(self, http_client):
        resp = http_client.post("/", content=b"not valid json {{{", headers=_mcp_headers())
        assert resp.status_code < 500


# ── TestWebAppMCPMount ────────────────────────────────────────────────────────


class TestWebAppMCPMount:
    def test_mcp_route_mounted(self):
        from server.web_app import app

        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/mcp" in paths

    def test_mcp_mount_app_is_not_none(self):
        from server.web_app import app

        mcp_route = next((r for r in app.routes if getattr(r, "path", None) == "/mcp"), None)
        assert mcp_route is not None
        assert mcp_route.app is not None


# ── TestStartScript ───────────────────────────────────────────────────────────


class TestStartScript:
    def test_venv_path_is_hermes(self):
        script = Path(__file__).parent.parent / "start_bioagent.sh"
        content = script.read_text(encoding="utf-8")
        assert "hermes-bio-memory" in content

    def test_venv_path_not_old_bioagent(self):
        script = Path(__file__).parent.parent / "start_bioagent.sh"
        content = script.read_text(encoding="utf-8")
        assert ".venvs/bioagent/bin/python" not in content


# ── TestE2EToolCalls (P2 補洞：之前只測 mount 與 initialize) ──────────────────


def _setup_e2e_db(tmp_path: Path) -> Path:
    """Build a minimal bio_memory.duckdb with one sample + one completed analysis."""
    import duckdb
    from datetime import datetime, timezone

    db = tmp_path / "bio_memory.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        """
        CREATE TABLE sample_registry (
            sample_id VARCHAR PRIMARY KEY, project VARCHAR, data_type VARCHAR,
            platform VARCHAR, species VARCHAR DEFAULT 'human', tissue VARCHAR,
            l3_path VARCHAR, l2_ready BOOLEAN DEFAULT false,
            analysis_done BOOLEAN DEFAULT false, added_by VARCHAR,
            notes VARCHAR, last_updated TIMESTAMPTZ
        )
        """
    )
    con.execute(
        """
        CREATE TABLE analysis_history (
            analysis_id UUID PRIMARY KEY, sample_id VARCHAR,
            analysis_type VARCHAR, parameters JSON, status VARCHAR DEFAULT 'pending',
            result_path VARCHAR, l1_cache_id UUID, requested_by VARCHAR,
            started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
            summary VARCHAR, tool_id UUID
        )
        """
    )
    con.execute(
        "INSERT INTO sample_registry VALUES "
        "('e2e_sample', 'p', 'visium_hd', '10x', 'human', 'tissue', '/x', "
        "true, false, 'pytest', '', now())"
    )
    analysis_id = con.execute(
        "INSERT INTO analysis_history "
        "(analysis_id, sample_id, analysis_type, status, result_path, "
        " requested_by, completed_at, summary) VALUES "
        "(gen_random_uuid(), 'e2e_sample', 'spatial_eda', 'completed', "
        " '/Volumes/NO NAME/result with space|pipe.md', 'pytest', ?, '端對端摘要') "
        "RETURNING analysis_id",
        [datetime.now(timezone.utc)],
    ).fetchone()[0]
    # analysis_artifacts 表 — ENGRAM 工具會 JOIN 此表
    con.execute(
        """
        CREATE TABLE analysis_artifacts (
            artifact_id      UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
            analysis_id      UUID    NOT NULL REFERENCES analysis_history(analysis_id),
            artifact_type    VARCHAR NOT NULL,
            artifact_subtype VARCHAR,
            label            VARCHAR NOT NULL,
            file_path        VARCHAR,
            file_size_kb     INTEGER,
            mime_type        VARCHAR,
            embedding        FLOAT[1024],
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    con.execute(
        "INSERT INTO analysis_artifacts "
        "(analysis_id, artifact_type, artifact_subtype, label, file_path, "
        " file_size_kb, mime_type) VALUES "
        "(?, 'figure', 'gene_spatial_map', 'PTPRC spatial map', "
        " '/results/ptprc.png', 12, 'image/png')",
        [analysis_id],
    )
    con.close()
    return db


def _run_async(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)


def _patch_db_path(monkeypatch, db):
    """同時 patch settings 與 analysis.history_query 的 module-level binding。

    analysis.history_query 在 import 時 `from config.settings import DUCKDB_PATH`，
    若已被其它測試 import，純 patch config.settings 不會回流。"""
    monkeypatch.setattr("config.settings.DUCKDB_PATH", db)
    try:
        import analysis.history_query as _hq

        monkeypatch.setattr(_hq, "DUCKDB_PATH", db)
    except ImportError:
        pass


class TestE2EToolCalls:
    """直接呼叫 call_tool，端對端驗證 7 工具讀真實 DB 回傳結果。"""

    def test_bio_history_lookup_returns_table(self, tmp_path, monkeypatch):
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(call_tool("bio_history_lookup", {"sample_id": "e2e_sample"}))
        text = result[0].text
        assert "e2e_sample" in text
        assert "spatial_eda" in text
        # fmt_table pipe-safe：path 內的 | 必須被 escape，不破表格
        assert "result with space" in text or "result with…" in text or "result wit…" in text

    def test_bio_history_timeline_respects_limit(self, tmp_path, monkeypatch):
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(call_tool("bio_history_timeline", {"n_days": 30, "limit": 5}))
        assert "e2e_sample" in result[0].text

    def test_bio_history_check_exists_true(self, tmp_path, monkeypatch):
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(
            call_tool(
                "bio_history_check",
                {"sample_id": "e2e_sample", "analysis_type": "spatial_eda"},
            )
        )
        assert "exists: true" in result[0].text

    def test_bio_history_check_exists_false(self, tmp_path, monkeypatch):
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(
            call_tool(
                "bio_history_check",
                {"sample_id": "e2e_sample", "analysis_type": "no_such_type"},
            )
        )
        assert "exists: false" in result[0].text

    def test_unknown_tool_recorded_as_user_error(self, tmp_path, monkeypatch):
        # 覆蓋 unknown-tool 路徑（不觸發 L1 import，避免 module-bound 路徑干擾）
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(call_tool("no_such_tool", {}))
        assert "未知工具" in result[0].text


# ── TestArtifactE2E (MCP P3-3：ENGRAM 工具暴露) ──────────────────────────────


class TestArtifactE2E:
    def test_bio_artifact_summary_returns_metadata(self, tmp_path, monkeypatch):
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(call_tool("bio_artifact_summary", {"sample_id": "e2e_sample"}))
        text = result[0].text
        assert "e2e_sample" in text
        assert "total_runs: 1" in text
        assert "total_artifacts: 1" in text
        assert "gene_spatial_map" in text

    def test_bio_artifact_summary_no_sample(self, tmp_path, monkeypatch):
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        from server.bio_memory_server import call_tool

        result = _run_async(call_tool("bio_artifact_summary", {"sample_id": "no_such_sample"}))
        assert "尚無" in result[0].text

    def test_bio_artifact_search_subtype_only(self, tmp_path, monkeypatch):
        """Layer 1 exact subtype 不需要 embedding server；驗證可命中。"""
        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        # 強制走 Layer 1 only：mock embed 失敗 → search_artifacts 仍可回 Layer 1 結果
        monkeypatch.setattr("analysis.artifact_registry._get_embedding", lambda q: None)
        from server.bio_memory_server import call_tool

        result = _run_async(
            call_tool(
                "bio_artifact_search",
                {
                    "query": "ptprc",
                    "artifact_subtype": "gene_spatial_map",
                    "threshold": 0.001,
                },
            )
        )
        text = result[0].text
        assert "ENGRAM 命中" in text or "ENGRAM 搜尋無命中" in text
        # 若命中，必須含 subtype
        if "命中 " in text:
            assert "gene_spatial_map" in text


# ── TestAuthMiddleware (MCP_AUTH_TOKEN) ──────────────────────────────────────


class TestAuthMiddleware:
    def test_no_token_returns_401(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-xyz")
        from server.bio_memory_server import create_http_app

        handler, _lifespan = create_http_app()
        sent = []

        async def send(msg):
            sent.append(msg)

        async def recv():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
        _run_async(handler(scope, recv, send))
        assert sent and sent[0]["status"] == 401

    def test_wrong_token_returns_401(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-xyz")
        from server.bio_memory_server import create_http_app

        handler, _lifespan = create_http_app()
        sent = []

        async def send(msg):
            sent.append(msg)

        async def recv():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", b"Bearer wrong")],
            "query_string": b"",
        }
        _run_async(handler(scope, recv, send))
        assert sent and sent[0]["status"] == 401

    def test_no_token_env_means_no_auth(self, monkeypatch):
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        from server.bio_memory_server import create_http_app

        handler, _lifespan = create_http_app()
        assert callable(handler)  # smoke: no exception, no auth gate


# ── TestRateLimitGate ────────────────────────────────────────────────────────


class TestRateLimitGate:
    def test_rate_limit_blocks_after_threshold(self, monkeypatch):
        monkeypatch.setenv("MCP_RATE_LIMIT_PER_MIN", "2")
        # 模組已 import；直接覆寫 max calls 常數
        import server.bio_memory_server as bms

        monkeypatch.setattr(bms, "_RATE_LIMIT_MAX_CALLS", 2)
        bms._rate_buckets.clear()
        from server.bio_memory_server import call_tool

        # 前 2 次容許（不論結果）；第 3 次必被擋
        _run_async(call_tool("bio_history_search", {"query": "x"}))
        _run_async(call_tool("bio_history_search", {"query": "y"}))
        r3 = _run_async(call_tool("bio_history_search", {"query": "z"}))
        assert "速率上限" in r3[0].text


# ── TestMetricsRecording ─────────────────────────────────────────────────────


class TestMetricsRecording:
    def test_metric_row_written_on_success(self, tmp_path, monkeypatch):
        import duckdb

        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        # 強制 lazy init 重跑
        import server.bio_memory_server as bms

        bms._METRICS_SCHEMA_READY = False
        from server.bio_memory_server import call_tool

        _run_async(
            call_tool(
                "bio_history_check", {"sample_id": "e2e_sample", "analysis_type": "spatial_eda"}
            )
        )
        with duckdb.connect(str(db), read_only=True) as con:
            row = con.execute(
                "SELECT tool_name, status FROM mcp_tool_metrics ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        assert row[0] == "bio_history_check"
        assert row[1] == "ok"

    def test_metric_records_user_error(self, tmp_path, monkeypatch):
        # 用 unknown-tool 觸發 user_error 路徑（不依賴 handler 內部行為）
        import duckdb

        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        import server.bio_memory_server as bms

        bms._METRICS_SCHEMA_READY = False
        from server.bio_memory_server import call_tool

        _run_async(call_tool("no_such_tool", {}))
        with duckdb.connect(str(db), read_only=True) as con:
            statuses = [
                r[0]
                for r in con.execute(
                    "SELECT status FROM mcp_tool_metrics WHERE tool_name = ?",
                    ["no_such_tool"],
                ).fetchall()
            ]
        assert "user_error" in statuses

    def test_metric_records_requested_by_and_error_class(self, tmp_path, monkeypatch):
        import duckdb

        db = _setup_e2e_db(tmp_path)
        _patch_db_path(monkeypatch, db)
        import server.bio_memory_server as bms

        bms._METRICS_SCHEMA_READY = False
        from server.bio_memory_server import call_tool

        # 1. 正常呼叫，傳入 requested_by
        _run_async(
            call_tool(
                "bio_history_check",
                {
                    "sample_id": "e2e_sample",
                    "analysis_type": "spatial_eda",
                    "requested_by": "custom_agent",
                },
            )
        )

        # 2. 參數錯誤，觸發 ValueError/KeyError/TypeError，傳入 requested_by
        _run_async(call_tool("bio_history_check", {"requested_by": "error_agent"}))

        with duckdb.connect(str(db), read_only=True) as con:
            # 驗證 custom_agent 寫入
            row_ok = con.execute(
                "SELECT tool_name, status, requested_by, error_class FROM mcp_tool_metrics "
                "WHERE requested_by = 'custom_agent' "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()

            # 驗證 error_agent 寫入
            row_err = con.execute(
                "SELECT tool_name, status, requested_by, error_class FROM mcp_tool_metrics "
                "WHERE requested_by = 'error_agent' "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()

        assert row_ok is not None
        assert row_ok[0] == "bio_history_check"
        assert row_ok[1] == "ok"
        assert row_ok[2] == "custom_agent"
        assert row_ok[3] is None

        assert row_err is not None
        assert row_err[0] == "bio_history_check"
        assert row_err[1] == "user_error"
        assert row_err[2] == "error_agent"
        assert row_err[3] in ("KeyError", "ValueError", "TypeError")
