"""Tests for Spotify connector Prometheus metrics (tasks 8.1-8.4).

Covers:
- ConnectorMetrics integration: standard connector metrics are recorded during
  poll cycles (task 8.1)
- Spotify-specific counters: polls_total, track_changes_total, sessions_total,
  token_refreshes_total (task 8.2)
- Spotify-specific histogram: session_duration_seconds (task 8.2)
- Health endpoint on port 40083 (task 8.3) — server initialisation checked
  without starting a live server to keep tests fast
- Heartbeat protocol config: default 120s interval via HeartbeatConfig (task 8.4)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.spotify import (
    ListeningSession,
    SpotifyConnector,
    SpotifyConnectorConfig,
    spotify_polls_total,
    spotify_session_duration_seconds,
    spotify_sessions_total,
    spotify_token_refreshes_total,
    spotify_track_changes_total,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> SpotifyConnectorConfig:
    return SpotifyConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        poll_active_s=60,
        poll_idle_s=300,
        session_idle_timeout_s=300,
        health_port=40083,
    )


@pytest.fixture(autouse=True)
def clear_spotify_metrics() -> None:
    """Clear Spotify-specific metric state before each test."""
    for collector in [
        spotify_polls_total,
        spotify_track_changes_total,
        spotify_sessions_total,
        spotify_token_refreshes_total,
    ]:
        collector._metrics.clear()

    spotify_session_duration_seconds._metrics.clear()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


def _get_counter(counter, **labels) -> float:
    """Read a Prometheus counter value by label set."""
    return counter.labels(**labels)._value.get()


def _get_histogram_count(histogram, **labels) -> float:
    """Read the _count sample of a Prometheus histogram."""
    samples = list(histogram.collect()[0].samples)
    target = dict(labels)
    count_s = next(
        (s for s in samples if s.name.endswith("_count") and dict(s.labels) == target),
        None,
    )
    return count_s.value if count_s is not None else 0.0


def _get_histogram_sum(histogram, **labels) -> float:
    """Read the _sum sample of a Prometheus histogram."""
    samples = list(histogram.collect()[0].samples)
    target = dict(labels)
    sum_s = next(
        (s for s in samples if s.name.endswith("_sum") and dict(s.labels) == target),
        None,
    )
    return sum_s.value if sum_s is not None else 0.0


# ---------------------------------------------------------------------------
# Task 8.2 — Spotify-specific counters
# ---------------------------------------------------------------------------


class TestSpotifyPollsTotal:
    """connector_spotify_polls_total increments on poll outcomes."""

    @pytest.mark.asyncio
    async def test_polls_total_increments_on_active_playback(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
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

        assert (
            _get_counter(spotify_polls_total, endpoint_identity="spotify:alice", status="success")
            == 1.0
        )

    @pytest.mark.asyncio
    async def test_polls_total_increments_on_idle(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"

        await connector._handle_no_playback(now, now.isoformat())

        assert (
            _get_counter(spotify_polls_total, endpoint_identity="spotify:alice", status="idle")
            == 1.0
        )

    @pytest.mark.asyncio
    async def test_polls_total_accumulates_across_cycles(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"

        for _ in range(3):
            await connector._handle_no_playback(now, now.isoformat())

        assert (
            _get_counter(spotify_polls_total, endpoint_identity="spotify:alice", status="idle")
            == 3.0
        )


class TestSpotifyTrackChangesTotal:
    """connector_spotify_track_changes_total increments on track change events."""

    @pytest.mark.asyncio
    async def test_track_changes_total_increments_on_new_track(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
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
            "context": {"uri": "spotify:playlist:abc"},
            "timestamp": int(now.timestamp() * 1000),
            "device": None,
        }
        item = payload["item"]

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock):
            await connector._handle_active_playback(payload, item, now, now.isoformat())

        assert _get_counter(spotify_track_changes_total, endpoint_identity="spotify:alice") == 1.0

    @pytest.mark.asyncio
    async def test_track_changes_total_does_not_increment_on_same_track(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
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
            "context": {"uri": "spotify:playlist:abc"},
            "timestamp": int(now.timestamp() * 1000),
            "device": None,
        }
        item = payload["item"]

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock):
            # First call — starts session, emits track_change
            await connector._handle_active_playback(payload, item, now, now.isoformat())
            # Second call — same track, no event
            await connector._handle_active_playback(
                payload, item, now + timedelta(seconds=30), now.isoformat()
            )

        # Only 1 track_change should have been emitted (initial start)
        assert _get_counter(spotify_track_changes_total, endpoint_identity="spotify:alice") == 1.0

    @pytest.mark.asyncio
    async def test_track_changes_total_increments_via_recently_played(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"
        connector._last_recently_played_cursor = "1000000"  # old cursor

        played_at_ms = int(now.timestamp() * 1000)
        mock_items = [
            {
                "track": {
                    "id": "track99",
                    "name": "Gap Fill Song",
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
                connector, "_get_recently_played", new_callable=AsyncMock, return_value=mock_items
            ),
            patch.object(connector, "_submit_envelope", new_callable=AsyncMock),
        ):
            await connector._poll_recently_played(now, now.isoformat())

        assert _get_counter(spotify_track_changes_total, endpoint_identity="spotify:alice") == 1.0
        assert connector._last_recently_played_cursor == str(played_at_ms)


class TestSpotifySessionsTotal:
    """connector_spotify_sessions_total increments when sessions close."""

    @pytest.mark.asyncio
    async def test_sessions_total_increments_on_session_close(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"

        session = ListeningSession(
            context_uri="spotify:playlist:abc",
            started_at=now,
            track_names=["Song A", "Song B"],
        )
        session.drain_started_at = now + timedelta(minutes=10)

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock):
            await connector._emit_session_summary(session, now.isoformat())

        assert _get_counter(spotify_sessions_total, endpoint_identity="spotify:alice") == 1.0

    @pytest.mark.asyncio
    async def test_sessions_total_not_incremented_for_empty_session(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"

        empty_session = ListeningSession(
            context_uri=None,
            started_at=now,
            track_names=[],  # empty — should be skipped
        )

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock) as mock_submit:
            await connector._emit_session_summary(empty_session, now.isoformat())

        mock_submit.assert_not_awaited()
        assert _get_counter(spotify_sessions_total, endpoint_identity="spotify:alice") == 0.0


class TestSpotifySessionDurationSeconds:
    """connector_spotify_session_duration_seconds histogram records session lengths."""

    @pytest.mark.asyncio
    async def test_session_duration_observed_on_session_close(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"

        session = ListeningSession(
            context_uri="spotify:album:xyz",
            started_at=now,
            track_names=["Song A"],
        )
        session.drain_started_at = now + timedelta(minutes=15)  # 900s

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock):
            await connector._emit_session_summary(session, now.isoformat())

        count = _get_histogram_count(
            spotify_session_duration_seconds, endpoint_identity="spotify:alice"
        )
        total = _get_histogram_sum(
            spotify_session_duration_seconds, endpoint_identity="spotify:alice"
        )
        assert count == 1.0
        assert abs(total - 900.0) < 1.0

    @pytest.mark.asyncio
    async def test_session_duration_accumulates_across_multiple_sessions(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"

        sessions = []
        for i, minutes in enumerate([10, 20, 5]):
            s = ListeningSession(
                context_uri=f"spotify:playlist:p{i}",
                started_at=now + timedelta(hours=i),
                track_names=["Song"],
            )
            s.drain_started_at = s.started_at + timedelta(minutes=minutes)
            sessions.append(s)

        with patch.object(connector, "_submit_envelope", new_callable=AsyncMock):
            for s in sessions:
                await connector._emit_session_summary(s, now.isoformat())

        count = _get_histogram_count(
            spotify_session_duration_seconds, endpoint_identity="spotify:alice"
        )
        assert count == 3.0


class TestSpotifyTokenRefreshesTotal:
    """connector_spotify_token_refreshes_total increments on token refresh outcomes."""

    @pytest.mark.asyncio
    async def test_token_refreshes_total_success(self, config: SpotifyConnectorConfig) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._refresh_token = "rt123"
        connector._client_id = "clientabc"
        connector._http_client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "expires_in": 3600,
        }
        connector._http_client.post = AsyncMock(return_value=mock_response)

        with patch.object(connector, "_persist_tokens", new_callable=AsyncMock):
            await connector._refresh_access_token()

        assert (
            _get_counter(
                spotify_token_refreshes_total,
                endpoint_identity="spotify:alice",
                status="success",
            )
            == 1.0
        )

    @pytest.mark.asyncio
    async def test_token_refreshes_total_error_on_400(self, config: SpotifyConnectorConfig) -> None:
        from butlers.connectors.spotify import SpotifyCredentialError

        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._refresh_token = "rt123"
        connector._client_id = "clientabc"
        connector._http_client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error":"invalid_grant"}'
        connector._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(SpotifyCredentialError):
            await connector._refresh_access_token()

        assert (
            _get_counter(
                spotify_token_refreshes_total,
                endpoint_identity="spotify:alice",
                status="error",
            )
            == 1.0
        )

    @pytest.mark.asyncio
    async def test_token_refreshes_total_error_on_transport_failure(
        self, config: SpotifyConnectorConfig
    ) -> None:
        import httpx

        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._refresh_token = "rt123"
        connector._client_id = "clientabc"
        connector._http_client = MagicMock()
        connector._http_client.post = AsyncMock(side_effect=httpx.TransportError("network error"))

        with pytest.raises(RuntimeError, match="Token refresh transport error"):
            await connector._refresh_access_token()

        assert (
            _get_counter(
                spotify_token_refreshes_total,
                endpoint_identity="spotify:alice",
                status="error",
            )
            == 1.0
        )


# ---------------------------------------------------------------------------
# Task 8.1 — ConnectorMetrics (standard) is integrated in SpotifyConnector
# ---------------------------------------------------------------------------


class TestConnectorMetricsIntegration:
    """Standard ConnectorMetrics methods are called during Spotify connector operations."""

    @pytest.mark.asyncio
    async def test_ingest_submission_metric_recorded_on_success(
        self, config: SpotifyConnectorConfig, now: datetime
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._spotify_user_id = "alice"
        connector._metrics = MagicMock()

        mock_result = {"status": "accepted"}
        connector._mcp_client = MagicMock()
        connector._mcp_client.call_tool = AsyncMock(return_value=mock_result)

        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "spotify",
                "provider": "spotify",
                "endpoint_identity": "spotify:alice",
            },
            "event": {
                "external_event_id": "spotify:1000:t1",
                "external_thread_id": None,
                "observed_at": now.isoformat(),
                "event_type": "spotify.track_change",
            },
            "sender": {"identity": "alice"},
            "payload": {"raw": {}, "normalized_text": "test"},
            "control": {
                "idempotency_key": "k1",
                "policy_tier": "default",
                "ingestion_tier": "full",
            },
        }

        # Bypass filter gate
        connector._ingestion_policy = None

        await connector._submit_envelope(envelope)

        connector._metrics.record_ingest_submission.assert_called_once()
        # record_ingest_submission is called as record_ingest_submission(status, latency)
        call_args = connector._metrics.record_ingest_submission.call_args
        # Positional args: (status, latency) — status is first positional arg
        assert call_args[0][0] == "success"

    @pytest.mark.asyncio
    async def test_checkpoint_save_metric_recorded(self, config: SpotifyConnectorConfig) -> None:
        mock_cursor_pool = MagicMock()
        connector = SpotifyConnector(
            config=config, db_pool=MagicMock(), cursor_pool=mock_cursor_pool
        )
        connector._endpoint_identity = "spotify:alice"
        connector._last_recently_played_cursor = "1700000000000"
        connector._metrics = MagicMock()

        with patch("butlers.connectors.spotify.save_cursor", new_callable=AsyncMock):
            await connector._save_checkpoint()

        connector._metrics.record_checkpoint_save.assert_called_once_with("success")


# ---------------------------------------------------------------------------
# Task 8.3 — Health endpoint port configuration
# ---------------------------------------------------------------------------


class TestHealthEndpointConfig:
    """Health server is configured on port 40083 (default) or the configured port."""

    def test_default_health_port_is_40083(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("CONNECTOR_HEALTH_PORT", raising=False)

        cfg = SpotifyConnectorConfig.from_env()
        assert cfg.health_port == 40083

    def test_custom_health_port_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40099")

        cfg = SpotifyConnectorConfig.from_env()
        assert cfg.health_port == 40099

    def test_health_server_uses_make_health_socket(self, config: SpotifyConnectorConfig) -> None:
        """_start_health_server calls make_health_socket with the configured port."""
        connector = SpotifyConnector(config=config)
        connector._start_time = 0.0
        connector._endpoint_identity = "spotify:alice"

        with (
            patch("butlers.connectors.spotify.make_health_socket") as mock_socket,
            patch("butlers.connectors.spotify.uvicorn.Server") as mock_server_cls,
            patch("butlers.connectors.spotify.Thread") as mock_thread,
        ):
            mock_socket.return_value = MagicMock()
            mock_server_instance = MagicMock()
            mock_server_cls.return_value = mock_server_instance
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            connector._start_health_server()

        mock_socket.assert_called_once_with("127.0.0.1", 40083)

    def test_health_server_binds_to_localhost(self, config: SpotifyConnectorConfig) -> None:
        """Health server binds to 127.0.0.1, not 0.0.0.0."""
        connector = SpotifyConnector(config=config)

        with (
            patch("butlers.connectors.spotify.make_health_socket") as mock_socket,
            patch("butlers.connectors.spotify.uvicorn.Server"),
            patch("butlers.connectors.spotify.Thread") as mock_thread,
        ):
            mock_socket.return_value = MagicMock()
            mock_thread.return_value = MagicMock()
            connector._start_health_server()

        call_args = mock_socket.call_args
        assert call_args[0][0] == "127.0.0.1"


# ---------------------------------------------------------------------------
# Task 8.4 — Heartbeat protocol (120s interval)
# ---------------------------------------------------------------------------


class TestHeartbeatProtocol:
    """ConnectorHeartbeat is initialised with 120s default interval."""

    def test_default_heartbeat_interval_is_120s(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from butlers.connectors.heartbeat import HeartbeatConfig

        monkeypatch.delenv("CONNECTOR_HEARTBEAT_INTERVAL_S", raising=False)
        monkeypatch.delenv("CONNECTOR_HEARTBEAT_ENABLED", raising=False)

        hb_config = HeartbeatConfig.from_env(
            connector_type="spotify",
            endpoint_identity="spotify:alice",
        )

        assert hb_config.interval_s == 120
        assert hb_config.enabled is True

    def test_heartbeat_created_with_connector_type_spotify(
        self, config: SpotifyConnectorConfig
    ) -> None:
        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:alice"
        connector._metrics = MagicMock()
        connector._mcp_client = MagicMock()

        connector._endpoint_identity_ready()

        assert connector._heartbeat is not None
        assert connector._heartbeat._config.connector_type == "spotify"
        assert connector._heartbeat._config.endpoint_identity == "spotify:alice"

    def test_heartbeat_interval_respects_env(
        self, config: SpotifyConnectorConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONNECTOR_HEARTBEAT_INTERVAL_S", "180")

        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:bob"
        connector._mcp_client = MagicMock()

        connector._endpoint_identity_ready()

        assert connector._heartbeat is not None
        assert connector._heartbeat._config.interval_s == 180

    def test_heartbeat_disabled_via_env(
        self, config: SpotifyConnectorConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONNECTOR_HEARTBEAT_ENABLED", "false")

        connector = SpotifyConnector(config=config)
        connector._endpoint_identity = "spotify:carol"
        connector._mcp_client = MagicMock()

        connector._endpoint_identity_ready()

        assert connector._heartbeat is not None
        assert connector._heartbeat._config.enabled is False
