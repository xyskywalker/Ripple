# responses_adapter.py
# =============================================================================
# OpenAI Responses API 适配器
#
# 职责：
#   - 将 Ripple 的 (system_prompt, user_message) 调用转换为
#     OpenAI Responses API 格式的 HTTP 请求
#   - 解析 Responses API 的返回结构并提取文本内容
#   - 兼容多种端点来源：
#     · 标准 OpenAI（api.openai.com）
#     · 国内 OpenAI 兼容端点（火山引擎/豆包等）
#     · Azure AI Foundry（cognitiveservices.azure.com）
#
# 背景：
#   标准 Chat Completions API 使用 /chat/completions 端点和 messages 字段。
#   部分模型提供商额外提供 Responses API（/responses），使用 input 字段
#   替代 messages，内容类型为 input_text / input_image。
#   本模块为 Responses API 提供轻量级 httpx 直连适配。
#
# URL 兼容性：
#   适配器智能处理以下 URL 格式：
#   1. 基础 URL：https://ark.cn-beijing.volces.com/api/v3
#      → 自动追加 /responses
#   2. 完整路径：https://xxx.azure.com/openai/responses
#      → 直接使用
#   3. 带 query 参数：https://xxx.azure.com/openai/responses?api-version=...
#      → 直接使用（保留所有 query 参数）
#
# 认证方式：
#   - 标准端点：Authorization: Bearer <key>
#   - Azure 端点：api-key: <key>（自动检测 Azure 域名）
#
# 两种 API 格式对比：
#
#   Chat Completions (/chat/completions):
#     {"model": "xxx", "messages": [{"role": "user", "content": "..."}]}
#     → response.choices[0].message.content
#
#   Responses (/responses):
#     {"model": "xxx", "input": [{"role": "user", "content": "..."}]}
#     → response.output[0].content[0].text  或  response.output_text
# =============================================================================

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

# Azure 相关域名后缀（用于自动检测认证方式）
_AZURE_DOMAIN_SUFFIXES = (
    "cognitiveservices.azure.com",
    "openai.azure.com",
    "services.ai.azure.com",
)


class ResponsesAPIAdapter:
    """OpenAI Responses API 适配器。

    通过 httpx 异步 HTTP 直连调用 Responses API 端点。
    将 Ripple 标准的 (system_prompt, user_message) 调用格式
    转换为 Responses API 要求的请求结构。
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
        """初始化适配器。

        Args:
            url: API 端点 URL，支持以下格式：
                 - 基础 URL：https://ark.cn-beijing.volces.com/api/v3
                   → 自动追加 /responses
                 - 完整 URL：https://xxx.azure.com/openai/responses
                   → 直接使用
                 - 带参数 URL：https://xxx.azure.com/openai/responses?api-version=2025-04-01-preview
                   → 直接使用（保留 query 参数）
            api_key: API 密钥。
            model: 模型名称（如 "doubao-seed-1-6-flash-250828"）。
            temperature: 生成温度。
            max_tokens: 最大输出 token 数。
            timeout: 请求超时时间（秒）。
            max_retries: 最大重试次数。
            api_version: Azure API 版本（可选）。当 URL 为 Azure 基础 URL
                 且未包含 api-version 参数时，自动追加此参数。
        """
        # 智能解析 URL：判断是否需要追加 /responses 路径和 query 参数
        self._endpoint = self._resolve_endpoint(url, api_version)

        # 自动检测 Azure 域名以决定认证头格式
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
        """调用 Responses API 并返回文本响应。

        Args:
            system_prompt: 系统提示词（作为 instructions 或 developer 消息）。
            user_message: 用户消息。

        Returns:
            模型输出的文本内容。

        Raises:
            httpx.HTTPStatusError: HTTP 请求失败。
            ValueError: 响应格式无法解析。
        """
        # 构建请求体
        request_body = self._build_request(system_prompt, user_message)

        # 请求头：Azure 使用 api-key，其他使用 Authorization: Bearer
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._is_azure:
            headers["api-key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # 发送请求（带重试）
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
    # URL 与认证检测
    # =========================================================================

    @staticmethod
    def _resolve_endpoint(
        url: str, api_version: Optional[str] = None
    ) -> str:
        """智能解析端点 URL。

        处理逻辑：
        1. 如果 URL 路径中已包含 /responses → 直接使用（保留所有 query 参数）
        2. 如果 URL 路径中不含 /responses → 在路径末尾追加 /responses
        3. 如果是 Azure URL 且 query 中无 api-version → 自动追加 api_version 参数

        支持的 URL 格式示例：
        - https://ark.cn-beijing.volces.com/api/v3
          → https://ark.cn-beijing.volces.com/api/v3/responses
        - https://xxx.azure.com/openai/responses?api-version=2025-04-01-preview
          → 原样保留
        - https://xxx.azure.com/openai（配合 api_version="2025-04-01-preview"）
          → https://xxx.azure.com/openai/responses?api-version=2025-04-01-preview
        """
        parsed = urlparse(url)

        # 检查路径是否已包含 /responses
        path = parsed.path
        if "/responses" not in path:
            # 追加 /responses
            path = path.rstrip("/") + "/responses"

        # 处理 query 参数
        query_params = parse_qs(parsed.query, keep_blank_values=True)

        # Azure 端点：如果未包含 api-version 且提供了 api_version，自动追加
        if api_version and "api-version" not in query_params:
            hostname = parsed.hostname or ""
            if any(hostname.endswith(d) for d in _AZURE_DOMAIN_SUFFIXES):
                query_params["api-version"] = [api_version]

        # 重新编码 query string
        new_query = urlencode(query_params, doseq=True)

        # 重组 URL
        resolved = urlunparse(
            parsed._replace(path=path, query=new_query)
        )
        return resolved

    @staticmethod
    def _detect_azure(url: str) -> bool:
        """检测 URL 是否为 Azure 端点。

        通过域名后缀匹配以下 Azure 服务域名：
        - cognitiveservices.azure.com（Azure OpenAI Service）
        - openai.azure.com（Azure OpenAI）
        - services.ai.azure.com（Azure AI Foundry）
        """
        hostname = urlparse(url).hostname or ""
        return any(hostname.endswith(d) for d in _AZURE_DOMAIN_SUFFIXES)

    # =========================================================================
    # 请求构建与响应解析
    # =========================================================================

    def _build_request(
        self, system_prompt: str, user_message: str
    ) -> Dict[str, Any]:
        """构建 Responses API 请求体。

        使用 instructions 字段传递系统提示词（OpenAI 推荐方式），
        input 数组传递用户消息。
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
            "store": False,  # 不存储对话（模拟场景无需持久化）
        }

        if self._max_tokens is not None:
            body["max_output_tokens"] = self._max_tokens

        return body

    @staticmethod
    def _extract_text(response_data: Dict[str, Any]) -> str:
        """从 Responses API 响应中提取文本内容。

        兼容两种响应格式：
        1. 标准 OpenAI Responses: output_text 字段或 output[].content[].text
        2. 国内厂商变体: 可能结构略有不同
        """
        # 优先使用 output_text（OpenAI SDK 的便捷字段）
        if "output_text" in response_data:
            return response_data["output_text"]

        # 遍历 output 数组提取文本
        output = response_data.get("output", [])
        texts: List[str] = []
        for item in output:
            # 标准格式：output[].content[].text
            content = item.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        # output_text 类型
                        if part.get("type") in ("output_text", "text"):
                            texts.append(part.get("text", ""))
            elif isinstance(content, str):
                texts.append(content)

            # 备用：直接从 message 字段提取
            if not texts and "message" in item:
                msg = item["message"]
                if isinstance(msg, dict) and "content" in msg:
                    texts.append(str(msg["content"]))

        if texts:
            return "\n".join(texts)

        # 最后尝试 choices（某些兼容端点可能混用格式）
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
        """从 ModelEndpointConfig 创建适配器实例。

        Args:
            config: ModelEndpointConfig 实例。

        Returns:
            ResponsesAPIAdapter 实例。

        Raises:
            ValueError: 缺少必要的配置（url / api_key）。
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
