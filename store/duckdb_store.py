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

from server.identity import Identity, KeyState, Role
from store.base import (
    _HISTORY_MUTABLE_COLS,
    _JOB_COLUMNS,
    _SAMPLE_MUTABLE_COLS,
    _job_row_to_dict,
    _validate_cols,
)

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
    # API-key identity lifecycle
    # ------------------------------------------------------------------

    def register_api_key(
        self,
        *,
        key_hash: str,
        key_prefix: str,
        user_id: str,
        role: str,
        created_by: str,
    ) -> None:
        normalized_role = Role(role).value
        with self.write_conn() as con:
            con.execute(
                """
                INSERT INTO api_keys
                    (key_hash, key_prefix, user_id, role, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                [key_hash, key_prefix, user_id, normalized_role, created_by],
            )

    def get_identity_for_key_hash(self, key_hash: str) -> Identity | KeyState | None:
        with self.read_conn() as con:
            row = con.execute(
                "SELECT user_id, role, revoked_at FROM api_keys WHERE key_hash = ?",
                [key_hash],
            ).fetchone()
        if row is None:
            return None
        if row[2] is not None:
            return KeyState.REVOKED
        return Identity(user_id=row[0], role=Role(row[1]))

    def revoke_api_key(self, key_hash: str) -> bool:
        with self.write_conn() as con:
            rows = con.execute(
                """
                UPDATE api_keys
                SET revoked_at = now()
                WHERE key_hash = ? AND revoked_at IS NULL
                RETURNING key_hash
                """,
                [key_hash],
            ).fetchall()
        return bool(rows)

    def revoke_api_key_by_prefix(self, key_prefix: str) -> bool:
        with self.write_conn() as con:
            matches = con.execute(
                "SELECT key_hash FROM api_keys WHERE key_prefix = ? AND revoked_at IS NULL",
                [key_prefix],
            ).fetchall()
            if len(matches) > 1:
                raise ValueError("key prefix is ambiguous")
            if not matches:
                return False
            rows = con.execute(
                "UPDATE api_keys SET revoked_at = now() "
                "WHERE key_hash = ? AND revoked_at IS NULL RETURNING key_hash",
                [matches[0][0]],
            ).fetchall()
        return bool(rows)

    def list_api_keys(self, user_id: str | None = None) -> list[dict]:
        where = "WHERE user_id = ?" if user_id else ""
        params = [user_id] if user_id else []
        with self.read_conn() as con:
            rows = con.execute(
                f"""
                SELECT key_prefix, user_id, role, created_at, created_by, revoked_at
                FROM api_keys {where}
                ORDER BY created_at
                """,
                params,
            ).fetchall()
        columns = ["key_prefix", "user_id", "role", "created_at", "created_by", "revoked_at"]
        return [dict(zip(columns, row)) for row in rows]

    def count_active_api_keys(self) -> int:
        with self.read_conn() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL"
            ).fetchone()
        return int(row[0]) if row else 0

    def count_api_keys(self) -> int:
        with self.read_conn() as con:
            row = con.execute("SELECT COUNT(*) FROM api_keys").fetchone()
        return int(row[0]) if row else 0

    def record_access_audit(
        self,
        *,
        user_id: str,
        role: str,
        tool_name: str,
        target: str | None,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        with self.write_conn() as con:
            con.execute(
                """
                INSERT INTO access_audit_log
                    (user_id, role, tool_name, target, outcome, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [user_id, Role(role).value, tool_name, target, outcome, detail],
            )

    # ------------------------------------------------------------------
    # Durable analysis jobs
    # ------------------------------------------------------------------

    def enqueue_job(self, **job) -> None:
        with self.write_conn() as con:
            con.execute(
                """
                INSERT INTO analysis_jobs
                    (job_id, tool_name, arguments, requested_by, resource_class,
                     priority, max_retries, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    job["job_id"],
                    job["tool_name"],
                    job["arguments_json"],
                    job["requested_by"],
                    job["resource_class"],
                    int(job.get("priority", 0)),
                    int(job.get("max_retries", 1)),
                    job["created_at"],
                ],
            )

    def get_job(self, job_id: str) -> dict | None:
        columns = ", ".join(_JOB_COLUMNS)
        with self.read_conn() as con:
            row = con.execute(
                f"SELECT {columns} FROM analysis_jobs WHERE job_id = ?",
                [job_id],
            ).fetchone()
        return _job_row_to_dict(row) if row else None

    def list_jobs(
        self,
        *,
        requested_by: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if requested_by:
            clauses.append("requested_by = ?")
            params.append(requested_by)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        columns = ", ".join(_JOB_COLUMNS)
        with self.read_conn() as con:
            rows = con.execute(
                f"SELECT {columns} FROM analysis_jobs {where} "
                f"ORDER BY created_at DESC LIMIT {max(1, min(int(limit), 500))}",
                params,
            ).fetchall()
        return [_job_row_to_dict(row) for row in rows]

    def count_pending_jobs(self, requested_by: str) -> int:
        with self.read_conn() as con:
            row = con.execute(
                """
                SELECT COUNT(*) FROM analysis_jobs
                WHERE requested_by = ? AND status IN ('queued', 'running')
                """,
                [requested_by],
            ).fetchone()
        return int(row[0]) if row else 0

    def claim_next_job(
        self, worker_id: str, resource_class: str, lease_seconds: int
    ) -> dict | None:
        columns = ", ".join(_JOB_COLUMNS)
        with self.write_conn() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(
                    """
                    UPDATE analysis_jobs
                    SET status = 'failed', completed_at = now(),
                        error = 'worker lease expired after maximum retries'
                    WHERE status = 'running'
                      AND lease_expires_at <= now()
                      AND retry_count >= max_retries
                    """
                )
                candidate = con.execute(
                    """
                    SELECT job_id FROM analysis_jobs
                    WHERE resource_class = ?
                      AND cancel_requested = FALSE
                      AND (
                        status = 'queued'
                        OR (status = 'running' AND lease_expires_at <= now()
                            AND retry_count < max_retries)
                      )
                    ORDER BY priority DESC, created_at
                    LIMIT 1
                    """,
                    [resource_class],
                ).fetchone()
                if candidate is None:
                    con.execute("COMMIT")
                    return None
                row = con.execute(
                    f"""
                    UPDATE analysis_jobs
                    SET retry_count = CASE WHEN status = 'running'
                                           THEN retry_count + 1 ELSE retry_count END,
                        status = 'running',
                        started_at = COALESCE(started_at, now()),
                        lease_owner = ?,
                        heartbeat_at = now(),
                        lease_expires_at = now() + (? * INTERVAL '1 second')
                    WHERE job_id = ?
                      AND cancel_requested = FALSE
                      AND (status = 'queued'
                           OR (status = 'running' AND lease_expires_at <= now()))
                    RETURNING {columns}
                    """,
                    [worker_id, max(1, int(lease_seconds)), candidate[0]],
                ).fetchone()
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return _job_row_to_dict(row) if row else None

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        with self.write_conn() as con:
            rows = con.execute(
                """
                UPDATE analysis_jobs
                SET heartbeat_at = now(),
                    lease_expires_at = now() + (? * INTERVAL '1 second')
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                  AND cancel_requested = FALSE
                RETURNING job_id
                """,
                [max(1, int(lease_seconds)), job_id, worker_id],
            ).fetchall()
        return bool(rows)

    def finish_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        status: str,
        result_json: str | None,
        error: str | None,
    ) -> bool:
        if status not in {"succeeded", "failed", "canceled"}:
            raise ValueError(f"invalid terminal job status: {status}")
        with self.write_conn() as con:
            rows = con.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, result = ?, error = ?, completed_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = now()
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                RETURNING job_id
                """,
                [status, result_json, error, job_id, worker_id],
            ).fetchall()
        return bool(rows)

    def request_cancel_job(self, job_id: str, requested_by: str | None) -> str | None:
        owner_clause = " AND requested_by = ?" if requested_by else ""
        owner_params = [requested_by] if requested_by else []
        with self.write_conn() as con:
            rows = con.execute(
                f"""
                UPDATE analysis_jobs
                SET status = 'canceled', cancel_requested = TRUE, completed_at = now()
                WHERE job_id = ? AND status = 'queued'{owner_clause}
                RETURNING job_id
                """,
                [job_id, *owner_params],
            ).fetchall()
            if rows:
                return "canceled"
            rows = con.execute(
                f"""
                UPDATE analysis_jobs
                SET cancel_requested = TRUE
                WHERE job_id = ? AND status = 'running'{owner_clause}
                RETURNING job_id
                """,
                [job_id, *owner_params],
            ).fetchall()
        return "cancel_requested" if rows else None

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
