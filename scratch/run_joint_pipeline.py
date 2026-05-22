#!/usr/bin/env python3
"""
Evo_PRISM Joint Downstream Pipeline (98 Samples bulk RNA-seq)
端對端聯合下游分析測試腳本。

執行流程：
1. EDA：分析 98 樣本的 count 矩陣與相關矩陣，產出報告與圖檔。
2. DEG：進行多組對照 (pw24hr vs ctrl, pw48hr vs ctrl, pw72hr vs ctrl, pw120hr vs ctrl) 的差異分析與火山圖繪製。
3. Heatmap：基於 DEG 顯著基因 union 繪製 Heatmap。
4. ORA：對所有 DEG 組別的 Up/Down 基因集進行 GO/KEGG/Reactome 富集分析。
5. Observability：記錄每一階段的執行耗時與狀態至 DuckDB 的 mcp_tool_metrics 表中。
6. DB Check：查詢並展示 analysis_history 與 mcp_tool_metrics 中的登記資料。
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from pathlib import Path

# 將專案根目錄加入路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

# 強制重設標準輸出與標準錯誤的編碼為 UTF-8，防止 Windows CP950 下 emoji 輸出崩潰 (Eq. CP950)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import duckdb
import pandas as pd

from config.settings import DUCKDB_PATH
from analysis.bulk_eda import generate_bulk_report
from analysis.bulk_deg import run_deg_analysis
from analysis.bulk_heatmap import run_bulk_heatmaps
from analysis.enrichment import run_ora
from analysis.path_utils import results_dir
from server.bio_memory_server import _record_metric

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] — %(message)s",
)
logger = logging.getLogger("joint_pipeline")


def run_pipeline() -> None:
    sample_id = "Kallisto_v1"
    counts_path = Path("i:/Evo_PRISM/bulk_rna_data/Kallisto_v1/results_kallisto/gene_counts_mapped_symbol.tsv")
    deseq2_counts = Path("i:/Evo_PRISM/bulk_rna_data/Kallisto_v1/results_kallisto/deseq2_counts.csv")
    coldata_path = Path("i:/Evo_PRISM/bulk_rna_data/Kallisto_v1/results_kallisto/deseq2_coldata.tsv")

    # 確保四大核心分析工具已在 tools 中註冊，以供 metrics 填寫與回填 tool_id (L2 Observability) (AB4)
    logger.info("以 Lazy Registry 裝飾器自動登記四大下游工具於 DuckDB 中...")
    from analysis.tool_registry import register_all_lazy_tools
    with duckdb.connect(str(DUCKDB_PATH)) as con:
        register_all_lazy_tools(con)
    logger.info("✓ 四大核心分析工具註冊完畢！")

    logger.info("==============================================================")
    logger.info("   Evo_PRISM Joint Downstream Pipeline (98 Samples) 啟動")
    logger.info("==============================================================")
    logger.info("數據庫路徑：%s", DUCKDB_PATH)
    logger.info("樣本 ID：%s", sample_id)
    logger.info("計數矩陣 (tsv)：%s", counts_path)
    logger.info("計數矩陣 (csv)：%s", deseq2_counts)
    logger.info("設計元數據：%s", coldata_path)

    # 1. EDA 階段
    logger.info("\n--- [1/4] EDA 探索性數據分析 ---")
    start_time = time.time()
    try:
        analysis_id, report_path = generate_bulk_report(
            sample_id=sample_id,
            counts_path=counts_path,
            requested_by="agent"
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info("✓ EDA 成功！報告：%s", report_path)
        _record_metric("bio_run_bulk_eda", duration_ms, "ok", requested_by="agent")
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception("✗ EDA 失敗：%s", e)
        _record_metric("bio_run_bulk_eda", duration_ms, "system_error", error_class=type(e).__name__, requested_by="agent")
        raise

    # 2. DEG 差異表達分析階段
    logger.info("\n--- [2/4] DEG 差異表達分析 ---")
    comparisons = [
        ("pw24hr", "ctrl"),
        ("pw48hr", "ctrl"),
        ("pw72hr", "ctrl"),
        ("pw120hr", "ctrl")
    ]
    start_time = time.time()
    try:
        deg_analysis_id, deg_report_path = run_deg_analysis(
            sample_id=sample_id,
            counts_path=deseq2_counts,
            coldata_path=coldata_path,
            comparisons=comparisons,
            fc_threshold=1.0,
            pval_threshold=0.05,
            requested_by="agent"
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info("✓ DEG 成功！報告：%s", deg_report_path)
        _record_metric("bio_run_deg", duration_ms, "ok", requested_by="agent")
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception("✗ DEG 失敗：%s", e)
        _record_metric("bio_run_deg", duration_ms, "system_error", error_class=type(e).__name__, requested_by="agent")
        raise

    # 3. Heatmap 階段
    logger.info("\n--- [3/4] Heatmap 熱圖繪製 ---")
    start_time = time.time()
    try:
        # 蒐集產出的 DEG table paths
        deg_dir = results_dir(sample_id, "bulk_deg")
        deg_tables = sorted(deg_dir.glob("DEG_*.csv"))
        logger.info("收集到 DEG 結果檔：%s", [p.name for p in deg_tables])

        hm_analysis_id, hm_report_path = run_bulk_heatmaps(
            sample_id=sample_id,
            counts_path=deseq2_counts,
            deg_tables=deg_tables,
            top_n=50,
            fc_threshold=1.0,
            pval_threshold=0.05,
            requested_by="agent"
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info("✓ Heatmap 成功！報告：%s", hm_report_path)
        _record_metric("bio_run_heatmaps", duration_ms, "ok", requested_by="agent")
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception("✗ Heatmap 失敗：%s", e)
        _record_metric("bio_run_heatmaps", duration_ms, "system_error", error_class=type(e).__name__, requested_by="agent")
        raise

    # 4. ORA 富集分析階段
    logger.info("\n--- [4/4] ORA 富集分析 ---")
    start_time = time.time()
    try:
        # 對每組 DEG table 跑富集分析
        ora_reports = []
        for deg_path in deg_tables:
            logger.info("對 %s 進行通路富集...", deg_path.name)
            ora_analysis_id, ora_report_path = run_ora(
                sample_id=sample_id,
                deg_table_path=deg_path,
                libraries=["GO_Biological_Process_2023", "KEGG_2019_Mouse", "Reactome_2022"],
                organism="mouse",
                fc_threshold=1.0,
                pval_threshold=0.05,
                requested_by="agent"
            )
            ora_reports.append(ora_report_path)
            logger.info("✓ %s 富集分析完成！報告：%s", deg_path.name, ora_report_path)

        duration_ms = int((time.time() - start_time) * 1000)
        logger.info("✓ ORA 富集分析全數完成！")
        _record_metric("bio_run_enrichment", duration_ms, "ok", requested_by="agent")
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception("✗ ORA 失敗：%s", e)
        _record_metric("bio_run_enrichment", duration_ms, "system_error", error_class=type(e).__name__, requested_by="agent")
        raise

    logger.info("==============================================================")
    logger.info("   Evo_PRISM Joint Downstream Pipeline 執行完畢")
    logger.info("==============================================================")


def verify_database_entries() -> None:
    logger.info("\n--- [5/5] 驗證 DuckDB 記錄與指標 ---")
    with duckdb.connect(str(DUCKDB_PATH)) as con:
        # 1. 查詢 analysis_history
        logger.info("\n[1] analysis_history 執行歷史記錄：")
        df_hist = con.execute("""
            SELECT analysis_id, analysis_type, status, completed_at, summary, tool_id
            FROM analysis_history
            WHERE sample_id='Kallisto_v1'
            ORDER BY started_at ASC
        """).fetchdf()
        print(df_hist.to_markdown(index=False))

        # 2. 查詢 mcp_tool_metrics
        logger.info("\n[2] mcp_tool_metrics 指標記錄：")
        df_metrics = con.execute("""
            SELECT metric_id, tool_name, tool_id, duration_ms, status, error_class, recorded_at
            FROM mcp_tool_metrics
            ORDER BY recorded_at ASC
        """).fetchdf()
        print(df_metrics.to_markdown(index=False))

        # 3. 檢查 tool_id 覆蓋率與 metrics 關聯
        null_tool_ids = df_hist[df_hist['tool_id'].isna()]
        if not null_tool_ids.empty:
            logger.warning("警告：存在 tool_id 為空的歷史記錄！")
            print(null_tool_ids)
        else:
            logger.info("✓ 成功驗證：analysis_history 中所有運作的 tool_id 覆蓋率達 100%！")


if __name__ == "__main__":
    try:
        run_pipeline()
        verify_database_entries()
    except Exception as err:
        logger.critical("聯合下游分析管線發生致命錯誤：%s", err)
        sys.exit(1)
