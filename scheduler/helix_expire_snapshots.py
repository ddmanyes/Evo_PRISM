"""
HELIX Snapshot Expiration Scheduler — Ebbinghaus forgetting curve.

Downsamples diagnosis_img in tool_stabilization_log progressively:
  closed_at > HELIX_SNAPSHOT_DECAY_DAYS_1 (default 180d) → factor=0.5  (~25 VLM tokens)
  closed_at > HELIX_SNAPSHOT_DECAY_DAYS_2 (default 365d) → factor=0.25 (~6 VLM tokens)

Spatial layout survives at lower resolution; fine text detail fades —
mirroring biological memory degradation over time.

Install launchd plist (see docs/launchd_helix_expire.plist.example) to run
weekly at 04:00.  Safe to run manually: already-downsampled rows are skipped.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    DUCKDB_PATH,
    HELIX_SNAPSHOT_DECAY_DAYS_1,
    HELIX_SNAPSHOT_DECAY_DAYS_2,
)

logger = logging.getLogger(__name__)


def expire_snapshots(db_path: Path = DUCKDB_PATH, dry_run: bool = False) -> dict:
    """Downsample diagnosis_img for old closed stabilization iterations.

    Returns a summary dict:
        {"checked": int, "downsampled_half": int, "downsampled_quarter": int, "skipped": int}
    """
    from analysis.tool_visualizer import downsample_snapshot

    now = datetime.now(timezone.utc)
    cutoff_half = now - timedelta(days=HELIX_SNAPSHOT_DECAY_DAYS_1)
    cutoff_quarter = now - timedelta(days=HELIX_SNAPSHOT_DECAY_DAYS_2)

    stats: dict[str, int] = {
        "checked": 0,
        "downsampled_half": 0,
        "downsampled_quarter": 0,
        "skipped": 0,
    }

    with duckdb.connect(str(db_path)) as con:
        rows = con.execute(
            """
            SELECT log_id, closed_at, diagnosis_img
            FROM   tool_stabilization_log
            WHERE  closed_at IS NOT NULL
              AND  diagnosis_img IS NOT NULL
              AND  diagnosis_img LIKE 'data:image/png;base64,%'
            ORDER  BY closed_at ASC
            """
        ).fetchall()

        for log_id, closed_at, diagnosis_img in rows:
            stats["checked"] += 1

            if closed_at is None:
                stats["skipped"] += 1
                continue
            # Normalise to UTC-aware
            if hasattr(closed_at, "tzinfo") and closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)

            # Determine target downsample factor based on age
            if closed_at < cutoff_quarter:
                target_factor = 0.25
                bucket = "downsampled_quarter"
            elif closed_at < cutoff_half:
                target_factor = 0.5
                bucket = "downsampled_half"
            else:
                stats["skipped"] += 1
                continue

            # Skip already-small images to avoid double downsampling.
            # A 640×640 PNG base64 ≈ 133K chars; 320×320 ≈ 33K; 160×160 ≈ 8K.
            b64_len = len(diagnosis_img) - len("data:image/png;base64,")
            already_small = (target_factor == 0.5 and b64_len < 40_000) or (
                target_factor == 0.25 and b64_len < 12_000
            )
            if already_small:
                stats["skipped"] += 1
                continue

            logger.info(
                "expire_snapshots: log_id=%s  age=%s  factor=%.2f",
                log_id,
                str(closed_at)[:10],
                target_factor,
            )

            if not dry_run:
                try:
                    new_uri = downsample_snapshot(diagnosis_img, factor=target_factor)
                    con.execute(
                        "UPDATE tool_stabilization_log SET diagnosis_img = ? WHERE log_id = ?",
                        [new_uri, str(log_id)],
                    )
                    stats[bucket] += 1
                except Exception as exc:
                    logger.warning("expire_snapshots: failed for log_id=%s — %s", log_id, exc)
                    stats["skipped"] += 1
            else:
                stats[bucket] += 1

        if not dry_run:
            con.execute("CHECKPOINT")

    logger.info("expire_snapshots: %s", stats)
    return stats


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="HELIX snapshot expiration (Ebbinghaus forgetting curve)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    result = expire_snapshots(dry_run=args.dry_run)
    print("\n--- helix_expire_snapshots ---")
    print(f"  Checked:              {result['checked']}")
    print(f"  Downsampled to 0.5x:  {result['downsampled_half']}")
    print(f"  Downsampled to 0.25x: {result['downsampled_quarter']}")
    print(f"  Skipped (too new / already small): {result['skipped']}")
    if args.dry_run:
        print("\n[DRY RUN — no changes written]")
