"""Core routing — route tool calls and mail between butlers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import asyncpg
from fastmcp import Client as MCPClient
from opentelemetry import trace

from butlers.core.telemetry import inject_trace_context

logger = logging.getLogger(__name__)
_ROUTER_CLIENTS: dict[str, tuple[MCPClient, Any]] = {}
_ROUTER_CLIENT_LOCKS: dict[str, asyncio.Lock] = {}


def _router_lock(endpoint_url: str) -> asyncio.Lock:
    lock = _ROUTER_CLIENT_LOCKS.get(endpoint_url)
    if lock is None:
        lock = asyncio.Lock()
        _ROUTER_CLIENT_LOCKS[endpoint_url] = lock
    return lock


def _is_cached_router_client_healthy(client_ctx: MCPClient, client: Any) -> bool:
    probe = client_ctx if hasattr(client_ctx, "is_connected") else client
    checker = getattr(probe, "is_connected", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return True


async def _close_cached_router_client(endpoint_url: str) -> None:
    cached = _ROUTER_CLIENTS.pop(endpoint_url, None)
    if cached is None:
        return

    client_ctx, _client = cached
    try:
        await client_ctx.__aexit__(None, None, None)
    except asyncio.CancelledError:
        logger.debug(
            "Cancelled while closing cached switchboard router client for %s",
            endpoint_url,
            exc_info=True,
        )
    except Exception:
        logger.debug(
            "Failed to close cached switchboard router client for %s",
            endpoint_url,
            exc_info=True,
        )


async def _get_cached_router_client(
    endpoint_url: str,
    *,
    reconnect: bool = False,
) -> Any:
    async with _router_lock(endpoint_url):
        if reconnect:
            await _close_cached_router_client(endpoint_url)

        cached = _ROUTER_CLIENTS.get(endpoint_url)
        if cached is not None:
            client_ctx, client = cached
            if _is_cached_router_client_healthy(client_ctx, client):
                return client
            await _close_cached_router_client(endpoint_url)

        client_ctx = MCPClient(endpoint_url, name="switchboard-router")
        entered_client = await client_ctx.__aenter__()
        client = entered_client if entered_client is not None else client_ctx
        _ROUTER_CLIENTS[endpoint_url] = (client_ctx, client)
        return client


async def _call_tool_with_router_client(
    endpoint_url: str,
    tool_name: str,
    args: dict[str, Any],
) -> Any:
    first_exc: Exception | None = None

    for reconnect in (False, True):
        try:
            client = await _get_cached_router_client(endpoint_url, reconnect=reconnect)
            return await client.call_tool(tool_name, args, raise_on_error=False)
        except Exception as exc:
            if reconnect:
                if first_exc is None:
                    message = f"Failed to call tool {tool_name} on {endpoint_url}: {exc}"
                else:
                    message = (
                        f"Failed to call tool {tool_name} on {endpoint_url}: "
                        f"{first_exc} (reconnect failed: {exc})"
                    )
                raise ConnectionError(message) from exc

            first_exc = exc
            logger.info(
                "Switchboard router call failed for %s (%s); reconnecting once",
                endpoint_url,
                tool_name,
            )

async def _reset_router_client_cache_for_tests() -> None:
    """Test helper: close and clear cached router clients."""
    endpoints = list(_ROUTER_CLIENTS.keys())
    for endpoint_url in endpoints:
        await _close_cached_router_client(endpoint_url)
    _ROUTER_CLIENT_LOCKS.clear()


def _extract_mcp_error_text(result: Any) -> str:
    """Best-effort extraction of MCP error text from a CallToolResult."""
    content = getattr(result, "content", None) or []
    if content:
        first = content[0]
        return str(getattr(first, "text", "") or first)
    return ""


def _build_trigger_args(args: dict[str, Any]) -> dict[str, Any]:
    """Map legacy handle_message args to trigger args."""
    prompt = str(args.get("prompt") or args.get("message") or "")
    trigger_args: dict[str, Any] = {"prompt": prompt}
    if args.get("context") is not None:
        trigger_args["context"] = str(args["context"])
    return trigger_args


async def route(
    pool: asyncpg.Pool,
    target_butler: str,
    tool_name: str,
    args: dict[str, Any],
    source_butler: str = "switchboard",
    *,
    call_fn: Any | None = None,
) -> dict[str, Any]:
    """Route a tool call to a target butler via its MCP endpoint.

    Looks up the target butler in the registry, connects via SSE MCP client,
    calls the specified tool, logs the routing, and returns the result.

    Parameters
    ----------
    pool:
        Database connection pool.
    target_butler:
        Name of the butler to route to.
    tool_name:
        Name of the MCP tool to call.
    args:
        Arguments to pass to the tool.
    source_butler:
        Name of the calling butler (for logging).
    call_fn:
        Optional callable for testing; signature
        ``async (endpoint_url, tool_name, args) -> Any``.
        When *None*, the default MCP client is used.
    """
    tracer = trace.get_tracer("butlers")
    with tracer.start_as_current_span("switchboard.route") as span:
        span.set_attribute("target", target_butler)
        span.set_attribute("tool_name", tool_name)

        t0 = time.monotonic()

        # Look up target
        row = await pool.fetchrow(
            "SELECT endpoint_url FROM butler_registry WHERE name = $1", target_butler
        )
        if row is None:
            span.set_status(trace.StatusCode.ERROR, "Butler not found")
            await _log_routing(
                pool, source_butler, target_butler, tool_name, False, 0, "Butler not found"
            )
            return {"error": f"Butler '{target_butler}' not found in registry"}

        endpoint_url = row["endpoint_url"]

        # Inject trace context into args
        trace_context = inject_trace_context()
        if trace_context:
            args = {**args, "_trace_context": trace_context}

        try:
            if call_fn is not None:
                result = await call_fn(endpoint_url, tool_name, args)
            else:
                result = await _call_butler_tool(endpoint_url, tool_name, args)
            duration_ms = int((time.monotonic() - t0) * 1000)
            await _log_routing(
                pool, source_butler, target_butler, tool_name, True, duration_ms, None
            )
            # Update last_seen_at on successful route
            await pool.execute(
                "UPDATE butler_registry SET last_seen_at = now() WHERE name = $1",
                target_butler,
            )
            return {"result": result}
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            await _log_routing(
                pool, source_butler, target_butler, tool_name, False, duration_ms, error_msg
            )
            return {"error": error_msg}


async def post_mail(
    pool: asyncpg.Pool,
    target_butler: str,
    sender: str,
    sender_channel: str,
    body: str,
    subject: str | None = None,
    priority: int | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    call_fn: Any | None = None,
) -> dict[str, Any]:
    """Deliver a message to another butler's mailbox via the Switchboard.

    Validates the target butler exists and has the mailbox module enabled,
    then routes to the target's ``mailbox_post`` tool.

    Parameters
    ----------
    pool:
        Database connection pool.
    target_butler:
        Name of the butler to deliver mail to.
    sender:
        Identity of the sending butler or external caller.
    sender_channel:
        Channel through which the sender is communicating (e.g. "mcp", "telegram").
    body:
        Message body.
    subject:
        Optional message subject line.
    priority:
        Optional priority (0=critical ... 4=backlog).
    metadata:
        Optional additional metadata dict.
    call_fn:
        Optional callable for testing; forwarded to :func:`route`.

    Returns
    -------
    dict
        ``{"message_id": "<id>"}`` on success, or ``{"error": "<description>"}``
        on failure.
    """
    # 1. Validate target butler exists
    row = await pool.fetchrow("SELECT modules FROM butler_registry WHERE name = $1", target_butler)
    if row is None:
        await _log_routing(
            pool, sender, target_butler, "mailbox_post", False, 0, "Butler not found"
        )
        return {"error": f"Butler '{target_butler}' not found in registry"}

    # 2. Validate target butler has mailbox module
    modules = json.loads(row["modules"]) if isinstance(row["modules"], str) else row["modules"]
    if "mailbox" not in modules:
        await _log_routing(
            pool,
            sender,
            target_butler,
            "mailbox_post",
            False,
            0,
            "Mailbox module not enabled",
        )
        return {"error": f"Butler '{target_butler}' does not have the mailbox module enabled"}

    # 3. Build args for mailbox_post tool
    args: dict[str, Any] = {
        "sender": sender,
        "sender_channel": sender_channel,
        "body": body,
    }
    if subject is not None:
        args["subject"] = subject
    if priority is not None:
        args["priority"] = priority
    if metadata is not None:
        args["metadata"] = metadata if isinstance(metadata, str) else json.dumps(metadata)

    # 4. Route to target butler's mailbox_post tool
    result = await route(
        pool,
        target_butler,
        "mailbox_post",
        args,
        source_butler=sender,
        call_fn=call_fn,
    )

    # 5. Extract message_id from successful result
    if "result" in result:
        inner = result["result"]
        wrapped: dict[str, Any] = {"result": inner}
        if isinstance(inner, dict) and "message_id" in inner:
            wrapped["message_id"] = inner["message_id"]
            return wrapped
        wrapped["message_id"] = str(inner)
        return wrapped

    return result


async def _call_butler_tool(endpoint_url: str, tool_name: str, args: dict[str, Any]) -> Any:
    """Call a tool on another butler via MCP SSE client.

    Raises
    ------
    ConnectionError
        If the target endpoint cannot be reached.
    RuntimeError
        If the target tool returns an MCP error result.
    """
    result = await _call_tool_with_router_client(endpoint_url, tool_name, args)
    if getattr(result, "is_error", False):
        error_text = _extract_mcp_error_text(result)
        # Backward compatibility: many callers still route "handle_message"
        # while daemon core exposes "trigger". Retry automatically.
        if tool_name == "handle_message" and "Unknown tool" in error_text:
            trigger_args = _build_trigger_args(args)
            result = await _call_tool_with_router_client(endpoint_url, "trigger", trigger_args)

    if getattr(result, "is_error", False):
        error_text = _extract_mcp_error_text(result)
        if not error_text:
            error_text = f"Tool '{tool_name}' returned an error."
        raise RuntimeError(error_text)

    # FastMCP 2.x CallToolResult carries structured data directly.
    if hasattr(result, "data"):
        return result.data

    # Backward-compat fallback for list-of-block results.
    if result and hasattr(result, "__iter__"):
        for block in result:
            text = getattr(block, "text", None)
            if text is None:
                continue
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
            return text

    return result


async def _log_routing(
    pool: asyncpg.Pool,
    source: str,
    target: str,
    tool_name: str,
    success: bool,
    duration_ms: int,
    error: str | None,
) -> None:
    """Log a routing event."""
    await pool.execute(
        """
        INSERT INTO routing_log
            (source_butler, target_butler, tool_name, success, duration_ms, error)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        source,
        target,
        tool_name,
        success,
        duration_ms,
        error,
    )
