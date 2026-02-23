"""Tests for TribunalAgent."""

import json
import pytest
from unittest.mock import AsyncMock

from ripple.agents.tribunal import TribunalAgent
from ripple.primitives.pmf_models import TribunalOpinion


@pytest.fixture
def mock_llm_caller():
    return AsyncMock()


@pytest.fixture
def agent(mock_llm_caller):
    return TribunalAgent(
        role="MarketAnalyst",
        perspective="Market size, competition, and trends",
        expertise="Consumer goods analysis",
        llm_caller=mock_llm_caller,
        system_prompt="You are a market analyst.",
    )


class TestTribunalAgentEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_returns_opinion(self, agent, mock_llm_caller):
        mock_llm_caller.return_value = json.dumps({
            "scores": {"demand_resonance": 4, "propagation_potential": 3},
            "narrative": "The product shows promise in niche segments.",
        })
        opinion = await agent.evaluate(
            evidence="Wave 1: 3 of 5 star agents responded positively.",
            dimensions=["demand_resonance", "propagation_potential"],
            rubric="1=no interest, 5=strong demand",
            round_number=0,
        )
        assert isinstance(opinion, TribunalOpinion)
        assert opinion.member_role == "MarketAnalyst"
        assert opinion.scores["demand_resonance"] == 4
        assert opinion.round_number == 0

    @pytest.mark.asyncio
    async def test_evaluate_handles_markdown_json(self, agent, mock_llm_caller):
        mock_llm_caller.return_value = '```json\n{"scores": {"demand_resonance": 3}, "narrative": "OK"}\n```'
        opinion = await agent.evaluate(
            evidence="Evidence text",
            dimensions=["demand_resonance"],
            rubric="rubric text",
            round_number=0,
        )
        assert opinion.scores["demand_resonance"] == 3


class TestTribunalAgentChallenge:
    @pytest.mark.asyncio
    async def test_challenge_returns_string(self, agent, mock_llm_caller):
        mock_llm_caller.return_value = json.dumps({
            "challenge": "The market analysis ignores regulatory risks.",
        })
        other_opinion = TribunalOpinion(
            member_role="UserAdvocate",
            scores={"demand_resonance": 5},
            narrative="Users love it.",
            round_number=0,
        )
        result = await agent.challenge(other_opinion)
        assert isinstance(result, str)
        assert len(result) > 0


class TestTribunalAgentRevise:
    @pytest.mark.asyncio
    async def test_revise_returns_updated_opinion(self, agent, mock_llm_caller):
        mock_llm_caller.return_value = json.dumps({
            "scores": {"demand_resonance": 3},
            "narrative": "Revised assessment after considering challenges.",
        })
        original = TribunalOpinion(
            member_role="MarketAnalyst",
            scores={"demand_resonance": 4},
            narrative="Original.",
            round_number=0,
        )
        revised = await agent.revise(
            original_opinion=original,
            challenges=["The market analysis ignores regulatory risks."],
            round_number=1,
        )
        assert isinstance(revised, TribunalOpinion)
        assert revised.round_number == 1
        assert revised.scores["demand_resonance"] == 3


class TestTribunalAgentPromptStratification:
    @pytest.mark.asyncio
    async def test_system_prompt_passed_to_llm(self, mock_llm_caller):
        """Verify system_prompt is passed as system_prompt kwarg."""
        mock_llm_caller.return_value = json.dumps({
            "scores": {"d": 3},
            "narrative": "test",
        })
        agent = TribunalAgent(
            role="Analyst",
            perspective="test",
            expertise="test",
            llm_caller=mock_llm_caller,
            system_prompt="SYSTEM_MARKER",
        )
        await agent.evaluate(
            evidence="e", dimensions=["d"], rubric="r", round_number=0,
        )
        call_kwargs = mock_llm_caller.call_args
        assert call_kwargs.kwargs["system_prompt"] == "SYSTEM_MARKER"
