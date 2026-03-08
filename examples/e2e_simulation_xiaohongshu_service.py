#!/usr/bin/env python3
# =============================================================================
# e2e_simulation_xiaohongshu_service.py — Xiaohongshu 48h E2E via HTTP+SSE
#
# Two modes: basic (topic only) / enhanced (topic + account + history)
#
# The service stores artifacts under `/data/ripple_outputs/`. When the container
# mounts the host `data/ripple-service/` directory to `/data`, the JSON result
# and compact Markdown log become available on the host under:
#   data/ripple-service/ripple_outputs/
#
# This script is a pure HTTP client:
#   1) create simulation job
#   2) stream SSE progress
#   3) poll final result
#   4) call the service-side report endpoint
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
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Sequence

import httpx

from e2e_helpers import (
    REPO_ROOT,
    build_event_from_topic,
    build_historical_from_posts,
    build_source_from_account,
    create_arg_parser,
    is_terminal_job_status,
    print_compact_log,
    print_progress,
    print_result_summary,
    progress_event_from_service_event,
    result_from_service_job,
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
    build_report_rounds,
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
DEFAULT_ARTIFACTS_DIR = REPO_ROOT / "data" / "ripple-service" / "ripple_outputs"
_CONTAINER_ARTIFACTS_DIR = "/data/ripple_outputs"
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


async def _wait_for_local_artifacts(
    result: Dict[str, Any],
    *,
    wait_timeout: float = 5.0,
    poll_interval: float = 0.2,
) -> None:
    compact_log = result.get("compact_log_file")
    output_file = result.get("output_file")
    paths = [Path(path) for path in (compact_log, output_file) if isinstance(path, str) and path]
    if not paths:
        return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_timeout
    while True:
        if all(path.exists() for path in paths):
            return
        if loop.time() >= deadline:
            logger.warning("服务产物尚未全部出现在宿主机目录: %s", [str(path) for path in paths])
            return
        await asyncio.sleep(poll_interval)


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
    artifacts_dir: Path,
) -> Dict[str, Any]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
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

    result = result_from_service_job(
        job,
        local_root=artifacts_dir,
        container_root=_CONTAINER_ARTIFACTS_DIR,
    )
    result["service_job_id"] = job_id
    await _wait_for_local_artifacts(result)
    return result


async def run_basic_service(
    waves: int,
    *,
    base_url: str,
    api_token: str,
    poll_interval: float,
    wait_timeout: float,
    artifacts_dir: Path,
) -> Dict[str, Any]:
    return await _run_service_simulation(
        label="基础模拟",
        request=_build_request(waves=waves, source=None, historical=None),
        base_url=base_url,
        api_token=api_token,
        poll_interval=poll_interval,
        wait_timeout=wait_timeout,
        artifacts_dir=artifacts_dir,
    )


async def run_enhanced_service(
    waves: int,
    *,
    base_url: str,
    api_token: str,
    poll_interval: float,
    wait_timeout: float,
    artifacts_dir: Path,
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
        artifacts_dir=artifacts_dir,
    )


async def _run_mode(
    *,
    label: str,
    run_coro,
    report_rounds: Sequence[Any],
    base_url: str,
    api_token: str,
    report_max_llm_calls: int,
    report_timeout: float,
    no_report: bool,
) -> Dict[str, Any]:
    result = await run_coro
    print_result_summary(
        result,
        label,
        extra_fields={"service_job_id": result.get("service_job_id")},
    )
    print_compact_log(result, label)

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
    parser.add_argument(
        "--artifacts-dir",
        default=str(DEFAULT_ARTIFACTS_DIR),
        help=f"宿主机日志目录（默认 {DEFAULT_ARTIFACTS_DIR}）",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    api_token = str(args.api_token or "").strip()
    waves = args.waves
    no_report = args.no_report
    artifacts_dir = Path(args.artifacts_dir)

    if args.mode in ("basic", "all"):
        await _run_mode(
            label="基础模拟",
            run_coro=run_basic_service(
                waves,
                base_url=base_url,
                api_token=api_token,
                poll_interval=args.poll_interval,
                wait_timeout=args.timeout,
                artifacts_dir=artifacts_dir,
            ),
            report_rounds=build_report_rounds(),
            base_url=base_url,
            api_token=api_token,
            report_max_llm_calls=args.report_max_llm_calls,
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
                artifacts_dir=artifacts_dir,
            ),
            report_rounds=build_report_rounds(SAMPLE_ACCOUNT, SAMPLE_POSTS),
            base_url=base_url,
            api_token=api_token,
            report_max_llm_calls=args.report_max_llm_calls,
            report_timeout=args.timeout,
            no_report=no_report,
        )


if __name__ == "__main__":
    asyncio.run(main())
