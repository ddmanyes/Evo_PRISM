"""
HELIX-Vision unit tests — analysis/tool_visualizer.py

Covers:
  - compute_loc: correct count, None on failure
  - compute_halstead_volume: float or None
  - compute_complexity: int or None
  - render_diagnosis_snapshot: returns data URI string
  - downsample_snapshot: returns smaller base64 data URI
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers — imported from non-test module so pytest assertion rewriting
# does not break `inspect.getsource()` for these stubs.
# ---------------------------------------------------------------------------

from tests._visualizer_stubs import simple_fn as _simple_fn  # noqa: E402
from tests._visualizer_stubs import branchy_fn as _branchy_fn  # noqa: E402


# ---------------------------------------------------------------------------
# compute_loc
# ---------------------------------------------------------------------------

class TestComputeLoc:
    def test_returns_positive_int(self):
        from analysis.tool_visualizer import compute_loc
        result = compute_loc(_simple_fn)
        assert isinstance(result, int)
        assert result > 0

    def test_branchy_has_more_loc_than_simple(self):
        from analysis.tool_visualizer import compute_loc
        assert compute_loc(_branchy_fn) > compute_loc(_simple_fn)

    def test_builtin_returns_none(self):
        from analysis.tool_visualizer import compute_loc
        assert compute_loc(len) is None


# ---------------------------------------------------------------------------
# compute_halstead_volume
# ---------------------------------------------------------------------------

class TestComputeHalsteadVolume:
    def test_returns_float_or_none(self):
        from analysis.tool_visualizer import compute_halstead_volume
        result = compute_halstead_volume(_simple_fn)
        assert result is None or isinstance(result, float)

    def test_positive_when_available(self):
        from analysis.tool_visualizer import compute_halstead_volume
        result = compute_halstead_volume(_branchy_fn)
        if result is not None:
            assert result > 0

    def test_builtin_returns_none(self):
        from analysis.tool_visualizer import compute_halstead_volume
        assert compute_halstead_volume(len) is None


# ---------------------------------------------------------------------------
# compute_complexity
# ---------------------------------------------------------------------------

class TestComputeComplexity:
    def test_simple_fn_low_cc(self):
        from analysis.tool_visualizer import compute_complexity
        cc = compute_complexity(_simple_fn)
        assert cc is None or cc <= 2

    def test_branchy_fn_higher_than_simple(self):
        from analysis.tool_visualizer import compute_complexity
        cc_s = compute_complexity(_simple_fn)
        cc_b = compute_complexity(_branchy_fn)
        if cc_s is not None and cc_b is not None:
            assert cc_b >= cc_s

    def test_builtin_returns_none(self):
        from analysis.tool_visualizer import compute_complexity
        assert compute_complexity(len) is None


# ---------------------------------------------------------------------------
# render_diagnosis_snapshot
# ---------------------------------------------------------------------------

class TestRenderDiagnosisSnapshot:
    def test_returns_data_uri(self):
        from analysis.tool_visualizer import render_diagnosis_snapshot
        uri = render_diagnosis_snapshot(
            tool_name="test_tool",
            fn=_simple_fn,
            diagnosis_text="test diagnosis",
        )
        assert uri.startswith("data:image/png;base64,")

    def test_with_revision_history(self):
        from analysis.tool_visualizer import render_diagnosis_snapshot
        history = [
            {"revision": 1, "old_hash": "aaa", "new_hash": "bbb",
             "changed_at": "2026-01-01", "reason": "init"},
            {"revision": 2, "old_hash": "bbb", "new_hash": "ccc",
             "changed_at": "2026-02-01", "reason": "fix"},
        ]
        uri = render_diagnosis_snapshot(
            tool_name="t",
            fn=_branchy_fn,
            diagnosis_text="unstable",
            revision_history=history,
            complexity=5,
        )
        assert uri.startswith("data:image/png;base64,")

    def test_downsample_factor_reduces_size(self):
        from analysis.tool_visualizer import render_diagnosis_snapshot
        full = render_diagnosis_snapshot("t", _simple_fn, "d", downsample_factor=1.0)
        half = render_diagnosis_snapshot("t", _simple_fn, "d", downsample_factor=0.5)
        assert len(half) < len(full)


# ---------------------------------------------------------------------------
# downsample_snapshot
# ---------------------------------------------------------------------------

class TestDownsampleSnapshot:
    def test_returns_data_uri(self):
        from analysis.tool_visualizer import render_diagnosis_snapshot, downsample_snapshot
        uri = render_diagnosis_snapshot("t", _simple_fn, "d")
        result = downsample_snapshot(uri, factor=0.5)
        assert result.startswith("data:image/png;base64,")

    def test_output_smaller_than_input(self):
        from analysis.tool_visualizer import render_diagnosis_snapshot, downsample_snapshot
        uri = render_diagnosis_snapshot("t", _simple_fn, "d")
        half = downsample_snapshot(uri, factor=0.5)
        assert len(half) < len(uri)

    def test_quarter_smaller_than_half(self):
        from analysis.tool_visualizer import render_diagnosis_snapshot, downsample_snapshot
        uri  = render_diagnosis_snapshot("t", _branchy_fn, "d")
        half = downsample_snapshot(uri, factor=0.5)
        qtr  = downsample_snapshot(uri, factor=0.25)
        assert len(qtr) < len(half)
