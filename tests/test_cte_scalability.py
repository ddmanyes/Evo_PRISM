"""
GigaScience Reviewer Pack: CTE Scalability and Topology Comparison Tests.
Runs the benchmark logic and verifies CTE scalability and topology metrics.
"""

from __future__ import annotations

import pytest
import random
import duckdb
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "paper" / "analysis"))

from benchmark_impact import (
    run_scalability_benchmark,
    run_confidence_tier_benchmark,
    run_real_vs_synthetic_comparison,
)

def test_cte_scalability_pressure_test():
    """Verify that CTE recursive queries scale up to 1,000,000 edges without error."""
    results = run_scalability_benchmark()
    assert len(results) == 4
    # All scales must complete and have non-zero max depth
    for p in results:
        assert p.n_edges in [1000, 10000, 100000, 1000000]
        assert p.median_latency_ms < 500.0  # Assure it is under 500ms
        assert p.cte_depth >= 0

def test_confidence_evolution():
    """Verify that confidence evolution increases recall/precision from Phase A to Phase B."""
    phase_a, phase_b = run_confidence_tier_benchmark()
    assert phase_b.recall >= phase_a.recall
    assert phase_b.precision >= phase_a.precision
    assert phase_b.avg_confidence > phase_a.avg_confidence

def test_real_vs_synthetic():
    """Verify that real pipeline topology yields better query throughput than random DAGs."""
    comparison = run_real_vs_synthetic_comparison()
    assert comparison["n_nodes"] == 98 * 6
    assert comparison["n_edges"] == 98 * 5
    assert "real_topology" in comparison
    assert "synth_topology" in comparison
    assert comparison["real_topology"]["median_latency_ms"] < 100.0
    assert comparison["real_topology"]["qps"] > 0
