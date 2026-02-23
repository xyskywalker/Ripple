#!/usr/bin/env python3
# =============================================================================
# e2e_simulation_cny_robot_xiaohongshu.py
# — Spring Festival Gala robot topic (Xiaohongshu lifestyle blogger, 48h)
#
# Usage:
#   python examples/e2e_simulation_cny_robot_xiaohongshu.py
#   python examples/e2e_simulation_cny_robot_xiaohongshu.py --waves 4
#   python examples/e2e_simulation_cny_robot_xiaohongshu.py --no-report
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from e2e_helpers import (
    build_event_from_topic,
    build_historical_from_posts,
    build_source_from_account,
    config_file_path,
    create_arg_parser,
    print_progress,
    run_and_interpret,
    setup_logging,
    simulate,
)

# Reuse social-media report prompts from the main xiaohongshu E2E
from e2e_simulation_xiaohongshu import _build_report_rounds

setup_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
PLATFORM = "xiaohongshu"
SIMULATION_HOURS = 48
DEFAULT_WAVES = SIMULATION_HOURS // 2
MAX_LLM_CALLS = 300

# =============================================================================
# Sample data — Spring Festival Gala robot topic
# =============================================================================
TOPIC: Dict[str, Any] = {
    "id": "topic-cny-robot-001",
    "title": "春晚机器人太好哭｜奶奶的最爱破防了",
    "description": (
        "以普通人观感写 2026 春晚机器人节目：蔡明与松延动力机器人小品《奶奶的最爱》、"
        "宇树《武BOT》武术表演，情感向、生活化。"
    ),
    "target_platform": PLATFORM,
    "content": (
        "除夕夜蹲了整场春晚，没想到被两个机器人节目整破防了。\n\n"
        "一个是蔡明老师和松延动力机器人一起的小品《奶奶的最爱》，机器人 1:1 复刻奶奶的神态和动作，"
        "看到「奶奶」给孙子夹菜、唠叨，眼泪直接绷不住，想我外婆了。\n\n"
        "另一个是宇树科技的《武BOT》武术表演，和去年《秧BOT》一样震撼，今年动作更流畅、更有力量感，"
        "感觉人形机器人真的从「会动」变成「会演」了。\n\n"
        "你们今年春晚最戳中自己的是哪个节目？评论区聊聊～\n"
        "配图建议：首图春晚节目截图+人物/家人团圆照；标签 #春晚 #机器人 #奶奶的最爱 #武BOT #生活记录。"
    ),
}

ACCOUNT: Dict[str, Any] = {
    "id": "account-cny-robot-001",
    "platform_code": PLATFORM,
    "account_name": "小日子慢半拍",
    "bio": "标记我的生活｜宅家·读书·小确幸，真实记录偶尔蹭热点",
    "main_category": "生活方式",
    "sub_categories": ["生活记录", "治愈系", "情感生活", "热点观感"],
    "content_style": "真实、松弛感、有共鸣、活人感，不刻意精致",
    "target_audience": "18-34 岁女性为主、一二线职场与学生党",
    "followers_count": 2100,
    "posts_count": 47,
    "verification_status": "none",
    "started_at": "2024-06-01",
}

POSTS: List[Dict[str, Any]] = [
    {
        "id": "post-cny-001",
        "title": "冬日宅家仪式感｜热茶书本过周末",
        "content": "入冬之后越来越不想出门，索性把周末都留给家里...",
        "post_type": "图文",
        "views": 12800, "likes": 368, "comments": 42, "favorites": 195, "shares": 28,
    },
    {
        "id": "post-cny-002",
        "title": "过年氛围感小物清单｜百元内搞定年味",
        "content": "还有两周就过年了，今年提前买了春联、窗花、小灯笼...",
        "post_type": "图文",
        "views": 11200, "likes": 341, "comments": 56, "favorites": 220, "shares": 19,
    },
]


# =============================================================================
# Main
# =============================================================================

async def main() -> None:
    parser = create_arg_parser(
        "Ripple E2E — 春晚机器人选题（小红书生活类博主 48h）",
        modes=(),  # single mode only, no basic/enhanced/all
        default_waves=DEFAULT_WAVES,
    )
    args = parser.parse_args()

    print()
    print("─" * 60)
    print("  春晚机器人选题 — 实时进度")
    print("─" * 60)

    coro = simulate(
        event=build_event_from_topic(TOPIC),
        skill="social-media",
        platform=PLATFORM,
        source=build_source_from_account(ACCOUNT),
        historical=build_historical_from_posts(POSTS),
        environment=None,
        max_waves=args.waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=1,
    )

    await run_and_interpret(
        "春晚机器人选题",
        coro,
        config_file_path(),
        report_rounds=_build_report_rounds(ACCOUNT, POSTS),
        no_report=args.no_report,
    )


if __name__ == "__main__":
    asyncio.run(main())
