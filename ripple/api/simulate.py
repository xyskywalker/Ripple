# simulate.py
# =============================================================================
# 公共 API — Ripple 模拟入口。
#
# 提供 simulate() 一键模拟函数，内部使用 SimulationRuntime 编排。
# 模拟结果完整保存为 JSON 文件。
# =============================================================================

"""公共 API — Ripple 模拟入口。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ripple.engine.recorder import SimulationRecorder
from ripple.engine.runtime import SimulationRuntime, ProgressCallback
from ripple.llm.router import ModelRouter
from ripple.skills.manager import SkillManager

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = "ripple_outputs"

# 强制免责声明 / Mandatory disclaimer
_DISCLAIMER = (
    "本报告由 AI 模拟生成，仅供参考，不构成任何商业决策建议。"
    "模拟结果受模型能力、输入数据质量和随机性影响，"
    "实际市场表现可能与预测显著不同。"
)

# deliberation_rounds 服务端硬上限 / Server-side hard cap
_MAX_DELIBERATION_ROUNDS = 4

# Skill-specific tribunal configurations / 各 Skill 的合议庭配置
_TRIBUNAL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "pmf-validation": {
        "dimensions": [
            "demand_resonance",
            "propagation_potential",
            "competitive_differentiation",
            "adoption_friction",
            "sustained_value",
        ],
        "rubric_key": "scorecard-dimensions",
        "members": [
            {"role": "MarketAnalyst", "perspective": "Market size, competition, timing, distribution", "expertise": "Market analysis"},
            {"role": "UserAdvocate", "perspective": "User needs, adoption path, friction points", "expertise": "User research"},
            {"role": "DevilsAdvocate", "perspective": "Risks, blind spots, failure modes, counterevidence", "expertise": "Risk analysis"},
        ],
    },
    "social-media": {
        "dimensions": [
            "reach_realism",
            "decay_realism",
            "virality_plausibility",
            "audience_activation",
            "timeline_realism",
        ],
        "rubric_key": "propagation-calibration",
        "members": [
            {"role": "PropagationDynamicist", "perspective": "Propagation decay curves, energy distribution, virality probability", "expertise": "Propagation dynamics"},
            {"role": "PlatformEcologist", "perspective": "Algorithm recommendation, traffic pool tiers, competitive content diversion", "expertise": "Platform ecosystem analysis"},
            {"role": "DevilsAdvocate", "perspective": "Cold start barriers, attention scarcity, content homogeneity, new account disadvantage", "expertise": "Risk analysis"},
        ],
    },
}

# Fallback config for unknown skills that declare a tribunal prompt
_DEFAULT_TRIBUNAL_CONFIG: Dict[str, Any] = {
    "dimensions": [
        "reach_realism",
        "decay_realism",
        "virality_plausibility",
        "audience_activation",
        "timeline_realism",
    ],
    "rubric_key": "propagation-calibration",
    "members": [
        {"role": "Analyst", "perspective": "General analysis", "expertise": "General analysis"},
        {"role": "Critic", "perspective": "Risks and blind spots", "expertise": "Critical analysis"},
        {"role": "DevilsAdvocate", "perspective": "Counterevidence and failure modes", "expertise": "Risk analysis"},
    ],
}


def _make_llm_caller(router, role: str):
    """创建指定角色的 LLM 调用函数。

    返回 async def(system_prompt, user_prompt) -> str 签名的协程函数，
    供 OmniscientAgent / StarAgent / SeaAgent 使用。

    所有 adapter 均暴露统一接口 async call(system_prompt, user_message) -> str，
    因此只需单一代码路径。
    """

    async def caller(*, system_prompt: str = "", user_prompt: str = "") -> str:
        if not router.check_budget(role):
            raise RuntimeError(f"LLM 调用次数已达上限（角色: {role}）")
        router.record_attempt(role)
        budget = router.budget
        call_num = budget.total_attempts
        limit_str = str(budget.max_calls) if not budget.is_unlimited else "∞"
        logger.info(f"[{role}] LLM 调用 #{call_num}/{limit_str}")
        adapter = router.get_model_backend(role)
        content = await adapter.call(system_prompt, user_prompt)
        router.record_call(role)
        return content

    return caller


def _resolve_output_path(
    output_path: Optional[str], run_id: str,
) -> Path:
    """确定输出文件路径。

    如果调用者指定了 output_path，直接使用；
    否则在默认目录下自动生成带时间戳和 run_id 的文件名。
    """
    if output_path:
        p = Path(output_path)
        # 如果指定的是目录，则在其中自动命名
        if p.is_dir() or str(output_path).endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            return p / f"{ts}_{run_id}.json"
        # 确保父目录存在
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # 默认：当前目录下 ripple_outputs/
    out_dir = Path(_DEFAULT_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_dir / f"{ts}_{run_id}.json"


async def simulate(
    event: Dict[str, Any],
    skill: str = "social-media",
    platform: Optional[str] = None,
    source: Optional[Dict[str, Any]] = None,
    historical: Optional[List[Dict[str, Any]]] = None,
    environment: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
    max_waves: int = 8,
    random_seed: Optional[int] = None,
    max_llm_calls: int = 200,
    skill_path: Optional[str] = None,
    config_file: Optional[str] = None,
    output_path: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    simulation_horizon: Optional[str] = None,
    # --- PMF Validation extensions ---
    channel: Optional[str] = None,
    vertical: Optional[str] = None,
    ensemble_runs: int = 3,
    deliberation_rounds: int = 3,
    redact_input: bool = False,
) -> Dict[str, Any]:
    """一键模拟（通用输入协议）。

    参数：
        event: 核心事件（自然语言 + 可选结构化度量）
        skill: 领域 Skill 名称（默认 "social-media"）
        platform: 平台标识（如 "xiaohongshu", "weibo" 等）
        source: 来源画像（自然语言描述）
        historical: 历史表现数据列表
        environment: 环境上下文
        llm_config: LLM 模型配置（最高优先级）。支持两种格式：
            - 简写: {"star": "gpt-4o", "sea": "claude-haiku"}
            - 完整: {"star": {"model_platform": "openai",
                               "model_name": "gpt-4o",
                               "api_key": "sk-xxx",
                               "url": "https://..."}}
        max_waves: 最大 Wave 数
        random_seed: 随机种子（保留兼容）
        max_llm_calls: 单次模拟的 LLM 调用总次数上限（ensemble 不倍增）
        skill_path: Skill 目录路径（如果提供，跳过搜索）
        config_file: LLM 配置文件路径（可选，不传则自动搜索
            llm_config.yaml）
        output_path: 模拟结果 JSON 文件输出路径（可选）。
            - 指定文件路径：直接保存到该路径
            - 指定目录路径（以 / 结尾）：在该目录下自动命名
            - 不指定：在 ./ripple_outputs/ 下自动命名
        on_progress: 进度回调函数（可选）。支持同步和异步函数。
            模拟过程中每个关键节点会调用此函数传入 SimulationEvent，
            适用于实时 UI 更新、WebSocket 推送、进度条显示等场景。
        simulation_horizon: 模拟时间范围（如 "48h"），用于确定性
            wave 数计算。若不传则回退到 LLM 估计的 estimated_total_waves。
        channel: 渠道标识（v0 仅支持 generic/None，自媒体通过 platform 指定）。
        ensemble_runs: 集成运行次数（默认 3）。共享同一 BudgetState，不倍增预算。
        deliberation_rounds: 合议庭总轮数（含 Round 1 独立评估），服务端上限 4。
        redact_input: 是否对落盘输入进行脱敏（默认 False）。

    返回：
        模拟结果字典，包含 output_file 和 disclaimer 字段。
    """
    logger.info(f"开始模拟: skill={skill}, platform={platform}, channel={channel}")

    # 1. 加载 Skill
    skill_manager = SkillManager()
    if skill_path:
        loaded_skill = skill_manager.load(skill, skill_path=Path(skill_path))
    else:
        loaded_skill = skill_manager.load(skill)
    logger.info(f"Skill 加载完成: {loaded_skill.name} v{loaded_skill.version}")

    # 2. 服务端强制 deliberation_rounds 上限 / Enforce server-side cap
    deliberation_rounds = min(deliberation_rounds, _MAX_DELIBERATION_ROUNDS)

    # 4. 创建 LLM 路由器（单实例，共享预算 — ensemble 不倍增）
    router = ModelRouter(
        llm_config=llm_config,
        max_llm_calls=max_llm_calls,
        config_file=config_file,
    )

    # 5. 创建 LLM callers（Star 和 Sea 分离，实现模型成本分层）
    omniscient_caller = _make_llm_caller(router, "omniscient")
    star_caller = _make_llm_caller(router, "star")
    sea_caller = _make_llm_caller(router, "sea")

    # 6. 提取 Skill profile（domain + platform + channel）
    skill_profile = loaded_skill.domain_profile
    if platform and platform in loaded_skill.platform_profiles:
        skill_profile += "\n\n" + loaded_skill.platform_profiles[platform]
        logger.info(f"已注入平台画像: {platform}")
    elif platform:
        logger.warning(
            f"未找到平台画像: {platform}"
            f"（可用: {list(loaded_skill.platform_profiles.keys())}）"
        )

    # 渠道画像注入 / Channel profile injection
    channel_profiles = getattr(loaded_skill, "channel_profiles", {}) or {}
    if channel and channel in channel_profiles:
        skill_profile += "\n\n" + channel_profiles[channel]
        logger.info(f"已注入渠道画像: {channel}")

    # 垂直领域画像注入 / Vertical profile injection
    vertical_profiles = getattr(loaded_skill, "vertical_profiles", {}) or {}
    if vertical and vertical in vertical_profiles:
        skill_profile += "\n\n" + vertical_profiles[vertical]
        logger.info(f"已注入垂直领域画像: {vertical}")
    elif vertical:
        logger.warning(
            f"未找到垂直领域画像: {vertical}"
            f"（可用: {list(vertical_profiles.keys())}）"
        )

    # 7. 构建 extra_phases（任何声明了 tribunal prompt 的 Skill 自动注册 DELIBERATE）
    extra_phases = None
    if loaded_skill.prompts.get("tribunal"):
        tribunal_caller = _make_llm_caller(router, "tribunal")
        tribunal_config = _TRIBUNAL_CONFIGS.get(
            loaded_skill.name, _DEFAULT_TRIBUNAL_CONFIG
        )

        async def _deliberate_handler(context: Dict[str, Any]) -> Dict[str, Any]:
            """DELIBERATE phase handler — delegates to DeliberationOrchestrator."""
            from dataclasses import asdict
            from ripple.engine.deliberation import DeliberationOrchestrator
            from ripple.primitives.pmf_models import TribunalMember

            # Extract rubric from skill (skill-aware key)
            rubric_key = tribunal_config["rubric_key"]
            scorecard_rubric = getattr(loaded_skill, "rubrics", {}).get(
                rubric_key, ""
            )
            dimensions = tribunal_config["dimensions"]

            # Inject tribunal skill context into system_prompt (trusted zone)
            from ripple.prompts import SKILL_CONTEXT_SEPARATOR, SKILL_CONTEXT_END
            tribunal_system = ""
            tribunal_prompt = loaded_skill.prompts.get("tribunal", "")
            if tribunal_prompt:
                tribunal_system += (
                    SKILL_CONTEXT_SEPARATOR + tribunal_prompt + SKILL_CONTEXT_END
                )
            if scorecard_rubric:
                tribunal_system += (
                    "\n\n===== SCORING RUBRIC =====\n\n"
                    + scorecard_rubric
                    + "\n\n===== END SCORING RUBRIC =====\n\n"
                )

            members = [
                TribunalMember(
                    role=m["role"],
                    perspective=m["perspective"],
                    expertise=m["expertise"],
                )
                for m in tribunal_config["members"]
            ]

            orch = DeliberationOrchestrator(
                members=members,
                llm_caller=tribunal_caller,
                dimensions=dimensions,
                rubric=scorecard_rubric
                if scorecard_rubric
                else "1=very weak, 2=weak, 3=moderate, 4=strong, 5=very strong",
                max_rounds=deliberation_rounds,
                system_prompt=tribunal_system,
            )

            evidence_pack = context.get("evidence_pack", {})
            records = await orch.run(evidence_pack=evidence_pack)
            records_dict = [asdict(r) for r in records]

            # Lightweight summary for Omniscient consumption (avoid context overflow)
            last = records[-1] if records else None
            deliberation_summary = {
                "rounds_executed": len(records),
                "converged": bool(getattr(last, "converged", False)),
                "final_positions": [
                    {"member_role": o.member_role, "scores": o.scores}
                    for o in (getattr(last, "opinions", []) if last else [])
                ],
                "consensus_points": list(getattr(last, "consensus_points", [])) if last else [],
                "dissent_points": list(getattr(last, "dissent_points", [])) if last else [],
            }

            return {
                "deliberation_records": records_dict,
                "deliberation_summary": deliberation_summary,
            }

        extra_phases = {
            "DELIBERATE": {
                "after": "RIPPLE",
                "weight": 0.15,
                "handler": _deliberate_handler,
            }
        }

    # 8. 构造 simulation_input
    simulation_input: Dict[str, Any] = {"event": event, "skill": skill}
    if platform:
        simulation_input["platform"] = platform
    if channel:
        simulation_input["channel"] = channel
    if vertical:
        simulation_input["vertical"] = vertical
    if source:
        simulation_input["source"] = source
    if historical:
        simulation_input["historical"] = historical
    if environment:
        simulation_input["environment"] = environment
    if simulation_horizon:
        simulation_input["simulation_horizon"] = simulation_horizon

    # 9. 提前生成 run_id 和输出路径，创建增量记录器
    run_id = str(uuid.uuid4())[:8]
    file_path = _resolve_output_path(output_path, run_id)
    recorder = SimulationRecorder(output_path=file_path, run_id=run_id)

    # 输入脱敏：记录器落盘使用脱敏版本，LLM 调用使用完整版本
    if redact_input:
        redacted_input = _redact_simulation_input(simulation_input)
        recorder.record_simulation_input(redacted_input)
    else:
        recorder.record_simulation_input(simulation_input)

    # 10. 执行模拟（单次或集成模式）
    try:
        if ensemble_runs <= 1:
            # 单次模拟（原有行为） / Single run (existing behavior)
            result = await _run_single_simulation(
                omniscient_caller=omniscient_caller,
                star_caller=star_caller,
                sea_caller=sea_caller,
                skill_profile=skill_profile,
                skill_prompts=loaded_skill.prompts,
                on_progress=on_progress,
                recorder=recorder,
                extra_phases=extra_phases,
                simulation_input=simulation_input,
                run_id=run_id,
            )
        else:
            # 集成模式 — 共享预算，不倍增 / Ensemble mode — shared budget, no multiplication
            result = await _run_ensemble(
                omniscient_caller=omniscient_caller,
                star_caller=star_caller,
                sea_caller=sea_caller,
                skill_profile=skill_profile,
                skill_prompts=loaded_skill.prompts,
                on_progress=on_progress,
                recorder=recorder,
                extra_phases=extra_phases,
                simulation_input=simulation_input,
                run_id=run_id,
                ensemble_runs=ensemble_runs,
                random_seed=random_seed,
            )

        # 记录器完成最终写入
        total_waves = result.get("total_waves", 0)
        recorder.finalize(total_waves)

    except Exception as exc:
        recorder.mark_failed(str(exc))
        logger.error(f"模拟失败: run_id={run_id}, error={exc}")
        raise

    result["output_file"] = str(file_path.resolve())
    result["compact_log_file"] = str(recorder.compact_log_path.resolve())

    # 11. 强制免责声明注入 / Mandatory disclaimer injection
    result["disclaimer"] = _DISCLAIMER

    logger.info(
        f"模拟完成: run_id={run_id}, 结果已保存至 {file_path.resolve()}"
    )
    return result


async def _run_single_simulation(
    *,
    omniscient_caller,
    star_caller,
    sea_caller,
    skill_profile,
    skill_prompts,
    on_progress,
    recorder,
    extra_phases,
    simulation_input,
    run_id,
) -> Dict[str, Any]:
    """执行单次模拟。 / Run a single simulation."""
    runtime = SimulationRuntime(
        omniscient_caller=omniscient_caller,
        star_caller=star_caller,
        sea_caller=sea_caller,
        skill_profile=skill_profile,
        skill_prompts=skill_prompts,
        on_progress=on_progress,
        recorder=recorder,
        extra_phases=extra_phases,
    )
    return await runtime.run(simulation_input, run_id=run_id)


async def _run_ensemble(
    *,
    omniscient_caller,
    star_caller,
    sea_caller,
    skill_profile,
    skill_prompts,
    on_progress,
    recorder,
    extra_phases,
    simulation_input,
    run_id,
    ensemble_runs: int,
    random_seed: Optional[int],
) -> Dict[str, Any]:
    """执行集成模拟（多次运行 + 聚合）。 / Run ensemble simulation (multiple runs + aggregation).

    共享同一 BudgetState（通过 callers），不倍增预算。
    预算不足时允许提前终止后续 run。
    / Shares one BudgetState (via callers), no budget multiplication.
    Allows early termination if budget is exhausted.
    """
    from ripple.api.variant_isolation import compute_variant_seeds
    from ripple.api.ensemble import aggregate_ordinal_scores, compute_fleiss_kappa
    from collections import Counter

    seeds = compute_variant_seeds("default", random_seed or 42, ensemble_runs)

    def _extract_grade(res: Dict[str, Any]) -> Optional[str]:
        g = res.get("grade") or res.get("pmf_grade") or res.get("overall_grade")
        if isinstance(g, str):
            return g.strip()
        return None

    def _extract_scores(res: Dict[str, Any]) -> Optional[Dict[str, int]]:
        candidates: List[Dict[str, Any]] = []

        for k in ("scores", "dimension_scores"):
            v = res.get(k)
            if isinstance(v, dict):
                candidates.append(v)

        sc = res.get("scorecard")
        if isinstance(sc, dict):
            for k in ("scores", "dimension_scores"):
                v = sc.get(k)
                if isinstance(v, dict):
                    candidates.append(v)
            dims = sc.get("dimensions")
            if isinstance(dims, dict):
                extracted: Dict[str, Any] = {}
                for dim, payload in dims.items():
                    if isinstance(payload, dict) and "score" in payload:
                        extracted[dim] = payload.get("score")
                if extracted:
                    candidates.append(extracted)

        if not candidates:
            return None

        # Prefer the candidate with the most keys
        best = max(candidates, key=lambda d: len(d.keys()))
        cleaned: Dict[str, int] = {}
        for dim, val in best.items():
            try:
                iv = int(val)
            except (TypeError, ValueError):
                continue
            if 1 <= iv <= 5:
                cleaned[str(dim)] = iv

        return cleaned or None

    all_results: List[Dict[str, Any]] = []
    failed = 0

    for i, seed in enumerate(seeds):
        sub_run_id = f"{run_id}r{i + 1}"
        if recorder is not None and hasattr(recorder, "begin_ensemble_run"):
            recorder.begin_ensemble_run(
                run_index=i,
                run_id=sub_run_id,
                random_seed=seed,
            )
        try:
            runtime = SimulationRuntime(
                omniscient_caller=omniscient_caller,
                star_caller=star_caller,
                sea_caller=sea_caller,
                skill_profile=skill_profile,
                skill_prompts=skill_prompts,
                on_progress=on_progress,
                recorder=recorder,
                extra_phases=extra_phases,
            )
            inp = dict(simulation_input)
            inp["random_seed"] = seed
            result = await runtime.run(inp, run_id=sub_run_id)
            all_results.append(result)
        except Exception as exc:
            failed += 1
            logger.warning("Ensemble run failed (idx=%d, seed=%s): %s", i, seed, exc)
            # Budget exhaustion should stop further runs (shared budget).
            msg = str(exc)
            if "LLM 调用次数已达上限" in msg or "budget" in msg.lower():
                if recorder is not None and hasattr(recorder, "end_ensemble_run"):
                    recorder.end_ensemble_run(error=msg)
                break
            if recorder is not None and hasattr(recorder, "end_ensemble_run"):
                recorder.end_ensemble_run(error=msg)
            continue
        else:
            if recorder is not None and hasattr(recorder, "end_ensemble_run"):
                recorder.end_ensemble_run()

    completed = len(all_results)
    last = all_results[-1] if all_results else {}

    # Aggregate PMF-like ordinal outputs when available
    grades = [g for g in (_extract_grade(r) for r in all_results) if g]
    grade_counts = Counter(grades)
    grade_mode = grade_counts.most_common(1)[0][0] if grade_counts else None
    grade_agreement = (
        (grade_counts[grade_mode] / len(grades)) if grade_mode and grades else 0.0
    )

    score_dicts = [s for s in (_extract_scores(r) for r in all_results) if s]
    score_agg = aggregate_ordinal_scores(score_dicts) if score_dicts else {}

    # Fleiss' kappa agreement across dimensions (items=dimensions, raters=runs, categories=1..5).
    # Only computed when we have >=2 runs and >=2 shared dimensions.
    dimension_kappa: Optional[float] = None
    dimension_kappa_level: Optional[str] = None
    kappa_dimensions: List[str] = []
    if len(score_dicts) >= 2:
        common_dims = set(score_dicts[0].keys())
        for s in score_dicts[1:]:
            common_dims &= set(s.keys())
        kappa_dimensions = sorted(common_dims)
        if len(kappa_dimensions) >= 2:
            ratings_matrix: List[List[int]] = []
            for dim in kappa_dimensions:
                row = [0, 0, 0, 0, 0]
                ok = True
                for s in score_dicts:
                    v = s.get(dim)
                    try:
                        iv = int(v)
                    except (TypeError, ValueError):
                        ok = False
                        break
                    if not (1 <= iv <= 5):
                        ok = False
                        break
                    row[iv - 1] += 1
                if ok and sum(row) == len(score_dicts):
                    ratings_matrix.append(row)
            if len(ratings_matrix) >= 2:
                dimension_kappa = float(compute_fleiss_kappa(ratings_matrix))
                if dimension_kappa >= 0.8:
                    dimension_kappa_level = "high"
                elif dimension_kappa >= 0.4:
                    dimension_kappa_level = "medium"
                else:
                    dimension_kappa_level = "low"

    ensemble_stats: Dict[str, Any] = {
        "runs_requested": ensemble_runs,
        "runs_completed": completed,
        "runs_failed": failed,
        "seeds": list(seeds),
        # v4.2: items=1 → no kappa; report grade sequence + agreement rate
        "grade_sequence": grades,
        "grade_mode": grade_mode,
        "grade_agreement_rate": grade_agreement,
        "dimension_aggregates": score_agg,
        "dimension_agreement_kappa": dimension_kappa,
        "dimension_agreement_level": dimension_kappa_level,
        "kappa_dimensions": kappa_dimensions,
    }

    merged = dict(last)
    merged["ensemble_runs_completed"] = completed
    merged["ensemble_runs_requested"] = ensemble_runs
    merged["ensemble_stats"] = ensemble_stats

    # Persist the aggregated view as the synthesis output (top-level keys)
    if recorder is not None:
        recorder.record_synthesis(merged)

    return merged


def _redact_simulation_input(simulation_input: Dict[str, Any]) -> Dict[str, Any]:
    """对模拟输入进行脱敏处理。 / Redact sensitive fields from simulation input.

    保留结构化枚举字段（product_type、channel、platform、name 等），
    替换字符串类型的描述字段为 [REDACTED]。
    / Preserves structured enum fields, replaces string description fields with [REDACTED].
    """
    # 不需要脱敏的安全字段（结构化枚举标签） / Safe fields (structured enum labels)
    _SAFE_KEYS = {
        "skill", "platform", "channel", "vertical", "product_type", "name",
        "simulation_horizon", "random_seed",
    }

    def _redact_value(key: str, value: Any) -> Any:
        if key in _SAFE_KEYS:
            return value
        if isinstance(value, str):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: _redact_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact_value(key, item) for item in value]
        return value

    return {k: _redact_value(k, v) for k, v in simulation_input.items()}
