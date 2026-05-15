"""
Pytest fixtures shared across all tests.
"""
import pytest
import duckdb
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_db(tmp_path):
    """In-memory DuckDB for testing — never touches the real bio_memory.duckdb."""
    db_path = tmp_path / "test_bio_memory.duckdb"
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


@pytest.fixture
def l3_crc_path():
    """Path to CRC test data (read-only L3)."""
    path = Path("/Volumes/NO NAME/bio_DB/crc_visium_data/official_v4")
    if not path.exists():
        pytest.skip("CRC test data not available")
    return path
