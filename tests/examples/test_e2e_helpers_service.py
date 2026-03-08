from __future__ import annotations

import sys
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from e2e_helpers import (
    is_terminal_job_status,
    progress_event_from_service_event,
    resolve_service_artifact_path,
    result_from_service_job,
)


def test_is_terminal_job_status() -> None:
    assert is_terminal_job_status("completed") is True
    assert is_terminal_job_status("failed") is True
    assert is_terminal_job_status("cancelled") is True
    assert is_terminal_job_status("running") is False


def test_progress_event_from_service_event_maps_payload() -> None:
    event = {
        "job_id": "job_123",
        "type": "progress.phase_end",
        "payload": {
            "phase": "INIT",
            "wave": None,
            "progress": 0.25,
            "detail": {"estimated_waves": 24},
            "agent_id": None,
            "agent_type": None,
        },
    }

    progress = progress_event_from_service_event(event)

    assert progress is not None
    assert progress.run_id == "job_123"
    assert progress.type == "phase_end"
    assert progress.phase == "INIT"
    assert progress.progress == 0.25
    assert progress.detail == {"estimated_waves": 24}


def test_progress_event_from_service_event_ignores_non_progress_events() -> None:
    event = {
        "job_id": "job_123",
        "type": "job.completed",
        "payload": {},
    }

    assert progress_event_from_service_event(event) is None


def test_resolve_service_artifact_path_maps_container_path_to_repo_local() -> None:
    local_root = Path("/tmp/ripple_outputs_host")

    resolved = resolve_service_artifact_path(
        "/app/ripple_outputs/20260307_demo.json",
        local_root=local_root,
    )

    assert resolved == str(local_root / "20260307_demo.json")


def test_result_from_service_job_normalizes_artifact_paths() -> None:
    local_root = Path("/tmp/ripple_outputs_host")
    job = {
        "job_id": "job_abc",
        "status": "completed",
        "result": {
            "run_id": "run_demo",
            "total_waves": 2,
            "output_file": "/app/ripple_outputs/run_demo.json",
            "compact_log_file": "/app/ripple_outputs/run_demo.md",
        },
    }

    result = result_from_service_job(job, local_root=local_root)

    assert result["run_id"] == "run_demo"
    assert result["total_waves"] == 2
    assert result["output_file"] == str(local_root / "run_demo.json")
    assert result["compact_log_file"] == str(local_root / "run_demo.md")



def test_print_progress_renders_deliberation_round_updates(capsys) -> None:
    from e2e_helpers import ProgressEvent, print_progress

    print_progress(
        ProgressEvent(
            type="round_start",
            phase="DELIBERATE",
            run_id="job_demo",
            progress=0.75,
            detail={"round_number": 1, "total_rounds": 3},
        )
    )
    print_progress(
        ProgressEvent(
            type="round_end",
            phase="DELIBERATE",
            run_id="job_demo",
            progress=0.8,
            detail={"round_number": 1, "total_rounds": 3, "converged": False},
        )
    )

    out = capsys.readouterr().out
    assert "合议" in out
    assert "Round 1/3" in out
