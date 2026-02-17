# router.py
# =============================================================================
# LLM 模型路由与调用次数控制模块 / LLM model routing & call budget control
#
# 职责 / Responsibilities:
#   - 根据 Agent 角色选择 LLM 适配器 / Select adapter by agent role
#     (ChatCompletions / Responses / Anthropic / Bedrock)
#   - 管理 LLM 调用次数上限 / Manage per-simulation LLM call budget
#   - 调用次数接近上限时触发模型降级 / Trigger model degradation near budget limit
#
# 配置优先级（高→低） / Config priority (high→low):
#   1. 代码传入 llm_config 字典 / Code-level llm_config dict
#   2. 配置文件 llm_config.yaml / Config file
#   3. 环境变量 / Env vars (${VAR} in YAML)
#
# 不提供硬编码默认模型，配置缺失时抛出 ConfigurationError。
# / No hardcoded defaults; raises ConfigurationError on missing config.
#
# 注意：BudgetState 在此模块内完整定义。primitives/models.py 中仅为占位。
# / Note: BudgetState is fully defined here; primitives/models.py is a placeholder.
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 异常 / Exceptions
# =============================================================================


class ConfigurationError(Exception):
    """LLM 配置缺失或不完整时抛出的异常。 / Raised when LLM config is missing or incomplete."""
    pass


# =============================================================================
# 调用次数预算 / Call Budget
# =============================================================================


@dataclass
class BudgetState:
    """LLM 调用次数预算状态。 / LLM call budget state.

    以"总调用次数"作为唯一限制维度——简单、直观、可预测。
    / Total call count as the sole budget dimension — simple & predictable.
    每次完整模拟共享同一个 BudgetState 实例。max_calls <= 0 表示不限制。
    / Shared per simulation run. max_calls <= 0 means unlimited.
    """

    total_calls: int = 0
    max_calls: int = 200  # <= 0 表示不限制 / <= 0 means unlimited
    calls_by_role: Dict[str, int] = field(default_factory=dict)
    # [P1-2] 调用尝试计数 — 包含失败请求，用于成本审计 / Attempt count including failures, for cost audit
    total_attempts: int = 0
    attempts_by_role: Dict[str, int] = field(default_factory=dict)

    @property
    def is_unlimited(self) -> bool:
        """是否为不限制模式。 / Whether in unlimited mode."""
        return self.max_calls <= 0

    @property
    def is_exceeded(self) -> bool:
        """调用次数是否已超限。不限制模式下永远返回 False。 / Whether budget exceeded. Always False in unlimited mode."""
        if self.is_unlimited:
            return False
        return self.total_calls >= self.max_calls

    @property
    def remaining(self) -> int:
        """剩余可用调用次数。不限制模式下返回 -1。 / Remaining calls. Returns -1 in unlimited mode."""
        if self.is_unlimited:
            return -1
        return max(0, self.max_calls - self.total_calls)

    @property
    def usage_ratio(self) -> float:
        """当前用量占比（0.0 ~ 1.0+）。不限制模式下返回 0.0。 / Usage ratio (0.0~1.0+). Returns 0.0 in unlimited mode."""
        if self.is_unlimited:
            return 0.0
        return self.total_calls / self.max_calls

    def record_attempt(self, role: str) -> None:
        """记录一次 LLM 调用尝试（无论成功或失败）。 / Record an LLM call attempt (success or failure).

        [P1-2] 在发起 HTTP 请求前调用，用于成本审计。
        / Called before HTTP request; for cost audit even on failures.
        """
        self.total_attempts += 1
        self.attempts_by_role[role] = self.attempts_by_role.get(role, 0) + 1

    def record_call(self, role: str) -> None:
        """记录一次 LLM 调用成功。 / Record a successful LLM call."""
        self.total_calls += 1
        self.calls_by_role[role] = self.calls_by_role.get(role, 0) + 1

    def to_dict(self) -> dict:
        """序列化为字典，便于持久化或审计。 / Serialize to dict for persistence/audit."""
        return {
            "total_calls": self.total_calls,
            "total_attempts": self.total_attempts,
            "max_calls": self.max_calls,
            "unlimited": self.is_unlimited,
            "remaining": self.remaining,
            "calls_by_role": dict(self.calls_by_role),
            "attempts_by_role": dict(self.attempts_by_role),
        }


# =============================================================================
# 模型路由器 / Model Router
# =============================================================================


class ModelRouter:
    """模型路由器 — 根据角色选择适配器，管理调用次数。
    / Model router — selects adapter by role, manages call budget.

    核心能力 / Core capabilities:
    - 三层优先级配置（代码 > 文件 > 环境变量） / Three-tier priority config
    - 根据 api_mode 创建适配器，统一 async call() 接口
      / Creates adapter by api_mode; uniform async call() interface
    - 适配器按角色缓存 / Adapters cached by role
    - 超 80% 上限自动降级，超 100% 禁止调用
      / Auto-degrades at 80% budget; blocks at 100%
    - max_llm_calls <= 0 表示不限制 / <= 0 means unlimited
    """

    def __init__(
        self,
        llm_config: Optional[Dict[str, Any]] = None,
        max_llm_calls: int = 200,
        config_file: Optional[str] = None,
    ) -> None:
        """初始化路由器。 / Initialize router.

        Args:
            llm_config: 用户自定义模型配置字典（最高优先级）。
                / User config dict (highest priority). Supports shorthand & full format.
                - 简写 / Shorthand: {"star": "gpt-4o", "sea": "claude-haiku"}
                - 完整 / Full: {"star": {"model_platform": "openai", "model_name": "gpt-4o", ...}}
                可选 _degradation 键定义降级映射。
                / Optional _degradation key for degradation mapping.
            max_llm_calls: 单次模拟的 LLM 调用总次数上限，<= 0 不限制。
                / Max LLM calls per simulation. <= 0 means unlimited.
            config_file: LLM 配置文件路径（可选，不传则自动搜索）。
                / Config file path (optional, auto-search if omitted).
        """
        from ripple.llm.config import LLMConfigLoader

        self._config_loader = LLMConfigLoader(
            llm_config=llm_config, config_file=config_file
        )
        self._budget = BudgetState(max_calls=max_llm_calls)

        # 适配器缓存：角色 → adapter 实例 / Adapter cache: role → adapter instance
        self._model_cache: Dict[str, Any] = {}

        # 启动时输出配置摘要（隐藏 API Key） / Log config summary at startup (key masked)
        summary = self._config_loader.summary()
        for role, info in summary.items():
            logger.info(
                "模型路由: %s → %s/%s (url=%s, key=%s)",
                role,
                info["platform"],
                info["model"],
                info["url"],
                info["api_key"],
            )
        if self._budget.is_unlimited:
            logger.info("LLM 调用次数: 不限制")
        else:
            logger.info("LLM 调用次数上限: %d", max_llm_calls)

    @property
    def budget(self) -> BudgetState:
        """当前预算状态。 / Current budget state."""
        return self._budget

    @property
    def config_loader(self) -> Any:
        """配置加载器（供外部检查配置使用）。 / Config loader (for external inspection)."""
        return self._config_loader

    # =========================================================================
    # 模型选择 / Model Selection
    # =========================================================================

    def get_model(self, role: str) -> str:
        """获取角色对应的模型名称。 / Get model name for a role.

        如果当前应降级且该角色有降级配置，返回降级后的模型名称。
        / Returns degraded model name if budget degradation is active.
        角色配置不存在时抛出 ConfigurationError。 / Raises ConfigurationError if role config missing.
        """
        if self.should_degrade():
            degraded = self._get_degraded_model(role)
            if degraded is not None:
                logger.info(
                    "调用次数接近上限 (%d/%d)，角色 '%s' 降级到 %s",
                    self._budget.total_calls,
                    self._budget.max_calls,
                    role,
                    degraded,
                )
                return degraded

        # 从配置解析（缺失会抛出 ConfigurationError） / Resolve from config (raises on missing)
        config = self._config_loader.resolve(role)
        return config.model_name

    def get_endpoint_config(self, role: str):
        """获取角色的 ModelEndpointConfig 对象。 / Get ModelEndpointConfig for a role."""
        return self._config_loader.resolve(role)

    # =========================================================================
    # 适配器管理 / Adapter Management
    # =========================================================================

    def get_api_mode(self, role: str) -> str:
        """获取角色配置的 API 模式。 / Get API mode for a role.

        Returns:
            "chat_completions", "responses", "anthropic", or "bedrock".
        """
        config = self._config_loader.resolve(role)
        return config.api_mode

    def get_model_backend(self, role: str) -> Any:
        """获取角色对应的 LLM 适配器实例（带缓存）。
        / Get cached LLM adapter instance for a role.

        根据 api_mode 自动选择适配器 / Auto-selects adapter by api_mode:
        - "chat_completions": ChatCompletionsAdapter (httpx)
        - "responses": ResponsesAPIAdapter (httpx)
        - "anthropic": AnthropicAdapter (httpx)
        - "bedrock": BedrockAdapter (boto3)

        统一接口 / Uniform interface: async call(system_prompt, user_message) -> str

        Returns:
            对应的 adapter 实例。 / The adapter instance.

        Raises:
            ConfigurationError: 角色配置缺失或不完整。 / Role config missing or incomplete.
        """
        # 降级检查 / Degradation check
        degraded_model = None
        if self.should_degrade():
            degraded_model = self._get_degraded_model(role)

        cache_key = f"_degraded_{role}" if degraded_model else role

        # 降级切换时清除原缓存 / Clear original cache on degradation switch
        if degraded_model and role in self._model_cache:
            del self._model_cache[role]

        # 缓存命中 / Cache hit
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        # 获取角色的端点配置 / Get endpoint config for role
        config = self._config_loader.resolve(role)

        # 降级时替换模型名，保留连接配置 / Replace model name on degradation, keep connection config
        if degraded_model:
            from dataclasses import replace
            config = replace(config, model_name=degraded_model)

        # 根据 api_mode 创建对应的适配器 / Create adapter by api_mode
        adapter = self._create_adapter(config)

        self._model_cache[cache_key] = adapter
        logger.info(
            "LLM 适配器已创建: role=%s, api_mode=%s, model=%s, url=%s",
            role,
            config.api_mode,
            config.model_name,
            config.url or "(default)",
        )
        return adapter

    @staticmethod
    def _create_adapter(config) -> Any:
        """根据 api_mode 创建对应的 LLM 适配器。 / Create LLM adapter by api_mode."""
        if config.api_mode == "responses":
            from ripple.llm.responses_adapter import ResponsesAPIAdapter
            return ResponsesAPIAdapter.from_endpoint_config(config)

        if config.api_mode == "chat_completions":
            from ripple.llm.chat_completions_adapter import (
                ChatCompletionsAdapter,
            )
            return ChatCompletionsAdapter.from_endpoint_config(config)

        if config.api_mode == "anthropic":
            from ripple.llm.anthropic_adapter import AnthropicAdapter
            return AnthropicAdapter.from_endpoint_config(config)

        if config.api_mode == "bedrock":
            from ripple.llm.bedrock_adapter import BedrockAdapter
            return BedrockAdapter.from_endpoint_config(config)

        raise ConfigurationError(
            f"不支持的 api_mode: '{config.api_mode}'。"
            f"仅支持: chat_completions, responses, anthropic, bedrock。"
        )

    def clear_model_cache(self) -> None:
        """清除所有缓存的适配器。 / Clear all cached adapters."""
        self._model_cache.clear()

    # =========================================================================
    # 调用次数控制 / Call Budget Control
    # =========================================================================

    def check_budget(self, role: str) -> bool:
        """检查调用次数是否允许此角色继续调用。 / Check if budget allows this role to proceed."""
        if self._budget.is_exceeded:
            logger.warning(
                "LLM 调用次数已达上限 (%d/%d)",
                self._budget.total_calls,
                self._budget.max_calls,
            )
            return False
        return True

    def record_attempt(self, role: str) -> None:
        """记录一次 LLM 调用尝试（含失败）。 / Record an LLM call attempt (incl. failures)."""
        self._budget.record_attempt(role)

    def record_call(self, role: str) -> None:
        """记录一次 LLM 调用成功。 / Record a successful LLM call."""
        self._budget.record_call(role)

    def should_degrade(self) -> bool:
        """判断是否应该触发降级。 / Whether model degradation should be triggered.

        超过上限 80% 时建议降级，不限制模式下永远不触发。
        / Suggests degradation at 80% budget. Never triggers in unlimited mode.
        """
        if self._budget.is_unlimited:
            return False
        return self._budget.usage_ratio >= 0.8

    # =========================================================================
    # 降级（从配置获取） / Degradation (from config)
    # =========================================================================

    def _get_degraded_model(self, role: str) -> Optional[str]:
        """从配置中查找角色的降级模型。 / Look up degraded model for a role from config.

        降级映射通过 _degradation 键在配置中定义。
        / Defined via _degradation key in code config or config file.
        未配置时返回 None（不降级）。 / Returns None if not configured (no degradation).
        """
        return self._config_loader.get_degradation(role)
