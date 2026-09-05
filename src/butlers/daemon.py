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
from dataclasses import dataclass
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
    validate_approval_config,
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
from butlers.daemon_utils import (
    _extract_identity_scope_credentials,
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
    ModuleRuntimeState,
    ModuleStartupStatus,
)
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.base import Module, ToolMeta
from butlers.modules.pipeline import MessagePipeline
from butlers.modules.registry import ModuleRegistry, default_registry
from butlers.storage import S3BlobStore

logger = logging.getLogger(__name__)

_MCP_SERVER_START_TIMEOUT_S = 5.0
_MCP_SERVER_START_POLL_INTERVAL_S = 0.01


@dataclass(frozen=True)
class _SchedulerRuntimeContext:
    """Timezone and butler-owned hooks shared by every scheduler entry point."""

    default_timezone: str
    prompt_hooks: dict[str, Any] | None
    completion_hooks: dict[str, Any] | None


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
        # bu-ep4ks.2: dashboard chat Stop button — infrastructure endpoint the
        # API calls server-to-server, always registered like route.execute.
        "cancel_session",
        "schedule_costs",
        "notify",
        "remind",
        "get_attachment",
        "module.states",
        "module.set_enabled",
        "correct",
        # Added in #1712 and #1714 respectively; always registered on every butler.
        "memory_access",
        "memory_catalog_fetch",
        "shutdown",
        # bu-p6ey8.1: dashboard chat confirm-loop reply channel; always
        # registered on every butler — any butler can be the classification
        # or pinned-target destination of a dashboard conversation.
        "conversation_reply",
        # bu-0ynlk.9: owner-scoped cross-butler dashboard chat recall; always
        # registered on every butler for the same reason as conversation_reply
        # above — any butler may need to recall a turn the owner had with a
        # different butler.
        "conversation_recall",
        "conversation_thread_read",
        # bu-gxmfx: cross-butler delegation ledger; non-STAFFER only, same
        # gate as notify/remind above.
        "delegate_ask",
        "delegate_receive",
        "delegate_answer",
        "delegate_wake",
        # bu-ep4ks.10: domain-event bus (standing pub/sub); non-STAFFER only,
        # same gate as delegate_* above.
        "publish_event",
        "subscribe_to_event",
        "unsubscribe_from_event",
        "list_my_subscriptions",
        "receive_domain_event",
        # bu-6jv4m.8: the subscriber's own reaction receipt. Same gate as the
        # rest of the bus -- only a butler that can receive an event can close
        # the loop on one.
        "report_event_reaction",
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

# Server-to-server control tools registered only on the owning butler.
CHRONICLER_CORE_TOOL_NAMES: frozenset[str] = frozenset({"chronicler_day_close_refresh"})

# Backwards-compatible alias: every core tool registered on any butler type.
CORE_TOOL_NAMES: frozenset[str] = (
    UNIVERSAL_CORE_TOOL_NAMES
    | MESSENGER_CORE_TOOL_NAMES
    | DOMAIN_CORE_TOOL_NAMES
    | CHRONICLER_CORE_TOOL_NAMES
)

_DEFAULT_TELEGRAM_CHAT_CONTACT_INFO_TYPE = "telegram_chat_id"
_NO_TELEGRAM_CHAT_CONFIGURED_ERROR = (
    "No bot <-> user telegram chat has been configured - please add a "
    "telegram_chat_id entity_info entry on the owner entity via the dashboard"
)

# A small, explicit compatibility map for durable actions written before the
# relationship dedup producer adopted the registered memory callable name.
# Keep this at the owning-butler boundary: it lets an already-approved legacy
# row use the normal audited executor without rewriting its provenance or
# allowing a generic cross-butler tool lookup.
_LEGACY_APPROVAL_TOOL_ALIASES: dict[str, dict[str, str]] = {
    "relationship": {"entity_merge": "memory_entity_merge"},
}


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
        # Cached park -> Switchboard delivery boundary (bu-mda0r). Built once
        # in _apply_approval_gates() (after the credential store and DB pool
        # exist) and reused by every PENDING park path on this daemon: the
        # gate wrapper, the email/recipient guards (via approvals_hooks), the
        # calendar overlap-approval enqueuer, and notify()'s own park sites.
        self._approval_push_runtime: Any | None = None
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
        """Recover eligible route-inbox rows under a fenced processing lease.

        Called on startup to recover from crashes or restarts. Accepted rows
        and stale processing leases are eligible for recovery; reclaimed
        dashboard processing rows reconcile their durable predecessor and
        suppress automatic replay when it is unprovable.

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
        app = self._build_mcp_http_app(
            self.mcp,
            butler_name=self.config.name,
            approval_push_runtime=self._approval_push_runtime,
            runtime_probe_coordinator=self._build_runtime_probe_coordinator(),
        )
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

    def _build_runtime_probe_coordinator(self) -> Any | None:
        """Build Switchboard's runtime-probe coordinator, or ``None`` elsewhere.

        Only Switchboard owns this control plane: ``core_201`` grants the
        replay-receipt table to ``butler_switchboard_rw`` alone, so no other
        butler could commit a receipt even if it exposed the route.

        The coordinator is built whether or not a verifier keyring is mounted.
        Without one it answers every request ``503/unavailable``, which is the
        deployed state in this phase --- the route exists, verified by tests
        against fixture keys, and does nothing in production.
        """
        if self.config.name != "switchboard":
            return None
        pool = self.db.pool if self.db is not None else None
        if pool is None:
            return None

        from butlers.core.runtime_probe_control.coordinator import RuntimeProbeCoordinator

        # The credential store is passed as the Codex authority explicitly, the
        # same way the dashboard's verification path passes it: a probe must
        # never infer authority from the pool it happens to hold.
        return RuntimeProbeCoordinator(pool, codex_auth_authority=self._credential_store)

    @classmethod
    def _build_mcp_http_app(
        cls,
        mcp: FastMCP,
        *,
        butler_name: str,
        approval_push_runtime: Any | None = None,
        runtime_probe_coordinator: Any | None = None,
    ) -> Any:
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

        # Switchboard's private runtime-probe control plane.  Attached beside
        # /health rather than registered as an MCP tool, so it is invisible to
        # tool enumeration and unreachable from a model session.
        #
        # The readiness gate is attached with it, never separately: a 200/ready
        # from a process with no control route would tell the signed client to
        # go and sign a capability for a 404.
        if runtime_probe_coordinator is not None:
            from butlers.core.runtime_probe_control.endpoint import (
                build_runtime_probe_control_route,
                build_runtime_probe_readiness_route,
            )

            for control_route in (
                build_runtime_probe_control_route(runtime_probe_coordinator),
                build_runtime_probe_readiness_route(),
            ):
                if not cls._attach_route_via_public_api(streamable_app, control_route):
                    streamable_app.routes.append(control_route)

        guarded_app = _McpRuntimeSessionGuard(
            streamable_app,
            butler_name=butler_name,
            approval_push_runtime=approval_push_runtime,
        )
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
            switchboard_client=self.switchboard_client,
            approval_push_runtime=self._approval_push_runtime,
        )

    async def _build_scheduler_runtime_context(self) -> _SchedulerRuntimeContext:
        """Resolve scheduler timezone and butler-owned hooks for any tick entry point."""
        if self.db is None or self.db.pool is None:
            raise RuntimeError("Scheduler runtime context requires an initialized database")

        # Resolve the owner's general timezone so hour-pinned crons fire at the
        # intended local time, failing open to UTC.  Resolved once at loop
        # start; a timezone change takes effect after the next daemon restart,
        # consistent with other cold scheduler config.
        from butlers.core.general_settings import resolve_general_timezone

        shared_pool = (
            self._credential_store.shared_pool if self._credential_store is not None else None
        )
        default_timezone = await resolve_general_timezone(shared_pool)

        # Build butler-specific scheduler hooks. Chronicler binds the exact local
        # day into its prompt before dispatch, then persists the prose output to
        # tier2_cache after completion. Both hooks receive the same tick timestamp
        # and effective timezone, avoiding the UTC/local rollover defect (#2681).
        prompt_hooks = None
        completion_hooks = None
        if self.config.name == "chronicler":
            from butlers.chronicler.day_close_writer import (
                build_day_close_completion_hooks,
                build_day_close_prompt_hooks,
            )

            # Wire the memory write-back loop (bu-93y4rt, tasks.md §8) when the
            # memory module is enabled and started. store_fact_fn writes ONLY to
            # the chronicler's own schema; the enrichment proposer routes to
            # relationship over MCP (best-effort — a missing switchboard client
            # is a silent no-op). Both are optional: without them the hook keeps
            # doing exactly the tier2-cache write it always has.
            store_fact_fn = None
            propose_enrichment_fn = None
            memory_module = self._resolve_memory_module()
            if memory_module is not None:
                try:
                    memory_engine = memory_module._get_embedding_engine()
                    # Use the memory module's OWN runtime pool, which targets the
                    # dedicated memory schema (chronicler_mem) when configured —
                    # NOT self.db.pool (the chronicler domain schema). This is why
                    # synthesized facts land in chronicler_mem while chronicler.*
                    # domain tables stay untouched (bu-93y4rt / bu-w6jca).
                    memory_pool = memory_module._get_pool()
                except Exception:
                    logger.warning(
                        "Failed to resolve memory pool/engine; chronicler "
                        "write-back disabled for this run",
                        exc_info=True,
                    )
                    memory_engine = None
                    memory_pool = None

                if memory_engine is not None and memory_pool is not None:
                    from butlers.chronicler.writeback import (
                        build_chronicler_fact_writer,
                        build_relationship_enrichment_proposer,
                    )

                    store_fact_fn = build_chronicler_fact_writer(memory_pool, memory_engine)
                    propose_enrichment_fn = build_relationship_enrichment_proposer(
                        lambda: self.switchboard_client
                    )

            prompt_hooks = build_day_close_prompt_hooks(timezone=default_timezone)
            completion_hooks = build_day_close_completion_hooks(
                self.db.pool,
                timezone=default_timezone,
                store_fact_fn=store_fact_fn,
                propose_enrichment_fn=propose_enrichment_fn,
            )

        return _SchedulerRuntimeContext(
            default_timezone=default_timezone,
            prompt_hooks=prompt_hooks,
            completion_hooks=completion_hooks,
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

        runtime_context = await self._build_scheduler_runtime_context()

        daemon = self
        await _background.scheduler_loop(
            pool=self.db.pool,
            dispatch_fn=self._dispatch_scheduled_task,
            interval=self.config.scheduler.tick_interval_seconds,
            butler_name=self.config.name,
            tick_fn=_tick,
            get_switchboard_client=lambda: daemon.switchboard_client,
            get_db=lambda: daemon.db,
            prompt_hooks=runtime_context.prompt_hooks,
            completion_hooks=runtime_context.completion_hooks,
            get_eligibility_pool=lambda: daemon._audit_pool,
            default_timezone=runtime_context.default_timezone,
        )

    def _resolve_memory_module(self) -> Any | None:
        """Return the started memory module instance, or ``None``.

        Used to wire the chronicler day-close memory write-back (bu-93y4rt):
        the caller reads the module's embedding engine and its runtime pool
        (which targets the dedicated ``chronicler_mem`` schema). Returns
        ``None`` when the memory module is absent or failed to start, so the
        day-close hook falls back to cache-only behaviour.
        """
        for mod in self._modules:
            if mod.name != "memory":
                continue
            status = self._module_statuses.get(mod.name)
            if status is not None and status.status != "active":
                return None
            return mod
        return None

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

        # The approvals module also uses this map after a human manually
        # approves an action to derive its autonomy fingerprint.  Wire it
        # whenever that module is active, not only when this butler enables
        # automatic approval gates.  A disabled gate must not silently change
        # the fingerprint basis to the all-args fallback.
        approvals_module = next(
            (mod for mod in self._active_modules if mod.name == "approvals"),
            None,
        )
        tool_metadata: dict[str, ToolMeta] = {}
        if approvals_module is not None:
            for mod in self._active_modules:
                try:
                    declared = mod.tool_metadata()
                    if declared:
                        tool_metadata.update(declared)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Module '%s' tool_metadata() failed: %s", mod.name, exc)
                    continue

            set_tool_metadata = getattr(approvals_module, "set_tool_metadata", None)
            if callable(set_tool_metadata):
                set_tool_metadata(tool_metadata)

        # The executor is required for *all* approval queues, even when no
        # automatic gates are configured.  Relationship, for example, creates
        # its own human-gated merge proposals and has an empty ``gated_tools``
        # map.  It must still dispatch an approved row through the registered
        # tool in this daemon, never through a different butler or a gate-wrapped
        # public call.
        originals: dict[str, Any] = {}
        if approvals_module is not None:
            set_policy = getattr(approvals_module, "set_approval_policy", None)
            if callable(set_policy):
                set_policy(approval_config)

            set_executor = getattr(approvals_module, "set_tool_executor", None)
            mcp = getattr(self, "mcp", None)
            butler_name = getattr(self.config, "name", "")
            # Direct approval producers bypass the normal gate wrapper, so
            # validate their declared durable commands against this daemon's
            # actual registered MCP surface before it can accept new work.
            if mcp is not None:
                from butlers.modules.approvals.command_contracts import (
                    validate_owner_command_registry,
                )

                await validate_owner_command_registry(mcp, butler_name)
            if callable(set_executor) and mcp is not None:
                aliases = _LEGACY_APPROVAL_TOOL_ALIASES.get(butler_name, {})

                async def _execute_approved_tool(
                    tool_name: str,
                    tool_args: dict[str, Any],
                    *,
                    _originals: dict[str, Any] = originals,
                    _aliases: dict[str, str] = aliases,
                    _mcp: Any = mcp,
                ) -> Any:
                    """Run a registered tool outside the approval gate.

                    Gated tools use their captured pre-gate handler. Ungated
                    approval producers resolve the tool from this daemon's MCP
                    registry. Legacy aliases are deliberately scoped to the
                    owning relationship runtime and never mutate the stored
                    action name.
                    """
                    executable_name = _aliases.get(tool_name, tool_name)
                    original_fn = _originals.get(executable_name) or _originals.get(tool_name)
                    if original_fn is None:
                        tool_obj = await _resolve_mcp_tool(_mcp, executable_name)
                        if tool_obj is None:
                            raise RuntimeError(
                                f"No registered handler for approved tool: {executable_name}"
                            )
                        original_fn = getattr(tool_obj, "fn", None)
                        if not callable(original_fn):
                            raise RuntimeError(
                                f"Registered handler is unavailable for approved tool: "
                                f"{executable_name}"
                            )

                    raw_result = original_fn(**tool_args)
                    if inspect.isawaitable(raw_result):
                        raw_result = await raw_result
                    if isinstance(raw_result, dict):
                        if raw_result.get("error"):
                            raise RuntimeError(str(raw_result["error"]))
                        if raw_result.get("success") is False:
                            raise RuntimeError("tool reported unsuccessful execution")
                    return raw_result

                set_executor(_execute_approved_tool)

        # Fail closed before any gate wrapping happens: reject startup if the
        # approval config names a tool no module registered, or (when modules
        # declare arg_sensitivities={"_write": True}) leaves a chat-reachable
        # write tool ungated. All module tools are already registered on
        # self.mcp by this point (lifecycle.py registers modules before
        # calling this method). Only butlers with approvals enabled pay for
        # listing the registered tool set, and this runs after the direct
        # owner-command registry check above so that check's more specific
        # handler-drift diagnostics surface first.
        if approval_config is not None and approval_config.enabled:
            registered_tools = {tool.name for tool in await self.mcp.list_tools()}
            validate_approval_config(approval_config, registered_tools, tool_metadata)

        if approval_config is None or not approval_config.enabled:
            return originals

        pool = self.db.pool

        decision_memory_writer = None
        if approvals_module is not None:
            get_decision_memory_writer = getattr(
                approvals_module, "get_decision_memory_writer", None
            )
            if callable(get_decision_memory_writer):
                decision_memory_writer = get_decision_memory_writer()

        # Cached once and reused by every PENDING park path on this daemon
        # (see the field docstring on __init__), not just the gate wrapper.
        self._approval_push_runtime = self._build_approval_push_runtime()
        if self._approval_push_runtime is not None:
            await self._warn_if_approval_callback_secret_missing()

        wrapped_originals = await apply_approval_gates(
            self.mcp,
            approval_config,
            pool,
            self.config.name,
            tool_metadata=tool_metadata,
            decision_memory_writer=decision_memory_writer,
            approval_push_runtime=self._approval_push_runtime,
        )
        # Keep the executor closure wired above, but make its captured mapping
        # available once gate wrapping has saved the original handlers.
        originals.update(wrapped_originals)

        if originals:
            logger.info(
                "Applied approval gates to %d tool(s): %s",
                len(originals),
                ", ".join(sorted(originals.keys())),
            )

        return originals

    def _build_approval_push_runtime(self) -> Any | None:
        """Build the deterministic park → Switchboard delivery boundary.

        Approval pushes originate at the daemon gate rather than a model-facing
        ``notify()`` tool.  The resulting control-plane envelope still travels
        through Switchboard and Messenger, preserving their delivery logging and
        owner-channel validation while deliberately avoiding the insight broker.
        """
        from butlers.modules.approvals.notifications import ApprovalPushRuntime

        pool = self.db.pool if self.db is not None else None
        if pool is None or self._credential_store is None:
            logger.warning(
                "Approval push runtime unavailable; parked actions will remain dashboard-only "
                "(butler=%s)",
                self.config.name,
            )
            return None

        async def _resolve_owner_recipient() -> str | None:
            return await self._resolve_default_notify_recipient(
                channel="telegram",
                intent="send",
                recipient=None,
            )

        async def _dispatch(envelope: dict[str, Any]) -> None:
            deliver_args = {
                "source_butler": self.config.name,
                "notify_request": envelope,
            }
            client = self.switchboard_client
            if client is None:
                if self.config.name != "switchboard":
                    raise RuntimeError(
                        "Switchboard client not connected; cannot deliver approval request"
                    )
                from butlers.tools.switchboard.notification.deliver import (
                    deliver as switchboard_deliver,
                )

                result = await switchboard_deliver(
                    pool,
                    source_butler=self.config.name,
                    notify_request=envelope,
                )
                if result.get("status") == "failed":
                    raise RuntimeError(
                        "Approval request delivery failed: "
                        f"{result.get('error') or 'unknown error'}"
                    )
                return

            result = await asyncio.wait_for(
                client.call_tool("deliver", deliver_args),
                timeout=_background._DEFERRED_NOTIFY_TIMEOUT_S,
            )
            if result.is_error:
                error_text = str(result.content[0].text) if result.content else "Unknown error"
                raise RuntimeError(f"Approval request delivery failed: {error_text}")
            if isinstance(result.data, dict) and result.data.get("status") == "failed":
                error_text = str(result.data.get("error") or "Unknown error")
                raise RuntimeError(f"Approval request delivery failed: {error_text}")

        return ApprovalPushRuntime(
            dispatch=_dispatch,
            resolve_owner_recipient=_resolve_owner_recipient,
            credential_store=self._credential_store,
        )

    async def _warn_if_approval_callback_secret_missing(self) -> None:
        """Log loudly, once at startup, when APPROVAL_CALLBACK_SECRET is unresolvable.

        Without this Tier 1 secret, ``emit_approval_push`` structurally cannot
        dispatch a single-action push: it resolves 'failed' before every
        attempt (see ``notifications.py::_callback_secret``). That was
        previously visible only as a per-push ``logger.warning`` an operator
        would have to be looking at the right log line to see. This makes the
        degraded state loud and startup-time visible instead -- a clear
        operator-facing signal, not a silent per-push warning. This never
        raises: a missing secret degrades approval pushes, it must not take
        the daemon down (dashboard-only fallback remains available).
        """
        if self._credential_store is None:
            return
        from butlers.core.approval_callbacks import APPROVAL_CALLBACK_SECRET_KEY

        try:
            secret = await self._credential_store.resolve(
                APPROVAL_CALLBACK_SECRET_KEY, env_fallback=False
            )
        except Exception:  # noqa: BLE001 - a probe failure must not block startup
            logger.warning(
                "Could not probe %s at startup (butler=%s)",
                APPROVAL_CALLBACK_SECRET_KEY,
                self.config.name,
                exc_info=True,
            )
            return

        if not secret:
            logger.error(
                "DEGRADED: %s is not provisioned (butler=%s). Every approval push will "
                "resolve 'failed' before dispatch -- parked actions will NOT reach the "
                "owner via Telegram until this secret is provisioned. Dashboard review "
                "remains the only reachable decision path; see ApprovalMetrics."
                "callback_secret_configured for the same signal on the dashboard.",
                APPROVAL_CALLBACK_SECRET_KEY,
                self.config.name,
            )

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
        approval_push_runtime = self._approval_push_runtime
        origin_butler = self.config.name

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
            from butlers.modules.approvals.park import park_pending_action

            action_id = _uuid.uuid4()
            now = _dt.now(_UTC)
            expires_at = now + _td(hours=expiry_hours)

            # Bind the sanitized dict directly (no json.dumps, no ::jsonb cast):
            # every asyncpg pool here registers register_jsonb_codec() (db.py),
            # whose encoder already calls json.dumps() once. Passing a
            # pre-serialized string double-encodes it into a jsonb-typed STRING
            # instead of an OBJECT (bu-cymc4/bu-bstqu; mirrors gate.py's fix).
            safe_tool_args = json.loads(json.dumps(tool_args, default=str))

            # park_pending_action is the single choke point for PENDING
            # inserts: it writes the row AND attempts the owner-facing push
            # in one call, so this park path cannot silently skip notifying
            # the owner (bu-mda0r).
            await park_pending_action(
                pool,
                action_id=action_id,
                tool_name=tool_name,
                tool_args=safe_tool_args,
                agent_summary=agent_summary,
                requested_at=now,
                expires_at=expires_at,
                session_id=get_current_runtime_session_id(),
                origin_butler=origin_butler,
                approval_push_runtime=approval_push_runtime,
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
        """Build local/fallback resolution plus an explicit global authority.

        ``cli-auth/codex`` must receive ``system_global_pool`` even in a flat
        topology where it is the same object as ``local_pool``.  A missing
        global pool is intentionally represented as absent rather than being
        inferred from the local credential scope.
        """
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
                        "Codex system-global authority is unavailable",
                        shared_db_name,
                        shared_db_schema,
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
                        "Shared credential DB unavailable (db=%s); "
                        "Codex system-global authority is unavailable",
                        shared_db_name,
                    )
                    await shared_db.close()
                    shared_pool = None

        if shared_pool is not None and shared_pool is not local_pool:
            fallback_pools.append(shared_pool)

        return CredentialStore(
            local_pool,
            fallback_pools=fallback_pools,
            system_global_pool=shared_pool,
        )
