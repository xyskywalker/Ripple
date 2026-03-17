import json
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = (
    REPO_ROOT / "integrations" / "openclaw" / "ripple-orchestrator" / "scripts"
)


def _run_script(
    script_path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    proc_env = None
    if env is not None:
        proc_env = dict(env)
    return subprocess.run(
        ["bash", str(script_path), *args],
        capture_output=True,
        text=True,
        check=False,
        env=proc_env,
    )


def test_doctor_wrapper_forces_json_flag(fake_ripple_cli):
    script = SCRIPTS_DIR / "doctor.sh"
    assert script.exists()

    result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["doctor", "--json"]


def test_llm_show_wrapper_forces_json_flag(fake_ripple_cli):
    script = SCRIPTS_DIR / "llm_show.sh"
    assert script.exists()

    result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["llm", "show", "--json"]


def test_llm_test_wrapper_forces_json_flag(fake_ripple_cli):
    script = SCRIPTS_DIR / "llm_test.sh"
    assert script.exists()

    result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["llm", "test", "--json"]


def test_llm_set_wrapper_forwards_args_and_ends_with_single_json(fake_ripple_cli):
    script = SCRIPTS_DIR / "llm_set.sh"
    assert script.exists()

    result = _run_script(
        script,
        "provider",
        "--json",
        "openai",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["llm", "set", "provider", "openai", "--json"]
    assert payload["argv"].count("--json") == 1


def test_install_init_uses_override_and_prints_composed_json(fake_ripple_cli, tmp_path):
    script = SCRIPTS_DIR / "install_init.sh"
    assert script.exists()

    fake_install = tmp_path / "install.sh"
    fake_install.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "exit 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_install.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    result = _run_script(
        script,
        env={
            "RIPPLE_CLI_BIN": str(fake_ripple_cli),
            "RIPPLE_INSTALL_SCRIPT": str(fake_install),
        },
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert "doctor" in payload
    assert "llm_show" in payload
    assert payload["doctor"]["argv"] == ["doctor", "--json"]
    assert payload["llm_show"]["argv"] == ["llm", "show", "--json"]


def test_install_init_falls_back_when_override_path_is_missing(fake_ripple_cli, tmp_path):
    script = SCRIPTS_DIR / "install_init.sh"
    assert script.exists()

    fake_home = tmp_path / "fake-home"
    fallback_install = fake_home / ".ripple" / "src" / "Ripple" / "install.sh"
    fallback_install.parent.mkdir(parents=True, exist_ok=True)
    fallback_install.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "exit 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fallback_install.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    result = _run_script(
        script,
        env={
            "HOME": str(fake_home),
            "RIPPLE_CLI_BIN": str(fake_ripple_cli),
            "RIPPLE_INSTALL_SCRIPT": str(tmp_path / "missing-install.sh"),
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["doctor"]["argv"] == ["doctor", "--json"]
