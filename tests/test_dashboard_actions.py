"""控制面板手動操作層（Phase 2）測試。

兩部分：
  1. dashboard_actions.dispatch / list_actions 純邏輯（monkeypatch scheduler/HELIX）
  2. web_app guard 三層防護（env-gate / loopback / token）經 HTTP 驗證

策略：scheduler / HELIX 真實函數一律 monkeypatch 掉，測試不碰真實 DB / 不跑備份。
"""

from __future__ import annotations

import contextlib

import pytest

import server.dashboard_actions as da


# ── dispatch / list_actions 純邏輯 ───────────────────────────────────────────


def test_list_actions_shape():
    actions = da.list_actions()
    names = {a["action"] for a in actions}
    assert {
        "backup",
        "cleanup_l1",
        "cleanup_figure",
        "cleanup_dynamic",
        "rebuild_hnsw",
        "mark_stable",
        "close_stabilize",
        "prune_deprecated",
    } <= names
    by_name = {a["action"]: a for a in actions}
    assert by_name["prune_deprecated"]["destructive"] is True
    assert by_name["backup"]["destructive"] is False
    assert all(a["description"] for a in actions)


def test_dispatch_unknown_action():
    out = da.dispatch("not_a_real_action", {})
    assert out["ok"] is False
    assert "未知操作" in out["message"]


def test_dispatch_none_action():
    out = da.dispatch(None, {})
    assert out["ok"] is False


def test_backup_action_success(monkeypatch):
    class _FakePath:
        name = "20260520_1200"

    monkeypatch.setattr("scheduler.backup_db.backup", lambda: _FakePath())
    out = da.dispatch("backup", {})
    assert out["ok"] is True
    assert out["result"]["backup_dir"] == "20260520_1200"
    assert "20260520_1200" in out["message"]


def test_cleanup_l1_action(monkeypatch):
    monkeypatch.setattr("scheduler.cleanup_l1_cache.cleanup_expired", lambda: 7)
    out = da.dispatch("cleanup_l1", {})
    assert out["ok"] is True
    assert out["result"]["deleted"] == 7


def test_cleanup_dynamic_action(monkeypatch):
    monkeypatch.setattr(
        "scheduler.cleanup_dynamic_code.cleanup_old_archives",
        lambda: (3, [("a", "t1"), ("b", "t2"), ("c", "t3")]),
    )
    out = da.dispatch("cleanup_dynamic", {})
    assert out["ok"] is True
    assert out["result"] == {"removed": 3, "candidates": 3}


def test_rebuild_hnsw_action(monkeypatch):
    monkeypatch.setattr("scheduler.rebuild_hnsw.rebuild_hnsw", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "scheduler.rebuild_hnsw.rebuild_artifact_fts", lambda: {"status": "skipped"}
    )
    out = da.dispatch("rebuild_hnsw", {})
    assert out["ok"] is True
    assert out["result"]["hnsw"]["status"] == "ok"
    assert out["result"]["fts"]["status"] == "skipped"


def test_scheduler_error_wrapped(monkeypatch):
    def _boom():
        raise RuntimeError("backup size below threshold")

    monkeypatch.setattr("scheduler.backup_db.backup", _boom)
    out = da.dispatch("backup", {})
    assert out["ok"] is False
    assert "執行失敗" in out["message"]


# ── HELIX 操作（monkeypatch con + tool_registry）──────────────────────────────


@pytest.fixture
def _fake_helix(monkeypatch):
    """讓 _helix_con() 回傳一個 dummy CM，並捕捉 tool_registry 呼叫參數。"""
    calls: dict[str, tuple] = {}

    @contextlib.contextmanager
    def _dummy_con():
        yield "DUMMY_CON"

    monkeypatch.setattr(da, "_helix_con", _dummy_con)
    monkeypatch.setattr(
        "analysis.tool_registry.mark_stable",
        lambda con, name, reason: calls.__setitem__("mark_stable", (con, name, reason)),
    )
    monkeypatch.setattr(
        "analysis.tool_registry.close_stabilization",
        lambda con, log_id, outcome, action_taken=None: calls.__setitem__(
            "close", (con, log_id, outcome, action_taken)
        ),
    )
    monkeypatch.setattr(
        "analysis.tool_registry.prune_deprecated",
        lambda con, name: calls.__setitem__("prune", (con, name)) or 4,
    )
    return calls


def test_mark_stable_requires_args(_fake_helix):
    out = da.dispatch("mark_stable", {"tool_name": "bio_run_bulk_eda"})  # 缺 reason
    assert out["ok"] is False
    assert "參數錯誤" in out["message"]
    assert "mark_stable" not in _fake_helix  # 未進到 tool_registry


def test_mark_stable_success(_fake_helix):
    out = da.dispatch("mark_stable", {"tool_name": "bio_run_bulk_eda", "reason": "tested"})
    assert out["ok"] is True
    con, name, reason = _fake_helix["mark_stable"]
    assert (con, name, reason) == ("DUMMY_CON", "bio_run_bulk_eda", "tested")


def test_close_stabilize_bad_outcome(_fake_helix):
    out = da.dispatch("close_stabilize", {"log_id": "abc12345", "outcome": "nope"})
    assert out["ok"] is False
    assert "outcome" in out["message"]
    assert "close" not in _fake_helix


def test_close_stabilize_success(_fake_helix):
    out = da.dispatch(
        "close_stabilize",
        {"log_id": "abc12345", "outcome": "stabilized", "action_taken": "refactored"},
    )
    assert out["ok"] is True
    con, log_id, outcome, action_taken = _fake_helix["close"]
    assert outcome == "stabilized"
    assert action_taken == "refactored"


def test_prune_deprecated_success(_fake_helix):
    out = da.dispatch("prune_deprecated", {"tool_name": "bio_run_spatial_eda"})
    assert out["ok"] is True
    assert out["result"]["deleted"] == 4


# ── web_app guard 三層防護（HTTP）─────────────────────────────────────────────


def _enable(monkeypatch, *, enabled=True, remote=False, token=""):
    monkeypatch.setattr("config.settings.DASHBOARD_ACTIONS_ENABLED", enabled)
    monkeypatch.setattr("config.settings.DASHBOARD_ACTIONS_ALLOW_REMOTE", remote)
    monkeypatch.setattr("config.settings.DASHBOARD_ACTION_TOKEN", token)


def test_actions_status_default_disabled(web_app_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    r = web_app_client.get("/api/dashboard/actions")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert len(body["actions"]) >= 8


def test_action_blocked_when_disabled(web_app_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    r = web_app_client.post("/api/dashboard/action", json={"action": "backup"})
    assert r.status_code == 403
    assert "未啟用" in r.json()["detail"]


def test_action_blocked_non_loopback(web_app_client, monkeypatch):
    # TestClient 的 client.host 是 "testclient"（非 loopback）→ remote 未放行時應 403
    _enable(monkeypatch, enabled=True, remote=False)
    r = web_app_client.post("/api/dashboard/action", json={"action": "backup"})
    assert r.status_code == 403
    assert "loopback" in r.json()["detail"]


def test_action_token_required(web_app_client, monkeypatch):
    _enable(monkeypatch, enabled=True, remote=True, token="s3cret")
    # 無 header → 401
    r = web_app_client.post("/api/dashboard/action", json={"action": "backup"})
    assert r.status_code == 401


def test_action_passes_guard_then_dispatch(web_app_client, monkeypatch):
    # 三層全過（enabled + remote 放行 loopback 限制 + 無 token）→ 進到 dispatch
    _enable(monkeypatch, enabled=True, remote=True)
    # 用未知 action：證明 guard 已過（否則會是 403），dispatch 回 ok=False → 400
    r = web_app_client.post("/api/dashboard/action", json={"action": "bogus"})
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert "未知操作" in r.json()["message"]


def test_action_token_match_passes(web_app_client, monkeypatch):
    _enable(monkeypatch, enabled=True, remote=True, token="s3cret")
    monkeypatch.setattr("scheduler.cleanup_l1_cache.cleanup_expired", lambda: 0)
    r = web_app_client.post(
        "/api/dashboard/action",
        json={"action": "cleanup_l1"},
        headers={"X-Dashboard-Token": "s3cret"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
