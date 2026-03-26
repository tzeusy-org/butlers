"""Tests for the OwnTracks dashboard API router.

Covers all endpoints with mocked CredentialStore and mocked switchboard DB:
  POST /api/connectors/owntracks/token/generate
  GET  /api/connectors/owntracks/status
  GET  /api/connectors/owntracks/config

All DB interactions are mocked via dependency_overrides and patch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.models.owntracks import OwnTracksConnectionState
from butlers.api.routers.owntracks import (
    _build_webhook_url,
    _derive_connection_state,
    _generate_token,
    _get_db_manager,
    _mask_token,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# CredentialStore mock helpers
# ---------------------------------------------------------------------------


def _make_cred_store(*, token: str | None = None) -> MagicMock:
    """Build a mock CredentialStore that returns specified values."""
    store = MagicMock()

    creds: dict[str, str | None] = {
        "owntracks_webhook_token": token,
    }

    async def _resolve(key: str, **_kwargs) -> str | None:
        return creds.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock(return_value=None)
    store.delete = AsyncMock(return_value=True)
    return store


def _make_db_manager() -> MagicMock:
    """Build a mock DatabaseManager."""
    pool = MagicMock()
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    return db_manager


def _build_app(cred_store: MagicMock | None = None) -> object:
    """Return an app instance with mocked DB dependency wired in."""
    _app = create_app(api_key="")

    if cred_store is not None:
        db_manager = _make_db_manager()
        _app.dependency_overrides[_get_db_manager] = lambda: db_manager

    return _app


# ---------------------------------------------------------------------------
# Unit tests: token helpers
# ---------------------------------------------------------------------------


class TestTokenHelpers:
    def test_generate_token_length(self):
        """Generated token should be 64 hex characters (32 bytes)."""
        token = _generate_token()
        assert len(token) == 64

    def test_generate_token_hex(self):
        """Generated token must be a valid hex string."""
        token = _generate_token()
        assert all(c in "0123456789abcdef" for c in token)

    def test_generate_token_randomness(self):
        """Two consecutive tokens must not be equal."""
        assert _generate_token() != _generate_token()

    def test_mask_token_standard(self):
        """Standard 64-char token should be masked to 'xxxx...xxxx'."""
        token = "ab12" + "00" * 28 + "ef90"
        result = _mask_token(token)
        assert result is not None
        assert result.startswith("ab12")
        assert result.endswith("ef90")
        assert "..." in result

    def test_mask_token_none(self):
        """None input returns None."""
        assert _mask_token(None) is None

    def test_mask_token_short(self):
        """Token shorter than 8 characters returns None."""
        assert _mask_token("ab12") is None

    def test_mask_token_exactly_eight(self):
        """Token of exactly 8 characters is masked."""
        result = _mask_token("abcd1234")
        assert result is not None
        assert "abcd" in result
        assert "1234" in result


# ---------------------------------------------------------------------------
# Unit tests: webhook URL helper
# ---------------------------------------------------------------------------


class TestWebhookURL:
    def test_localhost_uses_http(self):
        url = _build_webhook_url("localhost", 40083)
        assert url.startswith("http://")
        assert "localhost:40083" in url
        assert url.endswith("/owntracks/webhook")

    def test_127_0_0_1_uses_http(self):
        url = _build_webhook_url("127.0.0.1", 40083)
        assert url.startswith("http://")

    def test_external_host_uses_https(self):
        url = _build_webhook_url("myhost.example.com", 40083)
        assert url.startswith("https://")
        assert "myhost.example.com:40083" in url
        assert url.endswith("/owntracks/webhook")

    def test_tailnet_host_uses_https(self):
        url = _build_webhook_url("my-device.ts.net", 40083)
        assert url.startswith("https://")

    def test_ipv6_localhost_uses_http_and_brackets(self):
        """IPv6 loopback ::1 should use http and be bracketed in the URL."""
        url = _build_webhook_url("::1", 40083)
        assert url.startswith("http://")
        assert "[::1]" in url
        assert url.endswith("/owntracks/webhook")

    def test_https_port_443_omitted(self):
        """Default HTTPS port 443 should not appear in the URL."""
        url = _build_webhook_url("my-device.ts.net", 443)
        assert url == "https://my-device.ts.net/owntracks/webhook"

    def test_http_port_80_omitted(self):
        """Default HTTP port 80 should not appear in the URL."""
        url = _build_webhook_url("localhost", 80)
        assert url == "http://localhost/owntracks/webhook"


# ---------------------------------------------------------------------------
# Unit tests: connection state derivation
# ---------------------------------------------------------------------------


class TestDeriveConnectionState:
    def test_no_token_returns_not_configured(self):
        state, running, last_event, today, uptime = _derive_connection_state(
            token_configured=False,
            heartbeat_row=None,
        )
        assert state == OwnTracksConnectionState.not_configured
        assert running is False
        assert last_event is None
        assert today == 0

    def test_token_configured_no_heartbeat(self):
        """Token configured but no heartbeat → offline (connector not started yet)."""
        state, running, last_event, today, uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=None,
        )
        assert state == OwnTracksConnectionState.offline
        assert running is False

    def test_recent_heartbeat_no_events(self):
        """Recent heartbeat but no events → no_events state."""
        now = datetime.now(UTC)
        row = {
            "state": "running",
            "last_heartbeat_at": now - timedelta(seconds=60),
            "uptime_s": 3600,
            "counter_messages_ingested": 0,
            "today_messages_ingested": 0,
        }
        state, running, last_event, today, uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.no_events
        assert running is True
        assert today == 0
        assert last_event is None

    def test_recent_heartbeat_with_events(self):
        """Recent heartbeat and events received → connected state."""
        now = datetime.now(UTC)
        row = {
            "state": "running",
            "last_heartbeat_at": now - timedelta(seconds=60),
            "uptime_s": 7200,
            "counter_messages_ingested": 42,
            "today_messages_ingested": 5,
        }
        state, running, last_event, today, uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.connected
        assert running is True
        assert today == 5
        assert last_event is not None
        assert uptime == 7200.0

    def test_stale_heartbeat(self):
        """Old heartbeat (> 5 minutes) → stale state."""
        now = datetime.now(UTC)
        row = {
            "state": "running",
            "last_heartbeat_at": now - timedelta(minutes=10),
            "uptime_s": 7200,
            "counter_messages_ingested": 5,
            "today_messages_ingested": 1,
        }
        state, running, last_event, today, uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.stale
        assert running is False

    def test_offline_no_heartbeat_timestamp(self):
        """Heartbeat row exists but no timestamp → offline state."""
        row = {
            "state": "unknown",
            "last_heartbeat_at": None,
            "uptime_s": None,
            "counter_messages_ingested": 0,
            "today_messages_ingested": 0,
        }
        state, running, last_event, today, uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.offline
        assert running is False


# ---------------------------------------------------------------------------
# Integration-style tests: POST /api/connectors/owntracks/token/generate
# ---------------------------------------------------------------------------


class TestOwnTracksTokenGenerate:
    @pytest.fixture
    def cred_store(self):
        return _make_cred_store()

    @pytest.fixture
    async def client(self, cred_store):
        app = _build_app(cred_store)
        with patch(
            "butlers.api.routers.owntracks._make_credential_store",
            return_value=cred_store,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                yield c, cred_store

    async def test_generate_returns_64_hex_token(self, client):
        c, store = client
        response = await c.post("/api/connectors/owntracks/token/generate")
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert len(data["token"]) == 64
        assert all(ch in "0123456789abcdef" for ch in data["token"])

    async def test_generate_stores_token_in_cred_store(self, client):
        c, store = client
        response = await c.post("/api/connectors/owntracks/token/generate")
        assert response.status_code == 200
        # Token should have been stored
        store.store.assert_called_once()
        call_args = store.store.call_args
        assert call_args[0][0] == "owntracks_webhook_token"
        assert len(call_args[0][1]) == 64  # The token value

    async def test_generate_includes_message(self, client):
        c, store = client
        response = await c.post("/api/connectors/owntracks/token/generate")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert len(data["message"]) > 0

    async def test_generate_cred_store_unavailable_returns_503(self):
        app = create_app(api_key="")
        app.dependency_overrides[_get_db_manager] = lambda: None
        with patch(
            "butlers.api.routers.owntracks._make_credential_store",
            return_value=None,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.post("/api/connectors/owntracks/token/generate")
        assert response.status_code == 503
        assert "credential" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Integration-style tests: GET /api/connectors/owntracks/status
# ---------------------------------------------------------------------------


class TestOwnTracksStatus:
    async def test_status_no_token_returns_not_configured(self):
        """No token → not_configured state regardless of connector."""
        cred_store = _make_cred_store(token=None)
        app = _build_app(cred_store)
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=cred_store,
            ),
            patch(
                "butlers.api.routers.owntracks._make_switchboard_pool",
                return_value=None,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "not_configured"
        assert data["token_configured"] is False
        assert data["connector_running"] is False

    async def test_status_token_configured_no_connector(self):
        """Token configured but no connector running → offline."""
        cred_store = _make_cred_store(token="a" * 64)
        app = _build_app(cred_store)
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=cred_store,
            ),
            patch(
                "butlers.api.routers.owntracks._make_switchboard_pool",
                return_value=None,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["token_configured"] is True
        assert data["connector_running"] is False

    async def test_status_connected(self):
        """Recent heartbeat + events → connected state."""
        cred_store = _make_cred_store(token="a" * 64)
        now = datetime.now(UTC)
        heartbeat_row = {
            "state": "running",
            "last_heartbeat_at": now - timedelta(seconds=60),
            "uptime_s": 3600,
            "counter_messages_ingested": 10,
            "today_messages_ingested": 3,
        }

        pool_mock = AsyncMock()
        pool_mock.fetchrow = AsyncMock(return_value=heartbeat_row)

        app = _build_app(cred_store)
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=cred_store,
            ),
            patch(
                "butlers.api.routers.owntracks._make_switchboard_pool",
                return_value=pool_mock,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "connected"
        assert data["token_configured"] is True
        assert data["connector_running"] is True
        assert data["events_today"] == 3
        assert data["last_event_at"] is not None
        assert data["uptime_seconds"] == 3600.0

    async def test_status_no_events_yet(self):
        """Recent heartbeat but no events → no_events state."""
        cred_store = _make_cred_store(token="b" * 64)
        now = datetime.now(UTC)
        heartbeat_row = {
            "state": "running",
            "last_heartbeat_at": now - timedelta(seconds=30),
            "uptime_s": 120,
            "counter_messages_ingested": 0,
            "today_messages_ingested": 0,
        }

        pool_mock = AsyncMock()
        pool_mock.fetchrow = AsyncMock(return_value=heartbeat_row)

        app = _build_app(cred_store)
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=cred_store,
            ),
            patch(
                "butlers.api.routers.owntracks._make_switchboard_pool",
                return_value=pool_mock,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "no_events"
        assert data["connector_running"] is True
        assert data["events_today"] == 0
        assert data["last_event_at"] is None

    async def test_status_stale_connector(self):
        """Old heartbeat → stale state."""
        cred_store = _make_cred_store(token="c" * 64)
        now = datetime.now(UTC)
        heartbeat_row = {
            "state": "running",
            "last_heartbeat_at": now - timedelta(minutes=15),
            "uptime_s": 900,
            "counter_messages_ingested": 5,
            "today_messages_ingested": 1,
        }

        pool_mock = AsyncMock()
        pool_mock.fetchrow = AsyncMock(return_value=heartbeat_row)

        app = _build_app(cred_store)
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=cred_store,
            ),
            patch(
                "butlers.api.routers.owntracks._make_switchboard_pool",
                return_value=pool_mock,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "stale"
        assert data["connector_running"] is False

    async def test_status_db_unavailable_degrades_gracefully(self):
        """DB unavailable → graceful degradation, returns 200 not 500."""
        app = create_app(api_key="")
        app.dependency_overrides[_get_db_manager] = lambda: None
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=None,
            ),
            patch(
                "butlers.api.routers.owntracks._make_switchboard_pool",
                return_value=None,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "not_configured"
        assert data["token_configured"] is False


# ---------------------------------------------------------------------------
# Integration-style tests: GET /api/connectors/owntracks/config
# ---------------------------------------------------------------------------


class TestOwnTracksConfig:
    async def test_config_webhook_url_default(self):
        """Config returns webhook URL with default host/port when no env vars set."""
        app = create_app(api_key="")
        app.dependency_overrides[_get_db_manager] = lambda: None
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=None,
            ),
            patch.dict("os.environ", {}, clear=False),
        ):
            # Remove owntracks host/port overrides if present
            import os

            os.environ.pop("OWNTRACKS_CONNECTOR_HOST", None)
            os.environ.pop("OWNTRACKS_CONNECTOR_PORT", None)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/config")

        assert response.status_code == 200
        data = response.json()
        assert "webhook_url" in data
        assert "/owntracks/webhook" in data["webhook_url"]
        assert "40083" in data["webhook_url"]

    async def test_config_webhook_url_custom_host(self):
        """Config computes webhook URL from OWNTRACKS_CONNECTOR_HOST env var."""
        cred_store = _make_cred_store(token=None)
        app = _build_app(cred_store)
        with (
            patch(
                "butlers.api.routers.owntracks._make_credential_store",
                return_value=cred_store,
            ),
            patch.dict(
                "os.environ",
                {
                    "OWNTRACKS_CONNECTOR_HOST": "my-tailnet-host.ts.net",
                    "OWNTRACKS_CONNECTOR_PORT": "40083",
                },
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/config")

        assert response.status_code == 200
        data = response.json()
        assert "my-tailnet-host.ts.net" in data["webhook_url"]
        assert data["webhook_url"].startswith("https://")

    async def test_config_token_masked_when_configured(self):
        """Config returns masked token when one is configured."""
        token = "a1b2c3d4" + "00" * 24 + "e5f6e5f6"  # 64 valid hex chars
        cred_store = _make_cred_store(token=token)
        app = _build_app(cred_store)
        with patch(
            "butlers.api.routers.owntracks._make_credential_store",
            return_value=cred_store,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/config")

        assert response.status_code == 200
        data = response.json()
        assert data["token_masked"] is not None
        # Raw token must NOT appear
        assert token not in data["token_masked"]
        assert "..." in data["token_masked"]

    async def test_config_no_token_masked_is_null(self):
        """Config returns token_masked=null when no token configured."""
        cred_store = _make_cred_store(token=None)
        app = _build_app(cred_store)
        with patch(
            "butlers.api.routers.owntracks._make_credential_store",
            return_value=cred_store,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/config")

        assert response.status_code == 200
        data = response.json()
        assert data["token_masked"] is None

    async def test_config_setup_instructions_present(self):
        """Config response includes iOS and Android setup instructions."""
        app = create_app(api_key="")
        app.dependency_overrides[_get_db_manager] = lambda: None
        with patch(
            "butlers.api.routers.owntracks._make_credential_store",
            return_value=None,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                response = await c.get("/api/connectors/owntracks/config")

        assert response.status_code == 200
        data = response.json()
        instructions = data["setup_instructions"]
        assert len(instructions["steps_ios"]) > 0
        assert len(instructions["steps_android"]) > 0
        assert instructions["mode"] == "HTTP"
        assert "troubleshooting_hint" in instructions
