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
