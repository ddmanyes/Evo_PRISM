"""
ENGRAM-Core — Evidence & iNdexed Graph of Research Artifacts & Memory.

Manages the permanent record of every file produced by an analysis run.
Mirrors the HELIX pattern: register once, query forever, semantic search via HNSW.

Key functions:
    register_artifact()   — log a single output file (auto-embeds, blob split since v14)
    get_artifacts()       — fetch all artifacts for one analysis run
    compare_analyses()    — side-by-side artifact lists for N analysis runs
    artifact_summary()    — 0-token metadata overview for a sample
    search_artifacts()    — Hybrid RRF: exact subtype boost + HNSW cosine (9A-2)
    link_artifacts()      — record directed relation between two artifacts (9B-2)
    get_lineage()         — retrieve provenance chain for an artifact (9B-3)
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb


def _ensure_vss(con: duckdb.DuckDBPyConnection) -> None:
    """Load VSS extension before any CHECKPOINT or HNSW operation. Silently skipped on failure."""
    try:
        con.execute("LOAD vss")
        con.execute("SET hnsw_enable_experimental_persistence = true")
    except Exception:
        pass


import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

INLINE_SIZE_LIMIT_KB = 500

KNOWN_SUBTYPES = frozenset(
    {
        "volcano",
        "pca",
        "heatmap",
        "qc_figure",
        "scatter",
        "deg_list",
        "pathway_scores",
        "timeseries",
        "eda_report",
        "summary_report",
        "qc_csv",
        "counts_csv",
        "run_log",
    }
)

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".log": "text/plain",
}

# RRF smoothing constant (Cormack et al. SIGIR 2009)
_RRF_K = 60

# DuckDB FTS sidecar schema name created by migration v18 (P0-B)
_FTS_SCHEMA_ARTIFACTS = "fts_main_analysis_artifacts"


def _fts_artifacts_available(con: duckdb.DuckDBPyConnection) -> bool:
    """Check whether the FTS BM25 index on analysis_artifacts is present.

    Returns True only when migration v18 has been applied AND the fts
    extension is loadable. Failure modes (extension missing, schema absent)
    are silently caught so search_artifacts() degrades to 2-layer RRF.
    """
    try:
        con.execute("LOAD fts")
    except Exception:
        return False
    try:
        row = con.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?",
            [_FTS_SCHEMA_ARTIFACTS],
        ).fetchone()
        return row is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mime_for(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def _read_inline(path: Path) -> Optional[str]:
    """Return base64-encoded content if file ≤ INLINE_SIZE_LIMIT_KB, else None."""
    try:
        if path.stat().st_size > INLINE_SIZE_LIMIT_KB * 1024:
            return None
        return base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None


def _extract_csv_schema(path: Path) -> str:
    """Return first-row column names for a CSV file (≤ 500 bytes read)."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            header = f.readline(500).strip()
        return header[:200]
    except OSError:
        return ""


def _extract_report_lead(path: Path) -> str:
    """Return first non-empty paragraph of a Markdown/text report (≤ 300 chars)."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:300]
    except OSError:
        pass
    return ""


def _make_embed_text(
    label: str,
    artifact_subtype: Optional[str],
    analysis_type: Optional[str],
    parameters: Optional[str],
    path: Optional[Path] = None,
) -> str:
    """Build rich embed text for semantic indexing (9A-3).

    Enhances base label+subtype with file-content signals:
    - CSV: column schema (header row)
    - report/log: first paragraph
    """
    parts = [label]
    if artifact_subtype:
        parts.append(artifact_subtype)
    if analysis_type:
        parts.append(analysis_type)
    if parameters:
        parts.append(parameters[:200])

    if path is not None and path.exists():
        suffix = path.suffix.lower()
        if suffix in (".csv", ".tsv"):
            schema = _extract_csv_schema(path)
            if schema:
                parts.append(f"columns: {schema}")
        elif suffix in (".md", ".txt", ".log"):
            lead = _extract_report_lead(path)
            if lead:
                parts.append(lead)

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
# Provenance hash helpers (9B-1)
# ---------------------------------------------------------------------------


def _hash_input_data(paths: list[Path]) -> str:
    """SHA256[:16] of sorted (path, mtime) pairs — changes when input data changes."""
    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            stat = p.stat()
            h.update(f"{p}:{stat.st_mtime}:{stat.st_size}".encode())
        except OSError:
            h.update(str(p).encode())
    return h.hexdigest()[:16]


def _hash_function_source(fn) -> str:
    """SHA256[:16] of a callable's source code (ignores whitespace-only changes)."""
    import ast
    import inspect

    try:
        src = inspect.getsource(fn)
        tree = ast.parse(src)
        normalised = ast.dump(tree)
    except Exception:
        normalised = str(fn)
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def _hash_env() -> str:
    """SHA256[:16] of Python version + key package versions + critical env vars."""
    import sys

    parts = [f"python={sys.version}"]
    for pkg in ("duckdb", "numpy", "pandas", "scanpy", "anndata"):
        try:
            parts.append(f"{pkg}={importlib.metadata.version(pkg)}")
        except importlib.metadata.PackageNotFoundError:
            pass
    for var in ("BIO_DB_ROOT", "INFERENCE_BACKEND", "EMBED_PROVIDER"):
        val = os.environ.get(var, "")
        if val:
            parts.append(f"{var}={val}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _provenance_col_exists(con: duckdb.DuckDBPyConnection) -> bool:
    """Return True if migration v16 has added provenance columns."""
    row = con.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'analysis_artifacts' "
        "  AND column_name = 'input_data_hash' "
        "  AND table_schema = 'main'"
    ).fetchone()
    return row is not None


def _matryoshka_col_exists(con: duckdb.DuckDBPyConnection) -> bool:
    """Return True if migration v17 has added the embedding_256 column."""
    row = con.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'analysis_artifacts' "
        "  AND column_name = 'embedding_256' "
        "  AND table_schema = 'main'"
    ).fetchone()
    return row is not None


def _relations_table_exists(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'artifact_relations' AND table_schema = 'main'"
    ).fetchone()
    return row is not None


def _blob_table_exists(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'analysis_artifact_blobs' AND table_schema = 'main'"
    ).fetchone()
    return row is not None


def _metrics_table_exists(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'engram_search_metrics' AND table_schema = 'main'"
    ).fetchone()
    return row is not None


def _record_search_metric(
    con: duckdb.DuckDBPyConnection,
    query: str,
    returned_n: int,
    latency_ms: int,
    search_layer: str,
    threshold: Optional[float],
    sample_id: Optional[str],
) -> None:
    if not _metrics_table_exists(con):
        return
    try:
        con.execute(
            """
            INSERT INTO engram_search_metrics
                (query, returned_n, latency_ms, search_layer, threshold, sample_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [query, returned_n, latency_ms, search_layer, threshold, sample_id],
        )
    except Exception as exc:
        logger.warning("artifact_registry: failed to record search metric: %s", exc)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def _insert_artifact_row(
    con: duckdb.DuckDBPyConnection,
    artifact_id: str,
    analysis_id: str,
    artifact_type: str,
    artifact_subtype: Optional[str],
    label: str,
    rel_path: str,
    size_kb: Optional[int],
    mime_type: str,
    embedding,
    now: datetime,
    *,
    use_matryoshka: bool = False,
    use_provenance: bool = False,
    embedding_256=None,
    input_data_hash: Optional[str] = None,
    code_hash: Optional[str] = None,
    env_hash: Optional[str] = None,
) -> None:
    cols = [
        "artifact_id", "analysis_id", "artifact_type", "artifact_subtype",
        "label", "file_path", "file_size_kb", "mime_type", "embedding",
    ]
    vals: list = [
        artifact_id, analysis_id, artifact_type, artifact_subtype,
        label, rel_path, size_kb, mime_type, embedding,
    ]
    if use_matryoshka:
        cols.append("embedding_256")
        vals.append(embedding_256)
    if use_provenance:
        cols += ["input_data_hash", "code_hash", "env_hash"]
        vals += [input_data_hash, code_hash, env_hash]
    cols.append("created_at")
    vals.append(now)
    placeholders = ", ".join("?" * len(vals))
    con.execute(f"INSERT INTO analysis_artifacts ({', '.join(cols)}) VALUES ({placeholders})", vals)


def register_artifact(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
    file_path: "Path | str",
    artifact_type: str,
    label: str,
    *,
    artifact_subtype: Optional[str] = None,
    input_paths: Optional[list[Path]] = None,
    producing_fn=None,
) -> str:
    """Register a single output file produced by an analysis run.

    Since migration v14, inline_data is stored in analysis_artifact_blobs (1:0..1).
    file_path is stored relative to BIO_DB_ROOT (migration v12).
    Since migration v16, provenance hashes are recorded when available (9B-1).

    Args:
        input_paths:  List of input data files used to produce this artifact.
                      Used to compute input_data_hash. If None, hash is skipped.
        producing_fn: The Python callable that generated this artifact.
                      Used to compute code_hash. If None, hash is skipped.

    Returns:
        UUID string of the new artifact_id.
    """
    from config.settings import BIO_DB_ROOT

    path = Path(file_path)
    artifact_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    mime_type = _mime_for(path)

    size_kb: Optional[int] = None
    inline_data: Optional[str] = None

    if path.exists():
        try:
            raw_bytes = path.read_bytes()
            size_kb = len(raw_bytes) // 1024
            
            # Check if base64 encoded size exceeds DuckDB constraint (500KB / 512,000 bytes)
            temp_inline = base64.b64encode(raw_bytes).decode()
            if len(temp_inline) > 512000:
                # Spill-to-disk guard
                overflow_dir = BIO_DB_ROOT / "results" / "overflow"
                overflow_dir.mkdir(parents=True, exist_ok=True)
                
                ext = path.suffix or ".txt"
                filename = f"overflow_{artifact_id}{ext}"
                overflow_path = overflow_dir / filename
                overflow_path.write_bytes(raw_bytes)
                
                path = overflow_path
                size_kb = int(overflow_path.stat().st_size / 1024)
                inline_data = None
                logger.info("register_artifact: Spilled oversized artifact %s to %s due to size limit", file_path, overflow_path)
            else:
                inline_data = temp_inline
        except OSError as exc:
            logger.warning("register_artifact: failed to read file bytes: %s", exc)
            size_kb = path.stat().st_size // 1024
            inline_data = None
    else:
        logger.warning("register_artifact: file not found: %s", path)

    # Store relative path (migration v12)
    try:
        rel_path = str(path.relative_to(BIO_DB_ROOT))
    except ValueError:
        rel_path = str(path)
        logger.warning(
            "register_artifact: %s is outside BIO_DB_ROOT %s — "
            "stored as absolute path, portability will break on server deployment",
            path,
            BIO_DB_ROOT,
        )

    analysis_type, parameters = _get_analysis_context(con, analysis_id)
    embed_text_str = _make_embed_text(label, artifact_subtype, analysis_type, parameters, path)
    embedding = _get_embedding(embed_text_str)

    # Provenance hashes (migration v16 — silently skipped on older schema)
    input_data_hash: Optional[str] = None
    code_hash: Optional[str] = None
    env_hash: Optional[str] = None
    if _provenance_col_exists(con):
        if input_paths is not None:
            input_data_hash = _hash_input_data(input_paths)
        if producing_fn is not None:
            code_hash = _hash_function_source(producing_fn)
        env_hash = _hash_env()

    # Matryoshka 256-dim sub-vector (migration v17, 9D-1)
    embedding_256: Optional[list[float]] = None
    use_matryoshka = _matryoshka_col_exists(con)
    if use_matryoshka and embedding is not None:
        embedding_256 = embedding[:256]

    use_provenance = _provenance_col_exists(con)

    # INSERT into analysis_artifacts updates the HNSW index — VSS must be loaded first.
    _ensure_vss(con)
    _insert_artifact_row(
        con, artifact_id, analysis_id, artifact_type, artifact_subtype,
        label, rel_path, size_kb, mime_type, embedding, now,
        use_matryoshka=use_matryoshka,
        use_provenance=use_provenance,
        embedding_256=embedding_256,
        input_data_hash=input_data_hash,
        code_hash=code_hash,
        env_hash=env_hash,
    )

    # Write blob to separate table if migration v14 has been applied
    if inline_data and _blob_table_exists(con):
        try:
            con.execute(
                """
                INSERT INTO analysis_artifact_blobs (artifact_id, inline_data)
                VALUES (?, ?)
                ON CONFLICT (artifact_id) DO NOTHING
                """,
                [artifact_id, inline_data],
            )
        except Exception as exc:
            logger.warning("register_artifact: blob insert failed: %s", exc)

    # CHECKPOINT after every write — ExFAT has no journal (CLAUDE.md §6)
    # Must LOAD vss first; analysis_artifacts has an HNSW index.
    try:
        _ensure_vss(con)
        con.execute("CHECKPOINT")
    except Exception as exc:
        logger.warning("register_artifact: CHECKPOINT failed: %s", exc)

    logger.info(
        "register_artifact: %s  type=%s/%s  size=%s KB  blob=%s",
        path.name,
        artifact_type,
        artifact_subtype or "-",
        size_kb,
        inline_data is not None,
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

    Joins analysis_artifact_blobs for inline_data when include_inline=True
    and blob table exists (migration v14+). Falls back to inline_data column
    on older schemas.
    """
    params: list = [analysis_id]
    where = ["aa.analysis_id = ?"]

    if artifact_type:
        where.append("aa.artifact_type = ?")
        params.append(artifact_type)
    if artifact_subtype:
        where.append("aa.artifact_subtype = ?")
        params.append(artifact_subtype)

    use_blob_table = include_inline and _blob_table_exists(con)
    inline_col = "b.inline_data" if use_blob_table else "NULL AS inline_data"
    blob_join = (
        "LEFT JOIN analysis_artifact_blobs b ON aa.artifact_id = b.artifact_id"
        if use_blob_table
        else ""
    )

    rows = con.execute(
        f"""
        SELECT aa.artifact_id, aa.analysis_id, aa.artifact_type, aa.artifact_subtype,
               aa.label, aa.file_path, {inline_col}, aa.file_size_kb,
               aa.mime_type, aa.created_at
        FROM   analysis_artifacts aa
        {blob_join}
        WHERE  {" AND ".join(where)}
        ORDER  BY aa.created_at
        """,
        params,
    ).fetchall()

    cols = [
        "artifact_id",
        "analysis_id",
        "artifact_type",
        "artifact_subtype",
        "label",
        "file_path",
        "inline_data",
        "file_size_kb",
        "mime_type",
        "created_at",
    ]
    return _rows_to_dicts(rows, cols)


def compare_analyses(
    con: duckdb.DuckDBPyConnection,
    analysis_ids: list[str],
    *,
    artifact_subtype: Optional[str] = None,
    include_inline: bool = True,
) -> dict[str, list[dict]]:
    """Return artifacts grouped by analysis_id for side-by-side comparison."""
    if not analysis_ids:
        return {}

    use_blob_table = include_inline and _blob_table_exists(con)
    inline_col = "b.inline_data" if use_blob_table else "NULL AS inline_data"
    blob_join = (
        "LEFT JOIN analysis_artifact_blobs b ON aa.artifact_id = b.artifact_id"
        if use_blob_table
        else ""
    )

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
        {blob_join}
        WHERE  aa.analysis_id IN ({placeholders})
               {subtype_clause}
        ORDER  BY aa.analysis_id, aa.created_at
        """,
        params,
    ).fetchall()

    result: dict[str, list[dict]] = {aid: [] for aid in analysis_ids}
    for r in rows:
        aid = str(r[1])
        result[aid].append(
            {
                "artifact_id": str(r[0]),
                "analysis_id": aid,
                "artifact_type": r[2],
                "artifact_subtype": r[3],
                "label": r[4],
                "file_path": r[5],
                "inline_data": r[6],
                "file_size_kb": r[7],
                "mime_type": r[8],
                "created_at": str(r[9]),
                "analysis_type": r[10],
                "parameters": r[11],
                "completed_at": str(r[12]) if r[12] else None,
                "tool_version": r[13],
                "tool_status": r[14],
            }
        )
    return result


def artifact_summary(
    con: duckdb.DuckDBPyConnection,
    sample_id: str,
) -> dict:
    """Return a 0-token metadata overview for all analyses of a sample."""
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
        ORDER  BY ah.completed_at DESC NULLS LAST
        """,
        [sample_id],
    ).fetchall()

    if not rows:
        return {
            "sample_id": sample_id,
            "total_runs": 0,
            "total_artifacts": 0,
            "by_subtype": {},
            "latest_run": None,
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
        "sample_id": sample_id,
        "total_runs": len(rows),
        "total_artifacts": total_artifacts,
        "by_subtype": subtype_counts,
        "latest_run": {
            "analysis_id": str(latest[0]),
            "analysis_type": latest[1],
            "completed_at": str(latest[2]) if latest[2] else None,
            "artifact_count": latest[3],
        },
    }


# ── search_artifacts helpers ─────────────────────────────────────────────────

_ARTIFACT_COLS = [
    "artifact_id", "analysis_id", "artifact_type", "artifact_subtype",
    "label", "file_path", "file_size_kb", "mime_type", "created_at",
]


def _build_sample_filter(sample_id: Optional[str]) -> tuple[str, str, list]:
    if sample_id:
        return (
            "JOIN analysis_history ah ON aa.analysis_id = ah.analysis_id",
            "AND ah.sample_id = ?",
            [sample_id],
        )
    return "", "", []


def _layer1_exact_search(
    con: duckdb.DuckDBPyConnection,
    artifact_subtype: Optional[str],
    sample_join: str,
    sample_where: str,
    sample_params: list,
    n: int,
) -> tuple[dict[str, int], dict[str, dict]]:
    ranked: dict[str, int] = {}
    rows_map: dict[str, dict] = {}
    if not artifact_subtype:
        return ranked, rows_map
    rows = con.execute(
        f"""
        SELECT aa.artifact_id::VARCHAR, aa.analysis_id::VARCHAR,
               aa.artifact_type, aa.artifact_subtype, aa.label, aa.file_path,
               aa.file_size_kb, aa.mime_type, aa.created_at
        FROM   analysis_artifacts aa
        {sample_join}
        WHERE  aa.artifact_subtype = ?
               {sample_where}
        ORDER  BY aa.created_at DESC
        LIMIT  ?
        """,
        [artifact_subtype] + sample_params + [n * 2],
    ).fetchall()
    for rank, row in enumerate(rows, start=1):
        aid = str(row[0])
        ranked[aid] = rank
        rows_map[aid] = dict(zip(_ARTIFACT_COLS, row))
    return ranked, rows_map


def _hnsw_rows_standard(
    con: duckdb.DuckDBPyConnection,
    embedding: list,
    sample_join: str,
    sample_where: str,
    sample_params: list,
    n: int,
) -> list:
    return con.execute(
        f"""
        SELECT aa.artifact_id::VARCHAR, aa.analysis_id::VARCHAR,
               aa.artifact_type, aa.artifact_subtype, aa.label, aa.file_path,
               aa.file_size_kb, aa.mime_type, aa.created_at
        FROM   analysis_artifacts aa
        {sample_join}
        WHERE  aa.embedding IS NOT NULL
               {sample_where}
        ORDER  BY array_cosine_distance(aa.embedding, ?::FLOAT[1024])
        LIMIT  ?
        """,
        sample_params + [embedding, n * 2],
    ).fetchall()


def _hnsw_rows_matryoshka(
    con: duckdb.DuckDBPyConnection,
    embedding: list,
    sample_join: str,
    sample_where: str,
    sample_params: list,
    n: int,
) -> list:
    coarse_rows = con.execute(
        f"""
        SELECT aa.artifact_id::VARCHAR
        FROM   analysis_artifacts aa
        {sample_join}
        WHERE  aa.embedding_256 IS NOT NULL
               {sample_where}
        ORDER  BY array_cosine_distance(aa.embedding_256, ?::FLOAT[256])
        LIMIT  ?
        """,
        sample_params + [embedding[:256], n * 10],
    ).fetchall()
    coarse_ids = [str(r[0]) for r in coarse_rows]
    if not coarse_ids:
        return []
    id_placeholders = ", ".join("?" * len(coarse_ids))
    return con.execute(
        f"""
        SELECT aa.artifact_id::VARCHAR, aa.analysis_id::VARCHAR,
               aa.artifact_type, aa.artifact_subtype, aa.label,
               aa.file_path, aa.file_size_kb, aa.mime_type, aa.created_at
        FROM   analysis_artifacts aa
        WHERE  aa.artifact_id::VARCHAR IN ({id_placeholders})
          AND  aa.embedding IS NOT NULL
        ORDER  BY array_cosine_distance(aa.embedding, ?::FLOAT[1024])
        LIMIT  ?
        """,
        coarse_ids + [embedding, n * 2],
    ).fetchall()


def _layer2_hnsw_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    sample_join: str,
    sample_where: str,
    sample_params: list,
    n: int,
) -> tuple[dict[str, int], dict[str, dict]]:
    ranked: dict[str, int] = {}
    rows_map: dict[str, dict] = {}
    embedding = _get_embedding(query)
    if embedding is None:
        return ranked, rows_map
    try:
        con.execute("LOAD vss")
    except Exception:
        pass
    from config.settings import MATRYOSHKA_ENABLED
    use_matryoshka = MATRYOSHKA_ENABLED and _matryoshka_col_exists(con) and len(embedding) >= 256
    try:
        rows = (
            _hnsw_rows_matryoshka(con, embedding, sample_join, sample_where, sample_params, n)
            if use_matryoshka
            else _hnsw_rows_standard(con, embedding, sample_join, sample_where, sample_params, n)
        )
        for rank, row in enumerate(rows, start=1):
            aid = str(row[0])
            ranked[aid] = rank
            rows_map[aid] = dict(zip(_ARTIFACT_COLS, row))
    except Exception as exc:
        logger.warning("search_artifacts: HNSW search failed: %s", exc)
    return ranked, rows_map


def _layer3_fts_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    sample_join: str,
    sample_where: str,
    sample_params: list,
    n: int,
) -> tuple[dict[str, int], dict[str, dict]]:
    ranked: dict[str, int] = {}
    rows_map: dict[str, dict] = {}
    if not _fts_artifacts_available(con):
        return ranked, rows_map
    try:
        fts_sql = f"""
            SELECT aa.artifact_id::VARCHAR, aa.analysis_id::VARCHAR,
                   aa.artifact_type, aa.artifact_subtype, aa.label, aa.file_path,
                   aa.file_size_kb, aa.mime_type, aa.created_at,
                   {_FTS_SCHEMA_ARTIFACTS}.match_bm25(aa.artifact_id, ?) AS bm25
            FROM   analysis_artifacts aa
            {sample_join}
            WHERE  {_FTS_SCHEMA_ARTIFACTS}.match_bm25(aa.artifact_id, ?) IS NOT NULL
                   {sample_where}
            ORDER  BY bm25 DESC
            LIMIT  ?
        """
        rows = con.execute(fts_sql, [query, query] + sample_params + [n * 2]).fetchall()
        for rank, row in enumerate(rows, start=1):
            aid = str(row[0])
            ranked[aid] = rank
            rows_map[aid] = dict(zip(_ARTIFACT_COLS, row[: len(_ARTIFACT_COLS)]))
    except Exception as exc:
        logger.warning("search_artifacts: FTS layer failed: %s", exc)
    return ranked, rows_map


def _rrf_fuse(
    exact_ranked: dict[str, int],
    hnsw_ranked: dict[str, int],
    fts_ranked: dict[str, int],
    threshold: float,
    n: int,
) -> list[tuple[float, str]]:
    all_ids = set(exact_ranked) | set(hnsw_ranked) | set(fts_ranked)
    scored = []
    for aid in all_ids:
        r_e = exact_ranked.get(aid)
        r_h = hnsw_ranked.get(aid)
        r_f = fts_ranked.get(aid)
        rrf = (
            (1 / (_RRF_K + r_e) if r_e else 0.0)
            + (1 / (_RRF_K + r_h) if r_h else 0.0)
            + (1 / (_RRF_K + r_f) if r_f else 0.0)
        )
        scored.append((rrf, aid))
    scored.sort(reverse=True)
    return [(s, aid) for s, aid in scored if s >= threshold][:n]


def _build_artifact_results(
    top: list[tuple[float, str]],
    exact_rows_map: dict[str, dict],
    hnsw_rows_map: dict[str, dict],
    fts_rows_map: dict[str, dict],
    exact_ranked: dict[str, int],
    hnsw_ranked: dict[str, int],
    fts_ranked: dict[str, int],
) -> tuple[list[dict], str]:
    contributed = []
    if any(aid in exact_ranked for _, aid in top):
        contributed.append("exact")
    if any(aid in hnsw_ranked for _, aid in top):
        contributed.append("hnsw")
    if any(aid in fts_ranked for _, aid in top):
        contributed.append("fts")
    batch_layer = "rrf" if len(contributed) > 1 else (contributed[0] if contributed else "none")
    results = []
    for rrf_score, aid in top:
        row_dict = exact_rows_map.get(aid) or hnsw_rows_map.get(aid) or fts_rows_map.get(aid, {})
        row_dict["score"] = round(rrf_score, 6)
        in_e = aid in exact_ranked
        in_h = aid in hnsw_ranked
        in_f = aid in fts_ranked
        per_item = [t for t, f in (("exact", in_e), ("hnsw", in_h), ("fts", in_f)) if f]
        row_dict["search_layer"] = "rrf" if len(per_item) > 1 else per_item[0]
        results.append(row_dict)
    return results, batch_layer


def search_artifacts(
    con: duckdb.DuckDBPyConnection,
    query: str,
    *,
    n: int = 5,
    threshold: float = 0.01,
    artifact_subtype: Optional[str] = None,
    sample_id: Optional[str] = None,
) -> list[dict]:
    """Hybrid RRF search across artifact embeddings (9A-2 + P0-B FTS).

    Combines up to three retrieval layers via Reciprocal Rank Fusion (k=60):
      Layer 1 — exact match on artifact_subtype (SQL)
      Layer 2 — HNSW cosine vector search (semantic)
      Layer 3 — DuckDB FTS BM25 over label + subtype + type (keyword, P0-B)

    Layer 3 activates automatically when migration v18 has run (the
    fts_main_analysis_artifacts schema exists). It is silently skipped
    otherwise, preserving backward compatibility.

    RRF score range: ~0.008 (single layer, rank N) to ~0.050 (all layers rank 1).
    threshold default 0.01 filters results ranking poorly in all layers.

    Args:
        con:              Open DuckDB connection (VSS loaded internally).
        query:            Natural language query.
        n:                Max results to return.
        threshold:        Minimum RRF score (range ~0.008–0.033). Default 0.01.
        artifact_subtype: If provided, used as Layer 1 exact match.
        sample_id:        Restrict search to a specific sample.

    Returns:
        List of artifact dicts with 'score' (RRF) and 'search_layer' fields.
    """
    t_start = time.monotonic()
    sample_join, sample_where, sample_params = _build_sample_filter(sample_id)

    exact_ranked, exact_rows_map = _layer1_exact_search(
        con, artifact_subtype, sample_join, sample_where, sample_params, n
    )
    hnsw_ranked, hnsw_rows_map = _layer2_hnsw_search(
        con, query, sample_join, sample_where, sample_params, n
    )
    fts_ranked, fts_rows_map = _layer3_fts_search(
        con, query, sample_join, sample_where, sample_params, n
    )

    top = _rrf_fuse(exact_ranked, hnsw_ranked, fts_ranked, threshold, n)
    if not top:
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _record_search_metric(con, query, 0, latency_ms, "none", threshold, sample_id)
        return []

    results, batch_layer = _build_artifact_results(
        top, exact_rows_map, hnsw_rows_map, fts_rows_map,
        exact_ranked, hnsw_ranked, fts_ranked,
    )

    latency_ms = int((time.monotonic() - t_start) * 1000)
    _record_search_metric(con, query, len(results), latency_ms, batch_layer, threshold, sample_id)
    return results


# ---------------------------------------------------------------------------
# Provenance & Lineage API (9B-2 / 9B-3)
# ---------------------------------------------------------------------------

VALID_RELATION_TYPES = frozenset(
    {
        "derived_from",
        "used_by",
        "compared_with",
    }
)


def link_artifacts(
    con: duckdb.DuckDBPyConnection,
    src_artifact_id: str,
    dst_artifact_id: str,
    relation_type: str = "derived_from",
) -> Optional[str]:
    """Record a directed relation between two artifacts (9B-2).

    Example: link PCA artifact → DEG CSV that was derived from it.

    Args:
        src_artifact_id: The source (upstream) artifact.
        dst_artifact_id: The destination (downstream) artifact.
        relation_type:   One of 'derived_from' | 'used_by' | 'compared_with'.

    Returns:
        UUID of the new relation_id, or None if artifact_relations table absent.
    """
    if not _relations_table_exists(con):
        logger.warning("link_artifacts: artifact_relations table not found — run migration v16")
        return None

    if relation_type not in VALID_RELATION_TYPES:
        raise ValueError(
            f"relation_type must be one of {VALID_RELATION_TYPES}, got {relation_type!r}"
        )

    relation_id = str(uuid.uuid4())
    con.execute(
        """
        INSERT INTO artifact_relations
            (relation_id, src_artifact_id, dst_artifact_id, relation_type)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (src_artifact_id, dst_artifact_id, relation_type) DO NOTHING
        """,
        [relation_id, src_artifact_id, dst_artifact_id, relation_type],
    )
    try:
        con.execute("CHECKPOINT")
    except Exception as exc:
        logger.warning("link_artifacts: CHECKPOINT failed: %s", exc)
    return relation_id


def get_lineage(
    con: duckdb.DuckDBPyConnection,
    artifact_id: str,
    *,
    direction: str = "upstream",
) -> list[dict]:
    """Retrieve the provenance chain for an artifact (9B-3).

    Uses tool_artifact_lineage view when available, otherwise falls back to
    artifact_relations + analysis_artifacts direct query.

    Args:
        artifact_id: The artifact to trace.
        direction:   'upstream' (what produced this) or 'downstream' (what uses this).

    Returns:
        List of relation dicts with artifact metadata and provenance hashes.
    """
    if not _relations_table_exists(con):
        return []

    if direction == "upstream":
        # src → dst means dst was derived from src; to find upstream of target,
        # look for relations where dst_artifact_id = target
        join_col = "dst_artifact_id"
        other_col = "src_artifact_id"
    else:
        join_col = "src_artifact_id"
        other_col = "dst_artifact_id"

    has_provenance = _provenance_col_exists(con)
    provenance_cols = (
        "aa.input_data_hash, aa.code_hash, aa.env_hash,"
        if has_provenance
        else "NULL AS input_data_hash, NULL AS code_hash, NULL AS env_hash,"
    )

    rows = con.execute(
        f"""
        SELECT
            r.relation_id, r.relation_type,
            aa.artifact_id, aa.label, aa.artifact_subtype,
            aa.artifact_type, aa.file_path, aa.created_at,
            ah.analysis_id, ah.analysis_type, ah.sample_id,
            {provenance_cols}
            t.tool_name, t.version AS tool_version
        FROM   artifact_relations r
        JOIN   analysis_artifacts aa ON aa.artifact_id = r.{other_col}
        JOIN   analysis_history   ah ON aa.analysis_id = ah.analysis_id
        LEFT JOIN tools           t  ON ah.tool_id = t.tool_id
        WHERE  r.{join_col} = ?
        ORDER  BY aa.created_at
        """,
        [artifact_id],
    ).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "relation_id": str(row[0]),
                "relation_type": row[1],
                "artifact_id": str(row[2]),
                "label": row[3],
                "artifact_subtype": row[4],
                "artifact_type": row[5],
                "file_path": row[6],
                "created_at": str(row[7]),
                "analysis_id": str(row[8]),
                "analysis_type": row[9],
                "sample_id": row[10],
                "input_data_hash": row[11],
                "code_hash": row[12],
                "env_hash": row[13],
                "tool_name": row[14],
                "tool_version": row[15],
            }
        )
    return result
