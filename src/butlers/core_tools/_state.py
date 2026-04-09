"""State core tools: state_get, state_set, state_delete, state_list."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from opentelemetry import trace

from butlers.core.state import state_delete as _state_delete
from butlers.core.state import state_get as _state_get
from butlers.core.state import state_list as _state_list
from butlers.core.state import state_set as _state_set
from butlers.core.telemetry import extract_trace_context, tag_butler_span
from butlers.core_tools._base import ToolContext


def register_state_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register state group tools: state_get, state_set, state_delete, state_list."""
    daemon = ctx.daemon
    pool = ctx.pool

    @_core_tool("state")
    async def state_get(key: str, _trace_context: dict | None = None) -> dict:
        """Get a value from the state store."""
        parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
        tracer = trace.get_tracer("butlers")
        with tracer.start_as_current_span("butler.tool.state_get", context=parent_ctx) as span:
            tag_butler_span(span, daemon.config.name)
            value = await _state_get(pool, key)
            return {"key": key, "value": value}

    @_core_tool("state")
    async def state_set(key: str, value: Any, _trace_context: dict | None = None) -> dict:
        """Set a value in the state store."""
        parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
        tracer = trace.get_tracer("butlers")
        with tracer.start_as_current_span("butler.tool.state_set", context=parent_ctx) as span:
            tag_butler_span(span, daemon.config.name)
            await _state_set(pool, key, value)
            return {"key": key, "status": "ok"}

    @_core_tool("state")
    async def state_delete(key: str, _trace_context: dict | None = None) -> dict:
        """Delete a key from the state store."""
        parent_ctx = extract_trace_context(_trace_context) if _trace_context else None
        tracer = trace.get_tracer("butlers")
        with tracer.start_as_current_span("butler.tool.state_delete", context=parent_ctx) as span:
            tag_butler_span(span, daemon.config.name)
            await _state_delete(pool, key)
            return {"key": key, "status": "deleted"}

    @_core_tool("state")
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
