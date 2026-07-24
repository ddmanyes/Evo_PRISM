"""RegistryStore — backend-agnostic Protocol for ER metadata operations.

Concrete implementations:
  - DuckDBStore  (store/duckdb_store.py)  — default, wraps bio_memory.duckdb
  - PostgresStore (store/postgres_store.py) — target (ER_DB_BACKEND=postgres)

Low-level escape hatches (write_conn / read_conn) allow sub-functions that
still accept a raw connection (register_artifact, write_diagnosis, etc.) to keep
working in P1. They will be replaced by semantic store calls in P2.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Protocol, runtime_checkable

# Whitelist of mutable columns for dynamic UPDATE queries (H1 fix).
# 2026-07-24 架構審查（候選 4）：這兩個 store/duckdb_store.py 與 store/postgres_store.py
# 逐字複製過，有雙後端行為漂移風險（改一邊漏改另一邊，兩後端對「哪些欄位可被 update」
# 答案不一致）。收斂到 Protocol 檔案，兩個實作都 import 這裡的版本。
_HISTORY_MUTABLE_COLS = frozenset({
    "status", "result_path", "completed_at", "summary", "tool_id",
    "failure_diagnosis", "tags", "parameter_hash", "summary_metrics",
    "analysis_version", "tool_version", "user_approval", "parent_analysis_id",
})
_SAMPLE_MUTABLE_COLS = frozenset({
    "project", "data_type", "platform", "species", "tissue", "l3_path",
    "l2_ready", "analysis_done", "added_by", "notes", "last_updated",
    "condition", "time_point", "batch", "donor_id", "tags", "alias",
})


def _validate_cols(cols: set[str], allowed: frozenset[str], ctx: str) -> None:
    unknown = cols - allowed
    if unknown:
        raise ValueError(f"{ctx}: unknown or immutable columns {sorted(unknown)}")


@runtime_checkable
class RegistryStore(Protocol):
    # ------------------------------------------------------------------
    # Low-level escape hatches (P1 only; removed after sub-functions are ported)
    # ------------------------------------------------------------------

    @contextmanager
    def write_conn(self) -> Iterator:
        """Yield a write connection (DuckDB or psycopg) with VSS/extensions loaded."""
        ...

    @contextmanager
    def read_conn(self) -> Iterator:
        """Yield a read-only connection."""
        ...

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
        """INSERT a new row into analysis_history."""
        ...

    def complete_history(
        self,
        analysis_id: str,
        result_path: str,
        summary: str,
        completed_at: datetime,
        *,
        summary_metrics: dict | None = None,
    ) -> None:
        """UPDATE analysis_history: status='completed'."""
        ...

    def fail_history(
        self,
        analysis_id: str,
        completed_at: datetime,
        *,
        failure_diagnosis: str | None = None,
    ) -> None:
        """UPDATE analysis_history: status='failed'."""
        ...

    def update_history(self, analysis_id: str, **kwargs) -> None:
        """General-purpose UPDATE for analysis_history (used by agent_history)."""
        ...

    def get_history(
        self,
        sample_id: str,
        analysis_type: str,
        status: str = "completed",
    ) -> dict | None:
        """Return latest completed row for (sample_id, analysis_type), or None."""
        ...

    def list_history(
        self,
        *,
        sample_id: str | None = None,
        analysis_type: str | None = None,
        n_days: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return recent analysis_history rows, newest first."""
        ...

    def cleanup_stale_runs(self, hours: int = 24) -> int:
        """Mark running rows older than `hours` as 'stale'. Returns count."""
        ...

    def get_canonical_id(self, sample_id: str, analysis_type: str) -> str | None:
        """Return analysis_id of current canonical run, or None."""
        ...

    def mark_canonical(
        self, analysis_id: str, sample_id: str, analysis_type: str
    ) -> None:
        """Mark analysis_id canonical; demote previous canonical to superseded."""
        ...

    def supersede_by_id(
        self, analysis_id: str, superseded_analysis_id: str
    ) -> None:
        """Mark analysis_id canonical; demote one *named* prior row to superseded.

        Unlike mark_canonical (which demotes whatever is currently canonical for
        the whole sample+analysis_type), this targets a specific corrected run —
        the caller already knows which row this one replaces. Used by
        external_import when registering a fix for a known-wrong result.
        """
        ...

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
        """INSERT into sample_registry (idempotent: skip if sample_id exists)."""
        ...

    def get_sample(self, sample_id: str) -> dict | None:
        """Return sample_registry row as dict, or None."""
        ...

    def list_samples(
        self,
        *,
        data_type: str | None = None,
        l2_ready: bool | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return sample_registry rows."""
        ...

    def update_sample(self, sample_id: str, **kwargs) -> None:
        """UPDATE sample_registry fields by sample_id."""
        ...

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
        """INSERT into mcp_tool_metrics (best-effort, non-blocking)."""
        ...

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        """Return summary statistics (sample_count, history_count, etc.)."""
        ...
