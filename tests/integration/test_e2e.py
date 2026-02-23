"""端到端集成测试：模拟一条小红书笔记的传播。
/ E2E integration test: simulate propagation of a Xiaohongshu note.

使用 mock LLM，验证完整的 5-Phase 流程。
Uses mock LLM to verify the full 5-Phase pipeline.
"""
import pytest
import json
from unittest.mock import AsyncMock
from ripple.engine.runtime import SimulationRuntime


class TestE2ESimulation:
    @pytest.mark.asyncio
    async def test_xiaohongshu_note_propagation(self):
        """模拟一条小红书美妆笔记的传播过程。
        / Simulate propagation of a Xiaohongshu beauty note.

        预期流程 / Expected flow：
        1. INIT: 全视者初始化 1 star + 2 sea / Omniscient inits 1 star + 2 sea
        2. SEED: 种子涟漪注入 / Seed ripple injection
        3. RIPPLE wave 0: sea_young_women amplify
        4. RIPPLE wave 1: sea_students mutate (破圈 / cross-circle)
        5. RIPPLE wave 2: star_kol amplify
        6. RIPPLE wave 3: 终止 / terminate
        7. OBSERVE: 检测破圈涌现 / Detect cross-circle emergence
        8. FEEDBACK & RECORD
        """

        # 准备所有 mock 响应 — INIT 拆分为 3 次 sub-call / Prepare all mock responses — INIT split into 3 sub-calls
        init_dynamics = json.dumps({
            "wave_time_window": "4h",
            "wave_time_window_reasoning": "小红书内容在4-6小时内决定命运",
            "energy_decay_per_wave": 0.15,
            "platform_characteristics": "内容驱动型平台",
        })

        init_agents = json.dumps({
            "star_configs": [
                {"id": "star_kol", "description": "美妆头部博主，50万粉丝",
                 "influence_level": "high"}
            ],
            "sea_configs": [
                {"id": "sea_young_women",
                 "description": "18-25岁女性，美妆护肤兴趣",
                 "interest_tags": ["美妆", "护肤"]},
                {"id": "sea_students",
                 "description": "大学生群体，关注性价比和真实评价",
                 "interest_tags": ["学生", "性价比"]},
            ],
        })

        init_topology = json.dumps({
            "topology": {
                "edges": [
                    {"from": "star_kol", "to": "sea_young_women",
                     "weight": 0.8},
                    {"from": "sea_young_women", "to": "sea_students",
                     "weight": 0.4},
                    {"from": "sea_students", "to": "star_kol",
                     "weight": 0.2},
                ]
            },
            "seed_ripple": {
                "content": "成分党必看！这款面霜的真实成分分析",
                "initial_energy": 0.6,
            },
        })

        wave_responses = [
            # Wave 0: 激活 sea_young_women / Activate sea_young_women
            json.dumps({
                "wave_number": 0,
                "simulated_time_elapsed": "4h",
                "simulated_time_remaining": "44h",
                "continue_propagation": True,
                "activated_agents": [
                    {"agent_id": "sea_young_women",
                     "incoming_ripple_energy": 0.6,
                     "activation_reason": "美妆成分话题直接命中核心兴趣"}
                ],
                "skipped_agents": [
                    {"agent_id": "star_kol",
                     "skip_reason": "内容尚未形成话题，KOL不会主动参与"},
                    {"agent_id": "sea_students",
                     "skip_reason": "与学生群体兴趣关联度不够"},
                ],
                "global_observation": "内容在美妆爱好者中引发关注",
            }),
            # Wave 1: 激活 sea_students（破圈） / Activate sea_students (cross-circle)
            json.dumps({
                "wave_number": 1,
                "simulated_time_elapsed": "8h",
                "simulated_time_remaining": "40h",
                "continue_propagation": True,
                "activated_agents": [
                    {"agent_id": "sea_students",
                     "incoming_ripple_energy": 0.45,
                     "activation_reason": "年轻女性群体的热烈反应引起学生圈关注"}
                ],
                "skipped_agents": [
                    {"agent_id": "star_kol",
                     "skip_reason": "话题热度还不够触及KOL关注阈值"},
                ],
                "global_observation": "开始出现破圈迹象，讨论从纯美妆扩展到消费观",
            }),
            # Wave 2: 激活 star_kol / Activate star_kol
            json.dumps({
                "wave_number": 2,
                "simulated_time_elapsed": "12h",
                "simulated_time_remaining": "36h",
                "continue_propagation": True,
                "activated_agents": [
                    {"agent_id": "star_kol",
                     "incoming_ripple_energy": 0.75,
                     "activation_reason": "话题已跨越两个群体，形成可讨论的趋势"}
                ],
                "skipped_agents": [],
                "global_observation": "KOL参与将进一步放大传播",
            }),
            # Wave 3: 终止 / Terminate
            json.dumps({
                "wave_number": 3,
                "simulated_time_elapsed": "16h",
                "simulated_time_remaining": "32h",
                "continue_propagation": False,
                "termination_reason": "传播峰值已过，进入自然衰减期",
                "activated_agents": [],
                "skipped_agents": [],
                "global_observation": "内容生命周期进入尾声",
            }),
        ]

        observe_resp = json.dumps({
            "phase_vector": {
                "heat": "explosion",
                "sentiment": "unified",
                "coherence": "ordered",
            },
            "phase_transition_detected": True,
            "transition_description": "内容从小圈子美妆讨论演变为跨圈层消费话题",
            "emergence_events": [
                {"description": "破圈涌现：美妆话题触发了学生群体的消费观讨论",
                 "evidence": "sea_students 的 mutate 响应"}
            ],
            "topology_recommendations": [],
        })

        synth_resp = json.dumps({
            "prediction": {
                "impact": "high",
                "reach_estimate": "10-50万曝光",
                "verdict": "内容有较大概率成为小爆款",
            },
            "timeline": [
                {"wave": 0, "event": "核心美妆圈响应"},
                {"wave": 1, "event": "破圈至学生群体"},
                {"wave": 2, "event": "KOL参与放大"},
            ],
            "bifurcation_points": [
                {"wave": 1, "description": "如果学生群体未响应，传播会局限在美妆圈"}
            ],
            "agent_insights": {
                "star_kol": "在话题成熟后参与，符合KOL行为模式",
                "sea_young_women": "核心驱动力，初始放大者",
                "sea_students": "破圈关键，将话题从美妆扩展到消费观",
            },
        })

        omniscient_responses = [
            init_dynamics, init_agents, init_topology,
            *wave_responses, observe_resp, synth_resp,
        ]
        omniscient_caller = AsyncMock(side_effect=omniscient_responses)

        # Agent 响应 / Agent responses
        agent_responses = {
            "sea_young_women": json.dumps({
                "response_type": "amplify",
                "cluster_reaction": "大量收藏和转发，评论区热烈讨论成分",
                "outgoing_energy": 0.65,
                "sentiment_shift": "正面兴奋",
                "reasoning": "成分分析类内容是该群体最关注的",
            }),
            "sea_students": json.dumps({
                "response_type": "mutate",
                "cluster_reaction": "话题从成分分析转向了'学生党平替推荐'",
                "outgoing_energy": 0.5,
                "sentiment_shift": "正面但方向漂移",
                "reasoning": "学生群体更关心性价比，将话题带向了消费观",
            }),
            "star_kol": json.dumps({
                "response_type": "amplify",
                "response_content": "这条笔记分析得很专业，我来补充几点",
                "outgoing_energy": 0.85,
                "reasoning": "话题已成趋势，参与可增加自己的专业形象",
            }),
        }

        call_count = {"n": 0}

        async def agent_caller(**kwargs):
            # 按调用顺序返回不同 Agent 的响应 / Return different agent responses by call order
            call_count["n"] += 1
            if call_count["n"] == 1:
                return agent_responses["sea_young_women"]
            elif call_count["n"] == 2:
                return agent_responses["sea_students"]
            else:
                return agent_responses["star_kol"]

        runtime = SimulationRuntime(
            omniscient_caller=omniscient_caller,
            agent_caller=agent_caller,
        )

        result = await runtime.run({
            "event": {"description": "成分党必看！这款面霜的真实成分分析"},
            "skill": "social-media",
            "platform": "xiaohongshu",
            "simulation_horizon": "48h",
            "source": {"description": "小红书美妆博主，3万粉丝"},
        })

        # 验证完整结果 / Verify complete results
        assert result["total_waves"] == 3  # Wave 0, 1, 2 有效
        assert result["wave_records_count"] == 3
        assert "prediction" in result
        assert result["prediction"]["impact"] == "high"
