#!/usr/bin/env python3
"""孤兒結果檔案 GC（Garbage Collection）。

掃描 results/ 目錄，找出在 analysis_history.result_path 和
analysis_artifacts.file_path 中均無記錄的「孤兒」檔案。

使用方式：
    uv run python scripts/gc_orphan_results.py              # 只掃描，不刪除
    uv run python scripts/gc_orphan_results.py --delete     # 刪除孤兒檔案
    uv run python scripts/gc_orphan_results.py --min-days 7 # 只處理 7 天以上的孤兒

孤兒判定：
    result_path (analysis_history) — 絕對路徑，直接比對。
    file_path   (analysis_artifacts) — 相對 BIO_DB_ROOT（v12 migration），轉成絕對後比對。
    兩者皆無記錄的檔案 → 孤兒。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from itertools import groupby
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.db_utils import open_db
from config.settings import BIO_DB_ROOT, DUCKDB_PATH

logger = logging.getLogger(__name__)


def _load_referenced(con) -> set[Path]:
    """Return resolved absolute Paths of all DB-referenced result files."""
    referenced: set[Path] = set()

    rows = con.execute(
        "SELECT result_path FROM analysis_history WHERE result_path IS NOT NULL"
    ).fetchall()
    for (rp,) in rows:
        referenced.add(Path(rp).resolve())

    try:
        rows = con.execute(
            "SELECT file_path FROM analysis_artifacts WHERE file_path IS NOT NULL"
        ).fetchall()
        for (fp,) in rows:
            p = Path(fp)
            if not p.is_absolute():
                p = BIO_DB_ROOT / p
            referenced.add(p.resolve())
    except Exception as exc:
        logger.warning("Could not read analysis_artifacts.file_path: %s", exc)

    return referenced


def scan_orphans(
    *,
    min_age_days: float = 0,
    results_root: Path | None = None,
) -> list[dict]:
    """Return list of orphan file dicts with path / size_bytes / age_days."""
    root = (results_root or BIO_DB_ROOT / "results").resolve()
    if not root.exists():
        logger.info("results/ 目錄不存在：%s", root)
        return []

    with open_db(DUCKDB_PATH) as con:
        referenced = _load_referenced(con)

    now = time.time()
    orphans: list[dict] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue

        age_days = (now - stat.st_mtime) / 86400
        if age_days < min_age_days:
            continue

        if path.resolve() not in referenced:
            orphans.append({
                "path": path,
                "size_bytes": stat.st_size,
                "age_days": round(age_days, 1),
            })

    return orphans


def _fmt(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes //= 1024
    return f"{n_bytes:.1f} TB"


def run(*, delete: bool = False, min_age_days: float = 0) -> None:
    orphans = scan_orphans(min_age_days=min_age_days)

    if not orphans:
        logger.info("無孤兒檔案（results/ 下所有檔案均有 DB 記錄）")
        return

    total_bytes = sum(o["size_bytes"] for o in orphans)
    logger.info(
        "發現 %d 個孤兒檔案  共 %s（age_filter ≥ %.0f 天）",
        len(orphans), _fmt(total_bytes), min_age_days,
    )

    orphans.sort(key=lambda o: str(o["path"].parent))
    for parent, group in groupby(orphans, key=lambda o: o["path"].parent):
        items = list(group)
        dir_bytes = sum(i["size_bytes"] for i in items)
        try:
            rel = parent.relative_to(BIO_DB_ROOT)
        except ValueError:
            rel = parent
        action = "WILL DELETE" if delete else "orphan"
        print(f"\n  {rel}/  [{len(items)} 個, {_fmt(dir_bytes)}]")
        for o in items:
            print(f"    [{action}]  {o['path'].name}  {_fmt(o['size_bytes'])}  age={o['age_days']}d")

    if not delete:
        print(f"\n合計：{len(orphans)} 個孤兒，可回收 {_fmt(total_bytes)}。")
        print("加上 --delete 執行刪除。")
        return

    deleted = errors = 0
    for o in orphans:
        try:
            o["path"].unlink()
            deleted += 1
            logger.info("已刪除：%s", o["path"])
        except OSError as exc:
            logger.warning("刪除失敗 %s：%s", o["path"], exc)
            errors += 1

    # 清理空目錄（由深到淺）
    root = (BIO_DB_ROOT / "results").resolve()
    for dirpath in sorted(root.rglob("*"), reverse=True):
        if dirpath.is_dir():
            try:
                dirpath.rmdir()
            except OSError:
                pass

    logger.info("刪除 %d 個，失敗 %d 個。", deleted, errors)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="掃描並清理孤兒 results/ 檔案")
    p.add_argument("--delete", action="store_true", help="實際刪除（預設只掃描）")
    p.add_argument(
        "--min-days",
        type=float,
        default=0,
        metavar="N",
        help="只處理修改時間超過 N 天的孤兒（預設 0 = 全部）",
    )
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
    args = _parse_args()
    run(delete=args.delete, min_age_days=args.min_days)
