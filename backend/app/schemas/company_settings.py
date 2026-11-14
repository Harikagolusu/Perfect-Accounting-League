"""Schemas for the per-company Settings document.

The body is an opaque JSON object: the Settings form owns the key set, so the
backend validates the envelope (it must be a JSON object, and small) rather than
every field. This keeps the API stable as the form grows without letting a
caller store an unbounded blob.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: An ordinary settings form is a few KB. Cap well above that so a mistake or an
#: abusive caller cannot park megabytes of JSON on a company row.
MAX_SETTINGS_BYTES = 64 * 1024


class CompanySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value).encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise ValueError(
                f"Settings document exceeds {MAX_SETTINGS_BYTES} bytes."
            )
        return value


class CompanySettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: uuid.UUID
    data: dict[str, Any]
    #: Null until the company saves settings for the first time.
    updated_at: datetime | None = None


__all__ = ["CompanySettingsResponse", "CompanySettingsUpdate", "MAX_SETTINGS_BYTES"]
