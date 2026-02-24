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
8c. Validate core credentials (ANTHROPIC_API_KEY) via DB-first resolution (fatal)
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
import functools
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, TypedDict
from urllib.parse import parse_qs, quote, quote_plus

import asyncpg
import httpx
import uvicorn
from fastapi import APIRouter
from fastmcp import Client as MCPClient
from fastmcp import FastMCP
from opentelemetry import trace
from opentelemetry.context import Context as OtelContext
from opentelemetry.trace import Link as OtelLink
from pydantic import ConfigDict, Field, ValidationError
from starlette.requests import ClientDisconnect
from starlette.routing import Mount, Route

from butlers.config import (
    ApprovalConfig,
    ButlerConfig,
    GatedToolConfig,
    load_config,
    parse_approval_config,
)
from butlers.core.logging import resolve_log_root
from butlers.core.metrics import ButlerMetrics, init_metrics
from butlers.core.route_inbox import (
    route_inbox_insert,
    route_inbox_mark_errored,
    route_inbox_mark_processed,
    route_inbox_mark_processing,
    route_inbox_recovery_sweep,
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
from butlers.core.telemetry import extract_trace_context, init_telemetry, tag_butler_span, tool_span
from butlers.core.tool_call_capture import (
    capture_tool_call,
    get_current_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.credential_store import (
    CredentialStore,
    ensure_secrets_schema,
    shared_db_name_from_env,
)
from butlers.credentials import (
    detect_secrets,
    validate_core_credentials_async,
    validate_credentials,
    validate_module_credentials_async,
)
from butlers.db import Database, schema_search_path
from butlers.migrations import has_butler_chain, run_migrations
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.base import Module, ToolIODescriptor
from butlers.modules.pipeline import MessagePipeline, _routing_ctx_var
from butlers.modules.registry import ModuleRegistry, default_registry
from butlers.storage import BlobNotFoundError, LocalBlobStore
from butlers.tools.attachments import get_attachment as _get_attachment
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
        "remind",
        "get_attachment",
        "module.states",
        "module.set_enabled",
    }
)

_DEFAULT_TELEGRAM_CHAT_SECRET = "BUTLER_TELEGRAM_CHAT_ID"
_NO_TELEGRAM_CHAT_CONFIGURED_ERROR = (
    "No bot <-> user telegram chat has been configured - please set "
    "BUTLER_TELEGRAM_CHAT_ID in /secrets"
)


type _DeterministicScheduleJobHandler = Callable[
    [asyncpg.Pool, dict[str, Any] | None], Awaitable[Any]
]


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


@functools.lru_cache(maxsize=1)
def _load_switchboard_eligibility_sweep_job() -> Callable[
    [asyncpg.Pool], Awaitable[dict[str, Any]]
]:
    """Load the switchboard eligibility sweep job from roster/ by file path."""
    import importlib.util as _ilu

    module_path = (
        Path(__file__).resolve().parents[2]
        / "roster"
        / "switchboard"
        / "jobs"
        / "eligibility_sweep.py"
    )
    module_name = "roster_switchboard_eligibility_sweep_job"
    spec = _ilu.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load switchboard eligibility sweep job from {module_path}")
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_eligibility_sweep_job


@functools.lru_cache(maxsize=1)
def _load_switchboard_connector_stats_jobs() -> tuple[
    Callable[[asyncpg.Pool], Awaitable[dict[str, int]]],
    Callable[[asyncpg.Pool], Awaitable[dict[str, int]]],
    Callable[[asyncpg.Pool], Awaitable[dict[str, int]]],
]:
    """Load switchboard connector statistics jobs from roster/ by file path."""
    import importlib.util as _ilu

    module_path = (
        Path(__file__).resolve().parents[2]
        / "roster"
        / "switchboard"
        / "jobs"
        / "connector_stats.py"
    )
    module_name = "roster_switchboard_connector_stats_jobs"
    spec = _ilu.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load switchboard connector stats jobs from {module_path}")
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        module.run_connector_stats_hourly_rollup,
        module.run_connector_stats_daily_rollup,
        module.run_connector_stats_pruning,
    )


async def _run_switchboard_eligibility_sweep_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the switchboard eligibility sweep deterministic schedule job."""
    del job_args
    run_eligibility_sweep_job = _load_switchboard_eligibility_sweep_job()
    return await run_eligibility_sweep_job(pool)


async def _run_switchboard_connector_stats_hourly_rollup_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, int]:
    """Run the switchboard connector statistics hourly rollup deterministic job."""
    del job_args
    run_hourly_rollup, _, _ = _load_switchboard_connector_stats_jobs()
    return await run_hourly_rollup(pool)


async def _run_switchboard_connector_stats_daily_rollup_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, int]:
    """Run the switchboard connector statistics daily rollup deterministic job."""
    del job_args
    _, run_daily_rollup, _ = _load_switchboard_connector_stats_jobs()
    return await run_daily_rollup(pool)


async def _run_switchboard_connector_stats_pruning_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, int]:
    """Run the switchboard connector statistics pruning deterministic job."""
    del job_args
    _, _, run_pruning = _load_switchboard_connector_stats_jobs()
    return await run_pruning(pool)


async def _run_memory_consolidation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run memory consolidation directly without spawning an LLM runtime session."""
    del job_args
    from butlers.modules.memory.consolidation import run_consolidation

    return await run_consolidation(pool=pool, embedding_engine=None, cc_spawner=None)


async def _run_memory_episode_cleanup_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run memory episode cleanup directly without spawning an LLM runtime session."""
    from butlers.modules.memory.consolidation import run_episode_cleanup

    max_entries = 10000
    if job_args is not None:
        unknown_args = sorted(set(job_args) - {"max_entries"})
        if unknown_args:
            raise RuntimeError(
                "memory_episode_cleanup job only supports job_args.max_entries; "
                f"received unsupported keys: {unknown_args}"
            )
        if "max_entries" in job_args:
            raw_max_entries = job_args["max_entries"]
            if (
                not isinstance(raw_max_entries, int)
                or isinstance(raw_max_entries, bool)
                or raw_max_entries <= 0
            ):
                raise RuntimeError(
                    "memory_episode_cleanup job_args.max_entries must be a positive integer"
                )
            max_entries = raw_max_entries

    return await run_episode_cleanup(pool=pool, max_entries=max_entries)


_MEMORY_MAINTENANCE_JOB_HANDLERS: dict[str, _DeterministicScheduleJobHandler] = {
    "memory_consolidation": _run_memory_consolidation_job,
    "memory_episode_cleanup": _run_memory_episode_cleanup_job,
}

_DETERMINISTIC_SCHEDULE_JOB_REGISTRY: dict[str, dict[str, _DeterministicScheduleJobHandler]] = {
    "general": dict(_MEMORY_MAINTENANCE_JOB_HANDLERS),
    "health": dict(_MEMORY_MAINTENANCE_JOB_HANDLERS),
    "relationship": dict(_MEMORY_MAINTENANCE_JOB_HANDLERS),
    "switchboard": {
        "connector_stats_hourly_rollup": _run_switchboard_connector_stats_hourly_rollup_job,
        "connector_stats_daily_rollup": _run_switchboard_connector_stats_daily_rollup_job,
        "connector_stats_pruning": _run_switchboard_connector_stats_pruning_job,
        "eligibility_sweep": _run_switchboard_eligibility_sweep_job,
        **_MEMORY_MAINTENANCE_JOB_HANDLERS,
    },
}

# Backward compatibility for legacy prompt-mode schedule names that now map
# to deterministic jobs.
_MEMORY_SCHEDULE_LEGACY_ALIASES: dict[str, str] = {
    "memory-consolidation": "memory_consolidation",
    "memory-episode-cleanup": "memory_episode_cleanup",
}

_DETERMINISTIC_SCHEDULE_LEGACY_ALIASES: dict[str, dict[str, str]] = {
    "general": dict(_MEMORY_SCHEDULE_LEGACY_ALIASES),
    "health": dict(_MEMORY_SCHEDULE_LEGACY_ALIASES),
    "relationship": dict(_MEMORY_SCHEDULE_LEGACY_ALIASES),
    "switchboard": {
        "connector-stats-hourly-rollup": "connector_stats_hourly_rollup",
        "connector-stats-daily-rollup": "connector_stats_daily_rollup",
        "connector-stats-pruning": "connector_stats_pruning",
        "eligibility-sweep": "eligibility_sweep",
        **_MEMORY_SCHEDULE_LEGACY_ALIASES,
    },
}


def _resolve_deterministic_schedule_job_name(
    *,
    butler_name: str,
    trigger_source: str,
    job_name: str | None,
) -> str | None:
    """Resolve deterministic schedule job name from explicit job or legacy alias."""
    if job_name is not None:
        normalized_job_name = job_name.strip()
        if not normalized_job_name:
            raise RuntimeError(
                "Deterministic scheduler job_name must be a non-empty string "
                f"(butler={butler_name!r})"
            )
        return normalized_job_name

    schedule_prefix = "schedule:"
    if not trigger_source.startswith(schedule_prefix):
        return None

    schedule_name = trigger_source[len(schedule_prefix) :].strip()
    if not schedule_name:
        return None
    aliases = _DETERMINISTIC_SCHEDULE_LEGACY_ALIASES.get(butler_name, {})
    return aliases.get(schedule_name)


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


class _McpRuntimeSessionGuard:
    """Bind runtime session IDs from MCP query params into request context."""

    _MAX_SESSION_MAP_SIZE = 4096

    def __init__(self, app: Any) -> None:
        self._app = app
        self._mcp_session_to_runtime_session: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        """Proxy unknown attributes to wrapped ASGI app for compatibility."""
        return getattr(self._app, name)

    def _resolve_runtime_session_id(self, scope: dict[str, Any]) -> str | None:
        query_string = scope.get("query_string")
        if not isinstance(query_string, (bytes, bytearray)):
            return None

        parsed = parse_qs(query_string.decode("utf-8", errors="replace"))
        runtime_values = parsed.get("runtime_session_id")
        runtime_session_id = runtime_values[0].strip() if runtime_values else None
        mcp_values = parsed.get("session_id")
        mcp_session_id = mcp_values[0].strip() if mcp_values else None

        if runtime_session_id and mcp_session_id:
            self._mcp_session_to_runtime_session[mcp_session_id] = runtime_session_id
            if len(self._mcp_session_to_runtime_session) > self._MAX_SESSION_MAP_SIZE:
                oldest = next(iter(self._mcp_session_to_runtime_session))
                self._mcp_session_to_runtime_session.pop(oldest, None)

        if runtime_session_id:
            return runtime_session_id
        if mcp_session_id:
            return self._mcp_session_to_runtime_session.get(mcp_session_id)
        return None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime_session_id = self._resolve_runtime_session_id(scope)
        token = set_current_runtime_session_id(runtime_session_id)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_current_runtime_session_id(token)


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

_MCP_TOOL_CALL_LOG_LINE = "MCP tool called (butler=%s module=%s tool=%s)"


@dataclass
class ModuleStartupStatus:
    """Per-module startup outcome tracked by the daemon."""

    status: str  # "active", "failed", "cascade_failed"
    phase: str | None = None  # "credentials", "config", "migration", "startup", "tools"
    error: str | None = None


_MODULE_ENABLED_KEY_PREFIX = "module::"
_MODULE_ENABLED_KEY_SUFFIX = "::enabled"


@dataclass
class ModuleRuntimeState:
    """Combined health and enabled state for a module at runtime."""

    health: Literal["active", "failed", "cascade_failed"]
    enabled: bool
    failure_phase: str | None = None
    failure_error: str | None = None


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
    if config.db_schema:
        flat["butler.db.schema"] = config.db_schema

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
    """Proxy around FastMCP that logs and span-wraps module tool handlers.

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
        module_runtime_states: dict[str, ModuleRuntimeState] | None = None,
    ) -> None:
        self._mcp = mcp
        self._butler_name = butler_name
        self._module_name = module_name or "unknown"
        self._declared_tool_names = declared_tool_names or set()
        self._filtered_tool_names = filtered_tool_names or set()
        self._registered_tool_names: set[str] = set()
        # Shared reference to the daemon's live runtime states dict.
        # Used for call-time module enabled/disabled gating.
        self._module_runtime_states: dict[str, ModuleRuntimeState] | None = module_runtime_states

    def _log_tool_call(self, tool_name: str) -> None:
        """Emit one info log per MCP tool invocation."""
        logger.info(
            _MCP_TOOL_CALL_LOG_LINE,
            self._butler_name,
            self._module_name,
            tool_name,
        )

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

            module_name_for_gate = self._module_name
            runtime_states_ref = self._module_runtime_states

            @functools.wraps(fn)
            async def instrumented(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                self._log_tool_call(resolved_tool_name)
                capture_input = {
                    k: kwargs.get(k)
                    for k in ("butler", "target_butler", "butler_name", "prompt", "context")
                    if k in kwargs
                }
                # Check module enabled state at call time to support live toggling.
                if runtime_states_ref is not None:
                    state = runtime_states_ref.get(module_name_for_gate)
                    if state is not None and not state.enabled:
                        disabled_result = {
                            "error": "module_disabled",
                            "module": module_name_for_gate,
                            "message": (
                                f"The {module_name_for_gate} module is disabled. "
                                "Enable it from the dashboard."
                            ),
                        }
                        capture_tool_call(
                            tool_name=resolved_tool_name,
                            module_name=self._module_name,
                            input_payload=capture_input,
                            outcome="module_disabled",
                            result_payload=disabled_result,
                        )
                        return disabled_result

                try:
                    with tool_span(resolved_tool_name, butler_name=self._butler_name):
                        result = await fn(*args, **kwargs)
                except Exception as exc:
                    capture_tool_call(
                        tool_name=resolved_tool_name,
                        module_name=self._module_name,
                        input_payload=capture_input,
                        outcome="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise

                capture_tool_call(
                    tool_name=resolved_tool_name,
                    module_name=self._module_name,
                    input_payload=capture_input,
                    outcome="success",
                    result_payload=result,
                )
                return result

            return original_decorator(instrumented)

        return wrapper

    def missing_declared_tool_names(self) -> set[str]:
        """Return declared tool names that were never registered."""
        if not self._declared_tool_names:
            return set()
        return self._declared_tool_names - self._registered_tool_names

    def __getattr__(self, name: str) -> Any:
        return getattr(self._mcp, name)


class _ToolCallLoggingMCP:
    """Proxy around FastMCP that logs every registered tool invocation."""

    def __init__(
        self,
        mcp: FastMCP,
        butler_name: str,
        *,
        module_name: str,
    ) -> None:
        self._mcp = mcp
        self._butler_name = butler_name
        self._module_name = module_name

    def _log_tool_call(self, tool_name: str) -> None:
        logger.info(
            _MCP_TOOL_CALL_LOG_LINE,
            self._butler_name,
            self._module_name,
            tool_name,
        )

    def tool(self, *args, **kwargs):
        """Return a decorator that logs each call into a registered tool."""
        declared_name = kwargs.get("name")
        original_decorator = self._mcp.tool(*args, **kwargs)

        def wrapper(fn):  # noqa: ANN001, ANN202
            resolved_tool_name = declared_name or fn.__name__

            @functools.wraps(fn)
            async def instrumented(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                self._log_tool_call(resolved_tool_name)
                capture_input = {
                    k: kwargs.get(k)
                    for k in ("butler", "target_butler", "butler_name", "prompt", "context")
                    if k in kwargs
                }
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    capture_tool_call(
                        tool_name=resolved_tool_name,
                        module_name=self._module_name,
                        input_payload=capture_input,
                        outcome="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise
                capture_tool_call(
                    tool_name=resolved_tool_name,
                    module_name=self._module_name,
                    input_payload=capture_input,
                    outcome="success",
                    result_payload=result,
                )
                return result

            return original_decorator(instrumented)

        return wrapper

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
        self._module_runtime_states: dict[str, ModuleRuntimeState] = {}
        self._module_configs: dict[str, Any] = {}
        self._gated_tool_originals: dict[str, Any] = {}
        # Maps registered tool name → module name for gating and introspection.
        self._tool_module_map: dict[str, str] = {}
        self._started_at: float | None = None
        self._accepting_connections = False
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._switchboard_heartbeat_task: asyncio.Task | None = None
        self._scheduler_loop_task: asyncio.Task | None = None
        self._liveness_reporter_task: asyncio.Task | None = None
        self.switchboard_client: MCPClient | None = None
        self._pipeline: MessagePipeline | None = None
        self._buffer: Any = None  # DurableBuffer instance (switchboard only)
        self._audit_db: Database | None = None  # Switchboard DB for daemon audit logging
        self._shared_credentials_db: Database | None = None
        self._credential_store: CredentialStore | None = None
        self.blob_store: LocalBlobStore | None = None
        # Background tasks spawned by route.execute accept phase (non-messenger butlers)
        self._route_inbox_tasks: set[asyncio.Task] = set()

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

            selected.append(mod)

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
        """
        for mod in self._modules:
            startup = self._module_statuses.get(mod.name)
            health = startup.status if startup else "active"
            is_unavailable = health in ("failed", "cascade_failed")

            # Look up sticky state from previous runs
            key = f"{_MODULE_ENABLED_KEY_PREFIX}{mod.name}{_MODULE_ENABLED_KEY_SUFFIX}"
            stored_value = await _state_get(pool, key)

            if is_unavailable:
                # Failed modules are always disabled; persist that to store
                enabled = False
                await _state_set(pool, key, False)
            elif stored_value is None:
                # First boot — healthy modules start enabled
                enabled = True
                await _state_set(pool, key, True)
            else:
                # Honor the sticky toggle from a previous run
                enabled = bool(stored_value)

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
        await _state_set(pool, key, enabled)
        logger.info("Module %r enabled=%s (persisted to state store)", name, enabled)
        return True

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

        log_root = resolve_log_root(self.config.logging.log_root)
        configure_logging(
            level=self.config.logging.level,
            fmt=self.config.logging.format,
            log_root=log_root,
            butler_name=self.config.name,
        )
        logger.info("Loaded config for butler: %s", self.config.name)

        # 1c. Initialize blob storage
        blob_storage_path = Path(self.config.blob_storage_dir)
        self.blob_store = LocalBlobStore(blob_storage_path)
        logger.info("Initialized blob storage at: %s", blob_storage_path)

        # 2. Initialize telemetry and metrics
        init_telemetry(f"butler.{self.config.name}")
        init_metrics(f"butler.{self.config.name}")

        # 2.5. Detect inline secrets in config
        config_values = _flatten_config_for_secret_scan(self.config)
        secret_warnings = detect_secrets(config_values)
        for warning in secret_warnings:
            logger.warning(warning)

        # 3. Initialize modules (topological order). The registry instantiates
        # every built-in module, then startup filters out modules that require
        # explicit config but are omitted from [modules.*].
        self._modules = self._select_startup_modules(self._registry.load_all(self.config.modules))

        # 4. Validate module config schemas (non-fatal per-module).
        self._module_configs = self._validate_module_configs()

        # 5. Validate butler.env credentials (env-only fast-fail for non-secret config).
        # Core secrets (ANTHROPIC_API_KEY) and module credentials are validated later
        # (steps 8b/8c) after the DB pool is available, so DB-stored secrets are visible.
        module_creds = self._collect_module_credentials()
        validate_credentials(
            self.config.env_required,
            self.config.env_optional,
        )

        # 6. Provision database
        # If db was injected (e.g., for testing), skip provisioning
        if self.db is None:
            self.db = Database.from_env(self.config.db_name)
            self.db.set_schema(self.config.db_schema)
            await self.db.provision()
            pool = await self.db.connect()
        else:
            # Database already provisioned and connected externally
            pool = self.db.pool
            if pool is None:
                raise RuntimeError("Injected Database must already be connected")

        # 7. Run core Alembic migrations
        db_url = self._build_db_url()
        migration_schema = self.config.db_schema or None
        await run_migrations(db_url, chain="core", schema=migration_schema)

        # 7b. Run butler-specific Alembic migrations (if chain exists)
        if has_butler_chain(self.config.name):
            logger.info("Running butler-specific migrations for: %s", self.config.name)
            await run_migrations(db_url, chain=self.config.name, schema=migration_schema)

        # 8. Run module Alembic migrations (non-fatal per-module)
        for mod in self._modules:
            if mod.name in self._module_statuses:
                continue
            rev = mod.migration_revisions()
            if rev:
                try:
                    await run_migrations(db_url, chain=rev, schema=migration_schema)
                except Exception as exc:
                    error_msg = str(exc)
                    self._module_statuses[mod.name] = ModuleStartupStatus(
                        status="failed", phase="migration", error=error_msg
                    )
                    logger.warning(
                        "Module '%s' disabled: migration failed: %s", mod.name, error_msg
                    )
        self._cascade_module_failures()

        # 8b. Create layered CredentialStore and validate module credentials
        # (non-fatal per-module).
        # DB pool is now available so DB-stored credentials are visible to resolve().
        # Only validate credentials for modules that haven't already failed (e.g. from
        # migration errors), to avoid redundant DB queries and overwriting earlier failure
        # statuses with spurious credential failures.
        credential_store = await self._build_credential_store(pool)
        self._credential_store = credential_store
        active_module_creds_for_validation = {
            k: v for k, v in module_creds.items() if k.split(".")[0] not in self._module_statuses
        }
        module_cred_failures = await validate_module_credentials_async(
            active_module_creds_for_validation, credential_store
        )
        for mod_key, missing_vars in module_cred_failures.items():
            # mod_key may be "modname" or "modname.scope" — map to root module.
            root_mod = mod_key.split(".")[0]
            error_msg = f"Missing credential(s): {', '.join(missing_vars)}"
            self._module_statuses[root_mod] = ModuleStartupStatus(
                status="failed", phase="credentials", error=error_msg
            )
            logger.warning("Module '%s' disabled: %s", root_mod, error_msg)
        self._cascade_module_failures()

        # 8c. Validate core credentials via DB-first resolution (runtime-aware).
        # Only credentials required by the configured runtime are checked.
        await validate_core_credentials_async(credential_store, self.config.runtime.type)

        # Filter module_creds to exclude failed modules for spawner.
        active_module_creds = {
            k: v
            for k, v in module_creds.items()
            if k.split(".")[0] not in self._module_statuses
            or self._module_statuses[k.split(".")[0]].status == "active"
        }

        # 9. Call module on_startup (non-fatal per-module)
        started_modules: list[Module] = []
        for mod in self._modules:
            if mod.name in self._module_statuses:
                continue
            try:
                validated_config = self._module_configs.get(mod.name)
                await mod.on_startup(validated_config, self.db, credential_store)
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
            credential_store=credential_store,
        )

        # 10b. Wire message classification pipeline for switchboard modules
        self._wire_pipelines(pool)

        # 11. Sync TOML schedules to DB
        schedules = [
            {
                "name": s.name,
                "cron": s.cron,
                "dispatch_mode": s.dispatch_mode.value,
                "prompt": s.prompt,
                "job_name": s.job_name,
                "job_args": s.job_args,
            }
            for s in self.config.schedules
        ]
        await sync_schedules(pool, schedules, stagger_key=self.config.name)

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

        # 13d. Initialize module runtime states (enabled/disabled) from state store
        await self._init_module_runtime_states(pool)

        # 14. Start FastMCP SSE server on configured port
        await self._start_mcp_server()

        # 14b. Start durable buffer workers and scanner (switchboard only)
        if self._buffer is not None:
            await self._buffer.start()

        # 14c. Recover unprocessed route_inbox rows (non-switchboard, non-messenger butlers)
        # Rows that were accepted but never processed due to a crash are re-dispatched here.
        if self.config.name not in ("switchboard", "messenger") and self.spawner is not None:
            await self._recover_route_inbox(pool)

        # 15. Launch switchboard heartbeat (non-switchboard butlers only)
        if self.config.switchboard_url is not None:
            self._switchboard_heartbeat_task = asyncio.create_task(
                self._switchboard_heartbeat_loop()
            )

        # 16. Start internal scheduler loop
        self._scheduler_loop_task = asyncio.create_task(self._scheduler_loop())

        # 17. Start liveness reporter (non-switchboard butlers only)
        if self.config.name != "switchboard":
            self._liveness_reporter_task = asyncio.create_task(self._liveness_reporter_loop())

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

        Also creates and starts the DurableBuffer that replaces the unbounded
        asyncio.create_task() dispatch with a bounded in-memory queue.
        """
        if self.config.name != "switchboard":
            return
        if self.spawner is None:
            return

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=self.spawner.trigger,
            source_butler="switchboard",
            enable_ingress_dedupe=True,
        )
        self._pipeline = pipeline

        # Build the process function that wraps pipeline.process()
        async def _buffer_process(ref: Any) -> None:
            from butlers.core.buffer import _MessageRef

            if not isinstance(ref, _MessageRef):
                return
            channel = ref.source.get("channel", "unknown")
            endpoint_identity = ref.source.get("endpoint_identity", "unknown")
            request_context = {
                "request_id": ref.request_id,
                "received_at": ref.event.get("observed_at", ""),
                "source_channel": channel,
                "source_endpoint_identity": f"{channel}:{endpoint_identity}",
                "source_sender_identity": ref.sender.get("identity", "unknown"),
                "source_thread_identity": ref.event.get("external_thread_id"),
                "trace_context": {},
            }
            try:
                await pipeline.process(
                    message_text=ref.message_text,
                    tool_name="bot_switchboard_handle_message",
                    tool_args={
                        "source": channel,
                        "source_channel": channel,
                        "source_identity": endpoint_identity,
                        "source_endpoint_identity": f"{channel}:{endpoint_identity}",
                        "sender_identity": ref.sender.get("identity", "unknown"),
                        "external_event_id": ref.event.get("external_event_id", ""),
                        "external_thread_id": ref.event.get("external_thread_id"),
                        "source_tool": "ingest",
                        "request_id": ref.request_id,
                        "request_context": request_context,
                    },
                    message_inbox_id=ref.message_inbox_id,
                )
            except Exception:
                logger.exception(
                    "DurableBuffer: pipeline processing failed for request_id=%s",
                    ref.request_id,
                )

        # Create and start the durable buffer
        from butlers.core.buffer import DurableBuffer

        self._buffer = DurableBuffer(
            config=self.config.buffer,
            pool=pool,
            process_fn=_buffer_process,
        )

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

    async def _recover_route_inbox(self, pool: asyncpg.Pool) -> None:
        """Re-dispatch route_inbox rows that were accepted but never processed.

        Called on startup to recover from crashes or restarts.  Rows in
        'accepted' state older than the grace period are re-dispatched
        as background tasks through the same path as the hot path.
        """
        if self.spawner is None:
            return

        spawner = self.spawner  # capture for closures

        async def _dispatch_recovered(
            *,
            row_id: uuid.UUID,
            route_envelope: dict,
        ) -> None:
            """Dispatch one recovered route_inbox row as a background task.

            Recovery tasks always start a fresh root span — there is no live accept-phase
            span to link to (the original request may have come from a previous daemon
            run).  The request_id attribute allows cross-trace correlation via logs.
            """
            import json as _json

            from butlers.tools.switchboard.routing.contracts import parse_route_envelope

            try:
                parsed = parse_route_envelope(route_envelope)
            except Exception as exc:
                logger.warning(
                    "route_inbox recovery: invalid envelope for id=%s, skipping: %s",
                    row_id,
                    exc,
                )
                await route_inbox_mark_errored(
                    pool,
                    row_id,
                    f"Invalid envelope on recovery: {exc}",
                )
                return

            route_context = parsed.request_context.model_dump(mode="json")
            route_request_id = str(parsed.request_context.request_id)

            # Rebuild context text
            context_parts: list[str] = []
            request_ctx_json = _json.dumps(route_context, ensure_ascii=False, indent=2)
            context_parts.append(
                f"REQUEST CONTEXT (for reply targeting and audit traceability):\n{request_ctx_json}"
            )
            _INTERACTIVE_CHANNELS = frozenset({"telegram", "whatsapp"})
            if parsed.request_context.source_channel in _INTERACTIVE_CHANNELS:
                source_channel = parsed.request_context.source_channel
                context_parts.append(
                    "INTERACTIVE DATA SOURCE:\n"
                    f"This message originated from an interactive channel ({source_channel}). "
                    "The user expects a reply through the same channel. \n\n"
                    "IMPORTANT: You MUST use the notify() tool on your MCP to send your response:\n"
                    f'- channel="{source_channel}"\n'
                    '- intent="reply" for contextual responses\n'
                    '- intent="react" with emoji for quick acknowledgments (telegram only)\n'
                    "- Pass the request_context from above as the request_context parameter\n"
                    "- reply/react request_context requires: request_id, source_channel, "
                    "source_endpoint_identity, source_sender_identity\n"
                    "- telegram reply/react additionally requires: source_thread_identity"
                )
            if parsed.input.conversation_history:
                context_parts.append(
                    f"\nCONVERSATION HISTORY:\n{parsed.input.conversation_history}"
                )
            if isinstance(parsed.input.context, dict):
                input_ctx_json = _json.dumps(parsed.input.context, ensure_ascii=False, indent=2)
                context_parts.append(f"\nINPUT CONTEXT:\n{input_ctx_json}")
            elif isinstance(parsed.input.context, str):
                context_parts.append(f"\nINPUT CONTEXT:\n{parsed.input.context}")

            context_text = "\n".join(context_parts) if context_parts else None

            _tracer = trace.get_tracer("butlers")
            # Fresh root span for recovery — no accept-phase span to link to.
            with _tracer.start_as_current_span(
                "route.process.recovery",
                context=OtelContext(),
            ) as _recovery_span:
                tag_butler_span(_recovery_span, self.config.name)
                _recovery_span.set_attribute("request_id", route_request_id)
                await route_inbox_mark_processing(pool, row_id)
                try:
                    result = await spawner.trigger(
                        prompt=parsed.input.prompt,
                        context=context_text,
                        trigger_source="route",
                        request_id=route_request_id,
                    )
                    await route_inbox_mark_processed(pool, row_id, result.session_id)
                except Exception as exc:
                    error_msg = f"{type(exc).__name__}: {exc}"
                    logger.exception("route_inbox recovery: trigger failed for id=%s", row_id)
                    _recovery_span.set_status(trace.StatusCode.ERROR, error_msg)
                    await route_inbox_mark_errored(pool, row_id, error_msg)

        try:
            recovered = await route_inbox_recovery_sweep(
                pool,
                dispatch_fn=_dispatch_recovered,
            )
            if recovered:
                logger.info(
                    "Butler %s: recovered %d unprocessed route_inbox row(s) on startup",
                    self.config.name,
                    recovered,
                )
        except Exception:
            logger.exception(
                "Butler %s: route_inbox recovery sweep failed on startup",
                self.config.name,
            )

    async def _start_mcp_server(self) -> None:
        """Start the FastMCP SSE server as a background asyncio task.

        Creates a uvicorn server bound to the configured port and launches it
        in a background task so that ``start()`` returns immediately.
        """
        app = self._build_mcp_http_app(self.mcp, butler_name=self.config.name)
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.config.port,
            log_level="warning",
            timeout_graceful_shutdown=0,
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

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

        guarded_app = _McpRuntimeSessionGuard(streamable_app)
        return _McpSseDisconnectGuard(guarded_app, butler_name=butler_name)

    async def _create_audit_pool(self, own_pool: asyncpg.Pool) -> asyncpg.Pool | None:
        """Create or reuse a connection pool for daemon-side audit logging.

        The switchboard butler reuses its own pool. Other butlers open a small
        dedicated pool to the switchboard DB context.

        Returns ``None`` (with a warning) if the pool cannot be created.
        """
        if self.config.name == "switchboard":
            return own_pool

        try:
            audit_db_name = self.config.db_name if self.config.db_schema else "butler_switchboard"
            audit_db_schema = "switchboard" if self.config.db_schema else None
            audit_db = Database.from_env(audit_db_name)
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

    async def _resolve_default_notify_recipient(
        self, *, channel: str, intent: str, recipient: str | None
    ) -> str | None:
        """Resolve notify recipient, including schedule-safe Telegram default chat mapping."""
        resolved_recipient = recipient.strip() if isinstance(recipient, str) else None
        if resolved_recipient:
            return resolved_recipient

        if channel != "telegram" or intent != "send":
            return None

        credential_store = self._credential_store
        if credential_store is None:
            return None

        configured_chat_id = await credential_store.resolve(
            _DEFAULT_TELEGRAM_CHAT_SECRET,
            env_fallback=False,
        )
        if not isinstance(configured_chat_id, str):
            return None

        normalized_chat_id = configured_chat_id.strip()
        return normalized_chat_id or None

    async def _dispatch_scheduled_task(
        self,
        *,
        trigger_source: str,
        prompt: str | None = None,
        job_name: str | None = None,
        job_args: dict[str, Any] | None = None,
    ) -> Any:
        """Dispatch one scheduled task via deterministic jobs or prompt fallback.

        Deterministic schedules are resolved through an explicit per-butler
        job registry. Prompt-mode schedules fall back to runtime/LLM dispatch.
        """
        resolved_job_name = _resolve_deterministic_schedule_job_name(
            butler_name=self.config.name,
            trigger_source=trigger_source,
            job_name=job_name,
        )
        if resolved_job_name is not None:
            pool = self.db.pool if self.db is not None else None
            if pool is None:
                raise RuntimeError(
                    "Deterministic scheduler dispatch requires an initialized DB pool "
                    f"(butler={self.config.name!r}, job_name={resolved_job_name!r})"
                )

            jobs_for_butler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(self.config.name, {})
            handler = jobs_for_butler.get(resolved_job_name)
            if handler is None:
                registered_jobs = ", ".join(sorted(jobs_for_butler)) or "<none>"
                raise RuntimeError(
                    "Unknown deterministic scheduler job "
                    f"(butler={self.config.name!r}, job_name={resolved_job_name!r}). "
                    f"Registered jobs: {registered_jobs}. "
                    "Use prompt dispatch mode for LLM-backed schedules."
                )

            logger.debug(
                "Dispatching deterministic scheduled task "
                "(butler=%s, job_name=%s, trigger_source=%s, job_args=%s)",
                self.config.name,
                resolved_job_name,
                trigger_source,
                job_args,
            )
            return await handler(pool, job_args)

        if self.spawner is None:
            raise RuntimeError("Scheduler dispatch requires an initialized spawner")
        if prompt is None or not prompt.strip():
            raise RuntimeError("Prompt-mode scheduler dispatch requires a non-empty prompt payload")
        return await self.spawner.trigger(prompt=prompt, trigger_source=trigger_source)

    async def _scheduler_loop(self) -> None:
        """Periodically call tick() to dispatch due scheduled tasks.

        Runs as a background task for the lifetime of the butler.  Sleeps for
        ``tick_interval_seconds`` (from ``[butler.scheduler]`` config, default 60),
        then calls ``tick()`` to evaluate and dispatch any due cron tasks.

        Exceptions from ``tick()`` are logged and the loop continues — a single
        tick failure never breaks the loop.

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

        pool = self.db.pool
        dispatch_fn = self._dispatch_scheduled_task
        interval = self.config.scheduler.tick_interval_seconds

        logger.info(
            "Scheduler loop started (tick_interval_seconds=%d) for butler %s",
            interval,
            self.config.name,
        )

        try:
            while True:
                await asyncio.sleep(interval)
                tick_task = asyncio.create_task(
                    _tick(pool, dispatch_fn, stagger_key=self.config.name)
                )
                try:
                    dispatched = await asyncio.shield(tick_task)
                    logger.debug(
                        "Scheduler loop: tick() dispatched %d task(s) for butler %s",
                        dispatched,
                        self.config.name,
                    )
                except asyncio.CancelledError:
                    # Cancellation arrived while tick() was running; let it finish.
                    logger.debug(
                        "Scheduler loop: cancelled during tick(), waiting for tick to finish"
                    )
                    try:
                        await tick_task
                    except Exception:
                        logger.exception(
                            "Scheduler loop: in-progress tick() raised on cancellation "
                            "for butler %s",
                            self.config.name,
                        )
                    raise
                except Exception:
                    logger.exception(
                        "Scheduler loop: tick() raised an exception for butler %s; continuing",
                        self.config.name,
                    )
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled for butler %s", self.config.name)

    async def _liveness_reporter_loop(self) -> None:
        """Periodically POST to the Switchboard's heartbeat endpoint to signal liveness.

        Runs as a background task for the lifetime of the butler (non-switchboard only).
        Sends an initial heartbeat within 5 seconds of startup, then repeats every
        ``heartbeat_interval_seconds`` (from ``[butler.scheduler]`` config, default 120).

        Connection failures are logged at WARNING level — transient unavailability is
        expected (e.g., Switchboard not yet started) and does not break the loop.

        The Switchboard URL is resolved from the ``BUTLERS_SWITCHBOARD_URL`` environment
        variable (default ``http://localhost:40200``), or from
        ``[butler.scheduler].switchboard_url`` in butler.toml.

        On cancellation (graceful shutdown), the loop exits cleanly.
        """
        butler_name = self.config.name
        url = f"{self.config.scheduler.switchboard_url}/api/switchboard/heartbeat"
        interval = self.config.scheduler.heartbeat_interval_seconds

        logger.info(
            "Liveness reporter started (heartbeat_interval_seconds=%d, url=%s) for butler %s",
            interval,
            url,
            butler_name,
        )

        payload = {"butler_name": butler_name}

        async def _post_heartbeat(phase: str) -> bool:
            """POST one heartbeat and return whether loop should continue.

            A persistent 404 means the target service does not expose the
            Switchboard heartbeat endpoint (wrong host/port/path). In that
            case we stop retrying to avoid noisy, unproductive log spam.
            """
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 404:
                    logger.warning(
                        "Liveness reporter: %s heartbeat endpoint not found (404) "
                        "for butler %s at %s; disabling reporter",
                        phase,
                        butler_name,
                        url,
                    )
                    return False
                resp.raise_for_status()
                logger.debug(
                    "Liveness reporter: %s heartbeat sent for butler %s (status %d)",
                    phase,
                    butler_name,
                    resp.status_code,
                )
                return True
            except Exception:
                logger.warning(
                    "Liveness reporter: %s heartbeat failed for butler %s",
                    phase,
                    butler_name,
                    exc_info=True,
                )
                return True

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                # Send initial heartbeat within 5 seconds of startup
                await asyncio.sleep(5)
                if not await _post_heartbeat("initial"):
                    return

                while True:
                    await asyncio.sleep(interval)
                    if not await _post_heartbeat("periodic"):
                        return
            except asyncio.CancelledError:
                logger.info("Liveness reporter cancelled for butler %s", butler_name)

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

        Every tool handler is wrapped with a ``tool_span`` that creates a
        ``butler.tool.<name>`` span with a ``butler.name`` attribute.
        """
        pool = self.db.pool
        spawner = self.spawner
        daemon = self
        butler_name = self.config.name
        mcp = _ToolCallLoggingMCP(self.mcp, butler_name, module_name="core")
        _route_metrics = ButlerMetrics(butler_name=butler_name)

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
                tag_butler_span(_span, butler_name)
                # Capture the accept-phase span context so the background processing
                # task can link back to it via a SpanLink (cross-trace correlation).
                accept_span_ctx = _span.get_span_context()
                return await _route_execute_inner(
                    schema_version=schema_version,
                    request_context=request_context,
                    input=input,
                    subrequest=subrequest,
                    target=target,
                    source_metadata=source_metadata,
                    trace_context=trace_context,
                    accept_span_ctx=accept_span_ctx,
                )

        async def _route_execute_inner(
            schema_version: str,
            request_context: dict[str, Any],
            input: dict[str, Any],
            subrequest: dict[str, Any] | None = None,
            target: dict[str, Any] | None = None,
            source_metadata: dict[str, Any] | None = None,
            trace_context: dict[str, str] | None = None,
            accept_span_ctx: trace.SpanContext | None = None,
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

            # Annotate the accept-phase span with request_id for cross-trace correlation.
            # Both the accept span and the process span carry this attribute so operators
            # can join the two sibling traces by request_id in their observability backend.
            _current_accept_span = trace.get_current_span()
            if _current_accept_span.is_recording():
                _current_accept_span.set_attribute("request_id", route_request_id)

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
                # --- Accept phase (<50ms): persist to route_inbox, return immediately ---
                #
                # The switchboard must not block waiting for the full LLM session.
                # We persist the envelope to route_inbox (durable), fire a background
                # asyncio task for processing, and return {"status": "accepted"} now.
                # See docs/architecture/concurrency.md Section 4.4.

                pool = daemon.db.pool if daemon.db is not None else None
                if pool is None:
                    return _route_error_response(
                        context_payload=route_context,
                        error_class="internal_error",
                        message="route.execute: database pool is not available",
                    )

                accept_started_at = time.monotonic()
                try:
                    inbox_id = await route_inbox_insert(pool, route_envelope=route_payload)
                except Exception as exc:
                    logger.warning(
                        "route.execute: route_inbox_insert failed: %s: %s",
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    return _route_error_response(
                        context_payload=route_context,
                        error_class="internal_error",
                        message=f"route.execute: failed to persist to route_inbox: {exc}",
                    )
                inbox_accepted_at = datetime.now(UTC)

                # --- Process phase (asynchronous): build context and call spawner ---

                # Build runtime context text (same as before, but in background)
                context_parts: list[str] = []

                # Add request_context header for runtime session
                request_ctx_json = json.dumps(route_context, ensure_ascii=False, indent=2)
                context_parts.append(
                    "REQUEST CONTEXT (for reply targeting and audit traceability):"
                    f"\n{request_ctx_json}"
                )

                # Inject interactive guidance when source is user-facing
                _INTERACTIVE_CHANNELS = frozenset({"telegram", "whatsapp"})
                source_channel = parsed_route.request_context.source_channel
                if source_channel in _INTERACTIVE_CHANNELS:
                    context_parts.append(
                        "INTERACTIVE DATA SOURCE:\n"
                        f"This message originated from an interactive channel ({source_channel}). "
                        "The user expects a reply through the same channel. "
                        "IMPORTANT: You MUST use the notify() tool on your MCP to send "
                        "your response:\n"
                        f'- channel="{source_channel}"\n'
                        '- intent="reply" for contextual responses\n'
                        '- intent="react" with emoji for quick acknowledgments (telegram only)\n'
                        "- Pass the request_context from above as the request_context parameter\n"
                        "- reply/react request_context requires: request_id, source_channel, "
                        "source_endpoint_identity, source_sender_identity\n"
                        "- telegram reply/react additionally requires: source_thread_identity"
                    )

                # Add conversation history if forwarded from switchboard
                if parsed_route.input.conversation_history:
                    context_parts.append(
                        f"\nCONVERSATION HISTORY:\n{parsed_route.input.conversation_history}"
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
                prompt_text = parsed_route.input.prompt

                async def _process_route(
                    _inbox_id: uuid.UUID,
                    _pool: asyncpg.Pool,
                    _spawner: Spawner,
                    _prompt: str,
                    _context: str | None,
                    _request_id: str,
                    _accepted_at: datetime,
                    _accept_span_ctx: trace.SpanContext | None,
                ) -> None:
                    """Background task: call spawner.trigger() and update route_inbox.

                    This task runs as a sibling trace (fresh root span) — it does NOT
                    inherit the switchboard's OTel context.  A SpanLink back to the
                    accept-phase span enables cross-trace correlation via request_id.
                    """
                    # Record how long the request waited in the route_inbox queue
                    process_latency_ms = (datetime.now(UTC) - _accepted_at).total_seconds() * 1000
                    _route_metrics.record_route_process_latency(process_latency_ms)

                    _tracer = trace.get_tracer("butlers")
                    # Build SpanLink to the accept-phase span for cross-trace correlation.
                    _links: list[OtelLink] = []
                    if _accept_span_ctx is not None and _accept_span_ctx.is_valid:
                        _links.append(
                            OtelLink(
                                context=_accept_span_ctx,
                                attributes={"request_id": _request_id},
                            )
                        )
                    # Start a fresh root span — do NOT inherit the switchboard's context.
                    with _tracer.start_as_current_span(
                        "route.process",
                        context=OtelContext(),
                        links=_links,
                    ) as _process_span:
                        tag_butler_span(_process_span, butler_name)
                        _process_span.set_attribute("request_id", _request_id)
                        try:
                            await route_inbox_mark_processing(_pool, _inbox_id)
                            # Decrement after the DB mark so the gauge stays accurate if
                            # mark_processing fails (row would still be in accepted state).
                            _route_metrics.route_queue_depth_dec()
                            result = await _spawner.trigger(
                                prompt=_prompt,
                                context=_context,
                                # Use 'route' as trigger_source to bypass the self-trigger
                                # rejection guard (trigger_source=="trigger" deadlock check).
                                trigger_source="route",
                                request_id=_request_id,
                            )
                            await route_inbox_mark_processed(_pool, _inbox_id, result.session_id)
                        except Exception as exc:
                            error_msg = f"{type(exc).__name__}: {exc}"
                            logger.exception(
                                "route_inbox: background processing failed for id=%s request_id=%s",
                                _inbox_id,
                                _request_id,
                            )
                            _process_span.set_status(trace.StatusCode.ERROR, error_msg)
                            await route_inbox_mark_errored(_pool, _inbox_id, error_msg)

                # Record accept-phase metrics: latency and queue depth
                _route_metrics.record_route_accept_latency(
                    (time.monotonic() - accept_started_at) * 1000
                )
                _route_metrics.route_queue_depth_inc()

                task = asyncio.create_task(
                    _process_route(
                        inbox_id,
                        pool,
                        spawner,
                        prompt_text,
                        context_text,
                        route_request_id,
                        inbox_accepted_at,
                        accept_span_ctx,
                    ),
                    name=f"route-inbox-{inbox_id}",
                )
                # Track so shutdown can drain these tasks
                daemon._route_inbox_tasks.add(task)
                task.add_done_callback(daemon._route_inbox_tasks.discard)

                # Return accepted immediately — switchboard no longer waits
                return {
                    "schema_version": "route_response.v1",
                    "request_context": route_context,
                    "status": "accepted",
                    "inbox_id": str(inbox_id),
                    "timing": {"duration_ms": _elapsed_ms()},
                }

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
            import importlib.util as _ilu

            from butlers.tools.switchboard.backfill.connector import (
                backfill_poll as _backfill_poll,
            )
            from butlers.tools.switchboard.backfill.connector import (
                backfill_progress as _backfill_progress,
            )
            from butlers.tools.switchboard.ingestion.ingest import ingest_v1
            from butlers.tools.switchboard.notification.deliver import (
                deliver as _switchboard_deliver,
            )
            from butlers.tools.switchboard.routing.route import (
                route as _switchboard_route,
            )

            _hb_path = (
                Path(__file__).resolve().parents[2]
                / "roster"
                / "switchboard"
                / "tools"
                / "connector"
                / "heartbeat.py"
            )
            _hb_spec = _ilu.spec_from_file_location("roster_switchboard_heartbeat", _hb_path)
            assert _hb_spec is not None and _hb_spec.loader is not None
            _hb_mod = _ilu.module_from_spec(_hb_spec)
            _hb_spec.loader.exec_module(_hb_mod)
            _connector_heartbeat = _hb_mod.heartbeat

            pipeline = daemon._pipeline
            # DurableBuffer instance created by _wire_pipelines (may be None if
            # pipeline wiring was skipped, e.g. in tests).
            buffer = daemon._buffer

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

                # Route accepted message via durable buffer (bounded queue)
                # or fall back to direct create_task if buffer is unavailable.
                if not result.duplicate and pipeline is not None:
                    normalized_text = payload.get("normalized_text", "")
                    if normalized_text:
                        if buffer is not None:
                            buffer.enqueue(
                                request_id=str(result.request_id),
                                message_inbox_id=result.request_id,
                                message_text=normalized_text,
                                source=source,
                                event=event,
                                sender=sender,
                            )
                        else:
                            # Fallback: unbounded create_task (buffer not wired)
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

                Called by the runtime instance during message classification to
                directly route a sub-message to the target butler.

                Args:
                    butler: Name of the target butler (e.g. "health", "relationship").
                    prompt: Self-contained prompt for the target butler.
                    context: Optional additional context for the target butler.
                """
                _routing_ctx = _routing_ctx_var.get() or {}
                if not isinstance(_routing_ctx, dict):
                    _routing_ctx = {}
                runtime_routing_ctx = get_current_runtime_session_routing_context()
                if isinstance(runtime_routing_ctx, dict):
                    if not _routing_ctx:
                        _routing_ctx = dict(runtime_routing_ctx)
                    else:
                        for key in (
                            "source_metadata",
                            "request_context",
                            "request_id",
                            "conversation_history",
                        ):
                            if _routing_ctx.get(key) in (None, "", {}):
                                _routing_ctx[key] = runtime_routing_ctx.get(key)
                source_metadata = _routing_ctx.get("source_metadata", {})
                if not isinstance(source_metadata, dict):
                    source_metadata = {}
                normalized_source_metadata: dict[str, Any] = {
                    "channel": str(source_metadata.get("channel", "mcp")),
                    "identity": str(source_metadata.get("identity", "unknown")),
                    "tool_name": str(source_metadata.get("tool_name", "route_to_butler")),
                }
                if source_metadata.get("source_id") not in (None, ""):
                    normalized_source_metadata["source_id"] = str(source_metadata["source_id"])
                request_context = _routing_ctx.get("request_context")
                if not isinstance(request_context, dict):
                    request_context = None
                raw_request_id = _routing_ctx.get("request_id")
                if raw_request_id in (None, "") and isinstance(request_context, dict):
                    raw_request_id = request_context.get("request_id")
                request_id = MessagePipeline._coerce_request_id(raw_request_id)
                conversation_history = _routing_ctx.get("conversation_history")
                source_channel = str(
                    request_context.get("source_channel")
                    if isinstance(request_context, dict)
                    and request_context.get("source_channel") not in (None, "")
                    else normalized_source_metadata["channel"]
                )
                source_sender_identity = str(
                    request_context.get("source_sender_identity")
                    if isinstance(request_context, dict)
                    and request_context.get("source_sender_identity") not in (None, "")
                    else normalized_source_metadata["identity"]
                )
                source_thread_identity = (
                    request_context.get("source_thread_identity")
                    if isinstance(request_context, dict)
                    else None
                )

                _input: dict[str, Any] = {"prompt": prompt, "context": context}
                if conversation_history:
                    _input["conversation_history"] = conversation_history

                envelope: dict[str, Any] = {
                    "schema_version": "route.v1",
                    "request_context": {
                        "request_id": request_id,
                        "received_at": datetime.now(UTC).isoformat(),
                        "source_channel": source_channel,
                        "source_endpoint_identity": "switchboard",
                        "source_sender_identity": source_sender_identity,
                        "source_thread_identity": source_thread_identity,
                        "trace_context": {},
                    },
                    "input": _input,
                    "target": {"butler": butler, "tool": "route.execute"},
                    "source_metadata": normalized_source_metadata,
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
                    # Pass through 'accepted' or 'error' status from the target butler so
                    # that telemetry and the runtime can see actual outcomes.
                    inner = result.get("result") if isinstance(result, dict) else None
                    if isinstance(inner, dict):
                        if inner.get("status") == "accepted":
                            return {"status": "accepted", "butler": butler}
                        if inner.get("status") == "error":
                            error_detail = inner.get("error", {})
                            error_msg = (
                                error_detail.get("message", str(error_detail))
                                if isinstance(error_detail, dict)
                                else str(error_detail)
                            )
                            logger.warning(
                                "route_to_butler: target %s returned error: %s",
                                butler,
                                error_msg,
                            )
                            return {
                                "status": "error",
                                "butler": butler,
                                "error": error_msg,
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
            @tool_span("deliver", butler_name=butler_name)
            async def deliver(
                source_butler: str = "switchboard",
                channel: str | None = None,
                message: str | None = None,
                recipient: str | None = None,
                metadata: dict[str, Any] | None = None,
                notify_request: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                """Deliver a notification through a channel (telegram, email).

                Accepts either a versioned notify.v1 envelope via notify_request,
                or legacy positional args (channel, message, recipient).
                """
                return await _switchboard_deliver(
                    pool,
                    channel=channel,
                    message=message,
                    recipient=recipient,
                    metadata=metadata,
                    source_butler=source_butler,
                    notify_request=notify_request,
                )

            @mcp.tool(name="connector.heartbeat")
            @tool_span("connector.heartbeat", butler_name=butler_name)
            async def connector_heartbeat(
                schema_version: str,
                connector: dict[str, Any],
                status: dict[str, Any],
                counters: dict[str, Any],
                checkpoint: dict[str, Any] | None = None,
                capabilities: dict[str, Any] | None = None,
                sent_at: str = "",
            ) -> dict[str, Any]:
                """Accept a connector heartbeat for liveness tracking and statistics."""
                payload = {
                    "schema_version": schema_version,
                    "connector": connector,
                    "status": status,
                    "counters": counters,
                    "sent_at": sent_at,
                }
                if checkpoint is not None:
                    payload["checkpoint"] = checkpoint
                if capabilities is not None:
                    payload["capabilities"] = capabilities
                result = await _connector_heartbeat(pool, payload)
                return result.model_dump()

            @mcp.tool(name="backfill.poll")
            @tool_span("backfill.poll", butler_name=butler_name)
            async def backfill_poll_tool(
                connector_type: str,
                endpoint_identity: str,
            ) -> dict[str, Any] | None:
                """Claim the next pending backfill job for a connector identity.

                Called by connector processes (e.g. Gmail connector) to atomically
                claim the oldest pending backfill job. Returns None when no pending
                job exists for this connector.

                Connectors MUST call this no more frequently than once every 60 seconds.

                Args:
                    connector_type: Canonical connector type (e.g. ``gmail``).
                    endpoint_identity: The account identity this connector serves.

                Returns:
                    Job payload with job_id, params, and cursor on success; None when
                    no pending job is available.
                """
                return await _backfill_poll(
                    pool,
                    connector_type=connector_type,
                    endpoint_identity=endpoint_identity,
                )

            @mcp.tool(name="backfill.progress")
            @tool_span("backfill.progress", butler_name=butler_name)
            async def backfill_progress_tool(
                job_id: str,
                connector_type: str,
                endpoint_identity: str,
                rows_processed: int,
                rows_skipped: int,
                cost_spent_cents_delta: int,
                cursor: dict[str, Any] | None = None,
                status: str | None = None,
                error: str | None = None,
            ) -> dict[str, Any]:
                """Report batch progress for an active backfill job.

                Called by connector processes to update cumulative counters, advance
                the resume cursor, and optionally mark the job as completed or errored.

                Connectors MUST stop processing when the returned status is anything
                other than ``active``.

                Args:
                    job_id: UUID of the job being reported on.
                    connector_type: Must match the job's connector_type.
                    endpoint_identity: Must match the job's endpoint_identity.
                    rows_processed: Rows processed in this batch (non-negative).
                    rows_skipped: Rows skipped in this batch (non-negative).
                    cost_spent_cents_delta: Additional cost in cents for this batch.
                    cursor: Optional updated resume cursor (opaque JSONB).
                    status: Optional terminal status (``completed`` or ``error``).
                    error: Optional error detail (accompany ``status="error"``).

                Returns:
                    ``{status: str}`` — the authoritative job status after this update.
                """
                return await _backfill_progress(
                    pool,
                    job_id=job_id,
                    connector_type=connector_type,
                    endpoint_identity=endpoint_identity,
                    rows_processed=rows_processed,
                    rows_skipped=rows_skipped,
                    cost_spent_cents_delta=cost_spent_cents_delta,
                    cursor=cursor,
                    status=status,
                    error=error,
                )

        @mcp.tool()
        async def tick() -> dict:
            """Evaluate due scheduled tasks and dispatch them now.

            Primarily driven by the internal scheduler loop. Retained as an MCP tool
            for debugging and manual triggering.
            """
            count = await _tick(
                pool,
                daemon._dispatch_scheduled_task,
                stagger_key=daemon.config.name,
            )
            return {"dispatched": count}

        # State tools
        @mcp.tool()
        async def state_get(key: str, _trace_context: dict | None = None) -> dict:
            """Get a value from the state store."""
            parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span("butler.tool.state_get", context=parent_ctx) as span:
                tag_butler_span(span, daemon.config.name)
                value = await _state_get(pool, key)
                return {"key": key, "value": value}

        @mcp.tool()
        async def state_set(key: str, value: Any, _trace_context: dict | None = None) -> dict:
            """Set a value in the state store."""
            parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span("butler.tool.state_set", context=parent_ctx) as span:
                tag_butler_span(span, daemon.config.name)
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
                tag_butler_span(span, daemon.config.name)
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
                tag_butler_span(span, daemon.config.name)
                return await _state_list(pool, prefix, keys_only)

        # Schedule tools
        @mcp.tool()
        async def schedule_list() -> list[dict]:
            """List all scheduled tasks."""
            tasks = await _schedule_list(pool)
            for t in tasks:
                t["id"] = str(t["id"])
                if t.get("calendar_event_id") is not None:
                    t["calendar_event_id"] = str(t["calendar_event_id"])
            return tasks

        @mcp.tool()
        async def schedule_create(
            name: str,
            cron: str,
            prompt: str | None = None,
            dispatch_mode: str = "prompt",
            job_name: str | None = None,
            job_args: dict[str, Any] | None = None,
            timezone: str | None = None,
            start_at: datetime | None = None,
            end_at: datetime | None = None,
            until_at: datetime | None = None,
            display_title: str | None = None,
            calendar_event_id: str | None = None,
        ) -> dict:
            """Create a new runtime scheduled task."""
            create_kwargs: dict[str, Any] = {
                "dispatch_mode": dispatch_mode,
                "job_name": job_name,
                "job_args": job_args,
                "stagger_key": daemon.config.name,
            }
            if timezone is not None:
                create_kwargs["timezone"] = timezone
            if start_at is not None:
                create_kwargs["start_at"] = start_at
            if end_at is not None:
                create_kwargs["end_at"] = end_at
            if until_at is not None:
                create_kwargs["until_at"] = until_at
            if display_title is not None:
                create_kwargs["display_title"] = display_title
            if calendar_event_id is not None:
                create_kwargs["calendar_event_id"] = calendar_event_id
            task_id = await _schedule_create(
                pool,
                name,
                cron,
                prompt,
                **create_kwargs,
            )
            return {
                "id": str(task_id),
                "status": "created",
                "dispatch_mode": dispatch_mode,
                "prompt": prompt,
                "job_name": job_name,
                "job_args": job_args,
                "timezone": timezone,
                "start_at": start_at.isoformat() if start_at else None,
                "end_at": end_at.isoformat() if end_at else None,
                "until_at": until_at.isoformat() if until_at else None,
                "display_title": display_title,
                "calendar_event_id": calendar_event_id,
            }

        @mcp.tool()
        async def remind(
            message: Annotated[
                str,
                Field(description="The reminder message to deliver."),
            ],
            channel: Annotated[
                Literal["telegram", "email"],
                Field(description="Delivery channel for the reminder."),
            ],
            delay_minutes: Annotated[
                int | None,
                Field(
                    description=(
                        "Minutes from now to deliver the reminder. "
                        "Mutually exclusive with remind_at."
                    )
                ),
            ] = None,
            remind_at: Annotated[
                datetime | None,
                Field(
                    description=(
                        "Absolute UTC datetime to deliver the reminder. "
                        "Mutually exclusive with delay_minutes."
                    )
                ),
            ] = None,
            request_context: Annotated[
                NotifyRequestContextInput | None,
                Field(
                    description=(
                        "Optional request context passed through to notify(). "
                        "Must be a dict/object — do NOT pass as a JSON string."
                    )
                ),
            ] = None,
        ) -> dict:
            """Set a one-shot reminder that delivers a message via notify().

            Exactly one of ``delay_minutes`` or ``remind_at`` must be provided.
            Internally creates a one-shot scheduled task that fires at the target
            time and calls ``notify()`` with the given message, channel, and
            optional request_context.
            """
            # --- validate inputs ---
            if delay_minutes is not None and remind_at is not None:
                return {
                    "status": "error",
                    "error": ("Provide exactly one of delay_minutes or remind_at, not both."),
                }
            if delay_minutes is None and remind_at is None:
                return {
                    "status": "error",
                    "error": ("Provide exactly one of delay_minutes or remind_at."),
                }
            if delay_minutes is not None and delay_minutes < 1:
                return {
                    "status": "error",
                    "error": "delay_minutes must be at least 1.",
                }

            # --- compute target time ---
            now = datetime.now(UTC)
            if delay_minutes is not None:
                target = now + timedelta(minutes=delay_minutes)
            else:
                if remind_at is None:
                    return {"status": "error", "error": "Internal error: remind_at is None."}
                # Ensure remind_at is timezone-aware (assume UTC if naive)
                if remind_at.tzinfo is None:
                    target = remind_at.replace(tzinfo=UTC)
                else:
                    target = remind_at
                if target <= now:
                    return {
                        "status": "error",
                        "error": "remind_at must be in the future.",
                    }

            # --- build cron expression for the target minute ---
            cron = f"{target.minute} {target.hour} {target.day} {target.month} *"

            # --- build prompt that calls notify() ---
            notify_args: dict[str, Any] = {
                "channel": channel,
                "message": message,
                "intent": "send",
            }
            if request_context is not None:
                notify_args["request_context"] = request_context

            prompt = (
                f"Deliver this reminder by calling the notify tool with "
                f"the following arguments: {json.dumps(notify_args)}"
            )

            # --- schedule a one-shot task ---
            until_at = target + timedelta(minutes=1)
            task_id = await _schedule_create(
                pool,
                f"remind-{target.strftime('%Y%m%dT%H%M')}-{str(uuid.uuid4())[:8]}",
                cron,
                prompt,
                stagger_key=daemon.config.name,
                until_at=until_at,
            )

            return {
                "id": str(task_id),
                "status": "scheduled",
                "remind_at": target.isoformat(),
                "channel": channel,
                "message": message,
            }

        def _resolve_schedule_tool_id(
            task_id: str | None,
            legacy_id: str | None,
            tool_name: str,
        ) -> str:
            """Accept both task_id and legacy id fields for MCP compatibility."""
            if task_id and legacy_id and task_id != legacy_id:
                raise ValueError(f"{tool_name} received both task_id and id with different values")
            resolved = task_id or legacy_id
            if resolved is None:
                raise ValueError(f"{tool_name} requires task_id or id")
            return resolved

        @mcp.tool()
        async def schedule_update(
            task_id: str | None = None,
            id: str | None = None,
            name: str | None = None,
            cron: str | None = None,
            dispatch_mode: str | None = None,
            prompt: str | None = None,
            job_name: str | None = None,
            job_args: dict[str, Any] | None = None,
            enabled: bool | None = None,
            timezone: str | None = None,
            start_at: datetime | None = None,
            end_at: datetime | None = None,
            until_at: datetime | None = None,
            display_title: str | None = None,
            calendar_event_id: str | None = None,
        ) -> dict:
            """Update a scheduled task. Only provided fields are changed."""
            resolved_id = _resolve_schedule_tool_id(task_id, id, "schedule_update")
            update_fields = {
                "name": name,
                "cron": cron,
                "dispatch_mode": dispatch_mode,
                "prompt": prompt,
                "job_name": job_name,
                "job_args": job_args,
                "enabled": enabled,
                "timezone": timezone,
                "start_at": start_at,
                "end_at": end_at,
                "until_at": until_at,
                "display_title": display_title,
                "calendar_event_id": calendar_event_id,
            }
            fields = {k: v for k, v in update_fields.items() if v is not None}
            await _schedule_update(
                pool,
                uuid.UUID(resolved_id),
                stagger_key=daemon.config.name,
                **fields,
            )
            return {
                "id": resolved_id,
                "status": "updated",
                "dispatch_mode": dispatch_mode,
                "prompt": prompt,
                "job_name": job_name,
                "job_args": job_args,
                "timezone": timezone,
                "start_at": start_at.isoformat() if start_at else None,
                "end_at": end_at.isoformat() if end_at else None,
                "until_at": until_at.isoformat() if until_at else None,
                "display_title": display_title,
                "calendar_event_id": calendar_event_id,
            }

        @mcp.tool()
        async def schedule_delete(task_id: str | None = None, id: str | None = None) -> dict:
            """Delete a runtime scheduled task."""
            resolved_id = _resolve_schedule_tool_id(task_id, id, "schedule_delete")
            await _schedule_delete(pool, uuid.UUID(resolved_id))
            return {"id": resolved_id, "status": "deleted"}

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
            channel: Annotated[
                Literal["telegram", "email"],
                Field(description="Delivery channel. Allowed values: telegram | email."),
            ],
            message: Annotated[
                str | None,
                Field(description="Message text. Required for send/reply intents."),
            ] = None,
            recipient: Annotated[
                str | None,
                Field(description="Optional explicit recipient identity (for example email)."),
            ] = None,
            subject: Annotated[
                str | None,
                Field(description="Optional subject line (email channel)."),
            ] = None,
            intent: Annotated[
                Literal["send", "reply", "react"],
                Field(description="Delivery intent. Allowed values: send | reply | react."),
            ] = "send",
            emoji: Annotated[
                str | None,
                Field(description="Required when intent=react."),
            ] = None,
            request_context: Annotated[
                NotifyRequestContextInput | None,
                Field(
                    description=(
                        "Context lineage for reply/react targeting. Must be a "
                        "dict/object — do NOT pass as a JSON string. Required keys "
                        "for reply/react: request_id, source_channel, "
                        "source_endpoint_identity, source_sender_identity. For "
                        "telegram reply/react include source_thread_identity."
                    )
                ),
            ] = None,
        ) -> dict:
            """Send a `notify.v1` envelope through Switchboard `deliver()`.

            Required fields:
            - `channel` (string enum): `telegram` or `email`
            - `message` (string): required for `send`/`reply`, omitted for `react`

            Optional fields:
            - `recipient` (string)
            - `subject` (string)
            - `intent` (string enum): `send` | `reply` | `react`
            - `emoji` (string): required when `intent="react"`
            - `request_context` (dict, NOT a JSON string): required for `reply`/`react` and must
              include `request_id`, `source_channel`, `source_endpoint_identity`,
              `source_sender_identity` plus `source_thread_identity` for telegram `reply`/`react`.

            Valid JSON example:
            {
              "channel": "telegram",
              "intent": "reply",
              "message": "Done. I logged it.",
              "request_context": {
                "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
                "source_channel": "telegram",
                "source_endpoint_identity": "switchboard",
                "source_sender_identity": "health",
                "source_thread_identity": "12345"
              }
            }
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
                    "error": "Unsupported notify intent. Supported intents: send, reply, react",
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

            resolved_recipient = await daemon._resolve_default_notify_recipient(
                channel=channel,
                intent=intent,
                recipient=recipient,
            )
            if channel == "telegram" and intent == "send" and resolved_recipient is None:
                return {
                    "status": "error",
                    "error": _NO_TELEGRAM_CHAT_CONFIGURED_ERROR,
                }

            delivery_message = message if message is not None else ""
            notify_request: dict[str, Any] = {
                "schema_version": "notify.v1",
                "origin_butler": butler_name,
                "delivery": {
                    "intent": intent,
                    "channel": channel,
                    "message": delivery_message,
                },
            }
            if emoji is not None:
                notify_request["delivery"]["emoji"] = emoji
            if resolved_recipient is not None:
                notify_request["delivery"]["recipient"] = resolved_recipient
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

        # Attachment retrieval tool
        @mcp.tool()
        @tool_span("get_attachment", butler_name=butler_name)
        async def get_attachment(storage_ref: str) -> dict:
            """Retrieve a media attachment for analysis.

            Returns base64-encoded blob data suitable for Claude vision/PDF input.

            Parameters
            ----------
            storage_ref:
                Storage reference string (e.g., 'local://2026/02/16/abc123.jpg')

            Returns
            -------
            dict
                - storage_ref: The storage reference
                - media_type: Inferred MIME type
                - data_base64: Base64-encoded blob data
                - size_bytes: Size of the blob in bytes
            """
            try:
                return await _get_attachment(daemon.blob_store, storage_ref)
            except BlobNotFoundError:
                # Return structured error instead of raising
                return {
                    "error": f"Attachment not found: {storage_ref}",
                    "status": "not_found",
                }
            except ValueError as exc:
                # Invalid storage_ref or size limit exceeded
                return {
                    "error": str(exc),
                    "status": "invalid",
                }
            except Exception as exc:
                logger.exception("get_attachment failed for %s", storage_ref)
                return {
                    "error": f"Failed to retrieve attachment: {exc}",
                    "status": "error",
                }

        # Module state management tools
        @mcp.tool(name="module.states")
        async def module_states() -> dict:
            """Return runtime state (health + enabled flag) for all modules.

            Returns a dict keyed by module name.  Each value is a dict with:
            - health: 'active' | 'failed' | 'cascade_failed'
            - enabled: bool
            - failure_phase: str or null
            - failure_error: str or null
            """
            states = daemon.get_module_states()
            return {
                name: {
                    "health": state.health,
                    "enabled": state.enabled,
                    "failure_phase": state.failure_phase,
                    "failure_error": state.failure_error,
                }
                for name, state in states.items()
            }

        @mcp.tool(name="module.set_enabled")
        async def module_set_enabled(name: str, enabled: bool) -> dict:
            """Toggle the runtime enabled flag for a module.

            Persists the change to the KV state store.

            Parameters
            ----------
            name:
                The module name to toggle.
            enabled:
                Whether to enable (True) or disable (False) the module.

            Returns
            -------
            dict
                - status: 'ok'
                - name: module name
                - enabled: new enabled state

            Raises
            ------
            ValueError
                If the module does not exist or is unavailable (health=failed).
            """
            try:
                await daemon.set_module_enabled(name, enabled)
                return {"status": "ok", "name": name, "enabled": enabled}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

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
                    module_runtime_states=self._module_runtime_states,
                )
                validated_config = self._module_configs.get(mod.name)
                await mod.register_tools(wrapped_mcp, validated_config, self.db)
                # Record tool → module mapping for introspection and gating.
                for tool_name in wrapped_mcp._registered_tool_names:
                    self._tool_module_map[tool_name] = mod.name
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
        2. Stop durable buffer (drain queue, cancel workers)
        2b. Cancel in-flight route_inbox background tasks
        3. Stop accepting new triggers and drain in-flight runtime sessions
        4. Cancel switchboard heartbeat
        5. Close Switchboard MCP client
        5b. Cancel internal scheduler loop (wait for in-progress tick() to finish)
        6. Module on_shutdown in reverse topological order
        7. Close DB pool
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

        # 2. Stop durable buffer — drain remaining queue then cancel workers/scanner
        if self._buffer is not None:
            shutdown_timeout = self.config.shutdown_timeout_s if self.config else 30.0
            await self._buffer.stop(drain_timeout_s=shutdown_timeout)
            self._buffer = None

        # 2b. Cancel in-flight route_inbox background tasks.
        # These tasks hold references to the spawner; cancel them before draining.
        # Rows remain in 'accepted'/'processing' state in DB and will be recovered
        # on next startup via _recover_route_inbox().
        if self._route_inbox_tasks:
            logger.info("Cancelling %d in-flight route_inbox task(s)", len(self._route_inbox_tasks))
            for task in list(self._route_inbox_tasks):
                task.cancel()
            # Allow tasks to handle CancelledError
            await asyncio.gather(*self._route_inbox_tasks, return_exceptions=True)
            self._route_inbox_tasks.clear()

        # 3. Stop accepting new triggers and drain in-flight runtime sessions
        self._accepting_connections = False
        if self.spawner is not None:
            self.spawner.stop_accepting()
            timeout = self.config.shutdown_timeout_s if self.config else 30.0
            await self.spawner.drain(timeout=timeout)

        # 4. Cancel switchboard heartbeat
        if self._switchboard_heartbeat_task is not None:
            self._switchboard_heartbeat_task.cancel()
            try:
                await self._switchboard_heartbeat_task
            except asyncio.CancelledError:
                pass
            self._switchboard_heartbeat_task = None

        # 5. Close Switchboard MCP client
        await self._disconnect_switchboard()

        # 5b. Cancel internal scheduler loop and wait for any in-progress tick() to finish
        if self._scheduler_loop_task is not None:
            self._scheduler_loop_task.cancel()
            try:
                await self._scheduler_loop_task
            except asyncio.CancelledError:
                pass
            self._scheduler_loop_task = None

        # 5c. Cancel liveness reporter loop
        if self._liveness_reporter_task is not None:
            self._liveness_reporter_task.cancel()
            try:
                await self._liveness_reporter_task
            except asyncio.CancelledError:
                pass
            self._liveness_reporter_task = None

        # 6. Module shutdown in reverse topological order (active modules only)
        active_set = {m.name for m in self._active_modules}
        for mod in reversed(self._modules):
            if mod.name not in active_set:
                continue
            try:
                await mod.on_shutdown()
            except Exception:
                logger.exception("Error during shutdown of module: %s", mod.name)

        # 7. Close audit DB pool (if separate from main DB)
        if self._audit_db is not None:
            await self._audit_db.close()
            self._audit_db = None

        # 8. Close credential-layer DB pools
        if self._shared_credentials_db is not None:
            await self._shared_credentials_db.close()
            self._shared_credentials_db = None

        # 9. Close DB pool
        if self.db:
            await self.db.close()

        logger.info("Butler shutdown complete")

    async def _build_credential_store(self, local_pool: asyncpg.Pool) -> CredentialStore:
        """Build a credential store with local override + shared fallback."""
        fallback_pools: list[asyncpg.Pool] = []
        schema_topology = bool(self.config.db_schema)
        configured_shared_db_name = shared_db_name_from_env()
        shared_db_name = configured_shared_db_name
        shared_db_schema: str | None = None
        if schema_topology:
            shared_db_name = self.config.db_name
            shared_db_schema = "shared"
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
