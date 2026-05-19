"""
每日備份 bio_memory.duckdb 至 APFS（~/bio_db_backups/）。

執行：
    uv run python scheduler/backup_db.py

排程（macOS launchd）：
    每日 02:00 自動執行，見 docs/launchd_backup.plist.example

備份格式：DuckDB EXPORT DATABASE（CSV + schema.sql），可直接 IMPORT 還原。
保留策略：最近 7 天，超過自動刪除。

失敗偵測：
- 備份目錄總大小 < MIN_BACKUP_BYTES 視為失敗（刪除空目錄、exit code 1）
- 寫入 logs/backup_status.json 紀錄 last_success_at / last_failure_at / last_size_bytes
- 健康檢查端點可讀此 JSON 判斷備份新鮮度
"""

import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

BACKUP_ROOT = Path.home() / "bio_db_backups"
KEEP_DAYS = 7
MIN_BACKUP_BYTES = 100 * 1024  # 100 KB 以下視為失敗（空目錄或僅 schema）
STATUS_PATH = Path(__file__).parent.parent / "logs" / "backup_status.json"

logger = logging.getLogger("backup_db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _dir_size_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _write_status(success: bool, dest: Path | None, size_bytes: int, error: str | None) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    prev: dict = {}
    if STATUS_PATH.exists():
        try:
            prev = json.loads(STATUS_PATH.read_text())
        except json.JSONDecodeError:
            prev = {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    status = {
        "last_run_at": now_iso,
        "last_success_at": now_iso if success else prev.get("last_success_at"),
        "last_failure_at": prev.get("last_failure_at") if success else now_iso,
        "last_dest": str(dest) if dest else prev.get("last_dest"),
        "last_size_bytes": size_bytes,
        "last_error": None if success else error,
    }
    STATUS_PATH.write_text(json.dumps(status, indent=2))


def backup() -> Path:
    today = datetime.now().strftime("%Y%m%d_%H%M")
    dest = BACKUP_ROOT / today
    dest.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        # EXPORT DATABASE does not support ? placeholder — use f-string with safe path
        con.execute(f"EXPORT DATABASE '{str(dest)}'")

    size_bytes = _dir_size_bytes(dest)
    size_mb = size_bytes / 1024**2

    if size_bytes < MIN_BACKUP_BYTES:
        # 空備份或僅 schema — 刪除目錄，回報失敗
        shutil.rmtree(dest, ignore_errors=True)
        err = f"backup size {size_bytes} B < {MIN_BACKUP_BYTES} B threshold"
        logger.error("[backup] %s — removed %s", err, dest.name)
        _write_status(False, dest, size_bytes, err)
        raise RuntimeError(err)

    logger.info("[backup] %s  %.1f MB", dest.name, size_mb)
    _write_status(True, dest, size_bytes, None)
    return dest


def prune_old_backups() -> None:
    if not BACKUP_ROOT.exists():
        return
    dirs = sorted(
        d for d in BACKUP_ROOT.iterdir()
        if d.is_dir() and re.match(r"^\d{8}_\d{4}$", d.name)
    )
    to_delete = dirs[:-KEEP_DAYS] if len(dirs) > KEEP_DAYS else []
    for old in to_delete:
        shutil.rmtree(old)
        logger.info("[backup] pruned %s", old.name)


def prune_empty_backups() -> int:
    """清除既有 0-byte 或低於門檻的歷史備份目錄。回傳刪除數。"""
    if not BACKUP_ROOT.exists():
        return 0
    removed = 0
    for d in BACKUP_ROOT.iterdir():
        if not d.is_dir() or not re.match(r"^\d{8}_\d{4}$", d.name):
            continue
        if _dir_size_bytes(d) < MIN_BACKUP_BYTES:
            shutil.rmtree(d)
            logger.info("[backup] removed empty/undersized %s", d.name)
            removed += 1
    return removed


def restore(backup_dir: Path) -> None:
    """緊急還原：從備份目錄匯入至 bio_memory.duckdb。還原前自動備份現有 DB。"""
    if DUCKDB_PATH.exists():
        pre = BACKUP_ROOT / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pre.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as _con:
            _con.execute(f"EXPORT DATABASE '{str(pre)}'")
        logger.info("[restore] pre-restore backup saved to %s", pre.name)

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        con.execute(f"IMPORT DATABASE '{str(backup_dir)}'")
    logger.info("[restore] done from %s", backup_dir)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--restore":
        dirs = sorted(
            d for d in BACKUP_ROOT.iterdir()
            if d.is_dir() and re.match(r"^\d{8}_\d{4}$", d.name)
        )
        if not dirs:
            logger.error("No backups found.")
            sys.exit(1)
        latest = dirs[-1]
        logger.info("Restoring from: %s", latest)
        restore(latest)
    elif len(sys.argv) == 2 and sys.argv[1] == "--prune-empty":
        n = prune_empty_backups()
        logger.info("[backup] removed %d empty backup dirs", n)
    else:
        try:
            backup()
            prune_old_backups()
            logger.info("[backup] complete.")
        except Exception as exc:
            logger.exception("[backup] FAILED: %s", exc)
            _write_status(False, None, 0, str(exc))
            sys.exit(1)
