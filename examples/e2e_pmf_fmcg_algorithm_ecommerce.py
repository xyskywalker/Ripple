#!/usr/bin/env python3
# =============================================================================
# e2e_pmf_fmcg_algorithm_ecommerce.py — PMF validation: FMCG x Douyin
#
# Simulates PMF validation of a new sparkling water brand on Douyin e-commerce.
# Two modes: basic (product only) / enhanced (+ brand account + history)
#
# Usage:
#   python examples/e2e_pmf_fmcg_algorithm_ecommerce.py basic
#   python examples/e2e_pmf_fmcg_algorithm_ecommerce.py enhanced
#   python examples/e2e_pmf_fmcg_algorithm_ecommerce.py all
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from e2e_helpers import (
    ReportRound,
    build_historical_from_posts,
    config_file_path,
    create_arg_parser,
    load_skill_report_bundle,
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
SKILL_NAME = "pmf-validation"
CHANNEL = "algorithm-ecommerce"
VERTICAL = "fmcg"
PLATFORM = "douyin"
SIMULATION_HOURS = 72
DEFAULT_WAVES = SIMULATION_HOURS // 3  # ~3h per wave
MAX_LLM_CALLS = 1000
ENSEMBLE_RUNS = 1
DELIBERATION_ROUNDS = 3

# =============================================================================
# Sample data
# =============================================================================
SAMPLE_PRODUCT: Dict[str, Any] = {
    "name": "清泉气泡水",
    "category": "0糖气泡水",
    "brand": "清泉（QingQuan）",
    "description": (
        "清泉气泡水是一款主打'真实果汁+0糖0脂'概念的气泡水新品。"
        "采用巴氏鲜榨果汁工艺（非浓缩还原），配合天然气泡水源，"
        "主打'喝得到果味，查得到0糖'的差异化定位。"
        "目标人群为25-35岁注重健康但又不想牺牲口感的都市白领。"
        "首发SKU为青柠味和白桃味两款，规格480ml，建议零售价6.8元/瓶。"
        "首发渠道为抖音电商（短视频种草+品牌自播+达人直播间分销），"
        "同步上线天猫旗舰店。线下渠道暂未铺设。"
    ),
    "price": "6.8元/瓶（480ml），抖音直播间首发价4.9元/瓶（限时3天）",
    "differentiators": [
        "巴氏鲜榨果汁工艺（非浓缩还原）",
        "0糖0脂0卡（赤藓糖醇代糖方案）",
        "天然气泡水源（非人工充气）",
        "包装设计主打'清透感'——透明瓶身+极简标签",
    ],
    "competitive_landscape": (
        "直接竞品：元气森林（市占率第一）、喜茶气泡水、农夫山泉苏打气泡水。"
        "间接竞品：所有0糖饮料、NFC果汁、椰子水等健康饮品。"
        "品类现状：0糖气泡水赛道已从蓝海转为红海。"
    ),
}

SAMPLE_BRAND_ACCOUNT: Dict[str, Any] = {
    "account_name": "清泉气泡水官方旗舰店",
    "bio": "真实果汁×0糖气泡 | 喝得到果味，查得到0糖",
    "platform_code": PLATFORM,
    "main_category": "食品饮料",
    "content_style": "清新自然风、产品特写+场景化饮用、强调成分透明",
    "target_audience": "25-35岁都市白领、健康饮品爱好者、健身人群",
    "followers_count": 8500,
    "posts_count": 23,
    "verification_status": "enterprise",
    "started_at": "2025-12-01",
}

SAMPLE_POSTS: List[Dict[str, Any]] = [
    {"title": "0糖气泡水盲测挑战｜能喝出哪杯是清泉吗？",
     "content": "找了5个同事做盲测，4款0糖气泡水PK...",
     "post_type": "短视频", "views": 85000, "likes": 3200, "comments": 280,
     "shares": 150, "sales": 45, "gmv": 220, "return_rate": 0.02},
    {"title": "配料表只有4行的气泡水，你见过吗？",
     "content": "翻了超市里10款气泡水的配料表，清泉的最短...",
     "post_type": "短视频", "views": 120000, "likes": 5800, "comments": 420,
     "shares": 380, "sales": 120, "gmv": 588, "return_rate": 0.03},
    {"title": "健身后喝什么？0糖气泡水测评",
     "content": "健身教练推荐的运动后饮品选择...",
     "post_type": "短视频", "views": 45000, "likes": 1800, "comments": 95,
     "shares": 60, "sales": 28, "gmv": 137, "return_rate": 0.01},
    {"title": "【直播回放】清泉气泡水首场品牌自播",
     "content": "品牌自播间，主播详细讲解产品工艺...",
     "post_type": "直播", "views": 12000, "likes": 450, "comments": 180,
     "shares": 25, "sales": 85, "gmv": 416, "return_rate": 0.05},
    {"title": "达人合作｜@小鹿爱喝水 清泉气泡水开箱",
     "content": "美食达人小鹿的开箱测评视频...",
     "post_type": "达人合作", "views": 250000, "likes": 12000, "comments": 850,
     "shares": 620, "sales": 380, "gmv": 1862, "return_rate": 0.08,
     "repurchase_rate": 0.05},
    {"title": "6块8一瓶的气泡水凭什么？",
     "content": "从水源到工艺拆解清泉的成本结构...",
     "post_type": "短视频", "views": 65000, "likes": 2400, "comments": 520,
     "shares": 180, "sales": 55, "gmv": 269, "return_rate": 0.02},
]


# =============================================================================
# PMF-specific data builders
# =============================================================================

def _build_event(product: Dict[str, Any]) -> Dict[str, Any]:
    """Build simulate() event from product data."""
    name = product.get("name", "")
    category = product.get("category", "")
    description = product.get("description", "")
    price = product.get("price", "")
    diffs = product.get("differentiators", [])

    parts = [f"产品：{name}", f"品类：{category}", f"定价：{price}"]
    if diffs:
        parts.append(f"核心差异点：{'、'.join(diffs)}")
    if description:
        parts.append(f"产品描述：{description[:500]}")

    return {
        "title": f"{name} — 抖音电商 PMF 验证",
        "description": description,
        "product_name": name,
        "category": category,
        "price": price,
        "differentiators": diffs,
        "target_channel": "抖音电商（算法推荐流 + 直播带货）",
        "validation_question": (
            f"'{name}'作为一款{category}新品，通过抖音电商渠道"
            f"能否验证 PMF？消费者在冲动下单后是否会产生真实复购需求？"
        ),
        "summary": " | ".join(parts),
    }


def _build_source(brand: Dict[str, Any]) -> Dict[str, Any]:
    """Build simulate() source from brand account."""
    name = brand.get("account_name", "")
    bio = brand.get("bio", "")
    followers = brand.get("followers_count", 0)
    style = brand.get("content_style", "")

    return {
        "account_name": name,
        "bio": bio,
        "platform_code": PLATFORM,
        "main_category": brand.get("main_category", ""),
        "content_style": style,
        "target_audience": brand.get("target_audience", ""),
        "followers_count": followers,
        "posts_count": brand.get("posts_count", 0),
        "verification_status": brand.get("verification_status", "enterprise"),
        "summary": f"品牌账号：{name} | 粉丝数：{followers} | 内容风格：{style}" + (f" | 简介：{bio}" if bio else ""),
    }


def _build_report_bundle(
    brand: Dict[str, Any] | None = None,
    historical_posts: List[Dict[str, Any]] | None = None,
) -> tuple[List[ReportRound], str, int]:
    """从 skill 加载 PMF 报告模板。 / Load the PMF report bundle from the skill."""
    request: Dict[str, Any] = {
        "skill": SKILL_NAME,
        "platform": PLATFORM,
        "channel": CHANNEL,
        "vertical": VERTICAL,
    }
    if brand:
        request["source"] = _build_source(brand)
    if historical_posts:
        request["historical"] = build_historical_from_posts(historical_posts)
    return load_skill_report_bundle(request)


def _build_report_rounds(
    brand: Dict[str, Any] | None = None,
    historical_posts: List[Dict[str, Any]] | None = None,
) -> List[ReportRound]:
    """兼容旧调用，仅返回轮次。 / Backward-compatible wrapper returning only rounds."""
    rounds, _, _ = _build_report_bundle(brand, historical_posts)
    return rounds


# =============================================================================
# Simulation runners
# =============================================================================

async def run_basic(waves: int) -> Dict[str, Any]:
    """Basic: product + channel + vertical only."""
    print()
    print("─" * 60)
    print("  PMF 验证 — 基础模拟（快消品 × 算法推荐电商）")
    print("─" * 60)
    return await simulate(
        event=_build_event(SAMPLE_PRODUCT),
        skill=SKILL_NAME,
        platform=PLATFORM,
        channel=CHANNEL,
        vertical=VERTICAL,
        source=None,
        historical=None,
        max_waves=waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=ENSEMBLE_RUNS,
        deliberation_rounds=DELIBERATION_ROUNDS,
    )


async def run_enhanced(waves: int) -> Dict[str, Any]:
    """Enhanced: product + brand account + Douyin history."""
    print()
    print("─" * 60)
    print("  PMF 验证 — 增强模拟（快消品 × 算法推荐电商 + 账号 + 历史）")
    print("─" * 60)
    return await simulate(
        event=_build_event(SAMPLE_PRODUCT),
        skill=SKILL_NAME,
        platform=PLATFORM,
        channel=CHANNEL,
        vertical=VERTICAL,
        source=_build_source(SAMPLE_BRAND_ACCOUNT),
        historical=build_historical_from_posts(SAMPLE_POSTS),
        max_waves=waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=ENSEMBLE_RUNS,
        deliberation_rounds=DELIBERATION_ROUNDS,
    )


# =============================================================================
# Main
# =============================================================================

_EXTRA_SUMMARY = {
    "ensemble_runs": ENSEMBLE_RUNS,
    "deliberation_rounds": DELIBERATION_ROUNDS,
}


async def main() -> None:
    parser = create_arg_parser(
        "Ripple PMF 验证 — 快消品 × 算法推荐电商（Douyin 72h）",
        default_waves=DEFAULT_WAVES,
    )
    args = parser.parse_args()
    waves = args.waves
    cfg = config_file_path()
    no_report = args.no_report
    basic_rounds, basic_role, basic_max_calls = _build_report_bundle()
    enhanced_rounds, enhanced_role, enhanced_max_calls = _build_report_bundle(SAMPLE_BRAND_ACCOUNT, SAMPLE_POSTS)

    if args.mode in ("basic", "all"):
        result = await run_and_interpret(
            "基础 PMF 验证",
            run_basic(waves),
            cfg,
            report_rounds=basic_rounds,
            report_role=basic_role,
            report_max_llm_calls=basic_max_calls,
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=no_report,
        )
        # Print PMF grade if available
        delib = result.get("deliberation")
        if delib:
            print(f"  PMF Grade: {delib.get('final_grade', 'N/A')}")

    if args.mode in ("enhanced", "all"):
        result = await run_and_interpret(
            "增强 PMF 验证",
            run_enhanced(waves),
            cfg,
            report_rounds=enhanced_rounds,
            report_role=enhanced_role,
            report_max_llm_calls=enhanced_max_calls,
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=no_report,
        )
        delib = result.get("deliberation")
        if delib:
            print(f"  PMF Grade: {delib.get('final_grade', 'N/A')}")


if __name__ == "__main__":
    asyncio.run(main())
