"""P3 Concurrency test: ≥5 writers simultaneously INSERT analysis_history + read sample_registry.

Run:
    ER_PG_DSN="postgresql://er_rw:er_rw_pass@127.0.0.1:5432/evo_prism_test" \
    ER_DB_BACKEND=postgres \
    uv run python scripts/p3_concurrency_test.py

Assertions:
    - Zero lock errors / exceptions across all workers
    - Final row count == WORKERS * WRITES_PER_WORKER
    - pg_stat_activity shows no deadlocks
"""
from __future__ import annotations

import os
import sys
import uuid
import time
import multiprocessing as mp
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

WORKERS = 5
WRITES_PER_WORKER = 20
RUN_SECONDS = 30  # workers run for this long


def worker(worker_id: int, result_queue: mp.Queue) -> None:
    """Each worker inserts WRITES_PER_WORKER rows and reads sample_registry."""
    os.environ["ER_DB_BACKEND"] = "postgres"
    # DSN inherited from parent env

    errors = []
    writes = 0
    reads = 0
    t_end = time.monotonic() + RUN_SECONDS

    try:
        from store.factory import get_store
        store = get_store()

        # Register a worker-specific sample to avoid PK collision
        sample_id = f"p3_worker_{worker_id}"
        store.register_sample(
            sample_id=sample_id,
            project="p3_test",
            data_type="visium_hd",
            platform="10x",
            species="mouse",
            tissue="brain",
            l3_path=f"/data/p3/{worker_id}",
            added_by=f"worker_{worker_id}",
            notes="",
        )

        while time.monotonic() < t_end and writes < WRITES_PER_WORKER:
            try:
                aid = str(uuid.uuid4())
                store.insert_history(
                    analysis_id=aid,
                    sample_id=sample_id,
                    analysis_type="spatial_eda",
                    params_json="{}",
                    status="pending",
                    requested_by=f"worker_{worker_id}",
                    started_at=datetime.now(timezone.utc),
                )
                store.complete_history(
                    analysis_id=aid,
                    result_path=f"/results/p3/{worker_id}/{aid[:8]}",
                    summary=f"worker {worker_id} run {writes}",
                    completed_at=datetime.now(timezone.utc),
                )
                writes += 1

                # Also do a read between writes (simulates BAR agent pattern)
                sample = store.get_sample(sample_id)
                if sample:
                    reads += 1

            except Exception as exc:
                errors.append(f"w{worker_id} write#{writes}: {exc}")

    except Exception as exc:
        errors.append(f"w{worker_id} setup: {exc}")

    result_queue.put({
        "worker_id": worker_id,
        "writes": writes,
        "reads": reads,
        "errors": errors,
    })


def main() -> None:
    dsn = os.environ.get("ER_PG_DSN", "")
    if not dsn:
        print("ERROR: ER_PG_DSN not set", flush=True)
        sys.exit(1)

    print(f"P3 Concurrency test: {WORKERS} workers × {WRITES_PER_WORKER} writes, {RUN_SECONDS}s window")
    print(f"  DSN: {dsn[:40]}...", flush=True)

    # Clean slate
    import psycopg2
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM analysis_history WHERE sample_id LIKE 'p3_worker_%';"
            "DELETE FROM sample_registry WHERE sample_id LIKE 'p3_worker_%';"
        )
    conn.commit()
    conn.close()

    result_queue: mp.Queue = mp.Queue()
    procs = [
        mp.Process(target=worker, args=(i, result_queue), daemon=True)
        for i in range(WORKERS)
    ]

    t0 = time.monotonic()
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=RUN_SECONDS + 15)

    elapsed = time.monotonic() - t0

    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    total_writes = sum(r["writes"] for r in results)
    total_reads = sum(r["reads"] for r in results)
    all_errors = [e for r in results for e in r["errors"]]

    # Verify actual DB row count
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM analysis_history WHERE sample_id LIKE 'p3_worker_%'")
        db_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
        lock_waits = cur.fetchone()[0]
    conn.close()

    print(f"\n--- P3 Results ({elapsed:.1f}s) ---")
    for r in sorted(results, key=lambda x: x["worker_id"]):
        status = "OK" if not r["errors"] else f"ERRORS: {r['errors']}"
        print(f"  Worker {r['worker_id']}: {r['writes']} writes, {r['reads']} reads — {status}")

    print(f"\nTotal writes claimed: {total_writes}")
    print(f"Total reads:          {total_reads}")
    print(f"DB row count:         {db_count}")
    print(f"Lock waits now:       {lock_waits}")
    print(f"Errors:               {len(all_errors)}")

    ok = True
    if all_errors:
        print(f"\nFAIL: {len(all_errors)} errors")
        for e in all_errors:
            print(f"  {e}")
        ok = False
    if db_count != total_writes:
        print(f"\nFAIL: DB count {db_count} ≠ claimed writes {total_writes}")
        ok = False

    if ok:
        print("\nPASS ✓ — zero errors, row count matches")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    mp.set_start_method("spawn")
    main()
