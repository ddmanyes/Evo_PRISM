"""影響分析 / 爆炸範圍（blast radius）— 借鏡 GitNexus 的 impact tool。

回答 HELIX §7 版本治理的關鍵問題：
    「我若改版 / deprecate 某個工具，會影響到哪些 sample 的哪些分析與產物？」

bio_DB 的影響圖（沿既有 schema，無需 migration）：

    tools(tool_id, tool_name, version)
       │  analysis_history.tool_id  ← 精確邊（confidence 1.0）
       │  analysis_type ↔ tool_name ← 啟發式邊（confidence 0.6，補 tool_id 稀疏）
       ▼
    analysis_history(analysis_id, sample_id, status)
       │  analysis_artifacts.analysis_id  ← same-analysis（confidence 0.9）
       ▼
    analysis_artifacts(artifact_id)

設計重點（吸收 GitNexus 的 confidence-on-edges 精神）：
    每條影響邊帶 (confidence, reason)，讓使用者區分「確定受影響」與「依命名推測」。
    這讓 impact 在 tool_id 覆蓋率僅 ~17% 的現況下仍可運作，且隨覆蓋率提升而精準。

詳見 docs/GITNEXUS_BORROW_ASSESSMENT.md。
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

logger = logging.getLogger(__name__)

# analysis_type → 產生它的工具 tool_name（啟發式邊用）。
# 與 server/agent.py 的 BIO_TOOLS 命名對齊；dynamic_code / l2_convert 無對應工具。
ANALYSIS_TYPE_TO_TOOL: dict[str, str] = {
    "bulk_eda": "bio_run_bulk_eda",
    "eda_report": "bio_run_spatial_eda",
    "bulk_deg": "bio_run_deg",
    "bulk_enrichment": "bio_run_enrichment",
    "bulk_heatmap": "bio_run_heatmaps",
    "mcseg_qc": "bio_run_mcseg_qc",
}

from analysis.validators import validate_sample_id

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-fA-F\-]{8,36}$")

# confidence tier 常數（與 GitNexus 0.5–1.0 對齊）
CONF_TOOL_ID_EXACT = 1.0
CONF_SAME_ANALYSIS = 0.9
CONF_TYPE_HEURISTIC = 0.6


@dataclass(frozen=True)
class AffectedAnalysis:
    """一筆受影響的分析。"""

    analysis_id: str
    analysis_type: str
    sample_id: Optional[str]
    status: Optional[str]
    confidence: float
    reason: str


@dataclass
class ImpactReport:
    """blast-radius 結果。"""

    target_kind: str  # 'tool' | 'artifact' | 'sample'
    target: str
    affected_analyses: list[AffectedAnalysis] = field(default_factory=list)
    affected_artifact_ids: list[str] = field(default_factory=list)
    affected_samples: list[str] = field(default_factory=list)
    untracked_note: str = ""  # tool_id 覆蓋缺口提示
    param_filter_note: str = ""  # diff-level tagging 縮窄說明（P3）

    @property
    def n_analyses(self) -> int:
        return len(self.affected_analyses)

    @property
    def n_artifacts(self) -> int:
        return len(self.affected_artifact_ids)

    @property
    def max_confidence(self) -> float:
        return max((a.confidence for a in self.affected_analyses), default=0.0)


def _resolve_tool_ids(con: duckdb.DuckDBPyConnection, tool_name: str) -> list[str]:
    """取某 tool_name 的所有版本 tool_id（含 deprecated）。"""
    rows = con.execute("SELECT tool_id FROM tools WHERE tool_name = ?", [tool_name]).fetchall()
    return [str(r[0]) for r in rows]


def _get_active_affected_params(
    con: duckdb.DuckDBPyConnection, tool_name: str
) -> list[str] | None:
    """取 active 版本的 affected_params（v25+ schema）；欄位不存在時回 None。"""
    try:
        row = con.execute(
            """
            SELECT affected_params FROM tools
            WHERE tool_name = ? AND status = 'active'
            LIMIT 1
            """,
            [tool_name],
        ).fetchone()
        if row and row[0] is not None:
            val = row[0]
            if isinstance(val, str):
                import json as _json
                val = _json.loads(val)
            if isinstance(val, list) and val:
                return [str(p) for p in val]
    except Exception:
        pass
    return None


def _analysis_uses_affected_params(
    parameters_json: str | dict | None,
    affected_params: list[str],
) -> bool:
    """回傳 True 如果 parameters 中包含任何 affected_params 的 key。"""
    if not parameters_json:
        return False
    if isinstance(parameters_json, str):
        import json as _json
        try:
            params = _json.loads(parameters_json)
        except Exception:
            return False
    else:
        params = parameters_json
    if not isinstance(params, dict):
        return False
    return bool(set(affected_params) & set(params.keys()))


def tool_impact(con: duckdb.DuckDBPyConnection, tool_name: str) -> ImpactReport:
    """改版 / deprecate 某工具的爆炸範圍。

    三條影響邊：
      1. analysis_history.tool_id ∈ 該工具所有版本 → confidence 1.0（tool_id-exact）
         若 active 版本有 affected_params（P3）：
           - parameters 含 affected_params key → confidence 1.0（param-affected）
           - 否則 → confidence 0.3（param-unaffected，降低但不移除）
      2. analysis_type 對應到 tool_name 但 tool_id 為 NULL → confidence 0.6（heuristic）
    再往下展開受影響的 artifacts 與 samples。
    """
    if not _TOOL_NAME_RE.match(tool_name):
        raise ValueError(f"無效的 tool_name：{tool_name!r}")

    report = ImpactReport(target_kind="tool", target=tool_name)
    seen: set[str] = set()

    # diff-level tagging（P3）：取 active 版本 affected_params
    affected_params = _get_active_affected_params(con, tool_name)

    # 邊 1：tool_id 精確
    tool_ids = _resolve_tool_ids(con, tool_name)
    if tool_ids:
        placeholders = ", ".join("?" * len(tool_ids))
        rows = con.execute(
            f"""
            SELECT analysis_id, analysis_type, sample_id, status, parameters
            FROM   analysis_history
            WHERE  tool_id IN ({placeholders})
            ORDER  BY started_at DESC
            """,
            tool_ids,
        ).fetchall()
        n_narrowed = 0
        for aid, atype, sid, status, params_raw in rows:
            if affected_params is not None:
                uses = _analysis_uses_affected_params(params_raw, affected_params)
                if uses:
                    conf = CONF_TOOL_ID_EXACT
                    reason = "tool_id-exact|param-affected"
                else:
                    conf = 0.3
                    reason = "tool_id-exact|param-unaffected"
                    n_narrowed += 1
            else:
                conf = CONF_TOOL_ID_EXACT
                reason = "tool_id-exact"
            report.affected_analyses.append(
                AffectedAnalysis(
                    analysis_id=str(aid),
                    analysis_type=atype,
                    sample_id=sid,
                    status=status,
                    confidence=conf,
                    reason=reason,
                )
            )
            seen.add(str(aid))

        if affected_params is not None:
            n_high = sum(1 for a in report.affected_analyses if a.confidence >= CONF_TOOL_ID_EXACT)
            report.param_filter_note = (
                f"diff-level tagging 啟用（affected_params={affected_params}）："
                f"{n_high} 筆確定受影響（confidence 1.0），"
                f"{n_narrowed} 筆降為 0.3（未使用受影響參數，可能毋需重跑）。"
            )

    # 邊 2：analysis_type 啟發式（補 tool_id 稀疏）
    heuristic_types = [t for t, name in ANALYSIS_TYPE_TO_TOOL.items() if name == tool_name]
    n_heuristic = 0
    if heuristic_types:
        tph = ", ".join("?" * len(heuristic_types))
        rows = con.execute(
            f"""
            SELECT analysis_id, analysis_type, sample_id, status
            FROM   analysis_history
            WHERE  analysis_type IN ({tph}) AND tool_id IS NULL
            ORDER  BY started_at DESC
            """,
            heuristic_types,
        ).fetchall()
        for aid, atype, sid, status in rows:
            if str(aid) in seen:
                continue
            report.affected_analyses.append(
                AffectedAnalysis(
                    analysis_id=str(aid),
                    analysis_type=atype,
                    sample_id=sid,
                    status=status,
                    confidence=CONF_TYPE_HEURISTIC,
                    reason="analysis_type-heuristic",
                )
            )
            seen.add(str(aid))
            n_heuristic += 1

    _expand_artifacts_and_samples(con, report, seen)

    if n_heuristic:
        report.untracked_note = (
            f"{n_heuristic} 筆分析以 analysis_type 啟發式匹配（tool_id 未回填，"
            f"confidence={CONF_TYPE_HEURISTIC}）；經 MCP 呼叫工具可讓 tool_id 回填以提升精度。"
        )
    return report


def artifact_impact(con: duckdb.DuckDBPyConnection, artifact_id: str) -> ImpactReport:
    """某 artifact 的下游影響。

    邊：
      1. artifact_relations 顯式 lineage（src=artifact → 下游 dst）—— 現多為 0 筆
      2. 同一 analysis 的其他 artifacts（same-analysis，confidence 0.9）
    """
    if not _ARTIFACT_ID_RE.match(artifact_id):
        raise ValueError(f"無效的 artifact_id：{artifact_id!r}")

    report = ImpactReport(target_kind="artifact", target=artifact_id)

    # 找這個 artifact 的母 analysis
    parent = con.execute(
        "SELECT analysis_id FROM analysis_artifacts WHERE artifact_id = ?",
        [artifact_id],
    ).fetchone()
    if not parent:
        return report
    parent_analysis = str(parent[0])

    # 邊 2：同 analysis 的其他 artifacts
    sibling_rows = con.execute(
        "SELECT artifact_id FROM analysis_artifacts WHERE analysis_id = ? AND artifact_id != ?",
        [parent_analysis, artifact_id],
    ).fetchall()
    report.affected_artifact_ids = [str(r[0]) for r in sibling_rows]

    # 邊 1：顯式 lineage（若 artifact_relations 存在且有資料）
    try:
        downstream = con.execute(
            """
            SELECT dst_artifact_id FROM artifact_relations
            WHERE  src_artifact_id = ?
            """,
            [artifact_id],
        ).fetchall()
        for r in downstream:
            did = str(r[0])
            if did not in report.affected_artifact_ids:
                report.affected_artifact_ids.append(did)
    except Exception:
        logger.debug("artifact_impact: artifact_relations 不可查（可能未 migrate）")

    # 受影響分析 = 母 analysis
    arow = con.execute(
        "SELECT analysis_type, sample_id, status FROM analysis_history WHERE analysis_id = ?",
        [parent_analysis],
    ).fetchone()
    if arow:
        report.affected_analyses.append(
            AffectedAnalysis(
                analysis_id=parent_analysis,
                analysis_type=arow[0],
                sample_id=arow[1],
                status=arow[2],
                confidence=CONF_SAME_ANALYSIS,
                reason="same-analysis",
            )
        )
        if arow[1]:
            report.affected_samples = [arow[1]]
    return report


def sample_impact(con: duckdb.DuckDBPyConnection, sample_id: str) -> ImpactReport:
    """某樣本的所有分析與產物（重跑 / 撤回樣本時的範圍）。"""
    validate_sample_id(sample_id)

    report = ImpactReport(target_kind="sample", target=sample_id)
    report.affected_samples = [sample_id]
    seen: set[str] = set()

    rows = con.execute(
        """
        SELECT analysis_id, analysis_type, sample_id, status
        FROM   analysis_history
        WHERE  sample_id = ?
        ORDER  BY started_at DESC
        """,
        [sample_id],
    ).fetchall()
    for aid, atype, sid, status in rows:
        report.affected_analyses.append(
            AffectedAnalysis(
                analysis_id=str(aid),
                analysis_type=atype,
                sample_id=sid,
                status=status,
                confidence=CONF_TOOL_ID_EXACT,
                reason="sample-direct",
            )
        )
        seen.add(str(aid))

    _expand_artifacts_and_samples(con, report, seen, collect_samples=False)
    return report


def _expand_artifacts_and_samples(
    con: duckdb.DuckDBPyConnection,
    report: ImpactReport,
    analysis_ids: set[str],
    *,
    collect_samples: bool = True,
) -> None:
    """把受影響 analyses 展開成 artifacts（+ samples）。"""
    if not analysis_ids:
        return
    ids = list(analysis_ids)
    placeholders = ", ".join("?" * len(ids))
    art_rows = con.execute(
        f"""
        SELECT DISTINCT artifact_id FROM analysis_artifacts
        WHERE  analysis_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    report.affected_artifact_ids = [str(r[0]) for r in art_rows]

    if collect_samples:
        samples = {a.sample_id for a in report.affected_analyses if a.sample_id}
        report.affected_samples = sorted(samples)


# ── cascade_impact：反向依賴鏈走訪（P3.5）─────────────────────────────────

_CASCADE_MAX_DEPTH = 20  # 防環深度上限


def cascade_impact(
    con: duckdb.DuckDBPyConnection,
    analysis_id: str,
    max_depth: int = _CASCADE_MAX_DEPTH,
) -> ImpactReport:
    """從某個分析出發，找出所有依賴它的下游分析（artifact lineage 反向走訪）。

    適用場景：
      某分析的結果被下游使用（如 bulk_eda 的 counts 被 bulk_deg 消費），
      升版或重跑後想知道「哪些下游分析需要跟著重跑」。

    原理：
      1. 取該 analysis_id 的所有產物 artifact_id（analysis_artifacts 表）。
      2. 以 WITH RECURSIVE 從這些 artifact 出發，順著 artifact_relations
         （src → dst，relation_type='derived_from'）正向走訪。
      3. 收集所有下游 artifact_id → 查出所屬 analysis_id。
      4. 排除起始 analysis_id 本身。

    備注：
      - `artifact_relations` 目前已有真實邊（bulk_eda→bulk_deg→bulk_enrichment）。
      - UNION（非 UNION ALL）去重，搭配 depth < max_depth 防環。
      - 若 artifact_relations 為空或無邊，回傳空 ImpactReport（不 raise）。

    Args:
        con:          DuckDB 連線（read_only 可用）。
        analysis_id:  起始分析 UUID。
        max_depth:    遞迴最大深度（預設 20）。

    Returns:
        ImpactReport（target_kind='analysis'，target=analysis_id）。
    """
    if not _ARTIFACT_ID_RE.match(analysis_id):
        raise ValueError(f"無效的 analysis_id：{analysis_id!r}")

    report = ImpactReport(target_kind="analysis", target=analysis_id)

    # Step 1：取起始分析的所有產物
    try:
        start_artifacts = con.execute(
            "SELECT artifact_id FROM analysis_artifacts WHERE analysis_id = ?",
            [analysis_id],
        ).fetchall()
    except Exception as exc:
        logger.warning("cascade_impact: 無法讀取 analysis_artifacts: %s", exc)
        return report

    if not start_artifacts:
        return report

    start_ids = [str(r[0]) for r in start_artifacts]

    # Step 2：遞迴走訪 artifact_relations（正向：src→dst）
    try:
        placeholders = ", ".join("?" * len(start_ids))
        rows = con.execute(
            f"""
            WITH RECURSIVE downstream(artifact_id, depth) AS (
                SELECT CAST(dst_artifact_id AS VARCHAR), 1
                FROM   artifact_relations
                WHERE  src_artifact_id IN ({placeholders})
                  AND  relation_type = 'derived_from'
              UNION
                SELECT CAST(r.dst_artifact_id AS VARCHAR), d.depth + 1
                FROM   artifact_relations r
                JOIN   downstream d ON CAST(r.src_artifact_id AS VARCHAR) = d.artifact_id
                WHERE  d.depth < ?
                  AND  r.relation_type = 'derived_from'
            )
            SELECT DISTINCT artifact_id FROM downstream
            """,
            start_ids + [max_depth],
        ).fetchall()
    except Exception as exc:
        logger.warning("cascade_impact: WITH RECURSIVE 失敗: %s", exc)
        return report

    if not rows:
        return report

    downstream_artifact_ids = [r[0] for r in rows]

    # Step 3：找下游 artifact 對應的 analysis_id
    try:
        ph2 = ", ".join("?" * len(downstream_artifact_ids))
        analysis_rows = con.execute(
            f"""
            SELECT DISTINCT aa.analysis_id, ah.analysis_type, ah.sample_id, ah.status
            FROM   analysis_artifacts aa
            JOIN   analysis_history ah ON ah.analysis_id = aa.analysis_id
            WHERE  CAST(aa.artifact_id AS VARCHAR) IN ({ph2})
              AND  CAST(aa.analysis_id AS VARCHAR) != ?
            """,
            downstream_artifact_ids + [analysis_id],
        ).fetchall()
    except Exception as exc:
        logger.warning("cascade_impact: 下游 analysis 查詢失敗: %s", exc)
        return report

    seen: set[str] = set()
    for aid, atype, sid, status in analysis_rows:
        aid_str = str(aid)
        if aid_str in seen or aid_str == analysis_id:
            continue
        report.affected_analyses.append(
            AffectedAnalysis(
                analysis_id=aid_str,
                analysis_type=atype,
                sample_id=sid,
                status=status,
                confidence=CONF_SAME_ANALYSIS,
                reason="cascade-derived_from",
            )
        )
        seen.add(aid_str)

    report.affected_artifact_ids = downstream_artifact_ids
    report.affected_samples = sorted({a.sample_id for a in report.affected_analyses if a.sample_id})
    return report


# ── Markdown 渲染（給 MCP tool 回傳）───────────────────────────────────────


def render_impact_md(report: ImpactReport) -> str:
    """把 ImpactReport 渲染成簡明 Markdown。"""
    lines = [
        f"# 影響分析：{report.target_kind} = `{report.target}`",
        "",
        f"- 受影響分析：**{report.n_analyses}** 筆",
        f"- 受影響產物：**{report.n_artifacts}** 個",
        f"- 涉及樣本：**{len(report.affected_samples)}** 個"
        + (
            f"（{', '.join(report.affected_samples[:8])}{'…' if len(report.affected_samples) > 8 else ''}）"
            if report.affected_samples
            else ""
        ),
    ]
    if report.affected_analyses:
        lines.append(f"- 最高信心：{report.max_confidence:.1f}")
    if report.param_filter_note:
        lines += ["", f"> 🔍 {report.param_filter_note}"]
    if report.untracked_note:
        lines += ["", f"> ⚠️ {report.untracked_note}"]

    if report.affected_analyses:
        lines += [
            "",
            "## 受影響分析（依信心排序）",
            "",
            "| analysis_id | type | sample | status | confidence | reason |",
            "|---|---|---|---|---|---|",
        ]
        for a in sorted(report.affected_analyses, key=lambda x: -x.confidence):
            lines.append(
                f"| {a.analysis_id[:8]} | {a.analysis_type} | {a.sample_id or '—'} "
                f"| {a.status or '—'} | {a.confidence:.1f} | {a.reason} |"
            )
    else:
        lines += ["", "（無受影響分析 — 此目標目前無下游依賴）"]

    return "\n".join(lines)


def compute_impact(
    *,
    tool_name: Optional[str] = None,
    artifact_id: Optional[str] = None,
    sample_id: Optional[str] = None,
    con: Optional[duckdb.DuckDBPyConnection] = None,
) -> ImpactReport:
    """統一入口：依傳入參數選擇 tool / artifact / sample 影響分析。

    恰好提供一個目標參數。
    """
    targets = [t for t in (tool_name, artifact_id, sample_id) if t]
    if len(targets) != 1:
        raise ValueError("compute_impact 需恰好一個目標：tool_name / artifact_id / sample_id")

    _own = con is None
    if con is None:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        if tool_name:
            return tool_impact(con, tool_name)
        if artifact_id:
            return artifact_impact(con, artifact_id)
        return sample_impact(con, sample_id)  # type: ignore[arg-type]
    finally:
        if _own:
            con.close()
