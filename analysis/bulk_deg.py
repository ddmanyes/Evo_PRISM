"""Bulk RNA-seq 差異表達分析（DEG）+ 火山圖。

對齊參考實作 ddmanyes/bulk-rnaseq-pipeline：
    - DESeq2 統計透過 ``omicverse.bulk.pyDEG`` 呼叫
    - 火山圖以 matplotlib + adjustText 手繪（與參考 pipeline 一致）

主要對外函數：
    run_deg_analysis(sample_id, counts_path, coldata_path, comparisons, ...)
        → (analysis_id, report_path)
        每組對照產出 DEG_<a>_vs_<b>.csv + Volcano_<a>_vs_<b>.png，
        並登記到 analysis_history (analysis_type='bulk_deg') + analysis_artifacts。

設計取捨：
    omicverse / adjustText 都是重套件，採延遲匯入；測試以 monkeypatch 取代真實 DESeq2 呼叫
    （DESeq2 對 84 樣本實跑數分鐘，不適合放 CI）。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import duckdb
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.db_utils import safe_write
from config.settings import DUCKDB_PATH, SUMMARY_MAX_CHARS
from analysis.path_utils import results_dir
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md
from analysis.tool_registry import register_tool_on_import

logger = logging.getLogger(__name__)

from analysis.validators import validate_sample_id

_GROUP_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_group(name: str) -> None:
    if not _GROUP_RE.match(name):
        raise ValueError(f"無效的 group 名稱：{name!r}")


# ── 資料載入 ─────────────────────────────────────────────────────────────────


def load_deg_inputs(
    counts_path: Path,
    coldata_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """載入 DESeq2 風格的 counts.csv + coldata.tsv。

    counts: gene index、樣本為欄；coldata: sample index、`group` 欄位必填。
    回傳 (counts, coldata)，已對齊樣本順序。
    """
    counts = pd.read_csv(counts_path, index_col=0)
    coldata = (
        pd.read_csv(coldata_path, sep="\t", index_col=0)
        if coldata_path.suffix in {".tsv", ".txt"}
        else pd.read_csv(coldata_path, index_col=0)
    )
    if "group" not in coldata.columns:
        raise ValueError(f"coldata 缺 'group' 欄：{coldata_path}")
    shared = [s for s in counts.columns if s in coldata.index]
    if not shared:
        raise ValueError("counts 與 coldata 樣本完全不重疊")
    return counts[shared], coldata.loc[shared]


# ── DEG（pyDEG/DESeq2 wrapper）──────────────────────────────────────────────


def deg_single_comparison(
    counts: pd.DataFrame,
    coldata: pd.DataFrame,
    group_a: str,
    group_b: str,
    *,
    method: str = "DEseq2",
    alpha: float = 0.05,
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
) -> pd.DataFrame:
    """跑單組對照的 DEG（A vs B；A 為 treat，B 為 ctrl）。

    回傳 omicverse DEG DataFrame，含 log2FC / qvalue / sig（up/down/normal）欄。
    """
    _validate_group(group_a)
    _validate_group(group_b)

    if "group" not in coldata.columns:
        raise ValueError("coldata 缺 'group' 欄")
    a_samples = coldata.index[coldata["group"] == group_a].tolist()
    b_samples = coldata.index[coldata["group"] == group_b].tolist()
    if not a_samples or not b_samples:
        raise ValueError(
            f"找不到對照樣本：group_a={group_a!r}({len(a_samples)}) "
            f"group_b={group_b!r}({len(b_samples)})"
        )

    # 延遲匯入 omicverse（重套件、~10s 載入）
    import omicverse as ov

    dds = ov.bulk.pyDEG(counts)
    dds.drop_duplicates_index()
    res = dds.deg_analysis(a_samples, b_samples, method=method, alpha=alpha)
    dds.foldchange_set(fc_threshold=fc_threshold, pval_threshold=pval_threshold)
    return res


# ── 火山圖（手繪 matplotlib + adjustText）─────────────────────────────────────


def volcano_plot(
    deg: pd.DataFrame,
    *,
    output_path: Path,
    title: str = "",
    fc_col: str = "log2FC",
    pval_col: str = "qvalue",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
    top_n_labels: int = 10,
    figsize: tuple[float, float] = (5.5, 5.0),
) -> Path:
    """為一張 DEG 表畫火山圖，並存檔。

    對齊參考 pipeline 的火山圖風格：
      - 上調 #e25d5d / 下調 #7388c1 / ns #d7d7d7
      - 加 fc/pval 閾值虛線；以 adjustText 標 top_n 顯著基因
    """
    required = {fc_col, pval_col}
    if missing := required - set(deg.columns):
        raise ValueError(f"deg 缺少欄位：{sorted(missing)}")
    df = deg.copy()
    df["-log10p"] = -np.log10(df[pval_col].clip(lower=1e-300))
    up = (df[fc_col] > fc_threshold) & (df[pval_col] < pval_threshold)
    dn = (df[fc_col] < -fc_threshold) & (df[pval_col] < pval_threshold)
    colors = np.where(up, "#e25d5d", np.where(dn, "#7388c1", "#d7d7d7"))

    fig, ax = plt.subplots(figsize=figsize)
    ax.scatter(df[fc_col], df["-log10p"], c=colors, s=8, alpha=0.7, edgecolors="none")
    ax.axhline(-np.log10(pval_threshold), color="grey", ls="--", lw=0.8)
    ax.axvline(fc_threshold, color="grey", ls="--", lw=0.8)
    ax.axvline(-fc_threshold, color="grey", ls="--", lw=0.8)
    ax.set_xlabel(f"{fc_col}")
    ax.set_ylabel(f"-log10({pval_col})")
    ax.set_title(title or "Volcano")

    # Top-N labels via adjustText（缺套件時降級為前 N 直接 annotate）
    sig = df[up | dn].nlargest(top_n_labels, "-log10p")
    if not sig.empty:
        texts = [ax.text(r[fc_col], r["-log10p"], str(g), fontsize=8) for g, r in sig.iterrows()]
        try:
            from adjustText import adjust_text

            adjust_text(texts, ax=ax, arrowprops=dict(arrowstyle="-", color="grey", lw=0.4))
        except ImportError:
            logger.debug("adjustText 未安裝；跳過標籤避撞")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


# ── 主流程：多組對照 + 報告 ──────────────────────────────────────────────────

_REPORT_TEMPLATE = """# Bulk DEG 分析報告

- **樣本登記 ID**：{sample_id}
- **執行時間**：{timestamp}
- **方法**：{method}（pyDEG / omicverse 封裝）
- **閾值**：|log2FC| > {fc_thr}, qvalue < {pval_thr}
- **對照組數**：{n_comparisons}

## 結果摘要

{summary_table}

## 火山圖

{volcano_figs}
"""


@register_tool_on_import(
    tool_name="bio_run_deg",
    version="1.0.0",
    description="對指定對照組別進行 bulk RNA-seq 差異表達分析 (DESeq2)",
)
def run_deg_analysis(
    sample_id: str,
    *,
    counts_path: Path,
    coldata_path: Path,
    comparisons: Sequence[tuple[str, str]],
    method: str = "DEseq2",
    fc_threshold: float = 1.0,
    pval_threshold: float = 0.05,
    requested_by: str = "agent",
    con: Optional[duckdb.DuckDBPyConnection] = None,
) -> tuple[str, str]:
    """跑多組對照的 DEG，產出每組 DEG CSV + 火山圖 + 彙整報告。

    Args:
        sample_id:       已登記的樣本 ID
        counts_path:     gene × sample 計數矩陣（CSV）
        coldata_path:    sample × group 設計表（TSV 或 CSV，需 'group' 欄）
        comparisons:     [(group_a, group_b), ...] —— A vs B（A=treat, B=ctrl）
        method:          'DEseq2'（pyDEG 預設）/ 'ttest' / 'wilcox'（依 omicverse 版本支援）
        fc_threshold:    |log2FC| 顯著閾值
        pval_threshold:  qvalue 顯著閾值
        con:             可傳入既有連線供測試 monkeypatch；None 則開新連線

    Returns:
        (analysis_id, report_path)
    """
    validate_sample_id(sample_id)
    if not comparisons:
        raise ValueError("comparisons 不可為空")
    for a, b in comparisons:
        _validate_group(a)
        _validate_group(b)

    counts_path = Path(counts_path)
    coldata_path = Path(coldata_path)

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps(
        {
            "counts_path": str(counts_path),
            "coldata_path": str(coldata_path),
            "comparisons": [list(c) for c in comparisons],
            "method": method,
            "fc_threshold": fc_threshold,
            "pval_threshold": pval_threshold,
        }
    )

    _own_con = con is None
    if con is None:
        con = duckdb.connect(str(DUCKDB_PATH))

    try:
        safe_write(
            con,
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    requested_by, started_at)
               VALUES (?, ?, 'bulk_deg', ?, 'running', ?, ?)""",
            [analysis_id, sample_id, params_json, requested_by, started_at],
        )

        counts, coldata = load_deg_inputs(counts_path, coldata_path)
        out_dir = results_dir(sample_id, "bulk_deg")
        ts = started_at.strftime("%Y%m%d_%H%M%S")

        summary_rows: list[dict] = []
        volcano_md_parts: list[str] = []
        artifact_files: list[tuple[Path, str, str, str]] = []
        # 結構：(path, artifact_type, label, subtype)

        for a, b in comparisons:
            try:
                deg = deg_single_comparison(
                    counts,
                    coldata,
                    a,
                    b,
                    method=method,
                    fc_threshold=fc_threshold,
                    pval_threshold=pval_threshold,
                )
            except Exception as exc:
                logger.warning("DEG %s_vs_%s 失敗：%s", a, b, exc, exc_info=True)
                summary_rows.append(
                    {
                        "comparison": f"{a}_vs_{b}",
                        "n_sig_up": 0,
                        "n_sig_down": 0,
                        "n_total": 0,
                        "status": f"failed: {type(exc).__name__}",
                    }
                )
                continue

            deg_csv = out_dir / f"DEG_{a}_vs_{b}_{ts}.csv"
            deg.to_csv(deg_csv)
            artifact_files.append((deg_csv, "csv", f"DEG {a} vs {b}", "deg_table"))

            volcano_png = out_dir / f"Volcano_{a}_vs_{b}_{ts}.png"
            try:
                volcano_plot(
                    deg,
                    output_path=volcano_png,
                    title=f"{a} vs {b}",
                    fc_threshold=fc_threshold,
                    pval_threshold=pval_threshold,
                )
                volcano_md_parts.append(_file_to_b64_md(volcano_png, f"Volcano {a} vs {b}"))
                artifact_files.append((volcano_png, "figure", f"火山圖 {a} vs {b}", "volcano"))
            except Exception:
                logger.warning("火山圖 %s_vs_%s 失敗，跳過", a, b, exc_info=True)
                volcano_md_parts.append(f"\n（{a} vs {b} 火山圖生成失敗）\n")

            up = (
                (deg.get("log2FC", pd.Series(dtype=float)) > fc_threshold)
                & (deg.get("qvalue", pd.Series(dtype=float)) < pval_threshold)
            ).sum()
            dn = (
                (deg.get("log2FC", pd.Series(dtype=float)) < -fc_threshold)
                & (deg.get("qvalue", pd.Series(dtype=float)) < pval_threshold)
            ).sum()
            summary_rows.append(
                {
                    "comparison": f"{a}_vs_{b}",
                    "n_sig_up": int(up),
                    "n_sig_down": int(dn),
                    "n_total": int(len(deg)),
                    "status": "ok",
                }
            )

        summary_df = pd.DataFrame(summary_rows)
        report_path = out_dir / f"bulk_deg_{sample_id}_{ts}.md"
        report_path.write_text(
            _REPORT_TEMPLATE.format(
                sample_id=sample_id,
                timestamp=started_at.isoformat(),
                method=method,
                fc_thr=fc_threshold,
                pval_thr=pval_threshold,
                n_comparisons=len(comparisons),
                summary_table=summary_df.to_markdown(index=False),
                volcano_figs="\n".join(volcano_md_parts) or "（無火山圖）",
            ),
            encoding="utf-8",
        )
        artifact_files.append((report_path, "report", "Bulk DEG 分析報告", "deg_report"))

        total_sig = int(summary_df[["n_sig_up", "n_sig_down"]].to_numpy().sum())
        full_summary = f"Bulk DEG {sample_id}：{len(comparisons)} 對照，共 {total_sig} 顯著基因。"
        summary = full_summary[:SUMMARY_MAX_CHARS]

        completed_at = datetime.now(timezone.utc)
        safe_write(
            con,
            """UPDATE analysis_history
                  SET status='completed', result_path=?, completed_at=?, summary=?
                WHERE analysis_id=?""",
            [str(report_path), completed_at, summary, analysis_id],
        )
        from analysis.failure_diagnosis import success_diagnosis, write_diagnosis

        write_diagnosis(con, analysis_id, success_diagnosis())
        try:
            from analysis.artifact_registry import register_artifact

            for path, atype, label, subtype in artifact_files:
                if path.exists():
                    register_artifact(
                        con, analysis_id, path, atype, label, artifact_subtype=subtype
                    )
        except Exception as _exc:
            logger.warning("bulk_deg: register_artifact 失敗（非致命）: %s", _exc)

    except Exception as _exc_outer:
        logger.exception("bulk_deg 分析失敗  analysis_id=%s", analysis_id)
        from analysis.failure_diagnosis import classify_exception, write_diagnosis

        try:
            safe_write(
                con,
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
            write_diagnosis(con, analysis_id, classify_exception(_exc_outer))
        finally:
            if _own_con:
                con.close()
        raise

    if _own_con:
        con.close()
    logger.info("bulk_deg 完成  analysis_id=%s  report=%s", analysis_id, report_path)
    return analysis_id, str(report_path)
