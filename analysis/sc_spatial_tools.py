"""Generic post-mcseg single-cell spatial analysis tools.

Operates on any AnnData h5ad with cell-level annotations (typically the output of
bio_run_mcseg_fullslide/roi's RNA counting step, or any externally-imported
single-cell result registered via bio_register_external_analysis_result).

Extracted as *generic, parameterized* tools from the 康育 VisiumHD wound-mechanism
bundle (2026-07-21) — see sb project note "康育 VisiumHD 傷口機轉分析方法建置進 EP
工具箱 — 規劃". Deliberately does NOT port dataset-specific pieces from that bundle
(manual cluster→cell-type label tables, hardcoded gene panels, hardcoded paths) —
those stay as free-text parameters supplied by the caller.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402

logger = logging.getLogger("evo_prism.sc_spatial_tools")


def _load_and_filter(h5ad_path: Path, cell_filter: dict | None):
    """Load an h5ad and optionally subset by an obs column's values."""
    import scanpy as sc

    adata = sc.read_h5ad(str(h5ad_path))
    if cell_filter:
        col = cell_filter["obs_column"]
        values = set(cell_filter["values"])
        if col not in adata.obs.columns:
            raise ValueError(f"cell_filter.obs_column '{col}' 不在 adata.obs 裡（可用欄位：{list(adata.obs.columns)}）")
        mask = adata.obs[col].astype(str).isin(values)
        if not mask.any():
            raise ValueError(f"cell_filter {cell_filter} 篩不到任何細胞")
        adata = adata[mask].copy()
    return adata


@register_tool_on_import(
    tool_name="run_geneset_score",
    version="1.0.0",
    description=(
        "任意基因模組評分（scanpy score_genes 封裝）。輸入 h5ad + 呼叫端提供的"
        "{模組名: [基因清單]} 字典,可選先用 cell_filter 篩子集(例如只評分某種細胞型別)。"
        "2026-07-21 從康育 VisiumHD 傷口機轉 bundle 的巨噬 M1/M2/LAM 極化評分抽出的"
        "泛用版本——基因模組完全由呼叫端提供,不寫死任何特定生物情境。"
    ),
)
def run_geneset_score(
    sample_id: str,
    h5ad_path: str | Path,
    modules: dict[str, list[str]],
    out_dir: str | Path,
    cell_filter: dict | None = None,
    group_by: str | None = None,
    ctrl_size: int = 50,
    counts_layer: str = "counts",
    requested_by: str = "agent",
) -> dict:
    """
    Score cells against arbitrary gene modules using scanpy's score_genes.

    Args:
        h5ad_path: input AnnData, must be under BIO_DB_ROOT.
        modules: {module_name: [gene, ...]}. Genes not present in var_names are
            silently dropped per-module (recorded in the returned dict); a module
            with zero present genes is skipped with a warning, not a hard failure.
        cell_filter: optional {"obs_column": ..., "values": [...]} to subset first
            (e.g. score module only within a specific cell type).
        group_by: optional obs column to include in the output table (e.g. "day",
            "sample") for downstream time-course plotting — not used for scoring
            itself, just carried through to the CSV.
        counts_layer: adata.layers key holding raw counts, re-normalized fresh
            after any cell_filter subsetting (more correct than reusing pre-subset
            normalization). If this layer isn't present, falls back to using .X
            as-is (logged as a warning) — set to "" to skip the lookup entirely
            and always use .X.
        out_dir: must be under BIO_DB_ROOT.

    Returns:
        {"analysis_id", "artifact_ids", "out_csv", "genes_used": {module: [...]},
         "n_cells_scored"}
    """
    import json
    import uuid
    from datetime import datetime

    import scanpy as sc

    from store.factory import get_store

    validate_sample_id(sample_id)
    h5ad_path = Path(h5ad_path)
    out_dir = Path(out_dir)

    store = get_store()
    if not store.get_sample(sample_id):
        raise ValueError(f"sample_id '{sample_id}' 不存在於 sample_registry，請先 bio_register_sample。")
    if not h5ad_path.exists():
        raise FileNotFoundError(f"h5ad_path 不存在：{h5ad_path}")
    for label, p in [("h5ad_path", h5ad_path), ("out_dir", out_dir)]:
        try:
            p.relative_to(BIO_DB_ROOT)
        except ValueError:
            raise ValueError(f"{label} 必須在 BIO_DB_ROOT（{BIO_DB_ROOT}）底下；收到 {p}")
    if not modules:
        raise ValueError("modules 不能是空字典")

    adata = _load_and_filter(h5ad_path, cell_filter)
    n_cells = adata.n_obs
    logger.info(f"run_geneset_score: {n_cells} cells after filter, {len(modules)} modules")

    # Prefer raw counts + fresh normalization if available (matches convention:
    # re-normalizing after subsetting is more correct than reusing pre-subset norm).
    if counts_layer and counts_layer in adata.layers:
        adata.X = adata.layers[counts_layer].copy()
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
    else:
        logger.warning(
            f"run_geneset_score: adata.layers[{counts_layer!r}] 不存在，直接用 .X 現有值評分"
            "（假設已經是合適的正規化狀態）"
        )

    genes_used: dict[str, list[str]] = {}
    for name, genes in modules.items():
        present = [g for g in genes if g in adata.var_names]
        genes_used[name] = present
        if not present:
            logger.warning(f"run_geneset_score: 模組 '{name}' 沒有任何基因出現在 var_names，跳過")
            continue
        sc.tl.score_genes(adata, present, score_name=f"{name}_score", ctrl_size=ctrl_size)

    score_cols = [f"{name}_score" for name in modules if genes_used.get(name)]
    if not score_cols:
        raise ValueError("所有模組都沒有基因出現在 var_names，沒有任何分數可算")

    cols = list(score_cols)
    if group_by:
        if group_by not in adata.obs.columns:
            raise ValueError(f"group_by '{group_by}' 不在 adata.obs 裡")
        cols = [group_by] + cols

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "geneset_scores.csv"
    adata.obs[cols].to_csv(out_csv)

    summary = (
        f"Gene-set module 評分：{n_cells:,} 細胞 × {len(score_cols)} 個模組"
        f"（{', '.join(score_cols)}）。"
    )
    params = {
        "h5ad_path": str(h5ad_path),
        "cell_filter": cell_filter,
        "group_by": group_by,
        "ctrl_size": ctrl_size,
        "counts_layer": counts_layer,
        "genes_used": genes_used,
    }

    # 一次性記錄 → history 走 analysis_run seam；artifact 另走專用 DuckDB con 收集 aid 回傳。
    from analysis.run_context import record_completed_run

    analysis_id = record_completed_run(
        sample_id, "geneset_score",
        params=params, result_path=str(out_csv), summary=summary,
        requested_by=requested_by, tool_name="run_geneset_score",
    )

    from analysis.artifact_registry import register_artifact
    from config.db_utils import connect_db
    from config.settings import DUCKDB_PATH

    _acon = connect_db(DUCKDB_PATH)
    try:
        aid = register_artifact(
            _acon, analysis_id, out_csv, "table", "Gene-set module scores",
            artifact_subtype="geneset_score",
        )
    finally:
        _acon.close()

    logger.info(f"run_geneset_score: analysis_id={analysis_id} {summary}")
    return {
        "analysis_id": analysis_id,
        "artifact_ids": [aid],
        "out_csv": str(out_csv),
        "genes_used": genes_used,
        "n_cells_scored": n_cells,
    }


@register_tool_on_import(
    tool_name="compute_spatial_nn_distance",
    version="1.0.0",
    description=(
        "任意兩群細胞的空間最近鄰距離(cKDTree)。輸入 h5ad + 來源/目標細胞篩選條件,"
        "可選依 group_by 分組獨立計算(例如按 sample 分組,避免跨 capture area 算出"
        "沒有意義的距離)。2026-07-21 從康育 bundle 的 SAA3+成纖維↔巨噬距離分析抽出"
        "的泛用版本。\n"
        "⚠️ 注意 obsm['spatial'] 的座標單位因資料而異(mcseg 產出的 h5ad 通常是像素,"
        "不是微米)——實測康育資料集是像素(centroid_x_px 尺度),要乘 0.2737 才是 µm。"
        "確定換算比例的話帶 distance_scale/distance_unit 讓輸出直接是有意義的單位,"
        "不確定就留預設(輸出跟 obsm['spatial'] 同單位,自己換算)。"
    ),
)
def compute_spatial_nn_distance(
    sample_id: str,
    h5ad_path: str | Path,
    source_filter: dict,
    target_filter: dict,
    out_dir: str | Path,
    group_by: str | None = None,
    spatial_key: str = "spatial",
    distance_scale: float = 1.0,
    distance_unit: str = "",
    requested_by: str = "agent",
) -> dict:
    """
    For each source cell, compute distance to the nearest target cell.

    Args:
        source_filter / target_filter: {"obs_column": ..., "values": [...]}.
        group_by: obs column (e.g. "sample") to compute distances independently
            within each group. Strongly recommended whenever the h5ad spans
            multiple capture areas/sections — cross-group spatial coordinates are
            usually not comparable.
        spatial_key: adata.obsm key holding 2D coordinates (default "spatial").
        distance_scale: multiply raw distances by this factor before saving —
            obsm['spatial'] is often in pixels, not a physical unit. E.g. for the
            康育 bundle's mcseg output, pixel→µm ratio is 0.2737 (derived from
            centroid_x_um / centroid_x_px in adata.obs, if present).
        distance_unit: free-text label for what distance_scale converts to (e.g.
            "µm"), only used for the summary string — purely cosmetic.

    Returns:
        {"analysis_id", "artifact_ids", "out_csv", "n_source_cells", "n_target_cells"}
    """
    import json
    import uuid
    from datetime import datetime

    import numpy as np
    import scanpy as sc
    from scipy.spatial import cKDTree

    from store.factory import get_store

    validate_sample_id(sample_id)
    h5ad_path = Path(h5ad_path)
    out_dir = Path(out_dir)

    store = get_store()
    if not store.get_sample(sample_id):
        raise ValueError(f"sample_id '{sample_id}' 不存在於 sample_registry，請先 bio_register_sample。")
    if not h5ad_path.exists():
        raise FileNotFoundError(f"h5ad_path 不存在：{h5ad_path}")
    for label, p in [("h5ad_path", h5ad_path), ("out_dir", out_dir)]:
        try:
            p.relative_to(BIO_DB_ROOT)
        except ValueError:
            raise ValueError(f"{label} 必須在 BIO_DB_ROOT（{BIO_DB_ROOT}）底下；收到 {p}")

    adata = sc.read_h5ad(str(h5ad_path))
    if spatial_key not in adata.obsm:
        raise ValueError(f"adata.obsm 沒有 '{spatial_key}'（可用：{list(adata.obsm.keys())}）")

    def _mask(f: dict):
        col = f["obs_column"]
        if col not in adata.obs.columns:
            raise ValueError(f"obs_column '{col}' 不在 adata.obs 裡")
        return adata.obs[col].astype(str).isin(set(f["values"])).values

    src_mask = _mask(source_filter)
    tgt_mask = _mask(target_filter)
    if not src_mask.any():
        raise ValueError(f"source_filter {source_filter} 篩不到任何細胞")
    if not tgt_mask.any():
        raise ValueError(f"target_filter {target_filter} 篩不到任何細胞")

    coords = adata.obsm[spatial_key]
    groups = adata.obs[group_by].astype(str).values if group_by else np.full(adata.n_obs, "all")

    distances = np.full(adata.n_obs, np.nan)
    for g in np.unique(groups[src_mask]):
        g_tgt_mask = tgt_mask & (groups == g)
        g_src_mask = src_mask & (groups == g)
        if not g_tgt_mask.any():
            logger.warning(f"compute_spatial_nn_distance: group '{g}' 沒有目標細胞，該組來源細胞距離留 NaN")
            continue
        tree = cKDTree(coords[g_tgt_mask])
        d, _ = tree.query(coords[g_src_mask], k=1)
        distances[g_src_mask] = d

    out_df = adata.obs.loc[src_mask, [c for c in [group_by] if c]].copy()
    out_df["nn_distance"] = distances[src_mask] * distance_scale

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "spatial_nn_distance.csv"
    out_df.to_csv(out_csv)

    n_source = int(src_mask.sum())
    n_target = int(tgt_mask.sum())
    unit_label = distance_unit if distance_unit else f"座標單位同 obsm['{spatial_key}']（scale={distance_scale}）"
    summary = (
        f"空間最近鄰距離：{n_source:,} 個來源細胞 → 最近的目標細胞（{n_target:,} 個）"
        f"{'，依 ' + group_by + ' 分組計算' if group_by else ''}。"
        f"中位距離 {out_df['nn_distance'].median():.2f} {unit_label}。"
    )
    params = {
        "h5ad_path": str(h5ad_path), "source_filter": source_filter,
        "target_filter": target_filter, "group_by": group_by, "spatial_key": spatial_key,
        "distance_scale": distance_scale, "distance_unit": distance_unit,
    }

    # 一次性記錄 → history 走 analysis_run seam；artifact 另走專用 DuckDB con 收集 aid 回傳。
    from analysis.run_context import record_completed_run

    analysis_id = record_completed_run(
        sample_id, "spatial_nn_distance",
        params=params, result_path=str(out_csv), summary=summary,
        requested_by=requested_by, tool_name="compute_spatial_nn_distance",
    )

    from analysis.artifact_registry import register_artifact
    from config.db_utils import connect_db
    from config.settings import DUCKDB_PATH

    _acon = connect_db(DUCKDB_PATH)
    try:
        aid = register_artifact(
            _acon, analysis_id, out_csv, "table", "Spatial nearest-neighbor distances",
            artifact_subtype="spatial_nn_distance",
        )
    finally:
        _acon.close()

    logger.info(f"compute_spatial_nn_distance: analysis_id={analysis_id} {summary}")
    return {
        "analysis_id": analysis_id,
        "artifact_ids": [aid],
        "out_csv": str(out_csv),
        "n_source_cells": n_source,
        "n_target_cells": n_target,
    }
