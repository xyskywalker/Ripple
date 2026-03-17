import json
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "integrations" / "openclaw" / "ripple-orchestrator"
SCRIPTS_DIR = SKILL_DIR / "scripts"
REFERENCES_DIR = SKILL_DIR / "references"
ASSETS_DIR = SKILL_DIR / "assets"

EXPECTED_SCRIPT_NAMES = {
    "install_init.sh",
    "version.sh",
    "doctor.sh",
    "llm_show.sh",
    "llm_set.sh",
    "llm_test.sh",
    "domain_list.sh",
    "domain_info.sh",
    "domain_schema.sh",
    "domain_example.sh",
    "domain_dump.sh",
    "validate.sh",
    "job_run.sh",
    "job_status.sh",
    "job_wait.sh",
    "job_list.sh",
    "job_result.sh",
    "job_log.sh",
    "job_cancel.sh",
    "job_delete.sh",
    "job_clean.sh",
}

EXPECTED_REFERENCE_NAMES = {
    "command-map.md",
    "workflow-playbooks.md",
    "input-authoring-guide.md",
    "history-and-control.md",
    "safety-and-config.md",
}

EXPECTED_TEMPLATE_NAMES = {
    "generic-request.json",
    "social-media-request.json",
    "pmf-validation-request.json",
}


def test_skill_docs_expose_complete_openclaw_surface_area():
    skill_md = SKILL_DIR / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")

    assert content.startswith("---\nname: ripple\n")
    assert "not a Ripple domain skill" in content
    assert "--json" in content
    assert "30 seconds" in content
    assert "llm setup" in content
    assert "domain schema" in content
    assert "domain example" in content
    assert "domain dump" in content
    assert "job cancel" in content
    assert "job delete" in content
    assert "job clean" in content


def test_all_expected_wrapper_scripts_exist_and_are_executable():
    for script_name in EXPECTED_SCRIPT_NAMES:
        script_path = SCRIPTS_DIR / script_name
        assert script_path.exists(), script_name
        assert os.access(script_path, os.X_OK), script_name


def test_reference_guides_exist_and_cover_deep_commands():
    for reference_name in EXPECTED_REFERENCE_NAMES:
        reference_path = REFERENCES_DIR / reference_name
        assert reference_path.exists(), reference_name

    command_map = (REFERENCES_DIR / "command-map.md").read_text(encoding="utf-8")
    assert "ripple-cli domain schema pmf-validation --json" in command_map
    assert "ripple-cli domain example social-media --json" in command_map
    assert "ripple-cli domain dump <domain> --section <section> --json" in command_map
    assert "ripple-cli job result <job_id> --summary --json" in command_map
    assert "ripple-cli job delete <job_id> --yes --json" in command_map
    assert "ripple-cli job clean --before 7d --yes --json" in command_map
    assert "ripple-cli llm setup" in command_map


def test_request_templates_exist_and_parse_as_json():
    templates_dir = ASSETS_DIR / "request-templates"
    assert templates_dir.exists()

    for template_name in EXPECTED_TEMPLATE_NAMES:
        template_path = templates_dir / template_name
        assert template_path.exists(), template_name
        payload = json.loads(template_path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert "event" in payload

    generic_payload = json.loads(
        (templates_dir / "generic-request.json").read_text(encoding="utf-8")
    )
    social_payload = json.loads(
        (templates_dir / "social-media-request.json").read_text(encoding="utf-8")
    )
    pmf_payload = json.loads(
        (templates_dir / "pmf-validation-request.json").read_text(encoding="utf-8")
    )

    assert generic_payload["skill"] == "<domain>"
    assert social_payload["skill"] == "social-media"
    assert pmf_payload["skill"] == "pmf-validation"
