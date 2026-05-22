"""
Evo_PRISM — Spatial Transcriptomics Executor Submodule.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def _exec_bio_check_l2_sufficiency(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH

    sample_id = args["sample_id"]
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT l2_ready, l3_path, data_type FROM sample_registry WHERE sample_id=?",
            [sample_id],
        ).fetchone()
    if row is None:
        return f"樣本 {sample_id!r} 不存在於 sample_registry，請先執行 bio_register_sample。"
    l2_ready, l3_path, data_type = row
    if l2_ready:
        return f"l2_ready=true。樣本 {sample_id!r} 的 L2 Parquet 已就緒，可直接執行分析。"
    _py = sys.executable
    cmd = (
        f"{_py} scripts/02_spatial_to_parquet.py --sample-id {sample_id}"
        if data_type in ("visium_hd", "visium")
        else f"# data_type={data_type!r}，請手動執行對應的 L2 轉換腳本。"
    )
    return (
        f"l2_ready=false。樣本 {sample_id!r} 尚未完成 L2 轉換。\n"
        f"l3_path: {l3_path}\n"
        f"執行以下命令完成轉換後再重試：\n{cmd}"
    )


def _exec_bio_run_spatial_eda(args: dict) -> str:
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.report_generator import run_full_eda_report

    sample_id = args["sample_id"]
    requested_by = args.get("requested_by", "agent")

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT l2_ready FROM sample_registry WHERE sample_id=?", [sample_id]
        ).fetchone()
    if row is None:
        return f"樣本 {sample_id!r} 不存在於 sample_registry，請先執行 bio_register_sample。"
    if not row[0]:
        return (
            f"樣本 {sample_id!r} 的 L2 Parquet 尚未就緒（l2_ready=false）。\n"
            f"請先呼叫 bio_check_l2_sufficiency 確認並執行轉換命令。"
        )

    # run_full_eda_report 內部已實作完整兩階段寫入（INSERT running → UPDATE completed/failed）
    try:
        result = run_full_eda_report(sample_id, requested_by=requested_by)
        summary = result.get("summary", "")
        report_path = result.get("report_path", "(無)")
        # 讀取完整報告（含 inline base64 圖片），讓 web_app 解析並顯示
        report_text = ""
        if report_path and report_path != "(無)":
            try:
                from pathlib import Path as _Path

                report_text = _Path(report_path).read_text(encoding="utf-8")
            except Exception:
                pass
        analysis_id = result.get("analysis_id", "")
        # tool_id 已由分析函數內部回填（analysis.report_generator）；此處不再重複。

        header = (
            f"EDA 完成。\n"
            f"analysis_id: {analysis_id}\n"
            f"summary: {summary}\n"
            f"report_path: {report_path}\n\n"
        )
        return header + report_text
    except Exception as e:
        logger.exception("bio_run_spatial_eda failed for %r", sample_id)
        return f"EDA 執行失敗：{e}"
