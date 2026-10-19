"""Pydantic v2 schemas used for request and response contracts."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Payload returned by the liveness probe."""

    status: str
    service: str


__all__ = ["HealthResponse"]