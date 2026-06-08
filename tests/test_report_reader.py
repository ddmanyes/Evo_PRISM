"""Unit tests for analysis.report_reader path sandbox + truncation logic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis import report_reader as rr  # noqa: E402


@pytest.fixture
def fake_results_root(tmp_path, monkeypatch):
    """Override ALLOWED_ROOTS so tests don't depend on the real results/ tree."""
    sandbox = (tmp_path / "results").resolve()
    sandbox.mkdir()
    monkeypatch.setattr(rr, "ALLOWED_ROOTS", (sandbox,))
    monkeypatch.setattr(rr, "BIO_DB_ROOT", tmp_path)
    return sandbox


# ── path policy ─────────────────────────────────────────────────────────────


class TestPathPolicy:
    def test_accepts_md_inside_sandbox(self, fake_results_root):
        f = fake_results_root / "ok.md"
        f.write_text("hello", encoding="utf-8")
        result = rr.read_report(str(f))
        assert result.head == "hello"
        assert result.truncated is False

    def test_rejects_absolute_outside_sandbox(self, fake_results_root):
        with pytest.raises(rr.ReportReadError, match="outside allowed roots"):
            rr.read_report("/etc/passwd")

    def test_rejects_path_traversal(self, fake_results_root, tmp_path):
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(rr.ReportReadError, match="outside allowed roots"):
            rr.read_report(f"results/../{outside.name}")

    def test_rejects_symlink_escape(self, fake_results_root, tmp_path):
        secret = tmp_path / "secret.md"
        secret.write_text("nope", encoding="utf-8")
        link = fake_results_root / "link.md"
        link.symlink_to(secret)
        with pytest.raises(rr.ReportReadError, match="outside allowed roots"):
            rr.read_report(str(link))

    def test_rejects_disallowed_extension(self, fake_results_root):
        f = fake_results_root / "data.parquet"
        f.write_bytes(b"PAR1")
        with pytest.raises(rr.ReportReadError, match="extension not allowed"):
            rr.read_report(str(f))

    def test_rejects_missing_file(self, fake_results_root):
        with pytest.raises(rr.ReportReadError, match="file not found"):
            rr.read_report(str(fake_results_root / "missing.md"))

    def test_rejects_directory(self, fake_results_root):
        sub = fake_results_root / "sub"
        sub.mkdir()
        with pytest.raises(rr.ReportReadError):
            rr.read_report(str(sub))

    def test_rejects_empty_path(self, fake_results_root):
        with pytest.raises(rr.ReportReadError, match="empty path"):
            rr.read_report("")

    def test_accepts_relative_path_anchored_to_bio_db_root(self, fake_results_root):
        f = fake_results_root / "rel.md"
        f.write_text("relative ok", encoding="utf-8")
        result = rr.read_report("results/rel.md")
        assert result.head == "relative ok"


# ── truncation ──────────────────────────────────────────────────────────────


class TestTruncation:
    def test_short_file_returns_full_text(self, fake_results_root):
        f = fake_results_root / "short.md"
        f.write_text("abcdef", encoding="utf-8")
        result = rr.read_report(str(f), max_chars=100)
        assert result.truncated is False
        assert result.head == "abcdef"
        assert result.tail == ""

    def test_long_file_truncates_head_and_tail(self, fake_results_root):
        f = fake_results_root / "long.md"
        text = "H" * 600 + "M" * 200 + "T" * 200  # 1000 chars
        f.write_text(text, encoding="utf-8")
        result = rr.read_report(str(f), max_chars=100, head_fraction=0.5)
        assert result.truncated is True
        assert result.total_chars == 1000
        assert len(result.head) == 50
        assert len(result.tail) == 50
        assert result.head.startswith("H")
        assert result.tail.endswith("T")

    def test_invalid_max_chars_raises(self, fake_results_root):
        f = fake_results_root / "x.md"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(rr.ReportReadError, match="max_chars"):
            rr.read_report(str(f), max_chars=0)

    def test_invalid_head_fraction_raises(self, fake_results_root):
        f = fake_results_root / "x.md"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(rr.ReportReadError, match="head_fraction"):
            rr.read_report(str(f), head_fraction=1.5)
