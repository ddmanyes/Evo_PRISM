"""
Evo_PRISM — Bulk Transcriptomics Executor Submodule.
"""

from __future__ import annotations

import logging
from pathlib import Path as _Path

logger = logging.getLogger(__name__)


def _exec_bio_run_bulk_eda(args: dict) -> str:
    from analysis.bulk_eda import generate_bulk_report

    sample_id = args["sample_id"]
    requested_by = args.get("requested_by", "agent")
    try:
        analysis_id, report_path = generate_bulk_report(sample_id, requested_by=requested_by)
        # 讀取完整報告（含 inline base64 圖片），讓 web_app 解析並顯示
        report_text = ""
        if report_path:
            try:
                report_text = _Path(report_path).read_text(encoding="utf-8")
            except Exception:
                pass
        # tool_id 已由分析函數內部回填（analysis.bulk_eda）；此處不再重複。

        header = f"Bulk EDA 完成。\nanalysis_id: {analysis_id}\nreport_path: {report_path}\n\n"
        return header + report_text
    except Exception as e:
        return f"Bulk EDA 執行失敗：{e}"


def _exec_bio_run_mcseg_qc(args: dict) -> str:
    from analysis.mcseg_quality import generate_mcseg_qc_report

    sample_id = args["sample_id"]
    qc_dir = args.get("qc_dir")
    requested_by = args.get("requested_by", "agent")
    try:
        analysis_id, report_path = generate_mcseg_qc_report(
            sample_id, qc_dir=qc_dir, requested_by=requested_by
        )
        report_text = ""
        if report_path:
            try:
                report_text = _Path(report_path).read_text(encoding="utf-8")
            except Exception:
                pass
        # tool_id 已由分析函數內部回填（analysis.mcseg_quality）；此處不再重複。

        header = f"MCseg QC 完成。\nanalysis_id: {analysis_id}\nreport_path: {report_path}\n\n"
        return header + report_text
    except FileNotFoundError as e:
        return (
            f"MCseg QC 無法執行：{e}\n"
            "本平台不即時重跑分割（依賴 MSseg 原專案 cellpose+GPU）；"
            "請先把成對的 *_nuc.npy / *_mcseg.npy 放入 results/mcseg_qc/（或指定 qc_dir）。"
        )
    except Exception as e:
        return f"MCseg QC 執行失敗：{e}"


def _exec_bio_run_mcseg_roi(args: dict) -> str:
    """單 ROI MCseg 完整管線（Stage 0–7）。"""
    import sys
    import subprocess
    from pathlib import Path as _Path

    sample_id = args["sample_id"]
    roi_x = int(args["roi_x"])
    roi_y = int(args["roi_y"])
    roi_w = int(args.get("roi_width_px", 1500))
    roi_h = int(args.get("roi_height_px", 1500))
    roi_name = args.get("roi_name") or f"roi_{roi_x}_{roi_y}"
    use_cpsam = bool(args.get("use_cpsam", True))
    # 路徑解析：優先用傳入值，否則從 sample_registry 查
    btf_path = args.get("btf_image_path")
    binned_dir = args.get("binned_dir")
    if not (btf_path and binned_dir):
        try:
            import duckdb
            from config.settings import DUCKDB_PATH, MCSEG_RESULTS_ROOT

            con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
            row = con.execute(
                "SELECT l3_path FROM sample_registry WHERE sample_id = ?",
                [sample_id],
            ).fetchone()
            con.close()
            if row:
                l3_path = _Path(row[0])
                btf_path = btf_path or str(next(l3_path.glob("*.tif*"), l3_path))
                binned_dir = binned_dir or str(l3_path)
        except Exception as e:
            return f"sample_registry 查詢失敗：{e}\n請明確傳入 btf_image_path 與 binned_dir。"
    else:
        from config.settings import MCSEG_RESULTS_ROOT

    output_base = args.get("output_base") or str(MCSEG_RESULTS_ROOT / sample_id)

    if not btf_path or not binned_dir:
        return "無法解析 btf_image_path / binned_dir，請明確傳入。"

    # 組裝 ROI_CONFIG 並動態執行 showcase 管線
    try:
        sys.path.insert(0, str(_Path("K:/plan_a/MSseg")))
        sys.path.insert(0, str(_Path("I:/Evo_PRISM")))

        from analysis.mcseg_wrapper import (
            crop_visium_hd_roi,
            run_mcseg_segmentation,
            run_rna_counting,
        )
        import matplotlib

        matplotlib.use("Agg")

        roi_cfg = {
            "name": roi_name,
            "x": roi_x,
            "y": roi_y,
            "width_px": roi_w,
            "height_px": roi_h,
            "pixel_size_um": 0.2737,
        }
        seg_params = {
            "use_cpsam": use_cpsam,
            "use_hematoxylin": True,
            "tile_size": 1024,
            "overlap": 128,
            "voronoi_distance": 9,
            "flow_threshold": 0.4,
            "min_size": 20,
            "max_size": 6000,
        }

        out_base = _Path(output_base)
        roi_dir = out_base / "roi" / roi_name
        roi_dir.mkdir(parents=True, exist_ok=True)

        # Stage 0
        import json as _json

        adata_path, he_path = crop_visium_hd_roi(btf_path, binned_dir, roi_cfg, roi_dir)
        # Persist ROI origin for downstream bio_compute_crc_metrics
        roi_info_path = roi_dir / "roi_info.json"
        roi_info_path.write_text(
            _json.dumps({"roi_x": roi_x, "roi_y": roi_y, "roi_w": roi_w, "roi_h": roi_h}),
            encoding="utf-8",
        )
        # Stage 1
        mask_path = roi_dir / "segmentation_masks.npy"
        run_mcseg_segmentation(he_path, mask_path, seg_params)
        # Stage 2
        cells_path = roi_dir / "cellpose_cells.h5ad"
        run_rna_counting(adata_path, mask_path, roi_cfg, cells_path)
        # Stage 3–7: run the showcase downstream via subprocess to keep GPU memory isolated
        import os

        showcase = _Path("I:/Evo_PRISM/scratch/run_visium_hd_showcase.py")
        result = subprocess.run(
            [
                sys.executable,
                str(showcase),
                "--roi-dir",
                str(roi_dir),
                "--sample-id",
                sample_id,
                "--adata-002um",
                str(adata_path),
                "--cells-h5ad",
                str(cells_path),
                "--mask",
                str(mask_path),
                "--he-crop",
                str(he_path),
                "--roi-name",
                roi_name,
                "--roi-x",
                str(roi_x),
                "--roi-y",
                str(roi_y),
                "--roi-width-px",
                str(roi_w),
                "--roi-height-px",
                str(roi_h),
            ],
            capture_output=True,
            text=True,
            timeout=7200,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        stdout = result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout

        summary_path = roi_dir / "analysis_summary.txt"
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""

        return (
            f"bio_run_mcseg_roi 完成。\n"
            f"sample_id: {sample_id}  ROI: {roi_name}\n"
            f"output_dir: {roi_dir}\n\n"
            f"{summary}\n"
            f"--- stdout tail ---\n{stdout}"
        )
    except Exception as e:
        import traceback

        return f"bio_run_mcseg_roi 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_run_mcseg_fullslide(args: dict) -> str:
    """全片 tiled MCseg 分割（Stage 0–2，不含 Scanpy downstream）。"""
    import sys
    from pathlib import Path as _Path

    sample_id = args["sample_id"]
    tile_size = int(args.get("tile_size", 1024))
    overlap = int(args.get("overlap", 128))
    use_cpsam = bool(args.get("use_cpsam", True))

    btf_path = args.get("btf_image_path")
    binned_dir = args.get("binned_dir")
    if not (btf_path and binned_dir):
        try:
            import duckdb
            from config.settings import DUCKDB_PATH, MCSEG_RESULTS_ROOT

            con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
            row = con.execute(
                "SELECT l3_path FROM sample_registry WHERE sample_id = ?", [sample_id]
            ).fetchone()
            con.close()
            if row:
                l3_path = _Path(row[0])
                btf_path = btf_path or str(next(l3_path.glob("*.tif*"), l3_path))
                binned_dir = binned_dir or str(l3_path)
        except Exception as e:
            return f"sample_registry 查詢失敗：{e}\n請明確傳入 btf_image_path 與 binned_dir。"
    else:
        from config.settings import MCSEG_RESULTS_ROOT

    output_base = args.get("output_base") or str(MCSEG_RESULTS_ROOT / sample_id)

    if not btf_path or not binned_dir:
        return "無法解析 btf_image_path / binned_dir，請明確傳入。"

    try:
        sys.path.insert(0, str(_Path("K:/plan_a/MSseg")))
        sys.path.insert(0, str(_Path("I:/Evo_PRISM")))
        import matplotlib

        matplotlib.use("Agg")

        from backend.src.segmentation.cellpose_runner import run_tiled_mcseg_v2  # type: ignore[import]
        import tifffile  # type: ignore[import]
        import numpy as np

        out_dir = _Path(output_base) / "fullslide"
        out_dir.mkdir(parents=True, exist_ok=True)

        seg_params = {
            "use_cpsam": use_cpsam,
            "use_hematoxylin": True,
            "tile_size": tile_size,
            "overlap": overlap,
            "voronoi_distance": 9,
            "flow_threshold": 0.4,
            "min_size": 20,
            "max_size": 6000,
        }

        import logging as _logging

        _logging.getLogger("mcseg_fullslide").info(f"Memory-mapping full-slide BTF: {btf_path}")
        # memmap avoids loading 10–80 GB BTF entirely into RAM;
        # the OS pages in only the tiles that run_tiled_mcseg_v2 touches.
        img = tifffile.memmap(str(btf_path), mode="r")
        mask = run_tiled_mcseg_v2(img, seg_params)

        mask_path = out_dir / "segmentation_masks_fullslide.npy"
        np.save(str(mask_path), mask)
        n_cells = int(mask.max())

        # Register in analysis_history (CLAUDE.md: every analysis must be logged)
        import duckdb as _duckdb
        import uuid as _uuid
        from datetime import datetime as _dt
        from config.settings import DUCKDB_PATH
        from config.db_utils import safe_write as _safe_write

        try:
            _con = _duckdb.connect(str(DUCKDB_PATH))
            _now = _dt.now().isoformat(timespec="seconds")
            _safe_write(
                _con,
                """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    result_path, requested_by, started_at, completed_at, summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    str(_uuid.uuid4()),
                    sample_id,
                    "mcseg_fullslide",
                    f'{{"tile_size":{tile_size},"overlap":{overlap},"use_cpsam":{int(use_cpsam)}}}',
                    "completed",
                    str(mask_path),
                    "bio_run_mcseg_fullslide",
                    _now,
                    _now,
                    f"Full-slide segmentation: {n_cells:,} cells",
                ],
            )
            _con.close()
        except Exception:
            pass  # DB failure must not block segmentation results

        return (
            f"bio_run_mcseg_fullslide 完成。\n"
            f"sample_id: {sample_id}\n"
            f"細胞數: {n_cells:,}\n"
            f"mask: {mask_path}\n"
            f"後續請用 bio_run_mcseg_roi（指定已有 mask）執行 Scanpy downstream。"
        )
    except Exception as e:
        import traceback

        return f"bio_run_mcseg_fullslide 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_run_deg(args: dict) -> str:
    """DEG 多組對照（DESeq2 via omicverse.pyDEG）+ 火山圖。"""
    from analysis.bulk_deg import run_deg_analysis

    sample_id = args["sample_id"]
    counts_path = _Path(args["counts_path"])
    coldata_path = _Path(args["coldata_path"])
    raw_comparisons = args["comparisons"]
    # comparisons 接受 [["a","b"], ...] 或 [{"group_a":"a","group_b":"b"}, ...]
    comparisons: list[tuple[str, str]] = []
    for c in raw_comparisons:
        if isinstance(c, dict):
            comparisons.append((c["group_a"], c["group_b"]))
        else:
            comparisons.append((c[0], c[1]))
    try:
        analysis_id, report_path = run_deg_analysis(
            sample_id,
            counts_path=counts_path,
            coldata_path=coldata_path,
            comparisons=comparisons,
            method=args.get("method", "DEseq2"),
            fc_threshold=float(args.get("fc_threshold", 1.0)),
            pval_threshold=float(args.get("pval_threshold", 0.05)),
            requested_by=args.get("requested_by", "agent"),
        )
        # tool_id 由 run_deg_analysis 內部回填
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"DEG 分析完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except FileNotFoundError as e:
        return f"DEG 無法執行：{e}"
    except Exception as e:
        return f"DEG 執行失敗：{e}"


def _exec_bio_run_enrichment(args: dict) -> str:
    """ORA 富集分析（gseapy.enrichr）對 DEG 表 up/down × N library。"""
    from analysis.enrichment import run_ora, DEFAULT_LIBRARIES

    sample_id = args["sample_id"]
    deg_path = _Path(args["deg_table_path"])
    libraries = tuple(args.get("libraries") or DEFAULT_LIBRARIES)
    try:
        analysis_id, report_path = run_ora(
            sample_id,
            deg_table_path=deg_path,
            libraries=libraries,
            organism=args.get("organism", "human"),
            fc_threshold=float(args.get("fc_threshold", 1.0)),
            pval_threshold=float(args.get("pval_threshold", 0.05)),
            top_term=int(args.get("top_term", 10)),
            requested_by=args.get("requested_by", "agent"),
        )
        # tool_id 由 run_ora 內部回填
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"富集分析完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except FileNotFoundError as e:
        return f"富集無法執行：{e}"
    except Exception as e:
        return f"富集執行失敗：{e}"


def _exec_bio_run_heatmaps(args: dict) -> str:
    """產出顯著基因熱圖 + top variable heatmap。"""
    from analysis.bulk_heatmap import run_bulk_heatmaps

    sample_id = args["sample_id"]
    counts_path = _Path(args["counts_path"])
    deg_tables = [_Path(p) for p in args["deg_tables"]]
    try:
        analysis_id, report_path = run_bulk_heatmaps(
            sample_id,
            counts_path=counts_path,
            deg_tables=deg_tables,
            top_n=int(args.get("top_n", 50)),
            fc_threshold=float(args.get("fc_threshold", 1.0)),
            pval_threshold=float(args.get("pval_threshold", 0.05)),
            requested_by=args.get("requested_by", "agent"),
        )
        # tool_id 由 run_bulk_heatmaps 內部回填
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"Heatmap 完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except Exception as e:
        return f"Heatmap 執行失敗：{e}"


def _exec_bio_compute_crc_metrics(args: dict) -> str:
    """
    Compute CRC segmentation quality metrics on an existing bio_run_mcseg_roi result.

    Requires the ROI directory produced by bio_run_mcseg_roi to contain:
      - segmentation_masks.npy
      - cellpose_cells.h5ad
      - crop_meta.json  (for ROI bbox; overridden by explicit roi_* args)

    Looks for tissue_positions.parquet under sample_registry.l3_path if not
    supplied explicitly.
    """
    import json as _json
    from pathlib import Path as _Path

    sample_id = args["sample_id"]
    roi_name = args["roi_name"]
    requested_by = args.get("requested_by", "agent")

    try:
        from config.settings import DUCKDB_PATH, MCSEG_RESULTS_ROOT

        # Resolve ROI output dir
        roi_dir = _Path(args.get("roi_dir") or MCSEG_RESULTS_ROOT / sample_id / "roi" / roi_name)
        if not roi_dir.exists():
            return f"ROI 目錄不存在：{roi_dir}\n請先執行 bio_run_mcseg_roi。"

        mask_path = roi_dir / "segmentation_masks.npy"
        cells_path = roi_dir / "cellpose_cells.h5ad"
        if not mask_path.exists():
            return f"找不到 segmentation_masks.npy：{mask_path}"
        if not cells_path.exists():
            return f"找不到 cellpose_cells.h5ad：{cells_path}"

        # ROI bbox — explicit args > roi_info.json > crop_meta.json fallback
        roi_info_path = roi_dir / "roi_info.json"
        crop_meta_path = roi_dir / "crop_meta.json"
        if "roi_x" in args:
            roi_x = int(args["roi_x"])
            roi_y = int(args["roi_y"])
            roi_w = int(args.get("roi_w", 1500))
            roi_h = int(args.get("roi_h", 1500))
        elif roi_info_path.exists():
            info = _json.loads(roi_info_path.read_text(encoding="utf-8"))
            roi_x = int(info["roi_x"])
            roi_y = int(info["roi_y"])
            roi_w = int(info.get("roi_w", 1500))
            roi_h = int(info.get("roi_h", 1500))
        elif crop_meta_path.exists():
            # Legacy: crop_meta only has dimensions, not origin — warn user
            meta = _json.loads(crop_meta_path.read_text(encoding="utf-8"))
            roi_x = 0
            roi_y = 0
            roi_w = int(meta.get("vfr_w", 1500))
            roi_h = int(meta.get("vfr_h", 1500))
            logger.warning(
                "roi_info.json not found; using roi_x=0, roi_y=0 from crop_meta.json. "
                "FTC may be inaccurate. Re-run bio_run_mcseg_roi to regenerate roi_info.json."
            )
        else:
            return (
                "無法取得 ROI bbox：請提供 roi_x/roi_y/roi_w/roi_h 參數，"
                "或重新執行 bio_run_mcseg_roi 以生成 roi_info.json。"
            )

        # Resolve tissue_positions.parquet
        tp_path = args.get("tp_parquet_path")
        if not tp_path:
            import duckdb

            try:
                con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
                row = con.execute(
                    "SELECT l3_path FROM sample_registry WHERE sample_id = ?",
                    [sample_id],
                ).fetchone()
                con.close()
                if row:
                    l3 = _Path(row[0])
                    candidates = [
                        l3
                        / "binned_outputs"
                        / "square_002um"
                        / "spatial"
                        / "tissue_positions.parquet",
                        l3
                        / "outs"
                        / "binned_outputs"
                        / "square_002um"
                        / "spatial"
                        / "tissue_positions.parquet",
                    ]
                    for c in candidates:
                        if c.exists():
                            tp_path = str(c)
                            break
            except Exception as e:
                logger.warning("tissue_positions 自動解析失敗：%s", e)

        if not tp_path:
            return (
                "找不到 tissue_positions.parquet。"
                "請傳入 tp_parquet_path 參數，或確認樣本已登記於 sample_registry。"
            )

        # Optional ENACT GT CSV
        enact_gt = args.get("enact_gt_csv")

        # Optional impossible pairs override
        impossible_pairs = args.get("impossible_pairs") or None

        from analysis.spatial_metrics import generate_crc_metrics_report

        analysis_id, report_path = generate_crc_metrics_report(
            sample_id=sample_id,
            roi_name=roi_name,
            mask_path=mask_path,
            adata_cells_path=cells_path,
            tp_parquet_path=tp_path,
            roi_x=roi_x,
            roi_y=roi_y,
            roi_w=roi_w,
            roi_h=roi_h,
            impossible_pairs=impossible_pairs,
            enact_gt_csv=enact_gt,
            n_hvgs=int(args.get("n_hvgs", 1000)),
            requested_by=requested_by,
        )

        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""

        return (
            f"bio_compute_crc_metrics 完成。\n"
            f"sample_id: {sample_id}  roi: {roi_name}\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )

    except Exception as e:
        import traceback

        return f"bio_compute_crc_metrics 失敗：{e}\n{traceback.format_exc()[-2000:]}"
