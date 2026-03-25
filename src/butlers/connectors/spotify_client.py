"""Async Spotify Web API client with automatic token refresh and rate limit handling.

This module provides a lightweight async HTTP client wrapping the Spotify Web API
endpoints needed by the Spotify connector:

- ``get_me()`` — Retrieve the authenticated user's Spotify profile.
- ``get_currently_playing()`` — Poll the user's current playback state.
- ``get_recently_played(after)`` — Fetch recently played tracks with cursor-based pagination.

Auth model:
- Bearer token resolved from ``CredentialStore`` at construction time.
- Proactive refresh 5 minutes before expiry using stored ``SPOTIFY_TOKEN_EXPIRES_AT``.
- Automatic retry on HTTP 401: exchange refresh token for new access token via
  ``POST https://accounts.spotify.com/api/token``, update ``CredentialStore``,
  retry once.
- Rate limit handling: honor ``Retry-After`` header on HTTP 429; fall back to
  exponential backoff with jitter (initial 30 s, max 600 s) when no header present.

Environment / CredentialStore keys:
- ``SPOTIFY_CLIENT_ID``          — Spotify OAuth app client ID.
- ``SPOTIFY_ACCESS_TOKEN``       — Short-lived access token (expiry ~1 h).
- ``SPOTIFY_REFRESH_TOKEN``      — Long-lived refresh token.
- ``SPOTIFY_TOKEN_EXPIRES_AT``   — UTC ISO8601 expiry timestamp of access token.

Usage::

    async with SpotifyClient(credential_store=store) as client:
        profile = await client.get_me()
        current = await client.get_currently_playing()
        recent  = await client.get_recently_played(after=cursor_ms)
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SPOTIFY_API_BASE = "https://api.spotify.com/v1"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

# Proactively refresh if the token expires within this window.
_PROACTIVE_REFRESH_WINDOW_S = 5 * 60  # 5 minutes

# Rate-limit back-off parameters (used when no Retry-After header present).
_BACKOFF_INITIAL_S: float = 30.0
_BACKOFF_MAX_S: float = 600.0
_BACKOFF_JITTER_FRACTION = 0.25  # ± 25 % jitter

# CredentialStore key names
_KEY_ACCESS_TOKEN = "SPOTIFY_ACCESS_TOKEN"
_KEY_REFRESH_TOKEN = "SPOTIFY_REFRESH_TOKEN"
_KEY_CLIENT_ID = "SPOTIFY_CLIENT_ID"
_KEY_EXPIRES_AT = "SPOTIFY_TOKEN_EXPIRES_AT"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SpotifyAuthError(Exception):
    """Raised when Spotify authentication fails and cannot be recovered automatically.

    This indicates that the refresh token is invalid or has been revoked, and the
    user must re-authorize via the dashboard OAuth flow.
    """


class SpotifyRateLimitError(Exception):
    """Raised when Spotify returns HTTP 429 and the caller should back off.

    Attributes
    ----------
    retry_after_s:
        Seconds to wait before retrying, derived from the ``Retry-After`` header
        or calculated via exponential backoff with jitter.
    """

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"Spotify rate limited; retry after {retry_after_s:.1f}s")
        self.retry_after_s = retry_after_s


class SpotifyAPIError(Exception):
    """Raised for unexpected Spotify API errors (non-401/429 status codes)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Spotify API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


# ---------------------------------------------------------------------------
# Helper: exponential back-off with jitter
# ---------------------------------------------------------------------------


def _jittered_backoff(
    attempt: int,
    initial: float = _BACKOFF_INITIAL_S,
    maximum: float = _BACKOFF_MAX_S,
) -> float:
    """Return a back-off delay with ± 25 % uniform jitter.

    Parameters
    ----------
    attempt:
        Zero-based retry attempt index.
    initial:
        Base delay in seconds.
    maximum:
        Upper cap on the delay (before jitter).
    """
    base = min(initial * (2**attempt), maximum)
    jitter = base * _BACKOFF_JITTER_FRACTION
    return base + random.uniform(-jitter, jitter)  # noqa: S311


# ---------------------------------------------------------------------------
# SpotifyClient
# ---------------------------------------------------------------------------


class SpotifyClient:
    """Async HTTP client for the Spotify Web API.

    Parameters
    ----------
    credential_store:
        A ``CredentialStore`` instance used to resolve and persist OAuth tokens.
    http_client:
        Optional pre-built ``httpx.AsyncClient``.  If ``None``, a new client is
        created (and closed) by the context manager.

    Usage (async context manager — preferred)::

        async with SpotifyClient(credential_store=store) as client:
            profile = await client.get_me()

    Usage (manual lifetime)::

        client = SpotifyClient(credential_store=store)
        await client.open()
        try:
            profile = await client.get_me()
        finally:
            await client.close()
    """

    def __init__(
        self,
        *,
        credential_store: CredentialStore,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credential_store = credential_store
        self._http_client = http_client
        self._owns_client = http_client is None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._client_id: str | None = None
        self._expires_at: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the HTTP client and load credentials from the store."""
        if self._owns_client:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        await self._load_credentials()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> SpotifyClient:
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------

    async def _load_credentials(self) -> None:
        """Resolve Spotify credentials from the credential store."""
        self._access_token = await self._credential_store.resolve(_KEY_ACCESS_TOKEN)
        self._refresh_token = await self._credential_store.resolve(_KEY_REFRESH_TOKEN)
        self._client_id = await self._credential_store.resolve(_KEY_CLIENT_ID)

        expires_at_str = await self._credential_store.resolve(_KEY_EXPIRES_AT)
        if expires_at_str:
            try:
                self._expires_at = datetime.fromisoformat(expires_at_str)
                if self._expires_at.tzinfo is None:
                    self._expires_at = self._expires_at.replace(tzinfo=UTC)
            except ValueError:
                logger.warning(
                    "Could not parse SPOTIFY_TOKEN_EXPIRES_AT=%r; treating as expired "
                    "and forcing refresh",
                    expires_at_str,
                )
                # Force proactive refresh by marking the token as already expired.
                self._expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            self._expires_at = None

    def _token_needs_refresh(self) -> bool:
        """Return True if the access token is expired or within the proactive window."""
        if self._access_token is None:
            return True
        if self._expires_at is None:
            return False  # No expiry info — assume still valid until we get a 401
        now = datetime.now(UTC)
        return now >= (self._expires_at - timedelta(seconds=_PROACTIVE_REFRESH_WINDOW_S))

    async def _refresh_access_token(self) -> None:
        """Exchange the stored refresh token for a new access token.

        Raises
        ------
        SpotifyAuthError
            If the refresh token is invalid or the request fails.
        """
        if not self._refresh_token:
            raise SpotifyAuthError(
                "No refresh token available; user must re-authorize via dashboard."
            )
        if not self._client_id:
            raise SpotifyAuthError(
                "No Spotify client_id available; configure via dashboard settings."
            )

        assert self._http_client is not None, "HTTP client not initialized"

        logger.info("Refreshing Spotify access token")
        response = await self._http_client.post(
            _SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            body = response.text
            logger.error(
                "Spotify token refresh failed: status=%d body=%r",
                response.status_code,
                body[:200],
            )
            raise SpotifyAuthError(
                f"Spotify token refresh failed (HTTP {response.status_code}). "
                "Re-connect Spotify via dashboard settings."
            )

        try:
            data: dict[str, Any] = response.json()
            new_access_token: str = data["access_token"]
        except Exception as exc:
            logger.error(
                "Spotify token refresh returned malformed response: %r",
                response.text[:200],
            )
            raise SpotifyAuthError(
                "Spotify token refresh returned an unexpected response. "
                "Re-connect Spotify via dashboard settings."
            ) from exc
        # Spotify may or may not rotate the refresh token
        new_refresh_token: str = data.get("refresh_token", self._refresh_token)
        expires_in: int = data.get("expires_in", 3600)
        new_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        # Persist to CredentialStore
        await self._credential_store.store(
            _KEY_ACCESS_TOKEN,
            new_access_token,
            category="spotify",
            description="Spotify OAuth access token",
            is_sensitive=True,
            expires_at=new_expires_at,
        )
        await self._credential_store.store(
            _KEY_REFRESH_TOKEN,
            new_refresh_token,
            category="spotify",
            description="Spotify OAuth refresh token",
            is_sensitive=True,
        )
        await self._credential_store.store(
            _KEY_EXPIRES_AT,
            new_expires_at.isoformat(),
            category="spotify",
            description="Spotify access token expiry timestamp (UTC ISO8601)",
            is_sensitive=False,
        )

        # Update in-memory state
        self._access_token = new_access_token
        self._refresh_token = new_refresh_token
        self._expires_at = new_expires_at
        logger.info("Spotify access token refreshed; expires_at=%s", new_expires_at.isoformat())

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Perform an authenticated GET request against the Spotify Web API.

        Handles:
        - Proactive token refresh before expiry window.
        - Automatic retry on HTTP 401 (one refresh attempt).
        - ``SpotifyRateLimitError`` on HTTP 429.
        - ``SpotifyAPIError`` on other error status codes.

        Returns ``None`` for HTTP 204 (No Content — valid Spotify empty response).

        Parameters
        ----------
        path:
            API path (e.g. ``"/me/player/currently-playing"``).
        params:
            Optional query string parameters.

        Returns
        -------
        dict or None
            Parsed JSON response, or ``None`` for empty-body responses.
        """
        assert self._http_client is not None, "HTTP client not initialized; call open() first"

        # Proactive refresh
        if self._token_needs_refresh():
            logger.debug("Proactive token refresh triggered for path=%r", path)
            await self._refresh_access_token()

        url = f"{_SPOTIFY_API_BASE}{path}"
        for attempt in range(2):  # first attempt + one retry after 401
            headers = {"Authorization": f"Bearer {self._access_token}"}
            response = await self._http_client.get(url, headers=headers, params=params)

            if response.status_code == 200:
                return response.json()  # type: ignore[no-any-return]

            if response.status_code == 204:
                return None  # No content — valid for currently-playing when nothing is playing

            if response.status_code == 401:
                if attempt == 0:
                    logger.info(
                        "Received 401 from Spotify; refreshing token and retrying path=%r",
                        path,
                    )
                    await self._refresh_access_token()
                    continue
                # Second 401 after refresh → auth is truly broken
                raise SpotifyAuthError(
                    "Spotify API returned 401 after token refresh. "
                    "Spotify authorization expired. Re-connect via dashboard settings."
                )

            if response.status_code == 429:
                # Always use attempt=0 for rate-limit backoff: the outer `attempt`
                # counter tracks 401-refresh retries, not rate-limit retries, so
                # passing it here would double the initial backoff on retry attempt 1.
                retry_after_s = self._parse_retry_after(response, attempt=0)
                raise SpotifyRateLimitError(retry_after_s)

            raise SpotifyAPIError(response.status_code, response.text[:500])

        # Should be unreachable
        raise SpotifyAuthError("Unexpected auth loop exit")  # pragma: no cover

    @staticmethod
    def _parse_retry_after(response: httpx.Response, *, attempt: int) -> float:
        """Parse the ``Retry-After`` header or compute exponential backoff."""
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass
        return _jittered_backoff(attempt, initial=_BACKOFF_INITIAL_S, maximum=_BACKOFF_MAX_S)

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """Fetch the authenticated user's Spotify profile.

        Returns
        -------
        dict
            Spotify user profile object.  Key fields:
            - ``id`` — the user's Spotify user ID.
            - ``display_name`` — human-readable name.
            - ``product`` — ``"premium"`` or ``"free"``.
            - ``country`` — ISO 3166-1 alpha-2 country code.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        result = await self._get("/me")
        if result is None:
            raise SpotifyAPIError(204, "GET /me returned 204 No Content")
        return result

    async def get_currently_playing(self) -> dict[str, Any] | None:
        """Fetch the user's currently playing track.

        Returns ``None`` when nothing is playing (HTTP 204 or empty item).

        Returns
        -------
        dict or None
            Spotify ``currently-playing`` object, or ``None`` if no active playback.
            Key fields when playing:
            - ``is_playing`` — ``True`` when actively playing.
            - ``item`` — track object (``name``, ``artists``, ``album``, ``id``, ``duration_ms``).
            - ``context`` — playback context (``type``, ``uri``).
            - ``progress_ms`` — current playback position.
            - ``timestamp`` — server-side timestamp (ms since epoch).

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        data = await self._get(
            "/me/player/currently-playing",
            params={"additional_types": "track"},
        )
        # `_get` returns None for HTTP 204 (no content). Additionally, Spotify may
        # return HTTP 200 with `item` set to null (e.g., private session or ads).
        # In both cases we normalize to None to match the documented contract.
        if data is None:
            return None
        if data.get("item") is None:
            return None
        return data

    async def get_recently_played(
        self,
        after: int | None = None,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch the user's recently played tracks.

        Parameters
        ----------
        after:
            Unix timestamp in milliseconds.  Only tracks played after this
            timestamp are returned.  Use ``None`` to return the most recent
            tracks with no time filter.
        limit:
            Maximum number of items to return (1–50, default 50).

        Returns
        -------
        dict
            Spotify ``recently-played`` paging object.  Key fields:
            - ``items`` — list of play history objects, each with a ``track``
              and ``played_at`` (RFC3339 string).
            - ``cursors.after`` — cursor for the next page.
            - ``cursors.before`` — cursor for the previous page.
            - ``next`` — URL of the next page (``None`` if this is the last page).

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after

        result = await self._get("/me/player/recently-played", params=params)
        if result is None:
            # 204 is not expected here, but handle gracefully.
            return {"items": [], "cursors": {}, "next": None}
        return result
