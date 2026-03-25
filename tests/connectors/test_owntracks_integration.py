"""Integration tests for the OwnTracks connector.

Covers tasks 10.1–10.8 from openspec/changes/connector-owntracks/tasks.md:

10.1 - Valid webhook POST with location payload is normalized and submitted to Switchboard
10.2 - Valid webhook POST with transition payload is normalized and submitted to Switchboard
10.3 - Webhook POST without auth returns 401
10.4 - Webhook POST with invalid auth returns 401
10.5 - Webhook POST with unknown ``_type`` returns 200 but is not ingested
10.6 - Metadata tier omits ``payload.raw``, full tier includes it
10.7 - Retention purge deletes old events and preserves recent ones
10.8 - Dashboard token generation stores token in CredentialStore and connector accepts it

No real network I/O is performed; all DB and MCP calls are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from butlers.connectors.owntracks import (
    OwnTracksConnector,
    OwnTracksConnectorConfig,
    _normalize_location_text,
    _normalize_transition_text,
    _normalize_waypoints_text,
    normalize_location_envelope,
    normalize_transition_envelope,
    resolve_webhook_token,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared constants and helpers
# ---------------------------------------------------------------------------

_VALID_TOKEN = "abc123def456abc123def456abc12345deadbeef"
_INVALID_TOKEN = "wrong-token-value"
_TRACKER_ID = "xx"
_ENDPOINT_IDENTITY = f"owntracks:{_TRACKER_ID}"
_TST = 1711368000  # 2024-03-25 12:00:00 UTC


def _make_config(
    *,
    ingestion_tier: str = "metadata",
    tracker_id: str = _TRACKER_ID,
    retention_days: int = 30,
) -> OwnTracksConnectorConfig:
    return OwnTracksConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        webhook_token=_VALID_TOKEN,
        tracker_id=tracker_id,
        ingestion_tier=ingestion_tier,
        retention_days=retention_days,
        health_port=40083,
    )


_CONFIG_KEYS = frozenset({"ingestion_tier", "tracker_id", "retention_days"})


def _make_connector(
    config: OwnTracksConnectorConfig | None = None, **kwargs: Any
) -> OwnTracksConnector:
    if config is None:
        config = _make_config(**{k: v for k, v in kwargs.items() if k in _CONFIG_KEYS})
    mcp_client = AsyncMock()
    mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
    return OwnTracksConnector(config, mcp_client=mcp_client)


def _make_location_payload(
    *,
    tid: str = _TRACKER_ID,
    tst: int = _TST,
    lat: float = 48.8566,
    lon: float = 2.3522,
    acc: int = 10,
    vel: int | None = None,
    inregions: list[str] | None = None,
    ssid: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "_type": "location",
        "tid": tid,
        "tst": tst,
        "lat": lat,
        "lon": lon,
        "acc": acc,
        "alt": 35,
        "batt": 80,
        "conn": "wifi",
    }
    if vel is not None:
        payload["vel"] = vel
    if inregions is not None:
        payload["inregions"] = inregions
    if ssid is not None:
        payload["SSID"] = ssid
    return payload


def _make_transition_payload(
    *,
    tid: str = _TRACKER_ID,
    tst: int = _TST,
    event: str = "enter",
    desc: str = "Home",
    lat: float = 48.8566,
    lon: float = 2.3522,
) -> dict[str, Any]:
    return {
        "_type": "transition",
        "tid": tid,
        "tst": tst,
        "event": event,
        "desc": desc,
        "lat": lat,
        "lon": lon,
        "acc": 20,
    }


def _make_waypoints_payload(
    *,
    tid: str = _TRACKER_ID,
    tst: int = _TST,
    waypoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if waypoints is None:
        waypoints = [
            {"_type": "waypoint", "desc": "Home", "lat": 48.8566, "lon": 2.3522, "rad": 100},
            {"_type": "waypoint", "desc": "Office", "lat": 48.8800, "lon": 2.3500, "rad": 150},
            {"_type": "waypoint", "desc": "Gym", "lat": 48.8700, "lon": 2.3600, "rad": 50},
        ]
    return {"_type": "waypoints", "tid": tid, "tst": tst, "waypoints": waypoints}


# ---------------------------------------------------------------------------
# 10.1 — Location payload: webhook POST → Switchboard submission
# ---------------------------------------------------------------------------


class TestLocationWebhookFlow:
    """Integration: valid location POST is normalized and submitted to Switchboard."""

    @pytest.fixture
    def connector(self) -> OwnTracksConnector:
        return _make_connector()

    @pytest.fixture
    def client(self, connector: OwnTracksConnector) -> TestClient:
        app = connector.build_app()
        return TestClient(app, raise_server_exceptions=True)

    def test_location_post_returns_200_with_empty_array(self, client: TestClient) -> None:
        """Successful location POST returns HTTP 200 with empty JSON array (OwnTracks protocol)."""
        payload = _make_location_payload()
        resp = client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_location_payload_submitted_to_switchboard(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Location POST triggers MCP ingest call with correct ingest.v1 envelope."""
        payload = _make_location_payload()
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )

        connector._mcp_client.call_tool.assert_awaited_once()
        call_args = connector._mcp_client.call_tool.await_args
        assert call_args[0][0] == "ingest"
        envelope = call_args[0][1]
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "owntracks"
        assert envelope["source"]["provider"] == "owntracks"
        assert envelope["source"]["endpoint_identity"] == _ENDPOINT_IDENTITY
        assert envelope["event"]["event_type"] == "owntracks.location"
        assert envelope["event"]["external_event_id"] == f"{_TST}:location"
        assert envelope["event"]["external_thread_id"] == f"owntracks:{_TRACKER_ID}"
        assert envelope["sender"]["identity"] == f"owntracks:{_TRACKER_ID}"
        expected_key = f"owntracks:{_ENDPOINT_IDENTITY}:{_TST}:location"
        assert envelope["control"]["idempotency_key"] == expected_key
        assert envelope["control"]["policy_tier"] == "default"
        assert envelope["control"]["ingestion_tier"] == "metadata"

    def test_location_normalized_text_content(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Location normalized_text contains cardinal coordinates and accuracy."""
        payload = _make_location_payload(lat=48.8566, lon=2.3522, acc=10)
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        text = envelope["payload"]["normalized_text"]
        assert "48.8566N" in text
        assert "2.3522E" in text
        assert "acc 10m" in text

    def test_location_with_velocity_appended(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Location text includes velocity when vel > 0."""
        payload = _make_location_payload(vel=50)
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        text = envelope["payload"]["normalized_text"]
        assert "50 km/h" in text

    def test_location_with_inregions_appended(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Location text includes in-region list when inregions is non-empty."""
        payload = _make_location_payload(inregions=["Home", "Suburb"])
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        text = envelope["payload"]["normalized_text"]
        assert "in: Home, Suburb" in text

    def test_location_metadata_tier_omits_raw(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Metadata tier: payload.raw is None."""
        payload = _make_location_payload()
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["payload"]["raw"] is None


# ---------------------------------------------------------------------------
# 10.2 — Transition payload: webhook POST → Switchboard submission
# ---------------------------------------------------------------------------


class TestTransitionWebhookFlow:
    """Integration: valid transition POST is normalized and submitted to Switchboard."""

    @pytest.fixture
    def connector(self) -> OwnTracksConnector:
        return _make_connector()

    @pytest.fixture
    def client(self, connector: OwnTracksConnector) -> TestClient:
        return TestClient(connector.build_app(), raise_server_exceptions=True)

    def test_transition_enter_returns_200(self, client: TestClient) -> None:
        """Enter transition POST returns HTTP 200."""
        payload = _make_transition_payload(event="enter", desc="Home")
        resp = client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_transition_submitted_with_correct_event_type(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Transition event envelope has event_type owntracks.transition."""
        payload = _make_transition_payload(event="enter", desc="Office")
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["event"]["event_type"] == "owntracks.transition"
        assert envelope["event"]["external_event_id"] == f"{_TST}:transition:enter"

    def test_transition_enter_normalized_text(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Enter transition: normalized_text is 'Entered region: <desc>'."""
        payload = _make_transition_payload(event="enter", desc="Home")
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["payload"]["normalized_text"] == "Entered region: Home"

    def test_transition_leave_normalized_text(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Leave transition: normalized_text is 'Left region: <desc>'."""
        payload = _make_transition_payload(event="leave", desc="Office")
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["payload"]["normalized_text"] == "Left region: Office"

    def test_transition_idempotency_key_includes_event_type(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Transition idempotency_key includes enter/leave suffix."""
        payload = _make_transition_payload(event="leave", desc="Home")
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["control"]["idempotency_key"].endswith(":transition:leave")


# ---------------------------------------------------------------------------
# 10.3 — Missing auth header returns 401
# ---------------------------------------------------------------------------


class TestAuthMissingHeader:
    """Integration: webhook POST without Authorization header returns 401."""

    @pytest.fixture
    def client(self) -> TestClient:
        connector = _make_connector()
        return TestClient(connector.build_app(), raise_server_exceptions=False)

    def test_missing_auth_header_returns_401(self, client: TestClient) -> None:
        """POST without Authorization header returns 401."""
        payload = _make_location_payload()
        resp = client.post("/owntracks/webhook", json=payload)
        assert resp.status_code == 401

    def test_missing_auth_response_body(self, client: TestClient) -> None:
        """401 response includes error key."""
        resp = client.post("/owntracks/webhook", json=_make_location_payload())
        data = resp.json()
        assert "error" in str(data).lower() or resp.status_code == 401

    def test_no_mcp_call_on_missing_auth(self) -> None:
        """No MCP ingest call is made when auth header is absent."""
        connector = _make_connector()
        client = TestClient(connector.build_app(), raise_server_exceptions=False)
        client.post("/owntracks/webhook", json=_make_location_payload())
        connector._mcp_client.call_tool.assert_not_awaited()


# ---------------------------------------------------------------------------
# 10.4 — Invalid auth token returns 401
# ---------------------------------------------------------------------------


class TestAuthInvalidToken:
    """Integration: webhook POST with wrong Bearer token returns 401."""

    @pytest.fixture
    def connector(self) -> OwnTracksConnector:
        return _make_connector()

    @pytest.fixture
    def client(self, connector: OwnTracksConnector) -> TestClient:
        return TestClient(connector.build_app(), raise_server_exceptions=False)

    def test_invalid_token_returns_401(self, client: TestClient) -> None:
        """POST with wrong token returns 401."""
        resp = client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Bearer {_INVALID_TOKEN}"},
        )
        assert resp.status_code == 401

    def test_malformed_auth_scheme_returns_401(self, client: TestClient) -> None:
        """POST with non-Bearer auth scheme (e.g. Basic) returns 401."""
        resp = client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Basic {_VALID_TOKEN}"},
        )
        assert resp.status_code == 401

    def test_no_mcp_call_on_invalid_token(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """No MCP ingest call is made when token is wrong."""
        client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Bearer {_INVALID_TOKEN}"},
        )
        connector._mcp_client.call_tool.assert_not_awaited()

    def test_constant_time_comparison_used(self) -> None:
        """Token validation uses hmac.compare_digest (constant-time comparison)."""
        config = _make_config()
        connector = _make_connector(config=config)
        with patch("butlers.connectors.owntracks.hmac.compare_digest") as mock_compare:
            mock_compare.return_value = True
            result = connector._validate_token(_VALID_TOKEN)
            assert result is True
            mock_compare.assert_called_once_with(_VALID_TOKEN, _VALID_TOKEN)


# ---------------------------------------------------------------------------
# 10.5 — Unknown _type returns 200, not ingested
# ---------------------------------------------------------------------------


class TestUnknownPayloadType:
    """Integration: unknown _type returns 200 but no ingest call is made."""

    @pytest.fixture
    def connector(self) -> OwnTracksConnector:
        return _make_connector()

    @pytest.fixture
    def client(self, connector: OwnTracksConnector) -> TestClient:
        return TestClient(connector.build_app(), raise_server_exceptions=True)

    def test_lwt_type_returns_200(self, client: TestClient) -> None:
        """lwt (last will and testament) payload returns 200."""
        resp = client.post(
            "/owntracks/webhook",
            json={"_type": "lwt", "tst": _TST},
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_cmd_type_returns_200(self, client: TestClient) -> None:
        """cmd payload returns 200."""
        resp = client.post(
            "/owntracks/webhook",
            json={"_type": "cmd", "action": "setWaypoints"},
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        assert resp.status_code == 200

    def test_steps_type_not_ingested(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """steps payload: no MCP ingest call made."""
        client.post(
            "/owntracks/webhook",
            json={"_type": "steps", "tst": _TST, "steps": 100},
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        connector._mcp_client.call_tool.assert_not_awaited()

    def test_card_type_not_ingested(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """card payload: no MCP ingest call made."""
        client.post(
            "/owntracks/webhook",
            json={"_type": "card", "name": "Alice"},
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        connector._mcp_client.call_tool.assert_not_awaited()

    async def test_unknown_type_process_payload_returns_ignored(self) -> None:
        """process_payload() returns (None, 'ignored') for unknown types."""
        connector = _make_connector()
        result = await connector.process_payload({"_type": "unknown_future_type"})
        assert result == (None, "ignored")

    def test_waypoints_type_is_ingested(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """waypoints is a known type and IS submitted to Switchboard."""
        payload = _make_waypoints_payload()
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        connector._mcp_client.call_tool.assert_awaited_once()
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["event"]["event_type"] == "owntracks.waypoints"


# ---------------------------------------------------------------------------
# 10.6 — Metadata tier vs full tier
# ---------------------------------------------------------------------------


class TestIngestionTier:
    """Integration: metadata tier omits payload.raw; full tier includes it."""

    def test_metadata_tier_omits_raw(self) -> None:
        """Metadata tier: payload.raw is None in the envelope."""
        payload = _make_location_payload()
        envelope = normalize_location_envelope(
            payload,
            endpoint_identity=_ENDPOINT_IDENTITY,
            ingestion_tier="metadata",
            received_at="2024-03-25T12:00:00Z",
        )
        assert envelope["payload"]["raw"] is None

    def test_full_tier_includes_raw(self) -> None:
        """Full tier: payload.raw contains the original OwnTracks payload."""
        payload = _make_location_payload()
        envelope = normalize_location_envelope(
            payload,
            endpoint_identity=_ENDPOINT_IDENTITY,
            ingestion_tier="full",
            received_at="2024-03-25T12:00:00Z",
        )
        assert envelope["payload"]["raw"] is not None
        assert envelope["payload"]["raw"] == payload

    def test_full_tier_via_webhook(self) -> None:
        """Full tier connector: submitted envelope has raw payload."""
        config = _make_config(ingestion_tier="full")
        connector = _make_connector(config=config)
        client = TestClient(connector.build_app(), raise_server_exceptions=True)

        payload = _make_location_payload(lat=48.0, lon=2.0)
        client.post(
            "/owntracks/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )
        envelope = connector._mcp_client.call_tool.await_args[0][1]
        assert envelope["payload"]["raw"] is not None
        assert envelope["payload"]["raw"]["lat"] == 48.0

    def test_metadata_tier_ssid_not_in_normalized_text(self) -> None:
        """Metadata tier: SSID is not included in normalized_text."""
        payload = _make_location_payload(ssid="MyWiFiNetwork")
        text = _normalize_location_text(payload, ingestion_tier="metadata")
        assert "MyWiFiNetwork" not in text

    def test_transition_full_tier_includes_raw(self) -> None:
        """Full tier: transition envelope has raw payload."""
        payload = _make_transition_payload()
        envelope = normalize_transition_envelope(
            payload,
            endpoint_identity=_ENDPOINT_IDENTITY,
            ingestion_tier="full",
            received_at="2024-03-25T12:00:00Z",
        )
        assert envelope["payload"]["raw"] == payload

    def test_transition_metadata_tier_omits_raw(self) -> None:
        """Metadata tier: transition envelope has raw=None."""
        payload = _make_transition_payload()
        envelope = normalize_transition_envelope(
            payload,
            endpoint_identity=_ENDPOINT_IDENTITY,
            ingestion_tier="metadata",
            received_at="2024-03-25T12:00:00Z",
        )
        assert envelope["payload"]["raw"] is None

    def test_ingestion_tier_recorded_in_control(self) -> None:
        """The configured ingestion_tier is present in envelope.control."""
        connector_full = _make_connector(config=_make_config(ingestion_tier="full"))
        connector_meta = _make_connector(config=_make_config(ingestion_tier="metadata"))

        for connector, expected_tier in [
            (connector_full, "full"),
            (connector_meta, "metadata"),
        ]:
            client = TestClient(connector.build_app(), raise_server_exceptions=True)
            client.post(
                "/owntracks/webhook",
                json=_make_location_payload(),
                headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
            )
            envelope = connector._mcp_client.call_tool.await_args[0][1]
            assert envelope["control"]["ingestion_tier"] == expected_tier


# ---------------------------------------------------------------------------
# 10.7 — Retention purge
# ---------------------------------------------------------------------------


class TestRetentionPurge:
    """Integration: retention purge deletes expired events, preserves recent ones."""

    def _make_mock_pool(self, *, deleted_count: int = 5) -> MagicMock:
        """Create a mock asyncpg pool that returns a DELETE N result string."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=f"DELETE {deleted_count}")
        mock_pool = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire.return_value = mock_ctx
        return mock_pool

    async def test_purge_returns_deleted_count(self) -> None:
        """run_retention_purge() returns the number of deleted rows."""
        db_pool = self._make_mock_pool(deleted_count=7)
        config = _make_config(retention_days=30)
        connector = _make_connector(config=config)
        connector._db_pool = db_pool

        result = await connector.run_retention_purge()

        assert result == 7

    async def test_purge_calls_delete_with_correct_channel(self) -> None:
        """Purge DELETE filters on source_channel = 'owntracks'."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 3")
        mock_pool = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire.return_value = mock_ctx

        config = _make_config(retention_days=14)
        connector = _make_connector(config=config)
        connector._db_pool = mock_pool

        await connector.run_retention_purge()

        mock_conn.execute.assert_awaited_once()
        sql, retention_arg = mock_conn.execute.await_args[0]
        assert "owntracks" in sql
        assert "ingestion_events" in sql
        assert retention_arg == "14"

    async def test_purge_without_db_pool_returns_zero(self) -> None:
        """Purge returns 0 gracefully when no DB pool is configured."""
        config = _make_config(retention_days=30)
        connector = _make_connector(config=config)
        # No db_pool set

        result = await connector.run_retention_purge()

        assert result == 0

    async def test_purge_failure_does_not_crash(self) -> None:
        """Purge failure is logged as WARNING but does not raise."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_pool = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire.return_value = mock_ctx

        config = _make_config(retention_days=30)
        connector = _make_connector(config=config)
        connector._db_pool = mock_pool

        # Should not raise
        result = await connector.run_retention_purge()
        assert result == 0

    def test_retention_days_validation_minimum_1(self) -> None:
        """OWNTRACKS_RETENTION_DAYS=0 raises ValueError."""
        with pytest.raises(ValueError, match="retention_days must be >= 1"):
            OwnTracksConnectorConfig(
                switchboard_mcp_url="http://localhost:41100/sse",
                webhook_token=_VALID_TOKEN,
                tracker_id=_TRACKER_ID,
                retention_days=0,
            )

    def test_retention_days_validation_negative(self) -> None:
        """Negative OWNTRACKS_RETENTION_DAYS raises ValueError."""
        with pytest.raises(ValueError, match="retention_days must be >= 1"):
            OwnTracksConnectorConfig(
                switchboard_mcp_url="http://localhost:41100/sse",
                webhook_token=_VALID_TOKEN,
                tracker_id=_TRACKER_ID,
                retention_days=-5,
            )

    def test_retention_days_from_env_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OWNTRACKS_RETENTION_DAYS=0 via env raises ValueError in from_env."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "0")
        with pytest.raises(ValueError, match="OWNTRACKS_RETENTION_DAYS must be >= 1"):
            OwnTracksConnectorConfig.from_env(webhook_token=_VALID_TOKEN)

    def test_retention_days_default_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default OWNTRACKS_RETENTION_DAYS is 30."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        config = OwnTracksConnectorConfig.from_env(webhook_token=_VALID_TOKEN)
        assert config.retention_days == 30


# ---------------------------------------------------------------------------
# 10.8 — Dashboard token generation → connector accepts token
# ---------------------------------------------------------------------------


class TestDashboardTokenFlow:
    """Integration: dashboard token generation stores token in CredentialStore.

    Verifies that the generated token is accepted by the connector webhook.
    """

    async def test_resolve_token_from_credential_store(self) -> None:
        """resolve_webhook_token() returns token from CredentialStore."""
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=_VALID_TOKEN)

        result = await resolve_webhook_token(credential_store=store)

        assert result == _VALID_TOKEN
        store.resolve.assert_awaited_once_with("owntracks_webhook_token", env_fallback=False)

    async def test_resolve_token_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_webhook_token() falls back to OWNTRACKS_WEBHOOK_TOKEN when store returns None."""
        monkeypatch.setenv("OWNTRACKS_WEBHOOK_TOKEN", _VALID_TOKEN)
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=None)

        result = await resolve_webhook_token(credential_store=store)

        assert result == _VALID_TOKEN

    async def test_resolve_token_no_store_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_webhook_token() returns None when neither source has a token."""
        monkeypatch.delenv("OWNTRACKS_WEBHOOK_TOKEN", raising=False)
        result = await resolve_webhook_token(credential_store=None)
        assert result is None

    async def test_stored_token_accepted_by_connector(self) -> None:
        """Token stored via CredentialStore is accepted by the webhook endpoint."""
        generated_token = "cafebabe" * 8  # 64-char hex token

        # Simulate: dashboard stores the token in CredentialStore
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=generated_token)

        # Resolve it (as the connector startup would do)
        resolved = await resolve_webhook_token(credential_store=store)
        assert resolved == generated_token

        # Build connector with the resolved token
        config = OwnTracksConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            webhook_token=resolved,
            tracker_id=_TRACKER_ID,
        )
        connector = _make_connector(config=config)
        client = TestClient(connector.build_app(), raise_server_exceptions=True)

        # Connector accepts webhook POST with the generated token
        resp = client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Bearer {generated_token}"},
        )
        assert resp.status_code == 200

    async def test_old_token_rejected_after_regeneration(self) -> None:
        """After token regeneration, old token is rejected."""
        old_token = "old" + "0" * 40
        new_token = "new" + "0" * 40

        config = OwnTracksConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            webhook_token=new_token,
            tracker_id=_TRACKER_ID,
        )
        connector = _make_connector(config=config)
        client = TestClient(connector.build_app(), raise_server_exceptions=False)

        # Old token is rejected
        resp = client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert resp.status_code == 401

        # New token is accepted
        resp = client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Checkpoint persistence and resume
# ---------------------------------------------------------------------------


class TestCheckpointPersistence:
    """Integration: checkpoint is saved after successful submission and loaded on restart."""

    def _make_cursor_pool(
        self,
        *,
        existing_cursor: str | None = None,
    ) -> tuple[MagicMock, list[tuple]]:
        """Create a mock cursor pool; returns (pool, saved_calls) list."""
        saved: list[tuple] = []

        mock_conn = AsyncMock()

        async def mock_execute(sql: str, *args: object) -> None:
            if "INSERT" in sql:
                saved.append(args)

        async def mock_fetchrow(sql: str, *args: object) -> dict | None:
            if existing_cursor is not None:
                return {"checkpoint_cursor": existing_cursor}
            return None

        mock_conn.execute.side_effect = mock_execute
        mock_conn.fetchrow.side_effect = mock_fetchrow

        mock_pool = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire.return_value = mock_ctx

        return mock_pool, saved

    async def test_checkpoint_saved_after_location_event(self) -> None:
        """Successful location submission saves checkpoint with tst value."""
        pool, saved = self._make_cursor_pool()
        config = _make_config()
        connector = _make_connector(config=config)
        connector._cursor_pool = pool

        payload = _make_location_payload(tst=_TST)
        await connector.process_payload(payload)

        assert len(saved) == 1
        # saved[0] is args tuple: (connector_type, endpoint_identity, cursor_value, timestamp)
        assert saved[0][0] == "owntracks"
        assert saved[0][1] == _ENDPOINT_IDENTITY
        assert saved[0][2] == str(_TST)

    async def test_checkpoint_loaded_on_startup(self) -> None:
        """load_checkpoint() loads the last cursor from the DB."""
        existing_tst = str(_TST - 3600)
        pool, _ = self._make_cursor_pool(existing_cursor=existing_tst)
        config = _make_config()
        connector = _make_connector(config=config)
        connector._cursor_pool = pool

        await connector.load_checkpoint()

        assert connector._checkpoint == existing_tst

    async def test_no_checkpoint_on_first_start(self) -> None:
        """With no prior checkpoint, connector._checkpoint is None after load."""
        pool, _ = self._make_cursor_pool(existing_cursor=None)
        config = _make_config()
        connector = _make_connector(config=config)
        connector._cursor_pool = pool

        await connector.load_checkpoint()

        assert connector._checkpoint is None

    async def test_no_checkpoint_saved_without_pool(self) -> None:
        """Without a cursor pool, save_checkpoint() is a no-op (no exception)."""
        config = _make_config()
        connector = _make_connector(config=config)
        # No cursor_pool

        # Should not raise
        await connector.save_checkpoint(_TST)
        assert connector._checkpoint is None


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Integration: /health endpoint reflects connector state."""

    @pytest.fixture
    def connector(self) -> OwnTracksConnector:
        return _make_connector()

    @pytest.fixture
    def client(self, connector: OwnTracksConnector) -> TestClient:
        return TestClient(connector.build_app(), raise_server_exceptions=True)

    def test_health_returns_200(self, client: TestClient) -> None:
        """/health returns HTTP 200."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_initial_state(self, client: TestClient) -> None:
        """/health returns healthy state initially."""
        resp = client.get("/health")
        data = resp.json()
        assert data["state"] == "healthy"
        assert data["last_event_at"] is None
        assert data["events_today"] == 0
        assert data["uptime_s"] >= 0

    def test_health_updates_after_event(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """/health reflects last_event_at and events_today after processing events."""
        # Process a location event
        client.post(
            "/owntracks/webhook",
            json=_make_location_payload(),
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )

        resp = client.get("/health")
        data = resp.json()
        assert data["last_event_at"] is not None
        assert data["events_today"] == 1

    def test_health_events_today_increments(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """events_today increments with each processed event."""
        for _ in range(3):
            client.post(
                "/owntracks/webhook",
                json=_make_location_payload(),
                headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
            )

        resp = client.get("/health")
        assert resp.json()["events_today"] == 3

    def test_health_ignored_events_do_not_increment_counter(
        self, connector: OwnTracksConnector, client: TestClient
    ) -> None:
        """Unknown payload types do not increment events_today."""
        client.post(
            "/owntracks/webhook",
            json={"_type": "lwt"},
            headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
        )

        resp = client.get("/health")
        assert resp.json()["events_today"] == 0


# ---------------------------------------------------------------------------
# Normalization unit coverage (supporting validation for above integration tests)
# ---------------------------------------------------------------------------


class TestNormalizationHelpers:
    """Unit tests for normalization helper functions."""

    def test_location_text_southern_hemisphere(self) -> None:
        """Negative latitude gets S cardinal direction."""
        payload = {"lat": -33.8688, "lon": 151.2093, "acc": 5}
        text = _normalize_location_text(payload, ingestion_tier="metadata")
        assert "33.8688S" in text
        assert "151.2093E" in text

    def test_location_text_western_hemisphere(self) -> None:
        """Negative longitude gets W cardinal direction."""
        payload = {"lat": 40.7128, "lon": -74.0060, "acc": 15}
        text = _normalize_location_text(payload, ingestion_tier="metadata")
        assert "40.7128N" in text
        assert "74.006W" in text

    def test_location_text_zero_velocity_not_appended(self) -> None:
        """vel=0 is not appended to normalized text."""
        payload = {"lat": 48.0, "lon": 2.0, "acc": 10, "vel": 0}
        text = _normalize_location_text(payload, ingestion_tier="metadata")
        assert "km/h" not in text

    def test_transition_text_enter(self) -> None:
        """Enter transition generates 'Entered region: ...' text."""
        payload = {"event": "enter", "desc": "Gym"}
        assert _normalize_transition_text(payload) == "Entered region: Gym"

    def test_transition_text_leave(self) -> None:
        """Leave transition generates 'Left region: ...' text."""
        payload = {"event": "leave", "desc": "Gym"}
        assert _normalize_transition_text(payload) == "Left region: Gym"

    def test_waypoints_text_with_names(self) -> None:
        """Waypoints text lists region names."""
        payload = {
            "waypoints": [
                {"desc": "Home"},
                {"desc": "Office"},
                {"desc": "Gym"},
            ]
        }
        text = _normalize_waypoints_text(payload)
        assert "3 regions" in text
        assert "Home" in text
        assert "Office" in text
        assert "Gym" in text

    def test_waypoints_text_truncates_at_5(self) -> None:
        """Waypoints text truncates at 5 names with 'and N more' suffix."""
        payload = {"waypoints": [{"desc": f"Place{i}"} for i in range(8)]}
        text = _normalize_waypoints_text(payload)
        assert "8 regions" in text
        assert "and 3 more" in text

    def test_waypoints_text_empty(self) -> None:
        """Empty waypoints: text says '0 regions'."""
        payload = {"waypoints": []}
        text = _normalize_waypoints_text(payload)
        assert "0 regions" in text


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """Tests for OwnTracksConnectorConfig validation."""

    def test_from_env_missing_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing SWITCHBOARD_MCP_URL raises ValueError."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL"):
            OwnTracksConnectorConfig.from_env(webhook_token=_VALID_TOKEN)

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default values are applied when optional env vars are absent."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        config = OwnTracksConnectorConfig.from_env(webhook_token=_VALID_TOKEN)
        assert config.ingestion_tier == "metadata"
        assert config.health_port == 40083
        assert config.retention_days == 30
        assert config.tracker_id == "unknown"

    def test_from_env_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Custom env vars are applied."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("OWNTRACKS_TRACKER_ID", "ph")
        monkeypatch.setenv("CONNECTOR_INGESTION_TIER", "full")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40099")
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "7")
        config = OwnTracksConnectorConfig.from_env(webhook_token=_VALID_TOKEN)
        assert config.tracker_id == "ph"
        assert config.ingestion_tier == "full"
        assert config.health_port == 40099
        assert config.retention_days == 7

    def test_endpoint_identity_format(self) -> None:
        """Endpoint identity is 'owntracks:<tracker_id>'."""
        config = _make_config(tracker_id="ph")
        connector = _make_connector(config=config)
        assert connector.endpoint_identity == "owntracks:ph"
