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

On startup failure, already-initialized modules get on_shutdown() called.

Graceful shutdown: (a) stops the MCP server, (b) stops accepting new triggers,
(c) drains in-flight CC sessions up to a configurable timeout,
(d) closes Switchboard MCP client, (e) shuts down modules in reverse
topological order, (f) closes DB pool.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import uvicorn
from fastmcp import Client as MCPClient
from fastmcp import FastMCP
from opentelemetry import trace
from pydantic import ConfigDict, ValidationError
from starlette.requests import ClientDisconnect

from butlers.config import ButlerConfig, load_config, parse_approval_config
from butlers.core.runtimes import get_adapter
from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.scheduler import schedule_delete as _schedule_delete
from butlers.core.scheduler import schedule_list as _schedule_list
from butlers.core.scheduler import schedule_update as _schedule_update
from butlers.core.scheduler import sync_schedules
from butlers.core.scheduler import tick as _tick
from butlers.core.sessions import sessions_get as _sessions_get
from butlers.core.sessions import sessions_list as _sessions_list
from butlers.core.spawner import Spawner
from butlers.core.state import state_delete as _state_delete
from butlers.core.state import state_get as _state_get
from butlers.core.state import state_list as _state_list
from butlers.core.state import state_set as _state_set
from butlers.core.telemetry import extract_trace_context, init_telemetry, tool_span
from butlers.credentials import detect_secrets, validate_credentials
from butlers.db import Database
from butlers.migrations import has_butler_chain, run_migrations
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.base import Module, ToolIODescriptor
from butlers.modules.pipeline import MessagePipeline
from butlers.modules.registry import ModuleRegistry, default_registry

logger = logging.getLogger(__name__)


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


_TOOL_NAME_RE = re.compile(r"^(user|bot)_[a-z0-9_]+_[a-z0-9_]+$")


def _validate_tool_name(name: str, module_name: str, *, context: str = "registered tool") -> None:
    """Validate a tool name against the identity-prefixed naming contract."""
    if _TOOL_NAME_RE.fullmatch(name):
        return
    raise ModuleToolValidationError(
        f"Module '{module_name}' has invalid {context} name '{name}'. "
        "Expected 'user_<channel>_<action>' or 'bot_<channel>_<action>'."
    )


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

    All other attribute access is forwarded to the underlying FastMCP instance.
    """

    def __init__(
        self,
        mcp: FastMCP,
        butler_name: str,
        *,
        module_name: str | None = None,
        declared_tool_names: set[str] | None = None,
    ) -> None:
        self._mcp = mcp
        self._butler_name = butler_name
        self._module_name = module_name or "unknown"
        self._declared_tool_names = declared_tool_names or set()
        self._registered_tool_names: set[str] = set()

    def tool(self, *args, **kwargs):
        """Return a decorator that wraps the handler with tool_span."""
        declared_name = kwargs.get("name")
        original_decorator = self._mcp.tool(*args, **kwargs)

        def wrapper(fn):  # noqa: ANN001, ANN202
            resolved_tool_name = declared_name or fn.__name__
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
        config_dir: Path,
        registry: ModuleRegistry | None = None,
    ) -> None:
        self.config_dir = config_dir
        self._registry = registry or default_registry()
        self.config: ButlerConfig | None = None
        self.db: Database | None = None
        self.mcp: FastMCP | None = None
        self.spawner: Spawner | None = None
        self._modules: list[Module] = []
        self._module_configs: dict[str, Any] = {}
        self._gated_tool_originals: dict[str, Any] = {}
        self._started_at: float | None = None
        self._accepting_connections = False
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self.switchboard_client: MCPClient | None = None

    async def start(self) -> None:
        """Execute the full startup sequence.

        Steps execute in order. A failure at any step prevents subsequent steps.
        """
        # 1. Load config
        self.config = load_config(self.config_dir)
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

        # 4. Validate module config schemas before env credential checks.
        # This gives clearer feedback for malformed identity-scoped config.
        self._module_configs = self._validate_module_configs()

        # 5. Validate credentials
        module_creds = self._collect_module_credentials()
        validate_credentials(
            self.config.env_required,
            self.config.env_optional,
            module_credentials=module_creds,
        )

        # 6. Provision database
        self.db = Database.from_env(self.config.db_name)
        await self.db.provision()
        pool = await self.db.connect()

        # 7. Run core Alembic migrations
        db_url = self._build_db_url()
        await run_migrations(db_url, chain="core")

        # 7b. Run butler-specific Alembic migrations (if chain exists)
        if has_butler_chain(self.config.name):
            logger.info("Running butler-specific migrations for: %s", self.config.name)
            await run_migrations(db_url, chain=self.config.name)

        # 8. Run module Alembic migrations
        for mod in self._modules:
            rev = mod.migration_revisions()
            if rev:
                await run_migrations(db_url, chain=rev)

        # 9. Call module on_startup (topological order)
        started_modules: list[Module] = []
        try:
            for mod in self._modules:
                validated_config = self._module_configs.get(mod.name)
                await mod.on_startup(validated_config, self.db)
                started_modules.append(mod)
        except Exception:
            # Clean up already-started modules in reverse order
            logger.error(
                "Startup failure; cleaning up %d already-started module(s)",
                len(started_modules),
            )
            for mod in reversed(started_modules):
                try:
                    await mod.on_shutdown()
                except Exception:
                    logger.exception("Error during cleanup shutdown of module: %s", mod.name)
            raise

        # 10. Create Spawner with runtime adapter (verify binary on PATH)
        adapter_cls = get_adapter(self.config.runtime.type)
        runtime = adapter_cls()

        binary = runtime.binary_name
        if not shutil.which(binary):
            raise RuntimeBinaryNotFoundError(
                f"Runtime binary {binary!r} not found on PATH. "
                f"The {self.config.runtime.type!r} runtime requires {binary!r} to be installed."
            )

        self.spawner = Spawner(
            config=self.config,
            config_dir=self.config_dir,
            pool=pool,
            module_credentials_env=module_creds,
            runtime=runtime,
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

        # 13. Register module MCP tools
        await self._register_module_tools()

        # 13b. Apply approval gates to configured gated tools
        self._gated_tool_originals = self._apply_approval_gates()

        # 14. Start FastMCP SSE server on configured port
        await self._start_mcp_server()

        # Mark as accepting connections and record startup time
        self._accepting_connections = True
        self._started_at = time.monotonic()
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

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=self.spawner.trigger,
            source_butler="switchboard",
        )

        wired_modules: list[str] = []
        for mod in self._modules:
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
            log_level="info",
            timeout_graceful_shutdown=0,
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

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
            logger.warning(
                "Failed to connect to Switchboard at %s for butler %s; "
                "notify() will be unavailable until Switchboard is reachable",
                url,
                self.config.name,
                exc_info=True,
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

        Returns 'ok' when all components are healthy, 'degraded' otherwise.
        Currently checks DB pool availability.
        """
        try:
            pool = self.db.pool if self.db else None
            if pool is None:
                return "degraded"
            await pool.fetchval("SELECT 1")
        except Exception:
            logger.warning("Health check failed: DB pool unavailable")
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
            return {
                "name": daemon.config.name,
                "description": daemon.config.description,
                "port": daemon.config.port,
                "modules": [mod.name for mod in daemon._modules],
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

        # Notification tool
        @mcp.tool()
        @tool_span("notify", butler_name=butler_name)
        async def notify(channel: str, message: str, recipient: str | None = None) -> dict:
            """Send an outbound notification via the Switchboard.

            Forwards to the Switchboard's deliver() tool over the MCP client
            connection. Blocks until delivered or fails. Returns an error result
            (not an exception) if the Switchboard is unreachable or the channel
            is invalid.

            Parameters
            ----------
            channel:
                Notification channel — must be 'telegram' or 'email'.
            message:
                The message text to deliver.
            recipient:
                Optional recipient identifier. If omitted, the Switchboard
                delivers to the system owner's default for the channel.
            """
            # Validate message is not empty/whitespace
            if not message or not message.strip():
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

            client = daemon.switchboard_client
            if client is None:
                return {
                    "status": "error",
                    "error": ("Switchboard is not connected. Cannot deliver notification."),
                }

            # Build args for Switchboard's deliver() tool
            deliver_args: dict[str, Any] = {
                "channel": channel,
                "message": message,
                "source_butler": butler_name,
            }
            if recipient is not None:
                deliver_args["recipient"] = recipient

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

    def _validate_module_configs(self) -> dict[str, Any]:
        """Validate each module's raw config dict against its config_schema.

        Returns a mapping of module name to validated Pydantic model instance.
        If a module has no config_schema (returns None), the raw dict is passed
        through for backward compatibility.

        Extra fields not declared in the schema are rejected. Missing required
        fields and type mismatches produce clear error messages.

        Raises
        ------
        ModuleConfigError
            If validation fails (missing required fields, extra unknown fields,
            or type mismatches).
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
                raise ModuleConfigError(
                    f"Configuration validation failed for module '{mod.name}': {exc}"
                ) from exc
        return validated

    async def _register_module_tools(self) -> None:
        """Register MCP tools from all loaded modules.

        Module tools are registered through a ``_SpanWrappingMCP`` proxy that
        automatically wraps each tool handler with a ``butler.tool.<name>``
        span carrying the ``butler.name`` attribute.
        """
        for mod in self._modules:
            declared_tool_names = self._validate_module_io_descriptors(mod)
            wrapped_mcp = _SpanWrappingMCP(
                self.mcp,
                self.config.name,
                module_name=mod.name,
                declared_tool_names=declared_tool_names,
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

        Returns the mapping of tool_name -> original handler for gated tools.
        """
        approvals_raw = self.config.modules.get("approvals")
        approval_config = parse_approval_config(approvals_raw)

        if approval_config is None or not approval_config.enabled:
            return {}

        pool = self.db.pool
        originals = apply_approval_gates(self.mcp, approval_config, pool)

        # Wire the originals into the ApprovalsModule if it's loaded,
        # so the post-approval executor can invoke them directly
        if originals:
            for mod in self._modules:
                if mod.name == "approvals":
                    # Set up a tool executor that calls the original tool function
                    async def _execute_original(
                        tool_name: str,
                        tool_args: dict[str, Any],
                        _originals: dict[str, Any] = originals,
                    ) -> dict[str, Any]:
                        original_fn = _originals.get(tool_name)
                        if original_fn is None:
                            return {"error": f"No original handler for tool: {tool_name}"}
                        return await original_fn(**tool_args)

                    mod.set_tool_executor(_execute_original)
                    break

            logger.info(
                "Applied approval gates to %d tool(s): %s",
                len(originals),
                ", ".join(sorted(originals.keys())),
            )

        return originals

    async def shutdown(self) -> None:
        """Graceful shutdown.

        1. Stop MCP server
        2. Stop accepting new triggers and drain in-flight CC sessions
        3. Close Switchboard MCP client
        4. Module on_shutdown in reverse topological order
        5. Close DB pool
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

        # 3. Close Switchboard MCP client
        await self._disconnect_switchboard()

        # 4. Module shutdown in reverse topological order
        for mod in reversed(self._modules):
            try:
                await mod.on_shutdown()
            except Exception:
                logger.exception("Error during shutdown of module: %s", mod.name)

        # 5. Close DB pool
        if self.db:
            await self.db.close()

        logger.info("Butler shutdown complete")
