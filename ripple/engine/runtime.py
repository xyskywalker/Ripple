"""Ripple 引擎运行时。 / Ripple engine runtime.

职责 / Responsibilities:
1. 编排（Orchestration）—— 按 5-Phase 调用全视者和星海 Agent / Orchestrate 5-Phase calls to Omniscient & Star/Sea agents
2. 状态管理（State Management）—— 维护 Field、记录轨迹 / Maintain Field state & trace records
3. 安全防护（Safety Guards）—— 死循环检测、输出校验 / Deadloop detection & output validation

不负责：能量计算、激活判定、衰减公式、CAS 参数管理。
/ Not responsible for: energy calc, activation logic, decay formulas, CAS param management.
"""

import asyncio
import inspect
import json
import logging
import math
import re
import time
import uuid
from typing import Any, Callable, Awaitable, Dict, List, Optional, TYPE_CHECKING, Union

from ripple.primitives.events import SimulationEvent
from ripple.primitives.models import (
    AgentActivation, Field, Ripple, OmniscientVerdict, WaveRecord,
)
from ripple.agents.omniscient import OmniscientAgent
from ripple.agents.star import StarAgent
from ripple.agents.sea import SeaAgent

if TYPE_CHECKING:
    from ripple.engine.recorder import SimulationRecorder

logger = logging.getLogger(__name__)

# 类型别名：支持同步和异步回调 / Type alias: supports sync and async callbacks
ProgressCallback = Union[
    Callable[[SimulationEvent], Awaitable[None]],
    Callable[[SimulationEvent], None],
]

SAFETY_WAVE_MULTIPLIER = 3  # 安全上限 = estimated_total_waves * 此系数 / Safety cap = estimated_total_waves * this multiplier


def _extract_float(value: Any, default: float = 0.0) -> float:
    """从 LLM 输出中提取浮点数，容忍嵌套字典或异常类型。 / Extract float from LLM output; tolerates nested dicts or unusual types."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    if isinstance(value, dict):
        # LLM 有时会返回 {"value": 0.8, "reason": "..."} 之类的嵌套结构 / LLM sometimes returns nested structures like {"value": 0.8, ...}
        for key in ("value", "score", "energy", "initial_energy"):
            if key in value and isinstance(value[key], (int, float)):
                return float(value[key])
    return default


def _extract_int(value: Any, default: int = 0) -> int:
    """从 LLM 输出中提取整数，容忍嵌套字典或异常类型。 / Extract int from LLM output; tolerates nested dicts or unusual types."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    if isinstance(value, dict):
        for key in ("value", "count", "total", "estimated_total_waves"):
            if key in value and isinstance(value[key], (int, float)):
                return int(value[key])
    return default


def _parse_hours(s: str) -> float:
    """解析时间字符串为小时数。 / Parse a time string like "4h", "48h", "2.5h", "1d" into hours.

    无法解析时返回 0.0。 / Returns 0.0 if the string cannot be parsed.
    """
    if not s or not isinstance(s, str):
        return 0.0
    s = s.strip().lower()
    # 匹配 "4h", "2.5h", "48h" 格式 / Match patterns like "4h", "2.5h", "48h"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s)
    if m:
        return float(m.group(1))
    # 匹配 "1d", "2d" 格式 / Match patterns like "1d", "2d"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*d$", s)
    if m:
        return float(m.group(1)) * 24.0
    return 0.0


def _empty_agent_stats() -> Dict[str, Any]:
    """返回未被激活 Agent 的默认状态。 / Return default stats for an unactivated agent."""
    return {
        "activation_count": 0,
        "last_wave": None,
        "last_energy": 0.0,
        "last_response": None,
        "total_outgoing_energy": 0.0,
    }


class SimulationRuntime:
    """Ripple 模拟运行时编排器。 / Ripple simulation runtime orchestrator."""

    # 各阶段在总进度中的权重 / Phase weights in total progress (sum = 1.0)
    _PHASE_WEIGHTS = {
        "INIT": 0.05,
        "SEED": 0.05,
        "RIPPLE": 0.70,  # 占大头，内部按 wave 细分 / Largest share, subdivided by wave internally
        "OBSERVE": 0.10,
        "SYNTHESIZE": 0.10,
    }
    _PHASE_OFFSETS = {}  # 运行时计算 / Computed at runtime

    def __init__(
        self,
        omniscient_caller: Callable[..., Awaitable[str]],
        star_caller: Optional[Callable[..., Awaitable[str]]] = None,
        sea_caller: Optional[Callable[..., Awaitable[str]]] = None,
        skill_profile: str = "",
        on_progress: Optional[ProgressCallback] = None,
        # 增量记录器：模拟过程中动态写入 JSON 文件 / Incremental recorder: writes JSON dynamically during simulation
        recorder: Optional["SimulationRecorder"] = None,
        # 向后兼容：旧签名 agent_caller 同时用于 star 和 sea / Backward compat: legacy agent_caller used for both star and sea
        agent_caller: Optional[Callable[..., Awaitable[str]]] = None,
    ):
        self._omniscient = OmniscientAgent(llm_caller=omniscient_caller)
        # 兼容旧 API：如果传了 agent_caller，star/sea 未传则用它 / Compat: use agent_caller for star/sea if not provided
        if agent_caller is not None:
            self._star_caller = star_caller or agent_caller
            self._sea_caller = sea_caller or agent_caller
        elif star_caller is not None:
            self._star_caller = star_caller
            self._sea_caller = sea_caller if sea_caller is not None else star_caller
        else:
            raise TypeError(
                "SimulationRuntime 需要 star_caller/sea_caller 或 agent_caller"
            )
        self._skill_profile = skill_profile
        self._on_progress = on_progress
        self._recorder = recorder
        self._stars: Dict[str, StarAgent] = {}
        self._seas: Dict[str, SeaAgent] = {}
        self._wave_records: List[WaveRecord] = []
        self._seed_content: str = ""
        self._seed_energy: float = 0.0

        # 预计算阶段进度偏移量 / Pre-compute phase progress offsets
        offset = 0.0
        for phase in ("INIT", "SEED", "RIPPLE", "OBSERVE", "SYNTHESIZE"):
            self._PHASE_OFFSETS[phase] = offset
            offset += self._PHASE_WEIGHTS[phase]

    async def _emit(self, event: SimulationEvent) -> None:
        """触发进度回调（支持同步和异步回调）。 / Emit progress callback (sync and async)."""
        if self._on_progress is None:
            return
        result = self._on_progress(event)
        if inspect.isawaitable(result):
            await result

    def _progress(self, phase: str, phase_fraction: float = 0.0) -> float:
        """计算总进度值 (0.0 ~ 1.0)。 / Compute total progress (0.0 ~ 1.0).

        phase_fraction: 当前阶段内部的完成比例 / Completion ratio within current phase (0.0 ~ 1.0).
        """
        base = self._PHASE_OFFSETS.get(phase, 0.0)
        weight = self._PHASE_WEIGHTS.get(phase, 0.0)
        return min(1.0, base + weight * phase_fraction)

    async def run(
        self,
        simulation_input: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行完整模拟。 / Execute full simulation.

        Args:
            simulation_input: 模拟输入参数。 / Simulation input parameters.
            run_id: 可选的外部指定 run_id。若不传则自动生成。 / Optional external run_id; auto-generated if omitted.
        """
        run_id = run_id or str(uuid.uuid4())[:8]
        logger.info(f"[{run_id}] 开始模拟")

        # Phase 0: INIT
        await self._emit(SimulationEvent(
            type="phase_start", phase="INIT", run_id=run_id,
            progress=self._progress("INIT", 0.0),
        ))
        init_result = await self._omniscient.init(
            skill_profile=self._skill_profile,
            simulation_input=simulation_input,
        )
        self._create_agents(init_result)

        dp = init_result.get("dynamic_parameters", {})
        wave_time_window = dp.get("wave_time_window", "")
        if isinstance(wave_time_window, (int, float)):
            wave_time_window = f"{wave_time_window}h"
        horizon_str = simulation_input.get("simulation_horizon", "")

        # 确定性 wave 计算 / Deterministic wave calculation
        horizon_hours = _parse_hours(horizon_str)
        window_hours = _parse_hours(wave_time_window)

        if horizon_hours > 0 and window_hours > 0:
            estimated_waves = math.ceil(horizon_hours / window_hours)
            logger.info(
                f"[{run_id}] 确定性 wave 计算: "
                f"ceil({horizon_hours}h / {window_hours}h) = {estimated_waves}"
            )
        else:
            estimated_waves = _extract_int(
                dp.get("estimated_total_waves", 10), 10,
            )
            logger.info(
                f"[{run_id}] 回退到 LLM 估计: "
                f"estimated_total_waves = {estimated_waves}"
            )

        max_waves = estimated_waves * SAFETY_WAVE_MULTIPLIER

        # 存储以供快照和裁决调用使用 / Store for use in snapshot and verdict calls
        self._wave_time_window = wave_time_window
        self._simulation_horizon = horizon_str
        self._energy_decay_per_wave = _extract_float(
            dp.get("energy_decay_per_wave", 0.15), 0.15
        )

        logger.info(
            f"[{run_id}] INIT 完成: "
            f"Star×{len(init_result.get('star_configs', []))}, "
            f"Sea×{len(init_result.get('sea_configs', []))}, "
            f"预估 {estimated_waves} waves (安全上限 {max_waves})"
        )

        # 增量记录：INIT 阶段结果 / Incremental record: INIT phase result
        if self._recorder:
            self._recorder.record_init(
                init_result, estimated_waves, max_waves,
            )

        await self._emit(SimulationEvent(
            type="phase_end", phase="INIT", run_id=run_id,
            progress=self._progress("INIT", 1.0),
            total_waves=estimated_waves,
            detail={
                "star_count": len(init_result.get("star_configs", [])),
                "sea_count": len(init_result.get("sea_configs", [])),
                "estimated_waves": estimated_waves,
                "max_waves": max_waves,
            },
        ))

        # Phase 1: SEED
        logger.info(f"[{run_id}] ━━━ SEED 阶段 ━━━")
        await self._emit(SimulationEvent(
            type="phase_start", phase="SEED", run_id=run_id,
            progress=self._progress("SEED", 0.0),
            total_waves=estimated_waves,
        ))
        seed = init_result.get("seed_ripple", {})
        seed_content = seed.get("content", "")
        if not isinstance(seed_content, str):
            seed_content = str(seed_content)
        seed_energy = _extract_float(seed.get("initial_energy", 0.5), 0.5)
        seed_ripple = Ripple(
            id=f"ripple_{run_id}_seed",
            content=seed_content,
            content_embedding=[],
            energy=seed_energy,
            origin_agent="omniscient",
            ripple_type="seed",
            emotion={},
            trace=["omniscient"],
            tick_born=0,
            mutations=[],
            root_id=f"ripple_{run_id}_seed",
        )
        self._seed_content = seed_content
        self._seed_energy = seed_energy

        # 增量记录：SEED 阶段结果 / Incremental record: SEED phase result
        if self._recorder:
            self._recorder.record_seed(seed_content, seed_energy)

        await self._emit(SimulationEvent(
            type="phase_end", phase="SEED", run_id=run_id,
            progress=self._progress("SEED", 1.0),
            total_waves=estimated_waves,
            detail={
                "seed_content": seed_content[:200],
                "seed_energy": seed_energy,
            },
        ))

        # Phase 2: RIPPLE (统一涟漪循环) / Unified ripple loop
        wave_count = 0
        content_preview = seed_ripple.content[:50] if seed_ripple.content else ""
        history_lines = [f"种子涟漪已注入: '{content_preview}', "
                         f"能量={seed_ripple.energy}"]

        await self._emit(SimulationEvent(
            type="phase_start", phase="RIPPLE", run_id=run_id,
            progress=self._progress("RIPPLE", 0.0),
            wave=0, total_waves=estimated_waves,
        ))

        while wave_count < max_waves:
            wave_frac = wave_count / max(estimated_waves, 1)
            logger.info(
                f"[{run_id}] ━━━ Wave {wave_count + 1}/{estimated_waves} ━━━"
            )
            await self._emit(SimulationEvent(
                type="wave_start", phase="RIPPLE", run_id=run_id,
                progress=self._progress("RIPPLE", wave_frac),
                wave=wave_count, total_waves=estimated_waves,
            ))

            # 增量记录：wave 启动前的场快照 / Incremental record: field snapshot before wave starts
            pre_snapshot = self._build_snapshot()
            if self._recorder:
                self._recorder.record_wave_start(wave_count, pre_snapshot)

            verdict = await self._omniscient.ripple_verdict(
                field_snapshot=pre_snapshot,
                wave_number=wave_count,
                propagation_history=self._build_history_with_window(
                    history_lines[0],
                ),
                wave_time_window=wave_time_window,
                simulation_horizon=horizon_str,
            )

            if not verdict.continue_propagation:
                logger.info(
                    f"[{run_id}] 传播终止于 wave {wave_count}: "
                    f"{verdict.termination_reason or '全视者判定终止'}"
                )
                # 增量记录：wave 终止（传播结束） / Incremental record: wave terminated (propagation ends)
                if self._recorder:
                    self._recorder.record_wave_end(
                        wave_number=wave_count,
                        verdict=verdict,
                        agent_responses={},
                        post_snapshot=self._build_snapshot(),
                        terminated=True,
                    )
                await self._emit(SimulationEvent(
                    type="wave_end", phase="RIPPLE", run_id=run_id,
                    progress=self._progress("RIPPLE", wave_frac),
                    wave=wave_count, total_waves=estimated_waves,
                    detail={"terminated": True,
                            "reason": verdict.termination_reason
                            or "全视者判定终止"},
                ))
                break

            # Wave 0 Sea 保护: CAS 中种子扰动必须到达至少一个群体 Agent
            # / Wave 0 Sea guard: in CAS, seed perturbation must reach at least one group (Sea) agent
            if wave_count == 0:
                has_sea = any(
                    a.agent_id in self._seas
                    for a in verdict.activated_agents
                )
                if not has_sea and self._seas:
                    first_sea_id = next(iter(self._seas))
                    verdict.activated_agents.append(
                        AgentActivation(
                            agent_id=first_sea_id,
                            incoming_ripple_energy=self._seed_energy * 0.3,
                            activation_reason=(
                                "CAS guard: seed perturbation must reach "
                                "at least one group agent"
                            ),
                        )
                    )
                    logger.warning(
                        f"Wave 0 Sea guard: auto-injected {first_sea_id}"
                    )

            # 通知每个被激活的 Agent / Notify each activated agent
            for activation in verdict.activated_agents:
                aid = activation.agent_id
                atype = "sea" if aid in self._seas else "star"
                await self._emit(SimulationEvent(
                    type="agent_activated", phase="RIPPLE", run_id=run_id,
                    progress=self._progress("RIPPLE", wave_frac),
                    wave=wave_count, total_waves=estimated_waves,
                    agent_id=aid, agent_type=atype,
                    detail={"energy": activation.incoming_ripple_energy},
                ))

            # 并行激活被选中的 Agent / Activate selected agents in parallel
            responses = await self._activate_agents(
                verdict, ripple_content=seed_ripple.content,
            )

            # 通知每个 Agent 的响应 / Notify each agent's response
            for aid, resp in responses.items():
                atype = "sea" if aid in self._seas else "star"
                await self._emit(SimulationEvent(
                    type="agent_responded", phase="RIPPLE", run_id=run_id,
                    progress=self._progress("RIPPLE", wave_frac),
                    wave=wave_count, total_waves=estimated_waves,
                    agent_id=aid, agent_type=atype,
                    detail=resp,
                ))

            # 记录本轮 / Record this wave
            record = WaveRecord(
                wave_number=wave_count,
                verdict=verdict,
                agent_responses=responses,
                events=[],
            )
            self._wave_records.append(record)

            # 增量记录：wave 完成后的场快照和完整数据 / Incremental record: post-wave snapshot and full data
            if self._recorder:
                self._recorder.record_wave_end(
                    wave_number=wave_count,
                    verdict=verdict,
                    agent_responses=responses,
                    post_snapshot=self._build_snapshot(),
                )

            # 更新历史 / Update history
            for aid, resp in responses.items():
                history_lines.append(
                    f"Wave {wave_count}: {aid} → "
                    f"{resp.get('response_type', 'unknown')} "
                    f"(出能量={resp.get('outgoing_energy', 0.0):.2f})"
                )

            wave_count += 1
            await self._emit(SimulationEvent(
                type="wave_end", phase="RIPPLE", run_id=run_id,
                progress=self._progress("RIPPLE",
                                        wave_count / max(estimated_waves, 1)),
                wave=wave_count - 1, total_waves=estimated_waves,
                detail={"agent_count": len(responses)},
            ))
        else:
            logger.warning(
                f"[{run_id}] 达到安全上限 {max_waves} waves，强制终止"
            )

        effective_waves = wave_count

        await self._emit(SimulationEvent(
            type="phase_end", phase="RIPPLE", run_id=run_id,
            progress=self._progress("RIPPLE", 1.0),
            wave=effective_waves - 1, total_waves=estimated_waves,
            detail={"effective_waves": effective_waves},
        ))

        # Phase 3: OBSERVE
        logger.info(f"[{run_id}] ━━━ OBSERVE 阶段 ━━━")
        await self._emit(SimulationEvent(
            type="phase_start", phase="OBSERVE", run_id=run_id,
            progress=self._progress("OBSERVE", 0.0),
            total_waves=estimated_waves,
        ))
        observation = await self._omniscient.observe(
            field_snapshot=self._build_snapshot(),
            full_history="\n".join(history_lines),
        )

        # 增量记录：OBSERVE 阶段结果 / Incremental record: OBSERVE phase result
        if self._recorder:
            self._recorder.record_observation(observation)

        await self._emit(SimulationEvent(
            type="phase_end", phase="OBSERVE", run_id=run_id,
            progress=self._progress("OBSERVE", 1.0),
            total_waves=estimated_waves,
        ))

        # Phase 4: FEEDBACK & RECORD
        # 拓扑更新由全视者建议（如果有） / Topology update by Omniscient suggestion (if any)
        # ... 持久化逻辑 ... / ... persistence logic ...

        # 合成结果 / Synthesize result
        logger.info(f"[{run_id}] ━━━ SYNTHESIZE 阶段 ━━━")
        await self._emit(SimulationEvent(
            type="phase_start", phase="SYNTHESIZE", run_id=run_id,
            progress=self._progress("SYNTHESIZE", 0.0),
            total_waves=estimated_waves,
        ))
        result = await self._omniscient.synthesize_result(
            field_snapshot=self._build_snapshot(),
            observation=observation,
            simulation_input=simulation_input,
        )

        result["observation"] = observation
        result["total_waves"] = effective_waves
        result["run_id"] = run_id
        result["wave_records_count"] = len(self._wave_records)

        # 增量记录：SYNTHESIZE 阶段结果（合成数据写入顶层键以保持向后兼容） / Incremental record: SYNTHESIZE result (top-level keys for backward compat)
        if self._recorder:
            self._recorder.record_synthesis(result)

        logger.info(
            f"[{run_id}] 模拟完成: {effective_waves} waves, "
            f"LLM 调用链结束"
        )
        await self._emit(SimulationEvent(
            type="phase_end", phase="SYNTHESIZE", run_id=run_id,
            progress=1.0,
            total_waves=estimated_waves,
            detail={"total_waves": effective_waves},
        ))
        return result

    def _create_agents(self, init_result: Dict[str, Any]) -> None:
        """根据全视者 INIT 结果创建星海 Agent。 / Create Star/Sea agents from Omniscient INIT result."""
        for sc in init_result.get("star_configs", []):
            self._stars[sc["id"]] = StarAgent(
                agent_id=sc["id"],
                description=sc.get("description", ""),
                llm_caller=self._star_caller,
            )
        for sc in init_result.get("sea_configs", []):
            self._seas[sc["id"]] = SeaAgent(
                agent_id=sc["id"],
                description=sc.get("description", ""),
                llm_caller=self._sea_caller,
            )

    async def _activate_agents(
        self, verdict: OmniscientVerdict, ripple_content: str = "",
    ) -> Dict[str, Dict[str, Any]]:
        """并行激活被裁决选中的 Agent。 / Activate verdict-selected agents in parallel."""
        known_ids = set(self._stars.keys()) | set(self._seas.keys())
        if verdict.activated_agents:
            activated_ids = [a.agent_id for a in verdict.activated_agents]
            logger.info(
                f"本轮激活 {len(activated_ids)} 个 Agent: {activated_ids}"
            )
        else:
            logger.info(
                f"本轮未激活任何 Agent（已注册: {list(known_ids)}）"
            )

        tasks = {}
        for activation in verdict.activated_agents:
            aid = activation.agent_id
            agent = self._stars.get(aid) or self._seas.get(aid)
            if agent is None:
                logger.warning(
                    f"全视者激活了未知 Agent: {aid}（已注册: {list(known_ids)}）"
                )
                continue
            is_sea = aid in self._seas
            logger.info(
                f"激活 {'Sea' if is_sea else 'Star'} Agent: {aid}, "
                f"能量={activation.incoming_ripple_energy:.2f}"
            )
            tasks[aid] = agent.respond(
                ripple_content=ripple_content or self._seed_content,
                ripple_energy=activation.incoming_ripple_energy,
                ripple_source="omniscient_verdict",
            )

        results = {}
        if tasks:
            done = await asyncio.gather(
                *tasks.values(), return_exceptions=True,
            )
            for aid, result in zip(tasks.keys(), done):
                if isinstance(result, Exception):
                    logger.error(f"Agent {aid} 响应失败: {result}")
                    results[aid] = {"response_type": "error",
                                    "outgoing_energy": 0.0}
                else:
                    results[aid] = result

        return results

    def _build_snapshot(self) -> Dict[str, Any]:
        """构建当前 Field 快照供全视者参考。 / Build current Field snapshot for Omniscient reference."""
        agent_stats = self._extract_agent_stats()

        snapshot: Dict[str, Any] = {
            "seed_content": self._seed_content[:200] if self._seed_content else "",
            "seed_energy": self._seed_energy,
            "stars": {
                sid: {
                    "description": s.description,
                    "memory_count": len(s.memory),
                    **agent_stats.get(sid, _empty_agent_stats()),
                }
                for sid, s in self._stars.items()
            },
            "seas": {
                sid: {
                    "description": s.description,
                    "memory_count": len(s.memory),
                    **agent_stats.get(sid, _empty_agent_stats()),
                }
                for sid, s in self._seas.items()
            },
            "wave_records_count": len(self._wave_records),
        }
        if getattr(self, "_wave_time_window", ""):
            snapshot["wave_time_window"] = self._wave_time_window
        if getattr(self, "_simulation_horizon", ""):
            snapshot["simulation_horizon"] = self._simulation_horizon
        if hasattr(self, "_energy_decay_per_wave"):
            snapshot["energy_decay_per_wave"] = self._energy_decay_per_wave
        return snapshot

    def _extract_agent_stats(self) -> Dict[str, Dict[str, Any]]:
        """从 wave_records 中提取每个 Agent 的累积状态。 / Extract cumulative stats per agent from wave_records."""
        stats: Dict[str, Dict[str, Any]] = {}
        for record in self._wave_records:
            for activation in record.verdict.activated_agents:
                aid = activation.agent_id
                if aid not in stats:
                    stats[aid] = {
                        "activation_count": 0,
                        "last_wave": None,
                        "last_energy": 0.0,
                        "last_response": None,
                        "total_outgoing_energy": 0.0,
                    }
                s = stats[aid]
                s["activation_count"] += 1
                s["last_wave"] = record.wave_number
                s["last_energy"] = activation.incoming_ripple_energy
                resp = record.agent_responses.get(aid, {})
                s["last_response"] = resp.get("response_type")
                s["total_outgoing_energy"] += resp.get("outgoing_energy", 0.0)
        return stats

    def _build_history_with_window(
        self, seed_line: str, window_size: int = 5,
    ) -> str:
        """构建带滑动窗口的传播历史。 / Build propagation history with sliding window.

        最近 window_size 轮保留详细记录（含能量），更早的轮次压缩为摘要。
        / Recent window_size waves keep detailed records; older waves compressed to summary.
        """
        lines = [seed_line]

        if not self._wave_records:
            return "\n".join(lines)

        # 压缩摘要：超出窗口的旧记录 / Compressed summary: old records beyond window
        cutoff = len(self._wave_records) - window_size
        if cutoff > 0:
            old_records = self._wave_records[:cutoff]
            summary = self._compress_history(old_records)
            lines.append(summary)

        # 详细记录：最近 window_size 轮 / Detailed records: last window_size waves
        recent_records = self._wave_records[max(0, cutoff):]
        # 计算每个 Agent 截止到详细窗口起始时的激活次数 / Count activations per agent before detail window
        counts_before: Dict[str, int] = {}
        if cutoff > 0:
            for record in self._wave_records[:cutoff]:
                for act in record.verdict.activated_agents:
                    counts_before[act.agent_id] = (
                        counts_before.get(act.agent_id, 0) + 1
                    )

        running_counts = dict(counts_before)
        for record in recent_records:
            for act in record.verdict.activated_agents:
                aid = act.agent_id
                running_counts[aid] = running_counts.get(aid, 0) + 1
                resp = record.agent_responses.get(aid, {})
                out_e = resp.get("outgoing_energy", 0.0)
                rtype = resp.get("response_type", "unknown")
                lines.append(
                    f"Wave {record.wave_number}: {aid} → {rtype} "
                    f"(入能量={act.incoming_ripple_energy:.2f}, "
                    f"出能量={out_e:.2f}) "
                    f"[第{running_counts[aid]}次激活]"
                )

        return "\n".join(lines)

    @staticmethod
    def _compress_history(records: List[WaveRecord]) -> str:
        """将多轮 wave 记录压缩为摘要行。 / Compress multiple wave records into a summary line."""
        first_wave = records[0].wave_number
        last_wave = records[-1].wave_number
        agent_counts: Dict[str, int] = {}
        response_counts: Dict[str, int] = {}
        total_out_energy = 0.0

        for record in records:
            for act in record.verdict.activated_agents:
                aid = act.agent_id
                agent_counts[aid] = agent_counts.get(aid, 0) + 1
                resp = record.agent_responses.get(aid, {})
                rtype = resp.get("response_type", "unknown")
                response_counts[rtype] = response_counts.get(rtype, 0) + 1
                total_out_energy += resp.get("outgoing_energy", 0.0)

        agent_parts = [f"{aid}×{cnt}" for aid, cnt in
                       sorted(agent_counts.items(), key=lambda x: -x[1])]
        resp_parts = [f"{rt}({cnt})" for rt, cnt in
                      sorted(response_counts.items(), key=lambda x: -x[1])]

        return (
            f"Wave {first_wave}-{last_wave} 摘要: "
            f"激活 {', '.join(agent_parts)}; "
            f"总输出能量={total_out_energy:.1f}; "
            f"响应分布: {', '.join(resp_parts)}"
        )
