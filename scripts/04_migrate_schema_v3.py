"""
Migration v3 — Add tool versioning tables to bio_memory.duckdb.

New tables:
    tools              — versioned registry of analysis functions
    tool_dependencies  — directed dependency graph between tools

Safe to run multiple times (all DDL uses IF NOT EXISTS).
Does NOT modify existing rows; existing tool_id NULLs in analysis_history
remain NULL until tools are registered via analysis.tool_registry.register_tool.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

# Allow running as a top-level script without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH

_TOOLS_DDL = """
CREATE TABLE IF NOT EXISTS tools (
    tool_id        UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name      VARCHAR NOT NULL,
    version        VARCHAR NOT NULL,
    content_hash   VARCHAR(16) NOT NULL,
    module_path    VARCHAR NOT NULL,
    function_name  VARCHAR NOT NULL,
    description    VARCHAR,
    parameters     JSON,
    status         VARCHAR DEFAULT 'active',
    created_at     TIMESTAMP DEFAULT now(),
    deprecated_at  TIMESTAMP,
    UNIQUE (tool_name, content_hash)
)
"""

_TOOL_DEPENDENCIES_DDL = """
CREATE TABLE IF NOT EXISTS tool_dependencies (
    tool_id      UUID,              -- 軟引用：刻意不加 REFERENCES tools(tool_id)，見 migration v20
    depends_on   UUID,              -- 軟引用：同上（DuckDB FK 會擋死 tools UPDATE/DELETE）
    PRIMARY KEY (tool_id, depends_on)
)
"""


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    """Apply schema v3 migration to *db_path*."""
    print(f"Connecting to: {db_path}")
    con = duckdb.connect(str(db_path))

    try:
        print("Creating table: tools ...", end=" ")
        con.execute(_TOOLS_DDL)
        print("OK")

        print("Creating table: tool_dependencies ...", end=" ")
        con.execute(_TOOL_DEPENDENCIES_DDL)
        print("OK")

        print("Running CHECKPOINT ...", end=" ")
        con.execute("CHECKPOINT")
        print("OK")

        # --- Verify ---
        tables = {
            row[0]
            for row in con.execute(
                """
                SELECT table_name
                FROM   information_schema.tables
                WHERE  table_schema = 'main'
                  AND  table_type   = 'BASE TABLE'
                """
            ).fetchall()
        }

        required = {"tools", "tool_dependencies"}
        missing = required - tables
        if missing:
            print(f"\nERROR: expected tables not found: {missing}", file=sys.stderr)
            sys.exit(1)

        print("\n--- Migration v3 summary ---")
        print("  tools             : present")
        print("  tool_dependencies : present")
        print(
            "\nNext step: register active tools via "
            "analysis.tool_registry.register_tool() to populate the tools table."
        )

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
    print("\nDone.")
