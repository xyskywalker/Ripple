from __future__ import annotations

from pathlib import Path

import pytest

from ripple.service.job_manager import JobManager


@pytest.mark.asyncio
async def test_job_manager_sets_default_output_path(tmp_path: Path) -> None:
    captured: dict = {}

    async def fake_run(request: dict, on_progress):
        captured.update(request)
        return {"run_id": "run_demo", "total_waves": 1, "wave_records_count": 1}

    output_dir = tmp_path / "artifacts"
    manager = JobManager(
        db_path=tmp_path / "jobs.db",
        output_dir=output_dir,
        run_simulation=fake_run,
    )

    job_id = await manager.create_job({"event": {"title": "demo"}, "skill": "social-media"})
    await manager.wait(job_id, timeout=1)

    assert captured["output_path"] == str(output_dir) + "/"


@pytest.mark.asyncio
async def test_job_manager_preserves_explicit_output_path(tmp_path: Path) -> None:
    captured: dict = {}

    async def fake_run(request: dict, on_progress):
        captured.update(request)
        return {"run_id": "run_demo", "total_waves": 1, "wave_records_count": 1}

    manager = JobManager(
        db_path=tmp_path / "jobs.db",
        output_dir=tmp_path / "artifacts",
        run_simulation=fake_run,
    )

    job_id = await manager.create_job(
        {
            "event": {"title": "demo"},
            "skill": "social-media",
            "output_path": "/custom/output/",
        }
    )
    await manager.wait(job_id, timeout=1)

    assert captured["output_path"] == "/custom/output/"
