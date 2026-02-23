"""Tests for the optional Phase registration mechanism in SimulationRuntime."""

import json
import tempfile
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from ripple.engine.runtime import SimulationRuntime
from ripple.engine.recorder import SimulationRecorder


class TestDefaultPhaseSequence:
    """Without extra_phases, default 5-Phase behavior is unchanged."""

    @pytest.mark.asyncio
    async def test_default_phases(self):
        """Default phase sequence should be the original 5 phases."""
        runtime = SimulationRuntime(
            omniscient_caller=AsyncMock(),
            star_caller=AsyncMock(),
            sea_caller=AsyncMock(),
        )
        # Default: INIT, SEED, RIPPLE, OBSERVE, SYNTHESIZE
        assert len(runtime._phases) == 5
        assert "INIT" in runtime._phases
        assert "SYNTHESIZE" in runtime._phases


class TestExtraPhaseRegistration:
    """Skills can register extra phases via extra_phases parameter."""

    @pytest.mark.asyncio
    async def test_extra_phase_inserted(self):
        """Extra phase should be inserted at correct position."""
        mock_handler = AsyncMock(return_value={})

        extra_phases = {
            "DELIBERATE": {
                "after": "RIPPLE",
                "weight": 0.15,
                "handler": mock_handler,
            }
        }

        runtime = SimulationRuntime(
            omniscient_caller=AsyncMock(),
            star_caller=AsyncMock(),
            sea_caller=AsyncMock(),
            extra_phases=extra_phases,
        )
        phase_names = list(runtime._phases.keys())
        assert "DELIBERATE" in phase_names
        ripple_idx = phase_names.index("RIPPLE")
        deliberate_idx = phase_names.index("DELIBERATE")
        assert deliberate_idx == ripple_idx + 1

    @pytest.mark.asyncio
    async def test_phase_weights_rebalanced(self):
        """Phase weights should be dynamically rebalanced when extra phases registered."""
        extra_phases = {
            "DELIBERATE": {
                "after": "RIPPLE",
                "weight": 0.15,
                "handler": AsyncMock(),
            }
        }

        runtime = SimulationRuntime(
            omniscient_caller=AsyncMock(),
            agent_caller=AsyncMock(),
            extra_phases=extra_phases,
        )
        total_weight = sum(p["weight"] for p in runtime._phases.values())
        assert abs(total_weight - 1.0) < 0.01  # Weights sum to ~1.0


class TestExtraPhaseExecution:
    """Extra phase handlers should actually execute during runtime.run()."""

    @pytest.mark.asyncio
    async def test_extra_phase_handler_runs_and_records(self):
        captured = {}

        async def deliberate_handler(context):
            captured.update(context)
            ep = context.get("evidence_pack", {}) or {}
            return {
                "ok": True,
                "full_records_ref": ep.get("full_records_ref"),
            }

        extra_phases = {
            "DELIBERATE": {
                "after": "RIPPLE",
                "weight": 0.15,
                "handler": deliberate_handler,
            }
        }

        init_dynamics = json.dumps({
            "wave_time_window": "2h",
            "wave_time_window_reasoning": "test",
            "energy_decay_per_wave": 0.1,
            "platform_characteristics": "test",
        })
        init_agents = json.dumps({
            "star_configs": [{"id": "star_1", "description": "KOL"}],
            "sea_configs": [{"id": "sea_1", "description": "Users"}],
        })
        init_topology = json.dumps({
            "topology": {"edges": []},
            "seed_ripple": {"content": "seed", "initial_energy": 0.5},
        })
        wave0 = json.dumps({
            "wave_number": 0,
            "simulated_time_elapsed": "2h",
            "simulated_time_remaining": "4h",
            "continue_propagation": True,
            "activated_agents": [
                {"agent_id": "sea_1", "incoming_ripple_energy": 0.5, "activation_reason": "test"},
            ],
            "skipped_agents": [],
            "global_observation": "",
        })
        wave1_stop = json.dumps({
            "wave_number": 1,
            "simulated_time_elapsed": "4h",
            "simulated_time_remaining": "2h",
            "continue_propagation": False,
            "termination_reason": "stop",
            "activated_agents": [],
            "skipped_agents": [],
            "global_observation": "",
        })
        observe_resp = json.dumps({
            "phase_vector": {"heat": "growth"},
            "phase_transition_detected": False,
            "emergence_events": [],
            "topology_recommendations": [],
        })
        synth_resp = json.dumps({
            "prediction": {}, "timeline": [],
            "bifurcation_points": [], "agent_insights": {},
        })

        omniscient = AsyncMock(side_effect=[
            init_dynamics, init_agents, init_topology,
            wave0, wave1_stop,
            observe_resp, synth_resp,
        ])
        agent = AsyncMock(return_value=json.dumps({
            "response_type": "amplify",
            "cluster_reaction": "positive",
            "sentiment_shift": "up",
            "outgoing_energy": 0.4,
            "reasoning": "match",
        }))

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "run.json"
            recorder = SimulationRecorder(output_path=out, run_id="t")
            runtime = SimulationRuntime(
                omniscient_caller=omniscient,
                agent_caller=agent,
                recorder=recorder,
                extra_phases=extra_phases,
            )
            await runtime.run({"event": {"description": "t"}, "skill": "x"}, run_id="t")

            # Handler saw evidence pack pointer
            assert captured.get("evidence_pack", {}).get("full_records_ref") == "#/process/waves"

            # Recorder contains process.deliberation with stable JSON Pointer target
            data = json.loads(out.read_text(encoding="utf-8"))
            assert "deliberation" in data["process"]
            assert data["process"]["deliberation"]["ok"] is True
