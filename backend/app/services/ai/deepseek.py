"""DeepSeek AI provider implementation.

Uses the OpenAI-compatible client since DeepSeek exposes an OpenAI-compatible
API endpoint. This keeps the dependency minimal — no LangChain, LlamaIndex,
or other frameworks.
"""

from __future__ import annotations

import logging

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI
from openai import RateLimitError as OpenAIRateLimitError

from app.core.config import get_settings
from app.services.ai.provider import AIProvider, AIProviderError, AIResponse

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30


class DeepSeekProvider(AIProvider):
    """DeepSeek implementation of the AIProvider interface."""

    name = "deepseek"

    def __init__(self) -> None:
        settings = get_settings()
        # The key is held only so the client can be constructed. It is never
        # logged, serialised, or returned; `_api_key` is private and not part
        # of the public surface.
        self._api_key = settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url
        self._model = settings.deepseek_model

        if self._api_key:
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        else:
            self._client = None

    @property
    def model(self) -> str:
        return self._model

    def is_configured(self) -> bool:
        return bool(self._client)

    def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> AIResponse:
        if not self.is_configured():
            raise AIProviderError(
                "DeepSeek provider is not configured (missing API key).",
                provider=self.name,
                code=AIProviderError.NOT_CONFIGURED,
            )

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except APITimeoutError as exc:
            # Log only the error class: provider payloads and request IDs can
            # contain sensitive material and must never reach the log.
            logger.warning("DeepSeek request timed out (%s)", type(exc).__name__)
            raise AIProviderError(
                "AI request timed out.",
                provider=self.name,
                code=AIProviderError.TIMEOUT,
            ) from exc
        except OpenAIRateLimitError as exc:
            logger.warning("DeepSeek rate limit exceeded (%s)", type(exc).__name__)
            raise AIProviderError(
                "AI rate limit exceeded. Please try again later.",
                provider=self.name,
                code=AIProviderError.RATE_LIMIT,
            ) from exc
        except APIConnectionError as exc:
            logger.warning("DeepSeek connection error (%s)", type(exc).__name__)
            raise AIProviderError(
                "Could not connect to AI provider.",
                provider=self.name,
                code=AIProviderError.CONNECTION_ERROR,
            ) from exc
        except APIError as exc:
            logger.warning("DeepSeek API error (%s)", type(exc).__name__)
            raise AIProviderError(
                "AI provider returned an error.",
                provider=self.name,
                code=AIProviderError.PROVIDER_ERROR,
            ) from exc

        # Extract the generated text. The OpenAI-compatible client guarantees
        # at least one choice when the call succeeds.
        choice = response.choices[0]
        text = choice.message.content or ""

        usage = None
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return AIResponse(text=text.strip(), model=self._model, usage=usage)
