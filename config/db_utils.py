"""
DuckDB 安全寫入與維護工具。

safe_write()        — 寫入關鍵表後立即 CHECKPOINT（對抗 ExFAT 無日誌風險）
cleanup_stale_runs() — 清理 > 24h 的殭屍 running 狀態
get_connection()    — 統一連線入口，確保單一寫入者
"""

import duckdb
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

# 模組級單例連線（同程序內共用，避免多連線衝突）
_con: duckdb.DuckDBPyConnection | None = None


def _bootstrap_vss(con: duckdb.DuckDBPyConnection, read_only: bool = False) -> None:
    """Load VSS extension and enable HNSW persistence on every new connection (SQL-10).

    Centralised here so callers never need to repeat LOAD vss / SET statements.
    Silently skipped when VSS is unavailable (in-memory test DBs, missing extension).
    SET hnsw_enable_experimental_persistence is skipped for read_only connections —
    DuckDB silently ignores or errors on SET in read_only mode.
    """
    try:
        con.execute("LOAD vss")
    except Exception:
        return
    if not read_only:
        try:
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception:
            pass


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """取得 bio_memory.duckdb 連線（寫入模式為單例）。"""
    global _con
    if read_only:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        _bootstrap_vss(con, read_only=True)
        return con
    if _con is None:
        _con = duckdb.connect(str(DUCKDB_PATH))
        _bootstrap_vss(_con)
    return _con


def safe_write(con: duckdb.DuckDBPyConnection, sql: str, params: list = None) -> None:
    """
    執行寫入並立即 CHECKPOINT。

    只用於 analysis_history、sample_registry 等關鍵表的寫入。
    L1 memory_recent 等快取寫入不需要呼叫（效能考量）。

    ExFAT 無日誌系統，CHECKPOINT 強制把 WAL 刷入主檔，
    縮小斷電損壞視窗：損壞頂多丟失「上次 CHECKPOINT 之後的寫入」。
    """
    con.execute(sql, params or [])
    con.execute("CHECKPOINT")


def cleanup_stale_runs(con: duckdb.DuckDBPyConnection, hours: int = 24) -> int:
    """
    清理因程序中斷而卡在 running 的分析記錄。

    Agent 每次啟動時呼叫。超過 `hours` 小時仍為 running 的記錄
    標記為 stale，不刪除（保留 debug 依據）。

    Returns:
        清理筆數
    """
    hours = int(hours)
    cleaned = con.execute(
        """
        SELECT COUNT(*) FROM analysis_history
        WHERE status = 'running'
          AND started_at < now() - (? * INTERVAL '1 hour')
        """,
        [hours],
    ).fetchone()[0]

    if cleaned:
        con.execute(
            """
            UPDATE analysis_history
            SET    status = 'stale'
            WHERE  status  = 'running'
              AND  started_at < now() - (? * INTERVAL '1 hour')
            """,
            [hours],
        )
    if cleaned:
        con.execute("CHECKPOINT")
        print(f"[db_utils] cleaned {cleaned} stale running record(s)")
    return cleaned


def db_health_check(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """
    快速健康檢查，回傳各表筆數與殭屍狀態統計。
    Agent 啟動時可選擇性呼叫。con 可選：未傳入時自動開啟 read_only 連線。
    """
    def _run(c: duckdb.DuckDBPyConnection) -> dict:
        result = {}
        result["sample_count"] = c.execute(
            "SELECT COUNT(*) FROM sample_registry"
        ).fetchone()[0]
        result["history_count"] = c.execute(
            "SELECT COUNT(*) FROM analysis_history"
        ).fetchone()[0]
        result["stale_count"] = c.execute(
            "SELECT COUNT(*) FROM analysis_history WHERE status = 'stale'"
        ).fetchone()[0]
        result["running_count"] = c.execute(
            "SELECT COUNT(*) FROM analysis_history WHERE status = 'running'"
        ).fetchone()[0]
        result["l2_ready_count"] = c.execute(
            "SELECT COUNT(*) FROM sample_registry WHERE l2_ready = TRUE"
        ).fetchone()[0]
        return result

    if con is not None:
        return _run(con)

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as _con:
        return _run(_con)


if __name__ == "__main__":
    con = get_connection()
    cleanup_stale_runs(con)
    health = db_health_check(con)
    print("[health]", health)
    con.close()
