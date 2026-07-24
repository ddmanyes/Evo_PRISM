import sys
from pathlib import Path
import logging
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import MSSEG_PATH, BIO_DB_ROOT  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402

if str(MSSEG_PATH) not in sys.path:
    sys.path.insert(0, str(MSSEG_PATH))

logger = logging.getLogger("evo_prism.mcseg_wrapper")


def _compute_tiff_scale(btf_image_path: Path, binned_dir: Path) -> tuple[float, int, int]:
    """
    Compute scale factor: raw_TIFF_pixels = virtual_fullres_pixels × tiff_scale.

    Derived from tissue_hires_image.png dimensions + scalefactors_json.json +
    raw TIFF dimensions.  Returns (1.0, 0, 0) if metadata is unavailable.

    Returns
    -------
    (tiff_scale, vfr_w, vfr_h)
    """
    import json
    import tifffile
    from PIL import Image

    sf_path = Path(binned_dir) / "spatial" / "scalefactors_json.json"
    hires_path = Path(binned_dir) / "spatial" / "tissue_hires_image.png"
    if not (sf_path.exists() and hires_path.exists()):
        logger.warning("scalefactors_json / tissue_hires_image not found — using tiff_scale=1.0")
        return 1.0, 0, 0

    with open(sf_path) as f:
        sf = json.load(f)
    hires_scalef = float(sf.get("tissue_hires_scalef", 1.0))

    hires_img = Image.open(str(hires_path))
    W_hires, H_hires = hires_img.size  # PIL returns (width, height)
    hires_img.close()

    W_vfr = W_hires / hires_scalef
    H_vfr = H_hires / hires_scalef

    with tifffile.TiffFile(str(btf_image_path)) as tf:
        p = tf.pages[0]
        H_tiff, W_tiff = p.imagelength, p.imagewidth

    scale_w = W_tiff / W_vfr
    scale_h = H_tiff / H_vfr
    tiff_scale = (scale_w + scale_h) / 2
    logger.info(
        f"TIFF scale factor: {tiff_scale:.4f}  "
        f"(virtual_fullres {W_vfr:.0f}×{H_vfr:.0f} → TIFF {W_tiff}×{H_tiff})"
    )
    return tiff_scale, round(W_vfr), round(H_vfr)


def crop_visium_hd_roi(
    btf_image_path: str | Path, binned_dir: str | Path, roi_dict: dict, out_roi_dir: str | Path
):
    """
    Crop H&E gigapixel image and 2µm AnnData coordinate matrix for the selected ROI.

    Auto-detects the scale factor between virtual_fullres (CytAssist pixel space used
    by pxl_col/row_in_fullres) and the raw H&E TIFF.  The H&E crop is produced at full
    TIFF resolution for best segmentation quality; tiff_scale is persisted in
    crop_meta.json so that mcseg_wrapper.run_mcseg_segmentation can downscale the
    resulting mask back to virtual_fullres coordinates for consistent downstream use.
    """
    from backend.src.roi.extractor import (
        load_visium_adata,
        subset_anndata_roi,
        roi_to_fullres_px,
        read_btf_crop,
    )
    import json
    import tifffile

    out_roi_dir = Path(out_roi_dir)
    out_roi_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading 2µm Visium HD AnnData from {binned_dir}")
    adata = load_visium_adata(binned_dir, bin_size="002")

    logger.info(f"Subsetting AnnData to ROI: {roi_dict['name']}")
    sub = subset_anndata_roi(adata, roi_dict, binned_dir=binned_dir)

    adata_out_path = out_roi_dir / "adata_002um.h5ad"
    sub.write_h5ad(str(adata_out_path))
    logger.info(f"Saved cropped AnnData: {adata_out_path} ({sub.n_obs:,} bins)")

    # Compute scale factor: virtual_fullres coords → raw TIFF pixel coords
    tiff_scale, _vfr_w_full, _vfr_h_full = _compute_tiff_scale(Path(btf_image_path), Path(binned_dir))

    logger.info(f"Cropping H&E BTF image from {btf_image_path}")
    x0_vfr, y0_vfr, w_vfr, h_vfr = roi_to_fullres_px(roi_dict)
    x0_tiff = round(x0_vfr * tiff_scale)
    y0_tiff = round(y0_vfr * tiff_scale)
    w_tiff = round(w_vfr * tiff_scale)
    h_tiff = round(h_vfr * tiff_scale)

    crop, _, _ = read_btf_crop(btf_image_path, x0_tiff, y0_tiff, w_tiff, h_tiff)

    he_out_path = out_roi_dir / "he_crop.tif"
    tifffile.imwrite(str(he_out_path), crop)
    logger.info(
        f"Saved H&E ROI crop: {he_out_path}  shape={crop.shape}  tiff_scale={tiff_scale:.4f}"
    )

    # Persist scale info so run_mcseg_segmentation can downscale the mask
    (out_roi_dir / "crop_meta.json").write_text(
        json.dumps({"tiff_scale": tiff_scale, "vfr_w": w_vfr, "vfr_h": h_vfr}),
        encoding="utf-8",
    )

    return adata_out_path, he_out_path


def run_mcseg_segmentation(he_crop_path: str | Path, out_mask_path: str | Path, params: dict):
    """
    Dispatch Cellpose GPU segmentation to the host-native mcseg watcher.

    Docker Desktop on macOS has no GPU/Metal passthrough into Linux containers
    (confirmed: torch.cuda/mps both False in-container regardless of torch build),
    so the actual compute in `_run_mcseg_segmentation_impl` runs on the host instead,
    via a file-drop job queue under BIO_DB_ROOT/mcseg_jobs/ (bind-mounted, so this
    container and the host watcher — scripts/mcseg_host_watcher.py, launchd-managed,
    ~/.venvs/hermes-bio-memory with MPS-enabled torch — see the same files).
    Design: sb note "Evo-PRISM MCseg GPU Host-Native 混合架構計畫".

    On a Windows/Linux host with a real Nvidia GPU and Docker GPU passthrough
    (nvidia-container-toolkit), this dispatch indirection is unnecessary — call
    `_run_mcseg_segmentation_impl` directly in that deployment instead.
    """
    import json
    import time
    import uuid

    he_crop_path = Path(he_crop_path)
    out_mask_path = Path(out_mask_path)

    jobs_dir = BIO_DB_ROOT / "mcseg_jobs" / "jobs"
    done_dir = BIO_DB_ROOT / "mcseg_jobs" / "done"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(BIO_DB_ROOT))
        except ValueError:
            raise ValueError(
                f"mcseg host-native dispatch requires paths under BIO_DB_ROOT "
                f"(the bind-mounted share) so both container and host resolve the "
                f"same file; got {p}, BIO_DB_ROOT={BIO_DB_ROOT}"
            )

    job_id = uuid.uuid4().hex[:12]
    job_path = jobs_dir / f"{job_id}.json"
    done_path = done_dir / f"{job_id}.json"

    job_path.write_text(
        json.dumps(
            {
                "he_crop_path": _rel(he_crop_path),
                "out_mask_path": _rel(out_mask_path),
                "params": params,
            }
        ),
        encoding="utf-8",
    )
    logger.info(f"mcseg job {job_id} dispatched to host watcher (waiting for {done_path.name})")

    # Full-slide runs are documented as taking hours on GPU; give plenty of room
    # but still fail loudly rather than hang forever if the host watcher is down.
    timeout_s = int(params.get("host_timeout_s", 4 * 3600))
    poll_s = 5
    waited = 0
    while not done_path.exists():
        time.sleep(poll_s)
        waited += poll_s
        if waited >= timeout_s:
            job_path.unlink(missing_ok=True)
            raise TimeoutError(
                f"mcseg host watcher did not finish job {job_id} within {timeout_s}s — "
                f"is the launchd-managed watcher (~/.venvs/hermes-bio-memory, "
                f"scripts/mcseg_host_watcher.py) running on the host?"
            )

    result = json.loads(done_path.read_text(encoding="utf-8"))
    done_path.unlink(missing_ok=True)

    if not result.get("ok"):
        raise RuntimeError(f"mcseg host watcher job {job_id} failed: {result.get('error')}")

    logger.info(f"mcseg job {job_id} completed by host watcher (waited ~{waited}s)")
    return out_mask_path


@register_tool_on_import(
    tool_name="mcseg_segmentation_core",
    version="1.1.0",
    description=(
        "多重通道 Cellpose 分割核心實作（run_tiled_mcseg_v2）。2026-07-16 起在 Mac 主機部署"
        "上由 host-native venv(~/.venvs/hermes-bio-memory, MPS)透過 job queue 執行，"
        "非受 container 的 uv.lock 管控環境——env_hash 溯源不完整涵蓋此路徑，"
        "詳見 evo-prism-mcseg-gpu-host-native-混合架構計畫（sb）。"
    ),
)
def _run_mcseg_segmentation_impl(he_crop_path: str | Path, out_mask_path: str | Path, params: dict):
    """
    Run multi-pass Cellpose ensemble segmentation with Voronoi-constrained expansion on the H&E crop.
    Uses run_tiled_mcseg_v2 (identical to MSseg CLI full-slide pipeline) for consistency.
    Tile-based processing supports arbitrarily large ROIs and includes CUDA OOM fallback to CPU.

    Executed either in-process (non-Docker / GPU-passthrough deployments) or by
    scripts/mcseg_host_watcher.py on behalf of the container-side dispatcher above.
    """
    from backend.src.segmentation.cellpose_runner import run_tiled_mcseg_v2
    from cellpose import core as cellpose_core
    import cv2

    he_crop_path = Path(he_crop_path)
    out_mask_path = Path(out_mask_path)

    logger.info(f"Loading H&E crop: {he_crop_path}")
    img_rgb = cv2.imread(str(he_crop_path))
    if img_rgb is None:
        raise FileNotFoundError(f"Could not load H&E crop from {he_crop_path}")
    img_rgb = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2RGB)

    # Confirm GPU availability at wrapper level (mirrors cellpose_runner internal check)
    gpu_requested = bool(params.get("use_gpu", True))
    gpu_available = cellpose_core.use_gpu()
    use_gpu = gpu_requested and gpu_available

    if gpu_requested and not gpu_available:
        logger.warning("GPU was requested but is NOT available (CUDA/MPS). Falling back to CPU.")
    else:
        logger.info(
            f"GPU status: requested={gpu_requested}, available={gpu_available}, effective={use_gpu}"
        )

    # Tiled segmentation params (consistent with MSseg CLI defaults)
    tile_size = int(params.get("tile_size", 1024))
    overlap = int(params.get("overlap", 128))

    # Build cfg dict as expected by run_tiled_mcseg_v2(img, cfg)
    cfg = {
        # GPU & 後處理
        "use_gpu": use_gpu,
        "batch_size": int(params.get("batch_size", 4)),
        "voronoi_distance": int(params.get("voronoi_distance", 9)),
        "flow_threshold": float(params.get("flow_threshold", 0.4)),
        "min_size": int(params.get("min_size", 20)),
        "max_size": int(params.get("max_size", 6000)),
        "use_transcript_rescue": False,
        "clahe_clip_limit": float(params.get("clahe_clip_limit", 3.0)),
        # cyto3 Pass 1-4 直徑與 cellprob
        "dia_small": float(params.get("dia_small", 13.0)),
        "dia_mid": float(params.get("dia_mid", 17.0)),
        "dia_large": float(params.get("dia_large", 22.0)),
        "use_hematoxylin": bool(params.get("use_hematoxylin", True)),
        "cellprob_threshold": float(params.get("cellprob_threshold", -2.0)),
        # cpsam Pass 5-7（論文 7-pass）獨立直徑與 cellprob
        "use_cpsam": bool(params.get("use_cpsam", False)),
        "dia_cpsam_auto": float(params.get("dia_cpsam_auto", 0.0)),
        "dia_cpsam_small": float(params.get("dia_cpsam_small", 16.0)),
        "cellprob_cpsam_auto": float(params.get("cellprob_cpsam_auto", -1.0)),
        "cellprob_cpsam_small": float(params.get("cellprob_cpsam_small", -3.0)),
        "cellprob_cpsam_hema": float(params.get("cellprob_cpsam_hema", -1.0)),
    }

    passes = 7 if cfg["use_cpsam"] else 4
    logger.info(
        f"Running Tiled MCseg V2 ({passes}-pass). "
        f"GPU={use_gpu}, tile={tile_size}px, overlap={overlap}px, "
        f"Voronoi_D={cfg['voronoi_distance']}px"
    )

    # Progress callback for tiled segmentation
    def _progress(p: float, msg: str) -> None:
        bar = "█" * int(p * 20) + "░" * (20 - int(p * 20))
        logger.info(f"  [{bar}] {p * 100:.0f}%  {msg}")

    # run_tiled_mcseg_v2: same function used by MSseg CLI full-slide pipeline
    # Supports arbitrarily large images via tile-based processing
    # Includes CUDA OOM fallback to CPU per tile
    mask = run_tiled_mcseg_v2(
        img_rgb,
        cfg,
        tile_size=tile_size,
        overlap=overlap,
        progress_callback=_progress,
    )

    out_mask_path.parent.mkdir(parents=True, exist_ok=True)

    # If H&E was cropped at TIFF resolution (tiff_scale > 1), downscale mask back to
    # virtual_fullres dimensions so counter.py can map bins (virtual_fullres coords) correctly.
    import json

    meta_path = out_mask_path.parent / "crop_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        tiff_scale = float(meta.get("tiff_scale", 1.0))
        vfr_h = int(meta.get("vfr_h", mask.shape[0]))
        vfr_w = int(meta.get("vfr_w", mask.shape[1]))
        if abs(tiff_scale - 1.0) > 0.01:
            mask = _downscale_mask_array(mask, vfr_h, vfr_w)

    np.save(str(out_mask_path), mask)
    logger.info(
        f"Saved segmentation mask: {out_mask_path} | shape={mask.shape} | cells={mask.max():,}"
    )

    # Save visual TIF overlay
    import tifffile

    tif_dtype = np.uint16 if mask.max() <= 65535 else np.uint32
    overlay_path = out_mask_path.parent / "segmentation_masks.tif"
    tifffile.imwrite(str(overlay_path), mask.astype(tif_dtype), compression="zlib")
    logger.info(f"Saved TIF overlay: {overlay_path}")

    return out_mask_path


def _downscale_mask_array(mask: np.ndarray, vfr_h: int, vfr_w: int) -> np.ndarray:
    """Nearest-neighbour downscale a label mask, preserving integer cell IDs."""
    from skimage.transform import resize as sk_resize

    logger.info(f"Downscaling mask {mask.shape} → ({vfr_h},{vfr_w})")
    mask = sk_resize(
        mask,
        (vfr_h, vfr_w),
        order=0,  # nearest-neighbour — preserves integer labels
        preserve_range=True,
        anti_aliasing=False,
    ).astype(np.uint32)
    logger.info(f"Downscaled mask: shape={mask.shape}, cells={mask.max():,}")
    return mask


def downscale_mask_to_vfr(
    mask_path: str | Path,
    tiff_scale: float,
    vfr_w: int,
    vfr_h: int,
    out_path: str | Path,
) -> Path:
    """
    Downscale a raw-TIFF-resolution segmentation mask to virtual_fullres dimensions.

    RNA counting (count_rna_per_cell) requires the mask and the 2µm bin coordinates
    to live in the same pixel space (virtual_fullres, offset-only alignment, no scale
    conversion). Full-slide segmentation runs directly on the raw TIFF, so its mask
    must be downscaled here before counting — mirrors the downscale step already
    applied to ROI masks in run_mcseg_segmentation.
    """
    mask_path = Path(mask_path)
    out_path = Path(out_path)

    mask = np.load(str(mask_path))
    if abs(tiff_scale - 1.0) > 0.01:
        mask = _downscale_mask_array(mask, vfr_h, vfr_w)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(out_path), mask)
    logger.info(f"Saved vfr-space mask: {out_path} | shape={mask.shape} | cells={mask.max():,}")
    return out_path


def run_rna_counting(
    adata_002um_path: str | Path,
    mask_path: str | Path,
    roi_dict: dict,
    out_cells_path: str | Path,
    dilation_px: int = 6,
):
    """
    Perform RNA counting by mapping 2µm spatial bins to segmented Cellpose cell masks.
    """
    from backend.src.cellpose_counter.counter import count_rna_per_cell

    adata_002um_path = Path(adata_002um_path)
    mask_path = Path(mask_path)
    out_cells_path = Path(out_cells_path)

    roi_x_px = int(roi_dict.get("x", 0))
    roi_y_px = int(roi_dict.get("y", 0))
    pixel_size_um = float(roi_dict.get("pixel_size_um", 0.2737))

    logger.info(f"Running count_rna_per_cell with dilation={dilation_px}px")
    adata_cells = count_rna_per_cell(
        adata_path=adata_002um_path,
        mask_path=mask_path,
        roi_x_px=roi_x_px,
        roi_y_px=roi_y_px,
        pixel_size_um=pixel_size_um,
        dilation_px=dilation_px,
    )

    out_cells_path.parent.mkdir(parents=True, exist_ok=True)
    adata_cells.write_h5ad(str(out_cells_path))
    logger.info(f"Saved single-cell AnnData: {out_cells_path} ({adata_cells.n_obs} cells)")

    return out_cells_path


def run_fullslide_rna_counting(
    binned_dir: str | Path,
    mask_vfr_path: str | Path,
    out_cells_path: str | Path,
    pixel_size_um: float = 0.2737,
    dilation_px: int = 6,
):
    """
    RNA counting for a full-tissue (non-ROI) segmentation mask.

    A full slide is just "the ROI is the whole tissue, offset (0,0)": loads every
    in-tissue 2µm bin (no cropping) and maps it to the vfr-space mask via the same
    count_rna_per_cell used by the ROI pipeline.
    """
    from backend.src.roi.extractor import load_visium_adata

    binned_dir = Path(binned_dir)
    out_cells_path = Path(out_cells_path)

    logger.info(f"Loading full-tissue 2µm AnnData from {binned_dir}")
    adata = load_visium_adata(binned_dir, bin_size="002")
    n_bins_total = int(adata.n_obs)
    logger.info(f"  full-tissue bins: n_obs={n_bins_total:,}")

    adata_path = out_cells_path.parent / "adata_002um_fullslide.h5ad"
    adata_path.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(str(adata_path))

    out_path = run_rna_counting(
        adata_002um_path=adata_path,
        mask_path=mask_vfr_path,
        roi_dict={"x": 0, "y": 0, "pixel_size_um": pixel_size_um},
        out_cells_path=out_cells_path,
        dilation_px=dilation_px,
    )

    import scanpy as sc

    adata_cells = sc.read_h5ad(str(out_path))
    n_bins_assigned = int(adata_cells.obs["n_bins"].sum())

    return out_path, n_bins_total, n_bins_assigned


def run_mcseg_fullslide(
    btf_path: str | Path, binned_dir: str | Path, out_dir: str | Path, params: dict
) -> dict:
    """
    Dispatch full-slide Cellpose segmentation + RNA counting to the host-native mcseg watcher.

    Companion to run_mcseg_segmentation() above, for the whole-slide (non-ROI) case.
    Key difference: btf_path/binned_dir here are NOT required to live under BIO_DB_ROOT.
    Unlike the ROI path (where crop_visium_hd_roi already produced a bind-mounted
    he_crop.tif that both container and host can see), full-slide segmentation reads
    the raw source TIFF/binned_outputs directly — which commonly live on external
    drives never bind-mounted into the container (e.g. /Volumes/KINGSTON/Bioinfo_Projects).
    The host watcher runs natively on this Mac, so it can read any host filesystem path
    directly; only out_dir (where results are written) must be under BIO_DB_ROOT, since
    the caller (server/agent_bulk.py, in-container) needs a valid result_path for
    analysis_history and downstream container-side tools.

    2026-07-17: added to fix bio_run_mcseg_fullslide, which previously imported
    run_tiled_mcseg_v2 directly and ran in-process inside the container — broken because
    the container has neither GPU access nor a mount for external source data. See sb note
    "Evo-PRISM MCseg GPU Host-Native 混合架構計畫" (2026-07-17 續篇) for the investigation.
    """
    import json
    import time
    import uuid

    btf_path = Path(btf_path)
    binned_dir = Path(binned_dir)
    out_dir = Path(out_dir)

    jobs_dir = BIO_DB_ROOT / "mcseg_jobs" / "jobs"
    done_dir = BIO_DB_ROOT / "mcseg_jobs" / "done"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)

    try:
        out_dir_rel = str(out_dir.relative_to(BIO_DB_ROOT))
    except ValueError:
        raise ValueError(
            f"mcseg fullslide dispatch requires out_dir under BIO_DB_ROOT "
            f"(the bind-mounted share) so the container can see results afterwards; "
            f"got {out_dir}, BIO_DB_ROOT={BIO_DB_ROOT}"
        )

    job_id = uuid.uuid4().hex[:12]
    job_path = jobs_dir / f"{job_id}.json"
    done_path = done_dir / f"{job_id}.json"

    job_path.write_text(
        json.dumps(
            {
                "kind": "fullslide",
                # absolute host paths, read directly by the host watcher — no
                # BIO_DB_ROOT translation (unlike he_crop_path/out_mask_path above)
                "btf_path": str(btf_path),
                "binned_dir": str(binned_dir),
                "out_dir": out_dir_rel,
                "params": params,
            }
        ),
        encoding="utf-8",
    )
    logger.info(
        f"mcseg fullslide job {job_id} dispatched to host watcher (waiting for {done_path.name})"
    )

    # Full-slide runs are documented as taking hours on GPU; give plenty of room
    # but still fail loudly rather than hang forever if the host watcher is down.
    timeout_s = int(params.get("host_timeout_s", 6 * 3600))
    poll_s = 5
    waited = 0
    while not done_path.exists():
        time.sleep(poll_s)
        waited += poll_s
        if waited >= timeout_s:
            job_path.unlink(missing_ok=True)
            raise TimeoutError(
                f"mcseg host watcher did not finish fullslide job {job_id} within {timeout_s}s — "
                f"is the launchd-managed watcher (~/.venvs/hermes-bio-memory, "
                f"scripts/mcseg_host_watcher.py) running on the host?"
            )

    result = json.loads(done_path.read_text(encoding="utf-8"))
    done_path.unlink(missing_ok=True)

    if not result.get("ok"):
        raise RuntimeError(f"mcseg host watcher fullslide job {job_id} failed: {result.get('error')}")

    logger.info(f"mcseg fullslide job {job_id} completed by host watcher (waited ~{waited}s)")
    return result


@register_tool_on_import(
    tool_name="mcseg_fullslide_pipeline_core",
    version="1.0.0",
    description=(
        "全片(非 ROI)Cellpose 分割 + downscale + RNA counting 核心實作(run_tiled_mcseg_v2"
        " 全片版)。2026-07-17 新增,由 host-native venv(~/.venvs/hermes-bio-memory, MPS)"
        "透過 job queue 執行(見 run_mcseg_fullslide dispatcher),取代原本"
        " server/agent_bulk.py._exec_bio_run_mcseg_fullslide 直接在容器內執行的舊路徑"
        "(容器無 GPU、也讀不到容器外部原始資料)。非受 container 的 uv.lock 管控環境,"
        "詳見 evo-prism-mcseg-gpu-host-native-混合架構計畫（sb）2026-07-17 續篇。"
    ),
)
def _run_mcseg_fullslide_impl(
    btf_path: str | Path, binned_dir: str | Path, out_dir: str | Path, params: dict
) -> dict:
    """
    Full-slide Cellpose segmentation + downscale + RNA counting, running host-native.

    btf_path/binned_dir may be anywhere on the host filesystem — this function only
    ever runs on the host (in-process on non-Docker/GPU-passthrough deployments, or by
    scripts/mcseg_host_watcher.py on behalf of run_mcseg_fullslide's dispatcher above),
    so it always has full local filesystem access, unlike the container.
    """
    import tifffile
    import numpy as np

    btf_path = Path(btf_path)
    binned_dir = Path(binned_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sample_id = params.get("sample_id", "default_sample")
    tile_size = int(params.get("tile_size", 1024))
    overlap = int(params.get("overlap", 128))

    logger.info(f"Memory-mapping full-slide BTF: {btf_path}")
    try:
        # memmap avoids loading multi-GB BTFs entirely into RAM; the OS pages in
        # only the tiles that run_tiled_mcseg_v2 touches.
        img = tifffile.memmap(str(btf_path), mode="r")
    except ValueError:
        # BigTIFF tiled (non-contiguous) layout can't be memmapped directly —
        # fall back to a full decode.
        logger.warning(
            "TIFF is not memory-mappable (tiled/non-contiguous layout) — "
            "falling back to full in-RAM decode via tifffile.imread()."
        )
        img = tifffile.imread(str(btf_path))

    cfg = {
        # tile_cache is keyed by sample_id — without it, run_tiled_mcseg_v2 falls back
        # to a shared "default_sample" bucket and different samples' tile caches collide
        "sample_id": sample_id,
        "use_gpu": bool(params.get("use_gpu", True)),
        "batch_size": int(params.get("batch_size", 4)),
        "voronoi_distance": int(params.get("voronoi_distance", 9)),
        "flow_threshold": float(params.get("flow_threshold", 0.4)),
        "min_size": int(params.get("min_size", 20)),
        "max_size": int(params.get("max_size", 6000)),
        "use_transcript_rescue": False,
        "clahe_clip_limit": float(params.get("clahe_clip_limit", 3.0)),
        "dia_small": float(params.get("dia_small", 13.0)),
        "dia_mid": float(params.get("dia_mid", 17.0)),
        "dia_large": float(params.get("dia_large", 22.0)),
        "use_hematoxylin": bool(params.get("use_hematoxylin", True)),
        "cellprob_threshold": float(params.get("cellprob_threshold", -2.0)),
        "use_cpsam": bool(params.get("use_cpsam", False)),
        "dia_cpsam_auto": float(params.get("dia_cpsam_auto", 0.0)),
        "dia_cpsam_small": float(params.get("dia_cpsam_small", 16.0)),
        "cellprob_cpsam_auto": float(params.get("cellprob_cpsam_auto", -1.0)),
        "cellprob_cpsam_small": float(params.get("cellprob_cpsam_small", -3.0)),
        "cellprob_cpsam_hema": float(params.get("cellprob_cpsam_hema", -1.0)),
    }

    from backend.src.segmentation.cellpose_runner import run_tiled_mcseg_v2

    def _progress(p: float, msg: str) -> None:
        bar = "█" * int(p * 20) + "░" * (20 - int(p * 20))
        logger.info(f"  [{bar}] {p * 100:.0f}%  {msg}")

    passes = 7 if cfg["use_cpsam"] else 4
    logger.info(
        f"Running Tiled MCseg V2 fullslide ({passes}-pass). tile={tile_size}px, overlap={overlap}px"
    )
    mask = run_tiled_mcseg_v2(
        img, cfg, tile_size=tile_size, overlap=overlap, progress_callback=_progress
    )

    mask_path = out_dir / "segmentation_masks_fullslide.npy"
    np.save(str(mask_path), mask)
    n_cells = int(mask.max())
    del mask, img  # free the TIFF-resolution mask/memmap before downscaling

    tiff_scale, vfr_w, vfr_h = _compute_tiff_scale(btf_path, binned_dir)
    mask_vfr_path = out_dir / "segmentation_masks_fullslide_vfr.npy"
    downscale_mask_to_vfr(mask_path, tiff_scale, vfr_w, vfr_h, mask_vfr_path)

    cells_path = out_dir / "cellpose_cells_fullslide.h5ad"
    cells_path, n_bins_total, n_bins_assigned = run_fullslide_rna_counting(
        binned_dir=binned_dir,
        mask_vfr_path=mask_vfr_path,
        out_cells_path=cells_path,
    )

    logger.info(
        f"Fullslide done: {n_cells:,} cells, {n_bins_assigned:,}/{n_bins_total:,} bins assigned"
    )

    return {
        "n_cells": n_cells,
        "n_bins_total": n_bins_total,
        "n_bins_assigned": n_bins_assigned,
        "mask_path": str(mask_path),
        "mask_vfr_path": str(mask_vfr_path),
        "cells_path": str(cells_path),
    }


@register_tool_on_import(
    tool_name="register_external_mcseg_result",
    version="1.0.0",
    description=(
        "登記在 EP 系統外部(例如另一台有 CUDA GPU 的機器、或修 bug 後的 host-native 手動"
        "補算)完成的 mcseg 全片分割結果,補寫 analysis_history + artifact_registry,"
        "不重新執行任何運算。2026-07-21 新增,取代先前每次都要手寫 docker exec + SQL 的做法"
        "(見 dpcp01_vh_v114_02_hd_r004 的兩次手動補救:scanpy 缺套件補算、binned_outputs"
        "配對錯誤重算)。"
    ),
)
def register_external_mcseg_result(
    sample_id: str,
    mask_path: str | Path,
    mask_vfr_path: str | Path,
    cells_path: str | Path,
    n_cells: int,
    n_bins_total: int,
    n_bins_assigned: int,
    params: dict,
    requested_by: str = "external_import",
    notes: str = "",
    overlay_paths: list[str | Path] | None = None,
) -> dict:
    """
    Register an externally-computed mcseg full-slide result into analysis_history
    (+ optional artifact_registry entries for overlay figures), without re-running
    segmentation or RNA counting.

    All paths must already be under BIO_DB_ROOT (results/mcseg/<sample_id>/fullslide/,
    matching the layout _run_mcseg_fullslide_impl produces) — copy files there first
    if they came from elsewhere (e.g. a CUDA machine). n_cells is cross-checked against
    mask_path's actual max label to catch stats/file mismatches early.
    """
    import json
    import uuid
    from datetime import datetime

    from config.settings import BIO_DB_ROOT
    from store.factory import get_store

    sample_id = str(sample_id)
    mask_path = Path(mask_path)
    mask_vfr_path = Path(mask_vfr_path)
    cells_path = Path(cells_path)

    store = get_store()
    sample = store.get_sample(sample_id)
    if not sample:
        raise ValueError(f"sample_id '{sample_id}' 不存在於 sample_registry，請先 bio_register_sample。")

    for label, p in [("mask_path", mask_path), ("mask_vfr_path", mask_vfr_path), ("cells_path", cells_path)]:
        if not p.exists():
            raise FileNotFoundError(f"{label} 不存在：{p}（外部結果要先複製進 BIO_DB_ROOT 底下）")
        try:
            p.relative_to(BIO_DB_ROOT)
        except ValueError:
            raise ValueError(
                f"{label} 必須在 BIO_DB_ROOT（{BIO_DB_ROOT}）底下，容器/其他工具才讀得到；"
                f"收到 {p}"
            )

    try:
        mask = np.load(str(mask_path), mmap_mode="r")
        actual_n_cells = int(mask.max())
        if actual_n_cells != int(n_cells):
            raise ValueError(
                f"n_cells 跟 mask 檔案不符：傳入 {n_cells}，mask.max() 實際是 {actual_n_cells}。"
                f"請確認統計數字跟檔案是同一次結果。"
            )
    except ValueError:
        raise
    except OSError as exc:
        logger.warning(f"register_external_mcseg_result: 無法讀取 mask 驗證 n_cells: {exc}")

    assign_rate = n_bins_assigned / n_bins_total * 100 if n_bins_total else 0.0
    summary = (
        f"Full-slide segmentation + RNA counting (externally computed"
        f"{': ' + notes if notes else ''}): "
        f"{n_cells:,} cells, {n_bins_assigned:,}/{n_bins_total:,} bins assigned ({assign_rate:.1f}%)."
    )

    # 一次性記錄（外部結果）→ history 走 analysis_run seam（backend-agnostic）；
    # artifacts 另走專用 DuckDB con 以收集 artifact_ids 回傳給呼叫端顯示。
    from analysis.run_context import record_completed_run

    analysis_id = record_completed_run(
        sample_id, "mcseg_fullslide",
        params=params,
        result_path=str(cells_path),
        summary=summary,
        requested_by=requested_by,
    )

    artifact_ids = []
    if overlay_paths:
        from analysis.artifact_registry import register_artifact
        from config.db_utils import connect_db
        from config.settings import DUCKDB_PATH

        con = connect_db(DUCKDB_PATH)
        try:
            for op in overlay_paths:
                op = Path(op)
                if not op.exists():
                    logger.warning(f"register_external_mcseg_result: overlay 檔不存在，略過: {op}")
                    continue
                aid = register_artifact(
                    con, analysis_id, op, "figure",
                    f"Full-slide overlay ({op.stem})",
                    artifact_subtype="mcseg_overlay",
                )
                artifact_ids.append(aid)
        finally:
            con.close()

    logger.info(f"register_external_mcseg_result: analysis_id={analysis_id} {summary}")
    return {
        "analysis_id": analysis_id,
        "artifact_ids": artifact_ids,
        "summary": summary,
    }


def export_to_xenium(
    adata_cells_path: str | Path,
    mask_path: str | Path,
    transcripts_roi_csv_path: str | Path,
    he_crop_path: str | Path,
    out_xenium_dir: str | Path,
    pixel_size_um: float = 0.2737,
):
    """
    Export single-cell spatial transcriptomics data into the 10x Xenium Explorer format.
    """
    from backend.src.export.xenium_exporter import XeniumExporter
    from backend.src.api.export import _mask_to_geojson
    import json

    adata_cells_path = Path(adata_cells_path)
    mask_path = Path(mask_path)
    out_xenium_dir = Path(out_xenium_dir)

    # 1. Convert mask to polygon GeoJSON
    logger.info("Converting cell masks to GeoJSON polygons")
    geo_dict = _mask_to_geojson(mask_path, pixel_size_um)
    poly_json_path = mask_path.parent / "cellpose_polygons.json"
    with open(poly_json_path, "w", encoding="utf-8") as f:
        json.dump(geo_dict, f)

    # 2. Setup XeniumExporter
    logger.info(f"Exporting to Xenium Explorer bundle at {out_xenium_dir}")
    exporter = XeniumExporter(
        zarr_path=None,
        poly_json_path=poly_json_path,
        transcripts_csv_path=Path(transcripts_roi_csv_path)
        if transcripts_roi_csv_path and Path(transcripts_roi_csv_path).exists()
        else None,
        pixel_size_um=pixel_size_um,
        he_image_path=Path(he_crop_path) if he_crop_path and Path(he_crop_path).exists() else None,
        he_crop_bounds=None,
    )

    exporter.export(adata_cells_path, out_xenium_dir)
    logger.info("Successfully exported Xenium bundle!")

    return out_xenium_dir


def export_cell_metadata_json(
    adata_cells_path: str | Path, out_json_path: str | Path, key_markers=None
):
    """
    Export clean single-cell metadata, annotations, coordinates, and key marker expressions to a structured JSON file.
    """
    import scanpy as sc
    import json

    adata_cells_path = Path(adata_cells_path)
    out_json_path = Path(out_json_path)

    adata = sc.read_h5ad(str(adata_cells_path))

    if key_markers is None:
        key_markers = ["Krt14", "Col1a1", "Lgr5", "Sox9", "Mitf", "Acta2", "Pecam1", "Ptprc"]

    # Skin markers (both mouse and human standard nomenclatures)
    avail_markers = [g for g in key_markers if g in adata.var_names]

    spatial = adata.obsm["spatial"]
    cells_list = []

    for i, cell_name in enumerate(adata.obs_names):
        cell_obs = adata.obs.iloc[i]

        # Extract expressions
        marker_exp = {}
        for g in avail_markers:
            val = adata[cell_name, g].X
            if hasattr(val, "toarray"):
                val = val.toarray()[0, 0]
            elif hasattr(val, "item"):
                val = val.item()
            else:
                val = float(val)
            marker_exp[g] = float(val)

        cell_dict = {
            "cell_id": str(cell_name),
            "centroid_x_um": float(spatial[i, 0]),
            "centroid_y_um": float(spatial[i, 1]),
            "cell_area_um2": float(cell_obs.get("cell_area_um2", 0.0)),
            "total_umis": float(
                cell_obs.get("n_bins", 0.0)
                if "n_bins" in cell_obs
                else cell_obs.get("total_counts", 0.0)
            ),
            "leiden_cluster": str(cell_obs.get("leiden", "")),
            "cell_type": str(cell_obs.get("cell_type", "Unknown")),
            "marker_expression": marker_exp,
        }
        cells_list.append(cell_dict)

    out_dict = {
        "sample_id": str(adata.uns.get("sample_id", "SDS-D0D1D2")),
        "roi_name": str(adata.uns.get("active_roi", "roi1")),
        "total_cells": len(cells_list),
        "cells": cells_list,
    }

    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(out_dict, f, indent=2)

    logger.info(f"Successfully exported custom cell metadata JSON: {out_json_path}")
    return out_json_path
