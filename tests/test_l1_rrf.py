"""
Tests for 3-way RRF L1 cache (paper §2.4.1).

覆蓋：
  1. compute_input_fingerprint / compute_context_hash 工具函數
  2. write_to_l1_cache 儲存 fingerprint + context_hash
  3. semantic_search — 向後相容模式（純 cosine）
  4. semantic_search — RRF 命中：三路全符
  5. semantic_search — RRF 失效模式一：fingerprint 不符 → cache miss
  6. semantic_search — RRF 失效模式三：context_hash 不符 → cache miss
  7. _rrf_score / _rrf_hit_threshold 數學驗證

所有測試使用 mock embedding（不需 llama-server）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def l1_db(tmp_path) -> Path:
    """建立空的 L1 cache DB（含新欄位）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "init_l1",
        Path(__file__).parent.parent / "scripts" / "03_init_l1_cache.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_path = tmp_path / "test_rrf.duckdb"
    con = mod.init_l1_cache(cache_path=db_path)
    con.close()
    return db_path


def _fake_embed(dim: int = 1024) -> list[float]:
    """全 1 的假向量（cosine=1.0 對自身）。"""
    v = [1.0 / (dim ** 0.5)] * dim
    return v


def _insert(
    db_path: Path,
    *,
    query_text: str = "test query",
    input_fingerprint: str | None = None,
    context_hash: str | None = None,
    sample_id: str = "s1",
) -> str:
    """直接插入一筆假記錄（繞過 embedding server）。"""
    from config.settings import EMBEDDING_DIM

    rec_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=7)
    emb = _fake_embed(EMBEDDING_DIM)

    with duckdb.connect(str(db_path)) as con:
        try:
            con.execute("LOAD vss")
            con.execute("SET hnsw_enable_experimental_persistence = true")
        except Exception:
            pass
        con.execute(
            """
            INSERT INTO memory_recent
                (id, sample_id, query_text, report_text, summary,
                 embedding, analysis_id, input_fingerprint, context_hash,
                 created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            [rec_id, sample_id, query_text, "report", "summary",
             emb, input_fingerprint, context_hash, now, expires_at],
        )
        con.execute("CHECKPOINT")
    return rec_id


# ── 工具函數測試 ───────────────────────────────────────────────────────────────


def test_compute_input_fingerprint_raw():
    from analysis.l1_cache import compute_input_fingerprint

    fp = compute_input_fingerprint(raw_content="hello world")
    assert len(fp) == 16
    assert fp == compute_input_fingerprint(raw_content="hello world")  # deterministic
    assert fp != compute_input_fingerprint(raw_content="hello world2")


def test_compute_input_fingerprint_files(tmp_path):
    from analysis.l1_cache import compute_input_fingerprint

    f1 = tmp_path / "a.csv"
    f1.write_text("gene,count\nTP53,100")
    fp1 = compute_input_fingerprint(file_paths=[f1])
    assert len(fp1) == 16

    f1.write_text("gene,count\nTP53,999")  # 內容變更
    fp2 = compute_input_fingerprint(file_paths=[f1])
    assert fp1 != fp2, "內容變更應產生不同 fingerprint"


def test_compute_context_hash():
    from analysis.l1_cache import compute_context_hash

    h1 = compute_context_hash("s1", tool_ids=["t1", "t2"], env_info="py3.11")
    h2 = compute_context_hash("s1", tool_ids=["t2", "t1"], env_info="py3.11")  # 順序不同
    assert h1 == h2, "tool_ids 順序應不影響 hash"

    h3 = compute_context_hash("s2", tool_ids=["t1", "t2"], env_info="py3.11")
    assert h1 != h3, "不同 sample_id 應產生不同 hash"


# ── write_to_l1_cache 儲存驗證 ─────────────────────────────────────────────────


def test_write_stores_fingerprint_and_context(l1_db):
    from analysis.l1_cache import compute_context_hash, compute_input_fingerprint, write_to_l1_cache
    from config.settings import EMBEDDING_DIM

    fp = compute_input_fingerprint(raw_content="data_v1")
    ctx = compute_context_hash("s1", tool_ids=["bio_run_bulk_eda"])

    with patch("analysis.embed.embed_text", return_value=_fake_embed(EMBEDDING_DIM)):
        rec_id = write_to_l1_cache(
            "s1", "bulk EDA query", "report text", "summary",
            input_fingerprint=fp,
            context_hash=ctx,
            cache_path=l1_db,
            embedding_provider="mock",
        )

    with duckdb.connect(str(l1_db)) as con:
        row = con.execute(
            "SELECT input_fingerprint, context_hash FROM memory_recent WHERE id = ?",
            [rec_id],
        ).fetchone()

    assert row is not None
    assert row[0] == fp
    assert row[1] == ctx


# ── semantic_search：向後相容（純 cosine） ─────────────────────────────────────


def test_backward_compat_no_rrf(l1_db):
    """未提供 fingerprint/context → 純 cosine，不做 RRF 過濾。"""
    from analysis.l1_cache import semantic_search
    from config.settings import EMBEDDING_DIM

    _insert(l1_db, query_text="bulk EDA", input_fingerprint="fp_A", context_hash="ctx_A")

    with patch("analysis.embed.embed_text", return_value=_fake_embed(EMBEDDING_DIM)):
        results = semantic_search("bulk EDA", threshold=0.5, cache_path=l1_db, embedding_provider="mock")

    assert len(results) >= 1
    assert "rrf_score" not in results[0]  # 純 cosine 模式不附 rrf_score


# ── semantic_search：3-way RRF 命中 ──────────────────────────────────────────


def test_rrf_hit_all_match(l1_db):
    """三路全符 → 命中。"""
    from analysis.l1_cache import semantic_search
    from config.settings import EMBEDDING_DIM

    fp = "abc123def456abcd"
    ctx = "ctx123hash456ctx1"
    _insert(l1_db, query_text="bulk EDA", input_fingerprint=fp, context_hash=ctx)

    with patch("analysis.embed.embed_text", return_value=_fake_embed(EMBEDDING_DIM)):
        results = semantic_search(
            "bulk EDA", threshold=0.5,
            input_fingerprint=fp, context_hash=ctx,
            cache_path=l1_db, embedding_provider="mock",
        )

    assert len(results) == 1
    assert "rrf_score" in results[0]
    assert results[0]["rrf_score"] > 0


# ── 失效模式一：fingerprint 不符 ──────────────────────────────────────────────


def test_rrf_miss_fingerprint_changed(l1_db):
    """fingerprint 不符（輸入數據已更新）→ cache miss。"""
    from analysis.l1_cache import semantic_search
    from config.settings import EMBEDDING_DIM

    fp_old = "old_fingerprint12"
    fp_new = "new_fingerprint12"
    ctx = "ctx_stable_12345"
    _insert(l1_db, query_text="bulk EDA", input_fingerprint=fp_old, context_hash=ctx)

    with patch("analysis.embed.embed_text", return_value=_fake_embed(EMBEDDING_DIM)):
        results = semantic_search(
            "bulk EDA", threshold=0.5,
            input_fingerprint=fp_new,  # 數據已更新
            context_hash=ctx,
            cache_path=l1_db, embedding_provider="mock",
        )

    assert results == [], "fingerprint 不符應返回空列表（cache miss）"


# ── 失效模式三：context_hash 不符 ────────────────────────────────────────────


def test_rrf_miss_context_changed(l1_db):
    """context_hash 不符（不同樣本）→ cache miss。"""
    from analysis.l1_cache import semantic_search
    from config.settings import EMBEDDING_DIM

    fp = "fp_stable_1234567"
    ctx_s1 = "ctx_sample1_12345"
    ctx_s2 = "ctx_sample2_12345"
    _insert(l1_db, query_text="bulk EDA", input_fingerprint=fp, context_hash=ctx_s1)

    with patch("analysis.embed.embed_text", return_value=_fake_embed(EMBEDDING_DIM)):
        results = semantic_search(
            "bulk EDA", threshold=0.5,
            input_fingerprint=fp,
            context_hash=ctx_s2,  # 不同樣本
            cache_path=l1_db, embedding_provider="mock",
        )

    assert results == [], "context 不符應返回空列表（cache miss）"


# ── 失效模式二：工具版本變更 ──────────────────────────────────────────────────


def test_rrf_miss_tool_version_changed(l1_db):
    """tool_versions source_hash 不符（工具升版）→ cache miss（失效模式二）。"""
    from analysis.l1_cache import compute_context_hash, semantic_search
    from config.settings import EMBEDDING_DIM

    fp = "fp_stable_1234567"
    ctx_v1 = compute_context_hash(
        "s1",
        tool_ids=["bio_run_bulk_eda"],
        tool_versions={"bio_run_bulk_eda": "abc123"},  # 舊版 source_hash
    )
    ctx_v2 = compute_context_hash(
        "s1",
        tool_ids=["bio_run_bulk_eda"],
        tool_versions={"bio_run_bulk_eda": "def456"},  # HELIX 升版後新 source_hash
    )
    assert ctx_v1 != ctx_v2, "工具升版應產生不同 context_hash"

    _insert(l1_db, query_text="bulk EDA", input_fingerprint=fp, context_hash=ctx_v1)

    with patch("analysis.embed.embed_text", return_value=_fake_embed(EMBEDDING_DIM)):
        results = semantic_search(
            "bulk EDA", threshold=0.5,
            input_fingerprint=fp,
            context_hash=ctx_v2,  # 工具已升版
            cache_path=l1_db, embedding_provider="mock",
        )

    assert results == [], "工具升版後 context_hash 不符應返回空列表（cache miss）"


# ── _rrf_score / _rrf_hit_threshold 數學驗證 ──────────────────────────────────


def test_rrf_math():
    from analysis.l1_cache import _RRF_K, _W1, _W2, _W3, _rrf_hit_threshold, _rrf_score

    # 完美分數（所有排名=1）
    perfect = _rrf_score(1, 1, 1)
    expected = _W1 / (1 + _RRF_K) + _W2 / (1 + _RRF_K) + _W3 / (1 + _RRF_K)
    assert abs(perfect - expected) < 1e-10

    # 命中門檻 < 完美分數
    threshold = _rrf_hit_threshold(has_fp=True, has_ctx=True)
    assert threshold < perfect

    # fingerprint 失配應低於門檻
    from analysis.l1_cache import _MISMATCH_RANK
    miss_score = _rrf_score(1, _MISMATCH_RANK, 1)
    assert miss_score < threshold, "fingerprint 失配分數應低於命中門檻"

    # context 失配應低於門檻
    miss_score_ctx = _rrf_score(1, 1, _MISMATCH_RANK)
    assert miss_score_ctx < threshold, "context 失配分數應低於命中門檻"
