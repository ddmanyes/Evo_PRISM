"""Migration v27: per-key identities and immutable access audit metadata."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH  # noqa: E402


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL,
                description VARCHAR NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash     VARCHAR PRIMARY KEY,
                key_prefix   VARCHAR NOT NULL,
                user_id      VARCHAR NOT NULL,
                role         VARCHAR NOT NULL
                             CHECK (role IN ('viewer', 'analyst', 'admin')),
                created_at   TIMESTAMP NOT NULL DEFAULT now(),
                created_by   VARCHAR NOT NULL,
                revoked_at   TIMESTAMP
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_user_active "
            "ON api_keys(user_id, revoked_at)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS access_audit_log (
                audit_id     UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                occurred_at  TIMESTAMP NOT NULL DEFAULT now(),
                user_id      VARCHAR NOT NULL,
                role         VARCHAR NOT NULL,
                tool_name    VARCHAR NOT NULL,
                target       VARCHAR,
                outcome      VARCHAR NOT NULL,
                detail       VARCHAR
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_access_audit_user_time "
            "ON access_audit_log(user_id, occurred_at)"
        )

        exists = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 27"
        ).fetchone()
        if not exists:
            con.execute(
                """
                INSERT INTO schema_migrations(version, applied_at, description)
                VALUES (27, now(), 'Per-key identity registry and access audit log')
                """
            )
        con.execute("CHECKPOINT")


if __name__ == "__main__":
    migrate()
