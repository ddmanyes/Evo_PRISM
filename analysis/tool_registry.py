"""
Tool registry with content-hash versioning and stability tracking.

Detects when tool source code changes, manages tool lifecycle, and surfaces
"hot zones" — tools that change frequently, signalling design instability.

Key tables (bio_memory.duckdb):
  tools           — one active row per tool_name, deprecated rows kept for provenance
  tool_change_log — append-only log of every hash transition
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import duckdb

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
        SELECT tool_id, content_hash, COALESCE(revision_count, 1)
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
    min_revisions: int = 3,
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

    result: list[dict] = []
    for tool_name, rev_count, note, chash, tool_id in rows:
        log_rows = con.execute(
            """
            SELECT revision_number, old_hash, new_hash, change_reason, changed_at
            FROM   tool_change_log
            WHERE  tool_name = ?
            ORDER  BY revision_number DESC
            LIMIT  10
            """,
            [tool_name],
        ).fetchall()
        result.append({
            "tool_name":      tool_name,
            "revision_count": rev_count,
            "stability_note": note,
            "content_hash":   chash,
            "tool_id":        str(tool_id),
            "change_log": [
                {
                    "revision": r[0],
                    "old_hash": r[1],
                    "new_hash": r[2],
                    "reason":   r[3],
                    "changed_at": str(r[4]),
                }
                for r in log_rows
            ],
        })
    return result


# ---------------------------------------------------------------------------
# Pruning (stability-aware)
# ---------------------------------------------------------------------------

def prune_deprecated(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    keep_stable: int = 2,
    keep_unstable: int = 10,
    hot_threshold: int = 3,
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
    return deleted


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------

def tool_health_report(con: duckdb.DuckDBPyConnection) -> dict:
    """Return a summary dict for ``bio_tool_health`` agent tool.

    Keys:
      total_active       int
      total_deprecated   int
      hot_zones          list[dict]   (tools with revision_count >= 3)
      stale_analyses     dict[tool_name → int]   (deprecated-tool analyses)
      prune_candidates   dict[tool_name → int]   (unreferenced deprecated rows)
      recommendation     str
    """
    active_count = con.execute(
        "SELECT count(*) FROM tools WHERE status = 'active'"
    ).fetchone()[0]

    deprecated_count = con.execute(
        "SELECT count(*) FROM tools WHERE status = 'deprecated'"
    ).fetchone()[0]

    hot_zones = get_hot_tools(con, min_revisions=3)

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

    # Build recommendation text
    parts: list[str] = []
    if hot_zones:
        names = ", ".join(t["tool_name"] for t in hot_zones)
        parts.append(f"熱區工具（revision ≥ 3）：{names}。建議診斷後設定 stability_note。")
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
    if not parts:
        parts.append("工具庫健康，無需立即處理。")

    return {
        "total_active":     active_count,
        "total_deprecated": deprecated_count,
        "hot_zones":        hot_zones,
        "stale_analyses":   stale_analyses,
        "prune_candidates": prune_candidates,
        "recommendation":   " ".join(parts),
    }


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
