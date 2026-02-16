# tests/agents/test_star.py
import pytest
import json
from unittest.mock import AsyncMock
from ripple.agents.star import StarAgent


class TestStarAgent:
    @pytest.mark.asyncio
    async def test_respond_amplify(self):
        """星 Agent 收到涟漪后应能输出放大响应。"""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "amplify",
            "response_content": "这条内容说得太对了，必须转发",
            "outgoing_energy": 0.8,
            "reasoning": "内容与我的粉丝群体高度相关",
        })

        star = StarAgent(
            agent_id="star_kol_1",
            description="美妆头部博主，50万粉丝",
            llm_caller=mock_llm,
        )
        response = await star.respond(
            ripple_content="一条关于护肤成分的深度笔记",
            ripple_energy=0.7,
            ripple_source="sea_young_women",
        )

        assert response["response_type"] == "amplify"
        assert 0.0 <= response["outgoing_energy"] <= 1.0
        assert response["response_content"]  # 非空

    @pytest.mark.asyncio
    async def test_respond_ignore(self):
        """星 Agent 应能选择忽略涟漪。"""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "ignore",
            "response_content": "",
            "outgoing_energy": 0.0,
            "reasoning": "内容与我的领域无关",
        })

        star = StarAgent(
            agent_id="star_tech",
            description="科技博主",
            llm_caller=mock_llm,
        )
        response = await star.respond(
            ripple_content="美妆笔记",
            ripple_energy=0.5,
            ripple_source="sea_young_women",
        )

        assert response["response_type"] == "ignore"
        assert response["outgoing_energy"] == 0.0

    @pytest.mark.asyncio
    async def test_memory_accumulation(self):
        """星 Agent 应记住之前收到的涟漪（RAG 记忆）。"""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "comment",
            "response_content": "之前那条笔记后续怎样了",
            "outgoing_energy": 0.3,
            "reasoning": "基于之前记忆的关联",
        })

        star = StarAgent(
            agent_id="star_1", description="test",
            llm_caller=mock_llm,
        )
        # 第一次响应
        await star.respond(
            ripple_content="第一条涟漪",
            ripple_energy=0.5, ripple_source="sea_a",
        )
        # 第二次响应——应能看到记忆
        await star.respond(
            ripple_content="第二条涟漪",
            ripple_energy=0.6, ripple_source="sea_b",
        )

        assert len(star.memory) == 2

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self):
        """LLM 失败时应降级为 ignore。"""
        mock_llm = AsyncMock(side_effect=Exception("LLM down"))

        star = StarAgent(
            agent_id="star_1", description="test",
            llm_caller=mock_llm,
        )
        response = await star.respond(
            ripple_content="test",
            ripple_energy=0.5, ripple_source="sea_a",
        )

        assert response["response_type"] == "ignore"
        assert response["outgoing_energy"] == 0.0
