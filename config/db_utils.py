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


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """取得 bio_memory.duckdb 連線（寫入模式為單例）。"""
    global _con
    if read_only:
        return duckdb.connect(str(DUCKDB_PATH), read_only=True)
    if _con is None:
        _con = duckdb.connect(str(DUCKDB_PATH))
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
    cleaned = con.execute(
        f"""
        SELECT COUNT(*) FROM analysis_history
        WHERE status = 'running'
          AND started_at < now() - INTERVAL '{hours} hours'
        """
    ).fetchone()[0]

    if cleaned:
        con.execute(
            f"""
            UPDATE analysis_history
            SET    status = 'stale'
            WHERE  status  = 'running'
              AND  started_at < now() - INTERVAL '{hours} hours'
            """
        )
    if cleaned:
        con.execute("CHECKPOINT")
        print(f"[db_utils] cleaned {cleaned} stale running record(s)")
    return cleaned


def db_health_check(con: duckdb.DuckDBPyConnection) -> dict:
    """
    快速健康檢查，回傳各表筆數與殭屍狀態統計。
    Agent 啟動時可選擇性呼叫。
    """
    result = {}

    result["sample_count"] = con.execute(
        "SELECT COUNT(*) FROM sample_registry"
    ).fetchone()[0]

    result["history_count"] = con.execute(
        "SELECT COUNT(*) FROM analysis_history"
    ).fetchone()[0]

    result["stale_count"] = con.execute(
        "SELECT COUNT(*) FROM analysis_history WHERE status = 'stale'"
    ).fetchone()[0]

    result["running_count"] = con.execute(
        "SELECT COUNT(*) FROM analysis_history WHERE status = 'running'"
    ).fetchone()[0]

    result["l2_ready_count"] = con.execute(
        "SELECT COUNT(*) FROM sample_registry WHERE l2_ready = TRUE"
    ).fetchone()[0]

    return result


if __name__ == "__main__":
    con = get_connection()
    cleanup_stale_runs(con)
    health = db_health_check(con)
    print("[health]", health)
    con.close()
