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
        return json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.1"},
            },
        }).encode()

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
    }

    def _payload(self, req_id: int) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}}).encode()

    def test_returns_200(self, http_client):
        resp = http_client.post("/", content=self._payload(10), headers=_mcp_headers())
        assert resp.status_code == 200

    def test_contains_all_7_tools(self, http_client):
        resp = http_client.post("/", content=self._payload(11), headers=_mcp_headers())
        body = resp.content.decode()
        for tool in self._EXPECTED_TOOLS:
            assert tool in body, f"Tool {tool!r} missing from tools/list"

    def test_tool_count_is_7(self, http_client):
        resp = http_client.post("/", content=self._payload(12), headers=_mcp_headers())
        names = re.findall(r'"name"\s*:\s*"(bio_[^"]+)"', resp.content.decode())
        assert len(names) == 7


# ── TestMCPInvalidRequest ─────────────────────────────────────────────────────


class TestMCPInvalidRequest:
    def test_unknown_method_not_500(self, http_client):
        payload = json.dumps({"jsonrpc": "2.0", "id": 20, "method": "unknown/method", "params": {}}).encode()
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
        content = script.read_text()
        assert "hermes-bio-memory" in content

    def test_venv_path_not_old_bioagent(self):
        script = Path(__file__).parent.parent / "start_bioagent.sh"
        content = script.read_text()
        assert ".venvs/bioagent/bin/python" not in content
