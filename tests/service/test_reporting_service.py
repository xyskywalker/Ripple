from __future__ import annotations

import json
from pathlib import Path

import pytest

from ripple.service.reporting import (
    build_skill_report_profile,
    generate_report_from_result,
    load_output_json_document,
)


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


def test_build_skill_report_profile_loads_rounds_and_injects_request_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_dir = tmp_path / "skills" / "demo-skill"
    prompts_dir = skill_dir / "prompts"
    reports_dir = skill_dir / "reports"
    platforms_dir = skill_dir / "platforms"
    prompts_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    platforms_dir.mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "version: \"0.1.0\"\n"
        "description: demo\n"
        "prompts:\n"
        "  omniscient: prompts/omniscient.md\n"
        "  star: prompts/star.md\n"
        "  sea: prompts/sea.md\n"
        "domain_profile: domain-profile.md\n"
        "---\n",
        encoding="utf-8",
    )
    (skill_dir / "domain-profile.md").write_text("demo profile", encoding="utf-8")
    (prompts_dir / "omniscient.md").write_text("omniscient", encoding="utf-8")
    (prompts_dir / "star.md").write_text("star", encoding="utf-8")
    (prompts_dir / "sea.md").write_text("sea", encoding="utf-8")
    (platforms_dir / "generic.md").write_text("generic", encoding="utf-8")
    (reports_dir / "default.yaml").write_text(
        "description: demo report\n"
        "role: omniscient\n"
        "max_llm_calls: 5\n"
        "system_prefix: 统一前缀\n"
        "rounds:\n"
        "  - label: 第一轮\n"
        "    system_prompt: 输出中文总结\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    profile = build_skill_report_profile(
        request={
            "skill": "demo-skill",
            "platform": "generic",
            "event": {"title": "测试标题", "summary": "测试摘要"},
            "source": {"summary": "账号画像摘要"},
            "historical": [{"views": 100, "likes": 10}],
        }
    )

    assert profile.role == "omniscient"
    assert profile.max_llm_calls == 5
    assert len(profile.rounds) == 1
    assert "统一前缀" in profile.rounds[0].system_prompt
    assert "测试标题" in profile.rounds[0].extra_user_context
    assert "账号画像摘要" in profile.rounds[0].extra_user_context
