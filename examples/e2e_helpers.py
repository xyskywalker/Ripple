#!/usr/bin/env python3
"""Shared utilities for Ripple E2E simulation examples.

Provides common infrastructure so each E2E script stays concise:
  - Data builders (topic/account/history -> simulate() inputs)
  - Progress callback for terminal display
  - Simulation log loading & wave compression
  - Multi-round LLM report generation framework
  - Run summary & CLI helpers
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

# Project root (examples/ is one level below repo root)
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """Lightweight progress event used by examples without requiring local ripple code."""

    type: str
    phase: str
    run_id: str
    progress: float = 0.0
    wave: Optional[int] = None
    total_waves: Optional[int] = None
    agent_id: Optional[str] = None
    agent_type: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


@dataclass
class ReportRound:
    """报告轮次规范。 / Report round specification."""

    label: str
    system_prompt: str
    extra_user_context: str = ""


async def simulate(*args, **kwargs):
    """Lazy import wrapper so service-only scripts don't require local ripple source."""
    from ripple.api.simulate import simulate as ripple_simulate

    return await ripple_simulate(*args, **kwargs)


def _load_model_router_class():
    try:
        from ripple.llm.router import ModelRouter
    except Exception:
        return None
    return ModelRouter


# =============================================================================
# Logging bootstrap (idempotent — safe to call from multiple scripts)
# =============================================================================

def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )


# =============================================================================
# Data builders — convert MPlus-aligned dicts to simulate() inputs
# =============================================================================

def build_event_from_topic(topic: Dict[str, Any], default_platform: str = "xiaohongshu") -> Dict[str, Any]:
    """Build simulate() *event* from a topic dict (MPlus-aligned)."""
    title = topic.get("title") or ""
    description = topic.get("description") or ""
    content = topic.get("content") or ""
    target_platform = topic.get("target_platform") or default_platform

    parts = [f"标题：{title}"]
    if description:
        parts.append(f"选题说明：{description}")
    if content:
        preview = content[:500] + "..." if len(content) > 500 else content
        parts.append(f"正文摘要：{preview}")

    return {
        "title": title,
        "description": description,
        "content": content,
        "target_platform": target_platform,
        "summary": " ".join(parts),
    }


def build_source_from_account(account: Dict[str, Any]) -> Dict[str, Any]:
    """Build simulate() *source* from an account profile dict."""
    name = account.get("account_name") or ""
    bio = account.get("bio") or ""
    main_category = account.get("main_category") or ""
    sub_categories = account.get("sub_categories")
    sub_str = (
        "、".join(sub_categories[:5])
        if isinstance(sub_categories, list)
        else (str(sub_categories) if sub_categories else "")
    )
    content_style = account.get("content_style") or ""
    target_audience = account.get("target_audience") or ""
    followers_count = account.get("followers_count", 0) or 0
    posts_count = account.get("posts_count", 0) or 0
    verification_status = account.get("verification_status") or "none"

    parts = [f"账号名：{name}", f"主赛道：{main_category}"]
    if sub_str:
        parts.append(f"细分赛道：{sub_str}")
    if bio:
        parts.append(f"简介：{bio}")
    if content_style:
        parts.append(f"内容风格：{content_style}")
    if target_audience:
        parts.append(f"目标受众：{target_audience}")
    parts.append(f"粉丝数：{followers_count}，发帖数：{posts_count}，认证：{verification_status}")

    return {
        "account_name": name,
        "bio": bio,
        "main_category": main_category,
        "sub_categories": sub_categories,
        "content_style": content_style,
        "target_audience": target_audience,
        "followers_count": followers_count,
        "posts_count": posts_count,
        "verification_status": verification_status,
        "summary": " | ".join(parts),
    }


def build_historical_from_posts(
    posts: List[Dict[str, Any]], max_items: int = 10
) -> List[Dict[str, Any]]:
    """Build simulate() *historical* from a list of post dicts."""
    out = []
    for p in posts[:max_items]:
        views = p.get("views", 0) or 0
        likes = p.get("likes", 0) or 0
        comments = p.get("comments", 0) or 0
        favorites = p.get("favorites", 0) or 0
        shares = p.get("shares", 0) or 0
        engagement_rate = p.get("engagement_rate")
        if engagement_rate is None and views > 0:
            engagement_rate = round(
                (likes + comments + favorites + shares) / views * 100, 2,
            )
        entry: Dict[str, Any] = {
            "title": p.get("title") or "",
            "content_preview": (p.get("content") or "")[:300],
            "views": views,
            "likes": likes,
            "comments": comments,
            "favorites": favorites,
            "shares": shares,
            "engagement_rate": engagement_rate,
            "post_type": p.get("post_type") or "图文",
        }
        # Include e-commerce metrics when present
        for extra_key in ("sales", "gmv", "return_rate", "repurchase_rate"):
            if extra_key in p:
                entry[extra_key] = p[extra_key]
        out.append(entry)
    return out


def historical_engagement_stats(
    posts: List[Dict[str, Any]],
    metrics: Sequence[str] = ("views", "likes", "comments", "favorites"),
) -> Dict[str, Dict[str, Any]]:
    """Compute min/max/mean/p25/p75 for each metric across posts."""
    out: Dict[str, Dict[str, Any]] = {}
    for key in metrics:
        vals = sorted(
            int(v)
            for p in posts
            if (v := p.get(key)) is not None and isinstance(v, (int, float))
        )
        if not vals:
            out[key] = {"n": 0}
            continue
        n = len(vals)
        out[key] = {
            "n": n,
            "min": vals[0],
            "max": vals[-1],
            "mean": round(sum(vals) / n, 0),
            "p25": vals[max(0, int(n * 0.25) - 1)],
            "p75": vals[min(n - 1, int(n * 0.75))],
        }
    return out


def format_stats_block(
    posts: List[Dict[str, Any]],
    metrics: Sequence[str] = ("views", "likes", "comments", "favorites"),
) -> str:
    """Return a compact text block summarising historical engagement stats."""
    stats = historical_engagement_stats(posts, metrics)
    lines = []
    for k, v in stats.items():
        if isinstance(v, dict) and v.get("n", 0) > 0:
            lines.append(
                f"{k}: n={v['n']} min={v.get('min')} max={v.get('max')} "
                f"mean={v.get('mean')} p25={v.get('p25')} p75={v.get('p75')}"
            )
    return "\n".join(lines)


# =============================================================================
# Progress callback — real-time terminal display
# =============================================================================

_PHASE_CN: Dict[str, str] = {
    "INIT": "初始化",
    "SEED": "种子注入",
    "RIPPLE": "涟漪传播",
    "OBSERVE": "全局观测",
    "DELIBERATE": "合议庭审议",
    "SYNTHESIZE": "结果合成",
}

_BAR_WIDTH = 30
_BAR_FILL = "█"
_BAR_EMPTY = "░"


def _progress_bar(progress: float) -> str:
    filled = int(_BAR_WIDTH * progress)
    empty = _BAR_WIDTH - filled
    return f"[{_BAR_FILL * filled}{_BAR_EMPTY * empty}] {progress:>5.1%}"


def print_progress(event: Any) -> None:
    """Terminal progress callback (sync). Plug into ``simulate(on_progress=...)``."""
    bar = _progress_bar(event.progress)
    phase_cn = _PHASE_CN.get(event.phase, event.phase)

    if event.type == "phase_start":
        print(f"  {bar}  ▶ {phase_cn} 开始")

    elif event.type == "phase_end":
        detail = event.detail or {}
        if event.phase == "INIT":
            print(
                f"  {bar}  ✓ {phase_cn} 完成 — "
                f"Star×{detail.get('star_count', '?')} "
                f"Sea×{detail.get('sea_count', '?')} "
                f"预估{detail.get('estimated_waves', '?')}轮"
            )
        elif event.phase == "SEED":
            print(f"  {bar}  ✓ {phase_cn} 完成 — 能量={detail.get('seed_energy', '?')}")
        elif event.phase == "RIPPLE":
            print(f"  {bar}  ✓ {phase_cn} 完成 — 实际{detail.get('effective_waves', '?')}轮")
        elif event.phase == "DELIBERATE":
            print(f"  {bar}  ✓ {phase_cn} 完成 — {detail.get('rounds', '?')}轮合议")
        else:
            print(f"  {bar}  ✓ {phase_cn} 完成")

    elif event.type == "wave_start":
        w = (event.wave or 0) + 1
        print(f"  {bar}  ━ Wave {w}/{event.total_waves or '?'}")

    elif event.type == "wave_end":
        detail = event.detail or {}
        if detail.get("terminated"):
            print(f"  {bar}    ╰ 传播终止: {detail.get('reason', '')}")
        else:
            print(f"  {bar}    ╰ {detail.get('agent_count', 0)} 个 Agent 响应")

    elif event.type == "agent_activated":
        aid = event.agent_id or "?"
        atype = event.agent_type or "?"
        energy = (event.detail or {}).get("energy", "?")
        print(f"  {bar}    → 激活 {atype}:{aid} (能量={energy})")

    elif event.type == "agent_responded":
        aid = event.agent_id or "?"
        rtype = (event.detail or {}).get("response_type", "?")
        print(f"  {bar}    ← {aid} 响应: {rtype}")

    elif event.type == "round_start":
        detail = event.detail or {}
        round_number = detail.get("round_number", "?")
        total_rounds = detail.get("total_rounds", "?")
        print(f"  {bar}  ━ 合议 Round {round_number}/{total_rounds} 开始")

    elif event.type == "round_end":
        detail = event.detail or {}
        round_number = detail.get("round_number", "?")
        total_rounds = detail.get("total_rounds", "?")
        converged = detail.get("converged")
        suffix = "（已收敛）" if converged else ""
        print(f"  {bar}    ╰ 合议 Round {round_number}/{total_rounds} 完成{suffix}")


# =============================================================================
# Service-mode helpers — HTTP job status & SSE progress adaptation
# =============================================================================

_TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def is_terminal_job_status(status: str) -> bool:
    """Return True when a service job status is terminal."""
    return status in _TERMINAL_JOB_STATUSES


def progress_event_from_service_event(event: Dict[str, Any]) -> Optional[ProgressEvent]:
    """Convert one HTTP+SSE service event envelope to ``ProgressEvent``.

    Returns ``None`` for non-progress events such as ``job.started``.
    """
    event_type = str(event.get("type") or "")
    if not event_type.startswith("progress."):
        return None

    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    detail = payload.get("detail")
    if not isinstance(detail, dict):
        detail = {}

    progress = payload.get("progress", 0.0)
    try:
        progress_value = float(progress)
    except (TypeError, ValueError):
        progress_value = 0.0

    total_waves = payload.get("total_waves")
    if not isinstance(total_waves, int):
        total_waves = None

    wave = payload.get("wave")
    if not isinstance(wave, int):
        wave = None

    return ProgressEvent(
        type=event_type.removeprefix("progress."),
        phase=str(payload.get("phase") or ""),
        run_id=str(event.get("job_id") or "service-job"),
        progress=progress_value,
        wave=wave,
        total_waves=total_waves,
        agent_id=payload.get("agent_id"),
        agent_type=payload.get("agent_type"),
        detail=detail,
    )



def resolve_service_artifact_path(
    path_value: Optional[str],
    *,
    local_root: Path = REPO_ROOT / "ripple_outputs",
    container_root: str = "/app/ripple_outputs",
) -> Optional[str]:
    """Map a service-returned container artifact path to a host-local path."""
    if not path_value:
        return path_value

    path_str = str(path_value)
    container_prefix = container_root.rstrip("/") + "/"
    if path_str == container_root.rstrip("/"):
        return str(local_root)
    if path_str.startswith(container_prefix):
        rel = path_str[len(container_prefix):]
        return str(local_root / rel)
    return path_str



def result_from_service_job(
    job: Dict[str, Any],
    *,
    local_root: Path = REPO_ROOT / "ripple_outputs",
    container_root: str = "/app/ripple_outputs",
) -> Dict[str, Any]:
    """Extract a direct-``simulate()``-shaped result dict from a service job payload."""
    result = dict(job.get("result") or {})
    original_paths = {
        key: result.get(key)
        for key in ("output_file", "compact_log_file")
        if result.get(key)
    }
    for key in ("output_file", "compact_log_file"):
        result[key] = resolve_service_artifact_path(
            result.get(key),
            local_root=local_root,
            container_root=container_root,
        )
    if original_paths:
        result["service_artifacts"] = original_paths
    return result


# =============================================================================
# Simulation log loading & wave compression
# =============================================================================

def compress_waves_for_llm(waves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compress wave records to reduce LLM context tokens."""
    compressed: List[Dict[str, Any]] = []
    for w in waves:
        entry: Dict[str, Any] = {
            "wave_number": w.get("wave_number"),
            "terminated": w.get("terminated", False),
        }
        verdict = w.get("verdict") or {}
        entry["simulated_time"] = verdict.get("simulated_time_elapsed", "")
        entry["global_observation"] = verdict.get("global_observation", "")
        if verdict.get("termination_reason"):
            entry["termination_reason"] = verdict["termination_reason"]

        entry["activated_agents"] = [
            {
                "id": a.get("agent_id", ""),
                "energy": a.get("incoming_ripple_energy", 0),
                "reason": a.get("activation_reason", ""),
            }
            for a in (verdict.get("activated_agents") or [])
        ]

        skipped = verdict.get("skipped_agents") or []
        if skipped:
            entry["skipped_agents"] = [
                {"id": s.get("agent_id", ""), "reason": s.get("skip_reason", "")}
                for s in skipped
            ]

        entry["responses"] = {
            aid: {
                "type": r.get("response_type", "unknown"),
                "out_energy": r.get("outgoing_energy", 0),
            }
            for aid, r in (w.get("agent_responses") or {}).items()
        }

        if w.get("wave_number") == 0:
            pre = w.get("pre_snapshot") or {}
            entry["initial_state"] = {
                "star_count": len(pre.get("stars", {})),
                "sea_count": len(pre.get("seas", {})),
                "seed_energy": pre.get("seed_energy", 0),
            }

        compressed.append(entry)
    return compressed


def _read_text_from_container(path_value: str, container_name: str = "ripple-service") -> Optional[str]:
    if not path_value:
        return None
    try:
        proc = subprocess.run(
            ["docker", "exec", container_name, "cat", path_value],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        logger.debug("读取容器文件失败 %s: %s", path_value, exc)
        return None
    return proc.stdout


def load_simulation_log(result: Dict[str, Any]) -> Optional[str]:
    """Load the simulation log text, preferring the compact markdown log.

    Falls back to compressing the full JSON output if no compact log exists.
    When only service container paths are available, attempts to read artifacts
    directly from the running container.
    """
    compact_log = result.get("compact_log_file")
    if compact_log and Path(compact_log).exists():
        return Path(compact_log).read_text(encoding="utf-8")

    service_artifacts = result.get("service_artifacts") or {}
    service_compact_log = service_artifacts.get("compact_log_file")
    if isinstance(service_compact_log, str):
        compact_text = _read_text_from_container(service_compact_log)
        if compact_text:
            return compact_text

    output_file = result.get("output_file")
    if output_file and Path(output_file).exists():
        full_data = json.loads(Path(output_file).read_text(encoding="utf-8"))
    else:
        service_output_file = service_artifacts.get("output_file")
        if isinstance(service_output_file, str):
            output_text = _read_text_from_container(service_output_file)
            if output_text:
                full_data = json.loads(output_text)
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
    # Include deliberation if present (PMF validation etc.)
    if full_data.get("deliberation"):
        compact["deliberation"] = full_data["deliberation"]

    return json.dumps(compact, ensure_ascii=False, indent=1, default=str)


# =============================================================================
# LLM call wrapper
# =============================================================================

_AZURE_DOMAIN_SUFFIXES = (
    "cognitiveservices.azure.com",
    "openai.azure.com",
    "services.ai.azure.com",
)


def _read_yaml_config(path: Path) -> Dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}



def _read_container_llm_config(container_name: str = "ripple-service") -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["docker", "exec", container_name, "cat", "/app/llm_config.yaml"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        logger.debug("读取容器内 llm_config 失败: %s", exc)
        return None

    try:
        import yaml

        return yaml.safe_load(proc.stdout) or {}
    except Exception as exc:
        logger.warning("解析容器内 llm_config 失败: %s", exc)
        return None



def _load_report_config_data(config_file: Optional[str]) -> Optional[Dict[str, Any]]:
    if config_file:
        path = Path(config_file)
        if path.exists():
            try:
                return _read_yaml_config(path)
            except Exception as exc:
                logger.warning("读取本地 llm_config 失败: %s", exc)

    return _read_container_llm_config()



def _merge_role_config(config_data: Dict[str, Any], role: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    default = config_data.get("_default", {})
    if isinstance(default, dict):
        merged.update({k: v for k, v in default.items() if v is not None})

    role_cfg = config_data.get(role, {})
    if isinstance(role_cfg, str):
        merged["model_name"] = role_cfg
        merged.setdefault("model_platform", "openai")
    elif isinstance(role_cfg, dict):
        merged.update({k: v for k, v in role_cfg.items() if v is not None})

    return merged



def _is_azure_url(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return any(hostname.endswith(suffix) for suffix in _AZURE_DOMAIN_SUFFIXES)



def _resolve_openai_endpoint(url: str, api_mode: str) -> str:
    parsed = urlparse(url)
    suffix = "/responses" if api_mode == "responses" else "/chat/completions"
    path = parsed.path
    if suffix not in path:
        path = path.rstrip("/") + suffix
    query = urlencode(parse_qs(parsed.query, keep_blank_values=True), doseq=True)
    return urlunparse(parsed._replace(path=path, query=query))



def _extract_responses_text(data: Dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str) and text:
                return text
    return ""



def _extract_chat_completions_text(data: Dict[str, Any]) -> str:
    choices = data.get("choices", []) or []
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


async def call_llm(
    router: Any,
    role: str,
    system_prompt: str,
    user_message: str,
) -> str:
    """Call project-default LLM and return stripped text (empty on failure)."""
    try:
        adapter = router.get_model_backend(role)
        content = await adapter.call(system_prompt, user_message)
        return (content or "").strip()
    except Exception as exc:
        logger.warning("LLM 调用失败: %s", exc)
        return ""


async def _call_llm_from_config_data(
    config_data: Dict[str, Any],
    role: str,
    system_prompt: str,
    user_message: str,
) -> str:
    cfg = _merge_role_config(config_data, role)
    platform = str(cfg.get("model_platform") or "openai")
    api_mode = str(cfg.get("api_mode") or "chat_completions")
    url = str(cfg.get("url") or "")
    api_key = str(cfg.get("api_key") or "")
    model_name = str(cfg.get("model_name") or "")

    if platform != "openai":
        logger.warning("本地 fallback 仅支持 openai 兼容配置，当前=%s", platform)
        return ""
    if not url or not api_key or not model_name:
        logger.warning("fallback LLM 配置不完整，无法生成报告")
        return ""

    endpoint = _resolve_openai_endpoint(url, api_mode)
    headers = {"Content-Type": "application/json"}
    if _is_azure_url(url):
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    temperature = cfg.get("temperature", 0.7)
    timeout = float(cfg.get("timeout") or 120.0)
    max_tokens = cfg.get("max_tokens")

    if api_mode == "responses":
        body: Dict[str, Any] = {
            "model": model_name,
            "input": [{
                "role": "user",
                "content": [{"type": "input_text", "text": user_message}],
            }],
            "temperature": temperature,
        }
        if system_prompt:
            body["instructions"] = system_prompt
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
    else:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        body = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("fallback LLM 请求失败: %s", exc)
        return ""

    if api_mode == "responses":
        return _extract_responses_text(data).strip()
    return _extract_chat_completions_text(data).strip()


# =============================================================================
# Multi-round LLM report generation
# =============================================================================

async def generate_report(
    result: Dict[str, Any],
    config_file: Optional[str],
    rounds: List[ReportRound],
    role: str = "omniscient",
    max_llm_calls: int = 10,
) -> Optional[str]:
    """共享核心库报告生成器。 / Delegate to the shared core report generator."""
    from ripple.reporting import generate_report_from_result

    return await generate_report_from_result(
        result=result,
        rounds=[
            {
                "label": round_spec.label,
                "system_prompt": round_spec.system_prompt,
                "extra_user_context": round_spec.extra_user_context,
            }
            for round_spec in rounds
        ],
        role=role,
        max_llm_calls=max_llm_calls,
        config_file=config_file,
    )


def load_skill_report_bundle(request: Dict[str, Any]) -> tuple[List[ReportRound], str, int]:
    """从 Skill 加载报告模板。 / Load the report template bundle from the skill."""
    from ripple.reporting import build_skill_report_profile

    profile = build_skill_report_profile(request=request)
    rounds = [
        ReportRound(
            label=round_spec.label,
            system_prompt=round_spec.system_prompt,
            extra_user_context=round_spec.extra_user_context,
        )
        for round_spec in profile.rounds
    ]
    return rounds, profile.role, profile.max_llm_calls



# =============================================================================
# Run infrastructure
# =============================================================================

def config_file_path() -> Optional[str]:
    """Return path to project-root llm_config.yaml, or None."""
    p = REPO_ROOT / "llm_config.yaml"
    return str(p) if p.exists() else None


def print_result_summary(
    result: Dict[str, Any],
    label: str,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> None:
    """Print simulation run metadata."""
    print()
    print("=" * 60)
    print(f"  {label} — 运行摘要")
    print("=" * 60)
    print(f"  run_id:              {result.get('run_id')}")
    print(f"  total_waves:         {result.get('total_waves')}")
    print(f"  wave_records_count:  {result.get('wave_records_count')}")
    if extra_fields:
        for k, v in extra_fields.items():
            print(f"  {k + ':':22s}{v}")
    if result.get("output_file"):
        print(f"  output_file:         {result['output_file']}")
    if result.get("compact_log_file"):
        print(f"  compact_log:         {result['compact_log_file']}")
    print("=" * 60)


def print_compact_log(result: Dict[str, Any], label: str) -> None:
    """Print the compact markdown log as the final report."""
    compact_log = result.get("compact_log_file")
    if compact_log and Path(compact_log).exists():
        content = Path(compact_log).read_text(encoding="utf-8")
        print()
        print("=" * 60)
        print(f"  {label} — 精简日志")
        print("=" * 60)
        print(content)
        print("=" * 60)
    else:
        print(f"\n  ⚠ 精简日志不可用（compact_log_file 不存在）")


async def run_and_interpret(
    label: str,
    run_coro,
    config_file: Optional[str],
    report_rounds: Optional[List[ReportRound]] = None,
    report_role: str = "omniscient",
    report_max_llm_calls: int = 10,
    extra_summary_fields: Optional[Dict[str, Any]] = None,
    no_report: bool = False,
) -> Dict[str, Any]:
    """Execute a simulation coroutine, print summary, optionally generate LLM report.

    When *no_report* is True or *report_rounds* is None, only the compact
    markdown log is displayed.
    """
    result = await run_coro
    print_result_summary(result, label, extra_fields=extra_summary_fields)

    # Always show compact markdown log
    print_compact_log(result, label)

    # Optionally generate LLM interpretation report
    if report_rounds and not no_report and config_file:
        report = await generate_report(
            result,
            config_file,
            report_rounds,
            role=report_role,
            max_llm_calls=report_max_llm_calls,
        )
        if report:
            print()
            print("=" * 60)
            print(f"  {label} — LLM 解读报告")
            print("=" * 60)
            print(report)
            print("=" * 60)
        else:
            print(f"\n  ⚠ LLM 解读报告生成失败，请检查 llm_config.yaml。")

    return result


# =============================================================================
# CLI helpers
# =============================================================================

def create_arg_parser(
    description: str,
    *,
    modes: Sequence[str] = ("basic", "enhanced", "all"),
    default_waves: int = 24,
) -> argparse.ArgumentParser:
    """Create a standard argument parser for E2E scripts."""
    parser = argparse.ArgumentParser(description=description)
    if modes:
        parser.add_argument(
            "mode",
            choices=list(modes),
            help="；".join(f"{m}" for m in modes),
        )
    parser.add_argument(
        "--waves",
        type=int,
        default=default_waves,
        help=f"最大 wave 数（默认 {default_waves}；快速试跑可用 --waves 2）",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="不生成 LLM 解读报告",
    )
    return parser
