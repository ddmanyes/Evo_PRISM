"""
Phase 2B — 分析報告生成器（含 50 字中文摘要）。

50 字摘要是 L1 語意搜尋的品質上限：
    嵌入向量的語意品質直接決定了搜尋召回率，因此摘要越精準，
    未來 Agent 「這個問題之前分析過嗎？」的命中率就越高。

主要函數：
    generate_eda_report()    — 彙整 QC + 基因統計，生成 Markdown 報告
    generate_summary()       — 將報告壓縮成 ≤50 字的中文摘要（規則式，不呼叫 LLM）
    write_report_to_history() — 將報告 + 摘要寫入 analysis_history
    run_full_eda_report()    — 一鍵執行 EDA → 報告 → 歷史記錄
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH
from config.db_utils import safe_write
from analysis.path_utils import results_dir as _results_dir

logger = logging.getLogger(__name__)

# ── 報告樣板 ──────────────────────────────────────────────────────────────────

_REPORT_TEMPLATE = """\
# {sample_id} — 空間轉錄體 EDA 報告

**生成時間**：{timestamp}
**樣本 ID**：{sample_id}
**資料來源**：L2 Silver Parquet（8µm bins）

---

## 1. 資料概覽

| 指標 | 數值 |
|------|------|
| 有效 bins 數量 | {n_bins:,} |
| 偵測基因數（unique） | {n_genes:,} |
| 非零表達量（entries） | {n_nonzero:,} |
| 矩陣稀疏度 | {sparsity:.2%} |

---

## 2. QC 統計（per bin）

| 指標 | 數值 |
|------|------|
| 中位 genes/bin | {median_genes:.0f} |
| 中位 UMI/bin | {median_umi:.0f} |
| 平均 genes/bin | {mean_genes:.1f} |
| 平均 UMI/bin | {mean_umi:.1f} |
| bins with 0 genes | {zero_bins:,} ({zero_pct:.1%}) |

{qc_figure}

---

## 3. 前 20 高表達基因

{top_genes_table}

---

## 4. 空間覆蓋率

| 指標 | 數值 |
|------|------|
| array_row 範圍 | {row_min} – {row_max} |
| array_col 範圍 | {col_min} – {col_max} |
| 有效 bin 密度 | {valid_density:.2%}（有表達 / 總 bins） |

---

## 5. 結論摘要

{summary}

---

*由 BioAgent `report_generator.py` 自動生成。*
"""


# ── 內部工具 ──────────────────────────────────────────────────────────────────

from analysis.validators import validate_sample_id
from analysis.tool_registry import register_tool_on_import


def _l2_expr_glob(sample_id: str) -> str:
    from config.settings import BIO_DB_ROOT, L2_ROOT

    resolved = (L2_ROOT / sample_id).resolve()
    if not resolved.is_relative_to(BIO_DB_ROOT.resolve()):
        raise ValueError(f"Path traversal detected: {sample_id!r}")
    return str(resolved / "expression" / "*.parquet")


def _l2_obs_path(sample_id: str) -> str:
    from config.settings import BIO_DB_ROOT, L2_ROOT

    resolved = (L2_ROOT / sample_id).resolve()
    if not resolved.is_relative_to(BIO_DB_ROOT.resolve()):
        raise ValueError(f"Path traversal detected: {sample_id!r}")
    return str(resolved / "obs_metadata.parquet")


def _collect_stats(sample_id: str, db_path: Path) -> dict:
    """從 L2 Parquet 收集統計數字（純 DuckDB，0-token）。"""
    validate_sample_id(sample_id)
    expr_glob = _l2_expr_glob(sample_id)
    obs_path = _l2_obs_path(sample_id)

    with duckdb.connect(str(db_path), read_only=True) as con:
        n_bins = con.execute(f"SELECT COUNT(*) FROM read_parquet('{obs_path}')").fetchone()[0]

        n_genes = con.execute(
            f"SELECT COUNT(DISTINCT gene_name) FROM read_parquet('{expr_glob}')"
        ).fetchone()[0]

        n_nonzero = con.execute(f"SELECT COUNT(*) FROM read_parquet('{expr_glob}')").fetchone()[0]

        top_df = con.execute(
            f"""
            SELECT gene_name,
                   SUM(count)::BIGINT AS total_umi,
                   COUNT(*)::BIGINT   AS n_bins
            FROM   read_parquet('{expr_glob}')
            GROUP BY gene_name
            ORDER BY total_umi DESC
            LIMIT 20
            """
        ).fetchdf()

        qc_df = con.execute(
            f"""
            SELECT o.barcode,
                   o.array_row_8um,
                   o.array_col_8um,
                   COUNT(e.gene_name)        AS n_genes,
                   COALESCE(SUM(e.count), 0) AS total_counts
            FROM   read_parquet('{obs_path}') AS o
            LEFT JOIN read_parquet('{expr_glob}') AS e USING (barcode)
            GROUP BY o.barcode, o.array_row_8um, o.array_col_8um
            """
        ).fetchdf()

    sparsity = 1 - (n_nonzero / (n_bins * n_genes)) if n_bins * n_genes > 0 else 1.0
    zero_bins = int((qc_df["n_genes"] == 0).sum())

    return {
        "n_bins": n_bins,
        "n_genes": n_genes,
        "n_nonzero": n_nonzero,
        "sparsity": sparsity,
        "median_genes": float(qc_df["n_genes"].median()),
        "median_umi": float(qc_df["total_counts"].median()),
        "mean_genes": float(qc_df["n_genes"].mean()),
        "mean_umi": float(qc_df["total_counts"].mean()),
        "zero_bins": zero_bins,
        "zero_pct": zero_bins / n_bins if n_bins > 0 else 0,
        "row_min": int(qc_df["array_row_8um"].min()),
        "row_max": int(qc_df["array_row_8um"].max()),
        "col_min": int(qc_df["array_col_8um"].min()),
        "col_max": int(qc_df["array_col_8um"].max()),
        "valid_density": float((qc_df["n_genes"] > 0).mean()),
        "top_genes": top_df,
        "obs_df": qc_df,  # 供 _generate_qc_figure_b64 使用
    }


# ── 公開 API ──────────────────────────────────────────────────────────────────


def generate_summary(stats: dict, sample_id: str) -> str:
    """
    從統計數字生成 ≤50 字的中文摘要（規則式，不呼叫 LLM）。

    摘要格式：
        「{sample_id} EDA：{n_bins}萬bins，{n_genes}基因，
         中位{median_genes}基因/bin，{valid_density}有效覆蓋率，
         前三高表達基因為{top3}。」

    這是語意搜尋向量的核心語料，精準比流暢更重要。
    """
    top3 = "、".join(stats["top_genes"]["gene_name"].head(3).tolist())
    n_bins_w = stats["n_bins"] / 10000  # 萬

    summary = (
        f"{sample_id} EDA：{n_bins_w:.1f}萬bins，{stats['n_genes']:,}基因，"
        f"中位{stats['median_genes']:.0f}基因/bin，"
        f"{stats['valid_density']:.1%}有效覆蓋，"
        f"前三高：{top3}。"
    )

    # 硬截斷至 50 字（中文字符計算）
    if len(summary) > 50:
        summary = summary[:49] + "…"

    return summary


def generate_eda_report(
    sample_id: str,
    *,
    db_path: Optional[Path] = None,
) -> tuple[str, str, dict]:
    """
    生成完整 Markdown EDA 報告與 50 字摘要。

    Returns:
        (report_text, summary_text, stats_dict)
    """
    db_path = db_path or DUCKDB_PATH
    stats = _collect_stats(sample_id, db_path)

    tg = stats["top_genes"]
    top_genes_table = "| gene_name | total_umi | n_bins |\n|-----------|-----------|--------|\n"
    for _, row in tg.iterrows():
        top_genes_table += (
            f"| {row['gene_name']} | {int(row['total_umi']):,} | {int(row['n_bins']):,} |\n"
        )

    summary = generate_summary(stats, sample_id)
    qc_figure = _generate_qc_figure_b64(stats)

    report = _REPORT_TEMPLATE.format(
        sample_id=sample_id,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        n_bins=stats["n_bins"],
        n_genes=stats["n_genes"],
        n_nonzero=stats["n_nonzero"],
        sparsity=stats["sparsity"],
        median_genes=stats["median_genes"],
        median_umi=stats["median_umi"],
        mean_genes=stats["mean_genes"],
        mean_umi=stats["mean_umi"],
        zero_bins=stats["zero_bins"],
        zero_pct=stats["zero_pct"],
        top_genes_table=top_genes_table,
        row_min=stats["row_min"],
        row_max=stats["row_max"],
        col_min=stats["col_min"],
        col_max=stats["col_max"],
        valid_density=stats["valid_density"],
        summary=summary,
        qc_figure=qc_figure,
    )

    return report, summary, stats


def _generate_qc_figure_b64(stats: dict) -> str:
    """產生 QC 分布圖，回傳 Markdown 內嵌 base64 字串。失敗時回傳空字串。"""
    import base64
    import io

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        obs = stats.get("obs_df")
        if obs is None or obs.empty:
            return ""

        fig, axes = plt.subplots(1, 2, figsize=(11, 4))
        axes[0].hist(obs["n_genes"].dropna(), bins=60, color="#2563eb", edgecolor="none", alpha=0.8)
        axes[0].set_title("Genes per bin", fontsize=12)
        axes[0].set_xlabel("# genes")
        axes[0].set_ylabel("# bins")

        axes[1].hist(
            obs["total_counts"].dropna(), bins=60, color="#16a34a", edgecolor="none", alpha=0.8
        )
        axes[1].set_title("UMI per bin", fontsize=12)
        axes[1].set_xlabel("total UMI")
        axes[1].set_ylabel("# bins")

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"\n![QC distributions](data:image/png;base64,{b64})\n"
    except Exception as exc:
        logger.warning("QC figure generation failed: %s", exc)
        return ""


def write_report_to_history(
    sample_id: str,
    report_text: str,
    summary: str,
    *,
    analysis_id: Optional[str] = None,
    db_path: Optional[Path] = None,
    save_file: bool = True,
    requested_by: str = "report_generator",
) -> tuple[str, str]:
    """
    將報告存檔並寫入 analysis_history。

    若傳入 analysis_id（已 INSERT running），則 UPDATE 為 completed；
    否則直接 INSERT completed（向後相容舊呼叫方式）。

    Returns:
        (analysis_id, result_path) — UUID str 與儲存路徑（save_file=False 時路徑為空）
    """
    db_path = db_path or DUCKDB_PATH
    now = datetime.now(timezone.utc)

    result_path = ""
    if save_file:
        out_dir = _results_dir(sample_id, "report")
        fname = f"eda_report_{now.strftime('%Y%m%d_%H%M%S')}.md"
        result_path = str(out_dir / fname)
        Path(result_path).write_text(report_text, encoding="utf-8")
        logger.info("Report saved: %s", result_path)

    if analysis_id is None:
        analysis_id = str(uuid.uuid4())
        with duckdb.connect(str(db_path)) as con:
            safe_write(
                con,
                """INSERT INTO analysis_history
                       (analysis_id, sample_id, analysis_type, parameters, status,
                        result_path, requested_by, started_at, completed_at, summary)
                   VALUES (?, ?, 'eda_report', ?, 'completed', ?, ?, ?, ?, ?)""",
                [
                    analysis_id,
                    sample_id,
                    json.dumps({"format": "markdown"}),
                    result_path,
                    requested_by,
                    now,
                    now,
                    summary,
                ],
            )
    else:
        completed_at = datetime.now(timezone.utc)
        with duckdb.connect(str(db_path)) as con:
            safe_write(
                con,
                """UPDATE analysis_history
                      SET status='completed', result_path=?, completed_at=?, summary=?
                    WHERE analysis_id=?""",
                [result_path, completed_at, summary, analysis_id],
            )
            from analysis.failure_diagnosis import success_diagnosis, write_diagnosis
            write_diagnosis(con, analysis_id, success_diagnosis())
    return analysis_id, result_path


@register_tool_on_import(
    tool_name="bio_run_spatial_eda",
    version="1.0.0",
    description="執行空間轉錄體 EDA 探索性數據分析並繪製代表性基因空間圖表"
)
def run_full_eda_report(
    sample_id: str,
    *,
    db_path: Optional[Path] = None,
    save_file: bool = True,
    requested_by: str = "report_generator",
) -> dict:
    """
    一鍵執行：收集統計 → 生成報告 → 寫入歷史。

    Returns:
        {
            "analysis_id": str,
            "summary": str,          # 50 字摘要
            "report_path": str,      # 儲存路徑（save_file=False 時為空）
            "stats": dict,           # 原始統計數字
        }
    """
    db_path = db_path or DUCKDB_PATH

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    with duckdb.connect(str(db_path)) as con:
        safe_write(
            con,
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    requested_by, started_at)
               VALUES (?, ?, 'eda_report', ?, 'running', ?, ?)""",
            [analysis_id, sample_id, json.dumps({"format": "markdown"}), requested_by, started_at],
        )

    try:
        logger.info("Collecting stats for '%s'...", sample_id)
        report, summary, stats = generate_eda_report(sample_id, db_path=db_path)
        logger.info("Summary (%d chars): %s", len(summary), summary)

        analysis_id, report_path = write_report_to_history(
            sample_id,
            report,
            summary,
            analysis_id=analysis_id,
            db_path=db_path,
            save_file=save_file,
            requested_by=requested_by,
        )
    except Exception as _exc:
        logger.exception("eda_report 分析失敗  analysis_id=%s", analysis_id)
        with duckdb.connect(str(db_path)) as con:
            safe_write(
                con,
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
            from analysis.failure_diagnosis import classify_exception, write_diagnosis
            write_diagnosis(con, analysis_id, classify_exception(_exc))
        raise

    try:
        from analysis.l1_cache import write_to_l1_cache

        write_to_l1_cache(
            sample_id=sample_id,
            query_text=f"{sample_id} 空間轉錄體 EDA 分析",
            report_text=report,
            summary=summary,
            analysis_id=analysis_id,
        )
        logger.info("L1 cache written.")
    except Exception as e:
        logger.warning("L1 cache write skipped (embedding server offline?): %s", e)

    logger.info("Done. analysis_id=%s", analysis_id)

    return {
        "analysis_id": analysis_id,
        "summary": summary,
        "report_path": report_path,
        "stats": stats,
    }


if __name__ == "__main__":
    result = run_full_eda_report("crc_official_v4")
    print("\n=== Result ===")
    print(f"  analysis_id : {result['analysis_id']}")
    print(f"  summary     : {result['summary']}")
    print(f"  report_path : {result['report_path']}")
    print(f"  n_bins      : {result['stats']['n_bins']:,}")
