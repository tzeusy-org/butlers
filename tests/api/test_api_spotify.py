"""Tests for Spotify dashboard API router.

Covers all endpoints with mocked CredentialStore and mocked Spotify API:
- POST /api/connectors/spotify/config
- POST /api/connectors/spotify/oauth/start
- GET  /api/connectors/spotify/oauth/callback
- GET  /api/connectors/spotify/status
- POST /api/connectors/spotify/disconnect

All Spotify API calls (token exchange, /me) are mocked. CredentialStore
is injected via dependency_overrides.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.spotify import (
    _clear_state_store,
    _derive_pkce_challenge,
    _generate_pkce_verifier,
    _get_db_manager,
    _state_store,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_TOKEN_EXCHANGE_PATCH = "butlers.api.routers.spotify._exchange_code_for_tokens"
_FETCH_ME_PATCH = "butlers.api.routers.spotify._fetch_spotify_me"

# ---------------------------------------------------------------------------
# Fake token response
# ---------------------------------------------------------------------------

_FAKE_TOKENS = {
    "access_token": "BQA_fake_access_token",
    "refresh_token": "AQA_fake_refresh_token",
    "expires_in": 3600,
    "scope": "user-read-playback-state user-read-recently-played user-top-read",
    "token_type": "Bearer",
}

_FAKE_ME = {
    "display_name": "Test User",
    "email": "test@example.com",
    "product": "premium",
    "id": "testuser123",
}

# ---------------------------------------------------------------------------
# CredentialStore mock helpers
# ---------------------------------------------------------------------------


def _make_cred_store(
    *,
    client_id: str | None = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expires_at: str | None = None,
) -> MagicMock:
    """Build a mock CredentialStore that returns specified values per key."""
    store = MagicMock()

    creds: dict[str, str | None] = {
        "SPOTIFY_CLIENT_ID": client_id,
        "SPOTIFY_ACCESS_TOKEN": access_token,
        "SPOTIFY_REFRESH_TOKEN": refresh_token,
        "SPOTIFY_TOKEN_EXPIRES_AT": token_expires_at,
    }

    async def _resolve(key: str, **_kwargs) -> str | None:
        return creds.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock(return_value=None)
    store.delete = AsyncMock(return_value=True)
    return store


def _make_db_manager(cred_store: MagicMock) -> MagicMock:
    """Build a mock DatabaseManager with a credential_shared_pool method.

    Note: the pool returned by credential_shared_pool is a bare MagicMock.
    The actual cred_store is injected by patching _make_credential_store
    directly in each test, so the pool returned here is never used — it
    only satisfies the code path that calls db_manager.credential_shared_pool().
    """
    pool = MagicMock()
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    return db_manager


# ---------------------------------------------------------------------------
# App fixture factory
# ---------------------------------------------------------------------------


def _build_app(cred_store: MagicMock | None):
    """Return an app instance with mocked DB dependency wired in.

    Returns only the FastAPI app (not a tuple). Each test creates its own
    httpx.AsyncClient via transport=httpx.ASGITransport(app=app).
    """
    _app = create_app(api_key="")

    if cred_store is not None:
        db_manager = _make_db_manager(cred_store)

        # Wire the mock db_manager into the dependency injection
        _app.dependency_overrides[_get_db_manager] = lambda: db_manager

    return _app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_states():
    """Ensure the PKCE/CSRF state store is empty before and after each test."""
    _clear_state_store()
    yield
    _clear_state_store()


@pytest.fixture
def cred_store_no_client_id():
    return _make_cred_store(client_id=None)


@pytest.fixture
def cred_store_client_id_only():
    return _make_cred_store(client_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")


@pytest.fixture
def cred_store_connected():
    return _make_cred_store(
        client_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        access_token="BQA_fake_access_token",
        refresh_token="AQA_fake_refresh_token",
        token_expires_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Unit tests for PKCE helpers
# ---------------------------------------------------------------------------


class TestPKCEHelpers:
    def test_verifier_length(self):
        """Code verifier should be within RFC 7636 bounds (43-128 chars)."""
        verifier = _generate_pkce_verifier()
        assert 43 <= len(verifier) <= 128

    def test_verifier_url_safe(self):
        """Code verifier must use only unreserved URL-safe characters."""
        verifier = _generate_pkce_verifier()
        import re

        assert re.match(r"^[A-Za-z0-9\-_]+$", verifier), (
            f"Verifier contains invalid chars: {verifier}"
        )

    def test_challenge_is_deterministic(self):
        """Same verifier always produces the same S256 challenge."""
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        c1 = _derive_pkce_challenge(verifier)
        c2 = _derive_pkce_challenge(verifier)
        assert c1 == c2

    def test_challenge_format(self):
        """Challenge should be base64url without padding."""
        verifier = _generate_pkce_verifier()
        challenge = _derive_pkce_challenge(verifier)
        import re

        assert re.match(r"^[A-Za-z0-9\-_]+$", challenge)
        assert "=" not in challenge

    def test_verifier_and_challenge_differ(self):
        """Verifier and challenge must be different values."""
        verifier = _generate_pkce_verifier()
        challenge = _derive_pkce_challenge(verifier)
        assert verifier != challenge


# ---------------------------------------------------------------------------
# POST /api/connectors/spotify/config
# ---------------------------------------------------------------------------


class TestSpotifyConfig:
    async def test_config_stores_client_id(self, cred_store_no_client_id):
        """Config endpoint stores client_id and returns success."""
        app = _build_app(cred_store_no_client_id)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_no_client_id,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/connectors/spotify/config",
                    json={"client_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        cred_store_no_client_id.store.assert_called_once()
        call_kwargs = cred_store_no_client_id.store.call_args
        assert call_kwargs.args[0] == "SPOTIFY_CLIENT_ID"
        assert call_kwargs.args[1] == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

    async def test_config_rejects_invalid_client_id_too_short(self, cred_store_no_client_id):
        """Config endpoint rejects client_id that is not 32 hex chars."""
        app = _build_app(cred_store_no_client_id)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_no_client_id,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/connectors/spotify/config",
                    json={"client_id": "tooshort"},
                )

        assert resp.status_code == 422

    async def test_config_rejects_non_hex_client_id(self, cred_store_no_client_id):
        """Config endpoint rejects client_id with non-hex characters."""
        app = _build_app(cred_store_no_client_id)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_no_client_id,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/connectors/spotify/config",
                    json={"client_id": "Z1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"},  # 'Z' is not hex
                )

        assert resp.status_code == 422

    async def test_config_503_when_no_db(self):
        """Config returns 503 when credential store is unavailable."""
        app = _build_app(None)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=None,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/connectors/spotify/config",
                    json={"client_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"},
                )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/connectors/spotify/oauth/start
# ---------------------------------------------------------------------------


class TestSpotifyOAuthStart:
    async def test_start_returns_auth_url(self, cred_store_client_id_only):
        """oauth/start returns a Spotify authorization URL with PKCE params."""
        app = _build_app(cred_store_client_id_only)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_client_id_only,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/oauth/start")

        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_url" in data
        assert "state" in data
        url = data["authorization_url"]
        assert url.startswith("https://accounts.spotify.com/authorize")
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "user-read-playback-state" in url

    async def test_start_stores_state_entry(self, cred_store_client_id_only):
        """oauth/start persists a state entry in the in-memory store."""
        app = _build_app(cred_store_client_id_only)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_client_id_only,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/oauth/start")

        state = resp.json()["state"]
        assert state in _state_store

    async def test_start_400_when_no_client_id(self, cred_store_no_client_id):
        """oauth/start returns 400 when client_id is not configured."""
        app = _build_app(cred_store_no_client_id)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_no_client_id,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/oauth/start")

        assert resp.status_code == 400
        assert "client_id" in resp.json()["detail"].lower()

    async def test_start_503_when_no_db(self):
        """oauth/start returns 503 when credential store is unavailable."""
        app = _build_app(None)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=None,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/oauth/start")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/connectors/spotify/oauth/callback
# ---------------------------------------------------------------------------


class TestSpotifyOAuthCallback:
    async def _start_flow(self, cred_store) -> str:
        """Helper: call oauth/start and return the state token."""
        app = _build_app(cred_store)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/oauth/start")
        assert resp.status_code == 200
        return resp.json()["state"]

    async def test_callback_success_stores_tokens(self, cred_store_client_id_only):
        """Callback exchanges code and stores tokens on success."""
        state = await self._start_flow(cred_store_client_id_only)
        app = _build_app(cred_store_client_id_only)

        with (
            patch(
                "butlers.api.routers.spotify._make_credential_store",
                return_value=cred_store_client_id_only,
            ),
            patch(_TOKEN_EXCHANGE_PATCH, new=AsyncMock(return_value=_FAKE_TOKENS)),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"code": "fake_code", "state": state},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "expires_at" in data
        # Verify correct token values were stored (not just key names)
        call_args = {
            call.args[0]: call.args[1] for call in cred_store_client_id_only.store.call_args_list
        }
        assert call_args.get("SPOTIFY_ACCESS_TOKEN") == _FAKE_TOKENS["access_token"]
        assert call_args.get("SPOTIFY_REFRESH_TOKEN") == _FAKE_TOKENS["refresh_token"]
        assert "SPOTIFY_TOKEN_EXPIRES_AT" in call_args

    async def test_callback_redirects_when_dashboard_url_set(self, cred_store_client_id_only):
        """Callback redirects to dashboard URL when OAUTH_DASHBOARD_URL is set."""
        state = await self._start_flow(cred_store_client_id_only)
        app = _build_app(cred_store_client_id_only)

        with (
            patch(
                "butlers.api.routers.spotify._make_credential_store",
                return_value=cred_store_client_id_only,
            ),
            patch(_TOKEN_EXCHANGE_PATCH, new=AsyncMock(return_value=_FAKE_TOKENS)),
            patch(
                "butlers.api.routers.spotify._get_dashboard_url",
                return_value="http://localhost:41173/settings",
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"code": "fake_code", "state": state},
                )

        assert resp.status_code == 302
        assert "spotify_connected=1" in resp.headers["location"]

    async def test_callback_400_on_invalid_state(self, cred_store_client_id_only):
        """Callback returns 400 when state token is invalid or expired."""
        app = _build_app(cred_store_client_id_only)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_client_id_only,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"code": "fake_code", "state": "invalid_state_token"},
                )

        assert resp.status_code == 400
        assert "state" in resp.json()["detail"].lower()

    async def test_callback_400_on_user_denial(self, cred_store_client_id_only):
        """Callback returns 400 (or redirects) when Spotify sends error=access_denied."""
        app = _build_app(cred_store_client_id_only)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_client_id_only,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"error": "access_denied"},
                )

        assert resp.status_code == 400
        assert "access_denied" in resp.json()["detail"]

    async def test_callback_400_on_missing_params(self, cred_store_client_id_only):
        """Callback returns 400 when neither code nor error is provided."""
        app = _build_app(cred_store_client_id_only)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_client_id_only,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/connectors/spotify/oauth/callback")

        assert resp.status_code == 400

    async def test_callback_502_on_exchange_failure(self, cred_store_client_id_only):
        """Callback returns 502 when Spotify token exchange fails."""
        from butlers.api.routers.spotify import _TokenExchangeError

        state = await self._start_flow(cred_store_client_id_only)
        app = _build_app(cred_store_client_id_only)

        with (
            patch(
                "butlers.api.routers.spotify._make_credential_store",
                return_value=cred_store_client_id_only,
            ),
            patch(
                _TOKEN_EXCHANGE_PATCH,
                new=AsyncMock(side_effect=_TokenExchangeError("Spotify error", 400)),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"code": "fake_code", "state": state},
                )

        assert resp.status_code == 502

    async def test_state_consumed_after_callback(self, cred_store_client_id_only):
        """State token is one-time-use: second callback call with same state fails."""
        state = await self._start_flow(cred_store_client_id_only)
        app = _build_app(cred_store_client_id_only)

        with (
            patch(
                "butlers.api.routers.spotify._make_credential_store",
                return_value=cred_store_client_id_only,
            ),
            patch(_TOKEN_EXCHANGE_PATCH, new=AsyncMock(return_value=_FAKE_TOKENS)),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                # First call succeeds
                r1 = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"code": "fake_code", "state": state},
                )
                assert r1.status_code == 200

                # Second call with same state should fail
                r2 = await client.get(
                    "/api/connectors/spotify/oauth/callback",
                    params={"code": "fake_code", "state": state},
                )
                assert r2.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/connectors/spotify/status
# ---------------------------------------------------------------------------


class TestSpotifyStatus:
    async def test_status_not_configured_when_no_credentials(self):
        """Status returns not_configured when no client_id or DB available."""
        app = _build_app(None)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=None,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        assert resp.json()["state"] == "not_configured"
        assert resp.json()["client_id_configured"] is False

    async def test_status_not_configured_when_no_client_id(self, cred_store_no_client_id):
        """Status returns not_configured when client_id is missing."""
        app = _build_app(cred_store_no_client_id)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_no_client_id,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        assert resp.json()["state"] == "not_configured"

    async def test_status_needs_auth_when_client_id_but_no_tokens(self, cred_store_client_id_only):
        """Status returns needs_auth when client_id is set but no tokens."""
        app = _build_app(cred_store_client_id_only)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_client_id_only,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "needs_auth"
        assert data["client_id_configured"] is True

    async def test_status_connected_when_me_succeeds(self, cred_store_connected):
        """Status returns connected when GET /me succeeds."""
        app = _build_app(cred_store_connected)
        with (
            patch(
                "butlers.api.routers.spotify._make_credential_store",
                return_value=cred_store_connected,
            ),
            patch(_FETCH_ME_PATCH, new=AsyncMock(return_value=_FAKE_ME)),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "connected"
        assert data["display_name"] == "Test User"
        assert data["email"] == "test@example.com"
        assert data["product"] == "premium"
        assert data["last_verified_at"] is not None

    async def test_status_disconnected_when_me_fails(self, cred_store_connected):
        """Status returns disconnected when access token is invalid."""
        app = _build_app(cred_store_connected)
        with (
            patch(
                "butlers.api.routers.spotify._make_credential_store",
                return_value=cred_store_connected,
            ),
            patch(_FETCH_ME_PATCH, new=AsyncMock(return_value=None)),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        assert resp.json()["state"] == "disconnected"


# ---------------------------------------------------------------------------
# POST /api/connectors/spotify/disconnect
# ---------------------------------------------------------------------------


class TestSpotifyDisconnect:
    async def test_disconnect_deletes_tokens(self, cred_store_connected):
        """Disconnect deletes access, refresh, and expiry tokens."""
        app = _build_app(cred_store_connected)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_connected,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/disconnect")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Ensure delete was called for all 3 token keys (not client_id)
        deleted_keys = [call.args[0] for call in cred_store_connected.delete.call_args_list]
        assert "SPOTIFY_ACCESS_TOKEN" in deleted_keys
        assert "SPOTIFY_REFRESH_TOKEN" in deleted_keys
        assert "SPOTIFY_TOKEN_EXPIRES_AT" in deleted_keys
        assert "SPOTIFY_CLIENT_ID" not in deleted_keys

    async def test_disconnect_idempotent_when_no_db(self):
        """Disconnect returns success even when credential store is unavailable."""
        app = _build_app(None)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=None,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/connectors/spotify/disconnect")

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_disconnect_preserves_client_id(self, cred_store_connected):
        """Disconnect does not delete SPOTIFY_CLIENT_ID."""
        app = _build_app(cred_store_connected)
        with patch(
            "butlers.api.routers.spotify._make_credential_store",
            return_value=cred_store_connected,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post("/api/connectors/spotify/disconnect")

        deleted_keys = [call.args[0] for call in cred_store_connected.delete.call_args_list]
        assert "SPOTIFY_CLIENT_ID" not in deleted_keys
