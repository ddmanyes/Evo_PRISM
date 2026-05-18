"""
ENGRAM-Core — Evidence & iNdexed Graph of Research Artifacts & Memory.

Manages the permanent record of every file produced by an analysis run.
Mirrors the HELIX pattern: register once, query forever, semantic search via HNSW.

Key functions:
    register_artifact()   — log a single output file (auto-embeds if ≤ INLINE_SIZE_LIMIT_KB)
    get_artifacts()       — fetch all artifacts for one analysis run
    compare_analyses()    — side-by-side artifact lists for N analysis runs
    artifact_summary()    — 0-token metadata overview for a sample
    search_artifacts()    — two-layer search: exact subtype first, HNSW fallback
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

logger = logging.getLogger(__name__)

# Artifacts larger than this threshold store only file_path, not inline_data.
INLINE_SIZE_LIMIT_KB = 500

# Recognised artifact subtypes — used for precise SQL filtering.
KNOWN_SUBTYPES = frozenset({
    "volcano", "pca", "heatmap", "qc_figure", "scatter",
    "deg_list", "pathway_scores", "timeseries",
    "eda_report", "summary_report",
    "qc_csv", "counts_csv",
    "run_log",
})

_MIME = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg":  "image/svg+xml",
    ".csv":  "text/csv",
    ".tsv":  "text/tab-separated-values",
    ".md":   "text/markdown",
    ".txt":  "text/plain",
    ".json": "application/json",
    ".log":  "text/plain",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mime_for(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def _read_inline(path: Path) -> Optional[str]:
    """Return base64-encoded content if file ≤ INLINE_SIZE_LIMIT_KB, else None."""
    try:
        size_kb = path.stat().st_size // 1024
        if size_kb > INLINE_SIZE_LIMIT_KB:
            return None
        return base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None


def _make_embed_text(
    label: str,
    artifact_subtype: Optional[str],
    analysis_type: Optional[str],
    parameters: Optional[str],
) -> str:
    parts = [label]
    if artifact_subtype:
        parts.append(artifact_subtype)
    if analysis_type:
        parts.append(analysis_type)
    if parameters:
        parts.append(parameters[:200])
    return " ".join(parts)


def _get_embedding(text: str) -> Optional[list[float]]:
    try:
        from analysis.embed import embed_text
        return embed_text(text)
    except Exception as exc:
        logger.warning("artifact_registry: embedding skipped: %s", exc)
        return None


def _get_analysis_context(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
) -> tuple[Optional[str], Optional[str]]:
    row = con.execute(
        "SELECT analysis_type, parameters FROM analysis_history WHERE analysis_id = ?",
        [analysis_id],
    ).fetchone()
    if row is None:
        return None, None
    return (str(row[0]) if row[0] else None), (str(row[1]) if row[1] else None)


def _rows_to_dicts(rows: list, cols: list[str]) -> list[dict]:
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def register_artifact(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
    file_path: "Path | str",
    artifact_type: str,
    label: str,
    *,
    artifact_subtype: Optional[str] = None,
) -> str:
    """Register a single output file produced by an analysis run.

    Automatically reads file size and MIME type, embeds inline_data when the
    file is ≤ INLINE_SIZE_LIMIT_KB, and generates a semantic embedding for
    HNSW search (requires embedding server; skipped gracefully if unavailable).

    Args:
        con:              Open DuckDB connection (write, bio_memory.duckdb).
        analysis_id:      UUID of the parent analysis_history row.
        file_path:        Absolute path to the output file.
        artifact_type:    'figure' | 'csv' | 'report' | 'log'.
        label:            Human-readable description, e.g. 'PCA 圖'.
        artifact_subtype: Fine-grained type from KNOWN_SUBTYPES, e.g. 'pca'.

    Returns:
        UUID string of the new artifact_id.
    """
    path = Path(file_path)
    artifact_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    mime_type = _mime_for(path)

    size_kb: Optional[int] = None
    inline_data: Optional[str] = None

    if path.exists():
        size_kb = path.stat().st_size // 1024
        inline_data = _read_inline(path)
    else:
        logger.warning("register_artifact: file not found: %s", path)

    analysis_type, parameters = _get_analysis_context(con, analysis_id)
    embed_text = _make_embed_text(label, artifact_subtype, analysis_type, parameters)
    embedding = _get_embedding(embed_text)

    con.execute(
        """
        INSERT INTO analysis_artifacts
            (artifact_id, analysis_id, artifact_type, artifact_subtype,
             label, file_path, inline_data, file_size_kb, mime_type,
             embedding, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            artifact_id, analysis_id, artifact_type, artifact_subtype,
            label, str(path), inline_data, size_kb, mime_type,
            embedding, now,
        ],
    )
    logger.info(
        "register_artifact: %s  type=%s/%s  size=%s KB  inline=%s",
        path.name, artifact_type, artifact_subtype or "-",
        size_kb, inline_data is not None,
    )
    return artifact_id


def get_artifacts(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
    *,
    artifact_type: Optional[str] = None,
    artifact_subtype: Optional[str] = None,
    include_inline: bool = True,
) -> list[dict]:
    """Return all artifacts for one analysis run.

    Args:
        con:              Open DuckDB connection.
        analysis_id:      UUID of the analysis.
        artifact_type:    Optional filter: 'figure' | 'csv' | 'report' | 'log'.
        artifact_subtype: Optional filter: 'volcano' | 'pca' | ...
        include_inline:   If False, omit inline_data (saves memory when browsing).

    Returns:
        List of dicts ordered by created_at.
    """
    inline_col = "inline_data" if include_inline else "NULL AS inline_data"
    params: list = [analysis_id]
    where = ["analysis_id = ?"]

    if artifact_type:
        where.append("artifact_type = ?")
        params.append(artifact_type)
    if artifact_subtype:
        where.append("artifact_subtype = ?")
        params.append(artifact_subtype)

    rows = con.execute(
        f"""
        SELECT artifact_id, analysis_id, artifact_type, artifact_subtype,
               label, file_path, {inline_col}, file_size_kb, mime_type, created_at
        FROM   analysis_artifacts
        WHERE  {" AND ".join(where)}
        ORDER  BY created_at
        """,
        params,
    ).fetchall()

    cols = [
        "artifact_id", "analysis_id", "artifact_type", "artifact_subtype",
        "label", "file_path", "inline_data", "file_size_kb", "mime_type", "created_at",
    ]
    return _rows_to_dicts(rows, cols)


def compare_analyses(
    con: duckdb.DuckDBPyConnection,
    analysis_ids: list[str],
    *,
    artifact_subtype: Optional[str] = None,
    include_inline: bool = True,
) -> dict[str, list[dict]]:
    """Return artifacts grouped by analysis_id for side-by-side comparison.

    Args:
        con:              Open DuckDB connection.
        analysis_ids:     List of analysis UUIDs to compare.
        artifact_subtype: Narrow to a specific subtype (e.g. 'volcano').
        include_inline:   Whether to include base64 image data.

    Returns:
        Dict mapping analysis_id → list of artifact dicts.
        Each dict includes analysis metadata (type, parameters, tool_version).
    """
    if not analysis_ids:
        return {}

    inline_col = "aa.inline_data" if include_inline else "NULL AS inline_data"
    placeholders = ", ".join("?" * len(analysis_ids))
    params: list = list(analysis_ids)

    subtype_clause = ""
    if artifact_subtype:
        subtype_clause = "AND aa.artifact_subtype = ?"
        params.append(artifact_subtype)

    rows = con.execute(
        f"""
        SELECT aa.artifact_id, aa.analysis_id, aa.artifact_type, aa.artifact_subtype,
               aa.label, aa.file_path, {inline_col},
               aa.file_size_kb, aa.mime_type, aa.created_at,
               ah.analysis_type, ah.parameters, ah.completed_at,
               t.version AS tool_version, t.status AS tool_status
        FROM   analysis_artifacts aa
        JOIN   analysis_history   ah ON aa.analysis_id = ah.analysis_id
        LEFT   JOIN tools          t  ON ah.tool_id    = t.tool_id
        WHERE  aa.analysis_id IN ({placeholders})
               {subtype_clause}
        ORDER  BY aa.analysis_id, aa.created_at
        """,
        params,
    ).fetchall()

    result: dict[str, list[dict]] = {aid: [] for aid in analysis_ids}
    for r in rows:
        aid = str(r[1])
        result[aid].append({
            "artifact_id":      str(r[0]),
            "analysis_id":      aid,
            "artifact_type":    r[2],
            "artifact_subtype": r[3],
            "label":            r[4],
            "file_path":        r[5],
            "inline_data":      r[6],
            "file_size_kb":     r[7],
            "mime_type":        r[8],
            "created_at":       str(r[9]),
            "analysis_type":    r[10],
            "parameters":       r[11],
            "completed_at":     str(r[12]) if r[12] else None,
            "tool_version":     r[13],
            "tool_status":      r[14],
        })
    return result


def artifact_summary(
    con: duckdb.DuckDBPyConnection,
    sample_id: str,
) -> dict:
    """Return a 0-token metadata overview for all analyses of a sample.

    Designed for Agent use: one SQL call, no file IO, no LLM tokens.

    Returns:
        sample_id        str
        total_runs       int
        total_artifacts  int
        by_subtype       dict[subtype → count]
        latest_run       dict  (analysis_id, completed_at, artifact_count)
    """
    rows = con.execute(
        """
        SELECT
            ah.analysis_id,
            ah.analysis_type,
            ah.completed_at,
            COUNT(aa.artifact_id)                AS artifact_count,
            STRING_AGG(aa.artifact_subtype, ',') AS subtypes
        FROM   analysis_history   ah
        LEFT   JOIN analysis_artifacts aa ON ah.analysis_id = aa.analysis_id
        WHERE  ah.sample_id = ?
          AND  ah.status    = 'completed'
        GROUP  BY ah.analysis_id, ah.analysis_type, ah.completed_at
        ORDER  BY ah.completed_at DESC
        """,
        [sample_id],
    ).fetchall()

    if not rows:
        return {
            "sample_id":       sample_id,
            "total_runs":      0,
            "total_artifacts": 0,
            "by_subtype":      {},
            "latest_run":      None,
        }

    subtype_counts: dict[str, int] = {}
    total_artifacts = 0
    for _, _, _, artifact_count, subtypes in rows:
        total_artifacts += artifact_count or 0
        if subtypes:
            for st in subtypes.split(","):
                st = st.strip()
                if st and st != "None":
                    subtype_counts[st] = subtype_counts.get(st, 0) + 1

    latest = rows[0]
    return {
        "sample_id":       sample_id,
        "total_runs":      len(rows),
        "total_artifacts": total_artifacts,
        "by_subtype":      subtype_counts,
        "latest_run": {
            "analysis_id":    str(latest[0]),
            "analysis_type":  latest[1],
            "completed_at":   str(latest[2]) if latest[2] else None,
            "artifact_count": latest[3],
        },
    }


def search_artifacts(
    con: duckdb.DuckDBPyConnection,
    query: str,
    *,
    n: int = 5,
    threshold: float = 0.88,
    artifact_subtype: Optional[str] = None,
    sample_id: Optional[str] = None,
) -> list[dict]:
    """Two-layer semantic search across artifact embeddings.

    Layer 1: Exact match on artifact_subtype (zero token, always tried first).
    Layer 2: HNSW cosine vector search as fallback.

    Args:
        con:              Open DuckDB connection (VSS loaded internally).
        query:            Natural language query, e.g. '差異表現的圖'.
        n:                Max results to return.
        threshold:        Cosine similarity threshold (0–1).
        artifact_subtype: If provided, attempt exact subtype match first.
        sample_id:        Restrict search to a specific sample.

    Returns:
        List of artifact dicts with an added 'score' field (1.0 for exact matches).
    """
    cols = [
        "artifact_id", "analysis_id", "artifact_type", "artifact_subtype",
        "label", "file_path", "inline_data", "file_size_kb",
        "mime_type", "created_at", "score",
    ]

    sample_join = ""
    sample_params: list = []
    if sample_id:
        sample_join = "JOIN analysis_history ah ON aa.analysis_id = ah.analysis_id"
        sample_params = [sample_id]

    sample_where = "AND ah.sample_id = ?" if sample_id else ""

    # Layer 1: exact subtype match
    if artifact_subtype:
        exact_rows = con.execute(
            f"""
            SELECT aa.artifact_id::VARCHAR, aa.analysis_id::VARCHAR,
                   aa.artifact_type, aa.artifact_subtype, aa.label, aa.file_path,
                   aa.inline_data, aa.file_size_kb, aa.mime_type,
                   aa.created_at, 1.0 AS score
            FROM   analysis_artifacts aa
            {sample_join}
            WHERE  aa.artifact_subtype = ?
                   {sample_where}
            ORDER  BY aa.created_at DESC
            LIMIT  ?
            """,
            [artifact_subtype] + sample_params + [n],
        ).fetchall()
        if exact_rows:
            return _rows_to_dicts(exact_rows, cols)

    # Layer 2: HNSW semantic search
    embedding = _get_embedding(query)
    if embedding is None:
        logger.warning("search_artifacts: embedding unavailable, returning empty")
        return []

    try:
        con.execute("LOAD vss")
    except Exception:
        pass

    try:
        rows = con.execute(
            f"""
            SELECT aa.artifact_id, aa.analysis_id, aa.artifact_type,
                   aa.artifact_subtype, aa.label, aa.file_path,
                   aa.inline_data, aa.file_size_kb, aa.mime_type,
                   aa.created_at,
                   1 - array_cosine_distance(aa.embedding, ?::FLOAT[1024]) AS score
            FROM   analysis_artifacts aa
            {sample_join}
            WHERE  aa.embedding IS NOT NULL
                   {sample_where}
            ORDER  BY array_cosine_distance(aa.embedding, ?::FLOAT[1024])
            LIMIT  ?
            """,
            [embedding] + sample_params + [embedding, n],
        ).fetchall()
    except Exception as exc:
        logger.warning("search_artifacts: HNSW search failed: %s", exc)
        return []

    return [r for r in _rows_to_dicts(rows, cols) if (r["score"] or 0) >= threshold]
