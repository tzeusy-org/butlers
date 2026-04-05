"""Tests for GoogleCredentials model and credential store/resolve operations."""

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
    GoogleCredentials,
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


def _make_pool_with_values(key_to_value: dict[str, str | None]) -> MagicMock:
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
    return pool


def _make_acquire_pool(
    fetchrow_return=None, fetchrow_side_effect=None, fetch_return=None, fetch_side_effect=None
) -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = (
        AsyncMock(side_effect=fetchrow_side_effect)
        if fetchrow_side_effect
        else AsyncMock(return_value=fetchrow_return)
    )
    conn.fetch = (
        AsyncMock(side_effect=fetch_side_effect)
        if fetch_side_effect
        else AsyncMock(return_value=fetch_return or [])
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = cm
    return pool


# ---------------------------------------------------------------------------
# GoogleCredentials model
# ---------------------------------------------------------------------------


def test_google_credentials_model():
    """Valid credentials parse; whitespace stripped; empty fields raise; repr hides secrets."""
    creds = GoogleCredentials(
        client_id="  id  ", client_secret="  secret  ", refresh_token="  token  ", scope="gmail"
    )
    assert creds.client_id == "id" and creds.scope == "gmail"

    for cid, cs, rt in [("", "s", "r"), ("id", "", "r"), ("id", "s", "")]:
        with pytest.raises(Exception):
            GoogleCredentials(client_id=cid, client_secret=cs, refresh_token=rt)

    r = repr(GoogleCredentials(client_id="cid", client_secret="SECRET", refresh_token="TOKEN"))
    assert "SECRET" not in r and "TOKEN" not in r and "REDACTED" in r


# ---------------------------------------------------------------------------
# Key constants
# ---------------------------------------------------------------------------


def test_key_constants():
    assert KEY_CLIENT_ID == "GOOGLE_OAUTH_CLIENT_ID"
    assert KEY_CLIENT_SECRET == "GOOGLE_OAUTH_CLIENT_SECRET"
    assert CONTACT_INFO_REFRESH_TOKEN == "google_oauth_refresh"


# ---------------------------------------------------------------------------
# store_google_credentials
# ---------------------------------------------------------------------------


async def test_store_google_credentials():
    """Stores app keys with sensitivity; skips scope when None; skips entity write when no pool."""
    store = CredentialStore(_make_pool_with_values({}))

    with (
        patch.object(store, "store", new_callable=AsyncMock) as mock_store,
        patch(
            "butlers.google_credentials._resolve_account_entity_id",
            new_callable=AsyncMock,
            return_value=_ENTITY_ID,
        ),
        patch(
            "butlers.google_credentials._upsert_entity_refresh_token", new_callable=AsyncMock
        ) as mock_upsert,
    ):
        await store_google_credentials(
            store,
            pool=MagicMock(),
            client_id="cid",
            client_secret="csecret",
            refresh_token="rtoken",
            scope="gmail",
        )

    keys = [c[0][0] for c in mock_store.call_args_list]
    assert KEY_CLIENT_ID in keys and KEY_CLIENT_SECRET in keys and KEY_SCOPES in keys
    cid_call = next(c for c in mock_store.call_args_list if c[0][0] == KEY_CLIENT_ID)
    assert cid_call[1].get("is_sensitive") is False
    csecret_call = next(c for c in mock_store.call_args_list if c[0][0] == KEY_CLIENT_SECRET)
    assert csecret_call[1].get("is_sensitive") is True
    mock_upsert.assert_awaited_once()

    # scope=None: KEY_SCOPES not stored
    store2 = CredentialStore(_make_pool_with_values({}))
    with (
        patch.object(store2, "store", new_callable=AsyncMock) as mock_store2,
        patch(
            "butlers.google_credentials._resolve_account_entity_id",
            new_callable=AsyncMock,
            return_value=_ENTITY_ID,
        ),
        patch("butlers.google_credentials._upsert_entity_refresh_token", new_callable=AsyncMock),
    ):
        await store_google_credentials(
            store2, client_id="cid", client_secret="csecret", refresh_token="rtoken", scope=None
        )
    assert KEY_SCOPES not in [c[0][0] for c in mock_store2.call_args_list]

    # pool=None: entity info skipped
    store3 = CredentialStore(_make_pool_with_values({}))
    with (
        patch.object(store3, "store", new_callable=AsyncMock),
        patch(
            "butlers.google_credentials._upsert_entity_refresh_token", new_callable=AsyncMock
        ) as mock_upsert3,
    ):
        await store_google_credentials(
            store3, pool=None, client_id="cid", client_secret="csecret", refresh_token="rtoken"
        )
    mock_upsert3.assert_not_awaited()


# ---------------------------------------------------------------------------
# load_google_credentials
# ---------------------------------------------------------------------------


async def test_load_google_credentials():
    """Returns None when no keys; token from entity info; raises on missing token; repr safe."""
    empty_pool = _make_pool_with_values({})
    assert await load_google_credentials(CredentialStore(empty_pool)) is None

    pool = _make_pool_with_values(
        {KEY_CLIENT_ID: "cid", KEY_CLIENT_SECRET: "NEVER-REVEAL", KEY_SCOPES: "gmail"}
    )
    with (
        patch(
            "butlers.google_credentials._resolve_account_entity_id",
            new_callable=AsyncMock,
            return_value=_ENTITY_ID,
        ),
        patch(
            "butlers.google_credentials._resolve_entity_refresh_token",
            new_callable=AsyncMock,
            return_value="ALSO-SECRET",
        ),
    ):
        creds = await load_google_credentials(CredentialStore(pool), pool=MagicMock())
    assert creds is not None and creds.client_id == "cid" and creds.refresh_token == "ALSO-SECRET"
    r = repr(creds)
    assert "NEVER-REVEAL" not in r and "ALSO-SECRET" not in r

    pool2 = _make_pool_with_values({KEY_CLIENT_ID: "cid", KEY_CLIENT_SECRET: "csecret"})
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
            await load_google_credentials(CredentialStore(pool2), pool=MagicMock())


# ---------------------------------------------------------------------------
# store_app_credentials / load_app_credentials
# ---------------------------------------------------------------------------


async def test_store_and_load_app_credentials():
    """App credentials store client_id/secret; load returns None when missing."""
    store = CredentialStore(_make_pool_with_values({}))
    with patch.object(store, "store", new_callable=AsyncMock) as mock_store:
        await store_app_credentials(store, client_id="cid", client_secret="csecret")
    keys = [c[0][0] for c in mock_store.call_args_list]
    assert KEY_CLIENT_ID in keys and KEY_CLIENT_SECRET in keys

    for cid, cs, match in [("", "s", "client_id"), ("id", "", "client_secret")]:
        with pytest.raises(ValueError, match=match):
            await store_app_credentials(
                CredentialStore(_make_pool_with_values({})), client_id=cid, client_secret=cs
            )

    assert await load_app_credentials(CredentialStore(_make_pool_with_values({}))) is None
    pool2 = _make_pool_with_values({KEY_CLIENT_ID: "cid", KEY_CLIENT_SECRET: "csecret"})
    creds = await load_app_credentials(CredentialStore(pool2))
    assert creds is not None and creds.client_id == "cid" and creds.refresh_token is None


# ---------------------------------------------------------------------------
# delete_google_credentials
# ---------------------------------------------------------------------------


async def test_delete_google_credentials():
    """Default delete removes account token; returns False when no entity; True when deleted."""
    store = CredentialStore(_make_pool_with_values({}))
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
        patch("butlers.google_credentials._mark_account_revoked", new_callable=AsyncMock),
    ):
        result = await delete_google_credentials(store, pool=ci_pool)
    mock_del.assert_not_awaited()
    mock_token_del.assert_awaited_once_with(ci_pool, _ENTITY_ID)
    assert result is True

    store2 = CredentialStore(_make_pool_with_values({}))
    with patch(
        "butlers.google_credentials._resolve_account_entity_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        assert await delete_google_credentials(store2, pool=MagicMock()) is False


# ---------------------------------------------------------------------------
# resolve_google_credentials
# ---------------------------------------------------------------------------


async def test_resolve_google_credentials():
    """Resolves from DB; raises MissingGoogleCredentialsError when empty or no entity."""
    pool = _make_pool_with_values({KEY_CLIENT_ID: "db-cid", KEY_CLIENT_SECRET: "db-csecret"})
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
    ):
        creds = await resolve_google_credentials(
            CredentialStore(pool), pool=MagicMock(), caller="test"
        )
    assert creds.client_id == "db-cid" and creds.refresh_token == "db-rtoken"

    with pytest.raises(MissingGoogleCredentialsError):
        await resolve_google_credentials(CredentialStore(_make_pool_with_values({})), caller="test")

    pool3 = _make_pool_with_values({KEY_CLIENT_ID: "cid", KEY_CLIENT_SECRET: "csecret"})
    with patch(
        "butlers.google_credentials._resolve_account_entity_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(MissingGoogleCredentialsError):
            await resolve_google_credentials(CredentialStore(pool3), pool=MagicMock(), caller="cal")


# ---------------------------------------------------------------------------
# resolve_google_account_entity / list_google_account_entities
# ---------------------------------------------------------------------------


async def test_account_entity_helpers():
    """resolve and list handle table-missing exceptions gracefully."""
    row = MagicMock()
    row.__getitem__ = MagicMock(return_value=_ENTITY_ID)
    assert (
        await resolve_google_account_entity(_make_acquire_pool(fetchrow_return=row)) == _ENTITY_ID
    )
    assert await resolve_google_account_entity(_make_acquire_pool(fetchrow_return=None)) is None
    assert (
        await resolve_google_account_entity(
            _make_acquire_pool(
                fetchrow_side_effect=Exception('relation "public.google_accounts" does not exist')
            )
        )
        is None
    )

    _ACC_1 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")

    def _row(acc_id, email, eid, is_primary):
        r = MagicMock()
        data = {"id": acc_id, "email": email, "entity_id": eid, "is_primary": is_primary}
        r.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return r

    pool = _make_acquire_pool(fetch_return=[_row(_ACC_1, "alice@gmail.com", _ENTITY_ID, True)])
    result = await list_google_account_entities(pool)
    assert len(result) == 1 and result[0] == (_ACC_1, "alice@gmail.com", _ENTITY_ID, True)

    assert (
        await list_google_account_entities(
            _make_acquire_pool(
                fetch_side_effect=Exception('relation "public.google_accounts" does not exist')
            )
        )
        == []
    )
