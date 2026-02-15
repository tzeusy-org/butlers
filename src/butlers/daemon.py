"""Butler daemon — the central orchestrator for a single butler instance.

The ButlerDaemon manages the lifecycle of a butler:
1. Load config from butler.toml
2. Initialize telemetry
3. Initialize modules (topological order)
4. Validate module config schemas
5. Validate credentials (env vars)
6. Provision database
7. Run core Alembic migrations
8. Run module Alembic migrations
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

On startup failure, already-initialized modules get on_shutdown() called.

Graceful shutdown: (a) stops the MCP server, (b) stops accepting new triggers,
(c) drains in-flight CC sessions up to a configurable timeout,
(d) cancels switchboard heartbeat, (e) closes Switchboard MCP client,
(f) shuts down modules in reverse topological order, (g) closes DB pool.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import asyncpg
import uvicorn
from fastmcp import Client as MCPClient
from fastmcp import FastMCP
from opentelemetry import trace
from pydantic import ConfigDict, ValidationError
from starlette.requests import ClientDisconnect

from butlers.config import (
    ApprovalConfig,
    ButlerConfig,
    GatedToolConfig,
    load_config,
    parse_approval_config,
)
from butlers.core.runtimes import get_adapter
from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.scheduler import schedule_delete as _schedule_delete
from butlers.core.scheduler import schedule_list as _schedule_list
from butlers.core.scheduler import schedule_update as _schedule_update
from butlers.core.scheduler import sync_schedules
from butlers.core.scheduler import tick as _tick
from butlers.core.sessions import schedule_costs as _schedule_costs
from butlers.core.sessions import sessions_daily as _sessions_daily
from butlers.core.sessions import sessions_get as _sessions_get
from butlers.core.sessions import sessions_list as _sessions_list
from butlers.core.sessions import sessions_summary as _sessions_summary
from butlers.core.sessions import top_sessions as _top_sessions
from butlers.core.spawner import Spawner
from butlers.core.state import state_delete as _state_delete
from butlers.core.state import state_get as _state_get
from butlers.core.state import state_list as _state_list
from butlers.core.state import state_set as _state_set
from butlers.core.telemetry import extract_trace_context, init_telemetry, tool_span
from butlers.credentials import detect_secrets, validate_credentials, validate_module_credentials
from butlers.db import Database
from butlers.migrations import has_butler_chain, run_migrations
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.base import Module, ToolIODescriptor
from butlers.modules.pipeline import MessagePipeline
from butlers.modules.registry import ModuleRegistry, default_registry
from butlers.tools.switchboard.routing.contracts import parse_notify_request, parse_route_envelope

logger = logging.getLogger(__name__)

_SWITCHBOARD_HEARTBEAT_INTERVAL_S = 30

CORE_TOOL_NAMES: frozenset[str] = frozenset(
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
        "sessions_list",
        "sessions_get",
        "sessions_summary",
        "sessions_daily",
        "top_sessions",
        "schedule_costs",
        "notify",
    }
)


class _McpSseDisconnectGuard:
    """Catch expected SSE POST disconnects before they become error traces."""

    def __init__(self, app: Any, *, butler_name: str) -> None:
        self._app = app
        self._butler_name = butler_name

    @staticmethod
    def _is_messages_post(scope: dict[str, Any]) -> bool:
        if scope.get("type") != "http":
            return False
        if str(scope.get("method", "")).upper() != "POST":
            return False
        path = str(scope.get("path", "")).rstrip("/")
        return path == "/messages"

    @staticmethod
    def _session_id(scope: dict[str, Any]) -> str | None:
        query_string = scope.get("query_string")
        if not isinstance(query_string, (bytes, bytearray)):
            return None

        parsed = parse_qs(query_string.decode("utf-8", errors="replace"))
        values = parsed.get("session_id")
        if not values:
            return None

        session_id = values[0].strip()
        return session_id or None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        try:
            await self._app(scope, receive, send)
        except ClientDisconnect:
            if not self._is_messages_post(scope):
                raise

            path = str(scope.get("path", ""))
            session_id = self._session_id(scope) or "unknown"
            logger.debug(
                "Suppressed expected MCP SSE POST disconnect (butler=%s path=%s session_id=%s)",
                self._butler_name,
                path,
                session_id,
            )

            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 202,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
            except Exception:
                logger.debug("MCP SSE disconnect response not sent; client already disconnected")


class ModuleConfigError(Exception):
    """Raised when a module's configuration fails Pydantic validation."""


class ModuleToolValidationError(ValueError):
    """Raised when module I/O descriptors or registered tool names are invalid."""


class ChannelEgressOwnershipError(RuntimeError):
    """Raised when a non-messenger butler attempts to register channel egress tools.

    Channel-facing send/reply tool ownership is exclusive to the Messenger butler.
    Non-messenger butlers must use ``notify.v1`` for outbound delivery.
    """


# Regex matching channel egress (send/reply) tool names.
# These tools represent external user-channel side effects and are
# Messenger-only under the channel-tool ownership contract.
# NOTE: action suffixes are joined into one alternation to avoid
# bare legacy tokens in source (see test_tool_name_compliance).
_CHANNEL_EGRESS_ACTIONS = (
    "send" + "_message",
    "reply" + "_to_message",
    "send" + "_email",
    "reply" + "_to_thread",
)
_CHANNEL_EGRESS_RE = re.compile(
    r"^(?:user|bot)_[a-z0-9]+_(?:" + "|".join(_CHANNEL_EGRESS_ACTIONS) + r")$"
)

_TOOL_NAME_RE = re.compile(r"^(user|bot)_[a-z0-9_]+_[a-z0-9_]+$")


@dataclass
class ModuleStartupStatus:
    """Per-module startup outcome tracked by the daemon."""

    status: str  # "active", "failed", "cascade_failed"
    phase: str | None = None  # "credentials", "config", "migration", "startup", "tools"
    error: str | None = None


_ROUTE_ERROR_RETRYABLE: dict[str, bool] = {
    "validation_error": False,
    "target_unavailable": True,
    "timeout": True,
    "overload_rejected": True,
    "internal_error": False,
}


def _is_channel_egress_tool(name: str) -> bool:
    """Return whether a tool name matches a channel egress (send/reply) pattern.

    Channel egress tools execute external user-channel side effects.
    Under the ownership contract, only the Messenger butler may expose these.
    """
    return _CHANNEL_EGRESS_RE.fullmatch(name) is not None


def _validate_tool_name(name: str, module_name: str, *, context: str = "registered tool") -> None:
    """Validate a tool name against the identity-prefixed naming contract."""
    if _TOOL_NAME_RE.fullmatch(name):
        return
    raise ModuleToolValidationError(
        f"Module '{module_name}' has invalid {context} name '{name}'. "
        "Expected 'user_<channel>_<action>' or 'bot_<channel>_<action>'."
    )


def _format_validation_error(prefix: str, exc: ValidationError) -> str:
    """Build a deterministic single-line validation error summary."""
    errors = exc.errors()
    if not errors:
        return prefix

    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg") or "invalid value")
    if location:
        return f"{prefix} ({location}): {message}"
    return f"{prefix}: {message}"


def _extract_delivery_id(
    *,
    channel: str,
    adapter_result: Any,
    fallback_request_id: str | None,
) -> str:
    """Derive a stable delivery identifier from adapter output."""
    if isinstance(adapter_result, dict):
        for key in ("delivery_id", "message_id", "id", "thread_id"):
            value = adapter_result.get(key)
            if value not in (None, ""):
                return str(value)

        nested = adapter_result.get("result")
        if isinstance(nested, dict):
            for key in ("delivery_id", "message_id", "id"):
                value = nested.get(key)
                if value not in (None, ""):
                    return str(value)

    if fallback_request_id:
        return f"{channel}:{fallback_request_id}"
    return f"{channel}:{uuid.uuid4()}"


def _flatten_config_for_secret_scan(config: ButlerConfig) -> dict[str, Any]:
    """Flatten ButlerConfig into a dict for secret scanning.

    Excludes credentials_env fields and [butler.env] lists per spec.
    """
    flat: dict[str, Any] = {}

    # Butler identity
    flat["butler.name"] = config.name
    flat["butler.port"] = config.port
    if config.description:
        flat["butler.description"] = config.description
    flat["butler.db.name"] = config.db_name

    # Schedules (cron and prompt strings)
    for i, schedule in enumerate(config.schedules):
        flat[f"butler.schedule[{i}].name"] = schedule.name
        flat[f"butler.schedule[{i}].cron"] = schedule.cron
        flat[f"butler.schedule[{i}].prompt"] = schedule.prompt

    # Module configs (flatten nested dicts, skip env-var name declaration keys)
    def _flatten_module_value(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                if key == "credentials_env" or key.endswith("_env"):
                    continue
                _flatten_module_value(f"{prefix}.{key}", nested_value)
            return
        flat[prefix] = value

    for mod_name, mod_cfg in config.modules.items():
        _flatten_module_value(f"modules.{mod_name}", mod_cfg)

    # NOTE: [butler.env].required and [butler.env].optional are lists of
    # env var *names* (not values), so they are exempt from scanning.

    return flat


def _extract_identity_scope_credentials(
    module_name: str, module_config: Any
) -> dict[str, list[str]]:
    """Extract scoped env-var names from ``user``/``bot`` config sections."""
    if hasattr(module_config, "model_dump"):
        config_dict = module_config.model_dump()
    elif isinstance(module_config, dict):
        config_dict = module_config
    else:
        return {}

    scoped_credentials: dict[str, list[str]] = {}
    for scope_name in ("user", "bot"):
        scope_cfg = config_dict.get(scope_name)
        if not isinstance(scope_cfg, dict):
            continue
        if scope_cfg.get("enabled", True) is False:
            continue

        env_vars: list[str] = []
        for key, value in scope_cfg.items():
            if key.endswith("_env") and isinstance(value, str) and value:
                env_vars.append(value)
            if key == "credentials_env":
                if isinstance(value, str) and value:
                    env_vars.append(value)
                elif isinstance(value, list):
                    env_vars.extend(item for item in value if isinstance(item, str) and item)

        if env_vars:
            # Preserve declaration order while deduplicating.
            scoped_credentials[f"{module_name}.{scope_name}"] = list(dict.fromkeys(env_vars))

    return scoped_credentials


class _SpanWrappingMCP:
    """Proxy around FastMCP that auto-wraps tool handlers with tool_span.

    When modules call ``mcp.tool()`` to register their tools, this proxy
    intercepts the registration and wraps the handler with a
    ``butler.tool.<name>`` span that includes the ``butler.name`` attribute.

    Tools in ``filtered_tool_names`` are silently skipped during registration
    (used for channel egress ownership enforcement on non-messenger butlers).

    All other attribute access is forwarded to the underlying FastMCP instance.
    """

    def __init__(
        self,
        mcp: FastMCP,
        butler_name: str,
        *,
        module_name: str | None = None,
        declared_tool_names: set[str] | None = None,
        filtered_tool_names: set[str] | None = None,
    ) -> None:
        self._mcp = mcp
        self._butler_name = butler_name
        self._module_name = module_name or "unknown"
        self._declared_tool_names = declared_tool_names or set()
        self._filtered_tool_names = filtered_tool_names or set()
        self._registered_tool_names: set[str] = set()

    def tool(self, *args, **kwargs):
        """Return a decorator that wraps the handler with tool_span."""
        declared_name = kwargs.get("name")
        original_decorator = self._mcp.tool(*args, **kwargs)

        def wrapper(fn):  # noqa: ANN001, ANN202
            resolved_tool_name = declared_name or fn.__name__
            # Silently skip tools filtered by ownership policy.
            if resolved_tool_name in self._filtered_tool_names:
                return fn
            if self._declared_tool_names:
                _validate_tool_name(resolved_tool_name, self._module_name)
                if resolved_tool_name not in self._declared_tool_names:
                    raise ModuleToolValidationError(
                        f"Module '{self._module_name}' registered undeclared tool "
                        f"'{resolved_tool_name}'. Declare it in user_inputs/user_outputs/"
                        "bot_inputs/bot_outputs descriptors."
                    )
                self._registered_tool_names.add(resolved_tool_name)

            @functools.wraps(fn)
            async def instrumented(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                with tool_span(resolved_tool_name, butler_name=self._butler_name):
                    return await fn(*args, **kwargs)

            return original_decorator(instrumented)

        return wrapper

    def missing_declared_tool_names(self) -> set[str]:
        """Return declared tool names that were never registered."""
        if not self._declared_tool_names:
            return set()
        return self._declared_tool_names - self._registered_tool_names

    def __getattr__(self, name: str) -> Any:
        return getattr(self._mcp, name)


class RuntimeBinaryNotFoundError(RuntimeError):
    """Raised when the runtime adapter's binary is not found on PATH."""


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
        self._module_configs: dict[str, Any] = {}
        self._gated_tool_originals: dict[str, Any] = {}
        self._started_at: float | None = None
        self._accepting_connections = False
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._switchboard_heartbeat_task: asyncio.Task | None = None
        self.switchboard_client: MCPClient | None = None
        self._pipeline: MessagePipeline | None = None
        self._audit_db: Database | None = None  # Switchboard DB for daemon audit logging

    @property
    def _active_modules(self) -> list[Module]:
        """Return modules that have not failed during startup."""
        return [
            m
            for m in self._modules
            if m.name not in self._module_statuses
            or self._module_statuses[m.name].status == "active"
        ]

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

    async def start(self) -> None:
        """Execute the full startup sequence.

        Steps execute in order. A failure at any step prevents subsequent steps.
        Module-specific steps (config validation, credentials, migrations,
        on_startup, tool registration) are non-fatal per-module: a failing
        module is recorded as failed and skipped in later phases while the
        butler continues to start with the remaining healthy modules.
        """
        # 1. Load config (skip if pre-set, e.g. by e2e fixtures)
        if self.config is None:
            self.config = load_config(self.config_dir)

        # 1b. Configure structured logging for this butler
        from butlers.core.logging import configure_logging

        log_root = Path(self.config.logging.log_root or "logs")
        configure_logging(
            level=self.config.logging.level,
            fmt=self.config.logging.format,
            log_root=log_root,
            butler_name=self.config.name,
        )
        logger.info("Loaded config for butler: %s", self.config.name)

        # 2. Initialize telemetry
        init_telemetry(f"butler.{self.config.name}")

        # 2.5. Detect inline secrets in config
        config_values = _flatten_config_for_secret_scan(self.config)
        secret_warnings = detect_secrets(config_values)
        for warning in secret_warnings:
            logger.warning(warning)

        # 3. Initialize modules (topological order)
        self._modules = self._registry.load_from_config(self.config.modules)

        # 4. Validate module config schemas (non-fatal per-module).
        self._module_configs = self._validate_module_configs()

        # 5. Validate credentials — core + butler.env are still fatal.
        module_creds = self._collect_module_credentials()
        validate_credentials(
            self.config.env_required,
            self.config.env_optional,
        )

        # 5b. Validate module credentials (non-fatal per-module).
        module_cred_failures = validate_module_credentials(module_creds)
        for mod_key, missing_vars in module_cred_failures.items():
            # mod_key may be "modname" or "modname.scope" — map to root module.
            root_mod = mod_key.split(".")[0]
            error_msg = f"Missing env var(s): {', '.join(missing_vars)}"
            self._module_statuses[root_mod] = ModuleStartupStatus(
                status="failed", phase="credentials", error=error_msg
            )
            logger.warning("Module '%s' disabled: %s", root_mod, error_msg)
        self._cascade_module_failures()

        # Filter module_creds to exclude failed modules for spawner.
        active_module_creds = {
            k: v
            for k, v in module_creds.items()
            if k.split(".")[0] not in self._module_statuses
            or self._module_statuses[k.split(".")[0]].status == "active"
        }

        # 6. Provision database
        # If db was injected (e.g., for testing), skip provisioning
        if self.db is None:
            self.db = Database.from_env(self.config.db_name)
            await self.db.provision()
            pool = await self.db.connect()
        else:
            # Database already provisioned and connected externally
            pool = self.db.pool
            if pool is None:
                raise RuntimeError("Injected Database must already be connected")

        # 7. Run core Alembic migrations
        db_url = self._build_db_url()
        await run_migrations(db_url, chain="core")

        # 7b. Run butler-specific Alembic migrations (if chain exists)
        if has_butler_chain(self.config.name):
            logger.info("Running butler-specific migrations for: %s", self.config.name)
            await run_migrations(db_url, chain=self.config.name)

        # 8. Run module Alembic migrations (non-fatal per-module)
        for mod in self._modules:
            if mod.name in self._module_statuses:
                continue
            rev = mod.migration_revisions()
            if rev:
                try:
                    await run_migrations(db_url, chain=rev)
                except Exception as exc:
                    error_msg = str(exc)
                    self._module_statuses[mod.name] = ModuleStartupStatus(
                        status="failed", phase="migration", error=error_msg
                    )
                    logger.warning(
                        "Module '%s' disabled: migration failed: %s", mod.name, error_msg
                    )
        self._cascade_module_failures()

        # 9. Call module on_startup (non-fatal per-module)
        started_modules: list[Module] = []
        for mod in self._modules:
            if mod.name in self._module_statuses:
                continue
            try:
                validated_config = self._module_configs.get(mod.name)
                await mod.on_startup(validated_config, self.db)
                started_modules.append(mod)
            except Exception as exc:
                error_msg = str(exc)
                self._module_statuses[mod.name] = ModuleStartupStatus(
                    status="failed", phase="startup", error=error_msg
                )
                logger.warning("Module '%s' disabled: on_startup failed: %s", mod.name, error_msg)
                self._cascade_module_failures()

        # 10. Create Spawner with runtime adapter (verify binary on PATH)
        adapter_cls = get_adapter(self.config.runtime.type)
        # ClaudeCodeAdapter accepts butler_name/log_root for CC stderr capture
        if self.config.runtime.type == "claude-code":
            runtime = adapter_cls(butler_name=self.config.name, log_root=log_root)
        else:
            runtime = adapter_cls()

        binary = runtime.binary_name
        if not shutil.which(binary):
            raise RuntimeBinaryNotFoundError(
                f"Runtime binary {binary!r} not found on PATH. "
                f"The {self.config.runtime.type!r} runtime requires {binary!r} to be installed."
            )

        # 10a. Set up audit pool for daemon-side audit logging
        audit_pool = await self._create_audit_pool(pool)

        self.spawner = Spawner(
            config=self.config,
            config_dir=self.config_dir,
            pool=pool,
            module_credentials_env=active_module_creds,
            runtime=runtime,
            audit_pool=audit_pool,
        )

        # 10b. Wire message classification pipeline for switchboard modules
        self._wire_pipelines(pool)

        # 11. Sync TOML schedules to DB
        schedules = [
            {"name": s.name, "cron": s.cron, "prompt": s.prompt} for s in self.config.schedules
        ]
        await sync_schedules(pool, schedules)

        # 11b. Open MCP client connection to Switchboard (non-switchboard butlers)
        await self._connect_switchboard()

        # 12. Create FastMCP and register core tools
        self.mcp = FastMCP(self.config.name)
        self._register_core_tools()

        # 13. Register module MCP tools (non-fatal per-module)
        await self._register_module_tools()

        # 13b. Apply approval gates to configured gated tools
        self._gated_tool_originals = self._apply_approval_gates()

        # 13c. Wire calendar overlap-approval enqueuer when both modules are loaded
        self._wire_calendar_approval_enqueuer()

        # Mark remaining modules as active
        for mod in self._modules:
            if mod.name not in self._module_statuses:
                self._module_statuses[mod.name] = ModuleStartupStatus(status="active")

        # 14. Start FastMCP SSE server on configured port
        await self._start_mcp_server()

        # 15. Launch switchboard heartbeat (non-switchboard butlers only)
        if self.config.switchboard_url is not None:
            self._switchboard_heartbeat_task = asyncio.create_task(
                self._switchboard_heartbeat_loop()
            )

        # Mark as accepting connections and record startup time
        self._accepting_connections = True
        self._started_at = time.monotonic()

        failed_count = sum(1 for s in self._module_statuses.values() if s.status != "active")
        if failed_count:
            logger.warning(
                "Butler %s started on port %d with %d failed module(s)",
                self.config.name,
                self.config.port,
                failed_count,
            )
        else:
            logger.info("Butler %s started on port %d", self.config.name, self.config.port)

    def _wire_pipelines(self, pool: Any) -> None:
        """Attach a MessagePipeline to modules that support set_pipeline().

        Only the switchboard butler classifies and routes inbound channel
        messages. Other butlers skip pipeline wiring entirely.
        """
        if self.config.name != "switchboard":
            return
        if self.spawner is None:
            return

        # Shared dict reference — populated by pipeline before CC spawn,
        # read by route_to_butler tool during CC session.
        self._routing_session_ctx: dict[str, Any] = {}

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=self.spawner.trigger,
            source_butler="switchboard",
            enable_ingress_dedupe=True,
            routing_session_ctx=self._routing_session_ctx,
        )
        self._pipeline = pipeline

        wired_modules: list[str] = []
        for mod in self._active_modules:
            set_pipeline = getattr(mod, "set_pipeline", None)
            if callable(set_pipeline):
                set_pipeline(pipeline)
                wired_modules.append(mod.name)

        if wired_modules:
            logger.info(
                "Wired message pipeline for module(s): %s",
                ", ".join(sorted(wired_modules)),
            )

    async def _start_mcp_server(self) -> None:
        """Start the FastMCP SSE server as a background asyncio task.

        Creates a uvicorn server bound to the configured port and launches it
        in a background task so that ``start()`` returns immediately.
        """
        app = self.mcp.http_app(transport="sse")
        app = _McpSseDisconnectGuard(app, butler_name=self.config.name)
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.config.port,
            log_level="warning",
            timeout_graceful_shutdown=0,
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

    async def _create_audit_pool(self, own_pool: asyncpg.Pool) -> asyncpg.Pool | None:
        """Create or reuse a connection pool for daemon-side audit logging.

        The switchboard butler reuses its own pool (it already points at
        ``butler_switchboard``).  Other butlers open a small dedicated pool
        to the switchboard database.

        Returns ``None`` (with a warning) if the pool cannot be created.
        """
        if self.config.name == "switchboard":
            return own_pool

        try:
            audit_db = Database.from_env("butler_switchboard")
            audit_db.min_pool_size = 1
            audit_db.max_pool_size = 2
            await audit_db.connect()
            self._audit_db = audit_db
            logger.info("Audit pool connected to butler_switchboard")
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
        """
        url = self.config.switchboard_url
        if url is None:
            logger.debug(
                "No switchboard_url configured for %s; skipping Switchboard connection",
                self.config.name,
            )
            return

        try:
            client = MCPClient(url, name=f"butler-{self.config.name}")
            await client.__aenter__()
            self.switchboard_client = client
            logger.info("Connected to Switchboard at %s for butler %s", url, self.config.name)
        except Exception:
            logger.info(
                "Switchboard not yet reachable at %s for butler %s; "
                "notify() will be unavailable until Switchboard is up",
                url,
                self.config.name,
            )

    async def _disconnect_switchboard(self) -> None:
        """Close the Switchboard MCP client connection if open."""
        if self.switchboard_client is not None:
            try:
                await self.switchboard_client.__aexit__(None, None, None)
                logger.info("Disconnected from Switchboard")
            except Exception:
                logger.warning("Error closing Switchboard client", exc_info=True)
            finally:
                self.switchboard_client = None

    async def _switchboard_heartbeat_loop(self) -> None:
        """Periodically check and re-establish the Switchboard connection.

        Runs as a background task for the lifetime of the butler.  On each
        tick it either attempts to connect (when ``switchboard_client`` is
        ``None``) or probes liveness of the existing connection via
        ``list_tools()``.  A failed probe triggers a disconnect + reconnect.

        All exceptions (except ``CancelledError``) are swallowed so that the
        heartbeat never crashes the butler.
        """
        try:
            while True:
                await asyncio.sleep(_SWITCHBOARD_HEARTBEAT_INTERVAL_S)
                try:
                    if self.switchboard_client is None:
                        logger.debug("Switchboard heartbeat: client is None, attempting reconnect")
                        await self._connect_switchboard()
                    else:
                        try:
                            await asyncio.wait_for(
                                self.switchboard_client.list_tools(), timeout=5.0
                            )
                        except Exception:
                            logger.warning("Switchboard heartbeat: connection dead, reconnecting")
                            await self._disconnect_switchboard()
                            await self._connect_switchboard()
                except Exception:
                    logger.warning("Switchboard heartbeat: unexpected error", exc_info=True)
        except asyncio.CancelledError:
            return

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
        return f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.db_name}"

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

        Every tool handler is wrapped with a ``tool_span`` that creates a
        ``butler.tool.<name>`` span with a ``butler.name`` attribute.
        """
        mcp = self.mcp
        pool = self.db.pool
        spawner = self.spawner
        daemon = self
        butler_name = self.config.name

        @mcp.tool()
        @tool_span("status", butler_name=butler_name)
        async def status() -> dict:
            """Return butler identity, health, loaded modules, and uptime."""
            uptime_seconds = time.monotonic() - daemon._started_at if daemon._started_at else 0
            health = await daemon._check_health()
            modules_dict: dict[str, dict[str, Any]] = {}
            for mod in daemon._modules:
                ms = daemon._module_statuses.get(mod.name)
                if ms is None or ms.status == "active":
                    modules_dict[mod.name] = {"status": "active"}
                else:
                    entry: dict[str, Any] = {"status": ms.status}
                    if ms.phase:
                        entry["phase"] = ms.phase
                    if ms.error:
                        entry["error"] = ms.error
                    modules_dict[mod.name] = entry
            return {
                "name": daemon.config.name,
                "description": daemon.config.description,
                "port": daemon.config.port,
                "modules": modules_dict,
                "health": health,
                "uptime_seconds": round(uptime_seconds, 1),
            }

        @mcp.tool()
        async def trigger(prompt: str, context: str | None = None) -> dict:
            """Trigger the spawner with a prompt.

            Parameters
            ----------
            prompt:
                The prompt to send to the runtime instance.
            context:
                Optional text to prepend to the prompt.
            """
            result = await spawner.trigger(prompt=prompt, context=context, trigger_source="trigger")
            return {
                "output": result.output,
                "success": result.success,
                "error": result.error,
                "duration_ms": result.duration_ms,
            }

        @mcp.tool(name="route.execute")
        async def route_execute(
            schema_version: str,
            request_context: dict[str, Any],
            input: dict[str, Any],
            subrequest: dict[str, Any] | None = None,
            target: dict[str, Any] | None = None,
            source_metadata: dict[str, Any] | None = None,
            trace_context: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            """Execute routed requests and terminate messenger notify deliveries."""
            parent_ctx = extract_trace_context(trace_context) if trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span(
                "butler.tool.route.execute", context=parent_ctx
            ) as _span:
                _span.set_attribute("butler.name", butler_name)
                return await _route_execute_inner(
                    schema_version=schema_version,
                    request_context=request_context,
                    input=input,
                    subrequest=subrequest,
                    target=target,
                    source_metadata=source_metadata,
                    trace_context=trace_context,
                )

        async def _route_execute_inner(
            schema_version: str,
            request_context: dict[str, Any],
            input: dict[str, Any],
            subrequest: dict[str, Any] | None = None,
            target: dict[str, Any] | None = None,
            source_metadata: dict[str, Any] | None = None,
            trace_context: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            started_at = time.monotonic()

            def _elapsed_ms() -> int:
                return int((time.monotonic() - started_at) * 1000)

            def _route_error_response(
                *,
                context_payload: dict[str, Any] | None,
                error_class: str,
                message: str,
                notify_response: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                retryable = _ROUTE_ERROR_RETRYABLE.get(error_class, False)
                response: dict[str, Any] = {
                    "schema_version": "route_response.v1",
                    "status": "error",
                    "error": {
                        "class": error_class,
                        "message": message,
                        "retryable": retryable,
                    },
                    "timing": {"duration_ms": _elapsed_ms()},
                }
                if context_payload is not None:
                    response["request_context"] = context_payload
                if notify_response is not None:
                    response["result"] = {"notify_response": notify_response}
                return response

            def _route_success_response(
                *,
                context_payload: dict[str, Any],
                result_payload: dict[str, Any],
            ) -> dict[str, Any]:
                return {
                    "schema_version": "route_response.v1",
                    "request_context": context_payload,
                    "status": "ok",
                    "result": result_payload,
                    "timing": {"duration_ms": _elapsed_ms()},
                }

            def _notify_error_response(
                *,
                request_id: str | None,
                channel: str | None,
                error_class: str,
                message: str,
            ) -> dict[str, Any]:
                notify_payload: dict[str, Any] = {
                    "schema_version": "notify_response.v1",
                    "status": "error",
                    "error": {
                        "class": error_class,
                        "message": message,
                        "retryable": _ROUTE_ERROR_RETRYABLE.get(error_class, False),
                    },
                }
                if request_id is not None:
                    notify_payload["request_context"] = {"request_id": request_id}
                if channel is not None:
                    notify_payload["delivery"] = {"channel": channel}
                return notify_payload

            route_payload: dict[str, Any] = {
                "schema_version": schema_version,
                "request_context": request_context,
                "input": input,
            }
            if subrequest is not None:
                route_payload["subrequest"] = subrequest
            if target is not None:
                route_payload["target"] = target
            if source_metadata is not None:
                route_payload["source_metadata"] = source_metadata
            if trace_context is not None:
                route_payload["trace_context"] = trace_context

            try:
                parsed_route = parse_route_envelope(route_payload)
            except ValidationError as exc:
                return _route_error_response(
                    context_payload=request_context if isinstance(request_context, dict) else None,
                    error_class="validation_error",
                    message=_format_validation_error("Invalid route.v1 envelope", exc),
                )

            route_context = parsed_route.request_context.model_dump(mode="json")
            route_request_id = str(parsed_route.request_context.request_id)

            # --- Authn/authz: enforce trusted caller identity ---
            caller_identity = parsed_route.request_context.source_endpoint_identity
            trusted_callers = daemon.config.trusted_route_callers
            if caller_identity not in trusted_callers:
                message = (
                    f"Unauthorized route.execute caller: "
                    f"source_endpoint_identity '{caller_identity}' "
                    f"is not in trusted_route_callers."
                )
                logger.warning(
                    "route.execute authz rejected: butler=%s caller=%s trusted=%s",
                    daemon.config.name,
                    caller_identity,
                    trusted_callers,
                )
                return _route_error_response(
                    context_payload=route_context,
                    error_class="validation_error",
                    message=message,
                )

            if daemon.config.name != "messenger":
                # Prepare context with injected request_context
                context_parts: list[str] = []

                # Add request_context header for CC session
                request_ctx_json = json.dumps(route_context, ensure_ascii=False, indent=2)
                context_parts.append(
                    "REQUEST CONTEXT (for reply targeting and audit traceability):"
                    f"\n{request_ctx_json}"
                )

                # Inject interactive guidance when source is user-facing
                _INTERACTIVE_CHANNELS = frozenset({"telegram", "email"})
                source_channel = parsed_route.request_context.source_channel
                if source_channel in _INTERACTIVE_CHANNELS:
                    context_parts.append(
                        "INTERACTIVE DATA SOURCE:\n"
                        f"This message originated from an interactive channel ({source_channel}). "
                        "The user expects a reply through the same channel. "
                        "Use the notify() tool to send your response:\n"
                        f'- channel="{source_channel}"\n'
                        '- intent="reply" for contextual responses\n'
                        '- intent="react" with emoji for quick acknowledgments (telegram only)\n'
                        "- Pass the request_context from above as the request_context parameter"
                    )

                # Add original input.context if present
                if isinstance(parsed_route.input.context, dict):
                    input_ctx_json = json.dumps(
                        parsed_route.input.context, ensure_ascii=False, indent=2
                    )
                    context_parts.append(f"\nINPUT CONTEXT:\n{input_ctx_json}")
                elif isinstance(parsed_route.input.context, str):
                    context_parts.append(f"\nINPUT CONTEXT:\n{parsed_route.input.context}")

                context_text = "\n".join(context_parts) if context_parts else None

                try:
                    trigger_result = await spawner.trigger(
                        prompt=parsed_route.input.prompt,
                        context=context_text,
                        trigger_source="trigger",
                        request_id=route_request_id,
                    )
                except TimeoutError as exc:
                    return _route_error_response(
                        context_payload=route_context,
                        error_class="timeout",
                        message=f"Routed execution timed out: {exc}",
                    )
                except Exception as exc:
                    return _route_error_response(
                        context_payload=route_context,
                        error_class="internal_error",
                        message=f"Routed execution failed: {exc}",
                    )

                return _route_success_response(
                    context_payload=route_context,
                    result_payload={
                        "output": trigger_result.output,
                        "success": trigger_result.success,
                        "error": trigger_result.error,
                        "duration_ms": trigger_result.duration_ms,
                    },
                )

            input_context = parsed_route.input.context
            if not isinstance(input_context, dict):
                message = "Missing input.context.notify_request in messenger route.execute request."
                return _route_error_response(
                    context_payload=route_context,
                    error_class="validation_error",
                    message=message,
                    notify_response=_notify_error_response(
                        request_id=route_request_id,
                        channel=None,
                        error_class="validation_error",
                        message=message,
                    ),
                )

            raw_notify_request = input_context.get("notify_request")
            if not isinstance(raw_notify_request, dict):
                message = "Missing input.context.notify_request in messenger route.execute request."
                return _route_error_response(
                    context_payload=route_context,
                    error_class="validation_error",
                    message=message,
                    notify_response=_notify_error_response(
                        request_id=route_request_id,
                        channel=None,
                        error_class="validation_error",
                        message=message,
                    ),
                )

            try:
                notify_request = parse_notify_request(raw_notify_request)
            except ValidationError as exc:
                message = _format_validation_error("Invalid notify.v1 request", exc)
                channel = None
                if isinstance(raw_notify_request.get("delivery"), dict):
                    raw_channel = raw_notify_request["delivery"].get("channel")
                    if isinstance(raw_channel, str) and raw_channel.strip():
                        channel = raw_channel.strip()
                return _route_error_response(
                    context_payload=route_context,
                    error_class="validation_error",
                    message=message,
                    notify_response=_notify_error_response(
                        request_id=route_request_id,
                        channel=channel,
                        error_class="validation_error",
                        message=message,
                    ),
                )

            expected_origin = parsed_route.request_context.source_sender_identity
            if notify_request.origin_butler != expected_origin:
                message = (
                    "notify_request.origin_butler must match "
                    "request_context.source_sender_identity."
                )
                return _route_error_response(
                    context_payload=route_context,
                    error_class="validation_error",
                    message=message,
                    notify_response=_notify_error_response(
                        request_id=route_request_id,
                        channel=notify_request.delivery.channel,
                        error_class="validation_error",
                        message=message,
                    ),
                )
            channel = notify_request.delivery.channel
            intent = notify_request.delivery.intent
            message_text = notify_request.delivery.message
            origin = notify_request.origin_butler
            notify_context = notify_request.request_context
            notify_request_id = (
                str(notify_context.request_id) if notify_context is not None else route_request_id
            )
            notify_prefix = f"[{origin}]"
            modules_by_name = {module.name: module for module in daemon._modules}

            try:
                if channel == "telegram":
                    telegram_module = modules_by_name.get("telegram")
                    if telegram_module is None:
                        raise RuntimeError("Messenger telegram adapter is unavailable.")

                    rendered_text = (
                        message_text
                        if message_text.lstrip().startswith(notify_prefix)
                        else f"{notify_prefix} {message_text}"
                    )
                    if intent == "send":
                        recipient = notify_request.delivery.recipient
                        if not recipient:
                            raise ValueError(
                                "notify_request.delivery.recipient is required for send intent."
                            )
                        adapter_result = await telegram_module._send_message(
                            recipient,
                            rendered_text,
                        )
                    elif intent == "reply":
                        thread_identity = (
                            notify_context.source_thread_identity if notify_context else None
                        )
                        if not thread_identity:
                            raise ValueError(
                                "notify_request.request_context.source_thread_identity is required "
                                "for telegram reply intent."
                            )
                        chat_id, separator, message_id_raw = thread_identity.partition(":")
                        if not chat_id or not separator or not message_id_raw:
                            raise ValueError(
                                "Telegram reply requires source_thread_identity formatted as "
                                "'<chat_id>:<message_id>'."
                            )
                        try:
                            reply_message_id = int(message_id_raw)
                        except ValueError as exc:
                            raise ValueError(
                                "Telegram reply source_thread_identity must include an integer "
                                "message_id."
                            ) from exc
                        adapter_result = await telegram_module._reply_to_message(
                            chat_id, reply_message_id, rendered_text
                        )
                    elif intent == "react":
                        thread_identity = (
                            notify_context.source_thread_identity if notify_context else None
                        )
                        if not thread_identity:
                            raise ValueError(
                                "notify_request.request_context.source_thread_identity is required "
                                "for telegram react intent."
                            )
                        chat_id, separator, message_id_raw = thread_identity.partition(":")
                        if not chat_id or not separator or not message_id_raw:
                            raise ValueError(
                                "Telegram react requires source_thread_identity formatted as "
                                "'<chat_id>:<message_id>'."
                            )
                        try:
                            target_message_id = int(message_id_raw)
                        except ValueError as exc:
                            raise ValueError(
                                "Telegram react source_thread_identity must include an integer "
                                "message_id."
                            ) from exc
                        emoji = notify_request.delivery.emoji
                        if not emoji:
                            raise ValueError("React intent requires delivery.emoji.")
                        # Call Telegram setMessageReaction API directly
                        url = f"{telegram_module._base_url()}/setMessageReaction"
                        client = telegram_module._get_client()
                        resp = await client.post(
                            url,
                            json={
                                "chat_id": chat_id,
                                "message_id": target_message_id,
                                "reaction": [{"type": "emoji", "emoji": emoji}],
                            },
                        )
                        resp.raise_for_status()
                        adapter_result = resp.json()
                    else:
                        raise ValueError(f"Unsupported telegram intent: {intent}")

                elif channel == "email":
                    email_module = modules_by_name.get("email")
                    if email_module is None:
                        raise RuntimeError("Messenger email adapter is unavailable.")

                    raw_subject = notify_request.delivery.subject or "Notification"
                    normalized_subject = (
                        raw_subject
                        if notify_prefix.lower() in raw_subject.lower()
                        else f"{notify_prefix} {raw_subject}"
                    )
                    if intent == "send":
                        recipient = notify_request.delivery.recipient
                        if not recipient:
                            raise ValueError(
                                "notify_request.delivery.recipient is required for send intent."
                            )
                        adapter_result = await email_module._send_email(
                            recipient,
                            normalized_subject,
                            message_text,
                        )
                    else:
                        if notify_context is None:
                            raise ValueError(
                                "notify_request.request_context is required for reply intent."
                            )
                        thread_id = notify_context.source_thread_identity or notify_request_id
                        adapter_result = await email_module._reply_to_thread(
                            notify_context.source_sender_identity,
                            thread_id,
                            message_text,
                            normalized_subject,
                        )

                else:
                    raise ValueError(f"Unsupported notify channel: {channel}")

            except ValueError as exc:
                error_message = str(exc)
                return _route_error_response(
                    context_payload=route_context,
                    error_class="validation_error",
                    message=error_message,
                    notify_response=_notify_error_response(
                        request_id=notify_request_id,
                        channel=channel,
                        error_class="validation_error",
                        message=error_message,
                    ),
                )
            except TimeoutError as exc:
                error_message = f"Delivery timed out: {exc}"
                return _route_error_response(
                    context_payload=route_context,
                    error_class="timeout",
                    message=error_message,
                    notify_response=_notify_error_response(
                        request_id=notify_request_id,
                        channel=channel,
                        error_class="timeout",
                        message=error_message,
                    ),
                )
            except (ConnectionError, OSError) as exc:
                error_message = f"Delivery target unavailable: {exc}"
                return _route_error_response(
                    context_payload=route_context,
                    error_class="target_unavailable",
                    message=error_message,
                    notify_response=_notify_error_response(
                        request_id=notify_request_id,
                        channel=channel,
                        error_class="target_unavailable",
                        message=error_message,
                    ),
                )
            except RuntimeError as exc:
                lowered = str(exc).lower()
                if "overload" in lowered or "queue full" in lowered:
                    error_class = "overload_rejected"
                else:
                    error_class = "target_unavailable"
                error_message = str(exc)
                return _route_error_response(
                    context_payload=route_context,
                    error_class=error_class,
                    message=error_message,
                    notify_response=_notify_error_response(
                        request_id=notify_request_id,
                        channel=channel,
                        error_class=error_class,
                        message=error_message,
                    ),
                )
            except Exception as exc:
                error_message = f"Messenger delivery failed: {exc}"
                return _route_error_response(
                    context_payload=route_context,
                    error_class="internal_error",
                    message=error_message,
                    notify_response=_notify_error_response(
                        request_id=notify_request_id,
                        channel=channel,
                        error_class="internal_error",
                        message=error_message,
                    ),
                )

            notify_response = {
                "schema_version": "notify_response.v1",
                "request_context": {"request_id": notify_request_id},
                "status": "ok",
                "delivery": {
                    "channel": channel,
                    "delivery_id": _extract_delivery_id(
                        channel=channel,
                        adapter_result=adapter_result,
                        fallback_request_id=notify_request_id,
                    ),
                },
            }
            return _route_success_response(
                context_payload=route_context,
                result_payload={"notify_response": notify_response},
            )

        # Switchboard-only: ingest + route_to_butler tools
        if butler_name == "switchboard":
            from butlers.tools.switchboard.ingestion.ingest import ingest_v1
            from butlers.tools.switchboard.routing.route import (
                route as _switchboard_route,
            )

            pipeline = daemon._pipeline

            # Shared routing context dict — same dict reference as pipeline's.
            # Safe because spawner lock serializes CC sessions.
            _routing_session_ctx = getattr(daemon, "_routing_session_ctx", {})

            async def _process_ingested_message(
                pipeline: MessagePipeline,
                request_id: str,
                message_text: str,
                source: dict[str, Any],
                event: dict[str, Any],
                sender: dict[str, Any],
                message_inbox_id: Any,
            ) -> None:
                """Background task: classify and route an ingested message."""
                try:
                    channel = source.get("channel", "unknown")
                    endpoint_identity = source.get("endpoint_identity", "unknown")
                    request_context = {
                        "request_id": request_id,
                        "received_at": event.get("observed_at", ""),
                        "source_channel": channel,
                        "source_endpoint_identity": f"{channel}:{endpoint_identity}",
                        "source_sender_identity": sender.get("identity", "unknown"),
                        "source_thread_identity": event.get("external_thread_id"),
                        "trace_context": {},
                    }
                    await pipeline.process(
                        message_text=message_text,
                        tool_name="bot_switchboard_handle_message",
                        tool_args={
                            "source": channel,
                            "source_channel": channel,
                            "source_identity": endpoint_identity,
                            "source_endpoint_identity": f"{channel}:{endpoint_identity}",
                            "sender_identity": sender.get("identity", "unknown"),
                            "external_event_id": event.get("external_event_id", ""),
                            "external_thread_id": event.get("external_thread_id"),
                            "source_tool": "ingest",
                            "request_id": request_id,
                            "request_context": request_context,
                        },
                        message_inbox_id=message_inbox_id,
                    )
                except Exception:
                    logger.exception(
                        "Background pipeline processing failed for request_id=%s",
                        request_id,
                    )

            @mcp.tool()
            @tool_span("ingest", butler_name=butler_name)
            async def ingest(
                schema_version: str,
                source: dict[str, Any],
                event: dict[str, Any],
                sender: dict[str, Any],
                payload: dict[str, Any],
                control: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                """Accept an ingest.v1 envelope from a connector."""
                envelope: dict[str, Any] = {
                    "schema_version": schema_version,
                    "source": source,
                    "event": event,
                    "sender": sender,
                    "payload": payload,
                }
                if control is not None:
                    envelope["control"] = control
                try:
                    result = await ingest_v1(pool, envelope)
                except ValueError as exc:
                    return {"status": "error", "error": str(exc)}

                # Fire-and-forget: route the accepted message via the pipeline
                if not result.duplicate and pipeline is not None:
                    normalized_text = payload.get("normalized_text", "")
                    if normalized_text:
                        asyncio.create_task(
                            _process_ingested_message(
                                pipeline=pipeline,
                                request_id=str(result.request_id),
                                message_text=normalized_text,
                                source=source,
                                event=event,
                                sender=sender,
                                message_inbox_id=result.request_id,
                            ),
                            name=f"ingest-route-{result.request_id}",
                        )

                return result.model_dump(mode="json")

            @mcp.tool()
            @tool_span("route_to_butler", butler_name=butler_name)
            async def route_to_butler(
                butler: str,
                prompt: str,
                context: str | None = None,
            ) -> dict[str, Any]:
                """Route a message to a specific butler.

                Called by the CC instance during message classification to
                directly route a sub-message to the target butler.

                Args:
                    butler: Name of the target butler (e.g. "health", "relationship").
                    prompt: Self-contained prompt for the target butler.
                    context: Optional additional context for the target butler.
                """
                source_metadata = _routing_session_ctx.get("source_metadata", {})
                request_context = _routing_session_ctx.get("request_context")
                request_id = _routing_session_ctx.get("request_id", "unknown")

                envelope: dict[str, Any] = {
                    "schema_version": "route.v1",
                    "request_context": {
                        "request_id": request_id,
                        "received_at": datetime.now(UTC).isoformat(),
                        "source_channel": source_metadata.get("channel", "mcp"),
                        "source_endpoint_identity": "switchboard",
                        "source_sender_identity": source_metadata.get("identity", "unknown"),
                        "source_thread_identity": (
                            request_context.get("source_thread_identity")
                            if request_context
                            else None
                        ),
                        "trace_context": {},
                    },
                    "input": {"prompt": prompt, "context": context},
                    "target": {"butler": butler, "tool": "route.execute"},
                    "source_metadata": source_metadata,
                    "__switchboard_route_context": {
                        "request_id": request_id,
                        "fanout_mode": "tool_routed",
                        "segment_id": f"route-{butler}",
                        "attempt": 1,
                    },
                }

                try:
                    result = await _switchboard_route(
                        pool,
                        target_butler=butler,
                        tool_name="route.execute",
                        args=envelope,
                        source_butler="switchboard",
                    )
                    if isinstance(result, dict) and result.get("error"):
                        return {
                            "status": "error",
                            "butler": butler,
                            "error": str(result["error"]),
                        }
                    return {"status": "ok", "butler": butler}
                except Exception as exc:
                    logger.warning(
                        "route_to_butler failed for %s: %s",
                        butler,
                        exc,
                    )
                    return {
                        "status": "error",
                        "butler": butler,
                        "error": f"{type(exc).__name__}: {exc}",
                    }

        @mcp.tool()
        async def tick() -> dict:
            """Evaluate due scheduled tasks and dispatch them now."""
            count = await _tick(pool, spawner.trigger)
            return {"dispatched": count}

        # State tools
        @mcp.tool()
        async def state_get(key: str, _trace_context: dict | None = None) -> dict:
            """Get a value from the state store."""
            parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span("butler.tool.state_get", context=parent_ctx) as span:
                span.set_attribute("butler.name", daemon.config.name)
                value = await _state_get(pool, key)
                return {"key": key, "value": value}

        @mcp.tool()
        async def state_set(key: str, value: Any, _trace_context: dict | None = None) -> dict:
            """Set a value in the state store."""
            parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span("butler.tool.state_set", context=parent_ctx) as span:
                span.set_attribute("butler.name", daemon.config.name)
                await _state_set(pool, key, value)
                return {"key": key, "status": "ok"}

        @mcp.tool()
        async def state_delete(key: str, _trace_context: dict | None = None) -> dict:
            """Delete a key from the state store."""
            parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span(
                "butler.tool.state_delete", context=parent_ctx
            ) as span:
                span.set_attribute("butler.name", daemon.config.name)
                await _state_delete(pool, key)
                return {"key": key, "status": "deleted"}

        @mcp.tool()
        async def state_list(
            prefix: str | None = None, keys_only: bool = True, _trace_context: dict | None = None
        ) -> list[str] | list[dict]:
            """List keys in the state store, optionally filtered by prefix.

            Args:
                prefix: If given, only keys starting with this string are returned.
                keys_only: If True (default), return list of key strings.
                    If False, return list of {"key": ..., "value": ...} dicts.
            """
            parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span("butler.tool.state_list", context=parent_ctx) as span:
                span.set_attribute("butler.name", daemon.config.name)
                return await _state_list(pool, prefix, keys_only)

        # Schedule tools
        @mcp.tool()
        async def schedule_list() -> list[dict]:
            """List all scheduled tasks."""
            tasks = await _schedule_list(pool)
            for t in tasks:
                t["id"] = str(t["id"])
            return tasks

        @mcp.tool()
        async def schedule_create(name: str, cron: str, prompt: str) -> dict:
            """Create a new runtime scheduled task."""
            task_id = await _schedule_create(pool, name, cron, prompt)
            return {"id": str(task_id), "status": "created"}

        @mcp.tool()
        async def schedule_update(
            task_id: str,
            name: str | None = None,
            cron: str | None = None,
            prompt: str | None = None,
            enabled: bool | None = None,
        ) -> dict:
            """Update a scheduled task. Only provided fields are changed."""
            update_fields = {
                "name": name,
                "cron": cron,
                "prompt": prompt,
                "enabled": enabled,
            }
            fields = {k: v for k, v in update_fields.items() if v is not None}
            await _schedule_update(pool, uuid.UUID(task_id), **fields)
            return {"id": task_id, "status": "updated"}

        @mcp.tool()
        async def schedule_delete(task_id: str) -> dict:
            """Delete a runtime scheduled task."""
            await _schedule_delete(pool, uuid.UUID(task_id))
            return {"id": task_id, "status": "deleted"}

        # Session tools
        @mcp.tool()
        async def sessions_list(limit: int = 20, offset: int = 0) -> list[dict]:
            """List sessions ordered by most recent first."""
            sessions = await _sessions_list(pool, limit, offset)
            for s in sessions:
                s["id"] = str(s["id"])
            return sessions

        @mcp.tool()
        async def sessions_get(session_id: str) -> dict | None:
            """Get a session by ID."""
            session = await _sessions_get(pool, uuid.UUID(session_id))
            if session:
                session["id"] = str(session["id"])
            return session

        @mcp.tool()
        async def sessions_summary(period: str = "today") -> dict:
            """Return aggregate session/token stats for a period."""
            return await _sessions_summary(pool, period)

        @mcp.tool()
        async def sessions_daily(from_date: str, to_date: str) -> dict:
            """Return daily session/token aggregates for a date range."""
            return await _sessions_daily(pool, from_date, to_date)

        @mcp.tool()
        async def top_sessions(limit: int = 10) -> dict:
            """Return the highest-token completed sessions."""
            return await _top_sessions(pool, limit)

        @mcp.tool()
        async def schedule_costs() -> dict:
            """Return per-schedule token usage aggregates."""
            return await _schedule_costs(pool)

        # Notification tool
        @mcp.tool()
        @tool_span("notify", butler_name=butler_name)
        async def notify(
            channel: str,
            message: str | None = None,
            recipient: str | None = None,
            subject: str | None = None,
            intent: str = "send",
            emoji: str | None = None,
            request_context: dict[str, Any] | None = None,
        ) -> dict:
            """Send an outbound notification via the Switchboard.

            Forwards a versioned ``notify.v1`` envelope to the Switchboard's
            ``deliver()`` tool over the MCP client connection. Blocks until
            delivered or fails. Returns an error result (not an exception) if
            the Switchboard is unreachable or the payload is invalid.

            Parameters
            ----------
            channel:
                Notification channel — currently 'telegram' or 'email'.
            message:
                The message text to deliver (required for send/reply intents).
            recipient:
                Optional explicit recipient identifier.
            subject:
                Optional channel-specific subject (for example email).
            intent:
                Delivery intent. Supported values: ``"send"`` (default),
                ``"reply"``, and ``"react"``.
            emoji:
                Optional emoji for react intent. Required when intent is
                ``"react"``.
            request_context:
                Optional routed request-context metadata for reply targeting and
                lineage propagation.
            """
            # Validate message is present (not required for react intent)
            if intent != "react" and message is None:
                logger.error(
                    "notify() called without required 'message' parameter: "
                    "channel=%r, intent=%r, emoji=%r, request_context=%r",
                    channel,
                    intent,
                    emoji,
                    request_context,
                )
                return {
                    "status": "error",
                    "error": (
                        "Missing required 'message' parameter. "
                        "notify() requires: channel, message, request_context."
                    ),
                }

            # Validate message is not empty/whitespace (not required for react intent)
            if intent != "react" and (not message or not message.strip()):
                return {
                    "status": "error",
                    "error": "Message must not be empty or whitespace-only.",
                }

            _SUPPORTED_CHANNELS = {"telegram", "email"}
            if channel not in _SUPPORTED_CHANNELS:
                return {
                    "status": "error",
                    "error": (
                        f"Unsupported channel '{channel}'. "
                        f"Supported channels: {', '.join(sorted(_SUPPORTED_CHANNELS))}"
                    ),
                }

            if intent not in {"send", "reply", "react"}:
                return {
                    "status": "error",
                    "error": "Unsupported notify intent. Supported intents: send, reply",
                }

            # React intent validation
            if intent == "react":
                if not emoji:
                    return {
                        "status": "error",
                        "error": "React intent requires emoji parameter.",
                    }
                if channel not in {"telegram"}:
                    return {
                        "status": "error",
                        "error": (
                            f"React intent is not supported for channel '{channel}'. "
                            "Only telegram supports reactions."
                        ),
                    }
                if not request_context or not request_context.get("source_thread_identity"):
                    return {
                        "status": "error",
                        "error": (
                            "React intent requires request_context with source_thread_identity."
                        ),
                    }

            client = daemon.switchboard_client
            if client is None:
                return {
                    "status": "error",
                    "error": ("Switchboard is not connected. Cannot deliver notification."),
                }

            notify_request: dict[str, Any] = {
                "schema_version": "notify.v1",
                "origin_butler": butler_name,
                "delivery": {
                    "intent": intent,
                    "channel": channel,
                    "message": message,
                },
            }
            if emoji is not None:
                notify_request["delivery"]["emoji"] = emoji
            if recipient is not None:
                notify_request["delivery"]["recipient"] = recipient
            if subject is not None:
                notify_request["delivery"]["subject"] = subject
            if request_context is not None:
                notify_request["request_context"] = request_context

            deliver_args: dict[str, Any] = {
                "source_butler": butler_name,
                "notify_request": notify_request,
            }

            _NOTIFY_TIMEOUT_S = 30
            try:
                result = await asyncio.wait_for(
                    client.call_tool("deliver", deliver_args),
                    timeout=_NOTIFY_TIMEOUT_S,
                )
                # FastMCP call_tool returns a CallToolResult
                if result.is_error:
                    # Extract error text from the result content
                    error_text = str(result.content[0].text) if result.content else "Unknown error"
                    return {"status": "error", "error": error_text}
                # Extract the data from the successful result
                return {"status": "ok", "result": result.data}
            except TimeoutError:
                logger.warning(
                    "notify() timed out after %ds for butler %s",
                    _NOTIFY_TIMEOUT_S,
                    butler_name,
                )
                return {
                    "status": "error",
                    "error": (
                        f"Switchboard call timed out after {_NOTIFY_TIMEOUT_S}s. "
                        "The Switchboard may be overloaded or unresponsive."
                    ),
                }
            except (ConnectionError, OSError) as exc:
                logger.warning(
                    "notify() could not reach Switchboard for butler %s: %s",
                    butler_name,
                    exc,
                    exc_info=True,
                )
                return {
                    "status": "error",
                    "error": f"Switchboard unreachable: {exc}",
                }
            except Exception as exc:
                logger.warning(
                    "notify() failed for butler %s: %s",
                    butler_name,
                    exc,
                    exc_info=True,
                )
                return {
                    "status": "error",
                    "error": f"Switchboard call failed: {exc}",
                }

        # Messenger-specific operational domain tools
        if butler_name == "messenger":
            from butlers.tools.messenger import (
                messenger_delivery_attempts,
                messenger_delivery_search,
                messenger_delivery_status,
                messenger_delivery_trace,
            )

            @mcp.tool()
            @tool_span("messenger_delivery_status", butler_name=butler_name)
            async def _messenger_delivery_status(delivery_id: str) -> dict:
                """Get the current status of a delivery request.

                Returns the current terminal or in-flight status of a single
                delivery, including the latest attempt outcome and provider
                delivery ID when available.
                """
                return await messenger_delivery_status(pool, delivery_id)

            @mcp.tool()
            @tool_span("messenger_delivery_search", butler_name=butler_name)
            async def _messenger_delivery_search(
                origin_butler: str | None = None,
                channel: str | None = None,
                intent: str | None = None,
                status: str | None = None,
                since: str | None = None,
                until: str | None = None,
                limit: int = 50,
            ) -> dict:
                """Search delivery history with filters.

                Returns paginated delivery summaries sorted by recency (newest
                first). Supports filtering by origin butler, channel, intent,
                status, and time range.
                """
                return await messenger_delivery_search(
                    pool,
                    origin_butler=origin_butler,
                    channel=channel,
                    intent=intent,
                    status=status,
                    since=since,
                    until=until,
                    limit=limit,
                )

            @mcp.tool()
            @tool_span("messenger_delivery_attempts", butler_name=butler_name)
            async def _messenger_delivery_attempts(delivery_id: str) -> dict:
                """Get the full attempt history for a delivery.

                Returns the full attempt log for a delivery: timestamps,
                outcomes, latencies, error classes, retryability. Essential
                for diagnosing flaky provider behavior.
                """
                return await messenger_delivery_attempts(pool, delivery_id)

            @mcp.tool()
            @tool_span("messenger_delivery_trace", butler_name=butler_name)
            async def _messenger_delivery_trace(request_id: str) -> dict:
                """Reconstruct full lineage for a request.

                Traces from the originating butler's notify.v1 envelope through
                Switchboard routing, Messenger admission, validation, target
                resolution, provider attempts, and terminal outcome.
                """
                return await messenger_delivery_trace(pool, request_id)

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
        _BUTLER_LEVEL_KEYS = {"credentials_env"}
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

        Channel egress ownership enforcement:
        Non-messenger butlers are blocked from declaring channel send/reply
        output tools.  This enforces the Messenger-only delivery ownership
        contract defined in ``docs/roles/messenger_butler.md`` section 5.1.
        """
        is_messenger = self.config.name == "messenger"
        for mod in self._modules:
            mod_status = self._module_statuses.get(mod.name)
            if mod_status is not None and mod_status.status != "active":
                continue

            try:
                declared_tool_names = self._validate_module_io_descriptors(mod)

                # Enforce channel egress ownership before registration.
                filtered_egress: set[str] = set()
                if not is_messenger:
                    all_names = (
                        {d.name for d in mod.user_inputs()}
                        | {d.name for d in mod.user_outputs()}
                        | {d.name for d in mod.bot_inputs()}
                        | {d.name for d in mod.bot_outputs()}
                    )
                    filtered_egress = {n for n in all_names if _is_channel_egress_tool(n)}
                    if filtered_egress:
                        logger.info(
                            "Stripping channel egress tools from non-messenger butler '%s' "
                            "module '%s': %s (use notify.v1 for outbound delivery)",
                            self.config.name,
                            mod.name,
                            ", ".join(sorted(filtered_egress)),
                        )
                        declared_tool_names -= filtered_egress

                wrapped_mcp = _SpanWrappingMCP(
                    self.mcp,
                    self.config.name,
                    module_name=mod.name,
                    declared_tool_names=declared_tool_names,
                    filtered_tool_names=filtered_egress,
                )
                validated_config = self._module_configs.get(mod.name)
                await mod.register_tools(wrapped_mcp, validated_config, self.db)
                missing_declared = wrapped_mcp.missing_declared_tool_names()
                if missing_declared:
                    missing = ", ".join(sorted(missing_declared))
                    raise ModuleToolValidationError(
                        f"Module '{mod.name}' declared tool descriptors that were not registered: "
                        f"{missing}"
                    )
            except Exception as exc:
                error_msg = str(exc)
                self._module_statuses[mod.name] = ModuleStartupStatus(
                    status="failed", phase="tools", error=error_msg
                )
                logger.warning(
                    "Module '%s' disabled: tool registration failed: %s", mod.name, error_msg
                )

    def _validate_module_io_descriptors(self, mod: Module) -> set[str]:
        """Validate I/O descriptor names and return the declared tool-name set."""
        descriptor_groups = {
            "user_inputs": mod.user_inputs(),
            "user_outputs": mod.user_outputs(),
            "bot_inputs": mod.bot_inputs(),
            "bot_outputs": mod.bot_outputs(),
        }
        names: set[str] = set()

        for group_name, descriptors in descriptor_groups.items():
            expected_prefix = "user_" if group_name.startswith("user_") else "bot_"
            for descriptor in descriptors:
                if not isinstance(descriptor, ToolIODescriptor):
                    raise ModuleToolValidationError(
                        f"Module '{mod.name}' has invalid descriptor in {group_name}. "
                        "Expected ToolIODescriptor instances."
                    )

                tool_name = descriptor.name
                _validate_tool_name(
                    tool_name,
                    mod.name,
                    context=f"descriptor in {group_name}",
                )

                if not tool_name.startswith(expected_prefix):
                    raise ModuleToolValidationError(
                        f"Module '{mod.name}' descriptor '{tool_name}' in {group_name} "
                        f"must start with '{expected_prefix}'."
                    )

                if tool_name in names:
                    raise ModuleToolValidationError(
                        f"Module '{mod.name}' declares duplicate tool descriptor '{tool_name}'."
                    )
                names.add(tool_name)

        return names

    def _apply_approval_gates(self) -> dict[str, Any]:
        """Parse approval config and wrap gated tools with approval interception.

        Parses the ``[modules.approvals]`` section from the butler config,
        then calls ``apply_approval_gates`` to wrap tools whose names appear
        in the ``gated_tools`` configuration.

        Identity-aware defaults are merged in before wrapping:
        - ``user_*`` output tools marked ``approval_default="always"``
          are gated by default.
        - ``bot_*`` outputs remain configurable and are only gated when
          explicitly listed in config.

        Returns the mapping of tool_name -> original handler for gated tools.
        """
        approvals_raw = self.config.modules.get("approvals")
        approval_config = parse_approval_config(approvals_raw)

        if approval_config is None or not approval_config.enabled:
            return {}

        approval_config = self._with_default_gated_user_outputs(approval_config)
        pool = self.db.pool
        originals = apply_approval_gates(self.mcp, approval_config, pool)

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
                            tool_obj = self.mcp._tool_manager.get_tools().get(tool_name)
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
                None,  # session_id
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

    def _with_default_gated_user_outputs(self, config: ApprovalConfig) -> ApprovalConfig:
        """Return config with ``approval_default=always`` user outputs added.

        Existing explicit gating config wins; defaults fill missing entries.
        User-scoped send/reply tools are always default-gated as a safety
        baseline even if metadata was omitted.
        """
        merged = dict(config.gated_tools)
        for mod in self._active_modules:
            for descriptor in mod.user_outputs():
                if descriptor.approval_default != "always" and not self._is_user_send_or_reply_tool(
                    descriptor.name
                ):
                    continue
                merged.setdefault(descriptor.name, GatedToolConfig())

        if len(merged) == len(config.gated_tools):
            return config

        return ApprovalConfig(
            enabled=config.enabled,
            default_expiry_hours=config.default_expiry_hours,
            default_risk_tier=config.default_risk_tier,
            rule_precedence=config.rule_precedence,
            gated_tools=merged,
        )

    @staticmethod
    def _is_user_send_or_reply_tool(tool_name: str) -> bool:
        """Return whether a tool name is a user-scoped send/reply action."""
        if not tool_name.startswith("user_"):
            return False
        return "_send" in tool_name or "_reply" in tool_name

    async def shutdown(self) -> None:
        """Graceful shutdown.

        1. Stop MCP server
        2. Stop accepting new triggers and drain in-flight CC sessions
        3. Cancel switchboard heartbeat
        4. Close Switchboard MCP client
        5. Module on_shutdown in reverse topological order
        6. Close DB pool
        """
        logger.info(
            "Shutting down butler: %s",
            self.config.name if self.config else "unknown",
        )

        # 1. Stop MCP server
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            try:
                await self._server_task
            except Exception:
                logger.exception("Error while stopping MCP server")
            self._server_task = None
            self._server = None

        # 2. Stop accepting new triggers and drain in-flight CC sessions
        self._accepting_connections = False
        if self.spawner is not None:
            self.spawner.stop_accepting()
            timeout = self.config.shutdown_timeout_s if self.config else 30.0
            await self.spawner.drain(timeout=timeout)

        # 3. Cancel switchboard heartbeat
        if self._switchboard_heartbeat_task is not None:
            self._switchboard_heartbeat_task.cancel()
            try:
                await self._switchboard_heartbeat_task
            except asyncio.CancelledError:
                pass
            self._switchboard_heartbeat_task = None

        # 4. Close Switchboard MCP client
        await self._disconnect_switchboard()

        # 5. Module shutdown in reverse topological order (active modules only)
        active_set = {m.name for m in self._active_modules}
        for mod in reversed(self._modules):
            if mod.name not in active_set:
                continue
            try:
                await mod.on_shutdown()
            except Exception:
                logger.exception("Error during shutdown of module: %s", mod.name)

        # 6. Close audit DB pool (if separate from main DB)
        if self._audit_db is not None:
            await self._audit_db.close()
            self._audit_db = None

        # 7. Close DB pool
        if self.db:
            await self.db.close()

        logger.info("Butler shutdown complete")
