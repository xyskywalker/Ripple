# chat_completions_adapter.py
# =============================================================================
# OpenAI Chat Completions API 适配器 / OpenAI Chat Completions API adapter
#
# 职责 / Responsibilities:
#   - 将 Ripple 的 (system_prompt, user_message) 转换为 Chat Completions HTTP 请求
#     / Convert Ripple's (system_prompt, user_message) to Chat Completions HTTP requests
#   - 解析返回结构并提取文本内容 / Parse response and extract text content
#   - 兼容多种端点 / Compatible with multiple endpoints:
#     · 标准 OpenAI / Standard OpenAI (api.openai.com)
#     · 国内兼容端点 / CN-compatible (Volcengine, DeepSeek, Qwen, Zhipu, Moonshot, etc.)
#     · Azure OpenAI (cognitiveservices.azure.com)
#
# URL 兼容性 / URL compatibility:
#   1. 基础 URL / Base URL → 自动追加 / auto-appends /chat/completions
#   2. 完整路径 / Full path → 直接使用 / used as-is
#   3. 带 query 参数 / With query params → 保留 / preserved
#
# 认证方式 / Auth:
#   - 标准端点 / Standard: Authorization: Bearer <key>
#   - Azure 端点 / Azure: api-key: <key> (auto-detected)
#
# 请求格式 / Request format (OpenAI Chat Completions):
#   {"model": "xxx", "messages": [...]} → response["choices"][0]["message"]["content"]
# =============================================================================

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

# Azure 相关域名后缀（用于自动检测认证方式） / Azure domain suffixes for auth detection
_AZURE_DOMAIN_SUFFIXES = (
    "cognitiveservices.azure.com",
    "openai.azure.com",
    "services.ai.azure.com",
)


class ChatCompletionsAdapter:
    """OpenAI Chat Completions API 适配器。
    / OpenAI Chat Completions API adapter.

    通过 httpx 异步 HTTP 直连调用 Chat Completions API。
    / Async HTTP calls via httpx to Chat Completions API endpoints.
    将 Ripple 标准调用格式转换为 Chat Completions 请求结构。
    / Converts Ripple's (system_prompt, user_message) to Chat Completions format.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        api_version: Optional[str] = None,
    ):
        """初始化适配器。 / Initialize adapter.

        Args:
            url: API 端点 URL / API endpoint URL. Supports:
                 - 基础 URL / Base URL → auto-appends /chat/completions
                 - 完整 URL / Full URL → used as-is
                 - 带参数 URL / URL with query params → preserved
            api_key: API 密钥。 / API key.
            model: 模型名称。 / Model name (e.g. "gpt-4o").
            temperature: 生成温度。 / Generation temperature.
            max_tokens: 最大输出 token 数。 / Max output tokens.
            timeout: 请求超时时间（秒）。 / Request timeout in seconds.
            max_retries: 最大重试次数。 / Max retry count.
            api_version: Azure API 版本（可选）。 / Azure API version (optional).
        """
        self._endpoint = self._resolve_endpoint(url, api_version)
        self._is_azure = self._detect_azure(url)
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

        if self._is_azure:
            logger.info(
                "检测到 Azure 端点，将使用 api-key 认证头: %s",
                self._endpoint,
            )

    async def call(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """调用 Chat Completions API 并返回文本响应。 / Call Chat Completions API and return text.

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
        }
        if self._is_azure:
            headers["api-key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"

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
                    "Chat Completions API 调用失败 (HTTP %d)，第 %d/%d 次: %s",
                    e.response.status_code,
                    attempt + 1,
                    self._max_retries + 1,
                    e.response.text[:200],
                )
            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "Chat Completions API 请求异常，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Chat Completions API 未知异常，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )

        raise RuntimeError(
            f"Chat Completions API 调用在 {self._max_retries + 1} 次尝试后仍失败: "
            f"{last_error}"
        )

    # =========================================================================
    # URL 与认证检测 / URL & Auth Detection
    # =========================================================================

    @staticmethod
    def _resolve_endpoint(
        url: str, api_version: Optional[str] = None
    ) -> str:
        """智能解析端点 URL。 / Smartly resolve endpoint URL.

        处理逻辑 / Logic:
        1. 路径含 /chat/completions / Path has /chat/completions → 直接使用 / use as-is
        2. 路径不含 / Path missing → 追加 / append /chat/completions
        3. Azure URL 且无 api-version / Azure without api-version → 自动追加 / auto-append
        """
        parsed = urlparse(url)

        path = parsed.path
        if "/chat/completions" not in path:
            path = path.rstrip("/") + "/chat/completions"

        query_params = parse_qs(parsed.query, keep_blank_values=True)

        if api_version and "api-version" not in query_params:
            hostname = parsed.hostname or ""
            if any(hostname.endswith(d) for d in _AZURE_DOMAIN_SUFFIXES):
                query_params["api-version"] = [api_version]

        new_query = urlencode(query_params, doseq=True)

        resolved = urlunparse(
            parsed._replace(path=path, query=new_query)
        )
        return resolved

    @staticmethod
    def _detect_azure(url: str) -> bool:
        """检测 URL 是否为 Azure 端点。 / Detect if URL is an Azure endpoint."""
        hostname = urlparse(url).hostname or ""
        return any(hostname.endswith(d) for d in _AZURE_DOMAIN_SUFFIXES)

    # =========================================================================
    # 请求构建与响应解析 / Request Building & Response Parsing
    # =========================================================================

    def _build_request(
        self, system_prompt: str, user_message: str
    ) -> Dict[str, Any]:
        """构建 Chat Completions API 请求体。 / Build Chat Completions API request body."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        body: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }

        if self._max_tokens is not None:
            body["max_tokens"] = self._max_tokens

        return body

    @staticmethod
    def _extract_text(response_data: Dict[str, Any]) -> str:
        """从 Chat Completions API 响应中提取文本内容。 / Extract text from Chat Completions response.

        标准格式 / Standard: response["choices"][0]["message"]["content"]
        """
        choices = response_data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if content is not None:
                return content

        logger.warning(
            "Chat Completions API 响应中未找到文本内容: %s",
            json.dumps(response_data, ensure_ascii=False)[:300],
        )
        return ""

    @classmethod
    def from_endpoint_config(cls, config) -> ChatCompletionsAdapter:
        """从 ModelEndpointConfig 创建适配器实例。 / Create adapter from ModelEndpointConfig.

        Args:
            config: ModelEndpointConfig 实例。 / ModelEndpointConfig instance.

        Returns:
            ChatCompletionsAdapter 实例。 / ChatCompletionsAdapter instance.

        Raises:
            ValueError: 缺少必要的配置（url / api_key）。 / Missing required config (url / api_key).
        """
        if not config.url:
            raise ValueError(
                f"Chat Completions API 模式需要显式配置 url，"
                f"但角色的 url 为空。请在 llm_config 中设置 url。"
            )
        if not config.api_key:
            raise ValueError(
                f"Chat Completions API 模式需要显式配置 api_key，"
                f"但角色的 api_key 为空。"
                f"请在 llm_config 中设置 api_key 或通过环境变量提供。"
            )

        return cls(
            url=config.url,
            api_key=config.api_key,
            model=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=config.timeout or 120.0,
            max_retries=config.max_retries,
            api_version=config.api_version,
        )
