"""Tests for SkillManager extensions: rubrics and channel_profiles loading."""

import textwrap
from pathlib import Path

import pytest

from ripple.skills.manager import SkillManager, LoadedSkill


@pytest.fixture
def pmf_skill_dir(tmp_path):
    """Create a minimal pmf-validation skill directory."""
    skill_dir = tmp_path / "skills" / "pmf-validation"
    skill_dir.mkdir(parents=True)

    # SKILL.md
    (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: pmf-validation
        version: "0.1.0"
        description: PMF validation engine
        prompts:
          omniscient: prompts/omniscient.md
          tribunal: prompts/tribunal.md
        domain_profile: domain-profile.md
        ---
    """))

    # domain-profile.md
    (skill_dir / "domain-profile.md").write_text("PMF domain profile content")

    # prompts/
    prompts_dir = skill_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "omniscient.md").write_text("omniscient prompt")
    (prompts_dir / "tribunal.md").write_text("tribunal prompt")

    # rubrics/
    rubrics_dir = skill_dir / "rubrics"
    rubrics_dir.mkdir()
    (rubrics_dir / "scorecard-dimensions.md").write_text("dimension rubric content")
    (rubrics_dir / "pmf-grade-rubric.md").write_text("grade rubric content")

    # channels/
    channels_dir = skill_dir / "channels"
    channels_dir.mkdir()
    (channels_dir / "ecommerce-tmall.md").write_text("tmall channel profile")
    (channels_dir / "offline-distribution.md").write_text("distribution channel profile")

    return skill_dir


class TestSkillManagerRubrics:
    def test_load_rubrics(self, pmf_skill_dir):
        manager = SkillManager(search_paths=[pmf_skill_dir.parent])
        skill = manager.load("pmf-validation")
        assert hasattr(skill, "rubrics")
        assert "scorecard-dimensions" in skill.rubrics
        assert "pmf-grade-rubric" in skill.rubrics
        assert skill.rubrics["scorecard-dimensions"] == "dimension rubric content"

    def test_load_channel_profiles(self, pmf_skill_dir):
        manager = SkillManager(search_paths=[pmf_skill_dir.parent])
        skill = manager.load("pmf-validation")
        assert hasattr(skill, "channel_profiles")
        assert "ecommerce-tmall" in skill.channel_profiles
        assert "offline-distribution" in skill.channel_profiles
        assert skill.channel_profiles["ecommerce-tmall"] == "tmall channel profile"

    def test_empty_rubrics_and_channels(self, tmp_path):
        """Skill without rubrics/ or channels/ dirs should load with empty dicts."""
        skill_dir = tmp_path / "skills" / "basic-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: basic-skill
            version: "0.1.0"
            description: Basic skill
            prompts: {}
            ---
        """))
        manager = SkillManager(search_paths=[skill_dir.parent])
        skill = manager.load("basic-skill")
        assert skill.rubrics == {}
        assert skill.channel_profiles == {}


class TestSkillManagerVerticals:
    def test_load_vertical_profiles(self, pmf_skill_dir):
        """Vertical profiles should be loaded from verticals/ directory."""
        verticals_dir = pmf_skill_dir / "verticals"
        verticals_dir.mkdir()
        (verticals_dir / "fmcg.md").write_text("fmcg vertical content")
        (verticals_dir / "saas.md").write_text("saas vertical content")

        manager = SkillManager(search_paths=[pmf_skill_dir.parent])
        skill = manager.load("pmf-validation")
        assert hasattr(skill, "vertical_profiles")
        assert "fmcg" in skill.vertical_profiles
        assert "saas" in skill.vertical_profiles
        assert skill.vertical_profiles["fmcg"] == "fmcg vertical content"

    def test_empty_verticals(self, tmp_path):
        """Skill without verticals/ dir should load with empty dict."""
        skill_dir = tmp_path / "skills" / "no-verticals"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: no-verticals
            version: "0.1.0"
            description: Skill without verticals
            prompts: {}
            ---
        """))
        manager = SkillManager(search_paths=[skill_dir.parent])
        skill = manager.load("no-verticals")
        assert skill.vertical_profiles == {}

    def test_hidden_vertical_files_ignored(self, pmf_skill_dir):
        """Hidden files in verticals/ should be skipped."""
        verticals_dir = pmf_skill_dir / "verticals"
        verticals_dir.mkdir()
        (verticals_dir / ".draft.md").write_text("hidden draft")
        (verticals_dir / "fmcg.md").write_text("fmcg content")

        manager = SkillManager(search_paths=[pmf_skill_dir.parent])
        skill = manager.load("pmf-validation")
        assert ".draft" not in skill.vertical_profiles
        assert "fmcg" in skill.vertical_profiles
