# tests/api/test_pmf_simulate.py
"""Tests for PMF validation simulate() extensions."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ripple.api.simulate import simulate


class TestSimulateChannelParam:
    @pytest.mark.asyncio
    async def test_channel_param_accepted_v0_generic(self):
        """simulate() should accept generic channel (v0 scope)."""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder"):

            mock_skill = MagicMock()
            mock_skill.name = "pmf-validation"
            mock_skill.version = "0.1.0"
            mock_skill.domain_profile = "PMF profile"
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {"generic": "generic channel profile"}
            mock_skill.prompts = {"omniscient": "omniscient prompt", "tribunal": "tribunal prompt", "star": "star prompt", "sea": "sea prompt"}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            result = await simulate(
                event={"description": "test product"},
                skill="pmf-validation",
                channel="generic",
            )
            assert isinstance(result, dict)

            # v4.2: DELIBERATE must be inserted after RIPPLE (RIPPLE → DELIBERATE → OBSERVE)
            called = MockRuntime.call_args.kwargs
            extra_phases = called.get("extra_phases") or {}
            assert extra_phases.get("DELIBERATE", {}).get("after") == "RIPPLE"

    @pytest.mark.asyncio
    async def test_return_type_is_dict(self):
        """simulate() must always return Dict[str, Any], never a typed object."""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder"):

            mock_skill = MagicMock()
            mock_skill.name = "social-media"
            mock_skill.prompts = {"omniscient": "prompt"}
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            result = await simulate(event="test", skill="social-media")
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_output_contains_disclaimer(self):
        """每份输出必须包含强制免责声明。"""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder"):

            mock_skill = MagicMock()
            mock_skill.name = "pmf-validation"
            mock_skill.prompts = {"omniscient": "p"}
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            result = await simulate(
                event={"description": "test"},
                skill="pmf-validation",
            )
            assert "disclaimer" in result
            assert "不构成" in result["disclaimer"] or "does not constitute" in result["disclaimer"]


class TestCrossEnsembleBudgetControl:
    @pytest.mark.asyncio
    async def test_ensemble_budget_hard_limit(self):
        """v4.1 预算口径：单次 simulate() 的 max_llm_calls 不因 ensemble_runs 倍增。"""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder"):

            mock_skill = MagicMock()
            mock_skill.name = "pmf-validation"
            mock_skill.prompts = {"omniscient": "p"}
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=100)
            MockRouter.return_value = mock_router
            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            result = await simulate(
                event={"description": "test"},
                skill="pmf-validation",
                ensemble_runs=3,
                max_llm_calls=100,
            )
            assert MockRouter.call_count == 1
            assert MockRouter.call_args.kwargs["max_llm_calls"] == 100
            assert isinstance(result, dict)


class TestModelRouterTribunalFallback:
    def test_tribunal_fallback_to_omniscient(self):
        """When tribunal role not configured, should fall back to omniscient."""
        from ripple.llm.router import ModelRouter
        router = ModelRouter(
            llm_config={"omniscient": "gpt-4o"},
            max_llm_calls=100,
        )
        model = router.get_model("tribunal")
        assert model is not None


class TestVariantIsolationProtocol:
    def test_seed_consistency_across_variants(self):
        """同一 run_idx 跨 variant 使用相同 seed。"""
        from ripple.api.variant_isolation import compute_variant_seeds
        base_seed = 42
        seeds_a = compute_variant_seeds("variant_a", base_seed, ensemble_runs=3)
        seeds_b = compute_variant_seeds("variant_b", base_seed, ensemble_runs=3)
        # Same run_idx should produce same seed across variants
        assert seeds_a == seeds_b

    def test_shuffled_evaluation_order(self):
        """DELIBERATE 呈现方案顺序应随机化。"""
        from ripple.api.variant_isolation import shuffle_variant_order
        import random
        random.seed(42)
        variants = ["A", "B", "C"]
        orders = [shuffle_variant_order(variants, seed=i) for i in range(10)]
        # Not all orders should be the same (randomization working)
        unique_orders = set(tuple(o) for o in orders)
        assert len(unique_orders) > 1


class TestSocialMediaDeliberateRegistration:
    @pytest.mark.asyncio
    async def test_social_media_with_tribunal_registers_deliberate(self):
        """social-media skill with tribunal prompt should register DELIBERATE phase."""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder"):

            mock_skill = MagicMock()
            mock_skill.name = "social-media"
            mock_skill.version = "0.2.0"
            mock_skill.domain_profile = "social media profile"
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            mock_skill.prompts = {
                "omniscient": "omniscient prompt",
                "tribunal": "tribunal prompt",
                "star": "star prompt",
                "sea": "sea prompt",
            }
            mock_skill.rubrics = {"propagation-calibration": "rubric content"}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            result = await simulate(
                event={"description": "test content"},
                skill="social-media",
            )
            assert isinstance(result, dict)

            # DELIBERATE must be registered for social-media (has tribunal prompt)
            called = MockRuntime.call_args.kwargs
            extra_phases = called.get("extra_phases") or {}
            assert "DELIBERATE" in extra_phases
            assert extra_phases["DELIBERATE"]["after"] == "RIPPLE"

    @pytest.mark.asyncio
    async def test_skill_without_tribunal_no_deliberate(self):
        """Skill without tribunal prompt should NOT register DELIBERATE."""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder"):

            mock_skill = MagicMock()
            mock_skill.name = "some-skill"
            mock_skill.version = "0.1.0"
            mock_skill.domain_profile = "profile"
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            mock_skill.prompts = {
                "omniscient": "omniscient prompt",
                "star": "star prompt",
                "sea": "sea prompt",
            }
            # No tribunal prompt → MagicMock().get("tribunal") returns a MagicMock, not None!
            # So we need to use a real dict for prompts
            mock_skill.prompts = dict(mock_skill.prompts)  # ensure it's a real dict
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            await simulate(event="test", skill="some-skill")

            called = MockRuntime.call_args.kwargs
            extra_phases = called.get("extra_phases")
            assert extra_phases is None
