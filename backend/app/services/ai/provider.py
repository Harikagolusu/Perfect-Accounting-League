"""AI Provider interface and response model.

This module defines the contract that any AI provider (DeepSeek, local model,
etc.) must satisfy. Business logic depends only on this interface, not on
a specific implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AIResponse:
    """Normalised response from an AI provider.

    Keeping this minimal and provider-agnostic means downstream code never
    depends on DeepSeek-specific response fields.
    """

    text: str
    model: str
    usage: dict | None = None


@runtime_checkable
class AIProvider(Protocol):
    """Minimal interface for text generation providers."""

    name: str
    model: str

    def is_configured(self) -> bool:
        """Return True if this provider can make requests."""
        ...

    def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> AIResponse:
        """Generate text from a prompt.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system-level instruction.
            temperature: Sampling temperature (0 = deterministic).
            max_tokens: Optional hard cap on output tokens.

        Returns:
            An AIResponse with the generated text and metadata.

        Raises:
            AIProviderError: If the provider cannot complete the request.
        """
        ...


class AIProviderError(Exception):
    """Raised when an AI provider cannot fulfil a request.

    Wraps provider-specific errors (timeouts, rate limits, auth failures)
    into a single application-level exception so callers don't depend on
    provider-specific exception hierarchies.

    `code` is a short, stable, non-sensitive reason callers can branch on
    instead of parsing the message. Callers should treat `message` as
    user-facing text only: it never contains provider payloads, request IDs,
    or credentials.
    """

    #: Provider is reachable but the request could not be completed.
    PROVIDER_ERROR = "provider_error"
    #: The request exceeded the configured timeout.
    TIMEOUT = "timeout"
    #: The provider rejected the request because of rate limiting.
    RATE_LIMIT = "rate_limit"
    #: A network connection to the provider could not be established.
    CONNECTION_ERROR = "connection_error"
    #: The provider is missing required configuration (e.g. an API key).
    NOT_CONFIGURED = "not_configured"

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        *,
        code: str = PROVIDER_ERROR,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code