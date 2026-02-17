# responses_adapter.py
# =============================================================================
# OpenAI Responses API 适配器 / OpenAI Responses API adapter
#
# 职责 / Responsibilities:
#   - 将 Ripple 的 (system_prompt, user_message) 转换为 Responses API HTTP 请求
#     / Convert Ripple's calls to OpenAI Responses API HTTP requests
#   - 解析返回结构并提取文本内容 / Parse response and extract text
#   - 兼容多种端点 / Compatible with multiple endpoints:
#     · 标准 OpenAI / Standard OpenAI (api.openai.com)
#     · 国内兼容端点 / CN-compatible (Volcengine, etc.)
#     · Azure AI Foundry (cognitiveservices.azure.com)
#
# 背景 / Background:
#   Responses API (/responses) 使用 input 字段替代 messages，
#   内容类型为 input_text / input_image。本模块提供轻量 httpx 直连适配。
#   / Uses input field instead of messages; lightweight httpx direct adapter.
#
# URL 兼容性 / URL compatibility:
#   1. 基础 URL / Base URL → 自动追加 / auto-appends /responses
#   2. 完整路径 / Full path → 直接使用 / used as-is
#   3. 带 query 参数 / With query params → 保留 / preserved
#
# 认证方式 / Auth:
#   - 标准端点 / Standard: Authorization: Bearer <key>
#   - Azure 端点 / Azure: api-key: <key> (auto-detected)
#
# API 格式对比 / API format comparison:
#   Chat Completions: {"messages": [...]} → response.choices[0].message.content
#   Responses: {"input": [...]} → response.output_text or output[0].content[0].text
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


class ResponsesAPIAdapter:
    """OpenAI Responses API 适配器。
    / OpenAI Responses API adapter.

    通过 httpx 异步 HTTP 直连调用 Responses API。
    / Async HTTP calls via httpx to Responses API endpoints.
    将 Ripple 标准调用格式转换为 Responses API 请求结构。
    / Converts Ripple's (system_prompt, user_message) to Responses API format.
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
                 - 基础 URL / Base URL → auto-appends /responses
                 - 完整 URL / Full URL → used as-is
                 - 带参数 URL / URL with query params → preserved
            api_key: API 密钥。 / API key.
            model: 模型名称。 / Model name (e.g. "doubao-seed-1-6-flash-250828").
            temperature: 生成温度。 / Generation temperature.
            max_tokens: 最大输出 token 数。 / Max output tokens.
            timeout: 请求超时时间（秒）。 / Request timeout in seconds.
            max_retries: 最大重试次数。 / Max retry count.
            api_version: Azure API 版本（可选）。 / Azure API version (optional).
        """
        # 智能解析 URL：判断是否需追加 /responses 和 query 参数 / Smart URL resolution
        self._endpoint = self._resolve_endpoint(url, api_version)

        # 自动检测 Azure 域名以决定认证头格式 / Auto-detect Azure domain for auth header
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
        """调用 Responses API 并返回文本响应。 / Call Responses API and return text.

        Args:
            system_prompt: 系统提示词（作为 instructions）。 / System prompt (as instructions).
            user_message: 用户消息。 / User message.

        Returns:
            模型输出的文本内容。 / Model output text.

        Raises:
            httpx.HTTPStatusError: HTTP 请求失败。 / HTTP request failed.
            ValueError: 响应格式无法解析。 / Unparseable response format.
        """
        # 构建请求体 / Build request body
        request_body = self._build_request(system_prompt, user_message)

        # 请求头：Azure 用 api-key，其他用 Bearer / Headers: Azure uses api-key, others use Bearer
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._is_azure:
            headers["api-key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # 发送请求（带重试） / Send request with retries
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
                    "Responses API 调用失败 (HTTP %d)，第 %d/%d 次: %s",
                    e.response.status_code,
                    attempt + 1,
                    self._max_retries + 1,
                    e.response.text[:200],
                )
            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "Responses API 请求异常，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Responses API 未知异常，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )

        raise RuntimeError(
            f"Responses API 调用在 {self._max_retries + 1} 次尝试后仍失败: "
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
        1. 路径含 /responses / Path has /responses → 直接使用 / use as-is
        2. 路径不含 / Path missing → 追加 / append /responses
        3. Azure URL 且无 api-version / Azure without api-version → 自动追加 / auto-append
        """
        parsed = urlparse(url)

        # 检查路径是否已包含 /responses / Check if path already has /responses
        path = parsed.path
        if "/responses" not in path:
            # 追加 /responses / Append /responses
            path = path.rstrip("/") + "/responses"

        # 处理 query 参数 / Handle query params
        query_params = parse_qs(parsed.query, keep_blank_values=True)

        # Azure 端点：未包含 api-version 时自动追加 / Azure: auto-append api-version if missing
        if api_version and "api-version" not in query_params:
            hostname = parsed.hostname or ""
            if any(hostname.endswith(d) for d in _AZURE_DOMAIN_SUFFIXES):
                query_params["api-version"] = [api_version]

        # 重新编码 query string / Re-encode query string
        new_query = urlencode(query_params, doseq=True)

        # 重组 URL / Reassemble URL
        resolved = urlunparse(
            parsed._replace(path=path, query=new_query)
        )
        return resolved

    @staticmethod
    def _detect_azure(url: str) -> bool:
        """检测 URL 是否为 Azure 端点。 / Detect if URL is an Azure endpoint.

        通过域名后缀匹配 Azure 服务域名。
        / Matches Azure service domains by hostname suffix.
        """
        hostname = urlparse(url).hostname or ""
        return any(hostname.endswith(d) for d in _AZURE_DOMAIN_SUFFIXES)

    # =========================================================================
    # 请求构建与响应解析 / Request Building & Response Parsing
    # =========================================================================

    def _build_request(
        self, system_prompt: str, user_message: str
    ) -> Dict[str, Any]:
        """构建 Responses API 请求体。 / Build Responses API request body.

        使用 instructions 字段传递系统提示词，input 数组传递用户消息。
        / Uses instructions field for system prompt, input array for user message.
        """
        body: Dict[str, Any] = {
            "model": self._model,
            "instructions": system_prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_message,
                        }
                    ],
                }
            ],
            "temperature": self._temperature,
            "store": False,  # 不存储对话（模拟场景无需持久化） / No conversation storage (simulation)
        }

        if self._max_tokens is not None:
            body["max_output_tokens"] = self._max_tokens

        return body

    @staticmethod
    def _extract_text(response_data: Dict[str, Any]) -> str:
        """从 Responses API 响应中提取文本内容。 / Extract text from Responses API response.

        兼容两种响应格式 / Compatible with two formats:
        1. 标准 OpenAI Responses / Standard: output_text or output[].content[].text
        2. 国内厂商变体 / CN vendor variants: may differ slightly
        """
        # 优先使用 output_text / Prefer output_text (SDK convenience field)
        if "output_text" in response_data:
            return response_data["output_text"]

        # 遍历 output 数组提取文本 / Iterate output array to extract text
        output = response_data.get("output", [])
        texts: List[str] = []
        for item in output:
            # 标准格式 / Standard: output[].content[].text
            content = item.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        # output_text 类型 / output_text type
                        if part.get("type") in ("output_text", "text"):
                            texts.append(part.get("text", ""))
            elif isinstance(content, str):
                texts.append(content)

            # 备用：直接从 message 字段提取 / Fallback: extract from message field
            if not texts and "message" in item:
                msg = item["message"]
                if isinstance(msg, dict) and "content" in msg:
                    texts.append(str(msg["content"]))

        if texts:
            return "\n".join(texts)

        # 最后尝试 choices（某些兼容端点可能混用格式） / Last resort: choices (some endpoints mix formats)
        choices = response_data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return msg.get("content", "")

        logger.warning(
            "Responses API 响应中未找到文本内容: %s",
            json.dumps(response_data, ensure_ascii=False)[:300],
        )
        return ""

    @classmethod
    def from_endpoint_config(cls, config) -> ResponsesAPIAdapter:
        """从 ModelEndpointConfig 创建适配器实例。 / Create adapter from ModelEndpointConfig.

        Args:
            config: ModelEndpointConfig 实例。 / ModelEndpointConfig instance.

        Returns:
            ResponsesAPIAdapter 实例。 / ResponsesAPIAdapter instance.

        Raises:
            ValueError: 缺少必要的配置（url / api_key）。 / Missing required config (url / api_key).
        """
        if not config.url:
            raise ValueError(
                f"Responses API 模式需要显式配置 url，"
                f"但角色的 url 为空。请在 llm_config 中设置 url。"
            )
        if not config.api_key:
            raise ValueError(
                f"Responses API 模式需要显式配置 api_key，"
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
