"""海 Agent —— 群体行为模拟器。 / Sea Agent — crowd behavior simulator.

海 Agent 只知道： / Sea Agent only knows:
1. 自己代表的群体画像 / Its represented crowd profile
2. 收到的涟漪 / Received ripples
3. 当前群体情绪（滑动窗口记忆） / Current crowd sentiment (sliding-window memory)

不知道：全局状态、其他 Agent、传播全貌、平台参数。
/ Unaware of: global state, other agents, propagation overview, platform params.

Prompt 中显式引入群体内部差异性，避免 LLM 从众倾向过强（借鉴 OASIS 论文发现）。
/ Explicitly introduces intra-group diversity in prompts to counter LLM conformity bias (per OASIS findings).
"""

import json
import logging
from typing import Any, Callable, Awaitable, Dict, List

from ripple.prompts import (
    SEA_SYSTEM_PROMPT,
    SEA_USER_PROMPT,
    SEA_MEMORY_LINE,
    SEA_MEMORY_HEADER,
)

logger = logging.getLogger(__name__)

VALID_SEA_RESPONSE_TYPES = {
    "amplify", "absorb", "mutate", "suppress", "ignore",
}
FALLBACK_SEA_RESPONSE = {
    "response_type": "ignore",
    "cluster_reaction": "",
    "outgoing_energy": 0.0,
    "sentiment_shift": "",
    "reasoning": "LLM 调用失败，安全降级",
}


class SeaAgent:
    """海 Agent：群体行为模拟器。 / Sea Agent: crowd behavior simulator."""

    def __init__(
        self,
        agent_id: str,
        description: str,
        llm_caller: Callable[..., Awaitable[str]],
        system_prompt_template: str = "",
        max_retries: int = 1,
        memory_window: int = 5,
    ):
        self.agent_id = agent_id
        self.description = description
        self._llm_caller = llm_caller
        self._system_prompt_template = system_prompt_template
        self._max_retries = max_retries
        self._memory_window = memory_window
        self.memory: List[Dict[str, Any]] = []

    async def respond(
        self,
        ripple_content: str,
        ripple_energy: float,
        ripple_source: str,
    ) -> Dict[str, Any]:
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            ripple_content, ripple_energy, ripple_source,
        )

        for attempt in range(1 + self._max_retries):
            try:
                logger.info(
                    f"Sea Agent {self.agent_id} 调用 LLM "
                    f"(能量={ripple_energy:.2f}, 来源={ripple_source})"
                )
                raw = await self._llm_caller(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                response = self._parse_response(raw)
                self.memory.append({
                    "ripple_content": ripple_content,
                    "ripple_source": ripple_source,
                    "response": response,
                })
                # 滑动窗口 / Sliding window
                if len(self.memory) > self._memory_window:
                    self.memory = self.memory[-self._memory_window:]
                return response
            except Exception as e:
                logger.warning(
                    f"海 Agent {self.agent_id} 第 {attempt+1} 次失败: {e}"
                )

        return dict(FALLBACK_SEA_RESPONSE)

    def _build_system_prompt(self) -> str:
        memory_context = ""
        if self.memory:
            lines = []
            for m in self.memory:
                lines.append(
                    SEA_MEMORY_LINE.format(
                        ripple_source=m['ripple_source'],
                        response_type=m['response']['response_type'],
                    )
                )
            memory_context = SEA_MEMORY_HEADER + "\n".join(lines)

        base = SEA_SYSTEM_PROMPT.format(
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
        return SEA_USER_PROMPT.format(
            source=source,
            energy=energy,
            content=content,
        )

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        if raw is None:
            raise ValueError("SeaAgent LLM response is None")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            raise TypeError(
                f"SeaAgent expected str response, got {type(raw).__name__}"
            )

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
        if rtype not in VALID_SEA_RESPONSE_TYPES:
            rtype = "ignore"
        energy = max(0.0, min(1.0, float(data.get("outgoing_energy", 0.0))))

        return {
            "response_type": rtype,
            "cluster_reaction": data.get("cluster_reaction", ""),
            "outgoing_energy": energy,
            "sentiment_shift": data.get("sentiment_shift", ""),
            "reasoning": data.get("reasoning", ""),
        }
