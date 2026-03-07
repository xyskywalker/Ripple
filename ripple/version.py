from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import tomllib


@lru_cache(maxsize=1)
def _find_pyproject() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    raise RuntimeError("Could not find pyproject.toml for Ripple version lookup")


@lru_cache(maxsize=1)
def get_version() -> str:
    with _find_pyproject().open("rb") as fh:
        data = tomllib.load(fh)

    try:
        return str(data["project"]["version"])
    except KeyError as exc:
        raise RuntimeError("[project].version missing from pyproject.toml") from exc


VERSION = get_version()

__all__ = ["VERSION", "get_version"]
