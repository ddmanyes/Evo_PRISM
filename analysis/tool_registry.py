"""
HELIX-Core — Health-Evolving Loop with Iterative eXpiration.

Detects when tool source code changes, manages tool lifecycle, and surfaces
"hot zones" — tools that change frequently, signalling design instability.
Drives the stabilization iteration cycle and records complexity deltas as
objective improvement metrics.

Key tables (bio_memory.duckdb):
  tools                  — one active row per tool_name; deprecated rows kept for provenance
  tool_change_log        — append-only log of every hash transition (old_hash → new_hash)
  tool_stabilization_log — one row per stabilization iteration; diagnosis_img stores the
                           VLM visual memory snapshot (HELIX-Vision renders this)
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import duckdb

from config.settings import HELIX_HOT_THRESHOLD

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------

def _normalize_source(source: str) -> str:
    """Strip comment lines and collapse consecutive blank lines to one."""
    lines = source.splitlines()
    normalized: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped == "":
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        normalized.append(line)
    return "\n".join(normalized)


def compute_tool_hash(fn: Callable) -> str:
    """Return first 16 hex chars of SHA-256 over normalized source of *fn*.

    Normalization: strip lines beginning with ``#`` and collapse multiple
    consecutive blank lines into one, so trivial comment edits do not
    invalidate a stored hash.

    Returns the string ``"unavailable"`` when ``inspect.getsource`` raises
    ``OSError`` (e.g. built-ins or compiled extensions).
    """
    try:
        source = inspect.getsource(fn)
    except OSError:
        logger.warning("compute_tool_hash: source unavailable for %r", fn)
        return "unavailable"
    normalized = _normalize_source(source)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------

def register_tool(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    fn: Callable,
    version: str,
    description: str,
) -> str:
    """Register *fn* in the ``tools`` table and return its ``tool_id``.

    Logic:

    1. Compute content hash of *fn*.
    2. If a row with the same ``(tool_name, content_hash)`` already exists
       → return its ``tool_id`` unchanged (idempotent).
    3. If a hash-different *active* row exists → deprecate it, then insert.
    4. Insert new row with ``status='active'`` and return the new ``tool_id``.

    Args:
        con:         Open DuckDB connection (must have write access).
        tool_name:   Logical name matching ``tools/registry.json`` (e.g.
                     ``"bio_run_spatial_eda"``).
        fn:          The Python callable whose source will be hashed.
        version:     Semver string, e.g. ``"1.0.0"``.
        description: Human-readable description stored in the row.

    Returns:
        UUID string of the active ``tool_id`` for this tool.
    """
    content_hash = compute_tool_hash(fn)
    if content_hash == "unavailable":
        raise RuntimeError(
            f"register_tool: source unavailable for {tool_name!r} — "
            "cannot compute content hash; ensure the function is defined in a "
            "readable .py file, not a built-in or compiled extension."
        )
    module_path = fn.__module__ or ""
    function_name = fn.__qualname__

    # --- check exact duplicate (same name + same hash) ---
    existing = con.execute(
        """
        SELECT tool_id
        FROM   tools
        WHERE  tool_name = ? AND content_hash = ?
        LIMIT  1
        """,
        [tool_name, content_hash],
    ).fetchone()
    if existing:
        logger.debug(
            "register_tool: %r already registered (hash=%s)", tool_name, content_hash
        )
        return str(existing[0])

    # --- get old hash + compute next revision_number before deprecating ---
    now = datetime.now(timezone.utc)
    old_row = con.execute(
        """
        SELECT tool_id, content_hash, COALESCE(NULLIF(revision_count, 0), 1)
        FROM   tools
        WHERE  tool_name = ? AND status = 'active'
        LIMIT  1
        """,
        [tool_name],
    ).fetchone()

    old_hash = str(old_row[1]) if old_row else None
    next_revision = (int(old_row[2]) + 1) if old_row else 1

    # --- deprecate any currently active rows for this tool_name ---
    con.execute(
        """
        UPDATE tools
        SET    status = 'deprecated', deprecated_at = ?
        WHERE  tool_name = ? AND status = 'active'
        """,
        [now, tool_name],
    )

    # --- insert new active row ---
    new_tool_id = str(uuid.uuid4())
    con.execute(
        """
        INSERT INTO tools
            (tool_id, tool_name, version, content_hash,
             module_path, function_name, description, status,
             created_at, revision_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        [
            new_tool_id, tool_name, version, content_hash,
            module_path, function_name, description,
            now, next_revision,
        ],
    )

    # --- append to change log ---
    con.execute(
        """
        INSERT INTO tool_change_log
            (tool_name, old_hash, new_hash, new_tool_id, revision_number, changed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [tool_name, old_hash, content_hash, new_tool_id, next_revision, now],
    )

    logger.info(
        "register_tool: registered %r  version=%s  hash=%s  revision=%d  tool_id=%s",
        tool_name, version, content_hash, next_revision, new_tool_id,
    )
    return new_tool_id


def get_active_tool_id(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
) -> Optional[str]:
    """Return ``tool_id`` of the currently active version of *tool_name*.

    Returns ``None`` when no active row exists (tool not yet registered).
    """
    row = con.execute(
        """
        SELECT tool_id
        FROM   tools
        WHERE  tool_name = ? AND status = 'active'
        LIMIT  1
        """,
        [tool_name],
    ).fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def check_tool_drift(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    fn: Callable,
) -> dict:
    """Compare the current hash of *fn* against the stored active hash.

    Returns::

        {
            "drifted":      bool,
            "stored_hash":  str | None,   # None if tool not registered yet
            "current_hash": str,
            "tool_id":      str | None,
        }

    ``drifted=False`` and ``stored_hash=None`` means the tool has never been
    registered; call :func:`register_tool` before relying on ``tool_id``.
    """
    current_hash = compute_tool_hash(fn)

    row = con.execute(
        """
        SELECT tool_id, content_hash
        FROM   tools
        WHERE  tool_name = ? AND status = 'active'
        LIMIT  1
        """,
        [tool_name],
    ).fetchone()

    if row is None:
        return {
            "drifted": False,
            "stored_hash": None,
            "current_hash": current_hash,
            "tool_id": None,
        }

    stored_tool_id = str(row[0])
    stored_hash = str(row[1])
    return {
        "drifted": stored_hash != current_hash,
        "stored_hash": stored_hash,
        "current_hash": current_hash,
        "tool_id": stored_tool_id,
    }


# ---------------------------------------------------------------------------
# Stale-analysis detection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stability diagnosis
# ---------------------------------------------------------------------------

def set_stability_note(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    note: str,
    change_log_id: Optional[str] = None,
) -> None:
    """Write an AI-authored diagnosis onto the active tool row.

    Also optionally annotates the matching ``tool_change_log`` row with
    ``change_reason`` so the log is self-documenting.

    Args:
        con:           Open DuckDB connection (write access required).
        tool_name:     Logical tool name.
        note:          Free-text diagnosis, e.g. "路徑處理邏輯反覆修改，
                       建議抽象為獨立 helper 並加單元測試".
        change_log_id: Optional UUID of the ``tool_change_log`` row to
                       annotate with ``change_reason = note``.
    """
    con.execute(
        "UPDATE tools SET stability_note = ? WHERE tool_name = ? AND status = 'active'",
        [note, tool_name],
    )
    if change_log_id:
        con.execute(
            "UPDATE tool_change_log SET change_reason = ? WHERE log_id = ?",
            [note, change_log_id],
        )
    logger.info("set_stability_note: %r → %s", tool_name, note[:80])


def get_hot_tools(
    con: duckdb.DuckDBPyConnection,
    min_revisions: int = HELIX_HOT_THRESHOLD,
) -> list[dict]:
    """Return tools whose ``revision_count`` meets or exceeds *min_revisions*.

    Results are sorted by revision_count descending (hottest first).
    Each dict contains: ``tool_name``, ``revision_count``, ``stability_note``,
    ``content_hash``, ``tool_id``, and a ``change_log`` list (recent entries).
    """
    rows = con.execute(
        """
        SELECT tool_name, revision_count, stability_note, content_hash, tool_id
        FROM   tools
        WHERE  status = 'active'
          AND  COALESCE(revision_count, 1) >= ?
        ORDER  BY revision_count DESC
        """,
        [min_revisions],
    ).fetchall()

    if not rows:
        return []

    # Batch-fetch all change log entries for the hot tools in a single query
    # to avoid N+1 per-tool round trips.
    hot_names = [r[0] for r in rows]
    placeholders = ", ".join("?" * len(hot_names))
    log_all = con.execute(
        f"""
        SELECT tool_name, revision_number, old_hash, new_hash, change_reason, changed_at
        FROM   tool_change_log
        WHERE  tool_name IN ({placeholders})
        ORDER  BY tool_name, revision_number DESC
        """,
        hot_names,
    ).fetchall()

    # Group log rows by tool_name, keeping at most 10 per tool
    from collections import defaultdict
    log_by_tool: dict[str, list[dict]] = defaultdict(list)
    for tool_name_log, rev, old_h, new_h, reason, changed_at in log_all:
        entries = log_by_tool[tool_name_log]
        if len(entries) < 10:
            entries.append({
                "revision":   rev,
                "old_hash":   old_h,
                "new_hash":   new_h,
                "reason":     reason,
                "changed_at": str(changed_at),
            })

    return [
        {
            "tool_name":      tool_name,
            "revision_count": rev_count,
            "stability_note": note,
            "content_hash":   chash,
            "tool_id":        str(tool_id),
            "change_log":     log_by_tool.get(tool_name, []),
        }
        for tool_name, rev_count, note, chash, tool_id in rows
    ]


# ---------------------------------------------------------------------------
# Pruning (stability-aware)
# ---------------------------------------------------------------------------

def prune_deprecated(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    keep_stable: int = 2,
    keep_unstable: int = 10,
    hot_threshold: int = HELIX_HOT_THRESHOLD,
) -> int:
    """Delete old deprecated rows for *tool_name* that have no analysis_history FK.

    Pruning policy is stability-aware:
    - stable tools   (revision_count < hot_threshold) → keep last *keep_stable* deprecated rows
    - unstable tools (revision_count >= hot_threshold) → keep last *keep_unstable* deprecated rows

    Rows referenced by ``analysis_history.tool_id`` are NEVER deleted (provenance guarantee).

    Returns the number of rows deleted.
    """
    # Determine stability of the currently active version
    active = con.execute(
        "SELECT COALESCE(revision_count, 1) FROM tools "
        "WHERE tool_name = ? AND status = 'active' LIMIT 1",
        [tool_name],
    ).fetchone()
    is_hot = (active is not None) and (int(active[0]) >= hot_threshold)
    keep_n = keep_unstable if is_hot else keep_stable

    # Find prunable deprecated rows: not referenced by analysis_history, oldest first
    candidates = con.execute(
        """
        SELECT t.tool_id
        FROM   tools t
        LEFT JOIN analysis_history ah ON ah.tool_id = t.tool_id
        WHERE  t.tool_name = ?
          AND  t.status    = 'deprecated'
          AND  ah.tool_id  IS NULL
        ORDER  BY t.deprecated_at ASC
        """,
        [tool_name],
    ).fetchall()

    total = len(candidates)
    to_delete = candidates[: max(0, total - keep_n)]

    deleted = 0
    for (tid,) in to_delete:
        con.execute("DELETE FROM tool_change_log WHERE new_tool_id = ?", [tid])
        con.execute("DELETE FROM tools WHERE tool_id = ?", [tid])
        deleted += 1

    if deleted:
        logger.info(
            "prune_deprecated: %r — deleted %d deprecated rows (hot=%s, kept=%d)",
            tool_name, deleted, is_hot, keep_n,
        )

    # Clear diagnosis_img for closed iterations older than 1 year.
    # Text diagnosis and all other columns are preserved for provenance.
    # The helix_expire_snapshots scheduler handles progressive downsampling
    # at 180d/365d; this clause is a hard cleanup for very old entries that
    # were never downsampled (e.g. data pre-dating the scheduler).
    cutoff_1yr = datetime.now(timezone.utc) - timedelta(days=365)
    img_cleared = con.execute(
        """
        UPDATE tool_stabilization_log
        SET    diagnosis_img = NULL
        WHERE  tool_name   = ?
          AND  outcome     != 'ongoing'
          AND  closed_at   IS NOT NULL
          AND  closed_at   < ?
          AND  diagnosis_img IS NOT NULL
        """,
        [tool_name, cutoff_1yr],
    ).rowcount
    if img_cleared:
        logger.info(
            "prune_deprecated: %r — cleared diagnosis_img for %d old iteration(s)",
            tool_name, img_cleared,
        )

    return deleted


# ---------------------------------------------------------------------------
# Stabilization iteration tracking
# ---------------------------------------------------------------------------

def open_stabilization(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    diagnosis: str,
    action_taken: str,
    fn: Optional[Callable] = None,
    revision_history: Optional[list[dict]] = None,
) -> str:
    """Open a new stabilization iteration for *tool_name*.

    Automatically computes cyclomatic complexity and renders a visual diagnosis
    snapshot (PNG base64) when *fn* is provided.  The snapshot is stored in
    ``tool_stabilization_log.diagnosis_img`` for VLM recall.

    Returns the UUID string of the new ``tool_stabilization_log`` row.
    """
    active = con.execute(
        "SELECT COALESCE(revision_count, 1) FROM tools "
        "WHERE tool_name = ? AND status = 'active' LIMIT 1",
        [tool_name],
    ).fetchone()
    if active is None:
        raise ValueError(f"Tool {tool_name!r} not found in tools table.")
    revision_now = int(active[0])

    # Guard: refuse to open a second ongoing iteration for the same tool.
    # Duplicate ongoing rows corrupt the stabilization state machine —
    # close_stabilization() would only close the most-recently opened row,
    # leaving the earlier one dangling forever.
    existing_ongoing = con.execute(
        "SELECT log_id FROM tool_stabilization_log "
        "WHERE tool_name = ? AND closed_at IS NULL LIMIT 1",
        [tool_name],
    ).fetchone()
    if existing_ongoing:
        raise ValueError(
            f"Tool {tool_name!r} already has an open stabilization iteration "
            f"(log_id={existing_ongoing[0]}).  "
            "Call close_stabilization() first before opening a new one."
        )

    # Compute complexity and render snapshot when callable is available
    complexity_before: Optional[int] = None
    loc: Optional[int] = None
    halstead_volume: Optional[float] = None
    diagnosis_img: Optional[str] = None
    if fn is not None:
        try:
            from analysis.tool_visualizer import (
                compute_complexity,
                compute_loc,
                compute_halstead_volume,
                render_diagnosis_snapshot,
            )
            complexity_before = compute_complexity(fn)
            loc = compute_loc(fn)
            halstead_volume = compute_halstead_volume(fn)
            diagnosis_img = render_diagnosis_snapshot(
                tool_name=tool_name,
                fn=fn,
                diagnosis_text=diagnosis,
                revision_history=revision_history,
                complexity=complexity_before,
            )
            logger.info(
                "open_stabilization: snapshot rendered for %r  CC=%s  LOC=%s  HV=%s",
                tool_name, complexity_before, loc, halstead_volume,
            )
        except Exception as exc:
            logger.warning("open_stabilization: snapshot failed — %s", exc)

    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    con.execute(
        """
        INSERT INTO tool_stabilization_log
            (log_id, tool_name, trigger_revision, diagnosis,
             action_taken, outcome, revision_before, created_at,
             complexity_before, diagnosis_img, loc, halstead_volume)
        VALUES (?, ?, ?, ?, ?, 'ongoing', ?, ?, ?, ?, ?, ?)
        """,
        [log_id, tool_name, revision_now, diagnosis, action_taken,
         revision_now, now, complexity_before, diagnosis_img,
         loc, halstead_volume],
    )
    con.execute("CHECKPOINT")
    logger.info("open_stabilization: %r  revision=%d  log_id=%s", tool_name, revision_now, log_id)
    return log_id


def close_stabilization(
    con: duckdb.DuckDBPyConnection,
    log_id: str,
    outcome: str,
    action_taken: Optional[str] = None,
    fn: Optional[Callable] = None,
) -> None:
    """Close an open stabilization iteration.

    Automatically computes ``complexity_after`` when *fn* is provided, allowing
    the delta (complexity_before − complexity_after) to serve as a quantitative
    measure of improvement.

    Args:
        con:          Open DuckDB connection (write access).
        log_id:       UUID of the ``tool_stabilization_log`` row to close.
        outcome:      ``'stabilized'`` | ``'ongoing'`` | ``'reverted'``.
        action_taken: Optional update to the action description.
        fn:           If provided, complexity_after is computed and stored.
    """
    if outcome not in ("stabilized", "ongoing", "reverted"):
        raise ValueError(f"outcome must be stabilized/ongoing/reverted, got {outcome!r}")

    row = con.execute(
        "SELECT tool_name, revision_before FROM tool_stabilization_log WHERE log_id = ?",
        [log_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"No stabilization log found for log_id={log_id!r}")

    tool_name, revision_before = row[0], row[1]
    active = con.execute(
        "SELECT COALESCE(revision_count, 1) FROM tools "
        "WHERE tool_name = ? AND status = 'active' LIMIT 1",
        [tool_name],
    ).fetchone()
    revision_after = int(active[0]) if active else revision_before

    complexity_after: Optional[int] = None
    after_img: Optional[str] = None
    if fn is not None:
        try:
            from analysis.tool_visualizer import compute_complexity, render_diagnosis_snapshot
            complexity_after = compute_complexity(fn)
            # Fetch original diagnosis text for the after snapshot
            orig = con.execute(
                "SELECT diagnosis FROM tool_stabilization_log WHERE log_id = ?", [log_id]
            ).fetchone()
            diagnosis_text = orig[0] if orig else ""
            after_img = render_diagnosis_snapshot(
                tool_name=tool_name,
                fn=fn,
                diagnosis_text=f"[AFTER] {diagnosis_text}",
                complexity=complexity_after,
            )
            logger.info(
                "close_stabilization: after_img rendered for %r  CC=%s",
                tool_name, complexity_after,
            )
        except Exception as exc:
            logger.warning("close_stabilization: after_img/complexity failed — %s", exc)

    now = datetime.now(timezone.utc)
    updates = ["outcome = ?", "revision_after = ?", "closed_at = ?"]
    params: list = [outcome, revision_after, now]
    if action_taken is not None:   # allow empty string to clear the field
        updates.append("action_taken = ?")
        params.append(action_taken)
    if complexity_after is not None:
        updates.append("complexity_after = ?")
        params.append(complexity_after)
    if after_img is not None:
        updates.append("after_img = ?")
        params.append(after_img)
    params.append(log_id)

    con.execute(
        f"UPDATE tool_stabilization_log SET {', '.join(updates)} WHERE log_id = ?",
        params,
    )
    con.execute("CHECKPOINT")
    delta = revision_after - revision_before
    logger.info(
        "close_stabilization: log_id=%s  outcome=%s  revision_delta=+%d",
        log_id, outcome, delta,
    )


def get_open_stabilizations(
    con: duckdb.DuckDBPyConnection,
    tool_name: Optional[str] = None,
) -> list[dict]:
    """Return all open (``closed_at`` IS NULL) stabilization iterations.

    If *tool_name* is given, filters to that tool only.
    """
    sql = """
        SELECT log_id, tool_name, trigger_revision, diagnosis,
               action_taken, revision_before, created_at
        FROM   tool_stabilization_log
        WHERE  closed_at IS NULL
    """
    params: list = []
    if tool_name:
        sql += " AND tool_name = ?"
        params.append(tool_name)
    sql += " ORDER BY created_at DESC"

    rows = con.execute(sql, params).fetchall()
    return [
        {
            "log_id":           str(r[0]),
            "tool_name":        r[1],
            "trigger_revision": r[2],
            "diagnosis":        r[3],
            "action_taken":     r[4],
            "revision_before":  r[5],
            "created_at":       str(r[6]),
        }
        for r in rows
    ]


def backfill_revision_after(con: duckdb.DuckDBPyConnection) -> int:
    """Back-fill ``revision_after`` for closed rows that still have it NULL.

    This can happen if ``close_stabilization`` was called before the tool was
    re-registered.  Uses the current active ``revision_count`` as a proxy.
    Returns number of rows updated.
    """
    rows = con.execute(
        """
        SELECT sl.log_id, sl.tool_name
        FROM   tool_stabilization_log sl
        WHERE  sl.closed_at IS NOT NULL
          AND  sl.revision_after IS NULL
        """
    ).fetchall()

    updated = 0
    for log_id, tool_name in rows:
        active = con.execute(
            "SELECT COALESCE(revision_count, 1) FROM tools "
            "WHERE tool_name = ? AND status = 'active' LIMIT 1",
            [tool_name],
        ).fetchone()
        if active:
            con.execute(
                "UPDATE tool_stabilization_log SET revision_after = ? WHERE log_id = ?",
                [int(active[0]), str(log_id)],
            )
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------

def tool_health_report(con: duckdb.DuckDBPyConnection) -> dict:
    """Return a summary dict for ``bio_tool_health`` agent tool.

    Keys:
      total_active          int
      total_deprecated      int
      hot_zones             list[dict]   (tools with revision_count >= 3)
      open_stabilizations   list[dict]   (ongoing iterations)
      stale_analyses        dict[tool_name → int]
      prune_candidates      dict[tool_name → int]
      recommendation        str
    """
    active_count = con.execute(
        "SELECT count(*) FROM tools WHERE status = 'active'"
    ).fetchone()[0]

    deprecated_count = con.execute(
        "SELECT count(*) FROM tools WHERE status = 'deprecated'"
    ).fetchone()[0]

    hot_zones = get_hot_tools(con, min_revisions=HELIX_HOT_THRESHOLD)
    open_stabilizations = get_open_stabilizations(con)

    # stale analyses per tool_name
    stale_rows = con.execute(
        """
        SELECT t.tool_name, count(*) AS n
        FROM   analysis_history ah
        JOIN   tools t ON ah.tool_id = t.tool_id
        WHERE  t.status = 'deprecated'
        GROUP  BY t.tool_name
        ORDER  BY n DESC
        """
    ).fetchall()
    stale_analyses = {r[0]: r[1] for r in stale_rows}

    # unreferenced deprecated rows per tool_name (safe to prune)
    prune_rows = con.execute(
        """
        SELECT t.tool_name, count(*) AS n
        FROM   tools t
        LEFT JOIN analysis_history ah ON ah.tool_id = t.tool_id
        WHERE  t.status = 'deprecated'
          AND  ah.tool_id IS NULL
        GROUP  BY t.tool_name
        ORDER  BY n DESC
        """
    ).fetchall()
    prune_candidates = {r[0]: r[1] for r in prune_rows}

    # regression_zones: tools whose last closed complexity_after > current complexity_before
    # (i.e. complexity grew back after a stabilization — refactor regression)
    regression_rows = con.execute(
        """
        WITH last_closed AS (
            SELECT DISTINCT ON (tool_name)
                   tool_name, complexity_after, closed_at
            FROM   tool_stabilization_log
            WHERE  closed_at IS NOT NULL
              AND  complexity_after IS NOT NULL
            ORDER  BY tool_name, closed_at DESC
        ),
        last_open AS (
            SELECT DISTINCT ON (tool_name)
                   tool_name, complexity_before
            FROM   tool_stabilization_log
            ORDER  BY tool_name, created_at DESC
        )
        SELECT lc.tool_name,
               lo.complexity_before AS complexity_now,
               lc.complexity_after  AS complexity_after_last
        FROM   last_closed lc
        JOIN   last_open   lo ON lo.tool_name = lc.tool_name
        WHERE  lo.complexity_before > lc.complexity_after
        ORDER  BY (lo.complexity_before - lc.complexity_after) DESC
        """
    ).fetchall()
    regression_zones = [
        {
            "tool_name":              r[0],
            "complexity_now":         r[1],
            "complexity_after_last":  r[2],
            "regression":             r[1] - r[2],
        }
        for r in regression_rows
    ]

    # Build recommendation text
    parts: list[str] = []
    if open_stabilizations:
        names = ", ".join(s["tool_name"] for s in open_stabilizations)
        parts.append(f"進行中穩定化迭代（{len(open_stabilizations)} 筆）：{names}。完成後呼叫 close_stabilize 記錄結果。")
    if hot_zones:
        # Only flag hot zones that don't already have an open stabilization
        open_names = {s["tool_name"] for s in open_stabilizations}
        unattended = [t for t in hot_zones if t["tool_name"] not in open_names]
        if unattended:
            names = ", ".join(t["tool_name"] for t in unattended)
            parts.append(f"熱區工具（revision ≥ 3，尚無迭代）：{names}。建議開啟穩定化迭代。")
    if prune_candidates:
        total_prunable = sum(prune_candidates.values())
        parts.append(
            f"可安全清理 {total_prunable} 筆未被引用的 deprecated 記錄，"
            "呼叫 prune_deprecated() 執行。"
        )
    if stale_analyses:
        total_stale = sum(stale_analyses.values())
        parts.append(
            f"共 {total_stale} 筆分析結果由舊版工具產生，建議評估是否重跑。"
        )
    if regression_zones:
        names = ", ".join(r["tool_name"] for r in regression_zones)
        parts.append(
            f"回潮偵測（{len(regression_zones)} 個工具穩定化後複雜度上升）：{names}。"
            "建議重新開啟穩定化迭代。"
        )
    if not parts:
        parts.append("工具庫健康，無需立即處理。")

    return {
        "total_active":          active_count,
        "total_deprecated":      deprecated_count,
        "hot_zones":             hot_zones,
        "open_stabilizations":   open_stabilizations,
        "stale_analyses":        stale_analyses,
        "prune_candidates":      prune_candidates,
        "regression_zones":      regression_zones,
        "helix_self_health":     helix_self_health(con),
        "recommendation":        " ".join(parts),
    }


def get_complexity_trend(
    con: duckdb.DuckDBPyConnection,
    tool_name: Optional[str] = None,
) -> list[dict]:
    """Return closed stabilization iterations with complexity deltas.

    Each dict contains: ``tool_name``, ``log_id``, ``created_at``,
    ``closed_at``, ``complexity_before``, ``complexity_after``, ``delta``,
    ``outcome``.  Rows without both complexity values are excluded.

    If *tool_name* is given, filters to that tool only.
    """
    sql = """
        SELECT log_id, tool_name, created_at, closed_at,
               complexity_before, complexity_after, outcome
        FROM   tool_stabilization_log
        WHERE  closed_at IS NOT NULL
          AND  complexity_before IS NOT NULL
          AND  complexity_after  IS NOT NULL
    """
    params: list = []
    if tool_name:
        sql += " AND tool_name = ?"
        params.append(tool_name)
    sql += " ORDER BY closed_at DESC"

    rows = con.execute(sql, params).fetchall()
    return [
        {
            "log_id":             str(r[0]),
            "tool_name":          r[1],
            "created_at":         str(r[2]),
            "closed_at":          str(r[3]),
            "complexity_before":  r[4],
            "complexity_after":   r[5],
            "delta":              r[4] - r[5],   # positive = improvement
            "outcome":            r[6],
        }
        for r in rows
    ]


def get_stale_analyses(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
) -> list[dict]:
    """Return ``analysis_history`` rows produced by any deprecated version of *tool_name*.

    Each dict contains: ``analysis_id``, ``sample_id``, ``analysis_type``,
    ``completed_at``, ``summary``.

    Only rows whose ``tool_id`` references a ``deprecated`` tools row are
    returned; rows with ``tool_id = NULL`` are not considered stale.
    """
    rows = con.execute(
        """
        SELECT
            ah.analysis_id,
            ah.sample_id,
            ah.analysis_type,
            ah.completed_at,
            ah.summary
        FROM   analysis_history ah
        JOIN   tools            t  ON ah.tool_id = t.tool_id
        WHERE  t.tool_name = ? AND t.status = 'deprecated'
        ORDER  BY ah.completed_at DESC
        """,
        [tool_name],
    ).fetchall()

    return [
        {
            "analysis_id":   str(r[0]),
            "sample_id":     str(r[1]),
            "analysis_type": str(r[2]),
            "completed_at":  r[3],
            "summary":       str(r[4]) if r[4] is not None else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Stable-tool whitelist
# ---------------------------------------------------------------------------

def mark_stable(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    reason: str,
) -> None:
    """Mark a high-revision tool as intentionally stable (whitelist).

    Sets ``stability_note`` to a special sentinel prefix so that
    ``tool_health_report`` can suppress hot-zone noise for tools that are
    genuinely mature but happen to have a high revision count (e.g. a
    frequently-tweaked utility that is well-tested and intentionally evolving).

    Args:
        con:       Open DuckDB connection (write access).
        tool_name: Logical tool name.
        reason:    Human-readable explanation, e.g. "已有完整單元測試覆蓋，頻繁迭代屬正常維護".
    """
    sentinel = f"[STABLE] {reason}"
    con.execute(
        "UPDATE tools SET stability_note = ? WHERE tool_name = ? AND status = 'active'",
        [sentinel, tool_name],
    )
    con.execute("CHECKPOINT")
    logger.info("mark_stable: %r → %s", tool_name, sentinel[:80])


def is_marked_stable(con: duckdb.DuckDBPyConnection, tool_name: str) -> bool:
    """Return True if the active version of *tool_name* carries the [STABLE] sentinel."""
    row = con.execute(
        "SELECT stability_note FROM tools WHERE tool_name = ? AND status = 'active' LIMIT 1",
        [tool_name],
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return str(row[0]).startswith("[STABLE]")


# ---------------------------------------------------------------------------
# Stale-iteration auto-revert
# ---------------------------------------------------------------------------

def auto_revert_stale_stabilizations(
    con: duckdb.DuckDBPyConnection,
    days: int = 30,
) -> list[str]:
    """Close dangling open iterations that exceeded *days* without being closed.

    Sets ``outcome='reverted'`` and ``closed_at=now`` for every
    ``tool_stabilization_log`` row where ``closed_at IS NULL`` and
    ``created_at < now - days``.

    Returns list of ``log_id`` strings that were auto-reverted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = con.execute(
        """
        SELECT log_id, tool_name, created_at
        FROM   tool_stabilization_log
        WHERE  closed_at IS NULL
          AND  created_at < ?
        ORDER  BY created_at ASC
        """,
        [cutoff],
    ).fetchall()

    if not rows:
        return []

    now = datetime.now(timezone.utc)
    reverted: list[str] = []
    for log_id, tool_name, created_at in rows:
        con.execute(
            """
            UPDATE tool_stabilization_log
            SET    outcome = 'reverted', closed_at = ?,
                   action_taken = COALESCE(action_taken || ' | ', '') ||
                                  '[auto-reverted: exceeded 30-day open limit]'
            WHERE  log_id = ?
            """,
            [now, str(log_id)],
        )
        reverted.append(str(log_id))
        logger.info(
            "auto_revert_stale_stabilizations: %r log_id=%s created_at=%s",
            tool_name, log_id, str(created_at)[:10],
        )

    con.execute("CHECKPOINT")
    return reverted


# ---------------------------------------------------------------------------
# HELIX self-health
# ---------------------------------------------------------------------------

def helix_self_health(con: duckdb.DuckDBPyConnection) -> dict:
    """Return operational metrics about HELIX itself.

    Keys:
      tools_table_rows        int   — total rows in tools (active + deprecated)
      stabilization_log_rows  int   — total rows in tool_stabilization_log
      change_log_rows         int   — total rows in tool_change_log
      orphan_iterations       int   — open iterations with no matching active tool
      downsample_coverage_pct float — % of closed iterations that have diagnosis_img
    """
    tools_rows = con.execute("SELECT count(*) FROM tools").fetchone()[0]
    stab_rows  = con.execute("SELECT count(*) FROM tool_stabilization_log").fetchone()[0]
    chg_rows   = con.execute("SELECT count(*) FROM tool_change_log").fetchone()[0]

    orphan_count = con.execute(
        """
        SELECT count(*)
        FROM   tool_stabilization_log sl
        LEFT JOIN tools t ON t.tool_name = sl.tool_name AND t.status = 'active'
        WHERE  sl.closed_at IS NULL
          AND  t.tool_name IS NULL
        """
    ).fetchone()[0]

    coverage_row = con.execute(
        """
        SELECT
            count(*) FILTER (WHERE diagnosis_img IS NOT NULL) AS with_img,
            count(*) AS total
        FROM tool_stabilization_log
        WHERE closed_at IS NOT NULL
        """
    ).fetchone()
    with_img, total_closed = coverage_row[0], coverage_row[1]
    coverage_pct = round(100.0 * with_img / total_closed, 1) if total_closed > 0 else 0.0

    return {
        "tools_table_rows":        tools_rows,
        "stabilization_log_rows":  stab_rows,
        "change_log_rows":         chg_rows,
        "orphan_iterations":       orphan_count,
        "downsample_coverage_pct": coverage_pct,
    }
