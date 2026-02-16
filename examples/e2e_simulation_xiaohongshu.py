#!/usr/bin/env python3
# =============================================================================
# e2e_simulation_xiaohongshu.py — 端到端全链路模拟示例（小红书 48 小时）
#
# 本示例完成两种模拟，用于验证 Ripple（全视者中心制）与选题/账号数据的对接形态，
# 选题、账号、历史内容的字段与 MPlus 项目（/Users/xymbp/cr/MPlus-dev）对齐，
# 便于后续两项目整合时直接复用数据结构。
#
# 1. 基础模拟：仅输入社交媒体选题内容 + 小红书平台画像，模拟发布后 48 小时。
# 2. 增强模拟：输入选题内容 + 账号基本信息与历史内容供参考 + 小红书平台画像，
#              模拟发布后 48 小时。
#
# 模拟结束后，通过三轮 LLM 请求对完整的模拟 JSON（含增量记录的过程数据）
# 进行全面解读，输出包含以下章节的人类友好报告：
#   【模拟背景】【初始环境】【传播过程回顾】【关键传播路径】
#   【关键时间点解读】【数据预测】【运营建议】
# 所有 Agent 名称统一转为友好中文格式，所有英文术语翻译为中文。
#
# 用法：
#   # 从项目根目录运行（推荐）
#   python examples/e2e_simulation_xiaohongshu.py basic
#   python examples/e2e_simulation_xiaohongshu.py enhanced
#   python examples/e2e_simulation_xiaohongshu.py all
#
# 依赖：项目根目录存在 llm_config.yaml；Skill social-media 可被加载。
# =============================================================================

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将项目根目录加入 sys.path，便于直接运行本脚本
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ripple.api.simulate import simulate
from ripple.llm.router import ModelRouter
from ripple.primitives.events import SimulationEvent

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# 常量：平台与时间
# =============================================================================

# 平台标识（与 Ripple 及 MPlus platform_code 一致）
PLATFORM_XIAOHONGSHU = "xiaohongshu"

# 模拟时长：发布后 48 小时。使用 wave-based 编排（全视者自行判定时间推进），
# max_waves 作为兼容参数传入 simulate()。
SIMULATION_HOURS = 48
# wave 数作为 simulate() 兼容参数
WAVES_FOR_48H = SIMULATION_HOURS // 2

# 单次模拟的 LLM 调用次数上限（额度）。超限后引擎会停止发起新调用。
# 项目内默认在 ripple/api/simulate.py、ripple/llm/router.py 中为 200；此处示例设为 300。
MAX_LLM_CALLS = 300


# =============================================================================
# 全视者中心制架构下的 Agent 调用模式
#
# Ripple 中：
#   - 全视者（Omniscient）是唯一的全知决策者，负责初始化、传播裁决、观测和结果合成。
#     每轮 wave 由全视者决定哪些 Agent 被激活、传播是否继续。
#   - Star（KOL）和 Sea（受众群体）只在被全视者裁决激活时才调用 LLM。
#     它们是纯行为模拟器，不知道全局状态。
#   - 如果某些 Agent 在整场模拟中没有被全视者激活，日志中不会看到对应调用。
#     这是正常行为——由全视者判断哪些 Agent 在当前传播语境下应当响应。
# =============================================================================


# =============================================================================
# 数据结构说明（与 MPlus 对齐）
#
# 选题 (topic)：id, session_id, title, description, target_platform, content,
#                metadata, account_profile_id, status, created_at, updated_at
# 账号 (account_profile)：id, platform_code, account_name, account_id, bio,
#   main_category, sub_categories, content_style, target_audience,
#   followers_count, posts_count, verification_status, started_at, extra_metrics
# 历史内容 (post_performance)：id, account_profile_id, title, content, post_type,
#   tags, is_top, post_url, publish_time, views, likes, comments, favorites, shares
# =============================================================================


def build_event_from_topic(topic: Dict[str, Any]) -> Dict[str, Any]:
    """从 MPlus 选题结构构建 simulate() 的 event 输入。

    引擎通过全视者的 INIT 阶段解读 event 语义，支持自然语言 + 可选结构化。
    此处将选题的 title / description / content 组装为既有结构又可读的 payload。
    """
    title = topic.get("title") or ""
    description = topic.get("description") or ""
    content = topic.get("content") or ""
    target_platform = topic.get("target_platform") or PLATFORM_XIAOHONGSHU

    # 自然语言摘要，供 LLM 理解“要模拟什么内容”
    summary_parts = [f"标题：{title}"]
    if description:
        summary_parts.append(f"选题说明：{description}")
    if content:
        # 正文过长时只取前 500 字作为摘要
        content_preview = content[:500] + "..." if len(content) > 500 else content
        summary_parts.append(f"正文摘要：{content_preview}")

    return {
        "title": title,
        "description": description,
        "content": content,
        "target_platform": target_platform,
        "summary": " ".join(summary_parts),
    }


def build_source_from_account(account: Dict[str, Any]) -> Dict[str, Any]:
    """从 MPlus 账号画像构建 simulate() 的 source 输入。

    source 表示"发布者/来源"画像，供全视者 INIT 阶段生成 Star/Sea Agent 时参考。
    """
    name = account.get("account_name") or ""
    bio = account.get("bio") or ""
    main_category = account.get("main_category") or ""
    sub_categories = account.get("sub_categories")
    if isinstance(sub_categories, list):
        sub_str = "、".join(sub_categories[:5])
    else:
        sub_str = str(sub_categories) if sub_categories else ""

    content_style = account.get("content_style") or ""
    target_audience = account.get("target_audience") or ""
    followers_count = account.get("followers_count", 0) or 0
    posts_count = account.get("posts_count", 0) or 0
    verification_status = account.get("verification_status") or "none"

    parts = [f"账号名：{name}", f"主赛道：{main_category}"]
    if sub_str:
        parts.append(f"细分赛道：{sub_str}")
    if bio:
        parts.append(f"简介：{bio}")
    if content_style:
        parts.append(f"内容风格：{content_style}")
    if target_audience:
        parts.append(f"目标受众：{target_audience}")
    parts.append(f"粉丝数：{followers_count}，发帖数：{posts_count}，认证：{verification_status}")

    return {
        "account_name": name,
        "bio": bio,
        "main_category": main_category,
        "sub_categories": sub_categories,
        "content_style": content_style,
        "target_audience": target_audience,
        "followers_count": followers_count,
        "posts_count": posts_count,
        "verification_status": verification_status,
        "summary": " | ".join(parts),
    }


def build_historical_from_posts(
    posts: List[Dict[str, Any]], max_items: int = 10
) -> List[Dict[str, Any]]:
    """从 MPlus 历史内容列表构建 simulate() 的 historical 输入。

    historical 为历史表现数据列表，供全视者 INIT 阶段做基线/校准参考。
    每条保留标题、内容摘要、互动数据等，便于 LLM 理解账号历史表现。
    """
    out = []
    for p in posts[:max_items]:
        title = p.get("title") or ""
        content = (p.get("content") or "")[:300]
        views = p.get("views", 0) or 0
        likes = p.get("likes", 0) or 0
        comments = p.get("comments", 0) or 0
        favorites = p.get("favorites", 0) or 0
        shares = p.get("shares", 0) or 0
        engagement_rate = p.get("engagement_rate")
        if engagement_rate is None and views > 0:
            total_interact = likes + comments + favorites + shares
            engagement_rate = round(total_interact / views * 100, 2)

        out.append({
            "title": title,
            "content_preview": content,
            "views": views,
            "likes": likes,
            "comments": comments,
            "favorites": favorites,
            "shares": shares,
            "engagement_rate": engagement_rate,
            "post_type": p.get("post_type") or "图文",
        })
    return out


def _historical_engagement_stats(
    posts: List[Dict[str, Any]],
    metrics: tuple = ("views", "likes", "comments", "favorites"),
) -> Dict[str, Dict[str, Any]]:
    """基于历史内容计算查看/点赞/评论/收藏的区间与简单置信描述。

    返回形如：
      {"views": {"n": 2, "min": 15000, "max": 28000, "mean": 21500, "p25": 15000, "p75": 28000}, ...}
    用于与模拟结果结合后供 LLM 生成可读总结。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for key in metrics:
        vals = []
        for p in posts:
            v = p.get(key)
            if v is not None and isinstance(v, (int, float)):
                vals.append(int(v))
        if not vals:
            out[key] = {"n": 0}
            continue
        vals.sort()
        n = len(vals)
        p25_idx = max(0, int(n * 0.25) - 1)
        p75_idx = min(n - 1, int(n * 0.75))
        out[key] = {
            "n": n,
            "min": vals[0],
            "max": vals[-1],
            "mean": round(sum(vals) / n, 0),
            "p25": vals[p25_idx],
            "p75": vals[p75_idx],
        }
    return out


async def _call_default_llm_for_summary(
    router: ModelRouter,
    role: str,
    system_prompt: str,
    user_message: str,
) -> str:
    """使用项目默认 LLM（llm_config 中 _default / omniscient）生成一段文本。

    仅在本示例内使用，通过统一的 adapter.call() 接口调用。
    """
    try:
        adapter = router.get_model_backend(role)
        content = await adapter.call(system_prompt, user_message)
        return (content or "").strip()
    except Exception as exc:
        logger.warning("默认 LLM 调用失败（可读总结跳过）: %s", exc)
        return ""


def _compress_waves_for_llm(
    waves: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """压缩 wave 数据以控制 LLM 上下文 token 量。

    保留每轮的核心信息（裁决摘要、激活 Agent、响应类型、能量流），
    省略冗余的 pre/post snapshot 细节，仅首轮保留初始状态概要。
    """
    compressed: List[Dict[str, Any]] = []
    for w in waves:
        entry: Dict[str, Any] = {
            "wave_number": w.get("wave_number"),
            "terminated": w.get("terminated", False),
        }

        # 提取裁决核心信息
        verdict = w.get("verdict") or {}
        entry["simulated_time"] = verdict.get("simulated_time_elapsed", "")
        entry["global_observation"] = verdict.get("global_observation", "")
        if verdict.get("termination_reason"):
            entry["termination_reason"] = verdict["termination_reason"]

        # 激活的 Agent 列表（ID + 能量 + 激活原因）
        activated = verdict.get("activated_agents") or []
        entry["activated_agents"] = [
            {
                "id": a.get("agent_id", ""),
                "energy": a.get("incoming_ripple_energy", 0),
                "reason": a.get("activation_reason", ""),
            }
            for a in activated
        ]

        # 跳过的 Agent 列表（仅保留 ID + 原因，便于理解选择性激活）
        skipped = verdict.get("skipped_agents") or []
        if skipped:
            entry["skipped_agents"] = [
                {"id": s.get("agent_id", ""), "reason": s.get("skip_reason", "")}
                for s in skipped
            ]

        # Agent 响应摘要（响应类型 + 输出能量）
        responses = w.get("agent_responses") or {}
        entry["responses"] = {
            aid: {
                "type": r.get("response_type", "unknown"),
                "out_energy": r.get("outgoing_energy", 0),
            }
            for aid, r in responses.items()
        }

        # 仅首轮包含初始快照摘要（后续轮的初始状态可从前轮末尾推断）
        if w.get("wave_number") == 0:
            pre = w.get("pre_snapshot") or {}
            entry["initial_state"] = {
                "star_count": len(pre.get("stars", {})),
                "sea_count": len(pre.get("seas", {})),
                "seed_energy": pre.get("seed_energy", 0),
            }

        compressed.append(entry)

    return compressed


# 共享的 LLM 系统指令前缀 — 定义输出风格、Agent 命名与术语翻译规范
_SHARED_SYSTEM_PREFIX = (
    "你是 Ripple CAS（复杂自适应系统）社交传播模拟引擎的专业分析师。\n"
    "你的任务是基于模拟引擎输出的结构化数据，生成人类友好的专业解读。\n\n"
    "【格式规范】\n"
    "- 一律使用简体中文输出\n"
    "- 用【】标记章节标题\n"
    "- 不输出 JSON、代码块或 Markdown 格式，只输出纯文本\n"
    "- 段落清晰、逻辑连贯，可直接展示给运营人员阅读\n\n"
    "【Agent 命名规范】\n"
    "- 带 star_ 前缀的 Agent 一律显示为「星-」+ 中文描述，例如：\n"
    "  star_hr_话术教练 → 星-HR话术教练\n"
    "  star_反内卷生活方式博主 → 星-反内卷生活方式博主\n"
    "- 带 sea_ 前缀的 Agent 一律显示为「海-」+ 中文描述，例如：\n"
    "  sea_一线新一线25-34打工人 → 海-一线新一线25-34打工人\n"
    "  sea_value_contrast_trolls → 海-价值观对立与杠精路人\n"
    "- 如果 Agent 名称是纯英文，翻译为对应的中文描述\n"
    "- 如果 Agent 名称已经是中文但带有 star_/sea_ 前缀，去掉前缀改为「星-」或「海-」\n\n"
    "【术语翻译规范】\n"
    "- 相态/状态英文一律用中文表述：\n"
    "  explosion/exploding → 爆发期, growth/growing → 成长期,\n"
    "  decline/declining → 衰退期, seed/seeding → 种子期,\n"
    "  stable/plateau → 稳定期/平台期\n"
    "- 响应类型：amplify → 放大传播, absorb → 吸收, mutate → 变异/二创,\n"
    "  create → 原创/创作, ignore → 忽略, suppress → 抑制\n"
    "- 能量相关：incoming_ripple_energy → 输入能量, outgoing_energy → 输出能量\n"
    "- 其他专业术语也一律用中文描述\n"
)


async def _interpret_background_and_init(
    router: ModelRouter,
    full_data: Dict[str, Any],
    account: Optional[Dict[str, Any]] = None,
    historical_posts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """第一轮 LLM 调用：模拟背景与初始环境解读。

    聚焦范围：simulation_input、init 结果、seed 数据、账号/历史（如有）。
    输出章节：【模拟背景】【初始环境】
    """
    process = full_data.get("process") or {}
    context: Dict[str, Any] = {
        "模拟输入": full_data.get("simulation_input"),
        "初始化结果": process.get("init"),
        "种子涟漪": process.get("seed"),
    }
    if account:
        context["发布账号信息"] = {
            "名称": account.get("account_name"),
            "简介": account.get("bio"),
            "主赛道": account.get("main_category"),
            "粉丝数": account.get("followers_count"),
            "发帖数": account.get("posts_count"),
            "内容风格": account.get("content_style"),
            "目标受众": account.get("target_audience"),
        }
    if historical_posts:
        context["历史数据统计"] = _historical_engagement_stats(
            historical_posts,
        )

    system = _SHARED_SYSTEM_PREFIX + (
        "当前任务：撰写解读报告的前两个章节。\n\n"
        "【模拟背景】（100-150字）\n"
        "简要回顾本次模拟的背景信息：选题内容是什么、目标平台、"
        "发布账号的基本画像（如有）、历史数据概况（如有）。\n\n"
        "【初始环境】（200-300字）\n"
        "解读全视者（Omniscient Agent）在初始化阶段设定的模拟环境：\n"
        "- 创建了哪些星 Agent（KOL/意见领袖）和海 Agent（受众群体），"
        "各自的定位描述\n"
        "- 动态参数设定（如 wave 时间窗口、传播衰减等关键参数）\n"
        "- 种子涟漪的内容摘要与初始能量值\n"
        "- 预估的传播轮数与安全上限\n"
    )

    return await _call_default_llm_for_summary(
        router, "omniscient", system,
        json.dumps(context, ensure_ascii=False, indent=2, default=str),
    )


async def _interpret_propagation_process(
    router: ModelRouter,
    full_data: Dict[str, Any],
) -> str:
    """第二轮 LLM 调用：传播过程与关键事件回顾。

    聚焦范围：waves 数据（压缩后）、全局观测。
    输出章节：【传播过程回顾】【关键传播路径】
    """
    process = full_data.get("process") or {}
    waves_raw = process.get("waves") or []
    waves_compressed = _compress_waves_for_llm(waves_raw)

    context: Dict[str, Any] = {
        "总波数": full_data.get("total_waves"),
        "传播波次记录": waves_compressed,
        "全局观测": process.get("observation"),
    }

    system = _SHARED_SYSTEM_PREFIX + (
        "当前任务：撰写解读报告的中间两个章节。\n\n"
        "【传播过程回顾】（150-250字）\n"
        "概述整个涟漪传播过程的全貌：\n"
        "- 共经历了几轮传播 wave，整体传播节奏如何\n"
        "- 提炼 3-5 个关键节点（不要逐轮罗列），例如：\n"
        "  首轮破圈、多 Agent 协同爆发、争议引发热度波动、传播终止原因\n"
        "- 引用全视者的全局观测作为总结性判断\n\n"
        "【关键传播路径】（200-350字）\n"
        "挑选 2-3 个对传播影响最大的 Agent 进行深度解读：\n"
        "- 该 Agent 在哪些 wave 被激活\n"
        "- 接收了多少传播能量、输出了多少能量\n"
        "- 做了什么类型的响应（放大传播/原创/变异/吸收等）\n"
        "- 对整体传播态势起到了什么关键作用\n"
        "- 如果有 Agent 出现特殊模式（如持续被激活、能量突变），也请指出\n"
    )

    return await _call_default_llm_for_summary(
        router, "omniscient", system,
        json.dumps(context, ensure_ascii=False, indent=2, default=str),
    )


async def _interpret_prediction_and_advice(
    router: ModelRouter,
    full_data: Dict[str, Any],
    account: Optional[Dict[str, Any]] = None,
    historical_posts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """第三轮 LLM 调用：关键时间点、数据预测与运营建议。

    聚焦范围：prediction、timeline、bifurcation_points、agent_insights。
    输出章节：【关键时间点解读】【数据预测】【运营建议】
    """
    context: Dict[str, Any] = {
        "预测结果": full_data.get("prediction"),
        "时间线": full_data.get("timeline"),
        "分叉点": full_data.get("bifurcation_points"),
        "Agent洞察": full_data.get("agent_insights"),
        "总波数": full_data.get("total_waves"),
    }
    if account:
        context["账号信息"] = {
            "名称": account.get("account_name"),
            "粉丝数": account.get("followers_count"),
            "主赛道": account.get("main_category"),
        }
    if historical_posts:
        context["历史互动统计"] = _historical_engagement_stats(
            historical_posts,
        )

    system = _SHARED_SYSTEM_PREFIX + (
        "当前任务：撰写解读报告的最后三个章节。\n\n"
        "【关键时间点解读】（150-250字）\n"
        "基于时间线和分叉点数据，解读 2-3 个最重要的时间节点：\n"
        "- 涌现现象发生的时间点及其表现"
        "（如多 Agent 协同创作、UGC 爆发、叙事分叉等）\n"
        "- 相变触发条件（如从成长期进入爆发期/稳定期的临界点）\n"
        "- 传播方向发生分叉的关键 wave 及其反事实分析"
        "（如果没有这个事件会怎样）\n\n"
        "【数据预测】（150-250字）\n"
        "输出含置信度描述的关键指标预测：\n"
        "- 曝光量、互动总量、收藏、评论、转发、涨粉等预估区间"
        "（p50/p80/p95 或高/中/低情景）\n"
        "- 爆款概率判断与核心假设条件\n"
        "- 如果有历史数据统计，将模拟预测与历史基线做对比，"
        "说明是否超出该账号的常规表现\n\n"
        "【运营建议】（200-300字）\n"
        "基于以上所有分析，给出 3-5 条具体可落地的运营优化建议：\n"
        "- 内容优化方向（标题/封面/正文的改进点）\n"
        "- 发布时机与互动引导策略\n"
        "- 评论区运营要点（如何引导话题、如何处理争议）\n"
        "- 风险规避措施（可能的负面因素及应对）\n"
        "- 后续内容系列化建议（如何将热度转化为长期资产）\n"
    )

    return await _call_default_llm_for_summary(
        router, "omniscient", system,
        json.dumps(context, ensure_ascii=False, indent=2, default=str),
    )


async def generate_llm_interpretation(
    result: Dict[str, Any],
    topic: Dict[str, Any],
    config_file: Optional[str],
    account: Optional[Dict[str, Any]] = None,
    historical_posts: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """通过多轮 LLM 调用生成完整的模拟结果解读报告。

    将解读任务科学拆分为三次独立的 LLM 请求，每次聚焦不同维度的数据，
    避免单次上下文过长导致 LLM 理解漂移：

      第一轮：模拟背景 + 全视者初始环境解读
      第二轮：传播过程回顾 + 关键 Agent 传播路径
      第三轮：关键时间点解读 + 数据预测 + 运营建议

    所有 Agent 名称统一转为友好中文格式，所有英文状态/相态翻译为中文。

    Args:
        result: simulate() 返回的结果字典（含 output_file 路径）。
        topic: 选题数据。
        config_file: LLM 配置文件路径。
        account: 发布账号画像（可选，增强模拟时提供）。
        historical_posts: 历史内容数据（可选，增强模拟时提供）。

    Returns:
        完整的解读报告文本，或 None（如果 LLM 调用全部失败）。
    """
    if not config_file:
        return None

    # 读取完整的模拟 JSON（包含增量记录器写入的详细过程数据）
    output_file = result.get("output_file")
    if output_file and Path(output_file).exists():
        full_data = json.loads(
            Path(output_file).read_text(encoding="utf-8"),
        )
    else:
        # 回退到内存中的结果（不含过程数据，但仍可解读合成结果）
        full_data = result

    try:
        router = ModelRouter(config_file=config_file, max_llm_calls=10)
    except Exception as exc:
        logger.warning("创建 LLM 路由器失败: %s", exc)
        return None

    # 三轮 LLM 调用，每轮聚焦不同维度
    parts: List[str] = []

    # 第一轮：模拟背景与初始环境
    logger.info("解读报告 — 第 1/3 轮：模拟背景与初始环境")
    try:
        part1 = await _interpret_background_and_init(
            router, full_data, account, historical_posts,
        )
        if part1:
            parts.append(part1)
    except Exception as exc:
        logger.warning("第一轮解读失败（跳过）: %s", exc)

    # 第二轮：传播过程与关键事件
    logger.info("解读报告 — 第 2/3 轮：传播过程与关键事件")
    try:
        part2 = await _interpret_propagation_process(router, full_data)
        if part2:
            parts.append(part2)
    except Exception as exc:
        logger.warning("第二轮解读失败（跳过）: %s", exc)

    # 第三轮：数据预测与运营建议
    logger.info("解读报告 — 第 3/3 轮：数据预测与运营建议")
    try:
        part3 = await _interpret_prediction_and_advice(
            router, full_data, account, historical_posts,
        )
        if part3:
            parts.append(part3)
    except Exception as exc:
        logger.warning("第三轮解读失败（跳过）: %s", exc)

    if not parts:
        return None

    return "\n\n".join(parts)


# =============================================================================
# 示例数据（与 MPlus 字段一致，便于整合时替换为真实查询结果）
# =============================================================================

# 示例选题：职场/生活方式类，适合小红书
SAMPLE_TOPIC: Dict[str, Any] = {
    "id": "topic-e2e-001",
    "session_id": None,
    "title": "上班3年才懂的5个摸鱼不内耗法则",
    "description": "针对年轻职场人的轻幽默干货，强调不内卷、不内耗，适合小红书职场赛道。",
    "target_platform": PLATFORM_XIAOHONGSHU,
    "content": (
        "1. 任务边界清晰：到点就停，不主动揽活。\n"
        "2. 情绪不带走：下班后不回想同事和领导。\n"
        "3. 小确幸记录：每天记一件小事，减少焦虑。\n"
        "4. 拒绝无效加班：能明天做的绝不今晚熬。\n"
        "5. 把「关我啥事」当成口头禅，少操心别人。\n"
        "配图建议：办公室桌面/通勤场景/手账小图。"
    ),
    "metadata": {},
    "account_profile_id": None,
    "status": "draft",
}

# 示例账号画像（增强模拟用）
SAMPLE_ACCOUNT: Dict[str, Any] = {
    "id": "account-e2e-001",
    "platform_code": PLATFORM_XIAOHONGSHU,
    "account_name": "职场不内耗学姐",
    "account_id": None,
    "bio": "3年大厂→现在只想过好每一天 | 职场干货·反内卷",
    "main_category": "职场成长",
    "sub_categories": ["职场干货", "反内卷", "生活方式"],
    "content_style": "轻松幽默、有共鸣、带一点吐槽",
    "target_audience": "25-34岁职场人、一线新一线城市",
    "followers_count": 12000,
    "posts_count": 86,
    "verification_status": "none",
    "started_at": "2024-01-01",
    "extra_metrics": None,
}

# 示例历史内容（增强模拟用）
SAMPLE_POSTS: List[Dict[str, Any]] = [
    {
        "id": "post-001",
        "account_profile_id": "account-e2e-001",
        "title": "领导总说「再想想」怎么办",
        "content": "分享三个话术，既不硬刚又能推进进度...",
        "post_type": "图文",
        "tags": ["职场", "沟通"],
        "is_top": 1,
        "views": 28000,
        "likes": 2100,
        "comments": 180,
        "favorites": 890,
        "shares": 120,
    },
    {
        "id": "post-002",
        "account_profile_id": "account-e2e-001",
        "title": "周一早上如何不崩溃",
        "content": "三个小习惯，让周一没那么难熬...",
        "post_type": "图文",
        "tags": ["职场", "心态"],
        "is_top": 0,
        "views": 15000,
        "likes": 980,
        "comments": 76,
        "favorites": 420,
        "shares": 55,
    },
]


# =============================================================================
# 进度回调 — 实时终端显示
# =============================================================================

_PHASE_CN = {
    "INIT": "初始化",
    "SEED": "种子注入",
    "RIPPLE": "涟漪传播",
    "OBSERVE": "全局观测",
    "SYNTHESIZE": "结果合成",
}

_BAR_WIDTH = 30  # 进度条字符宽度


def _progress_bar(progress: float) -> str:
    """生成文本进度条：[████████░░░░░░░░] 45%"""
    filled = int(_BAR_WIDTH * progress)
    empty = _BAR_WIDTH - filled
    return f"[{'█' * filled}{'░' * empty}] {progress:>5.1%}"


def print_progress(event: SimulationEvent) -> None:
    """终端进度回调（同步）。

    在每个关键事件点打印一行结构化进度信息，包括进度条、阶段、事件详情。
    外部应用可参考此实现替换为 WebSocket 推送、SSE 流或 UI 更新。
    """
    bar = _progress_bar(event.progress)
    phase_cn = _PHASE_CN.get(event.phase, event.phase)

    if event.type == "phase_start":
        print(f"  {bar}  ▶ {phase_cn} 开始")

    elif event.type == "phase_end":
        detail = event.detail or {}
        if event.phase == "INIT":
            star_n = detail.get("star_count", "?")
            sea_n = detail.get("sea_count", "?")
            waves = detail.get("estimated_waves", "?")
            print(f"  {bar}  ✓ {phase_cn} 完成 — "
                  f"Star×{star_n} Sea×{sea_n} 预估{waves}轮")
        elif event.phase == "SEED":
            energy = detail.get("seed_energy", "?")
            print(f"  {bar}  ✓ {phase_cn} 完成 — 能量={energy}")
        elif event.phase == "RIPPLE":
            eff = detail.get("effective_waves", "?")
            print(f"  {bar}  ✓ {phase_cn} 完成 — 实际{eff}轮")
        else:
            print(f"  {bar}  ✓ {phase_cn} 完成")

    elif event.type == "wave_start":
        w = (event.wave or 0) + 1
        total = event.total_waves or "?"
        print(f"  {bar}  ━ Wave {w}/{total}")

    elif event.type == "wave_end":
        detail = event.detail or {}
        if detail.get("terminated"):
            reason = detail.get("reason", "")
            print(f"  {bar}    ╰ 传播终止: {reason}")
        else:
            n = detail.get("agent_count", 0)
            print(f"  {bar}    ╰ {n} 个 Agent 响应")

    elif event.type == "agent_activated":
        aid = event.agent_id or "?"
        atype = event.agent_type or "?"
        energy = (event.detail or {}).get("energy", "?")
        print(f"  {bar}    → 激活 {atype}:{aid} (能量={energy})")

    elif event.type == "agent_responded":
        aid = event.agent_id or "?"
        rtype = (event.detail or {}).get("response_type", "?")
        print(f"  {bar}    ← {aid} 响应: {rtype}")


# =============================================================================
# 模拟执行
# =============================================================================

def _config_file_path() -> Optional[str]:
    """返回项目根目录下的 llm_config.yaml 路径；不存在则返回 None。"""
    p = _REPO_ROOT / "llm_config.yaml"
    return str(p) if p.exists() else None


async def run_basic_simulation() -> Dict[str, Any]:
    """基础模拟：仅选题内容 + 小红书平台，模拟发布后 48 小时。"""
    logger.info("开始基础模拟：仅选题 + 小红书 %d 小时", SIMULATION_HOURS)

    event = build_event_from_topic(SAMPLE_TOPIC)
    config_file = _config_file_path()

    print()
    print("─" * 60)
    print("  基础模拟 — 实时进度")
    print("─" * 60)

    result = await simulate(
        event=event,
        skill="social-media",
        platform=PLATFORM_XIAOHONGSHU,
        source=None,
        historical=None,
        environment=None,
        max_waves=WAVES_FOR_48H,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file,
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
    )

    logger.info("基础模拟完成: run_id=%s", result.get("run_id"))
    return result


async def run_enhanced_simulation() -> Dict[str, Any]:
    """增强模拟：选题 + 账号画像 + 历史内容 + 小红书平台，模拟发布后 48 小时。"""
    logger.info(
        "开始增强模拟：选题 + 账号 + 历史内容 + 小红书 %d 小时（%d waves）",
        SIMULATION_HOURS, WAVES_FOR_48H,
    )

    event = build_event_from_topic(SAMPLE_TOPIC)
    source = build_source_from_account(SAMPLE_ACCOUNT)
    historical = build_historical_from_posts(SAMPLE_POSTS)
    config_file = _config_file_path()

    print()
    print("─" * 60)
    print("  增强模拟 — 实时进度")
    print("─" * 60)

    result = await simulate(
        event=event,
        skill="social-media",
        platform=PLATFORM_XIAOHONGSHU,
        source=source,
        historical=historical,
        environment=None,
        max_waves=WAVES_FOR_48H,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file,
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
    )

    logger.info("增强模拟完成: run_id=%s", result.get("run_id"))
    return result


def _print_result_summary(result: Dict[str, Any], label: str) -> None:
    """打印模拟运行元信息摘要（不含解读，解读由 LLM 完成）。"""
    print()
    print("=" * 60)
    print(f"  {label} — 运行摘要")
    print("=" * 60)
    print(f"  run_id:              {result.get('run_id')}")
    print(f"  total_waves:         {result.get('total_waves')}")
    print(f"  wave_records_count:  {result.get('wave_records_count')}")
    output_file = result.get("output_file", "")
    if output_file:
        print(f"  output_file:         {output_file}")
    print("=" * 60)


# =============================================================================
# 主入口
# =============================================================================

async def _run_and_interpret(
    label: str,
    run_coro,
    config_file: Optional[str],
    account: Optional[Dict[str, Any]] = None,
    historical_posts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """执行模拟并生成 LLM 解读报告的统一流程。"""
    result = await run_coro
    _print_result_summary(result, label)

    # 调用多轮 LLM 生成完整解读报告
    report = await generate_llm_interpretation(
        result, SAMPLE_TOPIC, config_file,
        account=account,
        historical_posts=historical_posts,
    )
    if report:
        print()
        print("=" * 60)
        print(f"  {label} — 完整解读报告（LLM 生成）")
        print("=" * 60)
        print(report)
        print("=" * 60)
    else:
        print()
        print("  ⚠ LLM 解读报告生成失败，请检查 llm_config.yaml 配置。")

    return result


async def main() -> None:
    global WAVES_FOR_48H

    parser = argparse.ArgumentParser(
        description="Ripple 端到端模拟示例（小红书 48 小时），与 MPlus 选题/账号字段对齐。",
    )
    parser.add_argument(
        "mode",
        choices=["basic", "enhanced", "all"],
        help="basic=仅选题; enhanced=选题+账号+历史; all=依次执行 basic 与 enhanced",
    )
    parser.add_argument(
        "--waves",
        type=int,
        default=WAVES_FOR_48H,
        help="最大 wave 数（默认 24，对应 48h @ 2h/wave；快速试跑可用 --waves 2）",
    )
    args = parser.parse_args()

    # 允许命令行覆盖 wave 数（仅影响本次运行）
    if args.waves != WAVES_FOR_48H:
        WAVES_FOR_48H = args.waves
        logger.info("使用命令行 wave 数: %d", WAVES_FOR_48H)

    config_file = _config_file_path()

    if args.mode in ("basic", "all"):
        await _run_and_interpret(
            "基础模拟",
            run_basic_simulation(),
            config_file,
        )

    if args.mode in ("enhanced", "all"):
        await _run_and_interpret(
            "增强模拟",
            run_enhanced_simulation(),
            config_file,
            account=SAMPLE_ACCOUNT,
            historical_posts=SAMPLE_POSTS,
        )


if __name__ == "__main__":
    asyncio.run(main())
