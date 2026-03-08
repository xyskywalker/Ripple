from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from ripple.primitives.events import SimulationEvent

from .event_bus import EventBus
from .job_repo_sqlite import JobRepoSQLite
from .runner import run_simulation_with_progress

RunSimulationFn = Callable[[dict, Callable[[SimulationEvent], Awaitable[None]]], Awaitable[dict]]


class JobManager:
    def __init__(
        self,
        db_path: str | Path,
        output_dir: str | Path = 'ripple_outputs',
        run_simulation: RunSimulationFn = run_simulation_with_progress,
    ):
        self.repo = JobRepoSQLite(db_path=db_path)
        self.repo.init_schema()
        self.event_bus = EventBus()
        self._run_simulation = run_simulation
        self._output_dir = Path(output_dir)
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_tokens: dict[str, tuple[str, datetime]] = {}

    async def create_job(self, request: dict) -> str:
        normalized_request = dict(request)
        if not normalized_request.get('output_path'):
            self._output_dir.mkdir(parents=True, exist_ok=True)
            normalized_request['output_path'] = str(self._output_dir) + '/'

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        self.repo.create_job(job_id, normalized_request)
        self._tasks[job_id] = asyncio.create_task(self._execute(job_id=job_id, request=normalized_request))
        return job_id

    async def _on_progress(self, job_id: str, event: SimulationEvent) -> None:
        await self.event_bus.publish(
            job_id=job_id,
            event_type=f"progress.{event.type}",
            payload={
                "phase": event.phase,
                "wave": event.wave,
                "progress": event.progress,
                "detail": event.detail or {},
                "agent_id": event.agent_id,
                "agent_type": event.agent_type,
            },
        )

    async def _execute(self, job_id: str, request: dict) -> None:
        self.repo.update_status(job_id, "running")
        await self.event_bus.publish(job_id, "job.started", {"phase": "INIT"})
        try:
            result = await self._run_simulation(request, lambda ev: self._on_progress(job_id, ev))
            self.repo.set_result(job_id, result)
            self.repo.update_status(job_id, "completed")
            await self.event_bus.publish(job_id, "job.completed", result)
        except asyncio.CancelledError:
            self.repo.update_status(job_id, "cancelled")
            await self.event_bus.publish(job_id, "job.cancelled", {"message": "cancelled"})
            raise
        except Exception as exc:  # pragma: no cover - safety path
            err = {"code": "simulation_error", "message": str(exc)}
            self.repo.set_error(job_id, err)
            self.repo.update_status(job_id, "failed")
            await self.event_bus.publish(job_id, "job.failed", err)

    def get_job(self, job_id: str) -> dict:
        return self.repo.get_job(job_id)

    async def wait(self, job_id: str, timeout: float) -> None:
        await asyncio.wait_for(self._tasks[job_id], timeout=timeout)

    def request_cancel(self, job_id: str, ttl_seconds: int = 60) -> dict:
        row = self.get_job(job_id)
        if row["status"] not in {"running", "cancel_pending"}:
            raise ValueError(f"job not cancellable in status={row['status']}")

        if row["status"] == "running":
            self.repo.update_status(job_id, "cancel_pending")

        token = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        self._cancel_tokens[job_id] = (token, expires_at)
        return {
            "cancel_token": token,
            "expires_at": expires_at.isoformat(),
        }

    def confirm_cancel(self, job_id: str, token: str) -> None:
        row = self.get_job(job_id)
        if row["status"] not in {"cancel_pending", "running"}:
            raise ValueError(f"job not cancellable in status={row['status']}")

        entry = self._cancel_tokens.get(job_id)
        if not entry:
            raise ValueError("missing cancel token")
        expected, expires_at = entry
        if datetime.now(timezone.utc) > expires_at:
            raise ValueError("cancel token expired")
        if token != expected:
            raise ValueError("cancel token mismatch")

        self.repo.update_status(job_id, "cancelling")
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
