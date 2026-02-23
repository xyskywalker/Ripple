"""合议庭辩论编排器。 / Deliberation orchestration for tribunal-based PMF evaluation.

Implements the DELIBERATE phase: multi-round structured debate with
dual-gate convergence (threshold + round limit).
"""

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List

from ripple.agents.tribunal import TribunalAgent
from ripple.primitives.pmf_models import (
    DeliberationRecord,
    TribunalMember,
    TribunalOpinion,
)

logger = logging.getLogger(__name__)


class DeliberationOrchestrator:
    """合议庭辩论编排：多轮结构化辩论 + 双重闸门收敛。

    / Orchestrates multi-round structured deliberation with dual-gate convergence.

    Protocol:
        Round 0 — evaluate only (each member scores independently).
        Rounds 1..max_rounds-1 — challenge → revise cycle.

    Dual-gate convergence:
        Gate 1 (threshold): all dims change ≤1 for every member between rounds.
        Gate 2 (consecutive): threshold gate passes for 2 consecutive round transitions.
        If both gates satisfied, stop early and mark converged=True.
        Otherwise, stop at max_rounds with converged=False.
    """

    CONSECUTIVE_STABLE_REQUIRED = 2  # Number of consecutive stable transitions to converge

    def __init__(
        self,
        members: List[TribunalMember],
        llm_caller: Callable[..., Awaitable[str]],
        dimensions: List[str],
        rubric: str,
        max_rounds: int = 4,
        system_prompt: str = "",
    ):
        self.members = members
        self.dimensions = dimensions
        self.rubric = rubric
        self.max_rounds = max_rounds

        # Create TribunalAgent instances from member configs
        self._agents: List[TribunalAgent] = [
            TribunalAgent(
                role=m.role,
                perspective=m.perspective,
                expertise=m.expertise,
                llm_caller=llm_caller,
                system_prompt=system_prompt,
            )
            for m in members
        ]

    async def run(
        self,
        evidence_pack: Dict[str, Any],
    ) -> List[DeliberationRecord]:
        """Execute the full deliberation protocol.

        Args:
            evidence_pack: Evidence dictionary with summary and key_signals.

        Returns:
            List of DeliberationRecord, one per round executed.
        """
        evidence_str = json.dumps(evidence_pack, ensure_ascii=False, default=str)
        records: List[DeliberationRecord] = []
        previous_opinions: List[TribunalOpinion] = []
        consecutive_stable = 0

        for round_num in range(self.max_rounds):
            if round_num == 0:
                # Round 0: evaluate only
                opinions = await self._evaluate_all(evidence_str, round_num)
                record = DeliberationRecord(
                    round_number=round_num,
                    opinions=opinions,
                    challenges=[],
                    consensus_points=self._find_consensus(opinions),
                    dissent_points=self._find_dissent(opinions),
                    converged=False,
                )
                records.append(record)
                previous_opinions = opinions
            else:
                # Rounds 1+: challenge → revise
                challenges = await self._challenge_round(previous_opinions)
                opinions = await self._revise_all(
                    previous_opinions, challenges, round_num
                )

                # Check threshold convergence gate
                is_stable = self._check_threshold_convergence(
                    previous_opinions, opinions
                )
                if is_stable:
                    consecutive_stable += 1
                else:
                    consecutive_stable = 0

                converged = consecutive_stable >= self.CONSECUTIVE_STABLE_REQUIRED

                record = DeliberationRecord(
                    round_number=round_num,
                    opinions=opinions,
                    challenges=challenges,
                    consensus_points=self._find_consensus(opinions),
                    dissent_points=self._find_dissent(opinions),
                    converged=converged,
                )
                records.append(record)
                previous_opinions = opinions

                if converged:
                    logger.info(
                        f"Deliberation converged at round {round_num} "
                        f"after {consecutive_stable} consecutive stable transitions."
                    )
                    break

        return records

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    async def _evaluate_all(
        self, evidence: str, round_number: int
    ) -> List[TribunalOpinion]:
        """All agents evaluate independently."""
        opinions = []
        for agent in self._agents:
            opinion = await agent.evaluate(
                evidence=evidence,
                dimensions=self.dimensions,
                rubric=self.rubric,
                round_number=round_number,
            )
            opinions.append(opinion)
        return opinions

    async def _challenge_round(
        self, opinions: List[TribunalOpinion]
    ) -> List[Dict[str, Any]]:
        """Each member challenges ONE opponent (the one with max score gap).

        Returns a list of challenge dicts, one per member.
        """
        challenges: List[Dict[str, Any]] = []

        for i, agent in enumerate(self._agents):
            # Find opponent with max score gap
            target_idx = self._find_max_gap_opponent(i, opinions)
            challenge_text = await agent.challenge(opinions[target_idx])
            challenges.append({
                "challenger": agent.role,
                "target": opinions[target_idx].member_role,
                "challenge": challenge_text,
            })

        return challenges

    async def _revise_all(
        self,
        previous_opinions: List[TribunalOpinion],
        challenges: List[Dict[str, Any]],
        round_number: int,
    ) -> List[TribunalOpinion]:
        """All agents revise based on challenges received."""
        revised = []
        for i, agent in enumerate(self._agents):
            # Collect challenges targeted at this member
            received = [
                c["challenge"]
                for c in challenges
                if c["target"] == agent.role
            ]
            # If no challenges targeted at this member, include all challenges
            # as context so they still have opportunity to revise
            if not received:
                received = [c["challenge"] for c in challenges]

            opinion = await agent.revise(
                original_opinion=previous_opinions[i],
                challenges=received,
                round_number=round_number,
            )
            revised.append(opinion)
        return revised

    def _find_max_gap_opponent(
        self, member_idx: int, opinions: List[TribunalOpinion]
    ) -> int:
        """Find the opponent with the largest total score gap from this member."""
        my_scores = opinions[member_idx].scores
        max_gap = -1
        target = -1

        for j, other_opinion in enumerate(opinions):
            if j == member_idx:
                continue
            gap = sum(
                abs(my_scores.get(d, 0) - other_opinion.scores.get(d, 0))
                for d in self.dimensions
            )
            if gap > max_gap:
                max_gap = gap
                target = j

        return target

    def _check_threshold_convergence(
        self,
        prev_opinions: List[TribunalOpinion],
        curr_opinions: List[TribunalOpinion],
    ) -> bool:
        """Check if all dims changed ≤1 for every member between rounds."""
        for prev, curr in zip(prev_opinions, curr_opinions):
            for dim in self.dimensions:
                prev_score = prev.scores.get(dim, 0)
                curr_score = curr.scores.get(dim, 0)
                if abs(curr_score - prev_score) > 1:
                    return False
        return True

    def _find_consensus(self, opinions: List[TribunalOpinion]) -> List[str]:
        """Identify dimensions where all members agree (scores within ≤1)."""
        consensus = []
        for dim in self.dimensions:
            scores = [op.scores.get(dim, 0) for op in opinions]
            if scores and (max(scores) - min(scores)) <= 1:
                consensus.append(dim)
        return consensus

    def _find_dissent(self, opinions: List[TribunalOpinion]) -> List[str]:
        """Identify dimensions where members disagree (scores differ by >1)."""
        dissent = []
        for dim in self.dimensions:
            scores = [op.scores.get(dim, 0) for op in opinions]
            if scores and (max(scores) - min(scores)) > 1:
                dissent.append(dim)
        return dissent
