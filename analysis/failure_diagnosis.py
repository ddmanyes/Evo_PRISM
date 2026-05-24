"""Lightweight rule-based failure diagnosis for analysis_history (PM1, Phase 13).

Inspired by EvolveMem [arXiv:2605.13941] per-question failure logging:
each analysis run records its failure category (or "success"), enabling
bio_failure_summary (PM1-C) to aggregate root causes for HELIX/ENGRAM self-evolution.

Usage in analysis modules:

    from analysis.failure_diagnosis import classify_exception, success_diagnosis, write_diagnosis
    ...
    except Exception as _exc:
        ...
        write_diagnosis(con, analysis_id, classify_exception(_exc))
        raise
    ...
    # after status='completed' update:
    write_diagnosis(con, analysis_id, success_diagnosis())

All functions are best-effort and non-blocking; any write failure is silently ignored.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

# Rule keywords for each failure category
_CACHE_KEYWORDS    = ("embedding", "hnsw", "vector", "cosine", "cache miss", "l1 cache", "similarity")
_L3_KEYWORDS       = ("l3", "not ready", "l3_not_ready", "parquet not found", "h5ad",
                      "file not found", "no such file", "filenotfounderror", "does not exist")
_TOOL_KEYWORDS     = ("tool", "version", "deprecated", "module", "importerror",
                      "has no attribute", "attributeerror", "no module named")
_HALLUCINATION_KWS = ("hallucin", "confabul", "fabricat")


def classify_exception(exc: BaseException) -> dict:
    """Map an exception to a failure_diagnosis dict.

    Returns dict with keys: type, detail (≤ 200 chars).
    The caller should NOT set 'diagnosed_at'; write_diagnosis() adds it.

    type values:
      cache_miss_semantic  — HNSW / embedding / similarity failure
      L3_not_ready         — raw data file missing or not yet converted
      wrong_tool_version   — import error, deprecated API, attribute missing
      hallucination        — model-generated content that is provably wrong
      insufficient_context — catch-all for other runtime failures
    """
    msg = str(exc).lower()
    if any(k in msg for k in _CACHE_KEYWORDS):
        return {"type": "cache_miss_semantic", "detail": str(exc)[:200]}
    if any(k in msg for k in _L3_KEYWORDS):
        return {"type": "L3_not_ready", "detail": str(exc)[:200]}
    if any(k in msg for k in _TOOL_KEYWORDS):
        return {"type": "wrong_tool_version", "detail": str(exc)[:200]}
    if any(k in msg for k in _HALLUCINATION_KWS):
        return {"type": "hallucination", "detail": str(exc)[:200]}
    return {"type": "insufficient_context", "detail": str(exc)[:200]}


def success_diagnosis() -> dict:
    """Return a success diagnosis dict (no timestamp; write_diagnosis() adds it)."""
    return {"type": "success", "detail": ""}


def write_diagnosis(
    con: "duckdb.DuckDBPyConnection",
    analysis_id: str,
    diagnosis: dict,
) -> None:
    """Write failure_diagnosis JSON to analysis_history (best-effort, non-blocking).

    Uses the existing open connection `con` to avoid extra round-trips.
    Silently ignores any error (e.g., column not yet migrated on older DB).
    """
    try:
        diagnosis.setdefault("diagnosed_at", datetime.now(timezone.utc).isoformat())
        con.execute(
            "UPDATE analysis_history SET failure_diagnosis = ? WHERE analysis_id = ?",
            [json.dumps(diagnosis, ensure_ascii=False), analysis_id],
        )
    except Exception:
        pass
