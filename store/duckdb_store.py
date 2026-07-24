"""DuckDB implementation of RegistryStore.

Wraps bio_memory.duckdb exactly as the pre-store code did — behaviour is
unchanged.  Every write calls CHECKPOINT (ExFAT guard, same as safe_write).
VSS is loaded on every connection (HNSW persistence).
"""
from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Iterator

import duckdb

from store.base import _HISTORY_MUTABLE_COLS, _SAMPLE_MUTABLE_COLS, _validate_cols

logger = logging.getLogger(__name__)


def _bootstrap(con: duckdb.DuckDBPyConnection, *, read_only: bool = False) -> None:
    try:
        con.execute("LOAD vss")
    except Exception:
        return
    if not read_only:
        try:
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception:
            pass


class DuckDBStore:
    """RegistryStore backed by a local DuckDB file."""

    def __init__(self, db_path: str | None = None) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        if db_path:
            from pathlib import Path as P
            self._path = str(P(db_path))
        else:
            from config.settings import DUCKDB_PATH
            self._path = str(DUCKDB_PATH)

    # ------------------------------------------------------------------
    # Low-level escape hatches
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def write_conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._path)
        _bootstrap(con)
        try:
            yield con
        finally:
            try:
                con.execute("CHECKPOINT")
            except Exception:
                pass
            con.close()

    @contextlib.contextmanager
    def read_conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._path, read_only=True)
        _bootstrap(con, read_only=True)
        try:
            yield con
        finally:
            con.close()

    # ------------------------------------------------------------------
    # analysis_history
    # ------------------------------------------------------------------

    def insert_history(
        self,
        analysis_id: str,
        sample_id: str,
        analysis_type: str,
        params_json: str,
        status: str,
        requested_by: str,
        started_at: datetime,
        *,
        tool_id: str | None = None,
        analysis_version: str | None = None,
        tool_version: str | None = None,
        parameter_hash: str | None = None,
    ) -> None:
        with self.write_conn() as con:
            con.execute(
                """
                INSERT INTO analysis_history
                    (analysis_id, sample_id, analysis_type, parameters, status,
                     requested_by, started_at, tool_id, analysis_version,
                     tool_version, parameter_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    analysis_id, sample_id, analysis_type, params_json, status,
                    requested_by, started_at, tool_id, analysis_version,
                    tool_version, parameter_hash,
                ],
            )

    def complete_history(
        self,
        analysis_id: str,
        result_path: str,
        summary: str,
        completed_at: datetime,
        *,
        summary_metrics: dict | None = None,
    ) -> None:
        metrics_json = json.dumps(summary_metrics) if summary_metrics else None
        with self.write_conn() as con:
            con.execute(
                """
                UPDATE analysis_history
                SET status = 'completed',
                    result_path = ?,
                    completed_at = ?,
                    summary = ?,
                    summary_metrics = ?
                WHERE analysis_id = ?
                """,
                [result_path, completed_at, summary, metrics_json, analysis_id],
            )

    def fail_history(
        self,
        analysis_id: str,
        completed_at: datetime,
        *,
        failure_diagnosis: str | None = None,
    ) -> None:
        with self.write_conn() as con:
            con.execute(
                """
                UPDATE analysis_history
                SET status = 'failed',
                    completed_at = ?,
                    failure_diagnosis = ?
                WHERE analysis_id = ?
                """,
                [completed_at, failure_diagnosis, analysis_id],
            )

    def update_history(self, analysis_id: str, **kwargs) -> None:
        if not kwargs:
            return
        _validate_cols(set(kwargs), _HISTORY_MUTABLE_COLS, "update_history")
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [analysis_id]
        with self.write_conn() as con:
            con.execute(
                f"UPDATE analysis_history SET {cols} WHERE analysis_id = ?",
                vals,
            )

    def get_history(
        self,
        sample_id: str,
        analysis_type: str,
        status: str = "completed",
    ) -> dict | None:
        with self.read_conn() as con:
            row = con.execute(
                """
                SELECT analysis_id, completed_at, result_path, summary, parameters
                FROM   analysis_history
                WHERE  sample_id = ? AND analysis_type = ? AND status = ?
                ORDER  BY completed_at DESC LIMIT 1
                """,
                [sample_id, analysis_type, status],
            ).fetchone()
        if not row:
            return None
        return {
            "analysis_id": str(row[0]),
            "completed_at": row[1],
            "result_path": row[2],
            "summary": row[3],
            "parameters": row[4],
        }

    def list_history(
        self,
        *,
        sample_id: str | None = None,
        analysis_type: str | None = None,
        n_days: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if sample_id:
            clauses.append("sample_id = ?")
            params.append(sample_id)
        if analysis_type:
            clauses.append("analysis_type = ?")
            params.append(analysis_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if n_days:
            clauses.append("completed_at >= now() - (? * INTERVAL '1 day')")
            params.append(n_days)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.read_conn() as con:
            rows = con.execute(
                f"""
                SELECT analysis_id, sample_id, analysis_type, status,
                       requested_by, started_at, completed_at, summary,
                       result_path, parameters
                FROM   analysis_history
                {where}
                ORDER  BY completed_at DESC NULLS LAST
                LIMIT  {int(limit)}
                """,
                params,
            ).fetchall()
        cols = [
            "analysis_id", "sample_id", "analysis_type", "status",
            "requested_by", "started_at", "completed_at", "summary",
            "result_path", "parameters",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def cleanup_stale_runs(self, hours: int = 24) -> int:
        hours = int(hours)
        with self.write_conn() as con:
            rows = con.execute(
                """
                UPDATE analysis_history
                SET    status = 'stale'
                WHERE  status  = 'running'
                  AND  started_at < now() - (? * INTERVAL '1 hour')
                RETURNING analysis_id
                """,
                [hours],
            ).fetchall()
        cleaned = len(rows)
        if cleaned:
            logger.info("[store] cleaned %d stale running record(s)", cleaned)
        return cleaned

    def get_canonical_id(self, sample_id: str, analysis_type: str) -> str | None:
        with self.read_conn() as con:
            row = con.execute(
                """
                SELECT analysis_id FROM analysis_history
                WHERE sample_id = ? AND analysis_type = ?
                  AND list_contains(COALESCE(tags, []), 'canonical')
                ORDER BY completed_at DESC LIMIT 1
                """,
                [sample_id, analysis_type],
            ).fetchone()
        return str(row[0]) if row else None

    # 一列同時只帶一個 canonical/superseded 標記：先濾掉舊的再 append 新的
    _RETAG_ROW = """
        UPDATE analysis_history
        SET tags = list_append(
            list_filter(COALESCE(tags, []), t -> t NOT IN ('canonical', 'superseded')),
            ?
        )
        WHERE analysis_id = ?
    """

    def mark_canonical(
        self, analysis_id: str, sample_id: str, analysis_type: str
    ) -> None:
        with self.write_conn() as con:
            con.execute(
                """
                UPDATE analysis_history
                SET tags = list_append(
                    list_filter(COALESCE(tags, []), t -> t NOT IN ('canonical', 'superseded')),
                    'superseded'
                )
                WHERE sample_id = ? AND analysis_type = ?
                  AND list_contains(COALESCE(tags, []), 'canonical')
                  AND analysis_id != ?
                """,
                [sample_id, analysis_type, analysis_id],
            )
            con.execute(self._RETAG_ROW, ["canonical", analysis_id])

    def supersede_by_id(
        self, analysis_id: str, superseded_analysis_id: str
    ) -> None:
        with self.write_conn() as con:
            con.execute(self._RETAG_ROW, ["superseded", superseded_analysis_id])
            con.execute(self._RETAG_ROW, ["canonical", analysis_id])

    # ------------------------------------------------------------------
    # sample_registry
    # ------------------------------------------------------------------

    def register_sample(
        self,
        sample_id: str,
        project: str,
        data_type: str,
        platform: str,
        species: str,
        tissue: str,
        l3_path: str,
        added_by: str,
        notes: str,
        **kwargs,
    ) -> None:
        with self.write_conn() as con:
            existing = con.execute(
                "SELECT 1 FROM sample_registry WHERE sample_id = ?", [sample_id]
            ).fetchone()
            if existing:
                return
            last_updated = kwargs.get("last_updated", datetime.now(timezone.utc))
            con.execute(
                """
                INSERT INTO sample_registry
                    (sample_id, project, data_type, platform, species, tissue,
                     l3_path, l2_ready, analysis_done, added_by, notes, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, false, false, ?, ?, ?)
                """,
                [
                    sample_id, project, data_type, platform, species, tissue,
                    l3_path, added_by, notes, last_updated,
                ],
            )

    def get_sample(self, sample_id: str) -> dict | None:
        with self.read_conn() as con:
            row = con.execute(
                "SELECT * FROM sample_registry WHERE sample_id = ?", [sample_id]
            ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in con.description]
        return dict(zip(cols, row))

    def list_samples(
        self,
        *,
        data_type: str | None = None,
        l2_ready: bool | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if data_type:
            clauses.append("data_type = ?")
            params.append(data_type)
        if l2_ready is not None:
            clauses.append("l2_ready = ?")
            params.append(l2_ready)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.read_conn() as con:
            rows = con.execute(
                f"SELECT * FROM sample_registry {where} ORDER BY last_updated DESC LIMIT {int(limit)}",
                params,
            ).fetchall()
            cols = [d[0] for d in con.description]
        return [dict(zip(cols, r)) for r in rows]

    def update_sample(self, sample_id: str, **kwargs) -> None:
        if not kwargs:
            return
        kwargs.setdefault("last_updated", datetime.now(timezone.utc))
        _validate_cols(set(kwargs), _SAMPLE_MUTABLE_COLS, "update_sample")
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [sample_id]
        with self.write_conn() as con:
            con.execute(
                f"UPDATE sample_registry SET {cols} WHERE sample_id = ?", vals
            )

    # ------------------------------------------------------------------
    # mcp_tool_metrics
    # ------------------------------------------------------------------

    def record_metric(
        self,
        tool_name: str,
        duration_ms: int,
        status: str,
        *,
        error_class: str | None = None,
        requested_by: str | None = None,
        tool_id: str | None = None,
    ) -> None:
        try:
            with self.write_conn() as con:
                if tool_id is None:
                    row = con.execute(
                        "SELECT tool_id FROM tools WHERE tool_name = ? AND status = 'active' LIMIT 1",
                        [tool_name],
                    ).fetchone()
                    tool_id = str(row[0]) if row else None
                con.execute(
                    """
                    INSERT INTO mcp_tool_metrics
                        (tool_name, tool_id, duration_ms, status, error_class, requested_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        tool_name, tool_id, int(duration_ms), status,
                        error_class, requested_by or "mcp_client",
                    ],
                )
        except Exception as exc:
            logger.debug("metric write failed (%s): %s", tool_name, exc)

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        result: dict = {}
        try:
            with self.read_conn() as con:
                result["sample_count"] = con.execute(
                    "SELECT COUNT(*) FROM sample_registry"
                ).fetchone()[0]
                result["history_count"] = con.execute(
                    "SELECT COUNT(*) FROM analysis_history"
                ).fetchone()[0]
                result["stale_count"] = con.execute(
                    "SELECT COUNT(*) FROM analysis_history WHERE status = 'stale'"
                ).fetchone()[0]
                result["running_count"] = con.execute(
                    "SELECT COUNT(*) FROM analysis_history WHERE status = 'running'"
                ).fetchone()[0]
                result["l2_ready_count"] = con.execute(
                    "SELECT COUNT(*) FROM sample_registry WHERE l2_ready = TRUE"
                ).fetchone()[0]
        except Exception as exc:
            result["schema_error"] = str(exc)
            result["sample_count"] = -1
            result["history_count"] = -1
        return result
