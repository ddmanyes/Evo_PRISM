"""
控制面板資料層 — 為 web_app 的 /dashboard 監控頁聚合各子系統狀態。

定位：純資料聚合，不含 FastAPI。每個 panel 函數回傳可 JSON 序列化的 dict，
方便 web_app 薄包成 API、也方便單元測試（直接傳 read-only con）。

四大監控子系統：
    overview()          — 頂層計數總覽（樣本/分析/動態碼/工具/artifact/快取）
    helix_panel()       — HELIX 工具健康（直接複用 tool_registry.tool_health_report）
    dynamic_code_panel()— 動態程式碼執行紀錄 + 畢業候選（依 description 分組計次）
    cache_panel()       — figure_cache / L1 cache / artifact 大小
    system_panel()      — server 在線狀態 / DB health / 備份 / 磁碟

手動操作（觸發排程、HELIX 操作、畢業）見 server/dashboard_actions.py（Phase 2）。
"""

from __future__ import annotations

import json
from typing import Any

from config.settings import BIO_DB_ROOT


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _check_port(port: int, timeout: float = 1.0) -> bool:
    """探活本機 llama-server /health。"""
    try:
        import httpx

        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=timeout).status_code == 200
    except Exception:
        return False


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ── Panel：總覽 ─────────────────────────────────────────────────────────────


def overview(con) -> dict:
    """頂層計數總覽（輕量，供儀表板首屏）。"""
    from config.db_utils import db_health_check

    db = db_health_check(con)

    by_type = dict(
        con.execute(
            "SELECT analysis_type, COUNT(*) FROM analysis_history GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    )

    dyn = con.execute(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE status='completed') AS completed,
          COUNT(*) FILTER (WHERE status='failed')    AS failed
        FROM analysis_history WHERE analysis_type='dynamic_code'
        """
    ).fetchone()

    tools = con.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE status='active')     AS active,
          COUNT(*) FILTER (WHERE status='deprecated') AS deprecated
        FROM tools
        """
    ).fetchone()

    art = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(file_size_kb),0) FROM analysis_artifacts"
    ).fetchone()

    return {
        "samples": db.get("sample_count", 0),
        "l2_ready": db.get("l2_ready_count", 0),
        "analyses_total": db.get("history_count", 0),
        "analyses_by_type": by_type,
        "running": db.get("running_count", 0),
        "stale": db.get("stale_count", 0),
        "dynamic_code": {"total": dyn[0], "completed": dyn[1], "failed": dyn[2]},
        "tools": {"active": tools[0], "deprecated": tools[1]},
        "artifacts": {"count": art[0], "total_kb": int(art[1])},
    }


# ── Panel：HELIX ────────────────────────────────────────────────────────────


def helix_panel(con) -> dict:
    """HELIX 工具健康全貌（直接複用 tool_registry.tool_health_report）。"""
    from analysis.tool_registry import tool_health_report

    report = tool_health_report(con)

    # 補一份 active 工具清單（report 著重熱區/異常，這裡給完整版本帳本一覽）
    tools = _rows_to_dicts(
        con.execute(
            """
            SELECT tool_name, version, status, revision_count,
                   stability_note, created_at
            FROM tools
            ORDER BY status, revision_count DESC, tool_name
            """
        )
    )
    report["tools"] = tools
    return report


# ── Panel：動態程式碼 ───────────────────────────────────────────────────────


def dynamic_code_panel(con, limit: int = 30) -> dict:
    """動態程式碼執行紀錄 + 畢業候選（高頻者最該沉澱進 HELIX）。"""
    recent = _rows_to_dicts(
        con.execute(
            """
            SELECT analysis_id::VARCHAR AS analysis_id,
                   summary, status, started_at, completed_at,
                   result_path,
                   parameters->>'description' AS description,
                   TRY_CAST(parameters->>'code_lines' AS INTEGER) AS code_lines,
                   TRY_CAST(parameters->>'fig_count'  AS INTEGER) AS fig_count
            FROM analysis_history
            WHERE analysis_type='dynamic_code'
            ORDER BY started_at DESC
            LIMIT ?
            """,
            [int(limit)],
        )
    )

    # 畢業候選：同 description 反覆出現 = 值得固化成正式 tool
    candidates = _rows_to_dicts(
        con.execute(
            """
            SELECT parameters->>'description' AS description,
                   COUNT(*) AS runs,
                   COUNT(*) FILTER (WHERE status='completed') AS completed_runs,
                   MAX(completed_at) AS last_run,
                   ANY_VALUE(result_path) AS sample_archive
            FROM analysis_history
            WHERE analysis_type='dynamic_code'
            GROUP BY 1
            HAVING COUNT(*) >= 2
            ORDER BY runs DESC
            LIMIT 20
            """
        )
    )

    return {"recent": recent, "promotion_candidates": candidates}


# ── Panel：快取 + artifact ──────────────────────────────────────────────────


def cache_panel(con) -> dict:
    """figure_cache / L1 cache / artifact 大小狀態 + 分析產出的圖檔總數。"""
    from scheduler.cleanup_figure_cache import stats as fig_stats
    from scheduler.cleanup_l1_cache import stats as l1_stats

    figc = fig_stats()
    l1 = l1_stats()

    art_by_subtype = _rows_to_dicts(
        con.execute(
            """
            SELECT COALESCE(artifact_subtype,'-') AS subtype,
                   COUNT(*) AS count,
                   COALESCE(SUM(file_size_kb),0)::INTEGER AS total_kb
            FROM analysis_artifacts
            GROUP BY 1 ORDER BY total_kb DESC
            """
        )
    )

    # 真實的「分析產出圖檔」總數：兩個來源
    #   1) analysis_artifacts 中 mime_type 為 image/* 的記錄（ENGRAM 系統登記的）
    #   2) dynamic_code 各次執行的 fig_count 加總（archive 內的 fig_*.png）
    img_artifacts = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(file_size_kb),0) "
        "FROM analysis_artifacts WHERE mime_type LIKE 'image/%'"
    ).fetchone()
    dyn_figs = con.execute(
        """
        SELECT COALESCE(SUM(TRY_CAST(parameters->>'fig_count' AS INTEGER)), 0)
        FROM analysis_history
        WHERE analysis_type='dynamic_code' AND status='completed'
        """
    ).fetchone()[0]

    return {
        "figure_cache": figc,
        "l1_cache": l1,
        "artifacts_by_subtype": art_by_subtype,
        "analysis_images": {
            "artifact_count": img_artifacts[0],
            "artifact_total_kb": int(img_artifacts[1]),
            "dynamic_code_figs": int(dyn_figs or 0),
            "total": img_artifacts[0] + int(dyn_figs or 0),
        },
    }


# ── Panel：系統健康 ─────────────────────────────────────────────────────────


def _read_json(rel: str) -> dict:
    p = BIO_DB_ROOT / rel
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def system_panel(con) -> dict:
    """server 在線 / DB health / 備份新鮮度 / 磁碟。純本機探活，不碰外部 API。"""
    from config.db_utils import db_health_check

    try:
        db = db_health_check(con)
        db_ok = True
    except Exception as e:
        db = {"error": str(e)}
        db_ok = False

    backup = _read_json("logs/backup_status.json")
    disk_free = None
    try:
        import shutil

        disk_free = round(shutil.disk_usage(BIO_DB_ROOT).free / 1024**3, 2)
    except Exception:
        pass

    return {
        "servers": {
            "embedding_8081": _check_port(8081),
            "multimodal_8080": _check_port(8080),
        },
        "db_ok": db_ok,
        "db": db,
        "backup": {
            "last_success_at": backup.get("last_success_at"),
            "last_error": backup.get("last_error"),
        },
        "disk_free_gb": disk_free,
    }


# ── 一次抓全部（供首屏單次請求）──────────────────────────────────────────────


def full_snapshot(con, dynamic_limit: int = 30) -> dict[str, Any]:
    """聚合所有 panel，供 /api/dashboard 一次回傳。"""
    return {
        "overview": overview(con),
        "helix": helix_panel(con),
        "dynamic_code": dynamic_code_panel(con, limit=dynamic_limit),
        "cache": cache_panel(con),
        "system": system_panel(con),
    }
