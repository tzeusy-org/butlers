"""Spawner — invokes ephemeral AI runtime instances for a butler.

The spawner is responsible for:
1. Generating a locked-down MCP config pointing exclusively at this butler
2. Invoking a runtime adapter (e.g. Claude Code) with that config
3. Passing only declared credentials to the runtime environment
4. Reading the butler's system prompt via the adapter
5. Enforcing serial dispatch (one instance at a time per butler)
6. Logging sessions before and after invocation
7. Passing the configured model to the SDK when set
8. Resolving models dynamically from the catalog (with TOML fallback)
9. Enforcing a process-wide global concurrency cap across all butlers

Global concurrency cap
----------------------
A module-level ``asyncio.Semaphore`` (``_global_semaphore``) limits the total
number of concurrently running LLM sessions across **all** Spawner instances in
the process.  This prevents runaway parallelism when many butlers are triggered
simultaneously.

The cap defaults to 3 and can be overridden via the
``BUTLERS_MAX_GLOBAL_SESSIONS`` environment variable.  Per-butler concurrency
limits (``max_concurrent_sessions`` in butler.toml) remain unchanged and still
apply — the global cap is an additional outer constraint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
from opentelemetry import trace
from opentelemetry.context import Context

from butlers.config import ButlerConfig
from butlers.core.audit import write_audit_entry
from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.logging import resolve_log_root
from butlers.core.mcp_urls import (
    canonical_runtime_mcp_url,
    prefer_ipv4_loopback_url,
    runtime_mcp_url,
)
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import (
    Complexity,
    apply_spend_routing_rules,
    check_monthly_ceiling,
    check_token_quota,
    next_same_tier_candidate,
    record_token_usage,
    resolve_model_with_effective_tier,
)
from butlers.core.permissions import SPAWN_PERMISSION, check_permission
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.runtimes.codex import MCPToolDiscoveryError
from butlers.core.session_process_logs import write as session_process_log_write
from butlers.core.sessions import session_complete, session_create
from butlers.core.skills import read_system_prompt

# ---------------------------------------------------------------------------
# Seam imports — functions extracted to focused sub-modules.
# Re-exported here so existing callers and test patches that reference
# ``butlers.core.spawner.<name>`` continue to resolve correctly.
# ---------------------------------------------------------------------------
from butlers.core.spawner_context import (
    _compose_system_prompt,
    _is_missing_memory_table_error,  # noqa: F401 — re-export for test patches
    _log_missing_memory_table_once,  # noqa: F401 — re-export for test patches
    _memory_context_token_budget,
    _memory_module_enabled,
    fetch_general_timezone_instruction,
    fetch_memory_context,
    fetch_routing_instructions,
    fetch_situational_context_preamble,
    fetch_system_prompt_override,
    store_session_episode,
)
from butlers.core.spawner_env import (
    _build_env,  # noqa: F401 — re-export for test patches
    _capture_pipeline_routing_context,  # noqa: F401 — re-export for test patches
)
from butlers.core.spawner_guardrails import (
    _check_degenerate_tool_loop,
    _check_token_budget,
    _check_tool_call_budget,
)
from butlers.core.spawner_provider import (
    _derive_llm_provider,  # noqa: F401 — re-export for test patches
    resolve_provider_config,  # noqa: F401 — re-export for test patches
)
from butlers.core.spawner_tool_calls import (
    _dedup_tool_calls_by_id,  # noqa: F401 — re-export for test patches
    _has_non_command_tool_calls,
    _looks_like_mcp_endpoint_alias,  # noqa: F401 — re-export for test patches
    _merge_tool_call_records,
    _normalize_tool_name,  # noqa: F401 — re-export for test patches
)
from butlers.core.telemetry import (
    clear_active_session_context,
    get_traceparent_env,
    set_active_session_context,
    tag_butler_span,
)
from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    consume_runtime_session_tool_calls,
    ensure_runtime_session_capture,
    set_runtime_session_routing_context,
)
from butlers.core.utils import generate_uuid7_string
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global spawn concurrency cap
# ---------------------------------------------------------------------------

_DEFAULT_MAX_GLOBAL_SESSIONS = 3
_global_semaphore: asyncio.Semaphore | None = None

# Lazily-populated pricing config cache — loaded once from pricing.toml on first
# session close that emits a spend event, then reused for the process lifetime.
# Avoids disk I/O on every session completion.
_cached_pricing: object | None = None  # PricingConfig when populated

# Last-resort model id used only when the model catalog returns nothing
# (no matching entry or DB unreachable). This is a hard-coded fallback
# constant, not config — nothing in git can override it. It exists so that
# the spawner keeps a deterministic dispatch model when the catalog path is
# fully degraded; the catalog path is the canonical source of truth.
_FALLBACK_MODEL_ID = "claude-haiku-4-5-20251001"

# Last-resort session timeout (seconds), paired with ``_FALLBACK_MODEL_ID``.
# Used only when neither the catalog nor a ``timeout_override`` supplies a
# timeout for the current spawn.
_FALLBACK_SESSION_TIMEOUT_S = 1800

# Runtime adapters own subprocess timeout handling because they can kill child
# processes and preserve adapter-specific diagnostics. The spawner keeps an
# outer guard only as a backstop for adapters that ignore the timeout.
_RUNTIME_TIMEOUT_MIN_CLEANUP_GRACE_S = 1.0
_RUNTIME_TIMEOUT_MAX_CLEANUP_GRACE_S = 10.0
_RUNTIME_TIMEOUT_CLEANUP_GRACE_FRACTION = 0.05

# ---------------------------------------------------------------------------
# Guardrail budget defaults
# ---------------------------------------------------------------------------

# Default maximum number of tool calls allowed per session.
# 0 means disabled (no limit enforced). This is the safe default so that
# existing deployments without explicit config are not affected.
_DEFAULT_MAX_TOOL_CALLS = 0

# Number of consecutive identical (name, input) tool call signatures that
# triggers a degenerate-loop guardrail. A conservative threshold keeps
# false-positive rates very low while catching true runaway loops.
# Only adjacent duplicates count — a loop requires the same call repeatedly
# without any different call in between.
_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD = 6


def _runtime_timeout_guard_s(timeout_s: float) -> float:
    grace_s = min(
        _RUNTIME_TIMEOUT_MAX_CLEANUP_GRACE_S,
        max(
            _RUNTIME_TIMEOUT_MIN_CLEANUP_GRACE_S,
            timeout_s * _RUNTIME_TIMEOUT_CLEANUP_GRACE_FRACTION,
        ),
    )
    return timeout_s + grace_s


def _get_global_semaphore() -> asyncio.Semaphore:
    """Return the process-wide spawn concurrency semaphore (lazy init).

    The cap is read from the ``BUTLERS_MAX_GLOBAL_SESSIONS`` environment
    variable on first call.  Defaults to 3 when the variable is absent or
    unparseable.  The semaphore is shared across **all** Spawner instances,
    so concurrent LLM sessions across every butler in the process are
    collectively bounded by this limit.
    """
    global _global_semaphore
    if _global_semaphore is None:
        raw = os.environ.get("BUTLERS_MAX_GLOBAL_SESSIONS", "")
        try:
            cap = int(raw)
            if cap < 1:
                raise ValueError("must be >= 1")
        except (ValueError, TypeError):
            cap = _DEFAULT_MAX_GLOBAL_SESSIONS
            if raw:
                logger.warning(
                    "BUTLERS_MAX_GLOBAL_SESSIONS=%r is not a valid positive integer; "
                    "defaulting to %d",
                    raw,
                    cap,
                )
        _global_semaphore = asyncio.Semaphore(cap)
        logger.info(
            "Global spawn concurrency cap initialised: max_global_sessions=%d",
            cap,
        )
    return _global_semaphore


def _reset_global_semaphore() -> None:
    """Reset the module-level global semaphore (for testing only)."""
    global _global_semaphore
    _global_semaphore = None


@dataclass
class SpawnerResult:
    """Result of a spawner invocation."""

    output: str | None = None
    success: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    model: str | None = None
    session_id: uuid.UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _append_runtime_session_query(
    url: str,
    runtime_session_id: str | None,
    trigger_source: str | None = None,
) -> str:
    """Append runtime_session_id and trigger_source query params to MCP URL."""
    if not runtime_session_id and not trigger_source:
        return url

    parsed = urlsplit(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    if runtime_session_id:
        query_items.append(("runtime_session_id", runtime_session_id))
    if trigger_source:
        query_items.append(("trigger_source", trigger_source))
    new_query = urlencode(query_items)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _estimate_worst_case_call_cost(
    model: str | None,
    max_token_budget: int | None,
) -> float | None:
    """Estimate the worst-case USD cost of a single call for per-call cap enforcement.

    The cap must be checked BEFORE the call runs, so no real token counts exist yet.
    The tightest defensible pre-spawn bound is the resolved model's input price applied
    to this dispatch's input-token budget (``max_token_budget``).  Output tokens are
    unbounded and therefore not included — the estimate intentionally bounds only the
    guaranteed-capped portion of the call, so a cap is enforced only when even the input
    side alone would exceed it.

    Returns
    -------
    float | None
        The estimated worst-case input cost in USD, or ``None`` when it cannot be
        computed (no token budget, no model, unpriced model, or any pricing error) —
        in which case the caller must not enforce the cap (fail-open).
    """
    if not model or max_token_budget is None or max_token_budget <= 0:
        return None
    try:
        from butlers.api.pricing import load_pricing

        global _cached_pricing
        if _cached_pricing is None:
            _cached_pricing = load_pricing()
        # estimate_cost returns None for unknown models; treat all budgeted tokens as
        # input tokens to bound the input-side cost.
        cost = _cached_pricing.estimate_cost(model, max_token_budget, 0)  # type: ignore[attr-defined]
    except Exception:
        logger.debug(
            "Per-call cap cost estimate failed for model=%s budget=%s (non-fatal)",
            model,
            max_token_budget,
            exc_info=True,
        )
        return None
    return cost


# ---------------------------------------------------------------------------
# Dispatch attempt provenance helper
# ---------------------------------------------------------------------------

_DISPATCH_ATTEMPTS_INSERT = """
    INSERT INTO public.model_dispatch_attempts
        (session_id, catalog_entry_id, butler, outcome,
         failure_reason, error_code, error_message,
         tool_call_count, attempt_index, logical_session_id)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""


async def _write_dispatch_attempt(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: uuid.UUID,
    butler: str,
    outcome: str,
    attempt_index: int,
    session_id: uuid.UUID | None = None,
    failure_reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    tool_call_count: int | None = None,
    logical_session_id: str | None = None,
) -> None:
    """Write one attempt row to public.model_dispatch_attempts (best-effort).

    ``outcome`` must be one of:
    - ``'quota_skip'``    — candidate skipped before invocation due to quota
    - ``'runtime_failure'`` — adapter raised a failover-eligible error
    - ``'suppressed'``    — failover decision was ineligible (side effects / unknown)
    - ``'exhausted'``     — all same-tier candidates tried, none succeeded
    - ``'success'``       — this attempt produced the final successful result

    Never raises — write failures are logged at DEBUG and silently ignored so
    the caller session is never disrupted by provenance instrumentation.
    """
    _error_message_trunc = error_message[:4096] if error_message else None
    try:
        await pool.execute(
            _DISPATCH_ATTEMPTS_INSERT,
            session_id,
            catalog_entry_id,
            butler,
            outcome,
            failure_reason,
            error_code,
            _error_message_trunc,
            tool_call_count,
            attempt_index,
            logical_session_id,
        )
    except Exception:
        logger.debug(
            "Failed to write dispatch attempt for butler=%s catalog_entry_id=%s outcome=%s",
            butler,
            catalog_entry_id,
            outcome,
            exc_info=True,
        )


class Spawner:
    """Core component that invokes ephemeral AI runtime instances for a butler.

    Each butler has exactly one Spawner. An asyncio.Semaphore with a configurable
    concurrency limit controls dispatch — at most ``max_concurrent_sessions``
    runtime instances may run simultaneously per butler. When
    ``max_concurrent_sessions`` is 1 (the default), behaviour is identical to
    the previous asyncio.Lock-based implementation (serial dispatch).

    Parameters
    ----------
    config:
        The butler's parsed ButlerConfig.
    config_dir:
        Path to the butler's config directory (containing CLAUDE.md, etc.).
    pool:
        asyncpg connection pool for session logging.
    module_credentials_env:
        Dict mapping module name to list of env var names needed by that module.
    runtime:
        A RuntimeAdapter instance to use for invocation. When not provided,
        a default ClaudeCodeAdapter is created.
    audit_pool:
        Optional asyncpg pool pointed at the switchboard database for writing
        daemon-side audit log entries.
    credential_store:
        Optional CredentialStore instance for DB-first credential resolution.
        When provided, credentials are resolved from the database before
        falling back to environment variables. When None, credentials are
        resolved exclusively from environment variables (for backwards
        compatibility and unit tests without a DB pool).
    """

    def __init__(
        self,
        config: ButlerConfig,
        config_dir: Path,
        pool: asyncpg.Pool | None = None,
        module_credentials_env: dict[str, list[str]] | None = None,
        runtime: RuntimeAdapter | None = None,
        audit_pool: asyncpg.Pool | None = None,
        credential_store: CredentialStore | None = None,
        runtime_config_accessor: Any | None = None,
    ) -> None:
        self._config = config
        self._config_dir = config_dir
        self._pool = pool
        self._module_credentials_env = module_credentials_env
        self._audit_pool = audit_pool
        self._credential_store = credential_store
        self._runtime_config_accessor = runtime_config_accessor

        # Cold fields: read from accessor (DB) if available, else fall back to the runtime seed.
        if runtime_config_accessor is not None and runtime_config_accessor._cache is not None:
            _max_concurrent = runtime_config_accessor._cache.max_concurrent
            _max_queued = runtime_config_accessor._cache.max_queued
        else:
            _max_concurrent = config.runtime_seed.max_concurrent_sessions
            _max_queued = config.runtime_seed.max_queued_sessions
        self._session_semaphore = asyncio.Semaphore(_max_concurrent)
        self._max_queued_sessions = _max_queued
        self._accepting = True
        self._in_flight: set[asyncio.Task] = set()
        self._in_flight_event = asyncio.Event()
        self._in_flight_event.set()  # Initially no in-flight sessions
        self._metrics = ButlerMetrics(butler_name=config.name)
        self._metrics.ensure_registered()
        self._mcp_warmup_lock = asyncio.Lock()
        self._warmed_mcp_urls: set[str] = set()
        # Self-healing module reference — wired by the daemon after module startup.
        # When non-None, the spawner fallback fires on hard crashes.
        self._healing_module: Any = None

        if runtime is not None:
            self._runtime = runtime
            # Seed the adapter pool with the injected runtime under the default
            # adapter type. Tests that inject a mock adapter get a one-entry
            # pool keyed by the fixed default type; per-session runtime types
            # are resolved lazily via :meth:`_get_or_create_adapter`.
            self._adapter_pool: dict[str, RuntimeAdapter] = {
                DEFAULT_RUNTIME_TYPE: runtime,
            }
            self._adapter_pool_cfg: dict[str, str] = {DEFAULT_RUNTIME_TYPE: ""}
        else:
            # Default: create a ClaudeCodeAdapter with the real SDK query
            from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

            log_root = resolve_log_root(config.logging.log_root)
            self._runtime = ClaudeCodeAdapter(
                butler_name=config.name,
                log_root=log_root,
                credential_store=credential_store,
            )
            self._adapter_pool = {
                DEFAULT_RUNTIME_TYPE: self._runtime,
            }
            self._adapter_pool_cfg = {DEFAULT_RUNTIME_TYPE: ""}

    def wire_healing_module(self, healing_module: Any) -> None:
        """Wire the self-healing module for spawner fallback dispatch.

        Called by the butler daemon after the self-healing module's
        ``on_startup()`` completes.  When wired, the spawner's except block
        fires ``dispatch_healing()`` as a background task on hard crashes.

        Parameters
        ----------
        healing_module:
            A :class:`~butlers.modules.self_healing.SelfHealingModule` instance
            (typed as ``Any`` to avoid a circular import).  Pass ``None`` to
            unwire.
        """
        self._healing_module = healing_module

    async def _resolve_provider_config(
        self, model_id: str | None
    ) -> dict[str, dict[str, Any]] | None:
        """Look up provider base URL from ``public.provider_config``.

        Delegates to the module-level :func:`resolve_provider_config`.
        """
        return await resolve_provider_config(self._pool, model_id)

    def _get_or_create_adapter(
        self,
        runtime_type: str,
        provider_config: dict[str, dict[str, Any]] | None = None,
    ) -> RuntimeAdapter:
        """Return a cached parent adapter for *runtime_type*, creating one lazily if needed.

        The TOML-configured adapter is seeded at construction time.  When the
        catalog resolves a *different* runtime type, this method instantiates a
        new adapter via :func:`~butlers.core.runtimes.base.create_adapter` and
        caches it.  The caller is responsible for calling ``.create_worker()``
        on the result.
        """
        cfg_str = str(provider_config) if provider_config else ""
        if runtime_type in self._adapter_pool:
            if self._adapter_pool_cfg.get(runtime_type, "") == cfg_str:
                return self._adapter_pool[runtime_type]

        from butlers.core.runtimes.base import create_adapter

        log_root = resolve_log_root(self._config.logging.log_root)
        adapter = create_adapter(
            runtime_type,
            provider_config=provider_config,
            butler_name=self._config.name,
            log_root=log_root,
            credential_store=self._credential_store,
        )
        self._adapter_pool[runtime_type] = adapter
        self._adapter_pool_cfg[runtime_type] = cfg_str
        logger.debug("Lazily instantiated adapter for runtime_type=%s", runtime_type)
        return adapter

    async def trigger(
        self,
        prompt: str,
        trigger_source: str,
        context: str | None = None,
        max_turns: int = 20,
        parent_context: Context | None = None,
        request_id: str | None = None,
        complexity: Complexity = Complexity.WORKHORSE,
        cwd: str | None = None,
        bypass_butler_semaphore: bool = False,
        max_token_budget: int | None = None,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        env_override: dict[str, str] | None = None,
        timeout_override: int | None = None,
        ingestion_event_id: str | None = None,
    ) -> SpawnerResult:
        """Spawn an ephemeral runtime instance.

        Acquires a slot in the per-butler concurrency pool (semaphore), generates
        the MCP config, invokes the runtime via the adapter, and logs the session.

        Parameters
        ----------
        prompt:
            The prompt to send to the runtime instance.
        trigger_source:
            What caused this invocation. Expected values are ``tick``,
            ``external``, ``trigger``, ``route``, or ``schedule:<task-name>``.
        context:
            Optional text to prepend to the prompt. If provided and non-empty,
            this will be prepended to the prompt with two newlines separating them.
        max_turns:
            Maximum number of turns for the runtime session. Defaults to 20.
        parent_context:
            Optional OpenTelemetry context for trace propagation. When provided,
            the spawned session's span will be a child of the parent trace.
        request_id:
            Optional request ID from ingestion request_context (UUIDv7 format).
            For non-ingestion triggers (scheduler, tick), this should be None.
        complexity:
            Task complexity tier used to select a model from the catalog.
            Defaults to ``Complexity.WORKHORSE``.  The catalog is queried with this
            tier; when no catalog entry matches the TOML-configured model is used.
        cwd:
            Optional working directory for the runtime invocation. When ``None``,
            defaults to the butler's config directory. Used by the self-healing
            dispatcher to set the CWD to an isolated worktree path.
        bypass_butler_semaphore:
            When ``True``, skip the per-butler ``_session_semaphore`` and run the
            session directly after acquiring the global semaphore. Intended for
            the self-healing dispatcher, which manages its own concurrency cap and
            must not block behind ordinary butler sessions.
        max_token_budget:
            Optional per-session token budget (input tokens). When set and the
            completed session's ``input_tokens`` exceeds this value, a
            ``RuntimeError("token_budget_exceeded: ...")`` is raised so the
            failover classifier treats this as a guardrail termination and
            suppresses any same-tier retry.
        max_tool_calls:
            Maximum number of tool calls allowed per session. ``0`` disables
            the limit (default). When exceeded, a
            ``RuntimeError("tool_call_budget_exceeded: ...")`` is raised,
            which the failover classifier suppresses from same-tier retry.
        env_override:
            When provided, replaces the automatically-built credential/env dict
            with this explicit environment map. Used by the QA dispatcher to pass
            a sandboxed environment that strips dangerous variables.
        timeout_override:
            When provided, overrides the resolved per-session timeout for this
            invocation (in seconds). Used by the self-healing and QA dispatchers
            whose workflow watchdog caps may differ from the catalog/default
            session timeout.
        ingestion_event_id:
            Optional UUID of the ``public.ingestion_events`` row that caused
            this trigger. Set by the route handler so the resulting session
            row joins back to the ingestion event (for chronicler contact
            resolution and downstream provenance). Internally-triggered
            sessions (tick, scheduler, manual trigger) leave this as ``None``.

        Returns
        -------
        SpawnerResult
            The result of the runtime invocation.

        Raises
        ------
        RuntimeError
            If the spawner has been stopped and is no longer accepting triggers.
        """
        if not self._accepting:
            raise RuntimeError("Spawner is shutting down; not accepting new triggers")

        # Prevent self-trigger deadlocks: an in-flight trigger-sourced session can
        # invoke the trigger tool again via MCP. Waiting on the semaphore here
        # when all slots are occupied would deadlock the runtime call graph.
        # We only reject when every concurrency slot is taken.
        # With n > 1 a free slot may still be available, so we allow the call.
        #
        # Implementation note: we access asyncio.Semaphore._value (a CPython
        # internal) because the public locked() method returns True even when
        # there are waiters but _value > 0 — i.e. free slots still exist. Using
        # locked() would over-reject when concurrent sessions are waiting but a
        # slot is genuinely available. _value has been stable across CPython
        # releases and the access is intentional. Alternatively, track a
        # separate counter if this ever becomes fragile.
        if trigger_source == "trigger" and self._session_semaphore._value == 0:
            error_msg = (
                "Runtime invocation rejected: trigger tool cannot be called while "
                "another session is in flight"
            )
            logger.warning(error_msg)
            return SpawnerResult(
                success=False,
                error=error_msg,
                model=_FALLBACK_MODEL_ID,
            )

        # Implementation note: queue-depth checks read Semaphore._waiters, which
        # is also a CPython internal. We intentionally pair this with _value so
        # backpressure only rejects when no active slot is available and the
        # waiter queue has reached max_queued_sessions. Revisit if asyncio internals
        # change or cross-interpreter portability becomes a requirement.
        raw_waiters = getattr(self._session_semaphore, "_waiters", None)
        queued_waiters = len(raw_waiters or ())
        if self._session_semaphore._value == 0 and queued_waiters >= self._max_queued_sessions:
            error_msg = (
                "Runtime invocation rejected: spawner queue is full "
                f"(max_queued_sessions={self._max_queued_sessions})"
            )
            logger.warning(error_msg)
            return SpawnerResult(
                success=False,
                error=error_msg,
                model=_FALLBACK_MODEL_ID,
            )

        self._in_flight_event.clear()
        task = asyncio.current_task()
        if task is not None:
            self._in_flight.add(task)
        _global_semaphore_acquired = False
        _semaphore_acquired = False
        global_sem = _get_global_semaphore()
        try:
            # Acquire the process-wide global cap first.
            # When all global slots are taken, log at INFO so operators can see
            # that spawns are being queued (metric: spawner_global_queue_depth).
            if global_sem._value == 0:
                logger.info(
                    "Spawn queued waiting for global cap (butler=%s, prompt=%.60r)",
                    self._config.name,
                    prompt,
                )
            self._metrics.spawner_global_queue_depth_inc()
            try:
                await global_sem.acquire()
                _global_semaphore_acquired = True
            finally:
                self._metrics.spawner_global_queue_depth_dec()

            if bypass_butler_semaphore:
                # Healing path: skip per-butler semaphore to avoid deadlock with
                # in-flight ordinary sessions. The healing dispatcher enforces its
                # own concurrency cap (Gate 8) before reaching here.
                _semaphore_acquired = True
                self._metrics.spawner_active_sessions_inc()
                try:
                    return await self._run(
                        prompt,
                        trigger_source,
                        context,
                        max_turns,
                        parent_context,
                        request_id,
                        complexity,
                        cwd=cwd,
                        max_token_budget=max_token_budget,
                        max_tool_calls=max_tool_calls,
                        env_override=env_override,
                        timeout_override=timeout_override,
                        ingestion_event_id=ingestion_event_id,
                    )
                finally:
                    self._metrics.spawner_active_sessions_dec()
            else:
                # Track triggers waiting for the per-butler semaphore slot only
                # (after the global cap is acquired, so the metric reflects
                # per-butler queue depth, not global backpressure wait time).
                self._metrics.spawner_queued_triggers_inc()
                async with self._session_semaphore:
                    # Slot acquired — no longer queued, now active
                    _semaphore_acquired = True
                    self._metrics.spawner_queued_triggers_dec()
                    self._metrics.spawner_active_sessions_inc()
                    try:
                        return await self._run(
                            prompt,
                            trigger_source,
                            context,
                            max_turns,
                            parent_context,
                            request_id,
                            complexity,
                            cwd=cwd,
                            max_token_budget=max_token_budget,
                            max_tool_calls=max_tool_calls,
                            env_override=env_override,
                            timeout_override=timeout_override,
                            ingestion_event_id=ingestion_event_id,
                        )
                    finally:
                        self._metrics.spawner_active_sessions_dec()
        finally:
            # Release global semaphore if acquired (not released via context manager).
            if _global_semaphore_acquired:
                global_sem.release()
            # If the global semaphore was acquired but the per-butler semaphore was not
            # (e.g. cancelled after global acquire but before/during per-butler acquire),
            # queued_triggers_dec was never called inside the async-with block;
            # decrement here to keep the gauge accurate.
            # Skip this in bypass mode: spawner_queued_triggers was never incremented.
            if (
                _global_semaphore_acquired
                and not _semaphore_acquired
                and not bypass_butler_semaphore
            ):
                self._metrics.spawner_queued_triggers_dec()
            if task is not None:
                self._in_flight.discard(task)
            if not self._in_flight:
                self._in_flight_event.set()

    def stop_accepting(self) -> None:
        """Stop accepting new trigger requests.

        Existing in-flight sessions continue until they complete or are
        cancelled via :meth:`drain`.
        """
        self._accepting = False
        logger.info("Spawner stopped accepting new triggers")

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for in-flight runtime sessions to complete, up to *timeout* seconds.

        If sessions are still running after the timeout, they are cancelled.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait for in-flight sessions to finish.
        """
        if not self._in_flight:
            logger.info("No in-flight sessions to drain")
            return

        logger.info(
            "Draining %d in-flight session(s) (timeout=%.1fs)",
            len(self._in_flight),
            timeout,
        )
        try:
            await asyncio.wait_for(self._in_flight_event.wait(), timeout=timeout)
            logger.info("All in-flight sessions drained successfully")
        except TimeoutError:
            remaining = len(self._in_flight)
            logger.warning(
                "Drain timeout after %.1fs; cancelling %d in-flight session(s)",
                timeout,
                remaining,
            )
            for task in list(self._in_flight):
                task.cancel()
            # Give cancelled tasks a moment to clean up
            if self._in_flight:
                await asyncio.sleep(0.1)
            self._in_flight.clear()
            self._in_flight_event.set()

    @property
    def in_flight_count(self) -> int:
        """Return the number of currently in-flight runtime sessions."""
        return len(self._in_flight)

    @staticmethod
    def _normalize_mcp_warmup_url(url: str) -> str | None:
        """Return a canonical warmup URL without per-session query params."""
        if not isinstance(url, str) or not url.strip():
            return None
        parsed = urlsplit(prefer_ipv4_loopback_url(canonical_runtime_mcp_url(url.strip())))
        if not parsed.scheme or not parsed.netloc:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))

    async def _ensure_mcp_endpoints_warmed(self, mcp_servers: dict[str, Any]) -> None:
        """Best-effort one-time MCP warmup before the first MCP-backed spawn.

        Daemon startup also schedules background warmup, but that task is
        intentionally fire-and-forget. A session triggered immediately after
        boot can therefore still beat the background task and hit a cold MCP
        server. Warm once on the spawn path so the first real Codex session
        does not depend on startup timing.
        """
        if not mcp_servers:
            return

        candidate_urls = [
            normalized
            for normalized in (
                self._normalize_mcp_warmup_url(server_cfg.get("url", ""))
                for server_cfg in mcp_servers.values()
                if isinstance(server_cfg, dict)
            )
            if normalized is not None
        ]
        if not candidate_urls:
            return

        async with self._mcp_warmup_lock:
            pending_urls = [
                url for url in dict.fromkeys(candidate_urls) if url not in self._warmed_mcp_urls
            ]
            if not pending_urls:
                return

            try:
                from butlers.core.mcp_warmup import warmup_mcp_urls

                results = await warmup_mcp_urls(self._config.name, pending_urls)
            except Exception:
                logger.warning(
                    "Pre-spawn MCP warmup failed for butler=%s; continuing without warmup",
                    self._config.name,
                    exc_info=True,
                )
                return

            warmed_now = 0
            for result in results:
                url = result.get("url")
                if result.get("success") and isinstance(url, str) and url:
                    self._warmed_mcp_urls.add(url)
                    warmed_now += 1

            if warmed_now:
                logger.debug(
                    "Pre-spawn MCP warmup completed for butler=%s warmed_urls=%d",
                    self._config.name,
                    warmed_now,
                )

    async def _run(
        self,
        prompt: str,
        trigger_source: str,
        context: str | None = None,
        max_turns: int = 20,
        parent_context: Context | None = None,
        request_id: str | None = None,
        complexity: Complexity = Complexity.WORKHORSE,
        cwd: str | None = None,
        max_token_budget: int | None = None,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        env_override: dict[str, str] | None = None,
        timeout_override: int | None = None,
        ingestion_event_id: str | None = None,
    ) -> SpawnerResult:
        """Internal: run the runtime invocation (called under lock)."""
        session_id: uuid.UUID | None = None
        runtime_session_id: str | None = None
        spawner_result: SpawnerResult | None = None
        runtime_invoked = False
        # When the MCPToolDiscoveryError handler consumes the runtime-session
        # tool-call buffer, it stores the result here so the failure-path
        # exception handler does not re-consume an empty buffer.
        preconsumed_runtime_tool_calls: list[dict[str, Any]] | None = None
        # Set to True when the failover loop already ran classify_failover_eligibility
        # and emitted the suppressed metric for this exception.  The outer except
        # block skips classification for exceptions that are already classified.
        _failover_already_classified: bool = False
        routing_context = _capture_pipeline_routing_context()
        # Ledger token tracking: set as soon as the adapter reports usage so that
        # ledger recording in the finally block captures tokens even when post-invoke
        # processing fails (e.g. session_complete raises). Tokens are consumed by the
        # upstream provider on invocation regardless of later failure.
        _ledger_input_tokens: int | None = None
        _ledger_output_tokens: int | None = None

        # Prepend context to prompt if provided
        final_prompt = prompt
        if context:
            final_prompt = f"{context}\n\n{prompt}"

        # Resolve model from the catalog; fall back to the hard-coded default
        # constants only when no catalog entry exists or catalog resolution fails.
        # resolve_model_with_effective_tier returns a 6-tuple including the effective tier
        # needed to restrict same-tier failover attempts.
        fallback_runtime_type = DEFAULT_RUNTIME_TYPE
        fallback_model = _FALLBACK_MODEL_ID
        catalog_result = None
        if self._pool is not None:
            try:
                catalog_result = await resolve_model_with_effective_tier(
                    self._pool, self._config.name, complexity
                )
            except Exception:
                logger.debug(
                    "Catalog model resolution failed for butler=%s complexity=%s; "
                    "using TOML config",
                    self._config.name,
                    complexity,
                    exc_info=True,
                )

        # Only trust the catalog result when it is a properly-typed tuple; fall back to TOML
        # for any unexpected value (e.g. a MagicMock from a test pool that does not stub
        # the catalog tables).
        _catalog_valid = (
            catalog_result is not None
            and isinstance(catalog_result, tuple)
            and len(catalog_result) == 6
            and isinstance(catalog_result[0], str)
            and isinstance(catalog_result[1], str)
            and isinstance(catalog_result[2], list)
            and isinstance(catalog_result[4], int)
            and isinstance(catalog_result[5], str)
        )
        catalog_entry_id: uuid.UUID | None = None
        catalog_timeout_s: int | None = None
        # Effective tier pinned from initial resolution for same-tier failover.
        # None when using static_fallback (no failover in that path).
        _failover_effective_tier: str | None = None
        if _catalog_valid:
            assert catalog_result is not None  # narrowing for type checker
            (
                resolved_runtime_type,
                model,
                catalog_extra_args,
                catalog_entry_id,
                catalog_timeout_s,
                _failover_effective_tier,
            ) = catalog_result
            resolution_source = "catalog"
        else:
            resolved_runtime_type = fallback_runtime_type
            model = fallback_model
            catalog_extra_args = []
            catalog_timeout_s = None
            resolution_source = "static_fallback"

        logger.debug(
            "Model resolution: butler=%s complexity=%s source=%s runtime_type=%s model=%s",
            self._config.name,
            complexity,
            resolution_source,
            resolved_runtime_type,
            model,
        )

        # ---------------------------------------------------------------------------
        # Spend routing rules (public.spend_rules) — model SELECTION override
        #
        # The Settings → Spend page stores ordered routing rules (condition → action,
        # position-sorted) and promises "rules evaluate top-to-bottom and the first
        # match wins". Until now that copy was a lie: rules were stored, reorderable,
        # and fed a decorative saved_7d analytics value, but were NEVER consulted at
        # dispatch. Here we make the promise real — after tier resolution and BEFORE
        # the spawn-time DENY gates (permissions / quota / ceiling). A matching rule
        # re-routes the resolved model to the rule's target (action.model), re-resolved
        # to a real dispatchable catalog row so downstream quota / failover / ledger all
        # operate on the rule-selected model. This is a SELECTION step (which model),
        # distinct from the authorization (permissions) and budget (quota/ceiling) DENY
        # gates below. apply_spend_routing_rules fails open, so a rules error never
        # wedges spawns. Only runs on a real catalog resolution (static_fallback has no
        # catalog_entry_id to route from).
        # ---------------------------------------------------------------------------
        # Per-call USD cap surfaced by a matching spend rule's action.max_cost_per_call
        # effect (None when no rule sets a cap). Enforced as a DENY gate below.
        _spend_rule_max_cost_per_call: float | None = None
        if catalog_entry_id is not None and self._pool is not None:
            try:
                _routing_result = await apply_spend_routing_rules(
                    self._pool,
                    self._config.name,
                    _failover_effective_tier or complexity,
                    (
                        resolved_runtime_type,
                        model,
                        catalog_extra_args,
                        catalog_entry_id,
                        catalog_timeout_s,
                    ),
                    trigger_source=trigger_source,
                )
                (
                    resolved_runtime_type,
                    model,
                    catalog_extra_args,
                    catalog_entry_id,
                    catalog_timeout_s,
                ) = _routing_result.resolved
                _spend_rule_max_cost_per_call = _routing_result.max_cost_per_call
            except Exception:
                logger.warning(
                    "Spend routing-rule evaluation failed for butler=%s; "
                    "keeping tier-resolved model=%s",
                    self._config.name,
                    model,
                    exc_info=True,
                )

        # ---------------------------------------------------------------------------
        # Same-tier failover: quota-skip loop
        #
        # Before invoking, check quota for the resolved catalog entry.  If exhausted,
        # skip to the next same-tier candidate.  Hard-block only when no candidates
        # remain.  The attempted_ids list grows as we skip quota-exhausted entries.
        # ---------------------------------------------------------------------------

        # Mint effective_request_id here (before the quota-skip loop) so that
        # quota_skip rows share the same logical_session_id as subsequent rows
        # (suppressed / runtime_failure / success) even when request_id is None
        # (scheduler/tick triggers).
        effective_request_id: str = request_id or generate_uuid7_string()

        _attempted_ids: list[uuid.UUID] = []

        # ---------------------------------------------------------------------------
        # Permissions-matrix enforcement (public.permissions)
        #
        # The Settings → Permissions matrix governs which butler may act. Before this
        # gate the matrix was decorative: cells were persisted + audited but never
        # consulted at runtime. Here we enforce the per-butler ``spawn`` permission at
        # the universal choke point where a butler acts — the spawn. A cell flipped to
        # ``granted=false`` blocks the spawn outright (no model can bring it back; this
        # is an authorization decision, not a budget one). Mirrors the spend-ceiling /
        # token-quota denial path: a ``quota_skip`` dispatch-attempt row (so the denial
        # is observable in provenance) plus a failed SpawnerResult. check_permission
        # fails open, so a DB error never wedges spawns.
        # ---------------------------------------------------------------------------
        if catalog_entry_id is not None and self._pool is not None:
            perm = await check_permission(self._pool, self._config.name, SPAWN_PERMISSION)
            if not perm.allowed:
                perm_msg = (
                    f"Permission denied: butler '{self._config.name}' is not granted "
                    f"'{SPAWN_PERMISSION}'"
                )
                if perm.reason:
                    perm_msg += f" (reason: {perm.reason})"
                logger.warning(
                    "Spawn blocked by permissions matrix for butler=%s: %s",
                    self._config.name,
                    perm_msg,
                )
                await _write_dispatch_attempt(
                    self._pool,
                    catalog_entry_id=catalog_entry_id,
                    butler=self._config.name,
                    outcome="quota_skip",
                    attempt_index=len(_attempted_ids),
                    failure_reason=perm_msg,
                    tool_call_count=0,
                    logical_session_id=effective_request_id,
                )
                return SpawnerResult(success=False, error=perm_msg, model=model)

        if catalog_entry_id is not None and self._pool is not None:
            while True:
                quota = await check_token_quota(self._pool, catalog_entry_id)
                if quota.allowed:
                    break
                # Quota exhausted: record and skip to next same-tier candidate.
                windows_exceeded = []
                if quota.limit_24h is not None and quota.usage_24h >= quota.limit_24h:
                    windows_exceeded.append(
                        f"24h (used={quota.usage_24h}, limit={quota.limit_24h})"
                    )
                if quota.limit_30d is not None and quota.usage_30d >= quota.limit_30d:
                    windows_exceeded.append(
                        f"30d (used={quota.usage_30d}, limit={quota.limit_30d})"
                    )
                alias = model or str(catalog_entry_id)
                quota_msg = f"Token quota exhausted for catalog entry '{alias}': " + "; ".join(
                    windows_exceeded
                )
                logger.warning(
                    "Quota exhausted for butler=%s catalog_entry_id=%s; "
                    "seeking next same-tier candidate: %s",
                    self._config.name,
                    catalog_entry_id,
                    quota_msg,
                )
                _skipped_attempt_index = len(_attempted_ids)
                _attempted_ids.append(catalog_entry_id)
                await _write_dispatch_attempt(
                    self._pool,
                    catalog_entry_id=catalog_entry_id,
                    butler=self._config.name,
                    outcome="quota_skip",
                    attempt_index=_skipped_attempt_index,
                    failure_reason=quota_msg,
                    tool_call_count=0,
                    logical_session_id=effective_request_id,
                )

                if _failover_effective_tier is None:
                    # No tier pinned (shouldn't happen in this branch, but be safe)
                    return SpawnerResult(success=False, error=quota_msg, model=model)

                next_candidate = await next_same_tier_candidate(
                    self._pool,
                    self._config.name,
                    _failover_effective_tier,
                    _attempted_ids,
                )
                if next_candidate is None:
                    # No candidates remain: hard block.
                    self._metrics.record_failover_exhausted(tier=_failover_effective_tier)
                    logger.warning(
                        "All same-tier candidates exhausted after quota skips for "
                        "butler=%s tier=%s",
                        self._config.name,
                        _failover_effective_tier,
                    )
                    # The last skipped entry is also the exhausted entry; no extra
                    # row needed — the quota_skip row already captures it.
                    return SpawnerResult(success=False, error=quota_msg, model=model)

                # Advance to the next candidate.
                next_rt, next_model, next_extra_args, next_entry_id, next_timeout_s = next_candidate
                self._metrics.record_failover_attempt(
                    from_model=model,
                    to_model=next_model,
                    reason="quota_exhausted",
                )
                resolved_runtime_type = next_rt
                model = next_model
                catalog_extra_args = next_extra_args
                catalog_entry_id = next_entry_id
                catalog_timeout_s = next_timeout_s
                # Loop again to check quota for the new candidate.

        # ---------------------------------------------------------------------------
        # Monthly spend-ceiling enforcement
        #
        # Independent of (and in addition to) the per-catalog-entry token quota above:
        # block the spawn when month-to-date spend has reached the configured monthly
        # USD ceiling.  Unlike the token quota — which is per-model and can be skipped
        # to a same-tier fallback — the ceiling is a global budget, so exceeding it is
        # a hard block regardless of which model resolved (switching models would not
        # bring spend back under budget).  Reuses the quota denial path: a
        # ``quota_skip`` dispatch-attempt row (so the denial is observable in
        # provenance) plus a failed SpawnerResult.  Fails open inside
        # check_monthly_ceiling, so a DB/pricing error never wedges spawns.
        # ---------------------------------------------------------------------------
        if catalog_entry_id is not None and self._pool is not None:
            ceiling = await check_monthly_ceiling(self._pool)
            if not ceiling.allowed:
                ceiling_msg = (
                    "Monthly spend ceiling reached: "
                    f"month-to-date ${ceiling.mtd_usd:.2f} >= ceiling "
                    f"${ceiling.ceiling_usd:.2f}"
                )
                logger.warning(
                    "Spawn blocked by monthly spend ceiling for butler=%s: %s",
                    self._config.name,
                    ceiling_msg,
                )
                await _write_dispatch_attempt(
                    self._pool,
                    catalog_entry_id=catalog_entry_id,
                    butler=self._config.name,
                    outcome="quota_skip",
                    attempt_index=len(_attempted_ids),
                    failure_reason=ceiling_msg,
                    tool_call_count=0,
                    logical_session_id=effective_request_id,
                )
                return SpawnerResult(success=False, error=ceiling_msg, model=model)

        # ---------------------------------------------------------------------------
        # Per-call cost cap enforcement (spend rule action.max_cost_per_call)
        #
        # A matching spend rule may carry a hard per-dispatch USD cap. Unlike the
        # monthly ceiling (a global running budget) this caps the cost of THIS single
        # call. The call has not run yet, so we enforce on a worst-case pre-spawn
        # estimate: the resolved model's input price times this dispatch's input-token
        # budget (max_token_budget). If that worst-case exceeds the cap, the dispatch is
        # DENIED here — switching models would not help (the cap is attached to the
        # matched rule, which already chose the model), so this is a hard block mirroring
        # the ceiling denial path (a quota_skip provenance row + a failed SpawnerResult).
        #
        # When the call has no input-token budget (max_token_budget is None) the per-call
        # cost is unbounded and cannot be guaranteed under the cap pre-spawn; we cannot
        # honestly enforce, so we log and allow rather than block arbitrarily. Operators
        # who want the cap enforced should pair it with a token budget. Estimation fails
        # open: any pricing/lookup error leaves the dispatch allowed.
        # ---------------------------------------------------------------------------
        if (
            _spend_rule_max_cost_per_call is not None
            and catalog_entry_id is not None
            and self._pool is not None
        ):
            worst_case_usd = _estimate_worst_case_call_cost(model, max_token_budget)
            if worst_case_usd is None:
                logger.info(
                    "Spend-rule per-call cap $%.4f set for butler=%s model=%s but call has no "
                    "input-token budget (or model is unpriced); cap not enforceable pre-spawn, "
                    "allowing dispatch",
                    _spend_rule_max_cost_per_call,
                    self._config.name,
                    model,
                )
            elif worst_case_usd > _spend_rule_max_cost_per_call:
                cap_msg = (
                    "Per-call spend cap exceeded: estimated worst-case "
                    f"${worst_case_usd:.4f} > cap ${_spend_rule_max_cost_per_call:.4f} "
                    f"(model={model}, input_budget={max_token_budget:,} tokens)"
                )
                logger.warning(
                    "Spawn blocked by per-call spend cap for butler=%s: %s",
                    self._config.name,
                    cap_msg,
                )
                await _write_dispatch_attempt(
                    self._pool,
                    catalog_entry_id=catalog_entry_id,
                    butler=self._config.name,
                    outcome="quota_skip",
                    attempt_index=len(_attempted_ids),
                    failure_reason=cap_msg,
                    tool_call_count=0,
                    logical_session_id=effective_request_id,
                )
                return SpawnerResult(success=False, error=cap_msg, model=model)

        # Resolve provider config (e.g. Ollama base URL) for the model
        provider_config = await self._resolve_provider_config(model)

        # Select adapter for the resolved runtime type (lazy instantiation on demand).
        # Fall back to the default adapter if the catalog resolved an unregistered runtime type.
        try:
            runtime = self._get_or_create_adapter(
                resolved_runtime_type, provider_config
            ).create_worker()
        except ValueError:
            logger.warning(
                "Catalog resolved unregistered runtime_type=%s for butler=%s; "
                "falling back to default runtime_type=%s",
                resolved_runtime_type,
                self._config.name,
                fallback_runtime_type,
            )
            resolved_runtime_type = fallback_runtime_type
            model = fallback_model
            catalog_extra_args = []
            catalog_timeout_s = None
            resolution_source = "static_fallback"
            runtime = self._get_or_create_adapter(fallback_runtime_type).create_worker()

        # Use the catalog-supplied extra args. There is no butler-level args
        # fallback: the model catalog is the sole source of per-session CLI args.
        merged_args = list(catalog_extra_args)

        # Get tracer and start butler.llm_session span with parent context
        tracer = trace.get_tracer("butlers")
        span = tracer.start_span("butler.llm_session", context=parent_context)
        tag_butler_span(span, self._config.name)
        span.set_attribute("prompt_length", len(final_prompt))

        # Attach span to context and publish for cross-task tool_span use
        token = trace.context_api.attach(trace.set_span_in_context(span))
        set_active_session_context(trace.context_api.get_current())
        t0 = time.monotonic()

        try:
            # Extract trace_id from active span
            trace_id: str | None = None
            if span.is_recording():
                trace_id = format(span.get_span_context().trace_id, "032x")

            # effective_request_id was minted before the quota-skip loop above
            # (non-null for both connector-sourced and internal triggers).

            # Create session record with trace_id and request_id
            if self._pool is not None:
                session_id = await session_create(
                    self._pool,
                    final_prompt,
                    trigger_source,
                    trace_id,
                    model=model,
                    request_id=effective_request_id,
                    ingestion_event_id=ingestion_event_id,
                    complexity=str(complexity),
                    resolution_source=resolution_source,
                )
                logger.debug(
                    "Session created with model=%s runtime_type=%s complexity=%s source=%s "
                    "session_id=%s",
                    model,
                    resolved_runtime_type,
                    complexity,
                    resolution_source,
                    session_id,
                )
                # Set session_id on span
                span.set_attribute("session_id", str(session_id))
                runtime_session_id = str(session_id)
                ensure_runtime_session_capture(runtime_session_id)
                set_runtime_session_routing_context(runtime_session_id, routing_context)

            # Read system prompt. The live override (HEAD of
            # public.system_prompt_history, set via the dashboard prompt editor)
            # takes precedence over the on-disk CLAUDE.md seed when present.
            shared_pool = (
                self._credential_store.shared_pool if self._credential_store is not None else None
            )
            prompt_override = await fetch_system_prompt_override(
                shared_pool or self._pool, self._config.name
            )
            system_prompt = read_system_prompt(
                self._config_dir, self._config.name, db_override=prompt_override
            )

            # Fetch situational context preamble (fail-open)
            context_preamble_ctx = await fetch_situational_context_preamble(
                self._pool, self._config.name
            )
            general_timezone_instruction = await fetch_general_timezone_instruction(
                self._pool,
                self._config.name,
                self._credential_store,
            )

            # Fetch owner routing instructions (switchboard only)
            # Intentional name check: routing instructions are the switchboard's classifier
            # context. No other staffer or domain butler uses this context injection.
            routing_ctx: str | None = None
            if self._config.name == "switchboard":
                routing_ctx = await fetch_routing_instructions(self._pool, self._config.name)

            memory_ctx: str | None = None
            memory_enabled = _memory_module_enabled(self._config)
            if memory_enabled:
                memory_ctx = await fetch_memory_context(
                    self._pool,
                    self._config.name,
                    final_prompt,
                    token_budget=_memory_context_token_budget(self._config),
                )
            system_prompt = _compose_system_prompt(
                system_prompt,
                memory_ctx,
                general_timezone_instruction=general_timezone_instruction,
                routing_instructions=routing_ctx,
                context_preamble=context_preamble_ctx,
            )

            # Build credential env.
            # Caller-supplied env_override replaces the default env entirely (used by
            # the QA dispatcher to pass a sandboxed environment).
            # Healing sessions get a minimal env: only PATH + GH_TOKEN for PR creation.
            # No butler-specific credentials are passed to the isolated healing agent.
            if env_override is not None:
                env: dict[str, str] = dict(env_override)
            elif trigger_source == "healing":
                env = {}
                host_path = os.environ.get("PATH")
                if host_path:
                    env["PATH"] = host_path
                # Resolve GH_TOKEN for PR creation via credential store or env fallback
                gh_token_key = "GH_TOKEN"
                if self._credential_store is not None:
                    gh_token_value = await self._credential_store.resolve(gh_token_key)
                else:
                    gh_token_value = os.environ.get(gh_token_key)
                if gh_token_value:
                    env[gh_token_key] = gh_token_value
                # Include traceparent for distributed tracing continuity
                env.update(get_traceparent_env())
            else:
                env = await _build_env(
                    self._config, self._module_credentials_env, self._credential_store
                )

            # Build MCP server config for the adapter.
            # Healing sessions use a minimal env (PATH + GH_TOKEN only) and no MCP servers.
            # QA sessions use a sandboxed env supplied via env_override by the QA dispatcher
            # (env isolation is the caller's responsibility, not enforced here) and also
            # receive no MCP servers — both to prevent access to live production state and
            # to avoid the Codex adapter's MCP-discovery retry path, which re-runs the full
            # subprocess when zero MCP tool calls are made (QA workflows are bash/git-only).
            if trigger_source in ("healing", "qa"):
                mcp_servers: dict[str, Any] = {}
            else:
                mcp_url = runtime_mcp_url(self._config.port)
                mcp_url = _append_runtime_session_query(
                    mcp_url,
                    runtime_session_id,
                    trigger_source=trigger_source,
                )
                mcp_servers = {
                    self._config.name: {
                        "url": mcp_url,
                    },
                }

            await self._ensure_mcp_endpoints_warmed(mcp_servers)

            # ---------------------------------------------------------------------------
            # Same-tier failover: runtime-failure retry loop
            #
            # One logical session may attempt multiple same-tier catalog candidates.
            # Loop invariant: (model, catalog_entry_id, runtime, merged_args,
            # catalog_timeout_s) are updated on each failover transition.
            # The session row (session_id) is created ONCE before this loop and
            # represents the final outcome. If a fallback model succeeds, the session
            # row's model field is updated to reflect the model that actually ran.
            # ---------------------------------------------------------------------------
            # Hard cap on attempts as a defensive backstop against unbounded looping.
            _MAX_FAILOVER_ATTEMPTS = 10
            _attempt_count = 0

            while True:
                _attempt_count += 1

                # Build per-attempt invoke kwargs using current (possibly updated) model.
                runtime_invoked = True
                invoke_kwargs: dict[str, Any] = {
                    "prompt": final_prompt,
                    "system_prompt": system_prompt,
                    "mcp_servers": mcp_servers,
                    "env": env,
                    "max_turns": max_turns,
                    "model": model,
                    "cwd": cwd if cwd is not None else str(self._config_dir),
                }
                if merged_args:
                    invoke_kwargs["runtime_args"] = merged_args
                if timeout_override is not None:
                    timeout_s = timeout_override
                elif catalog_timeout_s is not None:
                    timeout_s = catalog_timeout_s
                else:
                    timeout_s = _FALLBACK_SESSION_TIMEOUT_S
                invoke_kwargs["timeout"] = timeout_s
                merged_runtime_capture = False

                _attempt_exc: BaseException | None = None
                _attempt_tool_calls: list[dict[str, Any]] = []

                try:
                    invoke_task = asyncio.create_task(runtime.invoke(**invoke_kwargs))
                    try:
                        done, _pending = await asyncio.wait(
                            {invoke_task},
                            timeout=_runtime_timeout_guard_s(float(timeout_s)),
                        )
                        if not done:
                            invoke_task.cancel()
                            try:
                                await asyncio.wait_for(
                                    invoke_task, timeout=_RUNTIME_TIMEOUT_MIN_CLEANUP_GRACE_S
                                )
                            except (TimeoutError, asyncio.CancelledError):
                                pass
                            except Exception:
                                logger.debug(
                                    "Runtime task raised while handling spawner timeout "
                                    "for butler=%s",
                                    self._config.name,
                                    exc_info=True,
                                )
                            raise TimeoutError(
                                f"Session timed out after {timeout_s}s "
                                f"(model={model}, butler={self._config.name})"
                            )
                        result_text, tool_calls, usage = await invoke_task
                    finally:
                        if not invoke_task.done():
                            invoke_task.cancel()
                            try:
                                await invoke_task
                            except (asyncio.CancelledError, Exception):
                                pass
                except MCPToolDiscoveryError as exc:
                    executed_tool_calls = (
                        consume_runtime_session_tool_calls(runtime_session_id)
                        if runtime_session_id
                        else []
                    )
                    # Always record the consumed buffer so the failure path (below)
                    # can persist these calls instead of re-consuming nothing.
                    preconsumed_runtime_tool_calls = list(executed_tool_calls)
                    merged_tool_calls = _merge_tool_call_records(
                        exc.tool_calls,
                        executed_tool_calls,
                        butler_name=self._config.name,
                    )
                    if not _has_non_command_tool_calls(merged_tool_calls):
                        # No confirmed MCP calls — eligible for failover classification.
                        _attempt_exc = exc
                        _attempt_tool_calls = list(preconsumed_runtime_tool_calls)
                        # Reset preconsumed so the outer except handler doesn't
                        # double-use them if we fall through without a raise.
                        preconsumed_runtime_tool_calls = None
                    else:
                        logger.warning(
                            "Recovered Codex MCP discovery false-negative for "
                            "butler=%s session=%s; runtime capture confirmed %d MCP "
                            "tool call(s)",
                            self._config.name,
                            session_id,
                            len(
                                [
                                    c
                                    for c in merged_tool_calls
                                    if c.get("name") != "command_execution"
                                ]
                            ),
                        )
                        result_text, tool_calls, usage = (
                            exc.result_text,
                            merged_tool_calls,
                            exc.usage,
                        )
                        merged_runtime_capture = True
                        proc_info = runtime.last_process_info
                        if proc_info is not None:
                            if exc.last_attempt_process_info:
                                proc_info.update(exc.last_attempt_process_info)
                            proc_info["mcp_connection_failed"] = False
                            proc_info["retry_succeeded"] = True
                            proc_info["result_source"] = "runtime_capture"
                except Exception as attempt_exc:
                    # Capture the failure for classification below.
                    _attempt_exc = attempt_exc
                    # Collect tool calls captured before the failure.
                    if preconsumed_runtime_tool_calls is not None:
                        _attempt_tool_calls = list(preconsumed_runtime_tool_calls)
                        preconsumed_runtime_tool_calls = None
                    elif runtime_session_id:
                        _attempt_tool_calls = consume_runtime_session_tool_calls(runtime_session_id)

                # ------------------------------------------------------------------
                # Handle the attempt outcome.
                # ------------------------------------------------------------------
                if _attempt_exc is None:
                    # Invocation succeeded — exit the failover loop.
                    break

                # Invocation failed: classify for failover eligibility.
                _failover_decision = classify_failover_eligibility(
                    FailoverContext(
                        exception=_attempt_exc,
                        tool_calls=_attempt_tool_calls,
                        process_info=runtime.last_process_info,
                    )
                )

                if not _failover_decision.eligible:
                    # Failover suppressed — emit metric and re-raise to the outer handler.
                    self._metrics.record_failover_suppressed(reason=_failover_decision.reason)
                    logger.debug(
                        "Failover suppressed for butler=%s: %s",
                        self._config.name,
                        _failover_decision.reason,
                    )
                    # Record suppression provenance (best-effort).
                    if self._pool is not None and catalog_entry_id is not None:
                        await _write_dispatch_attempt(
                            self._pool,
                            catalog_entry_id=catalog_entry_id,
                            butler=self._config.name,
                            outcome="suppressed",
                            attempt_index=len(_attempted_ids),
                            session_id=session_id,
                            failure_reason=_failover_decision.reason,
                            error_code=type(_attempt_exc).__name__,
                            error_message=str(_attempt_exc),
                            tool_call_count=len(_attempt_tool_calls),
                            logical_session_id=effective_request_id,
                        )
                    # Mark as already classified so the outer except handler does not
                    # double-emit the suppressed metric for this exception.
                    _failover_already_classified = True
                    # Restore tool calls for the outer except block.
                    preconsumed_runtime_tool_calls = _attempt_tool_calls
                    raise _attempt_exc

                # Failover eligible — record runtime_failure provenance for the
                # attempt that just failed before advancing to the next candidate.
                _failed_catalog_entry_id = catalog_entry_id
                _failed_attempt_index = len(_attempted_ids)
                if self._pool is not None and _failed_catalog_entry_id is not None:
                    await _write_dispatch_attempt(
                        self._pool,
                        catalog_entry_id=_failed_catalog_entry_id,
                        butler=self._config.name,
                        outcome="runtime_failure",
                        attempt_index=_failed_attempt_index,
                        session_id=session_id,
                        failure_reason=_failover_decision.reason,
                        error_code=type(_attempt_exc).__name__,
                        error_message=str(_attempt_exc),
                        tool_call_count=len(_attempt_tool_calls),
                        logical_session_id=effective_request_id,
                    )

                # Attempt next same-tier candidate.
                if catalog_entry_id is not None:
                    _attempted_ids.append(catalog_entry_id)

                if (
                    _failover_effective_tier is None
                    or self._pool is None
                    or _attempt_count >= _MAX_FAILOVER_ATTEMPTS
                ):
                    # No tier pinned (static_fallback), no pool, or safety cap reached.
                    preconsumed_runtime_tool_calls = _attempt_tool_calls
                    raise _attempt_exc

                next_candidate = await next_same_tier_candidate(
                    self._pool,
                    self._config.name,
                    _failover_effective_tier,
                    _attempted_ids,
                )
                if next_candidate is None:
                    # All same-tier candidates exhausted — terminal failure.
                    self._metrics.record_failover_exhausted(tier=_failover_effective_tier)
                    logger.warning(
                        "All same-tier candidates exhausted for butler=%s tier=%s "
                        "after %d attempt(s)",
                        self._config.name,
                        _failover_effective_tier,
                        _attempt_count,
                    )
                    # The runtime_failure row for the last attempt was already written above.
                    preconsumed_runtime_tool_calls = _attempt_tool_calls
                    raise _attempt_exc

                next_rt, next_model, next_extra_args, next_entry_id, next_timeout_s = next_candidate
                self._metrics.record_failover_attempt(
                    from_model=model,
                    to_model=next_model,
                    reason=_failover_decision.reason.split(":")[0],
                )
                logger.info(
                    "Same-tier failover for butler=%s: %s → %s (tier=%s, reason=%s)",
                    self._config.name,
                    model,
                    next_model,
                    _failover_effective_tier,
                    _failover_decision.reason,
                )

                # Update candidate variables for the next attempt.
                resolved_runtime_type = next_rt
                model = next_model
                merged_args = list(next_extra_args)
                catalog_entry_id = next_entry_id
                catalog_timeout_s = next_timeout_s

                # Re-create the runtime adapter for the new model's runtime type.
                next_provider_config = await self._resolve_provider_config(model)
                try:
                    runtime = self._get_or_create_adapter(
                        resolved_runtime_type, next_provider_config
                    ).create_worker()
                except ValueError:
                    logger.warning(
                        "Failover candidate resolved unregistered runtime_type=%s for "
                        "butler=%s; falling back to default runtime_type=%s",
                        resolved_runtime_type,
                        self._config.name,
                        fallback_runtime_type,
                    )
                    resolved_runtime_type = fallback_runtime_type
                    model = fallback_model
                    merged_args = []
                    catalog_timeout_s = None
                    resolution_source = "static_fallback"
                    runtime = self._get_or_create_adapter(fallback_runtime_type).create_worker()
                # Loop back to try the next candidate.

            # End of failover loop — invocation succeeded.

            if runtime_session_id and not merged_runtime_capture:
                executed_tool_calls = consume_runtime_session_tool_calls(runtime_session_id)
                tool_calls = _merge_tool_call_records(
                    tool_calls,
                    executed_tool_calls,
                    butler_name=self._config.name,
                )

            duration_ms = int((time.monotonic() - t0) * 1000)

            # Extract token counts from usage dict (if provided by adapter)
            input_tokens: int | None = None
            output_tokens: int | None = None
            if usage:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                # Capture immediately for ledger recording in finally block; ensures
                # the ledger receives token data even if post-invoke processing fails.
                if input_tokens is not None:
                    _ledger_input_tokens = input_tokens
                    _ledger_output_tokens = output_tokens or 0

            # ------------------------------------------------------------------
            # Guardrail checks — run after tool-call merge and token extraction.
            # These conditions indicate intentional session termination and must
            # SUPPRESS same-tier failover (the failover classifier recognises the
            # marker strings in the RuntimeError message and returns eligible=False).
            #
            # Raise order: degenerate loop → tool-call budget → token budget.
            # When raised, ``preconsumed_runtime_tool_calls`` is set so the outer
            # except handler records the tool calls that actually ran.
            # ------------------------------------------------------------------
            _guardrail_reason: str | None = (
                _check_degenerate_tool_loop(tool_calls)
                or _check_tool_call_budget(tool_calls, max_tool_calls=max_tool_calls)
                or _check_token_budget(input_tokens, max_token_budget=max_token_budget)
            )
            if _guardrail_reason is not None:
                # Preserve tool calls so the except handler can persist them.
                preconsumed_runtime_tool_calls = list(tool_calls)
                logger.warning(
                    "Guardrail triggered for butler=%s session=%s: %s",
                    self._config.name,
                    session_id,
                    _guardrail_reason,
                )
                raise RuntimeError(_guardrail_reason)

            spawner_result = SpawnerResult(
                output=result_text,
                success=True,
                tool_calls=tool_calls,
                duration_ms=duration_ms,
                model=model,
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            # Log session completion
            if self._pool is not None and session_id is not None:
                await session_complete(
                    self._pool,
                    session_id,
                    output=result_text,
                    tool_calls=tool_calls,
                    duration_ms=duration_ms,
                    success=True,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                # When failover occurred, the session row was created with the initial
                # model but the actual invocation succeeded on a fallback model.
                # Update the model field so the persisted row reflects the model that ran.
                if _attempted_ids and model is not None:
                    try:
                        await self._pool.execute(
                            "UPDATE sessions SET model = $2 WHERE id = $1",
                            session_id,
                            model,
                        )
                    except Exception:
                        logger.debug(
                            "Failed to update session model after failover for session %s",
                            session_id,
                            exc_info=True,
                        )

                # Record successful fallback attempt provenance when failover occurred.
                # Only written when _attempted_ids is non-empty (meaning at least one
                # prior attempt failed or was skipped before this success).
                if _attempted_ids and self._pool is not None and catalog_entry_id is not None:
                    await _write_dispatch_attempt(
                        self._pool,
                        catalog_entry_id=catalog_entry_id,
                        butler=self._config.name,
                        outcome="success",
                        attempt_index=len(_attempted_ids),
                        session_id=session_id,
                        tool_call_count=len(tool_calls) if tool_calls else 0,
                        logical_session_id=effective_request_id,
                    )

                # Write process-level diagnostics (best-effort, never blocks result)
                proc_info = runtime.last_process_info
                if proc_info is not None:
                    try:
                        await session_process_log_write(
                            self._pool,
                            session_id,
                            pid=proc_info.get("pid"),
                            exit_code=proc_info.get("exit_code"),
                            command=proc_info.get("command"),
                            stderr=proc_info.get("stderr"),
                            runtime_type=proc_info.get("runtime_type"),
                            retry_attempted=proc_info.get("retry_attempted"),
                            retry_succeeded=proc_info.get("retry_succeeded"),
                            result_source=proc_info.get("result_source"),
                            attempt_count=proc_info.get("attempt_count"),
                        )
                    except Exception:
                        logger.debug(
                            "Failed to write process log for session %s",
                            session_id,
                            exc_info=True,
                        )

            # Write daemon-side audit log entry
            await write_audit_entry(
                self._audit_pool,
                self._config.name,
                "session",
                {
                    "session_id": str(session_id) if session_id else None,
                    "trigger_source": trigger_source,
                    "prompt": final_prompt[:200],
                    "duration_ms": duration_ms,
                    "tool_calls_count": len(tool_calls),
                    "model": model,
                    "runtime_type": resolved_runtime_type,
                    "complexity": str(complexity),
                    "resolution_source": resolution_source,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "max_token_budget": max_token_budget,
                },
            )

            # Emit egress audit entry for the outbound LLM API call.
            await write_audit_entry(
                self._audit_pool,
                self._config.name,
                "llm_api_call",
                {
                    "provider": _derive_llm_provider(model),
                    "model": model,
                    "session_id": str(session_id) if session_id else None,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )

            # Store episode via module-local memory tools (failure doesn't block)
            if memory_enabled and spawner_result.success and spawner_result.output:
                await store_session_episode(
                    self._pool,
                    self._config.name,
                    spawner_result.output,
                    session_id=session_id,
                )

            return spawner_result

        except Exception as exc:
            # Capture exc_info FIRST — before any cleanup code runs.
            # The traceback object is live only until cleanup clears the frame.
            _exc_type, _exc_value, _exc_tb = sys.exc_info()

            # Collect any tool calls captured before the failure (best-effort).
            # consume rather than discard so we preserve what ran before the error.
            # If the MCP-discovery recovery path already consumed the buffer to
            # decide whether to recover, reuse those — re-consuming here would
            # see an empty buffer and silently drop the tool-call history.
            captured_on_failure: list[dict[str, Any]] = []
            if preconsumed_runtime_tool_calls is not None:
                captured_on_failure = list(preconsumed_runtime_tool_calls)
            elif runtime_session_id:
                captured_on_failure = consume_runtime_session_tool_calls(runtime_session_id)
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"

            # Guardrail exceptions raised from the post-invocation success path
            # (degenerate_tool_loop, tool_call_budget_exceeded, token_budget_exceeded)
            # bypass the failover loop and arrive here unclassified. Classify them
            # so the suppressed metric is emitted and the outcome is observable.
            # Skip when the failover loop already classified and emitted for this exc.
            if not _failover_already_classified:
                _outer_failover_decision = classify_failover_eligibility(
                    FailoverContext(
                        exception=exc,
                        tool_calls=captured_on_failure,
                        process_info=runtime.last_process_info if runtime_invoked else None,
                    )
                )
                if not _outer_failover_decision.eligible:
                    self._metrics.record_failover_suppressed(reason=_outer_failover_decision.reason)
                    logger.debug(
                        "Failover suppressed for butler=%s (outer except): %s",
                        self._config.name,
                        _outer_failover_decision.reason,
                    )

            logger.error(
                "Runtime invocation failed: %s",
                error_msg,
                exc_info=True,
                extra={
                    "butler_name": self._config.name,
                    "trigger_source": trigger_source,
                    "model": model,
                    "timeout_s": timeout_s if "timeout_s" in locals() else None,
                },
            )

            # Record exception on span
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)

            spawner_result = SpawnerResult(
                error=error_msg,
                success=False,
                duration_ms=duration_ms,
                model=model,
                session_id=session_id,
            )

            # Log failed session — persist any captured tool calls so operators
            # can see what ran before the failure rather than always writing [].
            if self._pool is not None and session_id is not None:
                await session_complete(
                    self._pool,
                    session_id,
                    output=None,
                    tool_calls=captured_on_failure,
                    duration_ms=duration_ms,
                    success=False,
                    error=error_msg,
                )

            # Record dispatch failure in public.dispatch_failures (best-effort).
            # Gated only on pool and catalog_entry_id — session_id is nullable so
            # early-stage failures (e.g. session_create raising) are still tracked.
            # TOML-fallback dispatches have no catalog_entry_id and are not tracked.
            if self._pool is not None and catalog_entry_id is not None:
                try:
                    _error_code = type(exc).__name__
                    _error_message = error_msg[:4096] if error_msg else None
                    await self._pool.execute(
                        """
                        INSERT INTO public.dispatch_failures
                            (catalog_entry_id, error_code, error_message, butler, session_id)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        catalog_entry_id,
                        _error_code,
                        _error_message,
                        self._config.name,
                        session_id,
                    )
                except Exception:
                    logger.debug(
                        "Failed to record dispatch failure for catalog_entry_id=%s session=%s",
                        catalog_entry_id,
                        session_id,
                        exc_info=True,
                    )

            if self._pool is not None and session_id is not None:
                # Write process-level diagnostics (best-effort)
                proc_info = runtime.last_process_info
                if proc_info is not None:
                    try:
                        await session_process_log_write(
                            self._pool,
                            session_id,
                            pid=proc_info.get("pid"),
                            exit_code=proc_info.get("exit_code"),
                            command=proc_info.get("command"),
                            stderr=proc_info.get("stderr"),
                            runtime_type=proc_info.get("runtime_type"),
                            retry_attempted=proc_info.get("retry_attempted"),
                            retry_succeeded=proc_info.get("retry_succeeded"),
                            result_source=proc_info.get("result_source"),
                            attempt_count=proc_info.get("attempt_count"),
                        )
                    except Exception:
                        logger.debug(
                            "Failed to write process log for session %s",
                            session_id,
                            exc_info=True,
                        )

            # Runtime failures can leave provider/client context dirty.
            # Best-effort reset keeps subsequent sessions isolated.
            if runtime_invoked:
                try:
                    await runtime.reset()
                except Exception:
                    logger.warning(
                        "Runtime reset failed after invocation error for butler %s",
                        self._config.name,
                        exc_info=True,
                    )

            # Write daemon-side audit log entry (error)
            await write_audit_entry(
                self._audit_pool,
                self._config.name,
                "session",
                {
                    "session_id": str(session_id) if session_id else None,
                    "trigger_source": trigger_source,
                    "prompt": final_prompt[:200],
                    "duration_ms": duration_ms,
                    "model": model,
                    "runtime_type": resolved_runtime_type,
                    "complexity": str(complexity),
                    "resolution_source": resolution_source,
                },
                result="error",
                error=error_msg,
            )

            # Emit egress audit entry for the attempted LLM API call (error path).
            await write_audit_entry(
                self._audit_pool,
                self._config.name,
                "llm_api_call",
                {
                    "provider": _derive_llm_provider(model),
                    "model": model,
                    "session_id": str(session_id) if session_id else None,
                },
                result="error",
                error=error_msg,
            )

            # Self-healing spawner fallback — secondary path for hard crashes.
            # Fires only when:
            #   1. trigger_source != "healing" (no recursive healing)
            #   2. The self-healing module is loaded and wired
            #   3. We have a DB pool and a valid session_id
            if (
                trigger_source != "healing"
                and self._healing_module is not None
                and self._pool is not None
                and session_id is not None
                and _exc_value is not None
            ):
                try:
                    from butlers.core.healing import HealingConfig, dispatch_healing

                    _healing_cfg_dict = {}
                    _healing_cfg = getattr(self._healing_module, "_config", None)
                    if _healing_cfg is not None and hasattr(_healing_cfg, "model_dump"):
                        _healing_cfg_dict = _healing_cfg.model_dump()
                    healing_config = HealingConfig.from_module_config(_healing_cfg_dict)

                    # Resolve repo_root from the healing module if wired
                    _repo_root = getattr(self._healing_module, "_repo_root", Path("."))

                    # Resolve GH_TOKEN for PR creation
                    _gh_token: str | None = None
                    if self._credential_store is not None:
                        try:
                            _gh_token = await self._credential_store.resolve("GH_TOKEN")
                        except Exception as _cred_exc:
                            logger.debug(
                                "Failed to resolve %s from credential store: %s",
                                "GH_TOKEN",
                                _cred_exc,
                            )
                    if _gh_token is None:
                        _gh_token = os.environ.get("GH_TOKEN")

                    _task_registry: list[asyncio.Task] | None = None
                    if self._healing_module is not None and hasattr(
                        self._healing_module, "_watchdog_tasks"
                    ):
                        _task_registry = self._healing_module._watchdog_tasks

                    _fallback_task = asyncio.create_task(
                        dispatch_healing(
                            pool=self._pool,
                            butler_name=self._config.name,
                            session_id=session_id,
                            fingerprint_input=(_exc_value, _exc_tb),
                            config=healing_config,
                            repo_root=_repo_root,
                            spawner=self,
                            agent_context=None,  # Hard crash — no agent context
                            trigger_source=trigger_source,
                            gh_token=_gh_token,
                            task_registry=_task_registry,
                            metrics=self._metrics,
                        ),
                        name=f"healing-fallback-{session_id}",
                    )

                    def _log_fallback_error(t: asyncio.Task) -> None:
                        if not t.cancelled() and t.exception() is not None:
                            logger.warning(
                                "Self-healing fallback task failed (session=%s): %s",
                                session_id,
                                t.exception(),
                            )

                    _fallback_task.add_done_callback(_log_fallback_error)

                except Exception:
                    logger.warning(
                        "Failed to schedule self-healing fallback for session %s",
                        session_id,
                        exc_info=True,
                    )

            return spawner_result

        finally:
            if runtime_session_id:
                clear_runtime_session_routing_context(runtime_session_id)
            # Record session duration metric using wall-clock time from t0
            self._metrics.record_session_duration(int((time.monotonic() - t0) * 1000))
            # Record token usage when available (success path only; model always set)
            if spawner_result is not None and spawner_result.input_tokens is not None:
                self._metrics.record_token_usage(
                    input_tokens=spawner_result.input_tokens,
                    output_tokens=spawner_result.output_tokens or 0,
                    model=spawner_result.model or "unknown",
                    butler=self._config.name,
                )
            # Record token usage to ledger for both successful and failed sessions.
            # Uses _ledger_input_tokens set as soon as the adapter reports usage,
            # so the ledger receives token data even when post-invoke processing
            # fails (e.g. session_complete raises). Tokens are consumed by the
            # upstream provider on invocation regardless of session outcome.
            if (
                _ledger_input_tokens is not None
                and catalog_entry_id is not None
                and self._pool is not None
            ):
                await record_token_usage(
                    self._pool,
                    catalog_entry_id=catalog_entry_id,
                    butler_name=self._config.name,
                    session_id=session_id,
                    input_tokens=_ledger_input_tokens,
                    output_tokens=_ledger_output_tokens or 0,
                )
            # Emit per-call cost event to the live WS spend stream.
            # Uses the same token counts as the DB ledger (best-effort early capture).
            # Lazy import avoids a circular dependency: core → api.
            if _ledger_input_tokens is not None:
                try:
                    from butlers.api.pricing import estimate_session_cost, load_pricing
                    from butlers.api.routers.spend import emit_spend_event

                    # Cache pricing config at module level so pricing.toml is not
                    # read from disk on every session close (hot path).
                    global _cached_pricing
                    if _cached_pricing is None:
                        _cached_pricing = load_pricing()
                    _pricing = _cached_pricing
                    _cost_usd = estimate_session_cost(
                        _pricing,
                        model or "unknown",
                        _ledger_input_tokens,
                        _ledger_output_tokens or 0,
                    )
                    emit_spend_event(
                        {
                            "kind": "call",
                            "ts": time.time(),
                            "butler": self._config.name,
                            "model": model or "unknown",
                            "tokens_in": _ledger_input_tokens,
                            "tokens_out": _ledger_output_tokens or 0,
                            "cost_usd": _cost_usd,
                            "session_id": str(session_id) if session_id else "",
                            "extra": {},
                        }
                    )
                except Exception:
                    logger.debug(
                        "emit_spend_event failed for session=%s butler=%s (non-fatal)",
                        session_id,
                        self._config.name,
                        exc_info=True,
                    )
            # Clear session context before ending span so tool handlers
            # arriving after this point don't attach to a finished span.
            clear_active_session_context()
            # End span and detach context
            span.end()
            trace.context_api.detach(token)
