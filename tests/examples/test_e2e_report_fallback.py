from __future__ import annotations

import sys
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import e2e_helpers as helpers


@pytest.mark.asyncio
async def test_generate_report_delegates_to_shared_core_module(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    async def fake_generate_report_from_result(**kwargs):
        captured.update(kwargs)
        return "报告正文"

    monkeypatch.setattr("ripple.reporting.generate_report_from_result", fake_generate_report_from_result)

    result = {"compact_log_file": str(tmp_path / "demo.md")}
    rounds = [helpers.ReportRound(label="r1", system_prompt="sys")]

    report = await helpers.generate_report(result, "/tmp/llm_config.yaml", rounds, role="omniscient", max_llm_calls=7)

    assert report == "报告正文"
    assert captured["result"] == result
    assert captured["config_file"] == "/tmp/llm_config.yaml"
    assert captured["max_llm_calls"] == 7
