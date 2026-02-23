# test_anthropic_adapter.py
# =============================================================================
# AnthropicAdapter 单元测试 / AnthropicAdapter unit tests
# - URL 默认值与补全 / URL defaults & completion
# - 请求格式（system / messages / headers） / Request format
# - 响应解析 / Response parsing
# - from_endpoint_config 工厂方法 / Factory method
# =============================================================================

import pytest

from ripple.llm.anthropic_adapter import AnthropicAdapter


class TestResolveEndpoint:
    """URL 解析测试。 / URL resolution tests."""

    def test_uses_default_url_when_none(self):
        result = AnthropicAdapter._resolve_endpoint(None)
        assert result == "https://api.anthropic.com/v1/messages"

    def test_uses_default_url_when_empty(self):
        result = AnthropicAdapter._resolve_endpoint("")
        assert result == "https://api.anthropic.com/v1/messages"

    def test_appends_messages_to_base_url(self):
        result = AnthropicAdapter._resolve_endpoint(
            "https://api.anthropic.com/v1"
        )
        assert result == "https://api.anthropic.com/v1/messages"

    def test_preserves_existing_messages_path(self):
        url = "https://api.anthropic.com/v1/messages"
        result = AnthropicAdapter._resolve_endpoint(url)
        assert result == url

    def test_custom_proxy_url(self):
        result = AnthropicAdapter._resolve_endpoint(
            "https://my-proxy.example.com/anthropic/v1"
        )
        assert result == "https://my-proxy.example.com/anthropic/v1/messages"


class TestBuildRequest:
    """请求构建测试。 / Request building tests."""

    def test_includes_system_and_user(self):
        adapter = AnthropicAdapter(
            api_key="test-key",
            model="claude-sonnet-4-20250514",
        )
        body = adapter._build_request("You are a helper.", "Hello")
        assert body["model"] == "claude-sonnet-4-20250514"
        assert body["system"] == "You are a helper."
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert body["max_tokens"] == 4096

    def test_omits_system_when_empty(self):
        adapter = AnthropicAdapter(
            api_key="test-key",
            model="claude-sonnet-4-20250514",
        )
        body = adapter._build_request("", "Hello")
        assert "system" not in body
        assert body["messages"] == [{"role": "user", "content": "Hello"}]

    def test_custom_max_tokens(self):
        adapter = AnthropicAdapter(
            api_key="test-key",
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
        )
        body = adapter._build_request("sys", "user")
        assert body["max_tokens"] == 1024


class TestExtractText:
    """响应解析测试。 / Response parsing tests."""

    def test_extracts_from_standard_response(self):
        data = {
            "content": [
                {"type": "text", "text": "Hello there!"}
            ]
        }
        assert AnthropicAdapter._extract_text(data) == "Hello there!"

    def test_extracts_from_multi_block_response(self):
        data = {
            "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "Answer here."},
            ]
        }
        assert AnthropicAdapter._extract_text(data) == "Answer here."

    def test_returns_empty_on_no_content(self):
        assert AnthropicAdapter._extract_text({}) == ""

    def test_returns_empty_on_empty_content(self):
        assert AnthropicAdapter._extract_text({"content": []}) == ""


class TestFromEndpointConfig:
    """工厂方法测试。 / Factory method tests."""

    def test_raises_without_api_key(self):
        class FakeConfig:
            url = None
            api_key = None
            model_name = "claude-sonnet-4-20250514"
            model_platform = "anthropic"
            temperature = 0.7
            max_tokens = 4096
            timeout = 120.0
            max_retries = 3

        with pytest.raises(ValueError, match="api_key"):
            AnthropicAdapter.from_endpoint_config(FakeConfig())

    def test_creates_adapter_with_valid_config(self):
        class FakeConfig:
            url = None
            api_key = "sk-ant-test"
            model_name = "claude-sonnet-4-20250514"
            model_platform = "anthropic"
            temperature = 0.5
            max_tokens = 2048
            timeout = 60.0
            max_retries = 2

        adapter = AnthropicAdapter.from_endpoint_config(FakeConfig())
        assert adapter._model == "claude-sonnet-4-20250514"
        assert adapter._endpoint == "https://api.anthropic.com/v1/messages"
        assert adapter._temperature == 0.5

    def test_creates_adapter_with_custom_url(self):
        class FakeConfig:
            url = "https://my-proxy.com/v1"
            api_key = "sk-ant-test"
            model_name = "claude-sonnet-4-20250514"
            model_platform = "anthropic"
            temperature = 0.7
            max_tokens = 4096
            timeout = 120.0
            max_retries = 3

        adapter = AnthropicAdapter.from_endpoint_config(FakeConfig())
        assert "my-proxy.com" in adapter._endpoint
