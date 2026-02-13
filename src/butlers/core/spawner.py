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
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
from fastmcp import Client as MCPClient
from opentelemetry import trace

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.sessions import session_complete, session_create
from butlers.core.skills import read_system_prompt
from butlers.core.telemetry import get_traceparent_env

logger = logging.getLogger(__name__)
_MEMORY_CLIENTS: dict[str, tuple[MCPClient, Any]] = {}
_MEMORY_CLIENT_LOCKS: dict[str, asyncio.Lock] = {}


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


def _build_env(
    config: ButlerConfig,
    module_credentials_env: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Build an explicit env dict for the runtime instance.

    Only declared variables are included — no undeclared env vars leak through.
    Always includes ANTHROPIC_API_KEY, plus butler-level required/optional
    vars and module credential vars.
    """
    env: dict[str, str] = {}

    # Always include ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    # Butler-level required + optional env vars
    for var in config.env_required + config.env_optional:
        value = os.environ.get(var)
        if value is not None:
            env[var] = value

    # Module credentials
    if module_credentials_env:
        for _module_name, cred_vars in module_credentials_env.items():
            for var in cred_vars:
                value = os.environ.get(var)
                if value is not None:
                    env[var] = value

    # Include traceparent for distributed tracing
    env.update(get_traceparent_env())

    return env


def _memory_client_lock(url: str) -> asyncio.Lock:
    lock = _MEMORY_CLIENT_LOCKS.get(url)
    if lock is None:
        lock = asyncio.Lock()
        _MEMORY_CLIENT_LOCKS[url] = lock
    return lock


def _is_cached_memory_client_healthy(client_ctx: MCPClient, client: Any) -> bool:
    probe = client_ctx if hasattr(client_ctx, "is_connected") else client
    checker = getattr(probe, "is_connected", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return True


async def _close_cached_memory_client(url: str) -> None:
    cached = _MEMORY_CLIENTS.pop(url, None)
    if cached is None:
        return

    client_ctx, _client = cached
    try:
        await client_ctx.__aexit__(None, None, None)
    except asyncio.CancelledError:
        logger.debug("Cancelled while closing cached memory client for %s", url, exc_info=True)
    except Exception:
        logger.debug("Failed to close cached memory client for %s", url, exc_info=True)


async def _get_cached_memory_client(
    url: str,
    *,
    reconnect: bool = False,
) -> Any:
    async with _memory_client_lock(url):
        if reconnect:
            await _close_cached_memory_client(url)

        cached = _MEMORY_CLIENTS.get(url)
        if cached is not None:
            client_ctx, client = cached
            if _is_cached_memory_client_healthy(client_ctx, client):
                return client
            await _close_cached_memory_client(url)

        client_ctx = MCPClient(url, name="spawner-memory")
        entered_client = await client_ctx.__aenter__()
        client = entered_client if entered_client is not None else client_ctx
        _MEMORY_CLIENTS[url] = (client_ctx, client)
        return client


async def _call_memory_tool_with_reconnect(
    url: str,
    tool_name: str,
    arguments: dict[str, str],
    timeout: float,
) -> Any:
    first_exc: Exception | None = None

    for reconnect in (False, True):
        try:
            client = await _get_cached_memory_client(url, reconnect=reconnect)
            return await asyncio.wait_for(client.call_tool(tool_name, arguments), timeout=timeout)
        except TimeoutError:
            await _close_cached_memory_client(url)
            raise
        except Exception as exc:
            if reconnect:
                if first_exc is None:
                    message = f"Failed to call {tool_name} on Memory Butler at {url}: {exc}"
                else:
                    message = (
                        f"Failed to call {tool_name} on Memory Butler at {url}: "
                        f"{first_exc} (reconnect failed: {exc})"
                    )
                raise ConnectionError(message) from exc

            first_exc = exc
            logger.info("Memory tool call failed (%s); reconnecting once", tool_name)

async def _reset_memory_client_cache_for_tests() -> None:
    """Test helper: close and clear cached Memory Butler clients."""
    urls = list(_MEMORY_CLIENTS.keys())
    for url in urls:
        await _close_cached_memory_client(url)
    _MEMORY_CLIENT_LOCKS.clear()


async def fetch_memory_context(
    butler_name: str,
    prompt: str,
    *,
    timeout: float = 5.0,
    memory_butler_port: int = 8150,
) -> str | None:
    """Fetch memory context from the Memory Butler's MCP server.

    Connects to the Memory Butler via SSE and calls the ``memory_context``
    tool with the given prompt and butler name.

    Parameters
    ----------
    butler_name:
        Name of the butler requesting memory context.
    prompt:
        The trigger prompt to send for context retrieval.
    timeout:
        Request timeout in seconds. Defaults to 5.0.
    memory_butler_port:
        Port the Memory Butler listens on. Defaults to 8150.

    Returns
    -------
    str | None
        The memory context string on success, or None on any failure.
    """
    url = f"http://localhost:{memory_butler_port}/sse"
    try:
        result = await _call_memory_tool_with_reconnect(
            url,
            "memory_context",
            {"trigger_prompt": prompt, "butler": butler_name},
            timeout,
        )
        if result.is_error:
            logger.warning(
                "Memory Butler returned error for butler %s: %s",
                butler_name,
                result.content,
            )
            return None
        # Extract text from first content block
        if result.content:
            text = getattr(result.content[0], "text", None)
            if isinstance(text, str) and text:
                return text
        logger.warning(
            "Memory Butler returned empty response for butler %s",
            butler_name,
        )
        return None
    except Exception:
        logger.warning(
            "Failed to fetch memory context for butler %s",
            butler_name,
            exc_info=True,
        )
        return None


async def store_session_episode(
    butler_name: str,
    session_output: str,
    session_id: uuid.UUID | None = None,
    *,
    timeout: float = 5.0,
    memory_butler_port: int = 8150,
) -> bool:
    """Store a session episode to the Memory Butler after CC session completes.

    Connects to the Memory Butler via SSE and calls the
    ``memory_store_episode`` tool.

    Parameters
    ----------
    butler_name:
        Name of the butler whose session completed.
    session_output:
        The output text from the completed CC session.
    session_id:
        Optional session UUID to associate with the episode.
    timeout:
        Request timeout in seconds. Defaults to 5.0.
    memory_butler_port:
        Port the Memory Butler listens on. Defaults to 8150.

    Returns
    -------
    bool
        True on success, False on any failure.
    """
    url = f"http://localhost:{memory_butler_port}/sse"
    arguments: dict[str, str] = {
        "content": session_output,
        "butler": butler_name,
    }
    if session_id is not None:
        arguments["session_id"] = str(session_id)
    try:
        result = await _call_memory_tool_with_reconnect(
            url,
            "memory_store_episode",
            arguments,
            timeout,
        )
        if result.is_error:
            logger.warning(
                "Memory Butler returned error storing episode for butler %s: %s",
                butler_name,
                result.content,
            )
            return False
        return True
    except Exception:
        logger.warning(
            "Failed to store session episode for butler %s",
            butler_name,
            exc_info=True,
        )
        return False


class Spawner:
    """Core component that invokes ephemeral AI runtime instances for a butler.

    Each butler has exactly one Spawner. An asyncio.Lock ensures serial
    dispatch — only one runtime instance runs at a time per butler.

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
    """

    def __init__(
        self,
        config: ButlerConfig,
        config_dir: Path,
        pool: asyncpg.Pool | None = None,
        module_credentials_env: dict[str, list[str]] | None = None,
        runtime: RuntimeAdapter | None = None,
        sdk_query: Any = None,
    ) -> None:
        self._config = config
        self._config_dir = config_dir
        self._pool = pool
        self._module_credentials_env = module_credentials_env
        self._lock = asyncio.Lock()
        self._accepting = True
        self._in_flight: set[asyncio.Task] = set()
        self._in_flight_event = asyncio.Event()
        self._in_flight_event.set()  # Initially no in-flight sessions

        if runtime is not None:
            self._runtime = runtime
        elif sdk_query is not None:
            # Legacy path: wrap sdk_query in a ClaudeCodeAdapter
            from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

            self._runtime = ClaudeCodeAdapter(sdk_query=sdk_query)
        else:
            # Default: create a ClaudeCodeAdapter with the real SDK query
            from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

            self._runtime = ClaudeCodeAdapter()

    async def trigger(
        self,
        prompt: str,
        trigger_source: str,
        context: str | None = None,
        max_turns: int = 20,
    ) -> SpawnerResult:
        """Spawn an ephemeral runtime instance.

        Acquires a per-butler lock to ensure serial dispatch, generates the
        MCP config, invokes the runtime via the adapter, and logs the session.

        Parameters
        ----------
        prompt:
            The prompt to send to the runtime instance.
        trigger_source:
            What caused this invocation (schedule, trigger_tool, tick, heartbeat).
        context:
            Optional text to prepend to the prompt. If provided and non-empty,
            this will be prepended to the prompt with two newlines separating them.
        max_turns:
            Maximum number of turns for the CC session. Defaults to 20.

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

        self._in_flight_event.clear()
        task = asyncio.current_task()
        if task is not None:
            self._in_flight.add(task)
        try:
            async with self._lock:
                return await self._run(prompt, trigger_source, context, max_turns)
        finally:
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
        """Wait for in-flight CC sessions to complete, up to *timeout* seconds.

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
        """Return the number of currently in-flight CC sessions."""
        return len(self._in_flight)

    async def _run(
        self,
        prompt: str,
        trigger_source: str,
        context: str | None = None,
        max_turns: int = 20,
    ) -> SpawnerResult:
        """Internal: run the runtime invocation (called under lock)."""
        session_id: uuid.UUID | None = None

        # Prepend context to prompt if provided
        final_prompt = prompt
        if context:
            final_prompt = f"{context}\n\n{prompt}"

        # Read the configured model (defaults to Haiku if not overridden)
        model = self._config.runtime.model

        # Get tracer and start butler.cc_session span
        tracer = trace.get_tracer("butlers")
        span = tracer.start_span("butler.cc_session")
        span.set_attribute("butler.name", self._config.name)
        span.set_attribute("prompt_length", len(final_prompt))

        # Attach span to context
        token = trace.context_api.attach(trace.set_span_in_context(span))

        try:
            # Extract trace_id from active span
            trace_id: str | None = None
            if span.is_recording():
                trace_id = format(span.get_span_context().trace_id, "032x")

            # Create session record with trace_id
            if self._pool is not None:
                session_id = await session_create(
                    self._pool, final_prompt, trigger_source, trace_id, model=model
                )
                # Set session_id on span
                span.set_attribute("session_id", str(session_id))

            t0 = time.monotonic()

            # Read system prompt
            system_prompt = read_system_prompt(self._config_dir, self._config.name)

            # Inject memory context (graceful fallback on failure)
            if self._config.memory.enabled:
                memory_ctx = await fetch_memory_context(
                    self._config.name,
                    final_prompt,
                    memory_butler_port=self._config.memory.port,
                )
                if memory_ctx:
                    system_prompt = f"{system_prompt}\n\n{memory_ctx}"

            # Build credential env
            env = _build_env(self._config, self._module_credentials_env)

            # Build MCP server config for the adapter
            mcp_servers: dict[str, Any] = {
                self._config.name: {
                    "url": f"http://localhost:{self._config.port}/sse",
                },
            }

            # Include Memory MCP server for all butlers (except memory butler itself)
            if self._config.memory.enabled and self._config.name != "memory":
                mcp_servers["memory"] = {
                    "url": f"http://localhost:{self._config.memory.port}/sse",
                }

            # Invoke via runtime adapter
            result_text, tool_calls, usage = await self._runtime.invoke(
                prompt=final_prompt,
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                env=env,
                max_turns=max_turns,
                model=model,
                cwd=str(self._config_dir),
            )

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

            # Store episode to Memory Butler (fire-and-forget, failure doesn't block)
            if spawner_result.success and spawner_result.output:
                await store_session_episode(
                    self._config.name,
                    spawner_result.output,
                    session_id=session_id,
                )

            return spawner_result

        except Exception as exc:
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

            return spawner_result

        finally:
            # End span and detach context
            span.end()
            trace.context_api.detach(token)


# Backward-compatible alias
CCSpawner = Spawner
