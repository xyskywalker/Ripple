# test_responses_adapter.py
# =============================================================================
# ResponsesAPIAdapter 单元测试
# - URL 补全逻辑
# - Azure 检测
# - 请求构建
# - 响应解析
# - from_endpoint_config 工厂方法
# =============================================================================

import pytest

from ripple.llm.responses_adapter import ResponsesAPIAdapter


class TestResolveEndpoint:
    """URL 补全逻辑测试。"""

    def test_appends_responses_to_base_url(self):
        result = ResponsesAPIAdapter._resolve_endpoint(
            "https://ark.cn-beijing.volces.com/api/v3"
        )
        assert result == "https://ark.cn-beijing.volces.com/api/v3/responses"

    def test_preserves_existing_responses_path(self):
        url = "https://xxx.cognitiveservices.azure.com/openai/responses"
        result = ResponsesAPIAdapter._resolve_endpoint(url)
        assert "/responses" in result

    def test_preserves_query_params(self):
        url = "https://xxx.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview"
        result = ResponsesAPIAdapter._resolve_endpoint(url)
        assert "api-version=2025-04-01-preview" in result

    def test_appends_api_version_for_azure(self):
        result = ResponsesAPIAdapter._resolve_endpoint(
            "https://xxx.cognitiveservices.azure.com/openai",
            api_version="2025-04-01-preview",
        )
        assert "/responses" in result
        assert "api-version=2025-04-01-preview" in result

    def test_no_api_version_for_non_azure(self):
        result = ResponsesAPIAdapter._resolve_endpoint(
            "https://api.openai.com/v1",
            api_version="2025-04-01-preview",
        )
        assert "api-version" not in result


class TestDetectAzure:
    """Azure 域名检测测试。"""

    def test_detects_cognitiveservices(self):
        assert ResponsesAPIAdapter._detect_azure(
            "https://xxx.cognitiveservices.azure.com/openai"
        ) is True

    def test_non_azure_returns_false(self):
        assert ResponsesAPIAdapter._detect_azure(
            "https://api.openai.com/v1"
        ) is False


class TestBuildRequest:
    """请求构建测试。"""

    def test_uses_instructions_and_input(self):
        adapter = ResponsesAPIAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
        )
        body = adapter._build_request("You are a helper.", "Hello")
        assert body["model"] == "gpt-4o"
        assert body["instructions"] == "You are a helper."
        assert body["input"][0]["role"] == "user"
        assert body["input"][0]["content"][0]["type"] == "input_text"
        assert body["input"][0]["content"][0]["text"] == "Hello"

    def test_includes_max_output_tokens_when_set(self):
        adapter = ResponsesAPIAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            max_tokens=1024,
        )
        body = adapter._build_request("sys", "user")
        assert body["max_output_tokens"] == 1024

    def test_omits_max_output_tokens_when_none(self):
        adapter = ResponsesAPIAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            max_tokens=None,
        )
        body = adapter._build_request("sys", "user")
        assert "max_output_tokens" not in body


class TestExtractText:
    """响应解析测试。"""

    def test_extracts_from_output_text(self):
        data = {"output_text": "Hello!"}
        assert ResponsesAPIAdapter._extract_text(data) == "Hello!"

    def test_extracts_from_output_array(self):
        data = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "Hello from output!"}
                    ]
                }
            ]
        }
        assert ResponsesAPIAdapter._extract_text(data) == "Hello from output!"

    def test_falls_back_to_choices(self):
        data = {
            "choices": [
                {"message": {"content": "Hello from choices!"}}
            ]
        }
        assert ResponsesAPIAdapter._extract_text(data) == "Hello from choices!"

    def test_returns_empty_on_no_content(self):
        assert ResponsesAPIAdapter._extract_text({}) == ""


class TestFromEndpointConfig:
    """工厂方法测试。"""

    def test_raises_without_url(self):
        class FakeConfig:
            url = None
            api_key = "key"
            model_name = "gpt-4o"
            temperature = 0.7
            max_tokens = 4096
            timeout = 120.0
            max_retries = 3
            api_version = None

        with pytest.raises(ValueError, match="url"):
            ResponsesAPIAdapter.from_endpoint_config(FakeConfig())

    def test_raises_without_api_key(self):
        class FakeConfig:
            url = "https://api.openai.com/v1"
            api_key = None
            model_name = "gpt-4o"
            temperature = 0.7
            max_tokens = 4096
            timeout = 120.0
            max_retries = 3
            api_version = None

        with pytest.raises(ValueError, match="api_key"):
            ResponsesAPIAdapter.from_endpoint_config(FakeConfig())

    def test_creates_adapter_with_valid_config(self):
        class FakeConfig:
            url = "https://api.openai.com/v1"
            api_key = "sk-test"
            model_name = "gpt-4o"
            temperature = 0.5
            max_tokens = 2048
            timeout = 60.0
            max_retries = 2
            api_version = None

        adapter = ResponsesAPIAdapter.from_endpoint_config(FakeConfig())
        assert adapter._model == "gpt-4o"
        assert adapter._temperature == 0.5
