"""
Evo_PRISM — 計算密集型分析 Executor Submodule（Web UI `_exec_bio_*` handler）。

分檔規則見 server/agent.py 頂端「agent_*.py 家族的分檔規則」。本檔收「計算密集型」
分析：mcseg 分割、DEG、enrichment、heatmap、clustering、celltypist、geneset score、
空間鄰距、CRC metrics、loupe 匯出、外部結果登記等。工具→module+func 對照見 tool_catalog.py。
"""

from __future__ import annotations

import logging
from pathlib import Path as _Path

import sys as _sys
_sys.path.insert(0, str(_Path(__file__).parent.parent))
from config.settings import MSSEG_PATH as _MSSEG_PATH, EVO_PRISM_LEGACY_ROOT as _EVO_PRISM_LEGACY_ROOT  # noqa: E402

logger = logging.getLogger(__name__)


def _exec_bio_run_bulk_eda(args: dict) -> str:
    from pathlib import Path as _Path2
    from analysis.bulk_eda import generate_bulk_report

    sample_id = args["sample_id"]
    requested_by = args.get("requested_by", "agent")
    coldata_path = _Path2(args["coldata_path"]) if args.get("coldata_path") else None
    try:
        analysis_id, report_path = generate_bulk_report(
            sample_id, coldata_path=coldata_path, requested_by=requested_by
        )
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


def _roi_figures_md(roi_dir) -> str:
    """讀取 ROI 目錄下的分析圖，回傳 Markdown inline base64 字串（供 strip_base64_for_llm 處理）。"""
    import base64
    from pathlib import Path as _Path

    _FIGURES = [
        ("umap_spatial_combined.png", "UMAP + 空間分布"),
        ("mask_he_overlay.png", "H&E Overlay（細胞類型著色）"),
        ("mask_he_boundary.png", "H&E Overlay（純邊界版）"),
        ("skin_markers_dotplot.png", "Marker Gene Dotplot"),
    ]
    parts: list[str] = []
    for fname, alt in _FIGURES:
        p = _Path(roi_dir) / fname
        if p.exists():
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            parts.append(f"![{alt}](data:image/png;base64,{b64})\n")
    return "\n".join(parts) + ("\n" if parts else "")


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
            from config.settings import MCSEG_RESULTS_ROOT
            from store.factory import get_store as _get_store

            _sample = _get_store().get_sample(sample_id)
            if _sample:
                l3_path = _Path(_sample["l3_path"])
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
        sys.path.insert(0, str(_MSSEG_PATH))
        sys.path.insert(0, str(_EVO_PRISM_LEGACY_ROOT))

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

        showcase = _EVO_PRISM_LEGACY_ROOT / "scratch" / "run_visium_hd_showcase.py"
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

        if result.returncode != 0:
            stderr_tail = result.stderr[-2000:] if result.stderr else ""
            return (
                f"bio_run_mcseg_roi Stages 3–7 失敗（returncode={result.returncode}）。\n"
                f"sample_id: {sample_id}  ROI: {roi_name}\n"
                f"output_dir: {roi_dir}\n\n"
                f"--- stderr ---\n{stderr_tail}\n"
                f"--- stdout tail ---\n{stdout}"
            )

        summary_path = roi_dir / "analysis_summary.txt"
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""

        figures_md = _roi_figures_md(roi_dir)

        return (
            f"bio_run_mcseg_roi 完成。\n"
            f"sample_id: {sample_id}  ROI: {roi_name}\n"
            f"output_dir: {roi_dir}\n\n"
            f"{summary}\n"
            f"{figures_md}"
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
            from config.settings import MCSEG_RESULTS_ROOT
            from store.factory import get_store as _get_store

            _sample = _get_store().get_sample(sample_id)
            if _sample:
                l3_path = _Path(_sample["l3_path"])
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
        sys.path.insert(0, str(_MSSEG_PATH))
        sys.path.insert(0, str(_EVO_PRISM_LEGACY_ROOT))
        import matplotlib

        matplotlib.use("Agg")

        from analysis.mcseg_wrapper import run_mcseg_fullslide
        from analysis.run_context import analysis_run

        out_dir = _Path(output_base) / "fullslide"

        seg_params = {
            "sample_id": sample_id,  # tile_cache is keyed by this — without it, run_tiled_mcseg_v2
            # falls back to a shared "default_sample" bucket and different samples' tile caches collide
            "use_cpsam": use_cpsam,
            # MPS-safe for full-slide (README: "MPS-safe: tile=1024, batch<=2, cpsam disabled").
            # batch_size=1 chosen specifically when use_cpsam=True (7-pass) to leave extra
            # memory headroom on Apple unified memory during long tiled runs.
            "batch_size": 1 if use_cpsam else 2,
            "use_hematoxylin": True,
            "tile_size": tile_size,
            "overlap": overlap,
            "voronoi_distance": 9,
            "flow_threshold": 0.4,
            "min_size": 20,
            "max_size": 6000,
        }

        with analysis_run(
            sample_id,
            "mcseg_fullslide",
            params={"tile_size": tile_size, "overlap": overlap, "use_cpsam": use_cpsam},
            requested_by="agent",
            tool_name="bio_run_mcseg_fullslide",
        ) as run:
            # Dispatched to the host-native mcseg watcher (Docker Desktop on macOS has no
            # GPU/Metal passthrough, and this container doesn't mount external source data
            # like /Volumes/KINGSTON/Bioinfo_Projects — the host watcher runs natively and
            # has neither limitation). See analysis.mcseg_wrapper.run_mcseg_fullslide.
            result = run_mcseg_fullslide(btf_path, binned_dir, out_dir, seg_params)

            n_cells = result["n_cells"]
            n_bins_total = result["n_bins_total"]
            n_bins_assigned = result["n_bins_assigned"]
            mask_path = result["mask_path"]
            mask_vfr_path = result["mask_vfr_path"]
            cells_path = result["cells_path"]
            assign_rate = n_bins_assigned / n_bins_total * 100 if n_bins_total else 0.0

            summary = (
                f"Full-slide segmentation + RNA counting: {n_cells:,} cells, "
                f"{n_bins_assigned:,}/{n_bins_total:,} bins assigned ({assign_rate:.1f}%)"
            )
            run.artifact(mask_path, "data", "全片分割 mask", "mcseg_mask")
            run.artifact(mask_vfr_path, "data", "全片分割 mask（virtual fullres）", "mcseg_mask_vfr")
            run.artifact(cells_path, "data", "cell×gene AnnData", "mcseg_cells")
            run.complete(cells_path, summary)

        return (
            f"bio_run_mcseg_fullslide 完成。\n"
            f"sample_id: {sample_id}\n"
            f"細胞數: {n_cells:,}\n"
            f"bin 分配率: {n_bins_assigned:,}/{n_bins_total:,} ({assign_rate:.1f}%)\n"
            f"mask (TIFF解析度): {mask_path}\n"
            f"mask (virtual_fullres): {mask_vfr_path}\n"
            f"cell×gene AnnData: {cells_path}\n"
            f"（依範圍設定，僅執行至 RNA counting，未跑 Scanpy QC/clustering。）"
        )
    except Exception as e:
        import traceback

        return f"bio_run_mcseg_fullslide 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_register_external_mcseg_result(args: dict) -> str:
    """登記在 EP 外部完成的 mcseg 全片結果（不重新運算）。"""
    try:
        from analysis.mcseg_wrapper import register_external_mcseg_result

        result = register_external_mcseg_result(
            sample_id=args["sample_id"],
            mask_path=args["mask_path"],
            mask_vfr_path=args["mask_vfr_path"],
            cells_path=args["cells_path"],
            n_cells=int(args["n_cells"]),
            n_bins_total=int(args["n_bins_total"]),
            n_bins_assigned=int(args["n_bins_assigned"]),
            params=args.get("params") or {},
            requested_by=args.get("requested_by", "agent"),
            notes=args.get("notes", ""),
            overlay_paths=args.get("overlay_paths") or [],
        )
        return (
            f"bio_register_external_mcseg_result 完成。\n"
            f"analysis_id: {result['analysis_id']}\n"
            f"artifact_ids: {result['artifact_ids']}\n"
            f"{result['summary']}"
        )
    except Exception as e:
        import traceback

        return f"bio_register_external_mcseg_result 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_register_external_analysis_result(args: dict) -> str:
    """登記在 EP 外部完成的任意類型分析結果（不限 mcseg，不重新運算）。"""
    try:
        from analysis.external_import import register_external_analysis_result

        result = register_external_analysis_result(
            sample_id=args["sample_id"],
            analysis_type=args["analysis_type"],
            result_path=args["result_path"],
            summary=args["summary"],
            params=args.get("params") or {},
            requested_by=args.get("requested_by", "agent"),
            artifact_paths=args.get("artifact_paths") or [],
            supersedes_analysis_id=args.get("supersedes_analysis_id"),
        )
        return (
            f"bio_register_external_analysis_result 完成。\n"
            f"analysis_id: {result['analysis_id']}\n"
            f"artifact_ids: {result['artifact_ids']}"
        )
    except Exception as e:
        import traceback

        return f"bio_register_external_analysis_result 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_run_geneset_score(args: dict) -> str:
    """任意基因模組評分（scanpy score_genes 封裝）。"""
    try:
        from analysis.sc_spatial_tools import run_geneset_score

        result = run_geneset_score(
            sample_id=args["sample_id"],
            h5ad_path=args["h5ad_path"],
            modules=args["modules"],
            out_dir=args["out_dir"],
            cell_filter=args.get("cell_filter"),
            group_by=args.get("group_by"),
            ctrl_size=int(args.get("ctrl_size", 50)),
            counts_layer=args.get("counts_layer", "counts"),
            requested_by=args.get("requested_by", "agent"),
        )
        return (
            f"bio_run_geneset_score 完成。\n"
            f"analysis_id: {result['analysis_id']}\n"
            f"評分細胞數: {result['n_cells_scored']:,}\n"
            f"使用的基因（每模組）: {result['genes_used']}\n"
            f"輸出: {result['out_csv']}"
        )
    except Exception as e:
        import traceback

        return f"bio_run_geneset_score 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_compute_spatial_nn_distance(args: dict) -> str:
    """任意兩群細胞的空間最近鄰距離。"""
    try:
        from analysis.sc_spatial_tools import compute_spatial_nn_distance

        result = compute_spatial_nn_distance(
            sample_id=args["sample_id"],
            h5ad_path=args["h5ad_path"],
            source_filter=args["source_filter"],
            target_filter=args["target_filter"],
            out_dir=args["out_dir"],
            group_by=args.get("group_by"),
            spatial_key=args.get("spatial_key", "spatial"),
            distance_scale=float(args.get("distance_scale", 1.0)),
            distance_unit=args.get("distance_unit", ""),
            requested_by=args.get("requested_by", "agent"),
        )
        return (
            f"bio_compute_spatial_nn_distance 完成。\n"
            f"analysis_id: {result['analysis_id']}\n"
            f"來源細胞數: {result['n_source_cells']:,}　目標細胞數: {result['n_target_cells']:,}\n"
            f"輸出: {result['out_csv']}"
        )
    except Exception as e:
        import traceback

        return f"bio_compute_spatial_nn_distance 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_run_sc_clustering(args: dict) -> str:
    """任意單細胞 h5ad 的 QC + Leiden clustering + UMAP。"""
    try:
        from analysis.sc_clustering import run_sc_clustering

        result = run_sc_clustering(
            sample_id=args["sample_id"],
            h5ad_path=args["h5ad_path"],
            out_dir=args["out_dir"],
            min_counts=args.get("min_counts"),
            min_genes=args.get("min_genes"),
            qc_percentile=int(args.get("qc_percentile", 10)),
            min_bins=int(args.get("min_bins", 0)),
            n_top_genes=int(args.get("n_top_genes", 1000)),
            n_pcs=int(args.get("n_pcs", 15)),
            n_neighbors=int(args.get("n_neighbors", 15)),
            resolution=float(args.get("resolution", 0.5)),
            n_top_markers=int(args.get("n_top_markers", 10)),
            marker_gene_sets=args.get("marker_gene_sets"),
            score_threshold=float(args.get("score_threshold", 0.05)),
            unassigned_label=args.get("unassigned_label", "Unassigned"),
            requested_by=args.get("requested_by", "agent"),
        )
        cluster_lines = "\n".join(
            f"  cluster {cl}: {n:,} cells"
            + (f" → {result['cell_type_assignment'][cl]}" if result.get("cell_type_assignment") else "")
            for cl, n in result["cluster_sizes"].items()
        )
        return (
            f"bio_run_sc_clustering 完成。\n"
            f"analysis_id: {result['analysis_id']}\n"
            f"細胞數: {result['n_cells_before']:,} → {result['n_cells_after']:,}（QC 後）\n"
            f"Cluster 數: {result['n_clusters']}\n{cluster_lines}\n"
            f"h5ad_out: {result['h5ad_out']}\n"
            f"report: {result['report_path']}"
        )
    except Exception as e:
        import traceback

        return f"bio_run_sc_clustering 失敗：{e}\n{traceback.format_exc()[-2000:]}"


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
            try:
                from store.factory import get_store as _get_store

                _sample = _get_store().get_sample(sample_id)
                if _sample:
                    l3 = _Path(_sample["l3_path"])
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


def _exec_bio_get_marker_genes(args: dict) -> str:
    """Rank marker genes for an existing MCseg ROI umap_computed.h5ad."""
    from pathlib import Path as _Path
    sample_id = args["sample_id"]
    roi_name = args["roi_name"]
    roi_dir = args.get("roi_dir")
    groupby = args.get("groupby", "leiden")
    n_genes = int(args.get("n_genes", 20))
    method = args.get("method", "wilcoxon")
    requested_by = str(args.get("requested_by", "agent"))

    try:
        from analysis.marker_genes import run_marker_genes
        analysis_id, report_path = run_marker_genes(
            sample_id=sample_id, roi_name=roi_name,
            roi_dir=_Path(roi_dir) if roi_dir else None,
            groupby=groupby, n_genes=n_genes, method=method,
            requested_by=requested_by,
        )
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"bio_get_marker_genes 完成。\n"
            f"sample_id: {sample_id}  ROI: {roi_name}\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except Exception as e:
        import traceback
        return f"bio_get_marker_genes 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_convert_ndpi_to_tiff(args: dict) -> str:
    """Convert a Hamamatsu .ndpi whole-slide image to a pyramidal BigTIFF."""
    sample_id = args["sample_id"]
    input_ndpi = args["input_ndpi"]
    output_tiff = args.get("output_tiff")
    compression = str(args.get("compression", "jpeg"))
    quality = int(args.get("quality", 85))
    tile_size = int(args.get("tile_size", 256))
    requested_by = str(args.get("requested_by", "agent"))

    try:
        from analysis.image_conversion import convert_ndpi_to_tiff
        analysis_id, out_path = convert_ndpi_to_tiff(
            sample_id=sample_id, input_ndpi=input_ndpi, output_tiff=output_tiff,
            compression=compression, quality=quality, tile_size=tile_size,
            requested_by=requested_by,
        )
        # 注意：out_path 是 GB 級二進位 TIFF，不可像其他 handler 那樣 read_text() 內嵌預覽。
        return (
            f"bio_convert_ndpi_to_tiff 完成。\n"
            f"sample_id: {sample_id}\n"
            f"analysis_id: {analysis_id}\n"
            f"input_ndpi: {input_ndpi}\n"
            f"output_tiff: {out_path}\n"
        )
    except Exception as e:
        import traceback
        return f"bio_convert_ndpi_to_tiff 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_relabel_clusters(args: dict) -> str:
    """Apply manual label_map to cluster column, regenerate UMAP."""
    from pathlib import Path as _Path
    sample_id = args["sample_id"]
    roi_name = args["roi_name"]
    label_map = args.get("label_map") or {}
    roi_dir = args.get("roi_dir")
    groupby = args.get("groupby", "leiden")
    requested_by = str(args.get("requested_by", "agent"))

    if not isinstance(label_map, dict):
        return "bio_relabel_clusters 失敗：label_map 必須是 JSON 物件（dict）。"

    try:
        from analysis.relabel_clusters import run_relabel_clusters
        analysis_id, report_path = run_relabel_clusters(
            sample_id=sample_id, roi_name=roi_name, label_map=label_map,
            roi_dir=_Path(roi_dir) if roi_dir else None,
            groupby=groupby, requested_by=requested_by,
        )
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"bio_relabel_clusters 完成。\n"
            f"sample_id: {sample_id}  ROI: {roi_name}\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except Exception as e:
        import traceback
        return f"bio_relabel_clusters 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_run_celltypist(args: dict) -> str:
    """CellTypist automated cell type annotation on existing umap_computed.h5ad."""
    from pathlib import Path as _Path
    sample_id = args["sample_id"]
    roi_name = args["roi_name"]
    roi_dir = args.get("roi_dir")
    model = args.get("model", "Immune_All_Low.pkl")
    majority_voting = bool(args.get("majority_voting", True))
    requested_by = str(args.get("requested_by", "agent"))

    try:
        from analysis.celltypist_annotate import run_celltypist
        analysis_id, report_path = run_celltypist(
            sample_id=sample_id, roi_name=roi_name,
            roi_dir=_Path(roi_dir) if roi_dir else None,
            model=model, majority_voting=majority_voting,
            requested_by=requested_by,
        )
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"bio_run_celltypist 完成。\n"
            f"sample_id: {sample_id}  ROI: {roi_name}  model: {model}\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except Exception as e:
        import traceback
        return f"bio_run_celltypist 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_run_mcseg_merge(args: dict) -> str:
    """Merge multiple MCseg ROI cellpose_cells.h5ad and run integrated Scanpy pipeline."""
    from pathlib import Path as _Path
    sample_id = args["sample_id"]
    roi_names = args.get("roi_names") or []
    merged_name = args.get("merged_name", "merged")
    output_base = args.get("output_base")
    integrate = args.get("integrate", "auto")
    requested_by = str(args.get("requested_by", "agent"))

    if not isinstance(roi_names, list) or len(roi_names) < 2:
        return "bio_run_mcseg_merge 失敗：roi_names 必須是至少包含 2 個名稱的陣列。"

    try:
        from analysis.mcseg_merge import run_mcseg_merge
        analysis_id, report_path = run_mcseg_merge(
            sample_id=sample_id, roi_names=roi_names, merged_name=merged_name,
            output_base=_Path(output_base) if output_base else None,
            integrate=integrate, requested_by=requested_by,
        )
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"bio_run_mcseg_merge 完成。\n"
            f"sample_id: {sample_id}  merged_name: {merged_name}\n"
            f"ROIs: {roi_names}\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except Exception as e:
        import traceback
        return f"bio_run_mcseg_merge 失敗：{e}\n{traceback.format_exc()[-2000:]}"


def _exec_bio_export_loupe(args: dict) -> str:
    """Export MCseg ROI to Loupe Browser GeoJSON + cell_metadata.csv (+ optional .cloupe)."""
    from pathlib import Path as _Path
    sample_id = args["sample_id"]
    roi_name = args["roi_name"]
    roi_dir = args.get("roi_dir")
    pixel_size_um = float(args.get("pixel_size_um", 0.2737))
    requested_by = str(args.get("requested_by", "agent"))

    try:
        from analysis.loupe_export import run_loupe_export
        analysis_id, report_path = run_loupe_export(
            sample_id=sample_id, roi_name=roi_name,
            roi_dir=_Path(roi_dir) if roi_dir else None,
            pixel_size_um=pixel_size_um, requested_by=requested_by,
        )
        try:
            report_text = _Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_text = ""
        return (
            f"bio_export_loupe 完成。\n"
            f"sample_id: {sample_id}  ROI: {roi_name}\n"
            f"analysis_id: {analysis_id}\n"
            f"report_path: {report_path}\n\n"
            f"{report_text}"
        )
    except Exception as e:
        import traceback
        return f"bio_export_loupe 失敗：{e}\n{traceback.format_exc()[-2000:]}"
