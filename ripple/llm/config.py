# config.py
# =============================================================================
# LLM 配置加载与合并模块 / LLM config loading & merging module
#
# 职责 / Responsibilities:
#   - 定义 LLM 模型配置的数据结构（ModelEndpointConfig）
#     / Define data structure for LLM model config (ModelEndpointConfig)
#   - 实现三层优先级配置加载：代码传入 > 配置文件 > 环境变量
#     / Three-tier priority loading: code > config file > env vars
#   - 为 ModelRouter 提供解析后的完整配置，供各 adapter 使用
#     / Provide resolved configs to ModelRouter for adapter creation
#   - 配置缺失时抛出 ConfigurationError，不提供任何硬编码默认值
#     / Raise ConfigurationError on missing config; no hardcoded defaults
#
# 设计依据 / Design rationale:
#   - 各 LLM 适配器通过 from_endpoint_config() 从 ModelEndpointConfig 创建实例
#     / Each adapter creates instances via from_endpoint_config()
#   - 不同 Provider 的 API 格式由各适配器独立处理
#     / API format differences handled by individual adapters
#   - 本模块只负责"配置从哪来"和"怎么合并"
#     / This module only handles config sourcing & merging
# =============================================================================

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# =============================================================================
# 数据结构 / Data Structures
# =============================================================================


@dataclass
class ModelEndpointConfig:
    """单个模型端点的完整配置。
    / Complete config for a single model endpoint.

    对应一个 Agent 角色（omniscient / star / sea 等）的模型配置。
    各 LLM 适配器通过 from_endpoint_config() 读取本配置创建实例。
    / Maps to one agent role; adapters instantiate via from_endpoint_config().
    """

    # --- 必填：模型标识 / Required: model identity ---
    model_platform: str  # 平台标识 / Platform: "openai" / "anthropic" / "bedrock" / "deepseek" etc.
    model_name: str  # 模型名称 / Model name: "gpt-4o" / "claude-opus-4-6" etc.

    # --- 可选：连接信息 / Optional: connection info ---
    api_key: Optional[str] = None  # API 密钥 / API key
    url: Optional[str] = None  # 自定义 endpoint URL / Custom endpoint URL (proxy, private deploy)

    # --- 可选：API 模式 / Optional: API mode ---
    # "chat_completions" — 标准 OpenAI Chat Completions 格式（默认） / standard OpenAI format (default)
    # "responses"        — OpenAI Responses API 格式 / OpenAI Responses API format
    # "anthropic"        — Anthropic Messages API 格式 / Anthropic Messages API format
    # "bedrock"          — AWS Bedrock InvokeModel 格式 / AWS Bedrock InvokeModel format
    api_mode: str = "chat_completions"

    # --- 可选：模型行为参数 / Optional: model behavior params ---
    temperature: float = 0.7
    # 安全默认上限，避免第三方 SDK 回退到异常超大值 / Safe default cap to prevent SDK fallback to huge values
    max_tokens: Optional[int] = 4096
    timeout: Optional[float] = None
    max_retries: int = 3

    # --- 可选：Azure 专用 / Optional: Azure-specific ---
    api_version: Optional[str] = None
    azure_deployment_name: Optional[str] = None

    # --- 可选：额外参数（透传给适配器） / Optional: extra params (passed through to adapters) ---
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelEndpointConfig:
        """从字典构建配置。 / Build config from dict.

        支持两种格式 / Two formats supported:
        1. 简写格式（仅模型名字符串） / Shorthand (model name string): "claude-opus-4-6"
        2. 完整格式（字典） / Full dict: {"model_platform": "anthropic", "model_name": "claude-opus-4-6", ...}

        简写格式时自动推断 model_platform。 / Platform auto-inferred for shorthand.
        字段优先级 / Field priority: model_name > model_type (compat) > model (legacy)
        """
        if isinstance(data, str):
            # 简写格式：仅模型名 / Shorthand: model name only
            platform = _infer_platform(data)
            return cls(model_platform=platform, model_name=data)

        # 完整格式：从字典提取字段 / Full format: extract fields from dict
        # 优先级 / Priority: model_name > model_type (compat) > model (legacy)
        model_name = (
            data.get("model_name")
            or data.get("model_type")
            or data.get("model", "")
        )
        model_platform = data.get("model_platform") or _infer_platform(
            model_name
        )

        # api_mode：显式指定或自动推断 / Explicit or auto-inferred
        _valid_api_modes = (
            "chat_completions", "responses", "anthropic", "bedrock",
        )
        api_mode = data.get("api_mode") or _infer_api_mode(
            model_platform, data.get("url")
        )
        if api_mode not in _valid_api_modes:
            raise ValueError(
                f"不支持的 api_mode: '{api_mode}'。"
                f"仅支持: {', '.join(_valid_api_modes)}。"
            )

        # 已识别字段集合 / Known field set
        _known_keys = {
            "model",
            "model_name",
            "model_type",  # 向后兼容 / backward compat
            "model_platform",
            "api_key",
            "url",
            "api_mode",
            "temperature",
            "max_tokens",
            "timeout",
            "max_retries",
            "api_version",
            "azure_deployment_name",
        }

        return cls(
            model_platform=model_platform,
            model_name=model_name,
            api_key=data.get("api_key"),
            url=data.get("url"),
            api_mode=api_mode,
            temperature=float(data.get("temperature", 0.7)),
            max_tokens=(
                data["max_tokens"] if "max_tokens" in data else 4096
            ),
            timeout=data.get("timeout"),
            max_retries=int(data.get("max_retries", 3)),
            api_version=data.get("api_version"),
            azure_deployment_name=data.get("azure_deployment_name"),
            extra={
                k: v
                for k, v in data.items()
                if k not in _known_keys
            },
        )


# =============================================================================
# 平台推断 / Platform Inference
# =============================================================================

# 模型名称 → 平台映射规则（按前缀/关键词匹配） / Model name → platform mapping rules (prefix/keyword)
_PLATFORM_INFERENCE_RULES: List[tuple] = [
    # Anthropic 系列 / Anthropic family
    (["claude"], "anthropic"),
    # OpenAI 系列 / OpenAI family
    (["gpt-", "o1-", "o3-", "chatgpt"], "openai"),
    # 其他常见平台（后续可扩展） / Other platforms (extensible)
    (["gemini"], "google"),
    (["deepseek"], "deepseek"),
    (["qwen", "qwq"], "qwen"),
    (["llama", "meta-llama"], "ollama"),
]


def _infer_platform(model_name: str) -> str:
    """根据模型名称推断 model_platform。 / Infer model_platform from model name.

    未能推断时返回 "openai"（最通用的 fallback）。
    / Falls back to "openai" when inference fails.
    """
    name_lower = model_name.lower()
    for keywords, platform in _PLATFORM_INFERENCE_RULES:
        for kw in keywords:
            if kw in name_lower:
                return platform
    logger.debug(
        "无法从模型名称 '%s' 推断平台，使用默认 'openai'", model_name
    )
    return "openai"


# =============================================================================
# API 模式推断 / API Mode Inference
# =============================================================================


def _infer_api_mode(platform: str, url: Optional[str] = None) -> str:
    """根据 platform 和 url 自动推断 api_mode。 / Auto-infer api_mode from platform & url.

    推断规则（按优先级） / Inference rules (by priority):
    1. platform == "anthropic" 且无自定义 URL / and no custom URL -> "anthropic"
    2. platform == "bedrock" -> "bedrock"
    3. URL 包含 / URL contains /responses -> "responses"
    4. 其他 / Otherwise -> "chat_completions"
    """
    platform_lower = platform.lower() if platform else ""

    if platform_lower == "anthropic" and not url:
        return "anthropic"
    if platform_lower == "bedrock":
        return "bedrock"
    if url and "/responses" in url:
        return "responses"
    return "chat_completions"


# =============================================================================
# 保留的角色名列表（用于错误提示） / Reserved Role Names (for error hints)
# =============================================================================

# 这些是 Ripple 引擎使用的已知角色名，仅用于配置缺失时给出友好错误提示。
# / Known engine roles; used only for friendlier error messages on missing config.
_KNOWN_ROLES = [
    "omniscient",
    "star",
    "sea",
]


# =============================================================================
# 配置加载器 / Config Loader
# =============================================================================


class LLMConfigLoader:
    """LLM 配置加载器 — 实现三层优先级配置合并。
    / LLM config loader — three-tier priority merging.

    优先级（高→低） / Priority (high→low):
    1. 代码传入（llm_config 字典参数） / Code-level config dict
    2. 配置文件（YAML / JSON） / Config file (YAML/JSON)
    3. 环境变量（通过 ${VAR} 在 YAML 中引用） / Env vars (${VAR} in YAML)

    不提供硬编码默认模型。角色无配置且无 _default 可继承时抛出 ConfigurationError。
    / No hardcoded defaults. Raises ConfigurationError if no config found for a role.

    llm_config 字典格式 / Dict format:
    {
        # 全局默认 / Global default
        "_default": {
            "model_platform": "anthropic",
            "model_name": "claude-sonnet-4-20250514",
            "api_key": "sk-ant-xxx",
        },
        # 角色级配置（覆盖全局默认） / Role-level (overrides _default)
        "star": {
            "model_name": "claude-opus-4-6",
            "temperature": 0.8,
        },
        "sea": "claude-haiku",  # 简写格式 / Shorthand: model name only
        # 降级映射（可选） / Degradation mapping (optional)
        "_degradation": {
            "star": "claude-sonnet-4-20250514",
            "omniscient": "claude-sonnet-4-20250514",
        },
    }
    """

    # 配置文件搜索路径（按优先级） / Config file search paths (by priority)
    _CONFIG_SEARCH_PATHS = [
        "llm_config.yaml",
        "llm_config.yml",
        "config/llm_config.yaml",
        "config/llm_config.yml",
    ]

    # 以下划线开头的键是元配置，不是角色名 / Underscore-prefixed keys are meta-config, not roles
    _META_KEYS = {"_default", "_degradation"}

    def __init__(
        self,
        llm_config: Optional[Dict[str, Any]] = None,
        config_file: Optional[str] = None,
    ):
        """初始化配置加载器。 / Initialize config loader.

        Args:
            llm_config: 代码传入的配置字典（最高优先级）。 / Code-level config dict (highest priority).
            config_file: 配置文件路径（不传则自动搜索）。 / Config file path (auto-search if omitted).
        """
        self._code_config = llm_config or {}
        self._file_config: Dict[str, Any] = {}

        # 加载配置文件 / Load config file
        self._load_config_file(config_file)

    def _load_config_file(self, config_file: Optional[str]) -> None:
        """加载配置文件（YAML）。 / Load config file (YAML)."""
        if config_file:
            path = Path(config_file)
            if path.exists():
                self._file_config = self._read_yaml(path)
                logger.info("LLM 配置文件已加载: %s", path)
                return
            else:
                logger.warning("指定的 LLM 配置文件不存在: %s", path)
                return

        # 自动搜索默认路径 / Auto-search default paths
        for search_path in self._CONFIG_SEARCH_PATHS:
            path = Path(search_path)
            if path.exists():
                self._file_config = self._read_yaml(path)
                logger.info("自动发现 LLM 配置文件: %s", path)
                return

        logger.debug("未发现 LLM 配置文件，将依赖代码配置")

    @staticmethod
    def _read_yaml(path: Path) -> Dict[str, Any]:
        """读取 YAML 文件并展开环境变量引用。 / Read YAML and expand env var refs."""
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # 递归展开 ${ENV_VAR} 引用 / Recursively expand ${ENV_VAR} refs
        return _expand_env_vars(raw)

    def resolve(self, role: str) -> ModelEndpointConfig:
        """解析指定角色的完整模型配置。 / Resolve full model config for a role.

        合并策略（按优先级覆盖） / Merge strategy (by priority):
        1. 角色级代码配置（最高） / Role-level code config (highest)
        2. 全局代码默认 (_default) / Global code default
        3. 角色级文件配置 / Role-level file config
        4. 全局文件默认 (_default) / Global file default

        四层合并后 model_name 仍为空则抛出 ConfigurationError。
        / Raises ConfigurationError if model_name is still empty after merging.
        api_key / url 允许缺失（部分适配器可从环境变量读取）。
        / api_key / url may be absent (some adapters read from env vars).

        Raises:
            ConfigurationError: 角色的模型配置缺失或不完整。 / Role config missing or incomplete.
        """
        from ripple.llm.router import ConfigurationError

        merged: Dict[str, Any] = {}

        # 第 4 层：文件全局默认 / Layer 4: file global default
        file_default = self._file_config.get("_default", {})
        if isinstance(file_default, dict):
            merged.update(
                {k: v for k, v in file_default.items() if v is not None}
            )

        # 第 3 层：文件角色级配置 / Layer 3: file role-level config
        file_role = self._file_config.get(role, {})
        if isinstance(file_role, str):
            merged["model_name"] = file_role
            merged["model_platform"] = _infer_platform(file_role)
        elif isinstance(file_role, dict):
            merged.update(
                {k: v for k, v in file_role.items() if v is not None}
            )

        # 第 2 层：代码全局默认 / Layer 2: code global default
        code_default = self._code_config.get("_default", {})
        if isinstance(code_default, dict):
            merged.update(
                {k: v for k, v in code_default.items() if v is not None}
            )

        # 第 1 层：代码角色级配置（最高优先级） / Layer 1: code role-level (highest)
        code_role = self._code_config.get(role, {})
        if isinstance(code_role, str):
            merged["model_name"] = code_role
            merged["model_platform"] = _infer_platform(code_role)
        elif isinstance(code_role, dict):
            merged.update(
                {k: v for k, v in code_role.items() if v is not None}
            )

        # 校验：model_name 必须存在且非空 / Validate: model_name must be present
        # 兼容旧格式 / Legacy compat: model_name > model_type > model
        model_name = (
            merged.get("model_name")
            or merged.get("model_type")
            or merged.get("model", "")
        )
        if not model_name:
            hint = ""
            if role in _KNOWN_ROLES:
                hint = (
                    f"\n提示：'{role}' 是 Ripple 引擎的已知角色，"
                    f"请在 llm_config 参数、llm_config.yaml 配置文件"
                    f"或 _default 全局配置中为其指定模型。"
                )
            raise ConfigurationError(
                f"角色 '{role}' 的 LLM 模型配置缺失：未找到 model_name。"
                f"已搜索：代码传入 llm_config['{role}']、"
                f"配置文件 '{role}' 节、"
                f"_default 全局配置。{hint}"
            )
        # 统一归一化为 model_name（供 from_dict 使用） / Normalize to model_name for from_dict
        merged["model_name"] = model_name

        return ModelEndpointConfig.from_dict(merged)

    def has_role(self, role: str) -> bool:
        """检查指定角色是否有配置（角色级或可通过 _default 继承）。 / Check if role has config (direct or via _default)."""
        # 角色级直接配置 / Direct role-level config
        if role in self._code_config or role in self._file_config:
            return True
        # _default 全局配置中包含 model_name / _default has model_name
        for cfg in (self._code_config, self._file_config):
            default = cfg.get("_default", {})
            if isinstance(default, dict) and (
                default.get("model_name")
                or default.get("model_type")
                or default.get("model")
            ):
                return True
        return False

    def get_degradation(self, role: str) -> Optional[str]:
        """获取角色的降级模型名称。 / Get degraded model name for a role.

        从 _degradation 配置中查找。代码配置优先于文件配置。
        / Looks up _degradation config. Code config takes priority over file.
        未配置时返回 None。 / Returns None if not configured.
        """
        # 代码传入的降级映射（最高优先级） / Code-level degradation mapping (highest)
        code_deg = self._code_config.get("_degradation", {})
        if isinstance(code_deg, dict) and role in code_deg:
            return str(code_deg[role])

        # 文件配置的降级映射 / File-level degradation mapping
        file_deg = self._file_config.get("_degradation", {})
        if isinstance(file_deg, dict) and role in file_deg:
            return str(file_deg[role])

        return None

    def all_configured_roles(self) -> List[str]:
        """返回所有已配置的角色名列表（不含 _ 开头的元配置键）。 / List all configured role names (excluding _ meta keys)."""
        roles = set()
        for cfg in (self._code_config, self._file_config):
            roles.update(
                k for k in cfg.keys() if not k.startswith("_")
            )
        return sorted(roles)

    def resolve_all(
        self, roles: Optional[List[str]] = None
    ) -> Dict[str, ModelEndpointConfig]:
        """批量解析多个角色的配置。 / Batch-resolve configs for multiple roles.

        如果不传 roles，自动收集所有已配置的角色。 / Auto-collects all configured roles if omitted.
        """
        if roles is None:
            roles = self.all_configured_roles()

        result = {}
        for role in roles:
            try:
                result[role] = self.resolve(role)
            except Exception:
                # summary 场景下跳过无法解析的角色 / Skip unresolvable roles in summary
                pass
        return result

    def summary(self) -> Dict[str, Dict[str, str]]:
        """输出配置摘要（隐藏 API Key），用于日志/调试。 / Output config summary (API key masked) for logging/debug."""
        all_configs = self.resolve_all()
        result = {}
        for role, cfg in all_configs.items():
            result[role] = {
                "platform": cfg.model_platform,
                "model": cfg.model_name,
                "url": cfg.url or "(auto)",
                "api_key": _mask_key(cfg.api_key),
                "temperature": str(cfg.temperature),
            }
        return result


# =============================================================================
# 工具函数 / Utility Functions
# =============================================================================


def _expand_env_vars(obj: Any) -> Any:
    """递归展开字典/列表中的 ${ENV_VAR} 引用。 / Recursively expand ${ENV_VAR} refs in dicts/lists.

    支持格式 / Supported formats:
    - ${VAR_NAME}          → os.environ["VAR_NAME"]
    - ${VAR_NAME:-default} → os.environ.get("VAR_NAME", "default")
    """
    if isinstance(obj, str):
        import re

        def _replace(match):
            var_expr = match.group(1)
            if ":-" in var_expr:
                var_name, default = var_expr.split(":-", 1)
                return os.environ.get(var_name.strip(), default.strip())
            return os.environ.get(var_expr.strip(), match.group(0))

        return re.sub(r"\$\{([^}]+)\}", _replace, obj)

    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _normalize_base_url(url: str) -> str:
    """将 URL 规范化为基础地址，剥离已知的 API 路径后缀。
    / Normalize URL to base address, stripping known API path suffixes.

    某些 SDK 会自动追加 /chat/completions 等路径，需自动剥离避免路径重复。
    / Some SDKs auto-append paths; strip to avoid duplication.

    已知剥离后缀 / Known suffixes stripped:
    /chat/completions, /completions, /responses, /embeddings

    示例 / Examples:
    - ".../api/v3/chat/completions" → ".../api/v3"
    - ".../v1" → ".../v1" (unchanged)
    """
    # 按从长到短的顺序尝试剥离 / Try stripping longest suffix first
    _API_PATH_SUFFIXES = [
        "/chat/completions",
        "/completions",
        "/responses",
        "/embeddings",
    ]

    normalized = url.rstrip("/")
    for suffix in _API_PATH_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    return normalized


def _mask_key(key: Optional[str]) -> str:
    """遮蔽 API Key，仅显示前 8 位和后 4 位。 / Mask API key, showing only first 8 and last 4 chars."""
    if not key:
        return "(env)"
    if len(key) <= 12:
        return key[:3] + "***"
    return key[:8] + "..." + key[-4:]
