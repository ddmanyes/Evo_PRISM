"""
Unit tests for HELIX Eq.(1) f_promote and Eq.(2) HealthScore formulas.

These tests verify that the mathematical formulas produce correct values
given known inputs, and that boundary conditions (clip, threshold) work.
All tests are pure — no DB connection required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Eq.(1) f_promote ─────────────────────────────────────────────────────────


class TestComputeFPromote:
    """Tests for analysis.code_promoter.compute_f_promote (Eq.1)."""

    def setup_method(self):
        from analysis.code_promoter import compute_f_promote
        self.fn = compute_f_promote

    def test_paper_example(self):
        """Paper example: ReuseCount=3, UserApproval=1, Complexity=8 → 3.4."""
        # f = 1.0*3 + 2.0*1 − 0.2*8 = 3 + 2 − 1.6 = 3.4
        score = self.fn(reuse_count=3, user_approval=1, complexity=8)
        assert abs(score - 3.4) < 1e-9

    def test_promotes_at_threshold(self):
        """f_promote exactly at θ_promote=3.0 should meet promotion threshold."""
        from config.settings import HELIX_THETA_PROMOTE
        score = self.fn(reuse_count=3, user_approval=0, complexity=0)
        # 1.0*3 + 2.0*0 − 0.2*0 = 3.0 == θ_promote
        assert score == pytest.approx(HELIX_THETA_PROMOTE)

    def test_below_threshold_no_approval_high_complexity(self):
        """High complexity without approval can push score below threshold."""
        score = self.fn(reuse_count=2, user_approval=0, complexity=20)
        # 1.0*2 + 2.0*0 − 0.2*20 = 2 − 4 = -2 < 3.0
        assert score < 3.0

    def test_approval_lifts_score(self):
        """UserApproval=1 adds 2.0 (β) to the score."""
        score_no = self.fn(reuse_count=2, user_approval=0, complexity=5)
        score_yes = self.fn(reuse_count=2, user_approval=1, complexity=5)
        assert abs(score_yes - score_no - 2.0) < 1e-9

    def test_zero_inputs(self):
        """All-zero inputs produce 0.0."""
        assert self.fn(0, 0, 0) == pytest.approx(0.0)

    def test_complexity_penalty(self):
        """Each unit of complexity reduces score by γ=0.2."""
        base = self.fn(reuse_count=5, user_approval=0, complexity=0)
        penalized = self.fn(reuse_count=5, user_approval=0, complexity=10)
        assert abs(base - penalized - 2.0) < 1e-9  # 0.2 * 10 = 2.0


# ── Eq.(2) HealthScore ────────────────────────────────────────────────────────


class TestComputeHealthScore:
    """Tests for analysis.tool_registry.compute_health_score (Eq.2)."""

    def setup_method(self):
        from analysis.tool_registry import compute_health_score
        self.fn = compute_health_score

    def test_perfect_health(self):
        """Zero churn and zero complexity delta yields HealthScore=1.0."""
        assert self.fn(churn_ratio=0.0, delta_complexity_norm=0.0) == pytest.approx(1.0)

    def test_full_churn_clips_to_zero(self):
        """ChurnRatio=1.0, no complexity delta: 1 − 0.6*1 − 0 = 0.4."""
        score = self.fn(churn_ratio=1.0, delta_complexity_norm=0.0)
        assert score == pytest.approx(0.4)

    def test_high_delta_complexity(self):
        """Full complexity regression: 1 − 0 − 0.4*1 = 0.6."""
        score = self.fn(churn_ratio=0.0, delta_complexity_norm=1.0)
        assert score == pytest.approx(0.6)

    def test_combined_degradation(self):
        """Both factors at max: 1 − 0.6 − 0.4 = 0.0, clipped to 0.0."""
        score = self.fn(churn_ratio=1.0, delta_complexity_norm=1.0)
        assert score == pytest.approx(0.0)

    def test_clip_floor(self):
        """Overshooting inputs are clipped to 0.0 (never negative)."""
        score = self.fn(churn_ratio=2.0, delta_complexity_norm=2.0)
        assert score == pytest.approx(0.0)

    def test_clip_ceiling(self):
        """Negative inputs (impossible in practice) are clipped to 1.0."""
        score = self.fn(churn_ratio=-0.5, delta_complexity_norm=-0.5)
        assert score == pytest.approx(1.0)

    def test_warning_threshold(self):
        """Score just below HELIX_THETA_WARNING triggers warning."""
        from config.settings import HELIX_THETA_WARNING
        # churn=0.6 → score = 1 − 0.6*0.6 = 0.64; adjust to land just below 0.70
        score = self.fn(churn_ratio=0.51, delta_complexity_norm=0.0)
        # 1 − 0.6*0.51 = 1 − 0.306 = 0.694 < 0.70
        assert score < HELIX_THETA_WARNING

    def test_typical_healthy_tool(self):
        """Moderate churn=0.2, no regression: 1 − 0.6*0.2 = 0.88 ≥ θ_warning."""
        from config.settings import HELIX_THETA_WARNING
        score = self.fn(churn_ratio=0.2, delta_complexity_norm=0.0)
        assert score == pytest.approx(0.88)
        assert score >= HELIX_THETA_WARNING


# ── compute_code_complexity ───────────────────────────────────────────────────


class TestComputeCodeComplexity:
    """Tests for analysis.code_promoter.compute_code_complexity."""

    def setup_method(self):
        from analysis.code_promoter import compute_code_complexity
        self.fn = compute_code_complexity

    def test_simple_function(self):
        """A straight-line function has cyclomatic complexity = 1."""
        code = "def foo(x):\n    return x + 1\n"
        result = self.fn(code)
        assert result >= 1

    def test_branching_raises_complexity(self):
        """Each if branch adds 1 to McCabe CC."""
        simple = "def foo(x):\n    return x\n"
        branchy = (
            "def foo(x):\n"
            "    if x > 0:\n"
            "        return x\n"
            "    elif x < 0:\n"
            "        return -x\n"
            "    else:\n"
            "        return 0\n"
        )
        cc_simple = self.fn(simple)
        cc_branchy = self.fn(branchy)
        assert cc_branchy >= cc_simple

    def test_invalid_code_returns_one(self):
        """Unparseable code gracefully returns 1 (safe default)."""
        result = self.fn("this is not python !!!")
        assert result == 1

    def test_empty_string_returns_one(self):
        """Empty string returns 1 (minimum)."""
        result = self.fn("")
        assert result == 1
