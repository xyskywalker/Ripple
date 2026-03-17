import json
import stat
import subprocess
from pathlib import Path


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fake_ripple_cli_fixture_creates_executable_stub(fake_ripple_cli):
    assert fake_ripple_cli.exists()
    assert fake_ripple_cli.is_file()
    assert fake_ripple_cli.stat().st_mode & 0o111

    result = subprocess.run(
        [str(fake_ripple_cli), "alpha", "beta"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["alpha", "beta"]
    assert payload["cwd"]


def test_run_ripple_cli_uses_override_and_appends_json_once(fake_ripple_cli):
    repo_root = Path(__file__).resolve().parents[2]
    common_sh = (
        repo_root
        / "integrations"
        / "openclaw"
        / "ripple-orchestrator"
        / "scripts"
        / "_common.sh"
    )
    assert common_sh.exists()

    probe = f"""
set -euo pipefail
source "{common_sh}"
RIPPLE_CLI_BIN="{fake_ripple_cli}" run_ripple_cli version
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["version", "--json"]


def test_run_ripple_cli_does_not_duplicate_existing_json(fake_ripple_cli):
    repo_root = Path(__file__).resolve().parents[2]
    common_sh = (
        repo_root
        / "integrations"
        / "openclaw"
        / "ripple-orchestrator"
        / "scripts"
        / "_common.sh"
    )
    assert common_sh.exists()

    probe = f"""
set -euo pipefail
source "{common_sh}"
RIPPLE_CLI_BIN="{fake_ripple_cli}" run_ripple_cli version --json
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["version", "--json"]


def test_run_ripple_cli_failure_can_be_rescued_by_caller(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    common_sh = (
        repo_root
        / "integrations"
        / "openclaw"
        / "ripple-orchestrator"
        / "scripts"
        / "_common.sh"
    )
    assert common_sh.exists()

    failing_cli = tmp_path / "ripple-cli-fail"
    failing_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "exit 23",
                "",
            ]
        ),
        encoding="utf-8",
    )
    failing_cli.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    probe = f"""
set -euo pipefail
source "{common_sh}"
RIPPLE_CLI_BIN="{failing_cli}" run_ripple_cli version || echo rescued
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rescued"
