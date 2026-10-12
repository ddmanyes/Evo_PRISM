"""
Migration v29 — 移除 analysis_history / analysis_artifacts / sample_registry 上的
硬性 FK（改為軟引用），與 v20 對 tools(tool_id) 的處理同一原則。

背景：DuckDB（1.5.3 實測）對「被 FK 引用且存在引用列」的父表整體禁止 UPDATE ——
UPDATE 內部是 DELETE+INSERT，即使沒動到 key 欄位也會撞上
    Constraint Error: Violates foreign key constraint because key "..."
    is still referenced by a foreign key in a different table.

三條 live FK 全部踩到這個限制，且都已經在正式庫上把核心流程鎖死：

  analysis_artifacts.analysis_id → analysis_history(analysis_id)
      → 任何「已登記過 artifact」的 run 都不能再 UPDATE。canonical 治理首當其衝：
        mark_canonical() 要把舊 canonical 降為 superseded，而舊 canonical 幾乎必然
        帶著 artifact。2026-09-04 Kallisto_v1 的 bulk_eda 成為第一筆「canonical 且
        有 artifact」的列之後，該樣本的 bio_run_bulk_eda 就 100% 卡死在這裡
        （run 本體算完、報告寫檔、complete_history 成功，卻在 seam 收尾的
        mark_canonical 爆掉，連帶 _flush_artifacts 整段沒跑到）。
        同樣被擋的還有 update_history()（failure_diagnosis / parent_analysis_id /
        user_approval / tool_id 回填）。

  analysis_artifact_blobs.artifact_id → analysis_artifacts(artifact_id)
      → 帶 inline blob 的 artifact（正式庫 1059 筆中有 733 筆）不能 UPDATE，
        embedding 回填一類的維運動作全部被擋。

  analysis_history.sample_id → sample_registry(sample_id)
      → 有任何一筆分析紀錄的樣本（即所有真實樣本）都不能再 UPDATE，
        update_sample() / l2_ready 標記等寫入全被擋。

引用完整性改由應用層維持（artifact 一律經 register_artifact 以 run 的 analysis_id
寫入；history 一律經 store/run_context seam 寫入），與 v20 相同的取捨：FK 在 DuckDB
下幾乎不帶保護，卻擋死核心運作。Postgres 後端沒有這個限制，pg_schema.sql 的 FK
保留不動。

DuckDB 不支援 ALTER TABLE DROP CONSTRAINT，因此以「重建表（不含 FK）→ 搬資料 →
丟舊表 → 改名」方式移除。新表 DDL 直接由 live DDL 去掉 FK 子句衍生（而非另外手寫
一份），避免遺漏欄位/預設值/NOT NULL，並在搬完後逐欄位比對 schema 與列數。

重建會連帶丟掉表上的 index 與相依 view，故先快照 duckdb_indexes() / duckdb_views()
的 SQL，重建後原樣replay；analysis_artifacts 的 FTS sidecar 另以
PRAGMA create_fts_index(..., overwrite=1) 重建（見 scheduler/rebuild_hnsw.py）。

安全可重入：三條 FK 都不在時整支 no-op。

用法：
    python scripts/30_migrate_schema_v29_drop_analysis_fks.py [db_path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

SCHEMA_VERSION = 29
DESCRIPTION = (
    "Drop hard FKs on analysis_history/analysis_artifacts/sample_registry "
    "(DuckDB forbids UPDATE on referenced parent rows); soft references from here on"
)

# (child_table, referenced_table) —— 依序處理：先斷 child-most，父表才能被 DROP
_TARGETS = [
    ("analysis_artifact_blobs", "analysis_artifacts"),
    ("analysis_artifacts", "analysis_history"),
    ("analysis_history", "sample_registry"),
]

_FTS_SCHEMA_ARTIFACTS = "fts_main_analysis_artifacts"
_FK_RE = re.compile(
    r",\s*FOREIGN KEY\s*\([^)]*\)\s*REFERENCES\s+[\w.\"]+\s*\([^)]*\)",
    re.IGNORECASE,
)


# ── 快照 / 還原 ──────────────────────────────────────────────────────────────
def _table_ddl(con: duckdb.DuckDBPyConnection, table: str) -> str:
    row = con.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = ?", [table]
    ).fetchone()
    if not row:
        raise RuntimeError(f"找不到表 {table}")
    return row[0]


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in con.execute(f"DESCRIBE {table}").fetchall()]


def _has_fk(con: duckdb.DuckDBPyConnection, table: str, referenced: str) -> bool:
    rows = con.execute(
        """
        SELECT constraint_text FROM duckdb_constraints()
        WHERE table_name = ? AND constraint_type = 'FOREIGN KEY'
        """,
        [table],
    ).fetchall()
    return any(f"REFERENCES {referenced}(" in r[0] for r in rows)


def _index_sql(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT sql FROM duckdb_indexes() WHERE table_name = ?", [table]
        ).fetchall()
        if r[0]
    ]


def _dependent_views(con: duckdb.DuckDBPyConnection, tables: set[str]) -> list[tuple[str, str]]:
    """回傳 (view_name, ddl)——SQL 文字中提到任一目標表的 view。"""
    out = []
    for name, sql in con.execute(
        "SELECT view_name, sql FROM duckdb_views() WHERE internal = false"
    ).fetchall():
        if any(re.search(rf"\b{t}\b", sql) for t in tables):
            out.append((name, sql))
    return out


def _fts_present(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?",
        [_FTS_SCHEMA_ARTIFACTS],
    ).fetchone()
    return row is not None


# ── 重建 ─────────────────────────────────────────────────────────────────────
def _rebuild(con: duckdb.DuckDBPyConnection, table: str, referenced: str) -> None:
    live_ddl = _table_ddl(con, table)
    new_ddl, n_removed = _FK_RE.subn("", live_ddl)
    if n_removed != 1:
        raise RuntimeError(
            f"{table}: 預期剛好移除 1 條 FK 子句，實際 {n_removed}。live DDL：\n{live_ddl}"
        )
    if "FOREIGN KEY" in new_ddl.upper():
        raise RuntimeError(f"{table}: 去 FK 後仍殘留 FOREIGN KEY，拒絕重建\n{new_ddl}")

    tmp = f"{table}__noFK"
    new_ddl = new_ddl.replace(f"CREATE TABLE {table}(", f"CREATE TABLE {tmp}(", 1)
    if f"CREATE TABLE {tmp}(" not in new_ddl:
        raise RuntimeError(f"{table}: DDL 改名失敗，拒絕重建\n{new_ddl}")

    before_cols = _columns(con, table)
    before_n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    cols = ", ".join(f'"{c}"' for c, _ in before_cols)

    print(f"  {table}: 重建中（{before_n} 列，去掉 → {referenced} 的 FK）...", end=" ", flush=True)
    con.execute(new_ddl)
    con.execute(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {table}")
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {tmp} RENAME TO {table}")

    after_cols = _columns(con, table)
    after_n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if after_cols != before_cols:
        raise RuntimeError(
            f"{table} 欄位不符：\n  before={before_cols}\n  after ={after_cols}"
        )
    if after_n != before_n:
        raise RuntimeError(f"{table} 列數不符：{before_n} → {after_n}")
    print(f"OK（{after_n} 列 / {len(after_cols)} 欄）")


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))
    try:
        # analysis_artifacts 帶 HNSW 索引：任何寫入 / CHECKPOINT 前都必須先 LOAD vss。
        # 載不到不在這裡硬失敗（隔離測試 DB 根本沒有 HNSW/FTS）；真的需要時
        # CREATE INDEX ... USING HNSW 會自己噴錯，不會靜默寫壞。
        for ext in ("vss", "fts"):
            try:
                con.execute(f"LOAD {ext}")
            except Exception:
                try:
                    con.execute(f"INSTALL {ext}; LOAD {ext}")
                except Exception as exc:
                    print(f"  警告：{ext} 無法載入（{exc}）——若本 DB 有相關索引會在重建時失敗")
        try:
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception:
            pass

        todo = [(t, ref) for t, ref in _TARGETS if _has_fk(con, t, ref)]
        if not todo:
            print("三條 FK 都已不存在，略過（no-op）")
            _record_version(con)
            con.execute("CHECKPOINT")
            return

        tables = {t for t, _ in _TARGETS}
        views = _dependent_views(con, tables)
        # Only rebuilt tables lose their indexes. Fresh installs can already
        # have soft references on analysis_history, whose indexes remain live.
        indexes = {t: _index_sql(con, t) for t, _ in todo}
        fts_before = _fts_present(con)
        print(f"相依 view：{[v for v, _ in views]}")
        print(f"待重建索引：{ {t: len(v) for t, v in indexes.items()} }")
        print(f"FTS sidecar 存在：{fts_before}")

        for name, _ in views:
            con.execute(f"DROP VIEW IF EXISTS {name}")

        print("移除硬性 FK（改軟引用）：")
        for table, referenced in _TARGETS:
            if not _has_fk(con, table, referenced):
                print(f"  {table}: 已無 → {referenced} 的 FK，略過")
                continue
            _rebuild(con, table, referenced)

        print("重建索引：")
        for table, sqls in indexes.items():
            for sql in sqls:
                con.execute(sql)
            if sqls:
                print(f"  {table}: {len(sqls)} 個索引 OK")

        if fts_before:
            print("重建 analysis_artifacts FTS sidecar ...", end=" ", flush=True)
            con.execute(
                "PRAGMA create_fts_index("
                "'analysis_artifacts', 'artifact_id', "
                "'label', 'artifact_subtype', 'artifact_type', "
                "overwrite=1)"
            )
            print("OK")

        print("重建 view：")
        for name, sql in views:
            con.execute(sql)
            print(f"  {name} OK")

        _record_version(con)
        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        remaining = con.execute(
            """
            SELECT table_name, constraint_text FROM duckdb_constraints()
            WHERE constraint_type = 'FOREIGN KEY'
              AND table_name IN ('analysis_history', 'analysis_artifacts',
                                 'analysis_artifact_blobs')
            """
        ).fetchall()
        if remaining:
            print(f"\nERROR: 仍有硬性 FK 殘留：{remaining}", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v29 summary ---")
        print("  analysis_artifact_blobs : artifacts FK 已移除")
        print("  analysis_artifacts      : history FK 已移除")
        print("  analysis_history        : sample_registry FK 已移除")
        print("  mark_canonical / update_history / update_sample 解封")
    finally:
        con.close()


def _record_version(con: duckdb.DuckDBPyConnection) -> None:
    exists = con.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", [SCHEMA_VERSION]
    ).fetchone()
    if not exists:
        con.execute(
            "INSERT INTO schema_migrations(version, applied_at, description) "
            "VALUES (?, now(), ?)",
            [SCHEMA_VERSION, DESCRIPTION],
        )


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DUCKDB_PATH
    migrate(target)
    print("\nDone.")
