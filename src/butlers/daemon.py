"""Butler daemon — the central orchestrator for a single butler instance.

The ButlerDaemon manages the lifecycle of a butler:
1. Load config from butler.toml
2. Initialize telemetry
3. Initialize modules (topological order)
4. Validate module config schemas
5. Validate butler.env credentials (env-only fast-fail for non-secret config)
6. Provision database
7. Run core Alembic migrations
8. Run module Alembic migrations
8b. Create CredentialStore; validate module credentials via DB-first resolution (non-fatal)
9. Module on_startup (topological order)
10. Create Spawner with runtime adapter (verify binary on PATH)
10b. Wire message classification pipeline (switchboard only)
11. Sync TOML schedules to DB
11b. Open MCP client connection to Switchboard (non-switchboard butlers)
12. Create FastMCP server and register core tools
13. Register module MCP tools
13b. Apply approval gates to configured gated tools
14. Start FastMCP SSE server on configured port
15. Launch switchboard heartbeat (non-switchboard butlers)
16. Start internal scheduler loop (calls tick() every tick_interval_seconds)
17. Start liveness reporter (non-switchboard butlers — POST to Switchboard heartbeat endpoint)

On startup failure, already-initialized modules get on_shutdown() called.

Graceful shutdown: (a) stops the MCP server, (b) stops accepting new triggers,
(c) drains in-flight runtime sessions up to a configurable timeout,
(d) cancels switchboard heartbeat, (e) closes Switchboard MCP client,
(f) cancels scheduler loop (waits for in-progress tick() to finish),
(g) cancels liveness reporter loop, (h) shuts down modules in reverse topological order,
(i) closes DB pool.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict
from urllib.parse import quote, quote_plus

import asyncpg
import httpx  # noqa: F401 — re-export; tests patch butlers.daemon.httpx.AsyncClient
import uvicorn
from fastapi import APIRouter
from fastmcp import Client as MCPClient
from fastmcp import FastMCP
from pydantic import ConfigDict, Field, ValidationError
from starlette.routing import Mount, Route

import butlers.background as _background
from butlers.config import (
    ButlerConfig,
    parse_approval_config,
)
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import Complexity
from butlers.core.scheduler import tick as _tick
from butlers.core.spawner import Spawner
from butlers.core.state import state_get as _state_get
from butlers.core.state import state_set as _state_set
from butlers.core.tool_call_capture import (
    get_current_runtime_session_id,
)
from butlers.credential_store import (
    CredentialStore,
    ensure_secrets_schema,
    resolve_owner_entity_info,
    shared_db_name_from_env,
)

# The implementations of many helpers have been extracted into focused modules.
# Names that daemon.py itself uses are imported normally; names that are only
# re-exported for backward compatibility (tests import them from butlers.daemon)
# carry a noqa: F401 comment.
from butlers.daemon_utils import (
    _extract_delivery_id,  # noqa: F401 — re-export only
    _extract_identity_scope_credentials,
    _flatten_config_for_secret_scan,  # noqa: F401 — re-export only (tests import from here)
    _format_validation_error,
)
from butlers.db import Database, schema_search_path
from butlers.exceptions import ChannelEgressOwnershipError
from butlers.guards import _McpRuntimeSessionGuard, _McpSseDisconnectGuard
from butlers.mcp_patches import apply_streamable_http_disconnect_patch
from butlers.mcp_wrappers import _SpanWrappingMCP, _ToolCallLoggingMCP
from butlers.module_state import (
    _MODULE_DISABLED_BY_KEY_SUFFIX,
    _MODULE_ENABLED_KEY_PREFIX,
    _MODULE_ENABLED_KEY_SUFFIX,
    ModuleConfigError,  # noqa: F401 — re-export only
    ModuleRuntimeState,
    ModuleStartupStatus,
)
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.base import Module
from butlers.modules.pipeline import MessagePipeline
from butlers.modules.registry import ModuleRegistry, default_registry
from butlers.owner_bootstrap import (
    _ensure_owner_entity,  # noqa: F401 — re-export only (tests import from here)
)
from butlers.routing_guidance import (
    _INTERACTIVE_ROUTE_CHANNELS,  # noqa: F401 — re-export only
    _PASSIVE_SOURCE_CHANNELS,  # noqa: F401 — re-export only
    _SOURCE_TO_NOTIFY_CHANNEL,  # noqa: F401 — re-export only
    _build_interactive_route_guidance,  # noqa: F401 — re-export only
    _build_non_interactive_route_safety_guidance,  # noqa: F401 — re-export only
    _build_passive_route_guidance,  # noqa: F401 — re-export only
)
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,  # noqa: F401 — re-export; tests import from here and patch butlers.daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY
    _DeterministicScheduleJobHandler,  # noqa: F401 — re-export only
    _resolve_deterministic_schedule_job_name,  # noqa: F401 — re-export; tests import from here
)
from butlers.storage import S3BlobStore

logger = logging.getLogger(__name__)

_MCP_SERVER_START_TIMEOUT_S = 5.0
_MCP_SERVER_START_POLL_INTERVAL_S = 0.01

# Tool surface is now controlled by the core_groups mechanism in the
# runtime_config table (see RFC 0002 §Core Tool Gating via core_groups).
# These constants are retained for backward compatibility with contract tests
# that verify the complete tool surface. They are NOT used for gating logic.
UNIVERSAL_CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "status",
        "trigger",
        "route.execute",
        "tick",
        "state_get",
        "state_set",
        "state_delete",
        "state_list",
        "schedule_list",
        "schedule_create",
        "schedule_update",
        "schedule_delete",
        "schedule_trigger",
        "sessions_list",
        "sessions_get",
        "sessions_summary",
        "sessions_daily",
        "top_sessions",
        "schedule_costs",
        "notify",
        "remind",
        "get_attachment",
        "module.states",
        "module.set_enabled",
        "correct",
        # Added in #1712 and #1714 respectively; always registered on every butler.
        "memory_access",
        "shutdown",
    }
)

MESSENGER_CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "delivery_preferences_set",
        "delivery_preferences_get",
        "deferred_notifications_list",
        "deferred_notification_cancel",
        "scheduling_preferences_set",
        "scheduling_preferences_get",
    }
)

DOMAIN_CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "deadline_create",
        "deadline_update",
        "deadline_list",
        "deadline_delete",
        "event_chain_create",
        "event_chain_update",
        "event_chain_list",
        "event_chain_delete",
        "seasonal_period_create",
        "seasonal_period_update",
        "seasonal_period_list",
        "seasonal_period_delete",
        "seasonal_period_create_preset",
    }
)

# Backwards-compatible alias: all core tools across all butler types.
CORE_TOOL_NAMES: frozenset[str] = (
    UNIVERSAL_CORE_TOOL_NAMES | MESSENGER_CORE_TOOL_NAMES | DOMAIN_CORE_TOOL_NAMES
)

_DEFAULT_TELEGRAM_CHAT_CONTACT_INFO_TYPE = "telegram_chat_id"
_NO_TELEGRAM_CHAT_CONFIGURED_ERROR = (
    "No bot <-> user telegram chat has been configured - please add a "
    "telegram_chat_id entity_info entry on the owner entity via the dashboard"
)


async def _resolve_mcp_tool(mcp: Any, tool_name: str) -> Any | None:
    """Resolve a tool by name via FastMCP public API."""
    get_tool = getattr(mcp, "get_tool", None)
    if not callable(get_tool):
        raise RuntimeError("FastMCP instance does not expose required get_tool(name) API")

    try:
        tool_obj = get_tool(tool_name)
        if inspect.isawaitable(tool_obj):
            tool_obj = await tool_obj
    except KeyError:
        return None
    return tool_obj


class NotifyRequestContextInput(TypedDict):
    """notify.request_context contract passed through to notify.v1."""

    request_id: Annotated[str, Field(description="UUID7 request ID from REQUEST CONTEXT.")]
    source_channel: Annotated[
        str, Field(description="Source channel from REQUEST CONTEXT (for example telegram).")
    ]
    source_endpoint_identity: Annotated[
        str, Field(description="Source endpoint identity from REQUEST CONTEXT.")
    ]
    source_sender_identity: Annotated[
        str, Field(description="Source sender identity from REQUEST CONTEXT.")
    ]
    source_thread_identity: NotRequired[
        Annotated[
            str,
            Field(
                description=(
                    "Required for telegram reply/react intents; identifies the source thread/chat."
                )
            ),
        ]
    ]
    received_at: NotRequired[
        Annotated[str, Field(description="Optional RFC3339 source receive timestamp.")]
    ]


_ROUTE_ERROR_RETRYABLE: dict[str, bool] = {
    "validation_error": False,
    "target_unavailable": True,
    "timeout": True,
    "overload_rejected": True,
    "internal_error": False,
}


class ButlerDaemon:
    """Central orchestrator for a single butler instance."""

    def __init__(
        self,
        config_dir: Path | None = None,
        registry: ModuleRegistry | None = None,
        *,
        butler_name: str | None = None,
        db: Database | None = None,
    ) -> None:
        if config_dir is None and butler_name is None:
            raise ValueError("Either config_dir or butler_name must be provided")
        if config_dir is not None and butler_name is not None:
            raise ValueError("Cannot provide both config_dir and butler_name")

        # If butler_name is provided, derive config_dir from roster/
        if butler_name is not None:
            self.config_dir = Path("roster") / butler_name
        else:
            self.config_dir = config_dir  # type: ignore

        self._registry = registry or default_registry()
        self.config: ButlerConfig | None = None
        self.db: Database | None = db  # Allow injected Database for testing
        self.mcp: FastMCP | None = None
        self.spawner: Spawner | None = None
        self._modules: list[Module] = []
        self._module_statuses: dict[str, ModuleStartupStatus] = {}
        self._module_runtime_states: dict[str, ModuleRuntimeState] = {}
        self._module_configs: dict[str, Any] = {}
        self._gated_tool_originals: dict[str, Any] = {}
        # Maps registered tool name → module name for gating and introspection.
        self._tool_module_map: dict[str, str] = {}
        self._started_at: float | None = None
        self._accepting_connections = False
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._mcp_socket: socket.socket | None = None
        self._switchboard_heartbeat_task: asyncio.Task | None = None
        self._scheduler_loop_task: asyncio.Task | None = None
        self._route_inbox_recovery_task: asyncio.Task | None = None
        self._liveness_reporter_task: asyncio.Task | None = None
        self.switchboard_client: MCPClient | None = None
        self._pipeline: MessagePipeline | None = None
        self._buffer: Any = None  # DurableBuffer instance (switchboard only)
        self._audit_db: Database | None = None  # Switchboard DB for daemon audit logging
        # Switchboard-schema pool (butler_registry) used by the scheduler loop to
        # gate scheduled dispatch on eligibility_state. Set during startup.
        self._audit_pool: asyncpg.Pool | None = None
        self._shared_credentials_db: Database | None = None
        self._credential_store: CredentialStore | None = None
        self.blob_store: S3BlobStore | None = None
        # Background tasks spawned by route.execute accept phase (non-messenger butlers)
        self._route_inbox_tasks: set[asyncio.Task] = set()
        # Root-logger handler that mirrors application logs into butler_logs.
        # Attached after the DB pool is ready (lifecycle step 6b) and detached
        # in shutdown before the pool is closed.
        self._db_log_handler: logging.Handler | None = None

    @property
    def _active_modules(self) -> list[Module]:
        """Return modules that have not failed during startup."""
        return [
            m
            for m in self._modules
            if m.name not in self._module_statuses
            or self._module_statuses[m.name].status == "active"
        ]

    @staticmethod
    def _required_schema_fields(schema: type[Any]) -> list[str]:
        """Return sorted required field names for a Pydantic schema."""
        model_fields = getattr(schema, "model_fields", {})
        required: list[str] = []
        for field_name, field_info in model_fields.items():
            is_required = getattr(field_info, "is_required", None)
            if callable(is_required) and is_required():
                required.append(field_name)
        return sorted(required)

    def _select_startup_modules(self, modules: list[Module]) -> list[Module]:
        """Filter loaded modules to those eligible for startup in this config.

        Modules that define required config fields are only started when an
        explicit ``[modules.<name>]`` section exists in ``butler.toml``.
        This keeps intentionally omitted modules out of the startup path and
        avoids noisy "missing required field" validation warnings.
        """
        if self.config is None:
            return modules

        selected: list[Module] = []
        for mod in modules:
            if mod.name in self.config.modules:
                selected.append(mod)
                continue

            schema = mod.config_schema
            if schema is None:
                selected.append(mod)
                continue

            required_fields = self._required_schema_fields(schema)
            if required_fields:
                logger.info(
                    "Skipping module '%s': no [modules.%s] config provided and schema requires: %s",
                    mod.name,
                    mod.name,
                    ", ".join(required_fields),
                )
                continue

            # Module not in config → always skip (explicit config required)
            logger.info(
                "Skipping module '%s': no [modules.%s] config provided",
                mod.name,
                mod.name,
            )
            continue

        return selected

    def _cascade_module_failures(self) -> None:
        """Mark modules whose dependencies failed as ``cascade_failed``.

        Uses a fixed-point loop: if module B depends on module A and A is
        failed/cascade_failed, B is marked cascade_failed too.  Repeats
        until no new cascades are found.
        """
        failed_names = {
            name
            for name, s in self._module_statuses.items()
            if s.status in ("failed", "cascade_failed")
        }
        changed = True
        while changed:
            changed = False
            for mod in self._modules:
                if mod.name in failed_names:
                    continue
                for dep in mod.dependencies:
                    if dep in failed_names:
                        self._module_statuses[mod.name] = ModuleStartupStatus(
                            status="cascade_failed",
                            phase="dependency",
                            error=f"Dependency '{dep}' failed",
                        )
                        failed_names.add(mod.name)
                        changed = True
                        logger.warning(
                            "Module '%s' cascade-failed: dependency '%s' is unavailable",
                            mod.name,
                            dep,
                        )
                        break

    async def _init_module_runtime_states(self, pool: asyncpg.Pool) -> None:
        """Initialise ``_module_runtime_states`` from startup results + state store.

        For each module:
        - health is derived from ``_module_statuses`` (active / failed / cascade_failed).
        - enabled is read from the state store (key ``module::{name}::enabled``).
          If no stored value exists, healthy modules default to ``True``.
          Failed/cascade_failed modules default to ``False`` and cannot be enabled.

        **Self-healing:** If a module was disabled by a previous startup failure
        (``disabled_by == "failure"``) but is now healthy, it is automatically
        re-enabled.  User-intentional disables (``disabled_by == "user"``) are
        always respected.
        """
        for mod in self._modules:
            startup = self._module_statuses.get(mod.name)
            health = startup.status if startup else "active"
            is_unavailable = health in ("failed", "cascade_failed")

            # Look up sticky state from previous runs
            key = f"{_MODULE_ENABLED_KEY_PREFIX}{mod.name}{_MODULE_ENABLED_KEY_SUFFIX}"
            disabled_by_key = (
                f"{_MODULE_ENABLED_KEY_PREFIX}{mod.name}{_MODULE_DISABLED_BY_KEY_SUFFIX}"
            )
            stored_value = await _state_get(pool, key)

            if is_unavailable:
                # Failed modules are always disabled; persist that to store
                enabled = False
                await _state_set(pool, key, False)
                await _state_set(pool, disabled_by_key, "failure")
            elif stored_value is None:
                # First boot — healthy modules start enabled
                enabled = True
                await _state_set(pool, key, True)
            else:
                enabled = bool(stored_value)
                # Self-healing: module was disabled by a failure but is now
                # healthy — automatically re-enable it.
                if not enabled:
                    disabled_by = await _state_get(pool, disabled_by_key)
                    if disabled_by != "user":
                        logger.info(
                            "Module %r was disabled by a previous failure but is now "
                            "healthy — auto-re-enabling",
                            mod.name,
                        )
                        enabled = True
                        await _state_set(pool, key, True)

            self._module_runtime_states[mod.name] = ModuleRuntimeState(
                health=health,
                enabled=enabled,
                failure_phase=startup.phase if startup else None,
                failure_error=startup.error if startup else None,
            )

    def get_module_states(self) -> dict[str, ModuleRuntimeState]:
        """Return a snapshot of all module runtime states (health + enabled).

        Returns a dict keyed by module name.  Each value is a
        :class:`ModuleRuntimeState` with ``health``, ``enabled``,
        ``failure_phase``, and ``failure_error``.
        """
        return dict(self._module_runtime_states)

    async def set_module_enabled(self, name: str, enabled: bool) -> bool:
        """Toggle the runtime enabled flag for a module.

        Persists the change to the KV state store for cross-restart stickiness.

        Returns ``True`` on success.  Raises ``ValueError`` if the module does
        not exist or is unavailable (failed / cascade_failed) — unavailable
        modules cannot be re-enabled at runtime.
        """
        state = self._module_runtime_states.get(name)
        if state is None:
            raise ValueError(f"Unknown module: {name!r}")

        if state.health in ("failed", "cascade_failed"):
            raise ValueError(
                f"Module {name!r} is unavailable (health={state.health!r}) and cannot be toggled"
            )

        state.enabled = enabled
        if not self.db or not self.db.pool:
            raise RuntimeError("Cannot set module state: database not connected.")
        pool = self.db.pool
        key = f"{_MODULE_ENABLED_KEY_PREFIX}{name}{_MODULE_ENABLED_KEY_SUFFIX}"
        disabled_by_key = f"{_MODULE_ENABLED_KEY_PREFIX}{name}{_MODULE_DISABLED_BY_KEY_SUFFIX}"
        await _state_set(pool, key, enabled)
        # Mark user-intentional disables so self-healing doesn't override them.
        if not enabled:
            await _state_set(pool, disabled_by_key, "user")
        else:
            # Clear the disabled_by marker on re-enable.
            await _state_set(pool, disabled_by_key, None)
        logger.info("Module %r enabled=%s (persisted to state store)", name, enabled)
        return True

    async def start(self) -> None:
        """Execute the full startup sequence.

        Steps execute in order. A failure at any step prevents subsequent steps.
        Module-specific steps (config validation, credentials, migrations,
        on_startup, tool registration) are non-fatal per-module: a failing
        module is recorded as failed and skipped in later phases while the
        butler continues to start with the remaining healthy modules.

        The implementation lives in :mod:`butlers.lifecycle` to keep this file
        focused on class structure.  See :func:`butlers.lifecycle.run_startup`
        for the full step-by-step documentation.
        """
        from butlers.lifecycle import run_startup

        await run_startup(self)

    def _wire_pipelines(self, pool: Any) -> None:
        """Attach a MessagePipeline to modules that support set_pipeline().

        Only the switchboard butler classifies and routes inbound channel
        messages. Other butlers skip pipeline wiring entirely.

        Also creates and starts the DurableBuffer that replaces the unbounded
        asyncio.create_task() dispatch with a bounded in-memory queue.

        The implementation lives in :mod:`butlers.switchboard_wiring` to keep
        this file focused on class structure.
        """
        from butlers.switchboard_wiring import wire_pipelines

        wire_pipelines(self, pool)

    async def _recover_route_inbox(self, pool: asyncpg.Pool) -> None:
        """Re-dispatch route_inbox rows that were accepted but never processed.

        Called on startup to recover from crashes or restarts.  Rows in
        'accepted' state older than the grace period are re-dispatched
        as background tasks through the same path as the hot path.

        The implementation lives in :mod:`butlers.switchboard_wiring` to keep
        this file focused on class structure.
        """
        from butlers.switchboard_wiring import recover_route_inbox

        await recover_route_inbox(self, pool)

    async def _start_mcp_server(self) -> None:
        """Start the FastMCP SSE server as a background asyncio task.

        Pre-creates a TCP socket with SO_REUSEADDR set, then passes it to uvicorn
        via the ``sockets`` parameter so that re-binding after a crash (e.g. sockets
        stuck in TIME_WAIT) does not trigger uvicorn's sys.exit(1) shutdown path.

        The socket is stored on ``self._mcp_socket`` and closed in shutdown after
        the server task finishes.
        """
        app = self._build_mcp_http_app(self.mcp, butler_name=self.config.name)
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.config.port,
            log_level="warning",
            timeout_graceful_shutdown=self.config.shutdown_timeout_s,
        )
        # Pre-create the socket with SO_REUSEADDR so that a previously bound socket
        # in TIME_WAIT (e.g. after SIGKILL) does not block re-binding.  Raising the
        # OSError here (before the asyncio task is running) gives callers a clear,
        # catchable error instead of uvicorn's sys.exit(1).
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.config.port))
        sock.listen(config.backlog)
        self._mcp_socket = sock
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve(sockets=[sock]))
        deadline = time.monotonic() + _MCP_SERVER_START_TIMEOUT_S
        try:
            while not getattr(self._server, "started", False):
                if self._server_task.done():
                    # Surface the underlying exception (or absence thereof) before
                    # converting to a startup-specific RuntimeError below.
                    await self._server_task
                    raise RuntimeError(
                        f"MCP server task exited before startup completed for {self.config.name}"
                    )
                if time.monotonic() >= deadline:
                    self._server.should_exit = True
                    self._server_task.cancel()
                    try:
                        await self._server_task
                    except asyncio.CancelledError:
                        pass
                    raise TimeoutError(
                        "MCP server did not become ready within "
                        f"{_MCP_SERVER_START_TIMEOUT_S:.1f}s for {self.config.name}"
                    )
                await asyncio.sleep(_MCP_SERVER_START_POLL_INTERVAL_S)
        except BaseException:
            # On any failure path (timeout, server-task exit, cancellation),
            # release the pre-bound socket and clear startup state so callers
            # can retry without leaking the listening port.
            self._server_task = None
            self._server = None
            if self._mcp_socket is not None:
                self._mcp_socket.close()
                self._mcp_socket = None
            raise

    @staticmethod
    def _route_signature(route: Any) -> tuple[str, str | None, tuple[str, ...] | None]:
        methods = getattr(route, "methods", None)
        normalized_methods = tuple(sorted(str(method) for method in methods)) if methods else None
        return (type(route).__name__, getattr(route, "path", None), normalized_methods)

    @staticmethod
    def _attach_route_via_public_api(target: Any, route: Any) -> bool:
        if isinstance(route, Mount) and hasattr(target, "mount"):
            target.mount(path=route.path, app=route.app, name=route.name)
            return True

        if isinstance(route, Route):
            methods = sorted(route.methods) if route.methods else None
            add_api_route = getattr(target, "add_api_route", None)
            if callable(add_api_route):
                add_api_route(
                    route.path,
                    endpoint=route.endpoint,
                    methods=methods,
                    name=route.name,
                    include_in_schema=getattr(route, "include_in_schema", True),
                )
                return True

            add_route = getattr(target, "add_route", None)
            if callable(add_route):
                add_route(route.path, route.endpoint, methods=methods, name=route.name)
                return True

        return False

    @classmethod
    def _build_mcp_http_app(cls, mcp: FastMCP, *, butler_name: str) -> Any:
        """Build a unified ASGI app exposing streamable HTTP and legacy SSE MCP routes."""
        apply_streamable_http_disconnect_patch()
        # Codex and other modern MCP clients use streamable HTTP at /mcp.
        streamable_app = mcp.http_app(path="/mcp", transport="streamable-http")
        # Existing internal clients still use SSE at /sse + /messages.
        sse_app = mcp.http_app(path="/sse", transport="sse")

        supports_include_router = hasattr(streamable_app, "include_router")
        sse_router = APIRouter() if supports_include_router else None
        seen_routes = {cls._route_signature(route) for route in streamable_app.routes}
        for route in sse_app.routes:
            signature = cls._route_signature(route)
            if signature in seen_routes:
                continue
            if sse_router is not None:
                # Include-router keeps route operations, but mounted sub-apps
                # (e.g. /messages for SSE) must be attached to the parent app.
                target = streamable_app if isinstance(route, Mount) else sse_router
                if not cls._attach_route_via_public_api(target, route):
                    target.routes.append(route)
            else:
                if not cls._attach_route_via_public_api(streamable_app, route):
                    streamable_app.routes.append(route)
            seen_routes.add(signature)
        if sse_router is not None:
            streamable_app.include_router(sse_router)

        # Add a /health readiness probe endpoint.  Connectors (telegram, gmail)
        # poll this before starting their ingestion loops to avoid delivering
        # messages into a ConnectionError while the MCP server is still starting.
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        async def _health_endpoint(request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok"})

        health_route = Route("/health", _health_endpoint, methods=["GET"])
        if not cls._attach_route_via_public_api(streamable_app, health_route):
            streamable_app.routes.append(health_route)

        guarded_app = _McpRuntimeSessionGuard(streamable_app, butler_name=butler_name)
        return _McpSseDisconnectGuard(guarded_app, butler_name=butler_name)

    async def _create_audit_pool(self, own_pool: asyncpg.Pool) -> asyncpg.Pool | None:
        """Create or reuse a connection pool for daemon-side audit logging.

        The switchboard butler reuses its own pool. Other butlers open a small
        dedicated pool to the switchboard schema in the shared ``butlers`` DB.

        Returns ``None`` (with a warning) if the pool cannot be created.
        """
        # Intentional name check: the switchboard IS the audit schema owner. Reusing its own
        # pool avoids a redundant connection. This is switchboard-specific, not staffer-generic.
        if self.config.name == "switchboard":
            return own_pool

        try:
            audit_db_name = self.config.db_name or "butlers"
            audit_db_schema = "switchboard"
            audit_db = Database.from_env(audit_db_name)
            if audit_db is self.db:
                # Same DB object — reuse the existing pool directly (avoids double-close
                # on shutdown when the audit DB and main DB share the same connection).
                return own_pool
            audit_db.set_schema(audit_db_schema)
            audit_db.min_pool_size = 1
            audit_db.max_pool_size = 2
            await audit_db.connect()
            self._audit_db = audit_db
            logger.info(
                "Audit pool connected (db=%s, schema=%s)",
                audit_db_name,
                audit_db_schema or "<default>",
            )
            return audit_db.pool
        except Exception:
            logger.warning(
                "Failed to create audit pool for %s; daemon audit logging disabled",
                self.config.name,
                exc_info=True,
            )
            return None

    async def _connect_switchboard(self) -> None:
        """Open an MCP client connection to the Switchboard butler.

        Skips connection for the Switchboard butler itself (it IS the
        Switchboard) and when no ``switchboard_url`` is configured.

        Connection failures are logged as warnings but do not prevent
        butler startup — the butler can operate without the Switchboard,
        though the ``notify()`` tool will return errors until the
        connection is established.

        The FastMCP Client is entered as a long-lived async context
        manager (via ``__aenter__``). ``_disconnect_switchboard`` calls
        ``__aexit__`` to clean up.

        The implementation lives in :mod:`butlers.switchboard_wiring` to keep
        this file focused on class structure.
        """
        from butlers.switchboard_wiring import connect_switchboard

        await connect_switchboard(self)

    async def _disconnect_switchboard(self) -> None:
        """Close the Switchboard MCP client connection if open.

        The implementation lives in :mod:`butlers.switchboard_wiring` to keep
        this file focused on class structure.
        """
        from butlers.switchboard_wiring import disconnect_switchboard

        await disconnect_switchboard(self)

    async def _resolve_default_notify_recipient(
        self,
        *,
        channel: str,
        intent: str,
        recipient: str | None,
        request_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Resolve notify recipient with progressive fallback.

        Resolution order:
        1. Explicit ``recipient`` string → use as-is.
        2. ``request_context.source_endpoint_identity`` for matching channel
           → extract identifier (e.g. ``telegram:12345`` → ``12345``).
        3. Owner entity lookup via ``public.entity_info`` (Telegram send only).
        """
        resolved_recipient = recipient.strip() if isinstance(recipient, str) else None
        if resolved_recipient:
            return resolved_recipient

        # Try extracting from request_context (the sender's channel identity).
        if request_context is not None:
            endpoint = request_context.get("source_endpoint_identity", "")
            if isinstance(endpoint, str) and endpoint.startswith(f"{channel}:"):
                extracted = endpoint[len(channel) + 1 :]
                if extracted:
                    return extracted

        if channel != "telegram" or intent not in ("send", "insight"):
            return None

        pool = self.db.pool if self.db is not None else None
        if pool is not None:
            chat_id = await resolve_owner_entity_info(
                pool, _DEFAULT_TELEGRAM_CHAT_CONTACT_INFO_TYPE
            )
            if chat_id:
                return chat_id.strip() or None

        return None

    # Maps notify channel names to the entity_facts predicate used for delivery.
    # Channels that collapse to ``has-handle`` (e.g. ``telegram``) require an
    # additional object-value filter to avoid cross-platform ambiguity — see
    # ``_CHANNEL_HANDLE_PREFIX`` below.
    _CHANNEL_TO_PREDICATE: dict[str, str] = {
        "telegram": "has-handle",
        "email": "has-email",
        "phone": "has-phone",
        "sms": "has-phone",
    }

    # Telegram ``telegram_user_id`` entries in contact_info are written to
    # entity_facts as ``has-handle`` with object value ``telegram:<numeric_id>``.
    # This prefix disambiguates telegram from other ``has-handle`` entries
    # (e.g. linkedin, twitter, website handles).  For delivery, the numeric
    # part after the prefix is returned as the Telegram chat/user ID.
    _TELEGRAM_HANDLE_PREFIX = "telegram:"

    # Kept for use by ``_notifications.py`` error messages (references the
    # CI-type name for user-facing error text).
    _CHANNEL_TO_CONTACT_INFO_TYPE: dict[str, str] = {
        "telegram": "telegram_chat_id",
    }

    async def _resolve_entity_channel_identifier(
        self, *, entity_id: uuid.UUID, channel: str, msg_context: str | None = None
    ) -> str | None:
        """Resolve the channel identifier for a specific entity_id and channel type.

        Reads directly from ``relationship.entity_facts`` keyed on the entity.

        Resolution:
        - channel → predicate (``_CHANNEL_TO_PREDICATE``)
        - For ``telegram``: queries ``has-handle`` WHERE object starts with
          ``"telegram:"`` (the format written by the reconciler for
          ``telegram_user_id`` entries).  Returns the numeric part after the
          prefix, which equals the Telegram user/chat ID used for delivery.
          This prefix disambiguates Telegram from other ``has-handle`` entries
          (linkedin, twitter, etc.).
        - For ``email``/``phone``/``sms``: queries the corresponding predicate
          and returns the raw object value.

        Note on ``msg_context``: ``relationship.entity_facts`` has no ``context``
        column, so context-preference ordering (preferring work vs. personal
        addresses) is not preserved in this read path.  ``msg_context`` is still
        used downstream by the email guard (``check_email_recipient``) for
        context validation — only the context-aware *ordering* during resolution
        is dropped.

        Returns the identifier value on success, ``None`` if:
        - No DB pool is available.
        - No matching ``entity_facts`` row exists.
        - The ``relationship.entity_facts`` table does not exist (graceful
          schema-not-ready guard).
        - The executing role cannot read ``relationship.entity_facts`` due to
          schema isolation.
        """
        from butlers.identity import _CHANNEL_TYPE_TO_PREDICATE

        predicate = self._CHANNEL_TO_PREDICATE.get(channel)
        if predicate is None:
            # Channel has no known predicate mapping — cannot resolve via entity_facts.
            predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel)
        if predicate is None:
            logger.debug(
                "_resolve_entity_channel_identifier: no predicate for channel=%r; returning None",
                channel,
            )
            return None

        pool = self.db.pool if self.db is not None else None
        if pool is None:
            return None

        try:
            async with pool.acquire() as conn:
                # Query entity_facts for the active triple.
                # For telegram, filter to entries with the "telegram:" prefix
                # to avoid ambiguity with other has-handle entries (linkedin, etc.).
                # rel_019 normalised all legacy telegram has-handle rows to the
                # "telegram:" prefix in production, so no verbatim/unprefixed
                # fallback is required (bu-3nu0x).
                if channel == "telegram" and predicate == "has-handle":
                    row = await conn.fetchrow(
                        """
                        SELECT ef.object
                        FROM relationship.entity_facts ef
                        WHERE ef.subject    = $1
                          AND ef.predicate  = $2
                          AND ef.object LIKE $3
                          AND ef.object_kind = 'literal'
                          AND ef.validity   = 'active'
                        ORDER BY ef."primary" DESC NULLS LAST, ef.created_at ASC
                        LIMIT 1
                        """,
                        entity_id,
                        predicate,
                        self._TELEGRAM_HANDLE_PREFIX + "%",
                    )
                    if row is None:
                        return None
                    raw = row["object"]
                    if raw and raw.startswith(self._TELEGRAM_HANDLE_PREFIX):
                        # Strip prefix; return the numeric Telegram user/chat ID.
                        numeric = raw[len(self._TELEGRAM_HANDLE_PREFIX) :].strip()
                        return numeric or None
                    return None
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT ef.object
                        FROM relationship.entity_facts ef
                        WHERE ef.subject    = $1
                          AND ef.predicate  = $2
                          AND ef.object_kind = 'literal'
                          AND ef.validity   = 'active'
                        ORDER BY ef."primary" DESC NULLS LAST, ef.created_at ASC
                        LIMIT 1
                        """,
                        entity_id,
                        predicate,
                    )
                    if row is None:
                        return None
                    value = row["object"]
                    if not value:
                        return None
                    stripped = value.strip()
                    return stripped or None

        except Exception as exc:  # noqa: BLE001
            from butlers.credential_store import (
                _is_missing_column_or_schema_error,
                _is_missing_table_error,
            )

            if (
                _is_missing_table_error(exc)
                or _is_missing_column_or_schema_error(exc)
                or isinstance(exc, asyncpg.InsufficientPrivilegeError)
            ):
                logger.debug(
                    "_resolve_entity_channel_identifier skipped for entity_id=%s channel=%r; "
                    "relationship entity facts unavailable: %s",
                    entity_id,
                    channel,
                    exc,
                )
                return None
            raise

    async def _dispatch_scheduled_task(
        self,
        *,
        trigger_source: str,
        prompt: str | None = None,
        job_name: str | None = None,
        job_args: dict[str, Any] | None = None,
        complexity: Complexity = Complexity.WORKHORSE,
        max_token_budget: int | None = None,
    ) -> Any:
        """Dispatch one scheduled task via deterministic jobs or prompt fallback.

        Thin wrapper — implementation lives in :func:`butlers.background.dispatch_scheduled_task`.
        """
        return await _background.dispatch_scheduled_task(
            butler_name=self.config.name,
            pool=self.db.pool if self.db is not None else None,
            spawner=self.spawner,
            trigger_source=trigger_source,
            prompt=prompt,
            job_name=job_name,
            job_args=job_args,
            complexity=complexity,
            max_token_budget=max_token_budget,
        )

    async def _scheduler_loop(self) -> None:
        """Periodically call tick() to dispatch due scheduled tasks.

        Thin wrapper — implementation lives in :func:`butlers.background.scheduler_loop`.

        On cancellation (graceful shutdown):
        - If sleeping between ticks, the loop exits immediately.
        - If a tick() call is in-progress, ``asyncio.shield()`` wraps the inner
          task so that the CancelledError interrupts only the await but the
          tick itself continues running; the loop then awaits the shielded task
          to let the in-progress tick() finish before exiting.
        """
        if self.db is None or self.db.pool is None or self.spawner is None:
            logger.warning("Scheduler loop: DB or spawner not ready, loop will not run")
            return

        # Resolve the owner's general timezone so hour-pinned crons fire at the
        # intended local time, failing open to UTC.  Resolved once at loop
        # start; a timezone change takes effect after the next daemon restart,
        # consistent with other cold scheduler config.
        from butlers.core.general_settings import resolve_general_timezone

        shared_pool = (
            self._credential_store.shared_pool if self._credential_store is not None else None
        )
        default_timezone = await resolve_general_timezone(shared_pool)

        # Build butler-specific completion hooks.
        # The chronicler day-close hook persists the prose output to tier2_cache.
        # It must close the day in the owner's timezone — the cron fires at 01:05
        # local, so a UTC-based window would be off by a local day (#2681).
        completion_hooks = None
        if self.config.name == "chronicler":
            from butlers.chronicler.day_close_writer import build_day_close_completion_hooks

            completion_hooks = build_day_close_completion_hooks(
                self.db.pool, timezone=default_timezone
            )

        daemon = self
        await _background.scheduler_loop(
            pool=self.db.pool,
            dispatch_fn=self._dispatch_scheduled_task,
            interval=self.config.scheduler.tick_interval_seconds,
            butler_name=self.config.name,
            tick_fn=_tick,
            get_switchboard_client=lambda: daemon.switchboard_client,
            get_db=lambda: daemon.db,
            completion_hooks=completion_hooks,
            get_eligibility_pool=lambda: daemon._audit_pool,
            default_timezone=default_timezone,
        )

    async def _liveness_reporter_loop(self) -> None:
        """Periodically POST to the Switchboard's heartbeat endpoint to signal liveness.

        Thin wrapper — implementation lives in :func:`butlers.background.liveness_reporter_loop`.

        Connection failures are logged at WARNING level — transient unavailability is
        expected (e.g., Switchboard not yet started) and does not break the loop.

        On cancellation (graceful shutdown), the loop exits cleanly.
        """
        await _background.liveness_reporter_loop(
            butler_name=self.config.name,
            url=f"{self.config.scheduler.switchboard_url}/api/switchboard/heartbeat",
            interval=self.config.scheduler.heartbeat_interval_seconds,
            butler_type_value=self.config.type.value,
        )

    async def _switchboard_heartbeat_loop(self) -> None:
        """Periodically check and re-establish the Switchboard connection.

        All exceptions (except ``CancelledError``) are swallowed so that the
        heartbeat never crashes the butler.

        The implementation lives in :mod:`butlers.switchboard_wiring` to keep
        this file focused on class structure.
        """
        from butlers.switchboard_wiring import switchboard_heartbeat_loop

        await switchboard_heartbeat_loop(self)

    def _collect_module_credentials(self) -> dict[str, list[str]]:
        """Collect credentials_env from enabled modules.

        Sources (in priority order):
        1. ``credentials_env`` declared in butler.toml under ``[modules.<name>]``
        2. Identity-scoped ``user``/``bot`` config sections (if present/enabled)
        3. Module class ``credentials_env`` property (fallback)

        This aligns with the spec: credential declarations are config-driven
        via butler.toml, with the module class providing defaults.
        """
        creds: dict[str, list[str]] = {}
        loaded_modules = {mod.name: mod for mod in self._modules}
        for mod_name, mod_cfg in self.config.modules.items():
            # 1. Check TOML config first (spec-driven)
            toml_creds = mod_cfg.get("credentials_env")
            if toml_creds is not None:
                if isinstance(toml_creds, str):
                    creds[mod_name] = [toml_creds] if toml_creds else []
                elif isinstance(toml_creds, list):
                    creds[mod_name] = [
                        item for item in toml_creds if isinstance(item, str) and item
                    ]
                else:
                    logger.warning(
                        "Ignoring invalid type for credentials_env in module '%s' config. "
                        "Expected a string or list of strings, but got %s.",
                        mod_name,
                        type(toml_creds).__name__,
                    )
                    creds[mod_name] = []
                continue

            # 2. Extract identity-scoped env vars from validated config.
            validated_cfg = self._module_configs.get(mod_name)
            scoped_creds = _extract_identity_scope_credentials(mod_name, validated_cfg)
            if scoped_creds:
                creds.update(scoped_creds)
                continue

            # 3. Fallback to module class property
            mod = loaded_modules.get(mod_name)
            if mod is not None:
                env_list = getattr(mod, "credentials_env", [])
                if env_list:
                    creds[mod_name] = list(env_list)
        return creds

    def _build_db_url(self) -> str:
        """Build SQLAlchemy-compatible DB URL from Database config."""
        db = self.db
        user = quote(db.user, safe="")
        password = quote(db.password, safe="")
        db_name = quote(db.db_name, safe="")
        base = f"postgresql://{user}:{password}@{db.host}:{db.port}/{db_name}"
        schema = db.schema if isinstance(db.schema, str) else None
        search_path = schema_search_path(schema)
        if search_path is None:
            return base
        options = quote_plus(f"-csearch_path={search_path}")
        return f"{base}?options={options}"

    async def _check_health(self) -> str:
        """Check health of all core components.

        Returns 'ok' when all components are healthy, 'degraded' when the DB
        pool is unavailable or any module has a non-active status.
        """
        try:
            pool = self.db.pool if self.db else None
            if pool is None:
                return "degraded"
            await pool.fetchval("SELECT 1")
        except Exception:
            logger.warning("Health check failed: DB pool unavailable")
            return "degraded"

        # Any failed module degrades overall health.
        if any(s.status != "active" for s in self._module_statuses.values()):
            return "degraded"

        return "ok"

    def _register_core_tools(self) -> None:
        """Register all core MCP tools on the FastMCP server.

        Every tool handler is wrapped with a tool_span that creates a
        butler.tool.<name> span with a butler.name attribute.

        Tool definitions live in butlers.core_tools, grouped by domain.
        This method is a thin dispatcher: it builds the shared ToolContext
        and _core_tool factory, then delegates to register_all_core_tools.
        """
        from butlers.core_tools import ToolContext, register_all_core_tools

        butler_name = self.config.name
        butler_type = self.config.type
        mcp = _ToolCallLoggingMCP(self.mcp, butler_name, module_name="core")
        _route_metrics = ButlerMetrics(butler_name=butler_name)

        # Group-aware core tool decorator — mirrors the module _tool(group) pattern.
        # When core_groups is None (default), all groups are enabled (backward compat).
        # When set, only tools in the listed groups are registered on the MCP server.
        # Read from the RuntimeConfigAccessor (DB-backed, seeded from toml on first boot).
        _accessor = getattr(self, "_runtime_config_accessor", None)
        if _accessor is not None and _accessor._cache is not None:
            _core_groups = _accessor._cache.core_groups
        else:
            _core_groups = self.config.runtime_seed.core_groups

        # Name-gated groups: only effective for specific butlers.
        _name_gated_groups = {
            "switchboard_routing": "switchboard",
            "switchboard_backfill": "switchboard",
        }

        # Log warnings for ineffective group inclusions
        if _core_groups is not None:
            for group in _core_groups:
                required_name = _name_gated_groups.get(group)
                if required_name and butler_name != required_name:
                    logger.warning(
                        "core_groups includes '%s' but butler_name='%s' (only effective "
                        "for '%s'); group will have no effect",
                        group,
                        butler_name,
                        required_name,
                    )

        def _core_tool(group: str, **tool_kwargs):
            if _core_groups is None or group in _core_groups:
                return mcp.tool(**tool_kwargs)
            return lambda fn: fn

        ctx = ToolContext(
            daemon=self,
            pool=self.db.pool,
            spawner=self.spawner,
            butler_name=butler_name,
            butler_type=butler_type,
            is_switchboard=butler_name == "switchboard",
            is_messenger=butler_name == "messenger",
            route_metrics=_route_metrics,
        )
        register_all_core_tools(ctx, mcp, _core_tool)

    def _validate_module_configs(self) -> dict[str, Any]:
        """Validate each module's raw config dict against its config_schema.

        Returns a mapping of module name to validated Pydantic model instance.
        If a module has no config_schema (returns None), the raw dict is passed
        through for backward compatibility.

        Extra fields not declared in the schema are rejected. Missing required
        fields and type mismatches produce clear error messages.

        Modules that fail validation are recorded in ``_module_statuses``
        and excluded from later startup phases (non-fatal).
        """
        validated: dict[str, Any] = {}
        # Keys consumed at the butler level (not part of module schemas)
        _BUTLER_LEVEL_KEYS = {"credentials_env", "enabled"}
        for mod in self._modules:
            raw_config = {
                k: v
                for k, v in self.config.modules.get(mod.name, {}).items()
                if k not in _BUTLER_LEVEL_KEYS
            }
            schema = mod.config_schema
            if schema is None:
                validated[mod.name] = raw_config
                continue
            # Create a strict variant that forbids extra fields, unless the
            # schema already configures its own extra handling.
            effective_schema = schema
            current_extra = schema.model_config.get("extra")
            if current_extra is None:
                effective_schema = type(
                    f"{schema.__name__}Strict",
                    (schema,),
                    {"model_config": ConfigDict(extra="forbid")},
                )
            try:
                validated[mod.name] = effective_schema.model_validate(raw_config)
            except ValidationError as exc:
                error_msg = _format_validation_error(
                    f"Config validation failed for module '{mod.name}'", exc
                )
                self._module_statuses[mod.name] = ModuleStartupStatus(
                    status="failed", phase="config", error=error_msg
                )
                logger.warning("Module '%s' disabled: %s", mod.name, error_msg)
        return validated

    async def _register_module_tools(self) -> None:
        """Register MCP tools from all loaded modules.

        Skips modules that have already been marked as failed.  Tool
        registration failures are non-fatal: the module is recorded as
        failed and skipped.

        Module tools are registered through a ``_SpanWrappingMCP`` proxy that
        automatically wraps each tool handler with a ``butler.tool.<name>``
        span carrying the ``butler.name`` attribute.
        """
        for mod in self._modules:
            mod_status = self._module_statuses.get(mod.name)
            if mod_status is not None and mod_status.status != "active":
                continue

            try:
                wrapped_mcp = _SpanWrappingMCP(
                    self.mcp,
                    self.config.name,
                    module_name=mod.name,
                    module_runtime_states=self._module_runtime_states,
                    is_messenger=self.config.name == "messenger",
                )
                validated_config = self._module_configs.get(mod.name)
                await mod.register_tools(
                    wrapped_mcp, validated_config, self.db, butler_name=self.config.name
                )
                # Record tool → module mapping for introspection and gating.
                for tool_name in wrapped_mcp._registered_tool_names:
                    self._tool_module_map[tool_name] = mod.name
            except ChannelEgressOwnershipError:
                # Security guard: a non-messenger butler tried to grab channel
                # egress. Fail loud — do not silently disable and continue.
                raise
            except Exception as exc:
                error_msg = str(exc)
                self._module_statuses[mod.name] = ModuleStartupStatus(
                    status="failed", phase="tools", error=error_msg
                )
                logger.warning(
                    "Module '%s' disabled: tool registration failed: %s", mod.name, error_msg
                )

        # Allow modules to cross-wire after all tools are registered.
        module_map = {mod.name: mod for mod in self._modules}
        for mod in self._modules:
            on_ready = getattr(mod, "on_all_modules_ready", None)
            if on_ready is not None:
                try:
                    on_ready(module_map)
                except Exception as exc:
                    logger.warning("Module '%s' on_all_modules_ready failed: %s", mod.name, exc)

    async def _apply_approval_gates(self) -> dict[str, Any]:
        """Parse approval config and wrap gated tools with approval interception.

        Parses the ``[modules.approvals]`` section from the butler config,
        then calls ``apply_approval_gates`` to wrap tools whose names appear
        in the ``gated_tools`` configuration.

        Returns the mapping of tool_name -> original handler for gated tools.
        """
        approvals_raw = self.config.modules.get("approvals")
        approval_config = parse_approval_config(approvals_raw)

        if approval_config is None or not approval_config.enabled:
            return {}

        pool = self.db.pool
        originals = await apply_approval_gates(self.mcp, approval_config, pool, self.config.name)

        for mod in self._active_modules:
            if mod.name == "approvals" and hasattr(mod, "set_approval_policy"):
                mod.set_approval_policy(approval_config)
                break

        # Wire the originals into the ApprovalsModule if it's loaded,
        # so the post-approval executor can invoke them directly
        if originals:
            for mod in self._active_modules:
                if mod.name == "approvals":
                    # Set up a tool executor that calls the original tool function
                    async def _execute_original(
                        tool_name: str,
                        tool_args: dict[str, Any],
                        _originals: dict[str, Any] = originals,
                    ) -> dict[str, Any]:
                        original_fn = _originals.get(tool_name)
                        if original_fn is None:
                            tool_obj = await _resolve_mcp_tool(self.mcp, tool_name)
                            if tool_obj is None:
                                return {"error": f"No handler for tool: {tool_name}"}
                            original_fn = tool_obj.fn
                        return await original_fn(**tool_args)

                    mod.set_tool_executor(_execute_original)
                    break

            logger.info(
                "Applied approval gates to %d tool(s): %s",
                len(originals),
                ", ".join(sorted(originals.keys())),
            )

        return originals

    def _wire_calendar_approval_enqueuer(self) -> None:
        """Wire calendar overlap-approval enqueuer when both modules are loaded.

        When both the ``calendar`` and ``approvals`` modules are active on this
        butler, connects the calendar module's overlap-override gate to the
        approvals pending-action queue via a lightweight enqueue callback.
        """
        approvals_raw = self.config.modules.get("approvals")
        approval_config = parse_approval_config(approvals_raw)
        if approval_config is None or not approval_config.enabled:
            return

        calendar_mod = None
        for mod in self._active_modules:
            if mod.name == "calendar":
                calendar_mod = mod
                break

        if calendar_mod is None:
            return

        # Only wire if the calendar module exposes the setter.
        set_enqueuer = getattr(calendar_mod, "set_approval_enqueuer", None)
        if not callable(set_enqueuer):
            return

        pool = self.db.pool
        expiry_hours = approval_config.default_expiry_hours

        async def _enqueue_overlap_action(
            tool_name: str,
            tool_args: dict[str, Any],
            agent_summary: str,
        ) -> str:
            """Insert a pending_actions row for a calendar overlap override."""
            import uuid as _uuid
            from datetime import UTC as _UTC
            from datetime import datetime as _dt
            from datetime import timedelta as _td

            from butlers.modules.approvals.events import (
                ApprovalEventType,
                record_approval_event,
            )
            from butlers.modules.approvals.models import ActionStatus

            action_id = _uuid.uuid4()
            now = _dt.now(_UTC)
            expires_at = now + _td(hours=expiry_hours)

            await pool.execute(
                "INSERT INTO pending_actions "
                "(id, tool_name, tool_args, agent_summary, session_id, status, "
                "requested_at, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                action_id,
                tool_name,
                json.dumps(tool_args),
                agent_summary,
                get_current_runtime_session_id(),
                ActionStatus.PENDING.value,
                now,
                expires_at,
            )
            await record_approval_event(
                pool,
                ApprovalEventType.ACTION_QUEUED,
                actor="system:calendar_overlap_gate",
                action_id=action_id,
                reason="calendar overlap override requires approval",
                metadata={"tool_name": tool_name},
                occurred_at=now,
            )

            logger.info(
                "Calendar overlap override enqueued for approval (action=%s, tool=%s)",
                action_id,
                tool_name,
            )
            return str(action_id)

        set_enqueuer(_enqueue_overlap_action)
        logger.info("Wired calendar overlap-approval enqueuer via approvals module")

    def _wire_module_runtime(self) -> None:
        """Wire spawner and switchboard_client into modules that define wire_runtime().

        Called after ``_connect_switchboard()`` (step 11b) and
        ``_register_module_tools()`` (step 13) so that both the spawner and the
        switchboard client are already set when the modules receive their
        runtime references.

        Modules that do not define ``wire_runtime`` are silently skipped.
        Failures are non-fatal: a warning is logged and startup continues so
        that one misconfigured module cannot prevent the butler from serving.

        The repo root is located by walking up from ``config_dir`` until a
        ``pyproject.toml`` marker is found.  This handles both the standard
        ``roster/<butler-name>/`` layout and arbitrary config directories passed
        in tests or custom deployments.  Falls back to ``config_dir.parent``
        if no marker is found.
        """
        if self.spawner is None:
            logger.debug("_wire_module_runtime: spawner not yet set — skipping")
            return

        # Register the daemon's spawner in the core hook so modules (QaModule,
        # SelfHealingModule) can retrieve it at dispatch time without holding a
        # direct reference on their __init__ (Vision Rule 2).
        from butlers.core.spawn_hooks import register_spawner

        register_spawner(self.spawner)

        # Walk up from config_dir to find the repo root (marked by pyproject.toml).
        _candidate = self.config_dir.resolve()
        repo_root = _candidate.parent  # fallback: one level up
        for _parent in [_candidate, *_candidate.parents]:
            if (_parent / "pyproject.toml").exists():
                repo_root = _parent
                break

        for mod in self._active_modules:
            wire_fn = getattr(mod, "wire_runtime", None)
            if wire_fn is None or not callable(wire_fn):
                continue
            try:
                wire_fn(
                    self.spawner,
                    repo_root,
                    switchboard_client=self.switchboard_client,
                )
                logger.debug(
                    "Wired runtime into module '%s' (switchboard_client=%s)",
                    mod.name,
                    "connected" if self.switchboard_client is not None else "None",
                )
            except Exception:
                logger.warning("Module '%s' wire_runtime() failed", mod.name, exc_info=True)

    async def shutdown(self) -> None:
        """Graceful shutdown.

        1. Stop MCP server
        2. Stop durable buffer (drain queue, cancel workers)
        2b. Cancel in-flight route_inbox background tasks
        3. Stop accepting new triggers and drain in-flight runtime sessions
        4. Cancel switchboard heartbeat
        5. Close Switchboard MCP client
        5b. Cancel internal scheduler loop (wait for in-progress tick() to finish)
        6. Module on_shutdown in reverse topological order
        7. Close DB pool

        The implementation lives in :mod:`butlers.lifecycle` to keep this file
        focused on class structure.  See :func:`butlers.lifecycle.run_shutdown`
        for the full step-by-step documentation.
        """
        from butlers.lifecycle import run_shutdown

        await run_shutdown(self)

    async def _build_credential_store(self, local_pool: asyncpg.Pool) -> CredentialStore:
        """Build a credential store with local override + shared fallback."""
        fallback_pools: list[asyncpg.Pool] = []
        schema_topology = bool(self.config.db_schema)
        configured_shared_db_name = shared_db_name_from_env()
        shared_db_name = configured_shared_db_name
        shared_db_schema: str | None = None
        if schema_topology:
            shared_db_name = self.config.db_name
            shared_db_schema = "public"
            if (
                os.environ.get("BUTLER_SHARED_DB_NAME") is not None
                and configured_shared_db_name != shared_db_name
            ):
                logger.warning(
                    "Using transitional BUTLER_SHARED_DB_NAME=%s override in one-db mode; "
                    "expected %s",
                    configured_shared_db_name,
                    shared_db_name,
                )
                shared_db_name = configured_shared_db_name

        shared_pool: asyncpg.Pool | None = None

        if schema_topology:
            shared_db = Database.from_env(shared_db_name)
            shared_db.set_schema(shared_db_schema)
            if shared_db is self.db:
                # Test harnesses may patch Database.from_env to always return the
                # main DB object. Treat that as local-only mode.
                shared_pool = local_pool
            else:
                try:
                    await shared_db.provision()
                    shared_pool = await shared_db.connect()
                    await ensure_secrets_schema(shared_pool)
                    self._shared_credentials_db = shared_db
                except Exception:
                    logger.warning(
                        "Shared credential DB unavailable (db=%s, schema=%s); "
                        "falling back to local/env only",
                        shared_db_name,
                        shared_db_schema,
                        exc_info=True,
                    )
                    await shared_db.close()
                    shared_pool = None
        elif self.db is not None and self.db.db_name == shared_db_name:
            shared_pool = local_pool
        else:
            shared_db = Database.from_env(shared_db_name)
            if shared_db is self.db:
                # Test harnesses may patch Database.from_env to always return the
                # main DB object. Treat that as local-only mode.
                shared_pool = local_pool
            else:
                try:
                    await shared_db.provision()
                    shared_pool = await shared_db.connect()
                    await ensure_secrets_schema(shared_pool)
                    self._shared_credentials_db = shared_db
                except Exception:
                    logger.warning(
                        "Shared credential DB unavailable (db=%s); falling back to local/env only",
                        shared_db_name,
                        exc_info=True,
                    )
                    await shared_db.close()
                    shared_pool = None

        if shared_pool is not None and shared_pool is not local_pool:
            fallback_pools.append(shared_pool)

        return CredentialStore(local_pool, fallback_pools=fallback_pools)
