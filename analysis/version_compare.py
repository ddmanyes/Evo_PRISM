"""跨版本分析結果比對（bio_compare_versions）。

回答：「工具從 vA 升到 vB 後，哪些樣本的結果真的改變了？需要重跑嗎？」

四層工作流：
  Layer 0 — 程式碼/參數脈絡（source_hash 比對、change_reason）
  Layer 1 — 找可比較分析對（含 QUALIFY 去重，防笛卡兒積）
  Layer 2 — 依產物類型比較（DEG Jaccard/Spearman、enrichment Jaccard、EDA QC/Procrustes）
  Layer 3 — Agent 深探 hint（high_delta_samples、suggested_tools、investigation_context）

與 impact.py 的分工：
  impact.py  = 「哪些分析用了舊版工具？」（影響面）
  version_compare.py = 「結果真的不同嗎？」（差異量化）
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

# ── 固定閾值（verdict 決策用，集中成常數方便日後調整）─────────────────────────
JACCARD_NO_DIFF = 0.90        # DEG Jaccard ≥ 此值 → 無顯著差異
JACCARD_PARTIAL = 0.70        # DEG Jaccard ≥ 此值 → 偏移但結論大致一致
PATHWAY_JACCARD_NO_DIFF = 0.80
QC_REL_CHANGE_THRESHOLD = 0.05  # QC 相對變化 < 5% 視為穩定
PCA_PROCRUSTES_NO_DIFF = 0.05
SPEARMAN_MIN_GENES = 10       # 交集基因 < 此值時 Spearman 回 None

# ── 產物解析契約（analysis_type → parser config）──────────────────────────────
PARSER_REGISTRY: dict[str, dict[str, Any]] = {
    "diff_expr": {
        "artifact_label_pattern": "deg",     # artifact label 模糊比對
        "gene_col": None,                     # gene 在 index
        "fc_col": "log2FC",
        "pval_col": "qvalue",
    },
    "bulk_deg": {                             # bio_memory 的 analysis_type
        "artifact_label_pattern": "deg",
        "gene_col": None,
        "fc_col": "log2FC",
        "pval_col": "qvalue",
    },
    "enrichment": {
        "artifact_label_pattern": "enrich",
        "term_col": "Term",
        "pval_col": "Adjusted P-value",
    },
    "bulk_enrichment": {
        "artifact_label_pattern": "enrich",
        "term_col": "Term",
        "pval_col": "Adjusted P-value",
    },
    "eda": {
        "metrics_only": True,
        "needs_counts": True,
    },
    "bulk_eda": {
        "metrics_only": True,
        "needs_counts": True,
    },
}

# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class CodeContext:
    tool_name: str
    version_a: str
    version_b: str
    content_hash_a: str | None
    content_hash_b: str | None
    source_hash_changed: bool
    change_reason: str | None   # tool_change_log.reason；可能 None


@dataclass
class ParamSummary:
    exact_match_count: int
    param_diff_count: int
    param_diff_detail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MetricsDelta:
    val_a: float | None
    val_b: float | None
    delta: float | None
    rel_change: float | None


@dataclass
class PairResult:
    sample_id: str
    analysis_type: str
    aid_a: str
    aid_b: str
    params_identical: bool

    # 生物意義層
    gene_list_jaccard: float | None = None
    fold_change_spearman: float | None = None
    pathway_jaccard: float | None = None
    qc_delta: dict[str, MetricsDelta] = field(default_factory=dict)
    pca_procrustes_dist: float | None = None

    # 數值層（summary_metrics diff）
    metrics_diff: dict[str, MetricsDelta] = field(default_factory=dict)

    verdict: str = "資料不足"
    verdict_reason: str = ""


@dataclass
class ExplorationHints:
    high_delta_samples: list[dict[str, Any]] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    investigation_context: str = ""
    cascade_impact: list[str] = field(default_factory=list)


@dataclass
class ComparisonReport:
    # Layer 0
    code_context: CodeContext
    param_summary: ParamSummary

    # Layer 1
    matched_pairs: int
    unmatched_samples: list[str] = field(default_factory=list)

    # Layer 2
    per_pair_results: list[PairResult] = field(default_factory=list)

    # Layer 3
    exploration_hints: ExplorationHints = field(default_factory=ExplorationHints)

    # 落地
    report_artifact_id: str | None = None


# ── Layer 0 ───────────────────────────────────────────────────────────────────

def _build_code_context(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    version_a: str,
    version_b: str,
) -> CodeContext:
    rows = con.execute(
        """
        SELECT version, content_hash, created_at
        FROM tools
        WHERE tool_name = ?
          AND version IN (?, ?)
        ORDER BY created_at
        """,
        [tool_name, version_a, version_b],
    ).fetchall()

    ver_map: dict[str, dict[str, Any]] = {}
    for ver, ch, ca in rows:
        ver_map[ver] = {"content_hash": ch, "created_at": ca}

    hash_a = ver_map.get(version_a, {}).get("content_hash")
    hash_b = ver_map.get(version_b, {}).get("content_hash")
    source_hash_changed = (hash_a != hash_b) if (hash_a and hash_b) else True

    # change_reason：找兩版 created_at 之間的 tool_change_log.reason
    change_reason: str | None = None
    if version_a in ver_map and version_b in ver_map:
        ca_a = ver_map[version_a]["created_at"]
        ca_b = ver_map[version_b]["created_at"]
        ts_min, ts_max = (ca_a, ca_b) if ca_a <= ca_b else (ca_b, ca_a)
        try:
            r = con.execute(
                """
                SELECT reason FROM tool_change_log
                WHERE tool_name = ?
                  AND changed_at > ?
                  AND changed_at <= ?
                  AND reason IS NOT NULL
                  AND reason != ''
                ORDER BY changed_at DESC
                LIMIT 1
                """,
                [tool_name, ts_min, ts_max],
            ).fetchone()
            if r:
                change_reason = r[0]
        except Exception:
            pass

    return CodeContext(
        tool_name=tool_name,
        version_a=version_a,
        version_b=version_b,
        content_hash_a=hash_a,
        content_hash_b=hash_b,
        source_hash_changed=source_hash_changed,
        change_reason=change_reason,
    )


# ── Layer 1 ───────────────────────────────────────────────────────────────────

def _fetch_pairs(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    version_a: str,
    version_b: str,
    sample_id: str | None,
    analysis_type: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """找可比較的分析對（含 QUALIFY 去重）。

    Returns:
        (pairs, unmatched_samples)
        pairs: list of dicts with keys: sample_id, analysis_type, aid_a, aid_b,
               path_a, path_b, params_a, params_b, metrics_a, metrics_b, params_identical
        unmatched_samples: 只在一個版本有記錄的樣本
    """
    params: list[Any] = [tool_name, version_a, version_b]
    extra_conditions = ""
    if sample_id:
        extra_conditions += " AND ah.sample_id = ?"
        params.append(sample_id)
    if analysis_type:
        extra_conditions += " AND ah.analysis_type = ?"
        params.append(analysis_type)

    # 含 QUALIFY 去重：每個 (sample, type, version) 只留 lineage head / 最新完成
    sql = f"""
    WITH ranked AS (
        SELECT
            ah.analysis_id,
            ah.sample_id,
            ah.analysis_type,
            ah.result_path,
            ah.parameters,
            ah.parameter_hash,
            ah.summary_metrics,
            ah.parent_analysis_id,
            ah.completed_at,
            t.version AS tool_version
        FROM analysis_history ah
        JOIN tools t ON ah.tool_id = t.tool_id
        WHERE t.tool_name = ?
          AND t.version IN (?, ?)
          AND ah.status = 'completed'
          {extra_conditions}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ah.sample_id, ah.analysis_type, t.version
            ORDER BY
                CASE WHEN ah.parent_analysis_id IS NULL THEN 0 ELSE 1 END ASC,
                ah.completed_at DESC
        ) = 1
    )
    SELECT
        a.sample_id,
        a.analysis_type,
        a.analysis_id  AS aid_a,
        b.analysis_id  AS aid_b,
        a.result_path  AS path_a,
        b.result_path  AS path_b,
        a.parameters   AS params_a,
        b.parameters   AS params_b,
        a.summary_metrics AS metrics_a,
        b.summary_metrics AS metrics_b,
        (a.parameter_hash IS NOT DISTINCT FROM b.parameter_hash
         AND a.parameter_hash IS NOT NULL) AS params_identical
    FROM ranked a
    JOIN ranked b
      ON  b.sample_id     = a.sample_id
      AND b.analysis_type = a.analysis_type
    WHERE a.tool_version = ?
      AND b.tool_version = ?
    """
    params += [version_a, version_b]

    try:
        rows = con.execute(sql, params).fetchall()
    except Exception as exc:
        logger.error("Layer 1 SQL 失敗：%s", exc)
        return [], []

    cols = ["sample_id", "analysis_type", "aid_a", "aid_b",
            "path_a", "path_b", "params_a", "params_b",
            "metrics_a", "metrics_b", "params_identical"]
    pairs = [dict(zip(cols, r)) for r in rows]

    # 找 unmatched（只在一個版本有記錄的樣本）
    matched_sids = {p["sample_id"] for p in pairs}
    try:
        all_rows = con.execute(
            f"""
            WITH ranked AS (
                SELECT ah.sample_id, t.version AS tool_version
                FROM analysis_history ah
                JOIN tools t ON ah.tool_id = t.tool_id
                WHERE t.tool_name = ?
                  AND t.version IN (?, ?)
                  AND ah.status = 'completed'
                  {extra_conditions}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY ah.sample_id, ah.analysis_type, t.version
                    ORDER BY ah.completed_at DESC
                ) = 1
            )
            SELECT DISTINCT sample_id FROM ranked
            """,
            params[:len(params) - 2],  # remove version_a, version_b at end
        ).fetchall()
        all_sids = {r[0] for r in all_rows}
        unmatched = sorted(all_sids - matched_sids)
    except Exception:
        unmatched = []

    return pairs, unmatched


# ── Layer 2 helpers ───────────────────────────────────────────────────────────

def _find_artifact_path(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
    label_pattern: str,
    fallback_result_path: str | None,
) -> Path | None:
    """從 analysis_artifacts 找符合 label_pattern 的 CSV；找不到退回 result_path。"""
    try:
        rows = con.execute(
            """
            SELECT file_path, label, artifact_type, artifact_subtype
            FROM analysis_artifacts
            WHERE analysis_id = ?
              AND artifact_type IN ('table', 'csv', 'data')
            ORDER BY created_at DESC
            """,
            [analysis_id],
        ).fetchall()
        for fp, lbl, _at, _as in rows:
            lbl_str = (lbl or "").lower()
            if label_pattern.lower() in lbl_str:
                p = Path(fp)
                if p.exists():
                    return p
    except Exception:
        pass

    if fallback_result_path:
        p = Path(fallback_result_path)
        if p.suffix in (".csv", ".tsv") and p.exists():
            return p
    return None


def _load_deg_df(path: Path) -> Any | None:
    """讀 DEG CSV，回傳 DataFrame 或 None（降級）。"""
    try:
        import pandas as pd
        df = pd.read_csv(path, index_col=0)
        if "log2FC" not in df.columns or "qvalue" not in df.columns:
            logger.warning("DEG CSV 缺必要欄位（log2FC/qvalue）：%s", path)
            return None
        return df
    except Exception as exc:
        logger.warning("無法讀取 DEG CSV %s：%s", path, exc)
        return None


def _deg_sig_set(df: Any) -> set[str]:
    mask = (df["qvalue"] < 0.05) & (df["log2FC"].abs() > 1.0)
    return set(df.index[mask].astype(str))


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _compute_deg_metrics(path_a: Path, path_b: Path) -> tuple[float | None, float | None]:
    """回傳 (gene_list_jaccard, fold_change_spearman)。"""
    try:
        from scipy.stats import spearmanr
    except ImportError:
        spearmanr = None  # type: ignore[assignment]

    df_a = _load_deg_df(path_a)
    df_b = _load_deg_df(path_b)
    if df_a is None or df_b is None:
        return None, None

    sig_a = _deg_sig_set(df_a)
    sig_b = _deg_sig_set(df_b)
    jaccard = _jaccard(sig_a, sig_b)

    spearman: float | None = None
    if spearmanr is not None:
        common = sorted(set(df_a.index) & set(df_b.index))
        if len(common) >= SPEARMAN_MIN_GENES:
            fc_a = df_a.loc[common, "log2FC"].values
            fc_b = df_b.loc[common, "log2FC"].values
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    rho, _ = spearmanr(fc_a, fc_b)
                    spearman = float(rho)
                except Exception:
                    pass

    return jaccard, spearman


def _compute_enrichment_jaccard(path_a: Path, path_b: Path) -> float | None:
    try:
        import pandas as pd
        df_a = pd.read_csv(path_a)
        df_b = pd.read_csv(path_b)
        if "Term" not in df_a.columns or "Adjusted P-value" not in df_a.columns:
            return None
        sig_a = set(df_a.loc[df_a["Adjusted P-value"] < 0.05, "Term"].astype(str))
        sig_b = set(df_b.loc[df_b["Adjusted P-value"] < 0.05, "Term"].astype(str))
        return _jaccard(sig_a, sig_b)
    except Exception as exc:
        logger.warning("enrichment Jaccard 失敗：%s", exc)
        return None


def _parse_metrics(raw: Any) -> dict[str, float]:
    """從 summary_metrics（JSON str 或 dict）取出數值欄位。"""
    if raw is None:
        return {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for k, v in raw.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            result[k] = float(v)
    return result


def _compute_metrics_diff(metrics_a: dict[str, float], metrics_b: dict[str, float]) -> dict[str, MetricsDelta]:
    all_keys = set(metrics_a) | set(metrics_b)
    result: dict[str, MetricsDelta] = {}
    for k in all_keys:
        va = metrics_a.get(k)
        vb = metrics_b.get(k)
        delta = (vb - va) if (va is not None and vb is not None) else None
        rel = None
        if delta is not None and va is not None:
            rel = (delta / va) if va != 0 else None
        result[k] = MetricsDelta(val_a=va, val_b=vb, delta=delta, rel_change=rel)
    return result


def _qc_stable(qc_delta: dict[str, MetricsDelta]) -> bool:
    """所有 QC 相對變化 < 5%？"""
    for md in qc_delta.values():
        if md.rel_change is not None and abs(md.rel_change) >= QC_REL_CHANGE_THRESHOLD:
            return False
    return True


def _compute_pca_procrustes(
    con: duckdb.DuckDBPyConnection,
    aid_a: str,
    aid_b: str,
) -> float | None:
    """從 counts artifact 重算 PCA，用 Procrustes 比較結構差異。"""
    try:
        from scipy.spatial import procrustes
        from sklearn.decomposition import PCA
        import numpy as np
        import pandas as pd
    except ImportError:
        return None

    def _get_counts_path(analysis_id: str) -> Path | None:
        try:
            rows = con.execute(
                """
                SELECT file_path FROM analysis_artifacts
                WHERE analysis_id = ?
                  AND artifact_type IN ('counts', 'input', 'data')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [analysis_id],
            ).fetchall()
            for (fp,) in rows:
                p = Path(fp)
                if p.exists() and p.suffix in (".tsv", ".csv"):
                    return p
        except Exception:
            pass
        return None

    path_a = _get_counts_path(aid_a)
    path_b = _get_counts_path(aid_b)
    if path_a is None or path_b is None:
        return None

    try:
        sep_a = "\t" if path_a.suffix == ".tsv" else ","
        sep_b = "\t" if path_b.suffix == ".tsv" else ","
        counts_a = pd.read_csv(path_a, sep=sep_a, index_col=0)
        counts_b = pd.read_csv(path_b, sep=sep_b, index_col=0)

        # 取共同樣本
        common_cols = sorted(set(counts_a.columns) & set(counts_b.columns))
        if len(common_cols) < 3:
            return None

        counts_a = counts_a[common_cols]
        counts_b = counts_b[common_cols]

        def _pca_coords(counts: Any) -> Any:
            import numpy as np
            mat = np.log1p(counts.values.astype(float)).T  # samples × genes
            # top 500 high-variance genes
            var = mat.var(axis=0)
            top_idx = np.argsort(var)[-500:]
            mat = mat[:, top_idx]
            pca = PCA(n_components=2)
            return pca.fit_transform(mat)

        coords_a = _pca_coords(counts_a)
        coords_b = _pca_coords(counts_b)

        _, _, disparity = procrustes(coords_a, coords_b)
        return float(disparity)
    except Exception as exc:
        logger.warning("PCA Procrustes 失敗：%s", exc)
        return None


# ── verdict ───────────────────────────────────────────────────────────────────

def _decide_verdict(pr: PairResult, analysis_type: str) -> tuple[str, str]:
    """依固定決策表回傳 (verdict, reason)。先命中者勝。"""
    atype = analysis_type.lower()

    is_deg = any(t in atype for t in ("diff_expr", "bulk_deg", "deg"))
    is_enrich = any(t in atype for t in ("enrich",))
    is_eda = any(t in atype for t in ("eda",))

    # 1. 資料不足（缺檔時無法比較，比 params 差異更該優先回報——避免誤導「已比對」）
    if is_deg and pr.gene_list_jaccard is None:
        return "資料不足", "DEG CSV 缺失或無法解析"
    if is_enrich and pr.pathway_jaccard is None:
        return "資料不足", "enrichment CSV 缺失或無法解析"

    # 2. 參數不同（資料齊備但 params 不符 → 差異不可單獨歸因程式碼）
    if not pr.params_identical:
        return "參數不同（差異不可單獨歸因程式碼）", "parameter_hash 不符"

    # 3. DEG
    if is_deg and pr.gene_list_jaccard is not None:
        j = pr.gene_list_jaccard
        if j >= JACCARD_NO_DIFF and _qc_stable(pr.qc_delta):
            return "無顯著差異", f"Jaccard={j:.3f}≥{JACCARD_NO_DIFF}"
        if j >= JACCARD_PARTIAL:
            return "數值偏移但結論大致一致", f"Jaccard={j:.3f}∈[{JACCARD_PARTIAL},{JACCARD_NO_DIFF})"
        return "需重跑", f"Jaccard={j:.3f}<{JACCARD_PARTIAL}"

    # 4. Enrichment
    if is_enrich and pr.pathway_jaccard is not None:
        j = pr.pathway_jaccard
        if j >= PATHWAY_JACCARD_NO_DIFF:
            return "無顯著差異", f"pathway Jaccard={j:.3f}≥{PATHWAY_JACCARD_NO_DIFF}"
        return "需重跑", f"pathway Jaccard={j:.3f}<{PATHWAY_JACCARD_NO_DIFF}"

    # 5. EDA
    if is_eda:
        qc_ok = _qc_stable(pr.qc_delta)
        pca_ok = pr.pca_procrustes_dist is None or pr.pca_procrustes_dist < PCA_PROCRUSTES_NO_DIFF
        if qc_ok and pca_ok:
            return "無顯著差異", "QC 穩定且 PCA 結構無顯著偏移"
        reasons = []
        if not qc_ok:
            reasons.append("QC 相對變化≥5%")
        if not pca_ok and pr.pca_procrustes_dist is not None:
            reasons.append(f"Procrustes={pr.pca_procrustes_dist:.4f}≥{PCA_PROCRUSTES_NO_DIFF}")
        return "部分欄位偏移", "; ".join(reasons)

    return "資料不足", "analysis_type 無對應 parser"


# ── Layer 2 orchestrator ──────────────────────────────────────────────────────

def _compare_pair(
    con: duckdb.DuckDBPyConnection,
    pair: dict[str, Any],
) -> PairResult:
    sid = pair["sample_id"]
    atype = pair["analysis_type"]
    aid_a = str(pair["aid_a"])
    aid_b = str(pair["aid_b"])

    pr = PairResult(
        sample_id=sid,
        analysis_type=atype,
        aid_a=aid_a,
        aid_b=aid_b,
        params_identical=bool(pair["params_identical"]),
    )

    # summary_metrics diff（通用）
    ma = _parse_metrics(pair.get("metrics_a"))
    mb = _parse_metrics(pair.get("metrics_b"))
    pr.metrics_diff = _compute_metrics_diff(ma, mb)

    cfg = PARSER_REGISTRY.get(atype, {})
    is_deg = any(t in atype.lower() for t in ("diff_expr", "bulk_deg", "deg"))
    is_enrich = any(t in atype.lower() for t in ("enrich",))
    is_eda = any(t in atype.lower() for t in ("eda",))

    # DEG 比較
    if is_deg:
        path_a = _find_artifact_path(
            con, aid_a,
            cfg.get("artifact_label_pattern", "deg"),
            pair.get("path_a"),
        )
        path_b = _find_artifact_path(
            con, aid_b,
            cfg.get("artifact_label_pattern", "deg"),
            pair.get("path_b"),
        )
        if path_a and path_b:
            pr.gene_list_jaccard, pr.fold_change_spearman = _compute_deg_metrics(path_a, path_b)
        pr.qc_delta = {k: v for k, v in pr.metrics_diff.items()
                       if k not in ("n_comparisons", "n_sig_total", "n_sig_up",
                                    "n_sig_down", "quality_flags")}

    # Enrichment 比較
    elif is_enrich:
        path_a = _find_artifact_path(
            con, aid_a,
            cfg.get("artifact_label_pattern", "enrich"),
            pair.get("path_a"),
        )
        path_b = _find_artifact_path(
            con, aid_b,
            cfg.get("artifact_label_pattern", "enrich"),
            pair.get("path_b"),
        )
        if path_a and path_b:
            pr.pathway_jaccard = _compute_enrichment_jaccard(path_a, path_b)

    # EDA 比較
    elif is_eda:
        # QC delta from summary_metrics
        qc_keys = {"avg_detected_genes", "avg_total_counts", "n_samples"}
        pr.qc_delta = {k: v for k, v in pr.metrics_diff.items() if k in qc_keys}
        pr.pca_procrustes_dist = _compute_pca_procrustes(con, aid_a, aid_b)

    pr.verdict, pr.verdict_reason = _decide_verdict(pr, atype)
    return pr


# ── Layer 3 ───────────────────────────────────────────────────────────────────

_VERDICT_SEVERITY = {
    "需重跑": 0,
    "部分欄位偏移": 1,
    "數值偏移但結論大致一致": 2,
    "參數不同（差異不可單獨歸因程式碼）": 3,
    "資料不足": 4,
    "無顯著差異": 5,
}

_ATYPE_TO_SUGGESTED_TOOL: dict[str, str] = {
    "diff_expr": "bio_run_deg",
    "bulk_deg": "bio_run_deg",
    "enrichment": "bio_run_enrichment",
    "bulk_enrichment": "bio_run_enrichment",
    "eda": "bio_run_bulk_eda",
    "bulk_eda": "bio_run_bulk_eda",
}


def _build_exploration_hints(
    pairs: list[PairResult],
    tool_name: str,
    version_a: str,
    version_b: str,
    con: duckdb.DuckDBPyConnection | None = None,
) -> ExplorationHints:
    hints = ExplorationHints()

    # 依嚴重度排序，取 Top-5
    sorted_pairs = sorted(
        pairs,
        key=lambda p: _VERDICT_SEVERITY.get(p.verdict, 99),
    )
    for p in sorted_pairs[:5]:
        metric_name = "gene_list_jaccard" if p.gene_list_jaccard is not None else (
            "pathway_jaccard" if p.pathway_jaccard is not None else "metrics_diff"
        )
        metric_val = p.gene_list_jaccard if p.gene_list_jaccard is not None else p.pathway_jaccard
        hints.high_delta_samples.append({
            "sample_id": p.sample_id,
            "analysis_type": p.analysis_type,
            "metric": metric_name,
            "val": round(metric_val, 4) if metric_val is not None else None,
            "verdict": p.verdict,
        })
        tool = _ATYPE_TO_SUGGESTED_TOOL.get(p.analysis_type)
        if tool and tool not in hints.suggested_tools and p.verdict in ("需重跑", "部分欄位偏移"):
            hints.suggested_tools.append(tool)

    # 自然語言摘要
    need_rerun = [p for p in pairs if p.verdict == "需重跑"]
    partial = [p for p in pairs if p.verdict in ("部分欄位偏移", "數值偏移但結論大致一致")]
    no_diff = [p for p in pairs if p.verdict == "無顯著差異"]

    lines = [
        f"工具 {tool_name} 從 {version_a} 升到 {version_b}，共比對 {len(pairs)} 筆分析。"
    ]
    if need_rerun:
        sids = ", ".join(p.sample_id for p in need_rerun[:3])
        lines.append(f"{len(need_rerun)} 筆建議重跑（{sids}{'...' if len(need_rerun) > 3 else ''}）。")
    if partial:
        lines.append(f"{len(partial)} 筆有部分偏移但結論大致一致。")
    if no_diff:
        lines.append(f"{len(no_diff)} 筆無顯著差異。")
    hints.investigation_context = " ".join(lines)

    # cascade_impact：找「需重跑」分析的下游依賴（aid_a 和 aid_b 都查，取聯集）
    if con is not None and need_rerun:
        try:
            from analysis.impact import cascade_impact

            cascade_ids: list[str] = []
            for pr in need_rerun[:5]:  # 最多查 5 個，避免過慢
                for aid in (pr.aid_a, pr.aid_b):
                    cr = cascade_impact(con, aid)
                    cascade_ids.extend(a.analysis_id for a in cr.affected_analyses)
            hints.cascade_impact = sorted(set(cascade_ids))
        except Exception as exc:
            logger.warning("cascade_impact 查詢失敗（非致命）：%s", exc)

    return hints


# ── Report artifact landing ───────────────────────────────────────────────────

def _land_report(
    con: duckdb.DuckDBPyConnection,
    report: ComparisonReport,
    out_dir: Path,
) -> str | None:
    """將 ComparisonReport 序列化為 JSON，寫入 analysis_artifacts，回傳 artifact_id。"""
    import json
    import uuid as _uuid
    from dataclasses import asdict

    artifact_id = str(_uuid.uuid4())
    out_path = out_dir / f"version_compare_{artifact_id[:8]}.json"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        logger.warning("報告序列化失敗：%s", exc)
        return None

    try:
        con.execute(
            """
            INSERT INTO analysis_artifacts
                (artifact_id, analysis_id, artifact_type, artifact_subtype,
                 label, file_path, created_at)
            VALUES (?, NULL, 'report', 'version_compare',
                    ?, ?, now())
            """,
            [artifact_id,
             f"version_compare_{report.code_context.tool_name}_"
             f"{report.code_context.version_a}_vs_{report.code_context.version_b}",
             str(out_path)],
        )
    except Exception as exc:
        logger.warning("artifact 寫入失敗（非致命）：%s", exc)
        return None

    return artifact_id


# ── Public API ────────────────────────────────────────────────────────────────

def bio_compare_versions(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    version_a: str,
    version_b: str,
    sample_id: str | None = None,
    analysis_type: str | None = None,
    land_report: bool = True,
) -> ComparisonReport:
    """比較同工具兩個版本間的分析結果差異。

    Args:
        con: DuckDB 連線（與 impact.py 同風格）。
        tool_name: 工具名稱，例如 "bio_run_deg"。
        version_a: 舊版本號，例如 "1.0.0"。
        version_b: 新版本號，例如 "1.1.0"。
        sample_id: 指定樣本；None = 找所有有交集的樣本。
        analysis_type: 進一步篩選 analysis_type；None = 不限。
        land_report: 是否將報告落地為 analysis_artifacts（預設 True）。

    Returns:
        ComparisonReport dataclass。
    """
    from config.settings import DUCKDB_PATH

    # Layer 0
    code_ctx = _build_code_context(con, tool_name, version_a, version_b)

    # Layer 1
    pairs_raw, unmatched = _fetch_pairs(
        con, tool_name, version_a, version_b, sample_id, analysis_type
    )

    # Layer 0 param summary（逐對統計）
    exact = sum(1 for p in pairs_raw if p.get("params_identical"))
    diff_pairs = [p for p in pairs_raw if not p.get("params_identical")]
    param_detail: list[dict[str, Any]] = []
    for p in diff_pairs[:20]:  # 最多展示 20 筆
        import json
        try:
            pa = json.loads(p["params_a"]) if isinstance(p["params_a"], str) else (p["params_a"] or {})
            pb = json.loads(p["params_b"]) if isinstance(p["params_b"], str) else (p["params_b"] or {})
        except Exception:
            continue
        all_keys = set(pa) | set(pb)
        for k in all_keys:
            if pa.get(k) != pb.get(k):
                param_detail.append({
                    "sample_id": p["sample_id"],
                    "key": k,
                    "val_a": pa.get(k),
                    "val_b": pb.get(k),
                })

    param_summary = ParamSummary(
        exact_match_count=exact,
        param_diff_count=len(diff_pairs),
        param_diff_detail=param_detail,
    )

    # Layer 2
    per_pair: list[PairResult] = []
    for p in pairs_raw:
        try:
            pr = _compare_pair(con, p)
            per_pair.append(pr)
        except Exception as exc:
            logger.error("pair 比較失敗 %s/%s：%s", p["aid_a"], p["aid_b"], exc)
            per_pair.append(PairResult(
                sample_id=p["sample_id"],
                analysis_type=p["analysis_type"],
                aid_a=str(p["aid_a"]),
                aid_b=str(p["aid_b"]),
                params_identical=bool(p.get("params_identical")),
                verdict="資料不足",
                verdict_reason=f"比較過程例外：{exc}",
            ))

    # Layer 3
    hints = _build_exploration_hints(per_pair, tool_name, version_a, version_b, con=con)

    report = ComparisonReport(
        code_context=code_ctx,
        param_summary=param_summary,
        matched_pairs=len(per_pair),
        unmatched_samples=unmatched,
        per_pair_results=per_pair,
        exploration_hints=hints,
    )

    # 落地
    if land_report:
        out_dir = Path(str(DUCKDB_PATH)).parent / "reports" / "version_compare"
        report.report_artifact_id = _land_report(con, report, out_dir)

    return report


def render_comparison_md(report: ComparisonReport) -> str:
    """將 ComparisonReport 轉為 Markdown 摘要（供 MCP tool 回傳）。"""
    ctx = report.code_context
    lines = [
        f"# 跨版本比較：{ctx.tool_name} v{ctx.version_a} → v{ctx.version_b}",
        "",
        "## Layer 0：程式碼脈絡",
        f"- source_hash_changed: `{ctx.source_hash_changed}`",
        f"- hash_a: `{ctx.content_hash_a or 'N/A'}`",
        f"- hash_b: `{ctx.content_hash_b or 'N/A'}`",
        f"- change_reason: {ctx.change_reason or '（無變更說明）'}",
        "",
        "## Layer 1：分析對",
        f"- 比對成功：{report.matched_pairs} 對",
        f"- 參數完全一致：{report.param_summary.exact_match_count} 對",
        f"- 參數有差異：{report.param_summary.param_diff_count} 對",
    ]
    if report.unmatched_samples:
        lines.append(f"- 無法配對的樣本：{', '.join(report.unmatched_samples[:10])}")

    if report.per_pair_results:
        lines += ["", "## Layer 2：比較結果"]
        verdict_counts: dict[str, int] = {}
        for pr in report.per_pair_results:
            verdict_counts[pr.verdict] = verdict_counts.get(pr.verdict, 0) + 1
        for v, c in sorted(verdict_counts.items(), key=lambda x: _VERDICT_SEVERITY.get(x[0], 99)):
            lines.append(f"- {v}：{c} 筆")

        lines += ["", "### 各對細節"]
        for pr in report.per_pair_results:
            lines.append(
                f"- **{pr.sample_id}** ({pr.analysis_type}): `{pr.verdict}`"
                + (f"  — {pr.verdict_reason}" if pr.verdict_reason else "")
            )
            if pr.gene_list_jaccard is not None:
                lines.append(f"  - DEG Jaccard: {pr.gene_list_jaccard:.4f}")
            if pr.fold_change_spearman is not None:
                lines.append(f"  - log2FC Spearman ρ: {pr.fold_change_spearman:.4f}")
            if pr.pathway_jaccard is not None:
                lines.append(f"  - Pathway Jaccard: {pr.pathway_jaccard:.4f}")
            if pr.pca_procrustes_dist is not None:
                lines.append(f"  - PCA Procrustes: {pr.pca_procrustes_dist:.4f}")

    hints = report.exploration_hints
    if hints.investigation_context:
        lines += ["", "## Layer 3：探索建議", hints.investigation_context]
    if hints.suggested_tools:
        lines.append(f"- 建議工具：{', '.join(hints.suggested_tools)}")

    if report.report_artifact_id:
        lines += ["", f"*報告已落地為 artifact `{report.report_artifact_id}`*"]

    return "\n".join(lines)
