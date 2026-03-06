from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .auth import require_bearer
from .job_manager import JobManager
from .settings import ServiceSettings


def _load_json(text: str | None) -> dict | None:
    if not text:
        return None
    return json.loads(text)


def create_app() -> FastAPI:
    settings = ServiceSettings.from_env()
    manager = JobManager(db_path=settings.db_path)

    app = FastAPI(title="Ripple Service")

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
        try:
            row = manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "request": _load_json(row["request_json"]),
            "result": _load_json(row["result_json"]),
            "error": _load_json(row["error_json"]),
        }

    @app.get("/v1/simulations/{job_id}/events", dependencies=[Depends(require_bearer)])
    async def stream_events(job_id: str):
        try:
            manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

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
