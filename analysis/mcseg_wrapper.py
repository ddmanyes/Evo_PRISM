import sys
from pathlib import Path
import logging
import numpy as np

# Path to the external MSseg repository
MSSEG_PATH = Path("K:/plan_a/MSseg")
if str(MSSEG_PATH) not in sys.path:
    sys.path.insert(0, str(MSSEG_PATH))

logger = logging.getLogger("evo_prism.mcseg_wrapper")

def _compute_tiff_scale(btf_image_path: Path, binned_dir: Path) -> float:
    """
    Compute scale factor: raw_TIFF_pixels = virtual_fullres_pixels × tiff_scale.

    Derived from tissue_hires_image.png dimensions + scalefactors_json.json +
    raw TIFF dimensions.  Returns 1.0 if metadata is unavailable.
    """
    import json
    import tifffile
    from PIL import Image

    sf_path    = Path(binned_dir) / "spatial" / "scalefactors_json.json"
    hires_path = Path(binned_dir) / "spatial" / "tissue_hires_image.png"
    if not (sf_path.exists() and hires_path.exists()):
        logger.warning("scalefactors_json / tissue_hires_image not found — using tiff_scale=1.0")
        return 1.0

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
    return tiff_scale


def crop_visium_hd_roi(btf_image_path: str | Path, binned_dir: str | Path, roi_dict: dict, out_roi_dir: str | Path):
    """
    Crop H&E gigapixel image and 2µm AnnData coordinate matrix for the selected ROI.

    Auto-detects the scale factor between virtual_fullres (CytAssist pixel space used
    by pxl_col/row_in_fullres) and the raw H&E TIFF.  The H&E crop is produced at full
    TIFF resolution for best segmentation quality; tiff_scale is persisted in
    crop_meta.json so that mcseg_wrapper.run_mcseg_segmentation can downscale the
    resulting mask back to virtual_fullres coordinates for consistent downstream use.
    """
    from backend.src.roi.extractor import load_visium_adata, subset_anndata_roi, roi_to_fullres_px, read_btf_crop
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
    tiff_scale = _compute_tiff_scale(Path(btf_image_path), Path(binned_dir))

    logger.info(f"Cropping H&E BTF image from {btf_image_path}")
    x0_vfr, y0_vfr, w_vfr, h_vfr = roi_to_fullres_px(roi_dict)
    x0_tiff = round(x0_vfr * tiff_scale)
    y0_tiff = round(y0_vfr * tiff_scale)
    w_tiff  = round(w_vfr  * tiff_scale)
    h_tiff  = round(h_vfr  * tiff_scale)

    crop, _, _ = read_btf_crop(btf_image_path, x0_tiff, y0_tiff, w_tiff, h_tiff)

    he_out_path = out_roi_dir / "he_crop.tif"
    tifffile.imwrite(str(he_out_path), crop)
    logger.info(f"Saved H&E ROI crop: {he_out_path}  shape={crop.shape}  tiff_scale={tiff_scale:.4f}")

    # Persist scale info so run_mcseg_segmentation can downscale the mask
    (out_roi_dir / "crop_meta.json").write_text(
        json.dumps({"tiff_scale": tiff_scale, "vfr_w": w_vfr, "vfr_h": h_vfr}),
        encoding="utf-8",
    )

    return adata_out_path, he_out_path

def run_mcseg_segmentation(he_crop_path: str | Path, out_mask_path: str | Path, params: dict):
    """
    Run multi-pass Cellpose ensemble segmentation with Voronoi-constrained expansion on the H&E crop.
    Uses run_tiled_mcseg_v2 (identical to MSseg CLI full-slide pipeline) for consistency.
    Tile-based processing supports arbitrarily large ROIs and includes CUDA OOM fallback to CPU.
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
        logger.info(f"GPU status: requested={gpu_requested}, available={gpu_available}, effective={use_gpu}")
    
    # Tiled segmentation params (consistent with MSseg CLI defaults)
    tile_size = int(params.get("tile_size", 1024))
    overlap   = int(params.get("overlap",    128))
    
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
        logger.info(f"  [{bar}] {p*100:.0f}%  {msg}")
    
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
            from skimage.transform import resize as sk_resize
            logger.info(f"Downscaling mask {mask.shape} → ({vfr_h},{vfr_w}) (tiff_scale={tiff_scale:.4f})")
            mask = sk_resize(
                mask, (vfr_h, vfr_w),
                order=0,               # nearest-neighbour — preserves integer labels
                preserve_range=True,
                anti_aliasing=False,
            ).astype(np.uint32)
            logger.info(f"Downscaled mask: shape={mask.shape}, cells={mask.max():,}")

    np.save(str(out_mask_path), mask)
    logger.info(f"Saved segmentation mask: {out_mask_path} | shape={mask.shape} | cells={mask.max():,}")

    # Save visual TIF overlay
    import tifffile
    tif_dtype = np.uint16 if mask.max() <= 65535 else np.uint32
    overlay_path = out_mask_path.parent / "segmentation_masks.tif"
    tifffile.imwrite(str(overlay_path), mask.astype(tif_dtype), compression="zlib")
    logger.info(f"Saved TIF overlay: {overlay_path}")

    return out_mask_path

def run_rna_counting(adata_002um_path: str | Path, mask_path: str | Path, roi_dict: dict, out_cells_path: str | Path, dilation_px: int = 6):
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

def export_to_xenium(adata_cells_path: str | Path, mask_path: str | Path, transcripts_roi_csv_path: str | Path, he_crop_path: str | Path, out_xenium_dir: str | Path, pixel_size_um: float = 0.2737):
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
        transcripts_csv_path=Path(transcripts_roi_csv_path) if transcripts_roi_csv_path and Path(transcripts_roi_csv_path).exists() else None,
        pixel_size_um=pixel_size_um,
        he_image_path=Path(he_crop_path) if he_crop_path and Path(he_crop_path).exists() else None,
        he_crop_bounds=None,
    )
    
    exporter.export(adata_cells_path, out_xenium_dir)
    logger.info("Successfully exported Xenium bundle!")
    
    return out_xenium_dir

def export_cell_metadata_json(adata_cells_path: str | Path, out_json_path: str | Path, key_markers=None):
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
            "total_umis": float(cell_obs.get("n_bins", 0.0) if "n_bins" in cell_obs else cell_obs.get("total_counts", 0.0)),
            "leiden_cluster": str(cell_obs.get("leiden", "")),
            "cell_type": str(cell_obs.get("cell_type", "Unknown")),
            "marker_expression": marker_exp
        }
        cells_list.append(cell_dict)
        
    out_dict = {
        "sample_id": str(adata.uns.get("sample_id", "SDS-D0D1D2")),
        "roi_name": str(adata.uns.get("active_roi", "roi1")),
        "total_cells": len(cells_list),
        "cells": cells_list
    }
    
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(out_dict, f, indent=2)
        
    logger.info(f"Successfully exported custom cell metadata JSON: {out_json_path}")
    return out_json_path
