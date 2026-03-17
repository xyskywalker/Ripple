# manager.py
# =============================================================================
# Skill 发现、加载与管理。 / Skill discovery, loading & management.
#
# Skill 仅提供领域画像（domain_profile）、Prompt 模板和报告模板。
# / Skills provide domain profiles, prompt templates, and report templates.
# CAS 参数由全视者 Agent 在运行时动态决定，不再硬编码。
# / CAS params are dynamically decided by Omniscient at runtime, not hardcoded.
# 生命周期：discover -> select -> load -> freeze
# / Lifecycle: discover -> select -> load -> freeze
#
# SKILL.md 格式 / SKILL.md format:
#   ---
#   name: social-media
#   version: "..."
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

from ripple.runtime_paths import default_skill_search_paths
from ripple.version import VERSION

from ripple.skills.validator import (
    SkillValidationError,
    SKILL_NOT_FOUND,
    SKILL_SCHEMA_INVALID,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LoadedSkill — 加载完成的 Skill 快照（不可变数据对象） / Frozen Skill snapshot (immutable data object)
# =============================================================================


@dataclass
class LoadedSkill:
    """加载完成的 Skill 快照 — 冻结后不应修改。 / Loaded Skill snapshot — should not be modified after freeze."""

    name: str
    version: str
    description: str
    path: Path

    # Prompt 模板文本（角色名 -> 模板内容） / Prompt template text (role -> content)
    prompts: Dict[str, str]  # {"omniscient": "...", "star": "...", "sea": "..."}

    # Prompt 模板 SHA256 哈希（用于缓存与审计） / Prompt template SHA256 hashes (for caching & audit)
    prompt_hashes: Dict[str, str]

    # 领域画像自然语言文本（传递给全视者 Agent） / Domain profile NL text (passed to Omniscient)
    domain_profile: str = ""

    # 平台画像（约定扫描 {skill_dir}/platforms/*.md） / Platform profiles (convention: scan {skill_dir}/platforms/*.md)
    platform_profiles: Dict[str, str] = field(default_factory=dict)

    # 平台展示名映射（英文 key -> 中文标签） / Platform display labels (english key -> Chinese label)
    platform_labels: Dict[str, str] = field(default_factory=dict)

    # 评分 rubric（PMF 等领域使用） / Scoring rubrics (used by PMF validation etc.)
    rubrics: Dict[str, str] = field(default_factory=dict)

    # 报告模板配置（约定扫描 {skill_dir}/reports/*.yml|*.yaml）
    # / Report profile configs (convention: scan {skill_dir}/reports/*.yml|*.yaml)
    report_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 输入 schema 配置（约定读取 {skill_dir}/request-schema.yaml）
    # / Request schema config (convention: read {skill_dir}/request-schema.yaml)
    request_schema: Dict[str, Any] = field(default_factory=dict)
    request_schema_path: str = ""

    # 示例配置（约定扫描 {skill_dir}/examples/*.yml|*.yaml）
    # / Example configs (convention: scan {skill_dir}/examples/*.yml|*.yaml)
    example_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    example_paths: Dict[str, str] = field(default_factory=dict)

    # 渠道画像（约定扫描 {skill_dir}/channels/*.md） / Channel profiles (convention: scan {skill_dir}/channels/*.md)
    channel_profiles: Dict[str, str] = field(default_factory=dict)

    # 渠道展示名映射（英文 key -> 中文标签） / Channel display labels (english key -> Chinese label)
    channel_labels: Dict[str, str] = field(default_factory=dict)

    # 垂直领域画像（约定扫描 {skill_dir}/verticals/*.md） / Vertical profiles (convention: scan {skill_dir}/verticals/*.md)
    vertical_profiles: Dict[str, str] = field(default_factory=dict)

    # 垂直领域展示名映射（英文 key -> 中文标签） / Vertical display labels (english key -> Chinese label)
    vertical_labels: Dict[str, str] = field(default_factory=dict)

    # 原始 frontmatter 元数据 / Raw frontmatter metadata
    meta: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# SkillManager — Skill 发现、加载与管理 / Skill discovery, loading & management
# =============================================================================


class SkillManager:
    """Skill 发现、加载与管理。 / Skill discovery, loading & management.

    生命周期 / Lifecycle: discover -> select -> load -> freeze
    """

    def __init__(self, search_paths: Optional[List[Path]] = None) -> None:
        """初始化 SkillManager。 / Initialize SkillManager.

        Args:
            search_paths: Skill 搜索路径列表。None 时使用默认路径。 / Skill search paths. Defaults if None:
                  1. {cwd}/.agents/skills
                  2. {cwd}/skills
                  3. ~/.config/ripple/skills
                  4. ${RIPPLE_HOME_DIR:-~/.ripple}/src/Ripple/skills
        """
        self._search_paths: List[Path] = (
            list(search_paths) if search_paths else self._build_default_paths()
        )
        self._discovered: Dict[str, Dict[str, Any]] = {}

    def _build_default_paths(self) -> List[Path]:
        """构建默认搜索路径列表。 / Build default search path list."""
        return list(default_skill_search_paths())

    def _example_schema_error(self, skill_name: str, example_file: Path, message: str) -> SkillValidationError:
        """构造 example schema 错误。 / Build a structured example schema validation error."""
        return SkillValidationError(
            SKILL_SCHEMA_INVALID,
            f"Skill `{skill_name}` 的示例文件 `{example_file.as_posix()}` 无效：{message}",
        )

    def _validate_example_profile(
        self,
        *,
        skill_name: str,
        example_file: Path,
        payload: Dict[str, Any],
    ) -> None:
        """校验 example 文件结构。 / Validate example file structure."""
        required_text_fields = ("title", "summary", "use_when")
        for field_name in required_text_fields:
            value = payload.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise self._example_schema_error(
                    skill_name,
                    example_file,
                    f"字段 `{field_name}` 必须是非空字符串。",
                )

        command = payload.get("command")
        if not isinstance(command, dict) or not command:
            raise self._example_schema_error(
                skill_name,
                example_file,
                "字段 `command` 必须是非空对象。",
            )

        request = payload.get("request")
        if not isinstance(request, dict) or not request:
            raise self._example_schema_error(
                skill_name,
                example_file,
                "字段 `request` 必须是非空对象。",
            )

        tags = payload.get("tags")
        if tags is not None and (
            not isinstance(tags, list)
            or any(not isinstance(item, str) or not item.strip() for item in tags)
        ):
            raise self._example_schema_error(
                skill_name,
                example_file,
                "字段 `tags` 若存在，必须是非空字符串数组。",
            )

    # -------------------------------------------------------------------------
    # discover — 扫描目录查找 SKILL.md / Scan directories for SKILL.md
    # -------------------------------------------------------------------------

    def discover(self) -> List[Dict[str, Any]]:
        """发现所有可用的 Skill。 / Discover all available skills.

        扫描 search_paths 下所有子目录中的 SKILL.md 文件。
        同名 Skill 先扫描到者胜出。隐藏目录被忽略。
        / Scans SKILL.md in subdirectories of search_paths. First-found wins for same name. Hidden dirs ignored.

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
    # load — 加载指定 Skill / Load a specific skill
    # -------------------------------------------------------------------------

    def load(
        self,
        skill_name: str,
        skill_path: Optional[Path] = None,
    ) -> LoadedSkill:
        """加载指定 Skill。 / Load a specified skill.

        如果提供了 skill_path，直接从该路径加载（跳过 discover）；否则按 name 匹配。
        / If skill_path is given, load directly (skip discover); otherwise match by name.

        Args:
            skill_name: Skill 名称。 / Skill name.
            skill_path: 可选，直接指定 Skill 目录路径。 / Optional direct skill directory path.

        Returns:
            LoadedSkill 冻结快照。 / LoadedSkill frozen snapshot.

        Raises:
            SkillValidationError: 校验失败。 / Validation failed.
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
        """从 frontmatter 加载 Skill。 / Load skill from frontmatter."""
        name = frontmatter.get("name", "")
        if not name:
            raise SkillValidationError(
                SKILL_SCHEMA_INVALID,
                "frontmatter 缺少 name 字段",
            )

        # 解析 prompts 配置 / Parse prompts config
        prompts_config = frontmatter.get("prompts", {})

        # 加载 prompt 模板文件 / Load prompt template files
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

        # 加载 domain_profile / Load domain profile
        domain_profile = ""
        dp_path = frontmatter.get("domain_profile", "")
        if dp_path:
            dp_file = skill_dir / dp_path
            if dp_file.is_file():
                domain_profile = dp_file.read_text(encoding="utf-8")
            else:
                logger.warning("domain_profile 文件不存在: %s", dp_file)

        # 加载 platform profiles（约定优于配置） / Load platform profiles (convention over configuration)
        platform_profiles: Dict[str, str] = {}
        platforms_dir = skill_dir / "platforms"
        if platforms_dir.is_dir():
            for pf in sorted(platforms_dir.glob("*.md")):
                if pf.name.startswith("."):
                    continue
                platform_name = pf.stem
                platform_profiles[platform_name] = pf.read_text(encoding="utf-8")
                logger.info("加载平台画像: %s", platform_name)

        # 加载 rubrics（约定优于配置） / Load rubrics (convention over configuration)
        rubrics: Dict[str, str] = {}
        rubrics_dir = skill_dir / "rubrics"
        if rubrics_dir.is_dir():
            for rf in sorted(rubrics_dir.glob("*.md")):
                if rf.name.startswith("."):
                    continue
                rubric_name = rf.stem
                rubrics[rubric_name] = rf.read_text(encoding="utf-8")
                logger.info("加载评分 rubric: %s", rubric_name)

        # 加载 report profiles（约定优于配置） / Load report profiles (convention over configuration)
        report_profiles: Dict[str, Dict[str, Any]] = {}
        reports_dir = skill_dir / "reports"
        if reports_dir.is_dir():
            for ext in ("*.yaml", "*.yml"):
                for pf in sorted(reports_dir.glob(ext)):
                    if pf.name.startswith("."):
                        continue
                    profile_name = pf.stem
                    try:
                        loaded_profile = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
                    except yaml.YAMLError as exc:
                        logger.warning("加载报告模板失败: %s — %s", pf, exc)
                        continue
                    if not isinstance(loaded_profile, dict):
                        logger.warning("报告模板必须是 YAML 字典，已跳过: %s", pf)
                        continue
                    report_profiles[profile_name] = loaded_profile
                    logger.info("加载报告模板: %s", profile_name)

        # 加载 request schema（约定优于配置） / Load request schema (convention over configuration)
        request_schema: Dict[str, Any] = {}
        request_schema_path = ""
        schema_rel_path = str(frontmatter.get("request_schema") or "").strip()
        schema_file: Optional[Path] = None
        if schema_rel_path:
            candidate = skill_dir / schema_rel_path
            if candidate.is_file():
                schema_file = candidate
            else:
                logger.warning("request_schema 文件不存在: %s", candidate)
        else:
            candidate = skill_dir / "request-schema.yaml"
            if candidate.is_file():
                schema_file = candidate
        if schema_file is not None:
            try:
                loaded_schema = yaml.safe_load(schema_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                logger.warning("加载 request schema 失败: %s — %s", schema_file, exc)
            else:
                if isinstance(loaded_schema, dict):
                    request_schema = loaded_schema
                    request_schema_path = str(schema_file)
                    logger.info("加载输入 schema: %s", schema_file.name)
                else:
                    logger.warning("request schema 必须是 YAML 字典，已跳过: %s", schema_file)

        # 加载 examples（约定优于配置） / Load examples (convention over configuration)
        example_profiles: Dict[str, Dict[str, Any]] = {}
        example_paths: Dict[str, str] = {}
        examples_dir = skill_dir / "examples"
        if examples_dir.is_dir():
            for ext in ("*.yaml", "*.yml"):
                for ef in sorted(examples_dir.glob(ext)):
                    if ef.name.startswith("."):
                        continue
                    profile_name = ef.stem
                    try:
                        loaded_example = yaml.safe_load(ef.read_text(encoding="utf-8")) or {}
                    except yaml.YAMLError as exc:
                        raise self._example_schema_error(
                            name,
                            ef.relative_to(skill_dir),
                            f"YAML 解析失败：{exc}",
                        ) from exc
                    if not isinstance(loaded_example, dict):
                        raise self._example_schema_error(
                            name,
                            ef.relative_to(skill_dir),
                            "YAML 顶层必须是对象（mapping）。",
                        )
                    self._validate_example_profile(
                        skill_name=name,
                        example_file=ef.relative_to(skill_dir),
                        payload=loaded_example,
                    )
                    example_profiles[profile_name] = loaded_example
                    example_paths[profile_name] = str(ef)
                    logger.info("加载领域示例: %s", profile_name)

        # 加载 channel profiles（约定优于配置） / Load channel profiles (convention over configuration)
        channel_profiles: Dict[str, str] = {}
        channels_dir = skill_dir / "channels"
        if channels_dir.is_dir():
            for cf in sorted(channels_dir.glob("*.md")):
                if cf.name.startswith("."):
                    continue
                channel_name = cf.stem
                channel_profiles[channel_name] = cf.read_text(encoding="utf-8")
                logger.info("加载渠道画像: %s", channel_name)

        # 加载垂直领域画像（约定优于配置） / Load vertical profiles (convention over configuration)
        vertical_profiles: Dict[str, str] = {}
        verticals_dir = skill_dir / "verticals"
        if verticals_dir.is_dir():
            for vf in sorted(verticals_dir.glob("*.md")):
                if vf.name.startswith("."):
                    continue
                vertical_name = vf.stem
                vertical_profiles[vertical_name] = vf.read_text(encoding="utf-8")
                logger.info("加载垂直领域画像: %s", vertical_name)

        # 生成 prompt hashes / Generate prompt hashes
        prompt_hashes: Dict[str, str] = {}
        for role, content in prompts.items():
            prompt_hashes[role] = self._compute_prompt_hash(content)

        def _label_map(field_name: str) -> Dict[str, str]:
            """解析中英文展示标签映射。 / Parse bilingual display label mappings."""
            raw = frontmatter.get(field_name, {})
            if not isinstance(raw, dict):
                return {}
            labels: Dict[str, str] = {}
            for key, value in raw.items():
                key_text = str(key).strip()
                value_text = str(value).strip()
                if key_text and value_text:
                    labels[key_text] = value_text
            return labels

        loaded = LoadedSkill(
            name=name,
            version=frontmatter.get("version", VERSION),
            description=frontmatter.get("description", ""),
            path=skill_dir,
            prompts=prompts,
            prompt_hashes=prompt_hashes,
            domain_profile=domain_profile,
            platform_profiles=platform_profiles,
            platform_labels=_label_map("platform_labels"),
            rubrics=rubrics,
            report_profiles=report_profiles,
            request_schema=request_schema,
            request_schema_path=request_schema_path,
            example_profiles=example_profiles,
            example_paths=example_paths,
            channel_profiles=channel_profiles,
            channel_labels=_label_map("channel_labels"),
            vertical_profiles=vertical_profiles,
            vertical_labels=_label_map("vertical_labels"),
            # 保留完整 frontmatter，供 CLI 读取 use_when 等元信息。
            # Keep the full frontmatter so CLI can read use_when and similar metadata.
            meta=dict(frontmatter),
        )

        logger.info(
            "Skill '%s' v%s 加载完成 (%d prompts, %d platforms)",
            loaded.name, loaded.version, len(prompts), len(platform_profiles),
        )
        return loaded

    # -------------------------------------------------------------------------
    # 内部方法 / Internal methods
    # -------------------------------------------------------------------------

    def _parse_frontmatter(self, skill_path: Path) -> Dict[str, Any]:
        """解析 SKILL.md 的 YAML frontmatter。 / Parse YAML frontmatter from SKILL.md."""
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
        """计算 Prompt 模板内容的 SHA256 哈希。 / Compute SHA256 hash of prompt template content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
