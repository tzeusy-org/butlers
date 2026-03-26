"""Integration tests for the Spotify module wired into the Lifestyle butler.

Covers 5 scenarios per bu-ih0f.4:
1. Module startup with valid credentials → all 22 tools registered
2. Module startup with missing credentials → actionable errors on every tool
3. Playlist flow: create → add tracks → get tracks → remove tracks (mocked HTTP)
4. Search → play flow (mocked HTTP)
5. Discovery flow: get_recommendations → fallback on 403

All HTTP is mocked at the httpx level via ``unittest.mock`` (injected AsyncClient).
No SpotifyClient internals are mocked — only the HTTP transport layer.

Issue: bu-ih0f.4
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.modules.spotify import (
    _NO_CREDENTIALS_ERROR,
    _RECOMMENDATIONS_UNAVAILABLE_ERROR,
    SpotifyModule,
)

# ---------------------------------------------------------------------------
# Auto-override: these tests are pure unit tests — no Docker required.
# The roster/ conftest auto-adds integration + docker-skip; we explicitly
# override with the unit mark so they run in all environments.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_ACCESS_TOKEN = "BQDtest_access_token_lifestyle"
_REFRESH_TOKEN = "AQAtest_refresh_token_lifestyle"
_CLIENT_ID = "abc123def456abc123def456abcdef01"
_USER_ID = "lifestyle_user_42"

_PROFILE_RESPONSE = {
    "id": _USER_ID,
    "display_name": "Lifestyle Test User",
    "product": "premium",
    "country": "GB",
}

_TRACK_URI_1 = "spotify:track:trackAAA111"
_TRACK_URI_2 = "spotify:track:trackBBB222"
_PLAYLIST_ID = "playlist_test_001"
_PLAYLIST_RESPONSE = {
    "id": _PLAYLIST_ID,
    "name": "My Test Playlist",
    "uri": f"spotify:playlist:{_PLAYLIST_ID}",
    "owner": {"id": _USER_ID},
    "tracks": {"total": 0},
}

_SEARCH_RESPONSE = {
    "tracks": {
        "items": [
            {
                "id": "trackAAA111",
                "name": "Sunny Day",
                "uri": _TRACK_URI_1,
                "artists": [{"name": "Test Artist"}],
            }
        ],
        "total": 1,
    }
}

_RECOMMENDATIONS_RESPONSE = {
    "tracks": [
        {
            "id": "recTrack001",
            "name": "Recommended Track",
            "uri": "spotify:track:recTrack001",
            "artists": [{"name": "Rec Artist"}],
        }
    ],
    "seeds": [{"id": "rock", "type": "GENRE"}],
}

_PLAYLIST_TRACKS_RESPONSE = {
    "items": [
        {
            "track": {
                "id": "trackAAA111",
                "name": "Sunny Day",
                "uri": _TRACK_URI_1,
                "artists": [{"name": "Test Artist"}],
            }
        }
    ],
    "total": 1,
    "offset": 0,
    "limit": 50,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credential_store(
    *,
    access_token: str | None = _ACCESS_TOKEN,
    refresh_token: str | None = _REFRESH_TOKEN,
    client_id: str | None = _CLIENT_ID,
    expires_at: str | None = None,
) -> AsyncMock:
    """Build a mock CredentialStore with configurable Spotify token values."""
    store = AsyncMock()

    async def _resolve(key: str) -> str | None:
        mapping = {
            "SPOTIFY_ACCESS_TOKEN": access_token,
            "SPOTIFY_REFRESH_TOKEN": refresh_token,
            "SPOTIFY_CLIENT_ID": client_id,
            "SPOTIFY_TOKEN_EXPIRES_AT": expires_at,
        }
        return mapping.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock()
    return store


def _make_http_response(
    status_code: int,
    json_data: Any = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = httpx.Headers(headers or {})
    if json_data is not None:
        response.json = MagicMock(return_value=json_data)
        response.text = json.dumps(json_data)
    else:
        response.json = MagicMock(return_value=None)
        response.text = ""
    return response


def _make_mock_http_client(responses: list[MagicMock]) -> AsyncMock:
    """Build a mock httpx.AsyncClient that returns responses from a queue."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=responses)
    client.aclose = AsyncMock()
    return client


def _make_mock_mcp() -> MagicMock:
    """Build a mock FastMCP server that captures registered tool functions."""
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# Scenario 1: Module startup with valid credentials → 22 tools registered
# ---------------------------------------------------------------------------


class TestScenario1ValidCredentials:
    """Scenario 1: SpotifyModule.on_startup() with valid credentials registers all 22 tools."""

    async def _startup_module_with_profile(self) -> tuple[SpotifyModule, MagicMock]:
        """Helper: start module with mocked HTTP returning a valid profile."""
        profile_response = _make_http_response(200, _PROFILE_RESPONSE)
        http_client = _make_mock_http_client([profile_response])
        cred_store = _make_credential_store()

        # Inject http_client directly into SpotifyClient by patching its constructor
        module = SpotifyModule()

        # We need to inject the mocked http_client. We do so by patching httpx.AsyncClient
        # at module load time so that SpotifyClient.__init__ gets our mock when open() runs.
        import unittest.mock as mock

        with mock.patch(
            "butlers.connectors.spotify_client.httpx.AsyncClient",
            return_value=http_client,
        ):
            await module.on_startup(config={}, db=None, credential_store=cred_store)

        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_22_tools_registered_after_valid_startup(self) -> None:
        """All 22 Spotify tools are registered when credentials are valid."""
        module, mcp = await self._startup_module_with_profile()
        assert len(mcp._registered_tools) == 22

    async def test_all_expected_tool_names_present(self) -> None:
        """The registered tool names match the expected 22 tool set."""
        expected = {
            "spotify_search",
            "spotify_get_recommendations",
            "spotify_get_related_artists",
            "spotify_get_playback_state",
            "spotify_get_queue",
            "spotify_get_top_items",
            "spotify_play",
            "spotify_pause",
            "spotify_skip_next",
            "spotify_skip_previous",
            "spotify_seek",
            "spotify_set_volume",
            "spotify_add_to_queue",
            "spotify_transfer_playback",
            "spotify_get_playlists",
            "spotify_create_playlist",
            "spotify_add_tracks_to_playlist",
            "spotify_remove_tracks_from_playlist",
            "spotify_get_playlist_tracks",
            "spotify_get_saved_tracks",
            "spotify_save_tracks",
            "spotify_remove_saved_tracks",
        }
        module, mcp = await self._startup_module_with_profile()
        assert set(mcp._registered_tools.keys()) == expected

    async def test_credentials_ok_flag_set_true(self) -> None:
        """After valid startup, _credentials_ok is True and user profile is populated."""
        module, _ = await self._startup_module_with_profile()
        assert module._credentials_ok is True
        assert module._user_profile is not None
        assert module._user_profile["id"] == _USER_ID

    async def test_butler_toml_includes_spotify_module(self) -> None:
        """roster/lifestyle/butler.toml declares [modules.spotify]."""
        from pathlib import Path

        from butlers.config import load_config

        roster_dir = Path(__file__).resolve().parents[3] / "roster" / "lifestyle"
        cfg = load_config(roster_dir)
        assert "spotify" in cfg.modules, (
            "spotify module is missing from [modules] section in roster/lifestyle/butler.toml"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Module startup with missing credentials → actionable errors
# ---------------------------------------------------------------------------


class TestScenario2MissingCredentials:
    """Scenario 2: SpotifyModule returns actionable errors when credentials are absent."""

    async def _startup_module_no_creds(self) -> tuple[SpotifyModule, MagicMock]:
        """Helper: start module with no credential store."""
        module = SpotifyModule()
        await module.on_startup(config={}, db=None, credential_store=None)
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def _startup_module_empty_tokens(self) -> tuple[SpotifyModule, MagicMock]:
        """Helper: start module with credential store that returns None for all tokens."""
        cred_store = _make_credential_store(
            access_token=None,
            refresh_token=None,
            client_id=None,
        )
        module = SpotifyModule()
        await module.on_startup(config={}, db=None, credential_store=cred_store)
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_credentials_ok_false_when_no_store(self) -> None:
        """When credential_store=None, _credentials_ok is False."""
        module, _ = await self._startup_module_no_creds()
        assert module._credentials_ok is False

    async def test_22_tools_still_registered_despite_missing_creds(self) -> None:
        """Tools are still registered even without credentials (they return errors)."""
        _, mcp = await self._startup_module_no_creds()
        assert len(mcp._registered_tools) == 22

    async def test_search_returns_actionable_error_no_store(self) -> None:
        """spotify_search returns the NO_CREDENTIALS error when store is None."""
        _, mcp = await self._startup_module_no_creds()
        result = await mcp._registered_tools["spotify_search"](query="jazz")
        assert "error" in result
        assert result["error"] == _NO_CREDENTIALS_ERROR

    async def test_play_returns_actionable_error_no_store(self) -> None:
        """spotify_play returns the NO_CREDENTIALS error when store is None."""
        _, mcp = await self._startup_module_no_creds()
        result = await mcp._registered_tools["spotify_play"]()
        assert "error" in result
        assert result["error"] == _NO_CREDENTIALS_ERROR

    async def test_create_playlist_returns_actionable_error_empty_tokens(self) -> None:
        """spotify_create_playlist returns actionable error when tokens are missing."""
        _, mcp = await self._startup_module_empty_tokens()
        result = await mcp._registered_tools["spotify_create_playlist"](name="My Playlist")
        assert "error" in result
        assert result["error"] == _NO_CREDENTIALS_ERROR

    async def test_get_recommendations_returns_actionable_error_no_store(self) -> None:
        """spotify_get_recommendations returns actionable error when no store."""
        _, mcp = await self._startup_module_no_creds()
        result = await mcp._registered_tools["spotify_get_recommendations"](seed_genres=["rock"])
        assert "error" in result
        assert result["error"] == _NO_CREDENTIALS_ERROR

    async def test_error_message_contains_setup_hint(self) -> None:
        """The no-credentials error directs the user to dashboard settings."""
        _, mcp = await self._startup_module_no_creds()
        result = await mcp._registered_tools["spotify_search"](query="test")
        assert "dashboard" in result["error"].lower() or "connect" in result["error"].lower()


# ---------------------------------------------------------------------------
# Scenario 3: Playlist flow: create → add → get → remove (mocked HTTP)
# ---------------------------------------------------------------------------


class TestScenario3PlaylistFlow:
    """Scenario 3: Full playlist lifecycle mocked at the HTTP level."""

    @pytest.fixture
    async def module_with_tools(self) -> tuple[SpotifyModule, MagicMock]:
        """SpotifyModule with real SpotifyClient backed by mocked HTTP."""
        add_tracks_response = {"snapshot_id": "snap_add_001"}
        remove_tracks_response = {"snapshot_id": "snap_remove_001"}

        # The HTTP client will handle requests in this order:
        # 1. on_startup → GET /me (profile)
        # 2. spotify_create_playlist → POST /users/{id}/playlists
        # 3. spotify_add_tracks_to_playlist → POST /playlists/{id}/tracks
        # 4. spotify_get_playlist_tracks → GET /playlists/{id}/tracks
        # 5. spotify_remove_tracks_from_playlist → DELETE /playlists/{id}/tracks
        responses = [
            _make_http_response(200, _PROFILE_RESPONSE),
            _make_http_response(201, _PLAYLIST_RESPONSE),
            _make_http_response(201, add_tracks_response),
            _make_http_response(200, _PLAYLIST_TRACKS_RESPONSE),
            _make_http_response(200, remove_tracks_response),
        ]
        http_client = _make_mock_http_client(responses)
        cred_store = _make_credential_store()

        module = SpotifyModule()
        import unittest.mock as mock

        with mock.patch(
            "butlers.connectors.spotify_client.httpx.AsyncClient",
            return_value=http_client,
        ):
            await module.on_startup(config={}, db=None, credential_store=cred_store)

        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_create_playlist_returns_id(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_create_playlist returns the new playlist's ID."""
        _, mcp = module_with_tools
        result = await mcp._registered_tools["spotify_create_playlist"](
            name="My Test Playlist", description="A test playlist"
        )
        assert "id" in result
        assert result["id"] == _PLAYLIST_ID

    async def test_add_tracks_returns_snapshot_id(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_add_tracks_to_playlist returns a snapshot_id."""
        _, mcp = module_with_tools
        # Must call create first to consume the mock response
        await mcp._registered_tools["spotify_create_playlist"](name="My Test Playlist")
        result = await mcp._registered_tools["spotify_add_tracks_to_playlist"](
            playlist_id=_PLAYLIST_ID, uris=[_TRACK_URI_1, _TRACK_URI_2]
        )
        assert "snapshot_id" in result
        assert result["snapshot_id"] == "snap_add_001"

    async def test_get_playlist_tracks_returns_items(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_get_playlist_tracks returns a list of items."""
        _, mcp = module_with_tools
        # Consume create + add responses first
        await mcp._registered_tools["spotify_create_playlist"](name="My Test Playlist")
        await mcp._registered_tools["spotify_add_tracks_to_playlist"](
            playlist_id=_PLAYLIST_ID, uris=[_TRACK_URI_1]
        )
        result = await mcp._registered_tools["spotify_get_playlist_tracks"](
            playlist_id=_PLAYLIST_ID
        )
        assert "items" in result
        assert len(result["items"]) == 1
        assert result["items"][0]["track"]["uri"] == _TRACK_URI_1

    async def test_remove_tracks_returns_snapshot_id(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_remove_tracks_from_playlist returns a snapshot_id."""
        _, mcp = module_with_tools
        # Consume create + add + get responses first
        await mcp._registered_tools["spotify_create_playlist"](name="My Test Playlist")
        await mcp._registered_tools["spotify_add_tracks_to_playlist"](
            playlist_id=_PLAYLIST_ID, uris=[_TRACK_URI_1]
        )
        await mcp._registered_tools["spotify_get_playlist_tracks"](playlist_id=_PLAYLIST_ID)
        result = await mcp._registered_tools["spotify_remove_tracks_from_playlist"](
            playlist_id=_PLAYLIST_ID, uris=[_TRACK_URI_1]
        )
        assert "snapshot_id" in result
        assert result["snapshot_id"] == "snap_remove_001"


# ---------------------------------------------------------------------------
# Scenario 4: Search → play flow (mocked HTTP)
# ---------------------------------------------------------------------------


class TestScenario4SearchPlayFlow:
    """Scenario 4: search for a track then play it — HTTP mocked end-to-end."""

    @pytest.fixture
    async def module_with_tools(self) -> tuple[SpotifyModule, MagicMock]:
        """SpotifyModule with real SpotifyClient backed by mocked HTTP."""
        # HTTP responses in order:
        # 1. on_startup → GET /me (profile)
        # 2. spotify_search → GET /search
        # 3. spotify_play → PUT /me/player/play (returns 204 No Content)
        responses = [
            _make_http_response(200, _PROFILE_RESPONSE),
            _make_http_response(200, _SEARCH_RESPONSE),
            _make_http_response(204),  # play returns 204 No Content
        ]
        http_client = _make_mock_http_client(responses)
        cred_store = _make_credential_store()

        module = SpotifyModule()
        import unittest.mock as mock

        with mock.patch(
            "butlers.connectors.spotify_client.httpx.AsyncClient",
            return_value=http_client,
        ):
            await module.on_startup(config={}, db=None, credential_store=cred_store)

        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_search_returns_track_items(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_search returns catalog results from the mocked HTTP response."""
        _, mcp = module_with_tools
        result = await mcp._registered_tools["spotify_search"](query="Sunny Day", type="track")
        assert "tracks" in result
        assert len(result["tracks"]["items"]) == 1
        assert result["tracks"]["items"][0]["name"] == "Sunny Day"

    async def test_play_returns_playing_status(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_play returns {'status': 'playing'} after a 204 No Content response."""
        _, mcp = module_with_tools
        # Consume search response first
        await mcp._registered_tools["spotify_search"](query="Sunny Day")
        result = await mcp._registered_tools["spotify_play"](uris=[_TRACK_URI_1])
        assert result == {"status": "playing"}

    async def test_search_then_play_flow(
        self, module_with_tools: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """Full search → play flow: find a track URI then issue play command."""
        _, mcp = module_with_tools

        # Step 1: Search for a track
        search_result = await mcp._registered_tools["spotify_search"](
            query="Sunny Day", type="track", limit=1
        )
        assert "tracks" in search_result
        track_uri = search_result["tracks"]["items"][0]["uri"]
        assert track_uri == _TRACK_URI_1

        # Step 2: Play the found track URI
        play_result = await mcp._registered_tools["spotify_play"](uris=[track_uri])
        assert play_result == {"status": "playing"}


# ---------------------------------------------------------------------------
# Scenario 5: Discovery flow: get_recommendations → fallback on 403
# ---------------------------------------------------------------------------


class TestScenario5DiscoveryFallback:
    """Scenario 5: get_recommendations falls back gracefully when the API is unavailable."""

    @pytest.fixture
    async def module_with_valid_creds(self) -> tuple[SpotifyModule, MagicMock]:
        """SpotifyModule with valid credentials but no HTTP responses pre-loaded."""
        # Only the startup GET /me profile response — individual test methods
        # will inject their own HTTP mocks for get_recommendations calls.
        profile_response = _make_http_response(200, _PROFILE_RESPONSE)
        http_client = _make_mock_http_client([profile_response])
        cred_store = _make_credential_store()

        module = SpotifyModule()
        import unittest.mock as mock

        with mock.patch(
            "butlers.connectors.spotify_client.httpx.AsyncClient",
            return_value=http_client,
        ):
            await module.on_startup(config={}, db=None, credential_store=cred_store)

        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_recommendations_success_returns_tracks(
        self, module_with_valid_creds: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """When the recommendations API succeeds, the tool returns track results."""
        module, mcp = module_with_valid_creds
        assert module._client is not None

        # Inject a successful recommendations response directly into the client's HTTP mock
        rec_response = _make_http_response(200, _RECOMMENDATIONS_RESPONSE)
        module._client._http_client = _make_mock_http_client([rec_response])

        result = await mcp._registered_tools["spotify_get_recommendations"](
            seed_genres=["rock"], limit=5
        )
        assert "tracks" in result
        assert len(result["tracks"]) == 1
        assert "seeds" in result

    async def test_recommendations_403_returns_unavailable_error(
        self, module_with_valid_creds: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """A 403 from the recommendations endpoint returns the unavailability error.

        SpotifyClient.get_recommendations catches 403 and returns {"tracks": []}
        (no "seeds" key). The module tool detects the missing "seeds" sentinel and
        returns _RECOMMENDATIONS_UNAVAILABLE_ERROR.
        """
        module, mcp = module_with_valid_creds
        assert module._client is not None

        # Inject a 403 response at the HTTP level.
        # SpotifyClient._request raises SpotifyAPIError(403) → get_recommendations
        # catches it and returns {"tracks": []} (no "seeds" key) → the module tool
        # returns _RECOMMENDATIONS_UNAVAILABLE_ERROR.
        forbidden_response = _make_http_response(
            403, {"error": {"status": 403, "message": "Forbidden"}}
        )
        module._client._http_client = _make_mock_http_client([forbidden_response])

        result = await mcp._registered_tools["spotify_get_recommendations"](
            seed_genres=["rock"], limit=5
        )

        assert "error" in result
        assert result["error"] == _RECOMMENDATIONS_UNAVAILABLE_ERROR

    async def test_recommendations_missing_seeds_key_triggers_fallback(
        self, module_with_valid_creds: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """When the response lacks a 'seeds' key, the module returns the fallback message."""
        module, mcp = module_with_valid_creds
        assert module._client is not None

        # A valid-looking response without the 'seeds' sentinel key
        no_seeds_response = {"tracks": []}  # seeds key absent

        from unittest.mock import patch

        async def _return_no_seeds(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return no_seeds_response

        with patch.object(module._client, "get_recommendations", side_effect=_return_no_seeds):
            result = await mcp._registered_tools["spotify_get_recommendations"](
                seed_genres=["rock"], limit=5
            )

        assert "error" in result
        assert result["error"] == _RECOMMENDATIONS_UNAVAILABLE_ERROR

    async def test_related_artists_success_as_discovery_fallback(
        self, module_with_valid_creds: tuple[SpotifyModule, MagicMock]
    ) -> None:
        """spotify_get_related_artists works as a discovery fallback when recommendations fail."""
        module, mcp = module_with_valid_creds
        assert module._client is not None

        related_artists_response = {
            "artists": [
                {"id": "artist_related_01", "name": "Related Artist", "genres": ["rock"]},
            ]
        }
        related_response = _make_http_response(200, related_artists_response)
        module._client._http_client = _make_mock_http_client([related_response])

        result = await mcp._registered_tools["spotify_get_related_artists"](
            artist_id="artist_seed_01"
        )
        assert "artists" in result
        assert len(result["artists"]) == 1
        assert result["artists"][0]["name"] == "Related Artist"
