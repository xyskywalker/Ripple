#!/usr/bin/env python3
# =============================================================================
# e2e_simulation_xiaohongshu.py — Xiaohongshu 48h E2E simulation
#
# Two modes: basic (topic only) / enhanced (topic + account + history)
#
# Usage:
#   python examples/e2e_simulation_xiaohongshu.py basic
#   python examples/e2e_simulation_xiaohongshu.py enhanced
#   python examples/e2e_simulation_xiaohongshu.py all
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from e2e_helpers import (
    ReportRound,
    build_event_from_topic,
    build_historical_from_posts,
    build_source_from_account,
    config_file_path,
    create_arg_parser,
    format_stats_block,
    print_progress,
    run_and_interpret,
    setup_logging,
    simulate,
)

setup_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
PLATFORM = "xiaohongshu"
SIMULATION_HOURS = 48
DEFAULT_WAVES = SIMULATION_HOURS // 2  # 2h per wave -> 24 waves
MAX_LLM_CALLS = 300

# =============================================================================
# Sample data (MPlus-aligned)
# =============================================================================
SAMPLE_TOPIC: Dict[str, Any] = {
    "id": "topic-e2e-001",
    "title": "上班3年才懂的5个摸鱼不内耗法则",
    "description": "针对年轻职场人的轻幽默干货，强调不内卷、不内耗，适合小红书职场赛道。",
    "target_platform": PLATFORM,
    "content": (
        "1. 任务边界清晰：到点就停，不主动揽活。\n"
        "2. 情绪不带走：下班后不回想同事和领导。\n"
        "3. 小确幸记录：每天记一件小事，减少焦虑。\n"
        "4. 拒绝无效加班：能明天做的绝不今晚熬。\n"
        "5. 把「关我啥事」当成口头禅，少操心别人。\n"
        "配图建议：办公室桌面/通勤场景/手账小图。"
    ),
}

SAMPLE_ACCOUNT: Dict[str, Any] = {
    "id": "account-e2e-001",
    "platform_code": PLATFORM,
    "account_name": "职场不内耗学姐",
    "bio": "3年大厂→现在只想过好每一天 | 职场干货·反内卷",
    "main_category": "职场成长",
    "sub_categories": ["职场干货", "反内卷", "生活方式"],
    "content_style": "轻松幽默、有共鸣、带一点吐槽",
    "target_audience": "25-34岁职场人、一线新一线城市",
    "followers_count": 12000,
    "posts_count": 86,
    "verification_status": "none",
    "started_at": "2024-01-01",
}

SAMPLE_POSTS: List[Dict[str, Any]] = [
    {
        "id": "post-001",
        "title": "领导总说「再想想」怎么办",
        "content": "分享三个话术，既不硬刚又能推进进度...",
        "post_type": "图文",
        "views": 28000, "likes": 2100, "comments": 180, "favorites": 890, "shares": 120,
    },
    {
        "id": "post-002",
        "title": "周一早上如何不崩溃",
        "content": "三个小习惯，让周一没那么难熬...",
        "post_type": "图文",
        "views": 15000, "likes": 980, "comments": 76, "favorites": 420, "shares": 55,
    },
]


# =============================================================================
# Report prompts (social-media specific)
# =============================================================================
_SYSTEM_PREFIX = (
    "你是 Ripple CAS（复杂自适应系统）社交传播模拟引擎的专业分析师。\n"
    "你的任务是基于模拟引擎输出的结构化数据，生成人类友好的专业解读。\n\n"
    "【格式规范】\n"
    "- 一律使用简体中文输出\n"
    "- 用【】标记章节标题\n"
    "- 不输出 JSON、代码块或 Markdown 格式，只输出纯文本\n"
    "- 段落清晰、逻辑连贯，可直接展示给运营人员阅读\n\n"
    "【Agent 命名规范】\n"
    "- 带 star_ 前缀的 Agent 显示为「星-」+ 中文描述\n"
    "- 带 sea_ 前缀的 Agent 显示为「海-」+ 中文描述\n"
    "- 纯英文 Agent 名称翻译为中文\n\n"
    "【术语翻译规范】\n"
    "- 相态：explosion→爆发期, growth→成长期, decline→衰退期, seed→种子期, stable→稳定期\n"
    "- 响应：amplify→放大传播, absorb→吸收, mutate→变异/二创, create→原创, ignore→忽略, suppress→抑制\n"
    "- 能量：incoming_ripple_energy→输入能量, outgoing_energy→输出能量\n"
)


def _build_report_rounds(
    account: Dict[str, Any] | None = None,
    historical_posts: List[Dict[str, Any]] | None = None,
) -> List[ReportRound]:
    """Build the 3-round report specification for social-media simulation."""
    # Extra context for rounds that need account/history info
    extra_parts: List[str] = []
    if account:
        extra_parts.append(
            f"## 补充：发布账号\n"
            f"名称={account.get('account_name', '')} "
            f"简介={account.get('bio', '')} "
            f"赛道={account.get('main_category', '')} "
            f"粉丝={account.get('followers_count', 0)} "
            f"风格={account.get('content_style', '')} "
            f"受众={account.get('target_audience', '')}"
        )
    if historical_posts:
        stats_text = format_stats_block(historical_posts)
        if stats_text:
            extra_parts.append(f"## 补充：历史互动统计\n{stats_text}")
    extra_context = "\n\n".join(extra_parts)

    return [
        ReportRound(
            label="模拟背景与初始环境",
            system_prompt=_SYSTEM_PREFIX + (
                "当前任务：撰写解读报告的前两个章节。\n\n"
                "【模拟背景】（100-150字）\n"
                "简要回顾本次模拟的背景信息：选题内容、目标平台、"
                "发布账号的基本画像（如有）、历史数据概况（如有）。\n\n"
                "【初始环境】（200-300字）\n"
                "解读全视者在初始化阶段设定的模拟环境：\n"
                "- 创建了哪些星 Agent 和海 Agent，各自的定位描述\n"
                "- 动态参数设定（wave 时间窗口、传播衰减等）\n"
                "- 种子涟漪的内容摘要与初始能量值\n"
                "- 预估的传播轮数与安全上限\n"
            ),
            extra_user_context=extra_context,
        ),
        ReportRound(
            label="传播过程与关键事件",
            system_prompt=_SYSTEM_PREFIX + (
                "当前任务：撰写解读报告的中间两个章节。\n\n"
                "【传播过程回顾】（150-250字）\n"
                "概述整个涟漪传播过程的全貌：\n"
                "- 共经历了几轮 wave，整体传播节奏\n"
                "- 提炼 3-5 个关键节点（首轮破圈、爆发、争议、终止等）\n"
                "- 引用全视者的全局观测作为总结性判断\n\n"
                "【关键传播路径】（200-350字）\n"
                "挑选 2-3 个对传播影响最大的 Agent 深度解读：\n"
                "- 在哪些 wave 被激活、接收/输出多少能量\n"
                "- 做了什么类型的响应、对传播态势的关键作用\n"
            ),
        ),
        ReportRound(
            label="数据预测与运营建议",
            system_prompt=_SYSTEM_PREFIX + (
                "当前任务：撰写解读报告的最后三个章节。\n\n"
                "【关键时间点解读】（150-250字）\n"
                "解读 2-3 个最重要的时间节点：涌现现象、相变触发、传播分叉。\n\n"
                "【数据预测】（150-250字）\n"
                "输出含置信度描述的关键指标预测：\n"
                "- 曝光量、互动总量、收藏、评论、转发、涨粉等预估区间\n"
                "- 爆款概率判断与核心假设条件\n\n"
                "【运营建议】（200-300字）\n"
                "3-5 条具体可落地的运营优化建议：\n"
                "- 内容优化方向、发布时机、评论区运营、风险规避、系列化建议\n"
            ),
            extra_user_context=extra_context,
        ),
    ]


# =============================================================================
# Simulation runners
# =============================================================================

async def run_basic(waves: int) -> Dict[str, Any]:
    """Basic: topic + platform only."""
    print()
    print("─" * 60)
    print("  基础模拟 — 实时进度")
    print("─" * 60)
    return await simulate(
        event=build_event_from_topic(SAMPLE_TOPIC),
        skill="social-media",
        platform=PLATFORM,
        source=None,
        historical=None,
        environment=None,
        max_waves=waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=1,
    )


async def run_enhanced(waves: int) -> Dict[str, Any]:
    """Enhanced: topic + account + history."""
    print()
    print("─" * 60)
    print("  增强模拟 — 实时进度")
    print("─" * 60)
    return await simulate(
        event=build_event_from_topic(SAMPLE_TOPIC),
        skill="social-media",
        platform=PLATFORM,
        source=build_source_from_account(SAMPLE_ACCOUNT),
        historical=build_historical_from_posts(SAMPLE_POSTS),
        environment=None,
        max_waves=waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=1,
    )


# =============================================================================
# Main
# =============================================================================

async def main() -> None:
    parser = create_arg_parser(
        "Ripple E2E — 小红书 48h 模拟（basic / enhanced / all）",
        default_waves=DEFAULT_WAVES,
    )
    args = parser.parse_args()
    waves = args.waves
    cfg = config_file_path()
    no_report = args.no_report

    if args.mode in ("basic", "all"):
        await run_and_interpret(
            "基础模拟",
            run_basic(waves),
            cfg,
            report_rounds=_build_report_rounds(),
            no_report=no_report,
        )

    # "all" mode: basic and enhanced are fully independent runs —
    # no shared state, no shared router, no shared coroutine.
    if args.mode in ("enhanced", "all"):
        await run_and_interpret(
            "增强模拟",
            run_enhanced(waves),
            cfg,
            report_rounds=_build_report_rounds(SAMPLE_ACCOUNT, SAMPLE_POSTS),
            no_report=no_report,
        )


if __name__ == "__main__":
    asyncio.run(main())
