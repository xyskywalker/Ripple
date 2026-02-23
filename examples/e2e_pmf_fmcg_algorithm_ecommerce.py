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


# =============================================================================
# Report prompts (PMF-specific)
# =============================================================================

_SYSTEM_PREFIX = (
    "你是 Ripple CAS（复杂自适应系统）PMF 验证模拟引擎的专业分析师。\n"
    "你的任务是基于模拟引擎输出的结构化数据，生成人类友好的 PMF 验证解读报告。\n\n"
    "【格式规范】\n"
    "- 一律使用简体中文输出\n"
    "- 用【】标记章节标题\n"
    "- 不输出 JSON、代码块或 Markdown 格式，只输出纯文本\n"
    "- 段落清晰、逻辑连贯，可直接展示给创业团队/产品团队阅读\n\n"
    "【Agent 命名规范】\n"
    "- 带 star_ 前缀的 Agent 显示为「星-」+ 中文描述\n"
    "- 带 sea_ 前缀的 Agent 显示为「海-」+ 中文描述\n\n"
    "【PMF 验证视角】\n"
    "- 始终区分'促销驱动'与'需求驱动'的行为\n"
    "- 始终区分'冲动消费'与'理性选择'的信号\n"
    "- 对算法推荐电商渠道，重点关注复购率而非首购量\n"
    "- 警惕将'算法给的流量'误读为'市场自发需求'\n"
)


def _build_report_rounds(
    brand: Dict[str, Any] | None = None,
    historical_posts: List[Dict[str, Any]] | None = None,
) -> List[ReportRound]:
    """Build 3-round PMF report specification."""
    extra_parts: List[str] = []
    if brand:
        extra_parts.append(
            f"## 补充：品牌账号\n"
            f"名称={brand.get('account_name', '')} "
            f"粉丝={brand.get('followers_count', 0)} "
            f"风格={brand.get('content_style', '')}"
        )
    if historical_posts:
        stats_text = format_stats_block(
            historical_posts,
            metrics=("views", "likes", "comments", "shares", "sales"),
        )
        if stats_text:
            extra_parts.append(f"## 补充：历史数据统计\n{stats_text}")
    extra_context = "\n\n".join(extra_parts)

    return [
        ReportRound(
            label="验证背景与模拟环境",
            system_prompt=_SYSTEM_PREFIX + (
                "当前任务：撰写 PMF 验证报告的前两个章节。\n\n"
                "【验证背景】（100-150字）\n"
                "概述本次 PMF 验证的背景：验证什么产品、在什么渠道、"
                "所属行业特征、品牌当前状态。\n\n"
                "【模拟环境设定】（200-300字）\n"
                "解读全视者在初始化阶段的环境设定：\n"
                "- 创建了哪些 Star/Sea Agent\n"
                "- 算法推荐电商渠道的传播参数设定\n"
                "- 快消品行业的反乐观基线锚定\n"
            ),
            extra_user_context=extra_context,
        ),
        ReportRound(
            label="传播过程与 PMF 信号",
            system_prompt=_SYSTEM_PREFIX + (
                "当前任务：撰写 PMF 验证报告的中间两个章节。\n\n"
                "【传播过程回顾】（150-250字）\n"
                "概述算法推荐电商渠道中的传播全貌：\n"
                "- 脉冲-衰减周期的表现\n"
                "- 提炼 3-5 个关键节点\n\n"
                "【PMF 信号识别】（200-350字）\n"
                "严格区分：\n"
                "- 强 PMF 信号（自然流量转化、非促销复购、用户自发内容创作）\n"
                "- 弱 PMF 信号（促销驱动的高销量、KOL 带货脉冲）\n"
                "- 伪 PMF 信号（算法初始流量、限时特价转化）\n"
            ),
        ),
        ReportRound(
            label="PMF 评级与行动建议",
            system_prompt=_SYSTEM_PREFIX + (
                "当前任务：撰写 PMF 验证报告的最后三个章节。\n\n"
                "【PMF 评级判定】（150-250字）\n"
                "基于合议庭讨论和模拟数据，给出 PMF 评级（A/B/C/D/F）及核心依据。\n\n"
                "【关键风险与挑战】（150-250字）\n"
                "针对快消品 × 算法推荐电商的特有风险：\n"
                "- 冲动消费退货、付费流量依赖、渠道单一、巨头竞争挤压\n\n"
                "【行动建议】（200-300字）\n"
                "3-5 条具体可落地的下一步行动：产品端、渠道端、内容端、数据端、风控端。\n"
            ),
            extra_user_context=extra_context,
        ),
    ]


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

    if args.mode in ("basic", "all"):
        result = await run_and_interpret(
            "基础 PMF 验证",
            run_basic(waves),
            cfg,
            report_rounds=_build_report_rounds(),
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
            report_rounds=_build_report_rounds(SAMPLE_BRAND_ACCOUNT, SAMPLE_POSTS),
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=no_report,
        )
        delib = result.get("deliberation")
        if delib:
            print(f"  PMF Grade: {delib.get('final_grade', 'N/A')}")


if __name__ == "__main__":
    asyncio.run(main())
