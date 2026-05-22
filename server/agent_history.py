"""
Evo_PRISM — History, Memory, and Sandbox Code Executor Submodule.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)


def _exec_bio_history_check(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH

    sample_id = args["sample_id"]
    analysis_type = args["analysis_type"]
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            """
            SELECT analysis_id, completed_at, result_path, summary, parameters
            FROM   analysis_history
            WHERE  sample_id = ? AND analysis_type = ? AND status = 'completed'
            ORDER  BY completed_at DESC LIMIT 1
            """,
            [sample_id, analysis_type],
        ).fetchone()
    if row:
        analysis_id, completed_at, result_path, summary, parameters = row
        params_str = parameters if parameters else "{}"
        return (
            f"exists: true\nanalysis_id: {analysis_id}\n"
            f"completed_at: {str(completed_at)[:16]}\n"
            f"result_path: {result_path or '（未記錄）'}\n"
            f"parameters: {params_str}\n"
            f"summary: {(summary or '')[:80]}"
        )
    return f"exists: false\n{sample_id!r} × {analysis_type!r} 尚無完成存檔。"


def _exec_bio_history_lookup(args: dict) -> str:
    from analysis.history_query import recent_analyses, find_by_type

    sample_id = args.get("sample_id")
    analysis_type = args.get("analysis_type")
    limit = int(args.get("limit", 20))
    if analysis_type:
        df = find_by_type(analysis_type, sample_id=sample_id, limit=limit)
    else:
        df = recent_analyses(n=limit, sample_id=sample_id)
    if df.empty:
        return f"無分析記錄（sample_id={sample_id!r}）"
    rows = df[["sample_id", "analysis_type", "status", "completed_at", "summary"]].to_dict(
        "records"
    )
    lines = [f"分析歷史（共 {len(rows)} 筆）"]
    for r in rows:
        lines.append(
            f"• {r['sample_id']} / {r['analysis_type']} / {r['status']} "
            f"/ {str(r.get('completed_at', ''))[:16]} / {(r.get('summary') or '')[:40]}"
        )
    return "\n".join(lines)


def _exec_bio_history_timeline(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH

    n_days = int(args.get("n_days", 7))
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            """
            SELECT sample_id, analysis_type, status,
                   strftime(completed_at,'%Y-%m-%d %H:%M') AS completed_at, summary
            FROM   analysis_history
            WHERE  completed_at >= now() - (? * INTERVAL '1 day')
            ORDER  BY completed_at DESC LIMIT 30
            """,
            [n_days],
        ).fetchall()
    if not rows:
        return f"最近 {n_days} 天無分析記錄。"
    lines = [f"最近 {n_days} 天時間軸（{len(rows)} 筆）"]
    for r in rows:
        lines.append(f"• {r[3]} {r[0]} / {r[1]} / {r[2]} — {(r[4] or '')[:40]}")
    return "\n".join(lines)


def _exec_bio_history_search(args: dict) -> str:
    import duckdb
    from analysis.l1_cache import semantic_search
    from config.settings import DUCKDB_PATH

    results = semantic_search(
        args["query"],
        n=int(args.get("n", 5)),
        threshold=float(args.get("threshold", 0.88)),
        sample_id=args.get("sample_id"),
        analysis_type=args.get("analysis_type"),
    )
    if not results:
        return f"語意搜尋 cache miss（query={args['query']!r}）"
    # Enrich each L1 hit with parameters + result_path via l1_cache_id join
    l1_ids = [str(r["id"]) for r in results]  # 統一轉 str，避免 DuckDB UUID 物件型別不一致
    placeholders = ", ".join("?" * len(l1_ids))
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        enrichment_rows = con.execute(
            f"""
            SELECT l1_cache_id, parameters, result_path
            FROM   analysis_history
            WHERE  l1_cache_id IN ({placeholders}) AND status = 'completed'
            """,
            l1_ids,
        ).fetchall()
    enrichment = {str(row[0]): (row[1], row[2]) for row in enrichment_rows}
    if not enrichment:
        logger.warning("bio_history_search: enrichment 查詢無結果，l1_ids=%s", l1_ids)
    for r in results:
        params_raw, path_raw = enrichment.get(str(r["id"]), (None, None))
        r["parameters"] = params_raw if params_raw is not None else "{}"
        r["result_path"] = path_raw if path_raw is not None else "（未記錄）"
    lines = [f"語意搜尋命中 {len(results)} 筆"]
    for r in results:
        lines.append(
            f"  [{r['score']:.3f}] {r['sample_id']}\n"
            f"    摘要: {r['summary']}\n"
            f"    參數: {r['parameters']}\n"
            f"    結果路徑: {r['result_path']}"
        )
    return "\n".join(lines)


def _exec_bio_memory_query(args: dict) -> str:
    from analysis.l1_cache import semantic_search
    from config.settings import L1_COSINE_THRESHOLD

    results = semantic_search(
        args["query"],
        n=1,
        threshold=float(args.get("threshold", L1_COSINE_THRESHOLD)),
        sample_id=args.get("sample_id"),
    )
    if not results:
        return f"L1 cache miss（threshold={args.get('threshold', L1_COSINE_THRESHOLD)}）。建議執行 bio_run_spatial_eda。"
    r = results[0]
    report = r["report_text"]
    total_chars = len(report)
    if total_chars > 2000:
        report = (
            report[:2000]
            + f"\n…（完整報告共 {total_chars} 字，截斷於 2000 字，完整內容見 result_path）"
        )
    return (
        f"L1 cache hit（score={r['score']:.4f}）\n"
        f"summary: {r['summary']}\ncreated_at: {str(r['created_at'])[:16]}\n\n"
        f"--- 完整報告 ---\n{report}"
    )


def _exec_bio_sample_list(args: dict) -> str:
    """列出 sample_registry 中的樣本，支援 data_type / tissue / condition 過濾。"""
    import duckdb
    from config.settings import DUCKDB_PATH

    data_type: Optional[str] = args.get("data_type")
    tissue: Optional[str] = args.get("tissue")
    condition: Optional[str] = args.get("condition")
    limit: int = int(args.get("limit", 50))

    where_clauses: list[str] = []
    params: list = []

    if data_type:
        where_clauses.append("data_type = ?")
        params.append(data_type)
    if tissue:
        where_clauses.append("tissue ILIKE ?")
        params.append(f"%{tissue}%")
    if condition:
        # condition 對應 notes 欄位模糊比對
        where_clauses.append("notes ILIKE ?")
        params.append(f"%{condition}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            f"""
            SELECT sample_id, data_type, tissue, notes AS condition,
                   l2_ready, analysis_done
            FROM   sample_registry
            {where_sql}
            ORDER  BY last_updated DESC
            LIMIT  ?
            """,
            params,
        ).fetchall()

    if not rows:
        return "sample_registry 中無符合條件的樣本。"

    header = f"樣本清單（共 {len(rows)} 筆）\n{'─' * 60}"
    col_header = f"{'sample_id':<30} {'data_type':<15} {'tissue':<12} {'condition':<15} {'l2_ready':<9} {'done'}"
    lines = [header, col_header]
    for r in rows:
        sid, dtype, tis, cond, l2, done = r
        lines.append(
            f"{str(sid):<30} {str(dtype or ''):<15} {str(tis or ''):<12} "
            f"{str(cond or '')[:14]:<15} {'✓' if l2 else '✗':<9} {'✓' if done else '✗'}"
        )
    return "\n".join(lines)


def _exec_bio_sample_compare(args: dict) -> str:
    """比較多個樣本的最新各類型分析摘要，回傳對照表。"""
    import duckdb
    from config.settings import DUCKDB_PATH

    sample_ids: list[str] = args.get("sample_ids", [])
    if len(sample_ids) < 2:
        return "[Error] bio_sample_compare requires at least 2 sample_ids."

    placeholders = ", ".join("?" * len(sample_ids))

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        # 取每個樣本每種分析類型的最新 completed 紀錄
        rows = con.execute(
            f"""
            WITH ranked AS (
                SELECT ah.sample_id,
                       ah.analysis_type,
                       ah.completed_at,
                       ah.summary,
                       ah.result_path,
                       ROW_NUMBER() OVER (
                           PARTITION BY ah.sample_id, ah.analysis_type
                           ORDER BY ah.completed_at DESC
                       ) AS rn
                FROM   analysis_history ah
                WHERE  ah.sample_id IN ({placeholders})
                  AND  ah.status = 'completed'
            )
            SELECT sample_id, analysis_type,
                   strftime(completed_at, '%Y-%m-%d %H:%M') AS completed_at,
                   summary, result_path
            FROM   ranked
            WHERE  rn = 1
            ORDER  BY sample_id, analysis_type
            """,
            sample_ids,
        ).fetchall()

    if not rows:
        return f"指定樣本（{', '.join(sample_ids)}）均無 completed 分析記錄。"

    # 組裝對照表：以 analysis_type 為欄、sample_id 為列
    from collections import defaultdict

    table: dict[str, dict[str, str]] = defaultdict(dict)
    all_types: list[str] = []
    for sample_id, analysis_type, completed_at, summary, result_path in rows:
        entry = f"{(summary or '').strip()[:60]} [{completed_at}]"
        table[sample_id][analysis_type] = entry
        if analysis_type not in all_types:
            all_types.append(analysis_type)

    lines = [f"樣本比較對照表（{len(sample_ids)} 個樣本 × {len(all_types)} 種分析）"]
    lines.append(f"{'分析類型':<20} " + "  ".join(f"{sid[:20]:<22}" for sid in sample_ids))
    lines.append("─" * (22 + 24 * len(sample_ids)))
    for atype in all_types:
        row_parts = [f"{atype:<20}"]
        for sid in sample_ids:
            cell = table.get(sid, {}).get(atype, "（尚無記錄）")
            row_parts.append(f"{cell[:22]:<22}")
        lines.append("  ".join(row_parts))

    return "\n".join(lines)


def _resolve_tool_fn(tool_name: str):
    """Return the live Python callable for *tool_name*, or None if unresolvable."""
    import importlib
    import json

    # Explicit allowlist — only modules inside the analysis/ package are permitted.
    _ALLOWED_MODULES = {
        "analysis.report_generator",
        "analysis.bulk_eda",
        "analysis.spatial_eda",
        "analysis.pathway_scoring",
        "analysis.bulk_timeseries",
        "analysis.multiomics_integration",
    }

    registry_path = Path(__file__).parent.parent / "tools" / "registry.json"
    try:
        entries = json.loads(registry_path.read_text())
        for entry in entries:
            if entry.get("name") != tool_name:
                continue
            module_path = entry.get("module_path", "")
            function_name = entry.get("function_name", "")
            if module_path not in _ALLOWED_MODULES:
                logger.warning(
                    "_resolve_tool_fn: blocked disallowed module %r for tool %r",
                    module_path,
                    tool_name,
                )
                return None
            if not function_name.isidentifier():
                logger.warning(
                    "_resolve_tool_fn: invalid function_name %r for tool %r",
                    function_name,
                    tool_name,
                )
                return None
            mod = importlib.import_module(module_path)
            return getattr(mod, function_name, None)
    except Exception as exc:
        logger.debug("_resolve_tool_fn: could not resolve %r — %s", tool_name, exc)
    return None


def _exec_bio_tool_health(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.tool_registry import (
        tool_health_report,
        set_stability_note,
        prune_deprecated,
        open_stabilization,
        close_stabilization,
        get_complexity_trend,
    )

    action = args.get("action", "report")

    if action == "report":
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            report = tool_health_report(con)
            # Fetch diagnosis_img for each open stabilization for VLM recall
            snapshot_imgs: list[str] = []
            for s in report["open_stabilizations"]:
                row = con.execute(
                    "SELECT diagnosis_img FROM tool_stabilization_log WHERE log_id = ?",
                    [s["log_id"]],
                ).fetchone()
                if row and row[0]:
                    snapshot_imgs.append(f"\n![{s['tool_name']} 穩定化快照]({row[0]})\n")

        lines = [
            "工具庫健康報告",
            f"  active 工具：{report['total_active']} 個",
            f"  deprecated 版本：{report['total_deprecated']} 個",
        ]
        if report["open_stabilizations"]:
            lines.append(f"\n進行中穩定化迭代（{len(report['open_stabilizations'])} 筆）：")
            for s in report["open_stabilizations"]:
                lines.append(
                    f"  [{s['log_id'][:8]}…] {s['tool_name']}  "
                    f"開始於 {s['created_at'][:16]}  "
                    f"行動：{(s['action_taken'] or '—')[:50]}"
                )
        if report["hot_zones"]:
            open_names = {s["tool_name"] for s in report["open_stabilizations"]}
            lines.append("\n熱區工具（revision_count ≥ 3）：")
            for t in report["hot_zones"]:
                tag = " ✓迭代中" if t["tool_name"] in open_names else " ⚠️ 尚無迭代"
                note = t["stability_note"] or "（尚無診斷）"
                lines.append(
                    f"  {t['tool_name']}  revision={t['revision_count']}{tag}  診斷：{note}"
                )
                for entry in t["change_log"][:3]:
                    reason = entry["reason"] or "—"
                    lines.append(
                        f"    [{entry['revision']}] {entry['old_hash'] or 'init'} → "
                        f"{entry['new_hash']}  {entry['changed_at'][:16]}  {reason}"
                    )
            # Actionable prompt for unattended hot zones — Agent can act immediately
            unattended = [t for t in report["hot_zones"] if t["tool_name"] not in open_names]
            if unattended:
                lines.append("\n建議立即開啟穩定化迭代（複製下方參數呼叫 action=stabilize）：")
                for t in unattended:
                    lines.append(
                        f"  tool_name={t['tool_name']!r}  "
                        f"diagnosis='[描述為何頻繁修改]'  "
                        f"action_taken='[計畫重構方向]'"
                    )
        else:
            lines.append("  無熱區工具（所有工具 revision < 3）")
        if report["stale_analyses"]:
            lines.append("\n過期分析結果（由舊版工具產生）：")
            for name, n in report["stale_analyses"].items():
                lines.append(f"  {name}: {n} 筆")
        if report["prune_candidates"]:
            lines.append("\n可安全清理的 deprecated 紀錄：")
            for name, n in report["prune_candidates"].items():
                lines.append(f"  {name}: {n} 筆")
        lines.append(f"\n建議：{report['recommendation']}")
        result = "\n".join(lines)
        # Append VLM snapshots so web_app image extractor picks them up
        if snapshot_imgs:
            result += "\n\n**進行中迭代視覺快照（VLM 記憶參考）**" + "".join(snapshot_imgs)
        return result

    elif action == "diagnose":
        tool_name = args.get("tool_name")
        note = args.get("note")
        if not tool_name or not note:
            return "[Error] diagnose requires tool_name and note."
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            set_stability_note(con, tool_name, note)
            con.execute("CHECKPOINT")
        return (
            f"已寫入 stability_note for {tool_name!r}：\n{note}\n\n"
            "建議接著呼叫 action=stabilize 開啟正式穩定化迭代，記錄行動計畫。"
        )

    elif action == "stabilize":
        tool_name = args.get("tool_name")
        diagnosis = args.get("diagnosis")
        action_taken = args.get("action_taken")
        if not tool_name or not diagnosis or not action_taken:
            return "[Error] stabilize requires tool_name, diagnosis, and action_taken."
        # Resolve the live callable for complexity + snapshot rendering
        fn = _resolve_tool_fn(tool_name)
        rev_history = None
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            if fn:
                try:
                    rows = con.execute(
                        "SELECT revision_number, old_hash, new_hash, change_reason, changed_at "
                        "FROM tool_change_log WHERE tool_name = ? ORDER BY revision_number",
                        [tool_name],
                    ).fetchall()
                    rev_history = [
                        {
                            "revision": r[0],
                            "old_hash": r[1],
                            "new_hash": r[2],
                            "reason": r[3],
                            "changed_at": str(r[4]),
                        }
                        for r in rows
                    ]
                except Exception:
                    pass
            log_id = open_stabilization(
                con,
                tool_name,
                diagnosis,
                action_taken,
                fn=fn,
                revision_history=rev_history,
            )
            con.execute("CHECKPOINT")
        snapshot_note = "（已渲染視覺快照 ✓）" if fn else "（無法取得 callable，快照略過）"
        return (
            f"已開啟穩定化迭代 for {tool_name!r} {snapshot_note}\n"
            f"  log_id: {log_id}\n"
            f"  診斷：{diagnosis}\n"
            f"  行動：{action_taken}\n\n"
            f"完成後請呼叫 action=close_stabilize  log_id={log_id}  outcome=stabilized/reverted。"
        )

    elif action == "close_stabilize":
        log_id = args.get("log_id")
        outcome = args.get("outcome")
        if not log_id or not outcome:
            return "[Error] close_stabilize requires log_id and outcome."
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            try:
                # Fetch tool_name to resolve fn for complexity_after
                row = con.execute(
                    "SELECT tool_name FROM tool_stabilization_log WHERE log_id = ?",
                    [log_id],
                ).fetchone()
                fn = _resolve_tool_fn(row[0]) if row else None
                close_stabilization(
                    con,
                    log_id,
                    outcome,
                    action_taken=args.get("action_taken"),
                    fn=fn,
                )
                # Fetch complexity delta for summary
                result_row = con.execute(
                    "SELECT complexity_before, complexity_after FROM tool_stabilization_log "
                    "WHERE log_id = ?",
                    [log_id],
                ).fetchone()
                con.execute("CHECKPOINT")
            except ValueError as e:
                return f"[Error] {e}"
        outcome_zh = {
            "stabilized": "已穩定化 ✓",
            "ongoing": "仍在進行",
            "reverted": "已回退",
        }.get(outcome, outcome)
        complexity_note = ""
        if result_row and result_row[0] is not None and result_row[1] is not None:
            delta = result_row[0] - result_row[1]
            sign = "↓" if delta > 0 else "→" if delta == 0 else "↑"
            complexity_note = (
                f"\n  Cyclomatic Complexity: {result_row[0]} → {result_row[1]} ({sign}{abs(delta)})"
            )
        return f"迭代 {log_id[:8]}… 已關閉。結果：{outcome_zh}{complexity_note}"

    elif action == "trend":
        tool_name = args.get("tool_name")
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            rows = get_complexity_trend(con, tool_name=tool_name)
        if not rows:
            scope = f"{tool_name!r} " if tool_name else ""
            return f"尚無 {scope}已關閉的穩定化迭代含複雜度數據（complexity_before/after 須同時存在）。"
        lines = ["複雜度改善趨勢（Cyclomatic Complexity）："]
        for r in rows:
            delta = r["delta"]
            arrow = f"↓{delta}" if delta > 0 else f"↑{abs(delta)}" if delta < 0 else "→ 持平"
            lines.append(
                f"  [{r['closed_at'][:10]}] {r['tool_name']}  "
                f"{r['complexity_before']} → {r['complexity_after']}  ({arrow})  "
                f"outcome={r['outcome']}"
            )
        total_delta = sum(r["delta"] for r in rows)
        improved = sum(1 for r in rows if r["delta"] > 0)
        lines.append(
            f"\n合計 {len(rows)} 次迭代，{improved} 次降低複雜度，累積 CC 改善 {total_delta}。"
        )
        return "\n".join(lines)

    elif action == "prune":
        tool_name = args.get("tool_name")
        if not tool_name:
            return "[Error] prune requires tool_name."
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            deleted = prune_deprecated(con, tool_name)
            con.execute("CHECKPOINT")
        if deleted == 0:
            return f"{tool_name!r} 無可清理的 deprecated 紀錄（所有舊版本均有分析引用，已保留）。"
        return f"已清理 {tool_name!r} 的 {deleted} 筆 deprecated 紀錄（無分析引用的版本）。"

    return f"[Error] 未知 action: {action!r}，請使用 report/diagnose/stabilize/close_stabilize/trend/prune。"


def _exec_read_report_wrapper(args: dict) -> str:
    """Helper wrapper because `bio_read_report` was original mapping name."""
    return _exec_bio_read_report(args)


def _exec_bio_read_report(args: dict) -> str:
    """讀取報告原文（沙盒路徑檢查）。委派至 analysis.report_reader。"""
    from analysis.report_reader import read_report, ReportReadError

    try:
        r = read_report(
            args["result_path"],
            max_chars=int(args.get("max_chars", 8000)),
            head_fraction=float(args.get("head_fraction", 0.75)),
        )
    except ReportReadError as exc:
        return f"[ERROR] bio_read_report 失敗：{exc}"
    meta = (
        f"path: {r.path}\ntotal_chars: {r.total_chars} | truncated: {r.truncated}\nnote: {r.note}\n"
    )
    if r.tail:
        return f"{meta}--- HEAD ---\n{r.head}\n--- TAIL ---\n{r.tail}"
    return f"{meta}--- CONTENT ---\n{r.head}"


def _exec_bio_find_tool(args: dict) -> str:
    """語意搜尋既有可重用工具；引導 Agent 重用而非從零重寫。"""
    from analysis.tool_search import search_tools

    query = str(args.get("query", "")).strip()
    if not query:
        return "請提供 query（要做的分析意圖）。"
    n = int(args.get("n", 5))

    try:
        results = search_tools(query, n=n)
    except Exception as e:  # embedding server 離線等 → 友善降級，不中斷對話
        return (
            f"工具搜尋暫不可用（{e}）。可直接用 bio_execute_code，"
            "並優先 import 既有 analysis.* 函數（spatial_eda / bulk_eda / "
            "pathway_scoring / multiomics_integration / bulk_timeseries / report_generator）。"
        )

    if not results:
        return (
            f"找不到夠相似的既有工具（query={query!r}）。"
            "→ 屬非標準分析，請改用 bio_execute_code 撰寫"
            "（仍可 import 沙盒白名單的 analysis.* 函數作為基礎）。"
        )

    lines = [
        f"找到 {len(results)} 個可重用的既有工具（依相關度）。",
        "優先 import 重用，勿從零重寫；都不合適才用 bio_execute_code。\n",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. [{r['score']:.2f}] {r['name']}{r['signature']}\n"
            f"   用途：{r['summary'] or '（無說明）'}\n"
            f"   重用：{r['import_hint']}"
        )
    return "\n".join(lines)


def _exec_bio_get_playbook(args: dict) -> str:
    """取得某分析領域的技能說明書（標準步驟 + 每步該呼叫的函數 + 該出的圖）。"""
    from analysis.playbook import get_playbook, list_playbooks, PlaybookError

    key = str(args.get("domain", "")).strip()
    if not key:
        metas = list_playbooks()
        if not metas:
            return "目前沒有任何分析說明書（playbooks/ 為空）。"
        lines = ["可用的分析說明書（傳 domain=<name 或 data_type> 取完整內容）：\n"]
        for m in metas:
            lines.append(
                f"- {m['name']} (v{m['version']}, data_type={m['data_type']}): {m['when_to_use']}"
            )
        return "\n".join(lines)

    try:
        return get_playbook(key).as_markdown()
    except PlaybookError as e:
        return f"找不到說明書：{e}"


def _exec_bio_impact(args: dict) -> str:
    """影響分析 / 爆炸範圍（read-only，0 token）。"""
    from analysis.impact import compute_impact, render_impact_md

    tool_name = args.get("tool_name") or None
    artifact_id = args.get("artifact_id") or None
    sample_id = args.get("sample_id") or None
    try:
        report = compute_impact(
            tool_name=tool_name,
            artifact_id=artifact_id,
            sample_id=sample_id,
        )
    except ValueError as e:
        return f"影響分析參數錯誤：{e}"
    except Exception as e:
        return f"影響分析失敗：{e}"
    return render_impact_md(report)


def _exec_bio_register_sample(args: dict) -> str:
    import duckdb
    from config.db_utils import safe_write
    from config.settings import DUCKDB_PATH
    from datetime import datetime as dt, timezone as tz

    sample_id = args["sample_id"]
    if not re.match(r"^[a-z0-9_-]+$", sample_id):
        return f"樣本 ID {sample_id!r} 格式錯誤：只允許小寫英數字、底線和連字號。"
    with duckdb.connect(str(DUCKDB_PATH)) as con:
        if con.execute("SELECT 1 FROM sample_registry WHERE sample_id=?", [sample_id]).fetchone():
            return f"樣本 {sample_id!r} 已存在，跳過。"
        safe_write(
            con,
            """INSERT INTO sample_registry
                   (sample_id, project, data_type, platform, species, tissue,
                    l3_path, l2_ready, analysis_done, added_by, notes, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, false, false, ?, ?, ?)""",
            [
                sample_id,
                args.get("project", ""),
                args["data_type"],
                args.get("platform", ""),
                args.get("species", "human"),
                args.get("tissue", ""),
                args["l3_path"],
                "agent",
                args.get("notes", ""),
                dt.now(tz.utc),
            ],
        )
    return f"樣本 {sample_id!r} 已登記。data_type={args['data_type']!r}"


def _archive_history_insert(
    *,
    analysis_id: str,
    sample_id: Optional[str],
    description: str,
    code_lines: int,
    fig_count: int,
    error_summary: Optional[str],
    status: str,  # "completed" | "failed"
    rel_path: str,
    started_at,  # datetime aware UTC
    completed_at,  # datetime aware UTC
) -> None:
    """寫一筆 dynamic_code 歸檔記錄到 analysis_history；失敗只 log 不 raise。"""
    import duckdb
    from config.settings import DUCKDB_PATH
    from config.db_utils import safe_write

    params_json: dict[str, Any] = {
        "description": description,
        "code_lines": code_lines,
        "fig_count": fig_count,
    }
    if error_summary is not None:
        params_json["error_summary"] = error_summary

    summary_text = description[:50] or "dynamic code execution"
    if status == "failed":
        summary_text = f"[FAILED] {summary_text}"[:50]

    try:
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            safe_write(
                con,
                """INSERT INTO analysis_history
                       (analysis_id, sample_id, analysis_type, parameters, status,
                        result_path, requested_by, started_at, completed_at, summary)
                   VALUES (?, ?, 'dynamic_code', ?, ?, ?, 'agent', ?, ?, ?)""",
                [
                    analysis_id,
                    sample_id,
                    json.dumps(params_json),
                    status,
                    rel_path,
                    started_at,
                    completed_at,
                    summary_text,
                ],
            )
    except Exception:
        logger.warning("bio_execute_code: 寫入 analysis_history 失敗（不影響結果）", exc_info=True)


def _exec_bio_execute_code(args: dict) -> str:
    from server.code_executor import sandbox_exec, SecurityError
    from datetime import datetime as dt, timezone as tz
    from config.settings import DYNAMIC_CODE_DIR, BIO_DB_ROOT

    code = args["code"]
    description = args.get("description", "")
    timeout = int(args.get("timeout", 60))
    sample_id = args.get("sample_id") or None  # NULL 比 "unknown" 安全（FK 約束）

    analysis_id = str(uuid.uuid4())
    started_at = dt.now(tz.utc)
    archive_dir = DYNAMIC_CODE_DIR / f"{started_at.strftime('%Y-%m-%d')}_{analysis_id[:8]}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1) Write code immediately so SecurityError-blocked runs are still archived.
    (archive_dir / "code.py").write_text(code, encoding="utf-8")

    # SecurityError 在 sandbox_exec 內部檢查；preamble 為系統注入，不經 LLM 生成。
    # 圖檔直接落地到 archive_dir，省去 tempfile copy。
    preamble = f"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as _plt_orig
_hermes_fig_dir = {str(archive_dir)!r}
_hermes_fig_idx = [0]
_orig_show = _plt_orig.show
def _hermes_show(*a, **kw):
    idx = _hermes_fig_idx[0]
    _plt_orig.savefig(f"{{_hermes_fig_dir}}/fig_{{idx:02d}}.png", dpi=120, bbox_inches="tight")
    _hermes_fig_idx[0] += 1
    _plt_orig.close("all")
_plt_orig.show = _hermes_show
"""
    # SecurityError 提前歸檔並回傳，後續流程 result 保證非 None
    try:
        result = sandbox_exec(code, timeout=timeout, preamble=preamble)
    except SecurityError as e:
        completed_at = dt.now(tz.utc)
        duration_sec = (completed_at - started_at).total_seconds()
        err_msg = str(e)
        (archive_dir / "traceback.txt").write_text(f"SecurityError: {err_msg}\n", encoding="utf-8")
        sec_meta = {
            "analysis_id": analysis_id,
            "description": description,
            "status": "failed",
            "duration_sec": duration_sec,
            "code_lines": len(code.splitlines()),
            "fig_count": 0,
            "created_at": started_at.isoformat(),
            "error_summary": f"SecurityError: {err_msg[:200]}",
        }
        (archive_dir / "meta.json").write_text(
            json.dumps(sec_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            rel_sec = str(archive_dir.relative_to(BIO_DB_ROOT))
        except ValueError:
            rel_sec = str(archive_dir)
        _archive_history_insert(
            analysis_id=analysis_id,
            sample_id=sample_id,
            description=description,
            code_lines=len(code.splitlines()),
            fig_count=0,
            error_summary=f"SecurityError: {err_msg[:200]}",
            status="failed",
            rel_path=rel_sec,
            started_at=started_at,
            completed_at=completed_at,
        )
        return f"[SecurityError] 程式碼違反安全規則：{err_msg}\n歸檔：{rel_sec}/"

    completed_at = dt.now(tz.utc)
    duration_sec = (completed_at - started_at).total_seconds()

    if not result.success:
        status = "failed"
        tb_text = result.traceback or ""
        (archive_dir / "traceback.txt").write_text(tb_text, encoding="utf-8")
        if result.output:
            (archive_dir / "output.txt").write_text(result.output, encoding="utf-8")
        error_summary = tb_text.splitlines()[-1][:200] if tb_text.strip() else "unknown error"
        fig_count = len(sorted(archive_dir.glob("fig_*.png")))
        output_text = result.output or ""
    else:
        status = "completed"
        (archive_dir / "output.txt").write_text(result.output or "", encoding="utf-8")
        error_summary = None
        fig_count = len(sorted(archive_dir.glob("fig_*.png")))
        output_text = result.output or ""

    meta = {
        "analysis_id": analysis_id,
        "description": description,
        "status": status,
        "duration_sec": duration_sec,
        "code_lines": len(code.splitlines()),
        "fig_count": fig_count,
        "created_at": started_at.isoformat(),
        "error_summary": error_summary,
    }
    (archive_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 相對 BIO_DB_ROOT 的路徑（跨機器可攜）
    try:
        rel_archive = str(archive_dir.relative_to(BIO_DB_ROOT))
    except ValueError:
        rel_archive = str(archive_dir)

    # 寫入 analysis_history
    _archive_history_insert(
        analysis_id=analysis_id,
        sample_id=sample_id,
        description=description,
        code_lines=len(code.splitlines()),
        fig_count=fig_count,
        error_summary=error_summary,
        status=status,
        rel_path=rel_archive,
        started_at=started_at,
        completed_at=completed_at,
    )

    # Collect figures as base64 for inline rendering
    fig_md = ""
    for fp in sorted(archive_dir.glob("fig_*.png")):
        b64 = base64.b64encode(fp.read_bytes()).decode()
        fig_md += f"\n![figure](data:image/png;base64,{b64})\n"

    if status == "failed":
        tb_preview = (result.traceback or "")[:1000]
        return (
            f"執行失敗（{result.duration_sec}s）\n"
            f"歸檔（含 traceback）：{rel_archive}/\n"
            f"{tb_preview}"
        )

    out = output_text[:2000] if len(output_text) > 2000 else output_text
    return f"執行成功（{result.duration_sec}s）\n歸檔：{rel_archive}/\n{out}{fig_md}"
