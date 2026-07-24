"""run_pending_migrations.py — replaces entrypoint.sh's "re-run every NN_migrate_schema_v*.py
on every boot" loop, which isn't actually safe: later migrations restructure what earlier
ones created (e.g. v14 moves analysis_artifacts.inline_data into a separate blobs table),
so re-running an old script's own post-migration verification against an already-newer
schema fails (v9's script asserts inline_data exists; v14 already removed it).

Instead: read the current max applied version from schema_migrations (0 if the table/db
doesn't exist yet — first boot), and only exec scripts whose own vNN target is higher.

04_migrate_l1_rrf.py is intentionally excluded from this gating — it operates on the
separate L1 cache db (not schema_migrations-tracked) and is itself idempotent
(ADD COLUMN IF NOT EXISTS), documented safe to run on every boot regardless of version.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH  # noqa: E402

SCRIPTS_DIR = Path(__file__).parent
VERSION_RE = re.compile(r"_v(\d+)")


def current_max_version() -> int:
    if not DUCKDB_PATH.exists():
        return 0
    import duckdb

    try:
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            row = con.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            return row[0] or 0
    except Exception:
        # schema_migrations itself doesn't exist yet (pre-v10 db, or brand new) —
        # treat as version 0 so every migrate_schema_v*.py script is eligible to run.
        return 0


def main() -> int:
    max_applied = current_max_version()
    print(f"[run_pending_migrations] current max applied version: {max_applied}")

    candidates = sorted(
        SCRIPTS_DIR.glob("[0-9][0-9]_migrate_schema_*.py"),
        key=lambda p: int(p.name[:2]),
    )
    for script in candidates:
        m = VERSION_RE.search(script.name)
        if not m:
            print(f"[run_pending_migrations] SKIP (no version in filename): {script.name}")
            continue
        target_version = int(m.group(1))
        if target_version <= max_applied:
            print(f"[run_pending_migrations] skip {script.name} (v{target_version} already applied)")
            continue
        print(f"[run_pending_migrations] running: {script.name} (v{target_version})")
        subprocess.run([sys.executable, str(script)], check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())