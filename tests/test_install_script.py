from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = ROOT / "install.sh"


def _write_fake_python(
    path: Path,
    log_path: Path,
    version: str = "3.11.9",
    *,
    kind: str = "system",
    venv_log_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_venv_log_path = venv_log_path or log_path
    script = r"""#!/usr/bin/env bash
set -euo pipefail

default_log_file="__LOG_FILE__"
log_file="${FAKE_PYTHON_LOG:-$default_log_file}"
kind="${FAKE_PYTHON_KIND:-__KIND__}"
self_path="__SELF_PATH__"
venv_log_file="__VENV_LOG_FILE__"
printf '%s\\n' "$*" >> "$log_file"

if [ "${1:-}" = "-c" ]; then
  code="${2:-}"
  if [[ "$code" == *"sys.version_info"* ]]; then
    printf '%s\\n' "${FAKE_PYTHON_VERSION:-3.11.9}"
    exit 0
  fi
fi

if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ] && [ "${3:-}" = "--version" ]; then
  printf 'pip 24.0 from /tmp/fake-pip (python %s)\\n' "${FAKE_PYTHON_VERSION%%.*}"
  exit 0
fi

if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ] && [ -n "${3:-}" ]; then
  target_dir="${3}"
  mkdir -p "${target_dir}/bin"
  cat > "${target_dir}/bin/python" <<EOF
#!/usr/bin/env bash
set -euo pipefail
FAKE_PYTHON_KIND=venv FAKE_PYTHON_LOG="${venv_log_file}" exec "${self_path}" "\$@"
EOF
  chmod +x "${target_dir}/bin/python"
  exit 0
fi

if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ] && [ "${3:-}" = "install" ]; then
  has_break_system_packages=0
  for arg in "$@"; do
    if [ "$arg" = "--break-system-packages" ]; then
      has_break_system_packages=1
      break
    fi
  done
  if [ "${kind}" = "system" ] && [ "${FAKE_PYTHON_PEP668_ON_INSTALL:-0}" = "1" ] && { [ "$has_break_system_packages" != "1" ] || [ "${FAKE_PYTHON_PEP668_FAILS_EVEN_WITH_BREAK:-0}" = "1" ]; }; then
    cat >&2 <<'EOF'
error: externally-managed-environment

× This environment is externally managed
EOF
    exit 1
  fi
  exit 0
fi

printf 'unexpected fake python args: %s\\n' "$*" >&2
exit 1
"""
    path.write_text(
        script.replace("__LOG_FILE__", str(log_path))
        .replace("__KIND__", kind)
        .replace("__SELF_PATH__", str(path))
        .replace("__VENV_LOG_FILE__", str(effective_venv_log_path)),
        encoding="utf-8",
    )
    path.chmod(0o755)
    log_path.touch()
    effective_venv_log_path.touch()


def _write_fake_openclaw(path: Path, log_path: Path, config_set_log: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    script = """#!/usr/bin/env bash
set -euo pipefail

log_file="__LOG_FILE__"
config_set_log="__CONFIG_SET_LOG__"
printf '%s\\n' "$*" >> "$log_file"

if [ "${1:-}" = "config" ] && [ "${2:-}" = "get" ] && [ "${3:-}" = "gateway.mode" ]; then
  printf '%s\\n' "${FAKE_OPENCLAW_GATEWAY_MODE:-local}"
  exit 0
fi

if [ "${1:-}" = "gateway" ] && [ "${2:-}" = "status" ]; then
  exit_code="${FAKE_OPENCLAW_GATEWAY_STATUS_EXIT_CODE:-0}"
  if [ "$exit_code" != "0" ]; then
    printf '%s\\n' "${FAKE_OPENCLAW_GATEWAY_STATUS_STDERR:-gateway unavailable}" >&2
    exit "$exit_code"
  fi
  printf '%s\\n' '{"runtime":"running","rpc":"ok"}'
  exit 0
fi

if [ "${1:-}" = "config" ] && [ "${2:-}" = "set" ]; then
  printf '%s=%s\\n' "${3:-}" "${4:-}" >> "$config_set_log"
  exit 0
fi

if [ "${1:-}" = "config" ] && [ "${2:-}" = "validate" ]; then
  printf '%s\\n' '{"ok":true}'
  exit 0
fi

if [ "${1:-}" = "config" ] && [ "${2:-}" = "file" ]; then
  printf '%s\\n' "${FAKE_OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}"
  exit 0
fi

printf 'unexpected fake openclaw args: %s\\n' "$*" >&2
exit 1
"""
    path.write_text(
        script.replace("__LOG_FILE__", str(log_path)).replace(
            "__CONFIG_SET_LOG__", str(config_set_log)
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    log_path.touch()
    config_set_log.touch()


def _run_install_script(tmp_path: Path, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def test_install_script_clones_repo_installs_and_bootstraps_config(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    _write_fake_python(fake_bin / "python3", fake_python_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    repo_dir = home_dir / ".ripple" / "src" / "Ripple"
    config_path = repo_dir / "llm_config.yaml"
    internal_cli_path = home_dir / ".ripple" / "bin" / "ripple-cli"
    public_cli_path = public_bin_dir / "ripple-cli"

    assert result.returncode == 0, result.stdout + result.stderr
    assert repo_dir.is_dir()
    assert config_path.is_file()
    assert internal_cli_path.is_file()
    assert public_cli_path.exists()
    assert config_path.read_text(encoding="utf-8").strip()
    assert "🌊" in result.stdout
    assert "Ripple" in result.stdout
    assert "ripple-cli llm setup" in result.stdout
    assert str(public_cli_path) in result.stdout
    assert str(config_path) in result.stdout
    assert "-m pip install -e ." in fake_python_log.read_text(encoding="utf-8")


def test_install_script_prefers_active_virtualenv_python(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    system_python_log = tmp_path / "system-python.log"
    _write_fake_python(fake_bin / "python3", system_python_log)

    venv_dir = tmp_path / "venv"
    venv_python_log = tmp_path / "venv-python.log"
    _write_fake_python(venv_dir / "bin" / "python", venv_python_log, kind="venv")

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "VIRTUAL_ENV": str(venv_dir),
            "FAKE_PYTHON_VERSION": "3.11.9",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "-m pip install -e ." in venv_python_log.read_text(encoding="utf-8")
    assert "-m pip install -e ." not in system_python_log.read_text(encoding="utf-8")


def test_install_script_automatically_breaks_system_packages_on_pep668(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    system_python_log = tmp_path / "system-python.log"
    private_venv_python_log = tmp_path / "private-venv-python.log"
    _write_fake_python(
        fake_bin / "python3",
        system_python_log,
        venv_log_path=private_venv_python_log,
    )

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "FAKE_PYTHON_PEP668_ON_INSTALL": "1",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    internal_cli_path = home_dir / ".ripple" / "bin" / "ripple-cli"
    public_cli_path = public_bin_dir / "ripple-cli"
    private_venv_python = home_dir / ".ripple" / "venv" / "bin" / "python"
    combined_output = result.stdout + result.stderr
    system_log = system_python_log.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert internal_cli_path.is_file()
    assert public_cli_path.exists()
    assert not private_venv_python.exists()
    assert "虚拟环境" not in combined_output
    assert "-m pip install -e ." in system_log
    assert "--break-system-packages -e ." in system_log
    assert "-m venv" not in system_log
    assert not private_venv_python_log.read_text(encoding="utf-8").strip()


def test_install_script_can_break_system_packages_when_explicitly_enabled(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    system_python_log = tmp_path / "system-python.log"
    _write_fake_python(fake_bin / "python3", system_python_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "FAKE_PYTHON_PEP668_ON_INSTALL": "1",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_BREAK_SYSTEM_PACKAGES": "1",
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    system_log = system_python_log.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--break-system-packages -e ." in system_log
    assert "-m venv" not in system_log
    assert not (home_dir / ".ripple" / "venv" / "bin" / "python").exists()


def test_install_script_falls_back_to_private_venv_if_break_system_packages_still_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    system_python_log = tmp_path / "system-python.log"
    private_venv_python_log = tmp_path / "private-venv-python.log"
    _write_fake_python(
        fake_bin / "python3",
        system_python_log,
        venv_log_path=private_venv_python_log,
    )

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "FAKE_PYTHON_PEP668_ON_INSTALL": "1",
            "FAKE_PYTHON_PEP668_FAILS_EVEN_WITH_BREAK": "1",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    private_venv_python = home_dir / ".ripple" / "venv" / "bin" / "python"
    combined_output = result.stdout + result.stderr
    system_log = system_python_log.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert private_venv_python.is_file()
    assert "虚拟环境" in combined_output
    assert "--break-system-packages -e ." in system_log
    assert "-m venv" in system_log
    assert "-m pip install -e ." in private_venv_python_log.read_text(encoding="utf-8")


def test_install_script_writes_shell_path_snippet_for_user_bin_when_needed(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    _write_fake_python(fake_bin / "python3", fake_python_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = home_dir / ".local" / "bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    profile_path = home_dir / ".profile"
    bashrc_path = home_dir / ".bashrc"
    zshrc_path = home_dir / ".zshrc"

    assert result.returncode == 0, result.stdout + result.stderr
    assert (public_bin_dir / "ripple-cli").exists()
    assert profile_path.is_file()
    assert bashrc_path.is_file()
    assert zshrc_path.is_file()
    assert str(public_bin_dir) in profile_path.read_text(encoding="utf-8")
    assert str(public_bin_dir) in bashrc_path.read_text(encoding="utf-8")
    assert str(public_bin_dir) in zshrc_path.read_text(encoding="utf-8")


def test_install_script_rejects_python_below_311(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    _write_fake_python(fake_bin / "python3", fake_python_log, version="3.10.14")

    home_dir = tmp_path / "home"
    home_dir.mkdir()

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.10.14",
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    assert result.returncode != 0
    assert "Python 3.11+" in (result.stdout + result.stderr)
    assert "pip" not in fake_python_log.read_text(encoding="utf-8")


def test_install_script_can_run_again_after_user_edits_config(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    _write_fake_python(fake_bin / "python3", fake_python_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    public_bin_dir = tmp_path / "public-bin"

    env = {
        "HOME": str(home_dir),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_PYTHON_VERSION": "3.11.9",
        "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
        "RIPPLE_REPO_URL": str(ROOT),
    }

    first_run = _run_install_script(tmp_path, env)
    assert first_run.returncode == 0, first_run.stdout + first_run.stderr

    repo_dir = home_dir / ".ripple" / "src" / "Ripple"
    config_path = repo_dir / "llm_config.yaml"
    user_tail = "\n# user customized config\n"
    config_path.write_text(config_path.read_text(encoding="utf-8") + user_tail, encoding="utf-8")

    generated_file = repo_dir / "ripple.egg-info" / "PKG-INFO"
    generated_file.parent.mkdir(parents=True, exist_ok=True)
    generated_file.write_text("generated by editable install\n", encoding="utf-8")

    second_run = _run_install_script(tmp_path, env)

    assert second_run.returncode == 0, second_run.stdout + second_run.stderr
    assert config_path.read_text(encoding="utf-8").endswith(user_tail)


def test_install_script_installs_openclaw_skill_even_when_gateway_mode_is_remote(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    openclaw_log = tmp_path / "openclaw.log"
    config_set_log = tmp_path / "openclaw-config-set.log"
    _write_fake_python(fake_bin / "python3", fake_python_log)
    _write_fake_openclaw(fake_bin / "openclaw", openclaw_log, config_set_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    skills_dir = home_dir / ".openclaw" / "skills"
    target_dir = skills_dir / "ripple-orchestrator"
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "FAKE_OPENCLAW_GATEWAY_MODE": "remote",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target_dir.is_dir()
    assert "OpenClaw" in result.stdout
    assert "ripple-orchestrator" in result.stdout
    openclaw_calls = openclaw_log.read_text(encoding="utf-8")
    assert "config validate --json" in openclaw_calls
    config_updates = config_set_log.read_text(encoding="utf-8")
    assert 'skills.entries["ripple-orchestrator"].enabled=true' in config_updates


def test_install_script_installs_openclaw_skill_even_when_gateway_rpc_probe_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    openclaw_log = tmp_path / "openclaw.log"
    config_set_log = tmp_path / "openclaw-config-set.log"
    _write_fake_python(fake_bin / "python3", fake_python_log)
    _write_fake_openclaw(fake_bin / "openclaw", openclaw_log, config_set_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    skills_dir = home_dir / ".openclaw" / "skills"
    target_dir = skills_dir / "ripple-orchestrator"
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "FAKE_OPENCLAW_GATEWAY_MODE": "local",
            "FAKE_OPENCLAW_GATEWAY_STATUS_EXIT_CODE": "1",
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target_dir.is_dir()
    assert "OpenClaw" in result.stdout
    assert "ripple-orchestrator" in result.stdout
    openclaw_calls = openclaw_log.read_text(encoding="utf-8")
    assert "config validate --json" in openclaw_calls
    config_updates = config_set_log.read_text(encoding="utf-8")
    assert 'skills.entries["ripple-orchestrator"].enabled=true' in config_updates


def test_install_script_installs_openclaw_skill_when_local_gateway_is_running(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_python_log = tmp_path / "python.log"
    openclaw_log = tmp_path / "openclaw.log"
    config_set_log = tmp_path / "openclaw-config-set.log"
    _write_fake_python(fake_bin / "python3", fake_python_log)
    _write_fake_openclaw(fake_bin / "openclaw", openclaw_log, config_set_log)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    skills_dir = home_dir / ".openclaw" / "skills"
    target_dir = skills_dir / "ripple-orchestrator"
    public_bin_dir = tmp_path / "public-bin"

    result = _run_install_script(
        tmp_path,
        {
            "HOME": str(home_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_PYTHON_VERSION": "3.11.9",
            "FAKE_OPENCLAW_GATEWAY_MODE": "local",
            "FAKE_OPENCLAW_CONFIG_PATH": str(home_dir / ".openclaw" / "openclaw.json"),
            "OPENCLAW_SKILLS_DIR": str(skills_dir),
            "RIPPLE_PUBLIC_BIN_DIR": str(public_bin_dir),
            "RIPPLE_OPENCLAW_INSTALLER_PATH": str(
                ROOT / "integrations" / "openclaw" / "install_local_skill.sh"
            ),
            "RIPPLE_REPO_URL": str(ROOT),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target_dir.is_dir()
    assert (target_dir / "SKILL.md").is_file()
    assert "ripple-orchestrator" in result.stdout
    assert "OpenClaw" in result.stdout
    assert "session" in result.stdout

    openclaw_calls = openclaw_log.read_text(encoding="utf-8")
    assert "config validate --json" in openclaw_calls
    config_updates = config_set_log.read_text(encoding="utf-8")
    assert 'skills.entries["ripple-orchestrator"].enabled=true' in config_updates
