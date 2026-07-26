"""Dashboard API — FastAPI application factory.

Provides a single-page-of-glass REST API over the butler infrastructure.
The app factory creates a FastAPI instance with:
- CORS middleware (configurable origins)
- Lifespan handler for startup/shutdown of DB pools and MCP clients
- Health endpoints at GET /api/health and GET /health
- Router registration for future endpoint modules
- Optional static file serving for production (frontend/dist/)
"""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from butlers.api.dashboard_audit_middleware import DashboardAuditMiddleware
from butlers.api.deps import (
    get_butler_configs,
    get_db_manager,
    get_mcp_manager,
    get_pricing,
    init_db_manager,
    init_dependencies,
    init_pricing,
    shutdown_db_manager,
    shutdown_dependencies,
    wire_db_dependencies,
)
from butlers.api.lifespan_supervisor import supervise_lifespan_loop
from butlers.api.middleware import ApiKeyMiddleware, register_error_handlers
from butlers.api.router_discovery import discover_butler_routers
from butlers.api.routers.activity_feed import router as activity_feed_router
from butlers.api.routers.approvals import router as approvals_router
from butlers.api.routers.attention_ledger import router as attention_ledger_router
from butlers.api.routers.audit import router as audit_router
from butlers.api.routers.blob_storage import router as blob_storage_router
from butlers.api.routers.butler_logs import router as butler_logs_router
from butlers.api.routers.butler_management import router as butler_management_router
from butlers.api.routers.butlers import router as butlers_router
from butlers.api.routers.calendar_workspace import (
    accounts_router as calendar_accounts_router,
)
from butlers.api.routers.calendar_workspace import (
    export_router as calendar_export_router,
)
from butlers.api.routers.calendar_workspace import (
    router as calendar_workspace_router,
)
from butlers.api.routers.channel_defaults import router as channel_defaults_router
from butlers.api.routers.cli_auth import router as cli_auth_router
from butlers.api.routers.contacts import router as contacts_router
from butlers.api.routers.conversations import router as conversations_router
from butlers.api.routers.dashboard_briefing import router as dashboard_briefing_router
from butlers.api.routers.data_ops import _is_production
from butlers.api.routers.data_ops import router as data_ops_router
from butlers.api.routers.decisions import router as decisions_router
from butlers.api.routers.delegation import router as delegation_router
from butlers.api.routers.domain_events import router as domain_events_router
from butlers.api.routers.events import router as events_router
from butlers.api.routers.general_settings import router as general_settings_router
from butlers.api.routers.google_health import router as google_health_router
from butlers.api.routers.healing import router as healing_router
from butlers.api.routers.home_assistant import router as home_assistant_router
from butlers.api.routers.identity import router as identity_router
from butlers.api.routers.ingestion_connectors import router as ingestion_connectors_router
from butlers.api.routers.ingestion_events import rollup_router as ingestion_rollup_router
from butlers.api.routers.ingestion_events import router as ingestion_events_router
from butlers.api.routers.ingestion_pipeline import router as ingestion_pipeline_router
from butlers.api.routers.issues import router as issues_router
from butlers.api.routers.memory import butler_memory_router
from butlers.api.routers.memory import router as memory_router
from butlers.api.routers.model_settings import (
    butler_model_router,
    catalog_router,
    dispatch_router,
    pricing_router,
)
from butlers.api.routers.modules import router as modules_router
from butlers.api.routers.notifications import (
    butler_notifications_router,
)
from butlers.api.routers.notifications import (
    router as notifications_router,
)
from butlers.api.routers.oauth import router as oauth_router
from butlers.api.routers.owntracks import router as owntracks_router
from butlers.api.routers.permissions import router as permissions_router
from butlers.api.routers.preferences import router as preferences_router
from butlers.api.routers.priority_contacts import router as priority_contacts_router
from butlers.api.routers.provider_settings import router as provider_settings_router
from butlers.api.routers.qa import router as qa_router
from butlers.api.routers.runtime_config import router as runtime_config_router
from butlers.api.routers.schedules import router as schedules_router
from butlers.api.routers.search import router as search_router
from butlers.api.routers.secrets import router as secrets_router
from butlers.api.routers.secrets_v2 import (
    resolve_staleness_window_s,
)
from butlers.api.routers.secrets_v2 import (
    router as secrets_v2_router,
)
from butlers.api.routers.sessions import (
    butler_sessions_router,
)
from butlers.api.routers.sessions import (
    router as sessions_router,
)
from butlers.api.routers.settings_console import (
    router as settings_console_router,
)
from butlers.api.routers.settings_console import (
    run_settings_console_delta_loop,
)
from butlers.api.routers.spend import router as spend_router
from butlers.api.routers.spotify import router as spotify_router
from butlers.api.routers.sse import router as sse_router
from butlers.api.routers.state import router as state_router
from butlers.api.routers.steam import router as steam_router
from butlers.api.routers.system import router as system_router
from butlers.api.routers.telegram_auth import router as telegram_auth_router
from butlers.api.routers.timeline import router as timeline_router
from butlers.api.routers.timeline_saved_views import router as timeline_saved_views_router
from butlers.api.routers.webhooks import router as webhooks_router
from butlers.api.routers.whatsapp import router as whatsapp_router
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_KEY
from butlers.credential_store import CredentialStore
from butlers.db import (
    check_infra_default_creds,
    has_insecure_infra_defaults,
    is_grafana_anon_outside_dev,
)
from butlers.jobs.backup_health import (
    DEFAULT_RESTORE_DRILL_INTERVAL_S,
    run_restore_drill_loop,
)
from butlers.jobs.calendar_sync_deadman import (
    DEFAULT_CALENDAR_DEADMAN_CHECK_INTERVAL_S,
    run_calendar_sync_deadman_loop,
)
from butlers.jobs.deploy_drift import (
    DEFAULT_DRIFT_CHECK_INTERVAL_S,
    run_migration_drift_loop,
)
from butlers.jobs.external_deadman import (
    DEFAULT_DEADMAN_CHECK_INTERVAL_S,
    EXTERNAL_DEADMAN_URL_ENV,
    run_external_deadman_loop,
)
from butlers.jobs.model_verify import (
    DEFAULT_MODEL_VERIFY_INTERVAL_S,
    run_model_verify_loop,
)
from butlers.jobs.secrets_lifecycle import (
    DEFAULT_SCAN_INTERVAL_S,
    run_secrets_lifecycle_loop,
)
from butlers.jobs.secrets_staleness import (
    DEFAULT_STALENESS_SCAN_INTERVAL_S,
    run_secrets_staleness_loop,
)

logger = logging.getLogger(__name__)

_SECRETS_LIFECYCLE_SCAN_INTERVAL_ENV = "SECRETS_LIFECYCLE_SCAN_INTERVAL_S"
_MODEL_VERIFY_INTERVAL_ENV = "MODEL_VERIFY_INTERVAL_S"
_SECRETS_STALENESS_SCAN_INTERVAL_ENV = "SECRETS_STALENESS_SCAN_INTERVAL_S"
_MIGRATION_DRIFT_CHECK_INTERVAL_ENV = "MIGRATION_DRIFT_CHECK_INTERVAL_S"
_EXTERNAL_DEADMAN_CHECK_INTERVAL_ENV = "EXTERNAL_DEADMAN_CHECK_INTERVAL_S"
_CALENDAR_SYNC_DEADMAN_CHECK_INTERVAL_ENV = "CALENDAR_SYNC_DEADMAN_CHECK_INTERVAL_S"
_RESTORE_DRILL_INTERVAL_ENV = "RESTORE_DRILL_INTERVAL_S"


def _resolve_positive_float_env(env_var: str, default: float) -> float:
    """Read a positive-float env var, falling back to ``default`` (with a
    warning) when unset, non-numeric, or non-positive. Shared by the
    secrets-lifecycle and secrets-staleness interval lookups so their fallback
    behavior can never silently drift.
    """
    raw = os.environ.get(env_var, str(default))
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError("value must be a positive number")
    except ValueError:
        logger.warning(
            "%s=%r is not a valid positive number; falling back to default %s",
            env_var,
            raw,
            default,
        )
        return default
    return value


# Strong references to fire-and-forget background tasks spawned from lifespan.
# asyncio only holds a weak reference to a running Task once its creating
# scope returns nothing that keeps it alive; without this, a task can be
# garbage-collected mid-execution. Entries are removed via add_done_callback
# once the task completes (normally or via cancellation).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> asyncio.Task:
    """Register a lifespan background task for the GC-safety keepalive above."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for DB pools and MCP clients.

    On startup: initialize resources (will be implemented in future tasks)
    On shutdown: close all connections cleanly
    """
    # Startup
    init_dependencies()

    # Check infra creds for known-default values (A4 indicator: infra_creds_insecure_default).
    # Dev posture: warns loudly per credential.  Hardened posture: raises RuntimeError.
    check_infra_default_creds()

    # Check for DASHBOARD_EXPORT_SECRET env var (A4 indicator: export_secret_insecure_default).
    if os.environ.get("DASHBOARD_EXPORT_SECRET") in (None, ""):
        if _is_production():
            logger.error(
                "DASHBOARD_EXPORT_SECRET is not set (ENV=%r). "
                "Export token signing will be REFUSED at runtime. "
                "Set DASHBOARD_EXPORT_SECRET to a strong random secret before serving.",
                os.environ.get("ENV", ""),
            )
        else:
            logger.warning(
                "DASHBOARD_EXPORT_SECRET is not set; using dev-mode fallback. "
                "Export tokens are forgeable. Set DASHBOARD_EXPORT_SECRET in production."
            )

    try:
        init_pricing()
    except Exception:
        logger.warning("Failed to load pricing config; cost estimation disabled")

    # Initialize DB pools for all discovered butlers
    butler_configs = get_butler_configs()
    secrets_lifecycle_task: asyncio.Task | None = None
    settings_console_delta_task: asyncio.Task | None = None
    secrets_staleness_task: asyncio.Task | None = None
    migration_drift_task: asyncio.Task | None = None
    calendar_deadman_task: asyncio.Task | None = None
    external_deadman_task: asyncio.Task | None = None
    fleet_events_bridge_task: asyncio.Task | None = None
    restore_drill_task: asyncio.Task | None = None
    model_verify_task: asyncio.Task | None = None
    try:
        await init_db_manager(butler_configs)
        # Wire DB dependencies for both static and dynamic routers
        dynamic_routers = getattr(app.state, "butler_routers", [])
        dynamic_modules = [module for _, module in dynamic_routers]
        wire_db_dependencies(app, dynamic_modules=dynamic_modules)
        logger.info("DatabaseManager initialized for %d butler(s)", len(butler_configs))

        # The Telegram connector authenticates only the approval-callback
        # detail/decision routes with this Tier-1 DB credential. It is separate
        # from the optional generic dashboard API key and never falls back to
        # an environment variable.
        try:
            shared_pool = get_db_manager().credential_shared_pool()
            app.state.approval_callback_connector_token = await CredentialStore(
                shared_pool
            ).resolve(APPROVAL_CALLBACK_CONNECTOR_TOKEN_KEY, env_fallback=False)
            if not app.state.approval_callback_connector_token:
                logger.warning(
                    "Telegram approval callback connector credential is not configured: %s",
                    APPROVAL_CALLBACK_CONNECTOR_TOKEN_KEY,
                )
        except Exception:
            app.state.approval_callback_connector_token = None
            logger.warning(
                "Telegram approval callback connector credential could not be loaded",
                exc_info=True,
            )

        # Restore CLI auth tokens from DB to filesystem
        try:
            from butlers.cli_auth.persistence import restore_tokens

            db_mgr = get_db_manager()
            shared_pool = db_mgr.credential_shared_pool()
            store = CredentialStore(shared_pool)
            results = await restore_tokens(store)
            restored = sum(1 for v in results.values() if v)
            if restored:
                logger.info("Restored %d CLI auth token(s) from DB", restored)
        except Exception:
            logger.debug("CLI auth token restoration skipped", exc_info=True)

        # Proactive secrets lifecycle notifications (bu-1lb5j): a periodic
        # background scan for credentials that newly transitioned into an
        # 'expiring'/'failing'/'expired' state, pushed to the owner instead
        # of waiting for a /secrets page visit. Only started once the
        # DatabaseManager is actually available; sleeps before its first
        # scan (see run_secrets_lifecycle_loop) so it never fires mid-test.
        scan_interval_s = _resolve_positive_float_env(
            _SECRETS_LIFECYCLE_SCAN_INTERVAL_ENV, DEFAULT_SCAN_INTERVAL_S
        )
        secrets_lifecycle_task = _track_background_task(
            supervise_lifespan_loop(
                "secrets_lifecycle",
                lambda: run_secrets_lifecycle_loop(get_db_manager(), interval_s=scan_interval_s),
            )
        )

        # Hourly automated model-catalog verification sweep (bu-hmdqz.2):
        # closes the loop left open by the manual-only "Verify all" button —
        # last_verified_ok/last_verified_at were found 23 days stale for
        # every catalog entry. Sleeps before its first sweep (see
        # run_model_verify_loop) so it never fires real LLM-CLI verification
        # calls mid-test.
        model_verify_interval_s = _resolve_positive_float_env(
            _MODEL_VERIFY_INTERVAL_ENV, DEFAULT_MODEL_VERIFY_INTERVAL_S
        )
        model_verify_task = _track_background_task(
            supervise_lifespan_loop(
                "model_verify",
                lambda: run_model_verify_loop(get_db_manager(), interval_s=model_verify_interval_s),
            )
        )

        # Fleet-events NOTIFY bridge (bu-01r64.1): daemon processes publish
        # session/spend/notification/approval/ingestion events via Postgres NOTIFY
        # (butlers.fleet_events.publish_fleet_event) because they run in a
        # separate container from this process and the in-process event bus
        # below is otherwise invisible to them (see RFC 0022). This bridges
        # those NOTIFYs back into the real emit_event() bus. Guarded in its
        # own try/except so a bridge failure never takes down DatabaseManager
        # init — daemon-originated live events degrade to "missing" rather
        # than the whole dashboard-api failing to start.
        try:
            from butlers.api.fleet_events_bridge import run_fleet_events_listener

            fleet_events_bridge_task = _track_background_task(
                supervise_lifespan_loop(
                    "fleet_events_bridge",
                    run_fleet_events_listener,
                )
            )
        except Exception:
            logger.warning(
                "Failed to start fleet-events NOTIFY bridge; daemon-originated live events "
                "(session/spend/notification/approval/ingestion) will not reach "
                "WS /api/events/stream",
                exc_info=True,
            )

        # Settings Console live updates (bu-3quv8): fans header_delta /
        # attention_add / attention_remove onto the unified fleet event bus
        # (WS /api/events/stream) -- see run_settings_console_delta_loop's
        # docstring. Guarded in its own
        # try/except (rather than folded into the DatabaseManager try above)
        # so a pricing-config load failure can't misleadingly log as a DB
        # init failure; GET /api/settings/console is unaffected either way.
        try:
            settings_console_delta_task = _track_background_task(
                supervise_lifespan_loop(
                    "settings_console_delta",
                    lambda: run_settings_console_delta_loop(
                        butler_configs, get_mcp_manager(), get_pricing(), get_db_manager()
                    ),
                )
            )
        except Exception:
            logger.warning(
                "Failed to start settings-console delta loop; header/attention bus events "
                "disabled (GET /api/settings/console is unaffected)",
                exc_info=True,
            )

        # Background credential-staleness re-probe loop (bu-a63hn): unlike the
        # lifecycle notifier above (which only pushes when a credential
        # transitions into an attention state), this loop actively re-probes
        # any credential whose last_verified is stale so the passport arrives
        # already-verified instead of waiting for a manual click. Dispatches
        # through the exact same probe_* functions the dashboard endpoints
        # use — see butlers.jobs.secrets_staleness for the shared engine.
        staleness_interval_s = _resolve_positive_float_env(
            _SECRETS_STALENESS_SCAN_INTERVAL_ENV, DEFAULT_STALENESS_SCAN_INTERVAL_S
        )
        staleness_window_s = resolve_staleness_window_s(warn_invalid=True)
        secrets_staleness_task = _track_background_task(
            supervise_lifespan_loop(
                "secrets_staleness",
                lambda: run_secrets_staleness_loop(
                    get_db_manager(),
                    interval_s=staleness_interval_s,
                    staleness_s=staleness_window_s,
                ),
            )
        )

        # Migration-drift sentinel (bu-9r3hd.1): hourly comparison of the
        # codebase's Alembic heads against each butler schema's applied
        # alembic_version revisions, escalating to QA once drift persists
        # more than 24h. See butlers.jobs.deploy_drift for the full design
        # rationale (why this process, not a butler daemon's scheduler).
        # Runs independently of the /api/system/drift endpoint, which
        # computes the same comparison live on each request — this loop's
        # job is the escalation side effect, not serving the page.
        try:
            drift_interval_s = _resolve_positive_float_env(
                _MIGRATION_DRIFT_CHECK_INTERVAL_ENV, DEFAULT_DRIFT_CHECK_INTERVAL_S
            )
            migration_drift_task = _track_background_task(
                supervise_lifespan_loop(
                    "migration_drift",
                    lambda: run_migration_drift_loop(get_db_manager(), interval_s=drift_interval_s),
                )
            )
        except Exception:
            logger.warning(
                "Failed to start migration-drift sentinel loop; GET /api/system/drift "
                "is unaffected (it computes the comparison live per request)",
                exc_info=True,
            )

        # Calendar sync deadman (bu-hmdqz.10): periodic cross-schema check that
        # every provider calendar source's sync cursor has stamped within 2x
        # the poll interval, escalating to QA once staleness persists past a
        # second tick. See butlers.jobs.calendar_sync_deadman for the full
        # design rationale (why this process, not a butler daemon's own sync
        # poller loop -- the loop dying silently is exactly the failure mode
        # this check exists to catch).
        try:
            calendar_deadman_interval_s = _resolve_positive_float_env(
                _CALENDAR_SYNC_DEADMAN_CHECK_INTERVAL_ENV,
                DEFAULT_CALENDAR_DEADMAN_CHECK_INTERVAL_S,
            )
            calendar_deadman_task = _track_background_task(
                supervise_lifespan_loop(
                    "calendar_sync_deadman",
                    lambda: run_calendar_sync_deadman_loop(
                        get_db_manager(), interval_s=calendar_deadman_interval_s
                    ),
                )
            )
        except Exception:
            logger.warning("Failed to start calendar-sync-deadman loop", exc_info=True)

        # External deadman (bu-9r3hd.4): periodic outbound ping to an
        # operator-configured URL, catching a silently broken host/egress
        # firewall after a reboot -- see butlers.jobs.external_deadman for
        # the full design rationale. Only started when EXTERNAL_DEADMAN_URL
        # is actually configured; with no target there is nothing useful to
        # do every tick, and InfraStateSource treats "unconfigured" as a
        # legitimate absence rather than a failure.
        deadman_url = os.environ.get(EXTERNAL_DEADMAN_URL_ENV, "").strip()
        if deadman_url:
            try:
                deadman_interval_s = _resolve_positive_float_env(
                    _EXTERNAL_DEADMAN_CHECK_INTERVAL_ENV, DEFAULT_DEADMAN_CHECK_INTERVAL_S
                )
                external_deadman_task = _track_background_task(
                    supervise_lifespan_loop(
                        "external_deadman",
                        lambda: run_external_deadman_loop(
                            get_db_manager(), url=deadman_url, interval_s=deadman_interval_s
                        ),
                    )
                )
            except Exception:
                logger.warning("Failed to start external-deadman ping loop", exc_info=True)
        else:
            logger.info(
                "%s is not set; external-deadman ping loop disabled "
                "(no outside heartbeat monitor configured)",
                EXTERNAL_DEADMAN_URL_ENV,
            )

        # Weekly backup restore drill (bu-9r3hd.5): actually restores the most
        # recent backup into a scratch database and verifies it, rather than
        # trusting a hardcoded "success". See butlers.jobs.backup_health for
        # the full design rationale (why a restore drill can't run inline
        # with GET /api/system/backups). That endpoint's artifact-integrity
        # check (gzip decompression) IS computed live per request; only the
        # expensive, state-mutating restore attempt lives in this loop.
        try:
            restore_drill_interval_s = _resolve_positive_float_env(
                _RESTORE_DRILL_INTERVAL_ENV, DEFAULT_RESTORE_DRILL_INTERVAL_S
            )
            restore_drill_task = _track_background_task(
                supervise_lifespan_loop(
                    "restore_drill",
                    lambda: run_restore_drill_loop(
                        get_db_manager(), interval_s=restore_drill_interval_s
                    ),
                )
            )
        except Exception:
            logger.warning(
                "Failed to start backup restore-drill loop; GET /api/system/backups "
                "will report restore_drill.result='pending' indefinitely",
                exc_info=True,
            )

    except Exception:
        logger.warning("Failed to initialize DatabaseManager; DB endpoints will be unavailable")

    # Signal that lifespan startup has completed.  The health endpoints check
    # this flag and return 503 until startup finishes.
    app.state.ready = True

    yield

    # Shutdown
    app.state.ready = False
    if secrets_lifecycle_task is not None:
        secrets_lifecycle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await secrets_lifecycle_task
    if model_verify_task is not None:
        model_verify_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await model_verify_task
    if fleet_events_bridge_task is not None:
        fleet_events_bridge_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fleet_events_bridge_task
    if settings_console_delta_task is not None:
        settings_console_delta_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await settings_console_delta_task
    if secrets_staleness_task is not None:
        secrets_staleness_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await secrets_staleness_task
    if migration_drift_task is not None:
        migration_drift_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await migration_drift_task
    if calendar_deadman_task is not None:
        calendar_deadman_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await calendar_deadman_task
    if external_deadman_task is not None:
        external_deadman_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await external_deadman_task
    if restore_drill_task is not None:
        restore_drill_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await restore_drill_task
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
        Allowed CORS origins. Defaults to ["http://localhost:41173"] for
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
        _default = os.environ.get("DASHBOARD_CORS_ORIGINS", "http://localhost:41173")
        cors_origins = [o.strip() for o in _default.split(",") if o.strip()]

    app = FastAPI(
        title="Butlers Dashboard API",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Health endpoints return 503 until lifespan startup sets this True.
    app.state.ready = False
    app.state.approval_callback_connector_token = None
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

    # Audit middleware: record every non-GET /api/ mutation to dashboard_audit_log.
    # Registered after CORS so that CORS preflight (OPTIONS) is handled first
    # and audit only fires on genuine mutating requests.
    app.add_middleware(DashboardAuditMiddleware)

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
    app.include_router(attention_ledger_router)
    app.include_router(events_router)
    app.include_router(butler_logs_router)
    app.include_router(butlers_router)
    app.include_router(butler_management_router)
    app.include_router(notifications_router)
    app.include_router(butler_notifications_router)
    app.include_router(issues_router)
    app.include_router(spend_router)
    app.include_router(sessions_router)
    app.include_router(butler_sessions_router)
    app.include_router(activity_feed_router)
    app.include_router(schedules_router)
    app.include_router(modules_router)
    app.include_router(secrets_router)
    app.include_router(secrets_v2_router)
    app.include_router(state_router)
    app.include_router(ingestion_events_router)
    app.include_router(ingestion_rollup_router)
    app.include_router(identity_router)
    app.include_router(ingestion_connectors_router)
    app.include_router(ingestion_pipeline_router)
    app.include_router(priority_contacts_router)
    app.include_router(contacts_router)
    app.include_router(channel_defaults_router)
    app.include_router(ingestion_connectors_router)
    app.include_router(timeline_router)
    app.include_router(timeline_saved_views_router)
    app.include_router(calendar_workspace_router)
    app.include_router(calendar_accounts_router)
    app.include_router(calendar_export_router)
    app.include_router(search_router)
    app.include_router(audit_router)
    app.include_router(memory_router)
    app.include_router(butler_memory_router)
    app.include_router(oauth_router)
    app.include_router(cli_auth_router)
    app.include_router(sse_router)
    app.include_router(catalog_router)
    app.include_router(pricing_router)
    app.include_router(butler_model_router)
    app.include_router(dispatch_router)
    app.include_router(healing_router)
    app.include_router(qa_router)
    app.include_router(provider_settings_router)
    app.include_router(general_settings_router)
    app.include_router(blob_storage_router)
    app.include_router(owntracks_router)
    app.include_router(home_assistant_router)
    app.include_router(spotify_router)
    app.include_router(google_health_router)
    app.include_router(steam_router)
    app.include_router(telegram_auth_router)
    app.include_router(whatsapp_router)
    app.include_router(conversations_router)
    app.include_router(preferences_router)
    app.include_router(runtime_config_router)
    app.include_router(system_router)
    app.include_router(dashboard_briefing_router)
    app.include_router(permissions_router)
    app.include_router(settings_console_router)
    app.include_router(data_ops_router)
    app.include_router(decisions_router)
    app.include_router(delegation_router)
    app.include_router(domain_events_router)
    app.include_router(webhooks_router)

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
        if not app.state.ready:
            return JSONResponse(status_code=503, content={"status": "starting"})
        # Security-posture booleans — NEVER include secret values here.
        #
        # auth.api_key_auth_enabled: True when ApiKeyMiddleware is active.
        #   _effective_api_key is resolved once at create_app() time and
        #   captured via closure, matching exactly what the middleware uses.
        #
        # auth.export_secret_insecure_default: True when DASHBOARD_EXPORT_SECRET
        #   is absent.  In dev the signer falls back to a known constant (forgeable
        #   tokens); in production it refuses to sign.  Either way the posture is
        #   insecure.  Read from env each call so live changes are reflected.
        #
        # security.insecure_infra_defaults: True when any infra credential is at
        #   its known default (absent env var = docker-compose default applies) OR
        #   when Grafana anonymous access is enabled outside dev posture.
        #   Clears only when all infra creds are overridden AND anon access is
        #   disabled (or posture is dev).  Read at request time for live updates.
        #
        # security.role_enforcement_disabled: True when SET ROLE schema-isolation
        #   is NOT active for the managed database connections.  In dev posture
        #   the butler schema isolation layer is disabled (no DB role configured
        #   on the API pools); this clears only when all managed pools have an
        #   active, verified DB role.  Read from the DatabaseManager singleton
        #   so it reflects real connection state established at startup.
        try:
            db_mgr = get_db_manager()
            # Use bool() to guard against non-bool values (e.g. a MagicMock
            # leaked from a test's module-level singleton patch) reaching the
            # JSON response, which would cause a RecursionError in FastAPI's
            # jsonable_encoder.
            role_enforcement_disabled: bool = bool(db_mgr.role_enforcement_disabled)
        except RuntimeError:
            # DatabaseManager not yet initialized (startup path / tests that
            # don't wire a DB).  Conservative default: report as disabled.
            role_enforcement_disabled = True
        return {
            "status": "ok",
            "auth": {
                "api_key_auth_enabled": bool(_effective_api_key),
                "export_secret_insecure_default": not bool(
                    os.environ.get("DASHBOARD_EXPORT_SECRET")
                ),
            },
            "security": {
                "insecure_infra_defaults": has_insecure_infra_defaults()
                or is_grafana_anon_outside_dev(),
                "role_enforcement_disabled": role_enforcement_disabled,
            },
        }

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
