"""
Phase — Schema Migration v2
新增欄位至兩個 DuckDB 檔案：
  bio_memory.duckdb        → sample_registry + analysis_history
  gold/hermes_cache.duckdb → memory_recent

使用 ALTER TABLE ADD COLUMN IF NOT EXISTS（DuckDB ≥ 0.9 idempotent）。
可安全重複執行。
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

BASE_DIR = Path(__file__).parent.parent
MAIN_DB = BASE_DIR / "bio_memory.duckdb"
L1_DB = BASE_DIR / "gold" / "hermes_cache.duckdb"


# ── 各表要新增的欄位 (column_name, duckdb_type) ─────────────────────────────

SAMPLE_REGISTRY_COLS: list[tuple[str, str]] = [
    ("condition", "VARCHAR"),  # 實驗條件：control/tumor/treated/...
    ("time_point", "VARCHAR"),  # 時間點：0h/24h/day3/...
    ("batch", "VARCHAR"),  # 測序批次：batch_1/batch_2/...
    ("donor_id", "VARCHAR"),  # 供體 ID（連結同一個體多個樣本）
    ("tags", "VARCHAR[]"),  # 標籤陣列：paper_figure/key_result/qc_only/...
]

ANALYSIS_HISTORY_COLS: list[tuple[str, str]] = [
    ("analysis_version", "VARCHAR"),  # 分析函數版本：1.0/1.1/...
    ("tool_version", "VARCHAR"),  # 工具版本：scanpy 1.9/...
    ("tags", "VARCHAR[]"),  # 標籤：paper_figure/baseline/...
]

MEMORY_RECENT_COLS: list[tuple[str, str]] = [
    ("analysis_type", "VARCHAR"),  # 分析類型，供 HNSW 前置過濾
]


def _add_columns(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: list[tuple[str, str]],
) -> list[str]:
    """對指定 table 新增欄位，回傳成功加入的欄位名稱清單。"""
    added: list[str] = []
    for col_name, col_type in columns:
        sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
        try:
            con.execute(sql)
            added.append(col_name)
            print(f"  [OK] {table}.{col_name} {col_type}")
        except Exception as e:
            print(f"  [FAIL] {table}.{col_name}: {e}", file=sys.stderr)
            raise
    return added


def migrate_main_db(db_path: Path) -> None:
    """Migration for bio_memory.duckdb (sample_registry + analysis_history)."""
    print(f"\n=== Migrating {db_path} ===")
    if not db_path.exists():
        print(f"  [SKIP] 檔案不存在：{db_path}", file=sys.stderr)
        return

    try:
        con = duckdb.connect(str(db_path))
    except Exception as e:
        print(f"  [FAIL] 無法連線：{e}", file=sys.stderr)
        raise

    try:
        print("  → sample_registry")
        _add_columns(con, "sample_registry", SAMPLE_REGISTRY_COLS)

        print("  → analysis_history")
        _add_columns(con, "analysis_history", ANALYSIS_HISTORY_COLS)

        con.execute("CHECKPOINT")
        print("  CHECKPOINT OK")
    except Exception as e:
        print(f"  [FAIL] migration 中斷：{e}", file=sys.stderr)
        raise
    finally:
        con.close()

    print(f"  Done: {db_path.name}")


def migrate_l1_db(db_path: Path) -> None:
    """Migration for gold/hermes_cache.duckdb (memory_recent)."""
    print(f"\n=== Migrating {db_path} ===")
    if not db_path.exists():
        print(f"  [SKIP] 檔案不存在：{db_path}", file=sys.stderr)
        return

    try:
        con = duckdb.connect(str(db_path))
    except Exception as e:
        print(f"  [FAIL] 無法連線：{e}", file=sys.stderr)
        raise

    try:
        con.execute("INSTALL vss; LOAD vss")
        print("  → memory_recent")
        _add_columns(con, "memory_recent", MEMORY_RECENT_COLS)

        con.execute("CHECKPOINT")
        print("  CHECKPOINT OK")
    except Exception as e:
        print(f"  [FAIL] migration 中斷：{e}", file=sys.stderr)
        raise
    finally:
        con.close()

    print(f"  Done: {db_path.name}")


def verify_schema(db_path: Path, table: str, expected_cols: list[str]) -> None:
    """驗證 table 是否包含所有預期欄位。"""
    if not db_path.exists():
        return
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? AND table_schema = 'main'",
            [table],
        ).fetchall()
        actual = {r[0] for r in rows}
        missing = [c for c in expected_cols if c not in actual]
        if missing:
            print(f"  [WARN] {db_path.name}:{table} 仍缺欄位: {missing}", file=sys.stderr)
        else:
            print(f"  [VERIFY OK] {db_path.name}:{table} — 所有新欄位均存在")
    finally:
        con.close()


def main() -> None:
    try:
        migrate_main_db(MAIN_DB)
        migrate_l1_db(L1_DB)
    except Exception as e:
        print(f"\n[ERROR] Migration 失敗，請檢查上方錯誤訊息。\n{e}", file=sys.stderr)
        sys.exit(1)

    print("\n=== Verification ===")
    verify_schema(MAIN_DB, "sample_registry", [c for c, _ in SAMPLE_REGISTRY_COLS])
    verify_schema(MAIN_DB, "analysis_history", [c for c, _ in ANALYSIS_HISTORY_COLS])
    verify_schema(L1_DB, "memory_recent", [c for c, _ in MEMORY_RECENT_COLS])

    print("\nMigration v2 complete.")


if __name__ == "__main__":
    main()
