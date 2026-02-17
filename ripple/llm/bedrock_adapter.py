# bedrock_adapter.py
# =============================================================================
# AWS Bedrock 适配器 / AWS Bedrock adapter
#
# 职责 / Responsibilities:
#   - 将 Ripple 的 (system_prompt, user_message) 转换为 Bedrock InvokeModel 请求
#     / Convert Ripple's calls to AWS Bedrock InvokeModel API requests
#   - 支持 Anthropic-on-Bedrock (Claude) 和 Amazon 模型
#     / Supports Anthropic-on-Bedrock (Claude) and Amazon models
#   - 使用 boto3 处理 SigV4 签名 / Uses boto3 for SigV4 signing
#
# 依赖 / Dependency:
#   boto3 为可选依赖，通过 pip install ripple[bedrock] 安装。
#   / boto3 is optional; install via pip install ripple[bedrock].
#   未安装时导入正常，实例化时抛出错误。 / Import works without it; error on instantiation.
# =============================================================================

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# boto3 可选导入 / Optional boto3 import
try:
    import boto3
    _HAS_BOTO3 = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    _HAS_BOTO3 = False


class BedrockAdapter:
    """AWS Bedrock 适配器。 / AWS Bedrock adapter.

    通过 boto3 调用 AWS Bedrock InvokeModel API。
    / Calls AWS Bedrock InvokeModel API via boto3.
    支持 Anthropic Claude 和 Amazon 自有模型。boto3 为可选依赖。
    / Supports Anthropic Claude and Amazon models. boto3 is optional.
    """

    def __init__(
        self,
        model: str,
        region_name: Optional[str] = None,
        aws_profile: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ):
        """初始化适配器。 / Initialize adapter.

        Args:
            model: Bedrock 模型 ID。 / Bedrock model ID (e.g. "anthropic.claude-sonnet-4-20250514-v1:0").
            region_name: AWS 区域。 / AWS region (e.g. "us-east-1").
            aws_profile: AWS CLI profile 名称（可选）。 / AWS CLI profile name (optional).
            temperature: 生成温度。 / Generation temperature.
            max_tokens: 最大输出 token 数。 / Max output tokens.
            max_retries: 最大重试次数。 / Max retry count.

        Raises:
            ImportError: boto3 未安装。 / boto3 not installed.
        """
        if not _HAS_BOTO3:
            raise ImportError(
                "AWS Bedrock 适配器需要 boto3 库。"
                "请通过 pip install ripple[bedrock] 或 pip install boto3 安装。"
            )

        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_retries = max_retries

        # 创建 boto3 session 和 client / Create boto3 session & client
        session_kwargs: Dict[str, Any] = {}
        if aws_profile:
            session_kwargs["profile_name"] = aws_profile
        if region_name:
            session_kwargs["region_name"] = region_name

        session = boto3.Session(**session_kwargs)
        self._client = session.client("bedrock-runtime")

        self._is_anthropic = "anthropic" in model.lower() or "claude" in model.lower()

    async def call(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """调用 Bedrock InvokeModel API 并返回文本响应。 / Call Bedrock InvokeModel API and return text.

        Args:
            system_prompt: 系统提示词。 / System prompt.
            user_message: 用户消息。 / User message.

        Returns:
            模型输出的文本内容。 / Model output text.

        Raises:
            RuntimeError: 调用失败。 / Call failed.
        """
        import asyncio

        request_body = self._build_request(system_prompt, user_message)
        body_json = json.dumps(request_body)

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                # boto3 是同步的，用 asyncio.to_thread 包装 / boto3 is sync; wrap with asyncio.to_thread
                response = await asyncio.to_thread(
                    self._client.invoke_model,
                    modelId=self._model,
                    body=body_json,
                    contentType="application/json",
                    accept="application/json",
                )
                response_body = json.loads(
                    response["body"].read().decode("utf-8")
                )
                return self._extract_text(response_body)

            except Exception as e:
                last_error = e
                logger.warning(
                    "Bedrock InvokeModel 调用失败，第 %d/%d 次: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )

        raise RuntimeError(
            f"Bedrock InvokeModel 调用在 {self._max_retries + 1} 次尝试后仍失败: "
            f"{last_error}"
        )

    # =========================================================================
    # 请求构建与响应解析 / Request Building & Response Parsing
    # =========================================================================

    def _build_request(
        self, system_prompt: str, user_message: str
    ) -> Dict[str, Any]:
        """构建请求体。 / Build request body.

        根据模型类型选择请求格式 / Selects format by model type:
        - Anthropic Claude → Messages API 格式 / Messages API format
        - 其他模型 / Others → 通用 text completion 格式 / generic text completion format
        """
        if self._is_anthropic:
            body: Dict[str, Any] = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "messages": [{"role": "user", "content": user_message}],
            }
            if system_prompt:
                body["system"] = system_prompt
            return body

        # 通用格式（Amazon Titan 等） / Generic format (Amazon Titan, etc.)
        prompt = ""
        if system_prompt:
            prompt = f"{system_prompt}\n\n{user_message}"
        else:
            prompt = user_message

        return {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": self._max_tokens,
                "temperature": self._temperature,
            },
        }

    def _extract_text(self, response_data: Dict[str, Any]) -> str:
        """从响应中提取文本内容。 / Extract text from response."""
        if self._is_anthropic:
            # Anthropic Claude on Bedrock 格式 / Anthropic Claude on Bedrock format
            content = response_data.get("content", [])
            if isinstance(content, list) and content:
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
                first = content[0]
                if isinstance(first, dict) and "text" in first:
                    return first["text"]
        else:
            # Amazon Titan 等格式 / Amazon Titan etc. format
            results = response_data.get("results", [])
            if results:
                return results[0].get("outputText", "")

        logger.warning(
            "Bedrock 响应中未找到文本内容: %s",
            json.dumps(response_data, ensure_ascii=False)[:300],
        )
        return ""

    @classmethod
    def from_endpoint_config(cls, config) -> BedrockAdapter:
        """从 ModelEndpointConfig 创建适配器实例。 / Create adapter from ModelEndpointConfig.

        Args:
            config: ModelEndpointConfig 实例。 / ModelEndpointConfig instance.
                config.extra 可包含 / config.extra may contain:
                - region_name: AWS 区域 / AWS region
                - aws_profile: AWS CLI profile 名称 / AWS CLI profile name

        Returns:
            BedrockAdapter 实例。 / BedrockAdapter instance.

        Raises:
            ImportError: boto3 未安装。 / boto3 not installed.
        """
        extra = config.extra or {}

        return cls(
            model=config.model_name,
            region_name=extra.get("region_name"),
            aws_profile=extra.get("aws_profile"),
            temperature=config.temperature,
            max_tokens=config.max_tokens or 4096,
            max_retries=config.max_retries,
        )
