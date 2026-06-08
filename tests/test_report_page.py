"""/results/<analysis_id> 報告頁的單元測試。

涵蓋：
    - dynamic_code 視圖（description/badge/code/output/figures/traceback）
    - 通用目錄瀏覽視圖（l2_convert 的 parquet 列表 + schema preview）
    - _synthesize_archive_view 派發器依目錄內容路由
    - _resolve_result_path 相對路徑以 BIO_DB_ROOT 為基底
    - HTTP 整合：四種 analysis_type 都不再 500
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest


# ── _synthesize_dynamic_code_view（meta + code 結構） ─────────────────────


def _mk_dynamic_archive(arch: Path, **meta_extra) -> None:
    """測試 helper：建一個最小可行的 dynamic_code 目錄（meta + code）。"""
    arch.mkdir()
    default_meta = {
        "analysis_id": "abc",
        "description": "Top variable genes",
        "status": "completed",
        "code_lines": 12,
        "duration_sec": 0.42,
    }
    default_meta.update(meta_extra)
    (arch / "meta.json").write_text(json.dumps(default_meta, ensure_ascii=False), encoding="utf-8")
    (arch / "code.py").write_text("print('hello')\n", encoding="utf-8")


def test_dynamic_code_view_basic_layout(tmp_path):
    """description 當 H1、status badge、程式碼/輸出 區塊。"""
    from server.web_app import _synthesize_archive_view

    arch = tmp_path / "2026-05-20_abc12345"
    _mk_dynamic_archive(arch)
    (arch / "output.txt").write_text("hello\n", encoding="utf-8")

    md = _synthesize_archive_view(arch)

    assert "<h1" in md and "Top variable genes" in md
    assert "✓ 完成" in md
    assert "12 行程式碼" in md
    assert "<details" in md and "顯示 meta.json" in md
    assert "## 程式碼" in md and "print('hello')" in md
    assert "## 輸出" in md and "hello" in md


def test_dynamic_code_view_inlines_figures(tmp_path):
    """dynamic_code 視圖把 fig_*.png 以 inline base64 嵌入。"""
    from server.web_app import _synthesize_archive_view

    arch = tmp_path / "x"
    _mk_dynamic_archive(arch)
    fig_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    (arch / "fig_00.png").write_bytes(fig_bytes)

    md = _synthesize_archive_view(arch)

    assert "## 圖" in md
    expected_b64 = base64.b64encode(fig_bytes).decode()
    assert f"data:image/png;base64,{expected_b64}" in md


def test_dynamic_code_view_handles_traceback(tmp_path):
    """失敗的 dynamic_code → 紅色橫幅 + error_summary + traceback。"""
    from server.web_app import _synthesize_archive_view

    arch = tmp_path / "x"
    _mk_dynamic_archive(
        arch,
        status="failed",
        description="buggy run",
        error_summary="NameError: name 'x' is not defined",
    )
    (arch / "traceback.txt").write_text(
        "Traceback (most recent call last):\n  ...\nNameError: name 'x' is not defined\n",
        encoding="utf-8",
    )

    md = _synthesize_archive_view(arch)
    assert "× 失敗" in md
    assert "border-left:4px solid #ef4444" in md
    assert "執行失敗" in md
    assert "NameError" in md
    assert "Traceback" in md


# ── _synthesize_directory_browser_view（l2_convert / 通用） ────────────────


def test_directory_browser_lists_parquet_with_size(tmp_path):
    """通用目錄瀏覽：列出 parquet 檔 + 大小（不渲染 dynamic_code 元素）。"""
    from server.web_app import _synthesize_archive_view

    silver = tmp_path / "silver" / "sample1"
    silver.mkdir(parents=True)
    (silver / "expression.parquet").write_bytes(b"\x00" * 3072)  # 3.0 KB
    (silver / "obs.parquet").write_bytes(b"\x00" * 1024)  # 1.0 KB

    md = _synthesize_archive_view(silver)

    # 通用視圖標題（資料夾名 + 📁 emoji）
    assert "📁" in md and "sample1" in md
    # 不該出現 dynamic_code 專屬元素
    assert "程式碼" not in md
    assert "✓ 完成" not in md and "× 失敗" not in md
    # 應列出兩個 parquet
    assert "expression.parquet" in md and "3.0 KB" in md
    assert "obs.parquet" in md and "1.0 KB" in md
    # 副檔名分組
    assert ".parquet（2）" in md


def test_directory_browser_handles_empty_dir(tmp_path):
    from server.web_app import _synthesize_archive_view

    empty = tmp_path / "empty"
    empty.mkdir()

    md = _synthesize_archive_view(empty)
    assert "📁" in md and "empty" in md
    assert "資料夾為空" in md


# ── 派發器路由 ─────────────────────────────────────────────────────────────


def test_dispatcher_routes_dynamic_code_vs_browser(tmp_path):
    """有 meta.json + code.py → dynamic_code 視圖；無 → 通用瀏覽。"""
    from server.web_app import _synthesize_archive_view

    # 路徑 A：dynamic_code 結構
    a = tmp_path / "dyn"
    _mk_dynamic_archive(a)
    md_a = _synthesize_archive_view(a)
    assert "Top variable genes" in md_a  # description 當標題（dynamic_code 路徑）

    # 路徑 B：純資料夾（只有一個 parquet）
    b = tmp_path / "data"
    b.mkdir()
    (b / "x.parquet").write_bytes(b"\x00" * 1024)
    md_b = _synthesize_archive_view(b)
    assert "📁" in md_b  # 通用瀏覽路徑
    assert "Top variable genes" not in md_b  # 不該洩漏 A 的內容
    assert "✓ 完成" not in md_b  # 不該有 status badge


# ── _resolve_result_path 行為 ──────────────────────────────────────────────


def test_resolve_relative_uses_bio_db_root(monkeypatch, tmp_path):
    """相對 result_path 應以 BIO_DB_ROOT 為基底解析，不依賴 CWD。"""
    from server import web_app as wa

    monkeypatch.setattr(wa, "BIO_DB_ROOT", tmp_path)
    # 故意 chdir 到他處，驗證解析仍以 tmp_path 為基底
    monkeypatch.chdir(tmp_path.parent)

    resolved = wa._resolve_result_path("results/sub/file.md")
    assert resolved == (tmp_path / "results/sub/file.md").resolve()


def test_resolve_absolute_path_unchanged(tmp_path):
    from server.web_app import _resolve_result_path

    abs_path = tmp_path / "abs" / "file.md"
    resolved = _resolve_result_path(str(abs_path))
    assert resolved == abs_path.resolve()


# ── /results/{analysis_id} 整合（用真實 DB，只驗 status code） ──────────────


def test_results_route_does_not_500_for_any_type(web_app_client):
    """回歸保護：四種分析類型 GET /results/{id} 都不該再 500。"""
    import duckdb
    from config.settings import DUCKDB_PATH

    types = ["dynamic_code", "bulk_eda", "eda_report", "l2_convert"]
    samples: dict[str, str] = {}
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        for atype in types:
            row = con.execute(
                """
                SELECT analysis_id::VARCHAR
                FROM analysis_history
                WHERE analysis_type = ? AND status='completed' AND result_path IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1
                """,
                [atype],
            ).fetchone()
            if row:
                samples[atype] = row[0]

    if not samples:
        pytest.skip("DB 無任何完成紀錄可測")

    for atype, aid in samples.items():
        resp = web_app_client.get(f"/results/{aid}")
        assert resp.status_code != 500, f"{atype} 報告頁 500 回歸；body={resp.text[:200]!r}"
        # 200（成功）或 404（路徑不存在/已遷移）皆可接受，但不可 500
        assert resp.status_code in (200, 404), f"{atype} unexpected status {resp.status_code}"
