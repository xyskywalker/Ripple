"""Tests for unified runtime version sourcing."""

import tomllib
from pathlib import Path

from ripple import __version__
from ripple.version import get_version


def _pyproject_version() -> str:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def test_runtime_version_matches_pyproject():
    assert get_version() == _pyproject_version()


def test_package_version_matches_pyproject():
    assert __version__ == _pyproject_version()
