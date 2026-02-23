"""合议庭评审员 Agent。 / Tribunal Agent — expert evaluator for PMF validation.

TribunalAgent 扮演专业角色（市场分析师、魔鬼代言人等），
对产品方案进行结构化评估和辩论。
/ Plays professional roles (market analyst, devil's advocate, etc.)
for structured evaluation and debate of product proposals.
"""

import json
import logging
from typing import Any, Callable, Awaitable, Dict, List

from ripple.primitives.pmf_models import TribunalOpinion
from ripple.utils.json_parser import parse_json_from_llm

logger = logging.getLogger(__name__)

FALLBACK_SCORES: Dict[str, int] = {}  # Empty fallback


class TribunalAgent:
    """合议庭评审员：专业角色评估器。 / Tribunal Agent: professional role evaluator."""

    def __init__(
        self,
        role: str,
        perspective: str,
        expertise: str,
        llm_caller: Callable[..., Awaitable[str]],
        system_prompt: str = "",
        max_retries: int = 2,
    ):
        self.role = role
        self.perspective = perspective
        self.expertise = expertise
        self._llm_caller = llm_caller
        self._system_prompt = system_prompt
        self._max_retries = max_retries

    async def _call_llm(self, user_prompt: str) -> str:
        return await self._llm_caller(
            system_prompt=self._system_prompt,
            user_prompt=user_prompt,
        )

    async def evaluate(
        self,
        evidence: str,
        dimensions: List[str],
        rubric: str,
        round_number: int = 0,
    ) -> TribunalOpinion:
        """独立评估：基于证据输出评分卡和叙事。 / Independent evaluation: output scorecard and narrative based on evidence."""
        prompt = (
            f"You are a {self.role} with expertise in {self.expertise}.\n"
            f"Your evaluation perspective: {self.perspective}\n\n"
            f"## Evidence from simulation\n{evidence}\n\n"
            f"## Scoring rubric\n{rubric}\n\n"
            f"## Dimensions to evaluate\n{', '.join(dimensions)}\n\n"
            "Respond with JSON: {\"scores\": {dimension: 1-5}, \"narrative\": \"your analysis\"}"
        )
        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(prompt)
                data = parse_json_from_llm(raw)
                scores = {k: int(v) for k, v in data.get("scores", {}).items()}
                return TribunalOpinion(
                    member_role=self.role,
                    scores=scores,
                    narrative=data.get("narrative", ""),
                    round_number=round_number,
                )
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(f"TribunalAgent {self.role} evaluate attempt {attempt + 1} failed: {e}")

        logger.error(f"TribunalAgent {self.role} evaluate failed after retries: {last_error}")
        return TribunalOpinion(
            member_role=self.role,
            scores={d: 3 for d in dimensions},
            narrative=f"Evaluation failed: {last_error}",
            round_number=round_number,
        )

    async def challenge(
        self,
        other_opinion: TribunalOpinion,
    ) -> str:
        """质疑其他评审员的观点。 / Challenge another tribunal member's opinion."""
        prompt = (
            f"You are a {self.role}. Your perspective: {self.perspective}\n\n"
            f"Another evaluator ({other_opinion.member_role}) gave this assessment:\n"
            f"Scores: {json.dumps(other_opinion.scores)}\n"
            f"Narrative: {other_opinion.narrative}\n\n"
            "Respond with JSON: {\"challenge\": \"your specific challenge to their assessment\"}"
        )
        try:
            raw = await self._call_llm(prompt)
            data = parse_json_from_llm(raw)
            return data.get("challenge", raw)
        except (json.JSONDecodeError, ValueError):
            return raw if isinstance(raw, str) else ""

    async def revise(
        self,
        original_opinion: TribunalOpinion,
        challenges: List[str],
        round_number: int,
    ) -> TribunalOpinion:
        """基于质疑修正立场。 / Revise position based on challenges received."""
        challenges_text = "\n".join(f"- {c}" for c in challenges)
        prompt = (
            f"You are a {self.role}. Your perspective: {self.perspective}\n\n"
            f"Your previous assessment (round {original_opinion.round_number}):\n"
            f"Scores: {json.dumps(original_opinion.scores)}\n"
            f"Narrative: {original_opinion.narrative}\n\n"
            f"Challenges received:\n{challenges_text}\n\n"
            "Revise your assessment. You may keep, raise, or lower scores.\n"
            "Respond with JSON: {\"scores\": {dimension: 1-5}, \"narrative\": \"revised analysis\"}"
        )
        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(prompt)
                data = parse_json_from_llm(raw)
                scores = {k: int(v) for k, v in data.get("scores", {}).items()}
                return TribunalOpinion(
                    member_role=self.role,
                    scores=scores,
                    narrative=data.get("narrative", ""),
                    round_number=round_number,
                )
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(f"TribunalAgent {self.role} revise attempt {attempt + 1} failed: {e}")

        return TribunalOpinion(
            member_role=self.role,
            scores=dict(original_opinion.scores),
            narrative=f"Revision failed: {last_error}. Keeping original.",
            round_number=round_number,
        )
