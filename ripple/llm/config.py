# config.py
# =============================================================================
# LLM 配置加载与合并模块
#
# 职责：
#   - 定义 LLM 模型配置的数据结构（ModelEndpointConfig）
#   - 实现三层优先级配置加载：代码传入 > 配置文件 > 环境变量
#   - 为 ModelRouter 提供解析后的完整配置，供各 adapter 使用
#   - 配置缺失时抛出 ConfigurationError，不提供任何硬编码默认值
#
# 设计依据：
#   - 各 LLM 适配器（ChatCompletionsAdapter / ResponsesAPIAdapter /
#     AnthropicAdapter / BedrockAdapter）通过 from_endpoint_config()
#     从 ModelEndpointConfig 创建实例
#   - 不同 Provider 的 API 格式由各适配器独立处理
#   - 本模块只负责"配置从哪来"和"怎么合并"
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
# 数据结构
# =============================================================================


@dataclass
class ModelEndpointConfig:
    """单个模型端点的完整配置。

    对应一个 Agent 角色（omniscient / star / sea 等）的模型配置。
    各 LLM 适配器通过 from_endpoint_config() 读取本配置创建实例。
    """

    # --- 必填：模型标识 ---
    model_platform: str  # 平台标识: "openai" / "anthropic" / "bedrock" / "deepseek" 等
    model_name: str  # 模型名称: "gpt-4o" / "claude-opus-4-6" 等

    # --- 可选：连接信息 ---
    api_key: Optional[str] = None  # API 密钥
    url: Optional[str] = None  # 自定义 endpoint URL（代理网关、私有化部署等）

    # --- 可选：API 模式 ---
    # "chat_completions" — 标准 OpenAI Chat Completions 格式（默认）
    # "responses"        — OpenAI Responses API 格式
    # "anthropic"        — Anthropic Messages API 格式
    # "bedrock"          — AWS Bedrock InvokeModel 格式
    api_mode: str = "chat_completions"

    # --- 可选：模型行为参数 ---
    temperature: float = 0.7
    # 中文注释：给出安全默认上限，避免第三方 SDK 回退到异常超大值。
    max_tokens: Optional[int] = 4096
    timeout: Optional[float] = None
    max_retries: int = 3

    # --- 可选：Azure 专用 ---
    api_version: Optional[str] = None
    azure_deployment_name: Optional[str] = None

    # --- 可选：额外参数（透传给适配器） ---
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelEndpointConfig:
        """从字典构建配置。

        支持两种格式：
        1. 简写格式（仅模型名字符串）: "claude-opus-4-6"
        2. 完整格式（字典）: {"model_platform": "anthropic", "model_name": "claude-opus-4-6", ...}

        简写格式时自动推断 model_platform。

        字段优先级：model_name > model_type（向后兼容） > model（旧格式兼容）
        """
        if isinstance(data, str):
            # 简写格式：仅模型名
            platform = _infer_platform(data)
            return cls(model_platform=platform, model_name=data)

        # 完整格式：从字典提取字段
        # 优先级：model_name > model_type（向后兼容） > model（旧格式兼容）
        model_name = (
            data.get("model_name")
            or data.get("model_type")
            or data.get("model", "")
        )
        model_platform = data.get("model_platform") or _infer_platform(
            model_name
        )

        # api_mode：显式指定或自动推断
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

        # 已识别字段集合
        _known_keys = {
            "model",
            "model_name",
            "model_type",  # 向后兼容
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
# 平台推断
# =============================================================================

# 模型名称 → 平台映射规则（按前缀/关键词匹配）
_PLATFORM_INFERENCE_RULES: List[tuple] = [
    # Anthropic 系列
    (["claude"], "anthropic"),
    # OpenAI 系列
    (["gpt-", "o1-", "o3-", "chatgpt"], "openai"),
    # 其他常见平台（后续可扩展）
    (["gemini"], "google"),
    (["deepseek"], "deepseek"),
    (["qwen", "qwq"], "qwen"),
    (["llama", "meta-llama"], "ollama"),
]


def _infer_platform(model_name: str) -> str:
    """根据模型名称推断 model_platform。

    未能推断时返回 "openai"（最通用的 fallback）。
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
# API 模式推断
# =============================================================================


def _infer_api_mode(platform: str, url: Optional[str] = None) -> str:
    """根据 platform 和 url 自动推断 api_mode。

    推断规则（按优先级）：
    1. platform == "anthropic" 且无自定义 URL -> "anthropic"
    2. platform == "bedrock" -> "bedrock"
    3. URL 包含 /responses -> "responses"
    4. 其他 -> "chat_completions"
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
# 保留的角色名列表（用于错误提示）
# =============================================================================

# 这些是 Ripple 引擎使用的已知角色名。不作为默认值，仅用于在配置缺失时
# 给出更友好的错误提示。
_KNOWN_ROLES = [
    "omniscient",
    "star",
    "sea",
]


# =============================================================================
# 配置加载器
# =============================================================================


class LLMConfigLoader:
    """LLM 配置加载器 — 实现三层优先级配置合并。

    优先级（高→低）：
    1. 代码传入（llm_config 字典参数）
    2. 配置文件（YAML / JSON）
    3. 环境变量（通过 ${VAR} 在 YAML 中引用）

    不提供任何硬编码默认模型。如果某个角色既没有角色级配置，也没有
    _default 全局配置可继承，resolve() 会抛出 ConfigurationError。

    llm_config 字典格式：
    {
        # 全局默认 — 所有角色的基础配置
        "_default": {
            "model_platform": "anthropic",
            "model_name": "claude-sonnet-4-20250514",
            "api_key": "sk-ant-xxx",
        },
        # 角色级配置（覆盖全局默认的相应字段）
        "star": {
            "model_name": "claude-opus-4-6",
            "temperature": 0.8,
        },
        "sea": "claude-haiku",  # 简写格式：仅模型名
        # 降级映射（可选）
        "_degradation": {
            "star": "claude-sonnet-4-20250514",
            "omniscient": "claude-sonnet-4-20250514",
        },
    }
    """

    # 配置文件搜索路径（按优先级）
    _CONFIG_SEARCH_PATHS = [
        "llm_config.yaml",
        "llm_config.yml",
        "config/llm_config.yaml",
        "config/llm_config.yml",
    ]

    # 以下划线开头的键是元配置，不是角色名
    _META_KEYS = {"_default", "_degradation"}

    def __init__(
        self,
        llm_config: Optional[Dict[str, Any]] = None,
        config_file: Optional[str] = None,
    ):
        """初始化配置加载器。

        Args:
            llm_config: 代码传入的配置字典（最高优先级）。
            config_file: 配置文件路径（如果不传，自动搜索默认路径）。
        """
        self._code_config = llm_config or {}
        self._file_config: Dict[str, Any] = {}

        # 加载配置文件
        self._load_config_file(config_file)

    def _load_config_file(self, config_file: Optional[str]) -> None:
        """加载配置文件（YAML）。"""
        if config_file:
            path = Path(config_file)
            if path.exists():
                self._file_config = self._read_yaml(path)
                logger.info("LLM 配置文件已加载: %s", path)
                return
            else:
                logger.warning("指定的 LLM 配置文件不存在: %s", path)
                return

        # 自动搜索默认路径
        for search_path in self._CONFIG_SEARCH_PATHS:
            path = Path(search_path)
            if path.exists():
                self._file_config = self._read_yaml(path)
                logger.info("自动发现 LLM 配置文件: %s", path)
                return

        logger.debug("未发现 LLM 配置文件，将依赖代码配置")

    @staticmethod
    def _read_yaml(path: Path) -> Dict[str, Any]:
        """读取 YAML 文件并展开环境变量引用。"""
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # 递归展开 ${ENV_VAR} 引用
        return _expand_env_vars(raw)

    def resolve(self, role: str) -> ModelEndpointConfig:
        """解析指定角色的完整模型配置。

        合并策略（按优先级覆盖）：
        1. 角色级代码配置     （最高）
        2. 全局代码默认 (_default)
        3. 角色级文件配置
        4. 全局文件默认 (_default)

        不提供内置硬编码默认。如果经过以上四层合并后，model_name
        仍然为空，说明配置不完整，抛出 ConfigurationError。

        api_key / url 允许缺失（部分适配器可从环境变量读取）。

        Raises:
            ConfigurationError: 角色的模型配置缺失或不完整。
        """
        from ripple.llm.router import ConfigurationError

        merged: Dict[str, Any] = {}

        # 第 4 层：文件全局默认
        file_default = self._file_config.get("_default", {})
        if isinstance(file_default, dict):
            merged.update(
                {k: v for k, v in file_default.items() if v is not None}
            )

        # 第 3 层：文件角色级配置
        file_role = self._file_config.get(role, {})
        if isinstance(file_role, str):
            merged["model_name"] = file_role
            merged["model_platform"] = _infer_platform(file_role)
        elif isinstance(file_role, dict):
            merged.update(
                {k: v for k, v in file_role.items() if v is not None}
            )

        # 第 2 层：代码全局默认
        code_default = self._code_config.get("_default", {})
        if isinstance(code_default, dict):
            merged.update(
                {k: v for k, v in code_default.items() if v is not None}
            )

        # 第 1 层：代码角色级配置（最高优先级）
        code_role = self._code_config.get(role, {})
        if isinstance(code_role, str):
            merged["model_name"] = code_role
            merged["model_platform"] = _infer_platform(code_role)
        elif isinstance(code_role, dict):
            merged.update(
                {k: v for k, v in code_role.items() if v is not None}
            )

        # 校验：model_name 必须存在且非空
        # 兼容旧格式：model_name > model_type > model
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
        # 统一归一化为 model_name（供 from_dict 使用）
        merged["model_name"] = model_name

        return ModelEndpointConfig.from_dict(merged)

    def has_role(self, role: str) -> bool:
        """检查指定角色是否有配置（角色级或可通过 _default 继承）。"""
        # 角色级直接配置
        if role in self._code_config or role in self._file_config:
            return True
        # _default 全局配置中包含 model_name
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
        """获取角色的降级模型名称。

        从 _degradation 配置中查找。代码配置优先于文件配置。
        未配置时返回 None。
        """
        # 代码传入的降级映射（最高优先级）
        code_deg = self._code_config.get("_degradation", {})
        if isinstance(code_deg, dict) and role in code_deg:
            return str(code_deg[role])

        # 文件配置的降级映射
        file_deg = self._file_config.get("_degradation", {})
        if isinstance(file_deg, dict) and role in file_deg:
            return str(file_deg[role])

        return None

    def all_configured_roles(self) -> List[str]:
        """返回所有已配置的角色名列表（不含 _ 开头的元配置键）。"""
        roles = set()
        for cfg in (self._code_config, self._file_config):
            roles.update(
                k for k in cfg.keys() if not k.startswith("_")
            )
        return sorted(roles)

    def resolve_all(
        self, roles: Optional[List[str]] = None
    ) -> Dict[str, ModelEndpointConfig]:
        """批量解析多个角色的配置。

        如果不传 roles，自动收集所有已配置的角色。
        """
        if roles is None:
            roles = self.all_configured_roles()

        result = {}
        for role in roles:
            try:
                result[role] = self.resolve(role)
            except Exception:
                # summary 场景下跳过无法解析的角色
                pass
        return result

    def summary(self) -> Dict[str, Dict[str, str]]:
        """输出配置摘要（隐藏 API Key），用于日志 / 调试。"""
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
# 工具函数
# =============================================================================


def _expand_env_vars(obj: Any) -> Any:
    """递归展开字典/列表中的 ${ENV_VAR} 引用。

    支持格式：
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

    某些 SDK 会自动在基础 URL 后追加 /chat/completions 等路径。
    如果用户传入了完整的端点 URL（如 .../v3/chat/completions），
    需要自动剥离这些后缀以避免路径重复。

    已知需要剥离的后缀：
    - /chat/completions
    - /completions
    - /responses
    - /embeddings

    示例：
    - "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
      → "https://ark.cn-beijing.volces.com/api/v3"
    - "https://api.openai.com/v1"
      → "https://api.openai.com/v1"（无变化）
    """
    # 按从长到短的顺序尝试剥离
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
    """遮蔽 API Key，仅显示前 8 位和后 4 位。"""
    if not key:
        return "(env)"
    if len(key) <= 12:
        return key[:3] + "***"
    return key[:8] + "..." + key[-4:]
