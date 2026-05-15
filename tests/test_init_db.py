"""
Tests for scripts/00_init_db.py — verifies schema creation.
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_sample_registry_schema(tmp_db):
    from scripts.init_db import init_db
    init_db(tmp_db)

    result = tmp_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'sample_registry' ORDER BY column_name"
    ).fetchall()
    columns = {r[0] for r in result}

    assert "sample_id" in columns
    assert "l3_path" in columns
    assert "l2_ready" in columns
    assert "analysis_done" in columns


def test_analysis_history_schema(tmp_db):
    from scripts.init_db import init_db
    init_db(tmp_db)

    result = tmp_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'analysis_history' ORDER BY column_name"
    ).fetchall()
    columns = {r[0] for r in result}

    assert "analysis_id" in columns
    assert "sample_id" in columns
    assert "status" in columns
    assert "summary" in columns


def test_analysis_index_view_exists(tmp_db):
    from scripts.init_db import init_db
    init_db(tmp_db)

    views = tmp_db.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type = 'VIEW'"
    ).fetchall()
    view_names = {r[0] for r in views}
    assert "analysis_index" in view_names


def test_no_data_in_clean_db(tmp_db):
    from scripts.init_db import init_db
    init_db(tmp_db)

    count = tmp_db.execute("SELECT COUNT(*) FROM sample_registry").fetchone()[0]
    assert count == 0
