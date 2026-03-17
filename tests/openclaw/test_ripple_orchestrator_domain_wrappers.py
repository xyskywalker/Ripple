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


def test_version_wrapper_forces_json_flag(fake_ripple_cli):
    script = SCRIPTS_DIR / "version.sh"
    assert script.exists()

    result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["version", "--json"]


def test_domain_list_wrapper_forces_json_flag(fake_ripple_cli):
    script = SCRIPTS_DIR / "domain_list.sh"
    assert script.exists()

    result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["domain", "list", "--json"]


def test_domain_info_wrapper_preserves_domain_name(fake_ripple_cli):
    script = SCRIPTS_DIR / "domain_info.sh"
    assert script.exists()

    result = _run_script(
        script,
        "pmf-validation",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["domain", "info", "pmf-validation", "--json"]


def test_domain_schema_wrapper_supports_index_and_specific_domain(fake_ripple_cli):
    script = SCRIPTS_DIR / "domain_schema.sh"
    assert script.exists()

    index_result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert index_result.returncode == 0, index_result.stderr
    index_payload = json.loads(index_result.stdout.strip())
    assert index_payload["argv"] == ["domain", "schema", "--json"]

    named_result = _run_script(
        script,
        "pmf-validation",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert named_result.returncode == 0, named_result.stderr
    named_payload = json.loads(named_result.stdout.strip())
    assert named_payload["argv"] == [
        "domain",
        "schema",
        "pmf-validation",
        "--json",
    ]


def test_domain_example_wrapper_supports_deep_domain_examples(fake_ripple_cli):
    script = SCRIPTS_DIR / "domain_example.sh"
    assert script.exists()

    index_result = _run_script(script, env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)})
    assert index_result.returncode == 0, index_result.stderr
    index_payload = json.loads(index_result.stdout.strip())
    assert index_payload["argv"] == ["domain", "example", "--json"]

    named_result = _run_script(
        script,
        "pmf-validation",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert named_result.returncode == 0, named_result.stderr
    named_payload = json.loads(named_result.stdout.strip())
    assert named_payload["argv"] == [
        "domain",
        "example",
        "pmf-validation",
        "--json",
    ]


def test_domain_dump_wrapper_preserves_filters_and_appends_json_once(fake_ripple_cli):
    script = SCRIPTS_DIR / "domain_dump.sh"
    assert script.exists()

    result = _run_script(
        script,
        "social-media",
        "--section",
        "schema",
        "--json",
        env={"RIPPLE_CLI_BIN": str(fake_ripple_cli)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == [
        "domain",
        "dump",
        "social-media",
        "--section",
        "schema",
        "--json",
    ]
    assert payload["argv"].count("--json") == 1
