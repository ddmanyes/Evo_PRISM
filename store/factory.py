"""Factory for RegistryStore backends.

Backend selected by ER_DB_BACKEND environment variable:
  - "duckdb"   (default) → DuckDBStore
  - "postgres"           → PostgresStore (requires ER_PG_DSN)

DSN example:
  ER_PG_DSN=postgresql://er_rw:er_rw_pass@localhost:5432/evo_prism
"""
from __future__ import annotations

import os

from .duckdb_store import DuckDBStore

_store_instance: DuckDBStore | None = None


def get_store() -> DuckDBStore:
    """Return process-level RegistryStore (cached after first call)."""
    global _store_instance
    if _store_instance is not None:
        return _store_instance

    backend = os.environ.get("ER_DB_BACKEND", "duckdb").lower()

    if backend == "postgres":
        from .postgres_store import PostgresStore  # lazy import; psycopg optional
        dsn = os.environ.get("ER_PG_DSN", "")
        if not dsn:
            raise RuntimeError(
                "ER_DB_BACKEND=postgres requires ER_PG_DSN, e.g. "
                "postgresql://er_rw:password@localhost:5432/evo_prism"
            )
        _store_instance = PostgresStore(dsn)  # type: ignore[assignment]
    else:
        _store_instance = DuckDBStore()

    return _store_instance


def reset_store() -> None:
    """Clear the cached store (for testing only)."""
    global _store_instance
    _store_instance = None
