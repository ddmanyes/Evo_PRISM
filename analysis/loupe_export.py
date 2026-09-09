"""Loupe Browser export for MCseg ROI results.

Primary output: cells.geojson (polygons + cell_type/leiden annotations)
                cell_metadata.csv (mask_id → cell_type, leiden, spatial coords)
Optional:       .cloupe (via loupepy + 10x loupe_converter binary, if available)

The GeoJSON can be imported directly in Loupe Browser via:
  Tools → Import Custom Region → cells.geojson

Design note: .cloupe creation requires the platform-specific loupe_converter binary
from 10x Genomics. This module always produces GeoJSON + CSV and only attempts
.cloupe when loupepy and the converter are available.

Main function:
    run_loupe_export(sample_id, roi_name, ...) -> (analysis_id, report_path)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT, MCSEG_RESULTS_ROOT, MSSEG_PATH  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402

logger = logging.getLogger(__name__)

# Single authoritative list of MSseg root candidates (used by both import helpers)
_MSSEG_CANDIDATES = [
    MSSEG_PATH,
    BIO_DB_ROOT.parent.parent / "plan_a" / "MSseg",
    Path(__file__).parent.parent.parent / "plan_a" / "MSseg",
]


def _add_msseg_to_path() -> None:
    """Add the first existing MSseg root to sys.path."""
    for p in _MSSEG_CANDIDATES:
        p = p.resolve()
        if p.exists():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return


def _import_mask_to_geojson():
    """Lazy-import _mask_to_geojson from backend_msseg, trying _MSSEG_CANDIDATES."""
    # Try direct import first (if already on sys.path)
    try:
        from backend_msseg.src.api.export import _mask_to_geojson  # type: ignore

        return _mask_to_geojson
    except ImportError:
        pass

    # Try inserting known roots
    for root in _MSSEG_CANDIDATES:
        root = root.resolve()
        if root.exists() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from backend_msseg.src.api.export import _mask_to_geojson  # type: ignore

            return _mask_to_geojson
        except ImportError:
            continue

    # Fallback: inline implementation using the same algorithm
    logger.warning("_mask_to_geojson not found in backend_msseg — using inline fallback")
    return _mask_to_geojson_fallback


def _mask_to_geojson_fallback(mask_path: Path, pixel_size_um: float, min_area_px: int = 20) -> dict:
    """Inline fallback matching backend_msseg.src.api.export._mask_to_geojson."""
    import numpy as np
    from skimage import measure

    seg_mask = np.load(str(mask_path))
    features = []
    for prop in measure.regionprops(seg_mask):
        if prop.area < min_area_px:
            continue
        cid = prop.label
        r0, c0, r1, c1 = prop.bbox
        cell_crop = (seg_mask[r0:r1, c0:c1] == cid).astype(np.uint8)
        padded = np.pad(cell_crop, 1, mode="constant")
        contours = measure.find_contours(padded, level=0.5)
        if not contours:
            continue
        contour = max(contours, key=len)
        xy_um = np.column_stack(
            [
                (contour[:, 1] - 1 + c0) * pixel_size_um,
                (contour[:, 0] - 1 + r0) * pixel_size_um,
            ]
        )
        if not np.allclose(xy_um[0], xy_um[-1]):
            xy_um = np.vstack([xy_um, xy_um[0]])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [xy_um.tolist()]},
                "properties": {"full_id": str(int(cid)), "cell_id": int(cid)},
            }
        )
    return {"type": "FeatureCollection", "features": features}


@register_tool_on_import(
    tool_name="bio_export_loupe",
    version="1.0.0",
    description="匯出 MCseg ROI 結果為 Loupe Browser 格式（GeoJSON + cell_metadata.csv，選配 .cloupe）",
)
def run_loupe_export(
    sample_id: str,
    roi_name: str,
    roi_dir: Optional[Path] = None,
    pixel_size_um: float = 0.2737,
    requested_by: str = "agent",
) -> tuple[str, str]:
    """Export MCseg ROI segmentation to Loupe Browser format.

    Always produces:
      - cells.geojson  (polygons with cell_type/leiden in properties)
      - cell_metadata.csv  (mask_id, cell_type, leiden, spatial_x, spatial_y)

    Optionally produces (if loupepy + loupe_converter available):
      - <roi_name>.cloupe

    Returns (analysis_id, report_path).
    """
    validate_sample_id(sample_id)
    roi_dir = Path(roi_dir) if roi_dir else MCSEG_RESULTS_ROOT / sample_id / "roi" / roi_name
    h5ad_path = roi_dir / "umap_computed.h5ad"
    mask_path = roi_dir / "segmentation_masks.npy"

    if not h5ad_path.exists():
        raise FileNotFoundError(
            f"找不到 umap_computed.h5ad：{h5ad_path}\n請先執行 bio_run_mcseg_roi 完成 Stage 3–6。"
        )
    if not mask_path.exists():
        raise FileNotFoundError(f"找不到 segmentation_masks.npy：{mask_path}")

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps({"roi_name": roi_name, "pixel_size_um": pixel_size_um})

    from store.factory import get_store as _get_store

    _get_store().insert_history(
        analysis_id, sample_id, "loupe_export", params_json, "running", requested_by, started_at
    )

    try:
        import scanpy as sc

        adata = sc.read_h5ad(str(h5ad_path))

        # Build mask_id → obs metadata map
        # obs_names pattern: "cell_42" or "42" — extract integer mask label
        def _obs_to_mask_id(obs_name: str) -> int:
            digits = "".join(filter(str.isdigit, str(obs_name)))
            return int(digits) if digits else -1

        obs_df = adata.obs.copy()
        obs_df["mask_id"] = [_obs_to_mask_id(n) for n in adata.obs_names]

        # Extract available annotation columns
        annot_cols = [
            c
            for c in ("cell_type", "leiden", "celltypist_cell_type", "cell_type_manual")
            if c in obs_df.columns
        ]
        if "obsm" in dir(adata) and "spatial" in adata.obsm:
            obs_df["spatial_x"] = adata.obsm["spatial"][:, 0]
            obs_df["spatial_y"] = adata.obsm["spatial"][:, 1]

        # Output directory (validated to be under BIO_DB_ROOT)
        out_dir = BIO_DB_ROOT / "results" / "mcseg" / sample_id / "export" / "loupe" / roi_name
        try:
            out_dir.resolve().relative_to(BIO_DB_ROOT.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {out_dir}")
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = started_at.strftime("%Y%m%d_%H%M%S")

        # --- 1. Generate GeoJSON with annotations embedded ---
        mask_to_geojson = _import_mask_to_geojson()
        geojson = mask_to_geojson(mask_path, pixel_size_um)

        # Build fast lookup: mask_id → annotation dict
        mask_annot: dict[int, dict] = {}
        for _, row in obs_df.iterrows():
            mid = int(row["mask_id"])
            entry: dict = {}
            for col in annot_cols:
                entry[col] = str(row[col]) if col in row else "Unknown"
            mask_annot[mid] = entry

        for feat in geojson["features"]:
            mid = feat["properties"].get("cell_id", -1)
            if mid in mask_annot:
                feat["properties"].update(mask_annot[mid])

        geojson_path = out_dir / "cells.geojson"
        geojson_path.write_text(json.dumps(geojson), encoding="utf-8")
        logger.info("GeoJSON 寫入：%s (%d features)", geojson_path, len(geojson["features"]))

        # --- 2. Cell metadata CSV ---
        csv_cols = ["mask_id"] + annot_cols
        if "spatial_x" in obs_df.columns:
            csv_cols += ["spatial_x", "spatial_y"]
        csv_path = out_dir / "cell_metadata.csv"
        obs_df[csv_cols].to_csv(csv_path, index=False)

        # --- 3. Optional .cloupe via LoupeExporter ---
        cloupe_path: Optional[Path] = None
        cloupe_note = ""
        try:
            _add_msseg_to_path()
            from backend_msseg.src.export.loupe_exporter import LoupeExporter  # type: ignore

            exporter = LoupeExporter(poly_json_path=geojson_path)
            cloupe_path = exporter.export(h5ad_path, out_dir)
            cloupe_note = f"\n- `.cloupe`：`{cloupe_path}`"
        except ImportError as ie:
            cloupe_note = f"\n- `.cloupe` 未產生（缺少依賴：{ie}）"
        except Exception as ce:
            cloupe_note = f"\n- `.cloupe` 未產生（{ce}）"
            logger.warning("LoupeExporter 失敗（非致命）：%s", ce)

        annot_summary = ", ".join(f"`{c}`" for c in annot_cols) or "（無標注欄位）"
        report_text = (
            f"# Loupe Browser Export — {sample_id} / {roi_name}\n\n"
            f"**生成時間**：{started_at.isoformat()}\n"
            f"**輸出目錄**：`{out_dir}`\n"
            f"**包含標注欄位**：{annot_summary}\n"
            f"**細胞數**：{adata.n_obs}  **多邊形數**：{len(geojson['features'])}\n\n"
            f"## 輸出檔案\n\n"
            f"- `cells.geojson`：細胞邊界多邊形 + 標注屬性\n"
            f"- `cell_metadata.csv`：mask_id → 細胞類型 + 座標\n"
            f"{cloupe_note}\n\n"
            f"## Loupe Browser 匯入步驟\n\n"
            f"1. 開啟 Loupe Browser，載入空間轉錄體數據\n"
            f"2. **Tools → Import Custom Region**\n"
            f"3. 選取 `cells.geojson`\n"
            f"4. 各 feature 的 `cell_type` / `leiden` 屬性會顯示為 region 標籤\n\n"
            f"---\n*由 BioAgent analysis/loupe_export.py 自動生成*\n"
        )
        report_path = out_dir / f"loupe_export_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        summary = f"{sample_id}/{roi_name} Loupe export：{adata.n_obs} 細胞，GeoJSON 已產生"[:50]
        completed_at = datetime.now(timezone.utc)

        with _get_store().write_conn() as con:
            from analysis.tool_registry import get_active_tool_id

            tool_id = get_active_tool_id(con, "bio_export_loupe")
            con.execute(
                """UPDATE analysis_history
                      SET status='completed', result_path=?, completed_at=?, summary=?, tool_id=?
                    WHERE analysis_id=?""",
                [str(report_path), completed_at, summary, tool_id, analysis_id],
            )
            from analysis.failure_diagnosis import success_diagnosis, write_diagnosis

            write_diagnosis(con, analysis_id, success_diagnosis())
            try:
                from analysis.artifact_registry import register_artifact

                register_artifact(
                    con,
                    analysis_id,
                    geojson_path,
                    "data",
                    "cells.geojson",
                    artifact_subtype="loupe_geojson",
                )
                register_artifact(
                    con,
                    analysis_id,
                    csv_path,
                    "table",
                    "cell_metadata.csv",
                    artifact_subtype="loupe_metadata",
                )
                if cloupe_path and cloupe_path.exists():
                    register_artifact(
                        con,
                        analysis_id,
                        cloupe_path,
                        "data",
                        f"{roi_name}.cloupe",
                        artifact_subtype="loupe_cloupe",
                    )
                register_artifact(
                    con,
                    analysis_id,
                    report_path,
                    "report",
                    "Loupe export 報告",
                    artifact_subtype="loupe_report",
                )
            except Exception as _exc:
                logger.warning("loupe_export: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc:
        logger.exception("loupe_export 失敗  analysis_id=%s", analysis_id)
        with _get_store().write_conn() as con:
            con.execute(
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
            from analysis.failure_diagnosis import classify_exception, write_diagnosis

            write_diagnosis(con, analysis_id, classify_exception(_exc))
        raise

    logger.info("loupe_export 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
