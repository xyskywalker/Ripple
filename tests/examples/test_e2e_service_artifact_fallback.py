from __future__ import annotations

import sys
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import e2e_helpers as helpers


def test_result_from_service_job_preserves_original_service_artifact_paths() -> None:
    job = {
        "result": {
            "output_file": "/app/ripple_outputs/demo.json",
            "compact_log_file": "/app/ripple_outputs/demo.md",
        }
    }

    result = helpers.result_from_service_job(job, local_root=Path("/tmp/out"))

    assert result["service_artifacts"]["output_file"] == "/app/ripple_outputs/demo.json"
    assert result["service_artifacts"]["compact_log_file"] == "/app/ripple_outputs/demo.md"


def test_load_simulation_log_reads_compact_log_from_container_when_local_file_missing(monkeypatch) -> None:
    result = {
        "compact_log_file": "/tmp/not-mounted/demo.md",
        "service_artifacts": {
            "compact_log_file": "/app/ripple_outputs/demo.md",
        },
    }

    monkeypatch.setattr(
        helpers,
        "_read_text_from_container",
        lambda path_value, container_name="ripple-service": "# compact from container" if path_value == "/app/ripple_outputs/demo.md" else None,
    )

    log_text = helpers.load_simulation_log(result)

    assert log_text == "# compact from container"
