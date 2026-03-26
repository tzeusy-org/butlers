"""Async Spotify Web API client with automatic token refresh and rate limit handling.

This module provides a lightweight async HTTP client wrapping the Spotify Web API
endpoints needed by the Spotify connector:

- ``get_me()`` — Retrieve the authenticated user's Spotify profile.
- ``get_currently_playing()`` — Poll the user's current playback state.
- ``get_recently_played(after)`` — Fetch recently played tracks with cursor-based pagination.
- ``search()`` — Search Spotify catalog.
- ``get_playback_state()`` — Get full playback state (device, shuffle, repeat).
- ``play()`` — Start/resume playback on a device.
- ``pause()`` — Pause playback.
- ``skip_to_next()`` — Skip to the next track.
- ``skip_to_previous()`` — Skip to the previous track.
- ``seek_to_position()`` — Seek to a position in the current track.
- ``set_volume()`` — Set playback volume.
- ``set_shuffle()`` — Toggle shuffle mode.
- ``set_repeat()`` — Set repeat mode.
- ``get_queue()`` — Get the user's playback queue.
- ``add_to_queue()`` — Add an item to the playback queue.
- ``get_top_items()`` — Retrieve the user's top artists or tracks.
- ``get_recommendations()`` — Get track recommendations based on seeds.
- ``get_user_playlists()`` — Get current user's playlists.
- ``get_playlist()`` — Get a playlist by ID.
- ``create_playlist()`` — Create a new playlist.
- ``add_tracks_to_playlist()`` — Add tracks to a playlist.
- ``remove_tracks_from_playlist()`` — Remove tracks from a playlist.
- ``get_saved_tracks()`` — Get the user's liked/saved tracks.
- ``save_tracks()`` — Save tracks to the user's library.
- ``remove_saved_tracks()`` — Remove tracks from the user's library.
- ``check_saved_tracks()`` — Check if tracks are saved.

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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated HTTP request against the Spotify Web API.

        Handles:
        - Proactive token refresh before expiry window.
        - Automatic retry on HTTP 401 (one refresh attempt).
        - ``SpotifyRateLimitError`` on HTTP 429.
        - ``SpotifyAPIError`` on other error status codes.

        Returns ``None`` for HTTP 204 (No Content — valid Spotify empty response).

        Parameters
        ----------
        method:
            HTTP method (e.g. ``"GET"``, ``"PUT"``, ``"POST"``, ``"DELETE"``).
        path:
            API path (e.g. ``"/me/player/currently-playing"``).
        params:
            Optional query string parameters.
        json:
            Optional JSON body payload (ignored for GET requests).

        Returns
        -------
        Any
            Parsed JSON response (dict or list), or ``None`` for empty-body responses.
        """
        assert self._http_client is not None, "HTTP client not initialized; call open() first"

        # Proactive refresh
        if self._token_needs_refresh():
            logger.debug("Proactive token refresh triggered for %s path=%r", method.upper(), path)
            await self._refresh_access_token()

        url = f"{_SPOTIFY_API_BASE}{path}"
        for attempt in range(2):  # first attempt + one retry after 401
            headers = {"Authorization": f"Bearer {self._access_token}"}
            response = await self._http_client.request(
                method, url, headers=headers, params=params, json=json
            )

            if response.status_code in (200, 201):
                return response.json()  # type: ignore[no-any-return]

            if response.status_code == 204:
                return None  # No content — valid for many Spotify endpoints

            if response.status_code == 401:
                if attempt == 0:
                    logger.info(
                        "Received 401 from Spotify; refreshing token and retrying %s path=%r",
                        method.upper(),
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

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated GET request against the Spotify Web API.

        Thin wrapper around :meth:`_request` for GET calls.

        Returns ``None`` for HTTP 204 (No Content — valid Spotify empty response).

        Parameters
        ----------
        path:
            API path (e.g. ``"/me/player/currently-playing"``).
        params:
            Optional query string parameters.

        Returns
        -------
        Any
            Parsed JSON response (dict or list), or ``None`` for empty-body responses.
        """
        return await self._request("GET", path, params=params)

    async def _put(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated PUT request against the Spotify Web API.

        Thin wrapper around :meth:`_request` for PUT calls.

        Returns ``None`` for HTTP 204 (No Content).

        Parameters
        ----------
        path:
            API path (e.g. ``"/me/player/play"``).
        params:
            Optional query string parameters.
        json:
            Optional JSON body payload.

        Returns
        -------
        Any
            Parsed JSON response, or ``None`` for empty-body responses.
        """
        return await self._request("PUT", path, params=params, json=json)

    async def _post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated POST request against the Spotify Web API.

        Thin wrapper around :meth:`_request` for POST calls.

        Returns ``None`` for HTTP 204 (No Content).

        Parameters
        ----------
        path:
            API path (e.g. ``"/users/{user_id}/playlists"``).
        params:
            Optional query string parameters.
        json:
            Optional JSON body payload.

        Returns
        -------
        Any
            Parsed JSON response, or ``None`` for empty-body responses.
        """
        return await self._request("POST", path, params=params, json=json)

    async def _delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated DELETE request against the Spotify Web API.

        Thin wrapper around :meth:`_request` for DELETE calls.

        Returns ``None`` for HTTP 204 (No Content).

        Parameters
        ----------
        path:
            API path (e.g. ``"/playlists/{id}/tracks"``).
        params:
            Optional query string parameters.
        json:
            Optional JSON body payload.

        Returns
        -------
        Any
            Parsed JSON response, or ``None`` for empty-body responses.
        """
        return await self._request("DELETE", path, params=params, json=json)

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

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        market: str | None = None,
    ) -> dict[str, Any]:
        """Search the Spotify catalog.

        Parameters
        ----------
        query:
            Search keywords (and optional field filters, e.g. ``"artist:radiohead"``).
        types:
            List of item types to search.  Defaults to ``["track"]``.
            Supported values: ``"album"``, ``"artist"``, ``"playlist"``,
            ``"track"``, ``"show"``, ``"episode"``, ``"audiobook"``.
        limit:
            Maximum number of results per type (1–50, default 20).
        offset:
            Offset for pagination (default 0).
        market:
            Optional ISO 3166-1 alpha-2 country code for market filtering.

        Returns
        -------
        dict
            Spotify search result object.  Keys depend on requested types
            (e.g. ``"tracks"``, ``"artists"``), each containing a paging object.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        if types is None:
            types = ["track"]
        params: dict[str, Any] = {
            "q": query,
            "type": ",".join(types),
            "limit": limit,
            "offset": offset,
        }
        if market is not None:
            params["market"] = market
        result = await self._get("/search", params=params)
        if result is None:
            return {}
        return result

    # ------------------------------------------------------------------
    # Playback state
    # ------------------------------------------------------------------

    async def get_playback_state(self) -> dict[str, Any] | None:
        """Get information about the user's current playback state.

        Returns the full playback context including the active device, shuffle
        and repeat modes, and the current track.

        Returns
        -------
        dict or None
            Spotify playback state object, or ``None`` if there is no active
            device (HTTP 204).

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        return await self._get("/me/player")

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    async def play(
        self,
        *,
        device_id: str | None = None,
        context_uri: str | None = None,
        uris: list[str] | None = None,
        offset: dict[str, Any] | None = None,
        position_ms: int | None = None,
    ) -> None:
        """Start or resume playback on the user's active device.

        Parameters
        ----------
        device_id:
            Target Spotify device ID.  Uses currently active device if omitted.
        context_uri:
            Spotify URI for an album, artist, or playlist to play.
        uris:
            List of track/episode Spotify URIs to play.
        offset:
            Offset into the context (e.g. ``{"position": 5}`` or
            ``{"uri": "spotify:track:..."}``.
        position_ms:
            Playback start position in milliseconds.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {}
        if device_id is not None:
            params["device_id"] = device_id

        body: dict[str, Any] = {}
        if context_uri is not None:
            body["context_uri"] = context_uri
        if uris is not None:
            body["uris"] = uris
        if offset is not None:
            body["offset"] = offset
        if position_ms is not None:
            body["position_ms"] = position_ms

        await self._put("/me/player/play", params=params or None, json=body or None)

    async def pause(self, *, device_id: str | None = None) -> None:
        """Pause playback on the user's active device.

        Parameters
        ----------
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] | None = None
        if device_id is not None:
            params = {"device_id": device_id}
        await self._put("/me/player/pause", params=params)

    async def skip_to_next(self, *, device_id: str | None = None) -> None:
        """Skip to the next track in the queue.

        Parameters
        ----------
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] | None = None
        if device_id is not None:
            params = {"device_id": device_id}
        await self._post("/me/player/next", params=params)

    async def skip_to_previous(self, *, device_id: str | None = None) -> None:
        """Skip to the previous track or restart the current track.

        Parameters
        ----------
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] | None = None
        if device_id is not None:
            params = {"device_id": device_id}
        await self._post("/me/player/previous", params=params)

    async def seek_to_position(self, position_ms: int, *, device_id: str | None = None) -> None:
        """Seek to a position in the currently playing track.

        Parameters
        ----------
        position_ms:
            Position in milliseconds to seek to.
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"position_ms": position_ms}
        if device_id is not None:
            params["device_id"] = device_id
        await self._put("/me/player/seek", params=params)

    async def set_volume(self, volume_percent: int, *, device_id: str | None = None) -> None:
        """Set the volume for the user's active device.

        Parameters
        ----------
        volume_percent:
            Volume level (0–100).
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"volume_percent": volume_percent}
        if device_id is not None:
            params["device_id"] = device_id
        await self._put("/me/player/volume", params=params)

    async def set_shuffle(self, state: bool, *, device_id: str | None = None) -> None:
        """Toggle shuffle mode on the user's active device.

        Parameters
        ----------
        state:
            ``True`` to enable shuffle, ``False`` to disable.
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"state": "true" if state else "false"}
        if device_id is not None:
            params["device_id"] = device_id
        await self._put("/me/player/shuffle", params=params)

    async def set_repeat(self, state: str, *, device_id: str | None = None) -> None:
        """Set the repeat mode for playback.

        Parameters
        ----------
        state:
            One of ``"track"`` (repeat current track), ``"context"`` (repeat
            the current context), or ``"off"`` (turn off repeat).
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"state": state}
        if device_id is not None:
            params["device_id"] = device_id
        await self._put("/me/player/repeat", params=params)

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    async def get_queue(self) -> dict[str, Any]:
        """Get the list of objects that make up the user's queue.

        Returns
        -------
        dict
            Queue object with ``currently_playing`` and ``queue`` (list of
            tracks/episodes) keys.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        result = await self._get("/me/player/queue")
        if result is None:
            return {"currently_playing": None, "queue": []}
        return result

    async def add_to_queue(self, uri: str, *, device_id: str | None = None) -> None:
        """Add an item to the end of the user's playback queue.

        Parameters
        ----------
        uri:
            Spotify URI of the track or episode to add.
        device_id:
            Target device ID.  Uses currently active device if omitted.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"uri": uri}
        if device_id is not None:
            params["device_id"] = device_id
        await self._post("/me/player/queue", params=params)

    # ------------------------------------------------------------------
    # Top items and recommendations
    # ------------------------------------------------------------------

    async def get_top_items(
        self,
        item_type: str,
        *,
        time_range: str = "medium_term",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get the user's top artists or tracks based on calculated affinity.

        Parameters
        ----------
        item_type:
            Either ``"artists"`` or ``"tracks"``.
        time_range:
            One of ``"short_term"`` (4 weeks), ``"medium_term"`` (6 months, default),
            or ``"long_term"`` (all time).
        limit:
            Maximum number of items to return (1–50, default 20).
        offset:
            Offset for pagination (default 0).

        Returns
        -------
        dict
            Spotify paging object with ``items``, ``total``, ``limit``, ``offset``
            and pagination cursors.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {
            "time_range": time_range,
            "limit": limit,
            "offset": offset,
        }
        result = await self._get(f"/me/top/{item_type}", params=params)
        if result is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        return result

    async def get_recommendations(
        self,
        *,
        seed_artists: list[str] | None = None,
        seed_genres: list[str] | None = None,
        seed_tracks: list[str] | None = None,
        limit: int = 20,
        **audio_features: Any,
    ) -> dict[str, Any]:
        """Get track recommendations based on seeds and optional audio feature tuning.

        At least one seed (artists, genres, or tracks) must be provided; the
        combined total must not exceed 5 seed values.

        This method gracefully handles 403/404 responses (e.g., the endpoint
        is not available in the user's market or the user lacks the required
        subscription) and returns an empty recommendations object instead of
        raising.

        Parameters
        ----------
        seed_artists:
            List of Spotify artist IDs (up to 5 combined with other seeds).
        seed_genres:
            List of genre names available from the Recommendations API.
        seed_tracks:
            List of Spotify track IDs.
        limit:
            Number of recommended tracks to return (1–100, default 20).
        **audio_features:
            Optional audio feature constraints as keyword arguments, e.g.
            ``min_energy=0.4``, ``target_valence=0.8``.  See the Spotify
            Recommendations API docs for the full list of supported attributes.

        Returns
        -------
        dict
            Spotify recommendations object with a ``tracks`` list.  Returns
            ``{"tracks": []}`` on 403/404.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes (not 403/404).
        """
        params: dict[str, Any] = {"limit": limit}
        if seed_artists:
            params["seed_artists"] = ",".join(seed_artists)
        if seed_genres:
            params["seed_genres"] = ",".join(seed_genres)
        if seed_tracks:
            params["seed_tracks"] = ",".join(seed_tracks)
        params.update(audio_features)

        try:
            result = await self._get("/recommendations", params=params)
        except SpotifyAPIError as exc:
            if exc.status_code in (403, 404):
                logger.warning(
                    "get_recommendations: gracefully handling HTTP %d — "
                    "endpoint unavailable in this market or requires premium",
                    exc.status_code,
                )
                return {"tracks": []}
            raise
        if result is None:
            return {"tracks": []}
        return result

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    async def get_user_playlists(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Get a list of the current user's playlists.

        Parameters
        ----------
        limit:
            Maximum number of playlists to return (1–50, default 50).
        offset:
            Offset for pagination (default 0).

        Returns
        -------
        dict
            Spotify paging object with playlist summaries in ``items``.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        result = await self._get("/me/playlists", params=params)
        if result is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        return result

    async def get_playlist(
        self, playlist_id: str, *, fields: str | None = None, market: str | None = None
    ) -> dict[str, Any]:
        """Get a playlist by its Spotify ID.

        Parameters
        ----------
        playlist_id:
            Spotify playlist ID.
        fields:
            Optional comma-separated list of fields to include in the response
            (e.g. ``"name,tracks.items(track(name,artists))"``.
        market:
            Optional ISO 3166-1 alpha-2 country code.

        Returns
        -------
        dict
            Spotify playlist object.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {}
        if fields is not None:
            params["fields"] = fields
        if market is not None:
            params["market"] = market
        result = await self._get(f"/playlists/{playlist_id}", params=params or None)
        if result is None:
            raise SpotifyAPIError(204, f"GET /playlists/{playlist_id} returned 204 No Content")
        return result

    async def create_playlist(
        self,
        user_id: str,
        name: str,
        *,
        public: bool = True,
        collaborative: bool = False,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new playlist for the specified user.

        Parameters
        ----------
        user_id:
            Spotify user ID who will own the playlist.
        name:
            Name of the new playlist.
        public:
            Whether the playlist is public (default ``True``).
        collaborative:
            Whether the playlist is collaborative (default ``False``).
        description:
            Playlist description (default empty).

        Returns
        -------
        dict
            Newly created Spotify playlist object.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        body: dict[str, Any] = {
            "name": name,
            "public": public,
            "collaborative": collaborative,
            "description": description,
        }
        result = await self._post(f"/users/{user_id}/playlists", json=body)
        if result is None:
            raise SpotifyAPIError(204, f"POST /users/{user_id}/playlists returned 204 No Content")
        return result

    async def add_tracks_to_playlist(
        self,
        playlist_id: str,
        uris: list[str],
        *,
        position: int | None = None,
    ) -> dict[str, Any]:
        """Add one or more tracks/episodes to a playlist.

        Parameters
        ----------
        playlist_id:
            Spotify playlist ID.
        uris:
            List of Spotify track or episode URIs to add (max 100 per request).
        position:
            Zero-based position in the playlist at which to insert the tracks.
            Appends to the end if omitted.

        Returns
        -------
        dict
            Object with a ``snapshot_id`` key representing the new playlist state.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        body: dict[str, Any] = {"uris": uris}
        if position is not None:
            body["position"] = position
        result = await self._post(f"/playlists/{playlist_id}/tracks", json=body)
        if result is None:
            return {"snapshot_id": ""}
        return result

    async def remove_tracks_from_playlist(
        self,
        playlist_id: str,
        uris: list[str],
        *,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove one or more tracks from a playlist.

        Parameters
        ----------
        playlist_id:
            Spotify playlist ID.
        uris:
            List of Spotify track URIs to remove.
        snapshot_id:
            Optional playlist snapshot ID to guard against concurrent modifications.

        Returns
        -------
        dict
            Object with a ``snapshot_id`` key.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        body: dict[str, Any] = {"tracks": [{"uri": uri} for uri in uris]}
        if snapshot_id is not None:
            body["snapshot_id"] = snapshot_id
        result = await self._delete(f"/playlists/{playlist_id}/tracks", json=body)
        if result is None:
            return {"snapshot_id": ""}
        return result

    # ------------------------------------------------------------------
    # Saved tracks (library)
    # ------------------------------------------------------------------

    async def get_saved_tracks(
        self, *, limit: int = 50, offset: int = 0, market: str | None = None
    ) -> dict[str, Any]:
        """Get a list of the tracks saved in the user's 'Liked Songs'.

        Parameters
        ----------
        limit:
            Maximum number of items to return (1–50, default 50).
        offset:
            Offset for pagination (default 0).
        market:
            Optional ISO 3166-1 alpha-2 country code.

        Returns
        -------
        dict
            Spotify paging object with saved track objects in ``items``.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if market is not None:
            params["market"] = market
        result = await self._get("/me/tracks", params=params)
        if result is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        return result

    async def save_tracks(self, ids: list[str]) -> None:
        """Save one or more tracks to the current user's library.

        Parameters
        ----------
        ids:
            List of Spotify track IDs to save (max 50 per request).

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        await self._put("/me/tracks", json={"ids": ids})

    async def remove_saved_tracks(self, ids: list[str]) -> None:
        """Remove one or more tracks from the current user's library.

        Parameters
        ----------
        ids:
            List of Spotify track IDs to remove (max 50 per request).

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        await self._delete("/me/tracks", json={"ids": ids})

    async def check_saved_tracks(self, ids: list[str]) -> list[bool]:
        """Check if one or more tracks are saved in the user's library.

        Parameters
        ----------
        ids:
            List of Spotify track IDs to check (max 50 per request).

        Returns
        -------
        list of bool
            Ordered list of booleans corresponding to each ID; ``True`` if the
            track is saved.

        Raises
        ------
        SpotifyAuthError
            If authentication fails and cannot be refreshed.
        SpotifyRateLimitError
            If the API returns HTTP 429.
        SpotifyAPIError
            For other unexpected HTTP error status codes.
        """
        params: dict[str, Any] = {"ids": ",".join(ids)}
        result = await self._get("/me/tracks/contains", params=params)
        if result is None:
            return [False] * len(ids)
        # Spotify returns a JSON array (list[bool]), not a dict.
        return result
