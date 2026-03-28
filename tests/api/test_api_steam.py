"""Tests for Steam account management and playtime analytics API router.

Covers all endpoints with mocked database (asyncpg pool) and mocked Steam API:
- POST   /api/steam/accounts           — connect account (validate API key)
- GET    /api/steam/accounts           — list accounts
- DELETE /api/steam/accounts/{id}      — disconnect account
- PUT    /api/steam/accounts/{id}/primary — set primary account
- GET    /api/steam/accounts/{id}/status — per-account credential + poll health
- GET    /api/steam/connector/health   — proxy connector health endpoint
- GET    /api/steam/playtime           — playtime analytics (DB-backed)
- GET    /api/steam/playtime/{app}     — per-game playtime history (DB-backed)

DB calls are mocked via patching steam_account_registry functions and pool
directly. Steam API calls are mocked via SteamAPIClient patches.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.routers.steam import (
    _get_db_manager,
    _SteamValidationError,
    _validate_steam_credentials,
)
from butlers.steam.client import SteamAPIError, SteamRateLimitError
from butlers.steam_account_registry import (
    MissingSteamCredentialsError,
    SteamAccount,
    SteamAccountAlreadyExistsError,
    SteamAccountNotFoundError,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_VALIDATE_PATCH = "butlers.api.routers.steam._validate_steam_credentials"
_GET_SHARED_POOL_PATCH = "butlers.api.routers.steam._get_shared_pool"
_LIST_ACCOUNTS_PATCH = "butlers.api.routers.steam.list_steam_accounts"
_CREATE_ACCOUNT_PATCH = "butlers.api.routers.steam.create_steam_account"
_DISCONNECT_ACCOUNT_PATCH = "butlers.api.routers.steam.disconnect_account"
_SET_PRIMARY_PATCH = "butlers.api.routers.steam.set_primary_account"
_RESOLVE_ACCOUNT_PATCH = "butlers.api.routers.steam.resolve_steam_account"
_QUERY_PLAYTIME_PATCH = "butlers.api.routers.steam._query_playtime_aggregates"
_QUERY_GAME_HISTORY_PATCH = "butlers.api.routers.steam._query_game_play_history"
_FETCH_CONNECTOR_HEALTH_PATCH = "butlers.api.routers.steam._fetch_connector_health"
_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH = "butlers.api.routers.steam._probe_connector_account_health"

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_ACCOUNT_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
_ENTITY_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_STEAM_ID = 76561198000000001


def _make_account(
    *,
    account_id: uuid.UUID = _ACCOUNT_ID,
    steam_id: int = _STEAM_ID,
    display_name: str | None = "GamerDude",
    is_primary: bool = True,
    status: str = "active",
) -> SteamAccount:
    """Build a minimal SteamAccount for tests."""
    return SteamAccount(
        id=account_id,
        entity_id=_ENTITY_ID,
        steam_id=steam_id,
        display_name=display_name,
        profile_url="https://steamcommunity.com/id/gamerdude",
        avatar_url="https://avatars.steamstatic.com/abc.jpg",
        is_primary=is_primary,
        status=status,
        connected_at=datetime(2024, 1, 15, tzinfo=UTC),
        last_poll_at=None,
        metadata={},
    )


def _make_pool_with_key(api_key: str = "A" * 32) -> MagicMock:
    """Build a mock asyncpg pool that returns the given API key from entity_info."""
    row = {"value": api_key}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)

    pool = MagicMock()

    class _FakeAcquire:
        """Async context manager for pool.acquire()."""

        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            pass

    pool.acquire = MagicMock(return_value=_FakeAcquire())
    return pool


def _make_pool_no_key() -> MagicMock:
    """Build a mock pool that returns no API key row."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    pool = MagicMock()

    class _FakeAcquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            pass

    pool.acquire = MagicMock(return_value=_FakeAcquire())
    return pool


def _build_app(pool: MagicMock | None = None) -> httpx.AsyncClient:
    """Return a test httpx.AsyncClient wired to the app with mocked DB pool."""
    from butlers.api.app import create_app

    _app = create_app(api_key="")

    if pool is not None:
        _app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
    else:
        _app.dependency_overrides[_get_db_manager] = lambda: None

    return _app


# ---------------------------------------------------------------------------
# Helper: dummy player summary from Steam API
# ---------------------------------------------------------------------------

_PLAYER_SUMMARY = {
    "steamid": str(_STEAM_ID),
    "personaname": "GamerDude",
    "profileurl": "https://steamcommunity.com/id/gamerdude",
    "avatarfull": "https://avatars.steamstatic.com/abc_full.jpg",
}


# ---------------------------------------------------------------------------
# POST /api/steam/accounts
# ---------------------------------------------------------------------------


class TestConnectSteamAccount:
    async def test_success_creates_account(self):
        """POST /api/steam/accounts returns 200 and the new account on success."""
        account = _make_account()
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_VALIDATE_PATCH, return_value=_PLAYER_SUMMARY),
            patch(_CREATE_ACCOUNT_PATCH, return_value=account),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={
                        "steam_id": _STEAM_ID,
                        "api_key": "A" * 32,
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert str(_STEAM_ID) in body["message"] or "GamerDude" in body["message"]
        assert body["account"]["steam_id"] == _STEAM_ID
        assert body["account"]["is_primary"] is True
        assert body["account"]["status"] == "active"

    async def test_api_key_never_in_response(self):
        """POST /api/steam/accounts must not include the API key in any response field."""
        account = _make_account()
        pool = _make_pool_with_key()
        app = _build_app(pool)
        secret_key = "B" * 32

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_VALIDATE_PATCH, return_value=_PLAYER_SUMMARY),
            patch(_CREATE_ACCOUNT_PATCH, return_value=account),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={"steam_id": _STEAM_ID, "api_key": secret_key},
                )

        assert secret_key not in resp.text

    async def test_returns_409_when_account_already_exists(self):
        """POST /api/steam/accounts returns 409 when the steam_id is already connected."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_VALIDATE_PATCH, return_value=_PLAYER_SUMMARY),
            patch(
                _CREATE_ACCOUNT_PATCH,
                side_effect=SteamAccountAlreadyExistsError("Already exists"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={"steam_id": _STEAM_ID, "api_key": "A" * 32},
                )

        assert resp.status_code == 409
        assert "already connected" in resp.json()["detail"].lower()

    async def test_returns_400_on_invalid_api_key(self):
        """POST /api/steam/accounts returns 400 when the API key is invalid."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _VALIDATE_PATCH,
                side_effect=_SteamValidationError(
                    "Invalid API key", category="invalid_api_key", http_status=400
                ),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={"steam_id": _STEAM_ID, "api_key": "A" * 32},
                )

        assert resp.status_code == 400
        assert "Invalid API key" in resp.json()["detail"]

    async def test_returns_400_when_steam_id_not_found(self):
        """POST /api/steam/accounts returns 400 when the steam_id is not found."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _VALIDATE_PATCH,
                side_effect=_SteamValidationError(
                    "Steam account not found", category="steam_id_not_found", http_status=400
                ),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={"steam_id": _STEAM_ID, "api_key": "A" * 32},
                )

        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    async def test_returns_503_when_no_db(self):
        """POST /api/steam/accounts returns 503 when database is unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={"steam_id": _STEAM_ID, "api_key": "A" * 32},
                )

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_returns_422_when_api_key_too_short(self):
        """POST /api/steam/accounts returns 422 for API keys shorter than 32 chars."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/steam/accounts",
                json={"steam_id": _STEAM_ID, "api_key": "short"},
            )

        assert resp.status_code == 422

    async def test_returns_422_when_steam_id_zero(self):
        """POST /api/steam/accounts returns 422 for steam_id <= 0."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/steam/accounts",
                json={"steam_id": 0, "api_key": "A" * 32},
            )

        assert resp.status_code == 422

    async def test_display_name_override(self):
        """POST /api/steam/accounts uses custom display_name when provided."""
        account = _make_account(display_name="My Custom Name")
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_VALIDATE_PATCH, return_value=_PLAYER_SUMMARY),
            patch(_CREATE_ACCOUNT_PATCH, return_value=account) as mock_create,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/api/steam/accounts",
                    json={
                        "steam_id": _STEAM_ID,
                        "api_key": "A" * 32,
                        "display_name": "My Custom Name",
                    },
                )

        # display_name kwarg passed to create_steam_account
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["display_name"] == "My Custom Name"

    async def test_primary_flag_in_message_when_set_as_primary(self):
        """Response message mentions primary when first account sets as primary."""
        account = _make_account(is_primary=True)
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_VALIDATE_PATCH, return_value=_PLAYER_SUMMARY),
            patch(_CREATE_ACCOUNT_PATCH, return_value=account),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/steam/accounts",
                    json={"steam_id": _STEAM_ID, "api_key": "A" * 32},
                )

        assert "primary" in resp.json()["message"].lower()


# ---------------------------------------------------------------------------
# GET /api/steam/accounts
# ---------------------------------------------------------------------------


class TestListSteamAccounts:
    async def test_returns_all_accounts(self):
        """GET /api/steam/accounts returns a list of all connected accounts."""
        accounts = [
            _make_account(is_primary=True),
            _make_account(
                account_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
                steam_id=76561198000000002,
                display_name="Player2",
                is_primary=False,
            ),
        ]
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_LIST_ACCOUNTS_PATCH, return_value=accounts),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/accounts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accounts"]) == 2
        assert body["accounts"][0]["is_primary"] is True
        assert body["accounts"][1]["display_name"] == "Player2"

    async def test_returns_empty_list_when_no_accounts(self):
        """GET /api/steam/accounts returns an empty list when no accounts connected."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_LIST_ACCOUNTS_PATCH, return_value=[]),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/accounts")

        assert resp.status_code == 200
        assert resp.json()["accounts"] == []

    async def test_returns_503_when_no_db(self):
        """GET /api/steam/accounts returns 503 when database is unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/accounts")

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_api_key_not_in_response(self):
        """GET /api/steam/accounts must never expose API keys."""
        accounts = [_make_account()]
        pool = _make_pool_with_key(api_key="C" * 32)
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_LIST_ACCOUNTS_PATCH, return_value=accounts),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/accounts")

        assert "C" * 32 not in resp.text


# ---------------------------------------------------------------------------
# DELETE /api/steam/accounts/{id}
# ---------------------------------------------------------------------------


class TestDisconnectSteamAccount:
    async def test_success_soft_revokes_account(self):
        """DELETE /api/steam/accounts/{id} returns 200 and soft-revokes the account."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_DISCONNECT_ACCOUNT_PATCH) as mock_disconnect,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/api/steam/accounts/{_ACCOUNT_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "revoked" in body["message"].lower()

        # Must call disconnect with hard_delete=False
        mock_disconnect.assert_called_once_with(pool, _ACCOUNT_ID, hard_delete=False)

    async def test_returns_404_when_account_not_found(self):
        """DELETE /api/steam/accounts/{id} returns 404 for unknown account ID."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _DISCONNECT_ACCOUNT_PATCH,
                side_effect=SteamAccountNotFoundError("Not found"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/api/steam/accounts/{_ACCOUNT_ID}")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_returns_503_when_no_db(self):
        """DELETE /api/steam/accounts/{id} returns 503 when database is unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/api/steam/accounts/{_ACCOUNT_ID}")

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_returns_422_for_invalid_uuid(self):
        """DELETE /api/steam/accounts/{id} returns 422 for non-UUID account_id."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/steam/accounts/not-a-valid-uuid")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/steam/accounts/{id}/primary
# ---------------------------------------------------------------------------


class TestSetPrimarySteamAccount:
    async def test_success_sets_primary_account(self):
        """PUT /api/steam/accounts/{id}/primary returns 200 with updated account."""
        account = _make_account(is_primary=True)
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_SET_PRIMARY_PATCH, return_value=account) as mock_set_primary,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(f"/api/steam/accounts/{_ACCOUNT_ID}/primary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "primary" in body["message"].lower()
        assert body["account"]["id"] == str(_ACCOUNT_ID)
        assert body["account"]["is_primary"] is True

        # Verify the registry function was called with correct args.
        mock_set_primary.assert_called_once_with(pool, _ACCOUNT_ID)

    async def test_response_includes_display_name_in_message(self):
        """PUT /api/steam/accounts/{id}/primary message includes the display name."""
        account = _make_account(display_name="GamerDude", is_primary=True)
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_SET_PRIMARY_PATCH, return_value=account),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(f"/api/steam/accounts/{_ACCOUNT_ID}/primary")

        assert resp.status_code == 200
        assert "GamerDude" in resp.json()["message"]

    async def test_returns_404_when_account_not_found(self):
        """PUT /api/steam/accounts/{id}/primary returns 404 for unknown account ID."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _SET_PRIMARY_PATCH,
                side_effect=SteamAccountNotFoundError("Not found"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(f"/api/steam/accounts/{_ACCOUNT_ID}/primary")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_returns_503_when_no_db(self):
        """PUT /api/steam/accounts/{id}/primary returns 503 when database is unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(f"/api/steam/accounts/{_ACCOUNT_ID}/primary")

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_returns_422_for_invalid_uuid(self):
        """PUT /api/steam/accounts/{id}/primary returns 422 for non-UUID account_id."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put("/api/steam/accounts/not-a-valid-uuid/primary")

        assert resp.status_code == 422

    async def test_api_key_not_in_response(self):
        """PUT /api/steam/accounts/{id}/primary must never expose API keys."""
        account = _make_account(is_primary=True)
        pool = _make_pool_with_key(api_key="D" * 32)
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_SET_PRIMARY_PATCH, return_value=account),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(f"/api/steam/accounts/{_ACCOUNT_ID}/primary")

        assert "D" * 32 not in resp.text


# ---------------------------------------------------------------------------
# GET /api/steam/playtime
# ---------------------------------------------------------------------------

# Simulated rows from connectors.steam_play_history (post-aggregate query).
_PLAY_HISTORY_AGGREGATES = [
    {"app_id": 570, "app_name": "Dota 2", "total_playtime": 5000},
    {"app_id": 730, "app_name": "CS:GO", "total_playtime": 3000},
    {"app_id": 440, "app_name": "Team Fortress 2", "total_playtime": 1000},
]


def _make_simple_pool() -> MagicMock:
    """Build a minimal mock asyncpg pool (no fetchrow needed for playtime DB tests)."""
    return MagicMock()


class TestGetSteamPlaytime:
    async def test_returns_analytics_for_primary_account(self):
        """GET /api/steam/playtime returns aggregated analytics from DB for primary account."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=_PLAY_HISTORY_AGGREGATES),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime")

        assert resp.status_code == 200
        body = resp.json()
        assert body["steam_id"] == _STEAM_ID
        assert body["display_name"] == "GamerDude"
        assert body["total_games"] == 3
        assert body["total_playtime_minutes"] == 9000  # 5000+3000+1000
        assert len(body["top_games"]) == 3
        # Top game by playtime should be Dota 2
        assert body["top_games"][0]["app_id"] == 570
        assert body["top_games"][0]["name"] == "Dota 2"
        assert body["top_games"][0]["playtime_minutes"] == 5000

    async def test_top_n_limits_results(self):
        """GET /api/steam/playtime respects the top_n query parameter."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=_PLAY_HISTORY_AGGREGATES),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime?top_n=2")

        body = resp.json()
        assert len(body["top_games"]) == 2

    async def test_days_param_passed_to_query(self):
        """GET /api/steam/playtime passes the days param to the query helper."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=[]) as mock_query,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime?days=7")

        assert resp.status_code == 200
        mock_query.assert_called_once_with(pool, account_id=account.id, days=7)

    async def test_days_included_in_response(self):
        """GET /api/steam/playtime includes the days window in the response."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=[]),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime?days=14")

        assert resp.status_code == 200
        assert resp.json()["days"] == 14

    async def test_account_id_query_selects_specific_account(self):
        """GET /api/steam/playtime uses the account_id query param to select account."""
        second_id = uuid.UUID("99999999-9999-9999-9999-999999999999")
        account = _make_account(account_id=second_id, is_primary=False)
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account) as mock_resolve,
            patch(_QUERY_PLAYTIME_PATCH, return_value=[]),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime?account_id={second_id}")

        assert resp.status_code == 200
        mock_resolve.assert_called_once_with(pool, account=second_id)

    async def test_returns_400_when_no_primary_account(self):
        """GET /api/steam/playtime returns 400 when no primary account is configured."""
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _RESOLVE_ACCOUNT_PATCH,
                side_effect=MissingSteamCredentialsError("No primary"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime")

        assert resp.status_code == 400
        assert "primary" in resp.json()["detail"].lower()

    async def test_returns_404_when_account_not_found(self):
        """GET /api/steam/playtime returns 404 for unknown account_id."""
        pool = _make_simple_pool()
        app = _build_app(pool)
        missing_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _RESOLVE_ACCOUNT_PATCH,
                side_effect=SteamAccountNotFoundError("Not found"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime?account_id={missing_id}")

        assert resp.status_code == 404

    async def test_returns_503_when_no_db(self):
        """GET /api/steam/playtime returns 503 when database is unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime")

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_returns_422_for_top_n_out_of_range(self):
        """GET /api/steam/playtime returns 422 when top_n is out of [1, 100]."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/steam/playtime?top_n=200")

        assert resp.status_code == 422

    async def test_top_games_sorted_by_playtime_descending(self):
        """GET /api/steam/playtime top_games are sorted by total playtime descending."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        aggregates = [
            {"app_id": 1, "app_name": "Game A", "total_playtime": 100},
            {"app_id": 2, "app_name": "Game B", "total_playtime": 999},
            {"app_id": 3, "app_name": "Game C", "total_playtime": 500},
        ]

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=aggregates),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime")

        body = resp.json()
        playtimes = [g["playtime_minutes"] for g in body["top_games"]]
        assert playtimes == sorted(playtimes, reverse=True)

    async def test_queried_at_is_present(self):
        """GET /api/steam/playtime response includes a queried_at timestamp."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=[]),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime")

        assert resp.status_code == 200
        body = resp.json()
        assert "queried_at" in body
        assert body["queried_at"] is not None

    async def test_empty_db_returns_zero_totals(self):
        """GET /api/steam/playtime returns zero totals when no play history exists."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_PLAYTIME_PATCH, return_value=[]),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/playtime")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_games"] == 0
        assert body["total_playtime_minutes"] == 0
        assert body["top_games"] == []


# ---------------------------------------------------------------------------
# GET /api/steam/playtime/{app_id}
# ---------------------------------------------------------------------------

from datetime import date as _date  # noqa: E402  (local alias to avoid shadowing)

_APP_ID = 570

_GAME_HISTORY_ROWS = [
    {
        "date": _date(2026, 3, 27),
        "playtime_minutes": 120,
        "app_name": "Dota 2",
        "recorded_at": datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
    },
    {
        "date": _date(2026, 3, 26),
        "playtime_minutes": 80,
        "app_name": "Dota 2",
        "recorded_at": datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC),
    },
]


class TestGetSteamGamePlaytime:
    async def test_returns_history_for_app(self):
        """GET /api/steam/playtime/{app_id} returns history rows for that game."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=_GAME_HISTORY_ROWS),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["app_id"] == _APP_ID
        assert body["app_name"] == "Dota 2"
        assert body["total_playtime_minutes"] == 200  # 120 + 80
        assert len(body["history"]) == 2
        assert body["history"][0]["playtime_minutes"] == 120

    async def test_days_param_passed_to_query(self):
        """GET /api/steam/playtime/{app_id} passes days to query helper."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=_GAME_HISTORY_ROWS) as mock_query,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}?days=7")

        assert resp.status_code == 200
        mock_query.assert_called_once_with(pool, account_id=account.id, app_id=_APP_ID, days=7)

    async def test_days_in_response(self):
        """GET /api/steam/playtime/{app_id} includes the days window in the response."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=_GAME_HISTORY_ROWS),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}?days=14")

        assert resp.json()["days"] == 14

    async def test_account_id_query_selects_specific_account(self):
        """GET /api/steam/playtime/{app_id} uses account_id to select account."""
        second_id = uuid.UUID("99999999-9999-9999-9999-999999999999")
        account = _make_account(account_id=second_id, is_primary=False)
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account) as mock_resolve,
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=_GAME_HISTORY_ROWS),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}?account_id={second_id}")

        assert resp.status_code == 200
        mock_resolve.assert_called_once_with(pool, account=second_id)

    async def test_returns_404_when_no_history(self):
        """GET /api/steam/playtime/{app_id} returns 404 when no rows in window."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=[]),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}")

        assert resp.status_code == 404
        assert "no playtime history" in resp.json()["detail"].lower()

    async def test_returns_400_when_no_primary_account(self):
        """GET /api/steam/playtime/{app_id} returns 400 when no primary account configured."""
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _RESOLVE_ACCOUNT_PATCH,
                side_effect=MissingSteamCredentialsError("No primary"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}")

        assert resp.status_code == 400
        assert "primary" in resp.json()["detail"].lower()

    async def test_returns_404_when_account_not_found(self):
        """GET /api/steam/playtime/{app_id} returns 404 for unknown account_id."""
        pool = _make_simple_pool()
        app = _build_app(pool)
        missing_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _RESOLVE_ACCOUNT_PATCH,
                side_effect=SteamAccountNotFoundError("Not found"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}?account_id={missing_id}")

        assert resp.status_code == 404

    async def test_returns_503_when_no_db(self):
        """GET /api/steam/playtime/{app_id} returns 503 when database is unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}")

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_queried_at_in_response(self):
        """GET /api/steam/playtime/{app_id} includes queried_at in the response."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=_GAME_HISTORY_ROWS),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}")

        body = resp.json()
        assert "queried_at" in body
        assert body["queried_at"] is not None

    async def test_app_name_null_when_not_recorded(self):
        """GET /api/steam/playtime/{app_id} returns null app_name when not in history."""
        account = _make_account()
        pool = _make_simple_pool()
        app = _build_app(pool)

        rows_no_name = [
            {
                "date": _date(2026, 3, 27),
                "playtime_minutes": 60,
                "app_name": None,
                "recorded_at": datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
            }
        ]

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_QUERY_GAME_HISTORY_PATCH, return_value=rows_no_name),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/playtime/{_APP_ID}")

        assert resp.status_code == 200
        assert resp.json()["app_name"] is None


# ---------------------------------------------------------------------------
# Unit tests for _validate_steam_credentials
# ---------------------------------------------------------------------------


class TestValidateSteamCredentials:
    async def test_success_returns_player_summary(self):
        """Validation returns player summary dict on success."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value={"players": [_PLAYER_SUMMARY]})

        with patch("butlers.api.routers.steam.SteamAPIClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _validate_steam_credentials("A" * 32, _STEAM_ID)

        assert result["steamid"] == str(_STEAM_ID)
        assert result["personaname"] == "GamerDude"

    async def test_raises_invalid_api_key_on_403(self):
        """Validation raises _SteamValidationError(invalid_api_key) on HTTP 403.

        Steam returns HTTP 403 for invalid/unauthorized API keys. SteamAPIClient
        converts this to SteamRateLimitError(status_code=403) — not SteamAPIError —
        because both 403 and 429 are in _RATE_LIMIT_STATUSES. The router must
        distinguish 403 (bad key) from 429 (genuine rate limit) via status_code.
        """
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=SteamRateLimitError(retry_after_s=60.0, status_code=403)
        )

        with patch("butlers.api.routers.steam.SteamAPIClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_SteamValidationError) as exc_info:
                await _validate_steam_credentials("A" * 32, _STEAM_ID)

        assert exc_info.value.category == "invalid_api_key"
        assert exc_info.value.http_status == 400

    async def test_raises_steam_id_not_found_when_empty_players(self):
        """Validation raises _SteamValidationError(steam_id_not_found) when players list empty."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value={"players": []})

        with patch("butlers.api.routers.steam.SteamAPIClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_SteamValidationError) as exc_info:
                await _validate_steam_credentials("A" * 32, _STEAM_ID)

        assert exc_info.value.category == "steam_id_not_found"

    async def test_raises_api_error_on_rate_limit(self):
        """Validation raises _SteamValidationError(api_error) with 429 on rate limit."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=SteamRateLimitError(retry_after_s=60.0, status_code=429)
        )

        with patch("butlers.api.routers.steam.SteamAPIClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_SteamValidationError) as exc_info:
                await _validate_steam_credentials("A" * 32, _STEAM_ID)

        assert exc_info.value.category == "api_error"
        assert exc_info.value.http_status == 429

    async def test_raises_api_error_on_unexpected_status(self):
        """Validation raises _SteamValidationError(api_error) on unexpected HTTP status."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=SteamAPIError(500, "Internal Server Error"))

        with patch("butlers.api.routers.steam.SteamAPIClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_SteamValidationError) as exc_info:
                await _validate_steam_credentials("A" * 32, _STEAM_ID)

        assert exc_info.value.category == "api_error"
        assert exc_info.value.http_status == 502


# ---------------------------------------------------------------------------
# GET /api/steam/accounts/{id}/status
# ---------------------------------------------------------------------------


class TestGetSteamAccountStatus:
    async def test_returns_status_with_api_key(self):
        """GET /api/steam/accounts/{id}/status returns 200 when API key is present."""
        account = _make_account()
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH, return_value="healthy"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(_ACCOUNT_ID)
        assert body["steam_id"] == _STEAM_ID
        assert body["status"] == "active"
        assert body["has_api_key"] is True
        assert body["key_valid"] is None  # Not validated yet
        assert body["connector_health"] == "healthy"

    async def test_returns_status_without_api_key(self):
        """GET /api/steam/accounts/{id}/status returns has_api_key=False when key absent."""
        account = _make_account()
        pool = _make_pool_no_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH, return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["has_api_key"] is False
        assert body["connector_health"] is None

    async def test_returns_last_poll_at_when_present(self):
        """GET /api/steam/accounts/{id}/status includes last_poll_at when set."""
        poll_ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        account = SteamAccount(
            id=_ACCOUNT_ID,
            entity_id=_ENTITY_ID,
            steam_id=_STEAM_ID,
            display_name="GamerDude",
            profile_url=None,
            avatar_url=None,
            is_primary=True,
            status="active",
            connected_at=datetime(2024, 1, 15, tzinfo=UTC),
            last_poll_at=poll_ts,
            metadata={},
        )
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH, return_value="healthy"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["last_poll_at"] is not None
        assert "2024-06-01" in body["last_poll_at"]

    async def test_returns_null_last_poll_at_when_never_polled(self):
        """GET /api/steam/accounts/{id}/status returns null last_poll_at when never polled."""
        account = _make_account()  # last_poll_at=None
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH, return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 200
        assert resp.json()["last_poll_at"] is None

    async def test_returns_404_for_unknown_account(self):
        """GET /api/steam/accounts/{id}/status returns 404 when account not found."""
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(
                _RESOLVE_ACCOUNT_PATCH,
                side_effect=SteamAccountNotFoundError("Not found"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_returns_503_when_no_db(self):
        """GET /api/steam/accounts/{id}/status returns 503 when database unavailable."""
        app = _build_app(None)

        with patch(_GET_SHARED_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_returns_422_for_invalid_uuid(self):
        """GET /api/steam/accounts/{id}/status returns 422 for non-UUID account_id."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/steam/accounts/not-a-valid-uuid/status")

        assert resp.status_code == 422

    async def test_connector_health_degraded(self):
        """GET /api/steam/accounts/{id}/status returns degraded connector_health."""
        account = _make_account()
        pool = _make_pool_with_key()
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH, return_value="degraded"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert resp.status_code == 200
        assert resp.json()["connector_health"] == "degraded"

    async def test_api_key_never_returned_in_status(self):
        """GET /api/steam/accounts/{id}/status must never expose the API key."""
        account = _make_account()
        pool = _make_pool_with_key(api_key="Z" * 32)
        app = _build_app(pool)

        with (
            patch(_GET_SHARED_POOL_PATCH, return_value=pool),
            patch(_RESOLVE_ACCOUNT_PATCH, return_value=account),
            patch(_PROBE_CONNECTOR_ACCOUNT_HEALTH_PATCH, return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/steam/accounts/{_ACCOUNT_ID}/status")

        assert "Z" * 32 not in resp.text


# ---------------------------------------------------------------------------
# GET /api/steam/connector/health
# ---------------------------------------------------------------------------


_CONNECTOR_HEALTH_PAYLOAD = {
    "status": "healthy",
    "uptime_seconds": 3600,
    "active_accounts": 2,
    "account_health": [
        {
            "steam_id": "****0001",
            "endpoint_identity": "steam-76561198000000001",
            "status": "healthy",
            "error": None,
            "data_types": {
                "recently_played": {
                    "status": "healthy",
                    "last_poll_at": "2024-06-01T12:00:00+00:00",
                },
                "online_status": {
                    "status": "healthy",
                    "last_poll_at": "2024-06-01T12:00:00+00:00",
                },
            },
        },
    ],
}


class TestGetSteamConnectorHealth:
    async def test_returns_healthy_when_connector_running(self):
        """GET /api/steam/connector/health returns healthy status when connector is up."""
        app = _build_app()

        with patch(_FETCH_CONNECTOR_HEALTH_PATCH, return_value=_CONNECTOR_HEALTH_PAYLOAD):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/connector/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["uptime_seconds"] == 3600
        assert body["active_accounts"] == 2
        assert len(body["account_health"]) == 1
        assert body["account_health"][0]["steam_id"] == "****0001"
        assert body["account_health"][0]["status"] == "healthy"

    async def test_returns_not_running_when_connector_unreachable(self):
        """GET /api/steam/connector/health returns not_running when connector is down."""
        app = _build_app()

        with patch(_FETCH_CONNECTOR_HEALTH_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/connector/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_running"
        assert body["uptime_seconds"] is None
        assert body["active_accounts"] is None
        assert body["account_health"] == []
        assert body["connector_url"] is not None

    async def test_returns_connector_url_in_response(self):
        """GET /api/steam/connector/health always includes connector_url in response."""
        app = _build_app()

        with patch(_FETCH_CONNECTOR_HEALTH_PATCH, return_value=_CONNECTOR_HEALTH_PAYLOAD):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/connector/health")

        body = resp.json()
        assert "connector_url" in body
        assert "40089" in body["connector_url"]

    async def test_data_type_health_parsed_correctly(self):
        """GET /api/steam/connector/health parses data_types health correctly."""
        app = _build_app()

        with patch(_FETCH_CONNECTOR_HEALTH_PATCH, return_value=_CONNECTOR_HEALTH_PAYLOAD):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/connector/health")

        body = resp.json()
        data_types = body["account_health"][0]["data_types"]
        assert "recently_played" in data_types
        assert data_types["recently_played"]["status"] == "healthy"
        assert data_types["recently_played"]["last_poll_at"] is not None

    async def test_handles_null_last_poll_at_in_data_types(self):
        """GET /api/steam/connector/health handles null last_poll_at in data types."""
        payload = {
            "status": "degraded",
            "uptime_seconds": 120,
            "active_accounts": 1,
            "account_health": [
                {
                    "steam_id": "****0001",
                    "endpoint_identity": "steam-x",
                    "status": "degraded",
                    "error": "Rate limited",
                    "data_types": {
                        "recently_played": {
                            "status": "degraded",
                            "last_poll_at": None,
                        },
                    },
                }
            ],
        }
        app = _build_app()

        with patch(_FETCH_CONNECTOR_HEALTH_PATCH, return_value=payload):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/steam/connector/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        dt = body["account_health"][0]["data_types"]["recently_played"]
        assert dt["last_poll_at"] is None
        assert body["account_health"][0]["error"] == "Rate limited"

    async def test_health_endpoint_uses_env_port(self):
        """GET /api/steam/connector/health uses STEAM_CONNECTOR_HEALTH_PORT env var."""
        import os

        app = _build_app()
        captured_urls: list[str] = []

        async def _mock_fetch(url: str) -> dict | None:
            captured_urls.append(url)
            return None

        with (
            patch(_FETCH_CONNECTOR_HEALTH_PATCH, side_effect=_mock_fetch),
            patch.dict(os.environ, {"STEAM_CONNECTOR_HEALTH_PORT": "49999"}),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.get("/api/steam/connector/health")

        assert len(captured_urls) == 1
        assert "49999" in captured_urls[0]
