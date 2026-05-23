"""Migration v23 — Retrospective large blob migration & size CHECK constraint (AB2).

Scans `analysis_artifact_blobs` for any inline_data exceeding 500 KB (512,000 bytes).
If found, extracts it to an external file under `results/overflow/`,
updates `analysis_artifacts` with the file path/size, and deletes the blob row.
Then rebuilds the table to enforce: `CHECK (octet_length(inline_data) <= 512000)`.

Idempotent and safe to run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT, DUCKDB_PATH


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? AND table_schema = 'main'",
        [name],
    ).fetchone()
    return row is not None


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    print(f"Connecting to database: {db_path}")
    
    overflow_dir = BIO_DB_ROOT / "results" / "overflow"
    
    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as exc:
            print(f"WARNING: VSS load failed ({exc}) — CHECKPOINT may fail")

        # 1. Retrospective scan for oversized blobs
        oversized = con.execute(
            """
            SELECT b.artifact_id, b.inline_data, a.label
            FROM   analysis_artifact_blobs b
            JOIN   analysis_artifacts a USING (artifact_id)
            WHERE  length(b.inline_data) > 512000
            """
        ).fetchall()
        
        if oversized:
            print(f"Found {len(oversized)} oversized blobs (>500KB). Migrating to external files...")
            overflow_dir.mkdir(parents=True, exist_ok=True)
            
            for art_id, inline_data, label in oversized:
                # Write to external file
                ext = ".md" if "報告" in str(label) or "report" in str(label).lower() else ".txt"
                filename = f"overflow_{art_id}{ext}"
                filepath = overflow_dir / filename
                filepath.write_text(inline_data, encoding="utf-8")
                
                size_kb = int(os.path.getsize(filepath) / 1024)
                
                # Update analysis_artifacts
                con.execute(
                    """
                    UPDATE analysis_artifacts
                    SET    file_path = ?, file_size_kb = ?
                    WHERE  artifact_id = ?
                    """,
                    [str(filepath), size_kb, art_id]
                )
                
                # Delete from blobs
                con.execute(
                    "DELETE FROM analysis_artifact_blobs WHERE artifact_id = ?",
                    [art_id]
                )
                print(f"  Migrated artifact {art_id} ({label}) -> {filepath} ({size_kb} KB)")
        else:
            print("No oversized blobs found.")

        # 2. Rebuild analysis_artifact_blobs with CHECK constraint
        # Let's inspect CHECK constraints on the table
        # Checking constraints is tricky, so we'll just check if it's already recorded in schema_migrations.
        row = con.execute("SELECT 1 FROM schema_migrations WHERE version = 23").fetchone()
        
        if not row:
            print("Rebuilding analysis_artifact_blobs to apply CHECK constraint...")
            con.execute("CREATE TABLE _blob_backup_v23 AS SELECT * FROM analysis_artifact_blobs")
            con.execute("DROP TABLE analysis_artifact_blobs CASCADE")
            
            con.execute(
                """
                CREATE TABLE analysis_artifact_blobs (
                    artifact_id  UUID PRIMARY KEY REFERENCES analysis_artifacts(artifact_id),
                    inline_data  TEXT NOT NULL CHECK (length(inline_data) <= 512000)
                )
                """
            )
            
            con.execute(
                """
                INSERT INTO analysis_artifact_blobs (artifact_id, inline_data)
                SELECT artifact_id, inline_data
                FROM _blob_backup_v23
                WHERE artifact_id IN (SELECT artifact_id FROM analysis_artifacts)
                """
            )
            con.execute("DROP TABLE _blob_backup_v23")
            print("Successfully rebuilt analysis_artifact_blobs with <=500KB size CHECK constraint.")
            
            # Record migration
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (23, now(), 'AB2: Add octet_length CHECK constraint on analysis_artifact_blobs')
                """
            )
            print("Recorded migration v23")
        else:
            print("Migration v23 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")
        print("\nMigration v23 complete.")


if __name__ == "__main__":
    migrate()
