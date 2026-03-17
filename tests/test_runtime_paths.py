from __future__ import annotations

from pathlib import Path

from ripple.api.simulate import _resolve_output_path
from ripple.llm.config import LLMConfigLoader
from ripple.runtime_paths import (
    resolve_db_path,
    resolve_llm_config_path,
    resolve_output_dir,
)


def _write_config(path: Path, model_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "_default:\n"
        "  model_platform: openai\n"
        f"  model_name: {model_name}\n"
        "  api_key: sk-demo\n"
        "  url: https://example.test/v1\n"
        "  api_mode: responses\n",
        encoding="utf-8",
    )


def _make_workspace(path: Path) -> None:
    (path / "pyproject.toml").write_text("[project]\nname='ripple-local'\n", encoding="utf-8")
    (path / "ripple").mkdir(parents=True, exist_ok=True)
    (path / "skills").mkdir(parents=True, exist_ok=True)
    (path / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")


def test_resolve_llm_config_path_prefers_workspace_default_over_installed(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_workspace(workspace)

    ripple_home = tmp_path / "home" / ".ripple"
    _write_config(ripple_home / "src" / "Ripple" / "llm_config.yaml", "installed-model")

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))

    resolved = resolve_llm_config_path()

    assert resolved == str(workspace / "llm_config.yaml")


def test_resolve_llm_config_path_falls_back_to_installed_outside_workspace(monkeypatch, tmp_path: Path) -> None:
    current_dir = tmp_path / "random-dir"
    current_dir.mkdir()

    ripple_home = tmp_path / "home" / ".ripple"
    installed_config = ripple_home / "src" / "Ripple" / "llm_config.yaml"
    _write_config(installed_config, "installed-model")

    monkeypatch.chdir(current_dir)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))

    resolved = resolve_llm_config_path()

    assert resolved == str(installed_config)


def test_resolve_db_and_output_paths_prefer_workspace_defaults(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_workspace(workspace)

    ripple_home = tmp_path / "home" / ".ripple"
    (ripple_home / "data").mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))

    assert resolve_db_path() == str(workspace / "data" / "ripple.db")
    assert resolve_output_dir() == str(workspace / "ripple_outputs")


def test_resolve_db_and_output_paths_fall_back_to_installed_layout(monkeypatch, tmp_path: Path) -> None:
    current_dir = tmp_path / "random-dir"
    current_dir.mkdir()

    ripple_home = tmp_path / "home" / ".ripple"
    installed_db = ripple_home / "data" / "ripple.db"
    installed_output_dir = ripple_home / "data" / "ripple_outputs"
    installed_db.parent.mkdir(parents=True, exist_ok=True)
    installed_db.write_text("", encoding="utf-8")
    installed_output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(current_dir)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))

    assert resolve_db_path() == str(installed_db)
    assert resolve_output_dir() == str(installed_output_dir)


def test_llm_config_loader_auto_discovers_installed_config_outside_workspace(monkeypatch, tmp_path: Path) -> None:
    current_dir = tmp_path / "random-dir"
    current_dir.mkdir()

    ripple_home = tmp_path / "home" / ".ripple"
    installed_config = ripple_home / "src" / "Ripple" / "llm_config.yaml"
    _write_config(installed_config, "installed-model")

    monkeypatch.chdir(current_dir)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))
    monkeypatch.delenv("RIPPLE_LLM_CONFIG_PATH", raising=False)

    loader = LLMConfigLoader()
    resolved = loader.resolve("_default")

    assert resolved.model_name == "installed-model"


def test_simulate_output_path_uses_workspace_output_dir_in_local_mode(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_workspace(workspace)

    ripple_home = tmp_path / "home" / ".ripple"
    (ripple_home / "data" / "ripple_outputs").mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))
    monkeypatch.delenv("RIPPLE_OUTPUT_DIR", raising=False)

    output_path = _resolve_output_path(None, "run_demo")

    assert output_path.parent == workspace / "ripple_outputs"


def test_simulate_output_path_falls_back_to_installed_output_dir(monkeypatch, tmp_path: Path) -> None:
    current_dir = tmp_path / "random-dir"
    current_dir.mkdir()

    ripple_home = tmp_path / "home" / ".ripple"
    installed_output_dir = ripple_home / "data" / "ripple_outputs"
    installed_output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(current_dir)
    monkeypatch.setenv("RIPPLE_HOME_DIR", str(ripple_home))
    monkeypatch.delenv("RIPPLE_OUTPUT_DIR", raising=False)

    output_path = _resolve_output_path(None, "run_demo")

    assert output_path.parent == installed_output_dir
