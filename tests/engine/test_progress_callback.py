# tests/engine/test_progress_callback.py
"""Tests for SimulationEvent progress callback mechanism."""

import json
import pytest
from unittest.mock import AsyncMock

from ripple.engine.runtime import SimulationRuntime
from ripple.primitives.events import SimulationEvent


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _make_init_responses(**overrides):
    """Return 3 INIT sub-call responses (dynamics, agents, topology)."""
    dp = overrides.pop("dynamic_parameters", {
        "wave_time_window": "2h",
    })
    dynamics = {
        "wave_time_window": dp.get("wave_time_window", "2h"),
        "wave_time_window_reasoning": "test",
        "energy_decay_per_wave": 0.1,
        "platform_characteristics": "test",
    }
    agents = {
        "star_configs": overrides.get("star_configs",
                                       [{"id": "star_1", "description": "KOL"}]),
        "sea_configs": overrides.get("sea_configs",
                                      [{"id": "sea_1", "description": "群体"}]),
    }
    topology = {
        "topology": overrides.get("topology", {"edges": []}),
        "seed_ripple": overrides.get("seed_ripple",
                                      {"content": "测试", "initial_energy": 0.5}),
    }
    return [json.dumps(dynamics), json.dumps(agents), json.dumps(topology)]


def _make_wave_response(*, continue_propagation=True, activate=None,
                        termination_reason=None):
    data = {
        "wave_number": 0,
        "simulated_time_elapsed": "2h",
        "simulated_time_remaining": "4h",
        "continue_propagation": continue_propagation,
        "activated_agents": activate or [],
        "skipped_agents": [],
        "global_observation": "观测",
    }
    if termination_reason:
        data["termination_reason"] = termination_reason
    return json.dumps(data)


_OBSERVE_RESP = json.dumps({
    "phase_vector": {"heat": "growth"},
    "phase_transition_detected": False,
    "emergence_events": [],
    "topology_recommendations": [],
})

_SYNTH_RESP = json.dumps({
    "prediction": {}, "timeline": [],
    "bifurcation_points": [], "agent_insights": {},
})

_SEA_RESP = json.dumps({
    "response_type": "amplify",
    "cluster_reaction": "积极",
    "outgoing_energy": 0.4,
    "sentiment_shift": "正面",
    "reasoning": "匹配",
})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProgressCallbackBasic:
    """基础回调机制测试。"""

    @pytest.mark.asyncio
    async def test_no_callback_works(self):
        """不传 on_progress 时模拟正常完成（向后兼容）。"""
        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
        )
        result = await runtime.run({"event": {"description": "t"}})
        assert "run_id" in result

    @pytest.mark.asyncio
    async def test_async_callback_receives_events(self):
        """异步回调收到所有事件。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        assert len(events) > 0
        assert all(isinstance(e, SimulationEvent) for e in events)

    @pytest.mark.asyncio
    async def test_sync_callback_receives_events(self):
        """同步回调也能正常工作。"""
        events = []

        def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        assert len(events) > 0
        assert all(isinstance(e, SimulationEvent) for e in events)


class TestProgressEventPhases:
    """验证事件覆盖所有 5 个阶段。"""

    @pytest.mark.asyncio
    async def test_all_phases_have_start_and_end(self):
        """每个阶段都有 phase_start 和 phase_end。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(
                activate=[{"agent_id": "sea_1",
                           "incoming_ripple_energy": 0.5,
                           "activation_reason": "test"}],
            ),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(return_value=_SEA_RESP),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        phases_started = {e.phase for e in events if e.type == "phase_start"}
        phases_ended = {e.phase for e in events if e.type == "phase_end"}

        expected = {"INIT", "SEED", "RIPPLE", "OBSERVE", "SYNTHESIZE"}
        assert phases_started == expected
        assert phases_ended == expected

    @pytest.mark.asyncio
    async def test_wave_events_emitted(self):
        """RIPPLE 阶段中每轮 wave 都有 wave_start 和 wave_end。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(continue_propagation=True, activate=[]),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        wave_starts = [e for e in events if e.type == "wave_start"]
        wave_ends = [e for e in events if e.type == "wave_end"]

        assert len(wave_starts) >= 1
        assert len(wave_ends) >= 1
        assert wave_starts[0].wave == 0


class TestProgressEventAgents:
    """验证 Agent 激活和响应事件。"""

    @pytest.mark.asyncio
    async def test_agent_activated_and_responded_events(self):
        """Agent 被激活时产生 agent_activated 和 agent_responded 事件。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(
                activate=[{"agent_id": "sea_1",
                           "incoming_ripple_energy": 0.6,
                           "activation_reason": "匹配"}],
            ),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(return_value=_SEA_RESP),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        activated = [e for e in events if e.type == "agent_activated"]
        responded = [e for e in events if e.type == "agent_responded"]

        assert len(activated) == 1
        assert activated[0].agent_id == "sea_1"
        assert activated[0].agent_type == "sea"
        assert activated[0].detail["energy"] == 0.6

        assert len(responded) == 1
        assert responded[0].agent_id == "sea_1"


class TestProgressValues:
    """验证 progress 值的单调递增性和边界。"""

    @pytest.mark.asyncio
    async def test_progress_monotonically_increases(self):
        """progress 值随事件推进单调递增。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(
                activate=[{"agent_id": "star_1",
                           "incoming_ripple_energy": 0.5,
                           "activation_reason": "t"}],
            ),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        star_resp = json.dumps({
            "response_type": "amplify", "response_content": "test",
            "outgoing_energy": 0.4, "reasoning": "t",
        })
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(return_value=star_resp),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        # 只看 phase_start/phase_end/wave_start/wave_end 的 progress
        phase_events = [e for e in events
                        if e.type in ("phase_start", "phase_end",
                                      "wave_start", "wave_end")]
        progress_values = [e.progress for e in phase_events]

        # 单调非递减
        for i in range(1, len(progress_values)):
            assert progress_values[i] >= progress_values[i - 1], (
                f"progress decreased: {progress_values[i-1]} -> "
                f"{progress_values[i]} at event {i}"
            )

    @pytest.mark.asyncio
    async def test_progress_starts_at_zero_ends_at_one(self):
        """progress 从 0.0 开始，最后一个事件为 1.0。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        assert events[0].progress == 0.0
        assert events[-1].progress == 1.0


class TestProgressRunId:
    """验证所有事件携带一致的 run_id。"""

    @pytest.mark.asyncio
    async def test_consistent_run_id(self):
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
            on_progress=handler,
        )
        result = await runtime.run({"event": {"description": "t"}})

        run_ids = {e.run_id for e in events}
        assert len(run_ids) == 1
        assert run_ids.pop() == result["run_id"]


class TestProgressTotalWaves:
    """验证 total_waves 字段正确传播。"""

    @pytest.mark.asyncio
    async def test_total_waves_populated_after_init(self):
        """INIT 完成后所有事件都携带 total_waves。"""
        events = []

        async def handler(event: SimulationEvent):
            events.append(event)

        omniscient = AsyncMock(side_effect=[
            *_make_init_responses(dynamic_parameters={
                "wave_time_window": "1h",
            }),
            _make_wave_response(continue_propagation=False),
            _OBSERVE_RESP,
            _SYNTH_RESP,
        ])
        runtime = SimulationRuntime(
            omniscient_caller=omniscient,
            agent_caller=AsyncMock(),
            on_progress=handler,
        )
        await runtime.run({"event": {"description": "t"}})

        # INIT phase_end 之后的事件都应该有 total_waves
        # Without simulation_horizon, fallback to estimated_total_waves not in dp,
        # so it uses default 10
        after_init = False
        init_end_waves = None
        for e in events:
            if e.type == "phase_end" and e.phase == "INIT":
                init_end_waves = e.total_waves
                after_init = True
            elif after_init and e.type in ("phase_start", "phase_end",
                                           "wave_start", "wave_end"):
                assert e.total_waves == init_end_waves
