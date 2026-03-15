"""Cached MCP client for connector ingestion via Switchboard.

Provides a reusable ``CachedMCPClient`` that connectors use to call MCP tools
on the Switchboard butler's SSE server. The client lazily connects on first
use, health-checks before calls, and retries once on connection failure.

The pattern is extracted from ``roster/switchboard/tools/routing/route.py``
to avoid duplicating connection management across connectors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any
from urllib.parse import urlparse

import httpx
from fastmcp import Client as MCPClient

logger = logging.getLogger(__name__)

# Readiness probe defaults
_PROBE_INITIAL_DELAY_S: float = 0.5
_PROBE_MAX_DELAY_S: float = 30.0
_PROBE_MAX_ATTEMPTS: int = 60  # up to ~5 min total with 30s cap
_PROBE_HTTP_TIMEOUT_S: float = 5.0


def _switchboard_health_url(sse_url: str) -> str:
    """Derive the switchboard health endpoint URL from its SSE endpoint URL.

    Example: ``http://localhost:41100/sse`` → ``http://localhost:41100/health``
    """
    parsed = urlparse(sse_url)
    return f"{parsed.scheme}://{parsed.netloc}/health"


async def wait_for_switchboard_ready(
    sse_url: str,
    *,
    max_attempts: int = _PROBE_MAX_ATTEMPTS,
    initial_delay_s: float = _PROBE_INITIAL_DELAY_S,
    max_delay_s: float = _PROBE_MAX_DELAY_S,
    http_timeout_s: float = _PROBE_HTTP_TIMEOUT_S,
) -> None:
    """Poll the Switchboard health endpoint until it responds 200 OK.

    Connectors call this before entering their main poll/listen loop to avoid
    delivering messages into a ``ConnectionError`` when the Switchboard butler
    is still starting up.  Dropped messages are permanent for polling connectors
    because the offset is advanced by the upstream source (Telegram, Gmail)
    regardless of whether delivery to Switchboard succeeded.

    Uses exponential backoff with ±10 % jitter, capped at ``max_delay_s``.

    Parameters
    ----------
    sse_url:
        The MCP SSE endpoint URL used by the connector (e.g.
        ``http://localhost:41100/sse``).  The health URL is derived from it.
    max_attempts:
        Maximum number of probe attempts before giving up and raising
        ``TimeoutError``.
    initial_delay_s:
        Sleep duration before the first retry.
    max_delay_s:
        Upper bound on the per-retry sleep duration.
    http_timeout_s:
        Per-request HTTP timeout for each probe call.

    Raises
    ------
    TimeoutError
        If the Switchboard does not become healthy within ``max_attempts``
        attempts.
    """
    health_url = _switchboard_health_url(sse_url)
    # Redact any embedded credentials from the URL before logging or raising.
    _parsed_health = urlparse(health_url)
    _host_part = (
        f"{_parsed_health.hostname}:{_parsed_health.port}"
        if _parsed_health.port is not None
        else _parsed_health.hostname
    )
    _safe_url = f"{_parsed_health.scheme}://{_host_part}/health"
    logger.info(
        "Switchboard readiness probe: waiting for %s to become healthy",
        _safe_url,
    )

    async with httpx.AsyncClient(timeout=http_timeout_s) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    logger.info(
                        "Switchboard readiness probe: healthy after %d attempt(s)",
                        attempt,
                    )
                    return
                logger.debug(
                    "Switchboard readiness probe: HTTP %d on attempt %d",
                    resp.status_code,
                    attempt,
                )
            except httpx.RequestError as exc:
                logger.debug(
                    "Switchboard readiness probe: attempt %d failed: %s",
                    attempt,
                    exc,
                )

            if attempt >= max_attempts:
                break

            # Exponential backoff with ±10 % jitter
            base_delay = initial_delay_s * (2 ** (attempt - 1))
            capped_delay = min(base_delay, max_delay_s)
            jitter = capped_delay * 0.1 * (2 * random.random() - 1)
            sleep_s = capped_delay + jitter
            await asyncio.sleep(sleep_s)

    raise TimeoutError(
        f"Switchboard at {_safe_url} did not become healthy within {max_attempts} probe attempt(s)"
    )


class CachedMCPClient:
    """Lazy, reconnecting MCP client for connector-to-Switchboard calls.

    Parameters
    ----------
    endpoint_url:
        SSE endpoint URL of the target MCP server (e.g. ``http://localhost:41100/sse``).
    client_name:
        Human-readable name for logging and MCP session metadata.
    """

    def __init__(self, endpoint_url: str, *, client_name: str = "connector") -> None:
        self._endpoint_url = endpoint_url
        self._client_name = client_name
        self._lock = asyncio.Lock()
        self._client_ctx: MCPClient | None = None
        self._client: Any = None

    def is_connected(self) -> bool:
        """Check whether the cached client appears healthy."""
        if self._client_ctx is None:
            return False
        probe = self._client_ctx if hasattr(self._client_ctx, "is_connected") else self._client
        checker = getattr(probe, "is_connected", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return True

    async def _connect(self) -> None:
        """Establish a new MCP client connection."""
        await self._disconnect()
        client_ctx = MCPClient(self._endpoint_url, name=self._client_name)
        entered = await client_ctx.__aenter__()
        self._client_ctx = client_ctx
        self._client = entered if entered is not None else client_ctx

    async def _disconnect(self) -> None:
        """Close the current MCP client connection if any."""
        if self._client_ctx is not None:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except asyncio.CancelledError:
                logger.debug("Cancelled while closing MCP client for %s", self._client_name)
            except Exception:
                logger.debug(
                    "Error closing MCP client for %s",
                    self._client_name,
                    exc_info=True,
                )
            finally:
                self._client_ctx = None
                self._client = None

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Call an MCP tool with single-retry reconnect on failure.

        Parameters
        ----------
        tool_name:
            Name of the MCP tool to invoke (e.g. ``"ingest"``).
        args:
            Arguments dict to pass to the tool.

        Returns
        -------
        Any
            The parsed tool result (dict for JSON tools).

        Raises
        ------
        ConnectionError
            If both the initial call and the reconnect attempt fail.
        RuntimeError
            If the tool returns an MCP error result.
        """
        first_exc: Exception | None = None

        for reconnect in (False, True):
            async with self._lock:
                if reconnect or self._client is None or not self.is_connected():
                    try:
                        await self._connect()
                    except Exception as exc:
                        if first_exc is not None:
                            raise ConnectionError(
                                f"Failed to connect to {self._endpoint_url} "
                                f"for {tool_name}: {first_exc} (reconnect failed: {exc})"
                            ) from exc
                        raise ConnectionError(
                            f"Failed to connect to {self._endpoint_url} for {tool_name}: {exc}"
                        ) from exc
                client = self._client

            try:
                result = await client.call_tool(tool_name, args)
                return self._parse_result(result, tool_name)
            except RuntimeError:
                # Application-level MCP errors (e.g. tool returned is_error=True)
                # should not be retried — propagate immediately.
                raise
            except Exception as exc:
                if reconnect:
                    msg = f"Failed to call {tool_name} on {self._endpoint_url}: {exc}"
                    if first_exc is not None:
                        msg += f" (first attempt: {first_exc})"
                    raise ConnectionError(msg) from exc
                first_exc = exc
                logger.info(
                    "MCP call failed for %s (%s); reconnecting once",
                    self._endpoint_url,
                    tool_name,
                )

    @staticmethod
    def _parse_result(result: Any, tool_name: str) -> Any:
        """Extract structured data from an MCP CallToolResult."""
        if getattr(result, "is_error", False):
            content = getattr(result, "content", None) or []
            error_text = ""
            if content:
                first = content[0]
                error_text = str(getattr(first, "text", "") or first)
            if not error_text:
                error_text = f"Tool '{tool_name}' returned an error."
            raise RuntimeError(error_text)

        # FastMCP 2.x: structured data directly on result
        if hasattr(result, "data"):
            return result.data

        # Backward-compat: list-of-block results
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

    async def aclose(self) -> None:
        """Clean shutdown of the MCP client."""
        async with self._lock:
            await self._disconnect()
