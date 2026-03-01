"""Tests for Google credential management API endpoints and new credential helpers.

Covers:
- store_app_credentials() — partial upsert (client_id + client_secret)
- load_app_credentials() — reads partial/full credentials from DB
- delete_google_credentials() — removes stored credentials
- PUT /api/oauth/google/credentials — upsert endpoint
- DELETE /api/oauth/google/credentials — delete endpoint
- GET /api/oauth/google/credentials — masked status endpoint
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.credential_store import CredentialStore  # noqa: F401 — used by spec= in mock
from butlers.google_credentials import (
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    KEY_SCOPES,
    GoogleAppCredentials,
    delete_google_credentials,
    load_app_credentials,
    store_app_credentials,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_credential_store(
    stored: dict[str, str] | None = None,
    delete_returns: bool = False,
) -> AsyncMock:
    """Build a fake CredentialStore mock.

    Parameters
    ----------
    stored:
        Dict of key→value pairs that ``load()`` should return.
        Keys not present return ``None``.
    delete_returns:
        Value that ``delete()`` returns for any key.
    """
    stored = stored or {}
    store = AsyncMock(spec=CredentialStore)
    store.load.side_effect = lambda key: stored.get(key)
    store.delete.return_value = delete_returns
    return store


def _make_db_manager(row: dict | None = None, execute_result: str = "DELETE 0") -> MagicMock:
    """Build a fake DatabaseManager mock."""
    pool = MagicMock()
    conn = AsyncMock()
    if row is None:
        conn.fetchrow.return_value = None
    else:
        record = MagicMock()
        record.__getitem__ = lambda self, key: row[key]
        conn.fetchrow.return_value = record
    conn.execute.return_value = execute_result

    # pool.acquire() returns async context manager yielding conn
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = cm

    db_manager = MagicMock()
    db_manager.butler_names = ["test-butler"]
    db_manager.pool.return_value = pool
    db_manager.credential_shared_pool.return_value = pool
    return db_manager, conn


# ---------------------------------------------------------------------------
# store_app_credentials() tests
# ---------------------------------------------------------------------------


class TestStoreAppCredentials:
    async def test_stores_client_id_and_secret(self) -> None:
        store = _make_credential_store()
        await store_app_credentials(store, client_id="test-id", client_secret="test-secret")
        assert store.store.call_count == 2
        # Verify client_id stored
        id_call = store.store.call_args_list[0]
        assert id_call.args[0] == KEY_CLIENT_ID
        assert id_call.args[1] == "test-id"
        # Verify client_secret stored
        secret_call = store.store.call_args_list[1]
        assert secret_call.args[0] == KEY_CLIENT_SECRET
        assert secret_call.args[1] == "test-secret"

    async def test_strips_whitespace(self) -> None:
        store = _make_credential_store()
        await store_app_credentials(store, client_id="  test-id  ", client_secret="  secret  ")
        id_call = store.store.call_args_list[0]
        assert id_call.args[1] == "test-id"
        secret_call = store.store.call_args_list[1]
        assert secret_call.args[1] == "secret"

    async def test_empty_client_id_raises(self) -> None:
        store = _make_credential_store()
        with pytest.raises(ValueError, match="client_id"):
            await store_app_credentials(store, client_id="", client_secret="secret")

    async def test_empty_client_secret_raises(self) -> None:
        store = _make_credential_store()
        with pytest.raises(ValueError, match="client_secret"):
            await store_app_credentials(store, client_id="id", client_secret="")

    async def test_does_not_log_secret(self, caplog: pytest.LogCaptureFixture) -> None:
        store = _make_credential_store()
        with caplog.at_level("DEBUG"):
            await store_app_credentials(store, client_id="my-id", client_secret="my-super-secret")
        for record in caplog.records:
            assert "my-super-secret" not in record.getMessage()


# ---------------------------------------------------------------------------
# load_app_credentials() tests
# ---------------------------------------------------------------------------


class TestLoadAppCredentials:
    async def test_returns_none_when_no_row(self) -> None:
        store = _make_credential_store(stored={})
        result = await load_app_credentials(store)
        assert result is None

    async def test_returns_full_credentials(self) -> None:
        store = _make_credential_store(
            stored={
                KEY_CLIENT_ID: "test-id",
                KEY_CLIENT_SECRET: "test-secret",
                KEY_SCOPES: "gmail",
            }
        )
        with patch(
            "butlers.google_credentials.resolve_owner_contact_info",
            return_value="test-refresh",
        ):
            pool = MagicMock()
            result = await load_app_credentials(store, pool=pool)
        assert isinstance(result, GoogleAppCredentials)
        assert result.client_id == "test-id"
        assert result.client_secret == "test-secret"
        assert result.refresh_token == "test-refresh"
        assert result.scope == "gmail"

    async def test_returns_partial_credentials_without_refresh_token(self) -> None:
        store = _make_credential_store(
            stored={
                KEY_CLIENT_ID: "test-id",
                KEY_CLIENT_SECRET: "test-secret",
            }
        )
        result = await load_app_credentials(store)
        assert result is not None
        assert result.client_id == "test-id"
        assert result.refresh_token is None

    async def test_returns_none_when_client_id_missing(self) -> None:
        store = _make_credential_store(
            stored={
                KEY_CLIENT_SECRET: "test-secret",
            }
        )
        result = await load_app_credentials(store)
        assert result is None

    async def test_returns_none_when_client_secret_missing(self) -> None:
        store = _make_credential_store(
            stored={
                KEY_CLIENT_ID: "id",
            }
        )
        result = await load_app_credentials(store)
        assert result is None


# ---------------------------------------------------------------------------
# delete_google_credentials() tests
# ---------------------------------------------------------------------------


class TestDeleteGoogleCredentials:
    async def test_returns_true_when_row_deleted(self) -> None:
        store = _make_credential_store(delete_returns=True)
        result = await delete_google_credentials(store)
        assert result is True

    async def test_returns_false_when_no_row(self) -> None:
        store = _make_credential_store(delete_returns=False)
        result = await delete_google_credentials(store)
        assert result is False


# ---------------------------------------------------------------------------
# API endpoint tests: PUT /api/oauth/google/credentials
# ---------------------------------------------------------------------------


class TestUpsertCredentialsEndpoint:
    def _make_client(self, db_manager=None):
        app = create_app()
        if db_manager is not None:
            from butlers.api.routers import oauth

            app.dependency_overrides[oauth._get_db_manager] = lambda: db_manager
        return TestClient(app, raise_server_exceptions=False)

    def test_upsert_success(self) -> None:
        db_manager, conn = _make_db_manager()
        client = self._make_client(db_manager)
        response = client.put(
            "/api/oauth/google/credentials",
            json={"client_id": "my-client-id", "client_secret": "my-client-secret"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_upsert_empty_client_id_returns_422(self) -> None:
        db_manager, conn = _make_db_manager()
        client = self._make_client(db_manager)
        response = client.put(
            "/api/oauth/google/credentials",
            json={"client_id": "", "client_secret": "secret"},
        )
        assert response.status_code == 422

    def test_upsert_empty_client_secret_returns_422(self) -> None:
        db_manager, conn = _make_db_manager()
        client = self._make_client(db_manager)
        response = client.put(
            "/api/oauth/google/credentials",
            json={"client_id": "id", "client_secret": ""},
        )
        assert response.status_code == 422

    def test_upsert_no_db_returns_503(self) -> None:
        client = self._make_client(db_manager=None)
        response = client.put(
            "/api/oauth/google/credentials",
            json={"client_id": "id", "client_secret": "secret"},
        )
        assert response.status_code == 503

    def test_upsert_no_butler_pools_returns_503(self) -> None:
        db_manager = MagicMock()
        db_manager.butler_names = []
        db_manager.credential_shared_pool.side_effect = KeyError("no shared pool")
        app = create_app()
        from butlers.api.routers import oauth

        app.dependency_overrides[oauth._get_db_manager] = lambda: db_manager
        client = TestClient(app, raise_server_exceptions=False)
        response = client.put(
            "/api/oauth/google/credentials",
            json={"client_id": "id", "client_secret": "secret"},
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# API endpoint tests: DELETE /api/oauth/google/credentials
# ---------------------------------------------------------------------------


class TestDeleteCredentialsEndpoint:
    def _make_client(self, db_manager=None):
        app = create_app()
        if db_manager is not None:
            from butlers.api.routers import oauth

            app.dependency_overrides[oauth._get_db_manager] = lambda: db_manager
        return TestClient(app, raise_server_exceptions=False)

    def test_delete_when_row_exists(self) -> None:
        db_manager, conn = _make_db_manager(execute_result="DELETE 1")
        client = self._make_client(db_manager)
        response = client.delete("/api/oauth/google/credentials")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted"] is True

    def test_delete_when_no_row(self) -> None:
        db_manager, conn = _make_db_manager(execute_result="DELETE 0")
        client = self._make_client(db_manager)
        response = client.delete("/api/oauth/google/credentials")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted"] is False

    def test_delete_no_db_returns_503(self) -> None:
        client = self._make_client(db_manager=None)
        response = client.delete("/api/oauth/google/credentials")
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# API endpoint tests: GET /api/oauth/google/credentials
# ---------------------------------------------------------------------------


class TestGetCredentialStatusEndpoint:
    def _make_client(self, row=None, db_manager=None):
        app = create_app()
        if db_manager is not None:
            from butlers.api.routers import oauth

            app.dependency_overrides[oauth._get_db_manager] = lambda: db_manager
        return TestClient(app, raise_server_exceptions=False)

    def test_get_status_no_db_returns_503(self) -> None:
        client = self._make_client(db_manager=None)
        response = client.get("/api/oauth/google/credentials")
        assert response.status_code == 503

    def test_get_status_no_credentials_stored(self) -> None:
        db_manager, conn = _make_db_manager(row=None)
        client = self._make_client(db_manager=db_manager)

        with patch(
            "butlers.api.routers.oauth._check_google_credential_status",
        ) as mock_status:
            from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus

            mock_status.return_value = OAuthCredentialStatus(
                state=OAuthCredentialState.not_configured,
                remediation="Configure credentials.",
            )
            response = client.get("/api/oauth/google/credentials")

        assert response.status_code == 200
        data = response.json()
        assert data["client_id_configured"] is False
        assert data["client_secret_configured"] is False
        assert data["refresh_token_present"] is False
        assert data["oauth_health"] == "not_configured"

    def test_get_status_with_app_credentials_only(self) -> None:
        from butlers.google_credentials import GoogleAppCredentials

        db_manager, _ = _make_db_manager(row=None)
        client = self._make_client(db_manager=db_manager)

        with (
            patch(
                "butlers.api.routers.oauth._check_google_credential_status",
            ) as mock_status,
            patch(
                "butlers.api.routers.oauth.load_app_credentials",
                return_value=GoogleAppCredentials(
                    client_id="my-id",
                    client_secret="my-secret",
                ),
            ),
        ):
            from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus

            mock_status.return_value = OAuthCredentialStatus(
                state=OAuthCredentialState.not_configured,
                remediation="No refresh token.",
            )
            response = client.get("/api/oauth/google/credentials")

        assert response.status_code == 200
        data = response.json()
        assert data["client_id_configured"] is True
        assert data["client_secret_configured"] is True
        assert data["refresh_token_present"] is False

    def test_get_status_fully_configured(self) -> None:
        from butlers.google_credentials import GoogleAppCredentials

        db_manager, _ = _make_db_manager(row=None)
        client = self._make_client(db_manager=db_manager)

        with (
            patch(
                "butlers.api.routers.oauth._check_google_credential_status",
            ) as mock_status,
            patch(
                "butlers.api.routers.oauth.load_app_credentials",
                return_value=GoogleAppCredentials(
                    client_id="my-id",
                    client_secret="my-secret",
                    refresh_token="my-refresh",
                    scope="gmail calendar",
                ),
            ),
        ):
            from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus

            mock_status.return_value = OAuthCredentialStatus(
                state=OAuthCredentialState.connected,
            )
            response = client.get("/api/oauth/google/credentials")

        assert response.status_code == 200
        data = response.json()
        assert data["client_id_configured"] is True
        assert data["client_secret_configured"] is True
        assert data["refresh_token_present"] is True
        assert data["scope"] == "gmail calendar"
        assert data["oauth_health"] == "connected"

    def test_secret_values_not_returned(self) -> None:
        """Ensure secret values (client_secret, refresh_token) are never in the response."""
        from butlers.google_credentials import GoogleAppCredentials

        db_manager, _ = _make_db_manager(row=None)
        client = self._make_client(db_manager=db_manager)

        with (
            patch(
                "butlers.api.routers.oauth._check_google_credential_status",
            ) as mock_status,
            patch(
                "butlers.api.routers.oauth.load_app_credentials",
                return_value=GoogleAppCredentials(
                    client_id="my-id",
                    client_secret="SUPER_SECRET_VALUE",
                    refresh_token="TOP_SECRET_REFRESH",
                ),
            ),
        ):
            from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus

            mock_status.return_value = OAuthCredentialStatus(
                state=OAuthCredentialState.connected,
            )
            response = client.get("/api/oauth/google/credentials")

        response_text = response.text
        assert "SUPER_SECRET_VALUE" not in response_text
        assert "TOP_SECRET_REFRESH" not in response_text
