"""Tests for the Steam Account Registry (butlers.steam_account_registry).

Covers:
- SteamAccount._from_row: field mapping, missing metadata.
- resolve_steam_account: by steam_id, UUID (str/obj), primary default, errors.
- create_steam_account: primary flag logic, duplicate handling, revoke reactivation,
  api_key persistence, metadata.
- list_steam_accounts / get_steam_account: delegation and ordering.
- set_primary_account / disconnect_account: atomic swap, soft/hard, error cases.
"""

from __future__ import annotations

import json
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


class _FakeTx:
    async def __aenter__(self) -> _FakeTx:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


class _FakeConn:
    def __init__(self) -> None:
        self.fetchrow = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock(return_value="")
        self._tx = _FakeTx()

    def transaction(self) -> _FakeTx:
        return self._tx


def _make_pool(conn: _FakeConn) -> MagicMock:
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
    def test_round_trip_and_metadata(self) -> None:
        """Field mapping works; None metadata normalises to {}."""
        row = _make_account_row()
        account = SteamAccount._from_row(row)
        assert account.id == _ACCOUNT_ID
        assert account.steam_id == _STEAM_ID
        assert account.is_primary is True
        assert account.metadata == {}

        # Explicit metadata round-trips
        meta = {"poll_intervals": {"recently_played": 120}}
        row2 = _make_account_row(metadata=meta)
        assert SteamAccount._from_row(row2).metadata == meta

    def test_metadata_as_json_string(self) -> None:
        """asyncpg returns JSONB as a string when no codec is registered.

        The shared dashboard credential pool has no JSONB codec, so metadata
        arrives as a string and ``_from_row`` must json-decode it.
        """
        meta = {"poll_intervals": {"recently_played": 120}}
        row = _make_account_row(metadata=json.dumps(meta))  # type: ignore[arg-type]
        assert SteamAccount._from_row(row).metadata == meta

        # Empty JSONB ('{}') also round-trips to an empty dict
        row_empty = _make_account_row(metadata="{}")  # type: ignore[arg-type]
        assert SteamAccount._from_row(row_empty).metadata == {}


# ---------------------------------------------------------------------------
# resolve_steam_account
# ---------------------------------------------------------------------------


class TestResolveSteamAccount:
    @pytest.mark.parametrize(
        "kwargs,setup_row,check",
        [
            # by steam_id
            (
                {"steam_id": _STEAM_ID},
                _make_account_row(steam_id=_STEAM_ID),
                lambda a: a.steam_id == _STEAM_ID,
            ),
            # by UUID string
            (
                {"account": str(_ACCOUNT_ID)},
                _make_account_row(id=_ACCOUNT_ID),
                lambda a: a.id == _ACCOUNT_ID,
            ),
            # by UUID object
            (
                {"account": _ACCOUNT_ID},
                _make_account_row(id=_ACCOUNT_ID),
                lambda a: a.id == _ACCOUNT_ID,
            ),
            # default returns primary
            (
                {},
                _make_account_row(is_primary=True),
                lambda a: a.is_primary is True,
            ),
            # steam_id takes precedence over account when both supplied
            (
                {"steam_id": _STEAM_ID, "account": _ACCOUNT_ID},
                _make_account_row(steam_id=_STEAM_ID),
                lambda a: a.steam_id == _STEAM_ID,
            ),
        ],
        ids=["by-steam-id", "by-uuid-str", "by-uuid-obj", "default-primary", "steam-id-precedence"],
    )
    async def test_lookup_success(self, kwargs, setup_row, check) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=setup_row)
        pool = _make_pool(conn)
        account = await resolve_steam_account(pool, **kwargs)
        assert check(account)

    @pytest.mark.parametrize(
        "kwargs,exc_type,match",
        [
            ({"steam_id": _STEAM_ID}, SteamAccountNotFoundError, "steam_id"),
            ({"account": "not-a-uuid"}, SteamAccountNotFoundError, "Invalid account identifier"),
            ({}, MissingSteamCredentialsError, None),
        ],
        ids=["steam-id-not-found", "invalid-uuid-str", "no-primary"],
    )
    async def test_lookup_errors(self, kwargs, exc_type, match) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)
        if match:
            with pytest.raises(exc_type, match=match):
                await resolve_steam_account(pool, **kwargs)
        else:
            with pytest.raises(exc_type):
                await resolve_steam_account(pool, **kwargs)


# ---------------------------------------------------------------------------
# create_steam_account
# ---------------------------------------------------------------------------


class TestCreateSteamAccount:
    async def test_first_account_is_primary(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                None,
                _make_id_row(_ENTITY_ID),
                _make_account_row(is_primary=True),
            ]
        )
        assert (await create_steam_account(pool, steam_id=_STEAM_ID)).is_primary is True

    async def test_subsequent_account_not_primary(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)
        existing_primary = MagicMock()
        existing_primary.__getitem__ = MagicMock(return_value=True)
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                existing_primary,
                _make_id_row(_ENTITY_ID_2),
                _make_account_row(id=_ACCOUNT_ID_2, steam_id=_STEAM_ID_2, is_primary=False),
            ]
        )
        assert (await create_steam_account(pool, steam_id=_STEAM_ID_2)).is_primary is False

    @pytest.mark.parametrize("status", ["active", "suspended"])
    async def test_raises_on_duplicate_non_revoked_steam_id(self, status) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)
        existing = MagicMock()
        existing.__getitem__ = MagicMock(
            side_effect=lambda k: {"id": _ACCOUNT_ID, "entity_id": _ENTITY_ID, "status": status}[k]
        )
        conn.fetchrow = AsyncMock(side_effect=[existing])
        with pytest.raises(SteamAccountAlreadyExistsError, match="already connected"):
            await create_steam_account(pool, steam_id=_STEAM_ID)

    async def test_reactivates_revoked_account_with_and_without_api_key(self) -> None:
        """Reconnecting a revoked account reactivates; api_key updates entity_info only if given."""
        for api_key in ["NEWKEY456", None]:
            conn = _FakeConn()
            pool = _make_pool(conn)
            revoked = MagicMock()
            revoked.__getitem__ = MagicMock(
                side_effect=lambda k: {
                    "id": _ACCOUNT_ID,
                    "entity_id": _ENTITY_ID,
                    "status": "revoked",
                }[k]
            )
            conn.fetchrow = AsyncMock(side_effect=[revoked, _make_account_row(status="active")])

            account = await create_steam_account(
                pool, steam_id=_STEAM_ID, **({"api_key": api_key} if api_key else {})
            )
            assert account.status == "active"
            execute_calls = [str(c[0][0]) for c in conn.execute.call_args_list]
            if api_key:
                assert any("entity_info" in c for c in execute_calls)
            else:
                assert not any("entity_info" in c for c in execute_calls)

    async def test_api_key_metadata_and_jsonb_codec(self) -> None:
        """API key persists to entity_info; metadata round-trips and binds as a dict.

        bu-aaacv removed the json.dumps + ::jsonb double-encoding pattern, so the
        INSERT must pass metadata as a Python dict (the asyncpg JSONB codec handles
        encoding), not a pre-serialized string.
        """
        meta = {"poll_intervals": {"recently_played": 60}}
        conn = _FakeConn()
        pool = _make_pool(conn)
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                None,
                _make_id_row(_ENTITY_ID),
                _make_account_row(metadata=meta),
            ]
        )
        account = await create_steam_account(
            pool, steam_id=_STEAM_ID, api_key="ABC123KEY", metadata=meta
        )
        assert account.metadata == meta
        execute_calls = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("entity_info" in c for c in execute_calls)

        # Metadata reaches the INSERT as a dict (no json.dumps double-encode).
        insert_call = next(
            c
            for c in conn.fetchrow.call_args_list
            if "INSERT INTO public.steam_accounts" in str(c[0][0])
        )
        metadata_arg = insert_call[0][7]
        assert isinstance(metadata_arg, dict), (
            f"metadata must be a dict for the asyncpg JSONB codec, got {type(metadata_arg).__name__}"
        )
        assert metadata_arg == meta


# ---------------------------------------------------------------------------
# list_steam_accounts / get_steam_account
# ---------------------------------------------------------------------------


class TestListAndGet:
    async def test_list_empty_and_ordered(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        # Empty
        conn.fetch = AsyncMock(return_value=[])
        assert await list_steam_accounts(pool) == []

        # Ordered
        conn.fetch = AsyncMock(
            return_value=[
                _make_account_row(id=_ACCOUNT_ID, is_primary=True),
                _make_account_row(id=_ACCOUNT_ID_2, is_primary=False),
            ]
        )
        accounts = await list_steam_accounts(pool)
        assert len(accounts) == 2 and accounts[0].is_primary is True

    async def test_get_delegates_to_resolve(self) -> None:
        """get_steam_account returns primary by default and by UUID."""
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=_make_account_row(is_primary=True))
        pool = _make_pool(conn)
        assert (await get_steam_account(pool)).is_primary is True

        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))
        assert (await get_steam_account(pool, str(_ACCOUNT_ID))).id == _ACCOUNT_ID

    async def test_get_raises_when_no_primary(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)
        with pytest.raises(MissingSteamCredentialsError):
            await get_steam_account(pool)


# ---------------------------------------------------------------------------
# set_primary_account
# ---------------------------------------------------------------------------


class TestSetPrimaryAccount:
    async def test_sets_primary_atomically(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)
        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_id_row(_ACCOUNT_ID),
                _make_account_row(is_primary=True),
            ]
        )
        assert (await set_primary_account(pool, _ACCOUNT_ID)).is_primary is True
        execute_calls = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("is_primary = false" in c for c in execute_calls)

    async def test_raises_not_found(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)
        with pytest.raises(SteamAccountNotFoundError):
            await set_primary_account(pool, uuid.uuid4())


# ---------------------------------------------------------------------------
# disconnect_account
# ---------------------------------------------------------------------------


class TestDisconnectAccount:
    def _make_data_row(self, *, was_primary: bool = True) -> MagicMock:
        data = {
            "id": _ACCOUNT_ID,
            "entity_id": _ENTITY_ID,
            "is_primary": was_primary,
            "status": "active",
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    @pytest.mark.parametrize("hard_delete", [False, True], ids=["soft", "hard"])
    async def test_disconnect_modes(self, hard_delete: bool) -> None:
        """Soft marks revoked with revoked_at; hard deletes entity; neither auto-promotes."""
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=self._make_data_row())
        pool = _make_pool(conn)
        await disconnect_account(pool, _ACCOUNT_ID, hard_delete=hard_delete)
        calls = [str(c[0][0]) for c in conn.execute.call_args_list]
        if hard_delete:
            assert any("DELETE FROM public.entities" in c for c in calls)
            assert not any("revoked_at" in c for c in calls)
        else:
            assert any("status = 'revoked'" in c for c in calls)
            assert any("revoked_at" in c for c in calls)
            assert not any("DELETE FROM public.entities" in c for c in calls)
        # No auto-promote in either case
        assert not any("is_primary = true" in c for c in calls)

    async def test_raises_not_found(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)
        with pytest.raises(SteamAccountNotFoundError):
            await disconnect_account(pool, uuid.uuid4())
