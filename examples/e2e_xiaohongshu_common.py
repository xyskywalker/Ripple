#!/usr/bin/env python3
"""Shared Xiaohongshu E2E fixtures and report prompts."""

from __future__ import annotations

from typing import Any, Dict, List

from e2e_helpers import (
    ReportRound,
    build_historical_from_posts,
    build_source_from_account,
    load_skill_report_bundle,
)

PLATFORM = "xiaohongshu"
SIMULATION_HOURS = 48
DEFAULT_WAVES = SIMULATION_HOURS // 2
MAX_LLM_CALLS = 300

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

def build_report_bundle(
    account: Dict[str, Any] | None = None,
    historical_posts: List[Dict[str, Any]] | None = None,
) -> tuple[List[ReportRound], str, int]:
    """从 skill 加载社交媒体报告模板。 / Load the social-media report bundle from the skill."""
    request: Dict[str, Any] = {
        "skill": "social-media",
        "platform": PLATFORM,
    }
    if account:
        request["source"] = build_source_from_account(account)
    if historical_posts:
        request["historical"] = build_historical_from_posts(historical_posts)
    return load_skill_report_bundle(request)


def build_report_rounds(
    account: Dict[str, Any] | None = None,
    historical_posts: List[Dict[str, Any]] | None = None,
) -> List[ReportRound]:
    """兼容旧调用，仅返回轮次。 / Backward-compatible wrapper returning only rounds."""
    rounds, _, _ = build_report_bundle(account, historical_posts)
    return rounds
