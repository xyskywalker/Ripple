# tests/agents/test_omniscient.py
import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from ripple.agents.omniscient import OmniscientAgent
from ripple.primitives.models import OmniscientVerdict


class TestOmniscientInit:
    @pytest.mark.asyncio
    async def test_init_produces_star_and_sea_configs(self):
        """全视者 INIT 应通过 3 次 sub-call 产生星海配置、拓扑、动态参数。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.side_effect = [
            # Sub-call 1: dynamics
            json.dumps({
                "wave_time_window": "4h",
                "wave_time_window_reasoning": "小红书内容在4-6小时内决定命运",
                "energy_decay_per_wave": 0.15,
                "platform_characteristics": "内容驱动型平台",
            }),
            # Sub-call 2: agents
            json.dumps({
                "star_configs": [
                    {"id": "star_kol_1", "description": "美妆头部博主",
                     "influence_level": "high"}
                ],
                "sea_configs": [
                    {"id": "sea_young_women", "description": "18-25岁女性用户群体",
                     "interest_tags": ["美妆", "护肤"]},
                    {"id": "sea_students", "description": "大学生群体",
                     "interest_tags": ["学习", "生活"]},
                ],
            }),
            # Sub-call 3: topology
            json.dumps({
                "topology": {
                    "edges": [
                        {"from": "star_kol_1", "to": "sea_young_women",
                         "weight": 0.8},
                        {"from": "sea_young_women", "to": "sea_students",
                         "weight": 0.3},
                    ]
                },
                "seed_ripple": {
                    "content": "测试内容",
                    "initial_energy": 0.6,
                },
            }),
        ]

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        result = await agent.init(
            skill_profile="这是社交媒体领域的画像描述...",
            simulation_input={
                "event": {"description": "一条美妆笔记"},
                "skill": "social-media",
                "platform": "xiaohongshu",
            },
        )

        assert mock_llm_caller.call_count == 3  # 3 sub-calls
        assert len(result["star_configs"]) >= 1
        assert len(result["sea_configs"]) >= 1
        assert "topology" in result
        assert "dynamic_parameters" in result
        assert result["dynamic_parameters"]["wave_time_window"] == "4h"

    @pytest.mark.asyncio
    async def test_init_3_subcalls_order(self):
        """验证 INIT 3 次 sub-call 的顺序和内容传递。"""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            idx = len(calls)
            if idx == 1:
                return json.dumps({
                    "wave_time_window": "2h",
                    "wave_time_window_reasoning": "test",
                    "energy_decay_per_wave": 0.1,
                    "platform_characteristics": "test",
                })
            elif idx == 2:
                return json.dumps({
                    "star_configs": [{"id": "star_1", "description": "t",
                                      "influence_level": "low"}],
                    "sea_configs": [{"id": "sea_1", "description": "t",
                                     "interest_tags": []}],
                })
            else:
                return json.dumps({
                    "topology": {"edges": []},
                    "seed_ripple": {"content": "t", "initial_energy": 0.5},
                })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        result = await agent.init(
            skill_profile="test_profile",
            simulation_input={"event": {"description": "test"},
                              "skill": "test"},
        )

        assert len(calls) == 3
        # Sub-call 1 should mention time extraction
        assert "时间" in calls[0] or "wave_time_window" in calls[0]
        # Sub-call 2 should mention agents
        assert "star_configs" in calls[1] or "Agent" in calls[1]
        # Sub-call 3 should mention topology
        assert "topology" in calls[2] or "拓扑" in calls[2]
        # Sub-call 2 should receive dynamics from sub-call 1
        assert "2h" in calls[1]
        # Sub-call 3 should receive agent configs from sub-call 2
        assert "star_1" in calls[2]

    @pytest.mark.asyncio
    async def test_init_retry_on_invalid_json(self):
        """全视者 INIT sub-call 在 JSON 解析失败时应重试。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.side_effect = [
            "这不是JSON",  # Sub-call 1 第1次失败
            json.dumps({    # Sub-call 1 第2次成功
                "wave_time_window": "2h",
                "wave_time_window_reasoning": "test",
                "energy_decay_per_wave": 0.1,
                "platform_characteristics": "test",
            }),
            json.dumps({    # Sub-call 2
                "star_configs": [{"id": "star_1", "description": "test",
                                  "influence_level": "medium"}],
                "sea_configs": [{"id": "sea_1", "description": "test",
                                 "interest_tags": []}],
            }),
            json.dumps({    # Sub-call 3
                "topology": {"edges": []},
                "seed_ripple": {"content": "test", "initial_energy": 0.5},
            }),
        ]

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        result = await agent.init(
            skill_profile="test",
            simulation_input={"event": {"description": "test"},
                              "skill": "test"},
        )

        assert mock_llm_caller.call_count == 4  # 1 retry + 3 successful
        assert len(result["star_configs"]) >= 1


class TestOmniscientRippleVerdict:
    @pytest.mark.asyncio
    async def test_ripple_verdict_activates_agents(self):
        """RIPPLE 裁决应返回 OmniscientVerdict 含激活列表。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.return_value = json.dumps({
            "wave_number": 1,
            "simulated_time_elapsed": "4h",
            "simulated_time_remaining": "44h",
            "continue_propagation": True,
            "activated_agents": [
                {"agent_id": "sea_young_women",
                 "incoming_ripple_energy": 0.7,
                 "activation_reason": "兴趣匹配"},
            ],
            "skipped_agents": [
                {"agent_id": "star_kol_1",
                 "skip_reason": "内容尚未形成话题"},
            ],
            "global_observation": "初始传播",
        })

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        verdict = await agent.ripple_verdict(
            field_snapshot={"agents": {}, "ripples": []},
            wave_number=1,
            propagation_history="种子涟漪已注入",
        )

        assert isinstance(verdict, OmniscientVerdict)
        assert verdict.continue_propagation is True
        assert len(verdict.activated_agents) == 1
        assert verdict.activated_agents[0].agent_id == "sea_young_women"
        assert len(verdict.skipped_agents) == 1

    @pytest.mark.asyncio
    async def test_ripple_verdict_terminates(self):
        """RIPPLE 裁决应能终止传播。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.return_value = json.dumps({
            "wave_number": 8,
            "simulated_time_elapsed": "16h",
            "simulated_time_remaining": "0h",
            "continue_propagation": False,
            "termination_reason": "时间窗口耗尽",
            "activated_agents": [],
            "skipped_agents": [],
            "global_observation": "传播已完全终止",
        })

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        verdict = await agent.ripple_verdict(
            field_snapshot={}, wave_number=8,
            propagation_history="",
        )

        assert verdict.continue_propagation is False
        assert verdict.termination_reason == "时间窗口耗尽"

    @pytest.mark.asyncio
    async def test_ripple_verdict_includes_time_progress(self):
        """RIPPLE 裁决的 prompt 应包含时间进度信息。"""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 3,
                "simulated_time_elapsed": "12h",
                "simulated_time_remaining": "36h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=3,
            propagation_history="test",
            wave_time_window="4h",
            simulation_horizon="48h",
        )

        assert len(calls) == 1
        prompt = calls[0]
        assert "模拟时间进度" in prompt
        assert "4h" in prompt
        assert "48h" in prompt
        assert "12.0h" in prompt  # elapsed = 3 * 4
        assert "36.0h" in prompt  # remaining = 48 - 12


class TestOmniscientObserve:
    @pytest.mark.asyncio
    async def test_observe_detects_emergence(self):
        """OBSERVE 应检测涌现事件。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.return_value = json.dumps({
            "phase_vector": {
                "heat": "growth",
                "sentiment": "unified",
                "coherence": "ordered",
            },
            "phase_transition_detected": False,
            "emergence_events": [
                {"description": "多个不相关群体产生共鸣",
                 "evidence": "sea_young_women 和 sea_students 独立放大"},
            ],
            "topology_recommendations": [],
        })

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        obs = await agent.observe(
            field_snapshot={},
            full_history="Wave 0-3 的完整历史...",
        )

        assert obs["phase_vector"]["heat"] == "growth"
        assert len(obs["emergence_events"]) == 1
        assert obs["phase_transition_detected"] is False

    @pytest.mark.asyncio
    async def test_observe_detects_phase_transition(self):
        """OBSERVE 应能检测相变。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.return_value = json.dumps({
            "phase_vector": {
                "heat": "explosion",
                "sentiment": "polarized",
                "coherence": "chaotic",
            },
            "phase_transition_detected": True,
            "transition_description": "系统从增长态突然进入爆发态",
            "emergence_events": [],
            "topology_recommendations": [
                {"from": "sea_a", "to": "sea_b", "weight_delta": 0.1},
            ],
        })

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        obs = await agent.observe(field_snapshot={}, full_history="")

        assert obs["phase_transition_detected"] is True
        assert "explosion" in obs["phase_vector"]["heat"]


class TestOmniscientSynthesizeResult:
    @pytest.mark.asyncio
    async def test_synthesize_result(self):
        """synthesize_result 应返回预测结果。"""
        mock_llm_caller = AsyncMock()
        mock_llm_caller.return_value = json.dumps({
            "prediction": {"impact": "high"},
            "timeline": [{"wave": 0, "event": "初始传播"}],
            "bifurcation_points": [],
            "agent_insights": {"star_1": "关键放大者"},
        })

        agent = OmniscientAgent(llm_caller=mock_llm_caller)
        result = await agent.synthesize_result(
            field_snapshot={},
            observation={"phase_vector": {"heat": "growth"}},
            simulation_input={"event": {"description": "test"}},
        )

        assert "prediction" in result
        assert result["prediction"]["impact"] == "high"
        assert len(result["timeline"]) == 1


class TestOmniscientObservePrompt:
    @pytest.mark.asyncio
    async def test_observe_prompt_constrains_heat_values(self):
        """OBSERVE prompt should contain JSON example with heat enum values."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "phase_vector": {
                    "heat": "growth",
                    "sentiment": "unified",
                    "coherence": "ordered",
                },
                "phase_transition_detected": False,
                "emergence_events": [],
                "topology_recommendations": [],
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.observe(field_snapshot={}, full_history="test")

        prompt = calls[0]
        # Should contain enum constraint for heat
        assert "seed" in prompt and "growth" in prompt and "explosion" in prompt
        assert "stable" in prompt and "decline" in prompt
        # Should contain JSON example
        assert '"phase_vector"' in prompt
        assert '"heat"' in prompt


class TestOmniscientCASPrompt:
    @pytest.mark.asyncio
    async def test_ripple_prompt_contains_cas_principles(self):
        """Ripple prompt should include CAS accumulation principles."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 0,
                "simulated_time_elapsed": "0h",
                "simulated_time_remaining": "48h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=0,
            propagation_history="test",
        )

        prompt = calls[0]
        # CAS principles should be present
        assert "累积叠加" in prompt
        assert "自然衰减" in prompt
        assert "非线性" in prompt or "涌现" in prompt
        assert "反馈" in prompt

    @pytest.mark.asyncio
    async def test_ripple_prompt_contains_anti_optimism_principles(self):
        """Ripple prompt should include anti-optimism CAS principles."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 0,
                "simulated_time_elapsed": "0h",
                "simulated_time_remaining": "48h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=0,
            propagation_history="test",
        )

        prompt = calls[0]
        assert "注意力" in prompt and "竞争" in prompt
        assert "饱和" in prompt
        assert "基础概率" in prompt or "默认预期" in prompt

    @pytest.mark.asyncio
    async def test_ripple_prompt_json_example_shows_multiple_agents(self):
        """JSON example in prompt should show multiple agents including
        continued activation pattern."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 1,
                "simulated_time_elapsed": "2h",
                "simulated_time_remaining": "46h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=1,
            propagation_history="test",
        )

        prompt = calls[0]
        # Example should contain multiple agents
        assert prompt.count('"agent_id"') >= 3

    @pytest.mark.asyncio
    async def test_ripple_prompt_shows_agent_stats(self):
        """When snapshot has agent stats, prompt should display them."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 3,
                "simulated_time_elapsed": "6h",
                "simulated_time_remaining": "42h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={
                "stars": {
                    "star_1": {
                        "description": "KOL A",
                        "activation_count": 3,
                        "last_wave": 2,
                        "last_energy": 0.55,
                        "last_response": "create",
                        "total_outgoing_energy": 1.5,
                    },
                },
                "seas": {},
            },
            wave_number=3,
            propagation_history="test",
        )

        prompt = calls[0]
        # Agent list should include activation stats
        assert "已激活3次" in prompt or "激活3次" in prompt


class TestOmniscientSynthPrompt:
    @pytest.mark.asyncio
    async def test_synth_prompt_specifies_timeline_fields(self):
        """SYNTHESIZE prompt should specify time_from_publish and wave_range fields."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "prediction": {"impact": "high", "verdict": "growth trend"},
                "timeline": [{"time_from_publish": "0-2h", "event": "test"}],
                "bifurcation_points": [{"wave_range": "Wave0-1", "turning_point": "test"}],
                "agent_insights": {},
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.synthesize_result(
            field_snapshot={}, observation={}, simulation_input={},
        )

        prompt = calls[0]
        assert "time_from_publish" in prompt
        assert "wave_range" in prompt
        assert "turning_point" in prompt
        assert "verdict" in prompt

    @pytest.mark.asyncio
    async def test_synth_prompt_requires_phase_keyword_in_verdict(self):
        """SYNTHESIZE prompt should instruct verdict to contain phase keyword."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "prediction": {"impact": "test", "verdict": "test"},
                "timeline": [],
                "bifurcation_points": [],
                "agent_insights": {},
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.synthesize_result(
            field_snapshot={}, observation={}, simulation_input={},
        )

        prompt = calls[0]
        # Should mention phase keywords
        assert "explosion" in prompt or "growth" in prompt


class TestOmniscientWave0Hint:
    @pytest.mark.asyncio
    async def test_wave0_prompt_contains_first_wave_hint(self):
        """Wave 0 ripple prompt should contain hint about Sea priority."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 0,
                "simulated_time_elapsed": "0h",
                "simulated_time_remaining": "48h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=0,
            propagation_history="test",
        )

        prompt = calls[0]
        assert "首轮" in prompt or "种子涟漪" in prompt
        assert "群体" in prompt or "Sea" in prompt

    @pytest.mark.asyncio
    async def test_wave1_prompt_does_not_contain_first_wave_hint(self):
        """Wave 1+ ripple prompt should NOT contain the first wave hint."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "wave_number": 1,
                "simulated_time_elapsed": "4h",
                "simulated_time_remaining": "44h",
                "continue_propagation": True,
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "test",
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        await agent.ripple_verdict(
            field_snapshot={"stars": {}, "seas": {}},
            wave_number=1,
            propagation_history="test",
        )

        prompt = calls[0]
        assert "首轮传播注意" not in prompt


class TestOmniscientSynthDualTemplate:
    @pytest.mark.asyncio
    async def test_synth_uses_relative_template_without_historical(self):
        """Without historical data, synth prompt should use relative template."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "prediction": {
                    "impact": "moderate",
                    "relative_estimate": {
                        "simulation_horizon": "48h",
                        "vs_baseline": "同类内容平均水平",
                        "views_relative": "+10%~+20%",
                        "engagements_relative": "+5%~+15%",
                        "favorites_relative": "+5%~+10%",
                        "comments_relative": "+5%~+15%",
                        "shares_relative": "+3%~+10%",
                        "follows_relative": "+2%~+8%",
                        "confidence": "low",
                        "confidence_reasoning": "无历史数据锚定",
                    },
                    "verdict": "growth trend",
                },
                "timeline": [],
                "bifurcation_points": [],
                "agent_insights": {},
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        result = await agent.synthesize_result(
            field_snapshot={},
            observation={},
            simulation_input={"event": {"description": "test"}},
        )

        prompt = calls[0]
        assert "relative_estimate" in prompt
        assert "不要输出任何绝对数字" in prompt or "相对百分比" in prompt
        assert "prediction" in result

    @pytest.mark.asyncio
    async def test_synth_uses_anchored_template_with_historical(self):
        """With historical data, synth prompt should use anchored template."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append(user_prompt)
            return json.dumps({
                "prediction": {
                    "impact": "moderate",
                    "anchored_estimate": {
                        "simulation_horizon": "48h",
                        "historical_baseline": {
                            "source": "用户提供的历史数据",
                            "metrics": {"avg_views": 50000},
                        },
                        "predicted_change": "+20%",
                        "views": {"p50": 60000, "p80": 75000, "p95": 100000},
                        "engagements_total": {"p50": 9600},
                        "favorites": {"p50": 3200},
                        "comments": {"p50": 640},
                        "shares": {"p50": 480},
                        "follows_gained": {"p50": 150},
                        "confidence": "high",
                        "confidence_reasoning": "基于历史数据锚定",
                    },
                    "verdict": "growth trend",
                },
                "timeline": [],
                "bifurcation_points": [],
                "agent_insights": {},
            })

        agent = OmniscientAgent(llm_caller=tracking_caller)
        result = await agent.synthesize_result(
            field_snapshot={},
            observation={},
            simulation_input={
                "event": {"description": "test"},
                "historical": [
                    {"likes": 5000, "collects": 2000, "comments": 800},
                ],
            },
        )

        prompt = calls[0]
        assert "anchored_estimate" in prompt
        assert "historical_baseline" in prompt
        assert "历史基线" in prompt or "历史" in prompt
        assert "prediction" in result
