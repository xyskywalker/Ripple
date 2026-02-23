# tests/integration/test_pmf_pipeline.py
"""Integration test: full PMF validation pipeline with mocked LLM."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from ripple.api.simulate import simulate
from ripple.engine.deliberation import DeliberationOrchestrator
from ripple.primitives.pmf_models import (
    TribunalMember, DeliberationRecord,
)


class TestDeliberationIntegration:
    """Test the deliberation orchestrator with realistic mock data."""

    @pytest.mark.asyncio
    async def test_full_deliberation_cycle(self):
        """Run a complete deliberation with 2 members, 2 rounds."""
        call_count = 0
        responses = [
            # Round 0: evaluations
            json.dumps({"scores": {"demand": 4, "risk": 2}, "narrative": "Good product."}),
            json.dumps({"scores": {"demand": 2, "risk": 4}, "narrative": "Too risky."}),
            # Round 1: challenges
            json.dumps({"challenge": "You ignore market gaps."}),
            json.dumps({"challenge": "You ignore competition."}),
            # Round 1: revisions
            json.dumps({"scores": {"demand": 3, "risk": 3}, "narrative": "Balanced view."}),
            json.dumps({"scores": {"demand": 3, "risk": 3}, "narrative": "Agreed."}),
        ]

        async def mock_caller(*, system_prompt="", user_prompt=""):
            nonlocal call_count
            resp = responses[call_count % len(responses)]
            call_count += 1
            return resp

        members = [
            TribunalMember(role="Optimist", perspective="Growth", expertise="Marketing"),
            TribunalMember(role="Pessimist", perspective="Risks", expertise="Analysis"),
        ]
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_caller,
            dimensions=["demand", "risk"],
            rubric="1=low, 5=high",
            max_rounds=2,
        )
        evidence_pack = {
            "summary": "Simulated user reactions: mixed signals across 3 waves.",
            "key_signals": [
                {"wave_id": "w1", "agent_id": "star_1", "signal": "KOL positive reaction"},
                {"wave_id": "w2", "agent_id": "sea_1", "signal": "Group mixed reaction"},
            ],
        }
        records = await orch.run(evidence_pack=evidence_pack)

        assert len(records) == 2
        # Round 0: initial divergence
        assert records[0].opinions[0].scores["demand"] == 4
        assert records[0].opinions[1].scores["demand"] == 2
        # Round 1: convergence
        assert records[1].opinions[0].scores["demand"] == 3
        assert records[1].opinions[1].scores["demand"] == 3
        assert len(records[1].consensus_points) > 0  # Should detect consensus


class TestEnsembleStatisticsIntegration:
    """Test statistical aggregation utilities.

    v3 修正：默认 ensemble_runs=3，测试数据对齐为 3 次运行。
    """

    def test_aggregate_across_runs(self):
        from ripple.api.ensemble import aggregate_ordinal_scores
        # v3: 3 runs (default ensemble_runs=3)
        all_scores = [
            {"demand_resonance": 4, "adoption_friction": 2, "sustained_value": 3},
            {"demand_resonance": 4, "adoption_friction": 3, "sustained_value": 4},
            {"demand_resonance": 3, "adoption_friction": 2, "sustained_value": 3},
        ]
        result = aggregate_ordinal_scores(all_scores)
        assert result["demand_resonance"]["median"] == 4.0
        assert result["demand_resonance"]["stability_level"] == "high"  # range(max-min) <= 1
        assert result["adoption_friction"]["median"] in [2.0, 2.5, 3.0]

    def test_fleiss_kappa_integration(self):
        from ripple.api.ensemble import compute_fleiss_kappa
        # 3 runs (v3 default) scoring PMF grades on 5-point scale (A/B/C/D/F):
        # Convert to ratings matrix: each item (dimension) x categories
        # If 2 out of 3 runs give grade B (idx 1), and 1 gives C (idx 2):
        ratings = [
            [0, 2, 1, 0, 0],  # PMF grade: mostly B
        ]
        kappa = compute_fleiss_kappa(ratings)
        assert isinstance(kappa, float)
