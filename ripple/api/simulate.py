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
from ripple.skills.manager import SkillManager

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = "ripple_outputs"


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
        max_llm_calls: 单次模拟的 LLM 调用总次数上限
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

    返回：
        模拟结果字典，包含 output_file 字段（结果 JSON 文件的完整路径）。
    """
    logger.info(f"开始模拟: skill={skill}, platform={platform}")

    # 1. 加载 Skill
    skill_manager = SkillManager()
    if skill_path:
        loaded_skill = skill_manager.load(skill, skill_path=Path(skill_path))
    else:
        loaded_skill = skill_manager.load(skill)
    logger.info(f"Skill 加载完成: {loaded_skill.name} v{loaded_skill.version}")

    # 2. 创建 LLM 路由器
    from ripple.llm.router import ModelRouter

    router = ModelRouter(
        llm_config=llm_config,
        max_llm_calls=max_llm_calls,
        config_file=config_file,
    )

    # 3. 创建 LLM callers（Star 和 Sea 分离，实现模型成本分层）
    omniscient_caller = _make_llm_caller(router, "omniscient")
    star_caller = _make_llm_caller(router, "star")
    sea_caller = _make_llm_caller(router, "sea")

    # 4. 提取 Skill profile（domain + platform）
    skill_profile = loaded_skill.domain_profile
    if platform and platform in loaded_skill.platform_profiles:
        skill_profile += "\n\n" + loaded_skill.platform_profiles[platform]
        logger.info(f"已注入平台画像: {platform}")
    elif platform:
        logger.warning(
            f"未找到平台画像: {platform}"
            f"（可用: {list(loaded_skill.platform_profiles.keys())}）"
        )

    # 5. 构造 simulation_input
    simulation_input: Dict[str, Any] = {"event": event, "skill": skill}
    if platform:
        simulation_input["platform"] = platform
    if source:
        simulation_input["source"] = source
    if historical:
        simulation_input["historical"] = historical
    if environment:
        simulation_input["environment"] = environment
    if simulation_horizon:
        simulation_input["simulation_horizon"] = simulation_horizon

    # 6. 提前生成 run_id 和输出路径，创建增量记录器
    #    记录器从模拟开始就动态写入 JSON，而非结束后一次性写入。
    run_id = str(uuid.uuid4())[:8]
    file_path = _resolve_output_path(output_path, run_id)
    recorder = SimulationRecorder(output_path=file_path, run_id=run_id)
    recorder.record_simulation_input(simulation_input)

    # 7. 创建运行时并执行（传入记录器和 run_id）
    runtime = SimulationRuntime(
        omniscient_caller=omniscient_caller,
        star_caller=star_caller,
        sea_caller=sea_caller,
        skill_profile=skill_profile,
        on_progress=on_progress,
        recorder=recorder,
    )

    try:
        result = await runtime.run(simulation_input, run_id=run_id)

        # 8. 记录器完成最终写入（合成结果已在 runtime 中通过
        #    recorder.record_synthesis() 写入，此处仅 finalize 元信息）
        total_waves = result.get("total_waves", 0)
        recorder.finalize(total_waves)

    except Exception as exc:
        # 模拟失败时标记记录器状态，已有的过程数据仍保留在文件中
        recorder.mark_failed(str(exc))
        logger.error(f"模拟失败: run_id={run_id}, error={exc}")
        raise

    result["output_file"] = str(file_path.resolve())

    logger.info(
        f"模拟完成: run_id={run_id}, 结果已保存至 {file_path.resolve()}"
    )
    return result
