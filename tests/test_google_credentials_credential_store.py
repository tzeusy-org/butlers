"""Tests for the CredentialStore + entity_info-backed Google credential functions.

Covers:
- store_google_credentials() via CredentialStore + pool (entity_info)
- load_google_credentials() via CredentialStore + pool (entity_info)
- store_app_credentials() via CredentialStore
- load_app_credentials() via CredentialStore + pool
- delete_google_credentials() via CredentialStore + pool
- resolve_google_credentials() via CredentialStore + pool
- resolve_google_account_entity() helper
- list_google_account_entities() helper
- Multi-account: account param routing
- KEY_* constants exported
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import CredentialStore
from butlers.google_credentials import (
    CONTACT_INFO_REFRESH_TOKEN,
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    KEY_SCOPES,
    InvalidGoogleCredentialsError,
    MissingGoogleCredentialsError,
    delete_google_credentials,
    list_google_account_entities,
    load_app_credentials,
    load_google_credentials,
    resolve_google_account_entity,
    resolve_google_credentials,
    store_app_credentials,
    store_google_credentials,
)

pytestmark = pytest.mark.unit

_ENTITY_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
_ENTITY_ID_2 = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


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
    """Build a pool mock for resolve_owner_entity_info calls."""
    return MagicMock()


# ---------------------------------------------------------------------------
# KEY_* constants
# ---------------------------------------------------------------------------


class TestKeyConstants:
    def test_key_client_id(self):
        assert KEY_CLIENT_ID == "GOOGLE_OAUTH_CLIENT_ID"

    def test_key_client_secret(self):
        assert KEY_CLIENT_SECRET == "GOOGLE_OAUTH_CLIENT_SECRET"

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
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
                new_callable=AsyncMock,
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
        # Refresh token should go to entity_info on companion entity
        mock_upsert.assert_awaited_once_with(ci_pool, _ENTITY_ID, "rtoken")

    async def test_client_id_stored_as_not_sensitive(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock) as mock_store,
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
                new_callable=AsyncMock,
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
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
                new_callable=AsyncMock,
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
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
                new_callable=AsyncMock,
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
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
                new_callable=AsyncMock,
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

    async def test_no_pool_skips_entity_info_write(self) -> None:
        """When pool=None, refresh token is not persisted to entity_info."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch.object(store, "store", new_callable=AsyncMock),
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
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

    async def test_stores_with_explicit_account_email(self) -> None:
        """account='work@gmail.com' is passed to _resolve_account_entity_id."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch.object(store, "store", new_callable=AsyncMock),
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID_2,
            ) as mock_resolve,
            patch(
                "butlers.google_credentials._upsert_entity_refresh_token",
                new_callable=AsyncMock,
            ) as mock_upsert,
        ):
            await store_google_credentials(
                store,
                pool=ci_pool,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken2",
                account="work@gmail.com",
            )

        mock_resolve.assert_awaited_once_with(ci_pool, "work@gmail.com")
        mock_upsert.assert_awaited_once_with(ci_pool, _ENTITY_ID_2, "rtoken2")

    async def test_falls_back_to_owner_entity_when_no_account_entity(self) -> None:
        """When _resolve_account_entity_id returns None, fall back to owner entity."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch.object(store, "store", new_callable=AsyncMock),
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.upsert_owner_entity_info",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_owner_upsert,
        ):
            await store_google_credentials(
                store,
                pool=ci_pool,
                client_id="cid",
                client_secret="csecret",
                refresh_token="rtoken",
            )

        mock_owner_upsert.assert_awaited_once_with(ci_pool, CONTACT_INFO_REFRESH_TOKEN, "rtoken")


# ---------------------------------------------------------------------------
# load_google_credentials — CredentialStore + entity_info path
# ---------------------------------------------------------------------------


class TestLoadGoogleCredentialsWithCredentialStore:
    async def test_returns_none_when_no_keys_stored(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        result = await load_google_credentials(store)
        assert result is None

    async def test_returns_credentials_with_refresh_from_entity_info(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_SCOPES: "gmail calendar",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value="rtoken-from-ei",
            ),
        ):
            creds = await load_google_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.client_id == "cid"
        assert creds.client_secret == "csecret"
        assert creds.refresh_token == "rtoken-from-ei"
        assert creds.scope == "gmail calendar"

    async def test_raises_when_entity_info_has_no_refresh_token(self) -> None:
        """When entity_info has no refresh token, raise InvalidGoogleCredentialsError."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            with pytest.raises(InvalidGoogleCredentialsError, match="refresh_token"):
                await load_google_credentials(store, pool=ci_pool)

    async def test_scope_is_none_when_key_absent(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value="rtoken",
            ),
        ):
            creds = await load_google_credentials(store, pool=ci_pool)
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
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value="ALSO-NEVER-REVEAL",
            ),
        ):
            creds = await load_google_credentials(store, pool=ci_pool)
        assert creds is not None
        r = repr(creds)
        assert "NEVER-REVEAL" not in r
        assert "ALSO-NEVER-REVEAL" not in r

    async def test_loads_different_account_tokens(self) -> None:
        """Two accounts with different entity_ids return different refresh tokens."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        for entity_id, expected_token in [(_ENTITY_ID, "token-a"), (_ENTITY_ID_2, "token-b")]:
            with (
                patch(
                    "butlers.google_credentials._resolve_account_entity_id",
                    new_callable=AsyncMock,
                    return_value=entity_id,
                ),
                patch(
                    "butlers.google_credentials._resolve_entity_refresh_token",
                    new_callable=AsyncMock,
                    return_value=expected_token,
                ),
            ):
                creds = await load_google_credentials(store, pool=ci_pool)
            assert creds is not None
            assert creds.refresh_token == expected_token

    async def test_falls_back_to_owner_entity_when_no_account_entity(self) -> None:
        """When _resolve_account_entity_id returns None, fall back to owner entity."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="legacy-rtoken",
            ),
        ):
            creds = await load_google_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.refresh_token == "legacy-rtoken"


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
        assert "GOOGLE_REFRESH_TOKEN" not in keys_stored

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
# load_app_credentials — CredentialStore + entity_info path
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

    async def test_returns_refresh_token_from_entity_info(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
                KEY_SCOPES: "gmail",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value="rtoken-ci",
            ),
        ):
            creds = await load_app_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.refresh_token == "rtoken-ci"
        assert creds.scope == "gmail"

    async def test_refresh_token_none_when_entity_info_empty(self) -> None:
        """When entity_info has no refresh token, refresh_token is None (no fallback)."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "cid",
                KEY_CLIENT_SECRET: "csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            creds = await load_app_credentials(store, pool=ci_pool)

        assert creds is not None
        assert creds.refresh_token is None


# ---------------------------------------------------------------------------
# delete_google_credentials — CredentialStore + entity_info path
# ---------------------------------------------------------------------------


class TestDeleteGoogleCredentialsWithCredentialStore:
    async def test_deletes_only_refresh_token_for_account_by_default(self) -> None:
        """Default: only the refresh token for the specified account is deleted."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch.object(store, "delete", new_callable=AsyncMock, return_value=False) as mock_del,
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._delete_entity_refresh_token",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_token_del,
            patch(
                "butlers.google_credentials._mark_account_revoked",
                new_callable=AsyncMock,
            ),
        ):
            result = await delete_google_credentials(store, pool=ci_pool)

        # App credentials should NOT be deleted
        mock_del.assert_not_awaited()
        mock_token_del.assert_awaited_once_with(ci_pool, _ENTITY_ID)
        assert result is True

    async def test_delete_all_removes_app_credentials_and_all_tokens(self) -> None:
        """delete_all=True deletes app credentials AND all account refresh tokens in bulk."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        # Simulate google_accounts returning two rows via acquire context manager.
        # The bulk DELETE returns "DELETE 2" meaning two entity_info rows removed.
        row1, row2 = MagicMock(), MagicMock()
        row1.__getitem__ = MagicMock(side_effect=lambda k: _ENTITY_ID if k == "entity_id" else None)
        row2.__getitem__ = MagicMock(
            side_effect=lambda k: _ENTITY_ID_2 if k == "entity_id" else None
        )
        ci_pool = _make_acquire_pool(
            fetch_return=[row1, row2],
            execute_return="DELETE 2",
        )

        with patch.object(store, "delete", new_callable=AsyncMock, return_value=True) as mock_del:
            result = await delete_google_credentials(store, pool=ci_pool, delete_all=True)

        # App credentials should be deleted
        keys_deleted = [c[0][0] for c in mock_del.call_args_list]
        assert KEY_CLIENT_ID in keys_deleted
        assert KEY_CLIENT_SECRET in keys_deleted
        assert KEY_SCOPES in keys_deleted
        # Bulk delete for both accounts succeeded
        assert result is True

    async def test_returns_true_when_something_deleted(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._delete_entity_refresh_token",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.google_credentials._mark_account_revoked",
                new_callable=AsyncMock,
            ),
        ):
            result = await delete_google_credentials(store, pool=ci_pool)

        assert result is True

    async def test_returns_false_when_nothing_deleted(self) -> None:
        pool = _make_empty_pool()
        store = CredentialStore(pool)

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._delete_entity_refresh_token",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.google_credentials._mark_account_revoked",
                new_callable=AsyncMock,
            ),
        ):
            result = await delete_google_credentials(store, pool=MagicMock())

        assert result is False

    async def test_falls_back_to_owner_entity_when_no_account_entity(self) -> None:
        """When no account entity, falls back to deleting from owner entity."""
        pool = _make_empty_pool()
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.delete_owner_entity_info",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_owner_del,
        ):
            result = await delete_google_credentials(store, pool=ci_pool)

        mock_owner_del.assert_awaited_once_with(ci_pool, CONTACT_INFO_REFRESH_TOKEN)
        assert result is True


# ---------------------------------------------------------------------------
# resolve_google_credentials — CredentialStore + entity_info path
# ---------------------------------------------------------------------------


class TestResolveGoogleCredentialsWithCredentialStore:
    async def test_resolves_from_credential_store(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value="db-rtoken",
            ),
            patch(
                "butlers.google_credentials._google_accounts_table_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            creds = await resolve_google_credentials(store, pool=ci_pool, caller="test")
        assert creds.client_id == "db-cid"
        assert creds.refresh_token == "db-rtoken"

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

    async def test_resolves_refresh_from_entity_info(self) -> None:
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=_ENTITY_ID,
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value="ei-rtoken",
            ),
            patch(
                "butlers.google_credentials._google_accounts_table_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            creds = await resolve_google_credentials(store, pool=ci_pool, caller="test")

        assert creds.refresh_token == "ei-rtoken"

    async def test_raises_missing_error_for_nonexistent_account(self) -> None:
        """Specified account not found → MissingGoogleCredentialsError."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials._google_accounts_table_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            with pytest.raises(MissingGoogleCredentialsError, match="not connected"):
                await resolve_google_credentials(
                    store, pool=ci_pool, caller="test", account="missing@gmail.com"
                )

    async def test_raises_missing_error_for_no_primary(self) -> None:
        """No primary account → MissingGoogleCredentialsError with helpful message."""
        pool = _make_pool_with_values(
            {
                KEY_CLIENT_ID: "db-cid",
                KEY_CLIENT_SECRET: "db-csecret",
            }
        )
        store = CredentialStore(pool)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials._google_accounts_table_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            with pytest.raises(MissingGoogleCredentialsError, match="primary"):
                await resolve_google_credentials(store, pool=ci_pool, caller="calendar")


# ---------------------------------------------------------------------------
# Helpers for acquire-based pool mocks
# ---------------------------------------------------------------------------


def _make_acquire_pool(
    fetchrow_return=None,
    fetchrow_side_effect=None,
    fetch_return=None,
    fetch_side_effect=None,
    fetchval_return=None,
    execute_return="UPDATE 1",
) -> MagicMock:
    """Build a pool mock whose acquire() context manager yields a conn."""
    conn = MagicMock()
    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    if fetch_side_effect is not None:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.execute = AsyncMock(return_value=execute_return)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire.return_value = cm
    return pool


# ---------------------------------------------------------------------------
# resolve_google_account_entity — helper
# ---------------------------------------------------------------------------


class TestResolveGoogleAccountEntity:
    async def test_returns_entity_id_for_primary(self) -> None:
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value=_ENTITY_ID)
        pool = _make_acquire_pool(fetchrow_return=row)

        result = await resolve_google_account_entity(pool)
        assert result == _ENTITY_ID

    async def test_returns_none_when_no_primary(self) -> None:
        pool = _make_acquire_pool(fetchrow_return=None)

        result = await resolve_google_account_entity(pool)
        assert result is None

    async def test_returns_entity_id_for_email(self) -> None:
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value=_ENTITY_ID_2)
        pool = _make_acquire_pool(fetchrow_return=row)

        result = await resolve_google_account_entity(pool, email="work@gmail.com")
        assert result == _ENTITY_ID_2

    async def test_returns_none_when_email_not_found(self) -> None:
        pool = _make_acquire_pool(fetchrow_return=None)

        result = await resolve_google_account_entity(pool, email="missing@gmail.com")
        assert result is None

    async def test_returns_none_when_table_missing(self) -> None:
        pool = _make_acquire_pool(
            fetchrow_side_effect=Exception('relation "shared.google_accounts" does not exist')
        )

        result = await resolve_google_account_entity(pool)
        assert result is None


# ---------------------------------------------------------------------------
# list_google_account_entities — helper
# ---------------------------------------------------------------------------


class TestListGoogleAccountEntities:
    async def test_returns_empty_list_when_table_missing(self) -> None:
        pool = _make_acquire_pool(
            fetch_side_effect=Exception('relation "shared.google_accounts" does not exist')
        )

        result = await list_google_account_entities(pool)
        assert result == []

    async def test_returns_tuples_for_all_accounts(self) -> None:
        _ACCOUNT_ID_1 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
        _ACCOUNT_ID_2 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")

        def _make_row(acc_id, email, eid, is_primary):
            row = MagicMock()
            data = {
                "id": acc_id,
                "email": email,
                "entity_id": eid,
                "is_primary": is_primary,
            }
            row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
            return row

        pool = _make_acquire_pool(
            fetch_return=[
                _make_row(_ACCOUNT_ID_1, "alice@gmail.com", _ENTITY_ID, True),
                _make_row(_ACCOUNT_ID_2, "work@gmail.com", _ENTITY_ID_2, False),
            ]
        )

        result = await list_google_account_entities(pool)
        assert len(result) == 2
        assert result[0] == (_ACCOUNT_ID_1, "alice@gmail.com", _ENTITY_ID, True)
        assert result[1] == (_ACCOUNT_ID_2, "work@gmail.com", _ENTITY_ID_2, False)
