"""Tests for flexible output field handling (v3: correct OmniscientAgent interface)."""

import json
import pytest
from unittest.mock import AsyncMock

from ripple.agents.omniscient import OmniscientAgent


class TestFlexibleOutputFields:
    """OmniscientAgent should accept any valid JSON output without hardcoded field checking."""

    @pytest.mark.asyncio
    async def test_synthesize_accepts_pmf_fields(self):
        """synthesize_result should not reject valid JSON missing old hardcoded fields."""
        pmf_output = json.dumps({
            "grade": "B",
            "confidence": "medium",
            "executive_summary": "Moderate PMF signal.",
            "scorecard": {"dimensions": {}},
        })
        # Should NOT raise even though "prediction", "timeline" etc. are absent
        caller = AsyncMock(return_value=pmf_output)
        agent = OmniscientAgent(llm_caller=caller, system_prompt="")
        result = await agent.synthesize_result(
            field_snapshot={"seed_content": "test"},
            observation={"phase_vector": {}},
            simulation_input={"event": "test product"},
        )
        assert "grade" in result

    @pytest.mark.asyncio
    async def test_observe_accepts_pmf_fields(self):
        """observe() should not reject valid JSON missing old hardcoded fields."""
        observe_output = json.dumps({
            "pmf_signal": "moderate",
            "tribunal_readiness": True,
        })
        caller = AsyncMock(return_value=observe_output)
        agent = OmniscientAgent(llm_caller=caller, system_prompt="")
        result = await agent.observe(
            field_snapshot={"seed_content": "test"},
            full_history="Wave 1: ...",
        )
        assert "pmf_signal" in result
