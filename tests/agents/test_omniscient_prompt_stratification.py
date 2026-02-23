"""Tests for OmniscientAgent system/user prompt stratification (v4.2)."""

import pytest
from unittest.mock import AsyncMock

from ripple.agents.omniscient import OmniscientAgent


class TestOmniscientPromptStratification:
    """Verify that OmniscientAgent instructions go to system_prompt and data to user_prompt."""

    def _make_capturing_caller(self, script: list[str]):
        """Create an async caller that captures prompts and returns scripted responses."""
        captured = []
        idx = {"i": 0}

        async def caller(*, system_prompt="", user_prompt="", **kwargs):
            captured.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
            i = idx["i"]
            idx["i"] += 1
            return script[min(i, len(script) - 1)]

        return caller, captured

    @pytest.mark.asyncio
    async def test_init_dynamics_stratification(self):
        """INIT:dynamics — instructions in system_prompt, data in user_prompt."""
        caller, captured = self._make_capturing_caller([
            '{"wave_time_window":"4h","wave_time_window_reasoning":"test","energy_decay_per_wave":0.15,"platform_characteristics":"test"}',
            '{"star_configs":[{"id":"s1","description":"KOL"}],"sea_configs":[{"id":"c1","description":"Users"}]}',
            '{"topology":{"edges":[{"from":"s1","to":"c1","weight":0.5}]},"seed_ripple":{"content":"seed","initial_energy":0.5}}',
        ])
        agent = OmniscientAgent(llm_caller=caller, system_prompt="BASE_MARKER")
        await agent.init(skill_profile="test profile", simulation_input={"event": "test", "skill": "s"})

        # Sub-call 1 (INIT:dynamics)
        call0 = captured[0]
        # Instructions/schema should be in system_prompt
        assert "你的任务" in call0["system_prompt"]
        assert "wave_time_window" in call0["system_prompt"]  # JSON schema example
        assert "BASE_MARKER" in call0["system_prompt"]
        # Data should be in user_prompt
        assert "test profile" in call0["user_prompt"]  # skill_profile = data
        assert '"event"' in call0["user_prompt"]  # input_json = data
        # Instructions should NOT be in user_prompt
        assert "你的任务" not in call0["user_prompt"]

    @pytest.mark.asyncio
    async def test_ripple_verdict_stratification(self):
        """RIPPLE verdict — CAS principles in system_prompt, snapshot/history in user_prompt."""
        caller, captured = self._make_capturing_caller([
            # 3×INIT responses
            '{"wave_time_window":"4h","wave_time_window_reasoning":"","energy_decay_per_wave":0.15,"platform_characteristics":""}',
            '{"star_configs":[{"id":"s1","description":"K"}],"sea_configs":[{"id":"c1","description":"U"}]}',
            '{"topology":{"edges":[{"from":"s1","to":"c1","weight":0.5}]},"seed_ripple":{"content":"seed","initial_energy":0.5}}',
            # RIPPLE verdict
            '{"wave_number":0,"simulated_time_elapsed":"0h","simulated_time_remaining":"48h","continue_propagation":false,"activated_agents":[],"skipped_agents":[],"global_observation":"done"}',
        ])
        agent = OmniscientAgent(llm_caller=caller)
        await agent.init(skill_profile="p", simulation_input={"event": "e", "skill": "s"})

        verdict = await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=0,
            propagation_history="wave 0 history",
        )

        ripple_call = captured[3]  # 4th call = RIPPLE verdict
        # CAS principles + task instructions should be in system_prompt
        assert "CAS" in ripple_call["system_prompt"]
        assert "你的任务" in ripple_call["system_prompt"]
        # Runtime data should be in user_prompt
        assert "wave 0 history" in ripple_call["user_prompt"]

    @pytest.mark.asyncio
    async def test_observe_stratification(self):
        """OBSERVE — analysis instructions in system_prompt, snapshot/history in user_prompt."""
        caller, captured = self._make_capturing_caller([
            '{"phase_vector":{"heat":"growth","sentiment":"unified","coherence":"ordered"},"phase_transition_detected":false,"emergence_events":[],"topology_recommendations":[]}',
        ])
        agent = OmniscientAgent(llm_caller=caller)
        result = await agent.observe(
            field_snapshot={"test": "snapshot_data"},
            full_history="full history text",
        )

        call0 = captured[0]
        # Instructions + schema + field constraints should be in system_prompt
        assert "你的任务" in call0["system_prompt"]
        assert "heat" in call0["system_prompt"]  # field constraint enum
        # Data should be in user_prompt
        assert "snapshot_data" in call0["user_prompt"]
        assert "full history text" in call0["user_prompt"]

    @pytest.mark.asyncio
    async def test_synthesize_stratification(self):
        """SYNTHESIZE — prediction instructions in system_prompt, data in user_prompt."""
        caller, captured = self._make_capturing_caller([
            '{"prediction":{"impact":"test"},"timeline":[],"bifurcation_points":[],"agent_insights":{}}',
        ])
        agent = OmniscientAgent(llm_caller=caller)
        result = await agent.synthesize_result(
            field_snapshot={"test": "final_snapshot"},
            observation={"phase_vector": {"heat": "growth"}},
            simulation_input={"event": "test_event", "skill": "s"},
        )

        call0 = captured[0]
        # Prediction mode + schema should be in system_prompt
        assert "你的任务" in call0["system_prompt"]
        assert "prediction" in call0["system_prompt"]  # JSON schema
        # Data should be in user_prompt
        assert "final_snapshot" in call0["user_prompt"]
        assert "test_event" in call0["user_prompt"]

    @pytest.mark.asyncio
    async def test_backward_compat_empty_system_prompt(self):
        """Without explicit system_prompt, OmniscientAgent still works (system_prompt = phase instructions only)."""
        caller, captured = self._make_capturing_caller([
            '{"phase_vector":{"heat":"growth","sentiment":"unified","coherence":"ordered"},"phase_transition_detected":false,"emergence_events":[],"topology_recommendations":[]}',
        ])
        agent = OmniscientAgent(llm_caller=caller)  # no system_prompt arg
        await agent.observe(
            field_snapshot={"test": "data"},
            full_history="history",
        )

        call0 = captured[0]
        # Should still have phase instructions in system_prompt (from OMNISCIENT_OBSERVE_SYSTEM)
        assert "你的任务" in call0["system_prompt"]
        # Should NOT have empty system_prompt like before
        assert len(call0["system_prompt"].strip()) > 0
