"""Domain service for durable, owner-scoped analysis jobs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from server.identity import Identity, Role
from store.base import RegistryStore


class JobQueueError(RuntimeError):
    pass


class AnalysisJobQueue:
    def __init__(self, store: RegistryStore, *, max_pending_per_user: int = 3) -> None:
        self.store = store
        self.max_pending_per_user = max(1, int(max_pending_per_user))

    def submit(
        self,
        tool_name: str,
        arguments: dict,
        *,
        actor: Identity,
        resource_class: str = "cpu",
        priority: int = 0,
        max_retries: int = 1,
    ) -> dict:
        if not actor.permits(Role.ANALYST):
            raise JobQueueError("analyst role required")
        if resource_class not in {"cpu", "gpu"}:
            raise JobQueueError("resource_class must be cpu or gpu")
        if not isinstance(arguments, dict):
            raise JobQueueError("arguments must be an object")
        if self.store.count_pending_jobs(actor.user_id) >= self.max_pending_per_user:
            raise JobQueueError(
                f"pending job quota exceeded ({self.max_pending_per_user} per user)"
            )

        trusted_arguments = dict(arguments)
        trusted_arguments["requested_by"] = actor.user_id
        job_id = uuid.uuid4().hex
        self.store.enqueue_job(
            job_id=job_id,
            tool_name=tool_name,
            arguments_json=json.dumps(
                trusted_arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            requested_by=actor.user_id,
            resource_class=resource_class,
            priority=int(priority) if actor.role is Role.ADMIN else 0,
            max_retries=max(0, min(int(max_retries), 3)),
            created_at=datetime.now(timezone.utc),
        )
        job = self.store.get_job(job_id)
        if job is None:  # pragma: no cover - storage contract violation
            raise JobQueueError("job disappeared after enqueue")
        return job

    def get(self, job_id: str, *, actor: Identity) -> dict:
        job = self.store.get_job(job_id)
        if job is None or (
            actor.role is not Role.ADMIN and job["requested_by"] != actor.user_id
        ):
            raise JobQueueError("job not found")
        return job

    def list(
        self,
        *,
        actor: Identity,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        owner = None if actor.role is Role.ADMIN else actor.user_id
        return self.store.list_jobs(
            requested_by=owner,
            status=status,
            limit=max(1, min(int(limit), 500)),
        )

    def cancel(self, job_id: str, *, actor: Identity) -> str:
        owner = None if actor.role is Role.ADMIN else actor.user_id
        outcome = self.store.request_cancel_job(job_id, owner)
        if outcome is None:
            raise JobQueueError("job not found or not cancelable")
        return outcome

    def claim(
        self,
        worker_id: str,
        *,
        resource_class: str,
        lease_seconds: int,
    ) -> dict | None:
        if resource_class not in {"cpu", "gpu"}:
            raise JobQueueError("resource_class must be cpu or gpu")
        return self.store.claim_next_job(
            worker_id,
            resource_class,
            max(1, int(lease_seconds)),
        )

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: int) -> bool:
        return self.store.heartbeat_job(
            job_id,
            worker_id,
            max(1, int(lease_seconds)),
        )

    def finish(
        self,
        job_id: str,
        *,
        worker_id: str,
        result=None,
        error: str | None = None,
        canceled: bool = False,
    ) -> None:
        status = "canceled" if canceled else ("failed" if error else "succeeded")
        result_json = None
        if result is not None:
            result_json = json.dumps(result, ensure_ascii=False, default=str)
        changed = self.store.finish_job(
            job_id,
            worker_id,
            status=status,
            result_json=result_json,
            error=error,
        )
        if not changed:
            raise JobQueueError("worker is not the active lease owner")
