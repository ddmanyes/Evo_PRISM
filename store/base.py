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
import json
from typing import Iterator, Protocol, runtime_checkable

from server.identity import Identity, KeyState

_JOB_COLUMNS = (
    "job_id",
    "tool_name",
    "arguments",
    "requested_by",
    "resource_class",
    "priority",
    "status",
    "created_at",
    "started_at",
    "completed_at",
    "lease_owner",
    "lease_expires_at",
    "heartbeat_at",
    "retry_count",
    "max_retries",
    "cancel_requested",
    "result",
    "error",
)


def _job_row_to_dict(row) -> dict:
    item = dict(zip(_JOB_COLUMNS, row))
    for field in ("arguments", "result"):
        value = item.get(field)
        if isinstance(value, str):
            item[field] = json.loads(value)
    item["job_id"] = str(item["job_id"])
    item["cancel_requested"] = bool(item["cancel_requested"])
    return item

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
        *,
        requested_by: str | None = None,
    ) -> dict | None:
        """Return latest matching row, optionally restricted to one owner."""
        ...

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
    ) -> bool:
        """INSERT if absent and return whether a new row was created."""
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
    # figure_index (v30) —— figure_cache 的 content-addressed figure_id
    # 反查索引：讓「用描述找圖」不必重讀整份報告全文。
    # ------------------------------------------------------------------

    def record_figure(
        self,
        figure_id: str,
        *,
        alt: str | None = None,
        tool_name: str | None = None,
        sample_id: str | None = None,
        analysis_id: str | None = None,
        requested_by: str | None = None,
    ) -> None:
        """INSERT into figure_index (best-effort, non-blocking — same convention as record_metric)."""
        ...

    def find_figures(
        self,
        *,
        figure_id: str | None = None,
        sample_id: str | None = None,
        tool_name: str | None = None,
        query: str | None = None,
        requested_by: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query figure_index by exact/partial filters, newest first.

        ``query`` matches ``alt`` via ILIKE substring — no embedding involved
        (0 token, no dependency on the embedding server). ``requested_by=None``
        means unfiltered (admin/global view), matching ``visible_requested_by``.
        """
        ...

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        """Return summary statistics (sample_count, history_count, etc.)."""
        ...

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
        """Register one hashed member key. Plaintext keys are never accepted here."""
        ...

    def get_identity_for_key_hash(self, key_hash: str) -> Identity | KeyState | None:
        """Resolve an active identity, revoked sentinel, or unknown key."""
        ...

    def revoke_api_key(self, key_hash: str) -> bool:
        """Revoke one active key and return whether a row changed."""
        ...

    def revoke_api_key_by_prefix(self, key_prefix: str) -> bool:
        """Revoke one unambiguous active key by its display prefix."""
        ...

    def list_api_keys(self, user_id: str | None = None) -> list[dict]:
        """List lifecycle metadata without returning key hashes."""
        ...

    def count_active_api_keys(self) -> int:
        """Return the number of non-revoked registered keys."""
        ...

    def count_api_keys(self) -> int:
        """Return all key records, including revoked rows that keep auth enabled."""
        ...

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
        """Append one security-relevant operation without storing secrets."""
        ...

    # ------------------------------------------------------------------
    # Durable analysis jobs
    # ------------------------------------------------------------------

    def enqueue_job(self, **job) -> None:
        """Persist a new queued analysis job."""
        ...

    def get_job(self, job_id: str) -> dict | None:
        ...

    def list_jobs(
        self,
        *,
        requested_by: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        ...

    def count_pending_jobs(self, requested_by: str) -> int:
        ...

    def claim_next_job(
        self, worker_id: str, resource_class: str, lease_seconds: int
    ) -> dict | None:
        ...

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        ...

    def finish_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        status: str,
        result_json: str | None,
        error: str | None,
    ) -> bool:
        ...

    def request_cancel_job(self, job_id: str, requested_by: str | None) -> str | None:
        ...
