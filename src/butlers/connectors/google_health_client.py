"""Async Google Health API client.

Thin httpx wrapper for ``https://health.googleapis.com/v4/`` used by the
Google Health connector. It handles:

- Bearer-token authorization via an injected access-token provider.
- Automatic retry once after HTTP 401 by invalidating the in-memory token
  cache and asking the provider for a fresh token. If the retry also 401s
  the error propagates so the connector can mark the account revoked.
- HTTP 429 handling — returns a :class:`GoogleHealthRateLimitError` carrying
  the ``Retry-After`` value (or ``None`` to signal "use exponential
  backoff").
- Rate-limit header capture — returns the latest observed headers so the
  caller can expose them as metrics.

Auth model
----------

The client does NOT implement OAuth token refresh directly. Per
``about/heart-and-soul/security.md`` Tier-2 contract, Google Health is a
Tier-2 connector and MUST delegate refresh-token handling to the shared
Google credential pipeline (``google_credentials.load_google_credentials``
plus the companion-entity refresh-token primitive).  Callers pass an async
callable ``token_fetcher()`` that returns a fresh access-token string. The
client caches the most recent token in memory until a 401 occurs; it never
writes tokens to disk, logs, or the database.

Endpoint surface
----------------

Per §1 of ``openspec/changes/google-health-connector/tasks.md`` the exact
endpoint paths on ``health.googleapis.com/v4`` are confirmed at
implementation time and recorded in ``research-notes.md``. This client
exposes a generic :meth:`GoogleHealthClient.get_json` method plus
convenience wrappers named after the data-type bundles the connector
polls. Each wrapper takes ``since`` / ``until`` RFC3339 timestamps.

Usage::

    async def _fetcher() -> str:
        return await resolve_google_access_token(pool, entity_id)

    async with GoogleHealthClient(token_fetcher=_fetcher) as client:
        data = await client.get_sleep_sessions(since=..., until=...)
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)


GOOGLE_HEALTH_API_BASE = "https://health.googleapis.com/v4"
"""Base URL for Google Health API calls.

Per the Google Health API migration guide at
https://developers.google.com/health/migration the ``/v4`` prefix is
required. Specific endpoint paths are confirmed at implementation time
(see ``research-notes.md``).
"""


# Rate-limit back-off parameters used when no Retry-After header is returned.
_BACKOFF_INITIAL_S: float = 30.0
_BACKOFF_MAX_S: float = 600.0
_BACKOFF_JITTER_FRACTION: float = 0.25


TokenFetcher = Callable[[], Awaitable[str]]
"""Async callable returning a valid Google OAuth access token.

Implementations MUST:

- Return a freshly-minted access token when invalidated.
- Raise :class:`GoogleHealthCredentialError` (or a subclass) when the
  refresh token itself is invalid / revoked — the connector uses this to
  transition to ``degraded``.
- Never write tokens to disk or logs.
"""


class GoogleHealthError(Exception):
    """Base exception for Google Health client errors."""


class GoogleHealthCredentialError(GoogleHealthError):
    """Raised when Google Health authentication fails and cannot be recovered.

    The connector treats this as a terminal auth failure — it transitions to
    ``degraded`` and re-checks scopes / credentials periodically.
    """


class GoogleHealthRateLimitError(GoogleHealthError):
    """Raised when Google Health API returns HTTP 429.

    When Google Health returns a ``Retry-After`` header its integer value
    (seconds) is stored in :attr:`retry_after`. When absent, callers should
    fall back to exponential backoff with jitter — see
    :func:`exponential_backoff_delay`.
    """

    def __init__(self, retry_after: float | None) -> None:
        if retry_after is not None:
            super().__init__(f"Rate limited; retry after {retry_after:.1f}s")
        else:
            super().__init__("Rate limited; no Retry-After header")
        self.retry_after = retry_after


class GoogleHealthSourcePreconditionError(GoogleHealthError):
    """Raised when Google Health rejects the source account state."""

    def __init__(self, reason: str, message: str, redirect_uri: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.redirect_uri = redirect_uri


def exponential_backoff_delay(attempt: int) -> float:
    """Compute the next exponential-backoff delay with jitter.

    The connector uses this when ``Retry-After`` is absent from a 429
    response, or for generic transient failures.  ``attempt`` is 1-indexed.
    """
    if attempt < 1:
        attempt = 1
    base = min(_BACKOFF_INITIAL_S * (2 ** (attempt - 1)), _BACKOFF_MAX_S)
    jitter = base * _BACKOFF_JITTER_FRACTION
    return max(0.0, base + random.uniform(-jitter, jitter))


class GoogleHealthClient:
    """Thin async httpx wrapper over the Google Health API.

    The client caches the current access token in memory and invalidates it
    once after a 401.  All retry / backoff *above* single-request 401
    recovery is the connector's responsibility.
    """

    def __init__(
        self,
        *,
        token_fetcher: TokenFetcher,
        base_url: str = GOOGLE_HEALTH_API_BASE,
        client: httpx.AsyncClient | None = None,
        request_timeout_s: float = 30.0,
    ) -> None:
        self._token_fetcher = token_fetcher
        self._base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self._http = client or httpx.AsyncClient(timeout=request_timeout_s)
        self._cached_token: str | None = None
        # Most recent observed rate-limit headers. Callers (the connector's
        # metrics reporter) read these after each successful request.
        self._last_rate_limit_headers: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> GoogleHealthClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned_client:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def last_rate_limit_headers(self) -> dict[str, str]:
        """Snapshot of the most recent rate-limit headers observed.

        The caller is expected to label and emit these as Prometheus
        metrics per-resource. Common keys seen on Google APIs:
        ``X-RateLimit-Remaining``, ``X-RateLimit-Reset``, ``Retry-After``.
        The dict is a copy; mutating it has no effect on internal state.
        """
        return dict(self._last_rate_limit_headers)

    def invalidate_token(self) -> None:
        """Drop the cached access token so the next request refetches it.

        Called by the connector's 401 handler (if it exposes error-level
        visibility) or implicitly within :meth:`get_json` when a 401 is
        observed. In-memory only; no persistence side-effects.
        """
        self._cached_token = None

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue an authenticated GET and return the JSON body.

        Parameters
        ----------
        path:
            Path relative to the configured ``base_url`` (e.g. ``"/users/me/sessions"``).
            Leading slash is optional.
        params:
            Optional query-string parameters.

        Raises
        ------
        GoogleHealthCredentialError
            On a persistent 401 — the connector marks the account revoked.
        GoogleHealthRateLimitError
            On HTTP 429.
        httpx.HTTPStatusError
            On any other non-2xx status.
        """
        return await self._request_json("GET", path, params=params)

    async def post_json(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue an authenticated POST with a JSON body and return JSON."""
        return await self._request_json("POST", path, params=params, json_body=json_body)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue an authenticated request and return the JSON body."""
        url = f"{self._base_url}/{path.lstrip('/')}"

        for attempt in range(1, 3):  # initial + one retry after token refresh
            token = await self._get_token(force_refresh=(attempt == 2))
            headers = {"Authorization": f"Bearer {token}"}

            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params or {},
                    json=json_body,
                    headers=headers,
                )
            except httpx.TransportError as exc:
                raise GoogleHealthError(f"Google Health transport error for {path}: {exc}") from exc

            # Capture rate-limit headers regardless of status.
            self._capture_rate_limit_headers(response)

            if response.status_code == 200:
                return response.json()

            if response.status_code == 204:
                return {}

            if response.status_code == 401:
                if attempt == 1:
                    logger.info(
                        "Google Health: received 401 on %s — invalidating token and retrying",
                        path,
                    )
                    self.invalidate_token()
                    continue
                raise GoogleHealthCredentialError(
                    f"Google Health authorization failed after refresh for {path}"
                )

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                raise GoogleHealthRateLimitError(retry_after)

            if response.status_code == 400:
                precondition = _parse_source_precondition(response)
                if precondition is not None:
                    raise precondition

            # Any other error is terminal for this request.
            response.raise_for_status()

        raise GoogleHealthError(f"Google Health: exhausted retries for {path}")

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_token(self, *, force_refresh: bool) -> str:
        """Return a usable access token, possibly re-fetching on demand."""
        if force_refresh or self._cached_token is None:
            token = await self._token_fetcher()
            if not token:
                raise GoogleHealthCredentialError(
                    "Google Health token_fetcher returned empty token"
                )
            self._cached_token = token
        return self._cached_token

    # ------------------------------------------------------------------
    # Rate-limit capture
    # ------------------------------------------------------------------

    def _capture_rate_limit_headers(self, response: httpx.Response) -> None:
        """Record any rate-limit-related headers for downstream metrics."""
        headers: dict[str, str] = {}
        for name in ("Retry-After", "X-RateLimit-Remaining", "X-RateLimit-Reset"):
            value = response.headers.get(name)
            if value is not None:
                headers[name] = value
        self._last_rate_limit_headers = headers


def _parse_retry_after(raw: str | None) -> float | None:
    """Parse a ``Retry-After`` header into a float seconds value.

    Google Health returns either an integer number of seconds or an
    HTTP-date. For the HTTP-date case we do not try to convert; returning
    ``None`` signals the caller to fall back to exponential backoff.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _parse_source_precondition(
    response: httpx.Response,
) -> GoogleHealthSourcePreconditionError | None:
    """Parse Google Health source-state precondition errors from a 400 response."""
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    if error.get("status") != "FAILED_PRECONDITION":
        return None

    reason = "failed_precondition"
    redirect_uri: str | None = None
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        detail_reason = detail.get("reason")
        if isinstance(detail_reason, str) and detail_reason:
            reason = detail_reason
        metadata = detail.get("metadata")
        if isinstance(metadata, dict):
            raw_redirect_uri = metadata.get("redirect_uri")
            if isinstance(raw_redirect_uri, str):
                redirect_uri = raw_redirect_uri

    message = str(error.get("message") or "Google Health source precondition failed")
    return GoogleHealthSourcePreconditionError(reason, message, redirect_uri)


__all__ = [
    "GOOGLE_HEALTH_API_BASE",
    "GoogleHealthClient",
    "GoogleHealthCredentialError",
    "GoogleHealthError",
    "GoogleHealthRateLimitError",
    "GoogleHealthSourcePreconditionError",
    "TokenFetcher",
    "exponential_backoff_delay",
]
