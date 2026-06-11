#!/usr/bin/env python3
"""一次性回填：為現有 bulk_rnaseq 樣本補齊 condition / time_point / batch / tissue / project。

在已有 Kallisto 樣本但尚未填入實驗元數據的舊資料庫上執行一次。
--dry-run 模式只印出解析結果，不寫入 DB。

使用方式：
    uv run python scripts/02b_backfill_sample_metadata.py
    uv run python scripts/02b_backfill_sample_metadata.py --dry-run
    uv run python scripts/02b_backfill_sample_metadata.py --experiment-name hair_follicle_exp2
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH
from config.db_utils import open_db, safe_write

logger = logging.getLogger(__name__)


def _get_parse_fn():
    """取得解析函數（直接從 01_register_sample 模組載入）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "register_sample",
        Path(__file__).parent / "01_register_sample.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.parse_kallisto_sample_metadata


def backfill(
    con: duckdb.DuckDBPyConnection,
    experiment_name: str = "hair_follicle_exp1",
    *,
    dry_run: bool = False,
) -> None:
    """回填所有 bulk_rnaseq 樣本的元數據。

    只更新 condition / time_point / batch / tissue / project 均為空的欄位，
    避免覆蓋已手動填入的值。
    """
    parse_fn = _get_parse_fn()

    rows = con.execute(
        """
        SELECT sample_id, project, condition, time_point, batch, tissue
        FROM   sample_registry
        WHERE  data_type = 'bulk_rnaseq'
        ORDER  BY sample_id
        """
    ).fetchall()

    updated = skipped_no_parse = skipped_already_filled = 0

    for sample_id, project, condition, time_point, batch, tissue in rows:
        # 跳過已填入的
        if condition and time_point and batch and tissue:
            skipped_already_filled += 1
            logger.debug("已填入，跳過：%s", sample_id)
            continue

        meta = parse_fn(sample_id)
        if not meta:
            skipped_no_parse += 1
            logger.warning("無法解析樣本名稱：%s  → 跳過", sample_id)
            continue

        new_project = project if project and project != "Kallisto_v1" else experiment_name
        new_condition = condition or meta["condition"]
        new_time_point = time_point or meta["time_point"]
        new_batch = batch or meta["batch"]
        new_tissue = tissue or meta["tissue"]

        logger.info(
            "  %s → project=%-22s  cond=%-8s  tp=%-6s  batch=%-5s  tissue=%s",
            sample_id, new_project, new_condition, new_time_point, new_batch, new_tissue,
        )

        if not dry_run:
            safe_write(
                con,
                """
                UPDATE sample_registry
                SET    project    = ?,
                       condition  = ?,
                       time_point = ?,
                       batch      = ?,
                       tissue     = ?,
                       last_updated = now()
                WHERE  sample_id  = ?
                """,
                [new_project, new_condition, new_time_point, new_batch, new_tissue, sample_id],
            )
        updated += 1

    action = "（dry-run，未寫入）" if dry_run else ""
    logger.info(
        "回填完成%s：更新 %d 筆，已填跳過 %d 筆，無法解析跳過 %d 筆",
        action, updated, skipped_already_filled, skipped_no_parse,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回填 bulk_rnaseq 樣本元數據")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只印出解析結果，不寫入 DB",
    )
    p.add_argument(
        "--experiment-name",
        default="hair_follicle_exp1",
        metavar="NAME",
        help="project 欄位名稱（預設：hair_follicle_exp1）",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s — %(message)s",
    )
    args = _parse_args()

    with open_db(DUCKDB_PATH) as con:
        backfill(con, experiment_name=args.experiment_name, dry_run=args.dry_run)

        # 驗證結果
        summary = con.execute(
            """
            SELECT project, condition, tissue, COUNT(*) AS n
            FROM   sample_registry
            WHERE  data_type = 'bulk_rnaseq'
            GROUP  BY project, condition, tissue
            ORDER  BY project, condition, tissue
            """
        ).fetchall()
        logger.info("\n=== 回填後分佈 ===")
        for proj, cond, tiss, n in summary:
            logger.info("  %-26s  %-10s  %-20s  n=%d", proj, cond or "(空)", tiss or "(空)", n)


if __name__ == "__main__":
    main()
