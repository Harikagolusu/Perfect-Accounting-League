"""AI Provider interface and response model.

This module defines the contract that any AI provider (DeepSeek, local model,
etc.) must satisfy. Business logic depends only on this interface, not on
a specific implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
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


@dataclass(frozen=True)
class ImageInput:
    """One image sent to a vision-capable provider.

    The bytes are prepared in memory by the caller. Providers never read from
    disk and never see a storage path.
    """

    data: bytes
    mime_type: str


@runtime_checkable
class AIProvider(Protocol):
    """Minimal interface for text and vision providers."""

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
        json_mode: bool = False,
    ) -> AIResponse:
        """Generate text from a prompt.

        This is the compatibility text method. Extraction prefers
        `generate_from_text` for native PDF text, OCR text and structured
        document text; it must not be used for image-only inputs.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system-level instruction.
            temperature: Sampling temperature (0 = deterministic).
            max_tokens: Optional hard cap on output tokens.
            json_mode: Ask the provider for a JSON object response when the
                underlying API supports it.

        Returns:
            An AIResponse with the generated text and metadata.

        Raises:
            AIProviderError: If the provider cannot complete the request.
        """
        ...

    def generate_from_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> AIResponse:
        """Generate text from a text-only prompt.

        Kept as a distinct capability from vision so a provider that cannot
        accept images is still usable for digital PDFs, OCR text, Excel and
        DOCX. `generate_text` remains the compatibility method; providers may
        implement both by delegating one to the other.
        """
        ...

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
        """Generate text from a prompt plus one or more images.

        Args:
            prompt: The user prompt.
            images: Prepared images, in reading order.
            system_prompt: Optional system-level instruction.
            temperature: Sampling temperature (0 = deterministic).
            max_tokens: Optional hard cap on output tokens.
            json_mode: Ask the provider for a JSON object response when the
                underlying API supports it.

        Returns:
            An AIResponse whose `text` is the generated content.

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
    #: The provider does not implement the capability the caller needs.
    UNSUPPORTED_CAPABILITY = "unsupported_capability"

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