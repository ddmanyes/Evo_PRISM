"""
Phase 2C — Derive day_section (multi-timepoint tissue block) column for L2 Parquet

Many skin specimens in this project embed multiple time-point sections
(e.g. Day0/1/2/3 of a treatment time-course) side by side in a single
FFPE block, imaged as one Visium HD capture area. The section boundaries
are NOT recorded as a separate obs column by 02_spatial_to_parquet.py —
they show up only as gaps in the spatial_x coordinate distribution.

This script detects those gaps automatically and writes a `day_section`
column back into the sample's existing obs_metadata.parquet, so
downstream tools (bio_run_sc_clustering group_by, bio_compute_spatial_nn_distance
filters, etc.) can group by timepoint without re-deriving cutpoints by
hand each time.

Origin: dpcp01_vh_v114_02_hd_r004 case (2026-07-23) — confirmed against
the source manuscript's own methods text ("...embedded into a single
FFPE block for integrated analysis") that the gaps correspond exactly
to the described timepoints. See sb note:
10-projects/lcdda-bar-ep-串聯測試計畫-皮膚過敏vs發炎對毛囊生長與免疫細胞角色.md

⚠️ Not universal: only run this for samples known/suspected to be
multi-section captures (check sample_registry.notes or ask the user).
Running it on a genuinely single-section sample will just find no real
gaps and produce a meaningless single-bucket label — inspect the printed
histogram/gap report before trusting the output.

⚠️ THIS SCRIPT NEVER KNOWS THE PHYSICAL LEFT-TO-RIGHT vs RIGHT-TO-LEFT
DIRECTION — it only finds gaps and numbers the resulting buckets in
ascending-x order. Which end is Day0/control is a wet-lab fact that
must come from the person who mounted the slide, not from the
coordinates. On dpcp01, the initial run assumed ascending-x = Day0→Day3
and got it backwards (the user later clarified the real order is
right-to-left: control first). ALWAYS confirm slide orientation/mounting
order with the user BEFORE trusting the label assignment — pass
--labels in whatever order matches their answer (e.g. --labels
Day3,Day2,Day1,Day0 if ascending-x is actually right-to-left).

Usage:
    uv run python scripts/02c_derive_day_section.py --sample-id dpcp01_vh_v114_02_hd_r004 --dry-run
    # confirm direction with the user, THEN write for real with labels in the confirmed order:
    uv run python scripts/02c_derive_day_section.py --sample-id dpcp01_vh_v114_02_hd_r004 --n-sections 4 --labels Day3,Day2,Day1,Day0
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import L2_ROOT

# bin-pitch noise floor: real section gaps are far larger than the
# ~1 bin-width spacing between adjacent in-tissue spots (empirically
# ~14 units in dpcp01's 8µm-bin coordinate system; scale-independent
# because we always take the top-N largest gaps, not an absolute threshold)


def detect_section_gaps(x: np.ndarray, n_sections: int, min_side_frac: float = 0.02) -> list[float]:
    """Return n_sections-1 cutpoints (gap midpoints), sorted ascending.

    A naive "largest N gaps by size" picks up spurious edge artifacts: a
    handful of stray outlier points past a huge empty gap (e.g. a few
    mis-registered spots beyond the true tissue edge) can create a gap
    bigger than any real inter-section boundary. Confirmed on dpcp01:
    the single largest gap (1489 units) was 3 trailing noise points, not
    a section edge — it would have swallowed a real, smaller boundary
    (624 units) if not filtered out.

    Fix: only consider a gap as a candidate section boundary if BOTH
    sides of it contain at least `min_side_frac` of all points. This
    rejects "big gap, tiny remainder" edge noise while still finding
    real boundaries between substantially-sized sections.
    """
    xs = np.sort(x)
    diffs = np.diff(xs)
    n = len(xs)
    n_gaps = n_sections - 1
    if n_gaps <= 0:
        return []

    min_side = max(1, int(min_side_frac * n))
    order = np.argsort(diffs)[::-1]
    candidates = [i for i in order if min(i + 1, n - i - 1) >= min_side]

    if len(candidates) < n_gaps:
        raise ValueError(
            f"Only found {len(candidates)} gap(s) with both sides >= "
            f"{min_side_frac:.1%} of {n} points, need {n_gaps} for "
            f"--n-sections {n_sections}. Lower --min-side-frac or verify "
            f"this sample really has {n_sections} sections."
        )

    top_idx = sorted(candidates[:n_gaps])
    cutpoints = [(xs[i] + xs[i + 1]) / 2 for i in top_idx]
    return cutpoints


def derive(
    sample_id: str, n_sections: int, labels: list[str], min_side_frac: float = 0.02, dry_run: bool = False
) -> dict:
    obs_path = L2_ROOT / sample_id / "obs_metadata.parquet"
    if not obs_path.exists():
        raise FileNotFoundError(f"L2 obs_metadata.parquet not found for {sample_id}: {obs_path}")

    df = pd.read_parquet(obs_path)
    if "spatial_x" not in df.columns:
        raise ValueError(f"{obs_path} has no spatial_x column — not a spatial sample?")

    x = df["spatial_x"].values
    cutpoints = detect_section_gaps(x, n_sections, min_side_frac=min_side_frac)
    bins = [-np.inf, *cutpoints, np.inf]

    if len(labels) != n_sections:
        raise ValueError(f"--labels must have exactly {n_sections} entries, got {len(labels)}")

    day_section = pd.cut(x, bins=bins, labels=labels).astype(str)
    counts = pd.Series(day_section).value_counts().reindex(labels)

    print(f"[{sample_id}] detected cutpoints: {[round(c, 1) for c in cutpoints]}")
    print(f"[{sample_id}] section counts:\n{counts}")
    print(
        "[{}] ⚠️ inspect the counts above before trusting this — a section with "
        "far fewer bins than its neighbors may be an incomplete scan/segmentation "
        "gap rather than a real smaller section (see dpcp01 mcseg-vs-L2 discrepancy "
        "in the origin case note).".format(sample_id)
    )

    if dry_run:
        print(f"[{sample_id}] --dry-run: not writing back.")
        return {"cutpoints": cutpoints, "counts": counts.to_dict(), "written": False}

    backup_path = obs_path.with_name(f"{obs_path.name}.bak_{date.today().isoformat().replace('-', '')}")
    if not backup_path.exists():
        shutil.copy2(obs_path, backup_path)
        print(f"[{sample_id}] backed up original to {backup_path}")
    else:
        print(f"[{sample_id}] backup already exists at {backup_path}, not overwriting")

    df["day_section"] = day_section
    df.to_parquet(obs_path, index=False)
    print(f"[{sample_id}] wrote day_section column back to {obs_path}")

    return {"cutpoints": cutpoints, "counts": counts.to_dict(), "written": True, "backup_path": str(backup_path)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sample-id", required=True)
    p.add_argument("--n-sections", type=int, default=4, help="number of time-point sections (default 4: Day0-3)")
    p.add_argument(
        "--labels",
        default="Day0,Day1,Day2,Day3",
        help="comma-separated labels, left-to-right, must match --n-sections count",
    )
    p.add_argument("--dry-run", action="store_true", help="only print detected cutpoints/counts, don't write")
    p.add_argument(
        "--min-side-frac",
        type=float,
        default=0.02,
        help="reject a gap as a section boundary unless both sides hold >= this fraction of total points (default 0.02)",
    )
    args = p.parse_args()

    labels = [s.strip() for s in args.labels.split(",")]
    derive(args.sample_id, args.n_sections, labels, min_side_frac=args.min_side_frac, dry_run=args.dry_run)
