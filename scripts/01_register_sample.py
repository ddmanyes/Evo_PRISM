#!/usr/bin/env python3
"""登記單一樣本至 sample_registry。

使用方式（單筆登記）：
    python scripts/01_register_sample.py \\
        --sample-id hair_ctrl_1 \\
        --data-type bulk_rnaseq \\
        --platform kallisto \\
        --l3-path /mnt/space4/BulkRNA/raw/ctrl_1 \\
        [--project hair_follicle_exp1] \\
        [--species mouse] \\
        [--tissue skin] \\
        [--condition ctrl] \\
        [--time-point 0hr] \\
        [--batch rep1] \\
        [--notes "ctrl 第一批"] \\
        [--added-by zhanqiru] \\
        [--l2-ready]

批次掃描（自動登記 results_kallisto/ 下所有樣本）：
    python scripts/01_register_sample.py --scan-bulk-rna [--experiment-name hair_follicle_exp1]

資料完整性驗證（不寫入，只報告缺漏）：
    python scripts/01_register_sample.py --validate --l3-path /path/to/sample --data-type visium_hd
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BULK_RNA_ROOT, DUCKDB_PATH
from config.db_utils import safe_write

logger = logging.getLogger(__name__)

BULK_RESULTS_DIR = BULK_RNA_ROOT / "Kallisto_v1" / "results_kallisto"

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

# ── 資料完整性規格 ────────────────────────────────────────────────────────────
# 每個 data_type 的必要檔案／目錄。
# "required" = 全部缺少才報錯；"any_of" = 至少一個存在即可；"recommended" = 警告但不擋
_COMPLETENESS_SPEC: dict[str, dict] = {
    "bulk_rnaseq": {
        "required": [],
        "any_of": [
            ["abundance.tsv"],
            ["abundance.h5"],
        ],
        "recommended": ["run_info.json"],
        "note": "每個 sample 子目錄需含 abundance.tsv（kallisto 輸出）",
    },
    "visium_hd": {
        "required": [
            "filtered_feature_bc_matrix",
            "spatial",
        ],
        "any_of": [
            ["spatial/tissue_positions.parquet"],
            ["spatial/tissue_positions.csv"],
        ],
        "recommended": ["molecule_info.h5", "raw_feature_bc_matrix"],
        "note": "需含 10x Space Ranger outs/ 完整目錄結構",
    },
    "visium": {
        "required": [
            "filtered_feature_bc_matrix",
            "spatial",
        ],
        "any_of": [
            ["spatial/tissue_positions_list.csv"],
            ["spatial/tissue_positions.csv"],
        ],
        "recommended": ["molecule_info.h5"],
        "note": "需含 10x Space Ranger outs/ 完整目錄結構",
    },
    "scrna": {
        "required": [],
        "any_of": [
            ["barcodes.tsv.gz", "features.tsv.gz", "matrix.mtx.gz"],
            ["barcodes.tsv", "features.tsv", "matrix.mtx"],
            ["filtered_feature_bc_matrix"],
        ],
        "recommended": ["web_summary.html"],
        "note": "需含 Cell Ranger 輸出或 MEX 三檔格式",
    },
    "proteomics": {
        "required": [],
        "any_of": [
            ["proteinGroups.txt"],
            ["peptides.txt"],
            ["evidence.txt"],
        ],
        "recommended": ["parameters.txt", "summary.txt"],
        "note": "需含 MaxQuant 輸出（proteinGroups.txt 為主要輸入）",
    },
    "multiome": {
        "required": [
            "filtered_feature_bc_matrix",
            "atac_fragments.tsv.gz",
        ],
        "any_of": [],
        "recommended": ["summary.csv", "per_barcode_metrics.csv"],
        "note": "需含 Cell Ranger ARC outs/ 目錄",
    },
    "atac": {
        "required": [],
        "any_of": [
            ["fragments.tsv.gz"],
            ["atac_fragments.tsv.gz"],
        ],
        "recommended": ["singlecell.csv", "summary.csv"],
        "note": "需含 ATAC-seq fragments 檔案",
    },
}


# ── 資料完整性驗證 ────────────────────────────────────────────────────────────


def validate_l3_completeness(
    data_type: str,
    l3_path: "str | Path",
) -> dict:
    """驗證 L3 原始資料目錄的完整性。

    Args:
        data_type: 資料類型（見 VALID_DATA_TYPES）
        l3_path:   L3 原始資料根目錄路徑

    Returns:
        {
            "ok": bool,
            "missing_required": list[str],
            "missing_any_of": list[list[str]],  # 每組 any_of 都缺齊才記入
            "missing_recommended": list[str],
            "warnings": list[str],
            "note": str,
        }
    """
    path = Path(l3_path)
    spec = _COMPLETENESS_SPEC.get(data_type)
    result: dict = {
        "ok": True,
        "missing_required": [],
        "missing_any_of": [],
        "missing_recommended": [],
        "warnings": [],
        "note": spec["note"] if spec else "",
    }

    if spec is None:
        result["warnings"].append(f"data_type={data_type!r} 無完整性規格，跳過驗證")
        return result

    if not path.exists():
        result["ok"] = False
        result["missing_required"].append(f"<根目錄不存在：{path}>")
        return result

    # 必要檔案／目錄
    for item in spec["required"]:
        if not (path / item).exists():
            result["missing_required"].append(item)

    # any_of 群組：每個群組至少一組檔案全部存在即通過
    for group in spec["any_of"]:
        if not any(all((path / f).exists() for f in grp) for grp in [group]):
            result["missing_any_of"].append(group)

    # 建議檔案（不影響 ok，但記入警告）
    for item in spec["recommended"]:
        if not (path / item).exists():
            result["missing_recommended"].append(item)
            result["warnings"].append(f"建議檔案缺失：{item}")

    if result["missing_required"] or result["missing_any_of"]:
        result["ok"] = False

    return result


def print_completeness_report(sample_id: str, report: dict) -> None:
    """將完整性驗證結果輸出到 stderr。"""
    status = "OK" if report["ok"] else "FAIL"
    logger.info("[%s] %s  — %s", status, sample_id, report["note"])
    for m in report["missing_required"]:
        logger.error("  MISSING required  : %s", m)
    for grp in report["missing_any_of"]:
        logger.error("  MISSING (need any): %s", " | ".join(grp))
    for w in report["warnings"]:
        logger.warning("  %s", w)


# ── Kallisto 樣本名稱解析 ─────────────────────────────────────────────────────

# 條件 → 時間點對照（小時）
_CONDITION_TO_TIME: dict[str, str] = {
    "ctrl": "0hr",
    "pw6hr": "6hr",
    "pw12hr": "12hr",
    "pw24hr": "24hr",
    "pw36hr": "36hr",
    "pw48hr": "48hr",
    "pw72hr": "72hr",
    "pw96hr": "96hr",
    "pw120hr": "120hr",
}

# 已知時間條件前綴（長的先匹配，避免 pw12hr 被誤截為 pw1）
_CONDITIONS_SORTED = sorted(_CONDITION_TO_TIME.keys(), key=len, reverse=True)

# 樣本名稱模式：{condition}_{rep}_{tissue...}（rep 可含 _a{n} 後綴）
# 例：ctrl_1_Hair_germ | pw120hr_2_Hair_germ_a6 | pw12hr_1_upper_bulge
_SAMPLE_RE = re.compile(
    r"^(?P<cond>[a-zA-Z0-9]+)_(?P<rep>\d+)_(?P<tissue>.+)$"
)


def parse_kallisto_sample_metadata(sample_id: str) -> dict[str, str]:
    """從 Kallisto 樣本 ID 解析 condition / time_point / batch / tissue。

    回傳空 dict 表示無法解析（不符合命名規則）。

    Examples:
        ctrl_1_Hair_germ      → condition=ctrl,    time_point=0hr,   batch=rep1, tissue=Hair_germ
        pw120hr_2_Hair_germ_a6→ condition=pw120hr, time_point=120hr, batch=rep2, tissue=Hair_germ_a6
        pw6hr_3_upper_bulge   → condition=pw6hr,   time_point=6hr,   batch=rep3, tissue=upper_bulge
    """
    m = _SAMPLE_RE.match(sample_id)
    if not m:
        return {}

    cond = m.group("cond")
    rep = m.group("rep")
    tissue = m.group("tissue")

    # 驗證條件是否已知
    if cond not in _CONDITION_TO_TIME:
        return {}

    return {
        "condition": cond,
        "time_point": _CONDITION_TO_TIME[cond],
        "batch": f"rep{rep}",
        "tissue": tissue,
    }


# ── DB 操作 ───────────────────────────────────────────────────────────────────


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
    condition: str = "",
    time_point: str = "",
    batch: str = "",
    donor_id: str = "",
    tags: Optional[list[str]] = None,
    validate: bool = True,
) -> bool:
    """將單一樣本寫入 sample_registry，已存在則跳過。回傳是否新增成功。

    若 validate=True，登記前先執行 L3 完整性驗證。
    驗證失敗時記錄警告但仍寫入（避免阻擋批次掃描）。
    """
    if _sample_exists(con, sample_id):
        logger.info("樣本 %r 已存在，跳過", sample_id)
        return False

    # L3 完整性驗證
    if validate and l3_path:
        report = validate_l3_completeness(data_type, l3_path)
        print_completeness_report(sample_id, report)
        if not report["ok"]:
            logger.warning(
                "樣本 %r L3 資料不完整，仍繼續登記（l2_ready 已強制設為 False）",
                sample_id,
            )
            l2_ready = False  # 資料不完整不得標為 l2_ready

    safe_write(
        con,
        """INSERT INTO sample_registry
               (sample_id, project, data_type, platform, species, tissue,
                l3_path, l2_ready, analysis_done, added_by, notes, last_updated,
                condition, time_point, batch, donor_id, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, false, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            condition or None,
            time_point or None,
            batch or None,
            donor_id or None,
            tags or None,
        ],
    )
    logger.info(
        "已登記樣本 %r  data_type=%s  platform=%s  condition=%s  batch=%s",
        sample_id, data_type, platform, condition or "-", batch or "-",
    )
    return True


def scan_bulk_rna(
    con: duckdb.DuckDBPyConnection,
    added_by: str = "scan",
    experiment_name: str = "hair_follicle_exp1",
) -> None:
    """掃描 results_kallisto/ 下所有子目錄，自動登記尚未存在的樣本。

    experiment_name 用作 project 欄位，區分不同實驗批次。
    樣本名稱符合命名規則時自動解析 condition / time_point / batch / tissue。
    """
    if not BULK_RESULTS_DIR.exists():
        logger.error("Bulk RNA 結果目錄不存在：%s", BULK_RESULTS_DIR)
        return

    inserted = skipped = 0
    for sample_dir in sorted(BULK_RESULTS_DIR.iterdir()):
        if not sample_dir.is_dir():
            continue

        # 完整性驗證：需含 abundance.tsv 或 abundance.h5
        completeness = validate_l3_completeness("bulk_rnaseq", sample_dir)
        if not completeness["ok"]:
            logger.warning("跳過 %s（L3 資料不完整：%s）", sample_dir.name, completeness["missing_any_of"])
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

        # 解析樣本名稱元數據
        meta = parse_kallisto_sample_metadata(sample_id)

        ok = register_sample(
            con,
            sample_id=sample_id,
            data_type="bulk_rnaseq",
            platform="kallisto",
            l3_path=str(sample_dir),
            project=experiment_name,
            species="mouse",
            tissue=meta.get("tissue", ""),
            notes=notes,
            added_by=added_by,
            l2_ready=True,
            condition=meta.get("condition", ""),
            time_point=meta.get("time_point", ""),
            batch=meta.get("batch", ""),
            validate=False,  # 已在上方驗證
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
    mode.add_argument(
        "--validate",
        action="store_true",
        help="只執行完整性驗證，不寫入 DB（需同時指定 --l3-path 與 --data-type）",
    )

    p.add_argument("--data-type", choices=sorted(VALID_DATA_TYPES))
    p.add_argument("--platform", choices=sorted(VALID_PLATFORMS))
    p.add_argument("--l3-path", default="", metavar="PATH")
    p.add_argument("--project", default="")
    p.add_argument("--species", default="mouse")
    p.add_argument("--tissue", default="")
    p.add_argument("--condition", default="")
    p.add_argument("--time-point", default="")
    p.add_argument("--batch", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--added-by", default="script", metavar="NAME")
    p.add_argument("--l2-ready", action="store_true")
    p.add_argument(
        "--experiment-name",
        default="hair_follicle_exp1",
        metavar="NAME",
        help="--scan-bulk-rna 時的 project 名稱（預設：hair_follicle_exp1）",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = _parse_args()

    if args.validate:
        if not args.l3_path or not args.data_type:
            logger.error("--validate 需同時指定 --l3-path 與 --data-type")
            sys.exit(1)
        report = validate_l3_completeness(args.data_type, args.l3_path)
        print_completeness_report(args.l3_path, report)
        sys.exit(0 if report["ok"] else 1)

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        if args.scan_bulk_rna:
            scan_bulk_rna(con, added_by=args.added_by, experiment_name=args.experiment_name)
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
                condition=args.condition,
                time_point=getattr(args, "time_point", ""),
                batch=args.batch,
            )

        rows = con.execute(
            """SELECT sample_id, data_type, platform, condition, time_point, batch, l2_ready
               FROM sample_registry ORDER BY last_updated DESC LIMIT 20"""
        ).fetchall()
        logger.info("最近 sample_registry（最多 20 筆）：")
        for r in rows:
            logger.info(
                "  %-30s  %-12s  %-10s  cond=%-8s  tp=%-6s  batch=%-5s  l2=%s",
                *r,
            )


if __name__ == "__main__":
    main()
