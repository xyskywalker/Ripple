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
from typing import Any, Dict

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
from e2e_xiaohongshu_common import (
    DEFAULT_WAVES,
    MAX_LLM_CALLS,
    PLATFORM,
    SAMPLE_ACCOUNT,
    SAMPLE_POSTS,
    SAMPLE_TOPIC,
    SIMULATION_HOURS,
    build_report_rounds,
)

setup_logging()
logger = logging.getLogger(__name__)


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
            report_rounds=build_report_rounds(),
            no_report=no_report,
        )

    if args.mode in ("enhanced", "all"):
        await run_and_interpret(
            "增强模拟",
            run_enhanced(waves),
            cfg,
            report_rounds=build_report_rounds(SAMPLE_ACCOUNT, SAMPLE_POSTS),
            no_report=no_report,
        )


if __name__ == "__main__":
    asyncio.run(main())
