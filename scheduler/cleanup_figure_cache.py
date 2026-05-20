"""
定期清理 figure cache（gold/figure_cache/）中過期的圖檔。

背景：
    MCP call_tool 出口會把報告 inline base64 圖剝離成佔位符，並把原圖快取於
    gold/figure_cache/<id>.<ext> 供 bio_get_figure 索取（見 analysis/figure_cache.py）。
    這些檔案是 content-addressed 副本——過期清掉後，若報告重跑會以相同 id 自動重建，
    因此依 mtime age-based 刪除是安全的，不影響 analysis_history / result_path 的原始 png。

設計原則：
    - TTL 預設 14 天（settings.FIGURE_CACHE_TTL_DAYS，可由 env 覆蓋）
    - 只刪 figure cache 檔；絕不碰 result_path 下的原始輸出
    - 與 cleanup_l1_cache.py 同調，建議 launchd 每日 03:30 一併執行

執行：
    python scheduler/cleanup_figure_cache.py
    python scheduler/cleanup_figure_cache.py --dry-run   # 只印不刪
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from analysis.figure_cache import FIGURE_CACHE_DIR, prune_stale_figures
from config.settings import FIGURE_CACHE_TTL_DAYS


def _fmt_mb(n_bytes: int) -> str:
    return f"{n_bytes / 1_048_576:.1f} MB"


def cleanup(*, dry_run: bool = False, ttl_days: int | None = None) -> int:
    """刪除 mtime 超過 ttl_days 的 figure cache 檔，回傳刪除筆數。"""
    days = ttl_days if ttl_days is not None else FIGURE_CACHE_TTL_DAYS
    today = datetime.now(timezone.utc).date()

    if not FIGURE_CACHE_DIR.exists():
        print(f"[cleanup_figcache] Cache dir not found: {FIGURE_CACHE_DIR}")
        return 0

    deleted, freed = prune_stale_figures(days, dry_run=dry_run)
    if deleted == 0:
        print(f"[cleanup_figcache] Nothing to clean (>{days}d) ({today})")
        return 0

    verb = "would delete" if dry_run else "Deleted"
    print(f"[cleanup_figcache] {verb} {deleted} figures, {_fmt_mb(freed)} (>{days}d) ({today})")
    return deleted


def stats() -> dict:
    """回傳 figure cache 目前狀態（檔數、總大小）。"""
    if not FIGURE_CACHE_DIR.exists():
        return {"exists": False}
    files = [f for f in FIGURE_CACHE_DIR.iterdir() if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    return {"exists": True, "total_files": len(files), "total_bytes": total}


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    s = stats()
    print(f"[cleanup_figcache] cache stats: {s}")
    cleanup(dry_run=dry_run)
