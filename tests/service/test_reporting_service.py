from __future__ import annotations

import json
from pathlib import Path

import pytest

from ripple.service.reporting import generate_report_from_result, load_output_json_document


class _FakeAdapter:
    def __init__(self, expected_user_message: str):
        self._expected_user_message = expected_user_message

    async def call(self, system_prompt: str, user_message: str) -> str:
        assert system_prompt == "sys"
        assert user_message == self._expected_user_message
        return "报告正文"


@pytest.mark.asyncio
async def test_generate_report_uses_request_llm_config_before_default_file(monkeypatch, tmp_path: Path) -> None:
    compact_log = tmp_path / "demo.md"
    compact_log.write_text("demo compact log", encoding="utf-8")
    captured: dict = {}

    class _FakeRouter:
        def __init__(self, llm_config=None, max_llm_calls=200, config_file=None):
            captured["llm_config"] = llm_config
            captured["max_llm_calls"] = max_llm_calls
            captured["config_file"] = config_file

        def get_model_backend(self, role: str):
            captured["role"] = role
            return _FakeAdapter("demo compact log")

    monkeypatch.setattr("ripple.service.reporting.ModelRouter", _FakeRouter)

    report = await generate_report_from_result(
        result={"compact_log_file": str(compact_log)},
        rounds=[{"label": "r1", "system_prompt": "sys", "extra_user_context": ""}],
        role="omniscient",
        max_llm_calls=12,
        config_file="/app/llm_config.yaml",
        llm_config={"_default": {"model_name": "demo-model"}},
    )

    assert report == "报告正文"
    assert captured == {
        "llm_config": {"_default": {"model_name": "demo-model"}},
        "max_llm_calls": 12,
        "config_file": "/app/llm_config.yaml",
        "role": "omniscient",
    }


@pytest.mark.asyncio
async def test_generate_report_falls_back_to_default_config_file(monkeypatch, tmp_path: Path) -> None:
    compact_log = tmp_path / "demo.md"
    compact_log.write_text("demo compact log", encoding="utf-8")
    captured: dict = {}

    class _FakeRouter:
        def __init__(self, llm_config=None, max_llm_calls=200, config_file=None):
            captured["llm_config"] = llm_config
            captured["max_llm_calls"] = max_llm_calls
            captured["config_file"] = config_file

        def get_model_backend(self, role: str):
            captured["role"] = role
            return _FakeAdapter("demo compact log")

    monkeypatch.setattr("ripple.service.reporting.ModelRouter", _FakeRouter)

    report = await generate_report_from_result(
        result={"compact_log_file": str(compact_log)},
        rounds=[{"label": "r1", "system_prompt": "sys", "extra_user_context": ""}],
        role="omniscient",
        max_llm_calls=8,
        config_file="/app/llm_config.yaml",
        llm_config=None,
    )

    assert report == "报告正文"
    assert captured == {
        "llm_config": None,
        "max_llm_calls": 8,
        "config_file": "/app/llm_config.yaml",
        "role": "omniscient",
    }


def test_load_output_json_document_reads_json_file(tmp_path: Path) -> None:
    output_json = tmp_path / "demo.json"
    output_json.write_text(json.dumps({"prediction": {"impact": "ok"}}), encoding="utf-8")

    document = load_output_json_document({"output_file": str(output_json)})

    assert document == {"prediction": {"impact": "ok"}}
