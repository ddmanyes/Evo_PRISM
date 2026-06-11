#!/usr/bin/env python3
"""樣本 ID 規範化遷移腳本。

將舊的非規範 sample_id 更新為新命名規範，舊 ID 保留於 alias 欄位。

命名規範：
  Bulk RNA-seq : {PROJ}_{TISSUE}_{TP}_{REP}  例：HF01_HG_T0_R1
  Spatial      : {PROJ}_{MODALITY}_{YYYYMMDD}_{AREA}  例：MQ01_VH_20250428_A1D1

策略：因 DuckDB FK 為 NO ACTION，遷移順序：
  1. INSERT 新 sample_id row（複製舊 row）
  2. UPDATE 子表（analysis_history、engram_search_metrics）
  3. DELETE 舊 row

使用方式：
    uv run python scripts/03_rename_sample_ids.py --dry-run   # 預覽
    uv run python scripts/03_rename_sample_ids.py             # 執行
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH
from config.db_utils import open_db

logger = logging.getLogger(__name__)

# ── 組織縮寫對照 ──────────────────────────────────────────────────────────────

_TISSUE_MAP = {
    "hair_germ":   "HG",
    "hair_germ_a6": "HGA",
    "lower_bulge": "LB",
    "upper_bulge": "UB",
}

_COND_TO_HOURS = {
    "ctrl":   0,
    "pw6hr":  6,   "pw6":   6,
    "pw12hr": 12,  "pw12":  12,
    "pw24hr": 24,  "pw24":  24,
    "pw36hr": 36,  "pw36":  36,
    "pw48hr": 48,  "pw48":  48,
    "pw72hr": 72,  "pw72":  72,
    "pw96hr": 96,  "pw96":  96,
    "pw120hr": 120, "pw120": 120,
}

# Bulk RNA-seq 命名解析：{cond}_{rep}_{tissue}
_BULK_RE = re.compile(
    r"^(?P<cond>ctrl|pw\d+hr?)_(?P<rep>\d+)_(?P<tissue>.+)$",
    re.IGNORECASE,
)


def _bulk_new_id(old_id: str) -> str | None:
    m = _BULK_RE.match(old_id)
    if not m:
        return None
    cond = m.group("cond").lower()
    rep = m.group("rep")
    tissue_raw = m.group("tissue").lower()
    hours = _COND_TO_HOURS.get(cond)
    tissue_code = _TISSUE_MAP.get(tissue_raw)
    if hours is None or tissue_code is None:
        return None
    return f"HF01_{tissue_code}_T{hours}_R{rep}"


# ── 空間樣本靜態映射 ──────────────────────────────────────────────────────────

_SPATIAL_MAP: dict[str, str] = {
    "SDS-D0D1D2":       "HF01_VH_SDS_D02",
    "SDS-D3D4D5":       "HF01_VH_SDS_D35",
    "GEO_Zone_A":       "GEO01_V_PUB_ZA",
    "GEO_Zone_D":       "GEO01_V_PUB_ZD",
    "MQ250428-A1-D1":   "MQ01_VH_20250428_A1D1",
    "MQ250428-D1-D2":   "MQ01_VH_20250428_D1D2",
    "MQ250428-A1-M2":   "MQ01_VH_20250428_A1M2",
    "MQ250422-A1-M2":   "MQ01_VH_20250422_A1M2",
    "crc_official_v4":  "CRC01_VH_PUB_OFV4",
}


# ── 建立完整映射表 ────────────────────────────────────────────────────────────

def build_mapping(con) -> list[tuple[str, str]]:
    """回傳 [(old_id, new_id), ...] — 只包含需要更名的樣本。"""
    rows = con.execute("SELECT sample_id FROM sample_registry ORDER BY sample_id").fetchall()
    mapping = []
    for (old_id,) in rows:
        # 空間靜態映射
        if old_id in _SPATIAL_MAP:
            mapping.append((old_id, _SPATIAL_MAP[old_id]))
            continue
        # Bulk RNA-seq 動態解析
        new_id = _bulk_new_id(old_id)
        if new_id and new_id != old_id:
            mapping.append((old_id, new_id))
    return mapping


# ── 子表更新 ──────────────────────────────────────────────────────────────────

_CHILD_TABLES = [
    "analysis_history",
    "engram_search_metrics",
]


def _rename_one(con, old_id: str, new_id: str, *, dry_run: bool) -> None:
    """單一樣本重命名：INSERT 新 PK → 更新子表 → DELETE 舊 PK。"""
    logger.info("  %s  →  %s", old_id, new_id)

    if dry_run:
        return

    # 1. 複製 sample_registry row，新 sample_id，alias = old_id
    cols = [r[0] for r in con.execute("DESCRIBE sample_registry").fetchall()]
    col_list = ", ".join(cols)
    val_exprs = ", ".join(
        f"'{new_id}'" if c == "sample_id"
        else f"'{old_id}'" if c == "alias"
        else c
        for c in cols
    )
    con.execute(
        f"INSERT INTO sample_registry ({col_list}) "
        f"SELECT {val_exprs} FROM sample_registry WHERE sample_id = ?",
        [old_id],
    )

    # 2. 更新子表
    for table in _CHILD_TABLES:
        try:
            con.execute(
                f"UPDATE {table} SET sample_id = ? WHERE sample_id = ?",
                [new_id, old_id],
            )
        except Exception as exc:
            logger.warning("  %s UPDATE 失敗（跳過）: %s", table, exc)

    # 3. 刪除舊 row
    con.execute("DELETE FROM sample_registry WHERE sample_id = ?", [old_id])

    con.execute("CHECKPOINT")


# ── main ──────────────────────────────────────────────────────────────────────

def run(*, dry_run: bool = False) -> None:
    with open_db(DUCKDB_PATH) as con:
        mapping = build_mapping(con)

        if not mapping:
            logger.info("所有 sample_id 已符合規範，無需更名。")
            return

        logger.info("共 %d 筆需要更名%s：", len(mapping), "（dry-run，不寫入）" if dry_run else "")
        for old_id, new_id in mapping:
            _rename_one(con, old_id, new_id, dry_run=dry_run)

        if not dry_run:
            # 確認結果
            _r = con.execute("SELECT COUNT(*) FROM sample_registry WHERE alias IS NOT NULL").fetchone()
            remaining = _r[0] if _r else 0
            _t = con.execute("SELECT COUNT(*) FROM sample_registry").fetchone()
            total = _t[0] if _t else 0
            logger.info("完成：%d 筆已更名（alias 已填入），總計 %d 筆樣本", remaining, total)

            # 重新產生快照
            try:
                from scripts.export_registry import export_snapshot
                export_snapshot()
                logger.info("registry_snapshot.md 已更新")
            except Exception as exc:
                logger.warning("export_registry 失敗（非致命）: %s", exc)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="樣本 ID 規範化遷移")
    p.add_argument("--dry-run", action="store_true", help="只印出映射，不寫入")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    args = _parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
