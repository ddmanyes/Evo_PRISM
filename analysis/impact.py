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

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-fA-F\-]{8,36}$")
_SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

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
    target_kind: str            # 'tool' | 'artifact' | 'sample'
    target: str
    affected_analyses: list[AffectedAnalysis] = field(default_factory=list)
    affected_artifact_ids: list[str] = field(default_factory=list)
    affected_samples: list[str] = field(default_factory=list)
    untracked_note: str = ""    # tool_id 覆蓋缺口提示

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
    rows = con.execute(
        "SELECT tool_id FROM tools WHERE tool_name = ?", [tool_name]
    ).fetchall()
    return [str(r[0]) for r in rows]


def tool_impact(con: duckdb.DuckDBPyConnection, tool_name: str) -> ImpactReport:
    """改版 / deprecate 某工具的爆炸範圍。

    兩條影響邊：
      1. analysis_history.tool_id ∈ 該工具所有版本 → confidence 1.0（tool_id-exact）
      2. analysis_type 對應到 tool_name 但 tool_id 為 NULL → confidence 0.6（heuristic）
    再往下展開受影響的 artifacts 與 samples。
    """
    if not _TOOL_NAME_RE.match(tool_name):
        raise ValueError(f"無效的 tool_name：{tool_name!r}")

    report = ImpactReport(target_kind="tool", target=tool_name)
    seen: set[str] = set()

    # 邊 1：tool_id 精確
    tool_ids = _resolve_tool_ids(con, tool_name)
    if tool_ids:
        placeholders = ", ".join("?" * len(tool_ids))
        rows = con.execute(
            f"""
            SELECT analysis_id, analysis_type, sample_id, status
            FROM   analysis_history
            WHERE  tool_id IN ({placeholders})
            ORDER  BY started_at DESC
            """,
            tool_ids,
        ).fetchall()
        for aid, atype, sid, status in rows:
            report.affected_analyses.append(AffectedAnalysis(
                analysis_id=str(aid), analysis_type=atype, sample_id=sid,
                status=status, confidence=CONF_TOOL_ID_EXACT, reason="tool_id-exact",
            ))
            seen.add(str(aid))

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
            report.affected_analyses.append(AffectedAnalysis(
                analysis_id=str(aid), analysis_type=atype, sample_id=sid,
                status=status, confidence=CONF_TYPE_HEURISTIC,
                reason="analysis_type-heuristic",
            ))
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
        report.affected_analyses.append(AffectedAnalysis(
            analysis_id=parent_analysis, analysis_type=arow[0], sample_id=arow[1],
            status=arow[2], confidence=CONF_SAME_ANALYSIS, reason="same-analysis",
        ))
        if arow[1]:
            report.affected_samples = [arow[1]]
    return report


def sample_impact(con: duckdb.DuckDBPyConnection, sample_id: str) -> ImpactReport:
    """某樣本的所有分析與產物（重跑 / 撤回樣本時的範圍）。"""
    if not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(f"無效的 sample_id：{sample_id!r}")

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
        report.affected_analyses.append(AffectedAnalysis(
            analysis_id=str(aid), analysis_type=atype, sample_id=sid,
            status=status, confidence=CONF_TOOL_ID_EXACT, reason="sample-direct",
        ))
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


# ── Markdown 渲染（給 MCP tool 回傳）───────────────────────────────────────

def render_impact_md(report: ImpactReport) -> str:
    """把 ImpactReport 渲染成簡明 Markdown。"""
    lines = [
        f"# 影響分析：{report.target_kind} = `{report.target}`",
        "",
        f"- 受影響分析：**{report.n_analyses}** 筆",
        f"- 受影響產物：**{report.n_artifacts}** 個",
        f"- 涉及樣本：**{len(report.affected_samples)}** 個"
        + (f"（{', '.join(report.affected_samples[:8])}{'…' if len(report.affected_samples) > 8 else ''}）"
           if report.affected_samples else ""),
    ]
    if report.affected_analyses:
        lines.append(f"- 最高信心：{report.max_confidence:.1f}")
    if report.untracked_note:
        lines += ["", f"> ⚠️ {report.untracked_note}"]

    if report.affected_analyses:
        lines += ["", "## 受影響分析（依信心排序）", "",
                  "| analysis_id | type | sample | status | confidence | reason |",
                  "|---|---|---|---|---|---|"]
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
