"""
Phase 2B — 空間轉錄體基礎探索分析。

主要函數：
    gene_spatial_map()  — 單基因空間表達圖（matplotlib 輸出）
    qc_stats()          — 每 bin QC 統計（n_genes, total_counts, density）
    top_genes()         — 依總表達量排序的前 N 基因
    gene_coexpression() — 兩基因空間疊圖（3 panels）+ 共表達散點
"""

from __future__ import annotations

import uuid
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")  # 無 display 環境

import re
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import L2_ROOT, DUCKDB_PATH
from config.db_utils import safe_write
from analysis.viz_utils import fig_to_b64_md as _fig_to_b64_md
from analysis.path_utils import results_dir as _results_dir
from analysis.validators import validate_sample_id

logger = logging.getLogger(__name__)


_GENE_NAME_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")

_TOOL_NAME = "bio_run_spatial_eda"


def _validate_gene_name(name: str) -> None:
    if not _GENE_NAME_RE.match(name):
        raise ValueError(f"Invalid gene name {name!r}")


def _l2_expr_glob(sample_id: str) -> str:
    base = (L2_ROOT / sample_id / "expression").resolve()
    if not base.is_relative_to(L2_ROOT.resolve()):
        raise ValueError(f"Path traversal detected for sample_id={sample_id!r}")
    path = str(base / "*.parquet")
    if "'" in path:
        raise ValueError(f"Unsafe character in path: {path!r}")
    return path


def _l2_obs_path(sample_id: str) -> str:
    p = (L2_ROOT / sample_id / "obs_metadata.parquet").resolve()
    if not p.is_relative_to(L2_ROOT.resolve()):
        raise ValueError(f"Path traversal detected for sample_id={sample_id!r}")
    path = str(p)
    if "'" in path:
        raise ValueError(f"Unsafe character in path: {path!r}")
    return path


def _record_analysis(
    con: duckdb.DuckDBPyConnection,
    sample_id: str,
    analysis_type: str,
    parameters: dict,
    result_path: str,
    summary: str,
    status: str = "completed",
    requested_by: str = "spatial_eda",
) -> str:
    """Write analysis record and return analysis_id."""
    analysis_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    safe_write(
        con,
        """
        INSERT INTO analysis_history
            (analysis_id, sample_id, analysis_type, parameters, status,
             result_path, requested_by, started_at, completed_at, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            analysis_id,
            sample_id,
            analysis_type,
            json.dumps(parameters),
            status,
            result_path,
            requested_by,
            now,
            now,
            summary,
        ],
    )
    return analysis_id


def _backfill(con: duckdb.DuckDBPyConnection, analysis_id: str) -> None:
    try:
        from analysis.tool_registry import backfill_tool_id
        backfill_tool_id(con, _TOOL_NAME, analysis_id)
    except Exception as exc:
        logger.warning("backfill_tool_id failed (non-fatal): %s", exc)


def _register(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
    file_path: str,
    artifact_type: str,
    label: str,
    subtype: str,
    producing_fn=None,
) -> None:
    try:
        from analysis.artifact_registry import register_artifact
        register_artifact(
            con, analysis_id, file_path, artifact_type, label,
            artifact_subtype=subtype, producing_fn=producing_fn,
        )
    except Exception as exc:
        logger.warning("register_artifact failed (non-fatal): %s", exc)


# ── 公開 API ──────────────────────────────────────────────────────────────────


def gene_spatial_map(
    sample_id: str,
    gene_name: str,
    *,
    vmax_pct: float = 99.0,
    figsize: tuple[int, int] = (8, 7),
    save: bool = True,
    db_path: Optional[Path] = None,
    requested_by: str = "spatial_eda",
) -> tuple[str, str]:
    """繪製單基因空間表達圖，座標為 array_row/col（8µm bin grid）。

    Returns:
        (out_path, fig_md) — 儲存路徑與 inline base64 Markdown 字串
        （save=False 時兩者均為空字串）
    """
    validate_sample_id(sample_id)
    _validate_gene_name(gene_name)
    db_path = db_path or DUCKDB_PATH
    expr_glob = _l2_expr_glob(sample_id)
    obs_path = _l2_obs_path(sample_id)

    with duckdb.connect(str(db_path), read_only=True) as con:
        df = con.execute(
            f"""
            SELECT o.array_row_8um AS row,
                   o.array_col_8um AS col,
                   COALESCE(e.count, 0) AS expr
            FROM   read_parquet('{obs_path}') AS o
            LEFT JOIN (
                SELECT barcode, count
                FROM   read_parquet('{expr_glob}')
                WHERE  gene_name = ?
            ) AS e USING (barcode)
            """,
            [gene_name],
        ).fetchdf()

    if df.empty:
        raise ValueError(f"No spatial data found for sample '{sample_id}'")

    pivot = df.pivot_table(index="row", columns="col", values="expr", fill_value=0)
    vmax = float(df["expr"].quantile(vmax_pct / 100))

    # Use real coordinate extent so axes reflect actual array positions
    row_min, row_max = int(df["row"].min()), int(df["row"].max())
    col_min, col_max = int(df["col"].min()), int(df["col"].max())

    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(
        pivot.values,
        cmap="RdYlBu_r",
        vmin=0,
        vmax=max(vmax, 1),
        aspect="auto",
        extent=[col_min, col_max, row_max, row_min],
    )
    plt.colorbar(im, ax=ax, label="UMI count")
    ax.set_title(f"{gene_name}  |  {sample_id}", fontsize=13)
    ax.set_xlabel("array_col (8µm)")
    ax.set_ylabel("array_row (8µm)")
    plt.tight_layout()

    out_path = ""
    fig_md = ""
    if save:
        out_dir = _results_dir(sample_id, "spatial_eda")
        out_path = str(out_dir / f"spatial_{gene_name}.png")
        fig.savefig(out_path, dpi=150)
        fig_md = _fig_to_b64_md(fig, alt=f"{gene_name} spatial map")
        plt.close(fig)

        n_expr = int((df["expr"] > 0).sum())
        summary = f"{gene_name} 空間圖：{n_expr:,} bins 有表達，vmax={vmax:.1f}"
        with duckdb.connect(str(db_path)) as write_con:
            analysis_id = _record_analysis(
                write_con, sample_id, "spatial_gene_map",
                {"gene": gene_name, "vmax_pct": vmax_pct},
                out_path, summary, requested_by=requested_by,
            )
            _backfill(write_con, analysis_id)
            _register(
                write_con, analysis_id, out_path, "figure",
                f"{gene_name} 空間表達圖 — {n_expr:,} bins 有表達",
                "spatial_gene_map", producing_fn=gene_spatial_map,
            )
    else:
        plt.close(fig)

    return out_path, fig_md


def qc_stats(
    sample_id: str,
    *,
    save: bool = True,
    db_path: Optional[Path] = None,
    requested_by: str = "spatial_eda",
) -> pd.DataFrame:
    """計算每 bin 的 QC 指標：n_genes（偵測基因數）、total_counts（總 UMI）。

    Returns:
        DataFrame with columns: barcode, array_row_8um, array_col_8um,
                                 n_genes, total_counts
    """
    validate_sample_id(sample_id)
    db_path = db_path or DUCKDB_PATH
    expr_glob = _l2_expr_glob(sample_id)
    obs_path = _l2_obs_path(sample_id)

    with duckdb.connect(str(db_path), read_only=True) as con:
        df = con.execute(
            f"""
            SELECT o.barcode,
                   o.array_row_8um,
                   o.array_col_8um,
                   COUNT(e.gene_name)    AS n_genes,
                   COALESCE(SUM(e.count), 0) AS total_counts
            FROM   read_parquet('{obs_path}') AS o
            LEFT JOIN read_parquet('{expr_glob}') AS e USING (barcode)
            GROUP BY o.barcode, o.array_row_8um, o.array_col_8um
            ORDER BY total_counts DESC
            """
        ).fetchdf()

    if save:
        out_dir = _results_dir(sample_id, "spatial_eda")
        parquet_path = str(out_dir / "qc_stats.parquet")
        df.to_parquet(parquet_path, index=False)

        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        axes[0].hist(df["n_genes"], bins=100, color="steelblue", edgecolor="none")
        axes[0].set_xlabel("Genes per bin")
        axes[0].set_ylabel("Bin count")
        axes[0].set_title("n_genes distribution")

        axes[1].hist(df["total_counts"], bins=100, color="coral", edgecolor="none")
        axes[1].set_xlabel("Total UMI per bin")
        axes[1].set_ylabel("Bin count")
        axes[1].set_title("total_counts distribution")
        plt.tight_layout()

        fig_path = str(out_dir / "qc_distributions.png")
        fig.savefig(fig_path, dpi=150)
        df.attrs["fig_md"] = _fig_to_b64_md(fig, alt="QC distributions")
        plt.close(fig)

        median_genes = float(df["n_genes"].median())
        median_umi = float(df["total_counts"].median())
        summary = f"QC：中位 genes/bin={median_genes:.0f}，中位 UMI/bin={median_umi:.0f}，共 {len(df):,} bins"

        with duckdb.connect(str(db_path)) as write_con:
            analysis_id = _record_analysis(
                write_con, sample_id, "qc_stats", {},
                parquet_path, summary, requested_by=requested_by,
            )
            _backfill(write_con, analysis_id)
            _register(
                write_con, analysis_id, parquet_path, "csv",
                f"QC stats parquet — {len(df):,} bins",
                "qc_stats", producing_fn=qc_stats,
            )
            _register(
                write_con, analysis_id, fig_path, "figure",
                f"QC 分布圖（n_genes + total_counts）— 中位 genes={median_genes:.0f}，UMI={median_umi:.0f}",
                "qc_distributions", producing_fn=qc_stats,
            )

    return df


def top_genes(
    sample_id: str,
    n: int = 50,
    *,
    save: bool = False,
    db_path: Optional[Path] = None,
    requested_by: str = "spatial_eda",
) -> pd.DataFrame:
    """依總 UMI 量排序返回前 N 個高表達基因。

    Returns:
        DataFrame with columns: gene_name, total_counts, n_bins
    """
    validate_sample_id(sample_id)
    db_path = db_path or DUCKDB_PATH
    expr_glob = _l2_expr_glob(sample_id)

    with duckdb.connect(str(db_path), read_only=True) as con:
        df = con.execute(
            f"""
            SELECT gene_name,
                   SUM(count)   AS total_counts,
                   COUNT(*)     AS n_bins
            FROM   read_parquet('{expr_glob}')
            GROUP BY gene_name
            ORDER BY total_counts DESC
            LIMIT ?
            """,
            [n],
        ).fetchdf()

    if save:
        out_dir = _results_dir(sample_id, "spatial_eda")
        csv_path = str(out_dir / f"top_{n}_genes.csv")
        df.to_csv(csv_path, index=False)
        top3 = ", ".join(df["gene_name"].head(3).tolist())
        with duckdb.connect(str(db_path)) as write_con:
            _record_analysis(
                write_con, sample_id, "top_genes", {"n": n},
                csv_path, f"Top {n} genes：{top3}…",
                requested_by=requested_by,
            )

    return df


def gene_coexpression(
    sample_id: str,
    gene_a: str,
    gene_b: str,
    *,
    figsize: tuple[int, int] = (18, 6),
    save: bool = True,
    db_path: Optional[Path] = None,
    requested_by: str = "spatial_eda",
) -> tuple[str, str]:
    """兩基因空間疊圖（3 panels）：gene_a 空間圖、gene_b 空間圖、共表達散點。

    Returns:
        (out_path, fig_md) — 儲存路徑與 inline base64 Markdown 字串
        （save=False 時兩者均為空字串）
    """
    validate_sample_id(sample_id)
    _validate_gene_name(gene_a)
    _validate_gene_name(gene_b)
    db_path = db_path or DUCKDB_PATH
    expr_glob = _l2_expr_glob(sample_id)
    obs_path = _l2_obs_path(sample_id)

    with duckdb.connect(str(db_path), read_only=True) as con:
        df = con.execute(
            f"""
            SELECT o.array_row_8um AS row,
                   o.array_col_8um AS col,
                   MAX(CASE WHEN e.gene_name = ? THEN e.count ELSE 0 END) AS gene_a,
                   MAX(CASE WHEN e.gene_name = ? THEN e.count ELSE 0 END) AS gene_b
            FROM   read_parquet('{obs_path}') AS o
            LEFT JOIN (
                SELECT barcode, gene_name, count
                FROM   read_parquet('{expr_glob}')
                WHERE  gene_name IN (?, ?)
            ) AS e USING (barcode)
            GROUP BY o.array_row_8um, o.array_col_8um
            """,
            [gene_a, gene_b, gene_a, gene_b],
        ).fetchdf()

    if df.empty:
        raise ValueError(f"No spatial data found for sample '{sample_id}'")
    if df["gene_a"].max() == 0 and df["gene_b"].max() == 0:
        raise ValueError(
            f"基因 {gene_a!r} 與 {gene_b!r} 在樣本 {sample_id!r} 中均未偵測到"
        )

    row_min, row_max = int(df["row"].min()), int(df["row"].max())
    col_min, col_max = int(df["col"].min()), int(df["col"].max())
    extent = [col_min, col_max, row_max, row_min]

    pivot_a = df.pivot_table(index="row", columns="col", values="gene_a", fill_value=0)
    pivot_b = df.pivot_table(index="row", columns="col", values="gene_b", fill_value=0)
    vmax_a = max(float(df["gene_a"].quantile(0.99)), 1)
    vmax_b = max(float(df["gene_b"].quantile(0.99)), 1)

    fig, axes = plt.subplots(1, 3, figsize=figsize)

    # Panel 1: gene_a spatial
    im_a = axes[0].imshow(
        pivot_a.values, cmap="Blues", vmin=0, vmax=vmax_a,
        aspect="auto", extent=extent,
    )
    plt.colorbar(im_a, ax=axes[0], label="UMI")
    axes[0].set_title(f"{gene_a}", fontsize=12)
    axes[0].set_xlabel("array_col (8µm)")
    axes[0].set_ylabel("array_row (8µm)")

    # Panel 2: gene_b spatial
    im_b = axes[1].imshow(
        pivot_b.values, cmap="Reds", vmin=0, vmax=vmax_b,
        aspect="auto", extent=extent,
    )
    plt.colorbar(im_b, ax=axes[1], label="UMI")
    axes[1].set_title(f"{gene_b}", fontsize=12)
    axes[1].set_xlabel("array_col (8µm)")
    axes[1].set_ylabel("")

    # Panel 3: scatter co-expression
    axes[2].scatter(df["gene_a"], df["gene_b"], alpha=0.3, s=3, linewidths=0)
    axes[2].set_xlabel(f"{gene_a} (UMI)")
    axes[2].set_ylabel(f"{gene_b} (UMI)")
    axes[2].set_title(f"Co-expression: {gene_a} × {gene_b}", fontsize=12)

    fig.suptitle(f"{sample_id}", fontsize=13)
    plt.tight_layout()

    out_path = ""
    fig_md = ""
    if save:
        out_dir = _results_dir(sample_id, "spatial_eda")
        out_path = str(out_dir / f"coexpr_{gene_a}_{gene_b}.png")
        fig.savefig(out_path, dpi=150)
        fig_md = _fig_to_b64_md(fig, alt=f"co-expression {gene_a} × {gene_b}")
        plt.close(fig)

        n_both = int(((df["gene_a"] > 0) & (df["gene_b"] > 0)).sum())
        summary = f"{gene_a}×{gene_b} 共表達：{n_both:,} bins 同時有表達"
        with duckdb.connect(str(db_path)) as write_con:
            analysis_id = _record_analysis(
                write_con, sample_id, "gene_coexpression",
                {"gene_a": gene_a, "gene_b": gene_b},
                out_path, summary, requested_by=requested_by,
            )
            _backfill(write_con, analysis_id)
            _register(
                write_con, analysis_id, out_path, "figure",
                f"{gene_a}×{gene_b} 空間共表達圖 — {n_both:,} bins 同時有表達",
                "gene_coexpression", producing_fn=gene_coexpression,
            )
    else:
        plt.close(fig)

    return out_path, fig_md


if __name__ == "__main__":
    import sys

    sample = "crc_official_v4"
    print("[spatial_eda] QC stats...")
    qc = qc_stats(sample)
    print(f"  bins: {len(qc):,}  median_genes: {qc['n_genes'].median():.0f}")

    print("[spatial_eda] Top 10 genes:")
    print(top_genes(sample, n=10).to_string(index=False))

    gene = sys.argv[1] if len(sys.argv) > 1 else "PTPRC"
    print(f"[spatial_eda] Gene spatial map: {gene}")
    out_path, _ = gene_spatial_map(sample, gene)
    print(f"  saved: {out_path}")
