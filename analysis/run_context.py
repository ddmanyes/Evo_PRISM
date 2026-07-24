"""Analysis Run 生命週期 seam —— 分析執行不變式的唯一實作出口。

CLAUDE.md §6/§7.3 要求每個分析函數自行完成一整條「Analysis Run 生命週期」骨架
（insert running → 做事 → complete/fail → mark_canonical → diagnosis → 回填 tool_id →
flush artifacts → export_snapshot）。這條骨架原本被複製在 17 個 analysis/*.py，且已漂移成
「純 raw SQL / 半遷移 / 走 store」三種寫法——在 ER_DB_BACKEND=postgres 下，raw con.execute
的 completed/failed 寫入會打到錯的後端。

本模組把該骨架收斂成單一 context manager，讓不變式集中一處，呼叫端無從漂移：

    with analysis_run(sample_id, "bulk_deg", params=_params,
                      requested_by=..., tool_name="bio_run_deg") as run:
        # run.analysis_id / run.started_at 立即可用（可寫進報告模板）
        ... 計算 / 畫圖 / 組報告 ...
        run.artifact(deg_csv, "csv", "DEG a vs b", "deg_table")
        run.complete(report_path, summary, summary_metrics=metrics)
    return run.analysis_id, str(report_path)

後端分工：A 類 metadata（analysis_history / canonical）一律走 store（backend-agnostic）；
artifact（analysis_artifacts）與 HELIX tools ledger 天生綁 DuckDB VSS/HNSW，故 seam 內另持
一個專用 DuckDB 連線，**絕不**用 store.write_conn() 去 flush artifact（Postgres 後端會 yield
psycopg 連線而爆掉）。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config.settings as _settings
from config.db_utils import connect_db, param_hash
from store.factory import get_store
from analysis.tool_registry import get_active_tool_id
from analysis.failure_diagnosis import classify_exception, success_diagnosis
from analysis.artifact_registry import register_artifact

logger = logging.getLogger(__name__)


class AnalysisRun:
    """一次 Analysis Run 的生命週期 handle 兼 context manager。

    由 :func:`analysis_run` 建立。`__enter__` 開場（uuid + insert running），
    body 內呼叫 :meth:`artifact` 累積產出、:meth:`complete` 設定成功 payload，
    `__exit__` 依三分支收尾（見模組 docstring 與各方法說明）。
    """

    def __init__(
        self,
        sample_id: str,
        analysis_type: str,
        *,
        params: dict,
        requested_by: str = "agent",
        parent_analysis_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        canonical: bool = False,
    ) -> None:
        self.sample_id = sample_id
        self.analysis_type = analysis_type
        self.params = params
        self.requested_by = requested_by
        self.parent_analysis_id = parent_analysis_id
        self.tool_name = tool_name
        # canonical=True 才在成功時 mark_canonical（demote 前一個 canonical）。
        # 多數 analysis type 無 canonical 概念，預設 False 以忠實保留各分析函數既有行為。
        self.canonical = canonical

        self.analysis_id: str = str(uuid.uuid4())
        self.started_at: Optional[datetime] = None

        self._completed: bool = False
        self._result_path: Optional[str] = None
        self._summary: Optional[str] = None
        self._summary_metrics: Optional[dict] = None
        self._artifacts: list[tuple[Path, str, str, Optional[str], Any]] = []
        self._store: Any = None
        self._duck: Any = None

    # ── 開場 ──────────────────────────────────────────────────────────────
    def __enter__(self) -> "AnalysisRun":
        self.started_at = datetime.now(timezone.utc)
        self._store = get_store()
        # 專用 DuckDB 連線：供 HELIX tools ledger 讀 + artifact flush（VSS/HNSW，永遠 DuckDB）
        self._duck = connect_db(_settings.DUCKDB_PATH)

        # tool_id：best-effort 從 HELIX ledger 解析當前 active 版本（tools 表天生 DuckDB）
        tool_id: Optional[str] = None
        if self.tool_name:
            try:
                tool_id = get_active_tool_id(self._duck, self.tool_name)
            except Exception:  # tools 表不存在（隔離測試 DB）或尚未 register — 靜默 no-op
                tool_id = None

        # parent lineage 僅對 canonical 類型有意義（此 run 將取代的當前 canonical）
        if self.canonical and self.parent_analysis_id is None:
            try:
                self.parent_analysis_id = self._store.get_canonical_id(
                    self.sample_id, self.analysis_type
                )
            except Exception:
                self.parent_analysis_id = None

        self._store.insert_history(
            self.analysis_id,
            self.sample_id,
            self.analysis_type,
            json.dumps(self.params),
            "running",
            self.requested_by,
            self.started_at,
            tool_id=tool_id,
            parameter_hash=param_hash(self.params),
        )
        # insert_history 無 parent 欄位；有 lineage 就補一筆 update（超集：忠實於 raw 路徑）
        if self.parent_analysis_id:
            try:
                self._store.update_history(
                    self.analysis_id, parent_analysis_id=self.parent_analysis_id
                )
            except Exception:
                logger.warning("run_context: parent_analysis_id 寫入失敗（非致命）", exc_info=True)
        return self

    # ── body 期間累積 ─────────────────────────────────────────────────────
    def artifact(
        self,
        path: "Path | str",
        artifact_type: str,
        label: str,
        subtype: Optional[str] = None,
        *,
        producing_fn: Any = None,
    ) -> None:
        """登記一個此 Run 的產出檔（延遲到 __exit__ 成功時才真正寫入 analysis_artifacts）。"""
        self._artifacts.append((Path(path), artifact_type, label, subtype, producing_fn))

    def complete(
        self,
        result_path: "Path | str",
        summary: str,
        *,
        summary_metrics: Optional[dict] = None,
    ) -> None:
        """標記成功 payload。必須在 body 正常結束前呼叫，否則 __exit__ 視為程式錯誤（fail-fast）。"""
        self._result_path = str(result_path)
        self._summary = summary
        self._summary_metrics = summary_metrics
        self._completed = True

    # ── 收尾（三分支）────────────────────────────────────────────────────
    def __exit__(self, exc_type, exc, tb) -> bool:
        now = datetime.now(timezone.utc)
        try:
            # 分支 1：body 拋例外 → 標 failed（診斷 = classify_exception）→ re-raise
            if exc is not None:
                self._safe_fail(now, classify_exception(exc))
                return False  # 不吞例外，原例外照常往外拋

            # 分支 3：未 complete 又無例外 → fail-fast，杜絕殭屍 running
            if not self._completed:
                self._safe_fail(now, {"type": "failed", "detail": "run_not_completed"})
                raise RuntimeError(
                    f"analysis_run({self.analysis_type}) 結束但未呼叫 complete()："
                    "既未成功也未拋例外，該筆已標為 failed"
                )

            # 分支 2：成功收尾
            self._store.complete_history(
                self.analysis_id,
                self._result_path,
                self._summary,
                now,
                summary_metrics=self._summary_metrics,
            )
            if self.canonical:
                self._store.mark_canonical(self.analysis_id, self.sample_id, self.analysis_type)
            try:
                self._store.update_history(
                    self.analysis_id, failure_diagnosis=json.dumps(success_diagnosis())
                )
            except Exception:
                logger.warning("run_context: success diagnosis 寫入失敗（非致命）", exc_info=True)
            self._flush_artifacts()
            try:
                from scripts.export_registry import export_snapshot

                export_snapshot()
            except Exception as e:
                logger.warning("run_context: export_snapshot 失敗（非致命）: %s", e)
            return False
        finally:
            try:
                self._duck.close()
            except Exception:
                pass

    # ── 內部 helper ───────────────────────────────────────────────────────
    def _safe_fail(self, now: datetime, diagnosis: dict) -> None:
        try:
            self._store.fail_history(
                self.analysis_id, now, failure_diagnosis=json.dumps(diagnosis)
            )
        except Exception:
            logger.warning("run_context: fail_history 寫入失敗（非致命）", exc_info=True)

    def _flush_artifacts(self) -> None:
        for path, atype, label, subtype, producing_fn in self._artifacts:
            try:
                if path.exists():
                    register_artifact(
                        self._duck, self.analysis_id, path, atype, label,
                        artifact_subtype=subtype, producing_fn=producing_fn,
                    )
            except Exception as e:
                logger.warning(
                    "run_context: register_artifact 失敗（非致命）path=%s: %s", path, e
                )


def analysis_run(
    sample_id: str,
    analysis_type: str,
    *,
    params: dict,
    requested_by: str = "agent",
    parent_analysis_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    canonical: bool = False,
) -> AnalysisRun:
    """建立一次 Analysis Run 的 context manager。介面契約見模組 docstring。"""
    return AnalysisRun(
        sample_id,
        analysis_type,
        params=params,
        requested_by=requested_by,
        parent_analysis_id=parent_analysis_id,
        tool_name=tool_name,
        canonical=canonical,
    )


def record_completed_run(
    sample_id: str,
    analysis_type: str,
    *,
    params: dict,
    result_path: "Path | str",
    summary: str,
    requested_by: str = "agent",
    tool_name: Optional[str] = None,
    canonical: bool = False,
    artifacts: Optional[list[tuple]] = None,
    producing_fn: Any = None,
) -> str:
    """一次性記錄一個「已完成」的 Analysis Run，回傳 analysis_id。

    適用讀取型分析（先做完唯讀查詢/繪圖，再一次記錄，無 running 中間態）。
    內部即是 :func:`analysis_run` 的薄包裝——所有 history 寫入仍走同一個 seam 與 store，
    維持單一 locality。`artifacts` 為 (path, artifact_type, label, subtype) 的清單。
    """
    with analysis_run(
        sample_id,
        analysis_type,
        params=params,
        requested_by=requested_by,
        tool_name=tool_name,
        canonical=canonical,
    ) as run:
        for path, atype, label, subtype in artifacts or []:
            run.artifact(path, atype, label, subtype, producing_fn=producing_fn)
        run.complete(result_path, summary)
    return run.analysis_id
