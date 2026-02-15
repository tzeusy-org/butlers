"""Dashboard API — FastAPI application factory.

Provides a single-page-of-glass REST API over the butler infrastructure.
The app factory creates a FastAPI instance with:
- CORS middleware (configurable origins)
- Lifespan handler for startup/shutdown of DB pools and MCP clients
- Health endpoint at GET /api/health
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
    init_db_manager,
    init_dependencies,
    init_pricing,
    shutdown_db_manager,
    shutdown_dependencies,
    wire_db_dependencies,
)
from butlers.api.middleware import register_error_handlers
from butlers.api.router_discovery import discover_butler_routers
from butlers.api.routers.approvals import router as approvals_router
from butlers.api.routers.audit import router as audit_router
from butlers.api.routers.butlers import router as butlers_router
from butlers.api.routers.costs import router as costs_router
from butlers.api.routers.issues import router as issues_router
from butlers.api.routers.memory import router as memory_router
from butlers.api.routers.notifications import (
    butler_notifications_router,
)
from butlers.api.routers.notifications import (
    router as notifications_router,
)
from butlers.api.routers.schedules import router as schedules_router
from butlers.api.routers.search import router as search_router
from butlers.api.routers.sessions import (
    butler_sessions_router,
)
from butlers.api.routers.sessions import (
    router as sessions_router,
)
from butlers.api.routers.sse import router as sse_router
from butlers.api.routers.state import router as state_router
from butlers.api.routers.switchboard_views import router as switchboard_views_router
from butlers.api.routers.timeline import router as timeline_router
from butlers.api.routers.traces import router as traces_router

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
    except Exception:
        logger.warning("Failed to initialize DatabaseManager; DB endpoints will be unavailable")

    yield

    # Shutdown
    await shutdown_db_manager()
    await shutdown_dependencies()


def create_app(
    cors_origins: list[str] | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    cors_origins:
        Allowed CORS origins. Defaults to ["http://localhost:5173"] for
        local Vite dev server.
    static_dir:
        Path to the built frontend directory (e.g. ``frontend/dist/``).
        When set, mounts a ``StaticFiles`` handler at ``/`` with
        ``html=True`` for SPA fallback.  Falls back to the
        ``DASHBOARD_STATIC_DIR`` environment variable.  When neither is
        set, no static mount is registered (development mode).
    """
    if cors_origins is None:
        cors_origins = ["http://localhost:5173"]

    app = FastAPI(
        title="Butlers Dashboard API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    # --- Auto-discovered Butler Routers ---
    # Discover and mount roster/{butler}/api/router.py routers
    butler_routers = discover_butler_routers()
    app.state.butler_routers = butler_routers  # Store for wire_db_dependencies

    for butler_name, router_module in butler_routers:
        app.include_router(router_module.router)
        logger.info(
            "Mounted butler router: %s (prefix=%s)", butler_name, router_module.router.prefix
        )

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
    app.include_router(state_router)
    app.include_router(traces_router)
    app.include_router(timeline_router)
    app.include_router(search_router)
    app.include_router(audit_router)
    app.include_router(memory_router)
    app.include_router(switchboard_views_router)
    app.include_router(sse_router)

    @app.get("/api/health")
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
