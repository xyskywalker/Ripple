# tests/engine/test_topology_snapshot.py
"""Test that topology is included in runtime snapshot."""

import json
import pytest
from unittest.mock import AsyncMock

from ripple.engine.runtime import SimulationRuntime


class TestTopologyInSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_includes_topology(self):
        """After INIT, _build_snapshot() should include topology."""
        # Mock Omniscient INIT sub-call 1: dynamics
        init_dynamics = json.dumps({
            "wave_time_window": "4h",
            "energy_decay_per_wave": 0.15,
            "platform_characteristics": "test",
        })
        # Mock Omniscient INIT sub-call 2: agents
        init_agents = json.dumps({
            "star_configs": [{"id": "s1", "description": "KOL"}],
            "sea_configs": [{"id": "sea1", "description": "Users"}],
        })
        # Mock Omniscient INIT sub-call 3: topology + seed
        init_topology = json.dumps({
            "topology": {
                "edges": [{"from": "s1", "to": "sea1", "weight": 0.5}],
            },
            "seed_ripple": {"content": "Test", "initial_energy": 1.0},
        })

        # RIPPLE wave 0: immediately terminate
        wave_stop = json.dumps({
            "wave_number": 0,
            "simulated_time_elapsed": "0h",
            "simulated_time_remaining": "0h",
            "continue_propagation": False,
            "termination_reason": "test",
            "activated_agents": [],
            "skipped_agents": [],
            "global_observation": "terminate for test",
        })

        # OBSERVE
        observe_response = json.dumps({
            "phase_vector": {"heat": "low"},
            "phase_transition_detected": False,
            "emergence_events": [],
            "topology_recommendations": [],
        })

        # SYNTHESIZE
        synth_response = json.dumps({
            "prediction": {"impact": "none"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
        })

        omniscient_caller = AsyncMock(side_effect=[
            init_dynamics,    # INIT:dynamics
            init_agents,      # INIT:agents
            init_topology,    # INIT:topology
            wave_stop,        # RIPPLE wave 0 (terminate)
            observe_response, # OBSERVE
            synth_response,   # SYNTHESIZE
        ])
        agent_caller = AsyncMock(return_value=json.dumps({
            "response_type": "ignore",
            "outgoing_energy": 0.0,
            "cluster_reaction": "",
            "sentiment_shift": "",
            "reasoning": "",
        }))

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
        )

        # Capture the field_snapshot passed to ripple_verdict
        captured_snapshots = []
        original_ripple_verdict = runtime._omniscient.ripple_verdict

        async def capturing_ripple_verdict(*, field_snapshot, **kwargs):
            captured_snapshots.append(field_snapshot)
            return await original_ripple_verdict(
                field_snapshot=field_snapshot, **kwargs,
            )

        runtime._omniscient.ripple_verdict = capturing_ripple_verdict  # type: ignore[attr-defined]

        await runtime.run(
            {"event": "test", "simulation_horizon": "48h"},
            run_id="topo1",
        )

        # Verify topology is present in the snapshot passed to ripple_verdict
        assert len(captured_snapshots) >= 1
        snapshot = captured_snapshots[0]
        assert "topology" in snapshot, (
            "topology should be included in field_snapshot"
        )
        assert snapshot["topology"]["edges"][0]["from"] == "s1"
        assert snapshot["topology"]["edges"][0]["to"] == "sea1"
        assert snapshot["topology"]["edges"][0]["weight"] == 0.5

    @pytest.mark.asyncio
    async def test_snapshot_topology_absent_before_init(self):
        """Before INIT, _build_snapshot() should not include topology."""
        omniscient_caller = AsyncMock()
        agent_caller = AsyncMock()

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
        )

        # Before run(), topology should not be in snapshot
        snapshot = runtime._build_snapshot()
        assert "topology" not in snapshot, (
            "topology should not be in snapshot before INIT"
        )

    @pytest.mark.asyncio
    async def test_snapshot_topology_none_when_missing(self):
        """If INIT result has no topology key, snapshot should not include it."""
        omniscient_caller = AsyncMock()
        agent_caller = AsyncMock()

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
        )

        # Simulate topology being None (not set during INIT)
        runtime._topology = None
        snapshot = runtime._build_snapshot()
        assert "topology" not in snapshot, (
            "topology should not be in snapshot when _topology is None"
        )

    @pytest.mark.asyncio
    async def test_observe_receives_topology(self):
        """The snapshot passed to observe() should also contain topology."""
        init_dynamics = json.dumps({
            "wave_time_window": "4h",
            "energy_decay_per_wave": 0.15,
            "platform_characteristics": "test",
        })
        init_agents = json.dumps({
            "star_configs": [{"id": "s1", "description": "KOL"}],
            "sea_configs": [{"id": "sea1", "description": "Users"}],
        })
        init_topology = json.dumps({
            "topology": {
                "edges": [{"from": "s1", "to": "sea1", "weight": 0.8}],
            },
            "seed_ripple": {"content": "Test", "initial_energy": 0.5},
        })

        wave_stop = json.dumps({
            "wave_number": 0,
            "simulated_time_elapsed": "0h",
            "simulated_time_remaining": "0h",
            "continue_propagation": False,
            "termination_reason": "test",
            "activated_agents": [],
            "skipped_agents": [],
            "global_observation": "done",
        })

        observe_response = json.dumps({
            "phase_vector": {"heat": "low"},
            "phase_transition_detected": False,
            "emergence_events": [],
            "topology_recommendations": [],
        })

        synth_response = json.dumps({
            "prediction": {"impact": "none"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
        })

        omniscient_caller = AsyncMock(side_effect=[
            init_dynamics,
            init_agents,
            init_topology,
            wave_stop,
            observe_response,
            synth_response,
        ])
        agent_caller = AsyncMock(return_value=json.dumps({
            "response_type": "ignore",
            "outgoing_energy": 0.0,
            "cluster_reaction": "",
            "sentiment_shift": "",
            "reasoning": "",
        }))

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
        )

        # Capture observe's field_snapshot
        captured_observe_snapshots = []
        original_observe = runtime._omniscient.observe

        async def capturing_observe(*, field_snapshot, **kwargs):
            captured_observe_snapshots.append(field_snapshot)
            return await original_observe(
                field_snapshot=field_snapshot, **kwargs,
            )

        runtime._omniscient.observe = capturing_observe  # type: ignore[attr-defined]

        await runtime.run(
            {"event": "test", "simulation_horizon": "48h"},
            run_id="topo2",
        )

        assert len(captured_observe_snapshots) == 1
        snapshot = captured_observe_snapshots[0]
        assert "topology" in snapshot, (
            "topology should be in observe snapshot"
        )
        assert snapshot["topology"]["edges"][0]["weight"] == 0.8
