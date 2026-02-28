"""Tests for the CredentialStore + contact_info-backed Google credential functions.

Covers:
- store_google_credentials() via CredentialStore + pool (contact_info)
- load_google_credentials() via CredentialStore + pool (contact_info)
- store_app_credentials() via CredentialStore
- load_app_credentials() via CredentialStore + pool
- delete_google_credentials() via CredentialStore + pool
- resolve_google_credentials() via CredentialStore + pool
- KEY_* constants exported
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import CredentialStore
from butlers.google_credentials import (
    CONTACT_INFO_REFRESH_TOKEN,
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    KEY_REFRESH_TOKEN,
    KEY_SCOPES,
    InvalidGoogleCredentialsError,
    MissingGoogleCredentialsError,
    delete_google_credentials,
    load_app_credentials,
    load_google_credentials,
    resolve_google_credentials,
    store_app_credentials,
    store_google_credentials,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool_with_values(key_to_value: dict[str, str | None]) -> MagicMock:
    """Build a pool mock that returns different values for different secret keys.

    When CredentialStore.load() is called with a given key, the corresponding
    value from *key_to_value* is returned (or None when the key is absent).
    """

    async def _fetchrow(query: str, key: str) -> MagicMock | None:
        val = key_to_value.get(key)
        if val is None:
            return None
        row = MagicMock()
        row.__getitem__ = lambda self, k: val if k == "secret_value" else None
        return row

    async def _execute(*args, **kwargs) -> str:
        return "INSERT 0 1"

    conn = MagicMock()
    conn.fetchrow = _fetchrow
    conn.execute = _execute

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire.return_value = cm
    pool._conn = conn
    return pool


def _make_empty_pool() -> MagicMock:
    """Pool whose connection always returns None from fetchrow."""
    return _make_pool_with_values({})


def _make_contact_info_pool(value: str | None = None) -> MagicMock:
    """Build a pool mock for resolve_owner_contact_info calls."""
    return MagicMock()


# ---------------------------------------------------------------------------
# KEY_* constants
# ---------------------------------------------------------------------------


class TestKeyConstants:
    def test_key_client_id(self):
        assert KEY_CLIENT_ID == "GOOGLE_OAUTH_CLIENT_ID"

    def test_key_client_secret(self):
        assert KEY_CLIENT_SECRET == "GOOGLE_OAUTH_CLIENT_SECRET"

    def test_key_refresh_token(self):
        assert KEY_REFRESH_TOKEN == "GOOGLE_REFRESH_TOKEN"

    def test_key_scopes(self):
        assert KEY_SCOPES == "GOOGLE_OAUTH_SCOPES"

    def test_contact_info_refresh_token(self):
        assert CONTACT_INFO_REFRESH_TOKEN == "google_oauth_refresh"


# ---------------------------------------------------------------------------
# store_google_credentials — CredentialStore path
# ---------------------------------------------------------------------------


class TestStoreGoogleCredentialsWithCredentialStore:
    async def test_stores_app_keys_in_butler_secrets(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch.object(store, "store", new_callable=AsyncMock) as mock_store,
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_upsert,
        ):
            await store_google_credentials(
                store,
                pool=ci_pool,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
                scope="https://www.googleapis.com/auth/gmail.readonly",
            )

        keys_stored = [call_args[0][0] for call_args in mock_store.call_args_list]
        assert KEY_CLIENT_ID in keys_stored
        assert KEY_CLIENT_SECRET in keys_stored
        assert KEY_SCOPES in keys_stored
        # Refresh token should NOT be in butler_secrets
        assert KEY_REFRESH_TOKEN not in keys_stored
        # Refresh token should go to contact_info
        mock_upsert.assert_awaited_once_with(ci_pool, "google_oauth_refresh", "rtoken")

    async def test_client_id_stored_as_not_sensitive(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock) as mock_store,
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        client_id_call = next(c for c in mock_store.call_args_list if c[0][0] == KEY_CLIENT_ID)
        assert client_id_call[1].get("is_sensitive") is False

    async def test_client_secret_stored_as_sensitive(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock) as mock_store,
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        secret_call = next(c for c in mock_store.call_args_list if c[0][0] == KEY_CLIENT_SECRET)
        assert secret_call[1].get("is_sensitive") is True

    async def test_scope_not_stored_when_none(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock) as mock_store,
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
                scope=None,
            )

        keys_stored = [c[0][0] for c in mock_store.call_args_list]
        assert KEY_SCOPES not in keys_stored

    async def test_uses_google_category(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock) as mock_store,
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        for c in mock_store.call_args_list:
            assert c[1].get("category") == "google"

    async def test_validates_empty_client_id_raises(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        with pytest.raises(Exception):  # pydantic ValidationError
            await store_google_credentials(
                store, client_id="", client_secret="s", refresh_token="r"
            )

    async def test_no_pool_skips_contact_info_write(self) -> None:
        """When pool=None, refresh token is not persisted to contact_info."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock),
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
            ) as mock_upsert,
        ):
            await store_google_credentials(
                store,
                pool=None,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        mock_upsert.assert_not_awaited()


# ---------------------------------------------------------------------------
# load_google_credentials — CredentialStore + contact_info path
# ---------------------------------------------------------------------------


class TestLoadGoogleCredentialsWithCredentialStore:
    async def test_returns_none_when_no_keys_stored(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        result = await load_google_credentials(store)
        assert result is None

    async def test_returns_credentials_with_refresh_from_contact_info(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_SCOPES: "gmail calendar",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with patch(
            "butlers.google_credentials.resolve_owner_contact_info",
            new_callable=AsyncMock,
            return_value="rtoken-from-ci",
        ):
            creds = await load_google_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.client_id == "cid"
        assert creds.client_secret == "csecret"
        assert creds.refresh_token == "rtoken-from-ci"
        assert creds.scope == "gmail calendar"

    async def test_falls_back_to_butler_secrets_refresh_token(self) -> None:
        """When contact_info has no refresh token, fall back to butler_secrets."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_REFRESH_TOKEN: "rtoken-from-bs",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with patch(
            "butlers.google_credentials.resolve_owner_contact_info",
            new_callable=AsyncMock,
            return_value=None,
        ):
            creds = await load_google_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.refresh_token == "rtoken-from-bs"

    async def test_scope_is_none_when_key_absent(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_REFRESH_TOKEN: "rtoken",
            }
        )
        store = CredentialStore(pool)
        creds = await load_google_credentials(store)
        assert creds is not None
        assert creds.scope is None

    async def test_raises_when_some_required_keys_missing(self) -> None:
        """If only some keys are present (partial store), raise InvalidGoogleCredentialsError."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                # client_secret and refresh_token missing
            }
        )
        store = CredentialStore(pool)
        with pytest.raises(InvalidGoogleCredentialsError) as exc_info:
            await load_google_credentials(store)
        assert "client_secret" in str(exc_info.value) or "refresh_token" in str(exc_info.value)

    async def test_repr_does_not_leak_secrets(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "NEVER-REVEAL",
                KEY_REFRESH_TOKEN: "ALSO-NEVER-REVEAL",
            }
        )
        store = CredentialStore(pool)
        creds = await load_google_credentials(store)
        assert creds is not None
        r = repr(creds)
        assert "NEVER-REVEAL" not in r
        assert "ALSO-NEVER-REVEAL" not in r


# ---------------------------------------------------------------------------
# store_app_credentials — CredentialStore path
# ---------------------------------------------------------------------------


class TestStoreAppCredentialsWithCredentialStore:
    async def test_stores_client_id_and_secret(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
            await store_app_credentials(store, client_id="cid", client_secret="csecret")

        keys_stored = [c[0][0] for c in mock_store.call_args_list]
        assert KEY_CLIENT_ID in keys_stored
        assert KEY_CLIENT_SECRET in keys_stored

    async def test_does_not_overwrite_refresh_token(self) -> None:
        """store_app_credentials should only write client_id and client_secret."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
            await store_app_credentials(store, client_id="cid", client_secret="csecret")

        keys_stored = [c[0][0] for c in mock_store.call_args_list]
        assert KEY_REFRESH_TOKEN not in keys_stored

    async def test_empty_client_id_raises(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        with pytest.raises(ValueError, match="client_id"):
            await store_app_credentials(store, client_id="", client_secret="s")

    async def test_empty_client_secret_raises(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        with pytest.raises(ValueError, match="client_secret"):
            await store_app_credentials(store, client_id="id", client_secret="")


# ---------------------------------------------------------------------------
# load_app_credentials — CredentialStore + contact_info path
# ---------------------------------------------------------------------------


class TestLoadAppCredentialsWithCredentialStore:
    async def test_returns_none_when_no_keys_stored(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        result = await load_app_credentials(store)
        assert result is None

    async def test_returns_app_credentials_when_present(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
            }
        )
        store = CredentialStore(pool)
        creds = await load_app_credentials(store)
        assert creds is not None
        assert creds.client_id == "cid"
        assert creds.client_secret == "csecret"
        assert creds.refresh_token is None

    async def test_returns_refresh_token_from_contact_info(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_SCOPES: "gmail",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with patch(
            "butlers.google_credentials.resolve_owner_contact_info",
            new_callable=AsyncMock,
            return_value="rtoken-ci",
        ):
            creds = await load_app_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.refresh_token == "rtoken-ci"
        assert creds.scope == "gmail"

    async def test_falls_back_to_butler_secrets_refresh_token(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_REFRESH_TOKEN: "rtoken-bs",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with patch(
            "butlers.google_credentials.resolve_owner_contact_info",
            new_callable=AsyncMock,
            return_value=None,
        ):
            creds = await load_app_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.refresh_token == "rtoken-bs"


# ---------------------------------------------------------------------------
# delete_google_credentials — CredentialStore + contact_info path
# ---------------------------------------------------------------------------


class TestDeleteGoogleCredentialsWithCredentialStore:
    async def test_deletes_all_keys_and_contact_info(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch.object(
                store, "delete", new_callable=AsyncMock, return_value=True
            ) as mock_del,
            patch(
                "butlers.google_credentials.delete_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_ci_del,
        ):
            result = await delete_google_credentials(store, pool=ci_pool)

        keys_deleted = [c[0][0] for c in mock_del.call_args_list]
        assert KEY_CLIENT_ID in keys_deleted
        assert KEY_CLIENT_SECRET in keys_deleted
        assert KEY_REFRESH_TOKEN in keys_deleted
        assert KEY_SCOPES in keys_deleted
        mock_ci_del.assert_awaited_once_with(ci_pool, "google_oauth_refresh")
        assert result is True

    async def test_returns_true_when_something_deleted(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        delete_results = {KEY_CLIENT_ID: True}

        async def _delete(key: str) -> bool:
            return delete_results.get(key, False)

        with (
            patch.object(store, "delete", side_effect=_delete),
            patch(
                "butlers.google_credentials.delete_owner_contact_info",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await delete_google_credentials(store, pool=MagicMock())

        assert result is True

    async def test_returns_false_when_nothing_deleted(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "delete", new_callable=AsyncMock, return_value=False),
            patch(
                "butlers.google_credentials.delete_owner_contact_info",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await delete_google_credentials(store, pool=MagicMock())

        assert result is False


# ---------------------------------------------------------------------------
# resolve_google_credentials — CredentialStore + contact_info path
# ---------------------------------------------------------------------------


class TestResolveGoogleCredentialsWithCredentialStore:
    async def test_resolves_from_credential_store(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
                KEY_REFRESH_TOKEN: "db-rtoken",
            }
        )
        store = CredentialStore(pool)
        creds = await resolve_google_credentials(store, caller="test")
        assert creds.client_id == "db-cid"

    async def test_raises_when_no_db_data(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        with pytest.raises(MissingGoogleCredentialsError):
            await resolve_google_credentials(store, caller="test")

    async def test_raises_when_missing_db_data_includes_caller(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        with pytest.raises(MissingGoogleCredentialsError) as exc_info:
            await resolve_google_credentials(store, caller="test-caller")
        assert "test-caller" in str(exc_info.value)

    async def test_raises_when_db_has_partial_data(self) -> None:
        pool = _make_pool_with_values({KEY_CLIENT_ID: "cid"})
        store = CredentialStore(pool)
        with pytest.raises(MissingGoogleCredentialsError):
            await resolve_google_credentials(store, caller="test")

    async def test_resolves_refresh_from_contact_info(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with patch(
            "butlers.google_credentials.resolve_owner_contact_info",
            new_callable=AsyncMock,
            return_value="ci-rtoken",
        ):
            creds = await resolve_google_credentials(store, pool=ci_pool, caller="test")

        assert creds.refresh_token == "ci-rtoken"
