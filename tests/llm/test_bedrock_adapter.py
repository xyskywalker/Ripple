# test_bedrock_adapter.py
# =============================================================================
# BedrockAdapter 单元测试
# - 导入守卫（boto3 缺失时的行为）
# - 请求构建（Anthropic vs Amazon 格式）
# - 响应解析
# - from_endpoint_config 工厂方法
# =============================================================================

import pytest
from unittest.mock import MagicMock, patch


class TestImportGuard:
    """boto3 导入守卫测试。"""

    def test_module_imports_without_boto3(self):
        """即使 boto3 不可用，模块本身应能正常导入。"""
        from ripple.llm import bedrock_adapter
        assert hasattr(bedrock_adapter, "BedrockAdapter")

    @patch("ripple.llm.bedrock_adapter._HAS_BOTO3", False)
    def test_raises_import_error_without_boto3(self):
        """实例化时如果 boto3 不可用，应抛出 ImportError。"""
        from ripple.llm.bedrock_adapter import BedrockAdapter
        with pytest.raises(ImportError, match="boto3"):
            BedrockAdapter(model="anthropic.claude-sonnet-4-20250514-v1:0")


class TestBuildRequest:
    """请求构建测试（不需要真实 boto3 连接）。"""

    def _make_adapter(self, model: str = "anthropic.claude-sonnet-4-20250514-v1:0"):
        """创建带有 mock boto3 client 的 adapter。"""
        from ripple.llm.bedrock_adapter import BedrockAdapter

        with patch("ripple.llm.bedrock_adapter._HAS_BOTO3", True), \
             patch("ripple.llm.bedrock_adapter.boto3") as mock_boto3:
            mock_session = MagicMock()
            mock_boto3.Session.return_value = mock_session
            mock_session.client.return_value = MagicMock()
            adapter = BedrockAdapter(model=model)
        return adapter

    def test_anthropic_request_format(self):
        adapter = self._make_adapter("anthropic.claude-sonnet-4-20250514-v1:0")
        body = adapter._build_request("You are a helper.", "Hello")
        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert body["system"] == "You are a helper."
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert "max_tokens" in body

    def test_anthropic_omits_system_when_empty(self):
        adapter = self._make_adapter("anthropic.claude-sonnet-4-20250514-v1:0")
        body = adapter._build_request("", "Hello")
        assert "system" not in body

    def test_amazon_request_format(self):
        adapter = self._make_adapter("amazon.titan-text-express-v1")
        body = adapter._build_request("System context.", "Hello")
        assert "inputText" in body
        assert "System context." in body["inputText"]
        assert "Hello" in body["inputText"]


class TestExtractText:
    """响应解析测试。"""

    def _make_adapter(self, model: str):
        from ripple.llm.bedrock_adapter import BedrockAdapter
        with patch("ripple.llm.bedrock_adapter._HAS_BOTO3", True), \
             patch("ripple.llm.bedrock_adapter.boto3") as mock_boto3:
            mock_session = MagicMock()
            mock_boto3.Session.return_value = mock_session
            mock_session.client.return_value = MagicMock()
            adapter = BedrockAdapter(model=model)
        return adapter

    def test_extracts_anthropic_response(self):
        adapter = self._make_adapter("anthropic.claude-sonnet-4-20250514-v1:0")
        data = {"content": [{"type": "text", "text": "Hello!"}]}
        assert adapter._extract_text(data) == "Hello!"

    def test_extracts_amazon_response(self):
        adapter = self._make_adapter("amazon.titan-text-express-v1")
        data = {"results": [{"outputText": "Hello from Titan!"}]}
        assert adapter._extract_text(data) == "Hello from Titan!"

    def test_returns_empty_on_missing_content(self):
        adapter = self._make_adapter("anthropic.claude-sonnet-4-20250514-v1:0")
        assert adapter._extract_text({}) == ""


class TestFromEndpointConfig:
    """工厂方法测试。"""

    def test_creates_adapter_from_config(self):
        from ripple.llm.bedrock_adapter import BedrockAdapter

        class FakeConfig:
            model_name = "anthropic.claude-sonnet-4-20250514-v1:0"
            temperature = 0.5
            max_tokens = 2048
            max_retries = 2
            extra = {"region_name": "us-east-1", "aws_profile": "dev"}

        with patch("ripple.llm.bedrock_adapter._HAS_BOTO3", True), \
             patch("ripple.llm.bedrock_adapter.boto3") as mock_boto3:
            mock_session = MagicMock()
            mock_boto3.Session.return_value = mock_session
            mock_session.client.return_value = MagicMock()

            adapter = BedrockAdapter.from_endpoint_config(FakeConfig())
            assert adapter._model == "anthropic.claude-sonnet-4-20250514-v1:0"
            assert adapter._temperature == 0.5
            assert adapter._max_tokens == 2048

            # 验证 boto3.Session 收到正确的参数
            mock_boto3.Session.assert_called_once_with(
                profile_name="dev", region_name="us-east-1"
            )
