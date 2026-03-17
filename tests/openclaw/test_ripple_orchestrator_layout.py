import json
import os
import stat
import subprocess
from pathlib import Path


def _run_installer(
    script_path: Path, skills_dir: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OPENCLAW_SKILLS_DIR"] = str(skills_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _assert_skill_tree_exists(target_dir: Path) -> None:
    assert target_dir.exists() and target_dir.is_dir()
    assert (target_dir / "SKILL.md").exists()
    assert (target_dir / "references").exists()
    assert (target_dir / "assets").exists()


def test_ripple_orchestrator_layout_contract():
    repo_root = Path(__file__).resolve().parents[2]
    skill_dir = repo_root / "integrations" / "openclaw" / "ripple-orchestrator"
    skill_md = skill_dir / "SKILL.md"
    references_dir = skill_dir / "references"
    assets_dir = skill_dir / "assets"
    install_script = repo_root / "integrations" / "openclaw" / "install_local_skill.sh"
    skill_rel = skill_dir.relative_to(repo_root)

    assert skill_dir.exists() and skill_dir.is_dir()
    assert skill_rel == Path("integrations/openclaw/ripple-orchestrator")
    assert skill_rel.parts[0] != "skills"
    assert skill_md.exists() and skill_md.is_file()
    assert references_dir.exists() and references_dir.is_dir()
    assert assets_dir.exists() and assets_dir.is_dir()
    assert install_script.exists() and install_script.is_file()

    content = install_script.read_text(encoding="utf-8")
    assert ".openclaw/skills" in content
    assert 'TARGET_DIR="${TARGET_ROOT}/ripple"' in content


def test_install_script_installs_to_override_and_returns_json(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    install_script = repo_root / "integrations" / "openclaw" / "install_local_skill.sh"
    skills_root = tmp_path / 'skills "qa"\b\f'
    expected_target = skills_root / "ripple"

    result = _run_installer(install_script, skills_root)
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["target"] == str(expected_target)
    _assert_skill_tree_exists(expected_target)


def test_install_script_does_not_delete_existing_on_missing_source(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    source_script = repo_root / "integrations" / "openclaw" / "install_local_skill.sh"
    broken_script_dir = tmp_path / "broken-installer"
    broken_script_dir.mkdir(parents=True)
    broken_script = broken_script_dir / "install_local_skill.sh"
    broken_script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    broken_script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    skills_root = tmp_path / "skills-root"
    target_dir = skills_root / "ripple"
    target_dir.mkdir(parents=True)
    sentinel = target_dir / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")

    result = _run_installer(broken_script, skills_root)
    assert result.returncode != 0
    assert sentinel.exists()


def test_install_script_replaces_existing_installed_tree(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    install_script = repo_root / "integrations" / "openclaw" / "install_local_skill.sh"
    skills_root = tmp_path / "skills-root"
    target_dir = skills_root / "ripple"
    target_dir.mkdir(parents=True)
    stale = target_dir / "stale.txt"
    stale.write_text("old install", encoding="utf-8")

    result = _run_installer(install_script, skills_root)
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["target"] == str(target_dir)
    _assert_skill_tree_exists(target_dir)
    assert not stale.exists()


def test_install_script_rolls_back_when_replace_move_fails(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    install_script = repo_root / "integrations" / "openclaw" / "install_local_skill.sh"
    skills_root = tmp_path / "skills-root"
    target_dir = skills_root / "ripple"
    target_dir.mkdir(parents=True)
    sentinel = target_dir / "keep.txt"
    sentinel.write_text("old tree", encoding="utf-8")

    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    count_file = tmp_path / "mv-count.txt"
    mv_wrapper = wrapper_dir / "mv"
    mv_wrapper.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'count_file="${MV_WRAPPER_COUNT_FILE:?}"',
                'count="0"',
                'if [[ -f "${count_file}" ]]; then',
                '  count="$(cat "${count_file}")"',
                "fi",
                'count="$((count + 1))"',
                'printf "%s" "${count}" > "${count_file}"',
                'if [[ "${count}" -eq 2 ]]; then',
                "  exit 1",
                "fi",
                'exec /bin/mv "$@"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    mv_wrapper.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    extra_env = {
        "PATH": f"{wrapper_dir}:{os.environ['PATH']}",
        "MV_WRAPPER_COUNT_FILE": str(count_file),
    }
    result = _run_installer(install_script, skills_root, extra_env=extra_env)

    assert result.returncode != 0
    assert target_dir.exists() and target_dir.is_dir()
    assert sentinel.exists()
    assert not (target_dir / "SKILL.md").exists()
