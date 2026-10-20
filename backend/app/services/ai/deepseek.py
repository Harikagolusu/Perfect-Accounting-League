"""DeepSeek AI provider implementation.

Uses the OpenAI-compatible client since DeepSeek exposes an OpenAI-compatible
API endpoint. This keeps the dependency minimal — no LangChain, LlamaIndex,
or other frameworks.

Two capabilities are exposed behind the provider interface:

* `generate_text` — text-only prompts;
* `generate_from_images` — prompts plus prepared images (vision).

The invoice extraction service depends only on `AIProvider`, so replacing
DeepSeek with a local vision model later does not touch invoice logic.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from typing import Any

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI
from openai import RateLimitError as OpenAIRateLimitError

from app.core.config import get_settings
from app.services.ai.provider import AIProvider, AIProviderError, AIResponse, ImageInput

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

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._complete(
            messages, temperature=temperature, max_tokens=max_tokens
        )

    def generate_from_images(
        self,
        prompt: str,
        images: Sequence[ImageInput],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> AIResponse:
        if not self.is_configured():
            raise AIProviderError(
                "DeepSeek provider is not configured (missing API key).",
                provider=self.name,
                code=AIProviderError.NOT_CONFIGURED,
            )
        if not images:
            raise AIProviderError(
                "No images were supplied for extraction.",
                provider=self.name,
                code=AIProviderError.PROVIDER_ERROR,
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            if not image.data:
                raise AIProviderError(
                    "An image payload was empty.",
                    provider=self.name,
                    code=AIProviderError.PROVIDER_ERROR,
                )
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.mime_type};base64,{encoded}",
                    },
                }
            )

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        return self._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    def _complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int | None,
        json_mode: bool = False,
    ) -> AIResponse:
        """Call the chat-completions endpoint and normalise the result."""
        request: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if json_mode:
            # JSON Output mode. The prompt still pins the exact shape; this
            # only asks the API to emit a JSON object rather than prose.
            request["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**request)  # type: ignore[union-attr]
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
