"""MCP endpoint warmup — daemon-side pre-priming for Codex discovery race mitigation.

At daemon boot (after the MCP server is listening), this module opens short-lived
MCP client sessions against:
  1. The butler's own streamable-HTTP endpoint (``http://localhost:{port}/mcp``).
  2. Each additional endpoint URL supplied by the caller (e.g. Switchboard routes).

For each endpoint, it issues an MCP ``initialize`` + ``tools/list`` request.  This
primes any lazy import, connection pool, or OS-level buffer on the server side so
that the first real Codex spawn hits warm endpoints rather than cold ones.

**Warmup is best-effort**: any failure is logged at WARNING level and does not
propagate to the caller.  Daemon boot must never be blocked or failed by a warmup
error.

**Kill-switch**: set ``BUTLERS_MCP_WARMUP_DISABLED=1`` (or any truthy string) to
disable all warmup.  This allows operators to disable the feature without a code
change if it causes problems.

**Instrumentation**: warmup results are logged with structured fields so they can
be correlated with session MCP-discovery failure rates:
  - butler name
  - endpoint URL
  - latency (ms) for initialize + tools/list round-trip
  - number of tools discovered
  - success / failure status
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_KILL_SWITCH_ENV = "BUTLERS_MCP_WARMUP_DISABLED"

# Timeout for a single endpoint's initialize + tools/list round-trip.
_WARMUP_TIMEOUT_S = 10.0

# MCP protocol version used in initialize requests.
_MCP_PROTOCOL_VERSION = "2024-11-05"
_MCP_SESSION_ID_HEADER = "mcp-session-id"
_MCP_PROTOCOL_VERSION_HEADER = "mcp-protocol-version"
_JSON_CONTENT_TYPE = "application/json"
_STREAMABLE_HTTP_ACCEPT = f"{_JSON_CONTENT_TYPE}, text/event-stream"

# Client info sent in initialize requests.
_CLIENT_INFO = {"name": "butlers-warmup", "version": "1.0"}


def _is_disabled() -> bool:
    """Return True when the warmup kill-switch env var is set to a truthy value."""
    raw = os.environ.get(_KILL_SWITCH_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _build_mcp_headers(*, session_id: str | None = None) -> dict[str, str]:
    """Build streamable-HTTP MCP headers accepted by FastMCP's /mcp endpoint."""
    headers = {
        "Accept": _STREAMABLE_HTTP_ACCEPT,
        "Content-Type": _JSON_CONTENT_TYPE,
        _MCP_PROTOCOL_VERSION_HEADER: _MCP_PROTOCOL_VERSION,
    }
    if session_id:
        headers[_MCP_SESSION_ID_HEADER] = session_id
    return headers


async def _warmup_endpoint(url: str, *, butler_name: str) -> dict[str, Any]:
    """Issue initialize + tools/list against *url* and return a result dict.

    Always returns a dict with keys:
      - ``url`` (str)
      - ``success`` (bool)
      - ``latency_ms`` (int | None)
      - ``tool_count`` (int | None)
      - ``error`` (str | None)

    Never raises — all exceptions are caught and reflected in the result dict.
    """
    result: dict[str, Any] = {
        "url": url,
        "success": False,
        "latency_ms": None,
        "tool_count": None,
        "error": None,
    }

    try:
        t0 = time.monotonic()

        async with httpx.AsyncClient(timeout=_WARMUP_TIMEOUT_S) as client:
            # MCP initialize request (streamable-HTTP: single POST with JSON-RPC).
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            }
            init_resp = await client.post(url, json=init_payload, headers=_build_mcp_headers())
            init_resp.raise_for_status()

            # Extract session token from initialize response headers (streamable HTTP).
            session_token = init_resp.headers.get(_MCP_SESSION_ID_HEADER)

            # MCP tools/list request.
            tools_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
            tools_resp = await client.post(
                url,
                json=tools_payload,
                headers=_build_mcp_headers(session_id=session_token),
            )
            tools_resp.raise_for_status()

            latency_ms = int((time.monotonic() - t0) * 1000)

            # Parse tool count from response body (best-effort).
            tool_count: int | None = None
            try:
                body = tools_resp.json()
                tools_list = body.get("result", {}).get("tools", [])
                if isinstance(tools_list, list):
                    tool_count = len(tools_list)
            except Exception:
                pass  # non-fatal: latency is the primary signal

            result["success"] = True
            result["latency_ms"] = latency_ms
            result["tool_count"] = tool_count

            logger.info(
                "MCP warmup OK butler=%s url=%s latency_ms=%d tools=%s",
                butler_name,
                url,
                latency_ms,
                tool_count if tool_count is not None else "?",
            )

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "MCP warmup FAILED butler=%s url=%s error=%s",
            butler_name,
            url,
            result["error"],
        )

    return result


async def warmup_mcp_endpoints(
    butler_name: str,
    *,
    butler_port: int,
    extra_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Warm up the butler's own MCP endpoint plus any extra URLs.

    This is a convenience wrapper around :func:`warmup_mcp_urls`.
    """
    own_url = f"http://localhost:{butler_port}/mcp"
    return await warmup_mcp_urls(butler_name, [own_url, *(extra_urls or [])])


async def warmup_mcp_urls(
    butler_name: str,
    urls: list[str],
) -> list[dict[str, Any]]:
    """Warm up an explicit list of MCP endpoint URLs.

    Fires initialize + tools/list against each unique URL and returns a list of
    per-endpoint result dicts (see :func:`_warmup_endpoint`).

    Failure is best-effort: the function never raises. If the kill-switch is
    set, it logs at INFO level and returns an empty list immediately.
    """
    if _is_disabled():
        logger.info("MCP warmup disabled (%s=1) for butler=%s", _KILL_SWITCH_ENV, butler_name)
        return []

    all_urls = list(
        dict.fromkeys(url.strip() for url in urls if isinstance(url, str) and url.strip())
    )
    if not all_urls:
        return []

    tasks = [
        asyncio.create_task(_warmup_endpoint(url, butler_name=butler_name)) for url in all_urls
    ]
    results: list[dict[str, Any]] = await asyncio.gather(*tasks, return_exceptions=False)

    ok_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - ok_count
    logger.info(
        "MCP warmup complete butler=%s endpoints=%d ok=%d failed=%d",
        butler_name,
        len(results),
        ok_count,
        fail_count,
    )
    return results
