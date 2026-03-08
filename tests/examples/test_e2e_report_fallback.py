from __future__ import annotations

import sys
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import e2e_helpers as helpers


@pytest.mark.asyncio
async def test_generate_report_falls_back_without_local_ripple(monkeypatch, tmp_path: Path) -> None:
    compact_log = tmp_path / "demo.md"
    compact_log.write_text("demo compact log", encoding="utf-8")

    async def fake_call(config_data, role, system_prompt, user_message):
        assert config_data["_default"]["model_name"] == "demo-model"
        assert role == "omniscient"
        assert user_message == "demo compact log"
        return "报告正文"

    monkeypatch.setattr(helpers, "_load_model_router_class", lambda: None)
    monkeypatch.setattr(
        helpers,
        "_load_report_config_data",
        lambda _config_file: {
            "_default": {
                "model_platform": "openai",
                "model_name": "demo-model",
                "api_key": "sk-demo",
                "url": "https://example.test/v1",
                "api_mode": "responses",
            }
        },
    )
    monkeypatch.setattr(helpers, "_call_llm_from_config_data", fake_call)

    result = {
        "compact_log_file": str(compact_log),
    }
    rounds = [helpers.ReportRound(label="r1", system_prompt="sys")]

    report = await helpers.generate_report(result, None, rounds)

    assert report == "报告正文"
