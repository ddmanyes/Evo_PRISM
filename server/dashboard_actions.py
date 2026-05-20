"""控制面板手動操作層 — 為 web_app 的 /dashboard 提供「手動觸發」動作（Phase 2）。

定位：純操作邏輯，不含 FastAPI。每個 action 回傳可 JSON 序列化的 dict：

    {"ok": bool, "action": str, "result": <any>, "message": str}

授權邊界：本模組**不做授權檢查**。env-gate / loopback-only / X-Dashboard-Token
三層防護由 web_app 的 `_dashboard_actions_guard()` 在路由層把關；本模組假設
呼叫端已通過授權，專注把參數轉成對 scheduler / HELIX 的正確呼叫。

兩類操作：
    scheduler 類（無參數）：backup / cleanup_l1 / cleanup_figure / cleanup_dynamic / rebuild_hnsw
    HELIX 類（需參數）：    mark_stable / close_stabilize / prune_deprecated

設計：
    - scheduler 動作內部各自開自己的連線（函數已自管）；HELIX 動作開一條 write
      連線呼叫 tool_registry——HELIX 寫入內部已 CHECKPOINT（見 CLAUDE.md 7.6），
      無需外層 safe_write()。
    - dispatch() 統一捕捉例外：參數錯誤 → ok=False + 友善訊息；其餘 → ok=False +
      系統錯誤（server-side log 留完整 stack）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── HELIX 連線小工具 ─────────────────────────────────────────────────────────

def _helix_con():
    """開一條對主 DuckDB 的 write 連線（HELIX 操作用）。"""
    import duckdb

    from config.settings import DUCKDB_PATH

    return duckdb.connect(str(DUCKDB_PATH))


def _require(args: dict, key: str) -> str:
    """取出非空字串參數，缺漏即 ValueError（→ dispatch 轉友善訊息）。"""
    val = args.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(f"缺少必要參數：{key}")
    return str(val).strip()


# ── scheduler 類操作 ─────────────────────────────────────────────────────────

def _action_backup(_args: dict) -> dict:
    from scheduler.backup_db import backup

    dest = backup()
    return {"result": {"backup_dir": dest.name}, "message": f"備份完成：{dest.name}"}


def _action_cleanup_l1(_args: dict) -> dict:
    from scheduler.cleanup_l1_cache import cleanup_expired

    n = cleanup_expired()
    return {"result": {"deleted": n}, "message": f"L1 快取清理完成：刪除 {n} 筆過期記錄"}


def _action_cleanup_figure(_args: dict) -> dict:
    from scheduler.cleanup_figure_cache import cleanup

    n = cleanup()
    return {"result": {"deleted": n}, "message": f"figure cache 清理完成：刪除 {n} 個過期圖檔"}


def _action_cleanup_dynamic(_args: dict) -> dict:
    from scheduler.cleanup_dynamic_code import cleanup_old_archives

    removed, candidates = cleanup_old_archives()
    return {
        "result": {"removed": removed, "candidates": len(candidates)},
        "message": f"dynamic_code archive 清理完成：刪除 {removed} 個過期目錄",
    }


def _action_rebuild_hnsw(_args: dict) -> dict:
    from scheduler.rebuild_hnsw import (
        rebuild_artifact_fts,
        rebuild_hnsw,
        refresh_tool_catalog,
    )

    hnsw = rebuild_hnsw()
    fts = rebuild_artifact_fts()
    catalog = refresh_tool_catalog()
    return {
        "result": {"hnsw": hnsw, "fts": fts, "tool_catalog": catalog},
        "message": (
            f"索引重建完成：HNSW={hnsw.get('status')} · FTS={fts.get('status')} · "
            f"工具catalog indexed={catalog.get('indexed', '?')}"
        ),
    }


# ── HELIX 類操作 ─────────────────────────────────────────────────────────────

def _action_mark_stable(args: dict) -> dict:
    from analysis.tool_registry import mark_stable

    tool_name = _require(args, "tool_name")
    reason = _require(args, "reason")
    with _helix_con() as con:
        mark_stable(con, tool_name, reason)
    return {
        "result": {"tool_name": tool_name},
        "message": f"已標記 {tool_name} 為穩定（[STABLE]）",
    }


def _action_close_stabilize(args: dict) -> dict:
    from analysis.tool_registry import close_stabilization

    log_id = _require(args, "log_id")
    outcome = _require(args, "outcome")
    if outcome not in ("stabilized", "ongoing", "reverted"):
        raise ValueError(f"outcome 須為 stabilized/ongoing/reverted，收到 {outcome!r}")
    action_taken = args.get("action_taken") or None
    with _helix_con() as con:
        # fn=None：web 端關閉不重算 complexity_after（手動覆蓋；複雜度 delta 為選用）
        close_stabilization(con, log_id, outcome, action_taken=action_taken)
    return {
        "result": {"log_id": log_id, "outcome": outcome},
        "message": f"已關閉穩定化迭代 {log_id[:8]}…（outcome={outcome}）",
    }


def _action_prune_deprecated(args: dict) -> dict:
    from analysis.tool_registry import prune_deprecated

    tool_name = _require(args, "tool_name")
    with _helix_con() as con:
        deleted = prune_deprecated(con, tool_name)
    return {
        "result": {"tool_name": tool_name, "deleted": deleted},
        "message": f"已 prune {tool_name}：刪除 {deleted} 個無 FK 引用的 deprecated 版本",
    }


# ── 註冊表 ───────────────────────────────────────────────────────────────────

# action 名稱 → (handler, 是否為高破壞性操作[前端需強確認], 一句話說明)
ACTIONS: dict[str, tuple[Callable[[dict], dict], bool, str]] = {
    "backup":           (_action_backup,          False, "立即 EXPORT DATABASE 備份主 DuckDB"),
    "cleanup_l1":       (_action_cleanup_l1,       False, "刪除 L1 語意快取中已過期（TTL）的記錄"),
    "cleanup_figure":   (_action_cleanup_figure,   False, "刪除 figure cache 中過期的圖檔"),
    "cleanup_dynamic":  (_action_cleanup_dynamic,  False, "刪除 dynamic_code archive 中超過保留期的目錄"),
    "rebuild_hnsw":     (_action_rebuild_hnsw,     False, "重建 L1 HNSW 索引與 artifact FTS 索引"),
    "mark_stable":      (_action_mark_stable,      False, "把高 revision 工具標記為刻意穩定（抑制熱區噪音）"),
    "close_stabilize":  (_action_close_stabilize,  False, "關閉一個進行中的 HELIX 穩定化迭代"),
    "prune_deprecated": (_action_prune_deprecated, True,  "刪除某工具無歷史引用的舊 deprecated 版本（不可逆）"),
}


def list_actions() -> list[dict[str, Any]]:
    """供前端渲染按鈕的 metadata 清單。"""
    return [
        {"action": name, "destructive": destructive, "description": desc}
        for name, (_fn, destructive, desc) in ACTIONS.items()
    ]


def dispatch(action: str | None, args: dict | None = None) -> dict:
    """執行單一手動操作，永遠回傳結構化 dict（不向外拋例外）。"""
    args = args or {}
    if not action or action not in ACTIONS:
        return {
            "ok": False,
            "action": action,
            "message": f"未知操作：{action!r}（可用：{', '.join(ACTIONS)}）",
        }

    handler = ACTIONS[action][0]
    try:
        out = handler(args)
        return {"ok": True, "action": action, **out}
    except (ValueError, KeyError, TypeError) as e:
        logger.info("dashboard action %s 參數錯誤：%s", action, e)
        return {"ok": False, "action": action, "message": f"參數錯誤：{e}"}
    except Exception as e:  # noqa: BLE001 — 統一出口，避免 500 中斷面板
        logger.exception("dashboard action %s 系統錯誤", action)
        return {"ok": False, "action": action, "message": f"執行失敗：{e}"}
