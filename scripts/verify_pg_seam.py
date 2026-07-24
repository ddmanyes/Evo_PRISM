"""End-to-end Postgres-backend verification of the analysis_run seam.

The seam's whole reason to exist: under ER_DB_BACKEND=postgres the
completed/failed/tool_id/canonical writes must land in Postgres correctly
(the old raw con.execute path wrote to the wrong backend). DuckDB-backed test
fixtures can't exercise this (they seed a tmp DuckDB + patch DUCKDB_PATH, which
the Postgres backend ignores), so this harness seeds the sample through the
store itself and asserts against Postgres directly.

Provision a throwaway test DB first (once):
  docker exec sb-pg psql -U postgres -c "CREATE ROLE er_rw LOGIN PASSWORD 'er_rw_pass';"
  docker exec sb-pg psql -U postgres -c "CREATE DATABASE evo_prism_test OWNER er_rw;"
  docker exec sb-pg psql -U postgres -d evo_prism_test -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
  docker exec -i sb-pg psql -U er_rw -d evo_prism_test < scripts/pg_schema.sql

Then run:
  ER_DB_BACKEND=postgres ER_PG_DSN=postgresql://er_rw:er_rw_pass@127.0.0.1:5432/evo_prism_test \
      .venv/bin/python scripts/verify_pg_seam.py
"""
import os
import sys
import tempfile
from pathlib import Path

assert os.environ.get("ER_DB_BACKEND") == "postgres", "must run under postgres backend"

_REPO = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _REPO)
os.chdir(_REPO)

import psycopg2

from store.factory import get_store, reset_store
from analysis.run_context import analysis_run

DSN = os.environ["ER_PG_DSN"]


def _truncate():
    c = psycopg2.connect(DSN)
    with c, c.cursor() as cur:
        cur.execute("TRUNCATE analysis_history, sample_registry CASCADE")
    c.close()


def _pg(sql, params=()):
    c = psycopg2.connect(DSN)
    with c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    c.close()
    return rows


def main():
    _truncate()
    reset_store()
    store = get_store()
    store.register_sample("PGX", "proj", "scrna", "cellranger", "human",
                          "skin", "/tmp/l3", "verify", "pg seam test")

    tmp = Path(tempfile.mkdtemp())
    checks = []

    # 1) success path → completed
    r1 = tmp / "r1.md"; r1.write_text("ok")
    with analysis_run("PGX", "bulk_deg", params={"a": 1}, tool_name="bio_run_deg",
                      canonical=True) as run:
        aid1 = run.analysis_id
        run.complete(r1, "first run")
    row = _pg("SELECT status, tags FROM analysis_history WHERE analysis_id=%s", [aid1])[0]
    checks.append(("success→completed", row[0] == "completed"))
    checks.append(("success→canonical tag", "canonical" in (row[1] or [])))

    # 2) second canonical run demotes the first
    r2 = tmp / "r2.md"; r2.write_text("ok2")
    with analysis_run("PGX", "bulk_deg", params={"a": 2}, tool_name="bio_run_deg",
                      canonical=True) as run:
        aid2 = run.analysis_id
        run.complete(r2, "second run")
    t1 = _pg("SELECT tags FROM analysis_history WHERE analysis_id=%s", [aid1])[0][0]
    t2 = _pg("SELECT tags FROM analysis_history WHERE analysis_id=%s", [aid2])[0][0]
    checks.append(("2nd run demotes 1st", "superseded" in (t1 or []) and "canonical" not in (t1 or [])))
    checks.append(("2nd run canonical", "canonical" in (t2 or [])))
    checks.append(("get_canonical_id points to 2nd", store.get_canonical_id("PGX", "bulk_deg") == aid2))

    # 3) exception path → failed + re-raise
    aid3 = None
    try:
        with analysis_run("PGX", "custom", params={}, tool_name="bio_run_deg") as run:
            aid3 = run.analysis_id
            raise ValueError("boom")
    except ValueError:
        pass
    row = _pg("SELECT status FROM analysis_history WHERE analysis_id=%s", [aid3])[0]
    checks.append(("exception→failed", row[0] == "failed"))

    # 4) missing complete → fail-fast RuntimeError + row failed
    aid4 = None
    raised = False
    try:
        with analysis_run("PGX", "custom2", params={}) as run:
            aid4 = run.analysis_id
    except RuntimeError:
        raised = True
    row = _pg("SELECT status FROM analysis_history WHERE analysis_id=%s", [aid4])[0]
    checks.append(("missing complete→RuntimeError", raised))
    checks.append(("missing complete→failed row", row[0] == "failed"))

    # 5) external_import supersede_by_id on Postgres
    from analysis.external_import import register_external_analysis_result
    import analysis.external_import as ext
    ext.BIO_DB_ROOT = tmp  # allow result under tmp
    old = tmp / "old.md"; old.write_text("wrong direction")
    # register an independent prior run to supersede
    with analysis_run("PGX", "tsdir", params={}, canonical=True) as run:
        old_id = run.analysis_id
        run.complete(old, "old")
    new = tmp / "new.md"; new.write_text("fixed")
    out = register_external_analysis_result("PGX", "tsdir", new, "corrected",
                                            supersedes_analysis_id=old_id)
    to = _pg("SELECT tags FROM analysis_history WHERE analysis_id=%s", [old_id])[0][0]
    tn = _pg("SELECT tags, status FROM analysis_history WHERE analysis_id=%s", [out["analysis_id"]])[0]
    checks.append(("supersede_by_id demotes old", "superseded" in (to or [])))
    checks.append(("supersede_by_id promotes new", "canonical" in (tn[0] or [])))
    checks.append(("external new row completed", tn[1] == "completed"))

    print("\n=== Postgres seam verification ===")
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print(f"\n{'ALL PASS' if ok else 'SOME FAILED'} ({sum(p for _, p in checks)}/{len(checks)})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
