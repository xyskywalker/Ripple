"""Tests for Skill prompt injection into Agent LLM contexts."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from ripple.engine.runtime import SimulationRuntime


class TestSkillPromptPassthrough:
    """Verify that Skill prompts are passed through to SimulationRuntime."""

    @pytest.mark.asyncio
    async def test_runtime_accepts_skill_prompts(self):
        """SimulationRuntime should accept skill_prompts parameter."""
        omniscient_caller = AsyncMock()
        agent_caller = AsyncMock()

        skill_prompts = {
            "omniscient": "You are evaluating PMF...",
            "star": "You are a key user reacting to a product...",
            "sea": "You are a consumer group...",
        }

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
            skill_profile="test profile",
            skill_prompts=skill_prompts,
        )
        assert runtime._skill_prompts == skill_prompts

    @pytest.mark.asyncio
    async def test_runtime_defaults_to_empty_prompts(self):
        """Without skill_prompts, runtime should default to empty dict."""
        omniscient_caller = AsyncMock()
        agent_caller = AsyncMock()

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
        )
        assert runtime._skill_prompts == {}

    @pytest.mark.asyncio
    async def test_skill_prompt_injected_into_system_prompt(self):
        """v4 P2 fix: Verify skill prompt actually appears in LLM system_prompt, not user_prompt."""
        captured_omniscient = []
        captured_agents = []

        # Minimal Omniscient call script: 3×INIT + 1×verdict + 1×observe + 1×synthesize
        omniscient_script = [
            '{"wave_time_window":"4h","wave_time_window_reasoning":"","energy_decay_per_wave":0.15,"platform_characteristics":""}',
            '{"star_configs":[{"id":"star_1","description":"KOL"}],"sea_configs":[{"id":"sea_1","description":"Users"}]}',
            '{"topology":{"edges":[{"from":"star_1","to":"sea_1","weight":0.5}]},"seed_ripple":{"content":"seed","initial_energy":0.5}}',
            '{"wave_number":0,"simulated_time_elapsed":"4h","simulated_time_remaining":"4h","continue_propagation":true,"activated_agents":[{"agent_id":"star_1","incoming_ripple_energy":0.5,"activation_reason":"test"}],"skipped_agents":[],"global_observation":""}',
            '{"phase_vector":{"heat":"unknown","sentiment":"unknown","coherence":"unknown"},"phase_transition_detected":false,"emergence_events":[],"topology_recommendations":[]}',
            '{"prediction":{},"timeline":[],"bifurcation_points":[],"agent_insights":{}}',
        ]
        omniscient_idx = {"i": 0}

        async def omniscient_caller(*, system_prompt="", user_prompt="", **kwargs):
            captured_omniscient.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
            i = omniscient_idx["i"]
            omniscient_idx["i"] += 1
            return omniscient_script[min(i, len(omniscient_script) - 1)]

        async def agent_caller(*, system_prompt="", user_prompt="", **kwargs):
            captured_agents.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
            # Compatible with both StarAgent and SeaAgent parsers
            return '{"response_type":"ignore","response_content":"","cluster_reaction":"","sentiment_shift":"","outgoing_energy":0.0,"reasoning":""}'

        skill_prompts = {
            "omniscient": "PMF_SKILL_MARKER: You are evaluating product-market fit.",
            "star": "STAR_SKILL_MARKER: React as a key user.",
            "sea": "SEA_SKILL_MARKER: React as consumer group.",
        }

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
            skill_profile="test profile",
            skill_prompts=skill_prompts,
        )
        await runtime.run({"event": "x", "skill": "test-skill"}, run_id="t0")

        # Assert: skill prompt lands in system_prompt (trusted zone), not user_prompt
        assert any("PMF_SKILL_MARKER" in c["system_prompt"] for c in captured_omniscient)
        assert all("PMF_SKILL_MARKER" not in c["user_prompt"] for c in captured_omniscient)
        assert any("STAR_SKILL_MARKER" in c["system_prompt"] for c in captured_agents)
        assert all("STAR_SKILL_MARKER" not in c["user_prompt"] for c in captured_agents)
        assert any("===== SKILL CONTEXT =====" in c["system_prompt"] for c in captured_omniscient + captured_agents)
