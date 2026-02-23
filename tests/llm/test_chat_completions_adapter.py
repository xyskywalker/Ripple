# test_chat_completions_adapter.py
# =============================================================================
# ChatCompletionsAdapter 单元测试 / ChatCompletionsAdapter unit tests
# - URL 补全逻辑 / URL completion logic
# - Azure 检测 / Azure detection
# - 请求构建 / Request building
# - 响应解析 / Response parsing
# - from_endpoint_config 工厂方法 / Factory method
# =============================================================================

import pytest

from ripple.llm.chat_completions_adapter import ChatCompletionsAdapter


class TestResolveEndpoint:
    """URL 补全逻辑测试。 / URL completion logic tests."""

    def test_appends_chat_completions_to_base_url(self):
        result = ChatCompletionsAdapter._resolve_endpoint(
            "https://ark.cn-beijing.volces.com/api/v3"
        )
        assert result == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

    def test_preserves_existing_chat_completions_path(self):
        url = "https://api.openai.com/v1/chat/completions"
        result = ChatCompletionsAdapter._resolve_endpoint(url)
        assert result == url

    def test_preserves_query_params(self):
        url = "https://xxx.cognitiveservices.azure.com/openai/chat/completions?api-version=2025-04-01-preview"
        result = ChatCompletionsAdapter._resolve_endpoint(url)
        assert "api-version=2025-04-01-preview" in result

    def test_appends_api_version_for_azure(self):
        result = ChatCompletionsAdapter._resolve_endpoint(
            "https://xxx.cognitiveservices.azure.com/openai",
            api_version="2025-04-01-preview",
        )
        assert "/chat/completions" in result
        assert "api-version=2025-04-01-preview" in result

    def test_no_api_version_for_non_azure(self):
        result = ChatCompletionsAdapter._resolve_endpoint(
            "https://api.openai.com/v1",
            api_version="2025-04-01-preview",
        )
        assert "api-version" not in result

    def test_strips_trailing_slash(self):
        result = ChatCompletionsAdapter._resolve_endpoint(
            "https://api.openai.com/v1/"
        )
        assert result == "https://api.openai.com/v1/chat/completions"


class TestDetectAzure:
    """Azure 域名检测测试。 / Azure domain detection tests."""

    def test_detects_cognitiveservices(self):
        assert ChatCompletionsAdapter._detect_azure(
            "https://xxx.cognitiveservices.azure.com/openai"
        ) is True

    def test_detects_openai_azure(self):
        assert ChatCompletionsAdapter._detect_azure(
            "https://xxx.openai.azure.com/v1"
        ) is True

    def test_detects_ai_azure(self):
        assert ChatCompletionsAdapter._detect_azure(
            "https://xxx.services.ai.azure.com/openai"
        ) is True

    def test_non_azure_returns_false(self):
        assert ChatCompletionsAdapter._detect_azure(
            "https://api.openai.com/v1"
        ) is False

    def test_volcengine_returns_false(self):
        assert ChatCompletionsAdapter._detect_azure(
            "https://ark.cn-beijing.volces.com/api/v3"
        ) is False


class TestBuildRequest:
    """请求构建测试。 / Request building tests."""

    def test_includes_system_and_user_messages(self):
        adapter = ChatCompletionsAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
        )
        body = adapter._build_request("You are a helper.", "Hello")
        assert body["model"] == "gpt-4o"
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "You are a helper."
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "Hello"

    def test_omits_system_when_empty(self):
        adapter = ChatCompletionsAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
        )
        body = adapter._build_request("", "Hello")
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    def test_includes_max_tokens_when_set(self):
        adapter = ChatCompletionsAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            max_tokens=1024,
        )
        body = adapter._build_request("sys", "user")
        assert body["max_tokens"] == 1024

    def test_omits_max_tokens_when_none(self):
        adapter = ChatCompletionsAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            max_tokens=None,
        )
        body = adapter._build_request("sys", "user")
        assert "max_tokens" not in body


class TestExtractText:
    """响应解析测试。 / Response parsing tests."""

    def test_extracts_from_standard_response(self):
        data = {
            "choices": [
                {"message": {"role": "assistant", "content": "Hello there!"}}
            ]
        }
        assert ChatCompletionsAdapter._extract_text(data) == "Hello there!"

    def test_returns_empty_on_missing_choices(self):
        assert ChatCompletionsAdapter._extract_text({}) == ""

    def test_returns_empty_on_empty_choices(self):
        assert ChatCompletionsAdapter._extract_text({"choices": []}) == ""


class TestFromEndpointConfig:
    """工厂方法测试。 / Factory method tests."""

    def test_raises_without_url(self):
        class FakeConfig:
            url = None
            api_key = "key"
            model_name = "gpt-4o"
            model_platform = "openai"
            temperature = 0.7
            max_tokens = 4096
            timeout = 120.0
            max_retries = 3
            api_version = None

        with pytest.raises(ValueError, match="url"):
            ChatCompletionsAdapter.from_endpoint_config(FakeConfig())

    def test_raises_without_api_key(self):
        class FakeConfig:
            url = "https://api.openai.com/v1"
            api_key = None
            model_name = "gpt-4o"
            model_platform = "openai"
            temperature = 0.7
            max_tokens = 4096
            timeout = 120.0
            max_retries = 3
            api_version = None

        with pytest.raises(ValueError, match="api_key"):
            ChatCompletionsAdapter.from_endpoint_config(FakeConfig())

    def test_creates_adapter_with_valid_config(self):
        class FakeConfig:
            url = "https://api.openai.com/v1"
            api_key = "sk-test"
            model_name = "gpt-4o"
            model_platform = "openai"
            temperature = 0.5
            max_tokens = 2048
            timeout = 60.0
            max_retries = 2
            api_version = None

        adapter = ChatCompletionsAdapter.from_endpoint_config(FakeConfig())
        assert adapter._model == "gpt-4o"
        assert adapter._temperature == 0.5
        assert adapter._max_tokens == 2048
