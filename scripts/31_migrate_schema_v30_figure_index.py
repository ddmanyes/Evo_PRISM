"""Migration v30 — figure_index：把 figure_cache 的 content-addressed figure_id
接回一張可查詢的索引表。

背景（見 sb 筆記「evo-prism figure index 圖檔查找路徑重構」）：figure_cache.py 把報告裡的
inline base64 圖剝離、換成 `[圖片:<alt> | id=<figure_id> | 用 bio_get_figure 索取]` 佔位符，
圖存成 gold/figure_cache/<id>.<ext>。但 figure_id = sha256(base64)[:12] 只是 content-addressed
檔名，從未寫進任何資料庫表——使用者用描述（如「dpcp01 的 UMAP」）找圖時，agent 沒有「用描述查
figure_id」這個動作可做，只能把整份報告文字重新讀回 context 做全面盤點
（bio_sample_list → bio_history_lookup → bio_artifact_summary → bio_read_report），這正是
figure_cache 當初想省下的 token。

本 migration 只新增一張索引表，不改 figure_cache 既有的檔案儲存方式。寫入走
`store.record_figure()`（best-effort，比照 record_metric），查詢走
`store.find_figures()`（純 SQL ILIKE，不碰 embedding）。

⚠️ 與計畫文件的 DDL 有一處必要修正：原規劃用 `PRIMARY KEY (figure_id, requested_by, created_at)`
複合鍵，但 DuckDB（1.5.3 實測）對複合 PRIMARY KEY 的每個欄位都隱性套用 NOT NULL——
`requested_by` 在呼叫端是 `visible_requested_by(identity)`，admin 身份時本來就會是 None
（與 figure_cache.py 的 owner marker 邏輯同一慣例），插入會直接撞
`Constraint Error: NOT NULL constraint failed: figure_index.requested_by`。
改用 surrogate UUID 當 PK（比照 mcp_tool_metrics.metric_id 的既有寫法），
其餘三欄位維持一般可為 NULL 的索引欄位。

安全可重入：CREATE TABLE/INDEX 皆為 IF NOT EXISTS。

用法：
    python scripts/31_migrate_schema_v30_figure_index.py [db_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH  # noqa: E402

SCHEMA_VERSION = 30
DESCRIPTION = (
    "Add figure_index table so figure_cache's content-addressed figure_id can be "
    "looked up by sample_id/tool_name/alt instead of re-reading the full report"
)


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    with duckdb.connect(str(db_path)) as con:
        # figure_index 本身不帶向量欄位，但比照本 repo 其他 migration 的既有陷阱處理
        # （對已有 HNSW 索引的 DB，CHECKPOINT 前沒 LOAD vss 會炸）——維持一致慣例。
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail if this DB has HNSW indexes")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL,
                description VARCHAR NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS figure_index (
                id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                figure_id     VARCHAR NOT NULL,
                alt           VARCHAR,
                tool_name     VARCHAR,
                sample_id     VARCHAR,
                analysis_id   VARCHAR,          -- best-effort，多數工具呼叫拿不到，可為 NULL
                requested_by  VARCHAR,          -- None = admin/global（與 visible_requested_by 同慣例）
                created_at    TIMESTAMP DEFAULT now()
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_figure_index_sample ON figure_index(sample_id)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_figure_index_created ON figure_index(created_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_figure_index_figure_id ON figure_index(figure_id)"
        )

        exists = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", [SCHEMA_VERSION]
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO schema_migrations(version, applied_at, description) "
                "VALUES (?, now(), ?)",
                [SCHEMA_VERSION, DESCRIPTION],
            )
        con.execute("CHECKPOINT")
        print(f"Migration v{SCHEMA_VERSION} OK — figure_index table ready ({db_path})")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DUCKDB_PATH
    migrate(target)
