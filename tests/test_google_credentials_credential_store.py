"""Tests for the CredentialStore-backed Google credential functions.

Covers:
- store_google_credentials() via CredentialStore
- load_google_credentials() via CredentialStore
- store_app_credentials() via CredentialStore
- load_app_credentials() via CredentialStore
- delete_google_credentials() via CredentialStore
- resolve_google_credentials() DB-first via CredentialStore + env fallback
- KEY_* constants exported
"""

from __future__ import annotations

import unittest.mock as mock
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import CredentialStore
from butlers.google_credentials import (
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

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


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


# ---------------------------------------------------------------------------
# store_google_credentials — CredentialStore path
# ---------------------------------------------------------------------------


class TestStoreGoogleCredentialsWithCredentialStore:
    async def test_stores_all_four_keys(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
                scope="https://www.googleapis.com/auth/gmail.readonly",
            )

        keys_stored = [call_args[0][0] for call_args in mock_store.call_args_list]
        assert KEY_CLIENT_ID in keys_stored
        assert KEY_CLIENT_SECRET in keys_stored
        assert KEY_REFRESH_TOKEN in keys_stored
        assert KEY_SCOPES in keys_stored

    async def test_client_id_stored_as_not_sensitive(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
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

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        secret_call = next(c for c in mock_store.call_args_list if c[0][0] == KEY_CLIENT_SECRET)
        assert secret_call[1].get("is_sensitive") is True

    async def test_refresh_token_stored_as_sensitive(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
            await store_google_credentials(
                store,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        rt_call = next(c for c in mock_store.call_args_list if c[0][0] == KEY_REFRESH_TOKEN)
        assert rt_call[1].get("is_sensitive") is True

    async def test_scope_not_stored_when_none(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
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

        with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
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


# ---------------------------------------------------------------------------
# load_google_credentials — CredentialStore path
# ---------------------------------------------------------------------------


class TestLoadGoogleCredentialsWithCredentialStore:
    async def test_returns_none_when_no_keys_stored(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        result = await load_google_credentials(store)
        assert result is None

    async def test_returns_credentials_when_all_keys_present(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_REFRESH_TOKEN: "rtoken",
                KEY_SCOPES: "gmail calendar",
            }
        )
        store = CredentialStore(pool)
        creds = await load_google_credentials(store)
        assert creds is not None
        assert creds.client_id == "cid"
        assert creds.client_secret == "csecret"
        assert creds.refresh_token == "rtoken"
        assert creds.scope == "gmail calendar"

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
# load_app_credentials — CredentialStore path
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

    async def test_returns_refresh_token_when_stored(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_REFRESH_TOKEN: "rtoken",
                KEY_SCOPES: "gmail",
            }
        )
        store = CredentialStore(pool)
        creds = await load_app_credentials(store)
        assert creds is not None
        assert creds.refresh_token == "rtoken"
        assert creds.scope == "gmail"


# ---------------------------------------------------------------------------
# delete_google_credentials — CredentialStore path
# ---------------------------------------------------------------------------


class TestDeleteGoogleCredentialsWithCredentialStore:
    async def test_deletes_all_four_keys(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "delete", new_callable=AsyncMock, return_value=True) as mock_del:
            await delete_google_credentials(store)

        keys_deleted = [c[0][0] for c in mock_del.call_args_list]
        assert KEY_CLIENT_ID in keys_deleted
        assert KEY_CLIENT_SECRET in keys_deleted
        assert KEY_REFRESH_TOKEN in keys_deleted
        assert KEY_SCOPES in keys_deleted

    async def test_returns_true_when_something_deleted(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        # Only the first key exists
        delete_results = {KEY_CLIENT_ID: True}

        async def _delete(key: str) -> bool:
            return delete_results.get(key, False)

        with patch.object(store, "delete", side_effect=_delete):
            result = await delete_google_credentials(store)

        assert result is True

    async def test_returns_false_when_nothing_deleted(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with patch.object(store, "delete", new_callable=AsyncMock, return_value=False):
            result = await delete_google_credentials(store)

        assert result is False


# ---------------------------------------------------------------------------
# resolve_google_credentials — CredentialStore DB-first path
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

    async def test_falls_back_to_env_when_no_db_data(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        env = {
            "GMAIL_CLIENT_ID": "env-cid",
            "GMAIL_CLIENT_SECRET": "env-csecret",
            "GMAIL_REFRESH_TOKEN": "env-rtoken",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            creds = await resolve_google_credentials(store, caller="test")
        assert creds.client_id == "env-cid"

    async def test_raises_when_neither_db_nor_env(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        with mock.patch.dict("os.environ", {}, clear=True):
            with pytest.raises(MissingGoogleCredentialsError) as exc_info:
                await resolve_google_credentials(store, caller="test-caller")
        assert "test-caller" in str(exc_info.value)

    async def test_falls_back_to_env_when_db_has_partial_data(self) -> None:
        """When DB has only client_id (InvalidGoogleCredentialsError), fall back to env."""
        pool = _make_pool_with_values({KEY_CLIENT_ID: "cid"})
        store = CredentialStore(pool)
        env = {
            "GMAIL_CLIENT_ID": "env-cid",
            "GMAIL_CLIENT_SECRET": "env-csecret",
            "GMAIL_REFRESH_TOKEN": "env-rtoken",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            creds = await resolve_google_credentials(store, caller="test")
        assert creds.client_id == "env-cid"

    async def test_db_takes_priority_over_env(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
                KEY_REFRESH_TOKEN: "db-rtoken",
            }
        )
        store = CredentialStore(pool)
        env = {
            "GMAIL_CLIENT_ID": "env-cid",
            "GMAIL_CLIENT_SECRET": "env-csecret",
            "GMAIL_REFRESH_TOKEN": "env-rtoken",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            creds = await resolve_google_credentials(store, caller="test")
        assert creds.client_id == "db-cid"
