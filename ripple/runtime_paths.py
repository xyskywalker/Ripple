from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping


_LOCAL_LLM_CONFIG_RELATIVE_PATHS = (
    Path("llm_config.yaml"),
    Path("llm_config.yml"),
    Path("config") / "llm_config.yaml",
    Path("config") / "llm_config.yml",
)


def _env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _path_from_text(value: str | None) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def _first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _workspace_markers_present(current_dir: Path) -> bool:
    marker_hits = 0
    for present in (
        (current_dir / "pyproject.toml").is_file(),
        (current_dir / "ripple").is_dir(),
        (current_dir / "skills").is_dir(),
        (current_dir / "install.sh").is_file(),
    ):
        marker_hits += 1 if present else 0
    return marker_hits >= 2


def package_repo_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def ripple_home_dir(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    env_map = _env(env)
    configured = _path_from_text(env_map.get("RIPPLE_HOME_DIR"))
    if configured is not None:
        return configured
    return (home_dir or Path.home()) / ".ripple"


def installed_repo_dir(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    env_map = _env(env)
    configured = _path_from_text(env_map.get("RIPPLE_REPO_DIR"))
    if configured is not None:
        return configured
    return ripple_home_dir(env_map, home_dir) / "src" / "Ripple"


def installed_data_dir(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    env_map = _env(env)
    configured = _path_from_text(env_map.get("RIPPLE_DATA_DIR"))
    if configured is not None:
        return configured
    return ripple_home_dir(env_map, home_dir) / "data"


def installed_llm_config_path(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    return installed_repo_dir(env, home_dir) / "llm_config.yaml"


def installed_db_path(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    return installed_data_dir(env, home_dir) / "ripple.db"


def installed_output_dir(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    return installed_data_dir(env, home_dir) / "ripple_outputs"


def installed_skill_dir(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    return installed_repo_dir(env, home_dir) / "skills"


def current_workspace_db_path(current_dir: Path | None = None) -> Path:
    return (current_dir or Path.cwd()) / "data" / "ripple.db"


def current_workspace_output_dir(current_dir: Path | None = None) -> Path:
    return (current_dir or Path.cwd()) / "ripple_outputs"


def current_llm_config_candidates(current_dir: Path | None = None) -> list[Path]:
    base_dir = current_dir or Path.cwd()
    return [base_dir / relative for relative in _LOCAL_LLM_CONFIG_RELATIVE_PATHS]


def installed_llm_config_candidates(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> list[Path]:
    repo_dir = installed_repo_dir(env, home_dir)
    return [repo_dir / relative for relative in _LOCAL_LLM_CONFIG_RELATIVE_PATHS]


def default_skill_search_paths(
    current_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> list[Path]:
    cwd = current_dir or Path.cwd()
    home = home_dir or Path.home()
    return [
        cwd / ".agents" / "skills",
        cwd / "skills",
        home / ".config" / "ripple" / "skills",
        installed_skill_dir(env, home),
    ]


def prefer_workspace_defaults(
    current_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> bool:
    cwd = (current_dir or Path.cwd()).resolve()
    repo_dir = package_repo_dir().resolve()
    install_dir = installed_repo_dir(env, home_dir).resolve()
    if repo_dir != install_dir and _is_relative_to(cwd, repo_dir):
        return True
    return _workspace_markers_present(cwd)


def _installed_layout_exists(
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> bool:
    home = ripple_home_dir(env, home_dir)
    return (
        installed_repo_dir(env, home_dir).exists()
        or installed_data_dir(env, home_dir).exists()
        or home.exists()
    )


def resolve_llm_config_path(
    config_path: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    current_dir: Path | None = None,
    home_dir: Path | None = None,
) -> str:
    explicit = _path_from_text(config_path)
    if explicit is not None:
        return str(explicit)

    env_map = _env(env)
    env_path = _path_from_text(env_map.get("RIPPLE_LLM_CONFIG_PATH"))
    if env_path is not None:
        return str(env_path)

    cwd = current_dir or Path.cwd()
    current_existing = _first_existing(current_llm_config_candidates(cwd))
    if current_existing is not None:
        return str(current_existing)

    if prefer_workspace_defaults(cwd, env_map, home_dir):
        return str(cwd / _LOCAL_LLM_CONFIG_RELATIVE_PATHS[0])

    installed_existing = _first_existing(installed_llm_config_candidates(env_map, home_dir))
    if installed_existing is not None:
        return str(installed_existing)

    if _installed_layout_exists(env_map, home_dir):
        return str(installed_llm_config_path(env_map, home_dir))

    return str(cwd / _LOCAL_LLM_CONFIG_RELATIVE_PATHS[0])


def resolve_db_path(
    db_path: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    current_dir: Path | None = None,
    home_dir: Path | None = None,
) -> str:
    explicit = _path_from_text(db_path)
    if explicit is not None:
        return str(explicit)

    env_map = _env(env)
    env_path = _path_from_text(env_map.get("RIPPLE_DB_PATH"))
    if env_path is not None:
        return str(env_path)

    cwd = current_dir or Path.cwd()
    current_path = current_workspace_db_path(cwd)
    if current_path.exists():
        return str(current_path)

    if prefer_workspace_defaults(cwd, env_map, home_dir):
        return str(current_path)

    installed_path = installed_db_path(env_map, home_dir)
    if installed_path.exists() or _installed_layout_exists(env_map, home_dir):
        return str(installed_path)

    return str(current_path)


def resolve_output_dir(
    *,
    env: Mapping[str, str] | None = None,
    current_dir: Path | None = None,
    home_dir: Path | None = None,
) -> str:
    env_map = _env(env)
    env_path = _path_from_text(env_map.get("RIPPLE_OUTPUT_DIR"))
    if env_path is not None:
        return str(env_path)

    cwd = current_dir or Path.cwd()
    current_path = current_workspace_output_dir(cwd)
    if current_path.exists():
        return str(current_path)

    if prefer_workspace_defaults(cwd, env_map, home_dir):
        return str(current_path)

    installed_path = installed_output_dir(env_map, home_dir)
    if installed_path.exists() or _installed_layout_exists(env_map, home_dir):
        return str(installed_path)

    return str(current_path)
