"""Migration v27: durable analysis job queue."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH  # noqa: E402


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                job_id            VARCHAR PRIMARY KEY,
                tool_name         VARCHAR NOT NULL,
                arguments         JSON NOT NULL,
                requested_by      VARCHAR NOT NULL,
                resource_class    VARCHAR NOT NULL
                                  CHECK (resource_class IN ('cpu', 'gpu')),
                priority          INTEGER NOT NULL DEFAULT 0,
                status            VARCHAR NOT NULL DEFAULT 'queued'
                                  CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
                created_at        TIMESTAMP NOT NULL DEFAULT now(),
                started_at        TIMESTAMP,
                completed_at      TIMESTAMP,
                lease_owner       VARCHAR,
                lease_expires_at  TIMESTAMP,
                heartbeat_at      TIMESTAMP,
                retry_count       INTEGER NOT NULL DEFAULT 0,
                max_retries       INTEGER NOT NULL DEFAULT 1,
                cancel_requested  BOOLEAN NOT NULL DEFAULT FALSE,
                result             JSON,
                error              VARCHAR
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_claim "
            "ON analysis_jobs(resource_class, status, priority, created_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_owner "
            "ON analysis_jobs(requested_by, created_at)"
        )
        exists = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 27"
        ).fetchone()
        if not exists:
            con.execute(
                """
                INSERT INTO schema_migrations(version, applied_at, description)
                VALUES (27, now(), 'Durable analysis job queue with leases and cancellation')
                """
            )
        con.execute("CHECKPOINT")


if __name__ == "__main__":
    migrate()
