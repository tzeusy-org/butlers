"""Spawner — invokes ephemeral AI runtime instances for a butler.

The spawner is responsible for:
1. Generating a locked-down MCP config pointing exclusively at this butler
2. Invoking a runtime adapter (e.g. Claude Code) with that config
3. Passing only declared credentials to the runtime environment
4. Reading the butler's system prompt via the adapter
5. Enforcing serial dispatch (one instance at a time per butler)
6. Logging sessions before and after invocation
7. Passing the configured model to the SDK when set
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
from butlers.core.logging import resolve_log_root
from butlers.core.mcp_urls import runtime_mcp_url
from butlers.core.metrics import ButlerMetrics
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.sessions import session_complete, session_create
from butlers.core.skills import read_system_prompt
from butlers.core.telemetry import (
    clear_active_session_context,
    get_traceparent_env,
    set_active_session_context,
    tag_butler_span,
)
from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    consume_runtime_session_tool_calls,
    discard_runtime_session_tool_calls,
    ensure_runtime_session_capture,
    set_runtime_session_routing_context,
)
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_MEMORY_TABLE_NAMES = ("episodes", "facts", "rules", "memory_links", "memory_events")
_missing_memory_table_warnings: set[tuple[str, str]] = set()
_GLOBAL_CREDENTIAL_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _is_missing_memory_table_error(exc: Exception) -> bool:
    """Return whether an exception indicates missing memory module tables."""
    if exc.__class__.__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    if "relation" not in msg or "does not exist" not in msg:
        return False
    return any(table in msg for table in _MEMORY_TABLE_NAMES)


def _log_missing_memory_table_once(*, butler_name: str, operation: str) -> None:
    """Log missing-memory-schema warning once per butler+operation."""
    warning_key = (butler_name, operation)
    if warning_key in _missing_memory_table_warnings:
        logger.debug(
            "Skipping memory %s for butler %s; memory tables are still missing",
            operation,
            butler_name,
        )
        return

    _missing_memory_table_warnings.add(warning_key)
    logger.warning(
        "Skipping memory %s for butler %s because memory tables are missing. "
        "Run migrations or disable [modules.memory].",
        operation,
        butler_name,
    )


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


def _append_runtime_session_query(url: str, runtime_session_id: str | None) -> str:
    """Append runtime_session_id query param to MCP URL when available."""
    if not runtime_session_id:
        return url

    parsed = urlsplit(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_items.append(("runtime_session_id", runtime_session_id))
    new_query = urlencode(query_items)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _merge_tool_call_records(
    parsed_calls: list[dict[str, Any]],
    executed_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge parser + executed call records while preserving retry attempts."""
    if not executed_calls:
        return list(parsed_calls)
    if not parsed_calls:
        return list(executed_calls)

    def _payload_for_signature(call: dict[str, Any]) -> Any:
        payload = call.get("input")
        if payload is None:
            payload = call.get("args")
        if payload is None:
            payload = call.get("arguments")
        if payload is None:
            payload = call.get("parameters")
        if isinstance(payload, str):
            stripped = payload.strip()
            if stripped:
                try:
                    return json.loads(stripped)
                except Exception:
                    return payload
        return payload

    def _signature(call: dict[str, Any]) -> str:
        name = str(call.get("name", "") or "")
        payload = _payload_for_signature(call)
        return f"{name}|{json.dumps(payload, sort_keys=True, default=str)}"

    merged: list[dict[str, Any]] = []
    matched_parsed_indexes: set[int] = set()

    for executed_call in executed_calls:
        executed_signature = _signature(executed_call)
        parsed_index = next(
            (
                idx
                for idx, parsed_call in enumerate(parsed_calls)
                if (
                    idx not in matched_parsed_indexes
                    and _signature(parsed_call) == executed_signature
                )
            ),
            None,
        )
        if parsed_index is None:
            merged.append(executed_call)
            continue
        matched_parsed_indexes.add(parsed_index)
        merged_record = dict(parsed_calls[parsed_index])
        merged_record.update(executed_call)
        merged.append(merged_record)

    for idx, parsed_call in enumerate(parsed_calls):
        if idx in matched_parsed_indexes:
            continue
        merged.append(parsed_call)

    return merged


def _compose_system_prompt(
    base_system_prompt: str,
    memory_context: str | None,
    routing_instructions: str | None = None,
) -> str:
    """Compose the runtime system prompt from base instructions, routing instructions, and memory.

    Layering order (stable for token-cache efficiency):
    1. Base system prompt (CLAUDE.md — static)
    2. Owner routing instructions (semi-static, sorted by priority)
    3. Memory context (dynamic per-request)

    Contract:
    - Runtime always receives the raw CLAUDE.md-derived system prompt when no
      additional context is available.
    - Each layer is appended as a suffix separated from the previous by exactly
      one blank line.
    """
    prompt = base_system_prompt
    if routing_instructions:
        prompt = f"{prompt}\n\n{routing_instructions}"
    if memory_context:
        prompt = f"{prompt}\n\n{memory_context}"
    return prompt


def _capture_pipeline_routing_context() -> dict[str, Any] | None:
    """Best-effort capture of switchboard routing context when available."""
    try:
        from butlers.modules.pipeline import _routing_ctx_var
    except Exception:
        return None

    payload = _routing_ctx_var.get()
    if not isinstance(payload, dict) or not payload:
        return None
    return dict(payload)


async def _build_env(
    config: ButlerConfig,
    module_credentials_env: dict[str, list[str]] | None = None,
    credential_store: CredentialStore | None = None,
) -> dict[str, str]:
    """Build an explicit env dict for the runtime instance.

    Includes a minimal runtime baseline (`PATH`) plus declared credentials.
    This keeps runtime shebang resolution (for example ``#!/usr/bin/env node``)
    working in spawned subprocesses without requiring machine-specific paths.

    Other than `PATH`, only declared variables are included — undeclared env
    vars do not leak through.
    Always includes core API keys (ANTHROPIC/OPENAI), plus butler-level
    required/optional vars and module credential vars.

    When *credential_store* is provided, credentials are resolved DB-first
    with automatic env-var fallback via ``CredentialStore.resolve()``.
    When no store is provided (e.g. in unit tests without a DB pool),
    resolution falls back directly to ``os.environ``.
    """
    env: dict[str, str] = {}

    # Runtime baseline needed for CLI shebang resolution (e.g. /usr/bin/env node).
    host_path = os.environ.get("PATH")
    if host_path:
        env["PATH"] = host_path

    async def _resolve(key: str) -> str | None:
        """Resolve a credential key: DB-first when store available, else env."""
        if credential_store is not None:
            return await credential_store.resolve(key)
        return os.environ.get(key) or None

    # Always include core API keys (DB-first, env fallback).
    for key in _GLOBAL_CREDENTIAL_KEYS:
        value = await _resolve(key)
        if value:
            env[key] = value

    # Butler-level required + optional env vars
    for var in config.env_required + config.env_optional:
        value = await _resolve(var)
        if value is not None:
            env[var] = value

    # Module credentials (DB-first passthrough to spawned instances)
    if module_credentials_env:
        for _module_name, cred_vars in module_credentials_env.items():
            for var in cred_vars:
                value = await _resolve(var)
                if value is not None:
                    env[var] = value

    # Include traceparent for distributed tracing
    env.update(get_traceparent_env())

    return env


def _memory_module_enabled(config: ButlerConfig) -> bool:
    raw = config.modules.get("memory")
    return isinstance(raw, dict)


def _memory_context_token_budget(config: ButlerConfig) -> int:
    raw = config.modules.get("memory", {})
    if not isinstance(raw, dict):
        return 3000
    retrieval = raw.get("retrieval", {})
    if not isinstance(retrieval, dict):
        return 3000
    budget = retrieval.get("context_token_budget", 3000)
    try:
        parsed = int(budget)
    except (TypeError, ValueError):
        return 3000
    return parsed if parsed > 0 else 3000


async def _reset_memory_client_cache_for_tests() -> None:
    """Compatibility no-op for tests from legacy shared-memory architecture."""
    return None


async def fetch_memory_context(
    pool: asyncpg.Pool | None,
    butler_name: str,
    prompt: str,
    *,
    token_budget: int = 3000,
) -> str | None:
    """Fetch memory context via local memory tools when the module is enabled."""
    if pool is None:
        return None

    try:
        from butlers.modules.memory.tools import context as _context
        from butlers.modules.memory.tools._helpers import get_embedding_engine

        context = await _context.memory_context(
            pool,
            get_embedding_engine(),
            prompt,
            butler_name,
            token_budget=token_budget,
        )
        if isinstance(context, str) and context.strip():
            return context
        return None
    except Exception as exc:
        if _is_missing_memory_table_error(exc):
            _log_missing_memory_table_once(butler_name=butler_name, operation="context fetch")
            return None
        logger.warning(
            "Failed to fetch memory context for butler %s",
            butler_name,
            exc_info=True,
        )
        return None


_ROUTING_INSTRUCTIONS_TABLE = "routing_instructions"
_missing_routing_instructions_warnings: set[str] = set()


async def fetch_routing_instructions(
    pool: asyncpg.Pool | None,
    butler_name: str,
) -> str | None:
    """Fetch enabled routing instructions and format as a system prompt section.

    Returns a markdown section string ready for injection, or ``None`` when
    there are no instructions or the table doesn't exist.

    Instructions are sorted by ``(priority ASC, created_at ASC)`` for
    deterministic ordering that maximises token-cache hit rates.
    """
    if pool is None:
        return None

    try:
        rows = await pool.fetch(
            "SELECT instruction FROM routing_instructions"
            " WHERE enabled = TRUE AND deleted_at IS NULL"
            " ORDER BY priority ASC, created_at ASC, id ASC"
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg and "routing_instructions" in msg:
            if butler_name not in _missing_routing_instructions_warnings:
                _missing_routing_instructions_warnings.add(butler_name)
                logger.debug(
                    "routing_instructions table not yet created for %s; skipping",
                    butler_name,
                )
            return None
        logger.warning(
            "Failed to fetch routing instructions for %s: %s",
            butler_name,
            exc,
        )
        return None

    if not rows:
        return None

    lines = [f"- {row['instruction']}" for row in rows]
    return (
        "## Owner Routing Instructions\n\n"
        "The following routing directives have been set by the owner."
        " Follow these exactly when classifying and routing messages:\n\n" + "\n".join(lines)
    )


async def store_session_episode(
    pool: asyncpg.Pool | None,
    butler_name: str,
    session_output: str,
    session_id: uuid.UUID | None = None,
) -> bool:
    """Store a session episode through local memory module tools."""
    if pool is None:
        return False

    try:
        from butlers.modules.memory.tools import writing as _writing

        await _writing.memory_store_episode(
            pool,
            session_output,
            butler_name,
            session_id=str(session_id) if session_id is not None else None,
        )
        return True
    except Exception as exc:
        if _is_missing_memory_table_error(exc):
            _log_missing_memory_table_once(butler_name=butler_name, operation="episode storage")
            return False
        logger.warning(
            "Failed to store session episode for butler %s",
            butler_name,
            exc_info=True,
        )
        return False


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
        A RuntimeAdapter instance to use for invocation. If provided, sdk_query
        is ignored.
    sdk_query:
        DEPRECATED — Callable to use for the actual SDK invocation. Prefer
        passing a RuntimeAdapter via ``runtime``. When neither ``runtime``
        nor ``sdk_query`` is provided, a default ClaudeCodeAdapter is created.
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
        sdk_query: Any = None,
        audit_pool: asyncpg.Pool | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._config = config
        self._config_dir = config_dir
        self._pool = pool
        self._module_credentials_env = module_credentials_env
        self._audit_pool = audit_pool
        self._credential_store = credential_store
        self._session_semaphore = asyncio.Semaphore(config.runtime.max_concurrent_sessions)
        self._max_queued_sessions = config.runtime.max_queued_sessions
        self._accepting = True
        self._in_flight: set[asyncio.Task] = set()
        self._in_flight_event = asyncio.Event()
        self._in_flight_event.set()  # Initially no in-flight sessions
        self._metrics = ButlerMetrics(butler_name=config.name)

        if runtime is not None:
            self._runtime = runtime
        elif sdk_query is not None:
            # Legacy path: wrap sdk_query in a ClaudeCodeAdapter
            from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

            self._runtime = ClaudeCodeAdapter(sdk_query=sdk_query)
        else:
            # Default: create a ClaudeCodeAdapter with the real SDK query
            from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

            log_root = resolve_log_root(config.logging.log_root)
            self._runtime = ClaudeCodeAdapter(
                butler_name=config.name,
                log_root=log_root,
            )

    async def trigger(
        self,
        prompt: str,
        trigger_source: str,
        context: str | None = None,
        max_turns: int = 20,
        parent_context: Context | None = None,
        request_id: str | None = None,
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
                model=self._config.runtime.model,
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
                model=self._config.runtime.model,
            )

        self._in_flight_event.clear()
        task = asyncio.current_task()
        if task is not None:
            self._in_flight.add(task)
        # Track triggers waiting for a semaphore slot
        self._metrics.spawner_queued_triggers_inc()
        _semaphore_acquired = False
        try:
            async with self._session_semaphore:
                # Slot acquired — no longer queued, now active
                _semaphore_acquired = True
                self._metrics.spawner_queued_triggers_dec()
                self._metrics.spawner_active_sessions_inc()
                try:
                    return await self._run(
                        prompt, trigger_source, context, max_turns, parent_context, request_id
                    )
                finally:
                    self._metrics.spawner_active_sessions_dec()
        finally:
            # If cancelled before acquiring the semaphore, queued_triggers_dec
            # was never called inside the async-with block; decrement here to
            # keep the gauge accurate.
            if not _semaphore_acquired:
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

    async def _run(
        self,
        prompt: str,
        trigger_source: str,
        context: str | None = None,
        max_turns: int = 20,
        parent_context: Context | None = None,
        request_id: str | None = None,
    ) -> SpawnerResult:
        """Internal: run the runtime invocation (called under lock)."""
        session_id: uuid.UUID | None = None
        runtime_session_id: str | None = None
        runtime = self._runtime.create_worker()
        runtime_invoked = False
        routing_context = _capture_pipeline_routing_context()

        # Prepend context to prompt if provided
        final_prompt = prompt
        if context:
            final_prompt = f"{context}\n\n{prompt}"

        # Read the configured model (defaults to Haiku if not overridden)
        model = self._config.runtime.model

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

            # Create session record with trace_id and request_id
            if self._pool is not None:
                session_id = await session_create(
                    self._pool,
                    final_prompt,
                    trigger_source,
                    trace_id,
                    model=model,
                    request_id=request_id,
                )
                # Set session_id on span
                span.set_attribute("session_id", str(session_id))
                runtime_session_id = str(session_id)
                ensure_runtime_session_capture(runtime_session_id)
                set_runtime_session_routing_context(runtime_session_id, routing_context)

            # Read system prompt
            system_prompt = read_system_prompt(self._config_dir, self._config.name)

            # Fetch owner routing instructions (switchboard only)
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
                system_prompt, memory_ctx, routing_instructions=routing_ctx
            )

            # Build credential env
            env = await _build_env(
                self._config, self._module_credentials_env, self._credential_store
            )

            # Build MCP server config for the adapter
            mcp_url = runtime_mcp_url(self._config.port)
            mcp_url = _append_runtime_session_query(mcp_url, runtime_session_id)
            mcp_servers: dict[str, Any] = {
                self._config.name: {
                    "url": mcp_url,
                },
            }

            # Invoke via runtime adapter
            runtime_invoked = True
            result_text, tool_calls, usage = await runtime.invoke(
                prompt=final_prompt,
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                env=env,
                max_turns=max_turns,
                model=model,
                cwd=str(self._config_dir),
            )
            if runtime_session_id:
                executed_tool_calls = consume_runtime_session_tool_calls(runtime_session_id)
                tool_calls = _merge_tool_call_records(tool_calls, executed_tool_calls)

            duration_ms = int((time.monotonic() - t0) * 1000)

            # Extract token counts from usage dict (if provided by adapter)
            input_tokens: int | None = None
            output_tokens: int | None = None
            if usage:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")

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
            if runtime_session_id:
                discard_runtime_session_tool_calls(runtime_session_id)
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("Runtime invocation failed: %s", error_msg, exc_info=True)

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

            # Log failed session
            if self._pool is not None and session_id is not None:
                await session_complete(
                    self._pool,
                    session_id,
                    output=None,
                    tool_calls=[],
                    duration_ms=duration_ms,
                    success=False,
                    error=error_msg,
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
                },
                result="error",
                error=error_msg,
            )

            return spawner_result

        finally:
            if runtime_session_id:
                clear_runtime_session_routing_context(runtime_session_id)
            # Record session duration metric using wall-clock time from t0
            self._metrics.record_session_duration(int((time.monotonic() - t0) * 1000))
            # Clear session context before ending span so tool handlers
            # arriving after this point don't attach to a finished span.
            clear_active_session_context()
            # End span and detach context
            span.end()
            trace.context_api.detach(token)


# Backward-compatible alias
CCSpawner = Spawner
