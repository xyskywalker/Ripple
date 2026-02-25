#!/usr/bin/env python3
# =============================================================================
# e2e_ab_test_fmcg_coffee.py —— A/B 测试：冻干咖啡定位策略 × 抖音电商 PMF 验证 / A/B test: freeze-dried coffee positioning strategy × Douyin PMF validation
#
# 测试假设：相同基底产品仅改变定位策略，观察 PMF 是否显著差异。 / Hypothesis: same base product with different positioning may produce significantly different PMF performance.
#
#   A组（黑镜·零感）: "真0添加——0糖0脂0卡0代糖" → 健康焦虑驱动 / Group A: health-anxiety driven positioning
#   B组（黑镜·云南）: "云南保山单一产地 SCA 85+"  → 品质溢价驱动 / Group B: quality-premium driven positioning
#
# 控制变量：品牌、价格、规格、渠道、投放时段、目标人群。 / Controlled variables: brand, price, package size, channel, launch window, and target persona.
# 唯一自变量：核心定位策略（健康焦虑 vs 品质溯源）。 / Independent variable: core positioning strategy.
#
# 用法 / Usage:
#   python examples/e2e_ab_test_fmcg_coffee.py a              # 仅运行A组
#   python examples/e2e_ab_test_fmcg_coffee.py b              # 仅运行B组
#   python examples/e2e_ab_test_fmcg_coffee.py ab             # 双组 + A/B对比报告
#   python examples/e2e_ab_test_fmcg_coffee.py ab --waves 4   # 快速试跑
#   python examples/e2e_ab_test_fmcg_coffee.py compare \
#     --file-a ripple_outputs/xxx_a.md \
#     --file-b ripple_outputs/xxx_b.md                        # 从已有结果直接对比
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from e2e_helpers import (
    ReportRound,
    build_historical_from_posts,
    call_llm,
    config_file_path,
    create_arg_parser,
    format_stats_block,
    load_simulation_log,
    print_compact_log,
    print_progress,
    print_result_summary,
    run_and_interpret,
    setup_logging,
    simulate,
    REPO_ROOT,
)
from ripple.llm.router import ModelRouter

setup_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# 常量配置 / Constants
# =============================================================================
SKILL_NAME = "pmf-validation"
CHANNEL = "algorithm-ecommerce"
VERTICAL = "fmcg"
PLATFORM = "douyin"
SIMULATION_HOURS = 72
DEFAULT_WAVES = SIMULATION_HOURS // 3  # 每个 wave ≈ 3小时
MAX_LLM_CALLS = 1000
ENSEMBLE_RUNS = 1
DELIBERATION_ROUNDS = 3

# =============================================================================
# A 组产品定义（黑镜·零感） / Group A product definition (HEIJING Zero)
# =============================================================================
PRODUCT_A: Dict[str, Any] = {
    "name": "黑镜·零感冻干黑咖啡",
    "category": "冻干即溶咖啡",
    "brand": "黑镜（HEIJING）",
    "description": (
        "黑镜·零感是一款主打'真0添加'概念的冻干即溶黑咖啡。"
        "区别于市面上使用赤藓糖醇、甜菊糖苷等代糖方案的'伪0糖'产品，"
        "零感的配料表只有一行：100%阿拉比卡咖啡萃取冻干粉。"
        "核心卖点'0糖0脂0卡0代糖'直击当下消费者对隐性添加的焦虑。"
        "目标人群为25-35岁注重身材管理和成分透明的都市白领、健身人群。"
        "规格为2g×10颗迷你罐装，包装采用哑光黑+荧光绿配色，强调健康活力感。"
        "建议零售价59.9元/盒（5.99元/杯），抖音首发价39.9元/盒（3.99元/杯）。"
        "首发渠道为抖音电商（短视频种草+品牌自播+达人矩阵分销），"
        "内容策略主打'配料表只有一行'的视觉冲击和成分对比。"
    ),
    "price": "59.9元/盒（2g×10颗），抖音首发价39.9元/盒（限时7天）",
    "differentiators": [
        "真0添加：配料表仅一行（100%阿拉比卡冻干粉），0糖0脂0卡且0代糖",
        "成分透明：每罐印有完整营养成分检测报告二维码",
        "极简包装：哑光黑+荧光绿迷你罐，配料表占包装正面50%面积",
        "健康背书：获中国营养学会'清洁标签'认证",
    ],
    "competitive_landscape": (
        "直接竞品：三顿半（冻干咖啡品类开创者，但使用赤藓糖醇调味款占比40%）、"
        "隅田川（主力为挂耳和液体咖啡，冻干线非核心）、"
        "永璞（设计驱动，冻干线口味偏甜）、"
        "瑞幸冻干（价格杀手，但品控争议多）。"
        "间接竞品：所有主打'0糖'概念的饮品和代餐产品。"
        "品类现状：冻干咖啡赛道已从蓝海转为红海，三顿半一家独大，"
        "但'真0添加'细分赛道尚无强势品牌占位。"
    ),
}

# =============================================================================
# B 组产品定义（黑镜·云南） / Group B product definition (HEIJING Yunnan)
# =============================================================================
PRODUCT_B: Dict[str, Any] = {
    "name": "黑镜·云南冻干精品咖啡",
    "category": "冻干即溶咖啡",
    "brand": "黑镜（HEIJING）",
    "description": (
        "黑镜·云南是一款主打'精品产地溯源'概念的冻干即溶咖啡。"
        "精选云南保山高黎贡山海拔1800-2100米的小粒种阿拉比卡咖啡豆，"
        "经SCA（精品咖啡协会）杯测评分达85+，属精品级（Specialty Grade）。"
        "每罐印有种植庄园编号、采摘批次和海拔信息，实现全链路溯源。"
        "核心卖点'从海拔2000米到你的杯中'主打国产精品咖啡的品质叙事。"
        "目标人群为25-35岁追求品质生活的都市白领、精品咖啡入门用户。"
        "规格为2g×10颗迷你罐装，包装采用哑光黑+大地棕配色，强调产地自然感。"
        "建议零售价59.9元/盒（5.99元/杯），抖音首发价39.9元/盒（3.99元/杯）。"
        "首发渠道为抖音电商（短视频种草+品牌自播+达人矩阵分销），"
        "内容策略主打产地溯源纪录片风格和咖啡风味轮解析。"
    ),
    "price": "59.9元/盒（2g×10颗），抖音首发价39.9元/盒（限时7天）",
    "differentiators": [
        "单一产地溯源：云南保山高黎贡山小粒种，每罐标注庄园编号和海拔",
        "精品级认证：SCA杯测评分85+，具备花香、柑橘、红糖风味层次",
        "产地直采：与当地咖啡合作社签约，从采摘到冻干全程72小时完成",
        "品质叙事：包装内附产地明信片和风味轮卡片，增强仪式感",
    ],
    "competitive_landscape": (
        "直接竞品：三顿半（以拼配为主，单一产地款为限量系列非常规SKU）、"
        "隅田川（主打便捷性，未强调产地故事）、"
        "永璞（设计和联名驱动，产地叙事薄弱）、"
        "瑞幸冻干（价格导向，无产地溢价空间）。"
        "间接竞品：线下精品咖啡馆的零售豆/挂耳产品（如Manner、Seesaw）。"
        "品类现状：冻干咖啡赛道竞争激烈，但'单一产地可溯源'定位在即溶品类中"
        "仍属差异化空白，精品咖啡'第四波浪潮'的产地叙事尚未被冻干品牌充分占位。"
    ),
}

# =============================================================================
# 共享品牌账号（两组使用相同账号基线）
# =============================================================================
BRAND_ACCOUNT: Dict[str, Any] = {
    "account_name": "黑镜咖啡官方旗舰店",
    "bio": "一杯好咖啡，不需要解释 | 新锐冻干咖啡品牌",
    "platform_code": PLATFORM,
    "main_category": "食品饮料",
    "content_style": "高级质感、产品特写+场景化、强调品质细节",
    "target_audience": "25-35岁都市白领、咖啡爱好者、品质生活追求者",
    "followers_count": 12000,
    "posts_count": 18,
    "verification_status": "enterprise",
    "started_at": "2025-11-15",
}

# =============================================================================
# 共享历史数据（品牌通用内容，不偏向任一定位）
# =============================================================================
HISTORICAL_POSTS: List[Dict[str, Any]] = [
    {
        "title": "冻干咖啡盲测PK：黑镜 vs 三顿半 vs 隅田川",
        "content": "找了8个同事做盲测，3款冻干咖啡不贴标签直接冲泡品鉴...",
        "post_type": "短视频",
        "views": 180000, "likes": 7200, "comments": 560,
        "shares": 420, "sales": 95, "gmv": 3800, "return_rate": 0.03,
    },
    {
        "title": "配料表翻车现场：10款冻干咖啡成分大起底",
        "content": "买了市面上10款冻干咖啡，逐一拆解配料表和营养成分...",
        "post_type": "短视频",
        "views": 320000, "likes": 15000, "comments": 1200,
        "shares": 890, "sales": 210, "gmv": 8400, "return_rate": 0.02,
    },
    {
        "title": "2块钱一杯的冻干 vs 30块的手冲，盲测结果意外了",
        "content": "找了专业咖啡师和普通消费者各5人，盲测打分...",
        "post_type": "短视频",
        "views": 250000, "likes": 11000, "comments": 980,
        "shares": 650, "sales": 150, "gmv": 6000, "return_rate": 0.04,
    },
    {
        "title": "打工人续命指南：办公室冻干咖啡冲泡的5种方法",
        "content": "冰美式、燕麦拿铁、气泡美式、椰奶dirty、冰博克...",
        "post_type": "短视频",
        "views": 95000, "likes": 4200, "comments": 350,
        "shares": 280, "sales": 65, "gmv": 2600, "return_rate": 0.02,
    },
    {
        "title": "【直播回放】黑镜冻干咖啡品牌首场自播",
        "content": "品牌首场自播，主播详细讲解冻干工艺和品牌理念...",
        "post_type": "直播",
        "views": 28000, "likes": 850, "comments": 420,
        "shares": 65, "sales": 180, "gmv": 7200, "return_rate": 0.06,
    },
    {
        "title": "探访云南咖啡庄园：从咖啡樱桃到冻干的72小时",
        "content": "跟着镜头深入云南保山高黎贡山，记录咖啡豆从采摘到冻干全过程...",
        "post_type": "短视频",
        "views": 140000, "likes": 6500, "comments": 480,
        "shares": 520, "sales": 85, "gmv": 3400, "return_rate": 0.02,
    },
]


# =============================================================================
# 数据构建器（PMF 验证专用） / Data builders for PMF validation
# =============================================================================

def _build_event(product: Dict[str, Any], group_label: str) -> Dict[str, Any]:
    """从产品定义构建 simulate() 的 event 参数。 / Build simulate() event payload from product definition."""
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
        "title": f"[A/B测试-{group_label}组] {name} — 抖音电商 PMF 验证",
        "description": description,
        "product_name": name,
        "category": category,
        "price": price,
        "differentiators": diffs,
        "competitive_landscape": product.get("competitive_landscape", ""),
        "target_channel": "抖音电商（算法推荐流 + 直播带货）",
        "validation_question": (
            f"'{name}'作为一款{category}新品，"
            f"核心差异化定位为「{'；'.join(diffs[:2])}」，"
            f"通过抖音电商渠道能否验证 PMF？"
            f"消费者在算法推荐下单后是否会产生真实复购需求？"
            f"该定位能否在三顿半、隅田川主导的冻干咖啡红海中建立独立心智？"
        ),
        "summary": " | ".join(parts),
    }


def _build_source(brand: Dict[str, Any]) -> Dict[str, Any]:
    """从品牌账号构建 simulate() 的 source 参数。 / Build simulate() source payload from brand account."""
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
        "summary": (
            f"品牌账号：{name} | 粉丝数：{followers} | "
            f"内容风格：{style}" + (f" | 简介：{bio}" if bio else "")
        ),
    }


# =============================================================================
# 单组 PMF 报告提示词（a / b 单独运行时使用）
# =============================================================================

_INDIVIDUAL_SYSTEM_PREFIX = (
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


def _build_individual_report_rounds() -> List[ReportRound]:
    """构建单组 PMF 报告规范（3轮）。 / Build single-group PMF report specification (3 rounds)."""
    extra_context = ""
    stats_text = format_stats_block(
        HISTORICAL_POSTS,
        metrics=("views", "likes", "comments", "shares", "sales"),
    )
    if stats_text:
        extra_context = f"## 补充：历史数据统计\n{stats_text}"

    return [
        ReportRound(
            label="验证背景与模拟环境",
            system_prompt=_INDIVIDUAL_SYSTEM_PREFIX + (
                "当前任务：撰写 PMF 验证报告的前两个章节。\n\n"
                "【验证背景】（100-150字）\n"
                "概述本次 PMF 验证的背景：验证什么产品、核心定位、"
                "所属行业特征、目标渠道。\n\n"
                "【模拟环境设定】（200-300字）\n"
                "解读全视者在初始化阶段的环境设定。\n"
            ),
            extra_user_context=extra_context,
        ),
        ReportRound(
            label="传播过程与 PMF 信号",
            system_prompt=_INDIVIDUAL_SYSTEM_PREFIX + (
                "当前任务：撰写 PMF 验证报告的中间两个章节。\n\n"
                "【传播过程回顾】（150-250字）\n"
                "概述算法推荐电商渠道中的传播全貌。\n\n"
                "【PMF 信号识别】（200-350字）\n"
                "严格区分强 PMF 信号、弱 PMF 信号、伪 PMF 信号。\n"
            ),
        ),
        ReportRound(
            label="PMF 评级与行动建议",
            system_prompt=_INDIVIDUAL_SYSTEM_PREFIX + (
                "当前任务：撰写 PMF 验证报告的最后两个章节。\n\n"
                "【PMF 评级判定】（150-250字）\n"
                "基于合议庭讨论和模拟数据，给出 PMF 评级及核心依据。\n\n"
                "【行动建议】（200-300字）\n"
                "3-5 条具体可落地的下一步行动。\n"
            ),
            extra_user_context=extra_context,
        ),
    ]


# =============================================================================
# A/B 对比报告提示词（ab / compare 模式，4轮深度对比分析）
# =============================================================================

_AB_SYSTEM_PREFIX = (
    "你是 Ripple CAS（复杂自适应系统）PMF 验证引擎的资深 A/B 测试分析师。\n"
    "你将同时收到两组模拟的结构化摘要——A组（健康焦虑定位）和B组（品质溯源定位），\n"
    "以及从模拟数据中提取的精确数字和评分矩阵。请基于数据进行严谨的对比分析。\n\n"
    "【输出格式规范（必须遵守）】\n"
    "- 一律使用简体中文\n"
    "- 使用 Markdown 格式输出：用 ## 标记大标题，### 标记子标题\n"
    "- **所有对比数据必须用 Markdown 表格呈现**，严禁用纯文字罗列对比项\n"
    "- 用「」标记关键术语，用 **加粗** 标记关键结论\n"
    "- 每个章节至少包含一个数据表格\n\n"
    "【系统术语中文化（严格遵守，不得出现英文原文）】\n"
    "- wave_time_window → 波次时间窗口\n"
    "- energy_decay_per_wave → 每波能量衰减率\n"
    "- energy / E → 能量值\n"
    "- estimated_waves → 预估波次数\n"
    "- total_waves → 实际波次数\n"
    "- absorb → 吸收（被动接收信息）\n"
    "- comment → 评论互动\n"
    "- mutate → 变异传播（内容二创/改编）\n"
    "- create → 原创扩散（产出全新内容）\n"
    "- ignore → 忽略\n"
    "- demand_resonance → 需求共振\n"
    "- propagation_potential → 传播势能\n"
    "- competitive_differentiation → 竞争差异化\n"
    "- adoption_friction → 采纳摩擦\n"
    "- sustained_value → 持续价值\n\n"
    "【Agent 命名规范（严格遵守）】\n"
    "- 所有 Agent 必须使用中文缩略名，**严禁出现 star_xxx / sea_xxx 英文 ID**\n"
    "- 影响者节点格式：「星-XXX」（如「星-咖啡测评师」「星-反营销质疑者」）\n"
    "- 用户群体节点格式：「海-XXX」（如「海-白领跟风者」「海-价格敏感人群」）\n"
    "- 中文名从摘要中的括号描述提取，取前6-10个字作为缩略名即可\n\n"
    "【分析原则】\n"
    "- **用数字说话**：每个论点必须引用具体评分、波次编号、能量值\n"
    "- 区分「促销驱动」与「需求驱动」的行为\n"
    "- 对抖音电商渠道，重点关注复购信号而非首购冲量\n"
    "- 警惕将「算法流量」误读为「市场需求」\n"
)


def _build_scoring_matrix_text(
    grade_a: str, details_a: Dict[str, Any],
    grade_b: str, details_b: Dict[str, Any],
) -> str:
    """构建评分矩阵结构化文本，供 LLM 引用成表。 / Build structured scoring-matrix text for direct LLM table rendering."""
    dims = ["demand_resonance", "propagation_potential",
            "competitive_differentiation", "adoption_friction", "sustained_value"]
    dim_cn = {
        "demand_resonance": "需求共振",
        "propagation_potential": "传播势能",
        "competitive_differentiation": "竞争差异化",
        "adoption_friction": "采纳摩擦",
        "sustained_value": "持续价值",
    }
    roles_cn = {
        "MarketAnalyst": "市场分析师",
        "UserAdvocate": "用户代言人",
        "DevilsAdvocate": "魔鬼代言人",
    }

    lines = ["## 合议庭评分矩阵原始数据（请据此构建对比表格）\n"]

    rs_a = details_a.get("role_scores", {})
    rs_b = details_b.get("role_scores", {})
    da_a = details_a.get("dimension_averages", {})
    da_b = details_b.get("dimension_averages", {})

    lines.append("### 各角色×维度评分（1=极弱 2=弱 3=中等 4=强 5=极强）\n")
    header = "| 维度 |"
    for role in ["MarketAnalyst", "UserAdvocate", "DevilsAdvocate"]:
        header += f" A-{roles_cn[role]} | B-{roles_cn[role]} |"
    header += " A均分 | B均分 | 差值 |"
    lines.append(header)
    lines.append("|" + "---|" * (header.count("|") - 1))

    for dim in dims:
        row = f"| {dim_cn[dim]} |"
        for role in ["MarketAnalyst", "UserAdvocate", "DevilsAdvocate"]:
            va = rs_a.get(role, {}).get(dim, "-")
            vb = rs_b.get(role, {}).get(dim, "-")
            row += f" {va} | {vb} |"
        avg_a = da_a.get(dim, 0)
        avg_b = da_b.get(dim, 0)
        diff = round(avg_a - avg_b, 2)
        sign = "+" if diff > 0 else ""
        row += f" {avg_a} | {avg_b} | {sign}{diff} |"
        lines.append(row)

    oa_a = details_a.get("overall_average", 0)
    oa_b = details_b.get("overall_average", 0)
    diff_all = round(oa_a - oa_b, 2)
    sign = "+" if diff_all > 0 else ""
    lines.append(f"\n### 总体评级")
    lines.append(f"- A组 PMF Grade: **{grade_a}**（总体均分 {oa_a}）")
    lines.append(f"- B组 PMF Grade: **{grade_b}**（总体均分 {oa_b}）")
    lines.append(f"- 差值: {sign}{diff_all}（A组 {'占优' if diff_all > 0 else '落后' if diff_all < 0 else '持平'}）")
    lines.append(f"- 等级标准: ≥4.0=A, ≥3.5=B+, ≥3.0=B, ≥2.5=C+, ≥2.0=C, ≥1.5=D, <1.5=F")

    return "\n".join(lines)


def _build_product_comparison_text() -> str:
    """构建产品多维度对比结构化文本。 / Build structured text for multi-dimensional product comparison."""
    return (
        "## 产品多维度对比原始数据（请据此构建对比表格）\n\n"
        "| 维度 | A组（黑镜·零感） | B组（黑镜·云南） |\n"
        "|---|---|---|\n"
        "| 产品全名 | 黑镜·零感冻干黑咖啡 | 黑镜·云南冻干精品咖啡 |\n"
        "| 品牌 | 黑镜（HEIJING） | 黑镜（HEIJING） |\n"
        "| 品类 | 冻干即溶咖啡 | 冻干即溶咖啡 |\n"
        "| 核心定位 | 真0添加：0糖0脂0卡0代糖 | 云南保山单一产地 SCA 85+ |\n"
        "| 定位心理驱动 | 健康焦虑（对隐性添加的恐惧） | 品质溢价（精品咖啡身份认同） |\n"
        "| 核心差异化卖点 | 配料表仅一行（100%阿拉比卡冻干粉）；0代糖 | 单一产地溯源（庄园编号+海拔）；SCA 85+ |\n"
        "| 视觉锚点 | 配料表占包装正面50%；哑光黑+荧光绿 | 产地明信片+风味轮卡片；哑光黑+大地棕 |\n"
        "| 目标人群 | 25-35岁身材管理/成分透明白领、健身人群 | 25-35岁品质生活白领、精品咖啡入门者 |\n"
        "| 规格 | 2g×10颗迷你罐 | 2g×10颗迷你罐 |\n"
        "| 零售价 | 59.9元/盒（5.99元/杯） | 59.9元/盒（5.99元/杯） |\n"
        "| 抖音首发价 | 39.9元/盒（3.99元/杯），限时7天 | 39.9元/盒（3.99元/杯），限时7天 |\n"
        "| 内容策略方向 | 「配料表只有一行」视觉冲击 + 成分对比 | 产地溯源纪录片风格 + 风味轮解析 |\n"
        "| 主要竞品 | 三顿半（赤藓糖醇调味款40%）、元气森林系列 | 三顿半（拼配为主）、精品咖啡馆零售线 |\n"
        "| 竞争切入角度 | 攻击「伪0糖」（代糖方案） | 占位「精品冻干」空白 |\n\n"
        "### 渠道概况\n"
        "- 平台：抖音电商（算法推荐流 + 直播带货闭环）\n"
        "- 算法特征：完播率/互动率驱动推荐，分钟级反馈调参，流量池赛马晋级\n"
        "- 传播节奏：内容2-4小时内快速扩散，有效生命周期24-48小时\n"
        "- 电商链路：从看到→下单可在几分钟内完成，冲动消费比例高\n"
        "- 核心警惕：需区分「算法冷启动流量」与「市场自发需求」\n"
    )


def _build_ab_comparison_rounds(
    grade_a: str, details_a: Dict[str, Any],
    grade_b: str, details_b: Dict[str, Any],
    peaks_a: Optional[Dict[str, float]] = None,
    peaks_b: Optional[Dict[str, float]] = None,
) -> List[ReportRound]:
    """构建 4 轮 A/B 对比报告规范并注入关键上下文。 / Build 4-round A/B comparison report spec with scoring matrix, product comparison, and peak-energy context."""
    product_text = _build_product_comparison_text()
    scoring_text = _build_scoring_matrix_text(grade_a, details_a, grade_b, details_b)
    energy_text = _build_agent_energy_table(
        peaks_a or {}, peaks_b or {},
    )
    stats_text = format_stats_block(
        HISTORICAL_POSTS,
        metrics=("views", "likes", "comments", "shares", "sales"),
    )
    hist_text = f"\n\n## 品牌历史数据统计（两组共享）\n{stats_text}" if stats_text else ""

    # 所有轮次共享的结构化数据上下文
    full_data_context = (
        product_text + "\n\n" + scoring_text + "\n\n" + energy_text + hist_text
    )

    return [
        # ── 第1轮：测试背景与环境对照 ──
        ReportRound(
            label="测试背景与环境对照",
            system_prompt=_AB_SYSTEM_PREFIX + (
                "当前任务：撰写 A/B 对比报告的 **第一部分：测试背景**。\n"
                "你将收到产品对比原始数据表格和模拟摘要。\n\n"
                "请按以下结构输出（每个小节必须包含至少一个 Markdown 表格）：\n\n"
                "## 一、A/B 测试背景\n\n"
                "### 1.1 测试假设\n"
                "用2-3句话阐述：本次测试验证什么假设？自变量和因变量分别是什么？\n\n"
                "### 1.2 产品多维度对比\n"
                "基于提供的「产品多维度对比原始数据」，输出完整的对比表格。\n"
                "特别标注：哪些维度完全一致（控制变量），哪些维度存在差异（自变量）。\n\n"
                "### 1.3 渠道基本情况\n"
                "简述抖音电商渠道的4-5个核心特征，以及这些特征对A/B测试结果的影响方向。\n\n"
                "### 1.4 模拟环境参数对照\n"
                "输出以下格式的对照表格：\n"
                "| 参数 | A组 | B组 | 是否一致 |\n"
                "包含：波次时间窗口、每波能量衰减率、预估波次数、实际波次数、种子能量值、"
                "影响者节点数量、用户群体节点数量。\n"
                "最后给出一致性判定结论。\n\n"
                "### 1.5 Agent 配置对照\n"
                "分别列出两组的影响者节点（星 Agent）和用户群体节点（海 Agent）对照表。\n"
                "格式：| 功能位 | A组 | B组 |，用中文缩略名。\n"
                "分析两组 Agent 配置的相似度和差异点。\n"
            ),
            extra_user_context=full_data_context,
        ),

        # ── 第2轮：传播动力学对比 ──
        ReportRound(
            label="传播动力学对比分析",
            system_prompt=_AB_SYSTEM_PREFIX + (
                "当前任务：撰写 A/B 对比报告的 **第二部分：传播过程数据对比**。\n\n"
                "请按以下结构输出：\n\n"
                "## 二、传播动力学对比\n\n"
                "### 2.1 传播曲线形态对比\n"
                "输出表格：\n"
                "| 指标 | A组 | B组 | 解读 |\n"
                "包含：曲线类型（脉冲型/衰减型等）、实际波次数、传播终止原因、"
                "峰值出现时段、衰减拐点、能量衰减速率。\n\n"
                "### 2.2 关键节点时间线对比\n"
                "输出表格：\n"
                "| 时段 | A组事件 | B组事件 |\n"
                "按时间线逐段对比两组的关键传播事件。\n\n"
                "### 2.3 Agent 响应模式对比\n"
                "输出两个表格（影响者节点 + 用户群体节点），每个表格包含：\n"
                "| Agent（中文名） | A组主要行为 | A组峰值能量 | B组主要行为 | B组峰值能量 |\n"
                "用中文缩略名，列出各节点的典型响应模式（吸收/评论/变异/原创/忽略）和能量趋势。\n"
                "**峰值能量必须从提供的「Agent 峰值能量原始数据」表格中精确引用，不得写「未提供」**。\n"
                "若该 Agent 仅在一组中出现，另一组标记为「—」。\n\n"
                "### 2.4 传播差异总结\n"
                "用3-5条结论总结最重要的传播差异，**每条都必须引用具体数字**。\n"
            ),
            extra_user_context=full_data_context,
        ),

        # ── 第3轮：PMF评级与信号对比 ──
        ReportRound(
            label="PMF评级与信号深度对比",
            system_prompt=_AB_SYSTEM_PREFIX + (
                "当前任务：撰写 A/B 对比报告的 **第三部分：PMF 评分与信号分析**。\n"
                "你将收到完整的合议庭评分矩阵原始数据。\n\n"
                "请按以下结构输出：\n\n"
                "## 三、PMF 评分矩阵与信号分析\n\n"
                "### 3.1 合议庭评分矩阵\n"
                "基于提供的「合议庭评分矩阵原始数据」，输出完整的对比表格（保留所有角色评分和均分）。\n"
                "表格下方附总体评级对比（A组 Grade vs B组 Grade）。\n\n"
                "### 3.2 五维度逐项解读\n"
                "逐维度（需求共振、传播势能、竞争差异化、采纳摩擦、持续价值）输出：\n"
                "- 哪组占优（引用具体分数）\n"
                "- 该维度差异的根因（引用具体波次和 Agent 行为证据）\n\n"
                "### 3.3 PMF 信号分类对比表\n"
                "输出表格：\n"
                "| 信号类型 | A组（具体现象） | B组（具体现象） | 判定 |\n"
                "分三行：强PMF信号、弱PMF信号、伪PMF信号。\n"
                "每个单元格必须列举具体的 Agent 行为和波次编号作为证据。\n\n"
                "### 3.4 核心差异解读\n"
                "回答4个关键问题（每个50-100字，必须引用数字）：\n"
                "1. 哪组的 PMF 信号更「真实」（非促销/非算法驱动）？\n"
                "2. 哪组的复购潜力更强？\n"
                "3. 哪组更容易产生用户自发传播（UGC）？\n"
                "4. 哪种定位与抖音算法推荐逻辑更契合？\n"
            ),
            extra_user_context=full_data_context,
        ),

        # ── 第4轮：战略结论与成本效益 ──
        ReportRound(
            label="战略结论与成本效益分析",
            system_prompt=_AB_SYSTEM_PREFIX + (
                "当前任务：撰写 A/B 对比报告的 **最后部分：结论与建议**。\n\n"
                "请按以下结构输出：\n\n"
                "## 四、A/B 测试结论与战略建议\n\n"
                "### 4.1 测试结论\n"
                "输出结论表格：\n"
                "| 维度 | A组得分/表现 | B组得分/表现 | 胜出方 |\n"
                "覆盖：PMF等级、总体均分、传播持续性、信号真实度、复购潜力、UGC潜力。\n"
                "最后用1-2句话给出 **明确的总结论**：哪组胜出（或无显著差异），差异显著程度。\n\n"
                "### 4.2 A组定位策略建议（若选择健康焦虑路线）\n"
                "给出5条具体可执行建议，每条附预期量化目标或指标方向。\n"
                "涵盖：内容策略、达人组合、投放节奏、风险控制、复购机制。\n\n"
                "### 4.3 B组定位策略建议（若选择品质溯源路线）\n"
                "结构同上。\n\n"
                "### 4.4 组合策略可行性\n"
                "分析能否融合两种定位。给出具体执行方案或否定理由。\n\n"
                "### 4.5 成本效益对比\n"
                "输出表格：\n"
                "| 维度 | AI 模拟 A/B 测试 | 传统真实投放 A/B 测试 | 差异倍数 |\n"
                "覆盖：费用（元）、周期、可迭代次数、样本覆盖、数据颗粒度、局限性。\n\n"
                "### 4.6 下一步验证路径\n"
                "给出3步递进路线：AI模拟→小规模验证→全量推广，每步附预算和周期估算。\n"
            ),
            extra_user_context=full_data_context,
        ),
    ]


# =============================================================================
# 模拟运行器 / Simulation runners
# =============================================================================

async def run_a(waves: int) -> Dict[str, Any]:
    """运行 A 组模拟（黑镜·零感）。 / Run Group A simulation (HEIJING Zero positioning)."""
    print()
    print("━" * 70)
    print("  🅰️  A组 PMF 验证 — 黑镜·零感（0糖0脂0卡0代糖 · 健康焦虑定位）")
    print("━" * 70)
    return await simulate(
        event=_build_event(PRODUCT_A, "A"),
        skill=SKILL_NAME,
        platform=PLATFORM,
        channel=CHANNEL,
        vertical=VERTICAL,
        source=_build_source(BRAND_ACCOUNT),
        historical=build_historical_from_posts(HISTORICAL_POSTS),
        max_waves=waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=ENSEMBLE_RUNS,
        deliberation_rounds=DELIBERATION_ROUNDS,
    )


async def run_b(waves: int) -> Dict[str, Any]:
    """运行 B 组模拟（黑镜·云南）。 / Run Group B simulation (HEIJING Yunnan positioning)."""
    print()
    print("━" * 70)
    print("  🅱️  B组 PMF 验证 — 黑镜·云南（云南产地 SCA 85+ · 品质溯源定位）")
    print("━" * 70)
    return await simulate(
        event=_build_event(PRODUCT_B, "B"),
        skill=SKILL_NAME,
        platform=PLATFORM,
        channel=CHANNEL,
        vertical=VERTICAL,
        source=_build_source(BRAND_ACCOUNT),
        historical=build_historical_from_posts(HISTORICAL_POSTS),
        max_waves=waves,
        max_llm_calls=MAX_LLM_CALLS,
        config_file=config_file_path(),
        on_progress=print_progress,
        simulation_horizon=f"{SIMULATION_HOURS}h",
        ensemble_runs=ENSEMBLE_RUNS,
        deliberation_rounds=DELIBERATION_ROUNDS,
    )


# =============================================================================
# PMF 等级计算：从 JSON 完整日志的合议庭结构化数据中提取
# =============================================================================

# 评分到等级映射（1-5 量表均值）
_GRADE_THRESHOLDS: List[Tuple[float, str]] = [
    (4.0, "A"), (3.5, "B+"), (3.0, "B"), (2.5, "C+"),
    (2.0, "C"), (1.5, "D"), (0.0, "F"),
]


def _compute_grade(avg: float) -> str:
    """将均分映射为字母等级。"""
    for threshold, grade in _GRADE_THRESHOLDS:
        if avg >= threshold:
            return grade
    return "F"


def extract_pmf_grade(md_path: str) -> Tuple[str, Dict[str, Any]]:
    """从 JSON 完整日志中提取合议庭评分并计算 PMF Grade。

    通过 MD 路径推导同名 .json 路径，读取
    process.deliberation.deliberation_summary.final_positions 中
    各角色的五维评分，计算维度均分和总体均分后映射为等级。

    返回 (grade_str, details_dict)。
    """
    json_path = Path(md_path).with_suffix(".json")
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return "N/A", {}

    positions = (
        data.get("process", {})
        .get("deliberation", {})
        .get("deliberation_summary", {})
        .get("final_positions", [])
    )
    if not positions:
        return "N/A", {}

    role_scores: Dict[str, Dict[str, int]] = {}
    for pos in positions:
        role = pos.get("member_role", "")
        scores = pos.get("scores", {})
        if role and scores:
            role_scores[role] = {k: int(v) for k, v in scores.items()}

    if not role_scores:
        return "N/A", {}

    # 各维度跨角色均分
    all_dims: Dict[str, List[int]] = {}
    for scores in role_scores.values():
        for dim, val in scores.items():
            all_dims.setdefault(dim, []).append(val)

    dim_avgs = {d: round(sum(v) / len(v), 2) for d, v in all_dims.items()}
    all_values = [v for scores in role_scores.values() for v in scores.values()]
    overall_avg = round(sum(all_values) / len(all_values), 2) if all_values else 0.0
    grade = _compute_grade(overall_avg)

    return grade, {
        "role_scores": role_scores,
        "dimension_averages": dim_avgs,
        "overall_average": overall_avg,
    }


# =============================================================================
# MD 日志压缩：程序化抽取关键段并压缩 WAVES / MD log compression via key-section extraction
# =============================================================================

def _condense_md_for_comparison(md_text: str) -> str:
    """将完整 MD 日志压缩至约 15KB。 / Condense full MD log to ~15KB while preserving critical context.

    策略：非 WAVES 段原样保留；WAVES 段保留 W0、等间隔采样与最后一轮（仅 obs 行）。 / Strategy: keep non-WAVES sections intact; sample W0 + interval waves + last wave (obs only).
    """
    lines = md_text.splitlines()

    sections: Dict[str, List[str]] = {}
    current_section = "_header"
    sections[current_section] = []

    for line in lines:
        if line.startswith("### "):
            current_section = line.strip()
            sections[current_section] = []
        else:
            sections.setdefault(current_section, []).append(line)

    # 找到 WAVES 段的 key
    waves_key = None
    for key in sections:
        if key.startswith("### WAVES"):
            waves_key = key
            break

    # 压缩 WAVES 段
    if waves_key and waves_key in sections:
        wave_lines = sections[waves_key]
        # 解析所有 wave 块
        wave_blocks: List[Tuple[int, str, List[str]]] = []
        current_wave_num = -1
        current_wave_header = ""
        current_wave_lines: List[str] = []

        for wl in wave_lines:
            wave_match = re.match(r"^(W(\d+)\s+T=.*)$", wl)
            if wave_match:
                if current_wave_num >= 0:
                    wave_blocks.append(
                        (current_wave_num, current_wave_header, current_wave_lines)
                    )
                current_wave_num = int(wave_match.group(2))
                current_wave_header = wave_match.group(1)
                current_wave_lines = []
            else:
                current_wave_lines.append(wl)

        if current_wave_num >= 0:
            wave_blocks.append(
                (current_wave_num, current_wave_header, current_wave_lines)
            )

        total = len(wave_blocks)
        if total <= 8:
            sample_indices = set(range(total))
        else:
            # W0 + 固定步长采样 + 最后一轮 / W0 + fixed-interval samples + last wave
            step = max(1, total // 6)
            sample_indices = {0} | set(range(0, total, step)) | {total - 1}

        condensed_waves: List[str] = [f"（共 {total} 轮 wave，以下为采样摘要）"]
        for idx in sorted(sample_indices):
            if idx >= len(wave_blocks):
                continue
            wnum, wheader, wlines = wave_blocks[idx]
            condensed_waves.append(wheader)
            for wl in wlines:
                stripped = wl.strip()
                # 保留 obs / 响应汇总行（>agent）、跳过 +agent/-agent 的详细理由
                if stripped.startswith("obs:") or stripped.startswith(">"):
                    condensed_waves.append(wl)

        sections[waves_key] = condensed_waves

    # 重组输出
    out_lines: List[str] = []
    for key in ["_header"] + [k for k in sections if k != "_header"]:
        if key not in sections:
            continue
        if key != "_header":
            out_lines.append(key)
        out_lines.extend(sections[key])

    return "\n".join(out_lines)


# =============================================================================
# 程序化提取 Agent 峰值能量（从 JSON 完整日志的结构化数据中提取）
# =============================================================================


def _extract_agent_peak_energies(json_path: str) -> Dict[str, float]:
    """从 JSON 完整日志中提取各 Agent 的峰值 outgoing_energy。

    遍历 process.waves[*].agent_responses，取各 Agent 在所有波次中
    outgoing_energy 的最大值，返回 {agent_id: max_energy}。
    """
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    peaks: Dict[str, float] = {}
    for wave in data.get("process", {}).get("waves", []):
        resps = wave.get("agent_responses", {})
        if not isinstance(resps, dict):
            continue
        for aid, info in resps.items():
            if not isinstance(info, dict):
                continue
            e = info.get("outgoing_energy")
            if isinstance(e, (int, float)) and e > peaks.get(aid, 0.0):
                peaks[aid] = e
    return peaks


def _build_agent_energy_table(
    peaks_a: Dict[str, float],
    peaks_b: Dict[str, float],
) -> str:
    """构建两组 Agent 峰值能量对照表文本。 / Build side-by-side peak-energy table for two groups."""
    all_agents = sorted(set(peaks_a) | set(peaks_b))
    stars = [a for a in all_agents if a.startswith("star_")]
    seas = [a for a in all_agents if a.startswith("sea_")]

    lines = ["## Agent 峰值能量原始数据（程序化从全量波次中提取）\n"]

    lines.append("### 影响者节点（Star Agent）峰值能量\n")
    lines.append("| Agent ID | A组峰值能量 | B组峰值能量 |")
    lines.append("|---|---:|---:|")
    for a in stars:
        va = f"{peaks_a[a]:.2f}" if a in peaks_a else "—"
        vb = f"{peaks_b[a]:.2f}" if a in peaks_b else "—"
        lines.append(f"| {a} | {va} | {vb} |")

    lines.append("\n### 用户群体节点（Sea Agent）峰值能量\n")
    lines.append("| Agent ID | A组峰值能量 | B组峰值能量 |")
    lines.append("|---|---:|---:|")
    for a in seas:
        va = f"{peaks_a[a]:.2f}" if a in peaks_a else "—"
        vb = f"{peaks_b[a]:.2f}" if a in peaks_b else "—"
        lines.append(f"| {a} | {va} | {vb} |")

    lines.append(
        "\n> 注意：上述 Agent ID 在报告正文中应使用中文缩略名"
        "（如 star_cleanlabel_nutrition_kol → 「星-成分营养科普」）。"
    )
    return "\n".join(lines)


# =============================================================================
# LLM 预处理：单组日志结构化摘要提取 / LLM preprocessing for single-group structured summary
# =============================================================================

_PREPROCESS_SYSTEM = (
    "你是 Ripple CAS PMF 验证引擎的数据分析员。\n"
    "你的任务是从一组模拟日志中提取结构化的关键数据摘要，供后续 A/B 对比分析使用。\n\n"
    "请严格按以下结构输出，不要添加任何额外内容：\n\n"
    "【产品与定位】一句话概述产品名称和核心定位策略\n"
    "【Agent 配置】Star Agent 数量和类型列表（一行一个，附括号中的中文角色描述），"
    "Sea Agent 数量和类型列表（同上格式）\n"
    "【传播参数】wave_time_window、energy_decay_per_wave、预估/实际波次数、种子能量\n"
    "【传播模式摘要】3-5句话概述传播曲线形态、关键转折点、主导传播路径\n"
    "【Agent 响应模式】逐 Agent 列出：\n"
    "  - Agent ID + 中文角色名（如 star_xxx「星-XX达人」）\n"
    "  - 主要行为模式（absorb/comment/mutate/create/ignore）\n"
    "  - **峰值能量数值**（如 peak E=0.46）和末期能量数值\n"
    "  - 行为转变节点（如 W9 从 comment 转 ignore）\n"
    "【合议庭评分】逐角色列出五维评分（demand_resonance / propagation_potential / "
    "competitive_differentiation / adoption_friction / sustained_value），标注收敛状态\n"
    "【预测结论】方向（rise/stable/decline）和一句话摘要\n"
    "【关键时间线】逐条列出 TIMELINE 中的节点\n"
    "【分叉点】逐条列出 BIFURCATION 中的节点和可能路径\n"
    "【Agent 洞察】逐 Agent 列出核心洞察和建议行动（一行一个）\n"
)


async def _preprocess_single_log(
    condensed_log: str,
    group_label: str,
    router: ModelRouter,
) -> Optional[str]:
    """用 LLM 提取单组压缩日志结构化摘要。 / Use LLM to extract structured summary from a condensed single-group log."""
    logger.info("预处理 %s 组日志（LLM 结构化摘要）...", group_label)
    user_msg = f"以下是{group_label}组的模拟日志数据：\n\n{condensed_log}"
    try:
        return await call_llm(router, "omniscient", _PREPROCESS_SYSTEM, user_msg)
    except Exception as exc:
        logger.warning("%s 组预处理失败: %s", group_label, exc)
        return None


# =============================================================================
# A/B 对比报告生成器（三阶段：压缩→预处理→对比） / A/B comparison report generator (3-stage pipeline)
# =============================================================================

async def generate_ab_comparison_report(
    md_path_a: str,
    md_path_b: str,
    config_file: str,
    grade_a: str,
    details_a: Dict[str, Any],
    grade_b: str,
    details_b: Dict[str, Any],
    role: str = "omniscient",
    max_llm_calls: int = 20,
) -> Optional[str]:
    """三阶段生成 A/B 对比报告。 / Generate A/B comparison report in three stages.

    1) 程序化压缩；2) LLM 预处理；3) 注入评分矩阵后做四轮深度对比。 / 1) Programmatic compression; 2) LLM preprocessing; 3) Four-round deep comparison with scoring matrix context.
    """
    # 阶段零：从 JSON 完整日志中提取 Agent 峰值能量
    json_path_a = str(Path(md_path_a).with_suffix(".json"))
    json_path_b = str(Path(md_path_b).with_suffix(".json"))
    peaks_a: Dict[str, float] = {}
    peaks_b: Dict[str, float] = {}
    try:
        peaks_a = _extract_agent_peak_energies(json_path_a)
        peaks_b = _extract_agent_peak_energies(json_path_b)
        logger.info(
            "Agent 峰值能量提取完成（JSON）: A组 %d 个节点, B组 %d 个节点",
            len(peaks_a), len(peaks_b),
        )
    except Exception as exc:
        logger.warning("JSON 峰值能量提取失败（将在报告中缺省）: %s", exc)

    # 构建带评分 + 峰值能量数据的报告轮次
    rounds = _build_ab_comparison_rounds(
        grade_a, details_a, grade_b, details_b, peaks_a, peaks_b,
    )

    # 阶段一：程序化压缩 MD 日志
    try:
        raw_a = Path(md_path_a).read_text(encoding="utf-8")
        raw_b = Path(md_path_b).read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("读取 MD 文件失败: %s", exc)
        return None

    condensed_a = _condense_md_for_comparison(raw_a)
    condensed_b = _condense_md_for_comparison(raw_b)
    logger.info(
        "日志压缩完成: A组 %dKB→%dKB, B组 %dKB→%dKB",
        len(raw_a) // 1024, len(condensed_a) // 1024,
        len(raw_b) // 1024, len(condensed_b) // 1024,
    )

    try:
        router = ModelRouter(config_file=config_file, max_llm_calls=max_llm_calls)
    except Exception as exc:
        logger.warning("创建 LLM 路由器失败: %s", exc)
        return None

    # 阶段二：LLM 预处理（分别对每组做结构化摘要）
    print("  ▶ 阶段一：预处理A组日志...")
    summary_a = await _preprocess_single_log(condensed_a, "A", router)
    print("  ▶ 阶段二：预处理B组日志...")
    summary_b = await _preprocess_single_log(condensed_b, "B", router)

    if not summary_a or not summary_b:
        logger.warning("预处理阶段失败，尝试直接使用压缩日志进行对比")
        summary_a = summary_a or condensed_a
        summary_b = summary_b or condensed_b

    # 阶段三：合并两组摘要，进行 4 轮对比分析
    combined = (
        "═" * 40 + "\n"
        "A组结构化摘要（黑镜·零感 — 0糖0脂0卡0代糖 · 健康焦虑定位）\n"
        "═" * 40 + "\n\n"
        f"{summary_a}\n\n"
        "═" * 40 + "\n"
        "B组结构化摘要（黑镜·云南 — 云南产地 SCA 85+ · 品质溯源定位）\n"
        "═" * 40 + "\n\n"
        f"{summary_b}"
    )

    parts: List[str] = []
    for i, rd in enumerate(rounds, 1):
        print(f"  ▶ 对比分析第 {i}/{len(rounds)} 轮：{rd.label}")
        logger.info("A/B对比报告 — 第 %d/%d 轮：%s", i, len(rounds), rd.label)
        user_msg = combined
        if rd.extra_user_context:
            user_msg += "\n\n" + rd.extra_user_context
        try:
            text = await call_llm(router, role, rd.system_prompt, user_msg)
            if text:
                parts.append(text)
        except Exception as exc:
            logger.warning("第%d轮对比分析失败: %s", i, exc)

    return "\n\n" + ("─" * 40 + "\n\n").join(parts) if parts else None


def _save_ab_report(
    report: str,
    md_path_a: str,
    md_path_b: str,
    grade_a: str = "N/A",
    grade_b: str = "N/A",
) -> Optional[str]:
    """将 A/B 对比报告保存到 `ripple_outputs/`。 / Save A/B comparison report to `ripple_outputs/`."""
    output_dir = REPO_ROOT / "ripple_outputs"
    output_dir.mkdir(exist_ok=True)

    # 从文件名中提取 run_id
    run_id_a = Path(md_path_a).stem.split("_")[-1] if md_path_a else "unknown"
    run_id_b = Path(md_path_b).stem.split("_")[-1] if md_path_b else "unknown"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_ab_compare_{run_id_a}_vs_{run_id_b}.md"
    filepath = output_dir / filename

    header = (
        f"# A/B 测试对比报告：冻干咖啡定位策略 PMF 验证\n\n"
        f"- 生成时间：{datetime.now().isoformat()}\n"
        f"- A组 run_id：{run_id_a}（PMF Grade: {grade_a}）\n"
        f"- B组 run_id：{run_id_b}（PMF Grade: {grade_b}）\n"
        f"- A组产品：黑镜·零感（0糖0脂0卡0代糖 · 健康焦虑定位）\n"
        f"- B组产品：黑镜·云南（云南产地 SCA 85+ · 品质溯源定位）\n"
        f"- 模拟平台：抖音电商（算法推荐流 + 直播带货）\n"
        f"- 模拟时长：{SIMULATION_HOURS}小时\n\n"
        f"## 数据源引用\n\n"
        f"- A组精简日志：{md_path_a}\n"
        f"- B组精简日志：{md_path_b}\n\n"
        f"---\n\n"
    )

    filepath.write_text(header + report, encoding="utf-8")
    return str(filepath)


# =============================================================================
# A/B 对比流程入口（ab 与 compare 共用） / A/B comparison entry (shared by `ab` and `compare`)
# =============================================================================

async def run_comparison(
    md_path_a: str,
    md_path_b: str,
    config_file: Optional[str],
    no_report: bool = False,
) -> None:
    """基于两个既有 .md 文件执行 A/B 对比流程。 / Run A/B comparison workflow from two existing .md files."""

    # 解析 PMF Grade
    grade_a, details_a = extract_pmf_grade(md_path_a)
    grade_b, details_b = extract_pmf_grade(md_path_b)

    # 打印评级速览
    print()
    print("═" * 70)
    print("  A/B 测试 — PMF 评级速览")
    print("═" * 70)
    print(f"  A组（黑镜·零感 / 健康焦虑定位）: {grade_a}")
    if details_a.get("dimension_averages"):
        dims = details_a["dimension_averages"]
        print(f"       维度均分: {' | '.join(f'{k}={v}' for k, v in dims.items())}")
        print(f"       总体均分: {details_a.get('overall_average', 'N/A')}")
    print(f"  B组（黑镜·云南 / 品质溯源定位）: {grade_b}")
    if details_b.get("dimension_averages"):
        dims = details_b["dimension_averages"]
        print(f"       维度均分: {' | '.join(f'{k}={v}' for k, v in dims.items())}")
        print(f"       总体均分: {details_b.get('overall_average', 'N/A')}")
    print("═" * 70)

    # 生成 A/B 对比报告
    if not no_report and config_file:
        print()
        print("━" * 70)
        print("  正在生成 A/B 对比分析报告（预处理 + 4轮深度对比）...")
        print("━" * 70)

        report = await generate_ab_comparison_report(
            md_path_a, md_path_b, config_file,
            grade_a, details_a, grade_b, details_b,
        )
        if report:
            print()
            print("═" * 70)
            print("  A/B 测试 — 深度对比分析报告")
            print("═" * 70)
            print(report)
            print("═" * 70)

            report_path = _save_ab_report(
                report, md_path_a, md_path_b, grade_a, grade_b,
            )
            if report_path:
                print(f"\n  对比报告已保存至：{report_path}")
        else:
            print("\n  ⚠ A/B 对比报告生成失败，请检查 llm_config.yaml。")


# =============================================================================
# 主函数 / Main
# =============================================================================

_EXTRA_SUMMARY = {
    "ensemble_runs": ENSEMBLE_RUNS,
    "deliberation_rounds": DELIBERATION_ROUNDS,
}


async def main() -> None:
    parser = create_arg_parser(
        "Ripple A/B 测试 — 冻干咖啡定位策略 × 抖音电商 PMF 验证（72h）",
        modes=("a", "b", "ab", "compare"),
        default_waves=DEFAULT_WAVES,
    )
    # compare 模式专用参数：直接传入既有 .md 文件路径 / Compare-mode args: pass existing .md file paths directly
    parser.add_argument(
        "--file-a",
        type=str,
        default=None,
        help="A组模拟结果 .md 文件路径（compare 模式必填）",
    )
    parser.add_argument(
        "--file-b",
        type=str,
        default=None,
        help="B组模拟结果 .md 文件路径（compare 模式必填）",
    )
    args = parser.parse_args()
    waves = args.waves
    cfg = config_file_path()
    no_report = args.no_report

    # ── compare 模式：直接从已有 MD 文件生成对比报告 ──
    if args.mode == "compare":
        if not args.file_a or not args.file_b:
            parser.error("compare 模式需要同时提供 --file-a 和 --file-b 参数")
        if not Path(args.file_a).exists():
            parser.error(f"A组文件不存在: {args.file_a}")
        if not Path(args.file_b).exists():
            parser.error(f"B组文件不存在: {args.file_b}")

        print()
        print("━" * 70)
        print("  A/B 对比模式 — 从已有模拟结果生成对比报告")
        print(f"  A组文件: {args.file_a}")
        print(f"  B组文件: {args.file_b}")
        print("━" * 70)

        await run_comparison(args.file_a, args.file_b, cfg, no_report)
        return

    # ── A组单独运行 ──
    if args.mode == "a":
        result_a = await run_and_interpret(
            "A组 PMF 验证（黑镜·零感）",
            run_a(waves),
            cfg,
            report_rounds=_build_individual_report_rounds(),
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=no_report,
        )
        md_path = result_a.get("compact_log_file")
        if md_path:
            grade, details = extract_pmf_grade(md_path)
            print(f"\n  A组 PMF Grade: {grade} (均分 {details.get('overall_average', 'N/A')})")

    # ── B组单独运行 ──
    elif args.mode == "b":
        result_b = await run_and_interpret(
            "B组 PMF 验证（黑镜·云南）",
            run_b(waves),
            cfg,
            report_rounds=_build_individual_report_rounds(),
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=no_report,
        )
        md_path = result_b.get("compact_log_file")
        if md_path:
            grade, details = extract_pmf_grade(md_path)
            print(f"\n  B组 PMF Grade: {grade} (均分 {details.get('overall_average', 'N/A')})")

    # ── A/B 双组运行 + 对比报告 ──
    elif args.mode == "ab":
        result_a = await run_and_interpret(
            "A组 PMF 验证（黑镜·零感）",
            run_a(waves),
            cfg,
            report_rounds=None,
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=True,
        )
        result_b = await run_and_interpret(
            "B组 PMF 验证（黑镜·云南）",
            run_b(waves),
            cfg,
            report_rounds=None,
            extra_summary_fields=_EXTRA_SUMMARY,
            no_report=True,
        )

        md_a = result_a.get("compact_log_file", "")
        md_b = result_b.get("compact_log_file", "")
        if md_a and md_b:
            await run_comparison(md_a, md_b, cfg, no_report)
        else:
            print("\n  ⚠ 模拟输出文件缺失，无法生成对比报告。")


if __name__ == "__main__":
    asyncio.run(main())
