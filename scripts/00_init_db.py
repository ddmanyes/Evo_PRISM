"""
Phase 1 — Initialize bio_memory.duckdb
Creates: sample_registry, analysis_history, analysis_index view
Verifies: DuckDB VSS extension (HNSW) loads correctly
"""

import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "bio_memory.duckdb"


def init_db(db_path: "Path | duckdb.DuckDBPyConnection" = DB_PATH) -> duckdb.DuckDBPyConnection:
    if isinstance(db_path, duckdb.DuckDBPyConnection):
        con = db_path
        print("Connected: <existing connection>")
    else:
        con = duckdb.connect(str(db_path))
        print(f"Connected: {db_path}")

    # Verify VSS extension
    try:
        con.execute("INSTALL vss; LOAD vss;")
        print("VSS extension loaded (HNSW available)")
    except Exception as e:
        print(f"WARNING: VSS extension failed: {e}")
        print("L1 Gold HNSW index will not be available — continue anyway")

    # sample_registry
    # data_type 大類: visium_hd | visium | scrna | bulk_rnaseq |
    #                 multiome | atac | proteomics | imaging | other
    # platform    具體工具: 10x_visium_hd | cellranger | kallisto |
    #                       salmon | cellranger_arc | snapatac2 | maxquant | ...
    con.execute("""
        CREATE TABLE IF NOT EXISTS sample_registry (
            sample_id      VARCHAR PRIMARY KEY,
            project        VARCHAR,
            data_type      VARCHAR,
            platform       VARCHAR,
            species        VARCHAR,    -- 'mouse', 'human', 'rat'
            tissue         VARCHAR,    -- 'colon', 'lung', 'pancreas' ...
            l3_path        VARCHAR,
            l2_ready       BOOLEAN DEFAULT FALSE,
            analysis_done  BOOLEAN DEFAULT FALSE,
            added_by       VARCHAR,
            notes          VARCHAR,
            last_updated   TIMESTAMP DEFAULT now(),
            -- v2 metadata fields
            condition      VARCHAR,    -- 實驗條件：control/tumor/treated/...
            time_point     VARCHAR,    -- 時間點：0h/24h/day3/...
            batch          VARCHAR,    -- 測序批次：batch_1/batch_2/...
            donor_id       VARCHAR,    -- 供體 ID（連結同一個體多個樣本）
            tags           VARCHAR[]   -- 標籤陣列：paper_figure/key_result/qc_only/...
        )
    """)
    print("Table: sample_registry — OK")

    # analysis_history
    con.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            analysis_id      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            sample_id        VARCHAR REFERENCES sample_registry(sample_id),
            analysis_type    VARCHAR,    -- 'qc', 'spatial_gene', 'clustering', 'diff_expr'
            parameters       JSON,
            status           VARCHAR,    -- 'running', 'completed', 'failed'
            result_path      VARCHAR,
            l1_cache_id      UUID,
            requested_by     VARCHAR,
            started_at       TIMESTAMP,
            completed_at     TIMESTAMP,
            summary          VARCHAR,    -- max 50 chars, for token-efficient search
            tool_id          UUID,       -- reserved: FK to future tools table (NULL until scale-out)
            -- v2 metadata fields
            analysis_version VARCHAR,    -- 分析函數版本：1.0/1.1/...
            tool_version     VARCHAR,    -- 工具版本：scanpy 1.9/...
            tags             VARCHAR[]   -- 標籤：paper_figure/baseline/...
        )
    """)
    # Idempotent migration: add tool_id column to pre-existing DBs (DuckDB ≥ 0.9)
    try:
        con.execute("ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS tool_id UUID")
    except Exception as e:
        print(f"WARNING: could not ensure tool_id column: {e}")
    print("Table: analysis_history — OK")

    # tools — versioned tool registry (content-hash based)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tools (
            tool_id        UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            tool_name      VARCHAR NOT NULL,
            version        VARCHAR NOT NULL,
            content_hash   VARCHAR(16) NOT NULL,
            module_path    VARCHAR NOT NULL,
            function_name  VARCHAR NOT NULL,
            description    VARCHAR,
            parameters     JSON,
            status         VARCHAR DEFAULT 'active',  -- active | deprecated | candidate
            created_at     TIMESTAMP DEFAULT now(),
            deprecated_at  TIMESTAMP,
            UNIQUE (tool_name, content_hash)
        )
    """)
    print("Table: tools — OK")

    # tool_dependencies — directed dependency graph between tools
    # 注意：tool_id / depends_on 為「軟引用」UUID，刻意不加 REFERENCES tools(tool_id)。
    # DuckDB 1.5.2 禁止對「被 FK 引用的表」UPDATE/DELETE，會掐死 register_tool 停用舊版
    # 與 prune_deprecated 清理；引用完整性由 HELIX 應用層維護（見 migration v20）。
    con.execute("""
        CREATE TABLE IF NOT EXISTS tool_dependencies (
            tool_id      UUID,
            depends_on   UUID,
            PRIMARY KEY (tool_id, depends_on)
        )
    """)
    print("Table: tool_dependencies — OK")

    # analysis_index view (0-token compact browsing)
    con.execute("""
        CREATE OR REPLACE VIEW analysis_index AS
        SELECT
            sample_id,
            analysis_type,
            COUNT(*)                                              AS run_count,
            MAX(completed_at)::DATE                              AS last_run_date,
            MIN(started_at)::DATE                                AS first_run_date,
            STRING_AGG(DISTINCT requested_by, ', ')              AS run_by_members,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS fail_count
        FROM analysis_history
        GROUP BY sample_id, analysis_type
        ORDER BY last_run_date DESC
    """)
    print("View:  analysis_index — OK")

    return con


def populate_registry(con: duckdb.DuckDBPyConnection):
    """Insert known samples. Edit this list as new samples arrive.

    data_type choices:
        visium_hd | visium | scrna | bulk_rnaseq |
        multiome  | atac   | proteomics | imaging | other
    """
    samples = [
        # (sample_id, project, data_type, platform, species, tissue, l3_path, notes)
        (
            "crc_official_v4",
            "CRC_official",
            "visium_hd",
            "10x_visium_hd",
            "human",
            "colon",
            "/Volumes/NO NAME/bio_DB/crc_visium_data/official_v4",
            "Official 10x CRC demo dataset; test prototype for L2 pipeline",
        ),
        (
            "MQ250428-D1-D2",
            "MQ250428",
            "visium_hd",
            "10x_visium_hd",
            "mouse",
            "unknown",
            "/mnt/space4/MQ250428-D1-D2/outs",
            "Primary lab prototype; NDPI registration pending",
        ),
        (
            "MQ250428-A1-M2",
            "MQ250428",
            "visium_hd",
            "10x_visium_hd",
            "mouse",
            "unknown",
            "/mnt/space4/MQ250428-A1-M2/outs",
            "Secondary lab prototype; NDPI registration pending",
        ),
        (
            "Kallisto_v1",
            "Kallisto_v1",
            "bulk_rnaseq",
            "kallisto",
            "mouse",
            "unknown",
            "/mnt/space4/BulkRNA/Kallisto_v1/results_kallisto",
            "All conditions in results_kallisto/; t2g mapping required",
        ),
    ]

    inserted = 0
    skipped = 0
    for sample_id, project, data_type, platform, species, tissue, l3_path, notes in samples:
        existing = con.execute(
            "SELECT 1 FROM sample_registry WHERE sample_id = ?", [sample_id]
        ).fetchone()
        if existing:
            skipped += 1
            continue
        con.execute(
            """
            INSERT INTO sample_registry
                (sample_id, project, data_type, platform,
                 species, tissue, l3_path, added_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'init_script', ?)
            """,
            [sample_id, project, data_type, platform, species, tissue, l3_path, notes],
        )
        inserted += 1

    print(f"sample_registry: {inserted} inserted, {skipped} already exist")


def verify(con: duckdb.DuckDBPyConnection):
    print("\n--- Verification ---")
    rows = con.execute(
        "SELECT sample_id, data_type, platform, species, tissue FROM sample_registry"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]}  [{r[1]}/{r[2]}]  {r[3]} / {r[4]}")

    tables = con.execute(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_type, table_name"
    ).fetchall()
    print("\nSchema objects:")
    for t in tables:
        print(f"  {t[1]:<10} {t[0]}")


if __name__ == "__main__":
    con = init_db()
    populate_registry(con)
    verify(con)
    con.close()
    print("\nDone. bio_memory.duckdb initialized.")
