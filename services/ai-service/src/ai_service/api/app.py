"""FastAPI application factory.

Run with ``uvicorn ai_service.api.app:create_app --factory``.
"""

from fastapi import FastAPI

from ai_service.api.errors import install_error_handlers
from ai_service.api.health import ReadinessChecks, build_health_router, checks_from_settings
from ai_service.api.request_id import RequestIdMiddleware
from ai_service.api.settings import Settings


def create_app(
    settings: Settings | None = None,
    checks: ReadinessChecks | None = None,
) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="AI Service internal API",
        version="0.1.0",
        # Internal service: no docs UI and no schema route. internal-ai.yaml is the source
        # of truth (ADR-005); a schema export for the contract check uses app.openapi().
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(
        build_health_router(
            checks or checks_from_settings(settings),
            timeout_s=settings.readiness_timeout_s,
        )
    )
    return app
