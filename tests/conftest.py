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


@pytest.fixture(scope="session")
def web_app_client():
    """Session-scoped TestClient for server.web_app.app。

    `bio_memory_server.StreamableHTTPSessionManager.run()` 每實例只能呼叫一次，
    且 `web_app.app` 是 module 級 singleton——多個測試各自建 TestClient(app)
    會撞到 RuntimeError。session 內共用單一 client 即可。
    """
    from starlette.testclient import TestClient
    from server.web_app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


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
