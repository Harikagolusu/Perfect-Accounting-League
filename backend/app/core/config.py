"""Application settings, loaded from environment variables and an optional .env file.

This module is the single source of truth for configuration. Secrets are never
hardcoded here: values come from the process environment, falling back to the
local `.env` file (see `.env.example`).
"""

from functools import lru_cache

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

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins parsed into a list, for CORSMiddleware."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """True when running against the production environment."""
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()