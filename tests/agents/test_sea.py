# tests/agents/test_sea.py
# 海 Agent 测试 / SeaAgent tests
import pytest
import json
from unittest.mock import AsyncMock
from ripple.agents.sea import SeaAgent


class TestSeaAgent:
    @pytest.mark.asyncio
    async def test_respond_amplify(self):
        """海 Agent 应能放大涟漪。 / SeaAgent should amplify ripple."""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "amplify",
            "cluster_reaction": "群体积极转发，讨论热烈",
            "outgoing_energy": 0.6,
            "sentiment_shift": "正面情绪增强",
            "reasoning": "内容与群体兴趣高度匹配",
        })

        sea = SeaAgent(
            agent_id="sea_young_women",
            description="18-25岁女性用户群体，对美妆护肤感兴趣",
            llm_caller=mock_llm,
        )
        response = await sea.respond(
            ripple_content="护肤成分科普",
            ripple_energy=0.7,
            ripple_source="star_kol_1",
        )

        assert response["response_type"] == "amplify"
        assert response["cluster_reaction"]
        assert 0.0 <= response["outgoing_energy"] <= 1.0

    @pytest.mark.asyncio
    async def test_respond_suppress(self):
        """海 Agent 应能压制涟漪（沉默螺旋）。 / SeaAgent should suppress ripple (spiral of silence)."""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "suppress",
            "cluster_reaction": "少数人私下吐槽，但不敢公开反对",
            "outgoing_energy": 0.1,
            "sentiment_shift": "表面沉默，内心不满积累",
            "reasoning": "主流意见压力导致沉默螺旋",
        })

        sea = SeaAgent(
            agent_id="sea_conservatives",
            description="保守派用户群体",
            llm_caller=mock_llm,
        )
        response = await sea.respond(
            ripple_content="争议性话题",
            ripple_energy=0.6,
            ripple_source="sea_liberals",
        )

        assert response["response_type"] == "suppress"

    @pytest.mark.asyncio
    async def test_respond_mutate(self):
        """海 Agent 应能变异涟漪（语义漂移）。 / SeaAgent should mutate ripple (semantic drift)."""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "mutate",
            "cluster_reaction": "群体将话题从美妆转向了职场话题",
            "outgoing_energy": 0.5,
            "sentiment_shift": "话题方向发生漂移",
            "reasoning": "内容触发了群体对职场经历的联想",
        })

        sea = SeaAgent(
            agent_id="sea_office_workers",
            description="职场新人群体",
            llm_caller=mock_llm,
        )
        response = await sea.respond(
            ripple_content="美妆博主被质疑虚假宣传",
            ripple_energy=0.5,
            ripple_source="sea_young_women",
        )

        assert response["response_type"] == "mutate"

    @pytest.mark.asyncio
    async def test_group_diversity_in_prompt(self):
        """海 Agent 的 prompt 应包含群体内部差异性提示。 / SeaAgent prompt should include intra-group diversity hint."""
        mock_llm = AsyncMock()
        mock_llm.return_value = json.dumps({
            "response_type": "absorb",
            "cluster_reaction": "test",
            "outgoing_energy": 0.2,
            "sentiment_shift": "test",
            "reasoning": "test",
        })

        sea = SeaAgent(
            agent_id="sea_1", description="test group",
            llm_caller=mock_llm,
        )
        await sea.respond(
            ripple_content="test", ripple_energy=0.5, ripple_source="x",
        )

        # 验证 system_prompt 中包含差异性提示 / Verify system_prompt contains diversity hint
        call_args = mock_llm.call_args
        system_prompt = call_args.kwargs.get(
            "system_prompt", call_args.args[0] if call_args.args else ""
        )
        assert "铁板一块" in system_prompt or "分歧" in system_prompt \
            or "不同看法" in system_prompt

    @pytest.mark.asyncio
    async def test_default_behavior_anchor_in_prompt(self):
        """Sea prompt 应将默认行为锚定为 absorb 而非 amplify。 / Sea prompt should anchor default as absorb, not amplify."""
        calls = []

        async def tracking_caller(*, system_prompt="", user_prompt=""):
            calls.append({"system": system_prompt, "user": user_prompt})
            return json.dumps({
                "response_type": "absorb",
                "cluster_reaction": "群体关注但未传播",
                "outgoing_energy": 0.3,
                "sentiment_shift": "neutral",
                "reasoning": "test",
            })

        from ripple.agents.sea import SeaAgent
        sea = SeaAgent(
            agent_id="sea_test",
            description="测试群体",
            llm_caller=tracking_caller,
        )
        await sea.respond(
            ripple_content="test content",
            ripple_energy=0.5,
            ripple_source="test",
        )

        system_prompt = calls[0]["system"]
        assert "默认" in system_prompt or "观察" in system_prompt
        assert "吸收" in system_prompt or "absorb" in system_prompt
