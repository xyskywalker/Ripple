from __future__ import annotations

import asyncio
import click
import importlib.util
import json
import logging
import os
import platform as runtime_platform
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
import yaml
from rich.console import Console, Group
from rich.rule import Rule
from rich.table import Table
from typer.core import TyperCommand, TyperGroup

from ripple.api.simulate import simulate
from ripple.llm.config import LLMConfigLoader
from ripple.llm.router import ConfigurationError, ModelRouter
from ripple.primitives.events import SimulationEvent
from ripple.reporting import generate_skill_report_from_result
from ripple.service.job_repo_sqlite import JobRepoSQLite
from ripple.skills.manager import LoadedSkill, SkillManager
from ripple.skills.validator import SKILL_NOT_FOUND, SKILL_SCHEMA_INVALID, SkillValidationError
from ripple.version import get_version


logger = logging.getLogger(__name__)
console = Console()


class _ChineseHelpMixin:
    """统一中文化 --help 选项。 / Localize the built-in help option to Chinese."""

    _HELP_TEXT = "显示帮助并退出。"

    def get_help_option(self, ctx):  # type: ignore[override]
        help_options = self.get_help_option_names(ctx)
        if not help_options or not self.add_help_option:
            return None

        def show_help(current_ctx: click.Context, param: click.Parameter, value: bool) -> None:
            if value and not current_ctx.resilient_parsing:
                click.echo(current_ctx.get_help(), color=current_ctx.color)
                current_ctx.exit()

        return click.Option(
            help_options,
            is_flag=True,
            is_eager=True,
            expose_value=False,
            callback=show_help,
            help=self._HELP_TEXT,
        )


class ChineseHelpCommand(_ChineseHelpMixin, TyperCommand):
    """带中文帮助选项的命令。 / Command with localized help option."""


class ChineseHelpGroup(_ChineseHelpMixin, TyperGroup):
    """带中文帮助选项的命令组。 / Command group with localized help option."""


_HELP_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    cls=ChineseHelpGroup,
    add_completion=False,
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT_SETTINGS,
    pretty_exceptions_enable=False,
    help="Ripple 命令行工具，面向人类与 Agent，支持领域查看、输入校验、LLM 配置、任务运行与结果查询。",
    epilog=(
        "常用示例：\n"
        "  ripple-cli domain example social-media"
    ),
)
llm_app = typer.Typer(
    cls=ChineseHelpGroup,
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT_SETTINGS,
    help="管理 `llm_config.yaml` 中的 `_default` 模型配置，并执行显式连通性测试。",
    epilog=(
        "示例：\n"
        "  ripple-cli llm show\n"
        "  ripple-cli llm set --model gpt-5 --api-key sk-xxx\n"
        "  ripple-cli llm test"
    ),
)
domain_app = typer.Typer(
    cls=ChineseHelpGroup,
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT_SETTINGS,
    help="查看已安装领域 Skill 的元信息、支持平台、输入 Schema、领域示例以及内置提示词/报告模板。",
    epilog=(
        "示例：\n"
        "  ripple-cli domain list\n"
        "  ripple-cli domain schema pmf-validation\n"
        "  ripple-cli domain example social-media\n"
        "  ripple-cli domain info social-media\n"
        "  ripple-cli domain dump social-media --section reports"
    ),
)
job_app = typer.Typer(
    cls=ChineseHelpGroup,
    no_args_is_help=True,
    context_settings=_HELP_CONTEXT_SETTINGS,
    help="创建、查询、等待、取消、删除和批量清理模拟任务。",
    epilog=(
        "示例：\n"
        "  ripple-cli job run --input request.json\n"
        "  ripple-cli job wait job_xxx\n"
        "  ripple-cli job clean --before 7d --yes"
    ),
)
app.add_typer(llm_app, name="llm")
app.add_typer(domain_app, name="domain")
app.add_typer(job_app, name="job")


JsonOption = Annotated[
    bool,
    typer.Option("--json", help="以 JSON 输出到标准输出，适合 Agent、脚本和管道消费。"),
]
QuietOption = Annotated[
    bool,
    typer.Option("--quiet", "-q", help="静默模式。隐藏非关键的人类可读输出，只保留最终结果或错误。"),
]
VerboseOption = Annotated[
    int,
    typer.Option("--verbose", "-v", count=True, help="增加日志详细程度。可重复使用，例如 `-v`、`-vv`。"),
]
ConfigOption = Annotated[
    Optional[str],
    typer.Option("--config", help="指定 `llm_config.yaml` 路径。未传时默认读取当前目录下的 `./llm_config.yaml`。"),
]
DbOption = Annotated[
    Optional[str],
    typer.Option("--db", help="指定任务 SQLite 数据库路径。未传时默认使用 `./data/ripple.db`。"),
]
InputOption = Annotated[
    Optional[str],
    typer.Option("--input", help="输入 JSON 文件路径。传 `-` 表示从标准输入读取；不传时也可通过管道输入。"),
]
StatusFilterOption = Annotated[
    Optional[str],
    typer.Option("--status", help="按任务状态过滤，例如 `queued`、`running`、`completed`、`failed`、`cancelled`。"),
]
SourceFilterOption = Annotated[
    Optional[str],
    typer.Option("--source", help="按任务来源过滤，例如 `cli`。"),
]

_DEFAULT_OUTPUT_DIR = "ripple_outputs"
_DEFAULT_DB_PATH = "./data/ripple.db"
_DEFAULT_LLM_CONFIG_PATH = "./llm_config.yaml"
_LLM_PLATFORM_OPTIONS = (
    ("openai", "openai（包括所有openai兼容模型）"),
    ("anthropic", "anthropic（包括所有anthropic兼容模型）"),
    ("bedrock", "bedrock"),
)
_LLM_API_MODE_OPTIONS = (
    ("chat_completions", "chat_completions"),
    ("responses", "responses"),
    ("anthropic", "anthropic"),
    ("bedrock", "bedrock"),
)
_LLM_SETUP_OPENAI_API_MODE_OPTIONS = (
    ("chat_completions", "chat_completions"),
    ("responses", "responses"),
)
_LLM_SETUP_FIXED_API_MODES = {
    "anthropic": "anthropic",
    "bedrock": "bedrock",
}
_DEFAULT_HEARTBEAT_SECONDS = 30
_DEFAULT_STALE_SECONDS = 90
_MAX_RECENT_EVENTS = 5
_PROGRESS_BAR_WIDTH = 24
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_SCHEMA_TIER_LABELS = {
    "required": "必填",
    "recommended": "推荐",
    "optional": "可选",
}
_PHASE_LABELS = {
    "INIT": "初始化",
    "SEED": "种子注入",
    "RIPPLE": "涟漪传播",
    "OBSERVE": "全局观测",
    "DELIBERATE": "合议庭审议",
    "SYNTHESIZE": "结果合成",
}
_STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "cancel_pending": "等待取消",
    "cancelling": "取消中",
}
_AGENT_TYPE_LABELS = {"star": "星", "sea": "海", "omniscient": "全视者"}
_RESPONSE_TYPE_LABELS = {
    "amplify": "放大传播",
    "create": "主动创作",
    "comment": "评论互动",
    "ignore": "忽略",
    "absorb": "吸收围观",
    "mutate": "话题变形",
    "suppress": "抑制扩散",
    "error": "异常降级",
    "unknown": "未知响应",
}
_TRIBUNAL_ROLE_LABELS = {
    "PropagationDynamicist": "传播动力学审查员",
    "PlatformEcologist": "平台生态审查员",
    "DevilsAdvocate": "风险反证审查员",
    "MarketAnalyst": "市场审查员",
    "UserAdvocate": "用户视角审查员",
    "Analyst": "综合分析审查员",
    "Critic": "批判审查员",
}
_DIMENSION_LABELS = {
    "reach_realism": "覆盖真实性",
    "decay_realism": "衰减真实性",
    "virality_plausibility": "破圈可信度",
    "audience_activation": "受众激活",
    "timeline_realism": "节奏真实性",
    "demand_resonance": "需求共鸣",
    "propagation_potential": "传播潜力",
    "competitive_differentiation": "竞争差异",
    "adoption_friction": "采用阻力",
    "sustained_value": "持续价值",
}
_OBSERVATION_LABELS = {
    "heat": "热度",
    "sentiment": "情绪",
    "coherence": "结构",
}
_OBSERVATION_VALUE_LABELS = {
    "heat": {
        "growth": "增长",
        "decline": "回落",
        "stable": "平稳",
        "plateau": "平台期",
    },
    "sentiment": {
        "positive": "正向",
        "negative": "负向",
        "neutral": "中性",
        "mixed": "分化",
        "polarized": "两极分化",
        "unified": "一致",
    },
    "coherence": {
        "ordered": "有序",
        "chaotic": "混乱",
        "fragmented": "碎片化",
        "stable": "稳定",
    },
}
_DISPLAY_TRANSLATION_CACHE: dict[tuple[str, str], str] = {}
_DEPENDENCY_IMPORT_NAME_OVERRIDES = {
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
}
_FALLBACK_RUNTIME_DEPENDENCIES = [
    "fastapi",
    "httpx",
    "pydantic",
    "pyyaml",
    "python-dotenv",
    "rich",
    "typer",
    "uvicorn",
]


class CLIError(Exception):
    """CLI 结构化错误。 / Structured CLI error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 1,
        fix: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.fix = fix
        self.extra = extra or {}


@dataclass
class LiteralText:
    """原样纯文本输出。 / Literal plain-text output rendered without Rich wrapping."""

    text: str


@dataclass
class ProgressTracker:
    """构建轮询/人读共用快照。 / Build the shared polling + human display snapshot."""

    config_file: str | None = None
    recent_events: list[dict[str, str]] = field(default_factory=list)
    estimated_waves: int | None = None
    safety_max_waves: int | None = None
    requested_max_waves: int | None = None
    effective_max_waves: int | None = None
    current_wave: int | None = None
    total_waves: int | None = None
    last_phase: str = ""
    last_progress: float = 0.0
    last_omniscient: str = ""
    last_cas_signal: str = ""
    last_deliberation: str = ""
    last_prediction_verdict: str = ""

    def apply(self, event: SimulationEvent) -> dict[str, Any]:
        self.last_phase = event.phase
        self.last_progress = float(event.progress)
        if event.total_waves is not None:
            self.total_waves = int(event.total_waves)
        if event.wave is not None:
            self.current_wave = int(event.wave) + 1
        detail = event.detail or {}
        if event.type == "phase_end" and event.phase == "INIT":
            self.estimated_waves = detail.get("estimated_waves")
            self.safety_max_waves = detail.get("safety_max_waves")
            self.requested_max_waves = detail.get("requested_max_waves")
            self.effective_max_waves = detail.get("max_waves")
        if event.type == "wave_start":
            self.last_omniscient = _localize_text_for_display(
                detail.get("global_observation"),
                config_file=self.config_file,
                context="global_observation",
                limit=140,
            )
        if event.type == "wave_end":
            self.last_cas_signal = _localize_text_for_display(
                detail.get("cas_signal"),
                config_file=self.config_file,
                context="cas_signal",
                limit=140,
            )
        if event.type == "phase_end" and event.phase == "DELIBERATE":
            self.last_deliberation = _compact_text(_deliberation_summary_text(detail), 140)
        if event.type == "phase_end" and event.phase == "SYNTHESIZE":
            self.last_prediction_verdict = _localize_text_for_display(
                detail.get("prediction_verdict"),
                config_file=self.config_file,
                context="simulation_verdict",
                limit=160,
            )
        entry = _event_entry(event, config_file=self.config_file)
        if entry is not None:
            self.recent_events.append(entry)
            self.recent_events = self.recent_events[-_MAX_RECENT_EVENTS:]
        return {
            "headline": _event_headline(event),
            "progress_bar": _progress_bar(event.progress),
            "phase_label": _PHASE_LABELS.get(event.phase, event.phase),
            "highlights": _snapshot_highlights(self, event),
            "event_lines": _event_lines(event, config_file=self.config_file),
            "recent_events": list(self.recent_events),
        }


def _skill_validation_cli_error(exc: SkillValidationError) -> CLIError:
    """保留 Skill 错误码。 / Preserve skill validation codes in CLI responses."""
    code = exc.code if exc.code in {SKILL_NOT_FOUND, SKILL_SCHEMA_INVALID} else SKILL_SCHEMA_INVALID
    return CLIError(code, exc.message)


def _project_dependency_names() -> list[str]:
    """读取项目声明依赖；若不可用则回退到内置列表。 / Read project dependencies or fall back to a curated runtime list."""
    pyproject_file = Path.cwd() / "pyproject.toml"
    if pyproject_file.is_file():
        try:
            loaded = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            loaded = {}
        project = loaded.get("project") if isinstance(loaded, dict) else {}
        dependencies = project.get("dependencies") if isinstance(project, dict) else None
        if isinstance(dependencies, list):
            names: list[str] = []
            for item in dependencies:
                if not isinstance(item, str):
                    continue
                match = re.match(r"^\s*([A-Za-z0-9_.-]+)", item)
                if match:
                    names.append(match.group(1).lower())
            if names:
                return names
    return list(_FALLBACK_RUNTIME_DEPENDENCIES)


def _dependency_import_name(package_name: str) -> str:
    """把发行包名转换成 import 名。 / Convert distribution names into import module names."""
    normalized = str(package_name or "").strip().lower()
    if normalized in _DEPENDENCY_IMPORT_NAME_OVERRIDES:
        return _DEPENDENCY_IMPORT_NAME_OVERRIDES[normalized]
    return normalized.replace("-", "_")


def _check_runtime_dependencies() -> dict[str, Any]:
    """检查关键运行时依赖是否可导入。 / Check whether runtime dependencies are importable."""
    checked: list[dict[str, Any]] = []
    missing: list[str] = []
    for package_name in _project_dependency_names():
        module_name = _dependency_import_name(package_name)
        installed = importlib.util.find_spec(module_name) is not None
        checked.append(
            {
                "package": package_name,
                "module": module_name,
                "installed": installed,
            }
        )
        if not installed:
            missing.append(package_name)
    return {
        "ok": not missing,
        "missing": missing,
        "checked": checked,
    }


def _compact_text(value: Any, limit: int = 120) -> str:
    """压缩长文本，避免终端刷屏。 / Compact long text to avoid terminal spam."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _contains_ascii_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def _strip_markdown_code_fence(text: str) -> str:
    """去掉 Markdown 代码块包裹。 / Strip markdown code fences when present."""
    stripped = str(text or "").strip()
    match = re.fullmatch(r"```(?:\w+)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _extract_display_text(value: Any) -> str:
    """从结构化 LLM 输出中提取展示文本。 / Extract display text from structured LLM output."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "文本", "translation", "translated_text", "content", "summary", "摘要", "narrative", "结论"):
            candidate = value.get(key)
            if candidate is None:
                continue
            text = _extract_display_text(candidate)
            if text:
                return text
        for candidate in value.values():
            text = _extract_display_text(candidate)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_extract_display_text(item) for item in value]
        return "；".join(part for part in parts if part)
    return str(value).strip()


def _normalize_localized_text_output(value: Any) -> str:
    """规范化本地化输出，兼容 JSON/代码块响应。 / Normalize localized output, including JSON/code-fence replies."""
    raw_text = _compact_text(value, 400).strip()
    if not raw_text:
        return ""
    candidate = _strip_markdown_code_fence(raw_text)
    with suppress(json.JSONDecodeError, TypeError, ValueError):
        parsed = json.loads(candidate)
        extracted = _extract_display_text(parsed)
        if extracted:
            return extracted
    return candidate


def _localize_text_for_display(
    value: Any,
    *,
    config_file: str | None = None,
    context: str = "",
    limit: int = 160,
    allow_llm: bool = True,
) -> str:
    """将展示文本尽量本地化为中文。 / Localize display text to Chinese when possible.

    优先保留已有中文；若仍存在英文描述，则按需调用 LLM 做简体中文翻译/压缩，
    并使用进程内缓存避免重复调用。
    / Keep existing Chinese when present; if English remains, optionally use
    the configured LLM for Simplified Chinese translation/summarization, with
    process-local caching to avoid duplicate calls.
    """
    text = _compact_text(value, max(limit * 2, limit))
    if not text:
        return ""
    if not _contains_ascii_letters(text) or not allow_llm:
        return _compact_text(text, limit)

    cache_key = (context, text)
    cached = _DISPLAY_TRANSLATION_CACHE.get(cache_key)
    if cached is not None:
        return _compact_text(cached, limit)

    config_path = str(config_file or "").strip() or _resolve_config_path(None)
    system_prompt = (
        "你是终端文本本地化助手。"
        "请将输入内容翻译或改写为自然、准确、简洁的简体中文。"
        "保留事实、数字、专有名词和语气，不要使用 Markdown，不要解释。"
    )
    user_prompt = json.dumps(
        {"context": context, "text": text},
        ensure_ascii=False,
    )
    try:
        translated = asyncio.run(
            _call_text_llm(
                config_file=config_path,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                role="omniscient",
                max_llm_calls=1,
            )
        )
        normalized = _normalize_localized_text_output(translated)
        localized = _compact_text(normalized or text, limit)
    except Exception:
        localized = _compact_text(text, limit)
    _DISPLAY_TRANSLATION_CACHE[cache_key] = localized
    return localized


def _display_text(
    value: Any,
    *,
    config_file: str | None = None,
    context: str = "",
    limit: int = 160,
    allow_llm: bool = True,
) -> str:
    """统一的展示文本入口。 / Unified display-text entrypoint."""
    if allow_llm:
        return _localize_text_for_display(
            value,
            config_file=config_file,
            context=context,
            limit=limit,
            allow_llm=True,
        )
    return _compact_text(value, limit)


def _progress_bar(progress: float) -> str:
    """文本进度条。 / Text progress bar."""
    ratio = max(0.0, min(1.0, float(progress)))
    filled = int(_PROGRESS_BAR_WIDTH * ratio)
    empty = _PROGRESS_BAR_WIDTH - filled
    return f"[{'█' * filled}{'░' * empty}] {ratio:>5.1%}"


def _agent_type_label(agent_type: str | None) -> str:
    return _AGENT_TYPE_LABELS.get(str(agent_type or "").strip(), str(agent_type or "Agent"))


def _response_type_label(response_type: str | None) -> str:
    return _RESPONSE_TYPE_LABELS.get(str(response_type or "").strip(), str(response_type or "未知响应"))


def _tribunal_role_label(role: str | None) -> str:
    return _TRIBUNAL_ROLE_LABELS.get(str(role or "").strip(), str(role or "审查员"))


def _dimension_label(name: str | None) -> str:
    return _DIMENSION_LABELS.get(str(name or "").strip(), str(name or "维度"))


def _agent_label_text(event: SimulationEvent) -> str:
    detail = event.detail or {}
    label = str(detail.get("agent_label") or event.agent_id or "未知智能体").strip()
    if event.agent_type:
        return f"{_agent_type_label(event.agent_type)}·{label}"
    return label


def _format_response_mix(value: Any) -> str:
    mix = value if isinstance(value, dict) else {}
    if not mix:
        return ""
    parts = []
    for key, count in mix.items():
        parts.append(f"{_response_type_label(str(key))}×{count}")
    return "，".join(parts)


def _score_card_compact(scores: Any, *, max_items: int = 5) -> str:
    """压缩展示评分。 / Compact score rendering."""
    if not isinstance(scores, dict):
        return ""
    parts = []
    for idx, (name, value) in enumerate(scores.items()):
        if idx >= max_items:
            break
        parts.append(f"{_dimension_label(name)}={value}")
    return "，".join(parts)


def _summarize_observation(value: Any) -> str:
    """将 OBSERVE 输出转为中文摘要。 / Convert OBSERVE output into a readable Chinese summary."""
    if isinstance(value, dict):
        phase_vector = value.get("phase_vector") or {}
        parts = []
        if isinstance(phase_vector, dict):
            for key in ("heat", "sentiment", "coherence"):
                raw = str(phase_vector.get(key) or "").strip()
                if raw:
                    translated = _OBSERVATION_VALUE_LABELS.get(key, {}).get(raw, raw)
                    parts.append(f"{_OBSERVATION_LABELS.get(key, key)}{translated}")
        transition = value.get("phase_transition_detected")
        if transition is not None:
            parts.append("发生相变" if transition else "未发生相变")
        emergence = value.get("emergence_events") or []
        if isinstance(emergence, list) and emergence:
            parts.append(f"涌现事件 {len(emergence)} 个")
        return "，".join(parts)
    return _compact_text(value, 160)


def _deliberation_summary_text(detail: dict[str, Any]) -> str:
    rounds = detail.get("rounds")
    converged = detail.get("converged")
    consensus = [_dimension_label(item) for item in list(detail.get("consensus_points") or [])]
    dissent = [_dimension_label(item) for item in list(detail.get("dissent_points") or [])]
    parts = []
    if rounds:
        parts.append(f"{rounds} 轮")
    parts.append("已收敛" if converged else "未收敛")
    if consensus:
        parts.append(f"共识：{', '.join(str(item) for item in consensus[:2])}")
    if dissent:
        parts.append(f"分歧：{', '.join(str(item) for item in dissent[:2])}")
    return "；".join(parts)


def _deliberation_round_lines(detail: dict[str, Any], *, config_file: str | None = None) -> list[str]:
    """渲染单轮合议结果。 / Render a single deliberation round result."""
    lines: list[str] = []
    summary = _deliberation_summary_text(detail)
    if summary:
        lines.append(f"本轮结论：{summary}")
    for opinion in list(detail.get("opinions") or [])[:3]:
        if not isinstance(opinion, dict):
            continue
        role = _tribunal_role_label(opinion.get("member_role"))
        score_text = _score_card_compact(opinion.get("scores"))
        narrative = _localize_text_for_display(
            opinion.get("narrative"),
            config_file=config_file,
            context=f"tribunal_round_{detail.get('round_number', '?')}_{opinion.get('member_role')}",
            limit=56,
        )
        line = f"{role}：{score_text}" if score_text else role
        if narrative:
            line += f"｜{narrative}"
        lines.append(line)
    return lines


def _snapshot_highlights(tracker: ProgressTracker, event: SimulationEvent) -> list[str]:
    """构建状态快照摘要。 / Build a compact status snapshot."""
    lines: list[str] = []
    if tracker.estimated_waves is not None:
        lines.append(f"预估轮次：{tracker.estimated_waves}")
    if tracker.requested_max_waves is not None and tracker.effective_max_waves is not None:
        if tracker.safety_max_waves is not None:
            lines.append(
                f"请求轮次：{tracker.requested_max_waves}；执行上限：{tracker.effective_max_waves}（安全上限 {tracker.safety_max_waves}）"
            )
        else:
            lines.append(f"请求轮次：{tracker.requested_max_waves}；执行上限：{tracker.effective_max_waves}")
    elif tracker.effective_max_waves is not None:
        lines.append(f"执行上限：{tracker.effective_max_waves}")
    if tracker.current_wave is not None and tracker.total_waves is not None:
        lines.append(f"当前轮次：{tracker.current_wave}/{tracker.total_waves}")
    if tracker.last_omniscient:
        lines.append(f"全视者判断：{tracker.last_omniscient}")
    if tracker.last_cas_signal and tracker.last_cas_signal != tracker.last_omniscient:
        lines.append(f"CAS 信号：{tracker.last_cas_signal}")
    if tracker.last_deliberation:
        lines.append(f"合议结论：{tracker.last_deliberation}")
    if tracker.last_prediction_verdict:
        lines.append(f"综合结论：{tracker.last_prediction_verdict}")
    return lines[:6]


def _event_lines(event: SimulationEvent, *, config_file: str | None = None) -> list[str]:
    """构建当前事件的补充说明。 / Build supplemental lines for the current event."""
    detail = event.detail or {}
    lines: list[str] = []
    if event.type == "phase_end" and event.phase == "INIT":
        star_labels = list(detail.get("star_labels") or [])
        sea_labels = list(detail.get("sea_labels") or [])
        if star_labels:
            lines.append(f"星智能体：{'、'.join(str(item) for item in star_labels[:4])}")
        if sea_labels:
            lines.append(f"海智能体：{'、'.join(str(item) for item in sea_labels[:4])}")
        if detail.get("estimated_waves") is not None:
            lines.append(f"预估轮次：{detail.get('estimated_waves')}")
        requested = detail.get("requested_max_waves")
        effective = detail.get("max_waves")
        safety = detail.get("safety_max_waves")
        if requested is None and effective is not None:
            lines.append(f"执行上限：{effective}（未显式指定 max_waves，采用安全上限）")
        elif requested is not None and effective is not None:
            suffix = f"（安全上限 {safety}）" if safety is not None else ""
            lines.append(f"请求轮次：{requested}；执行上限：{effective}{suffix}")
        if detail.get("wave_time_window"):
            lines.append(f"单轮时间窗：{detail.get('wave_time_window')}")
        if detail.get("wave_time_window_reasoning"):
            lines.append(f"CAS 判断：{_compact_text(detail.get('wave_time_window_reasoning'), 100)}")
        return lines
    if event.type == "wave_start" and detail.get("global_observation"):
        lines.append(
            f"全视者判断：{_localize_text_for_display(detail.get('global_observation'), config_file=config_file, context='global_observation', limit=120)}"
        )
        return lines
    if event.type == "agent_activated":
        if detail.get("activation_reason"):
            lines.append(f"触发原因：{_compact_text(detail.get('activation_reason'), 120)}")
        return lines
    if event.type == "agent_responded":
        preview = detail.get("response_preview") or detail.get("cluster_reaction") or detail.get("response_content")
        if preview:
            localized = _localize_text_for_display(
                preview,
                config_file=config_file,
                context="agent_response_preview",
                limit=120,
            )
            lines.append(f"传播结论：{localized}")
        if detail.get("outgoing_energy") is not None:
            lines.append(f"出能量：{detail.get('outgoing_energy')}")
        return lines
    if event.type == "wave_end":
        if detail.get("response_mix"):
            lines.append(f"响应分布：{_format_response_mix(detail.get('response_mix'))}")
        if detail.get("cas_signal"):
            lines.append(
                f"CAS 信号：{_localize_text_for_display(detail.get('cas_signal'), config_file=config_file, context='cas_signal', limit=120)}"
            )
        if detail.get("reason"):
            lines.append(
                f"终止原因：{_localize_text_for_display(detail.get('reason'), config_file=config_file, context='termination_reason', limit=120)}"
            )
        return lines
    if event.type == "round_end":
        return _deliberation_round_lines(detail, config_file=config_file)
    if event.type == "phase_end" and event.phase == "DELIBERATE":
        summary = _deliberation_summary_text(detail)
        if summary:
            lines.append(f"合议结论：{summary}")
        return lines
    if event.type == "phase_end" and event.phase == "OBSERVE" and detail.get("observation_preview"):
        lines.append(f"全局观测：{_compact_text(_summarize_observation(detail.get('observation_preview')), 120)}")
        return lines
    if event.type == "phase_end" and event.phase == "SYNTHESIZE" and detail.get("prediction_verdict"):
        lines.append(
            f"综合结论：{_localize_text_for_display(detail.get('prediction_verdict'), config_file=config_file, context='simulation_verdict', limit=120)}"
        )
        return lines
    return lines


def _render_display_snapshot(snapshot: dict[str, Any], *, include_recent: bool = True) -> str:
    """将 display 快照格式化为人类可读文本。 / Render a display snapshot to human-friendly text."""
    lines: list[str] = []
    headline = str(snapshot.get("headline") or "").strip()
    progress_bar = str(snapshot.get("progress_bar") or "").strip()
    if headline or progress_bar:
        lines.append(" ".join(part for part in [progress_bar, headline] if part).strip())
    for item in snapshot.get("highlights", []):
        lines.append(f"  {item}")
    if include_recent and snapshot.get("recent_events"):
        lines.append("  最近动态：")
        for item in list(snapshot.get("recent_events") or [])[-3:]:
            if isinstance(item, dict):
                emoji = str(item.get("emoji") or "•")
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(f"    {emoji} {text}")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    text = str(value if value not in (None, "") else "-")
    return text.replace("|", "\\|").replace("\n", "<br>")


def _markdown_table(title: str, headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = [f"## {title}", "", f"| {' | '.join(headers)} |", f"| {' | '.join('---' for _ in headers)} |"]
    for row in rows:
        lines.append(f"| {' | '.join(_markdown_cell(cell) for cell in row)} |")
    return "\n".join(lines)


def _job_overview_rows(
    payload: dict[str, Any],
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> list[tuple[str, str]]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    init = digest.get("init") if isinstance(digest.get("init"), dict) else {}
    rows: list[tuple[str, str]] = []
    if summary.get("ensemble_runs") is not None:
        rows.append(("推演次数", str(summary.get("ensemble_runs"))))
    if summary.get("total_waves") is not None:
        rows.append(("实际轮次", str(summary.get("total_waves"))))
    if init.get("estimated_waves") is not None:
        rows.append(("预估轮次", str(init.get("estimated_waves"))))
    if init.get("max_waves") is not None:
        if init.get("requested_max_waves") is None and init.get("safety_max_waves") is not None:
            rows.append(("执行上限", f"{init.get('max_waves')}（安全上限）"))
        else:
            rows.append(("执行上限", str(init.get("max_waves"))))
    if digest.get("last_response_mix"):
        rows.append(("末轮响应", _format_response_mix(digest.get("last_response_mix"))))
    if digest.get("last_global_observation"):
        rows.append(
            (
                "全视者判断",
                _display_text(
                    digest.get("last_global_observation"),
                    config_file=config_file,
                    context="last_global_observation",
                    limit=120,
                    allow_llm=allow_llm,
                ),
            )
        )
    if summary.get("prediction_verdict"):
        rows.append(
            (
                "综合结论",
                _display_text(
                    summary.get("prediction_verdict"),
                    config_file=config_file,
                    context="final_verdict",
                    limit=160,
                    allow_llm=allow_llm,
                ),
            )
        )
    if payload.get("elapsed_seconds") is not None:
        rows.append(("耗时", f"{payload.get('elapsed_seconds')} 秒"))
    return rows


def _observation_rows(digest: dict[str, Any]) -> list[list[str]]:
    observation = digest.get("observation")
    if not isinstance(observation, dict):
        return []
    phase_vector = observation.get("phase_vector") or {}
    return [[
        _OBSERVATION_VALUE_LABELS.get("heat", {}).get(str(phase_vector.get("heat") or ""), str(phase_vector.get("heat") or "-")),
        _OBSERVATION_VALUE_LABELS.get("sentiment", {}).get(str(phase_vector.get("sentiment") or ""), str(phase_vector.get("sentiment") or "-")),
        _OBSERVATION_VALUE_LABELS.get("coherence", {}).get(str(phase_vector.get("coherence") or ""), str(phase_vector.get("coherence") or "-")),
        "是" if observation.get("phase_transition_detected") else "否",
        str(len(observation.get("emergence_events") or [])),
    ]]


def _deliberation_score_rows(
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> tuple[list[str], list[list[str]]] | None:
    deliberation = digest.get("deliberation")
    if not isinstance(deliberation, dict):
        return None
    summary = deliberation.get("deliberation_summary") or {}
    final_positions = list(summary.get("final_positions") or [])
    records = list(deliberation.get("deliberation_records") or [])
    if not final_positions:
        return None

    dimensions: list[str] = []
    for item in final_positions:
        if not isinstance(item, dict):
            continue
        for dim in (item.get("scores") or {}).keys():
            if dim not in dimensions:
                dimensions.append(dim)

    narrative_map: dict[str, str] = {}
    if records:
        last_record = records[-1] if isinstance(records[-1], dict) else {}
        for opinion in list(last_record.get("opinions") or []):
            if isinstance(opinion, dict):
                narrative_map[str(opinion.get("member_role") or "")] = _compact_text(opinion.get("narrative"), 72)

    headers = ["成员"] + [_dimension_label(dim) for dim in dimensions] + ["最终判断"]
    rows: list[list[str]] = []
    for item in final_positions:
        if not isinstance(item, dict):
            continue
        role = str(item.get("member_role") or "")
        scores = item.get("scores") or {}
        row = [_tribunal_role_label(role)]
        for dim in dimensions:
            row.append(str(scores.get(dim, "-")))
        row.append(
            _display_text(
                narrative_map.get(role, ""),
                config_file=config_file,
                context=f"final_position_{role}",
                limit=72,
                allow_llm=allow_llm,
            )
            or "-"
        )
        rows.append(row)
    return headers, rows


def _deliberation_round_rows(
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> tuple[list[str], list[list[str]]] | None:
    deliberation = digest.get("deliberation")
    if not isinstance(deliberation, dict):
        return None
    records = list(deliberation.get("deliberation_records") or [])
    if not records:
        return None

    headers = ["轮次", "收敛", "共识", "分歧", "评分概览"]
    rows: list[list[str]] = []
    for record in records[:4]:
        if not isinstance(record, dict):
            continue
        opinions_summary = []
        for opinion in list(record.get("opinions") or [])[:3]:
            if not isinstance(opinion, dict):
                continue
            context_key = f"round_{record.get('round_number')}_{opinion.get('member_role')}"
            opinions_summary.append(
                f"{_tribunal_role_label(opinion.get('member_role'))}: {_score_card_compact(opinion.get('scores'), max_items=3)}｜"
                f"{_display_text(opinion.get('narrative'), config_file=config_file, context=context_key, limit=40, allow_llm=allow_llm)}"
            )
        rows.append([
            str((record.get("round_number") or 0) + 1),
            "是" if record.get("converged") else "否",
            "、".join(_dimension_label(str(item)) for item in list(record.get("consensus_points") or [])[:2]) or "-",
            "、".join(_dimension_label(str(item)) for item in list(record.get("dissent_points") or [])[:2]) or "-",
            "\n".join(opinions_summary) or "-",
        ])
    return headers, rows


def _timeline_rows(
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> tuple[list[str], list[list[str]]] | None:
    timeline = digest.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return None
    rows: list[list[str]] = []
    for item in timeline[:5]:
        if not isinstance(item, dict):
            continue
        node = str(item.get("wave") or item.get("time_from_publish") or item.get("t") or "-")
        event_text = _display_text(
            item.get("event"),
            config_file=config_file,
            context="timeline_event",
            limit=60,
            allow_llm=allow_llm,
        )
        effect = _display_text(
            item.get("effect") or "，".join(str(x) for x in list(item.get("drivers") or [])[:3]),
            config_file=config_file,
            context="timeline_effect",
            limit=60,
            allow_llm=allow_llm,
        )
        rows.append([node, event_text or "-", effect or "-"])
    return ["节点", "事件", "影响"], rows


def _render_job_summary_markdown(
    payload: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> str:
    """渲染任务关键总结 Markdown。 / Render the persisted job summary markdown."""
    lines: list[str] = []
    job_id = str(payload.get("job_id") or "").strip()
    brief = str(payload.get("job_brief") or "").strip()
    status = str(payload.get("status") or "").strip()
    if job_id:
        lines.append(f"🆔 任务 ID：{job_id}")
    if brief:
        lines.append(
            f"📝 简述：{_display_text(brief, config_file=config_file, context='job_brief', limit=160, allow_llm=allow_llm)}"
        )
    if status:
        lines.append(f"📌 状态：{_STATUS_LABELS.get(status, status)}")

    digest = _artifact_digest(payload.get("output_file")) if status == "completed" else {}
    sections: list[str] = []

    overview_rows = _job_overview_rows(payload, digest, config_file=config_file, allow_llm=allow_llm)
    if overview_rows:
        sections.append(_markdown_table("模拟总览", ["指标", "内容"], [[a, b] for a, b in overview_rows]))

    observation_rows = _observation_rows(digest)
    if observation_rows:
        sections.append(_markdown_table("全局观测", ["热度", "情绪", "结构", "相变", "涌现事件"], observation_rows))

    score_data = _deliberation_score_rows(digest, config_file=config_file, allow_llm=allow_llm)
    if score_data is not None:
        sections.append(_markdown_table("合议庭最终评分", score_data[0], score_data[1]))

    round_data = _deliberation_round_rows(digest, config_file=config_file, allow_llm=allow_llm)
    if round_data is not None:
        sections.append(_markdown_table("合议轮次回顾", round_data[0], round_data[1]))

    timeline_data = _timeline_rows(digest, config_file=config_file, allow_llm=allow_llm)
    if timeline_data is not None:
        sections.append(_markdown_table("关键时间线", timeline_data[0], timeline_data[1]))

    return "\n\n".join(part for part in ["\n".join(lines).strip(), *sections] if part).strip()


def _read_optional_text_file(path_value: Any) -> str:
    path_text = str(path_value or "").strip()
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _completed_renderables_from_artifacts(payload: dict[str, Any], *, include_report: bool) -> list[Any]:
    """从持久化产物构建 completed 展示内容。 / Build completed-job renderables from persisted artifacts."""
    renderables: list[Any] = []
    summary_text = _read_optional_text_file(payload.get("summary_md_file"))
    report_text = _read_optional_text_file(payload.get("report_md_file")) if include_report else ""
    if summary_text:
        renderables.append(summary_text)
    if report_text:
        renderables.append(report_text)
    return renderables


def _render_report_markdown(
    payload: dict[str, Any],
    report_text: str,
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> str:
    job_id = str(payload.get("job_id") or "").strip()
    brief = str(payload.get("job_brief") or "").strip()
    status = str(payload.get("status") or "").strip()
    lines = ["# Ripple 详细报告", ""]
    if job_id:
        lines.append(f"- 任务 ID：{job_id}")
    if brief:
        lines.append(
            f"- 简述：{_display_text(brief, config_file=config_file, context='job_brief', limit=160, allow_llm=allow_llm)}"
        )
    if status:
        lines.append(f"- 状态：{_STATUS_LABELS.get(status, status)}")
    if payload.get("elapsed_seconds") is not None:
        lines.append(f"- 耗时：{payload.get('elapsed_seconds')} 秒")
    lines.extend(["", "---", "", report_text.strip()])
    return "\n".join(lines).strip() + "\n"


def _render_job_payload(
    payload: dict[str, Any],
    *,
    config_file: str | None = None,
    include_report: bool = False,
    allow_llm: bool = True,
) -> Any:
    """渲染 job 结果/状态。 / Render a job result or status payload."""
    status = str(payload.get("status") or "").strip()
    if status == "completed" and not allow_llm:
        artifact_renderables = _completed_renderables_from_artifacts(
            payload,
            include_report=include_report,
        )
        if artifact_renderables:
            if payload.get("output_file"):
                artifact_renderables.append(f"🗂️ 详细日志文件：{payload.get('output_file')}")
            if payload.get("compact_log_file"):
                artifact_renderables.append(f"🪵 精简日志文件：{payload.get('compact_log_file')}")
            if payload.get("summary_md_file"):
                artifact_renderables.append(f"📘 任务总结文件：{payload.get('summary_md_file')}")
            if payload.get("report_md_file"):
                artifact_renderables.append(f"📄 详细报告文件：{payload.get('report_md_file')}")
            return artifact_renderables[0] if len(artifact_renderables) == 1 else Group(*artifact_renderables)

    lines: list[str] = []
    job_id = str(payload.get("job_id") or "").strip()
    brief = str(payload.get("job_brief") or "").strip()
    if job_id:
        lines.append(f"🆔 任务 ID：{job_id}")
    if brief:
        lines.append(
            f"📝 简述：{_display_text(brief, config_file=config_file, context='job_brief', limit=160, allow_llm=allow_llm)}"
        )
    if status:
        lines.append(f"📌 状态：{_STATUS_LABELS.get(status, status)}")
    display = payload.get("display")
    if isinstance(display, dict):
        block = _render_display_snapshot(display, include_recent=True)
        if block:
            lines.append(block)

    digest = _artifact_digest(payload.get("output_file")) if status == "completed" else {}
    renderables: list[Any] = ["\n".join(lines)] if lines else []
    overview = _render_job_overview_table(payload, digest, config_file=config_file, allow_llm=allow_llm)
    if overview is not None:
        renderables.append(overview)
    observation_table = _render_observation_table(digest)
    if observation_table is not None:
        renderables.append(observation_table)
    deliberation_scores = _render_deliberation_score_table(digest, config_file=config_file, allow_llm=allow_llm)
    if deliberation_scores is not None:
        renderables.append(deliberation_scores)
    deliberation_rounds = _render_deliberation_rounds_table(digest, config_file=config_file, allow_llm=allow_llm)
    if deliberation_rounds is not None:
        renderables.append(deliberation_rounds)
    timeline_table = _render_timeline_table(digest, config_file=config_file, allow_llm=allow_llm)
    if timeline_table is not None:
        renderables.append(timeline_table)

    if include_report:
        report_text = _read_optional_text_file(payload.get("report_md_file"))
        if report_text:
            renderables.append(report_text)

    if payload.get("output_file"):
        renderables.append(f"🗂️ 详细日志文件：{payload.get('output_file')}")
    if payload.get("compact_log_file"):
        renderables.append(f"🪵 精简日志文件：{payload.get('compact_log_file')}")
    if payload.get("summary_md_file"):
        renderables.append(f"📘 任务总结文件：{payload.get('summary_md_file')}")
    if payload.get("report_md_file"):
        renderables.append(f"📄 详细报告文件：{payload.get('report_md_file')}")

    if not renderables:
        return ""
    if len(renderables) == 1:
        return renderables[0]
    return Group(*renderables)


def _render_job_list_table(payload: dict[str, Any]) -> Table:
    """渲染 job 列表表格。 / Render the human job list table."""
    table = Table(title="Ripple 任务列表", show_lines=True)
    table.add_column("任务 ID", overflow="fold")
    table.add_column("状态", no_wrap=True)
    table.add_column("领域", overflow="fold")
    table.add_column("简述", overflow="fold")
    table.add_column("产物文件", overflow="fold")
    table.add_column("创建时间", overflow="fold")
    for item in payload.get("jobs", []):
        table.add_row(
            str(item.get("job_id") or ""),
            _STATUS_LABELS.get(str(item.get("status") or ""), str(item.get("status") or "")),
            str(item.get("skill") or ""),
            # `job list` 必须是快速本地命令，绝不能为简述展示再触发 LLM。
            # / `job list` must stay fast and local-only; never invoke the LLM for brief rendering here.
            _compact_text(item.get("brief"), 40),
            _render_job_artifacts_text(item.get("artifacts")),
            str(item.get("created_at") or ""),
        )
    return table


def _selector_options(keys: list[str], labels: dict[str, str]) -> list[dict[str, str]]:
    """构建中英双语 selector 选项。 / Build bilingual selector options."""
    options: list[dict[str, str]] = []
    for key in keys:
        key_text = str(key or "").strip()
        if not key_text:
            continue
        options.append({"zh": str(labels.get(key_text) or key_text), "en": key_text})
    return options


def _selector_summary(label: str, options: list[dict[str, str]]) -> str:
    """渲染 selector 摘要。 / Render selector summary text."""
    if not options:
        return ""
    items = "、".join(f"{item['zh']}（{item['en']}）" for item in options)
    return f"{label}：{items}"


def _domain_command_options_text(item: dict[str, Any]) -> str:
    """渲染 domain list 的命令参数列。 / Render the command-options cell for `domain list`."""
    lines = [
        _selector_summary("平台", list(item.get("platform_options") or [])),
        _selector_summary("渠道", list(item.get("channel_options") or [])),
        _selector_summary("垂直", list(item.get("vertical_options") or [])),
    ]
    content = "\n".join(line for line in lines if line)
    return content or "-"


def _schema_value_text(value: Any) -> str:
    """格式化 schema 值。 / Format schema values for human-readable display."""
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text or "-"


def _schema_selector_payload(selector_name: str, skill: LoadedSkill) -> dict[str, Any]:
    """构建 selector 选项元数据。 / Build selector metadata payload."""
    mapping = {
        "platform": {
            "options": _selector_options(sorted(skill.platform_profiles.keys()), skill.platform_labels),
            "description": "平台画像 key。可放入 JSON 顶层，也可通过 `--platform` 传入并覆盖。",
            "tier": "recommended",
        },
        "channel": {
            "options": _selector_options(sorted(skill.channel_profiles.keys()), skill.channel_labels),
            "description": "渠道画像 key。可放入 JSON 顶层，也可通过 `--channel` 传入并覆盖。",
            "tier": "optional",
        },
        "vertical": {
            "options": _selector_options(sorted(skill.vertical_profiles.keys()), skill.vertical_labels),
            "description": "垂直行业画像 key。可放入 JSON 顶层，也可通过 `--vertical` 传入并覆盖。",
            "tier": "optional",
        },
    }
    payload = dict(mapping[selector_name])
    payload["path"] = selector_name
    payload["kind"] = "selector"
    payload["available"] = bool(payload["options"])
    return payload


def _schema_field_options(selector_name: str, skill: LoadedSkill) -> list[dict[str, str]]:
    """按 selector 名称解析双语选项。 / Resolve bilingual options for a selector."""
    if selector_name == "platform":
        return _selector_options(sorted(skill.platform_profiles.keys()), skill.platform_labels)
    if selector_name == "channel":
        return _selector_options(sorted(skill.channel_profiles.keys()), skill.channel_labels)
    if selector_name == "vertical":
        return _selector_options(sorted(skill.vertical_profiles.keys()), skill.vertical_labels)
    return []


def _normalize_schema_notes(raw_notes: Any) -> list[str]:
    """归一化备注数组。 / Normalize free-form notes."""
    if isinstance(raw_notes, list):
        return [str(item).strip() for item in raw_notes if str(item).strip()]
    text = str(raw_notes or "").strip()
    return [text] if text else []


def _normalize_schema_requirements(raw_requirements: Any) -> dict[str, list[dict[str, Any]]]:
    """归一化 schema requirement 定义。 / Normalize schema requirement definitions."""
    requirements: dict[str, list[dict[str, Any]]] = {
        "required": [],
        "recommended": [],
        "optional": [],
    }
    mapping = raw_requirements if isinstance(raw_requirements, dict) else {}
    for tier in requirements:
        raw_items = mapping.get(tier, [])
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, str):
                path = item.strip()
                if not path:
                    continue
                requirements[tier].append(
                    {"path": path, "tier": tier, "kind": "field", "description": ""}
                )
                continue
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("name") or "").strip()
            if not path:
                continue
            normalized = {
                "path": path,
                "tier": tier,
                "kind": str(item.get("kind") or "field").strip() or "field",
                "description": str(item.get("description") or "").strip(),
            }
            any_of = [str(value).strip() for value in list(item.get("any_of") or []) if str(value).strip()]
            if any_of:
                normalized["any_of"] = any_of
            all_of = [str(value).strip() for value in list(item.get("all_of") or []) if str(value).strip()]
            if all_of:
                normalized["all_of"] = all_of
            suggestion = str(item.get("suggestion") or "").strip()
            if suggestion:
                normalized["suggestion"] = suggestion
            requirements[tier].append(normalized)
    return requirements


def _normalize_schema_field(raw_field: Any, skill: LoadedSkill) -> Optional[dict[str, Any]]:
    """归一化单个字段定义。 / Normalize a single schema field definition."""
    if not isinstance(raw_field, dict):
        return None
    path = str(raw_field.get("path") or "").strip()
    if not path:
        return None
    field = {
        "path": path,
        "tier": str(raw_field.get("tier") or "optional").strip() or "optional",
        "type": str(raw_field.get("type") or "string").strip() or "string",
        "description": str(raw_field.get("description") or "").strip(),
    }
    for key in ("example", "default"):
        value = raw_field.get(key)
        if value is not None and value != "":
            field[key] = value
    enum_values = raw_field.get("enum")
    if isinstance(enum_values, list):
        compact_enum = [str(item).strip() for item in enum_values if str(item).strip()]
        if compact_enum:
            field["enum"] = compact_enum
    options_from = str(raw_field.get("options_from") or "").strip()
    if options_from:
        field["options_from"] = options_from
        field["options"] = _schema_field_options(options_from, skill)
    return field


def _schema_tier_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    """统一字段/规则的层级排序。 / Unified sort key for schema tiers."""
    tier = str(item.get("tier") or "optional")
    order = {"required": 0, "recommended": 1, "optional": 2}.get(tier, 9)
    return (order, str(item.get("path") or ""))


def _normalize_schema_sections(raw_sections: Any, skill: LoadedSkill) -> list[dict[str, Any]]:
    """归一化 schema sections。 / Normalize schema sections."""
    sections: list[dict[str, Any]] = []
    if not isinstance(raw_sections, list):
        return sections
    for index, raw_section in enumerate(raw_sections):
        if not isinstance(raw_section, dict):
            continue
        fields: list[dict[str, Any]] = []
        for raw_field in list(raw_section.get("fields") or []):
            normalized_field = _normalize_schema_field(raw_field, skill)
            if normalized_field is not None:
                fields.append(normalized_field)
        fields.sort(key=_schema_tier_sort_key)
        name = str(raw_section.get("name") or f"section_{index + 1}").strip()
        sections.append(
            {
                "name": name,
                "title": str(raw_section.get("title") or name).strip() or name,
                "description": str(raw_section.get("description") or "").strip(),
                "fields": fields,
            }
        )
    return sections


def _fallback_request_schema(skill: LoadedSkill) -> dict[str, Any]:
    """生成通用 fallback schema。 / Generate a generic fallback schema."""
    return {
        "summary": "当前 Skill 未提供专用 request-schema.yaml，以下为 Ripple 通用输入契约。",
        "notes": [
            "`event` 根对象必填；至少提供一种主体文本，模拟才能建立上下文。",
            "`platform`、`channel`、`vertical` 既可以放入 JSON 顶层，也可以通过对应 CLI 参数覆盖。",
            "如需更高质量结论，建议补充标题、正文、发布源画像与历史数据。",
        ],
        "requirements": {
            "required": [
                {
                    "path": "event",
                    "kind": "object",
                    "description": "事件对象必填，承载模拟种子文本与相关上下文。",
                },
                {
                    "path": "event.seed_text",
                    "kind": "one_of",
                    "any_of": [
                        "event.title",
                        "event.body",
                        "event.content",
                        "event.text",
                        "event.summary",
                        "event.description",
                    ],
                    "description": "至少提供一种主体文本，供智能体建立传播或反馈上下文。",
                },
            ],
            "recommended": [
                {
                    "path": "platform",
                    "kind": "selector",
                    "description": "建议指定平台画像，避免平台机制退化为泛化默认值。",
                },
                {
                    "path": "source.author_profile",
                    "kind": "field",
                    "description": "建议提供发布源画像，用于估计冷启动能力和可信度。",
                },
            ],
        },
        "sections": [
            {
                "name": "request",
                "title": "请求级参数",
                "description": "控制领域选择、环境画像与模拟参数。",
                "fields": [
                    {"path": "skill", "tier": "optional", "type": "string", "description": "领域 Skill 名称。"},
                    {"path": "platform", "tier": "recommended", "type": "enum", "description": "平台画像 key。", "options_from": "platform"},
                    {"path": "channel", "tier": "optional", "type": "enum", "description": "渠道画像 key。", "options_from": "channel"},
                    {"path": "vertical", "tier": "optional", "type": "enum", "description": "垂直行业画像 key。", "options_from": "vertical"},
                    {"path": "simulation_horizon", "tier": "optional", "type": "string", "description": "模拟观察窗口，例如 `48h`、`7d`。", "example": "7d"},
                    {"path": "historical", "tier": "optional", "type": "object", "description": "历史表现、基线数据或先验信息。"},
                    {"path": "max_waves", "tier": "optional", "type": "integer", "description": "传播轮次上限；不传时 CLI 会按预估轮次自动推导安全上限。"},
                    {"path": "max_llm_calls", "tier": "optional", "type": "integer", "description": "本次任务可使用的最大 LLM 调用次数。"},
                    {"path": "ensemble_runs", "tier": "optional", "type": "integer", "description": "集成推演次数；默认 1。", "default": 1},
                    {"path": "deliberation_rounds", "tier": "optional", "type": "integer", "description": "合议庭最大轮数；默认 3。", "default": 3},
                    {"path": "random_seed", "tier": "optional", "type": "integer", "description": "随机种子，用于提高结果复现性。"},
                    {"path": "report", "tier": "optional", "type": "integer", "description": "是否生成详细报告；`1` 生成，`0` 跳过。", "enum": ["0", "1"], "default": 1},
                    {"path": "redact_input", "tier": "optional", "type": "boolean", "description": "是否对输入进行脱敏。", "default": False},
                ],
            },
            {
                "name": "event",
                "title": "事件主体",
                "description": "承载内容、产品或待验证对象的核心信息。",
                "fields": [
                    {"path": "event.title", "tier": "recommended", "type": "string", "description": "标题或主题。"},
                    {"path": "event.body", "tier": "recommended", "type": "string", "description": "正文、详细说明或传播主体。"},
                    {"path": "event.content", "tier": "optional", "type": "string", "description": "备用主体文本字段。"},
                    {"path": "event.text", "tier": "optional", "type": "string", "description": "备用主体文本字段。"},
                    {"path": "event.summary", "tier": "optional", "type": "string", "description": "摘要信息。"},
                    {"path": "event.description", "tier": "optional", "type": "string", "description": "更长的背景描述。"},
                    {"path": "event.content_type", "tier": "optional", "type": "string", "description": "内容类型，如 note、post、video、launch。", "example": "note"},
                ],
            },
            {
                "name": "source",
                "title": "发布源信息",
                "description": "可选的账号、作者或渠道背景信息。",
                "fields": [
                    {"path": "source.author_profile", "tier": "recommended", "type": "string", "description": "作者画像、账号定位或历史表现概述。"},
                    {"path": "source.summary", "tier": "optional", "type": "string", "description": "发布源摘要。"},
                ],
            },
        ],
    }


def _build_domain_schema_payload(skill: LoadedSkill) -> dict[str, Any]:
    """构建统一 schema payload。 / Build a normalized schema payload for one skill."""
    raw_schema = skill.request_schema if isinstance(skill.request_schema, dict) and skill.request_schema else _fallback_request_schema(skill)
    requirements = _normalize_schema_requirements(raw_schema.get("requirements"))
    sections = _normalize_schema_sections(raw_schema.get("sections"), skill)
    fields = [field for section in sections for field in section.get("fields", [])]
    if not any(requirements.values()):
        for field in fields:
            tier = str(field.get("tier") or "optional")
            if tier in requirements:
                requirements[tier].append(
                    {
                        "path": field.get("path"),
                        "tier": tier,
                        "kind": "field",
                        "description": str(field.get("description") or ""),
                    }
                )
    for tier_items in requirements.values():
        tier_items.sort(key=_schema_tier_sort_key)
    selectors = {
        name: _schema_selector_payload(name, skill)
        for name in ("platform", "channel", "vertical")
    }
    uses_fallback = not bool(skill.request_schema)
    return {
        "source": "skill_file" if skill.request_schema else "fallback",
        "schema_path": skill.request_schema_path or None,
        "warning": (
            "该领域未提供专用 request-schema.yaml，当前返回的是 Ripple 通用 fallback schema，仅适合基础兼容，不建议作为严格输入契约。"
            if uses_fallback
            else None
        ),
        "summary": str(raw_schema.get("summary") or "").strip()
        or f"{skill.name} 领域输入契约。",
        "notes": _normalize_schema_notes(raw_schema.get("notes")),
        "requirements": requirements,
        "selectors": selectors,
        "sections": sections,
        "fields": fields,
        "field_count": len(fields),
    }


def _build_domain_schema_document(skill: LoadedSkill) -> dict[str, Any]:
    """构建 domain schema 文档。 / Build a full domain schema document payload."""
    platform_keys = sorted(skill.platform_profiles.keys())
    channel_keys = sorted(skill.channel_profiles.keys())
    vertical_keys = sorted(skill.vertical_profiles.keys())
    return {
        "name": skill.name,
        "version": skill.version,
        "description": str(skill.description or "").strip(),
        "use_when": str(skill.meta.get("use_when") or "").strip(),
        "path": str(skill.path),
        "platforms": platform_keys,
        "platform_options": _selector_options(platform_keys, skill.platform_labels),
        "channels": channel_keys,
        "channel_options": _selector_options(channel_keys, skill.channel_labels),
        "verticals": vertical_keys,
        "vertical_options": _selector_options(vertical_keys, skill.vertical_labels),
        "schema": _build_domain_schema_payload(skill),
    }


def _render_schema_requirements_table(requirements: dict[str, list[dict[str, Any]]]) -> Table:
    """渲染 schema requirement 表格。 / Render the schema requirements table."""
    table = Table(title="字段要求", show_lines=True)
    table.add_column("层级", no_wrap=True)
    table.add_column("规则", overflow="fold")
    table.add_column("类型", no_wrap=True)
    table.add_column("候选字段", overflow="fold")
    table.add_column("说明", overflow="fold")
    for tier in ("required", "recommended", "optional"):
        for item in requirements.get(tier, []):
            candidates = "、".join(list(item.get("any_of") or item.get("all_of") or [])) or "-"
            table.add_row(
                _SCHEMA_TIER_LABELS.get(tier, tier),
                str(item.get("path") or ""),
                str(item.get("kind") or "field"),
                candidates,
                str(item.get("description") or ""),
            )
    return table


def _render_schema_section_table(section: dict[str, Any]) -> Table:
    """渲染 schema section 表格。 / Render a schema section table."""
    title = str(section.get("title") or section.get("name") or "Schema 字段").strip()
    table = Table(title=title, show_lines=True)
    table.add_column("字段路径", overflow="fold")
    table.add_column("层级", no_wrap=True)
    table.add_column("类型", no_wrap=True)
    table.add_column("说明", overflow="fold")
    table.add_column("示例 / 可选值", overflow="fold")
    for field in section.get("fields", []):
        extra = "-"
        options = field.get("options")
        if isinstance(options, list) and options:
            extra = "、".join(f"{item['zh']}（{item['en']}）" for item in options)
        elif field.get("enum"):
            extra = "、".join(str(item) for item in list(field.get("enum") or []))
        elif "example" in field:
            extra = _schema_value_text(field.get("example"))
        elif "default" in field:
            extra = f"默认：{_schema_value_text(field.get('default'))}"
        table.add_row(
            str(field.get("path") or ""),
            _SCHEMA_TIER_LABELS.get(str(field.get("tier") or ""), str(field.get("tier") or "")),
            str(field.get("type") or ""),
            str(field.get("description") or ""),
            extra,
        )
    return table


def _render_domain_schema_human(payload: dict[str, Any]) -> Group:
    """渲染单个领域 schema。 / Render a single domain schema document."""
    schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    renderables: list[Any] = [
        Rule(title=f"{payload.get('name')} 输入 Schema"),
        f"说明：{payload.get('description') or '-'}",
        f"适用场景：{payload.get('use_when') or '-'}",
        f"Schema 摘要：{schema.get('summary') or '-'}",
        f"Schema 来源：{'skill 文件' if schema.get('source') == 'skill_file' else '通用 fallback'}",
    ]
    if schema.get("schema_path"):
        renderables.append(f"Schema 文件：{schema.get('schema_path')}")
    if schema.get("warning"):
        renderables.append(f"警告：{schema.get('warning')}")
    selector_lines = [
        _selector_summary("平台", list(payload.get("platform_options") or [])),
        _selector_summary("渠道", list(payload.get("channel_options") or [])),
        _selector_summary("垂直", list(payload.get("vertical_options") or [])),
    ]
    for line in selector_lines:
        if line:
            renderables.append(line)
    for note in list(schema.get("notes") or []):
        renderables.append(f"备注：{note}")
    renderables.append(_render_schema_requirements_table(schema.get("requirements") or {}))
    for section in list(schema.get("sections") or []):
        description = str(section.get("description") or "").strip()
        if description:
            renderables.append(f"{section.get('title')}：{description}")
        renderables.append(_render_schema_section_table(section))
    return Group(*renderables)


def _selector_value_payload(value: str | None, labels: dict[str, str]) -> Optional[dict[str, str]]:
    """把单个 selector value 渲染为中英双语对象。 / Render one selector value as bilingual payload."""
    text = str(value or "").strip()
    if not text:
        return None
    return {"zh": str(labels.get(text) or text), "en": text}


def _render_shell_snippet(command: str) -> str:
    """保持 shell 代码原样输出。 / Preserve shell snippet formatting."""
    return command.rstrip()


def _quote_shell_arg(value: Any) -> str:
    """对 shell 参数进行最小转义。 / Minimal quoting for shell arguments."""
    return json.dumps(str(value), ensure_ascii=False)


def _example_command_flags(command_meta: dict[str, Any], *, action: str) -> list[str]:
    """按动作构建 CLI flags。 / Build CLI flags for a specific command action."""
    flags: list[str] = []
    if action == "validate":
        option_order = [
            ("skill", "--skill"),
            ("platform", "--platform"),
            ("channel", "--channel"),
            ("vertical", "--vertical"),
        ]
    else:
        option_order = [
            ("skill", "--skill"),
            ("platform", "--platform"),
            ("channel", "--channel"),
            ("vertical", "--vertical"),
            ("simulation_horizon", "--simulation-horizon"),
            ("max_waves", "--max-waves"),
            ("max_llm_calls", "--max-llm-calls"),
            ("ensemble_runs", "--ensemble-runs"),
            ("deliberation_rounds", "--deliberation-rounds"),
            ("random_seed", "--random-seed"),
            ("report", "--report"),
            ("output_path", "--output-path"),
        ]
    for key, flag in option_order:
        value = command_meta.get(key)
        if value is None or value == "":
            continue
        flags.append(f"{flag} {_quote_shell_arg(value)}")
    if action != "validate" and bool(command_meta.get("redact_input")):
        flags.append("--redact-input")
    return flags


def _build_example_shell_command(
    *,
    request: dict[str, Any],
    command_meta: dict[str, Any],
    action: str,
    async_mode: bool = False,
) -> str:
    """生成可直接复制执行的 heredoc 命令。 / Build a copy-pasteable heredoc command."""
    payload_json = json.dumps(request, ensure_ascii=False, indent=2)
    flags = _example_command_flags(command_meta, action=action)
    base = f"ripple-cli {action} --input -"
    if async_mode:
        base += " --async"
    if flags:
        base += " " + " ".join(flags)
    return f"cat <<'JSON' | {base}\n{payload_json}\nJSON"


def _normalize_example_request(raw_request: Any) -> dict[str, Any]:
    """归一化 example request。 / Normalize example request payload."""
    return dict(raw_request) if isinstance(raw_request, dict) else {}


def _normalize_example_command(raw_command: Any, skill: LoadedSkill) -> dict[str, Any]:
    """归一化 example 命令元数据。 / Normalize example command metadata."""
    meta = dict(raw_command) if isinstance(raw_command, dict) else {}
    meta.setdefault("skill", skill.name)
    meta.setdefault("output_path", "./outputs")
    return meta


def _build_domain_example_document(skill: LoadedSkill, example_name: str, raw_example: dict[str, Any]) -> dict[str, Any]:
    """构建单个领域示例文档。 / Build one domain example document."""
    request = _normalize_example_request(raw_example.get("request"))
    command_meta = _normalize_example_command(raw_example.get("command"), skill)
    platform_value = str(command_meta.get("platform") or request.get("platform") or "").strip()
    channel_value = str(command_meta.get("channel") or request.get("channel") or "").strip()
    vertical_value = str(command_meta.get("vertical") or request.get("vertical") or "").strip()
    selectors = {
        "platform": _selector_value_payload(platform_value, skill.platform_labels),
        "channel": _selector_value_payload(channel_value, skill.channel_labels),
        "vertical": _selector_value_payload(vertical_value, skill.vertical_labels),
    }
    return {
        "name": example_name,
        "title": str(raw_example.get("title") or example_name).strip() or example_name,
        "summary": str(raw_example.get("summary") or "").strip(),
        "use_when": str(raw_example.get("use_when") or "").strip(),
        "tags": [str(item).strip() for item in list(raw_example.get("tags") or []) if str(item).strip()],
        "path": skill.example_paths.get(example_name),
        "selectors": selectors,
        "command": command_meta,
        "request": request,
        "commands": {
            "validate": _build_example_shell_command(request=request, command_meta=command_meta, action="validate"),
            "blocking": _build_example_shell_command(request=request, command_meta=command_meta, action="job run"),
            "async": _build_example_shell_command(
                request=request,
                command_meta=command_meta,
                action="job run",
                async_mode=True,
            ),
            "status": "ripple-cli job status <job_id>",
            "wait": "ripple-cli job wait <job_id>",
            "result": "ripple-cli job result <job_id>",
            "log": "ripple-cli job log <job_id>",
        },
    }


def _build_domain_examples_payload(skill: LoadedSkill) -> dict[str, Any]:
    """构建单个领域的 examples payload。 / Build examples payload for one skill."""
    examples: list[dict[str, Any]] = []
    for example_name, raw_example in sorted(skill.example_profiles.items()):
        if not isinstance(raw_example, dict):
            continue
        examples.append(_build_domain_example_document(skill, example_name, raw_example))
    return {
        "name": skill.name,
        "version": skill.version,
        "description": str(skill.description or "").strip(),
        "use_when": str(skill.meta.get("use_when") or "").strip(),
        "example_count": len(examples),
        "examples": examples,
    }


def _build_domain_example_index_item(skill: LoadedSkill) -> dict[str, Any]:
    """构建全领域 examples 索引项。 / Build one domain examples index item."""
    payload = _build_domain_examples_payload(skill)
    index_examples: list[dict[str, Any]] = []
    for item in payload["examples"]:
        index_examples.append(
            {
                "name": item["name"],
                "title": item["title"],
                "summary": item["summary"],
                "use_when": item["use_when"],
                "tags": item["tags"],
                "selectors": item["selectors"],
                "path": item["path"],
            }
        )
    return {
        "name": payload["name"],
        "version": payload["version"],
        "description": payload["description"],
        "use_when": payload["use_when"],
        "example_count": payload["example_count"],
        "examples": index_examples,
    }


def _render_domain_example_index(payload: dict[str, Any]) -> Table:
    """渲染全领域 examples 索引表。 / Render all-domain example index table."""
    table = Table(title="领域示例索引", show_lines=True)
    table.add_column("领域", no_wrap=True)
    table.add_column("示例名", no_wrap=True)
    table.add_column("标题", overflow="fold")
    table.add_column("说明", overflow="fold")
    table.add_column("适用场景", overflow="fold")
    table.add_column("选择器", overflow="fold")
    for domain in payload.get("domains", []):
        examples = list(domain.get("examples") or [])
        if not examples:
            table.add_row(
                str(domain.get("name") or ""),
                "-",
                "该领域尚未提供示例",
                str(domain.get("description") or ""),
                str(domain.get("use_when") or ""),
                "-",
            )
            continue
        for item in examples:
            selectors = []
            for selector_name in ("platform", "channel", "vertical"):
                selector = item.get("selectors", {}).get(selector_name)
                if isinstance(selector, dict) and selector.get("en"):
                    selectors.append(f"{selector['zh']}：{selector_name}={selector['en']}")
            table.add_row(
                str(domain.get("name") or ""),
                str(item.get("name") or ""),
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("use_when") or ""),
                "；".join(selectors) or "-",
            )
    return table


def _render_domain_examples_human(payload: dict[str, Any]) -> LiteralText:
    """渲染单领域 examples 详情。 / Render detailed examples for one skill."""
    divider = "=" * 88
    section_divider = "-" * 88
    lines: list[str] = [
        divider,
        f"{payload.get('name')} 领域示例",
        divider,
        f"说明：{payload.get('description') or '-'}",
        f"适用场景：{payload.get('use_when') or '-'}",
        f"示例数量：{payload.get('example_count') or 0}",
    ]
    examples = list(payload.get("examples") or [])
    if not examples:
        lines.append("该领域尚未提供 examples。")
        return LiteralText("\n".join(lines))
    for item in examples:
        lines.extend(
            [
                section_divider,
                f"示例：{item.get('name')}",
                f"标题：{item.get('title') or '-'}",
                f"说明：{item.get('summary') or '-'}",
                f"适用场景：{item.get('use_when') or '-'}",
                f"标签：{'、'.join(list(item.get('tags') or [])) or '-'}",
            ]
        )
        for selector_name in ("platform", "channel", "vertical"):
            selector = item.get("selectors", {}).get(selector_name)
            if isinstance(selector, dict) and selector.get("en"):
                label = {
                    "platform": "平台（platform）",
                    "channel": "渠道（channel）",
                    "vertical": "垂直领域（vertical）",
                }.get(selector_name, selector_name)
                lines.append(f"{label}：{selector['zh']}（{selector['en']}）")
        if item.get("path"):
            lines.append(f"示例文件：{item.get('path')}")
        lines.append("请求 JSON：")
        lines.append(json.dumps(item.get("request") or {}, ensure_ascii=False, indent=2))
        lines.append("校验命令：")
        lines.append(_render_shell_snippet(str(item.get("commands", {}).get("validate") or "")))
        lines.append("阻塞运行命令：")
        lines.append(_render_shell_snippet(str(item.get("commands", {}).get("blocking") or "")))
        lines.append("非阻塞运行命令：")
        lines.append(_render_shell_snippet(str(item.get("commands", {}).get("async") or "")))
        lines.append(f"状态查询：{item.get('commands', {}).get('status') or '-'}")
        lines.append(f"等待完成：{item.get('commands', {}).get('wait') or '-'}")
        lines.append(f"结果查看：{item.get('commands', {}).get('result') or '-'}")
        lines.append(f"日志查看：{item.get('commands', {}).get('log') or '-'}")
    return LiteralText("\n".join(lines))


def _job_artifacts_payload(result: dict[str, Any]) -> dict[str, Optional[str]]:
    """提取 job 产物路径字段。 / Extract persisted artifact paths for a job."""
    return {
        "artifact_dir": str(result.get("artifact_dir") or "").strip() or None,
        "output_file": str(result.get("output_file") or "").strip() or None,
        "compact_log_file": str(result.get("compact_log_file") or "").strip() or None,
        "summary_md_file": str(result.get("summary_md_file") or "").strip() or None,
        "report_md_file": str(result.get("report_md_file") or "").strip() or None,
    }


def _render_job_artifacts_text(artifacts: Any) -> str:
    """渲染 job list 的产物文件单元格。 / Render the artifacts cell for `job list`."""
    artifact_map = artifacts if isinstance(artifacts, dict) else {}
    lines: list[str] = []
    ordered_items = [
        ("详细日志", artifact_map.get("output_file")),
        ("精简日志", artifact_map.get("compact_log_file")),
        ("任务总结", artifact_map.get("summary_md_file")),
        ("详细报告", artifact_map.get("report_md_file")),
        ("产物目录", artifact_map.get("artifact_dir")),
    ]
    for label, value in ordered_items:
        text = str(value or "").strip()
        if text:
            name = Path(text).name or text
            lines.append(f"{label}：{name}")
            lines.append(f"路径：{text}")
    return "\n".join(lines) if lines else "-"


def _artifact_digest(output_file: str | None) -> dict[str, Any]:
    """从输出 JSON 提炼关键过程信息。 / Extract key process insights from the output JSON."""
    path_text = str(output_file or "").strip()
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(document, dict):
        return {}

    digest: dict[str, Any] = {"document": document}
    process = document.get("process") or {}
    if isinstance(process, dict):
        init = process.get("init") or {}
        if isinstance(init, dict):
            digest["init"] = init
        waves = process.get("waves") or []
        if isinstance(waves, list) and waves:
            digest["waves"] = waves
            last_wave = waves[-1] if isinstance(waves[-1], dict) else {}
            verdict = last_wave.get("verdict") or {}
            if isinstance(verdict, dict) and verdict.get("global_observation"):
                digest["last_global_observation"] = verdict.get("global_observation")
            responses = last_wave.get("agent_responses") or {}
            if isinstance(responses, dict) and responses:
                mix: dict[str, int] = {}
                for response in responses.values():
                    if not isinstance(response, dict):
                        continue
                    key = str(response.get("response_type") or "unknown")
                    mix[key] = mix.get(key, 0) + 1
                digest["last_response_mix"] = mix
        delib = process.get("deliberation") or {}
        if isinstance(delib, dict):
            digest["deliberation"] = delib
        observation = process.get("observation") or {}
        if isinstance(observation, dict):
            digest["observation"] = observation.get("content")

    prediction = document.get("prediction")
    digest["prediction_verdict"] = _derive_prediction_verdict(document)
    if isinstance(prediction, dict):
        digest["prediction"] = prediction
    digest["timeline"] = document.get("timeline") if isinstance(document.get("timeline"), list) else []
    return digest


def _render_job_overview_table(
    payload: dict[str, Any],
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> Optional[Table]:
    """渲染总览表。 / Render the overview table."""
    rows = _job_overview_rows(payload, digest, config_file=config_file, allow_llm=allow_llm)
    if not rows:
        return None
    table = Table(title="模拟总览")
    table.add_column("指标")
    table.add_column("内容")
    for label, value in rows:
        table.add_row(label, value)
    return table


def _render_observation_table(digest: dict[str, Any]) -> Optional[Table]:
    """渲染全局观测表。 / Render the observation table."""
    rows = _observation_rows(digest)
    if not rows:
        return None
    table = Table(title="全局观测")
    table.add_column("热度")
    table.add_column("情绪")
    table.add_column("结构")
    table.add_column("相变")
    table.add_column("涌现事件")
    for row in rows:
        table.add_row(*row)
    return table


def _render_deliberation_score_table(
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> Optional[Table]:
    """渲染合议庭最终评分表。 / Render the tribunal final score table."""
    table_data = _deliberation_score_rows(digest, config_file=config_file, allow_llm=allow_llm)
    if table_data is None:
        return None
    headers, rows = table_data
    table = Table(title="合议庭最终评分")
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*row)
    return table


def _render_deliberation_rounds_table(
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> Optional[Table]:
    """渲染合议轮次摘要表。 / Render the deliberation rounds table."""
    table_data = _deliberation_round_rows(digest, config_file=config_file, allow_llm=allow_llm)
    if table_data is None:
        return None
    headers, rows = table_data
    table = Table(title="合议轮次回顾")
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*row)
    return table


def _render_timeline_table(
    digest: dict[str, Any],
    *,
    config_file: str | None = None,
    allow_llm: bool = True,
) -> Optional[Table]:
    """渲染关键时间线表。 / Render the key timeline table."""
    table_data = _timeline_rows(digest, config_file=config_file, allow_llm=allow_llm)
    if table_data is None:
        return None
    headers, rows = table_data
    table = Table(title="关键时间线")
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*row)
    return table


class OutputHandler:
    """双通道输出。 / Dual-mode output handler."""

    def __init__(self, json_mode: bool, quiet: bool = False) -> None:
        self.json_mode = json_mode
        self.quiet = quiet

    def success(self, data: dict[str, Any], human_text: Any) -> None:
        if self.json_mode:
            typer.echo(json.dumps({"ok": True, **data}, ensure_ascii=False))
            return
        if human_text is None:
            return
        if isinstance(human_text, LiteralText):
            typer.echo(human_text.text)
            return
        if isinstance(human_text, Table):
            console.print(human_text)
        elif isinstance(human_text, (dict, list)):
            console.print_json(json.dumps(human_text, ensure_ascii=False))
        else:
            console.print(human_text)

    def error(self, exc: CLIError) -> None:
        if self.json_mode:
            payload = {
                "ok": False,
                "exit_code": exc.exit_code,
                "error": {"code": exc.code, "message": exc.message},
            }
            if exc.fix:
                payload["fix"] = exc.fix
            payload.update(exc.extra)
            typer.echo(json.dumps(payload, ensure_ascii=False))
            return
        console.print(f"[red]Error:[/red] {exc.message}")
        if exc.fix:
            console.print(f"[dim]Fix: {exc.fix}[/dim]")
        for key, value in exc.extra.items():
            console.print(f"[dim]{key}: {value}[/dim]")

    def progress(self, snapshot: dict[str, Any]) -> None:
        if self.quiet:
            return
        prefix = " ".join(
            part for part in [str(snapshot.get("progress_bar") or "").strip(), str(snapshot.get("headline") or "").strip()]
            if part
        ).strip()
        if prefix:
            typer.echo(prefix, err=True)
        for line in snapshot.get("event_lines", []):
            typer.echo(f"  {line}", err=True)


class HeartbeatPump:
    """后台心跳。 / Background heartbeat pinger."""

    def __init__(self, repo: JobRepoSQLite, job_id: str, interval_seconds: int) -> None:
        self._repo = repo
        self._job_id = job_id
        self._interval_seconds = max(1, int(interval_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._repo.update_job_fields(
                self._job_id,
                heartbeat_at=_utcnow_iso(),
            )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _configure_logging(verbose: int, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    elif verbose >= 2:
        level = logging.DEBUG
    elif verbose == 1:
        level = logging.INFO
    else:
        env_level = os.getenv("RIPPLE_LOG_LEVEL", "WARNING").upper()
        level = getattr(logging, env_level, logging.WARNING)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", force=True)


def _resolve_config_path(config_path: str | None) -> str:
    return str(config_path or os.getenv("RIPPLE_LLM_CONFIG_PATH") or _DEFAULT_LLM_CONFIG_PATH)


def _resolve_db_path(db_path: str | None) -> str:
    return str(db_path or os.getenv("RIPPLE_DB_PATH") or _DEFAULT_DB_PATH)


def _resolve_output_dir() -> str:
    return str(os.getenv("RIPPLE_OUTPUT_DIR") or _DEFAULT_OUTPUT_DIR)


def _heartbeat_seconds() -> int:
    return int(os.getenv("RIPPLE_HEARTBEAT_INTERVAL", str(_DEFAULT_HEARTBEAT_SECONDS)))


def _stale_seconds() -> int:
    return int(os.getenv("RIPPLE_STALE_THRESHOLD", str(_DEFAULT_STALE_SECONDS)))


def _mask_secret(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "(missing)"
    if len(text) <= 8:
        return text[:3] + "***"
    return text[:4] + "****" + text[-4:]


def _print_enum_options(
    label: str,
    options: tuple[tuple[str, str], ...],
    current: str | None = None,
) -> None:
    current_text = str(current or "").strip()
    seen_values: set[str] = set()
    rendered: list[str] = []

    for value, display in options:
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        rendered.append(click.style(display, bold=(value == current_text)))

    if current_text and current_text not in seen_values:
        rendered.append(click.style(current_text, bold=True))

    click.echo(f"{label}可选值：{' / '.join(rendered)}", color=True)


def _load_yaml_document(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    if not path.exists():
        if allow_missing:
            return {}
        raise CLIError(
            "CONFIG_MISSING",
            f"LLM config file not found: {path}",
            exit_code=5,
            fix="Create it with `ripple-cli llm setup` or point to an existing file with --config.",
        )
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CLIError(
            "CONFIG_INVALID",
            f"Invalid YAML in config file: {exc}",
            exit_code=5,
        ) from exc
    if not isinstance(loaded, dict):
        raise CLIError(
            "CONFIG_INVALID",
            "LLM config root must be a mapping / llm_config.yaml 顶层必须是字典。",
            exit_code=5,
        )
    return loaded


def _write_yaml_document(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _load_default_config(path: Path) -> dict[str, Any]:
    data = _load_yaml_document(path)
    default = data.get("_default")
    if not isinstance(default, dict):
        raise CLIError(
            "CONFIG_INVALID",
            "The `_default` section is required in llm_config.yaml.",
            exit_code=5,
            fix="Use `ripple-cli llm setup` or `ripple-cli llm set` to create the `_default` section.",
        )
    return dict(default)


def _ensure_default_model_config(config_file: str) -> None:
    path = Path(config_file)
    if not path.exists():
        raise CLIError(
            "CONFIG_MISSING",
            f"LLM config file not found: {path}",
            exit_code=5,
            fix="Create it with `ripple-cli llm setup` or pass --config PATH.",
        )
    try:
        loader = LLMConfigLoader(config_file=config_file)
        loader.resolve("omniscient")
    except ConfigurationError as exc:
        raise CLIError(
            "CONFIG_INVALID",
            str(exc),
            exit_code=5,
            fix="Fix the `_default` section in llm_config.yaml and retry.",
        ) from exc


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _extract_json_text(text: str) -> str:
    stripped = _strip_code_fence(text)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return stripped[start : end + 1]
    return stripped


async def _call_text_llm(
    *,
    config_file: str,
    system_prompt: str,
    user_prompt: str,
    role: str = "omniscient",
    max_llm_calls: int = 5,
) -> str:
    """轻量 LLM 调用封装。 / Lightweight shared LLM call wrapper."""
    router = ModelRouter(config_file=config_file, max_llm_calls=max_llm_calls)
    if not router.check_budget(role):
        raise CLIError(
            "LLM_BUDGET_EXCEEDED",
            f"LLM call budget exceeded for role={role}",
            exit_code=4,
        )
    router.record_attempt(role)
    adapter = router.get_model_backend(role)
    content = await adapter.call(system_prompt, user_prompt)
    router.record_call(role)
    return (content or "").strip()


async def _call_json_llm(
    *,
    config_file: str,
    system_prompt: str,
    user_prompt: str,
    role: str = "omniscient",
    max_llm_calls: int = 5,
) -> dict[str, Any]:
    raw = await _call_text_llm(
        config_file=config_file,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        role=role,
        max_llm_calls=max_llm_calls,
    )
    try:
        parsed = json.loads(_extract_json_text(raw))
    except json.JSONDecodeError as exc:
        raise CLIError(
            "LLM_RESPONSE_PARSE_ERROR",
            f"Could not parse LLM JSON response: {exc}",
            exit_code=4,
        ) from exc
    if not isinstance(parsed, dict):
        raise CLIError(
            "LLM_RESPONSE_PARSE_ERROR",
            "LLM JSON response must be an object / LLM JSON 响应必须是对象。",
            exit_code=4,
        )
    return parsed


def _load_json_request(input_path: str | None) -> dict[str, Any]:
    """读取输入 JSON。 / Read request JSON from file or stdin."""
    text = ""
    if input_path and input_path != "-":
        path = Path(input_path)
        if not path.exists():
            raise CLIError("FILE_NOT_FOUND", f"Input file not found: {path}")
        text = path.read_text(encoding="utf-8")
    elif input_path == "-":
        text = sys.stdin.read()
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise CLIError(
            "INVALID_INPUT",
            "No input provided. Use --input PATH, --input -, or pipe JSON to stdin.",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CLIError("INVALID_INPUT", f"Input JSON parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise CLIError("INVALID_INPUT", "Input JSON root must be an object.")
    return data


def _coerce_positive_int(name: str, value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CLIError("INVALID_INPUT", f"{name} must be an integer.") from exc
    if number <= 0:
        raise CLIError("INVALID_INPUT", f"{name} must be > 0.")
    return number


def _ensure_output_path_writable(output_path: str | None) -> None:
    target = Path(output_path or _resolve_output_dir())
    if target.exists() and target.is_file():
        raise CLIError(
            "INVALID_INPUT",
            "`--output-path` must point to a directory, not a file.",
            fix="Pass a directory path such as `--output-path ./outputs`.",
        )
    if not target.exists() and target.suffix:
        raise CLIError(
            "INVALID_INPUT",
            "`--output-path` must be a directory path; file names are no longer supported.",
            fix="Use `--output-path ./outputs` and let Ripple create a job-specific subdirectory automatically.",
        )
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CLIError(
            "INVALID_INPUT",
            f"Output path is not writable: {target}",
        ) from exc


def _prepare_job_artifact_dir(request: dict[str, Any], job_id: str) -> Path:
    """为任务创建独立产物目录。 / Create a dedicated artifact directory for the job."""
    base_dir = Path(str(request.get("output_path") or _resolve_output_dir()))
    _ensure_output_path_writable(str(base_dir))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifact_dir = base_dir / f"{job_id}_{timestamp}"
    try:
        artifact_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        artifact_dir = base_dir / f"{job_id}_{timestamp}_{uuid.uuid4().hex[:6]}"
        artifact_dir.mkdir(parents=True, exist_ok=False)
    request["output_path"] = str(artifact_dir) + "/"
    return artifact_dir


def _build_request(
    *,
    input_path: str | None,
    skill: str | None = None,
    platform: str | None = None,
    channel: str | None = None,
    vertical: str | None = None,
    max_waves: int | None = None,
    max_llm_calls: int | None = None,
    ensemble_runs: int | None = None,
    deliberation_rounds: int | None = None,
    random_seed: int | None = None,
    output_path: str | None = None,
    report: int | None = None,
    simulation_horizon: str | None = None,
    redact_input: bool = False,
) -> dict[str, Any]:
    request = _load_json_request(input_path)
    if "llm_config" in request:
        raise CLIError(
            "INVALID_INPUT",
            "CLI does not accept inline `llm_config`; use --config or llm_config.yaml instead.",
            fix="Remove the llm_config field from the input JSON and pass --config PATH if needed.",
        )
    if skill:
        request["skill"] = skill
    request.setdefault("skill", "social-media")
    if platform:
        request["platform"] = platform
    if channel:
        request["channel"] = channel
    if vertical:
        request["vertical"] = vertical
    if max_waves is not None:
        request["max_waves"] = max_waves
    raw_max_waves = request.get("max_waves")
    if raw_max_waves is None:
        request.pop("max_waves", None)
    else:
        request["max_waves"] = _coerce_positive_int("max_waves", raw_max_waves)
    if max_llm_calls is not None:
        request["max_llm_calls"] = max_llm_calls
    request["max_llm_calls"] = _coerce_positive_int("max_llm_calls", request.get("max_llm_calls", 200))
    if ensemble_runs is not None:
        request["ensemble_runs"] = ensemble_runs
    request["ensemble_runs"] = _coerce_positive_int("ensemble_runs", request.get("ensemble_runs", 1))
    if deliberation_rounds is not None:
        request["deliberation_rounds"] = deliberation_rounds
    request["deliberation_rounds"] = _coerce_positive_int("deliberation_rounds", request.get("deliberation_rounds", 3))
    if random_seed is not None:
        request["random_seed"] = random_seed
    if output_path:
        request["output_path"] = output_path
    if report is not None:
        request["report"] = report
    report_flag = request.get("report", 1)
    try:
        request["report"] = 1 if int(report_flag) != 0 else 0
    except (TypeError, ValueError) as exc:
        raise CLIError("INVALID_INPUT", "`report` must be 0 or 1.") from exc
    if simulation_horizon:
        request["simulation_horizon"] = simulation_horizon
    if redact_input:
        request["redact_input"] = True
    request.setdefault("redact_input", False)
    return request


def _preflight_request(
    request: dict[str, Any],
    *,
    config_file: str,
) -> tuple[LoadedSkill, dict[str, Any]]:
    """本地预检。 / Deterministic local preflight."""
    items: list[dict[str, Any]] = []
    if not isinstance(request.get("event"), dict):
        raise CLIError("INVALID_INPUT", "`event` is required and must be an object.")
    items.append({"name": "input_json", "ok": True})

    skill_name = str(request.get("skill") or "").strip()
    try:
        loaded_skill = SkillManager().load(skill_name)
    except SkillValidationError as exc:
        raise _skill_validation_cli_error(exc) from exc
    items.append({"name": "skill", "ok": True})

    platform_name = str(request.get("platform") or "").strip()
    if platform_name and platform_name not in (loaded_skill.platform_profiles or {}):
        raise CLIError(
            "INVALID_INPUT",
            f"Unknown platform '{platform_name}' for skill '{loaded_skill.name}'.",
        )
    channel_name = str(request.get("channel") or "").strip()
    channel_profiles = loaded_skill.channel_profiles or {}
    if channel_name and channel_name not in channel_profiles:
        raise CLIError(
            "INVALID_INPUT",
            f"Unknown channel '{channel_name}' for skill '{loaded_skill.name}'.",
        )
    vertical_name = str(request.get("vertical") or "").strip()
    vertical_profiles = loaded_skill.vertical_profiles or {}
    if vertical_name and vertical_name not in vertical_profiles:
        raise CLIError(
            "INVALID_INPUT",
            f"Unknown vertical '{vertical_name}' for skill '{loaded_skill.name}'.",
        )
    items.append({"name": "profiles", "ok": True})

    _ensure_default_model_config(config_file)
    items.append({"name": "llm_config_default", "ok": True})

    _ensure_output_path_writable(request.get("output_path"))
    items.append({"name": "output_path", "ok": True})

    return loaded_skill, {"ok": True, "items": items}


def _fallback_job_brief(request: dict[str, Any]) -> str:
    event = request.get("event") or {}
    title = str(event.get("title") or "").strip()
    body = str(event.get("body") or "").strip()
    skill = str(request.get("skill") or "simulation")
    platform_name = str(request.get("platform") or "generic")
    if request.get("redact_input"):
        return f"模拟 {platform_name} 平台上的 {skill} 行为"
    if title:
        return f"模拟 {platform_name} 平台上的 {skill} 传播：{title[:72]}"
    if body:
        return f"模拟 {platform_name} 平台上的 {skill} 传播：{body[:72]}"
    return f"模拟 {platform_name} 平台上的 {skill} 行为"


def _resolved_job_brief(
    *,
    request: dict[str, Any],
    brief: Any,
    brief_source: Any,
) -> tuple[str, str]:
    """为历史任务提供本地中文简述兜底。 / Provide a local Chinese brief fallback for legacy jobs."""
    text = str(brief or "").strip()
    source = str(brief_source or "").strip() or ("llm" if text else "fallback")
    fallback = _fallback_job_brief(request) if isinstance(request, dict) else ""
    if text and _contains_cjk(text):
        return text, source
    if fallback and (not text or (_contains_ascii_letters(text) and not _contains_cjk(text))):
        return fallback, "fallback"
    return text or fallback, source or "fallback"


def _generate_job_brief(request: dict[str, Any], config_file: str) -> tuple[str, str]:
    """生成 job 简述。 / Generate a concise job brief."""
    fallback = _fallback_job_brief(request)
    if request.get("redact_input"):
        return fallback, "fallback"
    event = request.get("event") or {}
    system_prompt = (
        "你是终端任务摘要助手。"
        "请用简体中文返回一句简短、自然、便于人和 Agent 理解的任务描述。"
        "不要使用引号、项目符号或 Markdown。"
    )
    user_prompt = json.dumps(
        {
            "skill": request.get("skill"),
            "platform": request.get("platform"),
            "channel": request.get("channel"),
            "vertical": request.get("vertical"),
            "event": {
                "title": event.get("title"),
                "body": event.get("body"),
            },
        },
        ensure_ascii=False,
    )
    try:
        text = asyncio.run(
            _call_text_llm(
                config_file=config_file,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                role="omniscient",
                max_llm_calls=1,
            )
        )
    except Exception:
        return fallback, "fallback"
    cleaned = " ".join(_normalize_localized_text_output(text).split())
    if not cleaned:
        return fallback, "fallback"
    if _contains_ascii_letters(cleaned) and not _contains_cjk(cleaned):
        return fallback, "fallback"
    return (cleaned[:120] or fallback, "llm")


def _render_doctor_table(checks: dict[str, Any]) -> Table:
    table = Table(title="Ripple 环境自检")
    table.add_column("检查项")
    table.add_column("状态")
    table.add_column("内容")
    for name, value in checks.items():
        ok = bool(value.get("ok"))
        status = "✅" if ok else "❌"
        compact = json.dumps(value, ensure_ascii=False)
        table.add_row(name, status, compact)
    return table


def _scan_skill_files(skill: LoadedSkill) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in sorted(skill.path.rglob("*")):
        if not path.is_file():
            continue
        # 跳过 .DS_Store 等隐藏垃圾文件，避免污染 Agent 参考上下文。
        # Skip hidden junk files such as .DS_Store to avoid polluting Agent reference context.
        if any(part.startswith(".") for part in path.relative_to(skill.path).parts):
            continue
        relative = str(path.relative_to(skill.path))
        category = "meta"
        if relative == skill.meta.get("domain_profile"):
            category = "profile"
        elif skill.request_schema_path and relative == str(Path(skill.request_schema_path).relative_to(skill.path)):
            category = "schema"
        elif relative.startswith("examples/"):
            category = "example"
        elif relative.startswith("prompts/"):
            category = "prompt"
        elif relative.startswith("platforms/"):
            category = "platform"
        elif relative.startswith("rubrics/"):
            category = "rubric"
        elif relative.startswith("reports/"):
            category = "report"
        elif relative.startswith("channels/"):
            category = "channel"
        elif relative.startswith("verticals/"):
            category = "vertical"
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping non-UTF-8 skill file: %s", path)
            continue
        result[relative] = {
            "category": category,
            "content": content,
        }
    return result


def _validation_item(
    field: str,
    status: str,
    feedback: str,
    suggestion: str | None = None,
) -> dict[str, str]:
    """构建校验条目。 / Build a validation item."""
    item = {
        "field": field,
        "status": status,
        "feedback": feedback,
    }
    if suggestion:
        item["suggestion"] = suggestion
    return item


def _is_present_value(value: Any) -> bool:
    """判断值是否可视为已提供。 / Decide whether a value should count as present."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _preflight_item_ok(preflight: dict[str, Any], name: str) -> bool:
    """读取预检结果。 / Read an item from preflight results."""
    for item in list(preflight.get("items") or []):
        if isinstance(item, dict) and item.get("name") == name:
            return bool(item.get("ok"))
    return False


def _tier_payload(items: list[dict[str, str]]) -> dict[str, Any]:
    """封装 tier 输出。 / Wrap a validation tier payload."""
    satisfied_statuses = {"present", "configured", "enabled", "defaulted", "valid", "selected"}
    return {
        "satisfied": all(str(item.get("status") or "") in satisfied_statuses for item in items),
        "items": items,
    }


def _selector_suggestion(options: dict[str, Any], label: str) -> str:
    """为平台/渠道/垂直画像构建建议。 / Build selector suggestion text."""
    names = [str(name).strip() for name in options.keys() if str(name).strip()]
    if not names:
        return f"可按需要补充 {label}。"
    preview = "、".join(names[:3])
    suffix = " 等" if len(names) > 3 else ""
    return f"可补充 {label}，例如：{preview}{suffix}。"


def _missing_fields(items: list[dict[str, str]]) -> list[str]:
    """提取缺失字段名。 / Extract missing field names."""
    return [
        str(item.get("field") or "")
        for item in items
        if str(item.get("status") or "") not in {"present", "configured", "enabled", "defaulted", "valid", "selected"}
    ]


def _validation_summary(
    required: dict[str, Any],
    recommended: dict[str, Any],
    optional: dict[str, Any],
) -> str:
    """生成说人话的校验摘要。 / Build a plain-language validation summary."""
    required_missing = _missing_fields(list(required.get("items") or []))
    recommended_missing = _missing_fields(list(recommended.get("items") or []))
    optional_missing = _missing_fields(list(optional.get("items") or []))
    if required_missing:
        fields = "、".join(required_missing[:3])
        return f"当前还不建议启动模拟，缺少必需信息：{fields}。先补齐这些字段，再重新校验。"

    parts = ["输入已通过本地预检，可直接启动模拟。"]
    if recommended_missing:
        fields = "、".join(recommended_missing[:3])
        parts.append(f"建议补充：{fields}，这样更利于提高结论稳定性和可解释性。")
    else:
        parts.append("推荐信息也比较完整。")
    if optional_missing:
        fields = "、".join(optional_missing[:2])
        parts.append(f"可选项尚未补充：{fields}。")
    return " ".join(parts)


def _run_validation(skill: LoadedSkill, request: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    """执行纯本地校验。 / Run deterministic local validation only."""
    event = request.get("event") if isinstance(request.get("event"), dict) else {}
    event = event if isinstance(event, dict) else {}
    source = request.get("source") if isinstance(request.get("source"), dict) else {}
    source = source if isinstance(source, dict) else {}

    seed_present = any(
        _is_present_value(event.get(key))
        for key in ("title", "body", "content", "text", "summary", "description")
    )

    required_items = [
        _validation_item(
            "event",
            "valid",
            "事件对象结构正确，可以承载模拟种子信息。",
        ),
        _validation_item(
            "event.seed_text",
            "present" if seed_present else "missing",
            "已提供可用于传播建模的主体文本。"
            if seed_present
            else "缺少事件主体文本，智能体无法建立传播上下文。",
            None if seed_present else "至少提供 `event.title` 或 `event.body`，必要时也可使用 `event.content`。",
        ),
        _validation_item(
            "skill",
            "selected",
            f"已选择领域 Skill：{skill.name}。",
        ),
        _validation_item(
            "llm_config._default",
            "configured" if _preflight_item_ok(preflight, "llm_config_default") else "missing",
            "默认模型配置可用，任务可直接启动。"
            if _preflight_item_ok(preflight, "llm_config_default")
            else "未找到可用的 `_default` 模型配置。",
            None
            if _preflight_item_ok(preflight, "llm_config_default")
            else "请先补全 `llm_config.yaml` 的 `_default` 配置。",
        ),
        _validation_item(
            "output_path",
            "configured" if _preflight_item_ok(preflight, "output_path") else "missing",
            "结果输出目录可写。"
            if _preflight_item_ok(preflight, "output_path")
            else "结果输出目录当前不可写。",
            None if _preflight_item_ok(preflight, "output_path") else "请检查 `--output-path` 是否为可写目录。",
        ),
    ]

    platform_name = str(request.get("platform") or "").strip()
    title_present = _is_present_value(event.get("title"))
    body_present = _is_present_value(event.get("body"))
    content_type_present = _is_present_value(event.get("content_type"))
    author_profile_present = _is_present_value(source.get("author_profile"))
    historical_present = _is_present_value(request.get("historical"))
    simulation_horizon_present = _is_present_value(request.get("simulation_horizon"))

    recommended_items = [
        _validation_item(
            "event.title",
            "present" if title_present else "missing",
            "标题已提供，有助于快速建立传播主题。"
            if title_present
            else "没有标题，首轮传播意图会更模糊。",
            None if title_present else "建议补充简短明确的 `event.title`。",
        ),
        _validation_item(
            "event.body",
            "present" if body_present else "missing",
            "正文已提供，可支撑更完整的传播判断。"
            if body_present
            else "正文缺失，智能体只能依据很少的上下文推演。",
            None if body_present else "建议补充 `event.body`，说明核心内容、卖点和语气。",
        ),
        _validation_item(
            "event.content_type",
            "present" if content_type_present else "missing",
            "内容类型已声明，智能体更容易匹配传播机制。"
            if content_type_present
            else "未声明内容类型，传播机制会偏泛化。",
            None if content_type_present else "建议补充 `event.content_type`，如 note、post、video、launch。"),
        _validation_item(
            "platform",
            "selected" if platform_name else "missing",
            f"已指定平台画像：{platform_name}。"
            if platform_name
            else "未指定平台，系统只能按更泛化的平台规律模拟。",
            None if platform_name else _selector_suggestion(skill.platform_profiles, "platform"),
        ),
        _validation_item(
            "source.author_profile",
            "present" if author_profile_present else "missing",
            "已提供发布者画像，有助于判断冷启动与可信度。"
            if author_profile_present
            else "缺少发布者画像，冷启动强弱只能按默认假设估计。",
            None
            if author_profile_present
            else "建议补充 `source.author_profile`，例如账号定位、粉丝画像、历史表现。",
        ),
        _validation_item(
            "historical",
            "present" if historical_present else "missing",
            "已提供历史数据，可用于校准衰减与反馈机制。"
            if historical_present
            else "没有历史数据，模拟会更依赖默认经验参数。",
            None if historical_present else "如有过往案例或基线数据，建议写入 `historical`。",
        ),
        _validation_item(
            "simulation_horizon",
            "present" if simulation_horizon_present else "missing",
            "已指定观察窗口，终止条件更清晰。"
            if simulation_horizon_present
            else "未指定观察窗口，将使用系统默认时域。",
            None if simulation_horizon_present else "建议补充 `simulation_horizon`，例如 `48h`、`7d`。",
        ),
    ]

    channel_name = str(request.get("channel") or "").strip()
    vertical_name = str(request.get("vertical") or "").strip()
    redact_input = bool(request.get("redact_input"))
    random_seed = request.get("random_seed")
    ensemble_runs = int(request.get("ensemble_runs", 1) or 1)

    optional_items = [
        _validation_item(
            "channel",
            "selected" if channel_name else "missing",
            f"已选择渠道画像：{channel_name}。"
            if channel_name
            else "未指定渠道，将按平台默认流量环境处理。",
            None if channel_name else _selector_suggestion(skill.channel_profiles, "channel"),
        ),
        _validation_item(
            "vertical",
            "selected" if vertical_name else "missing",
            f"已选择垂直画像：{vertical_name}。"
            if vertical_name
            else "未指定垂直行业，系统不会注入更细的行业基准。",
            None if vertical_name else _selector_suggestion(skill.vertical_profiles, "vertical"),
        ),
        _validation_item(
            "redact_input",
            "enabled" if redact_input else "defaulted",
            "已启用输入脱敏，适合共享日志或对外演示。"
            if redact_input
            else "当前未启用输入脱敏。",
            None if redact_input else "如果任务内容敏感，可添加 `--redact-input`。",
        ),
        _validation_item(
            "random_seed",
            "present" if _is_present_value(random_seed) else "defaulted",
            f"已固定随机种子：{random_seed}，结果更易复现。"
            if _is_present_value(random_seed)
            else "未固定随机种子，多次运行可能存在轻微波动。",
            None if _is_present_value(random_seed) else "如需稳定复现，可传入 `--random-seed`。",
        ),
        _validation_item(
            "ensemble_runs",
            "defaulted" if ensemble_runs == 1 else "present",
            "当前使用默认单次推演，响应速度更快。"
            if ensemble_runs == 1
            else f"当前配置为 {ensemble_runs} 次集成推演，结论更稳但耗时更高。",
        ),
    ]

    required = _tier_payload(required_items)
    recommended = _tier_payload(recommended_items)
    optional = _tier_payload(optional_items)
    valid = bool(preflight.get("ok")) and bool(required.get("satisfied"))
    return {
        "valid": valid,
        "ready_to_simulate": valid,
        "summary": _validation_summary(required, recommended, optional),
        "tiers": {
            "required": required,
            "recommended": recommended,
            "optional": optional,
        },
        "skill": skill.name,
        "platform": request.get("platform"),
        "preflight": preflight,
    }


def _load_result_json(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("result_json")
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _load_request_json(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("request_json")
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _load_output_document(result: dict[str, Any]) -> dict[str, Any]:
    output_file = str(result.get("output_file") or "").strip()
    if output_file:
        path = Path(output_file)
        if not path.exists():
            raise CLIError("FILE_NOT_FOUND", f"Output file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return dict(result)


def _load_compact_log(result: dict[str, Any]) -> str:
    compact_log = str(result.get("compact_log_file") or "").strip()
    if not compact_log:
        raise CLIError("FILE_NOT_FOUND", "compact_log_file is missing from the job result.")
    path = Path(compact_log)
    if not path.exists():
        raise CLIError("FILE_NOT_FOUND", f"Compact log not found: {path}")
    return path.read_text(encoding="utf-8")


def _result_summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "prediction": doc.get("prediction"),
        "timeline": doc.get("timeline"),
        "bifurcation_points": doc.get("bifurcation_points"),
    }


def _derive_prediction_verdict(result: dict[str, Any]) -> Any:
    prediction = result.get("prediction")
    if isinstance(prediction, dict):
        return (
            prediction.get("verdict")
            or prediction.get("impact")
            or prediction.get("spread")
        )
    return result.get("prediction_verdict")


def _elapsed_seconds(row: dict[str, Any]) -> Optional[float]:
    start = _parse_iso(row.get("started_at") or row.get("created_at"))
    end = _parse_iso(row.get("completed_at"))
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _row_display(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("status_snapshot_json") or "").strip()
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
    status = str(row.get("status") or "")
    if status == "completed":
        return {
            "headline": "✅ 模拟已完成",
            "progress_bar": _progress_bar(1.0),
            "phase_label": "已完成",
            "highlights": [],
            "event_lines": [],
            "recent_events": [],
        }
    if status == "failed":
        return {
            "headline": "❌ 模拟失败",
            "progress_bar": _progress_bar(1.0),
            "phase_label": "失败",
            "highlights": [],
            "event_lines": [],
            "recent_events": [],
        }
    if status == "cancelled":
        return {
            "headline": "🛑 模拟已取消",
            "progress_bar": _progress_bar(1.0),
            "phase_label": "已取消",
            "highlights": [],
            "event_lines": [],
            "recent_events": [],
        }
    return {
        "headline": f"⏳ 任务{_STATUS_LABELS.get(status, status or '排队中')}",
        "progress_bar": _progress_bar(float(row.get("progress") or 0.0)),
        "phase_label": _PHASE_LABELS.get(str(row.get("phase") or ""), str(row.get("phase") or "")),
        "highlights": [],
        "event_lines": [],
        "recent_events": [],
    }


def _status_payload(row: dict[str, Any]) -> dict[str, Any]:
    request = _load_request_json(row)
    job_brief, job_brief_source = _resolved_job_brief(
        request=request,
        brief=row.get("job_brief"),
        brief_source=row.get("job_brief_source"),
    )
    payload = {
        "job_id": row["job_id"],
        "job_brief": job_brief,
        "job_brief_source": job_brief_source,
        "status": row.get("status"),
        "source": row.get("source"),
        "created_at": row.get("created_at"),
        "phase": row.get("phase"),
        "progress": row.get("progress") or 0.0,
        "current_wave": row.get("current_wave"),
        "total_waves": row.get("total_waves"),
        "heartbeat_at": row.get("heartbeat_at"),
        "display": _row_display(row),
    }
    elapsed = _elapsed_seconds(row)
    if elapsed is not None:
        payload["elapsed_seconds"] = elapsed
    result = _load_result_json(row)
    if result:
        payload["result"] = result
        payload["summary"] = {
            "total_waves": result.get("total_waves"),
            "prediction_verdict": _derive_prediction_verdict(result),
            "ensemble_runs": request.get("ensemble_runs", 1),
        }
        payload["artifact_dir"] = result.get("artifact_dir")
        payload["output_file"] = result.get("output_file")
        payload["compact_log_file"] = result.get("compact_log_file")
        payload["summary_md_file"] = result.get("summary_md_file")
        payload["report_md_file"] = result.get("report_md_file")
    if row.get("error_json"):
        payload["error"] = json.loads(row["error_json"])
    return payload


def _event_percent(event: SimulationEvent) -> str:
    return f"{float(event.progress) * 100:.0f}%"


def _event_entry(event: SimulationEvent, *, config_file: str | None = None) -> Optional[dict[str, str]]:
    detail = event.detail or {}
    wave_text = f"第 {(event.wave or 0) + 1} 轮" if event.wave is not None else None
    if event.type == "phase_start":
        return {"emoji": "🧭", "text": f"{_PHASE_LABELS.get(event.phase, event.phase)}开始"}
    if event.type == "phase_end":
        return {"emoji": "✅", "text": f"{_PHASE_LABELS.get(event.phase, event.phase)}完成"}
    if event.type == "wave_start":
        text = f"{wave_text}开始"
        if detail.get("global_observation"):
            text += f"：{_localize_text_for_display(detail.get('global_observation'), config_file=config_file, context='global_observation_entry', limit=48)}"
        return {"emoji": "🌊", "text": text}
    if event.type == "wave_end":
        if detail.get("terminated"):
            return {"emoji": "🛑", "text": f"{wave_text}终止：{_compact_text(detail.get('reason'), 40)}"}
        mix = _format_response_mix(detail.get("response_mix"))
        text = f"{wave_text}完成"
        if mix:
            text += f"：{mix}"
        return {"emoji": "🌊", "text": text}
    if event.type == "agent_activated":
        return {
            "emoji": "✨",
            "text": f"激活 {_agent_label_text(event)}（入能量={(event.detail or {}).get('energy', '?')}）",
        }
    if event.type == "agent_responded":
        return {
            "emoji": "📝",
            "text": f"{_agent_label_text(event)}：{_response_type_label((event.detail or {}).get('response_type'))}",
        }
    if event.type == "round_start":
        return {
            "emoji": "🧑‍⚖️",
            "text": f"第 {detail.get('round_number', '?')}/{detail.get('total_rounds', '?')} 轮合议开始",
        }
    if event.type == "round_end":
        status_text = "，已收敛" if detail.get("converged") else ""
        return {
            "emoji": "🧑‍⚖️",
            "text": f"第 {detail.get('round_number', '?')}/{detail.get('total_rounds', '?')} 轮合议结果{status_text}",
        }
    return None


def _event_headline(event: SimulationEvent) -> str:
    phase_label = _PHASE_LABELS.get(event.phase, event.phase)
    detail = event.detail or {}
    if event.type == "wave_start":
        return f"🌊 第 {(event.wave or 0) + 1}/{event.total_waves or '?'} 轮传播"
    if event.type == "round_start":
        return f"🧑‍⚖️ 第 {detail.get('round_number', '?')}/{detail.get('total_rounds', '?')} 轮合议"
    if event.type == "round_end":
        return f"🧾 第 {detail.get('round_number', '?')}/{detail.get('total_rounds', '?')} 轮合议结果"
    if event.type == "phase_end":
        return f"✅ {phase_label}完成"
    if event.type == "phase_start":
        return f"🧭 {phase_label}开始"
    if event.type == "agent_activated":
        return f"✨ 激活 {_agent_label_text(event)}"
    if event.type == "agent_responded":
        return f"📝 {_agent_label_text(event)}：{_response_type_label(detail.get('response_type'))}"
    if event.type == "wave_end":
        return f"🌊 第 {(event.wave or 0) + 1}/{event.total_waves or '?'} 轮结束"
    return f"⏳ {phase_label}"


def _event_highlights(event: SimulationEvent) -> list[str]:
    return _event_lines(event)


def _snapshot_from_status(status: str, message: str) -> dict[str, Any]:
    emoji = {
        "completed": "✅",
        "failed": "❌",
        "cancelled": "🛑",
    }.get(status, "⏳")
    return {
        "headline": f"{emoji} {message}",
        "progress_bar": _progress_bar(1.0 if status in _TERMINAL_STATUSES else 0.0),
        "phase_label": _STATUS_LABELS.get(status, status.title()),
        "highlights": [],
        "event_lines": [],
        "recent_events": [],
    }


def _update_runtime_from_event(
    repo: JobRepoSQLite,
    output: OutputHandler,
    tracker: ProgressTracker,
    job_id: str,
    event: SimulationEvent,
) -> None:
    snapshot = tracker.apply(event)
    current_wave = (int(event.wave) + 1) if event.wave is not None else None
    repo.update_runtime(
        job_id,
        phase=event.phase,
        progress=float(event.progress),
        current_wave=current_wave,
        total_waves=event.total_waves,
        heartbeat_at=_utcnow_iso(),
        snapshot=snapshot,
    )
    output.progress(snapshot)


def _artifact_dir_from_result(result: dict[str, Any], request: dict[str, Any] | None = None) -> Optional[Path]:
    """定位任务产物目录。 / Locate the artifact directory for the job."""
    output_file = str(result.get("output_file") or "").strip()
    if output_file:
        return Path(output_file).resolve().parent
    output_dir = str((request or {}).get("output_path") or "").strip()
    if output_dir:
        return Path(output_dir).resolve()
    return None


def _write_text_artifact(path: Path, content: str) -> str:
    """写入 UTF-8 文本产物。 / Write a UTF-8 text artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if content.endswith("\n") else content + "\n"
    path.write_text(text, encoding="utf-8")
    return str(path.resolve())


def _build_job_result_payload(
    job_id: str,
    request: dict[str, Any],
    result: dict[str, Any],
    brief: str,
    brief_source: str,
    status: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "job_brief": brief,
        "job_brief_source": brief_source,
        "status": status,
        "elapsed_seconds": None,
        "artifact_dir": result.get("artifact_dir"),
        "output_file": result.get("output_file"),
        "compact_log_file": result.get("compact_log_file"),
        "summary_md_file": result.get("summary_md_file"),
        "report_md_file": result.get("report_md_file"),
        "summary": {
            "total_waves": result.get("total_waves"),
            "prediction_verdict": _derive_prediction_verdict(result),
            "ensemble_runs": request.get("ensemble_runs", 1),
        },
        "disclaimer": result.get("disclaimer"),
    }


def _run_simulation_job(
    *,
    repo: JobRepoSQLite,
    request: dict[str, Any],
    job_id: str,
    config_file: str,
    output: OutputHandler,
    brief: str,
    brief_source: str,
) -> dict[str, Any]:
    tracker = ProgressTracker(config_file=config_file)
    pump = HeartbeatPump(repo, job_id, _heartbeat_seconds())
    simulation_request = dict(request)
    report_enabled = bool(int(simulation_request.pop("report", 1)))

    async def on_progress(event: SimulationEvent) -> None:
        _update_runtime_from_event(repo, output, tracker, job_id, event)

    pump.start()
    try:
        result = asyncio.run(simulate(config_file=config_file, on_progress=on_progress, **simulation_request))
    except KeyboardInterrupt as exc:
        repo.request_cancel(job_id)
        repo.update_runtime(
            job_id,
            heartbeat_at=_utcnow_iso(),
            snapshot=_snapshot_from_status("cancelled", "模拟已取消"),
        )
        repo.update_status(job_id, "cancelled")
        raise CLIError("USER_INTERRUPT", "模拟已被用户中断。", exit_code=130) from exc
    except ConfigurationError as exc:
        repo.set_error(job_id, {"code": "CONFIG_INVALID", "message": str(exc)})
        repo.update_runtime(
            job_id,
            heartbeat_at=_utcnow_iso(),
            snapshot=_snapshot_from_status("failed", "模拟失败"),
        )
        repo.update_status(job_id, "failed")
        raise CLIError("CONFIG_INVALID", str(exc), exit_code=5) from exc
    except Exception as exc:
        repo.set_error(job_id, {"code": "SIMULATION_FAILED", "message": str(exc)})
        repo.update_runtime(
            job_id,
            heartbeat_at=_utcnow_iso(),
            snapshot=_snapshot_from_status("failed", "模拟失败"),
        )
        repo.update_status(job_id, "failed")
        raise CLIError("LLM_UNAVAILABLE", str(exc), exit_code=4) from exc
    finally:
        pump.stop()

    artifact_dir = _artifact_dir_from_result(result, request)
    if artifact_dir is not None:
        result["artifact_dir"] = str(artifact_dir.resolve())

    if report_enabled and artifact_dir is not None:
        try:
            report_body = asyncio.run(
                generate_skill_report_from_result(
                    result=result,
                    request=request,
                    config_file=config_file,
                )
            )
            if report_body:
                preview_payload = _build_job_result_payload(job_id, request, result, brief, brief_source, "completed")
                report_markdown = _render_report_markdown(
                    preview_payload,
                    report_body,
                    config_file=config_file,
                    allow_llm=False,
                )
                result["report_md_file"] = _write_text_artifact(artifact_dir / "report.md", report_markdown)
        except Exception as exc:
            logger.warning("failed to generate report artifact for %s: %s", job_id, exc)

    if artifact_dir is not None:
        preview_payload = _build_job_result_payload(job_id, request, result, brief, brief_source, "completed")
        summary_markdown = _render_job_summary_markdown(preview_payload, config_file=config_file, allow_llm=False)
        result["summary_md_file"] = _write_text_artifact(artifact_dir / "summary.md", summary_markdown)
        preview_payload["summary_md_file"] = result["summary_md_file"]

    repo.set_result(job_id, result)
    repo.update_runtime(
        job_id,
        phase="SYNTHESIZE",
        progress=1.0,
        heartbeat_at=_utcnow_iso(),
        snapshot=_snapshot_from_status("completed", "模拟完成"),
    )
    repo.update_status(job_id, "completed")
    row = repo.get_job(job_id)
    payload = _build_job_result_payload(job_id, request, result, brief, brief_source, "completed")
    payload["elapsed_seconds"] = _elapsed_seconds(row)
    return payload


def _spawn_worker(*, job_id: str, db_path: str, config_file: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ripple.cli.app",
            "_worker",
            "--db",
            db_path,
            "--config",
            config_file,
            job_id,
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=log_path.open("a", encoding="utf-8"),
    )


def _active_lock_conflict(repo: JobRepoSQLite, job_id: str) -> Optional[str]:
    return repo.try_acquire_active_job_lock(
        job_id,
        now_iso=_utcnow_iso(),
        worker_pid=os.getpid(),
        stale_threshold_seconds=_stale_seconds(),
    )


def _handle_cli_error(output: OutputHandler, exc: CLIError) -> None:
    output.error(exc)
    raise typer.Exit(exc.exit_code)


@app.command(
    cls=ChineseHelpCommand,
    help="查看 Ripple CLI 自身版本、Python 版本以及当前操作系统/架构信息。",
    short_help="查看版本信息",
    epilog="示例：\n  ripple-cli version\n  ripple-cli version --json",
)
def version(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    payload = {
        "version": get_version(),
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "platform": runtime_platform.system().lower(),
        "arch": runtime_platform.machine(),
    }
    output.success(payload, f"Ripple {payload['version']}")


@app.command(
    cls=ChineseHelpCommand,
    help="执行本地环境自检，检查 Python、LLM 配置、SQLite、输出目录、技能发现以及运行锁状态。",
    short_help="执行环境自检",
    epilog="示例：\n  ripple-cli doctor\n  ripple-cli doctor --config ./llm_config.yaml --db ./data/ripple.db",
)
def doctor(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    verbose: VerboseOption = 0,
    config_path: ConfigOption = None,
    db_path: DbOption = None,
) -> None:
    output = OutputHandler(json_mode, quiet)
    _configure_logging(verbose, quiet)
    try:
        config_file = _resolve_config_path(config_path)
        db_file = _resolve_db_path(db_path)
        repo = JobRepoSQLite(db_file)
        repo.init_schema()
        checks = {
            "python": {
                "ok": sys.version_info >= (3, 11),
                "value": ".".join(str(part) for part in sys.version_info[:3]),
                "required": ">=3.11",
            },
            "dependencies": _check_runtime_dependencies(),
        }
        try:
            _ensure_default_model_config(config_file)
            checks["llm_config"] = {"ok": True, "path": config_file}
        except CLIError as exc:
            checks["llm_config"] = {"ok": False, "path": config_file, "message": exc.message}
        checks["sqlite"] = {"ok": True, "path": db_file}
        output_dir = Path(_resolve_output_dir())
        output_dir.mkdir(parents=True, exist_ok=True)
        checks["output_dir"] = {"ok": output_dir.exists(), "path": str(output_dir)}
        skills = SkillManager().discover()
        checks["skills"] = {
            "ok": len(skills) > 0,
            "count": len(skills),
            "names": [entry["name"] for entry in skills],
        }
        running_page = repo.list_jobs(status="running", limit=1, offset=0)
        running_job_id = running_page["jobs"][0]["job_id"] if running_page["jobs"] else None
        checks["job_lock"] = {
            "ok": running_job_id is None,
            "status": "idle" if running_job_id is None else "running",
            "running_job_id": running_job_id,
        }
        payload = {"checks": checks}
        overall_ok = all(bool(item.get("ok")) for item in checks.values())
        payload["ok"] = overall_ok
        output.success(payload, _render_doctor_table(checks))
        if not overall_ok:
            raise typer.Exit(1)
    except CLIError as exc:
        _handle_cli_error(output, exc)


@llm_app.command(
    "show",
    cls=ChineseHelpCommand,
    help="显示当前 `_default` 模型配置，并自动遮盖 API Key 等敏感信息。",
    short_help="查看当前 LLM 配置",
    epilog="示例：\n  ripple-cli llm show\n  ripple-cli llm show --config ./llm_config.yaml --json",
)
def llm_show(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    config_path: ConfigOption = None,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        path = Path(_resolve_config_path(config_path))
        default = _load_default_config(path)
        payload = {
            "config_path": str(path),
            "default": {
                **default,
                "api_key": _mask_secret(str(default.get("api_key") or "")),
            },
        }
        output.success(payload, payload)
    except CLIError as exc:
        _handle_cli_error(output, exc)


@llm_app.command(
    "set",
    cls=ChineseHelpCommand,
    help="写入或更新 `llm_config.yaml` 中 `_default` 段的字段。未传入的字段会保持原值。`model_platform` 推荐只使用 `openai`（包括所有openai兼容模型）、`anthropic`（包括所有anthropic兼容模型）、`bedrock` 这 3 类用户心智更清晰的标识。",
    short_help="设置 LLM 配置",
    epilog=(
        "示例：\n"
        "  ripple-cli llm set --platform openai --model gpt-5 --api-key sk-xxx\n"
        "  ripple-cli llm set --url https://example.com/v1 --api-mode responses"
    ),
)
def llm_set(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    config_path: ConfigOption = None,
    platform_name: Annotated[
        Optional[str],
        typer.Option(
            "--platform",
            help="模型平台标识。推荐值：`openai`（包括所有openai兼容模型）、`anthropic`（包括所有anthropic兼容模型）、`bedrock`。",
        ),
    ] = None,
    model_name: Annotated[
        Optional[str],
        typer.Option("--model", help="模型名称，例如 `gpt-5`、`gpt-4.1`。"),
    ] = None,
    api_key: Annotated[
        Optional[str],
        typer.Option("--api-key", help="LLM 服务 API Key。命令输出会自动打码，不会明文回显。"),
    ] = None,
    url: Annotated[
        Optional[str],
        typer.Option("--url", help="LLM 服务根地址或兼容 OpenAI 的 API Base URL。"),
    ] = None,
    api_mode: Annotated[
        Optional[str],
        typer.Option("--api-mode", help="请求模式，例如 `responses`。一般保持与服务端兼容即可。"),
    ] = None,
    temperature: Annotated[
        Optional[float],
        typer.Option("--temperature", help="模型温度参数。值越高越发散，值越低越稳定。"),
    ] = None,
    max_retries: Annotated[
        Optional[int],
        typer.Option("--max-retries", help="单次 LLM 请求失败后的最大重试次数。"),
    ] = None,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        path = Path(_resolve_config_path(config_path))
        data = _load_yaml_document(path)
        default = data.get("_default")
        if not isinstance(default, dict):
            default = {}
        if platform_name is not None:
            default["model_platform"] = platform_name
        if model_name is not None:
            default["model_name"] = model_name
        if api_key is not None:
            default["api_key"] = api_key
        if url is not None:
            default["url"] = url
        if api_mode is not None:
            default["api_mode"] = api_mode
        if temperature is not None:
            default["temperature"] = temperature
        if max_retries is not None:
            default["max_retries"] = max_retries
        data["_default"] = default
        _write_yaml_document(path, data)
        payload = {
            "config_path": str(path),
            "default": {
                **default,
                "api_key": _mask_secret(str(default.get("api_key") or "")),
            },
        }
        output.success(payload, payload)
    except CLIError as exc:
        _handle_cli_error(output, exc)


@llm_app.command(
    "setup",
    cls=ChineseHelpCommand,
    help="交互式创建最小可用的 `_default` LLM 配置。更适合人类手动配置；Agent 更推荐用 `llm set`。",
    short_help="交互式创建 LLM 配置",
    epilog="示例：\n  ripple-cli llm setup\n  ripple-cli llm setup --config ./llm_config.yaml",
)
def llm_setup(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    config_path: ConfigOption = None,
) -> None:
    output = OutputHandler(json_mode, quiet)
    if json_mode:
        _handle_cli_error(
            output,
            CLIError(
                "INVALID_INPUT",
                "`llm setup` is interactive. Agents should use `llm set` instead.",
            ),
        )
    try:
        path = Path(_resolve_config_path(config_path))
        data = _load_yaml_document(path, allow_missing=True)
        existing_default = data.get("_default")
        if not isinstance(existing_default, dict):
            existing_default = {}

        _print_enum_options(
            "模型平台",
            _LLM_PLATFORM_OPTIONS,
            str(existing_default.get("model_platform") or ""),
        )
        platform_name = typer.prompt(
            "模型平台",
            default=str(existing_default.get("model_platform") or "openai"),
        )

        existing_model_name = str(existing_default.get("model_name") or "").strip()
        if existing_model_name:
            model_name = typer.prompt("模型名称", default=existing_model_name)
        else:
            model_name = typer.prompt("模型名称")

        existing_api_key = str(existing_default.get("api_key") or "")
        if existing_api_key:
            entered_api_key = typer.prompt(
                "API Key（已检测到现有配置，直接回车保留）",
                default="",
                show_default=False,
                hide_input=True,
            )
            api_key = existing_api_key if entered_api_key == "" else entered_api_key
        else:
            api_key = typer.prompt("API Key", hide_input=True)

        url = typer.prompt(
            "接口地址",
            default=str(existing_default.get("url") or ""),
        )
        platform_key = platform_name.strip().lower()
        if platform_key == "openai":
            existing_api_mode = str(existing_default.get("api_mode") or "").strip()
            if existing_api_mode not in {"chat_completions", "responses"}:
                existing_api_mode = "responses"
            _print_enum_options(
                "API 模式",
                _LLM_SETUP_OPENAI_API_MODE_OPTIONS,
                existing_api_mode,
            )
            api_mode = typer.prompt(
                "API 模式",
                default=existing_api_mode,
            )
        else:
            api_mode = _LLM_SETUP_FIXED_API_MODES.get(
                platform_key,
                str(existing_default.get("api_mode") or "responses"),
            )
        temperature_default = existing_default.get("temperature")
        if temperature_default in (None, ""):
            temperature_default = "0.7"
        temperature = float(
            typer.prompt(
                "温度参数",
                default=str(temperature_default),
            )
        )
        max_retries_default = existing_default.get("max_retries")
        if max_retries_default in (None, ""):
            max_retries_default = "3"
        max_retries = int(
            typer.prompt(
                "最大重试次数",
                default=str(max_retries_default),
            )
        )

        default = dict(existing_default)
        default.update(
            {
                "model_platform": platform_name,
                "model_name": model_name,
                "api_key": api_key,
                "url": url,
                "api_mode": api_mode,
                "temperature": temperature,
                "max_retries": max_retries,
            }
        )
        data["_default"] = default
        _write_yaml_document(path, data)
        output.success({"config_path": str(path)}, f"✅ 已写入配置：{path}")
    except CLIError as exc:
        _handle_cli_error(output, exc)


@llm_app.command(
    "test",
    cls=ChineseHelpCommand,
    help="使用 `_default` 模型发送一次极小请求，验证 API Key、模型名称和接口地址是否可用。",
    short_help="测试 LLM 连通性",
    epilog="示例：\n  ripple-cli llm test\n  ripple-cli llm test --config ./llm_config.yaml --json",
)
def llm_test(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    config_path: ConfigOption = None,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        config_file = _resolve_config_path(config_path)
        default = _load_default_config(Path(config_file))
        started = time.perf_counter()
        response = asyncio.run(
            _call_text_llm(
                config_file=config_file,
                system_prompt="You are a connectivity probe.",
                user_prompt="Reply with: ok",
                role="omniscient",
                max_llm_calls=1,
            )
        )
        payload = {
            "platform": default.get("model_platform"),
            "model": default.get("model_name"),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "response": response,
        }
        output.success(payload, payload)
    except CLIError as exc:
        _handle_cli_error(output, exc)
    except Exception as exc:
        _handle_cli_error(
            output,
            CLIError(
                "LLM_UNAVAILABLE",
                str(exc),
                exit_code=4,
                fix="Check the API key, model, and endpoint in llm_config.yaml.",
            ),
        )


@domain_app.command(
    "list",
    cls=ChineseHelpCommand,
    help="列出当前工作区可发现的全部领域 Skill，以及它们的版本、说明、适用场景和支持平台。",
    short_help="列出所有领域 Skill",
    epilog="示例：\n  ripple-cli domain list\n  ripple-cli domain list --json",
)
def domain_list(
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    manager = SkillManager()
    domains = []
    for entry in manager.discover():
        skill = manager.load(entry["name"], skill_path=Path(entry["path"]))
        platform_keys = sorted(skill.platform_profiles.keys())
        channel_keys = sorted(skill.channel_profiles.keys())
        vertical_keys = sorted(skill.vertical_profiles.keys())
        domains.append(
            {
                "name": skill.name,
                "version": skill.version,
                "description": str(skill.description or "").strip(),
                "platforms": platform_keys,
                "platform_options": _selector_options(platform_keys, skill.platform_labels),
                "channels": channel_keys,
                "channel_options": _selector_options(channel_keys, skill.channel_labels),
                "verticals": vertical_keys,
                "vertical_options": _selector_options(vertical_keys, skill.vertical_labels),
                "use_when": str(skill.meta.get("use_when") or "").strip(),
                "path": str(skill.path),
            }
        )
    payload = {"domains": domains}
    if json_mode:
        output.success(payload, payload)
        return
    table = Table(title="领域技能列表", show_lines=True)
    table.add_column("名称", no_wrap=True)
    table.add_column("版本", no_wrap=True)
    table.add_column("说明", overflow="fold")
    table.add_column("适用场景", overflow="fold")
    table.add_column("命令参数", overflow="fold")
    for item in domains:
        table.add_row(
            item["name"],
            item["version"],
            item["description"],
            item["use_when"],
            _domain_command_options_text(item),
        )
    output.success(payload, table)


@domain_app.command(
    "info",
    cls=ChineseHelpCommand,
    help="查看单个领域 Skill 的详细元信息，包括提示词角色、支持平台、报告模板与执行阶段。",
    short_help="查看单个 Skill 详情",
    epilog="示例：\n  ripple-cli domain info social-media\n  ripple-cli domain info pmf-validation --json",
)
def domain_info(
    name: Annotated[str, typer.Argument(help="领域 Skill 名称，例如 `social-media`、`pmf-validation`。")],
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        skill = SkillManager().load(name)
        platform_keys = sorted(skill.platform_profiles.keys())
        channel_keys = sorted(skill.channel_profiles.keys())
        vertical_keys = sorted(skill.vertical_profiles.keys())
        payload = {
            "name": skill.name,
            "version": skill.version,
            "description": str(skill.description or "").strip(),
            "use_when": str(skill.meta.get("use_when") or "").strip(),
            "path": str(skill.path),
            "prompts": sorted(skill.prompts.keys()),
            "has_tribunal": "tribunal" in skill.prompts,
            "domain_profile": skill.meta.get("domain_profile", ""),
            "platforms": platform_keys,
            "platform_options": _selector_options(platform_keys, skill.platform_labels),
            "rubrics": sorted(skill.rubrics.keys()),
            "reports": sorted(skill.report_profiles.keys()),
            "schema_available": bool(skill.request_schema),
            "request_schema_path": skill.request_schema_path or None,
            "example_count": len(skill.example_profiles),
            "examples": sorted(skill.example_profiles.keys()),
            "channels": channel_keys,
            "channel_options": _selector_options(channel_keys, skill.channel_labels),
            "verticals": vertical_keys,
            "vertical_options": _selector_options(vertical_keys, skill.vertical_labels),
            "phases": (
                ["INIT", "SEED", "RIPPLE", "OBSERVE", "DELIBERATE", "SYNTHESIZE"]
                if "tribunal" in skill.prompts
                else ["INIT", "SEED", "RIPPLE", "OBSERVE", "SYNTHESIZE"]
            ),
        }
        output.success(payload, payload)
    except SkillValidationError as exc:
        _handle_cli_error(output, _skill_validation_cli_error(exc))


@domain_app.command(
    "schema",
    cls=ChineseHelpCommand,
    help="查看领域输入 Schema。传领域名时返回单个领域的详细字段契约；不传时返回所有可用领域的 Schema，便于 Agent 统一发现和消费。",
    short_help="查看领域输入 Schema",
    epilog=(
        "示例：\n"
        "  ripple-cli domain schema\n"
        "  ripple-cli domain schema social-media\n"
        "  ripple-cli domain schema pmf-validation --json"
    ),
)
def domain_schema(
    name: Annotated[
        Optional[str],
        typer.Argument(help="领域 Skill 名称；不传时返回所有可用领域的 Schema。"),
    ] = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        manager = SkillManager()
        if name:
            payload = _build_domain_schema_document(manager.load(name))
            output.success(payload, payload if json_mode else _render_domain_schema_human(payload))
            return

        documents = []
        for entry in manager.discover():
            skill = manager.load(entry["name"], skill_path=Path(entry["path"]))
            documents.append(_build_domain_schema_document(skill))
        payload = {"domains": documents}
        if json_mode:
            output.success(payload, payload)
            return
        renderables: list[Any] = []
        for document in documents:
            renderables.append(_render_domain_schema_human(document))
        output.success(payload, Group(*renderables) if renderables else "未发现任何领域 Skill。")
    except SkillValidationError as exc:
        _handle_cli_error(output, _skill_validation_cli_error(exc))


@domain_app.command(
    "example",
    cls=ChineseHelpCommand,
    help="查看领域示例。传领域名时返回该领域的完整详细示例；不传时返回所有领域的示例索引，便于人类和 Agent 先发现可用示例再按需展开。",
    short_help="查看领域示例",
    epilog=(
        "示例：\n"
        "  ripple-cli domain example\n"
        "  ripple-cli domain example social-media\n"
        "  ripple-cli domain example pmf-validation --json"
    ),
)
def domain_example(
    name: Annotated[
        Optional[str],
        typer.Argument(help="领域 Skill 名称；不传时返回所有领域的示例索引。"),
    ] = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        manager = SkillManager()
        if name:
            payload = _build_domain_examples_payload(manager.load(name))
            output.success(payload, payload if json_mode else _render_domain_examples_human(payload))
            return

        domains = []
        for entry in manager.discover():
            skill = manager.load(entry["name"], skill_path=Path(entry["path"]))
            domains.append(_build_domain_example_index_item(skill))
        payload = {"domains": domains}
        output.success(payload, payload if json_mode else _render_domain_example_index(payload))
    except SkillValidationError as exc:
        _handle_cli_error(output, _skill_validation_cli_error(exc))


@domain_app.command(
    "dump",
    cls=ChineseHelpCommand,
    help="导出领域 Skill 的原始文件内容，便于人或 Agent 直接查看提示词、画像、报告模板等细节。",
    short_help="导出 Skill 文件内容",
    epilog=(
        "示例：\n"
        "  ripple-cli domain dump social-media\n"
        "  ripple-cli domain dump social-media --section schema\n"
        "  ripple-cli domain dump social-media --section examples\n"
        "  ripple-cli domain dump social-media --section reports\n"
        "  ripple-cli domain dump social-media --file prompts/omniscient.md --json"
    ),
)
def domain_dump(
    name: Annotated[str, typer.Argument(help="领域 Skill 名称，例如 `social-media`。")],
    file_name: Annotated[
        Optional[str],
        typer.Option("--file", help="仅导出指定文件。可传相对路径，如 `prompts/omniscient.md`，也可只传文件名。"),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            help="仅导出某一类文件，例如 `schema`、`examples`、`prompts`、`platforms`、`reports`、`channels`、`verticals`。",
        ),
    ] = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        skill = SkillManager().load(name)
        files = _scan_skill_files(skill)
        if file_name:
            files = {
                path: meta
                for path, meta in files.items()
                if path == file_name or Path(path).name == file_name
            }
        if section:
            files = {
                path: meta
                for path, meta in files.items()
                if meta["category"] == section.rstrip("s")
                or path.startswith(section.rstrip("s") + "/")
            }
        payload = {"name": skill.name, "files": files}
        output.success(payload, payload)
    except SkillValidationError as exc:
        _handle_cli_error(output, _skill_validation_cli_error(exc))


@app.command(
    cls=ChineseHelpCommand,
    help="对模拟输入执行纯本地校验，不调用 LLM。适合在真正运行任务前快速检查字段是否齐全、Skill/平台是否存在以及输出目录是否可写。",
    short_help="校验模拟输入",
    epilog=(
        "示例：\n"
        "  ripple-cli validate --input request.json\n"
        "  ripple-cli validate --input request.json --json\n"
        "  ripple-cli domain example social-media"
    ),
)
def validate(
    input_path: InputOption = None,
    skill: Annotated[
        Optional[str],
        typer.Option("--skill", help="领域 Skill 名称。未传时默认使用 `social-media`。"),
    ] = None,
    platform_name: Annotated[
        Optional[str],
        typer.Option("--platform", help="平台画像名称，例如 `xiaohongshu`。仅当该 Skill 提供对应平台画像时可用。"),
    ] = None,
    channel: Annotated[
        Optional[str],
        typer.Option("--channel", help="渠道画像名称，例如某个流量来源或触点类型。"),
    ] = None,
    vertical: Annotated[
        Optional[str],
        typer.Option("--vertical", help="垂直行业画像名称，用于加载更细的行业基准。"),
    ] = None,
    config_path: ConfigOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    verbose: VerboseOption = 0,
) -> None:
    output = OutputHandler(json_mode, quiet)
    _configure_logging(verbose, quiet)
    try:
        config_file = _resolve_config_path(config_path)
        request = _build_request(
            input_path=input_path,
            skill=skill,
            platform=platform_name,
            channel=channel,
            vertical=vertical,
        )
        loaded_skill, preflight = _preflight_request(request, config_file=config_file)
        payload = _run_validation(loaded_skill, request, preflight)
        output.success(payload, payload)
        if not payload.get("ready_to_simulate"):
            raise typer.Exit(1)
    except CLIError as exc:
        _handle_cli_error(output, exc)


@job_app.command(
    "run",
    cls=ChineseHelpCommand,
    help="启动一个模拟任务。默认阻塞执行并持续显示中文进度；加 `--async` 后会立即返回 `job_id`，由后台 worker 非阻塞执行。",
    short_help="启动模拟任务",
    epilog=(
        "示例：\n"
        "  ripple-cli domain example social-media"
    ),
)
def job_run(
    input_path: InputOption = None,
    skill: Annotated[
        Optional[str],
        typer.Option("--skill", help="领域 Skill 名称。未传时默认使用 `social-media`。"),
    ] = None,
    platform_name: Annotated[
        Optional[str],
        typer.Option("--platform", help="平台画像名称，例如 `xiaohongshu`。"),
    ] = None,
    channel: Annotated[
        Optional[str],
        typer.Option("--channel", help="渠道画像名称，用于注入更细的渠道传播环境。"),
    ] = None,
    vertical: Annotated[
        Optional[str],
        typer.Option("--vertical", help="垂直行业画像名称，用于加载更细的行业基准。"),
    ] = None,
    max_waves: Annotated[
        Optional[int],
        typer.Option(
            "--max-waves",
            help="最大传播轮次上限。若不传，则自动使用 `预估轮次 * 3` 作为安全上限。",
        ),
    ] = None,
    max_llm_calls: Annotated[
        Optional[int],
        typer.Option("--max-llm-calls", help="本次任务允许的最大 LLM 调用次数上限，用于控制成本与异常循环。"),
    ] = None,
    ensemble_runs: Annotated[
        Optional[int],
        typer.Option("--ensemble-runs", help="集成推演次数。默认 1；值越大结论越稳，但耗时也越高。"),
    ] = None,
    deliberation_rounds: Annotated[
        Optional[int],
        typer.Option("--deliberation-rounds", help="合议庭最大轮数。默认 3；用于控制最终审议阶段的收敛次数。"),
    ] = None,
    random_seed: Annotated[
        Optional[int],
        typer.Option("--random-seed", help="随机种子。传入后更利于复现同类结果。"),
    ] = None,
    output_path: Annotated[
        Optional[str],
        typer.Option(
            "--output-path",
            help="输出根目录。CLI 会自动创建 `job_id_时间戳/` 子目录，并把该任务的全部产物写入其中；不要传文件名。",
        ),
    ] = None,
    report: Annotated[
        int,
        typer.Option("--report", min=0, max=1, help="是否生成详细说人话报告。`1` 生成（默认），`0` 跳过。"),
    ] = 1,
    async_mode: Annotated[
        bool,
        typer.Option("--async", help="非阻塞模式。命令会立即返回 `job_id`，任务由后台 worker 执行。"),
    ] = False,
    simulation_horizon: Annotated[
        Optional[str],
        typer.Option("--simulation-horizon", help="模拟观察窗口，例如 `48h`、`7d`。"),
    ] = None,
    redact_input: Annotated[
        bool,
        typer.Option("--redact-input", help="脱敏输入内容，适合共享日志、演示或半公开环境。"),
    ] = False,
    config_path: ConfigOption = None,
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
    verbose: VerboseOption = 0,
) -> None:
    output = OutputHandler(json_mode, quiet)
    _configure_logging(verbose, quiet)
    try:
        config_file = _resolve_config_path(config_path)
        db_file = _resolve_db_path(db_path)
        repo = JobRepoSQLite(db_file)
        repo.init_schema()
        request = _build_request(
            input_path=input_path,
            skill=skill,
            platform=platform_name,
            channel=channel,
            vertical=vertical,
            max_waves=max_waves,
            max_llm_calls=max_llm_calls,
            ensemble_runs=ensemble_runs,
            deliberation_rounds=deliberation_rounds,
            random_seed=random_seed,
            output_path=output_path,
            report=report,
            simulation_horizon=simulation_horizon,
            redact_input=redact_input,
        )
        _preflight_request(request, config_file=config_file)
        brief, brief_source = _generate_job_brief(request, config_file)
        brief, brief_source = _resolved_job_brief(
            request=request,
            brief=brief,
            brief_source=brief_source,
        )
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        _prepare_job_artifact_dir(request, job_id)
        repo.create_job(
            job_id,
            request,
            source="cli",
            created_at=_utcnow_iso(),
            job_brief=brief,
            job_brief_source=brief_source,
        )
        if async_mode:
            conflict = repo.list_jobs(status="running", limit=1, offset=0)["jobs"]
            if conflict:
                raise CLIError(
                    "JOB_LOCK_CONFLICT",
                    f"Another job is currently running: {conflict[0]['job_id']}",
                    exit_code=3,
                    extra={"running_job_id": conflict[0]["job_id"]},
                )
            worker_log = Path(_resolve_output_dir()) / f"{job_id}.worker.log"
            try:
                _spawn_worker(
                    job_id=job_id,
                    db_path=db_file,
                    config_file=config_file,
                    log_path=worker_log,
                )
            except Exception as exc:
                repo.set_error(job_id, {"code": "WORKER_SPAWN_FAILED", "message": str(exc)})
                repo.update_status(job_id, "failed")
                raise CLIError("INVALID_INPUT", f"Failed to spawn worker: {exc}") from exc
            payload = {
                "job_id": job_id,
                "job_brief": brief,
                "job_brief_source": brief_source,
                "status": "queued",
                "message": f"任务已提交，可用 `ripple-cli job status {job_id}` 查询进度。",
            }
            output.success(
                payload,
                f"📮 任务已提交：{job_id}\n📝 简述：{brief}\n🔎 查询命令：ripple-cli job status {job_id}",
            )
            return

        conflict = _active_lock_conflict(repo, job_id)
        if conflict is not None:
            raise CLIError(
                "JOB_LOCK_CONFLICT",
                f"Another job is currently running: {conflict}",
                exit_code=3,
                extra={"running_job_id": conflict},
                fix=f"Wait for it to complete or cancel it: ripple-cli job cancel {conflict}",
            )
        payload = _run_simulation_job(
            repo=repo,
            request=request,
            job_id=job_id,
            config_file=config_file,
            output=output,
            brief=brief,
            brief_source=brief_source,
        )
        output.success(payload, _render_job_payload(payload, config_file=config_file, include_report=True, allow_llm=False))
    except CLIError as exc:
        _handle_cli_error(output, exc)


@job_app.command(
    "status",
    cls=ChineseHelpCommand,
    help="查看单个任务当前状态。若任务已完成，则直接显示已落盘的任务总结与详细报告。",
    short_help="查看任务状态",
    epilog="示例：\n  ripple-cli job status job_xxx\n  ripple-cli job status job_xxx --db ./data/ripple.db --json",
)
def job_status(
    job_id: Annotated[str, typer.Argument(help="要查询的任务 ID，例如 `job_abc123`。")],
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        repo = JobRepoSQLite(_resolve_db_path(db_path))
        row = repo.get_job(job_id)
        payload = _status_payload(row)
        output.success(payload, _render_job_payload(payload, allow_llm=False))
    except KeyError as exc:
        _handle_cli_error(output, CLIError("JOB_NOT_FOUND", f"Job not found: {job_id}"))  # pragma: no cover


@job_app.command(
    "wait",
    cls=ChineseHelpCommand,
    help="阻塞等待任务结束。等待过程中会持续轮询状态；任务完成后直接输出最终总结与报告。",
    short_help="等待任务结束",
    epilog="示例：\n  ripple-cli job wait job_xxx\n  ripple-cli job wait job_xxx --timeout 600 --poll-interval 3",
)
def job_wait(
    job_id: Annotated[str, typer.Argument(help="要等待的任务 ID，例如 `job_abc123`。")],
    timeout: Annotated[
        int,
        typer.Option("--timeout", help="最长等待秒数。传 `0` 表示一直等到任务结束。"),
    ] = 0,
    poll_interval: Annotated[
        int,
        typer.Option("--poll-interval", help="轮询间隔秒数。值越小更新越及时，但查询也越频繁。"),
    ] = 5,
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    if timeout < 0:
        _handle_cli_error(output, CLIError("INVALID_INPUT", "`--timeout` 不能小于 0。"))
    if poll_interval <= 0:
        _handle_cli_error(output, CLIError("INVALID_INPUT", "`--poll-interval` 必须大于 0。"))
    repo = JobRepoSQLite(_resolve_db_path(db_path))
    repo.init_schema()
    started = time.monotonic()
    last_headline = ""
    while True:
        try:
            row = repo.get_job(job_id)
        except KeyError as exc:
            _handle_cli_error(output, CLIError("JOB_NOT_FOUND", f"Job not found: {job_id}"))
            return  # pragma: no cover
        payload = _status_payload(row)
        if str(payload["status"]) in _TERMINAL_STATUSES:
            output.success(payload, _render_job_payload(payload, include_report=True, allow_llm=False))
            return
        headline = payload["display"].get("headline", "")
        if not json_mode and not quiet and headline != last_headline:
            output.progress(payload["display"])
            last_headline = headline
        if timeout > 0 and (time.monotonic() - started) >= timeout:
            _handle_cli_error(
                output,
                CLIError("WAIT_TIMEOUT", f"Timed out waiting for job {job_id}.", exit_code=2),
            )
        time.sleep(poll_interval)


@job_app.command(
    "list",
    cls=ChineseHelpCommand,
    help="列出任务数据库中的任务记录。该命令只读取 SQLite，不会触发任何 LLM 调用。",
    short_help="列出任务列表",
    epilog="示例：\n  ripple-cli job list\n  ripple-cli job list --status completed --limit 50\n  ripple-cli job list --json",
)
def job_list(
    status: StatusFilterOption = None,
    source: SourceFilterOption = None,
    limit: Annotated[int, typer.Option("--limit", help="返回的最大任务条数。默认 20。")] = 20,
    offset: Annotated[int, typer.Option("--offset", help="分页偏移量。与 `--limit` 搭配使用。")] = 0,
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    repo = JobRepoSQLite(_resolve_db_path(db_path))
    page = repo.list_jobs(status=status, source=source, limit=limit, offset=offset)
    jobs = []
    for row in page["jobs"]:
        request = _load_request_json(row)
        result = _load_result_json(row)
        brief, brief_source = _resolved_job_brief(
            request=request,
            brief=row.get("job_brief"),
            brief_source=row.get("job_brief_source"),
        )
        jobs.append(
            {
                "job_id": row["job_id"],
                "status": row.get("status"),
                "source": row.get("source"),
                "skill": request.get("skill"),
                "brief": brief,
                "brief_source": brief_source,
                "created_at": row.get("created_at"),
                "elapsed_seconds": _elapsed_seconds(row),
                "artifacts": _job_artifacts_payload(result),
            }
        )
    payload = {"jobs": jobs, "total": page["total"]}
    output.success(payload, _render_job_list_table(payload) if not json_mode else payload)


@job_app.command(
    "result",
    cls=ChineseHelpCommand,
    help="读取单个已完成任务的详细结果 JSON。加 `--summary` 时只返回精简摘要。",
    short_help="读取任务结果",
    epilog="示例：\n  ripple-cli job result job_xxx\n  ripple-cli job result job_xxx --summary --json",
)
def job_result(
    job_id: Annotated[str, typer.Argument(help="要读取结果的任务 ID，例如 `job_abc123`。")],
    summary: Annotated[
        bool,
        typer.Option("--summary", help="仅返回精简摘要，而不是完整结果 JSON。"),
    ] = False,
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        repo = JobRepoSQLite(_resolve_db_path(db_path))
        row = repo.get_job(job_id)
        if row.get("status") != "completed":
            raise CLIError("INVALID_INPUT", f"Job {job_id} is not completed.")
        result = _load_result_json(row)
        document = _load_output_document(result)
        payload = {"job_id": job_id, "result": _result_summary(document) if summary else document}
        output.success(payload, payload)
    except KeyError:
        _handle_cli_error(output, CLIError("JOB_NOT_FOUND", f"Job not found: {job_id}"))
    except CLIError as exc:
        _handle_cli_error(output, exc)


@job_app.command(
    "log",
    cls=ChineseHelpCommand,
    help="读取单个任务的精简日志 Markdown 内容，适合快速回顾关键过程。",
    short_help="读取任务精简日志",
    epilog="示例：\n  ripple-cli job log job_xxx\n  ripple-cli job log job_xxx --json",
)
def job_log(
    job_id: Annotated[str, typer.Argument(help="要读取日志的任务 ID，例如 `job_abc123`。")],
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        repo = JobRepoSQLite(_resolve_db_path(db_path))
        row = repo.get_job(job_id)
        result = _load_result_json(row)
        log_text = _load_compact_log(result)
        payload = {"job_id": job_id, "log": log_text}
        output.success(payload, log_text if not json_mode else payload)
    except KeyError:
        _handle_cli_error(output, CLIError("JOB_NOT_FOUND", f"Job not found: {job_id}"))
    except CLIError as exc:
        _handle_cli_error(output, exc)


@job_app.command(
    "cancel",
    cls=ChineseHelpCommand,
    help="请求取消一个正在运行的任务。任务会在下一个安全检查点停止。",
    short_help="取消运行中的任务",
    epilog="示例：\n  ripple-cli job cancel job_xxx\n  ripple-cli job cancel job_xxx --json",
)
def job_cancel(
    job_id: Annotated[str, typer.Argument(help="要取消的任务 ID，例如 `job_abc123`。")],
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        repo = JobRepoSQLite(_resolve_db_path(db_path))
        row = repo.get_job(job_id)
        if row.get("status") not in {"running", "cancel_pending", "cancelling"}:
            raise CLIError("INVALID_INPUT", f"Job {job_id} is not cancellable in status={row.get('status')}.")
        repo.request_cancel(job_id)
        worker_pid = row.get("worker_pid")
        if worker_pid:
            os.kill(int(worker_pid), signal.SIGTERM)
        payload = {
            "job_id": job_id,
            "status": "cancel_requested",
            "message": "已请求取消，任务会在下一个安全检查点停止。",
        }
        output.success(payload, f"🛑 已请求取消任务：{job_id}")
    except KeyError:
        _handle_cli_error(output, CLIError("JOB_NOT_FOUND", f"Job not found: {job_id}"))
    except ProcessLookupError:
        payload = {
            "job_id": job_id,
            "status": "cancel_requested",
            "message": "已请求取消，但 worker 进程已经退出。",
        }
        output.success(payload, f"🛑 已请求取消任务：{job_id}")
    except CLIError as exc:
        _handle_cli_error(output, exc)


def _parse_duration(text: str) -> timedelta:
    stripped = str(text).strip().lower()
    if not stripped:
        raise CLIError("INVALID_INPUT", "Duration cannot be empty.")
    if stripped.isdigit():
        return timedelta(hours=int(stripped))
    match = re.fullmatch(r"(\d+)([mhdw])", stripped)
    if not match:
        raise CLIError("INVALID_INPUT", f"Unsupported duration: {text}")
    number = int(match.group(1))
    unit = match.group(2)
    return {
        "m": timedelta(minutes=number),
        "h": timedelta(hours=number),
        "d": timedelta(days=number),
        "w": timedelta(weeks=number),
    }[unit]


def _delete_job_artifacts(result: dict[str, Any]) -> int:
    """删除任务产物文件并返回释放字节数。 / Delete job artifacts and return freed bytes."""
    freed_bytes = 0
    for key in ("output_file", "compact_log_file", "summary_md_file", "report_md_file"):
        path_value = str(result.get(key) or "").strip()
        if not path_value:
            continue
        path = Path(path_value)
        if path.exists():
            freed_bytes += path.stat().st_size
            path.unlink()
    artifact_dir = str(result.get("artifact_dir") or "").strip()
    if artifact_dir:
        path = Path(artifact_dir)
        with suppress(OSError):
            path.rmdir()
    return freed_bytes


@job_app.command(
    "delete",
    cls=ChineseHelpCommand,
    help="按 `job_id` 删除单个非运行态任务及其全部产物文件。若任务仍在运行，必须先执行 `job cancel`。",
    short_help="删除单个历史任务",
    epilog="示例：\n  ripple-cli job delete job_xxx --yes\n  ripple-cli job delete job_xxx --db ./data/ripple.db --json",
)
def job_delete(
    job_id: Annotated[str, typer.Argument(help="要删除的任务 ID，例如 `job_abc123`。")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="跳过二次确认，直接删除。适合 Agent、脚本或非交互场景。"),
    ] = False,
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    try:
        repo = JobRepoSQLite(_resolve_db_path(db_path))
        row = repo.get_job(job_id)
        status = str(row.get("status") or "").strip()
        if status in {"running", "cancel_pending", "cancelling"}:
            raise CLIError(
                "INVALID_INPUT",
                f"任务 {job_id} 当前仍在运行或等待取消，不能直接删除。",
                fix=f"请先执行 `ripple-cli job cancel {job_id}`，待任务结束后再删除。",
            )
        if not yes:
            if not sys.stdin.isatty():
                raise CLIError(
                    "INVALID_INPUT",
                    "非交互模式下删除任务必须显式传入 `--yes`。",
                    fix="重试时添加 `--yes`。",
                )
            confirmed = typer.confirm(f"确认删除任务 {job_id} 吗？")
            if not confirmed:
                output.success({"deleted": 0, "freed_bytes": 0, "job_id": job_id}, "已取消。")
                return
        freed_bytes = _delete_job_artifacts(_load_result_json(row))
        repo.delete_jobs([job_id])
        payload = {"deleted": 1, "freed_bytes": freed_bytes, "job_id": job_id}
        output.success(payload, f"🗑️ 已删除任务：{job_id}")
    except KeyError:
        _handle_cli_error(output, CLIError("JOB_NOT_FOUND", f"Job not found: {job_id}"))
    except CLIError as exc:
        _handle_cli_error(output, exc)


@job_app.command(
    "clean",
    cls=ChineseHelpCommand,
    help="按条件批量清理历史任务。只会删除非运行态任务，并同时清理其产物文件与数据库记录。",
    short_help="批量清理历史任务",
    epilog=(
        "示例：\n"
        "  ripple-cli job clean --dry-run\n"
        "  ripple-cli job clean --before 7d --yes\n"
        "  ripple-cli job clean --status completed --before 30d --yes"
    ),
)
def job_clean(
    before: Annotated[
        Optional[str],
        typer.Option("--before", help="只清理早于该相对时间的任务，例如 `12h`、`7d`、`2w`。"),
    ] = None,
    status: StatusFilterOption = None,
    all_mode: Annotated[
        bool,
        typer.Option("--all", help="包含所有非运行态任务，而不只限于 `completed`、`failed`、`cancelled`。"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="跳过二次确认，直接执行删除。适合 Agent 或脚本场景。"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="仅预览将被清理的任务，不真正删除任何文件或数据库记录。"),
    ] = False,
    db_path: DbOption = None,
    json_mode: JsonOption = False,
    quiet: QuietOption = False,
) -> None:
    output = OutputHandler(json_mode, quiet)
    repo = JobRepoSQLite(_resolve_db_path(db_path))
    before_iso = None
    if before:
        cutoff = datetime.now(timezone.utc) - _parse_duration(before)
        before_iso = cutoff.isoformat()
    candidates = repo.select_jobs_for_cleanup(
        before_iso=before_iso,
        status=status,
        include_all=all_mode,
    )
    payload = {
        "dry_run": bool(dry_run),
        "candidate_count": len(candidates),
        "cleaned": 0,
        "freed_bytes": 0,
        "job_ids": [row["job_id"] for row in candidates],
    }
    if dry_run:
        output.success(payload, payload)
        return
    if not yes and candidates:
        if not sys.stdin.isatty():
            _handle_cli_error(
                output,
                CLIError(
                    "INVALID_INPUT",
                    "非交互模式下批量清理任务必须显式传入 `--yes`。",
                    fix="可先使用 `--dry-run` 预览候选任务，再使用 `--yes` 执行清理。",
                ),
            )
        confirmed = typer.confirm(f"确认删除 {len(candidates)} 个任务吗？")
        if not confirmed:
            output.success({"cleaned": 0, "freed_bytes": 0, "job_ids": []}, "已取消。")
            return
    freed_bytes = 0
    for row in candidates:
        freed_bytes += _delete_job_artifacts(_load_result_json(row))
    repo.delete_jobs(payload["job_ids"])
    payload["cleaned"] = len(candidates)
    payload["freed_bytes"] = freed_bytes
    output.success(payload, payload)


@app.command("_worker", hidden=True)
def worker(
    db_path: DbOption = None,
    config_path: ConfigOption = None,
    job_id: str = typer.Argument(...),
) -> None:
    """隐藏 worker 入口。 / Hidden worker entrypoint."""
    repo = JobRepoSQLite(_resolve_db_path(db_path))
    repo.init_schema()
    row = repo.get_job(job_id)
    request = _load_request_json(row)
    config_file = _resolve_config_path(config_path)
    tracker = ProgressTracker()

    def _sigterm_handler(signum, frame):  # pragma: no cover - signal path
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _sigterm_handler)
    output = OutputHandler(json_mode=False, quiet=True)
    conflict = _active_lock_conflict(repo, job_id)
    if conflict is not None:
        repo.set_error(job_id, {"code": "JOB_LOCK_CONFLICT", "message": f"Another job is running: {conflict}"})
        repo.update_status(job_id, "failed")
        raise typer.Exit(3)
    try:
        _run_simulation_job(
            repo=repo,
            request=request,
            job_id=job_id,
            config_file=config_file,
            output=output,
            brief=str(row.get("job_brief") or ""),
            brief_source=str(row.get("job_brief_source") or "fallback"),
        )
    except CLIError:
        raise typer.Exit(0)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
