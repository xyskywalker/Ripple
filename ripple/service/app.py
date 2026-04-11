from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

from ripple.llm.router import ConfigurationError

from .auth import require_bearer
from .job_manager import JobManager
from .reporting import (
    extract_request_llm_config,
    generate_report_from_result,
    load_compact_log_text,
    load_job_request,
    load_job_result,
    load_output_json_document,
)
from .settings import ServiceSettings


def _load_json(text: str | None) -> dict | None:
    if not text:
        return None
    return json.loads(text)


def create_app() -> FastAPI:
    settings = ServiceSettings.from_env()
    manager = JobManager(db_path=settings.db_path, output_dir=settings.output_dir)

    app = FastAPI(title="Ripple Service")

    def _get_row(job_id: str) -> dict:
        try:
            return manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    def _get_completed_result(job_id: str) -> tuple[dict, dict]:
        row = _get_row(job_id)
        if row["status"] != "completed":
            raise HTTPException(status_code=409, detail=f"job not completed: status={row['status']}")
        result = load_job_result(row)
        if not result:
            raise HTTPException(status_code=409, detail="job result unavailable")
        return row, result

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "ripple-http-sse",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/v1/ping", dependencies=[Depends(require_bearer)])
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    @app.post("/v1/simulations", dependencies=[Depends(require_bearer)])
    async def create_simulation(request: dict) -> dict:
        job_id = await manager.create_job(request)
        return {
            "job_id": job_id,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/v1/simulations/{job_id}", dependencies=[Depends(require_bearer)])
    def get_simulation(job_id: str) -> dict:
        row = _get_row(job_id)
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "request": _load_json(row["request_json"]),
            "result": _load_json(row["result_json"]),
            "error": _load_json(row["error_json"]),
        }

    @app.get("/v1/simulations/{job_id}/artifacts/compact-log", dependencies=[Depends(require_bearer)])
    def get_compact_log(job_id: str) -> PlainTextResponse:
        _, result = _get_completed_result(job_id)
        try:
            content = load_compact_log_text(result)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return PlainTextResponse(content, media_type="text/plain")

    @app.get("/v1/simulations/{job_id}/artifacts/output-json", dependencies=[Depends(require_bearer)])
    def get_output_json(job_id: str) -> dict:
        _, result = _get_completed_result(job_id)
        try:
            return load_output_json_document(result)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/simulations/{job_id}/report", dependencies=[Depends(require_bearer)])
    async def generate_report(job_id: str, payload: dict) -> dict:
        rounds = payload.get("rounds")
        if not isinstance(rounds, list) or not rounds:
            raise HTTPException(status_code=422, detail="rounds is required")

        role = str(payload.get("role") or "omniscient")
        max_llm_calls_raw = payload.get("max_llm_calls", 10)
        try:
            max_llm_calls = int(max_llm_calls_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="max_llm_calls must be an integer") from exc

        row, result = _get_completed_result(job_id)
        request = load_job_request(row) or {}
        llm_config = extract_request_llm_config(request)

        # 可选：流式模式和超时覆盖 / Optional: stream mode and timeout override
        stream_val = payload.get("stream")
        stream = stream_val if isinstance(stream_val, bool) else None
        llm_timeout_raw = payload.get("llm_timeout")
        llm_timeout = float(llm_timeout_raw) if llm_timeout_raw is not None else None

        try:
            report = await generate_report_from_result(
                result=result,
                rounds=rounds,
                role=role,
                max_llm_calls=max_llm_calls,
                config_file=settings.llm_config_path,
                llm_config=llm_config,
                stream=stream,
                llm_timeout=llm_timeout,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ConfigurationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if not report:
            raise HTTPException(status_code=500, detail="report generation failed")
        return {
            "job_id": job_id,
            "report": report,
        }

    @app.get("/v1/simulations/{job_id}/events", dependencies=[Depends(require_bearer)])
    async def stream_events(job_id: str):
        _get_row(job_id)

        async def event_stream():
            q = manager.event_bus.subscribe(job_id)
            while True:
                event = await q.get()
                yield f"event: {event['type']}\n"
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in {"job.completed", "job.failed", "job.cancelled"}:
                    break

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/v1/simulations/{job_id}/cancel-request", dependencies=[Depends(require_bearer)])
    def cancel_request(job_id: str) -> dict:
        try:
            data = manager.request_cancel(job_id, ttl_seconds=settings.cancel_ttl_seconds)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "job_id": job_id,
            **data,
        }

    @app.post("/v1/simulations/{job_id}/cancel-confirm", dependencies=[Depends(require_bearer)])
    def cancel_confirm(job_id: str, payload: dict) -> dict:
        token = str(payload.get("cancel_token", ""))
        if not token:
            raise HTTPException(status_code=422, detail="cancel_token is required")
        try:
            manager.confirm_cancel(job_id, token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "job_id": job_id,
            "status": "cancelling",
        }

    return app
