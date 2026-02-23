"""星 Agent —— 纯行为模拟器。 / Star Agent — pure behavior simulator.

星 Agent 只知道： / Star Agent only knows:
1. 自己的画像描述 / Its own profile description
2. 收到的涟漪（内容、能量、来源） / Received ripples (content, energy, source)
3. 自己的历史记忆 / Its own historical memory

不知道：全局状态、其他 Agent、传播全貌、平台参数。
/ Unaware of: global state, other agents, propagation overview, platform params.
"""

import json
import logging
from typing import Any, Callable, Awaitable, Dict, List

from ripple.prompts import (
    STAR_SYSTEM_PROMPT,
    STAR_USER_PROMPT,
    STAR_MEMORY_LINE,
    STAR_MEMORY_HEADER,
)

logger = logging.getLogger(__name__)

VALID_RESPONSE_TYPES = {"amplify", "create", "comment", "ignore"}
FALLBACK_RESPONSE = {
    "response_type": "ignore",
    "response_content": "",
    "outgoing_energy": 0.0,
    "reasoning": "LLM 调用失败，安全降级",
}


class StarAgent:
    """星 Agent：个体 KOL 行为模拟器。 / Star Agent: individual KOL behavior simulator."""

    def __init__(
        self,
        agent_id: str,
        description: str,
        llm_caller: Callable[..., Awaitable[str]],
        system_prompt_template: str = "",
        max_retries: int = 1,
    ):
        self.agent_id = agent_id
        self.description = description
        self._llm_caller = llm_caller
        self._system_prompt_template = system_prompt_template
        self._max_retries = max_retries
        self.memory: List[Dict[str, Any]] = []

    async def respond(
        self,
        ripple_content: str,
        ripple_energy: float,
        ripple_source: str,
    ) -> Dict[str, Any]:
        """收到涟漪后生成响应。 / Generate response upon receiving a ripple."""
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            ripple_content, ripple_energy, ripple_source,
        )

        for attempt in range(1 + self._max_retries):
            try:
                logger.info(
                    f"Star Agent {self.agent_id} 调用 LLM "
                    f"(能量={ripple_energy:.2f}, 来源={ripple_source})"
                )
                raw = await self._llm_caller(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                response = self._parse_response(raw)
                self.memory.append({
                    "ripple_content": ripple_content,
                    "ripple_energy": ripple_energy,
                    "ripple_source": ripple_source,
                    "my_response": response,
                })
                return response
            except Exception as e:
                logger.warning(
                    f"星 Agent {self.agent_id} 第 {attempt+1} 次失败: {e}"
                )

        self.memory.append({
            "ripple_content": ripple_content,
            "ripple_energy": ripple_energy,
            "ripple_source": ripple_source,
            "my_response": FALLBACK_RESPONSE,
        })
        return dict(FALLBACK_RESPONSE)

    def _build_system_prompt(self) -> str:
        memory_context = ""
        if self.memory:
            recent = self.memory[-5:]  # 最近5条 / Last 5 entries
            memory_lines = []
            for m in recent:
                memory_lines.append(
                    STAR_MEMORY_LINE.format(
                        ripple_source=m['ripple_source'],
                        ripple_content_preview=m['ripple_content'][:50],
                        response_type=m['my_response']['response_type'],
                    )
                )
            memory_context = STAR_MEMORY_HEADER + "\n".join(memory_lines)

        base = STAR_SYSTEM_PROMPT.format(
            description=self.description,
            memory_context=memory_context,
        )
        # v4: Prepend skill context (if injected via system_prompt_template)
        if self._system_prompt_template:
            return self._system_prompt_template + base
        return base

    def _build_user_prompt(
        self, content: str, energy: float, source: str,
    ) -> str:
        return STAR_USER_PROMPT.format(
            source=source,
            energy=energy,
            content=content,
        )

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        data = json.loads(text)
        rtype = data.get("response_type", "ignore")
        if rtype not in VALID_RESPONSE_TYPES:
            rtype = "ignore"
        energy = max(0.0, min(1.0, float(data.get("outgoing_energy", 0.0))))

        return {
            "response_type": rtype,
            "response_content": data.get("response_content", ""),
            "outgoing_energy": energy,
            "reasoning": data.get("reasoning", ""),
        }
