"""/results/<analysis_id> 報告頁修復後的單元測試。

回歸保護：
    - 目錄類 result_path（dynamic_code / l2_convert）不再 IsADirectoryError → 500
    - _synthesize_archive_markdown 正確吸收 meta / code / output / 圖片
    - 相對 result_path 以 BIO_DB_ROOT 為基底（不依賴 CWD）
"""
from __future__ import annotations

import base64
import json

import pytest


# ── _synthesize_archive_markdown 單元測試 ──────────────────────────────────

def test_synthesize_includes_meta_code_output(tmp_path):
    from server.web_app import _synthesize_archive_markdown

    arch = tmp_path / "2026-05-20_abc12345"
    arch.mkdir()
    (arch / "meta.json").write_text(
        json.dumps({"analysis_id": "abc", "status": "completed", "code_lines": 12},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (arch / "code.py").write_text("print('hello')\n", encoding="utf-8")
    (arch / "output.txt").write_text("hello\n", encoding="utf-8")

    md = _synthesize_archive_markdown(arch)

    assert "## Meta" in md
    assert '"analysis_id"' in md
    assert "## code.py" in md
    assert "print('hello')" in md
    assert "## output.txt" in md
    assert "hello" in md


def test_synthesize_inlines_figures_as_base64(tmp_path):
    from server.web_app import _synthesize_archive_markdown

    arch = tmp_path / "x"
    arch.mkdir()
    fig_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    (arch / "fig_00.png").write_bytes(fig_bytes)

    md = _synthesize_archive_markdown(arch)

    assert "## Figures" in md
    expected_b64 = base64.b64encode(fig_bytes).decode()
    assert f"data:image/png;base64,{expected_b64}" in md


def test_synthesize_lists_unknown_files_with_size(tmp_path):
    from server.web_app import _synthesize_archive_markdown

    arch = tmp_path / "x"
    arch.mkdir()
    (arch / "feature.parquet").write_bytes(b"\x00" * 2048)  # 2 KB

    md = _synthesize_archive_markdown(arch)
    assert "其他檔案" in md
    assert "feature.parquet" in md
    assert "2.0 KB" in md


def test_synthesize_handles_traceback(tmp_path):
    """失敗的 dynamic_code 應渲染 traceback.txt。"""
    from server.web_app import _synthesize_archive_markdown

    arch = tmp_path / "x"
    arch.mkdir()
    (arch / "traceback.txt").write_text("NameError: x is not defined\n", encoding="utf-8")

    md = _synthesize_archive_markdown(arch)
    assert "## traceback.txt" in md
    assert "NameError" in md


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
        assert resp.status_code != 500, (
            f"{atype} 報告頁 500 回歸；body={resp.text[:200]!r}"
        )
        # 200（成功）或 404（路徑不存在/已遷移）皆可接受，但不可 500
        assert resp.status_code in (200, 404), (
            f"{atype} unexpected status {resp.status_code}"
        )
