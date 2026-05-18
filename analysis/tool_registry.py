"""
Tool registry with content-hash versioning.

Detects when tool source code changes and manages tool lifecycle.
Each registered tool gets a content hash derived from its normalized source,
allowing the agent to detect stale analysis_history rows when tools change.
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

    # --- deprecate any currently active rows for this tool_name ---
    now = datetime.now(timezone.utc)
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
             module_path, function_name, description, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        [
            new_tool_id,
            tool_name,
            version,
            content_hash,
            module_path,
            function_name,
            description,
            now,
        ],
    )
    logger.info(
        "register_tool: registered %r  version=%s  hash=%s  tool_id=%s",
        tool_name,
        version,
        content_hash,
        new_tool_id,
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
