#!/usr/bin/env python3
"""產生 docs/registry_snapshot.md — 樣本登記與分析狀態的靜態快照。

Agent 查詢樣本清單或分析狀態時直接 Read 此檔，無需每次查詢 DuckDB。

三個 section：
  1. 樣本清單（sample_registry）
  2. 分析狀態（每個 project × analysis_type 的 canonical 記錄）
  3. 待辦事項（l2_ready=True 但無 canonical 分析，或路徑異常）

使用方式：
    uv run python scripts/export_registry.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH, BIO_DB_ROOT
from config.db_utils import open_db

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path(BIO_DB_ROOT) / "docs" / "registry_snapshot.md"


# ── helpers ──────────────────────────────────────────────────────────────────


def _path_status(l3_path: str | None) -> str:
    if not l3_path:
        return "none"
    if l3_path.startswith("/Volumes/"):
        return "ok"
    if l3_path.startswith("/mnt/"):
        return "linux"
    if l3_path.upper().startswith("I:") or l3_path.upper().startswith("I:/"):
        return "win"
    return "other"


def _section1(con) -> str:
    rows = con.execute("""
        SELECT sample_id, data_type, project, condition, time_point, tissue,
               l2_ready, last_updated::date AS updated, l3_path
        FROM sample_registry
        ORDER BY data_type, project, sample_id
    """).fetchall()

    lines: list[str] = ["## 1. 樣本清單（sample_registry）\n"]

    prev_type = None
    prev_proj = None
    group_rows: list[str] = []

    def flush_group(dtype, proj, rows_md):
        if not rows_md:
            return ""
        header = f"### {dtype} / {proj}\n\n"
        header += "| sample_id | condition | time_point | tissue | l2_ready | path | updated |\n"
        header += "|-----------|-----------|------------|--------|----------|------|---------|\n"
        return header + "\n".join(rows_md) + "\n\n"

    output_groups: list[str] = []
    for r in rows:
        sample_id, dtype, proj, cond, tp, tissue, l2, updated, l3 = r
        ps = _path_status(l3)
        row_md = (
            f"| {sample_id} | {cond or ''} | {tp or ''} | {tissue or ''} "
            f"| {'✓' if l2 else '✗'} | {ps} | {updated} |"
        )
        if dtype != prev_type or proj != prev_proj:
            output_groups.append(flush_group(prev_type, prev_proj, group_rows))
            group_rows = []
            prev_type = dtype
            prev_proj = proj
        group_rows.append(row_md)
    output_groups.append(flush_group(prev_type, prev_proj, group_rows))

    # stats
    type_counts = {}
    for r in rows:
        type_counts[r[1]] = type_counts.get(r[1], 0) + 1
    stats = " | ".join(f"{t}: {n}" for t, n in sorted(type_counts.items()))

    lines.append("".join(output_groups))
    lines.append(f"**總計 {len(rows)} 筆** — {stats}\n")
    return "\n".join(lines)


def _section2(con) -> str:
    rows = con.execute("""
        SELECT
            COALESCE(sr.project, ah.sample_id) AS project,
            ah.sample_id,
            ah.analysis_type,
            ah.completed_at::date AS completed,
            ah.summary,
            ah.analysis_id
        FROM analysis_history ah
        LEFT JOIN sample_registry sr ON ah.sample_id = sr.sample_id
        WHERE list_contains(COALESCE(ah.tags, []), 'canonical')
        ORDER BY project, ah.analysis_type, completed DESC
    """).fetchall()

    if not rows:
        return "## 2. 分析狀態（canonical）\n\n（尚無 canonical 分析記錄）\n"

    lines = ["## 2. 分析狀態（canonical）\n"]
    prev_proj = None

    for proj, sample_id, atype, completed, summary, aid in rows:
        if proj != prev_proj:
            if prev_proj is not None:
                lines.append("")
            lines.append(f"### {proj}\n")
            lines.append("| analysis_type | completed | summary | analysis_id |")
            lines.append("|---------------|-----------|---------|-------------|")
            prev_proj = proj
        short_summary = (summary or "")[:80]
        short_id = str(aid)[:8] if aid else ""
        lines.append(f"| {atype} | {completed} | {short_summary} | `{short_id}…` |")

    return "\n".join(lines) + "\n"


def _section3(con) -> str:
    # path anomalies: only flag if l2_ready=False (L3 path still needed) AND path is not /Volumes/
    path_rows = con.execute("""
        SELECT sample_id, data_type, project, l3_path
        FROM sample_registry
        WHERE l3_path IS NOT NULL
          AND l3_path NOT LIKE '/Volumes/%'
          AND l2_ready = FALSE
          AND sample_id != 'Kallisto_v1'
        ORDER BY sample_id
    """).fetchall()

    # gap detection: skip bulk_rnaseq individual samples (analyzed at batch/project level via anchor)
    # only check spatial / scrna / other data types where per-sample analysis is expected
    gap_rows = con.execute("""
        SELECT sr.sample_id, sr.data_type, sr.project
        FROM sample_registry sr
        WHERE sr.l2_ready = TRUE
          AND sr.data_type NOT IN ('bulk_rnaseq')
          AND NOT EXISTS (
              SELECT 1 FROM analysis_history ah
              WHERE ah.sample_id = sr.sample_id
                AND list_contains(COALESCE(ah.tags, []), 'canonical')
          )
        ORDER BY sr.data_type, sr.sample_id
    """).fetchall()

    lines = ["## 3. 待辦事項（pipeline gaps）\n"]

    if path_rows:
        lines.append("**路徑異常**（非 /Volumes/ 路徑，需更新）\n")
        for sample_id, dtype, proj, l3 in path_rows:
            ps = _path_status(l3)
            lines.append(f"- [ ] `{sample_id}` ({dtype} / {proj})：path_status=**{ps}** — `{l3}`")
        lines.append("")

    if gap_rows:
        lines.append("**尚無 canonical 分析記錄**（l2_ready=True 但未跑分析）\n")
        for sample_id, dtype, proj in gap_rows:
            lines.append(f"- [ ] `{sample_id}` ({dtype} / {proj})")
        lines.append("")

    if not path_rows and not gap_rows:
        lines.append("（無待辦事項）\n")

    return "\n".join(lines)


def _section4(con) -> str:
    """Section 4：新舊 ID 對照表（alias cross-reference）。"""
    rows = con.execute("""
        SELECT sample_id, alias, data_type, project
        FROM sample_registry
        WHERE alias IS NOT NULL
        ORDER BY data_type, project, sample_id
    """).fetchall()

    if not rows:
        return "## 4. Sample ID 對照表（alias）\n\n（無 alias 記錄）\n"

    lines = ["## 4. Sample ID 對照表（alias）\n"]
    lines.append("| 新 ID | 舊 ID（alias）| 類型 | 專案 |")
    lines.append("|-------|--------------|------|------|")

    prev_type = None
    for sample_id, alias, dtype, proj in rows:
        if dtype != prev_type:
            lines.append(f"| **{dtype}** | | | |")
            prev_type = dtype
        lines.append(f"| `{sample_id}` | `{alias}` | {dtype} | {proj} |")

    return "\n".join(lines) + "\n"


# ── main export ───────────────────────────────────────────────────────────────


def export_snapshot() -> Path:
    """產生 registry_snapshot.md，回傳輸出路徑。"""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open_db(DUCKDB_PATH) as con:
        _ts_row = con.execute(
            "SELECT MAX(last_updated) FROM sample_registry"
        ).fetchone()
        db_ts = _ts_row[0] if _ts_row else None

        s1 = _section1(con)
        s2 = _section2(con)
        s3 = _section3(con)
        s4 = _section4(con)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    db_ts_str = str(db_ts)[:19] if db_ts else "unknown"

    content = f"""<!-- auto-generated — do not edit manually; run scripts/export_registry.py to refresh -->
# Registry Snapshot

> Snapshot generated: {now} | DB max last_updated: {db_ts_str}

{s1}
---

{s2}
---

{s3}
---

{s4}"""

    OUTPUT_PATH.write_text(content, encoding="utf-8")
    logger.info("registry_snapshot.md 已更新：%s", OUTPUT_PATH)
    return OUTPUT_PATH


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s — %(message)s",
    )
    path = export_snapshot()
    print(f"輸出：{path}")


if __name__ == "__main__":
    main()
