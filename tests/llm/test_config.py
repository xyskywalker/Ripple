from ripple.llm.config import ModelEndpointConfig


class TestModelEndpointConfig:
    def test_omits_temperature_when_not_configured(self):
        config = ModelEndpointConfig.from_dict(
            {
                "model_platform": "anthropic",
                "model_name": "claude-opus-4-7",
                "api_mode": "anthropic",
            }
        )

        assert config.temperature is None

    def test_preserves_explicit_temperature(self):
        config = ModelEndpointConfig.from_dict(
            {
                "model_platform": "anthropic",
                "model_name": "claude-opus-4-7",
                "api_mode": "anthropic",
                "temperature": 0.5,
            }
        )

        assert config.temperature == 0.5
