"""Async Steam Web API client with rate limiting and batch support.

This module provides a lightweight async HTTP client wrapping the Steam Web API
endpoints needed by the Steam module (MCP tools) and the Steam connector
(background polling).

API model:
- API key auth via ``key`` query parameter — never logged (redacted via httpx event hooks)
- Base URL: ``https://api.steampowered.com/{interface}/{method}/v{version}/``
- All responses are ``{"response": {...}}`` wrappers — automatically unwrapped
- Rate limit handling: exponential backoff on 429/403 (initial 60s, configurable max)
- Batch support for ``ISteamUser/GetPlayerSummaries`` (max 100 SteamIDs per call)

Usage::

    async with SteamAPIClient(api_key="your_key") as client:
        summary = await client.get_player_summaries(["76561198000000000"])
        owned = await client.request("IPlayerService", "GetOwnedGames", params={"steamid": "..."})
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STEAM_API_BASE = "https://api.steampowered.com"
_API_VERSION = 1

# GetPlayerSummaries hard limit per call.
_BATCH_SIZE_LIMIT = 100

# Rate-limit back-off parameters.
_BACKOFF_INITIAL_S: float = 60.0
_BACKOFF_MAX_S: float = 3600.0
_BACKOFF_JITTER_FRACTION: float = 0.1  # ±10% jitter

# HTTP status codes that trigger backoff.
_RATE_LIMIT_STATUSES = {429, 403}

# Redaction placeholder — replaces API key in any log output.
_REDACTED = "[REDACTED]"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SteamRateLimitError(Exception):
    """Raised when Steam returns HTTP 429 or 403 and the caller should back off.

    Attributes
    ----------
    retry_after_s:
        Seconds to wait before retrying, calculated via exponential backoff.
    status_code:
        The HTTP status code that triggered the rate limit.
    """

    def __init__(self, retry_after_s: float, status_code: int = 429) -> None:
        super().__init__(
            f"Steam API rate limited (HTTP {status_code}); retry after {retry_after_s:.1f}s"
        )
        self.retry_after_s = retry_after_s
        self.status_code = status_code


class SteamAPIError(Exception):
    """Raised for unexpected Steam API errors (non-rate-limit status codes).

    Attributes
    ----------
    status_code:
        The HTTP status code from Steam.
    body:
        The response body text (may be empty).
    """

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Steam API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


# ---------------------------------------------------------------------------
# Helper: exponential back-off with jitter
# ---------------------------------------------------------------------------


def _exponential_backoff(
    attempt: int,
    initial: float = _BACKOFF_INITIAL_S,
    maximum: float = _BACKOFF_MAX_S,
    jitter_fraction: float = _BACKOFF_JITTER_FRACTION,
) -> float:
    """Return a back-off delay with uniform jitter.

    Parameters
    ----------
    attempt:
        Zero-based retry attempt index.
    initial:
        Base delay in seconds for the first retry.
    maximum:
        Upper cap on the delay (before jitter is applied).
    jitter_fraction:
        Fraction of the base delay to use as jitter range (±).
    """
    base = min(initial * (2**attempt), maximum)
    jitter = base * jitter_fraction
    return base + random.uniform(-jitter, jitter)  # noqa: S311


# ---------------------------------------------------------------------------
# API key redaction helpers for httpx event hooks
# ---------------------------------------------------------------------------


def _make_request_redact_hook(api_key: str) -> Any:
    """Return an httpx request event hook that redacts the API key from the URL.

    The hook modifies the request URL in-place before it is sent, replacing the
    API key value with ``[REDACTED]`` so it never appears in httpx logs.
    """

    def _redact_request(request: httpx.Request) -> None:
        if not api_key:
            return
        # Replace api_key value in the query string if present.
        # Use the params dict directly — more robust than substring-matching
        # the raw URL string, which could yield false positives.
        params = dict(request.url.params)
        if "key" in params and params["key"] == api_key:
            params["key"] = _REDACTED
            request.url = request.url.copy_with(params=params)

    return _redact_request


# ---------------------------------------------------------------------------
# SteamAPIClient
# ---------------------------------------------------------------------------


class SteamAPIClient:
    """Async HTTP client for the Steam Web API.

    Parameters
    ----------
    api_key:
        Steam Web API key. Injected as ``key`` query parameter on every request.
        Never written to logs — redacted via httpx request event hooks.
    http_client:
        Optional pre-built ``httpx.AsyncClient``.  If ``None``, a new client is
        created (and closed) by the context manager / :meth:`open`.
    backoff_initial_s:
        Initial back-off delay in seconds on rate limit responses. Default: 60.
    backoff_max_s:
        Maximum back-off ceiling in seconds. Default: 3600.
    cache_ttl_s:
        How long (in seconds) to cache API responses. Stored for future use by
        caching layers. Set to 0 to disable caching. Default: 300.

    Usage (async context manager — preferred)::

        async with SteamAPIClient(api_key="...") as client:
            data = await client.request("ISteamUser", "GetPlayerSummaries",
                                        params={"steamids": "76561198000000000"})

    Usage (manual lifetime)::

        client = SteamAPIClient(api_key="...")
        await client.open()
        try:
            data = await client.request("ISteamUser", "GetPlayerSummaries", ...)
        finally:
            await client.close()
    """

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        backoff_initial_s: float = _BACKOFF_INITIAL_S,
        backoff_max_s: float = _BACKOFF_MAX_S,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self._api_key = api_key
        self._http_client = http_client
        self._owns_client = http_client is None
        self._backoff_initial_s = backoff_initial_s
        self._backoff_max_s = backoff_max_s
        self._cache_ttl_s = cache_ttl_s
        self._rate_limit_attempt: int = 0  # tracks consecutive rate-limit hits

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the HTTP client if not already open."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                event_hooks={
                    "request": [_make_request_redact_hook(self._api_key)],
                }
            )

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> SteamAPIClient:
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Core request method
    # ------------------------------------------------------------------

    async def request(
        self,
        interface: str,
        method: str,
        params: dict[str, Any] | None = None,
        version: int = _API_VERSION,
    ) -> dict[str, Any]:
        """Make an authenticated GET request to the Steam Web API.

        The ``key`` query parameter is injected automatically and is never
        written to logs.

        Parameters
        ----------
        interface:
            Steam API interface name, e.g. ``"ISteamUser"``.
        method:
            Steam API method name, e.g. ``"GetPlayerSummaries"``.
        params:
            Additional query parameters to include in the request.
        version:
            API version number. Defaults to 1.

        Returns
        -------
        dict
            The parsed ``response`` payload from Steam's JSON wrapper, or the
            full response body if no ``response`` key is present.

        Raises
        ------
        SteamRateLimitError
            If Steam returns HTTP 429 or 403.
        SteamAPIError
            If Steam returns any other non-2xx status code.
        RuntimeError
            If called before :meth:`open`.
        """
        if self._http_client is None:
            raise RuntimeError(
                "SteamAPIClient is not open. Call open() or use as async context manager."
            )

        url = f"{_STEAM_API_BASE}/{interface}/{method}/v{version}/"
        merged_params: dict[str, Any] = {"key": self._api_key, **(params or {})}

        try:
            response = await self._http_client.get(url, params=merged_params)
        except httpx.TransportError as exc:
            raise SteamAPIError(0, str(exc)) from exc

        if response.status_code in _RATE_LIMIT_STATUSES:
            retry_after = self._compute_backoff()
            logger.warning(
                "Steam API rate limited (HTTP %s); computed backoff %.1fs",
                response.status_code,
                retry_after,
            )
            raise SteamRateLimitError(retry_after_s=retry_after, status_code=response.status_code)

        if response.status_code >= 400:
            raise SteamAPIError(status_code=response.status_code, body=response.text)

        # Reset rate-limit counter on successful response.
        self._rate_limit_attempt = 0

        data: dict[str, Any] = response.json()

        # Unwrap Steam's {"response": {...}} envelope.
        if "response" in data:
            return data["response"]
        return data

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    async def get_player_summaries(self, steam_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch player summaries for a list of SteamIDs.

        Automatically splits requests into batches of up to
        :data:`_BATCH_SIZE_LIMIT` (100) SteamIDs each, as required by the
        Steam API ``ISteamUser/GetPlayerSummaries`` endpoint.

        Parameters
        ----------
        steam_ids:
            List of SteamID64 strings. May be empty (returns ``[]``).

        Returns
        -------
        list[dict]
            Concatenated list of player summary objects from all batches.
        """
        if not steam_ids:
            return []

        players: list[dict[str, Any]] = []
        for batch in _batch(steam_ids, _BATCH_SIZE_LIMIT):
            result = await self.request(
                "ISteamUser",
                "GetPlayerSummaries",
                params={"steamids": ",".join(batch)},
            )
            players.extend(result.get("players", []))

        return players

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_backoff(self) -> float:
        """Calculate the next backoff delay and increment the attempt counter."""
        delay = _exponential_backoff(
            attempt=self._rate_limit_attempt,
            initial=self._backoff_initial_s,
            maximum=self._backoff_max_s,
        )
        self._rate_limit_attempt += 1
        return delay


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _batch(items: list[Any], size: int) -> list[list[Any]]:
    """Split *items* into consecutive chunks of at most *size* elements.

    Parameters
    ----------
    items:
        Flat list to split.
    size:
        Maximum number of elements per chunk.

    Returns
    -------
    list[list]
        List of chunks; last chunk may be smaller than *size*.
    """
    if size <= 0:
        raise ValueError(f"batch size must be positive, got {size}")
    n = math.ceil(len(items) / size)
    return [items[i * size : (i + 1) * size] for i in range(n)]
