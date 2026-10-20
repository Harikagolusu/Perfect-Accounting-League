"""Tests for the DeepSeek AI provider foundation.

These tests do NOT call the real DeepSeek API. They verify:
- Settings load correctly
- Missing API key results in configured=False
- Provider factory returns DeepSeekProvider
- Provider does not expose credentials
- AI status endpoint works
- Test API handles missing configuration
- Provider API errors are converted to safe application errors
- /health remains independent of DeepSeek
"""

import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.ai import AIProviderError, get_ai_provider
from app.services.ai.deepseek import DeepSeekProvider

client = TestClient(app)


def clear_settings_cache():
    """Clear the lru_cache on get_settings."""
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_settings():
    """Ensure each test starts with a clean settings cache."""
    clear_settings_cache()
    yield
    clear_settings_cache()


class TestSettings:
    def test_settings_load_without_api_key(self):
        """Settings load fine when no AI key is set."""
        settings = get_settings()
        assert settings.ai_configured is False
        assert settings.deepseek_api_key == ""
        assert settings.deepseek_base_url == "https://api.deepseek.com"
        assert settings.deepseek_model == "deepseek-flash"

    def test_settings_with_api_key(self, monkeypatch):
        """Settings report configured=True when an API key is present."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key-12345")
        clear_settings_cache()
        settings = get_settings()
        assert settings.ai_configured is True
        assert settings.deepseek_api_key == "sk-test-key-12345"

    def test_settings_base_url_custom(self, monkeypatch):
        """Custom base URL is respected."""
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://custom.example.com")
        clear_settings_cache()
        settings = get_settings()
        assert settings.deepseek_base_url == "https://custom.example.com"

    def test_settings_model_custom(self, monkeypatch):
        """Custom model name is respected."""
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
        clear_settings_cache()
        settings = get_settings()
        assert settings.deepseek_model == "deepseek-reasoner"

    def test_ai_configured_requires_non_empty_key(self, monkeypatch):
        """Whitespace-only key does not count as configured."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
        clear_settings_cache()
        settings = get_settings()
        assert settings.ai_configured is False


class TestProviderFactory:
    def test_factory_returns_deepseek_provider(self):
        """get_ai_provider returns a DeepSeekProvider instance."""
        provider = get_ai_provider()
        assert isinstance(provider, DeepSeekProvider)

    def test_provider_has_correct_metadata(self):
        provider = get_ai_provider()
        assert provider.name == "deepseek"
        assert provider.model == "deepseek-flash"

    def test_provider_is_not_configured_without_key(self):
        provider = get_ai_provider()
        assert provider.is_configured() is False

    def test_provider_does_not_expose_credentials(self):
        """The provider should not expose the API key through its public interface."""
        provider = get_ai_provider()
        assert not hasattr(provider, "api_key")
        assert not hasattr(provider, "credentials")
        # The only public attributes are name, model, and methods
        public_attrs = [a for a in dir(provider) if not a.startswith("_")]
        assert "api_key" not in public_attrs
        assert "credentials" not in public_attrs

    def test_provider_is_configured_with_key(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key-12345")
        clear_settings_cache()
        provider = get_ai_provider()
        assert provider.is_configured() is True

    def test_provider_does_not_log_api_key(self, caplog, monkeypatch):
        """The provider should not log the API key."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-key-99999")
        clear_settings_cache()
        import logging

        provider = get_ai_provider()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        with caplog.at_level(logging.DEBUG):
            provider.generate_text("test")
        assert "sk-secret-key-99999" not in caplog.text


class TestAIResponseModel:
    def test_ai_response_has_text_model_usage(self):
        """AIResponse dataclass has expected fields."""
        from app.services.ai.provider import AIResponse

        resp = AIResponse(text="hello", model="deepseek-flash", usage={"total_tokens": 10})
        assert resp.text == "hello"
        assert resp.model == "deepseek-flash"
        assert resp.usage == {"total_tokens": 10}

    def test_ai_response_usage_defaults_to_none(self):
        from app.services.ai.provider import AIResponse

        resp = AIResponse(text="hello", model="deepseek-flash")
        assert resp.usage is None

    def test_ai_provider_error_carries_provider(self):
        from app.services.ai.provider import AIProviderError

        err = AIProviderError("connection failed", provider="deepseek")
        assert str(err) == "connection failed"
        assert err.provider == "deepseek"


class TestStatusEndpoint:
    def test_ai_status_when_not_configured(self):
        """Status endpoint reports configured=False without an API key."""
        response = client.get("/api/v1/ai/status")
        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is False
        assert data["provider"] == "deepseek"
        assert data["model"] == "deepseek-flash"

    def test_ai_status_does_not_expose_secrets(self):
        """Status endpoint response must not contain any key material."""
        response = client.get("/api/v1/ai/status")
        data = response.json()
        data_str = str(data).lower()
        assert "api_key" not in data_str
        assert "secret" not in data_str
        assert "key" not in data_str

    def test_health_independent_of_ai(self):
        """The /health endpoint works regardless of AI configuration."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_api_v1_health_independent_of_ai(self):
        """The /api/v1/health endpoint works regardless of AI configuration."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestTestEndpoint:
    def test_ai_test_rejects_missing_configuration(self):
        """Test endpoint returns 503 when no API key is configured."""
        response = client.post("/api/v1/ai/test", json={"prompt": "test"})
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        assert "not configured" in data["detail"].lower()

    def test_ai_test_rejects_empty_prompt(self):
        """Test endpoint rejects empty prompt."""
        response = client.post("/api/v1/ai/test", json={"prompt": ""})
        assert response.status_code == 422

    def test_ai_test_rejects_missing_prompt(self):
        """Test endpoint rejects missing prompt field."""
        response = client.post("/api/v1/ai/test", json={})
        assert response.status_code == 422

    def test_ai_test_rejects_too_long_prompt(self):
        """Test endpoint rejects overly long prompts."""
        response = client.post("/api/v1/ai/test", json={"prompt": "x" * 2001})
        assert response.status_code == 422


class TestProviderErrorConversion:
    def test_timeout_error_converted(self, monkeypatch):
        """APITimeoutError is converted to AIProviderError with timeout message."""
        from openai import APITimeoutError

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        clear_settings_cache()
        provider = get_ai_provider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )
        provider._client = mock_client

        with pytest.raises(AIProviderError) as exc_info:
            provider.generate_text("test")
        assert "timed out" in str(exc_info.value).lower()
        assert exc_info.value.code == AIProviderError.TIMEOUT

    def test_rate_limit_error_converted(self, monkeypatch):
        """RateLimitError is converted to AIProviderError with rate limit message."""
        from openai import RateLimitError

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        clear_settings_cache()
        provider = get_ai_provider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RateLimitError(
            "rate limited",
            response=MagicMock(),
            body=None,
        )
        provider._client = mock_client

        with pytest.raises(AIProviderError) as exc_info:
            provider.generate_text("test")
        assert "rate limit" in str(exc_info.value).lower()
        assert exc_info.value.code == AIProviderError.RATE_LIMIT

    def test_api_connection_error_converted(self, monkeypatch):
        """APIConnectionError is converted to AIProviderError with connection message."""
        from openai import APIConnectionError

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        clear_settings_cache()
        provider = get_ai_provider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock()
        )
        provider._client = mock_client

        with pytest.raises(AIProviderError) as exc_info:
            provider.generate_text("test")
        assert "connect" in str(exc_info.value).lower()
        assert exc_info.value.code == AIProviderError.CONNECTION_ERROR

    def test_api_error_converted(self, monkeypatch):
        """APIError is converted to AIProviderError with generic message."""
        from openai import APIError

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        clear_settings_cache()
        provider = get_ai_provider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APIError(
            "bad request",
            MagicMock(),
            body=None,
        )
        provider._client = mock_client

        with pytest.raises(AIProviderError) as exc_info:
            provider.generate_text("test")
        assert "error" in str(exc_info.value).lower()
        assert exc_info.value.code == AIProviderError.PROVIDER_ERROR

    def test_generate_text_returns_response_on_success(self, monkeypatch):
        """Successful generation returns an AIResponse."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        clear_settings_cache()
        provider = get_ai_provider()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "PAL AI connection successful."
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        result = provider.generate_text("test prompt")
        assert result.text == "PAL AI connection successful."
        assert result.model == "deepseek-flash"
        assert result.usage["total_tokens"] == 15

    def test_generate_text_raises_when_not_configured(self):
        """generate_text raises AIProviderError when no API key is set."""
        clear_settings_cache()
        provider = get_ai_provider()
        assert not provider.is_configured()
        with pytest.raises(AIProviderError) as exc_info:
            provider.generate_text("test")
        assert "not configured" in str(exc_info.value).lower()
        assert exc_info.value.code == AIProviderError.NOT_CONFIGURED


class TestAIProviderProtocol:
    def test_deepseek_provider_satisfies_protocol(self):
        """DeepSeekProvider satisfies the AIProvider protocol."""
        from app.services.ai.provider import AIProvider

        provider = get_ai_provider()
        assert isinstance(provider, AIProvider)


class TestEndpointErrorMapping:
    """Provider errors must become safe HTTP responses, never raw exceptions."""

    class _FakeProvider:
        name = "deepseek"
        model = "deepseek-flash"

        def __init__(self, error):
            self._error = error

        def is_configured(self) -> bool:
            return True

        def generate_text(self, *args, **kwargs):
            raise self._error

    @pytest.mark.parametrize(
        ("code", "expected_status"),
        [
            (AIProviderError.NOT_CONFIGURED, 503),
            (AIProviderError.TIMEOUT, 504),
            (AIProviderError.RATE_LIMIT, 429),
            (AIProviderError.CONNECTION_ERROR, 502),
            (AIProviderError.PROVIDER_ERROR, 502),
        ],
    )
    def test_provider_error_maps_to_safe_status(
        self, monkeypatch, code, expected_status
    ):
        error = AIProviderError("safe message", provider="deepseek", code=code)
        fake = self._FakeProvider(error)
        ai_router_module = sys.modules["app.api.v1.ai.router"]
        monkeypatch.setattr(
            ai_router_module, "get_ai_provider", lambda: fake
        )

        response = client.post("/api/v1/ai/test", json={"prompt": "hi"})

        assert response.status_code == expected_status
        assert response.json() == {"detail": "safe message"}
        # No credential material may appear in an error body.
        assert "sk-" not in response.text.lower()

    def test_success_returns_normalised_response(self, monkeypatch):
        from app.services.ai import AIResponse

        class _OkProvider:
            name = "deepseek"
            model = "deepseek-flash"

            def is_configured(self) -> bool:
                return True

            def generate_text(self, *args, **kwargs):
                return AIResponse(
                    text="PAL AI connection successful.",
                    model="deepseek-flash",
                    usage={"total_tokens": 12},
                )

        ai_router_module = sys.modules["app.api.v1.ai.router"]
        monkeypatch.setattr(
            ai_router_module, "get_ai_provider", lambda: _OkProvider()
        )

        response = client.post(
            "/api/v1/ai/test",
            json={"prompt": "Reply with exactly: PAL AI connection successful."},
        )

        assert response.status_code == 200
        assert response.json() == {
            "provider": "deepseek",
            "model": "deepseek-flash",
            "text": "PAL AI connection successful.",
        }