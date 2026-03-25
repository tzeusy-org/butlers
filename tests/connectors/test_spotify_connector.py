"""Tests for the Spotify connector.

Covers:
- SpotifyConnectorConfig.from_env() loading
- ListeningSessionTracker state machine (all transitions)
- build_track_change_envelope field mapping
- build_session_summary_envelope field mapping
- SpotifyConnector credential resolution and error recovery
- SpotifyConnector adaptive polling interval logic
- SpotifyConnector recently-played gap-filling cursor logic
- SpotifyConnector filter gate integration
- SpotifyConnector checkpoint persistence
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.spotify import (
    _CONNECTOR_CHANNEL,
    _CONNECTOR_PROVIDER,
    _CONNECTOR_TYPE,
    _DEFAULT_POLL_ACTIVE_S,
    _DEFAULT_POLL_IDLE_S,
    _DEFAULT_SESSION_IDLE_TIMEOUT_S,
    ListeningSession,
    ListeningSessionTracker,
    SpotifyConnector,
    SpotifyConnectorConfig,
    build_session_summary_envelope,
    build_track_change_envelope,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> SpotifyConnectorConfig:
    return SpotifyConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        poll_active_s=60,
        poll_idle_s=300,
        session_idle_timeout_s=300,
    )


@pytest.fixture
def tracker() -> ListeningSessionTracker:
    return ListeningSessionTracker(idle_timeout_s=300)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# SpotifyConnectorConfig
# ---------------------------------------------------------------------------


class TestSpotifyConnectorConfig:
    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("SPOTIFY_POLL_ACTIVE_S", raising=False)
        monkeypatch.delenv("SPOTIFY_POLL_IDLE_S", raising=False)
        monkeypatch.delenv("SPOTIFY_SESSION_IDLE_TIMEOUT_S", raising=False)
        monkeypatch.delenv("CONNECTOR_HEALTH_PORT", raising=False)

        cfg = SpotifyConnectorConfig.from_env()

        assert cfg.switchboard_mcp_url == "http://localhost:41100/sse"
        assert cfg.poll_active_s == _DEFAULT_POLL_ACTIVE_S
        assert cfg.poll_idle_s == _DEFAULT_POLL_IDLE_S
        assert cfg.session_idle_timeout_s == _DEFAULT_SESSION_IDLE_TIMEOUT_S
        assert cfg.health_port == 40083

    def test_from_env_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("SPOTIFY_POLL_ACTIVE_S", "30")
        monkeypatch.setenv("SPOTIFY_POLL_IDLE_S", "600")
        monkeypatch.setenv("SPOTIFY_SESSION_IDLE_TIMEOUT_S", "180")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40099")

        cfg = SpotifyConnectorConfig.from_env()

        assert cfg.poll_active_s == 30
        assert cfg.poll_idle_s == 600
        assert cfg.session_idle_timeout_s == 180
        assert cfg.health_port == 40099

    def test_from_env_missing_switchboard_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)

        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL is required"):
            SpotifyConnectorConfig.from_env()

    def test_from_env_invalid_int_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("SPOTIFY_POLL_ACTIVE_S", "not-a-number")

        cfg = SpotifyConnectorConfig.from_env()

        assert cfg.poll_active_s == _DEFAULT_POLL_ACTIVE_S


# ---------------------------------------------------------------------------
# ListeningSessionTracker
# ---------------------------------------------------------------------------


class TestListeningSessionTracker:
    def test_initial_state_is_idle(self, tracker: ListeningSessionTracker) -> None:
        assert tracker.state == "idle"
        assert tracker.current_session is None

    def test_idle_to_active_on_first_playback(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        events, closed = tracker.process_playback(
            track_id="track1",
            track_name="Song A",
            context_uri="spotify:playlist:abc",
            now=now,
        )
        assert tracker.state == "active"
        assert "track_change" in events
        assert closed == []
        assert tracker.current_session is not None
        assert tracker.current_session.track_names == ["Song A"]

    def test_active_same_track_no_event(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        tracker.process_playback(track_id="track1", track_name="Song A", context_uri=None, now=now)
        events, closed = tracker.process_playback(
            track_id="track1",
            track_name="Song A",
            context_uri=None,
            now=now + timedelta(seconds=30),
        )
        assert events == []
        assert closed == []
        assert tracker.state == "active"
        assert tracker.current_session is not None
        assert len(tracker.current_session.track_names) == 1

    def test_active_track_change_same_context(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        tracker.process_playback(
            track_id="track1",
            track_name="Song A",
            context_uri="spotify:playlist:abc",
            now=now,
        )
        events, closed = tracker.process_playback(
            track_id="track2",
            track_name="Song B",
            context_uri="spotify:playlist:abc",
            now=now + timedelta(seconds=60),
        )
        assert "track_change" in events
        assert closed == []
        assert tracker.state == "active"
        assert tracker.current_session is not None
        assert tracker.current_session.track_names == ["Song A", "Song B"]

    def test_active_context_change_closes_session(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        tracker.process_playback(
            track_id="track1",
            track_name="Song A",
            context_uri="spotify:playlist:abc",
            now=now,
        )
        events, closed = tracker.process_playback(
            track_id="track2",
            track_name="Song B",
            context_uri="spotify:album:xyz",
            now=now + timedelta(minutes=5),
        )
        assert "track_change" in events
        assert len(closed) == 1
        assert closed[0].context_uri == "spotify:playlist:abc"
        assert closed[0].track_names == ["Song A"]
        assert tracker.state == "active"
        assert tracker.current_session is not None
        assert tracker.current_session.context_uri == "spotify:album:xyz"

    def test_active_to_draining_on_no_playback(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        tracker.process_playback(track_id="track1", track_name="Song A", context_uri=None, now=now)
        closed = tracker.process_no_playback(now=now + timedelta(seconds=5))
        assert tracker.state == "draining"
        assert closed == []

    def test_draining_resume_returns_to_active(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        tracker.process_playback(track_id="track1", track_name="Song A", context_uri=None, now=now)
        tracker.process_no_playback(now=now + timedelta(seconds=5))
        assert tracker.state == "draining"

        events, closed = tracker.process_playback(
            track_id="track1",
            track_name="Song A",
            context_uri=None,
            now=now + timedelta(seconds=30),
        )
        assert tracker.state == "active"
        assert closed == []
        # Same track resumed: no additional track_change
        assert events == []

    def test_draining_resume_new_track_emits_event(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        tracker.process_playback(track_id="track1", track_name="Song A", context_uri=None, now=now)
        tracker.process_no_playback(now=now + timedelta(seconds=5))
        events, closed = tracker.process_playback(
            track_id="track2",
            track_name="Song B",
            context_uri=None,
            now=now + timedelta(seconds=30),
        )
        assert tracker.state == "active"
        assert "track_change" in events
        assert closed == []

    def test_draining_idle_timeout_closes_session(self, now: datetime) -> None:
        tracker = ListeningSessionTracker(idle_timeout_s=60)
        tracker.process_playback(
            track_id="track1", track_name="Song A", context_uri="spotify:album:x", now=now
        )
        tracker.process_no_playback(now=now + timedelta(seconds=5))
        # Not yet timed out
        closed = tracker.process_no_playback(now=now + timedelta(seconds=30))
        assert closed == []
        assert tracker.state == "draining"
        # Timeout exceeded
        closed = tracker.process_no_playback(now=now + timedelta(seconds=120))
        assert len(closed) == 1
        assert closed[0].context_uri == "spotify:album:x"
        assert tracker.state == "idle"
        assert tracker.current_session is None

    def test_idle_no_playback_is_noop(
        self, tracker: ListeningSessionTracker, now: datetime
    ) -> None:
        closed = tracker.process_no_playback(now=now)
        assert closed == []
        assert tracker.state == "idle"

    def test_session_track_count(self, tracker: ListeningSessionTracker, now: datetime) -> None:
        tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx", now=now)
        tracker.process_playback(
            track_id="t2",
            track_name="B",
            context_uri="ctx",
            now=now + timedelta(seconds=60),
        )
        assert tracker.current_session is not None
        assert tracker.current_session.track_count == 2

    def test_session_duration_seconds(self, now: datetime) -> None:
        session = ListeningSession(
            context_uri="ctx",
            started_at=now,
            track_names=["A"],
        )
        session.drain_started_at = now + timedelta(minutes=10)
        assert abs(session.duration_seconds - 600) < 1


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


class TestBuildTrackChangeEnvelope:
    def test_basic_fields(self, now: datetime) -> None:
        envelope = build_track_change_envelope(
            endpoint_identity="spotify:alice",
            spotify_user_id="alice",
            track_id="track123",
            track_name="My Song",
            artist_names=["Artist One", "Artist Two"],
            album_name="My Album",
            duration_ms=240000,
            context_uri="spotify:playlist:abc",
            device_name="MacBook",
            timestamp_ms=1000000,
            raw_payload={"is_playing": True},
            observed_at=now.isoformat(),
        )

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == _CONNECTOR_CHANNEL
        assert envelope["source"]["provider"] == _CONNECTOR_PROVIDER
        assert envelope["source"]["endpoint_identity"] == "spotify:alice"
        assert envelope["event"]["event_type"] == "spotify.track_change"
        assert envelope["event"]["external_event_id"] == "spotify:1000000:track123"
        assert envelope["event"]["external_thread_id"] == "spotify:playlist:abc"
        assert envelope["sender"]["identity"] == "alice"
        assert envelope["control"]["policy_tier"] == "default"
        assert envelope["control"]["ingestion_tier"] == "full"
        assert "idempotency_key" in envelope["control"]

    def test_normalized_text_with_context(self, now: datetime) -> None:
        envelope = build_track_change_envelope(
            endpoint_identity="spotify:alice",
            spotify_user_id="alice",
            track_id="t1",
            track_name="My Song",
            artist_names=["Artist"],
            album_name="Album",
            duration_ms=200000,
            context_uri="spotify:playlist:abc",
            device_name=None,
            timestamp_ms=1000,
            raw_payload={},
            observed_at=now.isoformat(),
        )
        text = envelope["payload"]["normalized_text"]
        assert "My Song" in text
        assert "Artist" in text
        # Context URI last segment used
        assert "abc" in text

    def test_normalized_text_without_context(self, now: datetime) -> None:
        envelope = build_track_change_envelope(
            endpoint_identity="spotify:alice",
            spotify_user_id="alice",
            track_id="t1",
            track_name="My Song",
            artist_names=["Artist"],
            album_name="Album",
            duration_ms=200000,
            context_uri=None,
            device_name=None,
            timestamp_ms=1000,
            raw_payload={},
            observed_at=now.isoformat(),
        )
        text = envelope["payload"]["normalized_text"]
        assert "My Song" in text
        assert "Artist" in text

    def test_idempotency_key_unique_per_track_and_timestamp(self, now: datetime) -> None:
        def make_env(track_id: str, ts_ms: int) -> dict[str, Any]:
            return build_track_change_envelope(
                endpoint_identity="spotify:alice",
                spotify_user_id="alice",
                track_id=track_id,
                track_name="S",
                artist_names=[],
                album_name="A",
                duration_ms=0,
                context_uri=None,
                device_name=None,
                timestamp_ms=ts_ms,
                raw_payload={},
                observed_at=now.isoformat(),
            )

        e1 = make_env("t1", 1000)
        e2 = make_env("t2", 1000)
        e3 = make_env("t1", 2000)
        keys = {
            e1["control"]["idempotency_key"],
            e2["control"]["idempotency_key"],
            e3["control"]["idempotency_key"],
        }
        assert len(keys) == 3


class TestBuildSessionSummaryEnvelope:
    def test_basic_fields(self, now: datetime) -> None:
        session = ListeningSession(
            context_uri="spotify:playlist:abc",
            started_at=now,
            track_names=["Song A", "Song B", "Song C"],
        )
        session.drain_started_at = now + timedelta(minutes=15)

        envelope = build_session_summary_envelope(
            endpoint_identity="spotify:alice",
            spotify_user_id="alice",
            session=session,
            observed_at=now.isoformat(),
        )

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == _CONNECTOR_CHANNEL
        assert envelope["event"]["event_type"] == "spotify.session_summary"
        assert envelope["event"]["external_thread_id"] == "spotify:playlist:abc"
        assert "session_start_ms" not in envelope["event"]["external_event_id"]
        assert envelope["event"]["external_event_id"].startswith("spotify:session:")

    def test_normalized_text_includes_track_count_and_duration(self, now: datetime) -> None:
        session = ListeningSession(
            context_uri="spotify:album:xyz",
            started_at=now,
            track_names=["A", "B"],
        )
        session.drain_started_at = now + timedelta(minutes=10)

        envelope = build_session_summary_envelope(
            endpoint_identity="spotify:alice",
            spotify_user_id="alice",
            session=session,
            observed_at=now.isoformat(),
        )
        text = envelope["payload"]["normalized_text"]
        assert "2 tracks" in text
        assert "10m0s" in text

    def test_raw_payload_contains_session_fields(self, now: datetime) -> None:
        session = ListeningSession(
            context_uri=None,
            started_at=now,
            track_names=["Song A"],
        )
        session.drain_started_at = now + timedelta(minutes=5)

        envelope = build_session_summary_envelope(
            endpoint_identity="spotify:alice",
            spotify_user_id="alice",
            session=session,
            observed_at=now.isoformat(),
        )
        raw = envelope["payload"]["raw"]
        assert "session_start" in raw
        assert "session_end" in raw
        assert raw["track_count"] == 1
        assert raw["tracks"] == ["Song A"]


# ---------------------------------------------------------------------------
# SpotifyConnector unit tests
# ---------------------------------------------------------------------------


class TestSpotifyConnectorConstants:
    """Verify connector type constants."""

    def test_connector_type_constant(self) -> None:
        assert _CONNECTOR_TYPE == "spotify"
        assert _CONNECTOR_CHANNEL == "spotify"
        assert _CONNECTOR_PROVIDER == "spotify"


class TestSpotifyConnectorAdaptivePolling:
    """Test adaptive polling interval logic via the actual connector methods."""

    @pytest.mark.asyncio
    async def test_poll_interval_resets_to_active_on_playback(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._current_poll_interval_s = 240.0
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"

        payload = {
            "is_playing": True,
            "item": {
                "id": "track1",
                "name": "Song A",
                "type": "track",
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
                "duration_ms": 180000,
            },
            "context": None,
            "timestamp": int(now.timestamp() * 1000),
            "device": None,
        }
        item = payload["item"]

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock):
            await connector._handle_active_playback(payload, item, now, now.isoformat())

        assert connector._current_poll_interval_s == config.poll_active_s

    @pytest.mark.asyncio
    async def test_poll_interval_increases_toward_idle_max(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._current_poll_interval_s = float(config.poll_active_s)
        connector._endpoint_identity = "spotify:alice"

        # First no-playback call: active → draining; interval steps up
        await connector._handle_no_playback(now, now.isoformat())
        assert connector._current_poll_interval_s > config.poll_active_s

        # Repeated no-playback calls converge to idle max
        for _ in range(20):
            await connector._handle_no_playback(now, now.isoformat())
        assert connector._current_poll_interval_s == config.poll_idle_s


class TestSpotifyConnectorCheckpoint:
    """Test checkpoint cursor management."""

    @pytest.mark.asyncio
    async def test_save_checkpoint_calls_cursor_store(self, config: SpotifyConnectorConfig) -> None:
        mock_pool = MagicMock()
        mock_cursor_pool = MagicMock()
        connector = SpotifyConnector(
            config=config,
            db_pool=mock_pool,
            cursor_pool=mock_cursor_pool,
        )
        connector._endpoint_identity = "spotify:alice"
        connector._last_recently_played_cursor = "1700000000000"

        with patch("butlers.connectors.spotify.save_cursor", new_callable=AsyncMock) as mock_save:
            await connector._save_checkpoint()
            mock_save.assert_awaited_once_with(
                mock_cursor_pool,
                _CONNECTOR_TYPE,
                "spotify:alice",
                "1700000000000",
            )

    @pytest.mark.asyncio
    async def test_save_checkpoint_no_cursor_is_noop(self, config: SpotifyConnectorConfig) -> None:
        mock_pool = MagicMock()
        mock_cursor_pool = MagicMock()
        connector = SpotifyConnector(
            config=config,
            db_pool=mock_pool,
            cursor_pool=mock_cursor_pool,
        )
        connector._endpoint_identity = "spotify:alice"
        connector._last_recently_played_cursor = None

        with patch("butlers.connectors.spotify.save_cursor", new_callable=AsyncMock) as mock_save:
            await connector._save_checkpoint()
            mock_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_checkpoint_unchanged_cursor_is_noop(
        self, config: SpotifyConnectorConfig
    ) -> None:
        """Checkpoint save is skipped when the cursor hasn't changed."""
        mock_pool = MagicMock()
        mock_cursor_pool = MagicMock()
        connector = SpotifyConnector(
            config=config,
            db_pool=mock_pool,
            cursor_pool=mock_cursor_pool,
        )
        connector._endpoint_identity = "spotify:alice"
        connector._last_recently_played_cursor = "1700000000000"
        connector._last_checkpoint_cursor = "1700000000000"  # same as current

        with patch("butlers.connectors.spotify.save_cursor", new_callable=AsyncMock) as mock_save:
            await connector._save_checkpoint()
            mock_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_load_checkpoint_sets_cursor(self, config: SpotifyConnectorConfig) -> None:
        mock_pool = MagicMock()
        mock_cursor_pool = MagicMock()
        connector = SpotifyConnector(
            config=config,
            db_pool=mock_pool,
            cursor_pool=mock_cursor_pool,
        )
        connector._endpoint_identity = "spotify:alice"

        with patch(
            "butlers.connectors.spotify.load_cursor",
            new_callable=AsyncMock,
            return_value="1700000000000",
        ):
            await connector._load_checkpoint()
            assert connector._last_recently_played_cursor == "1700000000000"
            assert connector._last_checkpoint_cursor == "1700000000000"


class TestSpotifyConnectorCredentialErrorRecovery:
    """Test credential error recovery flow."""

    def test_auth_error_state_can_be_set(self, config: SpotifyConnectorConfig) -> None:
        connector = SpotifyConnector(config=config)
        assert connector._auth_error is False
        connector._auth_error = True
        connector._auth_error_message = "auth expired"
        state, msg = connector._get_health_state()
        assert state == "error"
        assert msg == "auth expired"

    @pytest.mark.asyncio
    async def test_reload_credentials_returns_false_without_db_pool(
        self, config: SpotifyConnectorConfig
    ) -> None:
        connector = SpotifyConnector(config=config, db_pool=None)
        result = await connector._reload_credentials()
        assert result is False


class TestSpotifyConnectorHealthState:
    """Test health state reporting."""

    def test_starting_state_when_source_api_ok_is_none(
        self, config: SpotifyConnectorConfig
    ) -> None:
        connector = SpotifyConnector(config=config)
        state, msg = connector._get_health_state()
        assert state == "starting"
        assert msg is None

    def test_healthy_when_source_api_ok(self, config: SpotifyConnectorConfig) -> None:
        connector = SpotifyConnector(config=config)
        connector._source_api_ok = True
        state, _ = connector._get_health_state()
        assert state == "healthy"

    def test_degraded_when_source_api_not_ok(self, config: SpotifyConnectorConfig) -> None:
        connector = SpotifyConnector(config=config)
        connector._source_api_ok = False
        state, _ = connector._get_health_state()
        assert state == "degraded"

    def test_error_when_auth_error(self, config: SpotifyConnectorConfig) -> None:
        connector = SpotifyConnector(config=config)
        connector._auth_error = True
        connector._auth_error_message = "expired token"
        state, msg = connector._get_health_state()
        assert state == "error"
        assert msg == "expired token"


class TestSpotifyConnectorRecentlyPlayedCursorLogic:
    """Test recently-played cursor advancement logic."""

    @pytest.mark.asyncio
    async def test_recently_played_cursor_advances_on_new_tracks(
        self, config: SpotifyConnectorConfig
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"
        connector._last_recently_played_cursor = "1000000"

        # Mock the HTTP GET for recently-played
        mock_items = [
            {
                "track": {
                    "id": "track1",
                    "name": "Song A",
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "duration_ms": 200000,
                },
                "played_at": "2026-03-25T12:00:00Z",
                "context": None,
            }
        ]

        with (
            patch.object(
                connector,
                "_get_recently_played",
                new_callable=AsyncMock,
                return_value=mock_items,
            ),
            patch.object(
                connector,
                "_submit_envelope",
                new_callable=AsyncMock,
            ) as mock_submit,
        ):
            now = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
            played_at_ms = int(now.timestamp() * 1000)

            await connector._poll_recently_played(now, now.isoformat())

            # Cursor should advance to the played_at timestamp
            assert connector._last_recently_played_cursor == str(played_at_ms)
            mock_submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recently_played_skips_already_seen_tracks(
        self, config: SpotifyConnectorConfig
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"

        now = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
        played_at_ms = int(now.timestamp() * 1000)
        # Set cursor to already-seen timestamp
        connector._last_recently_played_cursor = str(played_at_ms)

        mock_items = [
            {
                "track": {
                    "id": "track1",
                    "name": "Song A",
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "duration_ms": 200000,
                },
                "played_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "context": None,
            }
        ]

        with (
            patch.object(
                connector,
                "_get_recently_played",
                new_callable=AsyncMock,
                return_value=mock_items,
            ),
            patch.object(
                connector,
                "_submit_envelope",
                new_callable=AsyncMock,
            ) as mock_submit,
        ):
            await connector._poll_recently_played(now, now.isoformat())

            # Should not submit — already seen
            mock_submit.assert_not_awaited()
