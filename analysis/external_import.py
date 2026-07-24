"""External analysis result import — generic registration, no re-computation.

Registers analysis results computed OUTSIDE this EP deployment (e.g. on a
different CUDA machine, or via manual bug-recovery) into analysis_history
(+ optional artifact_registry entries), without re-running anything.

Unlike analysis.mcseg_wrapper.register_external_mcseg_result (which cross-checks
n_cells against the actual mask file content), this generic version performs NO
content-level validation of the claimed stats/summary — only path existence and
BIO_DB_ROOT membership are checked. If a given analysis_type ends up needing
frequent external imports, consider adding a dedicated function with type-specific
cross-checks (mirroring register_external_mcseg_result) rather than growing this
one with per-type special cases.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402

logger = logging.getLogger("evo_prism.external_import")


def _register_artifacts(analysis_id: str, artifact_paths: list[dict]) -> list[str]:
    """Register artifacts for an already-open run and return their ids.

    Uses a dedicated DuckDB connection rather than the seam's deferred
    run.artifact() because the caller contract returns artifact_ids, which the
    seam's flush does not surface (CLAUDE.md §6). analysis_artifacts is
    DuckDB-bound regardless of ER_DB_BACKEND (VSS/HNSW).
    """
    if not artifact_paths:
        return []

    import config.settings as _settings
    from config.db_utils import open_db
    from analysis.artifact_registry import register_artifact

    artifact_ids: list[str] = []
    with open_db(_settings.DUCKDB_PATH) as con:
        for item in artifact_paths:
            p = Path(item["path"])
            if not p.exists():
                logger.warning(f"register_external_analysis_result: artifact 檔不存在，略過: {p}")
                continue
            try:
                p.relative_to(BIO_DB_ROOT)
            except ValueError:
                logger.warning(
                    f"register_external_analysis_result: artifact 不在 BIO_DB_ROOT 底下，略過: {p}"
                )
                continue
            artifact_ids.append(
                register_artifact(
                    con,
                    analysis_id,
                    p,
                    item.get("artifact_type", "figure"),
                    item.get("label", p.stem),
                    artifact_subtype=item.get("artifact_subtype"),
                )
            )
    return artifact_ids


@register_tool_on_import(
    tool_name="register_external_analysis_result",
    version="1.1.0",
    description=(
        "登記在 EP 系統外部完成的任意類型分析結果(不限 mcseg),補寫 analysis_history"
        " + artifact_registry,不重新執行任何運算。2026-07-21 新增,只做路徑安全檢查"
        "(存在 + 在 BIO_DB_ROOT 底下),不做內容層級交叉核對——跟"
        " mcseg_wrapper.register_external_mcseg_result(會核對 n_cells 對不對得上"
        " mask 檔案)不同,呼叫端要自己保證 summary/params 的正確性。"
        " 2026-07-23 新增 supersedes_analysis_id:登記某筆分析的修正版時,"
        " 明確標記舊記錄 superseded、新記錄 canonical,避免兩筆並存時查詢者"
        " 誤用到已被修正的舊結果(dpcp01時序方向修正案例踩過這個坑)。"
    ),
)
def register_external_analysis_result(
    sample_id: str,
    analysis_type: str,
    result_path: str | Path,
    summary: str,
    params: dict | None = None,
    requested_by: str = "external_import",
    artifact_paths: list[dict] | None = None,
    supersedes_analysis_id: str | None = None,
) -> dict:
    """
    Register an externally-computed analysis result of any type into analysis_history.

    Args:
        analysis_type: free-text label, e.g. "bulk_deg", "mcseg_fullslide",
            "custom_analysis" — matches the analysis_history.analysis_type convention
            used by the other bio_run_* tools.
        result_path: primary output file. Must already exist under BIO_DB_ROOT.
        summary: human-readable one-line summary, stored as-is (not validated).
        artifact_paths: optional list of {"path", "artifact_type", "label",
            "artifact_subtype"} dicts for figures/tables to register in
            artifact_registry. Entries with a missing file or a path outside
            BIO_DB_ROOT are skipped with a warning, not a hard failure.
        supersedes_analysis_id: pass the analysis_id of a specific prior row this
            result corrects/replaces (e.g. a wrong-direction result later fixed).
            That row gets tagged 'superseded' and this new row gets tagged
            'canonical' (analysis_history.tags), so bio_history_lookup and any
            future query can tell which one is authoritative without relying on
            someone reading summary text carefully. Must belong to the same
            sample_id — raises ValueError otherwise. Omit this for independent/
            parallel analyses that don't replace anything (e.g. re-running
            geneset_score with a different marker set — that's a new analysis,
            not a correction, and both remain equally valid).
    """
    from store.factory import get_store

    sample_id = str(sample_id)
    analysis_type = str(analysis_type)
    result_path = Path(result_path)
    params = params or {}
    artifact_paths = artifact_paths or []

    store = get_store()
    sample = store.get_sample(sample_id)
    if not sample:
        raise ValueError(f"sample_id '{sample_id}' 不存在於 sample_registry，請先 bio_register_sample。")

    if supersedes_analysis_id:
        with store.read_conn() as con:
            parent_row = con.execute(
                "SELECT sample_id FROM analysis_history WHERE analysis_id = ?",
                [supersedes_analysis_id],
            ).fetchone()
        if not parent_row:
            raise ValueError(f"supersedes_analysis_id 不存在：{supersedes_analysis_id}")
        if parent_row[0] != sample_id:
            raise ValueError(
                f"supersedes_analysis_id 屬於不同樣本（{parent_row[0]}），"
                f"跟這次註冊的 sample_id（{sample_id}）不符"
            )

    if not result_path.exists():
        raise FileNotFoundError(f"result_path 不存在：{result_path}（外部結果要先複製進 BIO_DB_ROOT 底下）")
    try:
        result_path.relative_to(BIO_DB_ROOT)
    except ValueError:
        raise ValueError(
            f"result_path 必須在 BIO_DB_ROOT（{BIO_DB_ROOT}）底下，容器/其他工具才讀得到；"
            f"收到 {result_path}"
        )

    # 生命週期骨架走 seam（CLAUDE.md §6）；外部結果無「running 中間態」，
    # 但仍用 CM 而非 record_completed_run，因為要在 body 內自行 register_artifact 收 id。
    from analysis.run_context import analysis_run

    with analysis_run(
        sample_id,
        analysis_type,
        params=params,
        requested_by=requested_by,
        parent_analysis_id=supersedes_analysis_id,
        tool_name="register_external_analysis_result",
    ) as run:
        analysis_id = run.analysis_id
        artifact_ids = _register_artifacts(analysis_id, artifact_paths)
        run.complete(result_path, summary)

    # canonical/superseded 標記：對「指定的那一筆舊記錄」翻轉，語意與 seam 的
    # mark_canonical（掃整個 sample+analysis_type）不同，故留在 seam 外顯式呼叫。
    if supersedes_analysis_id:
        store.supersede_by_id(analysis_id, supersedes_analysis_id)

    logger.info(
        f"register_external_analysis_result: analysis_id={analysis_id} "
        f"sample_id={sample_id} analysis_type={analysis_type} "
        f"supersedes={supersedes_analysis_id}"
    )
    return {
        "analysis_id": analysis_id,
        "artifact_ids": artifact_ids,
        "supersedes_analysis_id": supersedes_analysis_id,
    }