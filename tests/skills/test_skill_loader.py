# tests/skills/test_skill_loader.py
# Skill 加载测试

"""Skill 加载测试。"""
import pytest
from pathlib import Path
from ripple.skills.manager import SkillManager, LoadedSkill
from ripple.skills.validator import SkillValidationError

# Skill 目录路径
SKILL_DIR = Path(__file__).parent.parent.parent / "skills" / "social-media"


class TestSkillLoading:
    def test_default_search_paths_include_project_skills(self):
        """默认搜索路径应包含当前项目的 skills 目录。"""
        manager = SkillManager()
        paths = manager._build_default_paths()
        assert Path.cwd() / "skills" in paths

    def test_load_social_media_skill(self):
        """加载 social-media Skill。"""
        if not SKILL_DIR.exists():
            pytest.skip("Skill 目录不存在")
        manager = SkillManager()
        skill = manager.load("social-media", skill_path=SKILL_DIR)
        assert skill.name == "social-media"
        assert skill.version == "0.1.0"

    def test_prompts_loaded(self):
        """Prompt 模板加载（omniscient, star, sea）。"""
        if not SKILL_DIR.exists():
            pytest.skip("Skill 目录不存在")
        manager = SkillManager()
        skill = manager.load("social-media", skill_path=SKILL_DIR)
        assert "omniscient" in skill.prompts
        assert "star" in skill.prompts
        assert "sea" in skill.prompts
        for role, content in skill.prompts.items():
            assert len(content) > 0, f"Prompt {role} 为空"

    def test_domain_profile_loaded(self):
        """领域画像加载。"""
        if not SKILL_DIR.exists():
            pytest.skip("Skill 目录不存在")
        manager = SkillManager()
        skill = manager.load("social-media", skill_path=SKILL_DIR)
        assert "社交媒体" in skill.domain_profile

    def test_prompt_hashes_generated(self):
        """Prompt 哈希生成。"""
        if not SKILL_DIR.exists():
            pytest.skip("Skill 目录不存在")
        manager = SkillManager()
        skill = manager.load("social-media", skill_path=SKILL_DIR)
        assert len(skill.prompt_hashes) == len(skill.prompts)
        for role in skill.prompts:
            assert role in skill.prompt_hashes
            assert len(skill.prompt_hashes[role]) == 64  # SHA256 hex

    def test_skill_not_found(self):
        """未找到 Skill 应抛出 SkillValidationError。"""
        manager = SkillManager(search_paths=[Path("/nonexistent")])
        with pytest.raises(SkillValidationError) as exc_info:
            manager.load("nonexistent-skill")
        assert exc_info.value.code == "SKILL_NOT_FOUND"

    def test_invalid_frontmatter(self, tmp_path):
        """无效 frontmatter 应抛出 SkillValidationError。"""
        skill_dir = tmp_path / "skills" / "bad-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("no frontmatter here")

        manager = SkillManager()
        with pytest.raises(SkillValidationError) as exc_info:
            manager.load("bad-skill", skill_path=skill_dir)
        assert exc_info.value.code == "SKILL_SCHEMA_INVALID"

    def test_missing_name_field(self, tmp_path):
        """缺少 name 字段应抛出 SkillValidationError。"""
        skill_dir = tmp_path / "skills" / "no-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: test\n---\n"
        )

        manager = SkillManager()
        with pytest.raises(SkillValidationError) as exc_info:
            manager.load("no-name", skill_path=skill_dir)
        assert exc_info.value.code == "SKILL_SCHEMA_INVALID"


class TestSkillManager:
    def test_load_skill_without_cas_parameters(self, tmp_path):
        """Skill 不包含 cas_parameters 应能正常加载。"""
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: test-skill\n"
            "version: 0.1.0\n"
            "description: test\n"
            "prompts:\n"
            "  omniscient: prompts/omniscient.md\n"
            "  star: prompts/star.md\n"
            "  sea: prompts/sea.md\n"
            "domain_profile: domain-profile.md\n"
            "---\n"
        )

        (skill_dir / "domain-profile.md").write_text("领域画像内容")
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "omniscient.md").write_text("全视者指导")
        (prompts_dir / "star.md").write_text("星 prompt")
        (prompts_dir / "sea.md").write_text("海 prompt")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        skill = manager.load("test-skill")

        assert skill.name == "test-skill"
        assert "omniscient" in skill.prompts
        assert skill.domain_profile == "领域画像内容"

    def test_load_skill_with_minimal_fields(self, tmp_path):
        """最小字段的 Skill 应能正常加载。"""
        skill_dir = tmp_path / "skills" / "minimal-skill"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: minimal-skill\n"
            "prompts:\n"
            "  star: prompts/star.md\n"
            "  sea: prompts/sea.md\n"
            "---\n"
        )

        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "star.md").write_text("star")
        (prompts_dir / "sea.md").write_text("sea")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        skill = manager.load("minimal-skill")

        assert skill.name == "minimal-skill"
        assert skill.version == "0.1.0"  # default
        assert "star" in skill.prompts
        assert "sea" in skill.prompts

    def test_prompts_loaded_from_top_level(self, tmp_path):
        """Skill 的 prompts 从顶层字段加载，而非 domain_protocol。"""
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: test-skill\n"
            "version: 0.1.0\n"
            "description: test\n"
            "prompts:\n"
            "  omniscient: prompts/omniscient.md\n"
            "  star: prompts/star.md\n"
            "  sea: prompts/sea.md\n"
            "---\n"
        )

        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "omniscient.md").write_text("全视者指导内容")
        (prompts_dir / "star.md").write_text("星 agent prompt")
        (prompts_dir / "sea.md").write_text("海 agent prompt")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        skill = manager.load("test-skill")

        assert skill.prompts["omniscient"] == "全视者指导内容"
        assert skill.prompts["star"] == "星 agent prompt"
        assert skill.prompts["sea"] == "海 agent prompt"


class TestPlatformProfileLoading:
    def test_load_skill_with_platforms_dir(self, tmp_path):
        """Skill with platforms/ directory should auto-load platform profiles."""
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: test-skill\n"
            "version: 0.1.0\n"
            "prompts:\n"
            "  star: prompts/star.md\n"
            "---\n"
        )
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "star.md").write_text("star")

        platforms_dir = skill_dir / "platforms"
        platforms_dir.mkdir()
        (platforms_dir / "xiaohongshu.md").write_text("小红书画像内容")
        (platforms_dir / "weibo.md").write_text("微博画像内容")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        skill = manager.load("test-skill")

        assert "xiaohongshu" in skill.platform_profiles
        assert skill.platform_profiles["xiaohongshu"] == "小红书画像内容"
        assert "weibo" in skill.platform_profiles
        assert skill.platform_profiles["weibo"] == "微博画像内容"

    def test_load_skill_without_platforms_dir(self, tmp_path):
        """Skill without platforms/ directory should have empty platform_profiles."""
        skill_dir = tmp_path / "skills" / "no-platforms"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: no-platforms\n"
            "prompts:\n"
            "  star: prompts/star.md\n"
            "---\n"
        )
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "star.md").write_text("star")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        skill = manager.load("no-platforms")

        assert skill.platform_profiles == {}

    def test_platform_profiles_ignores_non_md_files(self, tmp_path):
        """platform_profiles should only load .md files."""
        skill_dir = tmp_path / "skills" / "filter-test"
        skill_dir.mkdir(parents=True)

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: filter-test\n"
            "prompts:\n"
            "  star: prompts/star.md\n"
            "---\n"
        )
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "star.md").write_text("star")

        platforms_dir = skill_dir / "platforms"
        platforms_dir.mkdir()
        (platforms_dir / "xiaohongshu.md").write_text("有效")
        (platforms_dir / "README.txt").write_text("忽略")
        (platforms_dir / ".hidden.md").write_text("忽略")

        manager = SkillManager(search_paths=[tmp_path / "skills"])
        skill = manager.load("filter-test")

        assert list(skill.platform_profiles.keys()) == ["xiaohongshu"]


class TestSkillPromptContent:
    def test_social_media_omniscient_prompt_contains_cautious_guidance(self):
        """Social media omniscient prompt should contain cautious prediction guidance."""
        from ripple.skills.manager import SkillManager
        mgr = SkillManager()
        skill = mgr.load("social-media")
        omniscient_prompt = skill.prompts.get("omniscient", "")
        assert "审慎" in omniscient_prompt or "90%" in omniscient_prompt

    def test_social_media_sea_prompt_contains_passive_consumer_anchor(self):
        """Social media sea prompt should mention passive consumption behavior."""
        from ripple.skills.manager import SkillManager
        mgr = SkillManager()
        skill = mgr.load("social-media")
        sea_prompt = skill.prompts.get("sea", "")
        assert "被动" in sea_prompt or "浏览" in sea_prompt or "划走" in sea_prompt

    def test_social_media_domain_profile_contains_base_rate(self):
        """Domain profile should mention base rate reality of content performance."""
        from ripple.skills.manager import SkillManager
        mgr = SkillManager()
        skill = mgr.load("social-media")
        profile = skill.domain_profile
        assert "90%" in profile or "基准" in profile or "绝大多数" in profile
