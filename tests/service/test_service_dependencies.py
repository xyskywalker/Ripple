from __future__ import annotations

from pathlib import Path
import tomllib


def _project_dependencies() -> list[str]:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)
    return list(pyproject["project"]["dependencies"])


def test_service_runtime_dependencies_are_declared() -> None:
    deps = _project_dependencies()

    assert any(dep.startswith("fastapi") for dep in deps)
    assert any(dep.startswith("uvicorn") for dep in deps)
