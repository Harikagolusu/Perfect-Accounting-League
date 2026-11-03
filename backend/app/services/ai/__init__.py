"""AI Provider foundation for PAL.

This package provides a clean abstraction over AI providers (currently DeepSeek)
so that business logic never depends on a specific provider implementation.

Usage:
    from app.services.ai import get_ai_provider

    provider = get_ai_provider()
    if provider.is_configured():
        response = provider.generate_text("Hello, world!")
"""

from app.core.config import get_settings
from app.services.ai.provider import AIProvider, AIProviderError, AIResponse, ImageInput
from app.services.ai.deepseek import DeepSeekProvider

__all__ = [
    "AIProvider",
    "AIProviderError",
    "AIResponse",
    "DeepSeekProvider",
    "ImageInput",
    "get_ai_provider",
    "get_reasoning_provider",
]


def get_ai_provider() -> AIProvider:
    """Return the configured AI provider instance.

    Currently always returns DeepSeekProvider. When a local model provider
    is added, this function can be changed to select the appropriate provider
    based on configuration, without touching any business logic.
    """
    return DeepSeekProvider()


def get_reasoning_provider() -> AIProvider:
    """Provider for reasoning-heavy, small-output tasks (ledger matching)."""
    return DeepSeekProvider(model=get_settings().deepseek_reasoner_model)