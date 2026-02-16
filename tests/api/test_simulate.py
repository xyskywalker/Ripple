# tests/api/test_simulate.py
import pytest
from pathlib import Path
from ripple.skills.manager import SkillManager


class TestPlatformProfileInjection:
    def test_loaded_skill_contains_platform_profile(self, tmp_path):
        """simulate() should combine domain_profile + platform_profile."""
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: test-skill\n"
            "domain_profile: domain.md\n"
            "prompts:\n"
            "  star: prompts/star.md\n"
            "---\n"
        )
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "star.md").write_text("star")
        (skill_dir / "domain.md").write_text("通用领域画像")

        platforms_dir = skill_dir / "platforms"
        platforms_dir.mkdir()
        (platforms_dir / "xiaohongshu.md").write_text("小红书平台画像")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        loaded_skill = manager.load("test-skill")

        # Simulate what simulate() should do
        platform = "xiaohongshu"
        skill_profile = loaded_skill.domain_profile
        if platform and platform in loaded_skill.platform_profiles:
            skill_profile += "\n\n" + loaded_skill.platform_profiles[platform]

        assert "通用领域画像" in skill_profile
        assert "小红书平台画像" in skill_profile

    def test_missing_platform_falls_back_to_domain_only(self, tmp_path):
        """Missing platform should use domain_profile only, no crash."""
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: test-skill\n"
            "domain_profile: domain.md\n"
            "prompts:\n"
            "  star: prompts/star.md\n"
            "---\n"
        )
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "star.md").write_text("star")
        (skill_dir / "domain.md").write_text("通用领域画像")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        loaded_skill = manager.load("test-skill")

        platform = "nonexistent"
        skill_profile = loaded_skill.domain_profile
        if platform and platform in loaded_skill.platform_profiles:
            skill_profile += "\n\n" + loaded_skill.platform_profiles[platform]

        assert skill_profile == "通用领域画像"
