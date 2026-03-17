from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner
import yaml

from ripple.primitives.events import SimulationEvent
from ripple.service.job_repo_sqlite import JobRepoSQLite


runner = CliRunner()


def _normalize_table_text(text: str) -> str:
    """压平 Rich 表格文本，便于断言折行内容。 / Flatten Rich table output for assertions."""
    return re.sub(r"[\s│┏┓┗┛┡┩┠┨┯┷┳┻╇━─]+", "", text)


def _extract_rich_table_column(text: str, column_index: int) -> str:
    """抽取 Rich 表格中的某一列文本。 / Extract one column from a rendered Rich table."""
    values: list[str] = []
    for line in text.splitlines():
        if "│" not in line:
            continue
        parts = [part.strip() for part in line.split("│")]
        if len(parts) <= column_index + 1:
            continue
        cell = parts[column_index + 1]
        if cell and cell not in {"Job ID", "任务 ID", "状态", "领域", "简述", "产物文件", "创建时间"}:
            values.append(cell)
    return "\n".join(values)


def _write_config(tmp_path: Path) -> Path:
    """写入最小 llm_config。 / Write a minimal llm_config file."""
    path = tmp_path / "llm_config.yaml"
    path.write_text(
        "_default:\n"
        "  model_platform: openai\n"
        "  model_name: demo-model\n"
        "  api_key: sk-demo-value\n"
        "  url: https://example.test/v1\n"
        "  api_mode: responses\n"
        "  temperature: 0.7\n"
        "  max_retries: 3\n",
        encoding="utf-8",
    )
    return path


def _write_skill(tmp_path: Path, name: str = "demo-skill") -> Path:
    """写入最小技能目录。 / Write a minimal skill directory."""
    skill_dir = tmp_path / "skills" / name
    prompts_dir = skill_dir / "prompts"
    platforms_dir = skill_dir / "platforms"
    channels_dir = skill_dir / "channels"
    verticals_dir = skill_dir / "verticals"
    reports_dir = skill_dir / "reports"
    examples_dir = skill_dir / "examples"
    prompts_dir.mkdir(parents=True)
    platforms_dir.mkdir(parents=True)
    channels_dir.mkdir(parents=True)
    verticals_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    examples_dir.mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "version: \"0.1.0\"\n"
        "description: 演示领域技能，用于验证 CLI 的领域展示输出。\n"
        "use_when: 适用于演示场景推演、CLI 调试和 Agent 读取结构验证。\n"
        "platform_labels:\n"
        "  generic: 通用平台\n"
        "channel_labels:\n"
        "  community: 社区渠道\n"
        "vertical_labels:\n"
        "  retail: 零售行业\n"
        "prompts:\n"
        "  omniscient: prompts/omniscient.md\n"
        "  star: prompts/star.md\n"
        "  sea: prompts/sea.md\n"
        "domain_profile: domain-profile.md\n"
        "---\n",
        encoding="utf-8",
    )
    (skill_dir / "domain-profile.md").write_text("demo domain profile", encoding="utf-8")
    (prompts_dir / "omniscient.md").write_text("omniscient prompt", encoding="utf-8")
    (prompts_dir / "star.md").write_text("star prompt", encoding="utf-8")
    (prompts_dir / "sea.md").write_text("sea prompt", encoding="utf-8")
    (platforms_dir / "generic.md").write_text("generic platform profile", encoding="utf-8")
    (channels_dir / "community.md").write_text("community channel profile", encoding="utf-8")
    (verticals_dir / "retail.md").write_text("retail vertical profile", encoding="utf-8")
    (reports_dir / "default.yaml").write_text(
        "role: omniscient\n"
        "max_llm_calls: 4\n"
        "rounds:\n"
        "  - label: 概览\n"
        "    system_prompt: 生成中文报告\n",
        encoding="utf-8",
    )
    (skill_dir / "request-schema.yaml").write_text(
        "summary: 演示领域输入契约，用于帮助 Agent 理解最少字段与可选增强信息。\n"
        "notes:\n"
        "  - 至少提供标题或正文，才能建立最基本的模拟上下文。\n"
        "requirements:\n"
        "  required:\n"
        "    - path: event\n"
        "      kind: object\n"
        "      description: 事件对象必填。\n"
        "    - path: event.seed_text\n"
        "      kind: one_of\n"
        "      any_of:\n"
        "        - event.title\n"
        "        - event.body\n"
        "      description: 标题和正文至少提供一个。\n"
        "  recommended:\n"
        "    - path: platform\n"
        "      kind: selector\n"
        "      description: 建议选择平台画像以提升模拟准确度。\n"
        "sections:\n"
        "  - name: request\n"
        "    title: 请求级参数\n"
        "    description: 控制模拟环境和领域画像注入。\n"
        "    fields:\n"
        "      - path: skill\n"
        "        tier: optional\n"
        "        type: string\n"
        "        description: 领域 skill 名称。\n"
        "        default: demo-skill\n"
        "      - path: platform\n"
        "        tier: recommended\n"
        "        type: enum\n"
        "        description: 平台画像 key。\n"
        "        options_from: platform\n"
        "      - path: channel\n"
        "        tier: optional\n"
        "        type: enum\n"
        "        description: 渠道画像 key。\n"
        "        options_from: channel\n"
        "      - path: vertical\n"
        "        tier: optional\n"
        "        type: enum\n"
        "        description: 垂直行业画像 key。\n"
        "        options_from: vertical\n"
        "  - name: event\n"
        "    title: 事件信息\n"
        "    description: 承载模拟主体内容。\n"
        "    fields:\n"
        "      - path: event.title\n"
        "        tier: required\n"
        "        type: string\n"
        "        description: 事件标题。\n"
        "        example: 演示标题\n"
        "      - path: event.body\n"
        "        tier: recommended\n"
        "        type: string\n"
        "        description: 事件正文。\n"
        "        example: 演示正文\n"
        "  - name: source\n"
        "    title: 发布源信息\n"
        "    description: 描述发布者或账号背景。\n"
        "    fields:\n"
        "      - path: source.author_profile\n"
        "        tier: recommended\n"
        "        type: string\n"
        "        description: 发布者画像。\n"
        "        example: 社区型账号，核心粉丝为零售从业者\n",
        encoding="utf-8",
    )
    (examples_dir / "basic.yaml").write_text(
        "title: 演示基础模拟\n"
        "summary: 仅使用标题、正文和平台的最小示例。\n"
        "use_when: 首次验证 CLI 与领域 schema 是否匹配。\n"
        "tags:\n"
        "  - basic\n"
        "  - quickstart\n"
        "command:\n"
        "  skill: demo-skill\n"
        "  platform: generic\n"
        "  output_path: ./outputs\n"
        "request:\n"
        "  event:\n"
        "    title: 演示标题\n"
        "    body: 演示正文\n"
        "  source:\n"
        "    author_profile: 社区型账号，核心粉丝为零售从业者\n",
        encoding="utf-8",
    )
    (examples_dir / "enhanced.yaml").write_text(
        "title: 演示增强模拟\n"
        "summary: 补充渠道、垂直和历史信息的增强示例。\n"
        "use_when: 需要给 Agent 展示更完整的输入结构时使用。\n"
        "tags:\n"
        "  - enhanced\n"
        "  - agent-friendly\n"
        "command:\n"
        "  skill: demo-skill\n"
        "  platform: generic\n"
        "  channel: community\n"
        "  vertical: retail\n"
        "  output_path: ./outputs\n"
        "  simulation_horizon: 7d\n"
        "request:\n"
        "  event:\n"
        "    title: 增强演示标题\n"
        "    body: 增强演示正文\n"
        "  source:\n"
        "    author_profile: 社区型账号，核心粉丝为零售从业者\n"
        "  historical:\n"
        "    baseline_notes: 过去 30 天社区话题互动率稳定\n",
        encoding="utf-8",
    )
    return skill_dir


def _read_json(result) -> dict:
    """解析 CLI JSON 输出。 / Parse CLI JSON output."""
    return json.loads(result.stdout.strip())


def test_version_json() -> None:
    """version 命令应输出 JSON。 / version command should emit JSON."""
    from ripple.cli.app import app

    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    payload = _read_json(result)
    assert "version" in payload
    assert payload["ok"] is True


def test_help_pages_are_chinese_and_include_examples() -> None:
    """根命令与关键子命令 help 应提供中文说明和示例。 / Help pages should expose Chinese guidance and examples."""
    from ripple.cli.app import app

    root_help = runner.invoke(app, ["--help"])
    assert root_help.exit_code == 0
    assert "Ripple 命令行工具" in root_help.stdout
    assert "查看版本信息" in root_help.stdout
    assert "ripple-cli domain example social-media" in root_help.stdout
    assert "显示帮助并退出" in root_help.stdout

    run_help = runner.invoke(app, ["job", "run", "--help"])
    assert run_help.exit_code == 0
    assert "启动一个模拟任务" in run_help.stdout
    assert "输出根目录" in run_help.stdout
    assert "非阻塞模式" in run_help.stdout
    assert "ripple-cli domain example social-media" in run_help.stdout

    dump_help = runner.invoke(app, ["domain", "dump", "--help"])
    assert dump_help.exit_code == 0
    assert "领域 Skill 名称" in dump_help.stdout
    assert "仅导出某一类文件" in dump_help.stdout
    assert "--section" in dump_help.stdout
    assert "prompts/omniscient.md" in dump_help.stdout
    assert "--json" in dump_help.stdout

    schema_help = runner.invoke(app, ["domain", "schema", "--help"])
    assert schema_help.exit_code == 0
    assert "查看领域输入" in schema_help.stdout
    assert "所有可用领域的" in schema_help.stdout
    assert "Schema" in schema_help.stdout
    assert "ripple-cli domain schema pmf-validation --json" in schema_help.stdout

    example_help = runner.invoke(app, ["domain", "example", "--help"])
    assert example_help.exit_code == 0
    assert "查看领域示例" in example_help.stdout
    assert "不传时返回所有领域的示例索引" in example_help.stdout
    assert "ripple-cli domain example social-media" in example_help.stdout
    assert "pmf-validation --json" in example_help.stdout

    validate_help = runner.invoke(app, ["validate", "--help"])
    assert validate_help.exit_code == 0
    assert "ripple-cli validate --input request.json" in validate_help.stdout
    assert "ripple-cli domain example social-media" in validate_help.stdout

    llm_set_help = runner.invoke(app, ["llm", "set", "--help"], terminal_width=200)
    assert llm_set_help.exit_code == 0
    llm_set_help_text = llm_set_help.stdout.replace("`", "").replace("\n", "")
    assert "openai（包括所有openai兼容模型）" in llm_set_help_text
    assert "anthropic（包括所有anthropic兼容模型）" in llm_set_help_text
    assert "bedrock" in llm_set_help_text
    assert "deepseek" not in llm_set_help_text
    assert "qwen" not in llm_set_help_text


def test_llm_show_masks_api_key(tmp_path: Path) -> None:
    """llm show 应遮盖 API Key。 / llm show should mask the API key."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    result = runner.invoke(app, ["llm", "show", "--json", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["default"]["api_key"].startswith("sk-d")
    assert payload["default"]["api_key"] != "sk-demo-value"


def test_llm_setup_prefills_existing_default_and_preserves_other_sections(tmp_path: Path) -> None:
    """llm setup 应复用已有 _default，并保留其他配置段。 / llm setup should reuse the existing _default values and preserve other sections."""
    from ripple.cli.app import app

    config_path = tmp_path / "llm_config.yaml"
    config_path.write_text(
        "_default:\n"
        "  model_platform: openai\n"
        "  model_name: existing-model\n"
        "  api_key: sk-existing-secret\n"
        "  url: https://existing.example/v1\n"
        "  api_mode: responses\n"
        "  temperature: 0.3\n"
        "  max_retries: 5\n"
        "omniscient:\n"
        "  temperature: 0.9\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["llm", "setup", "--config", str(config_path)],
        input="\n\n\n\n\n\n\n",
        color=True,
    )

    assert result.exit_code == 0
    assert "可选值：" in result.stdout
    assert "\x1b[1mopenai（包括所有openai兼容模型）\x1b[0m" in result.stdout
    assert "anthropic（包括所有anthropic兼容模型）" in result.stdout
    assert "bedrock" in result.stdout
    assert "deepseek" not in result.stdout
    assert "qwen" not in result.stdout
    assert "ollama" not in result.stdout
    assert "google" not in result.stdout
    assert "\x1b[1mresponses\x1b[0m" in result.stdout
    assert "chat_completions" in result.stdout
    api_mode_line = next(line for line in result.stdout.splitlines() if "API 模式可选值：" in line)
    assert "anthropic" not in api_mode_line
    assert "bedrock" not in api_mode_line
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert loaded["_default"]["model_platform"] == "openai"
    assert loaded["_default"]["model_name"] == "existing-model"
    assert loaded["_default"]["api_key"] == "sk-existing-secret"
    assert loaded["_default"]["url"] == "https://existing.example/v1"
    assert loaded["_default"]["api_mode"] == "responses"
    assert loaded["_default"]["temperature"] == 0.3
    assert loaded["_default"]["max_retries"] == 5
    assert loaded["omniscient"]["temperature"] == 0.9


@pytest.mark.parametrize(
    ("platform_name", "expected_api_mode"),
    [
        ("anthropic", "anthropic"),
        ("bedrock", "bedrock"),
    ],
)
def test_llm_setup_skips_api_mode_prompt_for_non_openai_platforms(
    tmp_path: Path,
    platform_name: str,
    expected_api_mode: str,
) -> None:
    """anthropic / bedrock 在 llm setup 中应跳过 api_mode 提示。 / anthropic and bedrock should skip the api_mode prompt in llm setup."""
    from ripple.cli.app import app

    config_path = tmp_path / "llm_config.yaml"

    result = runner.invoke(
        app,
        ["llm", "setup", "--config", str(config_path)],
        input=f"{platform_name}\nmodel-x\nsk-secret\nhttps://example.test\n0.6\n4\n",
        color=True,
    )

    assert result.exit_code == 0
    assert "API 模式可选值：" not in result.stdout
    assert "API 模式 [" not in result.stdout
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert loaded["_default"]["model_platform"] == platform_name
    assert loaded["_default"]["api_mode"] == expected_api_mode


def test_doctor_fails_when_runtime_dependency_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """doctor 应基于真实依赖检查结果失败。 / doctor should fail when runtime dependency checks report missing packages."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "ripple.db"

    def fake_dependency_check():
        return {
            "ok": False,
            "missing": ["rich"],
            "checked": [
                {"package": "typer", "module": "typer", "installed": True},
                {"package": "rich", "module": "rich", "installed": False},
            ],
        }

    monkeypatch.setattr("ripple.cli.app._check_runtime_dependencies", fake_dependency_check, raising=False)

    result = runner.invoke(
        app,
        ["doctor", "--json", "--config", str(config_path), "--db", str(db_path)],
    )

    assert result.exit_code == 1
    payload = _read_json(result)
    assert payload["checks"]["dependencies"]["ok"] is False
    assert payload["checks"]["dependencies"]["missing"] == ["rich"]


def test_domain_commands_include_use_when_and_dump_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """domain 命令应暴露 use_when 与内容导出。 / domain commands should expose use_when and dump contents."""
    from ripple.cli.app import app

    _write_skill(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "list", "--json"])
    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["domains"][0]["description"] == "演示领域技能，用于验证 CLI 的领域展示输出。"
    assert payload["domains"][0]["use_when"] == "适用于演示场景推演、CLI 调试和 Agent 读取结构验证。"
    assert payload["domains"][0]["platforms"] == ["generic"]
    assert payload["domains"][0]["platform_options"] == [{"zh": "通用平台", "en": "generic"}]
    assert payload["domains"][0]["path"].endswith("/skills/demo-skill")
    assert "skill_path" not in payload["domains"][0]

    human_list = runner.invoke(app, ["domain", "list"])
    assert human_list.exit_code == 0
    description_column = _extract_rich_table_column(human_list.stdout, 2)
    use_when_column = _extract_rich_table_column(human_list.stdout, 3)
    command_column = _extract_rich_table_column(human_list.stdout, 4)
    assert "演示领域技能" in description_column
    assert "适用于演示场景推" in use_when_column
    assert "CLI 调试和" in use_when_column
    assert "通用平台（" in command_column and "generic）" in command_column
    assert "社区渠道（" in command_column and "community）" in command_column
    assert "零售行业（" in command_column and "retail）" in command_column

    result = runner.invoke(app, ["domain", "info", "demo-skill", "--json"])
    assert result.exit_code == 0
    info = _read_json(result)
    assert info["name"] == "demo-skill"
    assert info["use_when"] == "适用于演示场景推演、CLI 调试和 Agent 读取结构验证。"
    assert "generic" in info["platforms"]
    assert info["platform_options"] == [{"zh": "通用平台", "en": "generic"}]
    assert info["channels"] == ["community"]
    assert info["channel_options"] == [{"zh": "社区渠道", "en": "community"}]
    assert info["verticals"] == ["retail"]
    assert info["vertical_options"] == [{"zh": "零售行业", "en": "retail"}]
    assert info["reports"] == ["default"]

    result = runner.invoke(app, ["domain", "dump", "demo-skill", "--json"])
    assert result.exit_code == 0
    dumped = _read_json(result)
    assert "SKILL.md" in dumped["files"]
    assert dumped["files"]["domain-profile.md"]["content"] == "demo domain profile"
    assert dumped["files"]["reports/default.yaml"]["category"] == "report"
    assert dumped["files"]["request-schema.yaml"]["category"] == "schema"

    schema_result = runner.invoke(app, ["domain", "schema", "demo-skill", "--json"])
    assert schema_result.exit_code == 0
    schema_payload = _read_json(schema_result)
    assert schema_payload["name"] == "demo-skill"
    assert schema_payload["schema"]["summary"] == "演示领域输入契约，用于帮助 Agent 理解最少字段与可选增强信息。"
    assert schema_payload["schema"]["requirements"]["required"][1]["any_of"] == ["event.title", "event.body"]
    assert schema_payload["schema"]["selectors"]["platform"]["options"] == [{"zh": "通用平台", "en": "generic"}]
    assert any(field["path"] == "event.title" for field in schema_payload["schema"]["fields"])

    all_schema_result = runner.invoke(app, ["domain", "schema", "--json"])
    assert all_schema_result.exit_code == 0
    all_schema_payload = _read_json(all_schema_result)
    assert all_schema_payload["domains"][0]["name"] == "demo-skill"
    assert all_schema_payload["domains"][0]["schema"]["selectors"]["channel"]["options"] == [
        {"zh": "社区渠道", "en": "community"}
    ]

    example_index_result = runner.invoke(app, ["domain", "example", "--json"])
    assert example_index_result.exit_code == 0
    example_index_payload = _read_json(example_index_result)
    assert example_index_payload["domains"][0]["name"] == "demo-skill"
    assert example_index_payload["domains"][0]["example_count"] == 2
    assert example_index_payload["domains"][0]["examples"][0]["name"] == "basic"

    example_result = runner.invoke(app, ["domain", "example", "demo-skill", "--json"])
    assert example_result.exit_code == 0
    example_payload = _read_json(example_result)
    assert example_payload["name"] == "demo-skill"
    assert len(example_payload["examples"]) == 2
    assert example_payload["examples"][0]["commands"]["blocking"].startswith("cat <<'JSON' | ripple-cli job run")
    assert "ripple-cli validate --input -" in example_payload["examples"][0]["commands"]["validate"]
    assert "--output-path" not in example_payload["examples"][0]["commands"]["validate"]
    assert example_payload["examples"][1]["selectors"]["channel"] == {"zh": "社区渠道", "en": "community"}
    assert example_payload["examples"][1]["request"]["historical"] == {"baseline_notes": "过去 30 天社区话题互动率稳定"}


def test_domain_schema_fallback_is_explicit_for_skills_without_request_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有专用 schema 的领域应显式标注 fallback。 / Skills without request-schema should expose an explicit fallback warning."""
    from ripple.cli.app import app

    skill_dir = _write_skill(tmp_path)
    (skill_dir / "request-schema.yaml").unlink()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "schema", "demo-skill", "--json"])

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["schema"]["source"] == "fallback"
    assert "未提供专用 request-schema.yaml" in payload["schema"]["warning"]


def test_domain_example_rejects_invalid_example_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """example 文件结构错误时应明确报 schema 错误。 / Invalid example files should fail with a schema error."""
    from ripple.cli.app import app

    skill_dir = _write_skill(tmp_path)
    (skill_dir / "examples" / "basic.yaml").write_text(
        "title: 错误示例\n"
        "summary:\n"
        "  - 这里不应该是列表\n"
        "use_when: 用于验证错误处理\n"
        "command:\n"
        "  skill: demo-skill\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "example", "demo-skill", "--json"])

    assert result.exit_code == 1
    payload = _read_json(result)
    assert payload["error"]["code"] == "SKILL_SCHEMA_INVALID"
    assert "examples/basic.yaml" in payload["error"]["message"]


def test_job_wait_rejects_non_positive_poll_interval(tmp_path: Path) -> None:
    """job wait 应拒绝非法轮询间隔。 / job wait should reject non-positive poll intervals."""
    from ripple.cli.app import app

    db_path = tmp_path / "ripple.db"

    result = runner.invoke(
        app,
        ["job", "wait", "job_demo", "--poll-interval", "0", "--db", str(db_path), "--json"],
    )

    assert result.exit_code == 1
    payload = _read_json(result)
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert "poll-interval" in payload["error"]["message"]


def test_builtin_domain_metadata_is_chinese_and_bilingual() -> None:
    """内置领域应提供中文说明与中英双语 selector 选项。 / Built-in domains should expose Chinese descriptions and bilingual selector options."""
    from ripple.cli.app import app

    social_result = runner.invoke(app, ["domain", "info", "social-media", "--json"])
    assert social_result.exit_code == 0
    social_payload = _read_json(social_result)
    assert "社交媒体内容传播模拟领域" in social_payload["description"]
    assert {"zh": "小红书", "en": "xiaohongshu"} in social_payload["platform_options"]

    pmf_result = runner.invoke(app, ["domain", "info", "pmf-validation", "--json"])
    assert pmf_result.exit_code == 0

    pmf_schema_result = runner.invoke(app, ["domain", "schema", "pmf-validation", "--json"])
    assert pmf_schema_result.exit_code == 0
    pmf_schema_payload = _read_json(pmf_schema_result)
    assert pmf_schema_payload["schema"]["selectors"]["platform"]["options"]
    assert {"zh": "小红书", "en": "xiaohongshu"} in pmf_schema_payload["schema"]["selectors"]["platform"]["options"]
    assert any(field["path"] == "event.product_name" for field in pmf_schema_payload["schema"]["fields"])
    assert any(field["path"] == "source.followers_count" for field in pmf_schema_payload["schema"]["fields"])
    pmf_example_result = runner.invoke(app, ["domain", "example", "pmf-validation", "--json"])
    assert pmf_example_result.exit_code == 0
    pmf_example_payload = _read_json(pmf_example_result)
    assert len(pmf_example_payload["examples"]) >= 2
    assert any(example["selectors"]["channel"]["en"] == "content-seeding" for example in pmf_example_payload["examples"])
    assert any("ripple-cli job run --input -" in example["commands"]["blocking"] for example in pmf_example_payload["examples"])
    pmf_payload = _read_json(pmf_result)
    assert "产品市场匹配验证领域" in pmf_payload["description"]
    assert {"zh": "社交电商", "en": "social-ecommerce"} in pmf_payload["channel_options"]
    assert {"zh": "SaaS 软件", "en": "saas"} in pmf_payload["vertical_options"]


def test_domain_example_human_output_keeps_commands_copy_safe() -> None:
    """domain example 人类输出中的命令和 JSON 块不应被自动折行。 / Human `domain example` output should keep commands and JSON blocks copy-safe."""
    from ripple.cli.app import LiteralText, _build_domain_examples_payload, _render_domain_examples_human
    from ripple.skills.manager import SkillManager

    skill = SkillManager().load("pmf-validation")
    payload = _build_domain_examples_payload(skill)
    renderable = _render_domain_examples_human(payload)
    assert isinstance(renderable, LiteralText)
    text = renderable.text

    assert (
        'cat <<\'JSON\' | ripple-cli job run --input - --skill "pmf-validation" --platform "xiaohongshu" --channel "content-seeding" --vertical "saas" --simulation-horizon "7d" --output-path "./outputs"'
        in text
    )
    assert "平台（platform）：小红书（xiaohongshu）" in text
    assert "渠道（channel）：内容种草（content-seeding）" in text
    assert "垂直领域（vertical）：SaaS 软件（saas）" in text
    assert "等待完成：ripple-cli job wait <job_id>" in text
    assert (
        '"description": "一款面向 10-100 人团队的 AI 会议纪要工具，支持实时转写、自动提炼待办、按项目归档，并可一键同步到飞书和企业微信。核心卖点是不开会后补纪要、不遗漏责任人、不丢项目上下文。定价为每人每月 39 元。",'
        in text
    )


def test_domain_example_json_output_is_agent_safe() -> None:
    """domain example 的 JSON 模式应可直接被 Agent 解析和消费。 / `domain example --json` should be directly parseable and agent-safe."""
    from ripple.cli.app import app

    result = runner.invoke(app, ["domain", "example", "pmf-validation", "--json"])
    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["ok"] is True
    assert len(payload["examples"]) >= 2

    target = next(example for example in payload["examples"] if example["name"] == "xiaohongshu-saas-content-seeding")
    assert target["commands"]["blocking"].splitlines()[0] == (
        'cat <<\'JSON\' | ripple-cli job run --input - --skill "pmf-validation" --platform "xiaohongshu" '
        '--channel "content-seeding" --vertical "saas" --simulation-horizon "7d" --output-path "./outputs"'
    )
    assert "--output-path" not in target["commands"]["validate"]
    assert target["commands"]["wait"] == "ripple-cli job wait <job_id>"
    assert target["request"]["event"]["description"].endswith("每人每月 39 元。")


def test_domain_example_index_json_output_is_agent_safe() -> None:
    """domain example 全量索引的 JSON 模式应稳定返回轻量索引结构。 / Domain example index JSON should remain a lightweight agent-safe index."""
    from ripple.cli.app import app

    result = runner.invoke(app, ["domain", "example", "--json"])
    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["ok"] is True
    assert len(payload["domains"]) >= 2

    first = payload["domains"][0]
    assert sorted(first.keys()) == ["description", "example_count", "examples", "name", "use_when", "version"]
    assert first["examples"]
    assert sorted(first["examples"][0].keys()) == ["name", "path", "selectors", "summary", "tags", "title", "use_when"]


def test_domain_example_index_human_selector_text_is_not_misleading() -> None:
    """domain example 索引的人类选择器列不应误导为 `platform=中文（英文）` 传参。 / Human selector text should present Chinese labels plus the actual CLI parameter form."""
    from ripple.cli.app import app

    result = runner.invoke(app, ["domain", "example"])

    assert result.exit_code == 0
    normalized = _normalize_table_text(_extract_rich_table_column(result.stdout, 5))
    assert "小红书：platform=xiaohongshu" in normalized
    assert "内容种草：channel=content-seeding" in normalized
    assert "SaaS软件：vertical=saas" in normalized
    assert "platform=小红书（xiaohongshu）" not in normalized


def test_domain_list_human_table_separates_domains_with_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """domain list 人类表格应在相邻领域之间显示横向分隔线。 / Human domain list table should separate domains with horizontal lines."""
    from ripple.cli.app import app

    _write_skill(tmp_path, name="demo-skill-a")
    _write_skill(tmp_path, name="demo-skill-b")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "list"])

    assert result.exit_code == 0
    # `├`/`┼` 表示表格中间存在行分隔，而不是多个领域内容糊成一整块。
    # / `├`/`┼` indicate row separators between domains instead of one merged block.
    assert "├" in result.stdout or "┼" in result.stdout


def test_domain_dump_ignores_hidden_non_utf8_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """domain dump 应忽略隐藏的非 UTF-8 文件。 / domain dump should ignore hidden non-UTF-8 files."""
    from ripple.cli.app import app

    skill_dir = _write_skill(tmp_path)
    # 模拟 macOS 生成的隐藏二进制文件。 / Simulate a macOS-generated hidden binary file.
    (skill_dir / ".DS_Store").write_bytes(b"\x00\xfc\xffbinary")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "dump", "demo-skill", "--json"])

    assert result.exit_code == 0
    dumped = _read_json(result)
    assert ".DS_Store" not in dumped["files"]
    assert "SKILL.md" in dumped["files"]


def test_validate_rejects_inline_llm_config_before_llm_call(tmp_path: Path) -> None:
    """validate 应在本地预检阶段拒绝 llm_config 字段。 / validate should reject inline llm_config during preflight."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps(
            {
                "event": {"title": "demo", "body": "body"},
                "skill": "social-media",
                "llm_config": {"_default": {"model_name": "bad"}},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["validate", "--input", str(input_path), "--json", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    payload = _read_json(result)
    assert payload["error"]["code"] == "INVALID_INPUT"


def test_validate_uses_local_only_rules_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate 应只走本地规则，不得调用 LLM。 / validate should use local-only rules and never call the LLM."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    _write_skill(tmp_path)
    monkeypatch.chdir(tmp_path)

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps(
            {
                "event": {"title": "demo title", "body": "demo body", "content_type": "note"},
                "skill": "demo-skill",
                "platform": "generic",
            }
        ),
        encoding="utf-8",
    )

    async def fail_call_json_llm(*args, **kwargs):
        raise AssertionError("validate should not call _call_json_llm")

    monkeypatch.setattr("ripple.cli.app._call_json_llm", fail_call_json_llm)

    result = runner.invoke(
        app,
        ["validate", "--input", str(input_path), "--json", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["valid"] is True
    assert payload["ready_to_simulate"] is True
    assert payload["tiers"]["required"]["satisfied"] is True
    assert any(
        item["field"] == "event.seed_text" and item["status"] == "present"
        for item in payload["tiers"]["required"]["items"]
    )
    assert "本地预检" in payload["summary"]


def test_validate_missing_seed_text_is_not_ready_to_simulate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少主体文本时 validate 应返回不可模拟。 / validate should block simulation when seed text is missing."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    _write_skill(tmp_path)
    monkeypatch.chdir(tmp_path)

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps(
            {
                "event": {},
                "skill": "demo-skill",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["validate", "--input", str(input_path), "--json", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    payload = _read_json(result)
    assert payload["valid"] is False
    assert payload["ready_to_simulate"] is False
    assert any(
        item["field"] == "event.seed_text" and item["status"] == "missing"
        for item in payload["tiers"]["required"]["items"]
    )
    assert "不建议启动模拟" in payload["summary"]


def test_llm_test_uses_default_model_via_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """llm test 应复用默认模型配置。 / llm test should reuse the default model config."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)

    async def fake_call_text_llm(*, config_file, system_prompt, user_prompt, role="omniscient", max_llm_calls=5):
        assert str(config_path) == config_file
        assert role == "omniscient"
        assert "Reply with: ok" in user_prompt
        return "ok"

    monkeypatch.setattr("ripple.cli.app._call_text_llm", fake_call_text_llm)
    result = runner.invoke(app, ["llm", "test", "--json", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["response"] == "ok"
    assert payload["model"] == "demo-model"


def test_localize_text_for_display_extracts_text_from_json_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地化助手应提取 JSON 包装里的真正文本。 / Localization helper should extract the real text from JSON-shaped replies."""
    from ripple.cli.app import _DISPLAY_TRANSLATION_CACHE, _localize_text_for_display

    async def fake_call_text_llm(**kwargs):
        return '```json\n{"上下文":"simulation_verdict","文本":"这是整理后的中文摘要。"}\n```'

    _DISPLAY_TRANSLATION_CACHE.clear()
    monkeypatch.setattr("ripple.cli.app._call_text_llm", fake_call_text_llm)

    localized = _localize_text_for_display(
        "This is an English verdict.",
        config_file="dummy.yaml",
        context="simulation_verdict",
        allow_llm=True,
    )

    assert localized == "这是整理后的中文摘要。"


def test_generate_job_brief_falls_back_when_llm_reply_is_still_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """job 简述若 LLM 仍返回英文，应回退到中文本地模板。 / Job brief should fall back to the Chinese local template when the LLM still replies in English."""
    from ripple.cli.app import _generate_job_brief

    async def fake_call_text_llm(**kwargs):
        return "Predict social-media spread for demo title"

    monkeypatch.setattr("ripple.cli.app._call_text_llm", fake_call_text_llm)

    brief, source = _generate_job_brief(
        {
            "skill": "social-media",
            "platform": "xiaohongshu",
            "event": {"title": "春季敏感肌修护笔记", "body": "正文"},
        },
        "dummy.yaml",
    )

    assert source == "fallback"
    assert brief == "模拟 xiaohongshu 平台上的 social-media 传播：春季敏感肌修护笔记"


def test_job_run_blocking_persists_completed_job_and_supports_status_list_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """阻塞运行应持久化任务并支持查询。 / Blocking run should persist the job and support queries."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "jobs.db"

    async def fake_simulate(**kwargs):
        output_dir = Path(str(kwargs["output_path"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_json = output_dir / "run.json"
        compact_log = output_dir / "run.md"
        output_json.write_text(
            json.dumps(
                {
                    "prediction": {"impact": "moderate"},
                    "timeline": [{"t": "T+2h", "event": "spread"}],
                    "bifurcation_points": [{"name": "seed"}],
                    "agent_insights": {"star_1": "insight"},
                }
            ),
            encoding="utf-8",
        )
        compact_log.write_text("compact log body", encoding="utf-8")
        on_progress = kwargs["on_progress"]
        await on_progress(
            SimulationEvent(
                type="phase_start",
                phase="INIT",
                run_id="run_demo",
                progress=0.05,
                detail={"message": "init"},
            )
        )
        await on_progress(
            SimulationEvent(
                type="wave_start",
                phase="RIPPLE",
                run_id="run_demo",
                progress=0.45,
                wave=2,
                total_waves=8,
            )
        )
        await on_progress(
            SimulationEvent(
                type="agent_activated",
                phase="RIPPLE",
                run_id="run_demo",
                progress=0.46,
                wave=2,
                total_waves=8,
                agent_id="sea_2",
                agent_type="sea",
                detail={"energy": 0.41},
            )
        )
        await on_progress(
            SimulationEvent(
                type="agent_responded",
                phase="RIPPLE",
                run_id="run_demo",
                progress=0.47,
                wave=2,
                total_waves=8,
                agent_id="sea_2",
                agent_type="sea",
                detail={"response_type": "amplify"},
            )
        )
        await on_progress(
            SimulationEvent(
                type="phase_end",
                phase="SYNTHESIZE",
                run_id="run_demo",
                progress=1.0,
                total_waves=8,
                detail={"total_waves": 5},
            )
        )
        return {
            "run_id": "run_demo",
            "total_waves": 5,
            "wave_records_count": 5,
            "prediction": {"impact": "moderate"},
            "timeline": [{"t": "T+2h", "event": "spread"}],
            "bifurcation_points": [{"name": "seed"}],
            "agent_insights": {"star_1": "insight"},
            "output_file": str(output_json),
            "compact_log_file": str(compact_log),
        }

    async def fake_report(**kwargs):
        return "## 报告\n内容"

    monkeypatch.setattr("ripple.cli.app.simulate", fake_simulate)
    monkeypatch.setattr("ripple.cli.app.generate_skill_report_from_result", fake_report)
    monkeypatch.setattr(
        "ripple.cli.app._generate_job_brief",
        lambda request, config_file: ("Predict demo spread", "llm"),
    )

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps({"event": {"title": "Demo title", "body": "Demo body"}, "skill": "social-media"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "run",
            "--input",
            str(input_path),
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["status"] == "completed"
    assert payload["job_brief"] == "模拟 generic 平台上的 social-media 传播：Demo title"
    assert payload["job_brief_source"] == "fallback"
    assert payload["artifact_dir"]
    assert payload["summary_md_file"].endswith("/summary.md")
    assert payload["report_md_file"].endswith("/report.md")
    job_id = payload["job_id"]

    status_result = runner.invoke(
        app,
        ["job", "status", job_id, "--db", str(db_path), "--json"],
    )
    assert status_result.exit_code == 0
    status_payload = _read_json(status_result)
    assert status_payload["status"] == "completed"
    assert status_payload["job_brief"] == "模拟 generic 平台上的 social-media 传播：Demo title"
    assert status_payload["job_brief_source"] == "fallback"
    assert status_payload["summary_md_file"].endswith("/summary.md")
    assert status_payload["report_md_file"].endswith("/report.md")

    list_result = runner.invoke(app, ["job", "list", "--db", str(db_path), "--json"])
    assert list_result.exit_code == 0
    list_payload = _read_json(list_result)
    assert list_payload["jobs"][0]["brief"] == "模拟 generic 平台上的 social-media 传播：Demo title"
    assert list_payload["jobs"][0]["brief_source"] == "fallback"

    result_result = runner.invoke(
        app,
        ["job", "result", job_id, "--summary", "--db", str(db_path), "--json"],
    )
    assert result_result.exit_code == 0
    result_payload = _read_json(result_result)
    assert result_payload["result"]["prediction"] == {"impact": "moderate"}
    assert "agent_insights" not in result_payload["result"]

    log_result = runner.invoke(
        app,
        ["job", "log", job_id, "--db", str(db_path), "--json"],
    )
    assert log_result.exit_code == 0
    log_payload = _read_json(log_result)
    assert log_payload["log"] == "compact log body"

    repo = JobRepoSQLite(db_path)
    row = repo.get_job(job_id)
    snapshot = json.loads(row["status_snapshot_json"])
    assert snapshot["headline"]


def test_job_run_blocking_defaults_to_single_ensemble_and_omits_max_waves_when_unspecified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 默认应单次推演，且未指定 max_waves 时不透传。 / CLI should default to a single run and omit max_waves when unspecified."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "jobs.db"
    captured: dict[str, object] = {}

    async def fake_simulate(**kwargs):
        captured.update(kwargs)
        output_dir = Path(str(kwargs["output_path"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "single.json").write_text("{}", encoding="utf-8")
        (output_dir / "single.md").write_text("# single", encoding="utf-8")
        return {
            "run_id": "run_single",
            "total_waves": 3,
            "wave_records_count": 3,
            "prediction": {"impact": "moderate"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
            "output_file": str(output_dir / "single.json"),
            "compact_log_file": str(output_dir / "single.md"),
        }

    async def fake_report(**kwargs):
        return None

    monkeypatch.setattr("ripple.cli.app.simulate", fake_simulate)
    monkeypatch.setattr("ripple.cli.app.generate_skill_report_from_result", fake_report)
    monkeypatch.setattr(
        "ripple.cli.app._generate_job_brief",
        lambda request, config_file: ("Single run brief", "fallback"),
    )

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps({"event": {"title": "Demo title", "body": "Demo body"}, "skill": "social-media"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "run",
            "--input",
            str(input_path),
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured["ensemble_runs"] == 1
    assert "max_waves" not in captured


def test_job_run_report_zero_skips_report_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--report 0` 应跳过详细报告产物。 / `--report 0` should skip the detailed report artifact."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "jobs.db"

    async def fake_simulate(**kwargs):
        output_dir = Path(str(kwargs["output_path"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "demo.json").write_text(json.dumps({"prediction": {"impact": "low"}}), encoding="utf-8")
        (output_dir / "demo.md").write_text("# compact", encoding="utf-8")
        return {
            "run_id": "run_no_report",
            "total_waves": 2,
            "wave_records_count": 2,
            "prediction": {"impact": "low"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
            "output_file": str(output_dir / "demo.json"),
            "compact_log_file": str(output_dir / "demo.md"),
        }

    async def fake_report(**kwargs):
        raise AssertionError("report generation should be skipped when --report 0 is used")

    monkeypatch.setattr("ripple.cli.app.simulate", fake_simulate)
    monkeypatch.setattr("ripple.cli.app.generate_skill_report_from_result", fake_report)
    monkeypatch.setattr(
        "ripple.cli.app._generate_job_brief",
        lambda request, config_file: ("No report brief", "fallback"),
    )

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps({"event": {"title": "Demo title", "body": "Demo body"}, "skill": "social-media"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "run",
            "--input",
            str(input_path),
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "--report",
            "0",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["summary_md_file"].endswith("/summary.md")
    assert payload.get("report_md_file") in (None, "")


def test_job_run_blocking_human_progress_is_chinese_and_rich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """阻塞模式应输出中文进度条和关键信息。 / Blocking mode should render Chinese progress bars and key details."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "jobs.db"
    async def fake_simulate(**kwargs):
        output_dir = Path(str(kwargs["output_path"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_json = output_dir / "rich.json"
        compact_log = output_dir / "rich.md"
        output_json.write_text(
            json.dumps(
                {
                    "prediction": {"verdict": "中等扩散，收藏高于转发。"},
                    "timeline": [
                        {"t": "T+2h", "event": "首轮种草扩散", "effect": "收藏增长快于评论"},
                    ],
                    "process": {
                        "init": {
                            "estimated_waves": 12,
                            "max_waves": 36,
                            "safety_max_waves": 36,
                        },
                        "waves": [
                            {
                                "verdict": {"global_observation": "冷启动受限，扩散未破圈。"},
                                "agent_responses": {
                                    "sea_office_ladies": {"response_type": "absorb"},
                                    "star_koc": {"response_type": "comment"},
                                },
                            }
                        ],
                        "observation": {
                            "content": {
                                "phase_vector": {
                                    "heat": "growth",
                                    "sentiment": "neutral",
                                    "coherence": "ordered",
                                },
                                "phase_transition_detected": False,
                                "emergence_events": [],
                            }
                        },
                        "deliberation": {
                            "deliberation_summary": {
                                "rounds_executed": 3,
                                "converged": True,
                                "consensus_points": ["reach_realism", "decay_realism"],
                                "dissent_points": ["virality_plausibility"],
                                "final_positions": [
                                    {
                                        "member_role": "PropagationDynamicist",
                                        "scores": {
                                            "reach_realism": 3,
                                            "decay_realism": 4,
                                            "virality_plausibility": 2,
                                        },
                                    },
                                    {
                                        "member_role": "PlatformEcologist",
                                        "scores": {
                                            "reach_realism": 3,
                                            "decay_realism": 4,
                                            "virality_plausibility": 2,
                                        },
                                    },
                                ],
                            },
                            "deliberation_records": [
                                {
                                    "round_number": 0,
                                    "converged": False,
                                    "consensus_points": ["reach_realism"],
                                    "dissent_points": ["virality_plausibility"],
                                    "opinions": [
                                        {
                                            "member_role": "PropagationDynamicist",
                                            "scores": {
                                                "reach_realism": 3,
                                                "decay_realism": 3,
                                                "virality_plausibility": 2,
                                            },
                                            "narrative": "冷启动限制明显。",
                                        }
                                    ],
                                },
                                {
                                    "round_number": 1,
                                    "converged": True,
                                    "consensus_points": ["reach_realism", "decay_realism"],
                                    "dissent_points": [],
                                    "opinions": [
                                        {
                                            "member_role": "PropagationDynamicist",
                                            "scores": {
                                                "reach_realism": 3,
                                                "decay_realism": 4,
                                                "virality_plausibility": 2,
                                            },
                                            "narrative": "传播符合冷启动内容常态。",
                                        }
                                    ],
                                },
                            ],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        compact_log.write_text("# log", encoding="utf-8")
        on_progress = kwargs["on_progress"]
        await on_progress(
            SimulationEvent(
                type="phase_end",
                phase="INIT",
                run_id="run_rich",
                progress=0.05,
                total_waves=12,
                detail={
                    "star_count": 2,
                    "sea_count": 3,
                    "estimated_waves": 12,
                    "safety_max_waves": 36,
                    "requested_max_waves": None,
                    "max_waves": 36,
                    "wave_time_window": "4h",
                    "wave_time_window_reasoning": "内容生命周期集中在发布后 4 小时。",
                    "star_labels": ["新号敏感肌达人", "护肤 KOC"],
                    "sea_labels": ["办公室白领群体", "成分党群体", "泛用户群体"],
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="wave_start",
                phase="RIPPLE",
                run_id="run_rich",
                progress=0.25,
                wave=0,
                total_waves=12,
                detail={
                    "global_observation": "全视者判断：内容有共鸣，但冷启动受限。",
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="agent_activated",
                phase="RIPPLE",
                run_id="run_rich",
                progress=0.26,
                wave=0,
                total_waves=12,
                agent_id="sea_office_ladies",
                agent_type="sea",
                detail={
                    "energy": 0.41,
                    "agent_label": "办公室白领群体",
                    "activation_reason": "午休护肤场景高度相关。",
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="agent_responded",
                phase="RIPPLE",
                run_id="run_rich",
                progress=0.27,
                wave=0,
                total_waves=12,
                agent_id="sea_office_ladies",
                agent_type="sea",
                detail={
                    "response_type": "absorb",
                    "agent_label": "办公室白领群体",
                    "cluster_reaction": "群体关注但暂未形成外扩。",
                    "outgoing_energy": 0.12,
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="wave_end",
                phase="RIPPLE",
                run_id="run_rich",
                progress=0.30,
                wave=0,
                total_waves=12,
                detail={
                    "agent_count": 4,
                    "response_mix": {"comment": 1, "absorb": 3},
                    "cas_signal": "扩散偏弱，讨论集中在敏感肌自救经验。",
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="round_end",
                phase="DELIBERATE",
                run_id="run_rich",
                progress=0.74,
                total_waves=12,
                detail={
                    "round_number": 1,
                    "total_rounds": 3,
                    "converged": False,
                    "consensus_points": ["reach_realism"],
                    "dissent_points": ["virality_plausibility"],
                    "opinions": [
                        {
                            "member_role": "PropagationDynamicist",
                            "scores": {
                                "reach_realism": 3,
                                "decay_realism": 3,
                                "virality_plausibility": 2,
                            },
                            "narrative": "冷启动限制明显。",
                        }
                    ],
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="phase_end",
                phase="DELIBERATE",
                run_id="run_rich",
                progress=0.83,
                total_waves=12,
                detail={
                    "rounds": 3,
                    "converged": False,
                    "consensus_points": ["timeline_realism"],
                    "dissent_points": ["virality_plausibility"],
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="phase_end",
                phase="OBSERVE",
                run_id="run_rich",
                progress=0.91,
                total_waves=12,
                detail={
                    "observation_preview": {
                        "phase_vector": {
                            "heat": "growth",
                            "sentiment": "neutral",
                            "coherence": "ordered",
                        },
                        "phase_transition_detected": False,
                    }
                },
            )
        )
        await on_progress(
            SimulationEvent(
                type="phase_end",
                phase="SYNTHESIZE",
                run_id="run_rich",
                progress=1.0,
                total_waves=12,
                detail={
                    "total_waves": 5,
                    "prediction_verdict": "中等扩散，收藏高于转发。",
                },
            )
        )
        return {
            "run_id": "run_rich",
            "total_waves": 5,
            "wave_records_count": 5,
            "prediction": {"verdict": "中等扩散，收藏高于转发。"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
            "output_file": str(output_json),
            "compact_log_file": str(compact_log),
        }

    monkeypatch.setattr("ripple.cli.app.simulate", fake_simulate)
    async def fake_report(**kwargs):
        return "## 详细报告\n\n- 这是中文报告\n- 传播未破圈"

    monkeypatch.setattr("ripple.cli.app.generate_skill_report_from_result", fake_report)
    monkeypatch.setattr(
        "ripple.cli.app._generate_job_brief",
        lambda request, config_file: ("Rich progress brief", "fallback"),
    )
    monkeypatch.setattr(
        "ripple.cli.app._localize_text_for_display",
        lambda value, **kwargs: {
            "Rich progress brief": "春季护肤内容传播测试",
            "中等扩散，收藏高于转发。": "中等扩散，收藏高于转发。",
            "冷启动受限，扩散未破圈。": "冷启动受限，扩散未破圈。",
            "冷启动限制明显。": "冷启动限制明显。",
            "传播符合冷启动内容常态。": "传播符合冷启动内容常态。",
            "首轮种草扩散": "首轮种草扩散",
            "收藏增长快于评论": "收藏增长快于评论",
        }.get(str(value), str(value)),
    )

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps({"event": {"title": "Demo title", "body": "Demo body"}, "skill": "social-media"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "run",
            "--input",
            str(input_path),
            "--db",
            str(db_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "初始化" in result.stderr
    assert "预估轮次：12" in result.stderr
    assert "执行上限：36" in result.stderr
    assert "全视者判断" in result.stderr
    assert "办公室白领群体" in result.stderr
    assert "群体关注但暂未形成外扩" in result.stderr
    assert "本轮结论" in result.stderr
    assert "传播动力学审查员" in result.stderr
    assert "热度增长，情绪中性，结构有序，未发生相变" in result.stderr
    assert "█" in result.stderr
    assert "合议庭最终评分" in result.stdout
    assert "传播动力学审查员" in result.stdout
    assert "合议轮次回顾" in result.stdout
    assert "关键时间线" in result.stdout
    assert "详细报告" in result.stdout
    assert "详细日志文件" in result.stdout
    assert "精简日志文件" in result.stdout
    assert "任务总结文件" in result.stdout
    assert "详细报告文件" in result.stdout


def test_job_status_human_renders_friendly_snapshot(tmp_path: Path) -> None:
    """job status 人类模式应优先显示友好快照。 / job status in human mode should render the friendly snapshot."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job("job_demo", {"event": {"title": "demo"}, "ensemble_runs": 1}, source="cli")
    repo.update_runtime(
        "job_demo",
        phase="RIPPLE",
        progress=0.44,
        current_wave=8,
        total_waves=12,
        snapshot={
            "headline": "🌊 第 8/12 轮传播 44%",
            "progress_bar": "[████████░░] 44%",
            "phase_label": "涟漪传播",
            "highlights": ["预估轮次：12", "执行上限：36", "当前轮次：8"],
            "recent_events": [{"emoji": "🧠", "text": "全视者判断：冷启动偏弱。"}],
        },
    )
    repo.update_status("job_demo", "running")

    result = runner.invoke(app, ["job", "status", "job_demo", "--db", str(db_path)])

    assert result.exit_code == 0
    assert "第 8/12 轮传播" in result.stdout
    assert "全视者判断：冷启动偏弱" in result.stdout
    assert "预估轮次：12" in result.stdout


def test_job_list_human_never_calls_localization_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """job list 人类模式不得触发 LLM 本地化。 / job list in human mode must never trigger LLM localization."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job(
        "job_list_demo",
        {"event": {"title": "demo"}, "skill": "social-media"},
        source="cli",
        job_brief="Predict social-media spread for demo title",
        job_brief_source="llm",
    )
    repo.try_acquire_active_job_lock(
        "job_list_demo",
        now_iso="2026-03-15T01:00:00+00:00",
        worker_pid=12345,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_list_demo", "completed")

    def fail_localize(*args, **kwargs):
        raise AssertionError("job list should not call _localize_text_for_display")

    monkeypatch.setattr("ripple.cli.app._localize_text_for_display", fail_localize)
    monkeypatch.setattr(
        "ripple.cli.app._resolve_config_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("job list should not resolve llm config")),
    )

    result = runner.invoke(app, ["job", "list", "--db", str(db_path)])

    assert result.exit_code == 0
    assert "Ripple 任务列表" in result.stdout
    assert "产物文件" in result.stdout


def test_job_list_includes_artifact_paths_in_json_and_human_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """job list 应返回并展示每个任务的产物文件路径。 / job list should expose artifact paths in JSON and human output."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output_json = artifact_dir / "demo.json"
    compact_log = artifact_dir / "demo.md"
    summary_md = artifact_dir / "summary.md"
    report_md = artifact_dir / "report.md"
    output_json.write_text("{}", encoding="utf-8")
    compact_log.write_text("# compact", encoding="utf-8")
    summary_md.write_text("# summary", encoding="utf-8")
    report_md.write_text("# report", encoding="utf-8")

    repo.create_job(
        "job_list_artifacts",
        {"event": {"title": "demo"}, "skill": "social-media"},
        source="cli",
        job_brief="done job with artifacts",
        job_brief_source="fallback",
    )
    repo.set_result(
        "job_list_artifacts",
        {
            "artifact_dir": str(artifact_dir),
            "output_file": str(output_json),
            "compact_log_file": str(compact_log),
            "summary_md_file": str(summary_md),
            "report_md_file": str(report_md),
        },
    )
    repo.try_acquire_active_job_lock(
        "job_list_artifacts",
        now_iso="2026-03-15T01:00:00+00:00",
        worker_pid=12345,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_list_artifacts", "completed")

    monkeypatch.setattr(
        "ripple.cli.app._resolve_config_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("job list should not resolve llm config")),
    )

    json_result = runner.invoke(app, ["job", "list", "--db", str(db_path), "--json"])

    assert json_result.exit_code == 0
    json_payload = _read_json(json_result)
    artifacts = json_payload["jobs"][0]["artifacts"]
    assert artifacts["artifact_dir"] == str(artifact_dir)
    assert artifacts["output_file"] == str(output_json)
    assert artifacts["compact_log_file"] == str(compact_log)
    assert artifacts["summary_md_file"] == str(summary_md)
    assert artifacts["report_md_file"] == str(report_md)

    human_result = runner.invoke(app, ["job", "list", "--db", str(db_path)])

    assert human_result.exit_code == 0
    assert "产物文件" in human_result.stdout
    artifact_column = _extract_rich_table_column(human_result.stdout, 4)
    assert "详细日志：d" in artifact_column
    assert "emo.json" in artifact_column
    assert "精简日志：d" in artifact_column
    assert "emo.md" in artifact_column
    assert "任务总结：s" in artifact_column
    assert "ummary.md" in artifact_column
    assert "详细报告：r" in artifact_column
    assert "eport.md" in artifact_column
    assert "产物目录：a" in artifact_column
    assert "rtifacts" in artifact_column
    assert "路径：" in artifact_column
    assert "/artifacts" in artifact_column


def test_job_list_human_table_separates_jobs_with_lines(tmp_path: Path) -> None:
    """job list 人类表格应在相邻任务之间显示横向分隔线。 / Human job list table should separate jobs with horizontal lines."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job(
        "job_list_first",
        {"event": {"title": "first"}, "skill": "social-media"},
        source="cli",
        job_brief="first job",
        job_brief_source="fallback",
    )
    repo.try_acquire_active_job_lock(
        "job_list_first",
        now_iso="2026-03-15T01:00:00+00:00",
        worker_pid=11111,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_list_first", "completed")

    repo.create_job(
        "job_list_second",
        {"event": {"title": "second"}, "skill": "social-media"},
        source="cli",
        job_brief="second job",
        job_brief_source="fallback",
    )
    repo.try_acquire_active_job_lock(
        "job_list_second",
        now_iso="2026-03-15T01:00:10+00:00",
        worker_pid=22222,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_list_second", "completed")

    result = runner.invoke(app, ["job", "list", "--db", str(db_path)])

    assert result.exit_code == 0
    # `├`/`┼` 说明表格中间存在行分隔，而不是所有多行内容连成一整块。
    # / `├`/`┼` indicate row separators between jobs instead of one merged block.
    assert "├" in result.stdout or "┼" in result.stdout


def test_job_status_completed_human_never_calls_localization_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已完成任务的 job status 也不得触发 LLM 本地化。 / Completed job status must not trigger LLM localization either."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output_json = artifact_dir / "demo.json"
    compact_log = artifact_dir / "demo.md"
    summary_md = artifact_dir / "summary.md"
    report_md = artifact_dir / "report.md"

    output_json.write_text(
        json.dumps(
            {
                "prediction": {"verdict": "传播整体表现为stable"},
                "timeline": [{"t": "T+2h", "event": "limited spread", "effect": "no breakout"}],
                "process": {
                    "init": {"estimated_waves": 12, "max_waves": 36, "safety_max_waves": 36},
                    "waves": [{"verdict": {"global_observation": "cold start with KOC traffic"}}],
                    "deliberation": {
                        "deliberation_summary": {
                            "final_positions": [
                                {
                                    "member_role": "DevilsAdvocate",
                                    "scores": {"virality_plausibility": 2},
                                }
                            ]
                        },
                        "deliberation_records": [
                            {
                                "round_number": 0,
                                "converged": True,
                                "opinions": [
                                    {
                                        "member_role": "DevilsAdvocate",
                                        "scores": {"virality_plausibility": 2},
                                        "narrative": "limited breakout expected",
                                    }
                                ],
                            }
                        ],
                    },
                    "observation": {
                        "content": {
                            "phase_vector": {"heat": "growth", "sentiment": "neutral", "coherence": "ordered"},
                            "phase_transition_detected": False,
                            "emergence_events": [],
                        }
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    compact_log.write_text("# compact", encoding="utf-8")
    summary_md.write_text("# summary", encoding="utf-8")
    report_md.write_text("# report", encoding="utf-8")

    repo.create_job(
        "job_status_done",
        {"event": {"title": "demo"}, "skill": "social-media"},
        source="cli",
        job_brief="Predict social-media spread for completed job",
        job_brief_source="llm",
    )
    repo.try_acquire_active_job_lock(
        "job_status_done",
        now_iso="2026-03-15T01:00:00+00:00",
        worker_pid=12345,
        stale_threshold_seconds=90,
    )
    repo.set_result(
        "job_status_done",
        {
            "output_file": str(output_json),
            "compact_log_file": str(compact_log),
            "summary_md_file": str(summary_md),
            "report_md_file": str(report_md),
            "total_waves": 12,
            "prediction": {"verdict": "传播整体表现为stable"},
        },
    )
    repo.update_status("job_status_done", "completed")

    def fail_localize(*args, **kwargs):
        raise AssertionError("completed job status should not call _localize_text_for_display")

    monkeypatch.setattr("ripple.cli.app._localize_text_for_display", fail_localize)
    monkeypatch.setattr(
        "ripple.cli.app._resolve_config_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("completed job status should not resolve llm config")),
    )

    result = runner.invoke(app, ["job", "status", "job_status_done", "--db", str(db_path)])

    assert result.exit_code == 0
    assert "# summary" in result.stdout
    assert "任务总结文件" in result.stdout


def test_job_delete_removes_completed_job_and_artifacts(tmp_path: Path) -> None:
    """job delete 应删除单个非运行态任务及其产物。 / job delete should remove one finished job and its artifacts."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output_json = artifact_dir / "demo.json"
    compact_log = artifact_dir / "demo.md"
    summary_md = artifact_dir / "summary.md"
    report_md = artifact_dir / "report.md"
    output_json.write_text("{}", encoding="utf-8")
    compact_log.write_text("# compact", encoding="utf-8")
    summary_md.write_text("# summary", encoding="utf-8")
    report_md.write_text("# report", encoding="utf-8")

    repo.create_job(
        "job_delete_done",
        {"event": {"title": "demo"}, "skill": "social-media"},
        source="cli",
        job_brief="done job",
        job_brief_source="fallback",
    )
    repo.set_result(
        "job_delete_done",
        {
            "artifact_dir": str(artifact_dir),
            "output_file": str(output_json),
            "compact_log_file": str(compact_log),
            "summary_md_file": str(summary_md),
            "report_md_file": str(report_md),
        },
    )
    repo.try_acquire_active_job_lock(
        "job_delete_done",
        now_iso="2026-03-15T01:00:00+00:00",
        worker_pid=12345,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_delete_done", "completed")

    result = runner.invoke(app, ["job", "delete", "job_delete_done", "--db", str(db_path), "--yes", "--json"])

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["deleted"] == 1
    assert payload["job_id"] == "job_delete_done"
    assert not output_json.exists()
    assert not compact_log.exists()
    assert not summary_md.exists()
    assert not report_md.exists()
    assert not artifact_dir.exists()
    with pytest.raises(KeyError):
        repo.get_job("job_delete_done")


def test_job_delete_rejects_running_job(tmp_path: Path) -> None:
    """运行中的任务必须先取消，不能直接删除。 / Running jobs must be cancelled before deletion."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job(
        "job_delete_running",
        {"event": {"title": "demo"}, "skill": "social-media"},
        source="cli",
        job_brief="running job",
        job_brief_source="fallback",
    )
    repo.try_acquire_active_job_lock(
        "job_delete_running",
        now_iso="2026-03-15T01:00:00+00:00",
        worker_pid=12345,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_delete_running", "running")

    result = runner.invoke(app, ["job", "delete", "job_delete_running", "--db", str(db_path), "--yes", "--json"])

    assert result.exit_code == 1
    payload = _read_json(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert "请先执行 `ripple-cli job cancel job_delete_running`" in payload["fix"]


def test_job_run_async_spawns_worker_and_worker_completes_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """异步运行应排队并由隐藏 worker 完成。 / Async run should enqueue and be completed by the hidden worker."""
    from ripple.cli.app import app

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "jobs.db"
    spawned: dict = {}

    class DummyPopen:
        def __init__(self, args, **kwargs):
            spawned["args"] = args
            spawned["kwargs"] = kwargs

    monkeypatch.setattr("ripple.cli.app.subprocess.Popen", DummyPopen)
    monkeypatch.setattr(
        "ripple.cli.app._generate_job_brief",
        lambda request, config_file: ("Queued demo brief", "fallback"),
    )

    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps({"event": {"title": "Queued title", "body": "Queued body"}, "skill": "social-media"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "run",
            "--input",
            str(input_path),
            "--async",
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["status"] == "queued"
    assert payload["job_brief"] == "模拟 generic 平台上的 social-media 传播：Queued title"
    assert payload["job_brief_source"] == "fallback"
    job_id = payload["job_id"]
    assert spawned["args"][-1] == job_id

    output_json = tmp_path / "async.json"
    compact_log = tmp_path / "async.log"
    output_json.write_text(json.dumps({"prediction": {"impact": "low"}}), encoding="utf-8")
    compact_log.write_text("async log", encoding="utf-8")

    async def fake_simulate(**kwargs):
        output_dir = Path(str(kwargs["output_path"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_json = output_dir / "async.json"
        compact_log = output_dir / "async.log"
        output_json.write_text(json.dumps({"prediction": {"impact": "low"}}), encoding="utf-8")
        compact_log.write_text("async log", encoding="utf-8")
        on_progress = kwargs["on_progress"]
        await on_progress(
            SimulationEvent(
                type="wave_start",
                phase="RIPPLE",
                run_id="run_async",
                progress=0.4,
                wave=1,
                total_waves=4,
            )
        )
        return {
            "run_id": "run_async",
            "total_waves": 2,
            "wave_records_count": 2,
            "prediction": {"impact": "low"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
            "output_file": str(output_json),
            "compact_log_file": str(compact_log),
        }

    async def fake_report(**kwargs):
        return "## 报告\n异步结果"

    monkeypatch.setattr("ripple.cli.app.simulate", fake_simulate)
    monkeypatch.setattr("ripple.cli.app.generate_skill_report_from_result", fake_report)
    worker_result = runner.invoke(
        app,
        [
            "_worker",
            job_id,
            "--db",
            str(db_path),
            "--config",
            str(config_path),
        ],
    )
    assert worker_result.exit_code == 0

    status_result = runner.invoke(
        app,
        ["job", "status", job_id, "--db", str(db_path), "--json"],
    )
    status_payload = _read_json(status_result)
    assert status_payload["status"] == "completed"


def test_job_cancel_marks_request_and_calls_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """job cancel 应标记取消并尝试发信号。 / job cancel should mark cancellation and attempt to signal the worker."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job("job_cancel", {"event": {"title": "demo"}}, source="cli")
    repo.try_acquire_active_job_lock(
        "job_cancel",
        now_iso="2026-03-14T10:00:00+00:00",
        worker_pid=99999,
        stale_threshold_seconds=90,
    )

    called: dict = {}

    def fake_kill(pid: int, sig: int) -> None:
        called["pid"] = pid
        called["sig"] = sig

    monkeypatch.setattr("ripple.cli.app.os.kill", fake_kill)
    result = runner.invoke(app, ["job", "cancel", "job_cancel", "--db", str(db_path), "--json"])

    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["status"] == "cancel_requested"
    assert called["pid"] == 99999
    assert repo.get_job("job_cancel")["cancel_requested"] == 1


def test_job_clean_dry_run_and_delete(tmp_path: Path) -> None:
    """job clean 应支持预览和删除。 / job clean should support preview and deletion."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job(
        "job_old",
        {"event": {"title": "old"}},
        source="cli",
        created_at="2026-03-01T10:00:00+00:00",
    )
    repo.try_acquire_active_job_lock(
        "job_old",
        now_iso="2026-03-01T10:00:05+00:00",
        worker_pid=701,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_old", "completed", completed_at="2026-03-01T11:00:00+00:00")

    result = runner.invoke(
        app,
        ["job", "clean", "--db", str(db_path), "--before", "7d", "--status", "completed", "--dry-run", "--json"],
    )
    assert result.exit_code == 0
    payload = _read_json(result)
    assert payload["dry_run"] is True
    assert payload["candidate_count"] == 1
    assert payload["cleaned"] == 0
    assert repo.list_jobs(limit=10, offset=0)["total"] == 1

    result = runner.invoke(
        app,
        ["job", "clean", "--db", str(db_path), "--before", "7d", "--status", "completed", "--yes", "--json"],
    )
    assert result.exit_code == 0
    assert repo.list_jobs(limit=10, offset=0)["total"] == 0


def test_job_list_and_status_use_local_chinese_fallback_for_legacy_english_brief(tmp_path: Path) -> None:
    """历史英文 brief 应在读取时本地兜底成中文。 / Legacy English job briefs should fall back to a local Chinese summary on read."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job(
        "job_legacy",
        {
            "skill": "social-media",
            "platform": "xiaohongshu",
            "event": {"title": "春季敏感肌修护笔记"},
        },
        source="cli",
        job_brief="Legacy english brief for social media simulation",
        job_brief_source="llm",
    )

    list_result = runner.invoke(app, ["job", "list", "--db", str(db_path), "--json"])
    assert list_result.exit_code == 0
    list_payload = _read_json(list_result)
    assert list_payload["jobs"][0]["brief_source"] == "fallback"
    assert "模拟 xiaohongshu 平台上的 social-media 传播" in list_payload["jobs"][0]["brief"]
    assert "Legacy english brief" not in list_payload["jobs"][0]["brief"]

    status_result = runner.invoke(app, ["job", "status", "job_legacy", "--db", str(db_path), "--json"])
    assert status_result.exit_code == 0
    status_payload = _read_json(status_result)
    assert status_payload["job_brief_source"] == "fallback"
    assert "春季敏感肌修护笔记" in status_payload["job_brief"]


def test_job_list_human_output_uses_chinese_task_id_header(tmp_path: Path) -> None:
    """job list 人类表格第一列应显示中文表头。 / Human job list table should use a Chinese task-id header."""
    from ripple.cli.app import app

    db_path = tmp_path / "jobs.db"
    repo = JobRepoSQLite(db_path)
    repo.init_schema()
    repo.create_job(
        "job_demo",
        {"skill": "social-media", "platform": "xiaohongshu", "event": {"title": "示例任务"}},
        source="cli",
        job_brief="示例任务",
        job_brief_source="fallback",
    )

    result = runner.invoke(app, ["job", "list", "--db", str(db_path)])

    assert result.exit_code == 0
    assert "任务 ID" in result.stdout
