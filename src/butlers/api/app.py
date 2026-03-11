"""Dashboard API — FastAPI application factory.

Provides a single-page-of-glass REST API over the butler infrastructure.
The app factory creates a FastAPI instance with:
- CORS middleware (configurable origins)
- Lifespan handler for startup/shutdown of DB pools and MCP clients
- Health endpoints at GET /api/health and GET /health
- Router registration for future endpoint modules
- Optional static file serving for production (frontend/dist/)
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from butlers.api.deps import (
    get_butler_configs,
    get_db_manager,
    init_db_manager,
    init_dependencies,
    init_pricing,
    shutdown_db_manager,
    shutdown_dependencies,
    wire_db_dependencies,
)
from butlers.api.middleware import ApiKeyMiddleware, register_error_handlers
from butlers.api.router_discovery import discover_butler_routers
from butlers.api.routers.approvals import router as approvals_router
from butlers.api.routers.audit import router as audit_router
from butlers.api.routers.butlers import router as butlers_router
from butlers.api.routers.calendar_workspace import (
    router as calendar_workspace_router,
)
from butlers.api.routers.cli_auth import router as cli_auth_router
from butlers.api.routers.costs import router as costs_router
from butlers.api.routers.ingestion_events import router as ingestion_events_router
from butlers.api.routers.issues import router as issues_router
from butlers.api.routers.memory import router as memory_router
from butlers.api.routers.model_settings import butler_model_router, catalog_router
from butlers.api.routers.modules import router as modules_router
from butlers.api.routers.notifications import (
    butler_notifications_router,
)
from butlers.api.routers.notifications import (
    router as notifications_router,
)
from butlers.api.routers.oauth import router as oauth_router
from butlers.api.routers.schedules import router as schedules_router
from butlers.api.routers.search import router as search_router
from butlers.api.routers.secrets import router as secrets_router
from butlers.api.routers.sessions import (
    butler_sessions_router,
)
from butlers.api.routers.sessions import (
    router as sessions_router,
)
from butlers.api.routers.sse import router as sse_router
from butlers.api.routers.state import router as state_router
from butlers.api.routers.timeline import router as timeline_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for DB pools and MCP clients.

    On startup: initialize resources (will be implemented in future tasks)
    On shutdown: close all connections cleanly
    """
    # Startup
    init_dependencies()
    try:
        init_pricing()
    except Exception:
        logger.warning("Failed to load pricing config; cost estimation disabled")

    # Initialize DB pools for all discovered butlers
    butler_configs = get_butler_configs()
    try:
        await init_db_manager(butler_configs)
        # Wire DB dependencies for both static and dynamic routers
        dynamic_routers = getattr(app.state, "butler_routers", [])
        dynamic_modules = [module for _, module in dynamic_routers]
        wire_db_dependencies(app, dynamic_modules=dynamic_modules)
        logger.info("DatabaseManager initialized for %d butler(s)", len(butler_configs))

        # Restore CLI auth tokens from DB to filesystem
        try:
            from butlers.cli_auth.persistence import restore_tokens
            from butlers.credential_store import CredentialStore

            db_mgr = get_db_manager()
            shared_pool = db_mgr.credential_shared_pool()
            store = CredentialStore(shared_pool)
            results = await restore_tokens(store)
            restored = sum(1 for v in results.values() if v)
            if restored:
                logger.info("Restored %d CLI auth token(s) from DB", restored)
        except Exception:
            logger.debug("CLI auth token restoration skipped", exc_info=True)

    except Exception:
        logger.warning("Failed to initialize DatabaseManager; DB endpoints will be unavailable")

    yield

    # Shutdown
    await shutdown_db_manager()
    await shutdown_dependencies()


def create_app(
    cors_origins: list[str] | None = None,
    static_dir: str | Path | None = None,
    api_key: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    cors_origins:
        Allowed CORS origins. Defaults to ["http://localhost:40173"] for
        local Vite dev server.
    static_dir:
        Path to the built frontend directory (e.g. ``frontend/dist/``).
        When set, mounts a ``StaticFiles`` handler at ``/`` with
        ``html=True`` for SPA fallback.  Falls back to the
        ``DASHBOARD_STATIC_DIR`` environment variable.  When neither is
        set, no static mount is registered (development mode).
    api_key:
        When provided, enables ``ApiKeyMiddleware`` with this key.  When
        ``None`` (default), the middleware reads ``DASHBOARD_API_KEY`` from
        the environment; if that variable is also absent, auth is disabled.
        Pass an empty string ``""`` to explicitly disable auth regardless of
        the environment variable (useful in tests).
    """
    if cors_origins is None:
        cors_origins = ["http://localhost:40173"]

    app = FastAPI(
        title="Butlers Dashboard API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False

    # OTel instrumentation (only when OTLP endpoint is configured)
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            from butlers.core.metrics import init_metrics
            from butlers.core.telemetry import init_telemetry

            init_telemetry("butlers-dashboard")
            init_metrics("butlers-dashboard")
            FastAPIInstrumentor().instrument_app(app)
            logger.info("FastAPI OTel instrumentation enabled")
        except Exception:
            logger.warning("Failed to enable FastAPI OTel instrumentation", exc_info=True)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API-key authentication (opt-in via DASHBOARD_API_KEY env var).
    # Resolve the effective key here so ApiKeyMiddleware receives a definitive
    # value and never reads the environment itself.
    #
    # Resolution rules:
    #   api_key=None  → read DASHBOARD_API_KEY from environment (default)
    #   api_key=""    → force-disable auth (useful in tests)
    #   api_key="..." → use as-is (testing / programmatic override)
    if api_key is None:
        _effective_api_key: str | None = os.environ.get("DASHBOARD_API_KEY") or None
    elif api_key == "":
        _effective_api_key = None
    else:
        _effective_api_key = api_key
    app.add_middleware(ApiKeyMiddleware, api_key=_effective_api_key)

    register_error_handlers(app)

    # --- Auto-discovered Butler Routers ---
    # Discover and mount roster/{butler}/api/router.py routers
    butler_routers = discover_butler_routers()
    app.state.butler_routers = butler_routers  # Store for wire_db_dependencies

    # --- Core Static Routers ---
    app.include_router(approvals_router)
    app.include_router(butlers_router)
    app.include_router(notifications_router)
    app.include_router(butler_notifications_router)
    app.include_router(issues_router)
    app.include_router(costs_router)
    app.include_router(sessions_router)
    app.include_router(butler_sessions_router)
    app.include_router(schedules_router)
    app.include_router(modules_router)
    app.include_router(secrets_router)
    app.include_router(state_router)
    app.include_router(ingestion_events_router)
    app.include_router(timeline_router)
    app.include_router(calendar_workspace_router)
    app.include_router(search_router)
    app.include_router(audit_router)
    app.include_router(memory_router)
    app.include_router(oauth_router)
    app.include_router(cli_auth_router)
    app.include_router(sse_router)
    app.include_router(catalog_router)
    app.include_router(butler_model_router)

    # --- Auto-discovered Butler Routers ---
    # Mount after static/core routers so dynamic routes cannot shadow
    # fixed API paths like /api/oauth/*.
    for butler_name, router_module in butler_routers:
        app.include_router(router_module.router)
        logger.info(
            "Mounted butler router: %s (prefix=%s)", butler_name, router_module.router.prefix
        )

    @app.get("/api/health")
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- Static file serving (production) ---
    # Mount AFTER all API routes so /api/* always takes precedence.
    resolved_static = static_dir or os.environ.get("DASHBOARD_STATIC_DIR")
    if resolved_static is not None:
        dist_path = Path(resolved_static)
        if dist_path.is_dir():
            app.mount(
                "/",
                StaticFiles(directory=str(dist_path), html=True),
                name="frontend",
            )
            logger.info("Mounted frontend static files from %s", dist_path)
        else:
            logger.warning("static_dir %s does not exist; skipping static mount", dist_path)

    return app
