"""Tests for the WhatsApp dashboard API router.

All bridge communication is mocked via dependency_overrides on _get_bridge_socket_path
and by patching the bridge HTTP helpers. Tests cover all endpoints with bridge
up/down scenarios.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.whatsapp import _get_bridge_socket_path

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def whatsapp_app():
    """Fresh app with bridge socket path overridden to a dummy path."""
    _app = create_app(api_key="")
    _app.dependency_overrides[_get_bridge_socket_path] = lambda: "/tmp/test-bridge.sock"
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
async def client(whatsapp_app):
    """AsyncClient configured for ASGI tests."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=whatsapp_app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Helper — bridge response mocker
# ---------------------------------------------------------------------------


def mock_bridge_get(return_value: dict | None):
    """Patch _bridge_get to return a fixed value."""
    return patch(
        "butlers.api.routers.whatsapp._bridge_get",
        new=AsyncMock(return_value=return_value),
    )


def mock_bridge_post(return_value: dict | None):
    """Patch _bridge_post to return a fixed value."""
    return patch(
        "butlers.api.routers.whatsapp._bridge_post",
        new=AsyncMock(return_value=return_value),
    )


# ---------------------------------------------------------------------------
# GET /api/connectors/whatsapp/status
# ---------------------------------------------------------------------------


class TestWhatsAppStatus:
    async def test_status_connected(self, client):
        """Status endpoint returns connected when bridge reports connected."""
        bridge_data = {
            "state": "connected",
            "phone": "+12345677890",
            "paired_at": "2026-01-15T10:00:00+00:00",
            "last_event_at": "2026-03-25T08:00:00+00:00",
        }
        with mock_bridge_get(bridge_data):
            response = await client.get("/api/connectors/whatsapp/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "connected"
        assert data["bridge_running"] is True
        # Phone should be masked
        assert data["phone"] is not None
        assert "7890" in data["phone"]
        assert data["paired_at"] is not None
        assert data["last_sync_at"] is not None

    async def test_status_bridge_down(self, client):
        """Status endpoint returns not_configured when bridge is unreachable."""
        with mock_bridge_get(None):
            response = await client.get("/api/connectors/whatsapp/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "not_configured"
        assert data["bridge_running"] is False
        assert data["phone"] is None

    async def test_status_pair_required(self, client):
        """Status maps pair_required bridge state correctly."""
        bridge_data = {"state": "pair_required", "phone": None}
        with mock_bridge_get(bridge_data):
            response = await client.get("/api/connectors/whatsapp/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "pair_required"
        assert data["bridge_running"] is True

    async def test_status_connecting_maps_to_disconnected(self, client):
        """Bridge 'connecting' state maps to 'disconnected' for the dashboard."""
        bridge_data = {"state": "connecting"}
        with mock_bridge_get(bridge_data):
            response = await client.get("/api/connectors/whatsapp/status")

        assert response.status_code == 200
        assert response.json()["state"] == "disconnected"

    async def test_status_unknown_state_maps_to_not_configured(self, client):
        """Unknown bridge state falls back to not_configured."""
        bridge_data = {"state": "some_unknown_state"}
        with mock_bridge_get(bridge_data):
            response = await client.get("/api/connectors/whatsapp/status")

        assert response.status_code == 200
        assert response.json()["state"] == "not_configured"


# ---------------------------------------------------------------------------
# POST /api/connectors/whatsapp/pair/start
# ---------------------------------------------------------------------------


class TestWhatsAppPairStart:
    async def test_pair_start_success(self, client):
        """pair/start returns QR data URI and expiry when bridge is up."""
        expires = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
        bridge_data = {
            "qr_data_uri": "data:image/png;base64,abc123==",
            "expires_at": expires,
        }
        with mock_bridge_post(bridge_data):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 200
        data = response.json()
        assert data["qr_data_uri"] == "data:image/png;base64,abc123=="
        assert data["expires_at"] is not None

    async def test_pair_start_bridge_down_returns_503(self, client):
        """pair/start raises 503 when bridge is unreachable."""
        with mock_bridge_post(None):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 503
        assert "bridge" in response.json()["detail"].lower()

    async def test_pair_start_empty_qr_returns_502(self, client):
        """pair/start raises 502 when bridge returns empty QR."""
        bridge_data = {"qr_data_uri": "", "expires_at": None}
        with mock_bridge_post(bridge_data):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 502

    async def test_pair_start_defaults_expiry_when_not_provided(self, client):
        """pair/start sets a sensible default expiry when bridge omits it."""
        bridge_data = {"qr_data_uri": "data:image/png;base64,xyz=="}
        with mock_bridge_post(bridge_data):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 200
        data = response.json()
        # Expiry should be set and in the future
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert expires_at > datetime.now(UTC)


# ---------------------------------------------------------------------------
# GET /api/connectors/whatsapp/pair/poll
# ---------------------------------------------------------------------------


class TestWhatsAppPairPoll:
    async def test_poll_waiting(self, client):
        """poll returns 'waiting' when pairing is in progress."""
        with mock_bridge_get({"status": "waiting"}):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        assert response.json()["status"] == "waiting"
        assert response.json()["phone"] is None

    async def test_poll_paired(self, client):
        """poll returns 'paired' with masked phone number on success."""
        with mock_bridge_get({"status": "paired", "phone": "+12345677890"}):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "paired"
        # Phone must be masked — raw number must not be returned
        assert data["phone"] is not None
        assert data["phone"] != "+12345677890"
        assert "7890" in data["phone"]

    async def test_poll_expired(self, client):
        """poll returns 'expired' when QR code expired."""
        with mock_bridge_get({"status": "expired"}):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        assert response.json()["status"] == "expired"

    async def test_poll_bridge_down_returns_waiting(self, client):
        """poll returns 'waiting' when bridge is unreachable (graceful degradation)."""
        with mock_bridge_get(None):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        assert response.json()["status"] == "waiting"

    async def test_poll_unknown_status_defaults_to_waiting(self, client):
        """poll maps unknown status strings to 'waiting'."""
        with mock_bridge_get({"status": "some_unknown"}):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        assert response.json()["status"] == "waiting"


# ---------------------------------------------------------------------------
# POST /api/connectors/whatsapp/disconnect
# ---------------------------------------------------------------------------


class TestWhatsAppDisconnect:
    async def test_disconnect_success(self, client):
        """disconnect returns success when bridge confirms disconnection."""
        with mock_bridge_post({"success": True, "message": "Disconnected"}):
            response = await client.post("/api/connectors/whatsapp/disconnect")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    async def test_disconnect_bridge_down_returns_success(self, client):
        """disconnect returns success even when bridge is unreachable (idempotent)."""
        with mock_bridge_post(None):
            response = await client.post("/api/connectors/whatsapp/disconnect")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "not running" in data["message"]


# ---------------------------------------------------------------------------
# GET /api/connectors/whatsapp/health
# ---------------------------------------------------------------------------


class TestWhatsAppHealth:
    async def test_health_connected(self, client):
        """health returns connected state when bridge is up."""
        bridge_data = {
            "state": "connected",
            "uptime_seconds": 3600.0,
            "last_event_at": "2026-03-25T08:00:00+00:00",
        }
        with mock_bridge_get(bridge_data):
            response = await client.get("/api/connectors/whatsapp/health")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "connected"
        assert data["bridge_running"] is True
        assert data["uptime_seconds"] == 3600.0
        assert data["last_event_at"] is not None

    async def test_health_bridge_down(self, client):
        """health returns not_configured with bridge_running=False when bridge is down."""
        with mock_bridge_get(None):
            response = await client.get("/api/connectors/whatsapp/health")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "not_configured"
        assert data["bridge_running"] is False

    async def test_health_pair_required(self, client):
        """health surfaces pair_required for the re-pair badge trigger."""
        bridge_data = {"state": "pair_required"}
        with mock_bridge_get(bridge_data):
            response = await client.get("/api/connectors/whatsapp/health")

        assert response.status_code == 200
        assert response.json()["state"] == "pair_required"
        assert response.json()["bridge_running"] is True


# ---------------------------------------------------------------------------
# Phone masking unit tests
# ---------------------------------------------------------------------------


class TestPhoneMasking:
    def test_mask_standard_us_number(self):
        from butlers.api.routers.whatsapp import _mask_phone

        result = _mask_phone("+12345677890")
        assert result is not None
        assert "7890" in result
        assert "+1" in result

    def test_mask_none_returns_none(self):
        from butlers.api.routers.whatsapp import _mask_phone

        assert _mask_phone(None) is None

    def test_mask_empty_string_returns_none(self):
        from butlers.api.routers.whatsapp import _mask_phone

        assert _mask_phone("") is None

    def test_mask_short_number_returns_unchanged(self):
        from butlers.api.routers.whatsapp import _mask_phone

        # Too short to mask meaningfully — returned unchanged
        assert _mask_phone("+12") == "+12"
