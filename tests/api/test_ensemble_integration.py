"""Integration tests for simulate() ensemble mode (P1).

Verifies:
- run boundaries are recorded via recorder.begin_ensemble_run/end_ensemble_run
- ensemble aggregation is attached to the returned result
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ripple.api.simulate import simulate


class TestSimulateEnsembleAggregation:
    @pytest.mark.asyncio
    async def test_ensemble_aggregation_and_run_boundaries(self):
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder") as MockRecorder:

            mock_skill = MagicMock()
            mock_skill.name = "pmf-validation"
            mock_skill.version = "0.1.0"
            mock_skill.domain_profile = "PMF profile"
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            mock_skill.prompts = {
                "omniscient": "omniscient prompt",
                "star": "star prompt",
                "sea": "sea prompt",
                "tribunal": "tribunal prompt",
            }
            mock_skill.rubrics = {"scorecard-dimensions": "rubric"}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            recorder = MockRecorder.return_value

            mock_runtime = AsyncMock()
            mock_runtime.run.side_effect = [
                {
                    "total_waves": 1,
                    "grade": "A",
                    "scores": {"demand_resonance": 4, "propagation_potential": 4},
                },
                {
                    "total_waves": 1,
                    "grade": "A",
                    "scores": {"demand_resonance": 4, "propagation_potential": 3},
                },
                {
                    "total_waves": 1,
                    "grade": "B",
                    "scores": {"demand_resonance": 3, "propagation_potential": 3},
                },
            ]
            MockRuntime.return_value = mock_runtime

            result = await simulate(
                event={"description": "test"},
                skill="pmf-validation",
                ensemble_runs=3,
            )

            assert result["ensemble_runs_requested"] == 3
            assert result["ensemble_runs_completed"] == 3
            stats = result["ensemble_stats"]
            assert stats["grade_mode"] == "A"
            assert stats["grade_agreement_rate"] == pytest.approx(2 / 3, abs=1e-6)
            assert "dimension_aggregates" in stats
            assert stats["dimension_aggregates"]["demand_resonance"]["median"] == 4.0

            assert recorder.begin_ensemble_run.call_count == 3
            assert recorder.end_ensemble_run.call_count == 3

