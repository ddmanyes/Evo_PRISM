"""工具語意搜尋（analysis/tool_search.py）測試。

不依賴 port 8081：用「關鍵字 one-hot」假 embedder 取代 embed_text，
讓餘弦相似度反映關鍵字重疊——如此可透過**真實 DuckDB HNSW** 驗證排序與門檻邏輯。
"""

from __future__ import annotations

import duckdb
import pytest

from analysis import tool_search as tsx
from config.settings import EMBEDDING_DIM

# 假 embedder 的詞彙表：每個關鍵字佔一個維度
_VOCAB = ["pathway", "score", "ssgsea", "timeseries", "log2", "fold", "mean", "tpm"]


def _fake_embed(text: str, provider=None):
    t = text.lower()
    v = [0.0] * EMBEDDING_DIM
    for i, kw in enumerate(_VOCAB):
        if kw in t:
            v[i] = 1.0
    v[-1] = 0.01  # 保底維度，避免全零向量（餘弦未定義）
    return v


@pytest.fixture
def patched_embed(monkeypatch):
    # tool_search 內以 `from analysis.embed import embed_text` 延遲匯入 → patch 來源即可
    monkeypatch.setattr("analysis.embed.embed_text", _fake_embed)


@pytest.fixture
def catalog(tmp_path, patched_embed):
    """索引兩個輕量模組（pathway_scoring + bulk_timeseries）到 tmp catalog。"""
    db = tmp_path / "catalog.duckdb"
    res = tsx.index_modules(
        modules=("analysis.pathway_scoring", "analysis.bulk_timeseries"),
        cache_path=db,
    )
    return db, res


# ── schema / 索引 ─────────────────────────────────────────────────────────────


def test_ensure_schema_idempotent(tmp_path):
    db = tmp_path / "c.duckdb"
    with duckdb.connect(str(db)) as con:
        tsx._setup_vss(con)
        tsx.ensure_schema(con)
        tsx.ensure_schema(con)  # 第二次不應報錯
        cols = [r[1] for r in con.execute("PRAGMA table_info('tool_catalog')").fetchall()]
    assert {"name", "kind", "signature", "summary", "embedding", "source_hash"} <= set(cols)


def test_index_modules_indexes_functions(catalog):
    _db, res = catalog
    assert res["indexed"] > 0
    assert res["errors"] == []


def test_index_modules_idempotent_skips_unchanged(tmp_path, patched_embed):
    db = tmp_path / "c.duckdb"
    first = tsx.index_modules(modules=("analysis.bulk_timeseries",), cache_path=db)
    second = tsx.index_modules(modules=("analysis.bulk_timeseries",), cache_path=db)
    assert first["indexed"] > 0
    # 內容沒變 → 第二次全部 skipped（不重算 embedding）
    assert second["indexed"] == 0
    assert second["skipped"] == first["indexed"]


# ── 搜尋排序 / 門檻 ───────────────────────────────────────────────────────────


def test_search_ranks_pathway_first(catalog):
    db, _ = catalog
    out = tsx.search_tools("pathway score ssgsea", cache_path=db, threshold=0.1)
    assert out, "應至少命中一個工具"
    assert "pathway_scoring" in out[0]["module_path"]
    assert out[0]["score"] >= out[-1]["score"]  # 降冪


def test_search_ranks_timeseries_first(catalog):
    db, _ = catalog
    out = tsx.search_tools("timeseries log2 fold", cache_path=db, threshold=0.1)
    assert out
    assert "bulk_timeseries" in out[0]["module_path"]


def test_search_threshold_filters(catalog):
    db, _ = catalog
    # 門檻拉到 0.99：one-hot 完全比對才過，無關 query 應全部濾掉
    out = tsx.search_tools("completely unrelated query xyz", cache_path=db, threshold=0.99)
    assert out == []


def test_search_returns_import_hint(catalog):
    db, _ = catalog
    out = tsx.search_tools("pathway score", cache_path=db, threshold=0.1)
    top = out[0]
    assert top["import_hint"].startswith("from analysis.")
    assert top["kind"] == "function"


def test_search_missing_db_returns_empty(tmp_path, patched_embed):
    assert tsx.search_tools("anything", cache_path=tmp_path / "nope.duckdb") == []


def test_search_empty_catalog_returns_empty(tmp_path, patched_embed):
    db = tmp_path / "empty.duckdb"
    tsx.index_modules(modules=(), cache_path=db)  # 建 schema 但無資料
    assert tsx.search_tools("anything", cache_path=db) == []


# ── register_tool 掛勾（畢業自動進 catalog）───────────────────────────────────


def test_index_registered_tool_is_searchable(tmp_path, patched_embed):
    db = tmp_path / "c.duckdb"
    status = tsx.index_registered_tool(
        "bio_run_pathway_scoring",
        "analysis.pathway_scoring",
        "score_pathways",
        "ssgsea pathway score 路徑評分工具",
        cache_path=db,
    )
    assert status == "indexed"
    out = tsx.search_tools("pathway score ssgsea", cache_path=db, threshold=0.1)
    names = [r["name"] for r in out]
    assert "bio_run_pathway_scoring" in names
    assert any(r["kind"] == "tool" for r in out)
