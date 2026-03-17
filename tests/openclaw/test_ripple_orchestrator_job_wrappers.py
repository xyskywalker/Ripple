import json
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


def test_validate_wrapper_preserves_request_arguments_and_forces_json(fake_ripple_cli):
    script = SCRIPTS_DIR / "validate.sh"
    assert script.exists()

    result = _run_script(
        script,
        "--input",
        "request.json",
        "--skill",
        "social-media",
        "--platform",
        "xiaohongshu",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "validate",
        "--input",
        "request.json",
        "--skill",
        "social-media",
        "--platform",
        "xiaohongshu",
        "--json",
    ]


def test_job_run_wrapper_defaults_to_async_and_single_json(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_run.sh"
    assert script.exists()

    result = _run_script(
        script,
        "--input",
        "request.json",
        "--skill",
        "pmf-validation",
        "--json",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "job",
        "run",
        "--input",
        "request.json",
        "--skill",
        "pmf-validation",
        "--async",
        "--json",
    ]
    assert payload["argv"].count("--json") == 1
    assert payload["argv"].count("--async") == 1


def test_job_status_wrapper_preserves_job_id(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_status.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["job", "status", "job_123", "--json"]


def test_job_wait_wrapper_defaults_to_30_second_polling(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_wait.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "job",
        "wait",
        "job_123",
        "--poll-interval",
        "30",
        "--json",
    ]


def test_job_wait_wrapper_preserves_explicit_poll_interval(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_wait.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        "--poll-interval",
        "5",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "job",
        "wait",
        "job_123",
        "--poll-interval",
        "5",
        "--json",
    ]


def test_job_list_wrapper_preserves_filters(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_list.sh"
    assert script.exists()

    result = _run_script(
        script,
        "--status",
        "completed",
        "--source",
        "cli",
        "--limit",
        "50",
        "--offset",
        "10",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "job",
        "list",
        "--status",
        "completed",
        "--source",
        "cli",
        "--limit",
        "50",
        "--offset",
        "10",
        "--json",
    ]


def test_job_result_wrapper_supports_summary_mode(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_result.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        "--summary",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["job", "result", "job_123", "--summary", "--json"]


def test_job_log_wrapper_preserves_job_id(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_log.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["job", "log", "job_123", "--json"]


def test_job_cancel_wrapper_preserves_job_id(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_cancel.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["job", "cancel", "job_123", "--json"]


def test_job_delete_wrapper_defaults_to_yes_and_json(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_delete.sh"
    assert script.exists()

    result = _run_script(
        script,
        "job_123",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["job", "delete", "job_123", "--yes", "--json"]
    assert payload["argv"].count("--yes") == 1


def test_job_clean_wrapper_defaults_to_yes_and_json(fake_ripple_cli):
    script = SCRIPTS_DIR / "job_clean.sh"
    assert script.exists()

    result = _run_script(
        script,
        "--before",
        "7d",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "job",
        "clean",
        "--before",
        "7d",
        "--yes",
        "--json",
    ]
    assert payload["argv"].count("--yes") == 1
