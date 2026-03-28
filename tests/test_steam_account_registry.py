"""Tests for the Steam Account Registry (butlers.steam_account_registry).

Covers:
- SteamAccount._from_row: field mapping, missing metadata.
- resolve_steam_account: by steam_id (int), by UUID str, by UUID object,
  primary default, missing primary raises MissingSteamCredentialsError,
  missing steam_id raises SteamAccountNotFoundError,
  invalid UUID raises SteamAccountNotFoundError.
- create_steam_account: first-account-is-primary, subsequent-not-primary,
  duplicate steam_id raises, api_key persistence, metadata stored.
- list_steam_accounts: empty list, ordering (primary first).
- get_steam_account: delegates to resolve_steam_account (primary default,
  UUID lookup, missing raises).
- set_primary_account: atomic swap, non-existent account raises.
- disconnect_account: soft revoke (status=revoked), hard delete (entity cascade),
  non-existent account raises.

All tests use asyncpg pool/connection mocks (no live DB).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.steam_account_registry import (
    MissingSteamCredentialsError,
    SteamAccount,
    SteamAccountAlreadyExistsError,
    SteamAccountNotFoundError,
    create_steam_account,
    disconnect_account,
    get_steam_account,
    list_steam_accounts,
    resolve_steam_account,
    set_primary_account,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 26, 0, 0, 0, tzinfo=UTC)

_ACCOUNT_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_ENTITY_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
_ACCOUNT_ID_2 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
_ENTITY_ID_2 = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_STEAM_ID = 76561198000000001
_STEAM_ID_2 = 76561198000000002


def _make_account_row(
    *,
    id: uuid.UUID = _ACCOUNT_ID,
    entity_id: uuid.UUID = _ENTITY_ID,
    steam_id: int = _STEAM_ID,
    display_name: str | None = "Test User",
    profile_url: str | None = "https://steamcommunity.com/id/testuser",
    avatar_url: str | None = "https://example.com/avatar.jpg",
    is_primary: bool = True,
    status: str = "active",
    connected_at: datetime = _NOW,
    last_poll_at: datetime | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a minimal asyncpg row mock for a steam_accounts row."""
    data = {
        "id": id,
        "entity_id": entity_id,
        "steam_id": steam_id,
        "display_name": display_name,
        "profile_url": profile_url,
        "avatar_url": avatar_url,
        "is_primary": is_primary,
        "status": status,
        "connected_at": connected_at,
        "last_poll_at": last_poll_at,
        "metadata": metadata or {},
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_id_row(id: uuid.UUID) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: id if k == "id" else None)
    return row


class _FakeConn:
    """Minimal asyncpg connection mock that supports .transaction()."""

    def __init__(self) -> None:
        self.fetchrow = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock(return_value="")
        self._tx = _FakeTx()

    def transaction(self) -> _FakeTx:
        return self._tx


class _FakeTx:
    async def __aenter__(self) -> _FakeTx:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


def _make_pool(conn: _FakeConn) -> MagicMock:
    """Wrap a _FakeConn in a pool mock."""
    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


# ---------------------------------------------------------------------------
# SteamAccount._from_row
# ---------------------------------------------------------------------------


class TestSteamAccountFromRow:
    def test_round_trip(self) -> None:
        row = _make_account_row()
        account = SteamAccount._from_row(row)
        assert account.id == _ACCOUNT_ID
        assert account.entity_id == _ENTITY_ID
        assert account.steam_id == _STEAM_ID
        assert account.display_name == "Test User"
        assert account.profile_url == "https://steamcommunity.com/id/testuser"
        assert account.avatar_url == "https://example.com/avatar.jpg"
        assert account.is_primary is True
        assert account.status == "active"
        assert account.connected_at == _NOW
        assert account.last_poll_at is None
        assert account.metadata == {}

    def test_metadata_none_becomes_empty_dict(self) -> None:
        row = _make_account_row(metadata=None)
        account = SteamAccount._from_row(row)
        assert account.metadata == {}

    def test_metadata_populated(self) -> None:
        meta = {"poll_intervals": {"recently_played": 120}}
        row = _make_account_row(metadata=meta)
        account = SteamAccount._from_row(row)
        assert account.metadata == meta


# ---------------------------------------------------------------------------
# resolve_steam_account
# ---------------------------------------------------------------------------


class TestResolveSteamAccount:
    async def test_lookup_by_steam_id(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(steam_id=_STEAM_ID))
        pool = _make_pool(conn)

        account = await resolve_steam_account(pool, steam_id=_STEAM_ID)

        assert account.steam_id == _STEAM_ID
        sql = conn.fetchrow.call_args[0][0]
        assert "steam_id = $1" in sql

    async def test_lookup_by_steam_id_not_found_raises(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(SteamAccountNotFoundError, match="steam_id"):
            await resolve_steam_account(pool, steam_id=_STEAM_ID)

    async def test_lookup_by_uuid_string(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))
        pool = _make_pool(conn)

        account = await resolve_steam_account(pool, account=str(_ACCOUNT_ID))

        assert account.id == _ACCOUNT_ID
        sql = conn.fetchrow.call_args[0][0]
        assert "id = $1" in sql

    async def test_lookup_by_uuid_object(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))
        pool = _make_pool(conn)

        account = await resolve_steam_account(pool, account=_ACCOUNT_ID)

        assert account.id == _ACCOUNT_ID

    async def test_lookup_by_uuid_not_found_raises(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(SteamAccountNotFoundError):
            await resolve_steam_account(pool, account=uuid.uuid4())

    async def test_lookup_by_invalid_string_raises(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        with pytest.raises(SteamAccountNotFoundError, match="Invalid account identifier"):
            await resolve_steam_account(pool, account="not-a-uuid")

    async def test_default_returns_primary(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(is_primary=True))
        pool = _make_pool(conn)

        account = await resolve_steam_account(pool)

        assert account.is_primary is True
        sql = conn.fetchrow.call_args[0][0]
        assert "is_primary = true" in sql

    async def test_default_raises_when_no_primary(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(MissingSteamCredentialsError):
            await resolve_steam_account(pool)

    async def test_steam_id_takes_precedence_over_account(self) -> None:
        """steam_id parameter wins if both steam_id and account are provided."""
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(steam_id=_STEAM_ID))
        pool = _make_pool(conn)

        account = await resolve_steam_account(pool, steam_id=_STEAM_ID, account=_ACCOUNT_ID)

        # Should have queried by steam_id
        sql = conn.fetchrow.call_args[0][0]
        assert "steam_id = $1" in sql
        assert account.steam_id == _STEAM_ID


# ---------------------------------------------------------------------------
# create_steam_account
# ---------------------------------------------------------------------------


class TestCreateSteamAccount:
    async def test_first_account_is_primary(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                None,  # duplicate steam_id check → no existing row
                None,  # _has_primary_account → no primary
                _make_id_row(_ENTITY_ID),  # _create_companion_entity
                _make_account_row(is_primary=True),  # INSERT RETURNING
            ]
        )

        account = await create_steam_account(pool, steam_id=_STEAM_ID)

        assert account.is_primary is True

    async def test_subsequent_account_not_primary(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        existing_primary_row = MagicMock()
        existing_primary_row.__getitem__ = MagicMock(return_value=True)

        conn.fetchrow = AsyncMock(
            side_effect=[
                None,  # duplicate steam_id check
                existing_primary_row,  # _has_primary_account → has a row
                _make_id_row(_ENTITY_ID_2),  # _create_companion_entity
                _make_account_row(
                    id=_ACCOUNT_ID_2,
                    entity_id=_ENTITY_ID_2,
                    steam_id=_STEAM_ID_2,
                    is_primary=False,
                ),  # INSERT RETURNING
            ]
        )

        account = await create_steam_account(pool, steam_id=_STEAM_ID_2)

        assert account.is_primary is False

    async def test_raises_on_duplicate_active_steam_id(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        # existing row with status='active' — should still raise
        existing_row = MagicMock()
        existing_row.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "id": _ACCOUNT_ID,
                "entity_id": _ENTITY_ID,
                "status": "active",
            }[k]
        )
        conn.fetchrow = AsyncMock(side_effect=[existing_row])

        with pytest.raises(SteamAccountAlreadyExistsError, match="already connected"):
            await create_steam_account(pool, steam_id=_STEAM_ID)

    async def test_raises_on_duplicate_suspended_steam_id(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        existing_row = MagicMock()
        existing_row.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "id": _ACCOUNT_ID,
                "entity_id": _ENTITY_ID,
                "status": "suspended",
            }[k]
        )
        conn.fetchrow = AsyncMock(side_effect=[existing_row])

        with pytest.raises(SteamAccountAlreadyExistsError, match="already connected"):
            await create_steam_account(pool, steam_id=_STEAM_ID)

    async def test_reactivates_revoked_account(self) -> None:
        """Reconnecting a revoked account reactivates it instead of raising."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        revoked_existing = MagicMock()
        revoked_existing.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "id": _ACCOUNT_ID,
                "entity_id": _ENTITY_ID,
                "status": "revoked",
            }[k]
        )
        reactivated_row = _make_account_row(status="active")

        conn.fetchrow = AsyncMock(
            side_effect=[
                revoked_existing,  # duplicate check → revoked existing
                reactivated_row,  # UPDATE … RETURNING
            ]
        )

        account = await create_steam_account(pool, steam_id=_STEAM_ID)

        assert account.status == "active"
        # Ensure an UPDATE was issued, not an INSERT
        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert not any("INSERT INTO public.steam_accounts" in c for c in execute_calls)
        update_sqls = [str(call[0][0]) for call in conn.fetchrow.call_args_list]
        assert any("UPDATE public.steam_accounts" in s for s in update_sqls)

    async def test_reactivates_revoked_account_and_updates_api_key(self) -> None:
        """Reactivating a revoked account also refreshes the API key."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        revoked_existing = MagicMock()
        revoked_existing.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "id": _ACCOUNT_ID,
                "entity_id": _ENTITY_ID,
                "status": "revoked",
            }[k]
        )
        reactivated_row = _make_account_row(status="active")

        conn.fetchrow = AsyncMock(
            side_effect=[
                revoked_existing,
                reactivated_row,
            ]
        )

        await create_steam_account(pool, steam_id=_STEAM_ID, api_key="NEWKEY456")

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("entity_info" in c for c in execute_calls)
        assert any("steam_api_key" in c for c in execute_calls)

    async def test_reactivates_revoked_account_without_api_key(self) -> None:
        """Reactivating without an API key does not write to entity_info."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        revoked_existing = MagicMock()
        revoked_existing.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "id": _ACCOUNT_ID,
                "entity_id": _ENTITY_ID,
                "status": "revoked",
            }[k]
        )
        reactivated_row = _make_account_row(status="active")

        conn.fetchrow = AsyncMock(
            side_effect=[
                revoked_existing,
                reactivated_row,
            ]
        )

        await create_steam_account(pool, steam_id=_STEAM_ID)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert not any("entity_info" in c for c in execute_calls)

    async def test_persists_api_key_when_provided(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                None,  # no duplicate
                None,  # no existing primary
                _make_id_row(_ENTITY_ID),  # companion entity
                _make_account_row(),  # INSERT RETURNING
            ]
        )

        await create_steam_account(pool, steam_id=_STEAM_ID, api_key="ABC123KEY")

        # Verify execute was called for entity_info INSERT
        assert conn.execute.await_count >= 1
        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("entity_info" in c for c in execute_calls)
        assert any("steam_api_key" in c for c in execute_calls)

    async def test_no_entity_info_when_no_api_key(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                None,
                _make_id_row(_ENTITY_ID),
                _make_account_row(),
            ]
        )

        await create_steam_account(pool, steam_id=_STEAM_ID)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert not any("entity_info" in c for c in execute_calls)

    async def test_metadata_passed_through(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        meta = {"poll_intervals": {"recently_played": 60}}
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                None,
                _make_id_row(_ENTITY_ID),
                _make_account_row(metadata=meta),
            ]
        )

        account = await create_steam_account(pool, steam_id=_STEAM_ID, metadata=meta)

        assert account.metadata == meta


# ---------------------------------------------------------------------------
# list_steam_accounts
# ---------------------------------------------------------------------------


class TestListSteamAccounts:
    async def test_returns_empty_list_when_no_accounts(self) -> None:
        conn = _FakeConn()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        result = await list_steam_accounts(pool)

        assert result == []

    async def test_returns_accounts_in_order(self) -> None:
        """Primary account should come first."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        primary_row = _make_account_row(id=_ACCOUNT_ID, steam_id=_STEAM_ID, is_primary=True)
        secondary_row = _make_account_row(id=_ACCOUNT_ID_2, steam_id=_STEAM_ID_2, is_primary=False)
        conn.fetch = AsyncMock(return_value=[primary_row, secondary_row])

        accounts = await list_steam_accounts(pool)

        assert len(accounts) == 2
        assert accounts[0].is_primary is True
        assert accounts[1].is_primary is False

    async def test_query_contains_ordering(self) -> None:
        conn = _FakeConn()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        await list_steam_accounts(pool)

        sql = conn.fetch.call_args[0][0]
        assert "is_primary DESC" in sql
        assert "connected_at ASC" in sql


# ---------------------------------------------------------------------------
# get_steam_account
# ---------------------------------------------------------------------------


class TestGetSteamAccount:
    async def test_returns_primary_when_no_arg(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(is_primary=True))
        pool = _make_pool(conn)

        account = await get_steam_account(pool)

        assert account.is_primary is True

    async def test_raises_missing_credentials_when_no_primary(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(MissingSteamCredentialsError):
            await get_steam_account(pool)

    async def test_lookup_by_uuid_string(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))
        pool = _make_pool(conn)

        account = await get_steam_account(pool, str(_ACCOUNT_ID))

        assert account.id == _ACCOUNT_ID

    async def test_lookup_by_uuid_object(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))
        pool = _make_pool(conn)

        account = await get_steam_account(pool, _ACCOUNT_ID)

        assert account.id == _ACCOUNT_ID

    async def test_raises_not_found_for_missing_uuid(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(SteamAccountNotFoundError):
            await get_steam_account(pool, uuid.uuid4())


# ---------------------------------------------------------------------------
# set_primary_account
# ---------------------------------------------------------------------------


class TestSetPrimaryAccount:
    async def test_sets_primary_atomically(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_id_row(_ACCOUNT_ID),  # verify target exists
                _make_account_row(is_primary=True),  # UPDATE RETURNING
            ]
        )

        account = await set_primary_account(pool, _ACCOUNT_ID)

        assert account.is_primary is True

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("is_primary = false" in c for c in execute_calls)
        assert any("is_primary = true" in c for c in execute_calls)

    async def test_raises_not_found_for_missing_account(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(SteamAccountNotFoundError):
            await set_primary_account(pool, uuid.uuid4())


# ---------------------------------------------------------------------------
# disconnect_account
# ---------------------------------------------------------------------------


class TestDisconnectAccount:
    def _make_account_data_row(self, *, was_primary: bool = True) -> MagicMock:
        data = {
            "id": _ACCOUNT_ID,
            "entity_id": _ENTITY_ID,
            "is_primary": was_primary,
            "status": "active",
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    async def test_soft_disconnect_marks_revoked(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_account_data_row())
        pool = _make_pool(conn)

        await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("status = 'revoked'" in c for c in execute_calls)

    async def test_soft_disconnect_stamps_revoked_at(self) -> None:
        """Soft disconnect must set revoked_at = now() for 30-day cursor retention."""
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_account_data_row())
        pool = _make_pool(conn)

        await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("revoked_at" in c for c in execute_calls)

    async def test_hard_delete_does_not_stamp_revoked_at(self) -> None:
        """Hard delete removes the entity entirely — no revoked_at stamp needed."""
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_account_data_row())
        pool = _make_pool(conn)

        await disconnect_account(pool, _ACCOUNT_ID, hard_delete=True)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        # Hard delete goes straight to DELETE — no UPDATE with revoked_at
        assert not any("revoked_at" in c for c in execute_calls)

    async def test_soft_disconnect_does_not_delete_entity(self) -> None:
        """Soft disconnect retains companion entity and credentials."""
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_account_data_row())
        pool = _make_pool(conn)

        await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert not any("DELETE FROM public.entities" in c for c in execute_calls)

    async def test_hard_delete_removes_entity(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_account_data_row())
        pool = _make_pool(conn)

        await disconnect_account(pool, _ACCOUNT_ID, hard_delete=True)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("DELETE FROM public.entities" in c for c in execute_calls)

    async def test_raises_not_found_for_missing_account(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(SteamAccountNotFoundError):
            await disconnect_account(pool, uuid.uuid4())

    async def test_no_auto_promote_on_soft_disconnect(self) -> None:
        """Per spec: no automatic promotion when primary is revoked.

        The user must manually set a new primary.
        """
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_account_data_row(was_primary=True))
        pool = _make_pool(conn)

        await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        # Must NOT auto-promote
        assert not any("is_primary = true" in c for c in execute_calls)
