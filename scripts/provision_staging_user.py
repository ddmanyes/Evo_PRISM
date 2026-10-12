#!/usr/bin/env python3
"""Plan or create one actor-scoped staging directory (dry-run by default)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.staged_ingest import actor_storage_id  # noqa: E402
from config import settings  # noqa: E402

_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")


def provision_staging_directory(
    user_id: str,
    *,
    staging_root: Path = settings.STAGING_ROOT,
    apply: bool = False,
) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise ValueError("user_id contains unsupported characters or is too long")
    root = Path(staging_root).resolve()
    directory = root / actor_storage_id(user_id)
    if apply:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    return {
        "mode": "applied" if apply else "dry-run",
        "user_id": user_id,
        "storage_id": actor_storage_id(user_id),
        "directory": str(directory),
        "permissions": "0700",
        "exists": directory.is_dir(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--staging-root", type=Path, default=settings.STAGING_ROOT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create the directory. Without this flag, no filesystem change is made.",
    )
    args = parser.parse_args()
    result = provision_staging_directory(
        args.user_id,
        staging_root=args.staging_root,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
