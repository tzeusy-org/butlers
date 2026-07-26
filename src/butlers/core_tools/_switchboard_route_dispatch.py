"""Shared core for dispatching one tool call through the Switchboard's
``route()`` primitive.

bu-xthtw (PR #3585 review, bu-o4kzr): ``_delegation.py``'s
``_dispatch_via_switchboard`` and ``_domain_events.py``'s
``_dispatch_receive_via_switchboard`` used to be two independently
hand-maintained ~60-line copies of the exact same client-vs-self-delivery
transport loop (timeout/connection-error handling, MCP-error extraction, the
Switchboard in-process self-delivery branch) that had already started to
drift apart. ``dispatch_via_switchboard_route`` below is that transport loop,
factored out once; each call site supplies its own ``classify`` callback for
the one piece of behavior the two files *deliberately* diverge on: how to
tell a route()-level dispatch success apart from a business-level failure
returned by the target tool itself. See each call site's own classify
function (``_delegation._classify_delegation_route_result`` and
``_domain_events._unwrap_route_result``) for that divergence, documented
where it happens instead of buried in a shared helper neither file fully
owns.
"""

from __future__ import annotations

import asyncio
import errno
from collections.abc import Callable
from typing import Any

import httpx

ROUTE_TIMEOUT_S = 30

# Route dispatch handles sockets, not local files. Keep the same bounded
# classifier at both the caller-to-Switchboard and Switchboard-to-target hops:
# PermissionError and FileNotFoundError are configuration or authorization
# failures that a delivery retry cannot repair.
_RETRYABLE_ROUTE_OS_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.EINTR,
        errno.ENETDOWN,
        errno.ENETRESET,
        errno.ENETUNREACH,
        errno.EPIPE,
        errno.ETIMEDOUT,
    }
)


def _is_direct_retryable_route_exception(exc: Exception) -> bool:
    """Return whether ``exc`` itself is a known transient route failure."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException)):
        return True
    return isinstance(exc, OSError) and exc.errno in _RETRYABLE_ROUTE_OS_ERRNOS


def is_retryable_route_exception(exc: Exception) -> bool:
    """Return whether an exception is a known transient route transport failure.

    FastMCP turns a transport error raised while opening a client into its
    exact ``RuntimeError('Client failed to connect: ...')`` wrapper. Preserve
    the underlying network classification only for that known shape and its
    direct cause; arbitrary runtime wrappers remain terminal and must never
    gain a reconnect attempt or be relabeled as ``ConnectionError``.
    """
    if _is_direct_retryable_route_exception(exc):
        return True
    return (
        type(exc) is RuntimeError
        and str(exc).startswith("Client failed to connect:")
        and isinstance(exc.__cause__, Exception)
        and _is_direct_retryable_route_exception(exc.__cause__)
    )


def _extract_mcp_error_text(result: Any) -> str:
    """Best-effort extraction of MCP error text from a CallToolResult."""
    content = getattr(result, "content", None) or []
    if content:
        first = content[0]
        return str(getattr(first, "text", "") or first)
    return "route tool returned an error"


async def dispatch_via_switchboard_route(
    client: Any,
    pool: Any,
    butler_name: str,
    *,
    target_butler: str,
    tool_name: str,
    args: dict[str, Any],
    classify: Callable[[Any], tuple[dict[str, Any] | None, str | None, bool]],
    route_purpose: str,
    timeout_s: float = ROUTE_TIMEOUT_S,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Dispatch one tool call through the Switchboard's ``route()`` primitive.

    Mirrors ``notify()``'s client-vs-self-delivery split: every butler except
    Switchboard itself calls ``client.call_tool("route", ...)`` (``client`` is
    the caller's live ``daemon.switchboard_client``, or the deterministic-job
    equivalent recovered via ``get_current_switchboard_client()``); Switchboard
    calls the underlying ``route()`` function directly in-process (it already
    owns the pool ``route()`` needs).

    ``route()`` (``roster/switchboard/tools/routing/route.py``) always
    returns ``{"error": "<ExceptionType>: <message>"}`` on a route-level
    failure (target unreachable, unknown tool, registry lookup), with a
    literal boolean ``"retryable"`` classification on current envelopes, or
    ``{"result": <target tool's own return value>}`` on success -- never the
    target's dict unwrapped at the top level. ``classify`` is the one seam
    where callers deliberately diverge on how to peel that envelope back and
    decide whether the *target tool's own* response also counts as a failure;
    given the raw value returned by ``route()`` (``result.data`` for a real
    MCP client, or the same shape returned directly by the in-process
    ``route()`` call for Switchboard's self-delivery branch), it must return
    ``(data, error_text, retryable)`` in this function's own return shape.

    ``route_purpose`` fills in the "Switchboard is not connected" message
    (e.g. ``"delegated call"``, ``"fan-out dispatch"``) so that diagnostic
    stays call-site-specific without duplicating the surrounding sentence.

    Returns ``(data, error_text, retryable)`` -- ``error_text`` is ``None`` on
    a successful dispatch, in which case ``data`` is whatever ``classify``
    extracted from the target tool's own payload (``None`` if the caller
    doesn't need it).
    """
    route_tool_args = {
        "target_butler": target_butler,
        "tool_name": tool_name,
        "args": args,
        "source_butler": butler_name,
    }

    if client is not None:
        try:
            result = await asyncio.wait_for(
                client.call_tool("route", route_tool_args),
                timeout=timeout_s,
            )
        except TimeoutError:
            return None, f"Switchboard route() call timed out after {timeout_s}s.", True
        except (ConnectionError, OSError, httpx.NetworkError, httpx.TimeoutException) as exc:
            return (
                None,
                f"Switchboard unreachable: {exc}",
                is_retryable_route_exception(exc),
            )
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}", False

        if result.is_error:
            return None, _extract_mcp_error_text(result), False
        return classify(result.data)

    if butler_name == "switchboard":
        from butlers.tools.switchboard.routing.route import route as _switchboard_route

        try:
            raw = await _switchboard_route(
                pool,
                target_butler,
                tool_name,
                args,
                source_butler=butler_name,
            )
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}", False

        return classify(raw)

    return (
        None,
        f"Switchboard is not connected. Cannot route the {route_purpose}. "
        "This is a transient infrastructure issue — retry after a delay.",
        True,
    )
