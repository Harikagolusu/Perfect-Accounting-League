"""AI Provider API endpoints.

Development and diagnostic endpoints for verifying AI provider configuration
and connectivity. These are NOT business endpoints — they exist solely to
confirm the provider is reachable and correctly configured.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.ai import AIProviderError, get_ai_provider

router = APIRouter()

#: Keeps the diagnostic call cheap and bounds token spend.
TEST_MAX_TOKENS = 256

#: Stable mapping from safe provider error codes to HTTP statuses.
_ERROR_STATUS = {
    AIProviderError.NOT_CONFIGURED: status.HTTP_503_SERVICE_UNAVAILABLE,
    AIProviderError.TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    AIProviderError.RATE_LIMIT: status.HTTP_429_TOO_MANY_REQUESTS,
    AIProviderError.CONNECTION_ERROR: status.HTTP_502_BAD_GATEWAY,
    AIProviderError.PROVIDER_ERROR: status.HTTP_502_BAD_GATEWAY,
}


class AIStatusResponse(BaseModel):
    """Safe, non-sensitive AI provider status."""

    configured: bool
    provider: str
    model: str


class AITestRequest(BaseModel):
    """Request body for the AI test endpoint."""

    prompt: str = Field(..., min_length=1, max_length=2000)


class AITestResponse(BaseModel):
    """Normalised test response from the AI provider."""

    provider: str
    model: str
    text: str


@router.get("/status", response_model=AIStatusResponse, tags=["ai"])
def ai_status() -> AIStatusResponse:
    """Return whether the AI provider is configured.

    Does NOT make an external API call. Does NOT expose the API key or
    any secrets. Safe for health checks and startup probes.
    """
    settings = get_settings()
    provider = get_ai_provider()

    return AIStatusResponse(
        configured=settings.ai_configured,
        provider=provider.name,
        model=provider.model,
    )


@router.post("/test", response_model=AITestResponse, tags=["ai"])
def ai_test(payload: AITestRequest) -> AITestResponse:
    """Send a test prompt to the configured AI provider.

    This is a DEVELOPMENT/TEST endpoint only. It verifies end-to-end
    connectivity and API key validity. It does NOT implement any business
    logic and should not be used by production workflows.

    Returns a normalised response with only the generated text and
    provider metadata. Errors are mapped to safe HTTP status codes without
    leaking provider-specific details.
    """
    provider = get_ai_provider()

    if not provider.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI provider is not configured.",
        )

    try:
        response = provider.generate_text(
            payload.prompt,
            max_tokens=TEST_MAX_TOKENS,
        )
    except AIProviderError as exc:
        # `str(exc)` is a safe, fixed message; the provider never puts
        # credentials or raw payloads into it. Status comes from the code.
        raise HTTPException(
            status_code=_ERROR_STATUS.get(
                exc.code, status.HTTP_502_BAD_GATEWAY
            ),
            detail=str(exc),
        ) from exc

    return AITestResponse(
        provider=provider.name,
        model=response.model,
        text=response.text,
    )
