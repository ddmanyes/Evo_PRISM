"""
每週備份還原測試。

流程：
1. 找出最新的 ~/bio_db_backups/<YYYYMMDD_HHMM>/ 目錄
2. IMPORT 至暫存 /tmp/bio_memory_verify.duckdb
3. 跑 db_health_check 驗證 sample_count > 0 且 history_count > 0
4. 把結果寫至 logs/restore_test_status.json
5. 失敗時 exit code 非零（讓 launchd 紀錄錯誤）

排程：每週日 05:00（避開 04:00 helix_expire）
"""

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH, BACKUP_ROOT

VERIFY_DB = Path("/tmp/bio_memory_verify.duckdb")
STATUS_PATH = Path(__file__).parent.parent / "logs" / "restore_test_status.json"

logger = logging.getLogger("weekly_restore_test")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _latest_backup() -> Path | None:
    if not BACKUP_ROOT.exists():
        return None
    dirs = sorted(
        d for d in BACKUP_ROOT.iterdir() if d.is_dir() and re.match(r"^\d{8}_\d{4}$", d.name)
    )
    return dirs[-1] if dirs else None


def _write_status(success: bool, backup: Path | None, stats: dict, error: str | None) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    prev: dict = {}
    if STATUS_PATH.exists():
        try:
            prev = json.loads(STATUS_PATH.read_text())
        except json.JSONDecodeError:
            prev = {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    STATUS_PATH.write_text(
        json.dumps(
            {
                "last_run_at": now_iso,
                "last_success_at": now_iso if success else prev.get("last_success_at"),
                "last_failure_at": prev.get("last_failure_at") if success else now_iso,
                "last_backup_tested": str(backup) if backup else prev.get("last_backup_tested"),
                "last_stats": stats,
                "last_error": None if success else error,
            },
            indent=2,
        )
    )


def _cleanup_verify_db() -> None:
    for p in (VERIFY_DB, VERIFY_DB.with_suffix(VERIFY_DB.suffix + ".wal")):
        if p.exists():
            p.unlink()


def run_restore_test() -> dict:
    latest = _latest_backup()
    if latest is None:
        err = "no backup found under ~/bio_db_backups"
        logger.error(err)
        _write_status(False, None, {}, err)
        raise RuntimeError(err)

    logger.info("Testing restore from %s", latest)
    _cleanup_verify_db()

    try:
        with duckdb.connect(str(VERIFY_DB)) as con:
            # HNSW 索引由 vss extension 提供，IMPORT schema 前必須先載入
            con.execute("INSTALL vss")
            con.execute("LOAD vss")
            # 主庫已啟用此選項；驗證庫匯入 HNSW 索引也需要
            con.execute("SET hnsw_enable_experimental_persistence = true")
            con.execute(f"IMPORT DATABASE '{str(latest)}'")
            stats = {
                "sample_count": con.execute("SELECT COUNT(*) FROM sample_registry").fetchone()[0],
                "history_count": con.execute("SELECT COUNT(*) FROM analysis_history").fetchone()[0],
            }
    except Exception as exc:
        err = f"IMPORT DATABASE failed: {type(exc).__name__}: {exc}"
        logger.error(err)
        _write_status(False, latest, {}, err)
        _cleanup_verify_db()
        raise

    if stats["sample_count"] <= 0 or stats["history_count"] <= 0:
        # 對照主庫，看是否本來就是空（避免誤報）
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as live:
            live_samples = live.execute("SELECT COUNT(*) FROM sample_registry").fetchone()[0]
        if live_samples > 0:
            err = f"restored DB has zero rows but live has {live_samples}: {stats}"
            logger.error(err)
            _write_status(False, latest, stats, err)
            raise RuntimeError(err)
        logger.warning("restored DB empty; live DB also empty — not raising")

    logger.info("[restore-test] OK %s", stats)
    _write_status(True, latest, stats, None)
    _cleanup_verify_db()
    return stats


if __name__ == "__main__":
    try:
        run_restore_test()
    except Exception as exc:
        logger.exception("[restore-test] FAILED: %s", exc)
        sys.exit(1)
