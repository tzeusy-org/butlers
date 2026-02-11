"""Core routing — route tool calls and mail between butlers."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import asyncpg
from opentelemetry import trace

from butlers.core.telemetry import inject_trace_context

logger = logging.getLogger(__name__)


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

    In production this would use the MCP SDK client; for now it raises
    ConnectionError to signal that real MCP integration is pending.
    """
    raise ConnectionError(
        f"Failed to call tool {tool_name} on {endpoint_url} — requires MCP client SDK integration"
    )


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
