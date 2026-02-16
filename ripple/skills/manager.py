# manager.py
# =============================================================================
# Skill 发现、加载与管理。
#
# Skill 仅提供领域画像（domain_profile）和 Prompt 模板。
# CAS 参数由全视者 Agent 在运行时动态决定，不再硬编码。
# 生命周期：discover -> select -> load -> freeze
#
# SKILL.md 格式：
#   ---
#   name: social-media
#   version: "0.1.0"
#   description: ...
#   prompts:
#     omniscient: prompts/omniscient.md
#     star: prompts/star.md
#     sea: prompts/sea.md
#   domain_profile: domain-profile.md
#   ---
# =============================================================================

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ripple.skills.validator import (
    SkillValidationError,
    SKILL_NOT_FOUND,
    SKILL_SCHEMA_INVALID,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LoadedSkill — 加载完成的 Skill 快照（不可变数据对象）
# =============================================================================


@dataclass
class LoadedSkill:
    """加载完成的 Skill 快照 — 冻结后不应修改。"""

    name: str
    version: str
    description: str
    path: Path

    # Prompt 模板文本（角色名 -> 模板内容）
    prompts: Dict[str, str]  # {"omniscient": "...", "star": "...", "sea": "..."}

    # Prompt 模板 SHA256 哈希（用于缓存与审计）
    prompt_hashes: Dict[str, str]

    # 领域画像自然语言文本（传递给全视者 Agent）
    domain_profile: str = ""

    # 平台画像（约定扫描 {skill_dir}/platforms/*.md）
    platform_profiles: Dict[str, str] = field(default_factory=dict)

    # 原始 frontmatter 元数据
    meta: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# SkillManager — Skill 发现、加载与管理
# =============================================================================


class SkillManager:
    """Skill 发现、加载与管理。

    生命周期：discover -> select -> load -> freeze
    """

    _DEFAULT_SEARCH_DIRS = (
        ".agents/skills",
        "skills",
    )
    _HOME_SEARCH_DIRS = (
        ".config/ripple/skills",
    )

    def __init__(self, search_paths: Optional[List[Path]] = None) -> None:
        """初始化 SkillManager。

        Args:
            search_paths: Skill 搜索路径列表。如果为 None，使用默认扫描路径。
                默认扫描路径：
                  1. {cwd}/.agents/skills
                  2. {cwd}/skills
                  3. ~/.config/ripple/skills
        """
        self._search_paths: List[Path] = (
            list(search_paths) if search_paths else self._build_default_paths()
        )
        self._discovered: Dict[str, Dict[str, Any]] = {}

    def _build_default_paths(self) -> List[Path]:
        """构建默认搜索路径列表。"""
        paths: List[Path] = []
        cwd = Path.cwd()
        for subdir in self._DEFAULT_SEARCH_DIRS:
            paths.append(cwd / subdir)
        home = Path.home()
        for subdir in self._HOME_SEARCH_DIRS:
            paths.append(home / subdir)
        return paths

    # -------------------------------------------------------------------------
    # discover — 扫描目录查找 SKILL.md
    # -------------------------------------------------------------------------

    def discover(self) -> List[Dict[str, Any]]:
        """发现所有可用的 Skill。

        扫描 search_paths 下所有子目录中的 SKILL.md 文件。
        同名 Skill 先扫描到者胜出。隐藏目录被忽略。

        Returns:
            [{"name": str, "description": str, "path": Path}, ...]
        """
        self._discovered.clear()
        results: List[Dict[str, Any]] = []

        for search_dir in self._search_paths:
            if not search_dir.is_dir():
                logger.debug("搜索路径不存在，跳过: %s", search_dir)
                continue

            for skill_dir in sorted(search_dir.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue

                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue

                try:
                    fm = self._parse_frontmatter(skill_dir)
                except Exception as exc:
                    logger.warning(
                        "解析 SKILL.md 失败，跳过: %s — %s", skill_md, exc
                    )
                    continue

                name = fm.get("name", "")
                if not name:
                    logger.warning("SKILL.md 缺少 name 字段，跳过: %s", skill_md)
                    continue

                if name in self._discovered:
                    logger.debug(
                        "Skill '%s' 已发现于 %s，忽略重复: %s",
                        name, self._discovered[name]["path"], skill_dir,
                    )
                    continue

                entry = {
                    "name": name,
                    "description": fm.get("description", ""),
                    "path": skill_dir,
                }
                self._discovered[name] = entry
                results.append(entry)
                logger.info("发现 Skill: %s @ %s", name, skill_dir)

        return results

    # -------------------------------------------------------------------------
    # load — 加载指定 Skill
    # -------------------------------------------------------------------------

    def load(
        self,
        skill_name: str,
        skill_path: Optional[Path] = None,
    ) -> LoadedSkill:
        """加载指定 Skill。

        如果提供了 skill_path，直接从该路径加载（跳过 discover）。
        否则从已发现的 Skill 中按 name 匹配。

        Args:
            skill_name: Skill 名称。
            skill_path: 可选，直接指定 Skill 目录路径。

        Returns:
            LoadedSkill 冻结快照。

        Raises:
            SkillValidationError: 校验失败。
        """
        if skill_path is not None:
            skill_dir = Path(skill_path)
        else:
            if not self._discovered:
                self.discover()
            if skill_name not in self._discovered:
                raise SkillValidationError(
                    SKILL_NOT_FOUND,
                    f"未找到 Skill: '{skill_name}'（已扫描路径: {self._search_paths}）",
                )
            skill_dir = Path(self._discovered[skill_name]["path"])

        if not skill_dir.is_dir():
            raise SkillValidationError(
                SKILL_NOT_FOUND,
                f"Skill 目录不存在: {skill_dir}",
            )

        frontmatter = self._parse_frontmatter(skill_dir)
        return self._load_skill(frontmatter, skill_dir)

    def _load_skill(
        self,
        frontmatter: Dict[str, Any],
        skill_dir: Path,
    ) -> LoadedSkill:
        """从 frontmatter 加载 Skill。"""
        name = frontmatter.get("name", "")
        if not name:
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                "frontmatter 缺少 name 字段",
            )

        # 解析 prompts 配置
        prompts_config = frontmatter.get("prompts", {})

        # 加载 prompt 模板文件
        prompts: Dict[str, str] = {}
        if isinstance(prompts_config, dict):
            for role, rel_path in prompts_config.items():
                if not isinstance(rel_path, str):
                    continue
                prompt_file = skill_dir / rel_path
                if prompt_file.is_file():
                    prompts[role] = prompt_file.read_text(encoding="utf-8")
                else:
                    logger.warning(
                        "Prompt 文件不存在: %s（角色: %s）", prompt_file, role
                    )
                    prompts[role] = ""

        # 加载 domain_profile
        domain_profile = ""
        dp_path = frontmatter.get("domain_profile", "")
        if dp_path:
            dp_file = skill_dir / dp_path
            if dp_file.is_file():
                domain_profile = dp_file.read_text(encoding="utf-8")
            else:
                logger.warning("domain_profile 文件不存在: %s", dp_file)

        # 加载 platform profiles（约定优于配置：扫描 {skill_dir}/platforms/*.md）
        platform_profiles: Dict[str, str] = {}
        platforms_dir = skill_dir / "platforms"
        if platforms_dir.is_dir():
            for pf in sorted(platforms_dir.glob("*.md")):
                if pf.name.startswith("."):
                    continue
                platform_name = pf.stem
                platform_profiles[platform_name] = pf.read_text(encoding="utf-8")
                logger.info("加载平台画像: %s", platform_name)

        # 生成 prompt hashes
        prompt_hashes: Dict[str, str] = {}
        for role, content in prompts.items():
            prompt_hashes[role] = self._compute_prompt_hash(content)

        loaded = LoadedSkill(
            name=name,
            version=frontmatter.get("version", "0.1.0"),
            description=frontmatter.get("description", ""),
            path=skill_dir,
            prompts=prompts,
            prompt_hashes=prompt_hashes,
            domain_profile=domain_profile,
            platform_profiles=platform_profiles,
            meta=frontmatter.get("meta", {}),
        )

        logger.info(
            "Skill '%s' v%s 加载完成 (%d prompts, %d platforms)",
            loaded.name, loaded.version, len(prompts), len(platform_profiles),
        )
        return loaded

    # -------------------------------------------------------------------------
    # 内部方法
    # -------------------------------------------------------------------------

    def _parse_frontmatter(self, skill_path: Path) -> Dict[str, Any]:
        """解析 SKILL.md 的 YAML frontmatter。"""
        skill_md = skill_path / "SKILL.md"
        if not skill_md.is_file():
            raise SkillValidationError(
                SKILL_NOT_FOUND,
                f"SKILL.md 文件不存在: {skill_md}",
            )

        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                f"SKILL.md 缺少 YAML frontmatter（文件必须以 --- 开头）: {skill_md}",
            )

        second_sep = text.find("---", 3)
        if second_sep == -1:
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                f"SKILL.md YAML frontmatter 缺少结束标记 ---: {skill_md}",
            )

        yaml_text = text[3:second_sep].strip()
        if not yaml_text:
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                f"SKILL.md YAML frontmatter 为空: {skill_md}",
            )

        try:
            result = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                f"SKILL.md YAML 解析失败: {exc}",
            ) from exc

        if not isinstance(result, dict):
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                f"SKILL.md YAML frontmatter 必须为字典，实际类型: {type(result).__name__}",
            )

        return result

    @staticmethod
    def _compute_prompt_hash(content: str) -> str:
        """计算 Prompt 模板内容的 SHA256 哈希。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
