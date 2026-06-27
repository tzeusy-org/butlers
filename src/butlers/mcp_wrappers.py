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
from butlers.exceptions import ChannelEgressOwnershipError, is_channel_egress_tool
from butlers.module_state import ModuleRuntimeState

logger = logging.getLogger(__name__)

_MCP_TOOL_CALL_LOG_LINE = "MCP tool called (butler=%s module=%s tool=%s)"
_MCP_TOOL_CALL_FAILED_LOG_LINE = "MCP tool call failed (butler=%s module=%s tool=%s): %s"
_VISIBLE_CAPTURE_INPUT_FIELDS = frozenset(
    ("butler", "target_butler", "butler_name", "prompt", "context")
)


def _visible_capture_input(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return the small raw-input allowlist that is safe to persist."""
    return {k: kwargs.get(k) for k in _VISIBLE_CAPTURE_INPUT_FIELDS if k in kwargs}


def _declared_tool_name(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    """Resolve the tool name explicitly declared at registration, if any.

    FastMCP's ``tool(name_or_fn=None, *, name=None, ...)`` accepts an overriding
    tool name either as the keyword ``name=`` or as the first positional argument
    (e.g. ``@mcp.tool("telegram_send_message")``). A bare ``@mcp.tool`` passes the
    function itself positionally, so only treat a *string* first positional as a
    declared name. Resolving both forms here keeps the channel-egress ownership
    guard fail-closed: a positionally-named egress tool must not slip through by
    falling back to the (unmatched) function name.
    """
    declared = kwargs.get("name")
    if declared is None and args and isinstance(args[0], str):
        declared = args[0]
    return declared


def _log_tool_call_failure(
    *,
    butler_name: str,
    module_name: str,
    tool_name: str,
    exc: Exception,
) -> None:
    """Emit one structured error log per failed MCP tool call.

    This is the observability hook the autonomous QA log-scanner relies on:
    when a butler's LLM agent catches a tool exception and the session still
    completes ``success=true``, the session-records source never sees the
    failure, so the *only* way QA learns about it is a structured error entry
    in ``logs/butlers/<butler>.log``.  FastMCP's own rich traceback only
    reaches the container's stdout/stderr and is never JSON-formatted into the
    per-butler log file.

    The per-butler file handler routes records via ``ButlerContextFileFilter``,
    which only passes records whose butler ContextVar matches.  Tool handlers
    can run in async tasks where that ContextVar is unset (the info-level
    "MCP tool called" line shows ``butler=None``), so we bind the butler
    context here to guarantee the record reaches the scanned log file.

    The ``exception`` extra is surfaced as the log entry's exception type so the
    log-scanner can fingerprint/score it; it is not a switchboard/timeout shape,
    so it is not matched by any non-actionable suppression rule.
    """
    from butlers.core.logging import set_butler_context

    set_butler_context(butler_name)
    logger.error(
        _MCP_TOOL_CALL_FAILED_LOG_LINE,
        butler_name,
        module_name,
        tool_name,
        f"{type(exc).__name__}: {exc}",
        extra={
            "butler_name": butler_name,
            "module_name": module_name,
            "tool": tool_name,
            "exception": type(exc).__name__,
        },
    )


def _tool_input_fingerprint(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Fingerprint the full tool input without storing sensitive raw arguments."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
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
        is_messenger: bool = False,
    ) -> None:
        self._mcp = mcp
        self._butler_name = butler_name
        self._module_name = module_name or "unknown"
        # Only the messenger butler is permitted to register channel-egress
        # (outbound send/reply) tools. Defaults to ``False`` so the guard fails
        # closed: a butler must be explicitly marked messenger to own egress.
        self._is_messenger = is_messenger
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
        declared_name = _declared_tool_name(args, kwargs)
        original_decorator = self._mcp.tool(*args, **kwargs)

        def wrapper(fn):  # noqa: ANN001, ANN202
            resolved_tool_name = declared_name or fn.__name__
            # Fail closed: non-messenger butlers may not own channel egress.
            if not self._is_messenger and is_channel_egress_tool(resolved_tool_name):
                raise ChannelEgressOwnershipError(
                    butler_name=self._butler_name,
                    tool_name=resolved_tool_name,
                    module_name=self._module_name,
                )
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
                    _log_tool_call_failure(
                        butler_name=self._butler_name,
                        module_name=self._module_name,
                        tool_name=resolved_tool_name,
                        exc=exc,
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
        declared_name = _declared_tool_name(args, kwargs)
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
                    _log_tool_call_failure(
                        butler_name=self._butler_name,
                        module_name=self._module_name,
                        tool_name=resolved_tool_name,
                        exc=exc,
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
