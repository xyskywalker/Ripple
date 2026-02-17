"""全视者 Agent —— Ripple 核心决策中心。 / Omniscient Agent — Ripple's core decision center.

全视者是系统中唯一拥有完整上下文的 Agent，负责：
The only agent with full context, responsible for:
1. 场景初始化（INIT） / Scene initialization (INIT)
2. 涟漪传播裁决（RIPPLE） / Ripple propagation verdict (RIPPLE)
3. 涌现检测与相变判断（OBSERVE） / Emergence detection & phase transition (OBSERVE)
4. 结果合成 / Result synthesis
"""

import json
import logging
from typing import Any, Callable, Awaitable, Dict, List, Optional

from ripple.primitives.models import (
    OmniscientVerdict, AgentActivation, AgentSkip,
    PhaseVector, Ripple,
)
from ripple.prompts import (
    RETRY_JSON_PREFIX,
    RETRY_JSON_PREFIX_SHORT,
    OMNISCIENT_INIT_DYNAMICS,
    OMNISCIENT_INIT_DYNAMICS_HORIZON_LINE,
    OMNISCIENT_INIT_AGENTS,
    OMNISCIENT_INIT_TOPOLOGY,
    OMNISCIENT_RIPPLE_TIME_PROGRESS,
    OMNISCIENT_RIPPLE_CAS_PRINCIPLES,
    OMNISCIENT_RIPPLE_WAVE0_HINT,
    OMNISCIENT_RIPPLE_VERDICT,
    OMNISCIENT_OBSERVE,
    OMNISCIENT_SYNTHESIZE_RELATIVE,
    OMNISCIENT_SYNTHESIZE_ANCHORED,
)

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """从 LLM JSON 输出中安全提取浮点数。 / Safely extract float from LLM JSON output."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    if isinstance(value, dict):
        for key in ("value", "score", "energy"):
            if key in value and isinstance(value[key], (int, float)):
                return float(value[key])
    return default


# 全视者 INIT 输出必须包含的字段 / Required fields in Omniscient INIT output
INIT_REQUIRED_FIELDS = {
    "star_configs", "sea_configs", "topology",
    "dynamic_parameters", "seed_ripple",
}

# OBSERVE 输出必须包含的字段 / Required fields in OBSERVE output
OBSERVE_REQUIRED_FIELDS = {
    "phase_vector", "phase_transition_detected",
    "emergence_events", "topology_recommendations",
}

# synthesize_result 输出必须包含的字段 / Required fields in synthesize_result output
SYNTH_REQUIRED_FIELDS = {
    "prediction", "timeline", "bifurcation_points", "agent_insights",
}


class OmniscientAgent:
    """全视者 Agent，Ripple 的全知裁决者。 / Omniscient Agent, Ripple's all-knowing arbiter."""

    def __init__(
        self,
        llm_caller: Callable[..., Awaitable[str]],
        system_prompt: str = "",
        max_retries: int = 2,
    ):
        self._llm_caller = llm_caller
        self._system_prompt = system_prompt
        self._max_retries = max_retries
        self._init_result: Optional[Dict[str, Any]] = None

    async def _call_llm(self, user_prompt: str, phase: str = "") -> str:
        """调用 LLM，由引擎注入的 caller 处理实际路由。 / Call LLM; routing handled by injected caller."""
        if phase:
            logger.info(f"Omniscient 调用 LLM: {phase}")
        return await self._llm_caller(
            system_prompt=self._system_prompt,
            user_prompt=user_prompt,
        )

    def _parse_json(self, raw: str) -> Dict[str, Any]:
        """从 LLM 输出中提取 JSON。支持 markdown code block 包裹。 / Extract JSON from LLM output; supports markdown code blocks."""
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
        return json.loads(text)

    # =========================================================================
    # Phase INIT
    # =========================================================================

    async def init(
        self,
        skill_profile: str,
        simulation_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Phase INIT: 初始化模拟场景（3 次聚焦 LLM 调用）。 / Initialize simulation scene (3 focused LLM calls).

        Sub-call 1: 场景分析 + 时间参数 / Scene analysis + time params (dynamic_parameters)
        Sub-call 2: Agent 配置 / Agent configs (star_configs, sea_configs)
        Sub-call 3: 拓扑 + 种子 / Topology + seed (topology, seed_ripple)

        Args:
            skill_profile: Skill 自然语言画像 / Skill natural language profile
            simulation_input: 模拟请求 / Simulation request (event, source, historical, etc.)

        Returns:
            初始化结果 / Init result with star_configs, sea_configs, topology,
            dynamic_parameters, seed_ripple
        """
        # Sub-call 1: 场景分析 + 时间参数 / Scene analysis + time params
        dynamic_parameters = await self._init_sub_call(
            self._build_init_dynamics_prompt(skill_profile, simulation_input),
            phase="INIT:dynamics",
            required_fields={"wave_time_window"},
            error_label="INIT:dynamics",
        )

        # Sub-call 2: Agent 配置 / Agent configs
        agents_result = await self._init_sub_call(
            self._build_init_agents_prompt(
                skill_profile, simulation_input, dynamic_parameters,
            ),
            phase="INIT:agents",
            required_fields={"star_configs", "sea_configs"},
            error_label="INIT:agents",
        )

        # Sub-call 3: 拓扑 + 种子 / Topology + seed
        topology_result = await self._init_sub_call(
            self._build_init_topology_prompt(
                skill_profile, simulation_input, dynamic_parameters,
                agents_result,
            ),
            phase="INIT:topology",
            required_fields={"topology", "seed_ripple"},
            error_label="INIT:topology",
        )

        # 合并为统一结果 / Merge into unified result
        result = {
            "dynamic_parameters": dynamic_parameters,
            "star_configs": agents_result["star_configs"],
            "sea_configs": agents_result["sea_configs"],
            "topology": topology_result["topology"],
            "seed_ripple": topology_result["seed_ripple"],
        }

        self._validate_init_result(result)
        self._init_result = result
        return result

    async def _init_sub_call(
        self,
        user_prompt: str,
        phase: str,
        required_fields: set,
        error_label: str,
    ) -> Dict[str, Any]:
        """执行单次 INIT sub-call，带重试。 / Execute single INIT sub-call with retries."""
        last_error = None
        prompt = user_prompt
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(prompt, phase=phase)
                result = self._parse_json(raw)
                missing = required_fields - set(result.keys())
                if missing:
                    raise ValueError(f"{error_label} 输出缺少必要字段: {missing}")
                return result
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    f"全视者 {error_label} 第 {attempt + 1} 次尝试失败: {e}"
                )
                if attempt < self._max_retries:
                    prompt = (
                        RETRY_JSON_PREFIX.format(error=e)
                        + user_prompt
                    )

        raise RuntimeError(
            f"全视者 {error_label} 在 {1 + self._max_retries} 次尝试后仍然失败: "
            f"{last_error}"
        )

    def _build_init_dynamics_prompt(
        self,
        skill_profile: str,
        simulation_input: Dict[str, Any],
    ) -> str:
        """Sub-call 1: 场景分析 + 时间参数。 / Scene analysis + time params."""
        input_json = json.dumps(simulation_input, ensure_ascii=False, indent=2)
        horizon = simulation_input.get("simulation_horizon", "")
        horizon_line = (
            OMNISCIENT_INIT_DYNAMICS_HORIZON_LINE.format(horizon=horizon)
            if horizon else ""
        )
        return OMNISCIENT_INIT_DYNAMICS.format(
            skill_profile=skill_profile,
            input_json=input_json,
            horizon_line=horizon_line,
        )

    def _build_init_agents_prompt(
        self,
        skill_profile: str,
        simulation_input: Dict[str, Any],
        dynamic_parameters: Dict[str, Any],
    ) -> str:
        """Sub-call 2: Agent 配置。 / Agent configs."""
        input_json = json.dumps(simulation_input, ensure_ascii=False, indent=2)
        dp_json = json.dumps(dynamic_parameters, ensure_ascii=False, indent=2)
        return OMNISCIENT_INIT_AGENTS.format(
            skill_profile=skill_profile,
            input_json=input_json,
            dp_json=dp_json,
        )

    def _build_init_topology_prompt(
        self,
        skill_profile: str,
        simulation_input: Dict[str, Any],
        dynamic_parameters: Dict[str, Any],
        agents_result: Dict[str, Any],
    ) -> str:
        """Sub-call 3: 拓扑 + 种子。 / Topology + seed."""
        input_json = json.dumps(simulation_input, ensure_ascii=False, indent=2)
        dp_json = json.dumps(dynamic_parameters, ensure_ascii=False, indent=2)
        agents_json = json.dumps(
            {
                "star_configs": agents_result["star_configs"],
                "sea_configs": agents_result["sea_configs"],
            },
            ensure_ascii=False, indent=2,
        )
        return OMNISCIENT_INIT_TOPOLOGY.format(
            skill_profile=skill_profile,
            input_json=input_json,
            dp_json=dp_json,
            agents_json=agents_json,
        )

    def _validate_init_result(self, result: Dict[str, Any]) -> None:
        """校验 INIT 输出的必要字段。 / Validate required fields in INIT output."""
        missing = INIT_REQUIRED_FIELDS - set(result.keys())
        if missing:
            raise ValueError(f"INIT 输出缺少必要字段: {missing}")
        if not result.get("star_configs"):
            raise ValueError("star_configs 不能为空")
        if not result.get("sea_configs"):
            raise ValueError("sea_configs 不能为空")

    # =========================================================================
    # Phase RIPPLE
    # =========================================================================

    async def ripple_verdict(
        self,
        field_snapshot: Dict[str, Any],
        wave_number: int,
        propagation_history: str,
        wave_time_window: str = "",
        simulation_horizon: str = "",
    ) -> OmniscientVerdict:
        """Phase RIPPLE: 每轮波纹的传播裁决。 / Propagation verdict for each wave.

        Args:
            field_snapshot: 当前 Field 快照 / Current Field snapshot (agents, ripples, topology)
            wave_number: 当前 wave 序号 / Current wave number
            propagation_history: 传播历史摘要 / Propagation history summary
            wave_time_window: 每轮 wave 对应的现实时间 / Real time per wave (e.g. "4h")
            simulation_horizon: 模拟总时长 / Total simulation horizon (e.g. "48h")

        Returns:
            OmniscientVerdict 含激活列表和 continue 决定 / OmniscientVerdict with activation list and continue decision
        """
        user_prompt = self._build_ripple_prompt(
            field_snapshot, wave_number, propagation_history,
            wave_time_window=wave_time_window,
            simulation_horizon=simulation_horizon,
        )

        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(
                    user_prompt, phase=f"RIPPLE verdict (wave {wave_number})",
                )
                data = self._parse_json(raw)
                return self._parse_verdict(data)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    f"全视者 RIPPLE 裁决第 {attempt + 1} 次尝试失败: {e}"
                )
                if attempt < self._max_retries:
                    user_prompt = (
                        RETRY_JSON_PREFIX_SHORT.format(error=e)
                        + user_prompt
                    )

        # 安全降级：终止传播 / Safe fallback: stop propagation
        logger.error(f"全视者 RIPPLE 裁决失败，安全降级为终止传播: {last_error}")
        return OmniscientVerdict(
            wave_number=wave_number,
            simulated_time_elapsed="unknown",
            simulated_time_remaining="0h",
            continue_propagation=False,
            termination_reason=f"全视者裁决失败: {last_error}",
            activated_agents=[],
            skipped_agents=[],
            global_observation="裁决失败，安全终止",
        )

    def _build_ripple_prompt(
        self,
        field_snapshot: Dict[str, Any],
        wave_number: int,
        propagation_history: str,
        wave_time_window: str = "",
        simulation_horizon: str = "",
    ) -> str:
        snapshot_json = json.dumps(
            field_snapshot, ensure_ascii=False, indent=2, default=str,
        )

        # 显式列出可用 Agent 及其激活统计 / Explicitly list available agents with activation stats
        agent_lines = []
        for sid, info in field_snapshot.get("stars", {}).items():
            desc = info.get('description', '')
            act_count = info.get('activation_count', 0)
            last_e = info.get('last_energy', 0.0)
            last_resp = info.get('last_response')
            if act_count > 0:
                agent_lines.append(
                    f"  - agent_id: \"{sid}\" (Star/KOL): {desc} "
                    f"| 已激活{act_count}次, 上次能量={last_e:.2f}, "
                    f"上次响应={last_resp}"
                )
            else:
                agent_lines.append(
                    f"  - agent_id: \"{sid}\" (Star/KOL): {desc} | 尚未激活"
                )
        for sid, info in field_snapshot.get("seas", {}).items():
            desc = info.get('description', '')
            act_count = info.get('activation_count', 0)
            last_e = info.get('last_energy', 0.0)
            last_resp = info.get('last_response')
            if act_count > 0:
                agent_lines.append(
                    f"  - agent_id: \"{sid}\" (Sea/群体): {desc} "
                    f"| 已激活{act_count}次, 上次能量={last_e:.2f}, "
                    f"上次响应={last_resp}"
                )
            else:
                agent_lines.append(
                    f"  - agent_id: \"{sid}\" (Sea/群体): {desc} | 尚未激活"
                )
        agent_list = "\n".join(agent_lines) if agent_lines else "  （无可用 Agent）"

        # 构建时间进度段 / Build time progress section
        time_progress = ""
        if wave_time_window and simulation_horizon:
            from ripple.engine.runtime import _parse_hours
            wtw_h = _parse_hours(wave_time_window)
            horizon_h = _parse_hours(simulation_horizon)
            if wtw_h > 0 and horizon_h > 0:
                elapsed_h = wave_number * wtw_h
                remaining_h = max(0, horizon_h - elapsed_h)
                time_progress = OMNISCIENT_RIPPLE_TIME_PROGRESS.format(
                    wave_time_window=wave_time_window,
                    elapsed_h=elapsed_h,
                    wave_number=wave_number,
                    simulation_horizon=simulation_horizon,
                    remaining_h=remaining_h,
                )

        # Wave 0: 注入首轮 Sea 优先提示 / Wave 0: inject first-wave hint for Sea priority
        wave0_hint = OMNISCIENT_RIPPLE_WAVE0_HINT if wave_number == 0 else ""

        return OMNISCIENT_RIPPLE_VERDICT.format(
            wave_number=wave_number,
            time_progress=time_progress,
            cas_principles=OMNISCIENT_RIPPLE_CAS_PRINCIPLES + wave0_hint,
            snapshot_json=snapshot_json,
            propagation_history=propagation_history,
            agent_list=agent_list,
        )

    def _parse_verdict(self, data: Dict[str, Any]) -> OmniscientVerdict:
        """将 LLM JSON 输出解析为 OmniscientVerdict。 / Parse LLM JSON output into OmniscientVerdict."""
        activated = [
            AgentActivation(
                agent_id=a["agent_id"],
                incoming_ripple_energy=_safe_float(a.get("incoming_ripple_energy", 0.5)),
                activation_reason=a["activation_reason"],
            )
            for a in data.get("activated_agents", [])
        ]
        skipped = [
            AgentSkip(
                agent_id=s["agent_id"],
                skip_reason=s["skip_reason"],
            )
            for s in data.get("skipped_agents", [])
        ]
        return OmniscientVerdict(
            wave_number=data.get("wave_number", 0),
            simulated_time_elapsed=data.get("simulated_time_elapsed", ""),
            simulated_time_remaining=data.get("simulated_time_remaining", ""),
            continue_propagation=bool(data.get("continue_propagation", False)),
            termination_reason=data.get("termination_reason"),
            activated_agents=activated,
            skipped_agents=skipped,
            global_observation=data.get("global_observation", ""),
        )

    # =========================================================================
    # Phase OBSERVE
    # =========================================================================

    async def observe(
        self,
        field_snapshot: Dict[str, Any],
        full_history: str,
    ) -> Dict[str, Any]:
        """Phase OBSERVE: 涌现检测与相变判断。 / Emergence detection & phase transition.

        Args:
            field_snapshot: 当前 Field 快照 / Current Field snapshot
            full_history: 完整传播历史 / Full propagation history

        Returns:
            观测结果 / Observation with phase_vector, phase_transition_detected,
            emergence_events, topology_recommendations
        """
        user_prompt = self._build_observe_prompt(field_snapshot, full_history)

        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(user_prompt, phase="OBSERVE")
                result = self._parse_json(raw)
                self._validate_observe_result(result)
                return result
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    f"全视者 OBSERVE 第 {attempt + 1} 次尝试失败: {e}"
                )
                if attempt < self._max_retries:
                    user_prompt = (
                        RETRY_JSON_PREFIX_SHORT.format(error=e)
                        + user_prompt
                    )

        # 安全降级：返回默认观测 / Safe fallback: return default observation
        logger.error(f"全视者 OBSERVE 失败，返回默认观测: {last_error}")
        return {
            "phase_vector": {
                "heat": "unknown", "sentiment": "unknown",
                "coherence": "unknown",
            },
            "phase_transition_detected": False,
            "emergence_events": [],
            "topology_recommendations": [],
        }

    def _build_observe_prompt(
        self,
        field_snapshot: Dict[str, Any],
        full_history: str,
    ) -> str:
        snapshot_json = json.dumps(
            field_snapshot, ensure_ascii=False, indent=2, default=str,
        )
        return OMNISCIENT_OBSERVE.format(
            snapshot_json=snapshot_json,
            full_history=full_history,
        )

    def _validate_observe_result(self, result: Dict[str, Any]) -> None:
        missing = OBSERVE_REQUIRED_FIELDS - set(result.keys())
        if missing:
            raise ValueError(f"OBSERVE 输出缺少必要字段: {missing}")

    # =========================================================================
    # 结果合成 / Result Synthesis
    # =========================================================================

    async def synthesize_result(
        self,
        field_snapshot: Dict[str, Any],
        observation: Dict[str, Any],
        simulation_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        """合成最终预测结果。 / Synthesize final prediction result.

        Args:
            field_snapshot: 最终 Field 快照 / Final Field snapshot
            observation: OBSERVE 阶段的输出 / OBSERVE phase output
            simulation_input: 原始模拟请求 / Original simulation request

        Returns:
            预测结果 / Prediction with prediction, timeline, bifurcation_points,
            agent_insights
        """
        user_prompt = self._build_synth_prompt(
            field_snapshot, observation, simulation_input,
        )

        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(user_prompt, phase="SYNTHESIZE")
                result = self._parse_json(raw)
                self._validate_synth_result(result)
                return result
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    f"全视者结果合成第 {attempt + 1} 次尝试失败: {e}"
                )
                if attempt < self._max_retries:
                    user_prompt = (
                        RETRY_JSON_PREFIX_SHORT.format(error=e)
                        + user_prompt
                    )

        logger.error(f"全视者结果合成失败: {last_error}")
        return {
            "prediction": {"error": str(last_error)},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
        }

    def _build_synth_prompt(
        self,
        field_snapshot: Dict[str, Any],
        observation: Dict[str, Any],
        simulation_input: Dict[str, Any],
    ) -> str:
        snapshot_json = json.dumps(
            field_snapshot, ensure_ascii=False, indent=2, default=str,
        )
        obs_json = json.dumps(
            observation, ensure_ascii=False, indent=2, default=str,
        )
        input_json = json.dumps(
            simulation_input, ensure_ascii=False, indent=2, default=str,
        )

        has_historical = bool(simulation_input.get("historical"))
        template = (
            OMNISCIENT_SYNTHESIZE_ANCHORED if has_historical
            else OMNISCIENT_SYNTHESIZE_RELATIVE
        )

        return template.format(
            snapshot_json=snapshot_json,
            obs_json=obs_json,
            input_json=input_json,
        )

    def _validate_synth_result(self, result: Dict[str, Any]) -> None:
        missing = SYNTH_REQUIRED_FIELDS - set(result.keys())
        if missing:
            raise ValueError(f"结果合成输出缺少必要字段: {missing}")
