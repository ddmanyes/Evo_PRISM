"""figure_cache：base64 剝除 + 按需索取的單元測試。"""

import base64

import pytest

import analysis.figure_cache as fc


# 1x1 透明 PNG
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """每個測試用隔離的快取目錄，不汙染 gold/figure_cache。"""
    monkeypatch.setattr(fc, "FIGURE_CACHE_DIR", tmp_path / "figure_cache")
    yield


def _report_with_img(n: int = 1) -> str:
    imgs = "".join(f"\n![fig {i}](data:image/png;base64,{_PNG_B64})\n" for i in range(n))
    return f"# 報告\n摘要文字。\n{imgs}\n結論文字。"


def test_strip_replaces_inline_image_with_placeholder():
    text = _report_with_img(1)
    out = fc.strip_base64_for_llm(text)

    assert "base64," not in out
    assert "用 bio_get_figure 索取" in out
    assert "id=" in out
    # 周邊文字保留
    assert "摘要文字。" in out and "結論文字。" in out


def test_strip_is_deterministic_content_addressed():
    """同一張圖 → 同一個 figure_id。"""
    out1 = fc.strip_base64_for_llm(_report_with_img(1))
    out2 = fc.strip_base64_for_llm(_report_with_img(1))
    assert out1 == out2


def test_strip_shrinks_token_footprint():
    # 模擬真實報告：base64 payload 通常數萬字元
    big_b64 = base64.b64encode(b"\x89PNG\r\n" + b"\x00" * 60_000).decode()
    text = f"# 報告\n![big](data:image/png;base64,{big_b64})\n結論。"
    out = fc.strip_base64_for_llm(text)
    assert "base64," not in out
    assert len(out) < len(text) / 50  # 大幅縮減


def test_strip_passthrough_when_no_image():
    text = "純文字報告，無圖。"
    assert fc.strip_base64_for_llm(text) == text


def test_strip_passthrough_non_string():
    assert fc.strip_base64_for_llm(None) is None  # type: ignore[arg-type]


def test_cache_then_load_roundtrip():
    out = fc.strip_base64_for_llm(_report_with_img(1))
    fig_id = out.split("id=")[1].split(" ")[0]

    raw, mime = fc.load_figure(fig_id)
    assert mime == "image/png"
    assert raw == base64.b64decode(_PNG_B64)


def test_load_figure_b64_roundtrip():
    out = fc.strip_base64_for_llm(_report_with_img(1))
    fig_id = out.split("id=")[1].split(" ")[0]

    b64, mime = fc.load_figure_b64(fig_id)
    assert mime == "image/png"
    assert base64.b64decode(b64) == base64.b64decode(_PNG_B64)


def test_load_missing_figure_raises():
    with pytest.raises(FileNotFoundError):
        fc.load_figure("deadbeef0000")


def test_load_rejects_bad_id():
    with pytest.raises(ValueError):
        fc.load_figure("../etc/passwd")


def test_cache_is_idempotent():
    fc.cache_figure(_PNG_B64, "png")
    fc.cache_figure(_PNG_B64, "png")
    files = list(fc.FIGURE_CACHE_DIR.glob("*.png"))
    assert len(files) == 1


# ── prune_stale_figures ────────────────────────────────────────────────────


def test_prune_removes_only_stale():
    import os
    import time

    fig_id = fc.cache_figure(_PNG_B64, "png")
    stale = fc.FIGURE_CACHE_DIR / f"{fig_id}.png"
    # 把 mtime 推到 20 天前
    old = time.time() - 20 * 86400
    os.utime(stale, (old, old))

    fresh_id = fc.cache_figure(base64.b64encode(b"\x89PNG\r\nfresh").decode(), "png")

    deleted, freed = fc.prune_stale_figures(ttl_days=14)
    assert deleted == 1 and freed > 0
    assert not stale.exists()
    assert (fc.FIGURE_CACHE_DIR / f"{fresh_id}.png").exists()


def test_prune_dry_run_keeps_files():
    import os
    import time

    fig_id = fc.cache_figure(_PNG_B64, "png")
    stale = fc.FIGURE_CACHE_DIR / f"{fig_id}.png"
    old = time.time() - 20 * 86400
    os.utime(stale, (old, old))

    deleted, _ = fc.prune_stale_figures(ttl_days=14, dry_run=True)
    assert deleted == 1
    assert stale.exists()  # dry-run 不刪


def test_prune_missing_dir_is_noop():
    # autouse fixture 指向尚未建立的目錄
    deleted, freed = fc.prune_stale_figures(ttl_days=14)
    assert (deleted, freed) == (0, 0)
