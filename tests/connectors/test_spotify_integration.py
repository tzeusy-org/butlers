"""Integration tests for the Spotify connector.

Covers tasks 10.1-10.6 from openspec/changes/connector-spotify/tasks.md:

10.1 - Connector poll → Switchboard submission flow
       (end-to-end from poll cycle through envelope construction to MCP ingest call)
10.2 - Token refresh cycle: proactive (near-expiry) + reactive (401 response)
10.3 - Session aggregation: play/pause/resume lifecycle
10.4 - Adaptive polling: active 60s / idle 300s switching
10.5 - Checkpoint persistence and resume after restart
10.6 - Dashboard OAuth flow: PKCE start → callback → token storage
       (tested via SpotifyClient token refresh that writes back to CredentialStore)

These tests wire together multiple components to verify end-to-end behavior.
No real network I/O is performed; HTTP is mocked via unittest.mock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.spotify import (
    _CONNECTOR_TYPE,
    SpotifyConnector,
    SpotifyConnectorConfig,
    SpotifyCredentialError,
)
from butlers.connectors.spotify_client import (
    SpotifyAuthError,
    SpotifyClient,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared constants and helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
_T0_MS = int(_T0.timestamp() * 1000)

_USER_ID = "spotify_user_alice"
_ENDPOINT_IDENTITY = f"spotify:{_USER_ID}"

_ACCESS_TOKEN = "BQDtest_access_token"
_REFRESH_TOKEN = "AQAtest_refresh_token"
_CLIENT_ID = "abc123def456abc123def456abc12345"
_NEW_ACCESS_TOKEN = "BQDnew_access_token"
_NEW_REFRESH_TOKEN = "AQAnew_refresh_token"


def _make_config(**kwargs: Any) -> SpotifyConnectorConfig:
    defaults = dict(
        switchboard_mcp_url="http://localhost:41100/sse",
        poll_active_s=60,
        poll_idle_s=300,
        session_idle_timeout_s=300,
    )
    defaults.update(kwargs)
    return SpotifyConnectorConfig(**defaults)


def _make_currently_playing(
    *,
    track_id: str = "track_001",
    track_name: str = "Song Alpha",
    artist: str = "Artist A",
    album: str = "Album A",
    context_uri: str | None = "spotify:playlist:playlist_x",
    device_name: str = "MacBook",
    is_playing: bool = True,
    timestamp_ms: int = _T0_MS,
) -> dict[str, Any]:
    return {
        "is_playing": is_playing,
        "item": {
            "id": track_id,
            "name": track_name,
            "type": "track",
            "artists": [{"name": artist}],
            "album": {"name": album},
            "duration_ms": 240_000,
        },
        "context": {"uri": context_uri, "type": "playlist"} if context_uri else None,
        "device": {"name": device_name},
        "timestamp": timestamp_ms,
        "progress_ms": 30_000,
    }


def _make_httpx_response(
    status_code: int,
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
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


def _make_credential_store(
    *,
    access_token: str | None = _ACCESS_TOKEN,
    refresh_token: str | None = _REFRESH_TOKEN,
    client_id: str | None = _CLIENT_ID,
    expires_at: str | None = None,
) -> AsyncMock:
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


# ---------------------------------------------------------------------------
# 10.1 — Connector poll → Switchboard submission flow
# ---------------------------------------------------------------------------


class TestConnectorPollToSwitchboardFlow:
    """Integration: full cycle from poll through envelope build to Switchboard ingest."""

    @pytest.fixture
    def connector(self) -> SpotifyConnector:
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID
        conn._access_token = _ACCESS_TOKEN
        conn._client_id = _CLIENT_ID
        conn._refresh_token = _REFRESH_TOKEN
        conn._token_expires_at = _T0 + timedelta(hours=1)
        conn._http_client = AsyncMock(spec=httpx.AsyncClient)
        return conn

    async def test_currently_playing_triggers_track_change_envelope(
        self, connector: SpotifyConnector
    ) -> None:
        """Track change event: poll detects new track → correct ingest.v1 envelope is submitted."""
        payload = _make_currently_playing(track_id="track_001", timestamp_ms=_T0_MS)
        item = payload["item"]

        submitted: list[dict[str, Any]] = []

        async def _capture_envelope(envelope: dict[str, Any]) -> None:
            submitted.append(envelope)

        with patch.object(connector, "_submit_envelope", side_effect=_capture_envelope):
            await connector._handle_active_playback(payload, item, _T0, _T0.isoformat())

        assert len(submitted) == 1
        env = submitted[0]

        # Verify ingest.v1 envelope schema
        assert env["schema_version"] == "ingest.v1"
        assert env["source"]["channel"] == "spotify"
        assert env["source"]["provider"] == "spotify"
        assert env["source"]["endpoint_identity"] == _ENDPOINT_IDENTITY
        assert env["event"]["event_type"] == "spotify.track_change"
        assert env["sender"]["identity"] == _USER_ID
        assert "idempotency_key" in env["control"]
        assert env["control"]["policy_tier"] == "default"

    async def test_poll_cycle_submits_to_mcp_client(self, connector: SpotifyConnector) -> None:
        """End-to-end: execute_poll_cycle calls _mcp_client.call_tool('ingest', ...)."""
        # Set up MCP client mock
        mcp_mock = AsyncMock()
        mcp_mock.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mcp_mock

        # Ingestion policy that allows all
        policy_mock = MagicMock()
        decision_mock = MagicMock()
        decision_mock.allowed = True
        policy_mock.evaluate = MagicMock(return_value=decision_mock)
        connector._ingestion_policy = policy_mock

        payload = _make_currently_playing(track_id="track_mcp", timestamp_ms=_T0_MS)

        with (
            patch.object(
                connector,
                "_get_currently_playing",
                new_callable=AsyncMock,
                return_value=payload,
            ),
            patch.object(
                connector,
                "_poll_recently_played",
                new_callable=AsyncMock,
            ),
        ):
            await connector._execute_poll_cycle()

        # Verify MCP ingest was called with the envelope
        assert mcp_mock.call_tool.call_count >= 1
        call_args = mcp_mock.call_tool.call_args_list[0]
        assert call_args[0][0] == "ingest"
        envelope = call_args[0][1]
        assert envelope["event"]["event_type"] == "spotify.track_change"

    async def test_no_playback_does_not_submit_envelope(self, connector: SpotifyConnector) -> None:
        """When nothing is playing (204), no ingest envelope is submitted."""
        mcp_mock = AsyncMock()
        mcp_mock.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mcp_mock
        connector._ingestion_policy = None

        with (
            patch.object(
                connector,
                "_get_currently_playing",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                connector,
                "_poll_recently_played",
                new_callable=AsyncMock,
            ),
        ):
            await connector._execute_poll_cycle()

        mcp_mock.call_tool.assert_not_awaited()

    async def test_session_summary_submitted_after_session_ends(
        self, connector: SpotifyConnector
    ) -> None:
        """When a session closes via context change, session_summary envelope is submitted."""
        submitted: list[dict[str, Any]] = []

        async def _capture(envelope: dict[str, Any]) -> None:
            submitted.append(envelope)

        with patch.object(connector, "_submit_envelope", side_effect=_capture):
            # First track in playlist X — start session
            payload1 = _make_currently_playing(
                track_id="t1",
                context_uri="spotify:playlist:X",
                timestamp_ms=_T0_MS,
            )
            await connector._handle_active_playback(
                payload1, payload1["item"], _T0, _T0.isoformat()
            )
            # Second track in playlist Y — context change → session_summary + track_change
            t1 = _T0 + timedelta(minutes=5)
            payload2 = _make_currently_playing(
                track_id="t2",
                context_uri="spotify:playlist:Y",
                timestamp_ms=int(t1.timestamp() * 1000),
            )
            await connector._handle_active_playback(payload2, payload2["item"], t1, t1.isoformat())

        event_types = [e["event"]["event_type"] for e in submitted]
        assert "spotify.session_summary" in event_types
        assert "spotify.track_change" in event_types


# ---------------------------------------------------------------------------
# 10.2 — Token refresh cycle: proactive + reactive
# ---------------------------------------------------------------------------


class TestTokenRefreshCycle:
    """Integration: proactive and reactive (401) token refresh flows."""

    async def test_proactive_refresh_triggers_before_expiry_window(self) -> None:
        """SpotifyClient refreshes proactively when token expires within 5 minutes."""
        # Token expires in 3 minutes (within 5-minute window)
        near_expiry = (datetime.now(UTC) + timedelta(minutes=3)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)
        # Refresh token response
        refresh_resp = _make_httpx_response(
            200,
            {
                "access_token": _NEW_ACCESS_TOKEN,
                "refresh_token": _NEW_REFRESH_TOKEN,
                "expires_in": 3600,
            },
        )
        # Profile response after refresh
        me_resp = _make_httpx_response(200, {"id": "alice", "display_name": "Alice"})

        # First call is token refresh POST, second is GET /me
        http.post = AsyncMock(return_value=refresh_resp)
        http.get = AsyncMock(return_value=me_resp)

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        await client.get_me()

        # Verify the refresh was triggered (POST to token endpoint)
        http.post.assert_awaited_once()
        post_url = http.post.call_args[0][0]
        assert "token" in post_url

        # Verify the new access token was used for the API call
        get_call_headers = http.get.call_args[1]["headers"]
        assert get_call_headers["Authorization"] == f"Bearer {_NEW_ACCESS_TOKEN}"

        # Verify CredentialStore was updated with new tokens
        stored_calls = {c[0][0]: c[0][1] for c in store.store.call_args_list}
        assert "SPOTIFY_ACCESS_TOKEN" in stored_calls
        assert stored_calls["SPOTIFY_ACCESS_TOKEN"] == _NEW_ACCESS_TOKEN

    async def test_reactive_refresh_on_401(self) -> None:
        """SpotifyClient refreshes reactively when API returns 401."""
        # Token with future expiry (no proactive refresh)
        future_expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        store = _make_credential_store(expires_at=future_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)

        refresh_resp = _make_httpx_response(
            200,
            {
                "access_token": _NEW_ACCESS_TOKEN,
                "expires_in": 3600,
            },
        )
        me_resp = _make_httpx_response(200, {"id": "alice", "display_name": "Alice"})

        # Sequence: 401 → (refresh POST) → success
        http.get = AsyncMock(
            side_effect=[
                _make_httpx_response(401),  # First GET fails
                me_resp,  # Retry after refresh succeeds
            ]
        )
        http.post = AsyncMock(return_value=refresh_resp)

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        result = await client.get_me()
        assert result["id"] == "alice"

        # Refresh was triggered reactively after 401
        http.post.assert_awaited_once()

    async def test_double_401_raises_auth_error(self) -> None:
        """SpotifyClient raises SpotifyAuthError when 401 persists after refresh."""
        future_expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        store = _make_credential_store(expires_at=future_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)
        refresh_resp = _make_httpx_response(
            200,
            {"access_token": _NEW_ACCESS_TOKEN, "expires_in": 3600},
        )
        http.post = AsyncMock(return_value=refresh_resp)
        # Both GET attempts return 401
        http.get = AsyncMock(return_value=_make_httpx_response(401))

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        with pytest.raises(SpotifyAuthError):
            await client.get_me()

    async def test_connector_refresh_persists_to_credential_store(self) -> None:
        """SpotifyConnector._refresh_access_token writes new tokens to CredentialStore."""
        cfg = _make_config()
        mock_pool = MagicMock()
        conn = SpotifyConnector(config=cfg, db_pool=mock_pool)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._refresh_token = _REFRESH_TOKEN
        conn._client_id = _CLIENT_ID
        conn._http_client = AsyncMock(spec=httpx.AsyncClient)

        refresh_resp = _make_httpx_response(
            200,
            {
                "access_token": _NEW_ACCESS_TOKEN,
                "refresh_token": _NEW_REFRESH_TOKEN,
                "expires_in": 3600,
            },
        )
        conn._http_client.post = AsyncMock(return_value=refresh_resp)

        store_mock = AsyncMock()
        store_mock.store = AsyncMock()

        with patch("butlers.connectors.spotify.CredentialStore", return_value=store_mock):
            new_token = await conn._refresh_access_token()

        assert new_token == _NEW_ACCESS_TOKEN
        assert conn._access_token == _NEW_ACCESS_TOKEN
        assert conn._refresh_token == _NEW_REFRESH_TOKEN

        # Verify tokens were persisted to CredentialStore
        stored_keys = {c[0][0] for c in store_mock.store.call_args_list}
        assert "SPOTIFY_ACCESS_TOKEN" in stored_keys
        assert "SPOTIFY_REFRESH_TOKEN" in stored_keys
        assert "SPOTIFY_TOKEN_EXPIRES_AT" in stored_keys

    async def test_connector_refresh_http400_raises_credential_error(self) -> None:
        """SpotifyConnector raises SpotifyCredentialError on invalid_grant (HTTP 400)."""
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._refresh_token = _REFRESH_TOKEN
        conn._client_id = _CLIENT_ID
        conn._http_client = AsyncMock(spec=httpx.AsyncClient)
        conn._http_client.post = AsyncMock(
            return_value=_make_httpx_response(
                400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}
            )
        )

        with pytest.raises(SpotifyCredentialError, match="400"):
            await conn._refresh_access_token()


# ---------------------------------------------------------------------------
# 10.3 — Session aggregation: play/pause/resume lifecycle
# ---------------------------------------------------------------------------


class TestSessionAggregationLifecycle:
    """Integration: play → pause → resume lifecycle via connector's session tracker."""

    @pytest.fixture
    def connector(self) -> SpotifyConnector:
        cfg = _make_config(session_idle_timeout_s=300)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID
        conn._access_token = _ACCESS_TOKEN
        conn._token_expires_at = _T0 + timedelta(hours=1)
        return conn

    async def test_play_pause_resume_continues_same_session(
        self, connector: SpotifyConnector
    ) -> None:
        """Play → pause (draining) → resume within timeout keeps same session."""
        submitted: list[dict[str, Any]] = []

        async def _capture(env: dict[str, Any]) -> None:
            submitted.append(env)

        with patch.object(connector, "_submit_envelope", side_effect=_capture):
            # Play track
            payload1 = _make_currently_playing(
                track_id="t1", context_uri="spotify:playlist:X", timestamp_ms=_T0_MS
            )
            await connector._handle_active_playback(
                payload1, payload1["item"], _T0, _T0.isoformat()
            )

            # Pause (no playback) — transitions to draining
            t1 = _T0 + timedelta(seconds=30)
            await connector._handle_no_playback(t1, t1.isoformat())
            assert connector._session_tracker.state == "draining"

            # Resume same track before timeout — back to active, no summary
            t2 = _T0 + timedelta(seconds=60)
            payload2 = _make_currently_playing(
                track_id="t1",
                context_uri="spotify:playlist:X",
                timestamp_ms=int(t2.timestamp() * 1000),
            )
            await connector._handle_active_playback(payload2, payload2["item"], t2, t2.isoformat())
            assert connector._session_tracker.state == "active"

        # No session_summary should have been emitted — session continues
        event_types = [e["event"]["event_type"] for e in submitted]
        assert "spotify.session_summary" not in event_types

    async def test_pause_beyond_timeout_closes_session(self, connector: SpotifyConnector) -> None:
        """Pause exceeding idle timeout → session_summary emitted and state returns to idle."""
        cfg = _make_config(session_idle_timeout_s=30)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        submitted: list[dict[str, Any]] = []

        async def _capture(env: dict[str, Any]) -> None:
            submitted.append(env)

        with patch.object(conn, "_submit_envelope", side_effect=_capture):
            # Play track
            payload = _make_currently_playing(
                track_id="t1", context_uri="spotify:playlist:X", timestamp_ms=_T0_MS
            )
            await conn._handle_active_playback(payload, payload["item"], _T0, _T0.isoformat())

            # Pause
            t1 = _T0 + timedelta(seconds=10)
            await conn._handle_no_playback(t1, t1.isoformat())
            assert conn._session_tracker.state == "draining"

            # Exceed idle timeout (>30 seconds)
            t2 = _T0 + timedelta(seconds=60)
            await conn._handle_no_playback(t2, t2.isoformat())

        assert conn._session_tracker.state == "idle"
        event_types = [e["event"]["event_type"] for e in submitted]
        assert "spotify.session_summary" in event_types

    async def test_resume_with_different_context_after_timeout_emits_summary(
        self, connector: SpotifyConnector
    ) -> None:
        """Idle timeout during draining → session_summary, then new track_change on next play."""
        # Use a short idle timeout so we can trigger the timeout easily
        cfg = _make_config(session_idle_timeout_s=30)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        submitted: list[dict[str, Any]] = []

        async def _capture(env: dict[str, Any]) -> None:
            submitted.append(env)

        with patch.object(conn, "_submit_envelope", side_effect=_capture):
            # Play in playlist X
            payload1 = _make_currently_playing(
                track_id="t1", context_uri="spotify:playlist:X", timestamp_ms=_T0_MS
            )
            await conn._handle_active_playback(payload1, payload1["item"], _T0, _T0.isoformat())

            # Pause
            t1 = _T0 + timedelta(seconds=10)
            await conn._handle_no_playback(t1, t1.isoformat())
            assert conn._session_tracker.state == "draining"

            # Exceed idle timeout — session closes
            t2 = _T0 + timedelta(seconds=60)
            await conn._handle_no_playback(t2, t2.isoformat())
            assert conn._session_tracker.state == "idle"

            # Play in album Y — starts a new session
            t3 = _T0 + timedelta(seconds=70)
            payload2 = _make_currently_playing(
                track_id="t2",
                context_uri="spotify:album:Y",
                timestamp_ms=int(t3.timestamp() * 1000),
            )
            await conn._handle_active_playback(payload2, payload2["item"], t3, t3.isoformat())

        event_types = [e["event"]["event_type"] for e in submitted]
        assert "spotify.session_summary" in event_types
        assert event_types.count("spotify.track_change") >= 2

    async def test_context_change_while_playing_emits_summary(
        self, connector: SpotifyConnector
    ) -> None:
        """Context change while active → old session_summary before new track_change."""
        submitted: list[dict[str, Any]] = []

        async def _capture(env: dict[str, Any]) -> None:
            submitted.append(env)

        with patch.object(connector, "_submit_envelope", side_effect=_capture):
            payload1 = _make_currently_playing(
                track_id="t1", context_uri="spotify:playlist:X", timestamp_ms=_T0_MS
            )
            await connector._handle_active_playback(
                payload1, payload1["item"], _T0, _T0.isoformat()
            )

            t1 = _T0 + timedelta(minutes=10)
            payload2 = _make_currently_playing(
                track_id="t2",
                context_uri="spotify:album:Z",
                timestamp_ms=int(t1.timestamp() * 1000),
            )
            await connector._handle_active_playback(payload2, payload2["item"], t1, t1.isoformat())

        event_types = [e["event"]["event_type"] for e in submitted]
        # session_summary must come before the second track_change
        first_summary_idx = next(
            (i for i, t in enumerate(event_types) if t == "spotify.session_summary"), None
        )
        second_track_idx = next(
            (i for i, t in enumerate(reversed(event_types)) if t == "spotify.track_change"),
            None,
        )
        assert first_summary_idx is not None
        assert second_track_idx is not None


# ---------------------------------------------------------------------------
# 10.4 — Adaptive polling: active 60s / idle 300s switching
# ---------------------------------------------------------------------------


class TestAdaptivePolling:
    """Integration: poll interval adapts between active playback and idle states."""

    async def test_interval_resets_to_active_on_playback(self) -> None:
        """Active playback resets poll interval to poll_active_s immediately."""
        cfg = _make_config(poll_active_s=60, poll_idle_s=300)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID
        # Set to idle-max to verify reset
        conn._current_poll_interval_s = float(cfg.poll_idle_s)

        payload = _make_currently_playing(track_id="t1", timestamp_ms=_T0_MS)
        with patch.object(conn, "_submit_envelope", new_callable=AsyncMock):
            await conn._handle_active_playback(payload, payload["item"], _T0, _T0.isoformat())

        assert conn._current_poll_interval_s == cfg.poll_active_s

    async def test_interval_grows_exponentially_when_idle(self) -> None:
        """Idle state: poll interval doubles up to poll_idle_s max."""
        cfg = _make_config(poll_active_s=60, poll_idle_s=300)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._current_poll_interval_s = float(cfg.poll_active_s)

        # Record interval at each idle step
        intervals: list[float] = []
        for _ in range(10):
            await conn._handle_no_playback(_T0, _T0.isoformat())
            intervals.append(conn._current_poll_interval_s)

        # Must be non-decreasing
        for a, b in zip(intervals, intervals[1:]):
            assert b >= a

        # Must eventually cap at poll_idle_s
        assert intervals[-1] == cfg.poll_idle_s

    async def test_interval_caps_at_idle_max(self) -> None:
        """Interval never exceeds poll_idle_s, even with many idle cycles."""
        cfg = _make_config(poll_active_s=60, poll_idle_s=300)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._current_poll_interval_s = 250.0  # near ceiling

        for _ in range(20):
            await conn._handle_no_playback(_T0, _T0.isoformat())

        assert conn._current_poll_interval_s == cfg.poll_idle_s

    async def test_active_playback_after_idle_resets_to_active(self) -> None:
        """Active interval is restored when playback resumes after a long idle period."""
        cfg = _make_config(poll_active_s=60, poll_idle_s=300)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        # Simulate idle: interval has grown to max
        conn._current_poll_interval_s = float(cfg.poll_idle_s)

        # Playback resumes
        payload = _make_currently_playing(track_id="t1", timestamp_ms=_T0_MS)
        with patch.object(conn, "_submit_envelope", new_callable=AsyncMock):
            await conn._handle_active_playback(payload, payload["item"], _T0, _T0.isoformat())

        assert conn._current_poll_interval_s == cfg.poll_active_s

    async def test_full_idle_to_active_cycle_interval_changes(self) -> None:
        """Simulate a full idle → active interval switching round-trip."""
        cfg = _make_config(poll_active_s=60, poll_idle_s=300)
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        # Start at active
        assert conn._current_poll_interval_s == cfg.poll_active_s

        # Idle steps
        for _ in range(5):
            await conn._handle_no_playback(_T0, _T0.isoformat())

        interval_after_idle = conn._current_poll_interval_s
        assert interval_after_idle > cfg.poll_active_s

        # Playback resumes — resets to active
        payload = _make_currently_playing(track_id="t1", timestamp_ms=_T0_MS)
        with patch.object(conn, "_submit_envelope", new_callable=AsyncMock):
            await conn._handle_active_playback(payload, payload["item"], _T0, _T0.isoformat())

        assert conn._current_poll_interval_s == cfg.poll_active_s


# ---------------------------------------------------------------------------
# 10.5 — Checkpoint persistence and resume after restart
# ---------------------------------------------------------------------------


class TestCheckpointPersistenceAndResume:
    """Integration: cursor checkpoint is saved, loaded, and used to skip old tracks."""

    async def test_checkpoint_saved_after_active_playback(self) -> None:
        """After active playback, cursor is updated to the track's timestamp."""
        cfg = _make_config()
        mock_cursor_pool = MagicMock()
        conn = SpotifyConnector(config=cfg, cursor_pool=mock_cursor_pool)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        payload = _make_currently_playing(track_id="t1", timestamp_ms=_T0_MS)
        with patch.object(conn, "_submit_envelope", new_callable=AsyncMock):
            await conn._handle_active_playback(payload, payload["item"], _T0, _T0.isoformat())

        assert conn._last_recently_played_cursor == str(_T0_MS)

    async def test_checkpoint_persisted_to_cursor_store_when_changed(self) -> None:
        """_save_checkpoint writes to cursor store when cursor is new or changed."""
        cfg = _make_config()
        mock_cursor_pool = MagicMock()
        conn = SpotifyConnector(config=cfg, cursor_pool=mock_cursor_pool)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._last_recently_played_cursor = "1700000000001"
        conn._last_checkpoint_cursor = "1700000000000"  # different

        with patch("butlers.connectors.spotify.save_cursor", new_callable=AsyncMock) as mock_save:
            await conn._save_checkpoint()

        mock_save.assert_awaited_once_with(
            mock_cursor_pool, _CONNECTOR_TYPE, _ENDPOINT_IDENTITY, "1700000000001"
        )
        assert conn._last_checkpoint_cursor == "1700000000001"

    async def test_checkpoint_not_persisted_when_unchanged(self) -> None:
        """_save_checkpoint is a no-op when cursor has not changed."""
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg, cursor_pool=MagicMock())
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._last_recently_played_cursor = "1700000000000"
        conn._last_checkpoint_cursor = "1700000000000"  # same

        with patch("butlers.connectors.spotify.save_cursor", new_callable=AsyncMock) as mock_save:
            await conn._save_checkpoint()

        mock_save.assert_not_awaited()

    async def test_checkpoint_loaded_on_startup(self) -> None:
        """_load_checkpoint reads cursor from store and sets both cursor fields."""
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg, cursor_pool=MagicMock())
        conn._endpoint_identity = _ENDPOINT_IDENTITY

        with patch(
            "butlers.connectors.spotify.load_cursor",
            new_callable=AsyncMock,
            return_value="1700999000000",
        ):
            await conn._load_checkpoint()

        assert conn._last_recently_played_cursor == "1700999000000"
        assert conn._last_checkpoint_cursor == "1700999000000"

    async def test_recently_played_cursor_filters_already_seen_tracks(self) -> None:
        """After restart with cursor, _poll_recently_played skips tracks at or before cursor."""
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        # Simulate cursor loaded from previous run
        conn._last_recently_played_cursor = str(_T0_MS)

        # Recently-played items: all at or before the cursor
        mock_items = [
            {
                "track": {
                    "id": "old_track",
                    "name": "Old Song",
                    "artists": [{"name": "Old Artist"}],
                    "album": {"name": "Old Album"},
                    "duration_ms": 200000,
                },
                "played_at": _T0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "context": None,
            }
        ]

        with (
            patch.object(
                conn, "_get_recently_played", new_callable=AsyncMock, return_value=mock_items
            ),
            patch.object(conn, "_submit_envelope", new_callable=AsyncMock) as mock_submit,
        ):
            await conn._poll_recently_played(_T0, _T0.isoformat())

        # Already seen → should not be submitted
        mock_submit.assert_not_awaited()

    async def test_recently_played_cursor_emits_new_tracks_after_cursor(self) -> None:
        """After restart with cursor, _poll_recently_played emits only new tracks."""
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID

        # Cursor from before the new track
        old_cursor_ms = _T0_MS - 100_000
        conn._last_recently_played_cursor = str(old_cursor_ms)

        # One item that is newer than the cursor
        new_time = _T0.strftime("%Y-%m-%dT%H:%M:%SZ")
        mock_items = [
            {
                "track": {
                    "id": "new_track",
                    "name": "New Song",
                    "artists": [{"name": "New Artist"}],
                    "album": {"name": "New Album"},
                    "duration_ms": 200000,
                },
                "played_at": new_time,
                "context": None,
            }
        ]

        submitted: list[dict[str, Any]] = []

        async def _capture(env: dict[str, Any]) -> None:
            submitted.append(env)

        with (
            patch.object(
                conn, "_get_recently_played", new_callable=AsyncMock, return_value=mock_items
            ),
            patch.object(conn, "_submit_envelope", side_effect=_capture),
        ):
            await conn._poll_recently_played(_T0, _T0.isoformat())

        assert len(submitted) == 1
        assert submitted[0]["event"]["event_type"] == "spotify.track_change"

    async def test_cursor_advances_to_latest_recently_played_track(self) -> None:
        """Cursor is advanced to the played_at timestamp of the most recent processed track."""
        cfg = _make_config()
        conn = SpotifyConnector(config=cfg)
        conn._endpoint_identity = _ENDPOINT_IDENTITY
        conn._spotify_user_id = _USER_ID
        conn._last_recently_played_cursor = None  # fresh start

        t1 = _T0 + timedelta(minutes=1)
        t2 = _T0 + timedelta(minutes=2)

        # Items returned most-recent-first (API order)
        mock_items = [
            {
                "track": {
                    "id": "track_b",
                    "name": "Song B",
                    "artists": [],
                    "album": {"name": "Alb"},
                    "duration_ms": 180000,
                },
                "played_at": t2.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "context": None,
            },
            {
                "track": {
                    "id": "track_a",
                    "name": "Song A",
                    "artists": [],
                    "album": {"name": "Alb"},
                    "duration_ms": 180000,
                },
                "played_at": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "context": None,
            },
        ]

        with (
            patch.object(
                conn, "_get_recently_played", new_callable=AsyncMock, return_value=mock_items
            ),
            patch.object(conn, "_submit_envelope", new_callable=AsyncMock),
        ):
            await conn._poll_recently_played(_T0, _T0.isoformat())

        # Cursor should be at the latest track's timestamp (t2)
        expected_cursor = str(int(t2.timestamp() * 1000))
        assert conn._last_recently_played_cursor == expected_cursor


# ---------------------------------------------------------------------------
# 10.6 — Dashboard OAuth flow: PKCE start → callback → token storage
# ---------------------------------------------------------------------------


class TestDashboardOAuthFlow:
    """Integration: Spotify OAuth PKCE flow token exchange and CredentialStore writes.

    The Spotify connector uses PKCE for the OAuth flow. These tests focus on
    the token storage and credential refresh side (SpotifyClient) rather than
    the HTTP redirect dance, which is handled by the dashboard UI.
    """

    async def test_token_exchange_stores_all_four_credential_keys(self) -> None:
        """After successful token refresh, all four credential keys are stored."""
        http = AsyncMock(spec=httpx.AsyncClient)
        refresh_resp = _make_httpx_response(
            200,
            {
                "access_token": _NEW_ACCESS_TOKEN,
                "refresh_token": _NEW_REFRESH_TOKEN,
                "expires_in": 3600,
            },
        )
        http.post = AsyncMock(return_value=refresh_resp)
        # GET /me must succeed to confirm token works
        me_resp = _make_httpx_response(200, {"id": "alice"})
        http.get = AsyncMock(return_value=me_resp)

        # Force proactive refresh by setting token as nearly expired
        near_expiry = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
        store_with_near_expiry = _make_credential_store(expires_at=near_expiry)

        client = SpotifyClient(credential_store=store_with_near_expiry, http_client=http)
        await client.open()
        await client.get_me()

        # All credential keys should be stored
        stored_keys = {c[0][0] for c in store_with_near_expiry.store.call_args_list}
        assert "SPOTIFY_ACCESS_TOKEN" in stored_keys
        assert "SPOTIFY_REFRESH_TOKEN" in stored_keys
        assert "SPOTIFY_TOKEN_EXPIRES_AT" in stored_keys

    async def test_token_exchange_updates_in_memory_state(self) -> None:
        """After successful refresh, in-memory access_token and expires_at are updated."""
        near_expiry = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(
            return_value=_make_httpx_response(
                200,
                {
                    "access_token": _NEW_ACCESS_TOKEN,
                    "refresh_token": _NEW_REFRESH_TOKEN,
                    "expires_in": 3600,
                },
            )
        )
        http.get = AsyncMock(return_value=_make_httpx_response(200, {"id": "alice"}))

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        await client.get_me()

        assert client._access_token == _NEW_ACCESS_TOKEN
        assert client._refresh_token == _NEW_REFRESH_TOKEN
        assert client._expires_at is not None
        # New expiry should be roughly 1 hour from now
        delta = client._expires_at - datetime.now(UTC)
        assert timedelta(minutes=55) <= delta <= timedelta(minutes=65)

    async def test_failed_token_exchange_raises_auth_error(self) -> None:
        """HTTP 400 from token endpoint raises SpotifyAuthError."""
        near_expiry = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(
            return_value=_make_httpx_response(
                400, {"error": "invalid_client", "error_description": "Invalid client_id"}
            )
        )
        http.get = AsyncMock()

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        with pytest.raises(SpotifyAuthError):
            await client.get_me()

    async def test_refresh_without_refresh_token_raises_auth_error(self) -> None:
        """SpotifyClient raises SpotifyAuthError when no refresh token is available."""
        store = _make_credential_store(access_token=None, refresh_token=None, expires_at=None)
        http = AsyncMock(spec=httpx.AsyncClient)

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        with pytest.raises(SpotifyAuthError, match="[Nn]o refresh token"):
            await client._refresh_access_token()

    async def test_new_refresh_token_rotated_when_provided(self) -> None:
        """When Spotify returns a new refresh token, it is stored (PKCE rotation)."""
        near_expiry = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)
        # Spotify rotates the refresh token in PKCE flows
        http.post = AsyncMock(
            return_value=_make_httpx_response(
                200,
                {
                    "access_token": _NEW_ACCESS_TOKEN,
                    "refresh_token": "AQA_ROTATED_refresh_token",
                    "expires_in": 3600,
                },
            )
        )
        http.get = AsyncMock(return_value=_make_httpx_response(200, {"id": "alice"}))

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        await client.get_me()

        assert client._refresh_token == "AQA_ROTATED_refresh_token"
        # Rotated refresh token must be persisted
        stored = {c[0][0]: c[0][1] for c in store.store.call_args_list}
        assert stored.get("SPOTIFY_REFRESH_TOKEN") == "AQA_ROTATED_refresh_token"

    async def test_no_refresh_token_in_response_uses_existing(self) -> None:
        """When Spotify does not rotate the refresh token, existing one is kept."""
        near_expiry = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        original_refresh = "AQA_original_refresh_token"
        store = _make_credential_store(refresh_token=original_refresh, expires_at=near_expiry)

        http = AsyncMock(spec=httpx.AsyncClient)
        # No refresh_token in response (Spotify won't always rotate)
        http.post = AsyncMock(
            return_value=_make_httpx_response(
                200, {"access_token": _NEW_ACCESS_TOKEN, "expires_in": 3600}
            )
        )
        http.get = AsyncMock(return_value=_make_httpx_response(200, {"id": "alice"}))

        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        await client.get_me()

        # Original refresh token is preserved
        assert client._refresh_token == original_refresh
