"""AI Provider API endpoints.

Development and diagnostic endpoints for verifying AI provider configuration
and connectivity. These are NOT business endpoints — they exist solely to
confirm the provider is reachable and correctly configured.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.secrets import SecretKeyMissing
from app.services.ai import AIProviderError, DeepSeekProvider, get_ai_provider
from app.services.ai.key_store import current_key, delete_key, masked, save_key

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
    provider = get_ai_provider()

    return AIStatusResponse(
        # The provider resolves the saved key first, then the env var.
        configured=provider.is_configured(),
        provider=provider.name,
        model=provider.model,
    )


class AIKeyStatus(BaseModel):
    """Whether a key is in use and where it came from. Never the key itself."""

    configured: bool
    source: Literal["settings", "environment"] | None
    masked: str | None


class AIKeyUpdate(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=256)


def _key_status() -> AIKeyStatus:
    key, source = current_key()
    return AIKeyStatus(configured=bool(key), source=source, masked=masked(key))


@router.get("/key", response_model=AIKeyStatus, tags=["ai"])
def get_ai_key() -> AIKeyStatus:
    return _key_status()


@router.put("/key", response_model=AIKeyStatus, tags=["ai"])
def put_ai_key(payload: AIKeyUpdate, db: Session = Depends(get_db)) -> AIKeyStatus:
    """Verify the key with DeepSeek, then save it encrypted.

    A key DeepSeek rejects is never stored, so a typo cannot silently switch
    extraction off.
    """
    key = payload.api_key.strip()
    try:
        DeepSeekProvider(api_key=key).generate_text("Reply with OK.", max_tokens=5)
    except AIProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="DeepSeek did not accept this key. Check it and try again.",
        ) from exc
    try:
        save_key(db, key)
    except SecretKeyMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The server has no PAL_SECRET_KEY, so keys cannot be saved.",
        ) from exc
    return _key_status()


@router.delete("/key", response_model=AIKeyStatus, tags=["ai"])
def delete_ai_key(db: Session = Depends(get_db)) -> AIKeyStatus:
    """Remove the saved key; the env var, if any, takes over again."""
    delete_key(db)
    return _key_status()


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
