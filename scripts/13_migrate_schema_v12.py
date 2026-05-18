"""
Migration v12 — file_path relative paths (Phase 9-SQL P0).

Converts analysis_artifacts.file_path from absolute paths to paths
relative to BIO_DB_ROOT.  On read, use config.settings.resolve_artifact_path()
to reconstruct the absolute path — makes the DB portable across
macOS (/Volumes/NO NAME/bio_DB/) and Linux (/mnt/space4/bio_lab_db/).

Safe to run multiple times (only updates rows where file_path starts with '/').
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH, BIO_DB_ROOT


def migrate(db_path: Path = DUCKDB_PATH) -> None:
    root = str(BIO_DB_ROOT).rstrip("/") + "/"
    print(f"Connecting to: {db_path}")
    print(f"BIO_DB_ROOT  : {root}")

    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception as e:
            print(f"WARNING: VSS load failed ({e}) — continuing")

        total = con.execute(
            "SELECT COUNT(*) FROM analysis_artifacts WHERE file_path LIKE '/%'"
        ).fetchone()[0]
        print(f"Rows with absolute file_path: {total}")

        if total > 0:
            # Strip the BIO_DB_ROOT prefix using string slicing (length of root string)
            root_len = len(root)
            con.execute(
                f"""
                UPDATE analysis_artifacts
                SET    file_path = substring(file_path, {root_len + 1})
                WHERE  file_path LIKE ?
                """,
                [f"{root}%"],
            )
            converted = con.execute(
                "SELECT COUNT(*) FROM analysis_artifacts WHERE file_path NOT LIKE '/%'"
            ).fetchone()[0]
            print(f"Converted to relative paths: {converted} rows")
        else:
            print("No absolute paths found — already relative or table empty")

        existing = con.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 12"
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO schema_migrations (version, applied_at, description)
                VALUES (12, now(), 'analysis_artifacts.file_path converted to BIO_DB_ROOT-relative paths')
                """
            )
            print("Recorded migration v12")
        else:
            print("Migration v12 already recorded — skipped")

        con.execute("CHECKPOINT")
        print("CHECKPOINT OK")

        sample = con.execute(
            "SELECT file_path FROM analysis_artifacts LIMIT 3"
        ).fetchall()
        if sample:
            print("\nSample file_path values (should be relative):")
            for r in sample:
                print(f"  {r[0]}")

        print("\nMigration v12 complete.")


if __name__ == "__main__":
    migrate()
    print("\nDone.")
