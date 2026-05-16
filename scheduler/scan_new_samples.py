#!/usr/bin/env python3
"""排程任務：掃描 results_kallisto/ 目錄並自動登記新樣本。

由 launchd 每 30 分鐘觸發（見 docs/launchd_scan_samples.plist.example）。
亦可手動執行：
    ~/.venvs/hermes-bio-memory/bin/python scheduler/scan_new_samples.py
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH
from scripts.register_sample import scan_bulk_rna

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("=== 掃描新樣本 開始 ===")
    t0 = time.monotonic()

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        scan_bulk_rna(con, added_by="scheduler")

    elapsed = time.monotonic() - t0
    logger.info("=== 掃描完成（%.1f 秒）===", elapsed)


if __name__ == "__main__":
    main()
