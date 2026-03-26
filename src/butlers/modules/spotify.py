"""Spotify module — MCP tools for Spotify playback, playlists, library, and discovery.

Wraps the ``SpotifyClient`` as a butler module with 22 MCP tools across 6 groups:
- Search
- Discovery (recommendations, related artists)
- Playback state (get state, queue, top items)
- Playback control (play, pause, skip, seek, volume, queue, transfer)
- Playlist management (get, create, add/remove tracks, list tracks)
- Library management (get/save/remove saved tracks)

Credentials are resolved via ``CredentialStore`` at startup. Missing credentials
produce actionable error messages rather than exceptions. Premium-required errors
are caught and returned with account tier information.

Configured via ``[modules.spotify]`` in ``butler.toml``.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from butlers.connectors.spotify_client import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyClient,
    SpotifyRateLimitError,
)
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel: all tools use this when the module has no credentials
# ---------------------------------------------------------------------------

_NO_CREDENTIALS_ERROR = (
    "Spotify not connected. Visit dashboard settings to link your Spotify account."
)

_AUTH_EXPIRED_ERROR = "Spotify authorization expired. Re-connect via dashboard settings."

_RECOMMENDATIONS_UNAVAILABLE_ERROR = (
    "Spotify Recommendations API is not available for this app. "
    "Use spotify_search and spotify_get_related_artists for discovery instead."
)


def _premium_required_error(product: str | None) -> str:
    tier = product or "unknown"
    return (
        f"This action requires Spotify Premium. Your account ({tier}) does not support "
        "playback control. You can still use playlist, library, and search tools."
    )


def _rate_limited_error(retry_after_s: float) -> str:
    return f"Spotify rate limited. Try again in {retry_after_s:.0f} seconds."


def _api_error_message(status_code: int, body: str) -> str:
    truncated = body[:200]
    return f"Spotify API error {status_code}: {truncated}"


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class SpotifyModuleConfig(BaseModel):
    """Configuration for the Spotify module."""

    playback_tools: bool = True
    """Whether to register playback control tools (requires Spotify Premium)."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


class SpotifyModule(Module):
    """Spotify module providing MCP tools for music control and discovery.

    All 22 tools delegate to ``SpotifyClient``.  When credentials are absent,
    every tool returns an actionable error string rather than raising.
    """

    def __init__(self) -> None:
        self._config: SpotifyModuleConfig = SpotifyModuleConfig()
        self._client: SpotifyClient | None = None
        self._user_profile: dict[str, Any] | None = None
        self._credentials_ok: bool = False

    @property
    def name(self) -> str:
        return "spotify"

    @property
    def config_schema(self) -> type[BaseModel]:
        return SpotifyModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Resolve Spotify credentials and verify connectivity via ``get_me()``.

        Parameters
        ----------
        config:
            Module configuration (``SpotifyModuleConfig`` or raw dict).
        db:
            Butler database instance (unused by this module).
        credential_store:
            ``CredentialStore`` for OAuth token resolution.  When ``None``,
            the module marks itself as unconfigured and tools return actionable
            errors.
        blob_store:
            Unused by this module.
        """
        self._config = (
            config
            if isinstance(config, SpotifyModuleConfig)
            else SpotifyModuleConfig(**(config or {}))
        )
        self._credentials_ok = False
        self._user_profile = None
        self._client = None

        if credential_store is None:
            logger.warning(
                "Spotify module: no credentials found. Connect Spotify via dashboard settings."
            )
            return

        # Verify that the essential tokens are present before constructing the client.
        access_token = await credential_store.resolve("SPOTIFY_ACCESS_TOKEN")
        refresh_token = await credential_store.resolve("SPOTIFY_REFRESH_TOKEN")

        if not access_token or not refresh_token:
            logger.warning(
                "Spotify module: no credentials found. Connect Spotify via dashboard settings."
            )
            return

        client = SpotifyClient(credential_store=credential_store)
        await client.open()
        self._client = client

        try:
            profile = await client.get_me()
            self._user_profile = profile
            self._credentials_ok = True
            logger.info(
                "Spotify module: connected as %r (product=%r)",
                profile.get("display_name") or profile.get("id"),
                profile.get("product"),
            )
        except SpotifyAuthError as exc:
            logger.warning("Spotify module: auth failed at startup — %s", exc)
            self._credentials_ok = False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Spotify module: get_me() failed at startup — %s", exc)
            self._credentials_ok = False

    async def on_shutdown(self) -> None:
        """Close the ``SpotifyClient`` HTTP client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Tool metadata (sensitivity)
    # ------------------------------------------------------------------

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Declare sensitivity metadata for Spotify tools.

        Read-only tools get an empty ToolMeta (default sensitivity heuristic).
        Write tools get explicit ``arg_sensitivities`` marking the tool as write.
        """
        write_tools = {
            "spotify_play",
            "spotify_pause",
            "spotify_skip_next",
            "spotify_skip_previous",
            "spotify_seek",
            "spotify_set_volume",
            "spotify_add_to_queue",
            "spotify_transfer_playback",
            "spotify_create_playlist",
            "spotify_add_tracks_to_playlist",
            "spotify_remove_tracks_from_playlist",
            "spotify_save_tracks",
            "spotify_remove_saved_tracks",
        }
        read_tools = {
            "spotify_search",
            "spotify_get_recommendations",
            "spotify_get_related_artists",
            "spotify_get_playback_state",
            "spotify_get_queue",
            "spotify_get_top_items",
            "spotify_get_playlists",
            "spotify_get_playlist_tracks",
            "spotify_get_saved_tracks",
        }
        meta: dict[str, ToolMeta] = {}
        for name in write_tools:
            meta[name] = ToolMeta(arg_sensitivities={"_write": True})
        for name in read_tools:
            meta[name] = ToolMeta(arg_sensitivities={"_write": False})
        return meta

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _no_credentials(self) -> dict[str, Any]:
        return {"error": _NO_CREDENTIALS_ERROR}

    def _handle_spotify_error(self, exc: Exception) -> dict[str, Any]:
        """Convert a SpotifyClient exception to an actionable error dict."""
        if isinstance(exc, SpotifyAuthError):
            return {"error": _AUTH_EXPIRED_ERROR}
        if isinstance(exc, SpotifyRateLimitError):
            return {"error": _rate_limited_error(exc.retry_after_s)}
        if isinstance(exc, SpotifyAPIError):
            # 403 Premium-required
            if exc.status_code == 403 and "premium" in exc.body.lower():
                product = self._user_profile.get("product") if self._user_profile else None
                return {"error": _premium_required_error(product)}
            return {"error": _api_error_message(exc.status_code, exc.body)}
        return {"error": f"Unexpected error: {exc}"}

    # ------------------------------------------------------------------
    # register_tools
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all 22 Spotify MCP tools on the FastMCP server."""
        self._config = (
            config
            if isinstance(config, SpotifyModuleConfig)
            else SpotifyModuleConfig(**(config or {}))
        )
        module = self  # capture for closures

        # ----------------------------------------------------------------
        # Group 1: Search
        # ----------------------------------------------------------------

        async def spotify_search(
            query: str,
            type: str = "track",  # noqa: A002
            limit: int = 10,
        ) -> dict[str, Any]:
            """Search the Spotify catalog.

            Args:
                query: Search keywords (e.g., artist name, track title).
                type: Item type to search. One of: track, artist, album, playlist.
                      Defaults to 'track'.
                limit: Maximum number of results (1-50, default 10).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.search(query, types=[type], limit=min(limit, 50))
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        mcp.tool()(spotify_search)

        # ----------------------------------------------------------------
        # Group 2: Discovery
        # ----------------------------------------------------------------

        async def spotify_get_recommendations(
            seed_artists: list[str] | None = None,
            seed_tracks: list[str] | None = None,
            seed_genres: list[str] | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            """Get track recommendations based on seed artists, tracks, or genres.

            Args:
                seed_artists: List of Spotify artist IDs (up to 5 total seeds).
                seed_tracks: List of Spotify track IDs.
                seed_genres: List of genre name strings.
                limit: Number of tracks to return (1-100, default 20).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                result = await module._client.get_recommendations(
                    seed_artists=seed_artists,
                    seed_tracks=seed_tracks,
                    seed_genres=seed_genres,
                    limit=min(limit, 100),
                )
                # A valid recommendation response always includes a "seeds" key.
                # If absent, the API is unavailable for this app (e.g., 403/404 sentinel).
                if "seeds" not in result:
                    return {"error": _RECOMMENDATIONS_UNAVAILABLE_ERROR}
                return result
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_get_related_artists(artist_id: str) -> dict[str, Any]:
            """Get artists related to a given Spotify artist.

            Args:
                artist_id: Spotify artist ID.
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                result = await module._client._get(f"/artists/{artist_id}/related-artists")
                if result is None:
                    return {"artists": []}
                return result
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        mcp.tool()(spotify_get_recommendations)
        mcp.tool()(spotify_get_related_artists)

        # ----------------------------------------------------------------
        # Group 3: Playback state
        # ----------------------------------------------------------------

        async def spotify_get_playback_state() -> dict[str, Any]:
            """Get the current Spotify playback state (device, track, shuffle, repeat).

            Returns null if no active playback device.
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                result = await module._client.get_playback_state()
                return result if result is not None else {"playback": None}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_get_queue() -> dict[str, Any]:
            """Get the user's Spotify playback queue (current track + upcoming)."""
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.get_queue()
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_get_top_items(
            type: str = "tracks",  # noqa: A002
            time_range: str = "medium_term",
            limit: int = 10,
        ) -> dict[str, Any]:
            """Get the user's top Spotify artists or tracks.

            Args:
                type: Item type: 'artists' or 'tracks'. Defaults to 'tracks'.
                time_range: One of 'short_term' (4 weeks), 'medium_term' (6 months),
                            or 'long_term' (all time). Defaults to 'medium_term'.
                limit: Maximum number of items (1-50, default 10).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.get_top_items(
                    type, time_range=time_range, limit=min(limit, 50)
                )
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        mcp.tool()(spotify_get_playback_state)
        mcp.tool()(spotify_get_queue)
        mcp.tool()(spotify_get_top_items)

        # ----------------------------------------------------------------
        # Group 4: Playback control (Premium required)
        # ----------------------------------------------------------------

        async def spotify_play(
            context_uri: str | None = None,
            uris: list[str] | None = None,
            device_id: str | None = None,
        ) -> dict[str, Any]:
            """Start or resume Spotify playback. Requires Spotify Premium.

            Args:
                context_uri: Spotify album/playlist URI to play (optional).
                uris: List of track URIs to play (optional).
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.play(context_uri=context_uri, uris=uris, device_id=device_id)
                return {"status": "playing"}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_pause(device_id: str | None = None) -> dict[str, Any]:
            """Pause Spotify playback. Requires Spotify Premium.

            Args:
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.pause(device_id=device_id)
                return {"status": "paused"}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_skip_next(device_id: str | None = None) -> dict[str, Any]:
            """Skip to the next track. Requires Spotify Premium.

            Args:
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.skip_to_next(device_id=device_id)
                return {"status": "skipped_next"}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_skip_previous(device_id: str | None = None) -> dict[str, Any]:
            """Skip to the previous track. Requires Spotify Premium.

            Args:
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.skip_to_previous(device_id=device_id)
                return {"status": "skipped_previous"}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_seek(position_ms: int, device_id: str | None = None) -> dict[str, Any]:
            """Seek to a position in the current track. Requires Spotify Premium.

            Args:
                position_ms: Position in milliseconds to seek to.
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.seek_to_position(position_ms, device_id=device_id)
                return {"status": "seeked", "position_ms": position_ms}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_set_volume(
            volume_percent: int, device_id: str | None = None
        ) -> dict[str, Any]:
            """Set the playback volume. Requires Spotify Premium.

            Args:
                volume_percent: Volume level (0-100).
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.set_volume(volume_percent, device_id=device_id)
                return {"status": "volume_set", "volume_percent": volume_percent}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_add_to_queue(uri: str, device_id: str | None = None) -> dict[str, Any]:
            """Add a track or episode to the playback queue. Requires Spotify Premium.

            Args:
                uri: Spotify URI of the track or episode to add.
                device_id: Target device ID (optional, uses active device).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.add_to_queue(uri, device_id=device_id)
                return {"status": "added_to_queue", "uri": uri}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_transfer_playback(device_id: str, play: bool = True) -> dict[str, Any]:
            """Transfer Spotify playback to a different device. Requires Spotify Premium.

            Args:
                device_id: Target device ID to transfer playback to.
                play: Whether to start playing on the new device (default true).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client._put(
                    "/me/player",
                    json={"device_ids": [device_id], "play": play},
                )
                return {"status": "transferred", "device_id": device_id}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        mcp.tool()(spotify_play)
        mcp.tool()(spotify_pause)
        mcp.tool()(spotify_skip_next)
        mcp.tool()(spotify_skip_previous)
        mcp.tool()(spotify_seek)
        mcp.tool()(spotify_set_volume)
        mcp.tool()(spotify_add_to_queue)
        mcp.tool()(spotify_transfer_playback)

        # ----------------------------------------------------------------
        # Group 5: Playlist management
        # ----------------------------------------------------------------

        async def spotify_get_playlists(limit: int = 20, offset: int = 0) -> dict[str, Any]:
            """Get the current user's Spotify playlists.

            Args:
                limit: Maximum number of playlists (1-50, default 20).
                offset: Pagination offset (default 0).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.get_user_playlists(limit=min(limit, 50), offset=offset)
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_create_playlist(
            name: str,
            description: str = "",
            public: bool = False,
        ) -> dict[str, Any]:
            """Create a new Spotify playlist.

            Args:
                name: Playlist name.
                description: Playlist description (optional).
                public: Whether the playlist is public (default false).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            if module._user_profile is None:
                return {"error": "User profile not available. Re-connect Spotify."}
            user_id = module._user_profile.get("id")
            if not user_id:
                return {"error": "Could not determine Spotify user ID."}
            try:
                return await module._client.create_playlist(
                    user_id, name, public=public, description=description
                )
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_add_tracks_to_playlist(
            playlist_id: str, uris: list[str]
        ) -> dict[str, Any]:
            """Add tracks to a Spotify playlist.

            Args:
                playlist_id: Spotify playlist ID.
                uris: List of Spotify track URIs to add.
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.add_tracks_to_playlist(playlist_id, uris)
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_remove_tracks_from_playlist(
            playlist_id: str, uris: list[str]
        ) -> dict[str, Any]:
            """Remove tracks from a Spotify playlist.

            Args:
                playlist_id: Spotify playlist ID.
                uris: List of Spotify track URIs to remove.
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.remove_tracks_from_playlist(playlist_id, uris)
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_get_playlist_tracks(
            playlist_id: str, limit: int = 50, offset: int = 0
        ) -> dict[str, Any]:
            """Get tracks in a Spotify playlist.

            Args:
                playlist_id: Spotify playlist ID.
                limit: Maximum number of tracks (1-100, default 50).
                offset: Pagination offset (default 0).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                result = await module._client._get(
                    f"/playlists/{playlist_id}/tracks",
                    params={"limit": min(limit, 100), "offset": offset},
                )
                if result is None:
                    return {"items": []}
                return result
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        mcp.tool()(spotify_get_playlists)
        mcp.tool()(spotify_create_playlist)
        mcp.tool()(spotify_add_tracks_to_playlist)
        mcp.tool()(spotify_remove_tracks_from_playlist)
        mcp.tool()(spotify_get_playlist_tracks)

        # ----------------------------------------------------------------
        # Group 6: Library management
        # ----------------------------------------------------------------

        async def spotify_get_saved_tracks(limit: int = 20, offset: int = 0) -> dict[str, Any]:
            """Get the user's saved (liked) Spotify tracks.

            Args:
                limit: Maximum number of tracks (1-50, default 20).
                offset: Pagination offset (default 0).
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                return await module._client.get_saved_tracks(limit=min(limit, 50), offset=offset)
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_save_tracks(ids: list[str]) -> dict[str, Any]:
            """Save tracks to the user's Spotify library.

            Args:
                ids: List of Spotify track IDs to save.
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.save_tracks(ids)
                return {"status": "saved", "count": len(ids)}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        async def spotify_remove_saved_tracks(ids: list[str]) -> dict[str, Any]:
            """Remove tracks from the user's Spotify library.

            Args:
                ids: List of Spotify track IDs to remove.
            """
            if not module._credentials_ok or module._client is None:
                return module._no_credentials()
            try:
                await module._client.remove_saved_tracks(ids)
                return {"status": "removed", "count": len(ids)}
            except Exception as exc:  # noqa: BLE001
                return module._handle_spotify_error(exc)

        mcp.tool()(spotify_get_saved_tracks)
        mcp.tool()(spotify_save_tracks)
        mcp.tool()(spotify_remove_saved_tracks)
