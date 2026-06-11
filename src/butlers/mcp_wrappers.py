"""MCP proxy wrappers for the Butler daemon.

Provides instrumented proxies around FastMCP that add telemetry and logging
to every tool invocation registered by butler modules.

Classes:
- _SpanWrappingMCP: Wraps tool handlers with OpenTelemetry spans and module
  enabled/disabled gating.
- _ToolCallLoggingMCP: Simpler proxy that only logs and captures tool calls.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any

from fastmcp import FastMCP

from butlers.core.telemetry import tool_span
from butlers.core.tool_call_capture import capture_tool_call, fingerprint_tool_call_payload
from butlers.module_state import ModuleRuntimeState

logger = logging.getLogger(__name__)

_MCP_TOOL_CALL_LOG_LINE = "MCP tool called (butler=%s module=%s tool=%s)"
_VISIBLE_CAPTURE_INPUT_FIELDS = frozenset(
    ("butler", "target_butler", "butler_name", "prompt", "context")
)


def _visible_capture_input(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return the small raw-input allowlist that is safe to persist."""
    return {k: kwargs.get(k) for k in _VISIBLE_CAPTURE_INPUT_FIELDS if k in kwargs}


def _tool_input_fingerprint(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Fingerprint the full tool input without storing sensitive raw arguments."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound_arguments = dict(bound.arguments)
        payload: Any = bound_arguments if bound_arguments else None
    except Exception:
        payload = {"args": args, "kwargs": kwargs}
    return fingerprint_tool_call_payload(payload)


class _SpanWrappingMCP:
    """Proxy around FastMCP that logs and span-wraps module tool handlers.

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
        module_runtime_states: dict[str, ModuleRuntimeState] | None = None,
    ) -> None:
        self._mcp = mcp
        self._butler_name = butler_name
        self._module_name = module_name or "unknown"
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
            self._registered_tool_names.add(resolved_tool_name)

            module_name_for_gate = self._module_name
            runtime_states_ref = self._module_runtime_states

            @functools.wraps(fn)
            async def instrumented(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                self._log_tool_call(resolved_tool_name)
                capture_input = _visible_capture_input(kwargs)
                input_fingerprint = _tool_input_fingerprint(fn, args, kwargs)
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
                            input_fingerprint=input_fingerprint,
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
                        input_fingerprint=input_fingerprint,
                        outcome="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise

                capture_tool_call(
                    tool_name=resolved_tool_name,
                    module_name=self._module_name,
                    input_payload=capture_input,
                    input_fingerprint=input_fingerprint,
                    outcome="success",
                    result_payload=result,
                )
                return result

            return original_decorator(instrumented)

        return wrapper

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
                capture_input = _visible_capture_input(kwargs)
                input_fingerprint = _tool_input_fingerprint(fn, args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    capture_tool_call(
                        tool_name=resolved_tool_name,
                        module_name=self._module_name,
                        input_payload=capture_input,
                        input_fingerprint=input_fingerprint,
                        outcome="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise
                capture_tool_call(
                    tool_name=resolved_tool_name,
                    module_name=self._module_name,
                    input_payload=capture_input,
                    input_fingerprint=input_fingerprint,
                    outcome="success",
                    result_payload=result,
                )
                return result

            return original_decorator(instrumented)

        return wrapper

    def __getattr__(self, name: str) -> Any:
        return getattr(self._mcp, name)
