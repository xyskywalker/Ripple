# router.py
# =============================================================================
# LLM 模型路由与调用次数控制模块
#
# 职责：
#   - 根据 Agent 角色选择 LLM 适配器（ChatCompletions / Responses /
#     Anthropic / Bedrock）
#   - 管理 LLM 调用次数上限（每次完整模拟的总调用次数限制）
#   - 调用次数接近上限时触发模型降级（降级映射从配置获取）
#
# 配置优先级（高→低）：
#   1. 代码传入 llm_config 字典（最高优先级）
#   2. 配置文件 llm_config.yaml
#   3. 环境变量（通过 ${VAR} 在 YAML 中引用）
#
# 不提供任何硬编码默认模型。所有角色的模型配置必须通过以上三种方式之一提供，
# 否则在解析时抛出 ConfigurationError。
#
# 注意：BudgetState 在此模块内完整定义。primitives/models.py 中的 BudgetState
# 仅为数据模型占位，不导入本模块，两者字段需保持一致。
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 异常
# =============================================================================


class ConfigurationError(Exception):
    """LLM 配置缺失或不完整时抛出的异常。"""
    pass


# =============================================================================
# 调用次数预算
# =============================================================================


@dataclass
class BudgetState:
    """LLM 调用次数预算状态。

    以"总调用次数"作为唯一限制维度——简单、直观、可预测。
    每次完整的端到端模拟共享同一个 BudgetState 实例。

    max_calls <= 0 表示不做限制。
    """

    total_calls: int = 0
    max_calls: int = 200  # <= 0 表示不限制
    calls_by_role: Dict[str, int] = field(default_factory=dict)
    # [P1-2] 调用尝试计数 — 包含失败的 HTTP 请求，用于成本审计
    total_attempts: int = 0
    attempts_by_role: Dict[str, int] = field(default_factory=dict)

    @property
    def is_unlimited(self) -> bool:
        """是否为不限制模式。"""
        return self.max_calls <= 0

    @property
    def is_exceeded(self) -> bool:
        """调用次数是否已超限。不限制模式下永远返回 False。"""
        if self.is_unlimited:
            return False
        return self.total_calls >= self.max_calls

    @property
    def remaining(self) -> int:
        """剩余可用调用次数。不限制模式下返回 -1。"""
        if self.is_unlimited:
            return -1
        return max(0, self.max_calls - self.total_calls)

    @property
    def usage_ratio(self) -> float:
        """当前用量占比（0.0 ~ 1.0+）。不限制模式下返回 0.0。"""
        if self.is_unlimited:
            return 0.0
        return self.total_calls / self.max_calls

    def record_attempt(self, role: str) -> None:
        """记录一次 LLM 调用尝试（无论成功或失败）。

        [P1-2] 在实际发起 HTTP 请求前调用。
        用于成本审计：即使请求失败，也消耗了 API 配额和延迟。
        """
        self.total_attempts += 1
        self.attempts_by_role[role] = self.attempts_by_role.get(role, 0) + 1

    def record_call(self, role: str) -> None:
        """记录一次 LLM 调用成功。"""
        self.total_calls += 1
        self.calls_by_role[role] = self.calls_by_role.get(role, 0) + 1

    def to_dict(self) -> dict:
        """序列化为字典，便于持久化或审计。"""
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
# 模型路由器
# =============================================================================


class ModelRouter:
    """模型路由器 — 根据角色选择适配器，管理调用次数。

    核心能力：
    - 通过 LLMConfigLoader 支持三层优先级配置（代码 > 文件 > 环境变量）
    - 根据 api_mode 创建对应的 LLM 适配器（ChatCompletions / Responses /
      Anthropic / Bedrock），统一 async call() 接口
    - 适配器按角色缓存，避免重复创建
    - 调用次数超 80% 上限时自动降级（需在配置中提供 _degradation 映射），
      超 100% 时禁止调用
    - max_llm_calls <= 0 表示不限制调用次数
    """

    def __init__(
        self,
        llm_config: Optional[Dict[str, Any]] = None,
        max_llm_calls: int = 200,
        config_file: Optional[str] = None,
    ) -> None:
        """初始化路由器。

        Args:
            llm_config: 用户自定义模型配置字典（最高优先级）。格式参见
                LLMConfigLoader 文档。支持简写和完整格式：
                - 简写: {"star": "gpt-4o", "sea": "claude-haiku"}
                - 完整: {"star": {"model_platform": "openai",
                                   "model_name": "gpt-4o",
                                   "api_key": "sk-xxx"}}
                可选 _degradation 键定义降级映射：
                - {"_degradation": {"star": "claude-sonnet-4-20250514",
                                     "omniscient": "claude-sonnet-4-20250514"}}
            max_llm_calls: 单次模拟的 LLM 调用总次数上限。
                <= 0 表示不限制。
            config_file: LLM 配置文件路径（可选，不传则自动搜索）。
        """
        from ripple.llm.config import LLMConfigLoader

        self._config_loader = LLMConfigLoader(
            llm_config=llm_config, config_file=config_file
        )
        self._budget = BudgetState(max_calls=max_llm_calls)

        # 适配器缓存：角色 → adapter 实例
        self._model_cache: Dict[str, Any] = {}

        # 启动时输出配置摘要（隐藏 API Key）
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
        """当前预算状态。"""
        return self._budget

    @property
    def config_loader(self) -> Any:
        """配置加载器（供外部检查配置使用）。"""
        return self._config_loader

    # =========================================================================
    # 模型选择
    # =========================================================================

    def get_model(self, role: str) -> str:
        """获取角色对应的模型名称（model_name 字符串）。

        如果当前应降级且该角色有降级配置，返回降级后的模型名称。
        角色配置不存在时抛出 ConfigurationError。
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

        # 从配置解析（缺失会抛出 ConfigurationError）
        config = self._config_loader.resolve(role)
        return config.model_name

    def get_endpoint_config(self, role: str):
        """获取角色的 ModelEndpointConfig 对象。"""
        return self._config_loader.resolve(role)

    # =========================================================================
    # 适配器管理
    # =========================================================================

    def get_api_mode(self, role: str) -> str:
        """获取角色配置的 API 模式。

        Returns:
            "chat_completions"、"responses"、"anthropic" 或 "bedrock"。
        """
        config = self._config_loader.resolve(role)
        return config.api_mode

    def get_model_backend(self, role: str) -> Any:
        """获取角色对应的 LLM 适配器实例（带缓存）。

        根据 api_mode 自动选择适配器类型：
        - "chat_completions": ChatCompletionsAdapter（httpx 直连）
        - "responses": ResponsesAPIAdapter（httpx 直连）
        - "anthropic": AnthropicAdapter（httpx 直连）
        - "bedrock": BedrockAdapter（boto3）

        所有适配器均暴露统一接口：async call(system_prompt, user_message) -> str

        Returns:
            对应的 adapter 实例。

        Raises:
            ConfigurationError: 角色配置缺失或不完整。
        """
        # 降级检查
        degraded_model = None
        if self.should_degrade():
            degraded_model = self._get_degraded_model(role)

        cache_key = f"_degraded_{role}" if degraded_model else role

        # 降级切换时清除原缓存
        if degraded_model and role in self._model_cache:
            del self._model_cache[role]

        # 缓存命中
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        # 获取角色的端点配置
        config = self._config_loader.resolve(role)

        # 降级时替换模型名，保留连接配置
        if degraded_model:
            from dataclasses import replace
            config = replace(config, model_name=degraded_model)

        # 根据 api_mode 创建对应的适配器
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
        """根据 api_mode 创建对应的 LLM 适配器。"""
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
        """清除所有缓存的适配器。"""
        self._model_cache.clear()

    # =========================================================================
    # 调用次数控制
    # =========================================================================

    def check_budget(self, role: str) -> bool:
        """检查调用次数是否允许此角色继续调用。"""
        if self._budget.is_exceeded:
            logger.warning(
                "LLM 调用次数已达上限 (%d/%d)",
                self._budget.total_calls,
                self._budget.max_calls,
            )
            return False
        return True

    def record_attempt(self, role: str) -> None:
        """记录一次 LLM 调用尝试（含失败）。"""
        self._budget.record_attempt(role)

    def record_call(self, role: str) -> None:
        """记录一次 LLM 调用成功。"""
        self._budget.record_call(role)

    def should_degrade(self) -> bool:
        """判断是否应该触发降级。

        当调用次数超过上限的 80% 时建议降级。
        不限制模式（max_calls <= 0）下永远不触发降级。
        """
        if self._budget.is_unlimited:
            return False
        return self._budget.usage_ratio >= 0.8

    # =========================================================================
    # 降级（从配置获取）
    # =========================================================================

    def _get_degraded_model(self, role: str) -> Optional[str]:
        """从配置中查找角色的降级模型。

        降级映射通过 _degradation 键在配置中定义（代码传入或配置文件均可）：
            _degradation:
              star: claude-sonnet-4-20250514
              omniscient: claude-sonnet-4-20250514

        未配置降级映射时返回 None（不降级，使用原模型）。
        """
        return self._config_loader.get_degradation(role)
