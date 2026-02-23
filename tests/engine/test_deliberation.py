"""Tests for deliberation orchestration."""

import json
import pytest
from unittest.mock import AsyncMock

from ripple.engine.deliberation import DeliberationOrchestrator
from ripple.primitives.pmf_models import (
    TribunalMember, TribunalOpinion, DeliberationRecord,
)


@pytest.fixture
def mock_tribunal_caller():
    caller = AsyncMock()
    caller.return_value = json.dumps({
        "scores": {"demand_resonance": 4, "propagation_potential": 3},
        "narrative": "The product shows promise.",
    })
    return caller


@pytest.fixture
def members():
    return [
        TribunalMember(role="MarketAnalyst", perspective="Market trends", expertise="Consumer goods"),
        TribunalMember(role="DevilsAdvocate", perspective="Risk identification", expertise="Failure analysis"),
    ]


class TestDeliberationOrchestrator:
    @pytest.mark.asyncio
    async def test_single_round(self, mock_tribunal_caller, members):
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_tribunal_caller,
            dimensions=["demand_resonance", "propagation_potential"],
            rubric="1=low, 5=high",
            max_rounds=1,
        )
        records = await orch.run(evidence_pack={"summary": "Test evidence", "key_signals": []})
        assert len(records) >= 1
        assert isinstance(records[0], DeliberationRecord)
        assert len(records[0].opinions) == 2  # Two members

    @pytest.mark.asyncio
    async def test_convergence_dual_gate_threshold(self, mock_tribunal_caller, members):
        """Threshold convergence: all dims change ≤1 for 2 consecutive rounds."""
        mock_tribunal_caller.side_effect = [
            # Round 0: evaluations (2 members)
            json.dumps({"scores": {"demand_resonance": 4, "propagation_potential": 3}, "narrative": "Good."}),
            json.dumps({"scores": {"demand_resonance": 2, "propagation_potential": 2}, "narrative": "Risky."}),
            # Round 1: challenges (2 members, 1 challenge each)
            json.dumps({"challenge": "Too optimistic."}),
            json.dumps({"challenge": "Too pessimistic."}),
            # Round 1: revisions — scores move closer
            json.dumps({"scores": {"demand_resonance": 3, "propagation_potential": 3}, "narrative": "Revised."}),
            json.dumps({"scores": {"demand_resonance": 3, "propagation_potential": 2}, "narrative": "Revised."}),
            # Round 2: challenges
            json.dumps({"challenge": "Still a bit off."}),
            json.dumps({"challenge": "Close enough."}),
            # Round 2: revisions — scores unchanged from Round 1 (converged)
            json.dumps({"scores": {"demand_resonance": 3, "propagation_potential": 3}, "narrative": "Stable."}),
            json.dumps({"scores": {"demand_resonance": 3, "propagation_potential": 2}, "narrative": "Stable."}),
        ]
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_tribunal_caller,
            dimensions=["demand_resonance", "propagation_potential"],
            rubric="1=low, 5=high",
            max_rounds=4,
        )
        records = await orch.run(evidence_pack={"summary": "Test evidence", "key_signals": []})
        # Should converge at round 2 (scores stable for 2 consecutive rounds: R1→R2)
        assert len(records) == 3  # Round 0 + Round 1 + Round 2
        assert records[-1].converged is True

    @pytest.mark.asyncio
    async def test_convergence_dual_gate_round_limit(self, mock_tribunal_caller, members):
        """Round limit: never converges, stops at max_rounds."""
        round_scores = [
            ({"demand_resonance": 4, "propagation_potential": 3}, {"demand_resonance": 1, "propagation_potential": 1}),
            ({"demand_resonance": 3, "propagation_potential": 4}, {"demand_resonance": 2, "propagation_potential": 1}),
            ({"demand_resonance": 2, "propagation_potential": 2}, {"demand_resonance": 4, "propagation_potential": 3}),
        ]
        responses = []
        for r_scores in round_scores:
            for s in r_scores:
                responses.append(json.dumps({"scores": s, "narrative": "Changing."}))
            responses.append(json.dumps({"challenge": "Disagree."}))
            responses.append(json.dumps({"challenge": "Also disagree."}))
        for _ in range(10):
            responses.append(json.dumps({"scores": {"demand_resonance": 5, "propagation_potential": 5}, "narrative": "Pad."}))
            responses.append(json.dumps({"challenge": "Pad."}))
        mock_tribunal_caller.side_effect = responses
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_tribunal_caller,
            dimensions=["demand_resonance", "propagation_potential"],
            rubric="1=low, 5=high",
            max_rounds=3,
        )
        records = await orch.run(evidence_pack={"summary": "Test evidence", "key_signals": []})
        assert len(records) == 3
        assert records[-1].converged is False

    @pytest.mark.asyncio
    async def test_challenge_sampling_one_per_member(self, mock_tribunal_caller, members):
        """Each member challenges 1 opponent (max gap)."""
        mock_tribunal_caller.side_effect = [
            # Round 0: evaluations
            json.dumps({"scores": {"demand_resonance": 5, "propagation_potential": 5}, "narrative": "Excellent."}),
            json.dumps({"scores": {"demand_resonance": 1, "propagation_potential": 1}, "narrative": "Terrible."}),
            # Round 1: challenges (exactly 2)
            json.dumps({"challenge": "Way too optimistic."}),
            json.dumps({"challenge": "Way too pessimistic."}),
            # Round 1: revisions
            json.dumps({"scores": {"demand_resonance": 3, "propagation_potential": 3}, "narrative": "Middle."}),
            json.dumps({"scores": {"demand_resonance": 3, "propagation_potential": 3}, "narrative": "Middle."}),
        ]
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_tribunal_caller,
            dimensions=["demand_resonance", "propagation_potential"],
            rubric="1=low, 5=high",
            max_rounds=2,
        )
        records = await orch.run(evidence_pack={"summary": "Evidence", "key_signals": []})
        assert len(records[1].challenges) == len(members)
