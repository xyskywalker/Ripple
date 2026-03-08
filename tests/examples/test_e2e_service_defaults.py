from __future__ import annotations

import sys
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import e2e_simulation_xiaohongshu_service as service_example


def test_service_example_uses_single_data_mount_directory() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    assert service_example.DEFAULT_ARTIFACTS_DIR == repo_root / "data" / "ripple-service" / "ripple_outputs"
    assert service_example._CONTAINER_ARTIFACTS_DIR == "/data/ripple_outputs"


def test_service_example_does_not_override_service_output_path() -> None:
    request = service_example._build_request(waves=2, source=None, historical=None)

    assert "output_path" not in request


def test_service_example_uses_extended_default_timeout() -> None:
    assert service_example.DEFAULT_WAIT_TIMEOUT == 4500.0
