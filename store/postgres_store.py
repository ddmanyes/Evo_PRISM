"""PostgreSQL implementation of RegistryStore.

Requires:  psycopg2-binary  (optional — only needed when ER_DB_BACKEND=postgres)
Configure: ER_PG_DSN=postgresql://er_rw:pass@localhost:5432/evo_prism

Key differences from DuckDBStore:
- Uses %s placeholders; _PgCursor translates ? → %s for escape-hatch callers
- Tags column is TEXT[] — uses ANY() and array_append/array_remove
- No CHECKPOINT needed (Postgres WAL handles durability)
- Opens a new connection per operation (no persistent file handle)
- gen_random_uuid() requires pgcrypto or Postgres >= 13 (built-in)
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Iterator

from server.identity import Identity, KeyState, Role
from store.base import (
    _HISTORY_MUTABLE_COLS,
    _JOB_COLUMNS,
    _SAMPLE_MUTABLE_COLS,
    _job_row_to_dict,
    _validate_cols,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_pg(sql: str) -> str:
    """Translate DuckDB-style ? position markers to psycopg2 %s.

    Escapes literal % (e.g. strftime format specifiers: %Y, %m, %H) as %%
    so psycopg2 does not misinterpret them as parameter placeholders.
    Existing %s placeholders (native psycopg2 style) are left untouched.
    """
    sql = re.sub(r"%(?!s)", "%%", sql)
    return sql.replace("?", "%s")


class _PgCursor:
    """Thin wrapper around a psycopg2 cursor, mimicking DuckDB's connection API.

    Allows existing escape-hatch callers (code_promoter, web_app, etc.) that
    do  `con.execute(sql, params).fetchall()`  to keep working unchanged.
    """

    def __init__(self, cursor) -> None:
        self._cur = cursor

    def execute(self, sql: str, params=None) -> "_PgCursor":
        # Use a SAVEPOINT so a failed statement doesn't abort the whole transaction.
        # This is critical when escape-hatch callers run DuckDB-specific SQL
        # (e.g. "LOAD fts") that is invalid in Postgres: without a savepoint the
        # entire connection enters an aborted state and all subsequent queries fail.
        #
        # Savepoint management uses a *separate* cursor so that self._cur retains
        # the result set from the actual SQL and fetchone()/fetchall() work correctly.
        conn = self._cur.connection
        with conn.cursor() as _sp:
            _sp.execute("SAVEPOINT _pgcursor_sp")
        try:
            self._cur.execute(_to_pg(sql), params or ())
        except Exception:
            with conn.cursor() as _sp:
                _sp.execute("ROLLBACK TO SAVEPOINT _pgcursor_sp")
            raise
        else:
            with conn.cursor() as _sp:
                _sp.execute("RELEASE SAVEPOINT _pgcursor_sp")
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def description(self):
        return self._cur.description


# ---------------------------------------------------------------------------
# PostgresStore
# ---------------------------------------------------------------------------


class PostgresStore:
    """RegistryStore backed by PostgreSQL (psycopg2)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    # ------------------------------------------------------------------
    # Internal connection factory
    # ------------------------------------------------------------------

    def _connect(self):
        import psycopg2  # lazy — not required unless backend=postgres

        return psycopg2.connect(self._dsn)

    # ------------------------------------------------------------------
    # Low-level escape hatches (P1 compat; will be removed in P3)
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def write_conn(self) -> Iterator[_PgCursor]:
        """Yield a _PgCursor inside a single transaction; COMMITs on exit."""
        con = self._connect()
        try:
            cur = _PgCursor(con.cursor())
            yield cur
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @contextlib.contextmanager
    def read_conn(self) -> Iterator[_PgCursor]:
        """Yield a _PgCursor (read-only, no COMMIT needed)."""
        con = self._connect()
        try:
            cur = _PgCursor(con.cursor())
            yield cur
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
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
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
                SET status          = 'completed',
                    result_path     = %s,
                    completed_at    = %s,
                    summary         = %s,
                    summary_metrics = %s::jsonb
                WHERE analysis_id = %s
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
                SET status            = 'failed',
                    completed_at      = %s,
                    failure_diagnosis = %s
                WHERE analysis_id = %s
                """,
                [completed_at, failure_diagnosis, analysis_id],
            )

    def update_history(self, analysis_id: str, **kwargs) -> None:
        if not kwargs:
            return
        _validate_cols(set(kwargs), _HISTORY_MUTABLE_COLS, "update_history")
        cols = ", ".join(f"{k} = %s" for k in kwargs)
        vals = list(kwargs.values()) + [analysis_id]
        with self.write_conn() as con:
            con.execute(
                f"UPDATE analysis_history SET {cols} WHERE analysis_id = %s",
                vals,
            )

    def get_history(
        self,
        sample_id: str,
        analysis_type: str,
        status: str = "completed",
        *,
        requested_by: str | None = None,
    ) -> dict | None:
        owner_clause = " AND requested_by = %s" if requested_by is not None else ""
        params = [sample_id, analysis_type, status]
        if requested_by is not None:
            params.append(requested_by)
        with self.read_conn() as con:
            row = con.execute(
                f"""
                SELECT analysis_id, completed_at, result_path, summary, parameters
                FROM   analysis_history
                WHERE  sample_id = %s AND analysis_type = %s AND status = %s
                       {owner_clause}
                ORDER  BY completed_at DESC LIMIT 1
                """,
                params,
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
        requested_by: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if sample_id:
            clauses.append("sample_id = %s")
            params.append(sample_id)
        if analysis_type:
            clauses.append("analysis_type = %s")
            params.append(analysis_type)
        if status:
            clauses.append("status = %s")
            params.append(status)
        if requested_by is not None:
            clauses.append("requested_by = %s")
            params.append(requested_by)
        if n_days:
            clauses.append("completed_at >= now() - %s * INTERVAL '1 day'")
            params.append(int(n_days))
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
                WHERE  status     = 'running'
                  AND  started_at < now() - %s * INTERVAL '1 hour'
                RETURNING analysis_id
                """,
                [hours],
            ).fetchall()
        cleaned = len(rows)
        if cleaned:
            logger.info("[pg_store] cleaned %d stale running record(s)", cleaned)
        return cleaned

    def get_canonical_id(self, sample_id: str, analysis_type: str) -> str | None:
        with self.read_conn() as con:
            row = con.execute(
                """
                SELECT analysis_id FROM analysis_history
                WHERE  sample_id     = %s
                  AND  analysis_type = %s
                  AND  'canonical' = ANY(COALESCE(tags, '{}'))
                ORDER  BY completed_at DESC LIMIT 1
                """,
                [sample_id, analysis_type],
            ).fetchone()
        return str(row[0]) if row else None

    # 一列同時只帶一個 canonical/superseded 標記：先移除舊的再 append 新的
    _RETAG_ROW = """
        UPDATE analysis_history
        SET tags = array_append(
            array_remove(array_remove(COALESCE(tags, '{}'), 'canonical'), 'superseded'),
            %s
        )
        WHERE analysis_id = %s
    """

    def mark_canonical(
        self, analysis_id: str, sample_id: str, analysis_type: str
    ) -> None:
        with self.write_conn() as con:
            # Demote existing canonical(s) for this (sample, type) to superseded
            con.execute(
                """
                UPDATE analysis_history
                SET tags = array_append(
                    array_remove(array_remove(COALESCE(tags, '{}'), 'canonical'), 'superseded'),
                    'superseded'
                )
                WHERE  sample_id     = %s
                  AND  analysis_type = %s
                  AND  'canonical' = ANY(COALESCE(tags, '{}'))
                  AND  analysis_id  != %s
                """,
                [sample_id, analysis_type, analysis_id],
            )
            # Promote the target row to canonical
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
    ) -> bool:
        with self.write_conn() as con:
            existing = con.execute(
                "SELECT 1 FROM sample_registry WHERE sample_id = %s", [sample_id]
            ).fetchone()
            if existing:
                return False
            last_updated = kwargs.get("last_updated", datetime.now(timezone.utc))
            con.execute(
                """
                INSERT INTO sample_registry
                    (sample_id, project, data_type, platform, species, tissue,
                     l3_path, l2_ready, analysis_done, added_by, notes, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, false, false, %s, %s, %s)
                """,
                [
                    sample_id, project, data_type, platform, species, tissue,
                    l3_path, added_by, notes, last_updated,
                ],
            )
        return True

    def get_sample(self, sample_id: str) -> dict | None:
        with self.read_conn() as con:
            row = con.execute(
                "SELECT * FROM sample_registry WHERE sample_id = %s", [sample_id]
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
            clauses.append("data_type = %s")
            params.append(data_type)
        if l2_ready is not None:
            clauses.append("l2_ready = %s")
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
        cols = ", ".join(f"{k} = %s" for k in kwargs)
        vals = list(kwargs.values()) + [sample_id]
        with self.write_conn() as con:
            con.execute(
                f"UPDATE sample_registry SET {cols} WHERE sample_id = %s", vals
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
                        "SELECT tool_id FROM tools WHERE tool_name = %s AND status = 'active' LIMIT 1",
                        [tool_name],
                    ).fetchone()
                    tool_id = str(row[0]) if row else None
                con.execute(
                    """
                    INSERT INTO mcp_tool_metrics
                        (tool_name, tool_id, duration_ms, status, error_class, requested_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
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
                VALUES (%s, %s, %s, %s, %s)
                """,
                [key_hash, key_prefix, user_id, normalized_role, created_by],
            )

    def get_identity_for_key_hash(self, key_hash: str) -> Identity | KeyState | None:
        with self.read_conn() as con:
            row = con.execute(
                "SELECT user_id, role, revoked_at FROM api_keys WHERE key_hash = %s",
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
                WHERE key_hash = %s AND revoked_at IS NULL
                RETURNING key_hash
                """,
                [key_hash],
            ).fetchall()
        return bool(rows)

    def revoke_api_key_by_prefix(self, key_prefix: str) -> bool:
        with self.write_conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT key_hash FROM api_keys "
                    "WHERE key_prefix = %s AND revoked_at IS NULL FOR UPDATE",
                    [key_prefix],
                )
                matches = cur.fetchall()
                if len(matches) > 1:
                    raise ValueError("key prefix is ambiguous")
                if not matches:
                    return False
                cur.execute(
                    "UPDATE api_keys SET revoked_at = now() "
                    "WHERE key_hash = %s AND revoked_at IS NULL RETURNING key_hash",
                    [matches[0][0]],
                )
                return cur.fetchone() is not None

    def list_api_keys(self, user_id: str | None = None) -> list[dict]:
        where = "WHERE user_id = %s" if user_id else ""
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
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM api_keys")
                row = cur.fetchone()
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
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO access_audit_log
                        (user_id, role, tool_name, target, outcome, detail)
                    VALUES (%s, %s, %s, %s, %s, %s)
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
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s)
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
                f"SELECT {columns} FROM analysis_jobs WHERE job_id = %s",
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
            clauses.append("requested_by = %s")
            params.append(requested_by)
        if status:
            clauses.append("status = %s")
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
                WHERE requested_by = %s AND status IN ('queued', 'running')
                """,
                [requested_by],
            ).fetchone()
        return int(row[0]) if row else 0

    def claim_next_job(
        self, worker_id: str, resource_class: str, lease_seconds: int
    ) -> dict | None:
        columns = ", ".join(f"j.{name}" for name in _JOB_COLUMNS)
        with self.write_conn() as con:
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
            row = con.execute(
                f"""
                WITH candidate AS (
                    SELECT job_id, status
                    FROM analysis_jobs
                    WHERE resource_class = %s
                      AND cancel_requested = FALSE
                      AND (
                        status = 'queued'
                        OR (status = 'running' AND lease_expires_at <= now()
                            AND retry_count < max_retries)
                      )
                    ORDER BY priority DESC, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE analysis_jobs AS j
                SET retry_count = CASE WHEN candidate.status = 'running'
                                       THEN j.retry_count + 1 ELSE j.retry_count END,
                    status = 'running',
                    started_at = COALESCE(j.started_at, now()),
                    lease_owner = %s,
                    heartbeat_at = now(),
                    lease_expires_at = now() + %s * INTERVAL '1 second'
                FROM candidate
                WHERE j.job_id = candidate.job_id
                RETURNING {columns}
                """,
                [resource_class, worker_id, max(1, int(lease_seconds))],
            ).fetchone()
        return _job_row_to_dict(row) if row else None

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        with self.write_conn() as con:
            rows = con.execute(
                """
                UPDATE analysis_jobs
                SET heartbeat_at = now(),
                    lease_expires_at = now() + %s * INTERVAL '1 second'
                WHERE job_id = %s AND status = 'running' AND lease_owner = %s
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
                SET status = %s, result = %s::jsonb, error = %s, completed_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = now()
                WHERE job_id = %s AND status = 'running' AND lease_owner = %s
                RETURNING job_id
                """,
                [status, result_json, error, job_id, worker_id],
            ).fetchall()
        return bool(rows)

    def request_cancel_job(self, job_id: str, requested_by: str | None) -> str | None:
        owner_clause = " AND requested_by = %s" if requested_by else ""
        owner_params = [requested_by] if requested_by else []
        with self.write_conn() as con:
            rows = con.execute(
                f"""
                UPDATE analysis_jobs
                SET status = 'canceled', cancel_requested = TRUE, completed_at = now()
                WHERE job_id = %s AND status = 'queued'{owner_clause}
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
                WHERE job_id = %s AND status = 'running'{owner_clause}
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
