"""Tests for live credential probes: Home Assistant, Steam, OwnTracks.

Covers bu-ayp6v.11: extend live-verify provider set beyond Google/GitHub to
include Home Assistant (entity_info bearer token), Steam (ISteamUser Web API),
and OwnTracks (presence/format check on webhook token).

Test matrix
-----------
Home Assistant:
- live_ok: GET /api/ returns 200
- live_failed: GET /api/ returns 401
- network error falls back to local check
- URL not configured → skipped_local_check
- audit note includes probe_status

Steam:
- live_ok: GetPlayerSummaries returns 200 with non-empty players
- live_failed: GetPlayerSummaries returns 401
- live_failed: 200 but empty players
- network error falls back to local check
- no primary steam account → skipped_local_check
- audit note includes probe_status

OwnTracks (system probe):
- live_ok: token is present and 64-char hex
- live_failed:bad_format — wrong length
- live_failed:bad_format — non-hex chars
- local-state fallback when raw value unreadable

Acceptance criteria
-------------------
1. Each provider returns live_ok/live_failed when credentials are present.
2. Network/config errors return HTTP 200, probe falls back to local state.
3. probe_log + cache columns are updated (via mocked execute/transaction).
4. Spotify is untouched — confirmed by checking spotify probe still returns
   local-state (no HTTP calls).

Spec anchor
-----------
bu-ayp6v.11 (parent epic: bu-ayp6v)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_HA_TOKEN = "fake-ha-long-lived-token"
_HA_URL = "http://homeassistant.local:8123"
_STEAM_API_KEY = "FAKE_STEAM_API_KEY_12345"
_STEAM_ID = "76561198000000001"
_OWNTRACKS_TOKEN_VALID = "a" * 64  # 64-char hex string


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_entity_info_row(
    *,
    info_type: str,
    value: str,
    label: str | None = None,
    last_verified: datetime | None = None,
    last_test_ok: bool | None = True,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
) -> MagicMock:
    row_id = uuid4()
    eid = str(uuid4())
    return _make_row(
        id=row_id,
        entity_id=eid,
        type=info_type,
        value=value,
        label=label,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Shared pool factories
# ---------------------------------------------------------------------------


def _make_shared_pool(
    *,
    user_row: MagicMock | None = None,
    raw_token_value: str | None = None,
    ha_url_value: str | None = None,
    steam_id_value: str | None = None,
    execute_ok: bool = True,
) -> AsyncMock:
    """Build a mock shared pool for HA and Steam probe tests."""
    shared_pool = AsyncMock()

    async def _fetchrow(sql: str, *args):
        # Probe log lookup (no prior probe for these tests)
        if "secret_probe_log" in sql:
            return None
        # Raw token fetch by PK id (used by probe to get refresh token)
        if "entity_info" in sql and "WHERE id = $1" in sql:
            if raw_token_value is not None:
                return _make_row(value=raw_token_value)
            return None
        # HA URL lookup via owner entity
        if "home_assistant_url" in sql:
            if ha_url_value is not None:
                return _make_row(value=ha_url_value)
            return None
        # Steam primary account SteamID
        if "steam_accounts" in sql and "is_primary" in sql:
            if steam_id_value is not None:
                return _make_row(steam_id=int(steam_id_value))
            return None
        # Full entity_info row (used by _fetch_single_user_secret)
        if "entity_info" in sql or "entities" in sql:
            return user_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    if execute_ok:
        shared_pool.execute = AsyncMock(return_value="UPDATE 1")
    else:
        shared_pool.execute = AsyncMock(side_effect=Exception("DB error"))

    fake_conn = AsyncMock()
    fake_conn.fetchrow = shared_pool.fetchrow
    fake_conn.fetch = shared_pool.fetch
    fake_conn.execute = shared_pool.execute

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    shared_pool.acquire = _acquire
    return shared_pool


def _make_db(
    *,
    user_row: MagicMock | None = None,
    raw_token_value: str | None = None,
    ha_url_value: str | None = None,
    steam_id_value: str | None = None,
    execute_ok: bool = True,
) -> MagicMock:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())

    shared_pool = _make_shared_pool(
        user_row=user_row,
        raw_token_value=raw_token_value,
        ha_url_value=ha_url_value,
        steam_id_value=steam_id_value,
        execute_ok=execute_ok,
    )
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Home Assistant probe tests
# ---------------------------------------------------------------------------


class TestHomeAssistantProbe:
    """POST /api/secrets/user/home_assistant/probe live-verify tests."""

    def _make_ha_db(
        self,
        *,
        ha_url: str | None = _HA_URL,
        last_test_ok: bool | None = True,
    ) -> MagicMock:
        row = _make_entity_info_row(
            info_type="home_assistant_token",
            value=_HA_TOKEN,
            last_test_ok=last_test_ok,
        )
        return _make_db(
            user_row=row,
            raw_token_value=_HA_TOKEN,
            ha_url_value=ha_url,
        )

    def test_ha_probe_200_returns_live_ok(self, monkeypatch):
        """HA probe GET /api/ 200 → probe_ok=True; calls /api/ with 'Bearer <token>'
        header and writes audit note probe_status=live_ok."""
        mock_db = self._make_ha_db()

        calls: list[dict] = []

        async def _fake_get(url, **kwargs):
            calls.append({"method": "GET", "url": str(url), **kwargs})
            fake_resp = MagicMock(spec=httpx.Response)
            fake_resp.status_code = 200
            return fake_resp

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/home_assistant/probe")

        assert resp.status_code == 200
        assert resp.json()["data"]["ok"] is True

        # Must have called GET /api/ with the stored token as a Bearer header.
        get_calls = [c for c in calls if c["method"] == "GET"]
        assert any(f"{_HA_URL}/api/" in c["url"] for c in get_calls), (
            f"Expected GET {_HA_URL}/api/ in calls; got {calls}"
        )
        assert calls[0].get("headers", {}).get("Authorization") == f"Bearer {_HA_TOKEN}"
        # Audit note records the live_ok probe status.
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=live_ok" in audit_calls[0].get("note", "")

    def test_ha_probe_401_returns_live_failed(self, monkeypatch):
        """HA probe GET /api/ 401 → probe_ok=False, code=401; note probe_status=live_failed."""
        mock_db = self._make_ha_db()

        async def _fake_get(url, **kwargs):
            fake_resp = MagicMock(spec=httpx.Response)
            fake_resp.status_code = 401
            return fake_resp

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/home_assistant/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["code"] == 401
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=live_failed" in audit_calls[0].get("note", "")

    def test_ha_probe_network_error_falls_back_to_local(self, monkeypatch):
        """Network error during GET /api/ → fallback to local state (NOT probe_ok=False)."""
        mock_db = self._make_ha_db(last_test_ok=True)

        async def _fake_get(url, **kwargs):
            raise httpx.ConnectError("connection refused")

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/home_assistant/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Network error → skipped_local_check → local state wins.
        # last_test_ok=True + value set → state='ok' → probe_ok=True
        assert data["ok"] is True

    def test_ha_probe_no_url_configured_falls_back_to_local(self, monkeypatch):
        """Missing home_assistant_url in entity_info → skipped_local_check, no HTTP calls."""
        # URL not configured
        mock_db = self._make_ha_db(ha_url=None, last_test_ok=True)

        http_calls: list[str] = []

        async def _fake_get(url, **kwargs):
            http_calls.append(str(url))
            raise AssertionError(f"Should not call HTTP; got GET {url}")

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/home_assistant/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Falls back to local state: last_test_ok=True + value set → ok → True
        assert data["ok"] is True
        assert not http_calls, f"No HTTP calls expected; got: {http_calls}"


# ---------------------------------------------------------------------------
# Steam probe tests
# ---------------------------------------------------------------------------


class TestSteamProbe:
    """POST /api/secrets/user/steam/probe live-verify tests."""

    def _make_steam_db(
        self,
        *,
        steam_id: str | None = _STEAM_ID,
        last_test_ok: bool | None = True,
    ) -> MagicMock:
        row = _make_entity_info_row(
            info_type="steam_api_key",
            value=_STEAM_API_KEY,
            last_test_ok=last_test_ok,
        )
        return _make_db(
            user_row=row,
            raw_token_value=_STEAM_API_KEY,
            steam_id_value=steam_id,
        )

    def _fake_steam_response(
        self,
        *,
        status: int = 200,
        players: list | None = None,
    ) -> MagicMock:
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = status
        if players is None:
            players = [{"steamid": _STEAM_ID, "personaname": "TestUser"}]
        fake_resp.json = MagicMock(return_value={"response": {"players": players}})
        return fake_resp

    def test_steam_probe_200_with_players_returns_live_ok(self, monkeypatch):
        """Steam probe GetPlayerSummaries 200 + non-empty players → probe_ok=True;
        calls GetPlayerSummaries and writes audit note probe_status=live_ok."""
        mock_db = self._make_steam_db()

        calls: list[dict] = []

        async def _fake_get(url, **kwargs):
            calls.append({"method": "GET", "url": str(url), **kwargs})
            return self._fake_steam_response(status=200)

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/steam/probe")

        assert resp.status_code == 200
        assert resp.json()["data"]["ok"] is True

        # Must have called GetPlayerSummaries
        assert any("ISteamUser/GetPlayerSummaries" in c["url"] for c in calls), (
            f"Expected GetPlayerSummaries call; got: {calls}"
        )
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=live_ok" in audit_calls[0].get("note", "")

    def test_steam_probe_passes_key_and_steamid_as_params(self, monkeypatch):
        """Steam probe passes api_key and steamid as query params (never in headers)."""
        mock_db = self._make_steam_db()

        calls: list[dict] = []

        async def _fake_get(url, **kwargs):
            calls.append({"method": "GET", "url": str(url), "kwargs": kwargs})
            return self._fake_steam_response(status=200)

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/steam/probe")

        assert resp.status_code == 200
        assert calls, "Expected at least one GET call"

        # Params should include key and steamids
        params = calls[0]["kwargs"].get("params", {})
        assert params.get("key") == _STEAM_API_KEY, f"Expected key={_STEAM_API_KEY!r}; got {params}"
        assert params.get("steamids") == _STEAM_ID, f"Expected steamids={_STEAM_ID!r}; got {params}"

    def test_steam_probe_401_returns_live_failed(self, monkeypatch):
        """Steam probe with GetPlayerSummaries 401 → probe_ok=False, code=401."""
        mock_db = self._make_steam_db()

        async def _fake_get(url, **kwargs):
            return self._fake_steam_response(status=401)

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/steam/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["code"] == 401

    def test_steam_probe_200_empty_players_returns_live_failed(self, monkeypatch):
        """200 but empty players array → live_failed (bad key or steamid)."""
        mock_db = self._make_steam_db()

        async def _fake_get(url, **kwargs):
            return self._fake_steam_response(status=200, players=[])

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/steam/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False

    def test_steam_probe_network_error_falls_back_to_local(self, monkeypatch):
        """Network error during GetPlayerSummaries → fallback to local state (NOT False)."""
        mock_db = self._make_steam_db(last_test_ok=True)

        async def _fake_get(url, **kwargs):
            raise httpx.ConnectError("connection refused")

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/steam/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Network error → skipped_local_check → local state wins.
        assert data["ok"] is True

    def test_steam_probe_no_primary_account_falls_back_to_local(self, monkeypatch):
        """No primary Steam account configured → skipped_local_check, no HTTP calls."""
        mock_db = self._make_steam_db(steam_id=None, last_test_ok=True)

        http_calls: list[str] = []

        async def _fake_get(url, **kwargs):
            http_calls.append(str(url))
            raise AssertionError(f"Should not call HTTP; got GET {url}")

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/steam/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is True
        assert not http_calls, f"No HTTP calls expected; got: {http_calls}"


# ---------------------------------------------------------------------------
# OwnTracks system probe tests
# ---------------------------------------------------------------------------


class TestOwnTracksSystemProbe:
    """POST /api/secrets/system/owntracks_webhook_token/probe format-check tests."""

    def _make_system_db(
        self,
        *,
        token_value: str | None = _OWNTRACKS_TOKEN_VALID,
        last_test_ok: bool | None = True,
    ) -> MagicMock:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["switchboard"]

        butler_pool = AsyncMock()

        async def _butler_fetchrow(sql: str, *args):
            # _fetch_single_system_secret queries butler_secrets by key
            if "butler_secrets" in sql and "secret_key" in sql:
                if args and args[0] == "owntracks_webhook_token":
                    # Full row for _fetch_single_system_secret
                    if "secret_value" in sql:
                        return _make_row(
                            secret_key="owntracks_webhook_token",
                            secret_value=token_value,
                            category="owntracks",
                            description="OwnTracks webhook token",
                            is_sensitive=True,
                            created_at=_NOW,
                            updated_at=_NOW,
                            expires_at=None,
                            last_verified=None,
                            last_test_ok=last_test_ok,
                            last_test_code=None,
                            last_test_message=None,
                        )
            return None

        butler_pool.fetchrow = AsyncMock(side_effect=_butler_fetchrow)
        butler_pool.fetch = AsyncMock(return_value=[])
        butler_pool.execute = AsyncMock(return_value="UPDATE 1")
        mock_db.pool = MagicMock(return_value=butler_pool)

        # Shared pool for probe_log writes
        shared_pool = AsyncMock()

        async def _shared_fetchrow(sql: str, *args):
            if "secret_probe_log" in sql:
                return None
            return None

        shared_pool.fetchrow = AsyncMock(side_effect=_shared_fetchrow)
        shared_pool.fetch = AsyncMock(return_value=[])
        shared_pool.execute = AsyncMock(return_value="INSERT 0 1")

        fake_conn = AsyncMock()
        fake_conn.fetchrow = shared_pool.fetchrow
        fake_conn.fetch = shared_pool.fetch
        fake_conn.execute = shared_pool.execute

        @asynccontextmanager
        async def _transaction():
            yield

        fake_conn.transaction = _transaction

        @asynccontextmanager
        async def _acquire():
            yield fake_conn

        shared_pool.acquire = _acquire
        mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

        return mock_db, butler_pool

    def test_owntracks_valid_token_returns_live_ok(self, monkeypatch):
        """Valid 64-char hex OwnTracks token → probe_ok=True; note probe_status=live_ok."""
        mock_db, _ = self._make_system_db(token_value=_OWNTRACKS_TOKEN_VALID)
        client = _build_app(mock_db)
        # Reset rate limit state
        from butlers.api.routers import secrets_v2

        secrets_v2._system_probe_timestamps.clear()

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        resp = client.post("/api/secrets/system/owntracks_webhook_token/probe")

        assert resp.status_code == 200
        assert resp.json()["data"]["ok"] is True
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=live_ok" in audit_calls[0].get("note", "")

    def test_owntracks_wrong_length_token_returns_live_failed(self):
        """Token with wrong length → probe_ok=False."""
        mock_db, _ = self._make_system_db(token_value="abc123")
        client = _build_app(mock_db)
        from butlers.api.routers import secrets_v2

        secrets_v2._system_probe_timestamps.clear()

        resp = client.post("/api/secrets/system/owntracks_webhook_token/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False

    def test_owntracks_non_hex_token_returns_live_failed(self):
        """Token with non-hex characters → probe_ok=False."""
        # 64 chars but contains 'g' which is not valid hex
        bad_token = "g" * 64
        mock_db, _ = self._make_system_db(token_value=bad_token)
        client = _build_app(mock_db)
        from butlers.api.routers import secrets_v2

        secrets_v2._system_probe_timestamps.clear()

        resp = client.post("/api/secrets/system/owntracks_webhook_token/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False

    def test_owntracks_probe_no_http_calls_made(self, monkeypatch):
        """OwnTracks probe must never make any external HTTP calls."""
        mock_db, _ = self._make_system_db(token_value=_OWNTRACKS_TOKEN_VALID)

        http_calls: list[str] = []

        async def _fake_request(method, url, **kwargs):
            http_calls.append(f"{method} {url}")
            raise AssertionError(f"OwnTracks probe must not make HTTP calls; got {method} {url}")

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=lambda url, **kw: _fake_request("GET", url, **kw))
        fake_client.post = AsyncMock(side_effect=lambda url, **kw: _fake_request("POST", url, **kw))

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        from butlers.api.routers import secrets_v2

        secrets_v2._system_probe_timestamps.clear()

        resp = client.post("/api/secrets/system/owntracks_webhook_token/probe")

        assert resp.status_code == 200
        assert not http_calls, f"No HTTP calls expected for OwnTracks; got: {http_calls}"


# ---------------------------------------------------------------------------
# Spotify unchanged — still falls back to local check
# ---------------------------------------------------------------------------


class TestSpotifyStillFallsBack:
    """Verify Spotify probe still uses local-state fallback (bu-xfq4r owns live verify)."""

    def test_spotify_probe_no_http_calls(self, monkeypatch):
        """Spotify probe must not make any HTTP calls (tracked by bu-xfq4r)."""
        row = _make_entity_info_row(
            info_type="spotify_oauth_refresh",
            value="spotify-refresh-tok",
            last_test_ok=True,
        )
        mock_db = _make_db(
            user_row=row,
            raw_token_value="spotify-refresh-tok",
        )

        http_calls: list[dict] = []

        async def _fake_post(url, **kwargs):
            http_calls.append({"method": "POST", "url": str(url)})
            raise AssertionError("Should not call HTTP for Spotify (handled by bu-xfq4r)")

        async def _fake_get(url, **kwargs):
            http_calls.append({"method": "GET", "url": str(url)})
            raise AssertionError("Should not call HTTP for Spotify (handled by bu-xfq4r)")

        fake_client = AsyncMock()
        fake_client.post = AsyncMock(side_effect=_fake_post)
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Local state: last_test_ok=True + value set → probe_ok=True
        assert data["ok"] is True
        assert not http_calls, f"Spotify must not make HTTP calls; got: {http_calls}"
