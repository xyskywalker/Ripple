from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ripple.llm.router import ModelRouter
from ripple.skills.manager import SkillManager

logger = logging.getLogger(__name__)


RoundSpec = Dict[str, str]

_METRIC_LABELS = {
    "views": "曝光",
    "likes": "点赞",
    "comments": "评论",
    "favorites": "收藏",
    "shares": "转发",
    "sales": "销量",
    "gmv": "成交额",
    "return_rate": "退货率",
    "repurchase_rate": "复购率",
    "engagement_rate": "互动率",
}
_IGNORED_HISTORY_KEYS = {
    "id",
    "title",
    "content",
    "content_preview",
    "post_type",
    "summary",
    "created_at",
    "updated_at",
    "timestamp",
}
_PREFERRED_HISTORY_METRICS = (
    "views",
    "likes",
    "comments",
    "favorites",
    "shares",
    "sales",
    "gmv",
    "return_rate",
    "repurchase_rate",
    "engagement_rate",
)


@dataclass(frozen=True)
class ReportRound:
    """单轮报告生成规范。 / One report-generation round specification."""

    label: str
    system_prompt: str
    extra_user_context: str = ""


@dataclass(frozen=True)
class ReportProfile:
    """报告模板配置。 / Report template profile."""

    name: str
    description: str = ""
    role: str = "omniscient"
    max_llm_calls: int = 10
    rounds: List[ReportRound] = field(default_factory=list)


def _load_json(text: str | None) -> dict | None:
    if not text:
        return None
    data = json.loads(text)
    return data if isinstance(data, dict) else None


def load_job_request(row: dict) -> dict | None:
    return _load_json(row.get("request_json"))


def load_job_result(row: dict) -> dict | None:
    return _load_json(row.get("result_json"))


def extract_request_llm_config(request: dict | None) -> dict | None:
    if not isinstance(request, dict):
        return None
    llm_config = request.get("llm_config")
    return llm_config if isinstance(llm_config, dict) else None


def _require_file(path_value: Any, *, name: str) -> Path:
    path_str = str(path_value or "").strip()
    if not path_str:
        raise FileNotFoundError(f"{name} path is missing")

    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"{name} file not found: {path}")
    return path


def load_compact_log_text(result: Dict[str, Any]) -> str:
    path = _require_file(result.get("compact_log_file"), name="compact_log_file")
    return path.read_text(encoding="utf-8")


def load_output_json_document(result: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_file(result.get("output_file"), name="output_file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("output_file JSON must be an object")
    return data


def _normalize_rounds(rounds: Iterable[dict | ReportRound]) -> List[RoundSpec]:
    normalized: List[RoundSpec] = []
    for index, item in enumerate(rounds, start=1):
        if isinstance(item, ReportRound):
            label = item.label
            system_prompt = item.system_prompt
            extra_user_context = item.extra_user_context
        elif isinstance(item, dict):
            label = str(item.get("label") or f"round_{index}")
            system_prompt = str(item.get("system_prompt") or "").strip()
            extra_user_context = str(item.get("extra_user_context") or "")
        else:
            raise ValueError(f"rounds[{index - 1}] must be an object")

        if not system_prompt:
            raise ValueError(f"rounds[{index - 1}].system_prompt is required")

        normalized.append(
            {
                "label": label,
                "system_prompt": system_prompt,
                "extra_user_context": extra_user_context,
            }
        )
    return normalized


def serialize_report_rounds(rounds: Iterable[dict | ReportRound]) -> List[RoundSpec]:
    """序列化报告轮次，便于经 HTTP 传输。 / Serialize rounds for HTTP transport."""
    return _normalize_rounds(rounds)


def _preview_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _historical_metric_summary(historical: Sequence[dict[str, Any]]) -> str:
    collected: Dict[str, List[float]] = {}
    for item in historical:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key in _IGNORED_HISTORY_KEYS or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                collected.setdefault(key, []).append(float(value))

    if not collected:
        return ""

    ordered = [
        key for key in _PREFERRED_HISTORY_METRICS if key in collected
    ] + [
        key for key in sorted(collected.keys()) if key not in _PREFERRED_HISTORY_METRICS
    ]

    lines: List[str] = []
    for key in ordered[:8]:
        values = collected.get(key) or []
        if not values:
            continue
        avg = round(sum(values) / len(values), 2)
        minimum = round(min(values), 2)
        maximum = round(max(values), 2)
        label = _METRIC_LABELS.get(key, key)
        lines.append(
            f"- {label}（{key}）：n={len(values)} min={minimum} max={maximum} mean={avg}"
        )
    return "\n".join(lines)


def build_request_report_context(request: Dict[str, Any]) -> str:
    """从请求中提炼稳定补充上下文。 / Build stable supplemental context from the request."""
    if not isinstance(request, dict):
        return ""

    sections: List[str] = []
    skill = str(request.get("skill") or "").strip() or "social-media"
    platform = str(request.get("platform") or "").strip()
    channel = str(request.get("channel") or "").strip()
    vertical = str(request.get("vertical") or "").strip()
    horizon = str(request.get("simulation_horizon") or "").strip()

    meta_lines = [f"- 领域：{skill}"]
    if platform:
        meta_lines.append(f"- 平台：{platform}")
    if channel:
        meta_lines.append(f"- 渠道：{channel}")
    if vertical:
        meta_lines.append(f"- 垂直领域：{vertical}")
    if horizon:
        meta_lines.append(f"- 模拟时长：{horizon}")
    if meta_lines:
        sections.append("## 请求元信息\n" + "\n".join(meta_lines))

    if request.get("redact_input"):
        sections.append("## 隐私说明\n- 本任务启用了输入脱敏，请基于结构化结果与聚合信号解读，不要还原敏感原文。")
        return "\n\n".join(sections)

    event = request.get("event") if isinstance(request.get("event"), dict) else {}
    if event:
        event_lines: List[str] = []
        title = str(event.get("title") or "").strip()
        summary = str(event.get("summary") or "").strip()
        description = str(event.get("description") or "").strip()
        body = str(event.get("body") or event.get("content") or "").strip()
        if title:
            event_lines.append(f"- 标题：{title}")
        if summary:
            event_lines.append(f"- 摘要：{_preview_text(summary, 220)}")
        elif description:
            event_lines.append(f"- 说明：{_preview_text(description, 220)}")
        if body and body != summary:
            event_lines.append(f"- 正文预览：{_preview_text(body, 220)}")
        if event_lines:
            sections.append("## 事件背景\n" + "\n".join(event_lines))

    source = request.get("source") if isinstance(request.get("source"), dict) else {}
    if source:
        source_lines: List[str] = []
        summary = str(source.get("summary") or "").strip()
        if summary:
            source_lines.append(f"- 画像摘要：{_preview_text(summary, 220)}")
        for key, label in (
            ("account_name", "账号/品牌"),
            ("bio", "简介"),
            ("main_category", "主赛道"),
            ("content_style", "内容风格"),
            ("target_audience", "目标受众"),
            ("followers_count", "粉丝数"),
        ):
            value = source.get(key)
            if value not in (None, "", []):
                source_lines.append(f"- {label}：{value}")
        if source_lines:
            sections.append("## 来源画像\n" + "\n".join(source_lines[:7]))

    historical = request.get("historical")
    if isinstance(historical, list) and historical:
        hist_summary = _historical_metric_summary(
            [item for item in historical if isinstance(item, dict)]
        )
        if hist_summary:
            sections.append("## 历史数据统计\n" + hist_summary)

    environment = request.get("environment")
    if isinstance(environment, dict) and environment:
        preview = _preview_text(json.dumps(environment, ensure_ascii=False), 220)
        sections.append("## 环境补充\n- " + preview)

    return "\n\n".join(sections)


def load_skill_report_profile(
    skill_name: str,
    *,
    profile_name: str = "default",
    skill_path: str | Path | None = None,
) -> ReportProfile:
    """从 skill 目录加载报告模板。 / Load a report profile from the skill directory."""
    manager = SkillManager()
    loaded_skill = manager.load(
        skill_name,
        skill_path=Path(skill_path) if skill_path is not None else None,
    )
    raw_profile = (loaded_skill.report_profiles or {}).get(profile_name)
    if not isinstance(raw_profile, dict):
        raise FileNotFoundError(
            f"Report profile not found: skill={skill_name}, profile={profile_name}"
        )

    role = str(raw_profile.get("role") or "omniscient").strip() or "omniscient"
    description = str(raw_profile.get("description") or "").strip()
    try:
        max_llm_calls = int(raw_profile.get("max_llm_calls") or 10)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid max_llm_calls in report profile: skill={skill_name}, profile={profile_name}"
        ) from exc

    system_prefix = str(raw_profile.get("system_prefix") or "").strip()
    rounds_raw = raw_profile.get("rounds")
    if not isinstance(rounds_raw, list) or not rounds_raw:
        raise ValueError(
            f"Report profile rounds are required: skill={skill_name}, profile={profile_name}"
        )

    rounds: List[ReportRound] = []
    for index, item in enumerate(rounds_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"report round must be a mapping: skill={skill_name}, profile={profile_name}, index={index}"
            )
        prompt_body = str(item.get("system_prompt") or item.get("prompt") or "").strip()
        if not prompt_body:
            raise ValueError(
                f"report round system_prompt is required: skill={skill_name}, profile={profile_name}, index={index}"
            )
        prompt = prompt_body
        if system_prefix:
            prompt = system_prefix.rstrip() + "\n\n" + prompt
        rounds.append(
            ReportRound(
                label=str(item.get("label") or f"round_{index}"),
                system_prompt=prompt,
                extra_user_context=str(item.get("extra_user_context") or ""),
            )
        )

    return ReportProfile(
        name=profile_name,
        description=description,
        role=role,
        max_llm_calls=max_llm_calls,
        rounds=rounds,
    )


def build_skill_report_profile(
    *,
    request: Dict[str, Any],
    profile_name: str = "default",
    skill_path: str | Path | None = None,
) -> ReportProfile:
    """结合请求上下文实例化 skill 报告模板。 / Materialize a skill report profile with request context."""
    skill_name = str(request.get("skill") or "").strip() or "social-media"
    base_profile = load_skill_report_profile(
        skill_name,
        profile_name=profile_name,
        skill_path=skill_path,
    )
    request_context = build_request_report_context(request)
    rounds: List[ReportRound] = []
    for item in base_profile.rounds:
        extra_parts = [item.extra_user_context.strip(), request_context.strip()]
        rounds.append(
            ReportRound(
                label=item.label,
                system_prompt=item.system_prompt,
                extra_user_context="\n\n".join(part for part in extra_parts if part),
            )
        )
    return ReportProfile(
        name=base_profile.name,
        description=base_profile.description,
        role=base_profile.role,
        max_llm_calls=base_profile.max_llm_calls,
        rounds=rounds,
    )


def compress_waves_for_llm(waves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compressed: List[Dict[str, Any]] = []
    for wave in waves:
        entry: Dict[str, Any] = {
            "wave_number": wave.get("wave_number"),
            "terminated": wave.get("terminated", False),
        }
        verdict = wave.get("verdict") or {}
        entry["simulated_time"] = verdict.get("simulated_time_elapsed", "")
        entry["global_observation"] = verdict.get("global_observation", "")
        if verdict.get("termination_reason"):
            entry["termination_reason"] = verdict["termination_reason"]

        entry["activated_agents"] = [
            {
                "id": agent.get("agent_id", ""),
                "energy": agent.get("incoming_ripple_energy", 0),
                "reason": agent.get("activation_reason", ""),
            }
            for agent in (verdict.get("activated_agents") or [])
        ]

        skipped = verdict.get("skipped_agents") or []
        if skipped:
            entry["skipped_agents"] = [
                {
                    "id": skipped_agent.get("agent_id", ""),
                    "reason": skipped_agent.get("skip_reason", ""),
                }
                for skipped_agent in skipped
            ]

        entry["responses"] = {
            agent_id: {
                "type": response.get("response_type", "unknown"),
                "out_energy": response.get("outgoing_energy", 0),
            }
            for agent_id, response in (wave.get("agent_responses") or {}).items()
        }

        if wave.get("wave_number") == 0:
            pre_snapshot = wave.get("pre_snapshot") or {}
            entry["initial_state"] = {
                "star_count": len(pre_snapshot.get("stars", {})),
                "sea_count": len(pre_snapshot.get("seas", {})),
                "seed_energy": pre_snapshot.get("seed_energy", 0),
            }

        compressed.append(entry)
    return compressed


def load_simulation_log(result: Dict[str, Any]) -> str:
    compact_log = result.get("compact_log_file")
    if compact_log:
        compact_path = Path(str(compact_log))
        if compact_path.exists():
            return compact_path.read_text(encoding="utf-8")

    full_data: Dict[str, Any]
    output_file = result.get("output_file")
    if output_file:
        output_path = Path(str(output_file))
        if output_path.exists():
            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                full_data = loaded
            else:
                raise ValueError("output_file JSON must be an object")
        else:
            full_data = result
    else:
        full_data = result

    process = full_data.get("process") or {}
    compact = {
        "simulation_input": full_data.get("simulation_input"),
        "init": process.get("init"),
        "seed": process.get("seed"),
        "waves": compress_waves_for_llm(process.get("waves") or []),
        "observation": process.get("observation"),
        "prediction": full_data.get("prediction"),
        "timeline": full_data.get("timeline"),
        "bifurcation_points": full_data.get("bifurcation_points"),
        "agent_insights": full_data.get("agent_insights"),
        "total_waves": full_data.get("total_waves"),
    }
    if full_data.get("deliberation"):
        compact["deliberation"] = full_data["deliberation"]

    return json.dumps(compact, ensure_ascii=False, indent=1, default=str)


async def _call_llm(
    router: Any,
    *,
    role: str,
    system_prompt: str,
    user_message: str,
) -> str:
    if hasattr(router, "check_budget") and not router.check_budget(role):
        raise RuntimeError(f"LLM call budget exceeded for role={role}")
    if hasattr(router, "record_attempt"):
        router.record_attempt(role)
    adapter = router.get_model_backend(role)
    content = await adapter.call(system_prompt, user_message)
    if hasattr(router, "record_call"):
        router.record_call(role)
    return (content or "").strip()


async def generate_report_from_result(
    *,
    result: Dict[str, Any],
    rounds: List[dict | ReportRound],
    role: str = "omniscient",
    max_llm_calls: int = 10,
    config_file: Optional[str] = None,
    llm_config: Optional[Dict[str, Any]] = None,
    stream: Optional[bool] = None,
    llm_timeout: Optional[float] = None,
) -> Optional[str]:
    normalized_rounds = _normalize_rounds(rounds)
    log_text = load_simulation_log(result)
    router = ModelRouter(
        llm_config=llm_config,
        max_llm_calls=max_llm_calls,
        config_file=config_file,
        stream=stream,
        timeout_override=llm_timeout,
    )

    parts: List[str] = []
    for round_spec in normalized_rounds:
        user_message = log_text
        extra_user_context = round_spec["extra_user_context"]
        if extra_user_context:
            user_message += "\n\n" + extra_user_context
        try:
            text = await _call_llm(
                router,
                role=role,
                system_prompt=round_spec["system_prompt"],
                user_message=user_message,
            )
        except Exception as exc:
            logger.warning("report round failed: label=%s error=%s", round_spec["label"], exc)
            continue
        if text:
            parts.append(text)

    return "\n\n".join(parts) if parts else None


async def generate_skill_report_from_result(
    *,
    result: Dict[str, Any],
    request: Dict[str, Any],
    config_file: Optional[str] = None,
    llm_config: Optional[Dict[str, Any]] = None,
    stream: Optional[bool] = None,
    llm_timeout: Optional[float] = None,
    profile_name: str = "default",
    skill_path: str | Path | None = None,
) -> Optional[str]:
    """基于 skill 报告模板直接生成报告。 / Generate a report directly from the skill profile."""
    profile = build_skill_report_profile(
        request=request,
        profile_name=profile_name,
        skill_path=skill_path,
    )
    return await generate_report_from_result(
        result=result,
        rounds=profile.rounds,
        role=profile.role,
        max_llm_calls=profile.max_llm_calls,
        config_file=config_file,
        llm_config=llm_config,
        stream=stream,
        llm_timeout=llm_timeout,
    )
