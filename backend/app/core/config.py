"""Application settings, loaded from environment variables and an optional .env file.

This module is the single source of truth for configuration. Secrets are never
hardcoded here: values come from the process environment, falling back to the
local `.env` file (see `.env.example`).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the PAL backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---------------------------------------------------
    app_name: str = "PAL Accounting Automation API"
    app_env: str = "development"
    debug: bool = True

    # --- Database ------------------------------------------------------
    # SQLAlchemy 2.x URL using the psycopg (v3) driver.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/pal_db"

    # --- API -----------------------------------------------------------
    api_v1_prefix: str = "/api/v1"

    # --- CORS ----------------------------------------------------------
    # Comma-separated list of allowed origins, e.g. "http://localhost:5173".
    cors_origins: str = "http://localhost:5173"

    # --- Document storage ----------------------------------------------
    # PAL runs inside the client's own environment, so uploaded documents are
    # kept on local disk. A relative path resolves against the process working
    # directory (the backend/ folder). Never point this at a shared or
    # unencrypted location on a multi-user machine.
    document_storage_path: str = "./storage/documents"
    # Uploads above this size are rejected before anything touches disk.
    document_max_upload_bytes: int = 20 * 1024 * 1024  # 20 MB

    # --- AI Provider (DeepSeek) ----------------------------------------
    # API key is NEVER logged, returned, or exposed to the frontend.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # --- Invoice extraction --------------------------------------------
    # Hard cap on PDF pages sent to the vision model. A document above this
    # limit is rejected for review rather than silently truncated.
    invoice_max_pages: int = 10

    @property
    def document_storage_dir(self) -> Path:
        """Absolute, expanded storage root for uploaded documents."""
        return Path(self.document_storage_path).expanduser().resolve()

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins parsed into a list, for CORSMiddleware."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """True when running against the production environment."""
        return self.app_env.lower() == "production"

    @property
    def ai_configured(self) -> bool:
        """Whether an AI provider is configured and usable."""
        return bool(self.deepseek_api_key and self.deepseek_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()