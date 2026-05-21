"""
Migration v20 — 移除 tools(tool_id) 上的硬性 FK（改為軟引用）。

背景：DuckDB 1.5.2 對「被 FK 引用且存在引用列的表」整體禁止 UPDATE/DELETE。
`tool_change_log.new_tool_id` 與 `tool_dependencies.(tool_id, depends_on)` 對
`tools(tool_id)` 的硬性 FK，使得 `tools` 表無法 UPDATE（register_tool 停用舊版）
也無法 DELETE（prune_deprecated 清理舊版）——HELIX §7 版本治理全面卡死。

引用完整性已由 HELIX 應用層維護（register_tool 寫 new_tool_id；prune 前手動檢查
analysis_history 引用），FK 在此幾乎不帶保護卻擋死核心運作，故移除。

DuckDB 1.5.2 不支援 ALTER TABLE DROP CONSTRAINT，因此以「重建表（不含 FK）→ 搬資料
→ 丟舊表」方式移除。安全可重入：FK 已不存在時自動略過。
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

# (table_name, new_DDL_without_FK, ordered_columns)
# 欄位順序與型別須與 live schema 完全一致（DESCRIBE 驗證於 2026-05-20）。
_TOOL_CHANGE_LOG_NEW = """
CREATE TABLE tool_change_log__noFK (
    log_id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name       VARCHAR NOT NULL,
    old_hash        VARCHAR,
    new_hash        VARCHAR NOT NULL,
    new_tool_id     UUID,                 -- 原 REFERENCES tools(tool_id)，改軟引用
    revision_number INTEGER NOT NULL,
    change_reason   VARCHAR,
    changed_at      TIMESTAMP DEFAULT now(),
    source_snapshot VARCHAR,
    changed_lines   VARCHAR,
    churn_ratio     DOUBLE
)
"""
_TOOL_CHANGE_LOG_COLS = (
    "log_id, tool_name, old_hash, new_hash, new_tool_id, revision_number, "
    "change_reason, changed_at, source_snapshot, changed_lines, churn_ratio"
)
_TOOL_CHANGE_LOG_EXPECTED = {
    "log_id", "tool_name", "old_hash", "new_hash", "new_tool_id", "revision_number",
    "change_reason", "changed_at", "source_snapshot", "changed_lines", "churn_ratio",
}

_TOOL_DEPENDENCIES_NEW = """
CREATE TABLE tool_dependencies__noFK (
    tool_id    UUID NOT NULL,            -- 原 REFERENCES tools(tool_id)，改軟引用
    depends_on UUID NOT NULL,            -- 原 REFERENCES tools(tool_id)，改軟引用
    PRIMARY KEY (tool_id, depends_on)
)
"""
_TOOL_DEPENDENCIES_COLS = "tool_id, depends_on"
_TOOL_DEPENDENCIES_EXPECTED = {"tool_id", "depends_on"}


def _has_fk_to_tools(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    rows = con.execute(
        """
        SELECT constraint_text FROM duckdb_constraints()
        WHERE table_name = ? AND constraint_type = 'FOREIGN KEY'
        """,
        [table],
    ).fetchall()
    return any("REFERENCES tools(tool_id)" in r[0] for r in rows)


def _live_columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}


def _rebuild(
    con: duckdb.DuckDBPyConnection,
    table: str,
    new_ddl: str,
    cols: str,
    expected: set[str],
) -> None:
    if not _has_fk_to_tools(con, table):
        print(f"  {table}: 已無 tools FK，略過")
        return

    live = _live_columns(con, table)
    if live != expected:
        raise RuntimeError(
            f"{table} schema 與預期不符，拒絕重建（避免遺漏欄位）。\n"
            f"  live={sorted(live)}\n  expected={sorted(expected)}"
        )

    tmp = new_ddl.split()[2]  # e.g. tool_change_log__noFK
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    print(f"  {table}: 重建中（{n} 列）...", end=" ")
    con.execute(new_ddl)
    con.execute(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {table}")
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    moved = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    assert moved == n, f"{table} 列數不符：{n} → {moved}"
    print(f"OK（{moved} 列）")


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))
    try:
        # DB 含 HNSW 索引（analysis_artifacts / tool_catalog 等），CHECKPOINT 與
        # 任何寫入都需先載入 vss，否則 bind index 失敗。
        try:
            con.execute("LOAD vss")
        except Exception:
            con.execute("INSTALL vss; LOAD vss")
        con.execute("SET hnsw_enable_experimental_persistence = true")

        print("移除 tools(tool_id) 硬性 FK（改軟引用）：")
        _rebuild(con, "tool_change_log", _TOOL_CHANGE_LOG_NEW,
                 _TOOL_CHANGE_LOG_COLS, _TOOL_CHANGE_LOG_EXPECTED)
        _rebuild(con, "tool_dependencies", _TOOL_DEPENDENCIES_NEW,
                 _TOOL_DEPENDENCIES_COLS, _TOOL_DEPENDENCIES_EXPECTED)

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        # Verify：tools 不再被任何 FK 引用
        remaining = con.execute(
            """
            SELECT table_name, constraint_text FROM duckdb_constraints()
            WHERE constraint_type = 'FOREIGN KEY'
              AND constraint_text LIKE '%REFERENCES tools(tool_id)%'
            """
        ).fetchall()
        if remaining:
            print(f"\nERROR: 仍有 FK 指向 tools(tool_id)：{remaining}", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v20 summary ---")
        print("  tool_change_log    : tools FK 已移除")
        print("  tool_dependencies  : tools FK 已移除")
        print("  tools 表現可正常 UPDATE/DELETE（register_tool / prune 解封）")
    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
