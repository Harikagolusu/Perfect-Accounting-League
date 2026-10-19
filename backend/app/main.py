"""FastAPI application entry point for the PAL backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router, health
from app.core.config import get_settings
from app.schemas import HealthResponse

settings = get_settings()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        debug=settings.debug,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router, prefix=settings.api_v1_prefix)

    # Root-level alias of the same handler, for orchestrator probes and a
    # quick `curl localhost:8000/health`.
    application.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["health"],
    )

    return application


app = create_app()