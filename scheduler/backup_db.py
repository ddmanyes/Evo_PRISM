"""
每日備份 bio_memory.duckdb 至 APFS（~/bio_db_backups/）。

執行：
    uv run python scheduler/backup_db.py

排程（macOS launchd）：
    每日 02:00 自動執行，見 docs/launchd_backup.plist.example

備份格式：DuckDB EXPORT DATABASE（CSV + schema.sql），可直接 IMPORT 還原。
保留策略：最近 7 天，超過自動刪除。
"""

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

BACKUP_ROOT = Path.home() / "bio_db_backups"
KEEP_DAYS   = 7


def backup() -> Path:
    today = datetime.now().strftime("%Y%m%d_%H%M")
    dest  = BACKUP_ROOT / today
    dest.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        con.execute("EXPORT DATABASE ?", [str(dest)])

    size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1024**2
    print(f"[backup] {dest.name}  {size_mb:.1f} MB")
    return dest


def prune_old_backups():
    if not BACKUP_ROOT.exists():
        return
    dirs = sorted(d for d in BACKUP_ROOT.iterdir() if d.is_dir())
    to_delete = dirs[:-KEEP_DAYS] if len(dirs) > KEEP_DAYS else []
    for old in to_delete:
        shutil.rmtree(old)
        print(f"[backup] pruned {old.name}")


def restore(backup_dir: Path):
    """緊急還原：從備份目錄匯入至 bio_memory.duckdb。還原前自動備份現有 DB。"""
    # 還原前先把現有 DB 備份，避免誤操作覆蓋
    if DUCKDB_PATH.exists():
        pre = BACKUP_ROOT / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pre.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as _con:
            _con.execute("EXPORT DATABASE ?", [str(pre)])
        print(f"[restore] pre-restore backup saved to {pre.name}")

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        con.execute("IMPORT DATABASE ?", [str(backup_dir)])
    print(f"[restore] done from {backup_dir}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--restore":
        dirs = sorted(
            d for d in BACKUP_ROOT.iterdir()
            if d.is_dir() and re.match(r'^\d{8}_\d{4}$', d.name)
        )
        if not dirs:
            print("No backups found.")
            sys.exit(1)
        latest = dirs[-1]
        print(f"Restoring from: {latest}")
        restore(latest)
    else:
        backup()
        prune_old_backups()
        print("[backup] complete.")
