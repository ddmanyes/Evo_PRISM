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
from datetime import datetime, timezone
from typing import Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_pg(sql: str) -> str:
    """Translate DuckDB-style ? position markers to psycopg2 %s."""
    return sql.replace("?", "%s")


class _PgCursor:
    """Thin wrapper around a psycopg2 cursor, mimicking DuckDB's connection API.

    Allows existing escape-hatch callers (code_promoter, web_app, etc.) that
    do  `con.execute(sql, params).fetchall()`  to keep working unchanged.
    """

    def __init__(self, cursor) -> None:
        self._cur = cursor

    def execute(self, sql: str, params=None) -> "_PgCursor":
        self._cur.execute(_to_pg(sql), params or ())
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
    ) -> dict | None:
        with self.read_conn() as con:
            row = con.execute(
                """
                SELECT analysis_id, completed_at, result_path, summary, parameters
                FROM   analysis_history
                WHERE  sample_id = %s AND analysis_type = %s AND status = %s
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
            clauses.append("sample_id = %s")
            params.append(sample_id)
        if analysis_type:
            clauses.append("analysis_type = %s")
            params.append(analysis_type)
        if status:
            clauses.append("status = %s")
            params.append(status)
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
            con.execute(
                """
                UPDATE analysis_history
                SET tags = array_append(
                    array_remove(array_remove(COALESCE(tags, '{}'), 'canonical'), 'superseded'),
                    'canonical'
                )
                WHERE analysis_id = %s
                """,
                [analysis_id],
            )

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
                "SELECT 1 FROM sample_registry WHERE sample_id = %s", [sample_id]
            ).fetchone()
            if existing:
                return
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
