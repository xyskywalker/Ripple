# llm/__init__.py
# 模型路由、调用次数控制、LLM 配置管理与适配器 / Model routing, call budget, LLM config & adapters

from ripple.llm.anthropic_adapter import AnthropicAdapter
from ripple.llm.chat_completions_adapter import ChatCompletionsAdapter
from ripple.llm.config import (
    LLMConfigLoader,
    ModelEndpointConfig,
)
from ripple.llm.responses_adapter import ResponsesAPIAdapter
from ripple.llm.router import (
    BudgetState,
    ConfigurationError,
    ModelRouter,
)

# BedrockAdapter 不在顶层导出（boto3 为可选依赖） / BedrockAdapter not exported at top level (boto3 is optional)

__all__ = [
    "AnthropicAdapter",
    "BudgetState",
    "ChatCompletionsAdapter",
    "ConfigurationError",
    "LLMConfigLoader",
    "ModelEndpointConfig",
    "ModelRouter",
    "ResponsesAPIAdapter",
]
