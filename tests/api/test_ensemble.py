# tests/api/test_ensemble.py
"""Tests for ensemble runner and statistical aggregation."""

import pytest
from unittest.mock import AsyncMock, patch

from ripple.api.ensemble import (
    compute_fleiss_kappa,
    compute_median_iqr,
    aggregate_ordinal_scores,
    EnsembleRunner,
)


class TestStatisticalUtils:
    def test_compute_median_iqr_odd(self):
        median, iqr = compute_median_iqr([1, 2, 3, 4, 5])
        assert median == 3
        assert iqr == 2  # Q3(4) - Q1(2) = 2

    def test_compute_median_iqr_even(self):
        median, iqr = compute_median_iqr([1, 2, 3, 4])
        assert median == 2.5
        assert iqr == 2  # Q3(3.5) - Q1(1.5) = 2

    def test_compute_median_iqr_single(self):
        median, iqr = compute_median_iqr([4])
        assert median == 4
        assert iqr == 0

    def test_fleiss_kappa_perfect_agreement(self):
        # v3 fix: correct matrix format.
        # 1 item (PMF scenario), 3 raters (ensemble runs), 5 categories (A/B/C/D/F).
        # All 3 raters chose category 0 (grade A).
        ratings = [[3, 0, 0, 0, 0]]
        kappa = compute_fleiss_kappa(ratings)
        assert kappa == pytest.approx(1.0, abs=0.01)

    def test_fleiss_kappa_no_agreement(self):
        # v3 fix: 1 item, 3 raters, each chose a different category.
        ratings = [[1, 1, 1, 0, 0]]
        kappa = compute_fleiss_kappa(ratings)
        assert kappa < 0.1

    def test_fleiss_kappa_multi_item(self):
        # 3 items, 3 raters each, 3 categories.
        # Item 1: all agree on cat 0. Item 2: split. Item 3: all agree on cat 1.
        ratings = [[3, 0, 0], [1, 1, 1], [0, 3, 0]]
        kappa = compute_fleiss_kappa(ratings)
        assert 0.3 < kappa < 0.8  # Moderate agreement

    def test_aggregate_ordinal_scores(self):
        all_scores = [
            {"demand": 4, "risk": 2},
            {"demand": 3, "risk": 3},
            {"demand": 4, "risk": 2},
        ]
        result = aggregate_ordinal_scores(all_scores)
        assert result["demand"]["median"] == 4
        assert result["risk"]["median"] == 2
        assert "range" in result["demand"]
        assert "stability_level" in result["demand"]
        assert "iqr" in result["demand"]  # optional


class TestEnsembleRunner:
    @pytest.mark.asyncio
    async def test_runner_calls_simulate_n_times(self):
        mock_simulate = AsyncMock(return_value={
            "pmf_grade": "B",
            "scores": {"demand": 4},
            "narrative": "Good.",
        })
        runner = EnsembleRunner(
            simulate_fn=mock_simulate,
            num_runs=3,
        )
        results = await runner.run(event="test product", skill="pmf-validation")
        assert len(results) == 3
        assert mock_simulate.call_count == 3
