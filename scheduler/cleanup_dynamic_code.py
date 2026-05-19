"""清理 dynamic_code 歸檔目錄（預設保留 90 天）。

`bio_execute_code` 每次執行會在 DYNAMIC_CODE_DIR 留下一個目錄含
code.py / output.txt / traceback.txt / meta.json / fig_*.png。
時間久了磁碟會長大，由本腳本依 meta.json 中的 created_at 清理。

analysis_history 不動 — 該表永久保存（CLAUDE.md §2 鐵律）；
歷史記錄保留 result_path 字串，但實體目錄已刪。讀回時 bio_read_report
會 raise FileNotFoundError，由呼叫端判斷。
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DYNAMIC_CODE_DIR  # noqa: E402

logger = logging.getLogger(__name__)


def cleanup_old_archives(days: int = 90) -> int:
    """刪除 created_at 早於 N 天前的歸檔目錄，回傳刪除數量。"""
    if not DYNAMIC_CODE_DIR.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0

    for entry in DYNAMIC_CODE_DIR.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(meta["created_at"])
        except (ValueError, KeyError, json.JSONDecodeError):
            logger.warning("跳過無法解析的歸檔：%s", entry)
            continue
        if created_at < cutoff:
            shutil.rmtree(entry)
            removed += 1
            logger.info(
                "已刪除過期歸檔：%s (created_at=%s)", entry.name, created_at.isoformat()
            )

    return removed


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="清理 dynamic_code 歸檔目錄")
    parser.add_argument("--days", type=int, default=90, help="保留天數（預設 90）")
    parser.add_argument("--dry-run", action="store_true", help="只列出將刪除的目錄")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        if not DYNAMIC_CODE_DIR.exists():
            print(f"DYNAMIC_CODE_DIR 不存在：{DYNAMIC_CODE_DIR}")
            sys.exit(0)
        candidates = []
        for entry in DYNAMIC_CODE_DIR.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created_at = datetime.fromisoformat(meta["created_at"])
            except Exception:
                continue
            if created_at < cutoff:
                candidates.append((entry.name, created_at.isoformat()))
        print(f"[dry-run] 將刪除 {len(candidates)} 個目錄（cutoff = {cutoff.isoformat()}）：")
        for name, ts in candidates:
            print(f"  {name}  (created_at={ts})")
    else:
        n = cleanup_old_archives(days=args.days)
        print(f"已刪除 {n} 個過期歸檔（保留 {args.days} 天內）")
