"""
Host-native mcseg GPU worker.

Runs on the Mac host (NOT inside Docker) because Docker Desktop on macOS has no
GPU/Metal passthrough into Linux containers — confirmed empirically: inside the
running evo_prism-evo-prism-1 container, torch.cuda.is_available() and
torch.backends.mps.is_available() are both False, so cellpose.core.use_gpu()
is always False there regardless of what torch build is installed.

This watcher polls BIO_DB_ROOT/mcseg_jobs/jobs/ (bind-mounted into the container
as /data/bio_db/mcseg_jobs/jobs/) for job files dropped by
analysis.mcseg_wrapper.run_mcseg_segmentation(), runs the actual cellpose
segmentation using THIS host venv's MPS-enabled torch, and writes back a
done/<job_id>.json marker. Design details: sb note
"Evo-PRISM MCseg GPU Host-Native 混合架構計畫".

Run with:
    ~/.venvs/hermes-bio-memory/bin/python scripts/mcseg_host_watcher.py
(a launchd plist manages this as a long-running service in production).
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT  # noqa: E402
from analysis.mcseg_wrapper import (  # noqa: E402
    _run_mcseg_segmentation_impl,
    _run_mcseg_fullslide_impl,
)

LOG_DIR = BIO_DB_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mcseg_host_watcher] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "mcseg_host_watcher.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("mcseg_host_watcher")

JOBS_DIR = BIO_DB_ROOT / "mcseg_jobs" / "jobs"
DONE_DIR = BIO_DB_ROOT / "mcseg_jobs" / "done"
POLL_INTERVAL_S = 5


def _process_job(job_path: Path) -> None:
    job_id = job_path.stem
    done_path = DONE_DIR / f"{job_id}.json"
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        kind = job.get("kind", "roi_segment")

        if kind == "fullslide":
            # btf_path/binned_dir are absolute host paths (not BIO_DB_ROOT-relative) —
            # full-slide source data commonly lives on external drives never bind-mounted
            # into the container. Only out_dir is BIO_DB_ROOT-relative. See
            # analysis.mcseg_wrapper.run_mcseg_fullslide for the dispatcher side.
            btf_path = Path(job["btf_path"])
            binned_dir = Path(job["binned_dir"])
            out_dir = BIO_DB_ROOT / job["out_dir"]
            params = job["params"]

            logger.info(f"job {job_id}: running mcseg FULLSLIDE on {btf_path}")
            result = _run_mcseg_fullslide_impl(btf_path, binned_dir, out_dir, params)

            done_path.write_text(json.dumps({"ok": True, **result}), encoding="utf-8")
            logger.info(f"job {job_id}: done (fullslide), {result.get('n_cells'):,} cells")
        else:
            he_crop_path = BIO_DB_ROOT / job["he_crop_path"]
            out_mask_path = BIO_DB_ROOT / job["out_mask_path"]
            params = job["params"]

            logger.info(f"job {job_id}: running mcseg on {he_crop_path}")
            _run_mcseg_segmentation_impl(he_crop_path, out_mask_path, params)

            done_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            logger.info(f"job {job_id}: done, wrote {out_mask_path}")
    except Exception as exc:
        logger.exception(f"job {job_id}: failed")
        done_path.write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
    finally:
        # job file is single-use control signal, not the result — safe to remove
        # regardless of success/failure (result/error lives in done_path).
        job_path.unlink(missing_ok=True)


def main() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"mcseg host watcher started. BIO_DB_ROOT={BIO_DB_ROOT}  watching {JOBS_DIR}")

    import torch
    from cellpose import core as cellpose_core

    logger.info(
        f"torch={torch.__version__}  mps_available={torch.backends.mps.is_available()}  "
        f"cellpose.use_gpu()={cellpose_core.use_gpu()}"
    )

    while True:
        # KINGSTON is exFAT — macOS creates "._*" AppleDouble sidecar files for
        # every write, which also match "*.json" and are not valid job payloads.
        for job_path in sorted(JOBS_DIR.glob("*.json")):
            if job_path.name.startswith("._"):
                continue
            _process_job(job_path)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
