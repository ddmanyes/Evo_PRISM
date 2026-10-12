"""Lease-based worker for durable analysis jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from analysis.job_queue import AnalysisJobQueue
from config import settings
from server.identity import Identity, Role
from server.tool_catalog import TOOL_CATALOG

logger = logging.getLogger(__name__)

Dispatcher = Callable[[str, dict, Identity], Awaitable[Any]]


async def _default_dispatcher(tool_name: str, arguments: dict, actor: Identity) -> Any:
    from server.bio_memory_server import dispatch_queued_tool

    return await dispatch_queued_tool(tool_name, arguments, actor)


class AnalysisWorker:
    def __init__(
        self,
        queue: AnalysisJobQueue,
        *,
        worker_id: str,
        resource_class: str = "cpu",
        lease_seconds: int = 300,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        if resource_class not in {"cpu", "gpu"}:
            raise ValueError("resource_class must be cpu or gpu")
        self.queue = queue
        self.worker_id = worker_id
        self.resource_class = resource_class
        self.lease_seconds = max(1, int(lease_seconds))
        self.dispatcher = dispatcher or _default_dispatcher

    async def _heartbeat(self, job_id: str, stopped: asyncio.Event) -> None:
        interval = max(0.25, min(self.lease_seconds / 3, 30.0))
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=interval)
            except TimeoutError:
                if not self.queue.heartbeat(
                    job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                ):
                    logger.error("Lost lease while heartbeating job %s", job_id)
                    return

    async def run_once(self) -> bool:
        job = self.queue.claim(
            self.worker_id,
            resource_class=self.resource_class,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False

        job_id = job["job_id"]
        actor = Identity(job["requested_by"], Role.ANALYST)
        stopped = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job_id, stopped))
        try:
            spec = TOOL_CATALOG.get(job["tool_name"])
            if spec is None or not spec.queueable or spec.dangerous:
                raise ValueError(f"target tool {job['tool_name']!r} is not queueable")

            raw_arguments = job["arguments"]
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
            if not isinstance(arguments, dict):
                raise ValueError("stored job arguments must be an object")

            current = self.queue.store.get_job(job_id)
            if current and current.get("cancel_requested"):
                self.queue.finish(job_id, worker_id=self.worker_id, canceled=True)
                return True

            result = await self.dispatcher(job["tool_name"], arguments, actor)
            current = self.queue.store.get_job(job_id)
            self.queue.finish(
                job_id,
                worker_id=self.worker_id,
                result=None if current and current.get("cancel_requested") else result,
                canceled=bool(current and current.get("cancel_requested")),
            )
        except Exception as exc:
            current = self.queue.store.get_job(job_id)
            canceled = bool(current and current.get("cancel_requested"))
            self.queue.finish(
                job_id,
                worker_id=self.worker_id,
                canceled=canceled,
                error=None if canceled else f"{exc.__class__.__name__}: {exc}",
            )
            logger.exception("Analysis job %s failed", job_id)
        finally:
            stopped.set()
            await heartbeat
        return True

    async def run_forever(self, *, poll_seconds: float = 2.0) -> None:
        delay = max(0.1, float(poll_seconds))
        while True:
            handled = await self.run_once()
            if not handled:
                await asyncio.sleep(delay)


def _worker_id(resource_class: str) -> str:
    configured = os.getenv("ANALYSIS_WORKER_ID", "").strip()
    return configured or f"{socket.gethostname()}-{resource_class}-{uuid.uuid4().hex[:8]}"


async def _main_async(resource_class: str) -> None:
    from store.factory import get_store

    queue = AnalysisJobQueue(
        get_store(), max_pending_per_user=settings.ANALYSIS_JOB_MAX_PENDING
    )
    worker = AnalysisWorker(
        queue,
        worker_id=_worker_id(resource_class),
        resource_class=resource_class,
        lease_seconds=settings.ANALYSIS_JOB_LEASE_SECONDS,
    )
    await worker.run_forever(poll_seconds=settings.ANALYSIS_JOB_POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-class", choices=("cpu", "gpu"), default="cpu")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(_main_async(args.resource_class))


if __name__ == "__main__":
    main()
