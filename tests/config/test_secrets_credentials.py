"""Tests for Google credential management API endpoints and credential helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.credential_store import CredentialStore
from butlers.google_credentials import (
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    GoogleAppCredentials,
    MissingGoogleCredentialsError,
    load_app_credentials,
    resolve_google_credentials,
    store_app_credentials,
)

pytestmark = pytest.mark.unit

_SHARED_CREDS = {
    "client_id": "shared-client-id.apps.googleusercontent.com",
    "client_secret": "shared-client-secret-abc",
    "refresh_token": "1//shared-refresh-token-xyz",
}
_OAUTH_BOOTSTRAP_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": _SHARED_CREDS["client_id"],
    "GOOGLE_OAUTH_CLIENT_SECRET": _SHARED_CREDS["client_secret"],
}
_LEGACY_GMAIL_ENV = {
    "GMAIL_CLIENT_ID": _SHARED_CREDS["client_id"],
    "GMAIL_CLIENT_SECRET": _SHARED_CREDS["client_secret"],
    "GMAIL_REFRESH_TOKEN": _SHARED_CREDS["refresh_token"],
}


def _make_pool_with_values(key_to_value: dict[str, str | None]) -> MagicMock:
    async def _fetchrow(query: str, key: str):
        val = key_to_value.get(key)
        if val is None:
            return None
        row = MagicMock()
        row.__getitem__ = lambda self, k: val if k == "secret_value" else None
        return row

    async def _execute(*args, **kwargs):
        return "INSERT 0 1"

    conn = MagicMock()
    conn.fetchrow = _fetchrow
    conn.execute = _execute
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = cm
    return pool


def _make_credential_store(stored: dict[str, str] | None = None) -> AsyncMock:
    stored = stored or {}
    store = AsyncMock(spec=CredentialStore)
    store.load.side_effect = lambda key: stored.get(key)
    return store


def _make_db_manager(row: dict | None = None, execute_result: str = "DELETE 0"):
    pool = MagicMock()
    conn = AsyncMock()
    if row is None:
        conn.fetchrow.return_value = None
    else:
        record = MagicMock()
        record.__getitem__ = lambda self, key: row[key]
        conn.fetchrow.return_value = record
    conn.execute.return_value = execute_result
    conn.fetch.return_value = []
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = cm
    db_manager = MagicMock()
    db_manager.butler_names = ["test-butler"]
    db_manager.pool.return_value = pool
    db_manager.credential_shared_pool.return_value = pool
    return db_manager, conn


def _make_api_client(db_manager=None) -> TestClient:
    app = create_app()
    if db_manager is not None:
        from butlers.api.routers import oauth

        app.dependency_overrides[oauth._get_db_manager] = lambda: db_manager
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Model contracts: from_env factories removed
# ---------------------------------------------------------------------------


def test_from_env_factories_removed() -> None:
    from butlers.google_credentials import GoogleCredentials

    assert not hasattr(GoogleCredentials, "from_env")
    from butlers.modules.calendar import _GoogleOAuthCredentials

    assert not hasattr(_GoogleOAuthCredentials, "from_env")


# ---------------------------------------------------------------------------
# resolve_google_credentials is DB-only; Gmail connector accepts injected creds
# ---------------------------------------------------------------------------


async def test_resolve_raises_when_db_empty() -> None:
    """resolve_google_credentials ignores env vars; DB is required."""
    for env_vars in [_LEGACY_GMAIL_ENV, _OAUTH_BOOTSTRAP_ENV]:
        store = CredentialStore(_make_pool_with_values({}))
        with patch("os.environ", {**env_vars}):
            with pytest.raises(MissingGoogleCredentialsError):
                await resolve_google_credentials(store, caller="test")


def test_gmail_connector_accepts_injected_credentials() -> None:
    from butlers.connectors.gmail import GmailConnectorConfig

    env = {
        "SWITCHBOARD_MCP_URL": "http://localhost:9000/mcp",
        "GMAIL_USER_EMAIL": "test@gmail.com",
        **_OAUTH_BOOTSTRAP_ENV,
    }
    with patch("os.environ", env):
        config = GmailConnectorConfig.from_env(
            gmail_client_id=_SHARED_CREDS["client_id"],
            gmail_client_secret=_SHARED_CREDS["client_secret"],
            gmail_refresh_token=_SHARED_CREDS["refresh_token"],
        )
    assert config.gmail_client_id == _SHARED_CREDS["client_id"]


# ---------------------------------------------------------------------------
# store_app_credentials / load_app_credentials helpers
# ---------------------------------------------------------------------------


async def test_store_and_load_app_credentials() -> None:
    """Store: strips whitespace, raises on empty. Load: None when incomplete, partial ok."""
    store = _make_credential_store()
    await store_app_credentials(store, client_id="  my-id  ", client_secret="  my-secret  ")
    assert store.store.call_args_list[0].args[1] == "my-id"
    assert store.store.call_args_list[1].args[1] == "my-secret"

    for cid, cs, match in [("", "s", "client_id"), ("id", "", "client_secret")]:
        with pytest.raises(ValueError, match=match):
            await store_app_credentials(_make_credential_store(), client_id=cid, client_secret=cs)

    assert await load_app_credentials(_make_credential_store(stored={})) is None
    assert await load_app_credentials(_make_credential_store(stored={KEY_CLIENT_ID: "id"})) is None
    result = await load_app_credentials(
        _make_credential_store(stored={KEY_CLIENT_ID: "id", KEY_CLIENT_SECRET: "s"})
    )
    assert result is not None and result.refresh_token is None


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def test_api_credentials_endpoints() -> None:
    """Upsert/delete/get/status endpoints: success, validation, no-DB 503, no secrets leaked."""
    db_manager, _ = _make_db_manager()
    # upsert success
    resp = _make_api_client(db_manager).put(
        "/api/oauth/google/credentials",
        json={"client_id": "my-client-id", "client_secret": "my-secret"},
    )
    assert resp.status_code == 200 and resp.json()["success"] is True

    # invalid input
    assert (
        _make_api_client(db_manager)
        .put("/api/oauth/google/credentials", json={"client_id": "", "client_secret": "s"})
        .status_code
        == 422
    )
    assert (
        _make_api_client(db_manager)
        .put("/api/oauth/google/credentials", json={"client_id": "id", "client_secret": ""})
        .status_code
        == 422
    )

    # no db → 503
    assert (
        _make_api_client(None)
        .put("/api/oauth/google/credentials", json={"client_id": "id", "client_secret": "s"})
        .status_code
        == 503
    )
    assert _make_api_client(None).delete("/api/oauth/google/credentials").status_code == 503
    assert _make_api_client(None).get("/api/oauth/google/credentials").status_code == 503

    # delete success
    db2, _ = _make_db_manager(execute_result="DELETE 1")
    resp_del = _make_api_client(db2).delete("/api/oauth/google/credentials")
    assert resp_del.status_code == 200 and resp_del.json()["deleted"] is True

    # get status: no secrets in response body
    client = _make_api_client(_make_db_manager()[0])
    with (
        patch("butlers.api.routers.oauth._check_google_credential_status") as mock_status,
        patch(
            "butlers.api.routers.oauth.load_app_credentials",
            return_value=GoogleAppCredentials(
                client_id="my-id",
                client_secret="SUPER_SECRET",
                refresh_token="TOP_SECRET",
                scope="gmail",
            ),
        ),
    ):
        from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus

        mock_status.return_value = OAuthCredentialStatus(state=OAuthCredentialState.connected)
        resp2 = client.get("/api/oauth/google/credentials")
    assert resp2.status_code == 200
    assert resp2.json()["client_id_configured"] is True
    assert "SUPER_SECRET" not in resp2.text and "TOP_SECRET" not in resp2.text
