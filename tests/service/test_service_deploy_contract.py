from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_uses_single_data_mount_for_service_artifacts() -> None:
    compose = yaml.safe_load((ROOT / "deploy" / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose["services"]["ripple-service"]["volumes"]

    assert "../../data/ripple-service:/data" in volumes
    assert all("/app/ripple_outputs" not in str(volume) for volume in volumes)


def test_start_script_prepares_artifact_directory_under_data_mount() -> None:
    script = (ROOT / "deploy" / "scripts" / "ripple-service-start.sh").read_text(encoding="utf-8")

    assert "mkdir -p data/ripple-service/ripple_outputs" in script
    assert "mkdir -p data/ripple-service ripple_outputs" not in script
