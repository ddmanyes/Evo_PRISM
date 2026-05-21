#!/usr/bin/env python3
"""登記單一樣本至 sample_registry。

使用方式（單筆登記）：
    python scripts/01_register_sample.py \\
        --sample-id hair_ctrl_1 \\
        --data-type bulk_rnaseq \\
        --platform kallisto \\
        --l3-path /mnt/space4/BulkRNA/raw/ctrl_1 \\
        [--project HairFollicle] \\
        [--species mouse] \\
        [--tissue skin] \\
        [--notes "ctrl 第一批"] \\
        [--added-by zhanqiru] \\
        [--l2-ready]

批次掃描（自動登記 results_kallisto/ 下所有樣本）：
    python scripts/01_register_sample.py --scan-bulk-rna
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT, DUCKDB_PATH
from config.db_utils import safe_write

logger = logging.getLogger(__name__)

BULK_RESULTS_DIR = BIO_DB_ROOT / "bulk_rna_data" / "Kallisto_v1" / "results_kallisto"

VALID_DATA_TYPES = {
    "visium_hd",
    "visium",
    "scrna",
    "bulk_rnaseq",
    "multiome",
    "atac",
    "proteomics",
    "imaging",
    "other",
}
VALID_PLATFORMS = {
    "10x_visium_hd",
    "cellranger",
    "kallisto",
    "salmon",
    "cellranger_arc",
    "snapatac2",
    "maxquant",
    "other",
}


def _sample_exists(con: duckdb.DuckDBPyConnection, sample_id: str) -> bool:
    return (
        con.execute("SELECT 1 FROM sample_registry WHERE sample_id = ?", [sample_id]).fetchone()
        is not None
    )


def register_sample(
    con: duckdb.DuckDBPyConnection,
    sample_id: str,
    data_type: str,
    platform: str,
    l3_path: str,
    project: str = "",
    species: str = "mouse",
    tissue: str = "",
    notes: str = "",
    added_by: str = "script",
    l2_ready: bool = False,
) -> bool:
    """將單一樣本寫入 sample_registry，已存在則跳過。回傳是否新增成功。"""
    if _sample_exists(con, sample_id):
        logger.info("樣本 %r 已存在，跳過", sample_id)
        return False

    safe_write(
        con,
        """INSERT INTO sample_registry
               (sample_id, project, data_type, platform, species, tissue,
                l3_path, l2_ready, analysis_done, added_by, notes, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, false, ?, ?, ?)""",
        [
            sample_id,
            project,
            data_type,
            platform,
            species,
            tissue,
            l3_path,
            l2_ready,
            added_by,
            notes,
            datetime.now(timezone.utc),
        ],
    )
    logger.info("已登記樣本 %r  data_type=%s  platform=%s", sample_id, data_type, platform)
    return True


def scan_bulk_rna(con: duckdb.DuckDBPyConnection, added_by: str = "scan") -> None:
    """掃描 results_kallisto/ 下所有子目錄，自動登記尚未存在的樣本。"""
    if not BULK_RESULTS_DIR.exists():
        logger.error("Bulk RNA 結果目錄不存在：%s", BULK_RESULTS_DIR)
        return

    inserted = skipped = 0
    for sample_dir in sorted(BULK_RESULTS_DIR.iterdir()):
        if not sample_dir.is_dir():
            continue
        if not (sample_dir / "abundance.tsv").exists():
            logger.warning("跳過 %s（找不到 abundance.tsv）", sample_dir.name)
            continue

        sample_id = sample_dir.name
        notes = ""
        run_info_path = sample_dir / "run_info.json"
        if run_info_path.exists():
            try:
                info = json.loads(run_info_path.read_text(encoding="utf-8"))
                notes = (
                    f"kallisto {info.get('kallisto_version', '?')}  "
                    f"reads={info.get('n_processed', '?')}  "
                    f"mapped={info.get('p_pseudoaligned', '?')}%"
                )
            except Exception:
                logger.warning("無法讀取 run_info.json：%s", run_info_path)

        ok = register_sample(
            con,
            sample_id=sample_id,
            data_type="bulk_rnaseq",
            platform="kallisto",
            l3_path=str(BULK_RESULTS_DIR / sample_id),
            project="Kallisto_v1",
            species="mouse",
            tissue="",
            notes=notes,
            added_by=added_by,
            l2_ready=True,
        )
        if ok:
            inserted += 1
        else:
            skipped += 1

    logger.info("掃描完成：新增 %d 筆，跳過 %d 筆（已存在）", inserted, skipped)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="登記樣本至 sample_registry")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--scan-bulk-rna",
        action="store_true",
        help="自動掃描 bulk_rna_data/results_kallisto/ 並批次登記",
    )
    mode.add_argument("--sample-id", metavar="ID", help="樣本 ID（小寫底線，如 hair_ctrl_1）")

    p.add_argument("--data-type", choices=sorted(VALID_DATA_TYPES))
    p.add_argument("--platform", choices=sorted(VALID_PLATFORMS))
    p.add_argument("--l3-path", default="", metavar="PATH")
    p.add_argument("--project", default="")
    p.add_argument("--species", default="mouse")
    p.add_argument("--tissue", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--added-by", default="script", metavar="NAME")
    p.add_argument("--l2-ready", action="store_true")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = _parse_args()

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        if args.scan_bulk_rna:
            scan_bulk_rna(con, added_by=args.added_by)
        else:
            if not args.data_type or not args.platform:
                logger.error("--data-type 與 --platform 在 --sample-id 模式下為必填")
                sys.exit(1)
            register_sample(
                con,
                sample_id=args.sample_id,
                data_type=args.data_type,
                platform=args.platform,
                l3_path=args.l3_path,
                project=args.project,
                species=args.species,
                tissue=args.tissue,
                notes=args.notes,
                added_by=args.added_by,
                l2_ready=args.l2_ready,
            )

        rows = con.execute(
            """SELECT sample_id, data_type, platform, l2_ready
               FROM sample_registry ORDER BY last_updated DESC"""
        ).fetchall()
        logger.info("目前 sample_registry（%d 筆）：", len(rows))
        for r in rows:
            logger.info("  %-30s  %-12s  %-12s  l2_ready=%s", *r)


if __name__ == "__main__":
    main()
