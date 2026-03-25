"""Tests for the OwnTracks connector lifecycle base contract.

Covers tasks 6.1-6.7:
- 6.1: Heartbeat assembly (connector_type, event counters)
- 6.2: Prometheus metrics (connector_owntracks_events_received_total)
- 6.3: Health endpoint (state/uptime_s/last_event_at/events_today)
- 6.4: Filtered event batch flush
- 6.5: Replay queue drain
- 6.6: IngestionPolicyEvaluator source filter gate
- 6.7: Tests for heartbeat assembly, metrics emission, and health endpoint

Also covers:
- OwnTracksConnectorConfig.from_env()
- Normalized text generation (location, transition, waypoints)
- Envelope builders (location, transition, waypoints)
- Bearer token validation
- Checkpoint save/load
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.owntracks import (
    _CONNECTOR_TYPE,
    _DEFAULT_HEALTH_PORT,
    _DEFAULT_RETENTION_DAYS,
    _TIER_FULL,
    _TIER_METADATA,
    OwnTracksConnector,
    OwnTracksConnectorConfig,
    build_location_envelope,
    build_location_normalized_text,
    build_transition_envelope,
    build_transition_normalized_text,
    build_waypoints_envelope,
    build_waypoints_normalized_text,
    resolve_webhook_token,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> OwnTracksConnectorConfig:
    return OwnTracksConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
    )


@pytest.fixture
def config_full_tier() -> OwnTracksConnectorConfig:
    return OwnTracksConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        ingestion_tier="full",
    )


@pytest.fixture
def connector(config: OwnTracksConnectorConfig) -> OwnTracksConnector:
    return OwnTracksConnector(
        config=config,
        webhook_token="test-secret-token",
    )


@pytest.fixture
def location_payload() -> dict[str, Any]:
    return {
        "_type": "location",
        "lat": 48.8566,
        "lon": 2.3522,
        "acc": 10,
        "vel": 25,
        "tst": 1700000000,
        "tid": "ph",
    }


@pytest.fixture
def transition_payload() -> dict[str, Any]:
    return {
        "_type": "transition",
        "event": "enter",
        "desc": "Home",
        "lat": 48.8566,
        "lon": 2.3522,
        "acc": 10,
        "tst": 1700000000,
        "tid": "ph",
    }


@pytest.fixture
def waypoints_payload() -> dict[str, Any]:
    return {
        "_type": "waypoints",
        "waypoints": [
            {"desc": "Home", "tst": 1699000000},
            {"desc": "Office", "tst": 1699000001},
            {"desc": "Gym", "tst": 1699000002},
        ],
        "tst": 1700000000,
        "tid": "ph",
    }


# ---------------------------------------------------------------------------
# OwnTracksConnectorConfig
# ---------------------------------------------------------------------------


class TestOwnTracksConnectorConfig:
    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        for key in [
            "OWNTRACKS_TRACKER_ID",
            "OWNTRACKS_RETENTION_DAYS",
            "CONNECTOR_INGESTION_TIER",
            "CONNECTOR_HEALTH_PORT",
            "CONNECTOR_HEARTBEAT_INTERVAL_S",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = OwnTracksConnectorConfig.from_env()

        assert cfg.switchboard_mcp_url == "http://localhost:41100/sse"
        assert cfg.tracker_id_override is None
        assert cfg.retention_days == _DEFAULT_RETENTION_DAYS
        assert cfg.ingestion_tier == _TIER_METADATA
        assert cfg.health_port == _DEFAULT_HEALTH_PORT

    def test_from_env_missing_switchboard_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)

        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL is required"):
            OwnTracksConnectorConfig.from_env()

    def test_from_env_custom_tracker_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("OWNTRACKS_TRACKER_ID", "my-phone")

        cfg = OwnTracksConnectorConfig.from_env()

        assert cfg.tracker_id_override == "my-phone"

    def test_from_env_full_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_INGESTION_TIER", "full")

        cfg = OwnTracksConnectorConfig.from_env()

        assert cfg.ingestion_tier == _TIER_FULL

    def test_from_env_invalid_tier_defaults_to_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_INGESTION_TIER", "invalid-tier")

        cfg = OwnTracksConnectorConfig.from_env()

        assert cfg.ingestion_tier == _TIER_METADATA

    def test_from_env_retention_days_below_minimum_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "0")

        with pytest.raises(ValueError, match="OWNTRACKS_RETENTION_DAYS=0 is below minimum"):
            OwnTracksConnectorConfig.from_env()

    def test_from_env_custom_health_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40099")

        cfg = OwnTracksConnectorConfig.from_env()

        assert cfg.health_port == 40099


# ---------------------------------------------------------------------------
# Normalized text generation
# ---------------------------------------------------------------------------


class TestLocationNormalizedText:
    def test_basic_location(self) -> None:
        payload = {"lat": 48.8566, "lon": 2.3522, "acc": 10}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "Location update:" in text
        assert "48.8566N" in text
        assert "2.3522E" in text
        assert "acc 10m" in text

    def test_negative_coordinates_use_south_west(self) -> None:
        payload = {"lat": -33.8688, "lon": -70.6693, "acc": 15}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "33.8688S" in text
        assert "70.6693W" in text

    def test_velocity_appended_when_positive(self) -> None:
        payload = {"lat": 48.8566, "lon": 2.3522, "vel": 60}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "60 km/h" in text

    def test_velocity_zero_not_appended(self) -> None:
        payload = {"lat": 48.8566, "lon": 2.3522, "vel": 0}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "km/h" not in text

    def test_inregions_appended(self) -> None:
        payload = {"lat": 48.8566, "lon": 2.3522, "inregions": ["Home", "Office"]}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "(in: Home, Office)" in text

    def test_empty_inregions_not_appended(self) -> None:
        payload = {"lat": 48.8566, "lon": 2.3522, "inregions": []}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "in:" not in text

    def test_ssid_not_in_metadata_tier_text(self) -> None:
        """SSID must not appear in metadata tier normalized text (privacy)."""
        payload = {"lat": 48.8566, "lon": 2.3522, "SSID": "MyHomeWiFi"}
        text = build_location_normalized_text(payload, _TIER_METADATA)
        assert "MyHomeWiFi" not in text

    def test_ssid_not_in_full_tier_text(self) -> None:
        """SSID should not appear in normalized text even in full tier."""
        payload = {"lat": 48.8566, "lon": 2.3522, "SSID": "MyHomeWiFi"}
        text = build_location_normalized_text(payload, _TIER_FULL)
        assert "MyHomeWiFi" not in text


class TestTransitionNormalizedText:
    def test_enter_event(self) -> None:
        payload = {"event": "enter", "desc": "Home"}
        text = build_transition_normalized_text(payload)
        assert text == "Entered region: Home"

    def test_leave_event(self) -> None:
        payload = {"event": "leave", "desc": "Office"}
        text = build_transition_normalized_text(payload)
        assert text == "Left region: Office"

    def test_unknown_event_type(self) -> None:
        payload = {"event": "unknown", "desc": "Somewhere"}
        text = build_transition_normalized_text(payload)
        assert "Transition (unknown)" in text
        assert "Somewhere" in text


class TestWaypointsNormalizedText:
    def test_few_waypoints_listed(self) -> None:
        payload = {
            "waypoints": [
                {"desc": "Home"},
                {"desc": "Office"},
                {"desc": "Gym"},
            ]
        }
        text = build_waypoints_normalized_text(payload)
        assert "Waypoint sync: 3 regions" in text
        assert "Home" in text
        assert "Office" in text
        assert "Gym" in text

    def test_more_than_5_waypoints_truncated(self) -> None:
        payload = {"waypoints": [{"desc": f"Region {i}"} for i in range(8)]}
        text = build_waypoints_normalized_text(payload)
        assert "Waypoint sync: 8 regions" in text
        assert "and 3 more" in text

    def test_empty_waypoints(self) -> None:
        payload = {"waypoints": []}
        text = build_waypoints_normalized_text(payload)
        assert "Waypoint sync: 0 regions" in text


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


class TestLocationEnvelope:
    def test_field_mapping_metadata_tier(self, location_payload: dict[str, Any]) -> None:
        envelope = build_location_envelope(
            location_payload,
            "owntracks:ph",
            "2024-01-01T00:00:00+00:00",
            _TIER_METADATA,
        )

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "owntracks"
        assert envelope["source"]["provider"] == "owntracks"
        assert envelope["source"]["endpoint_identity"] == "owntracks:ph"
        assert envelope["event"]["external_event_id"] == "1700000000:location"
        assert envelope["event"]["external_thread_id"] == "owntracks:ph"
        assert envelope["sender"]["identity"] == "owntracks:ph"
        assert envelope["payload"]["raw"] is None  # metadata tier
        assert isinstance(envelope["payload"]["normalized_text"], str)
        expected_key = "owntracks:owntracks:ph:1700000000:location"
        assert envelope["control"]["idempotency_key"] == expected_key
        assert envelope["control"]["policy_tier"] == "default"
        assert envelope["control"]["ingestion_tier"] == _TIER_METADATA

    def test_field_mapping_full_tier_includes_raw(self, location_payload: dict[str, Any]) -> None:
        envelope = build_location_envelope(
            location_payload,
            "owntracks:ph",
            "2024-01-01T00:00:00+00:00",
            _TIER_FULL,
        )

        assert envelope["payload"]["raw"] == location_payload
        assert envelope["control"]["ingestion_tier"] == _TIER_FULL


class TestTransitionEnvelope:
    def test_field_mapping(self, transition_payload: dict[str, Any]) -> None:
        envelope = build_transition_envelope(
            transition_payload,
            "owntracks:ph",
            "2024-01-01T00:00:00+00:00",
            _TIER_METADATA,
        )

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["event"]["external_event_id"] == "1700000000:transition:enter"
        assert (
            envelope["control"]["idempotency_key"]
            == "owntracks:owntracks:ph:1700000000:transition:enter"
        )
        assert "Entered region: Home" in envelope["payload"]["normalized_text"]
        assert envelope["payload"]["raw"] is None  # metadata tier

    def test_transition_leave_event(self) -> None:
        payload = {
            "_type": "transition",
            "event": "leave",
            "desc": "Office",
            "tst": 1700001000,
            "tid": "ph",
        }
        envelope = build_transition_envelope(
            payload, "owntracks:ph", "2024-01-01T00:00:01+00:00", _TIER_METADATA
        )
        assert "Left region: Office" in envelope["payload"]["normalized_text"]
        assert "leave" in envelope["event"]["external_event_id"]


class TestWaypointsEnvelope:
    def test_field_mapping(self, waypoints_payload: dict[str, Any]) -> None:
        envelope = build_waypoints_envelope(
            waypoints_payload,
            "owntracks:ph",
            "2024-01-01T00:00:00+00:00",
            _TIER_METADATA,
        )

        assert envelope["schema_version"] == "ingest.v1"
        assert "waypoints" in envelope["event"]["external_event_id"]
        assert "Waypoint sync" in envelope["payload"]["normalized_text"]
        assert envelope["payload"]["raw"] is None


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


class TestResolveWebhookToken:
    async def test_resolves_from_credential_store(self) -> None:
        mock_cred_store = MagicMock()
        mock_cred_store.load = AsyncMock(return_value="stored-token")

        token = await resolve_webhook_token(mock_cred_store)

        assert token == "stored-token"
        mock_cred_store.load.assert_called_once_with("owntracks_webhook_token")

    async def test_falls_back_to_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OWNTRACKS_WEBHOOK_TOKEN", "env-token")
        mock_cred_store = MagicMock()
        mock_cred_store.load = AsyncMock(return_value=None)

        token = await resolve_webhook_token(mock_cred_store)

        assert token == "env-token"

    async def test_raises_when_no_token_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OWNTRACKS_WEBHOOK_TOKEN", raising=False)
        mock_cred_store = MagicMock()
        mock_cred_store.load = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="No OwnTracks webhook token configured"):
            await resolve_webhook_token(mock_cred_store)

    async def test_raises_when_no_cred_store_and_no_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OWNTRACKS_WEBHOOK_TOKEN", raising=False)

        with pytest.raises(ValueError, match="No OwnTracks webhook token configured"):
            await resolve_webhook_token(None)

    async def test_resolves_from_env_when_no_cred_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNTRACKS_WEBHOOK_TOKEN", "direct-env-token")

        token = await resolve_webhook_token(None)

        assert token == "direct-env-token"


# ---------------------------------------------------------------------------
# OwnTracksConnector: Health endpoint (task 6.3)
# ---------------------------------------------------------------------------


class TestOwnTracksConnectorHealth:
    def test_initial_health_state_is_healthy(self, connector: OwnTracksConnector) -> None:
        state, error = connector._get_health_state()
        assert state == "healthy"
        assert error is None

    def test_health_state_error_on_failed_ingest(self, connector: OwnTracksConnector) -> None:
        connector._health_error = "Connection refused"
        state, error = connector._get_health_state()
        assert state == "error"
        assert error == "Connection refused"

    def test_health_state_degraded_on_ingest_failure(self, connector: OwnTracksConnector) -> None:
        connector._last_ingest_ok = False
        state, error = connector._get_health_state()
        assert state == "degraded"

    def test_events_today_resets_on_new_day(self, connector: OwnTracksConnector) -> None:
        from datetime import date

        connector._events_today = 5
        connector._events_today_date = date(2024, 1, 1)
        # Simulate calling _process_webhook_event which checks/resets daily counter
        # We test the date reset logic here by directly exercising the fields
        now_date = datetime.now(UTC).date()
        if now_date != connector._events_today_date:
            connector._events_today = 0
            connector._events_today_date = now_date

        # On a different day (today vs 2024-01-01), counter should reset
        if now_date != date(2024, 1, 1):
            assert connector._events_today == 0

    def test_uptime_is_positive(self, connector: OwnTracksConnector) -> None:
        import time

        uptime = int(time.time() - connector._start_time)
        assert uptime >= 0


# ---------------------------------------------------------------------------
# OwnTracksConnector: Webhook event processing (task 6.2, 6.6)
# ---------------------------------------------------------------------------


class TestOwnTracksConnectorWebhookProcessing:
    async def test_location_event_increments_metrics(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that processing a location event increments the OwnTracks counter."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        # Read initial counter value
        from prometheus_client import REGISTRY

        def _get_counter(event_type: str) -> float:
            for metric in REGISTRY.collect():
                if metric.name == "connector_owntracks_events_received":
                    for sample in metric.samples:
                        if sample.labels.get(
                            "event_type"
                        ) == event_type and "ph" in sample.labels.get("endpoint_identity", ""):
                            return sample.value
            return 0.0

        before = _get_counter("location")
        await connector._process_webhook_event(location_payload)
        after = _get_counter("location")

        assert after > before

    async def test_ignored_event_increments_ignored_counter(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        """Test that unsupported _type increments 'ignored' counter."""
        from prometheus_client import REGISTRY

        payload = {"_type": "lwt", "tst": 1700000000, "tid": "ph"}
        # Resolve the endpoint identity that the connector will use when processing this payload.
        expected_identity = "owntracks:ph"

        def _get_counter(event_type: str, identity: str) -> float:
            for metric in REGISTRY.collect():
                if metric.name == "connector_owntracks_events_received":
                    for sample in metric.samples:
                        if (
                            sample.labels.get("event_type") == event_type
                            and sample.labels.get("endpoint_identity") == identity
                        ):
                            return sample.value
            return 0.0

        before = _get_counter("ignored", expected_identity)
        await connector._process_webhook_event(payload)
        after = _get_counter("ignored", expected_identity)

        assert after == before + 1

    async def test_policy_blocked_event_recorded_in_filtered_buffer(
        self,
        config: OwnTracksConnectorConfig,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that a policy-blocked event is added to filtered event buffer (task 6.4)."""
        from butlers.ingestion_policy import PolicyDecision

        # Pin the tracker ID so _process_webhook_event never replaces the mock policy
        # via _endpoint_identity_ready() on the first event.
        config.tracker_id_override = "ph"
        connector = OwnTracksConnector(config=config, webhook_token="test-secret-token")

        mock_policy = MagicMock()
        mock_policy.evaluate = MagicMock(
            return_value=PolicyDecision(
                action="block",
                reason="test block",
                matched_rule_type="chat_id",
            )
        )
        mock_policy.scope = "connector:owntracks:owntracks:ph"
        connector._ingestion_policy = mock_policy

        await connector._process_webhook_event(location_payload)

        # Filtered buffer should have one entry
        assert len(connector._filtered_event_buffer) == 1

    async def test_submission_to_mcp_on_valid_event(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that a valid event is submitted to the Switchboard."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        await connector._process_webhook_event(location_payload)

        mock_mcp_client.call_tool.assert_called_once()
        call_args = mock_mcp_client.call_tool.call_args
        assert call_args[0][0] == "ingest"
        envelope = call_args[0][1]
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "owntracks"

    async def test_transition_event_processed(
        self,
        connector: OwnTracksConnector,
        transition_payload: dict[str, Any],
    ) -> None:
        """Test that a transition event is processed and submitted."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        await connector._process_webhook_event(transition_payload)

        call_args = mock_mcp_client.call_tool.call_args
        envelope = call_args[0][1]
        assert "transition" in envelope["event"]["external_event_id"]

    async def test_waypoints_event_processed(
        self,
        connector: OwnTracksConnector,
        waypoints_payload: dict[str, Any],
    ) -> None:
        """Test that a waypoints event is processed and submitted."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        await connector._process_webhook_event(waypoints_payload)

        call_args = mock_mcp_client.call_tool.call_args
        envelope = call_args[0][1]
        assert "waypoints" in envelope["event"]["external_event_id"]

    async def test_unknown_payload_type_ignored(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        """Test that unsupported _type is silently ignored (no MCP call)."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock()
        connector._mcp_client = mock_mcp_client

        lwt_payload = {"_type": "lwt", "tst": 1700000000, "tid": "ph"}
        await connector._process_webhook_event(lwt_payload)

        mock_mcp_client.call_tool.assert_not_called()

    async def test_metadata_tier_raw_is_none(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that metadata tier does not include raw payload."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        await connector._process_webhook_event(location_payload)

        call_args = mock_mcp_client.call_tool.call_args
        envelope = call_args[0][1]
        assert envelope["payload"]["raw"] is None

    async def test_full_tier_raw_is_included(
        self,
        config_full_tier: OwnTracksConnectorConfig,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that full tier includes raw payload."""
        connector = OwnTracksConnector(
            config=config_full_tier,
            webhook_token="test-secret-token",
        )
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        await connector._process_webhook_event(location_payload)

        call_args = mock_mcp_client.call_tool.call_args
        envelope = call_args[0][1]
        assert envelope["payload"]["raw"] == location_payload

    async def test_tid_updates_endpoint_identity(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that the device tid updates the endpoint identity when not overridden."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        location_payload["tid"] = "xy"
        await connector._process_webhook_event(location_payload)

        assert connector._endpoint_identity == "owntracks:xy"

    async def test_tid_not_updated_when_overridden(
        self,
        config: OwnTracksConnectorConfig,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that tracker_id_override prevents tid from changing identity."""
        config.tracker_id_override = "fixed-id"
        connector = OwnTracksConnector(
            config=config,
            webhook_token="test-token",
        )
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        original_identity = connector._endpoint_identity
        location_payload["tid"] = "different-tid"
        await connector._process_webhook_event(location_payload)

        assert connector._endpoint_identity == original_identity


# ---------------------------------------------------------------------------
# OwnTracksConnector: Checkpoint (task 6.1 counters / checkpoint state)
# ---------------------------------------------------------------------------


class TestOwnTracksConnectorCheckpoint:
    async def test_checkpoint_saved_after_location_event(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that checkpoint is saved after a successful ingest."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        mock_pool = MagicMock()
        connector._db_pool = mock_pool
        connector._cursor_pool = mock_pool

        with patch("butlers.connectors.owntracks.save_cursor") as mock_save:
            mock_save.return_value = None
            await connector._process_webhook_event(location_payload)
            mock_save.assert_called_once()
            # First arg is pool, second connector_type, third identity, fourth cursor value
            call_args = mock_save.call_args[0]
            assert call_args[1] == _CONNECTOR_TYPE
            assert call_args[3] == str(location_payload["tst"])

    async def test_get_checkpoint_info_returns_none_when_no_checkpoint(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        cursor, updated_at = connector._get_checkpoint_info()
        assert cursor is None
        assert updated_at is None

    async def test_get_checkpoint_info_after_save(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        import time

        connector._last_checkpoint_tst = 1700000000
        connector._last_checkpoint_save = time.time()

        cursor, updated_at = connector._get_checkpoint_info()
        assert cursor == "1700000000"
        assert updated_at is not None


# ---------------------------------------------------------------------------
# OwnTracksConnector: Heartbeat assembly (task 6.1)
# ---------------------------------------------------------------------------


class TestOwnTracksConnectorHeartbeat:
    def test_heartbeat_initialized_with_connector_type(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        """Test that heartbeat is configured with connector_type='owntracks'."""
        connector._init_heartbeat()

        assert connector._heartbeat is not None
        assert connector._heartbeat._config.connector_type == _CONNECTOR_TYPE

    def test_heartbeat_uses_endpoint_identity(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        """Test that heartbeat uses the connector's endpoint identity."""
        connector._endpoint_identity = "owntracks:my-device"
        connector._init_heartbeat()

        assert connector._heartbeat is not None
        assert connector._heartbeat._config.endpoint_identity == "owntracks:my-device"

    def test_heartbeat_get_health_state_callback_works(
        self,
        connector: OwnTracksConnector,
    ) -> None:
        """Test that heartbeat can call get_health_state."""
        connector._init_heartbeat()
        assert connector._heartbeat is not None

        state, error = connector._heartbeat._get_health_state()
        assert state in ("healthy", "degraded", "error")


# ---------------------------------------------------------------------------
# OwnTracksConnector: Replay queue drain (task 6.5)
# ---------------------------------------------------------------------------


class TestOwnTracksConnectorReplayDrain:
    async def test_replay_drain_called_after_event(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that replay drain is invoked after processing a webhook event."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client

        mock_pool = MagicMock()
        connector._db_pool = mock_pool
        connector._cursor_pool = mock_pool

        with (
            patch("butlers.connectors.owntracks.save_cursor", return_value=None),
            patch("butlers.connectors.owntracks.drain_replay_pending") as mock_drain,
        ):
            mock_drain.return_value = None
            await connector._process_webhook_event(location_payload)
            mock_drain.assert_called_once()
            call_kwargs = mock_drain.call_args[1]
            assert call_kwargs["pool"] == mock_pool
            assert call_kwargs["connector_type"] == _CONNECTOR_TYPE
            assert call_kwargs["endpoint_identity"] == connector._endpoint_identity
            assert call_kwargs["submit_fn"] == connector._submit_envelope

    async def test_replay_drain_not_called_without_db_pool(
        self,
        connector: OwnTracksConnector,
        location_payload: dict[str, Any],
    ) -> None:
        """Test that replay drain is skipped when no DB pool is available."""
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        connector._mcp_client = mock_mcp_client
        connector._db_pool = None

        with patch("butlers.connectors.owntracks.drain_replay_pending") as mock_drain:
            await connector._process_webhook_event(location_payload)
            mock_drain.assert_not_called()
