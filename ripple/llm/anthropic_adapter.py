# anthropic_adapter.py
# =============================================================================
# Anthropic Messages API 适配器 / Anthropic Messages API adapter
#
# 职责 / Responsibilities:
#   - 将 Ripple 的 (system_prompt, user_message) 转换为 Anthropic Messages API 请求
#     / Convert Ripple's calls to Anthropic Messages API HTTP requests
#   - 解析返回结构并提取文本内容 / Parse response and extract text
#   - 直连 / Direct connection: https://api.anthropic.com/v1/messages
#
# 请求格式 / Request format (Anthropic Messages API):
#   {"model": "...", "max_tokens": 4096, "system": "...", "messages": [...]}
#   → response["content"][0]["text"]
#
# 认证方式 / Auth: x-api-key + anthropic-version: 2023-06-01
# =============================================================================

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Anthropic API 默认端点 / Default Anthropic API endpoint
_DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Anthropic API 版本 / Anthropic API version
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter:
    """Anthropic Messages API 适配器。
    / Anthropic Messages API adapter.

    通过 httpx 异步 HTTP 直连调用 Anthropic Messages API。
    / Async HTTP calls via httpx to Anthropic Messages API.
    将 Ripple 标准调用格式转换为 Messages API 请求结构。
    / Converts Ripple's (system_prompt, user_message) to Messages API format.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """初始化适配器。 / Initialize adapter.

        Args:
            api_key: Anthropic API 密钥。 / Anthropic API key.
            model: 模型名称。 / Model name (e.g. "claude-sonnet-4-20250514").
            url: API 端点 URL（可选，默认官方端点）。 / Endpoint URL (optional, defaults to official).
            temperature: 生成温度。 / Generation temperature.
            max_tokens: 最大输出 token 数。 / Max output tokens.
            timeout: 请求超时时间（秒）。 / Request timeout in seconds.
            max_retries: 最大重试次数。 / Max retry count.
        """
        self._endpoint = self._resolve_endpoint(url)
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

    async def call(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """调用 Anthropic Messages API 并返回文本响应。 / Call Anthropic Messages API and return text.

        Args:
            system_prompt: 系统提示词。 / System prompt.
            user_message: 用户消息。 / User message.

        Returns:
            模型输出的文本内容。 / Model output text.

        Raises:
            httpx.HTTPStatusError: HTTP 请求失败。 / HTTP request failed.
            ValueError: 响应格式无法解析。 / Unparseable response format.
        """
        request_body = self._build_request(system_prompt, user_message)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout
                ) as client:
                    response = await client.post(
                        self._endpoint,
                        headers=headers,
                        json=request_body,
                    )
                    response.raise_for_status()
                    result = response.json()
                    return self._extract_text(result)

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    "Anthropic Messages API 调用失败 (HTTP %d)，第 %d/%d 次: %s",
                    e.response.status_code,
                    attempt + 1,
                    self._max_retries + 1,
                    e.response.text[:200],
                )
            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "Anthropic Messages API 请求异常，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Anthropic Messages API 未知异常，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )

        raise RuntimeError(
            f"Anthropic Messages API 调用在 {self._max_retries + 1} 次尝试后仍失败: "
            f"{last_error}"
        )

    # =========================================================================
    # URL 解析 / URL Resolution
    # =========================================================================

    @staticmethod
    def _resolve_endpoint(url: Optional[str]) -> str:
        """解析端点 URL。 / Resolve endpoint URL.

        如果 url 为空则使用默认端点；路径中不含 /messages 则自动追加。
        / Uses default endpoint if empty; auto-appends /messages if missing.
        """
        if not url:
            return _DEFAULT_ANTHROPIC_URL

        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        path = parsed.path
        if "/messages" not in path:
            path = path.rstrip("/") + "/messages"
        return urlunparse(parsed._replace(path=path))

    # =========================================================================
    # 请求构建与响应解析 / Request Building & Response Parsing
    # =========================================================================

    def _build_request(
        self, system_prompt: str, user_message: str
    ) -> Dict[str, Any]:
        """构建 Anthropic Messages API 请求体。 / Build Anthropic Messages API request body."""
        body: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": user_message}],
            "temperature": self._temperature,
        }

        if system_prompt:
            body["system"] = system_prompt

        return body

    @staticmethod
    def _extract_text(response_data: Dict[str, Any]) -> str:
        """从 Anthropic Messages API 响应中提取文本内容。 / Extract text from Anthropic Messages response.

        标准格式 / Standard: response["content"][0]["text"]
        """
        content = response_data.get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
            # 如果没有找到 type=text，取第一个 block 的 text / Fallback: first block's text
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                return first["text"]

        logger.warning(
            "Anthropic Messages API 响应中未找到文本内容: %s",
            json.dumps(response_data, ensure_ascii=False)[:300],
        )
        return ""

    @classmethod
    def from_endpoint_config(cls, config) -> AnthropicAdapter:
        """从 ModelEndpointConfig 创建适配器实例。 / Create adapter from ModelEndpointConfig.

        Args:
            config: ModelEndpointConfig 实例。 / ModelEndpointConfig instance.

        Returns:
            AnthropicAdapter 实例。 / AnthropicAdapter instance.

        Raises:
            ValueError: 缺少必要的配置（api_key）。 / Missing required config (api_key).
        """
        if not config.api_key:
            raise ValueError(
                f"Anthropic API 模式需要显式配置 api_key，"
                f"但角色的 api_key 为空。"
                f"请在 llm_config 中设置 api_key 或通过环境变量 "
                f"ANTHROPIC_API_KEY 提供。"
            )

        return cls(
            api_key=config.api_key,
            model=config.model_name,
            url=config.url,
            temperature=config.temperature,
            max_tokens=config.max_tokens or 4096,
            timeout=config.timeout or 120.0,
            max_retries=config.max_retries,
        )
