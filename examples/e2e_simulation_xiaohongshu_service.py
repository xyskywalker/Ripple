#!/usr/bin/env python3
# =============================================================================
# e2e_simulation_xiaohongshu_service.py — Xiaohongshu 48h E2E via HTTP+SSE
#
# Two modes: basic (topic only) / enhanced (topic + account + history)
#
# This script is a pure HTTP client and does not rely on any host/container
# artifact mount mapping:
#   1) create simulation job
#   2) stream SSE progress
#   3) poll final result
#   4) fetch output-json + compact-log via service APIs
#   5) call the service-side report endpoint
#
# Usage:
#   python examples/e2e_simulation_xiaohongshu_service.py basic
#   python examples/e2e_simulation_xiaohongshu_service.py enhanced
#   python examples/e2e_simulation_xiaohongshu_service.py all
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import Any, AsyncIterator, Dict, List, Sequence

import httpx

from e2e_helpers import (
    build_event_from_topic,
    build_historical_from_posts,
    build_source_from_account,
    create_arg_parser,
    is_terminal_job_status,
    print_progress,
    progress_event_from_service_event,
    setup_logging,
)
from e2e_xiaohongshu_common import (
    DEFAULT_WAVES,
    MAX_LLM_CALLS,
    PLATFORM,
    SAMPLE_ACCOUNT,
    SAMPLE_POSTS,
    SAMPLE_TOPIC,
    SIMULATION_HOURS,
    build_report_bundle,
)

setup_logging()
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.getenv("RIPPLE_BASE_URL", "http://127.0.0.1:8080")
DEFAULT_API_TOKEN = os.getenv("RIPPLE_API_TOKEN", "")
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_WAIT_TIMEOUT = 4500.0
DEFAULT_REPORT_TIMEOUT = DEFAULT_WAIT_TIMEOUT
DEFAULT_REPORT_MAX_LLM_CALLS = 10
_TERMINAL_SSE_TYPES = {"job.completed", "job.failed", "job.cancelled"}


class ServiceProgressPrinter:
    """Bridge service SSE payloads to the local terminal progress renderer."""

    def __init__(self) -> None:
        self._total_waves: int | None = None

    def handle(self, service_event: Dict[str, Any]) -> None:
        progress_event = progress_event_from_service_event(service_event)
        if progress_event is not None:
            detail = progress_event.detail or {}
            if progress_event.phase == "INIT" and progress_event.type == "phase_end":
                estimated = detail.get("estimated_waves")
                if isinstance(estimated, int):
                    self._total_waves = estimated
            if progress_event.total_waves is None:
                progress_event.total_waves = self._total_waves
            print_progress(progress_event)
            return

        event_type = str(service_event.get("type") or "")
        if event_type == "job.started":
            print("  [service] 任务已启动")
        elif event_type == "job.completed":
            print("  [service] 任务已完成")
        elif event_type == "job.failed":
            print("  [service] 任务失败")
        elif event_type == "job.cancelled":
            print("  [service] 任务已取消")


def _build_headers(api_token: str = "") -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    token = api_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_request(
    *,
    waves: int,
    source: Dict[str, Any] | None,
    historical: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    return {
        "event": build_event_from_topic(SAMPLE_TOPIC),
        "skill": "social-media",
        "platform": PLATFORM,
        "source": source,
        "historical": historical,
        "environment": None,
        "max_waves": waves,
        "max_llm_calls": MAX_LLM_CALLS,
        "simulation_horizon": f"{SIMULATION_HOURS}h",
        "ensemble_runs": 1,
    }


async def _create_job(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    request: Dict[str, Any],
) -> str:
    response = await client.post(f"{base_url}/v1/simulations", headers=headers, json=request)
    response.raise_for_status()
    payload = response.json()
    return str(payload["job_id"])


async def _get_job(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    job_id: str,
) -> Dict[str, Any]:
    response = await client.get(f"{base_url}/v1/simulations/{job_id}", headers=headers)
    response.raise_for_status()
    return response.json()


async def _iter_sse_messages(response: httpx.Response) -> AsyncIterator[Dict[str, Any]]:
    event_name = "message"
    data_lines: List[str] = []

    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield {
                    "event": event_name,
                    "data": json.loads("\n".join(data_lines)),
                }
            event_name = "message"
            data_lines = []
            continue

        field, _, value = line.partition(":")
        value = value.lstrip()
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)

    if data_lines:
        yield {
            "event": event_name,
            "data": json.loads("\n".join(data_lines)),
        }


async def _stream_job_events(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    job_id: str,
    printer: ServiceProgressPrinter,
) -> None:
    try:
        async with client.stream(
            "GET",
            f"{base_url}/v1/simulations/{job_id}/events",
            headers=headers,
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for message in _iter_sse_messages(response):
                service_event = message["data"]
                if isinstance(service_event, dict):
                    printer.handle(service_event)
                    if str(service_event.get("type") or "") in _TERMINAL_SSE_TYPES:
                        break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("SSE 订阅提前结束: %s", exc)


async def _wait_for_terminal_job(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    job_id: str,
    poll_interval: float,
    wait_timeout: float,
) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_timeout

    while True:
        job = await _get_job(client, base_url=base_url, headers=headers, job_id=job_id)
        status = str(job.get("status") or "")
        if is_terminal_job_status(status):
            return job
        if loop.time() >= deadline:
            raise TimeoutError(f"等待任务超时: {job_id} status={status}")
        await asyncio.sleep(poll_interval)


def _artifact_urls(base_url: str, job_id: str) -> Dict[str, str]:
    return {
        "output_json": f"{base_url}/v1/simulations/{job_id}/artifacts/output-json",
        "compact_log": f"{base_url}/v1/simulations/{job_id}/artifacts/compact-log",
    }


async def _request_output_json(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    job_id: str,
) -> Dict[str, Any] | None:
    response = await client.get(
        f"{base_url}/v1/simulations/{job_id}/artifacts/output-json",
        headers=headers,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.warning(
            "获取 output-json 失败: job_id=%s status=%s detail=%s",
            job_id,
            response.status_code,
            response.text,
        )
        return None

    payload = response.json()
    if isinstance(payload, dict):
        return payload

    logger.warning("output-json 响应不是 JSON 对象: job_id=%s", job_id)
    return None


async def _request_compact_log(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    job_id: str,
) -> str | None:
    response = await client.get(
        f"{base_url}/v1/simulations/{job_id}/artifacts/compact-log",
        headers=headers,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.warning(
            "获取 compact-log 失败: job_id=%s status=%s detail=%s",
            job_id,
            response.status_code,
            response.text,
        )
        return None

    text = response.text
    return text if text.strip() else None


async def _fetch_service_artifacts(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: Dict[str, str],
    job_id: str,
) -> Dict[str, Any]:
    urls = _artifact_urls(base_url, job_id)
    output_json = await _request_output_json(
        client,
        base_url=base_url,
        headers=headers,
        job_id=job_id,
    )
    compact_log_text = await _request_compact_log(
        client,
        base_url=base_url,
        headers=headers,
        job_id=job_id,
    )
    return {
        "artifact_urls": urls,
        "output_json": output_json,
        "compact_log_text": compact_log_text,
    }


async def _request_report(
    *,
    base_url: str,
    api_token: str,
    job_id: str,
    rounds: Sequence[Any],
    role: str = "omniscient",
    max_llm_calls: int = DEFAULT_REPORT_MAX_LLM_CALLS,
    report_timeout: float = DEFAULT_REPORT_TIMEOUT,
) -> str | None:
    headers = _build_headers(api_token)
    payload = {
        "rounds": _serialize_report_rounds(rounds),
        "role": role,
        "max_llm_calls": max_llm_calls,
    }

    timeout = httpx.Timeout(report_timeout, connect=30.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{base_url}/v1/simulations/{job_id}/report",
            headers=headers,
            json=payload,
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        detail = response.text
        with suppress(Exception):
            detail_json = response.json()
            if isinstance(detail_json, dict) and detail_json.get("detail"):
                detail = str(detail_json["detail"])
        logger.warning("服务端报告生成失败: status=%s detail=%s", response.status_code, detail)
        return None

    report = response.json().get("report")
    if isinstance(report, str) and report.strip():
        return report.strip()
    return None


def _serialize_report_rounds(rounds: Sequence[Any]) -> List[Dict[str, str]]:
    payload: List[Dict[str, str]] = []
    for index, round_spec in enumerate(rounds, start=1):
        payload.append(
            {
                "label": str(getattr(round_spec, "label", f"round_{index}") or f"round_{index}"),
                "system_prompt": str(getattr(round_spec, "system_prompt", "") or ""),
                "extra_user_context": str(getattr(round_spec, "extra_user_context", "") or ""),
            }
        )
    return payload
def _summary_value(result: Dict[str, Any], key: str) -> Any:
    value = result.get(key)
    if value is not None:
        return value

    output_json = result.get("output_json")
    if isinstance(output_json, dict):
        value = output_json.get(key)
        if value is not None:
            return value
        if key == "wave_records_count":
            waves = ((output_json.get("process") or {}).get("waves") or [])
            if isinstance(waves, list):
                return len(waves)
    return None


def _print_service_result_summary(result: Dict[str, Any], label: str) -> None:
    artifact_urls = result.get("artifact_urls") or {}

    print()
    print("=" * 60)
    print(f"  {label} — 运行摘要")
    print("=" * 60)
    print(f"  run_id:              {_summary_value(result, 'run_id')}")
    print(f"  total_waves:         {_summary_value(result, 'total_waves')}")
    print(f"  wave_records_count:  {_summary_value(result, 'wave_records_count')}")
    print(f"  service_job_id:       {result.get('service_job_id')}")
    if artifact_urls.get("output_json"):
        print(f"  output_json_api:     {artifact_urls['output_json']}")
    if artifact_urls.get("compact_log"):
        print(f"  compact_log_api:     {artifact_urls['compact_log']}")
    print("=" * 60)


def _print_compact_log_text(label: str, compact_log_text: str | None) -> None:
    if not compact_log_text:
        print("\n  ⚠ 精简日志不可用（compact-log API 无内容）")
        return

    print()
    print("=" * 60)
    print(f"  {label} — 精简日志")
    print("=" * 60)
    print(compact_log_text)
    print("=" * 60)


def _print_service_report(label: str, report: str) -> None:
    print()
    print("=" * 60)
    print(f"  {label} — 服务端 LLM 解读报告")
    print("=" * 60)
    print(report)
    print("=" * 60)


async def _run_service_simulation(
    *,
    label: str,
    request: Dict[str, Any],
    base_url: str,
    api_token: str,
    poll_interval: float,
    wait_timeout: float,
) -> Dict[str, Any]:
    headers = _build_headers(api_token)
    printer = ServiceProgressPrinter()

    print()
    print("─" * 60)
    print(f"  {label} — HTTP+SSE 实时进度")
    print("─" * 60)

    async with httpx.AsyncClient() as client:
        job_id = await _create_job(
            client,
            base_url=base_url,
            headers=headers,
            request=request,
        )
        print(f"  [service] job_id={job_id}")

        stream_task = asyncio.create_task(
            _stream_job_events(
                client,
                base_url=base_url,
                headers=headers,
                job_id=job_id,
                printer=printer,
            )
        )
        try:
            job = await _wait_for_terminal_job(
                client,
                base_url=base_url,
                headers=headers,
                job_id=job_id,
                poll_interval=poll_interval,
                wait_timeout=wait_timeout,
            )
        finally:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stream_task, timeout=3)
            if not stream_task.done():
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task

        status = str(job.get("status") or "")
        if status == "failed":
            raise RuntimeError(f"服务任务失败: {job.get('error')}")
        if status == "cancelled":
            raise RuntimeError(f"服务任务被取消: {job_id}")

        result = dict(job.get("result") or {})
        result["service_job_id"] = job_id
        result.update(
            await _fetch_service_artifacts(
                client,
                base_url=base_url,
                headers=headers,
                job_id=job_id,
            )
        )

    return result


async def run_basic_service(
    waves: int,
    *,
    base_url: str,
    api_token: str,
    poll_interval: float,
    wait_timeout: float,
) -> Dict[str, Any]:
    return await _run_service_simulation(
        label="基础模拟",
        request=_build_request(waves=waves, source=None, historical=None),
        base_url=base_url,
        api_token=api_token,
        poll_interval=poll_interval,
        wait_timeout=wait_timeout,
    )


async def run_enhanced_service(
    waves: int,
    *,
    base_url: str,
    api_token: str,
    poll_interval: float,
    wait_timeout: float,
) -> Dict[str, Any]:
    return await _run_service_simulation(
        label="增强模拟",
        request=_build_request(
            waves=waves,
            source=build_source_from_account(SAMPLE_ACCOUNT),
            historical=build_historical_from_posts(SAMPLE_POSTS),
        ),
        base_url=base_url,
        api_token=api_token,
        poll_interval=poll_interval,
        wait_timeout=wait_timeout,
    )


async def _run_mode(
    *,
    label: str,
    run_coro,
    report_rounds: Sequence[Any],
    report_role: str = "omniscient",
    base_url: str,
    api_token: str,
    report_max_llm_calls: int,
    report_timeout: float,
    no_report: bool,
) -> Dict[str, Any]:
    result = await run_coro
    _print_service_result_summary(result, label)
    _print_compact_log_text(label, result.get("compact_log_text"))

    if no_report:
        return result

    job_id = str(result.get("service_job_id") or "")
    if not job_id:
        logger.warning("缺少 service_job_id，无法请求服务端报告")
        return result

    report = await _request_report(
        base_url=base_url,
        api_token=api_token,
        job_id=job_id,
        rounds=report_rounds,
        role=report_role,
        max_llm_calls=report_max_llm_calls,
        report_timeout=report_timeout,
    )
    if report:
        _print_service_report(label, report)
    else:
        print("\n  ⚠ 服务端 LLM 解读报告生成失败，请检查服务日志与 LLM 配置。")
    return result


async def main() -> None:
    parser = create_arg_parser(
        "Ripple E2E — 小红书 48h 模拟（HTTP+SSE 服务版）",
        default_waves=DEFAULT_WAVES,
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"服务基地址（默认 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--api-token",
        default=DEFAULT_API_TOKEN,
        help="服务鉴权 Token（默认读取环境变量 RIPPLE_API_TOKEN）",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=f"状态轮询间隔秒数（默认 {DEFAULT_POLL_INTERVAL}）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT,
        help=f"等待任务完成的超时秒数（默认 {DEFAULT_WAIT_TIMEOUT}）",
    )
    parser.add_argument(
        "--report-max-llm-calls",
        type=int,
        default=DEFAULT_REPORT_MAX_LLM_CALLS,
        help=f"服务端报告阶段的 LLM 调用上限（默认 {DEFAULT_REPORT_MAX_LLM_CALLS}）",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    api_token = str(args.api_token or "").strip()
    waves = args.waves
    no_report = args.no_report
    basic_rounds, basic_role, basic_max_calls = build_report_bundle()
    enhanced_rounds, enhanced_role, enhanced_max_calls = build_report_bundle(SAMPLE_ACCOUNT, SAMPLE_POSTS)

    if args.mode in ("basic", "all"):
        await _run_mode(
            label="基础模拟",
            run_coro=run_basic_service(
                waves,
                base_url=base_url,
                api_token=api_token,
                poll_interval=args.poll_interval,
                wait_timeout=args.timeout,
            ),
            report_rounds=basic_rounds,
            report_role=basic_role,
            base_url=base_url,
            api_token=api_token,
            report_max_llm_calls=max(args.report_max_llm_calls, basic_max_calls),
            report_timeout=args.timeout,
            no_report=no_report,
        )

    if args.mode in ("enhanced", "all"):
        await _run_mode(
            label="增强模拟",
            run_coro=run_enhanced_service(
                waves,
                base_url=base_url,
                api_token=api_token,
                poll_interval=args.poll_interval,
                wait_timeout=args.timeout,
            ),
            report_rounds=enhanced_rounds,
            report_role=enhanced_role,
            base_url=base_url,
            api_token=api_token,
            report_max_llm_calls=max(args.report_max_llm_calls, enhanced_max_calls),
            report_timeout=args.timeout,
            no_report=no_report,
        )


if __name__ == "__main__":
    asyncio.run(main())
