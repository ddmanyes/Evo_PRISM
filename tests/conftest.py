"""
Pytest fixtures shared across all tests.
"""
import pytest
import duckdb
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import L3_ROOT  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    """In-memory DuckDB for testing — never touches the real bio_memory.duckdb."""
    db_path = tmp_path / "test_bio_memory.duckdb"
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


@pytest.fixture
def l3_crc_path():
    """Path to CRC test data (read-only L3).

    Resolves via ``config.settings.L3_ROOT`` so the fixture is portable across
    machines. Skips the test if the CRC test bundle is not present locally.
    """
    path = L3_ROOT / "official_v4"
    if not path.exists():
        pytest.skip(f"CRC test data not available at {path}")
    return path
