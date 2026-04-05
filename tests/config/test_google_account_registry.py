"""Tests for the Google Account Registry (butlers.google_account_registry)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.google_account_registry import (
    GoogleAccount,
    GoogleAccountAlreadyExistsError,
    GoogleAccountLimitExceededError,
    GoogleAccountNotFoundError,
    MissingGoogleCredentialsError,
    create_google_account,
    disconnect_account,
    get_google_account,
    list_google_accounts,
    set_primary_account,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 11, 0, 0, 0, tzinfo=UTC)
_ACCOUNT_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_ENTITY_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
_ACCOUNT_ID_2 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
_ENTITY_ID_2 = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _make_account_row(
    *,
    id=_ACCOUNT_ID,
    entity_id=_ENTITY_ID,
    email="test@gmail.com",
    display_name="Test User",
    is_primary=True,
    granted_scopes=None,
    status="active",
    connected_at=_NOW,
    last_token_refresh_at=None,
):
    data = {
        "id": id,
        "entity_id": entity_id,
        "email": email,
        "display_name": display_name,
        "is_primary": is_primary,
        "granted_scopes": granted_scopes or [],
        "status": status,
        "connected_at": connected_at,
        "last_token_refresh_at": last_token_refresh_at,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_id_row(id: uuid.UUID) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: id if k == "id" else None)
    return row


def _make_count_row(n: int) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: n if k == "cnt" else None)
    return row


def _make_value_row(value: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: value if k == "value" else None)
    return row


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _FakeConn:
    def __init__(self):
        self.fetchrow = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock(return_value="")
        self._tx = _FakeTx()

    def transaction(self):
        return self._tx


def _make_pool(conn: _FakeConn) -> MagicMock:
    pool = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


# ---------------------------------------------------------------------------
# GoogleAccount._from_row, create, list, get, set_primary
# ---------------------------------------------------------------------------


def test_google_account_from_row() -> None:
    account = GoogleAccount._from_row(_make_account_row(granted_scopes=["gmail.modify"]))
    assert account.id == _ACCOUNT_ID and account.email == "test@gmail.com"
    assert account.granted_scopes == ["gmail.modify"]


async def test_create_list_get_set_primary() -> None:
    """create: first=primary, second=not-primary, duplicate raises, limit raises.
    list: primary first. get: primary by default, raises when missing.
    set_primary: returns updated account; raises not-found."""
    # First account is primary
    conn = _FakeConn()
    conn.fetchrow = AsyncMock(
        side_effect=[
            _make_count_row(0),
            None,
            None,
            _make_id_row(_ENTITY_ID),
            _make_account_row(is_primary=True),
        ]
    )
    assert (
        await create_google_account(_make_pool(conn), email="first@gmail.com")
    ).is_primary is True

    # Second account is not primary
    conn2 = _FakeConn()
    ep = MagicMock()
    ep.__getitem__ = MagicMock(return_value=True)
    conn2.fetchrow = AsyncMock(
        side_effect=[
            _make_count_row(1),
            None,
            ep,
            _make_id_row(_ENTITY_ID_2),
            _make_account_row(
                id=_ACCOUNT_ID_2, entity_id=_ENTITY_ID_2, email="second@gmail.com", is_primary=False
            ),
        ]
    )
    assert (
        await create_google_account(_make_pool(conn2), email="second@gmail.com")
    ).is_primary is False

    # Duplicate raises
    conn3 = _FakeConn()
    conn3.fetchrow = AsyncMock(side_effect=[_make_count_row(1), _make_id_row(_ACCOUNT_ID)])
    with pytest.raises(GoogleAccountAlreadyExistsError):
        await create_google_account(_make_pool(conn3), email="test@gmail.com")

    # Limit raises
    conn4 = _FakeConn()
    conn4.fetchrow = AsyncMock(side_effect=[_make_count_row(10)])
    with pytest.raises(GoogleAccountLimitExceededError):
        await create_google_account(_make_pool(conn4), email="new@gmail.com")

    # list: primary first
    conn5 = _FakeConn()
    conn5.fetch = AsyncMock(
        return_value=[
            _make_account_row(is_primary=True),
            _make_account_row(id=_ACCOUNT_ID_2, is_primary=False),
        ]
    )
    accounts = await list_google_accounts(_make_pool(conn5))
    assert len(accounts) == 2 and accounts[0].is_primary is True

    # get: primary
    conn6 = _FakeConn()
    conn6.fetchrow = AsyncMock(return_value=_make_account_row(is_primary=True))
    assert (await get_google_account(_make_pool(conn6))).is_primary is True

    # get: raises missing
    conn7 = _FakeConn()
    conn7.fetchrow = AsyncMock(return_value=None)
    pool7 = _make_pool(conn7)
    with pytest.raises(MissingGoogleCredentialsError):
        await get_google_account(pool7)
    with pytest.raises(GoogleAccountNotFoundError):
        await get_google_account(pool7, "missing@gmail.com")

    # set_primary
    conn8 = _FakeConn()
    conn8.fetchrow = AsyncMock(
        side_effect=[_make_id_row(_ACCOUNT_ID), _make_account_row(is_primary=True)]
    )
    assert (await set_primary_account(_make_pool(conn8), _ACCOUNT_ID)).is_primary is True

    conn9 = _FakeConn()
    conn9.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(GoogleAccountNotFoundError):
        await set_primary_account(_make_pool(conn9), uuid.uuid4())


# ---------------------------------------------------------------------------
# disconnect_account
# ---------------------------------------------------------------------------


class _MultiPool:
    """Pool that serves different connections on successive acquires."""

    def __init__(self, *conns):
        self._conns = list(conns)
        self._idx = -1
        self._pool = MagicMock()
        self._pool.acquire = MagicMock(side_effect=self._acquire)

    def _acquire(self):
        self._idx += 1
        conn = self._conns[min(self._idx, len(self._conns) - 1)]
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    @property
    def pool(self):
        return self._pool


def _make_disconnect_conns(
    *, account_exists=True, was_primary=True, has_next=True, has_refresh_token=True
):
    conn1 = _FakeConn()
    conn2 = _FakeConn()
    if not account_exists:
        conn1.fetchrow = AsyncMock(return_value=None)
    else:
        data = {
            "id": _ACCOUNT_ID,
            "entity_id": _ENTITY_ID,
            "is_primary": was_primary,
            "status": "active",
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        token_row = _make_value_row("refresh-token") if has_refresh_token else None
        conn1.fetchrow = AsyncMock(side_effect=[row, token_row])
    conn2.fetchrow = AsyncMock(return_value=_make_id_row(_ACCOUNT_ID_2) if has_next else None)
    return conn1, conn2


async def test_disconnect_account() -> None:
    """Disconnect: marks revoked; auto-promotes if was primary; not-found raises."""
    conn1, conn2 = _make_disconnect_conns()
    mp = _MultiPool(conn1, conn2)
    with patch("butlers.google_account_registry._revoke_token_with_google", new=AsyncMock()):
        await disconnect_account(mp.pool, _ACCOUNT_ID)
    calls = [str(c[0][0]) for c in conn2.execute.call_args_list]
    assert any("status = 'revoked'" in c for c in calls)
    assert any("DELETE FROM public.entity_info" in c for c in calls)
    assert any("is_primary = true" in c for c in calls)

    # non-primary: no auto-promote
    c1b, c2b = _make_disconnect_conns(was_primary=False)
    mp2 = _MultiPool(c1b, c2b)
    with patch("butlers.google_account_registry._revoke_token_with_google", new=AsyncMock()):
        await disconnect_account(mp2.pool, _ACCOUNT_ID)
    assert not any("is_primary = true" in str(c[0][0]) for c in c2b.execute.call_args_list)

    # not found
    c1c, _ = _make_disconnect_conns(account_exists=False)
    mp3 = _MultiPool(c1c)
    with pytest.raises(GoogleAccountNotFoundError):
        await disconnect_account(mp3.pool, uuid.uuid4())

    # revocation failure doesn't block
    c1d, c2d = _make_disconnect_conns()
    mp4 = _MultiPool(c1d, c2d)

    async def _fail(*_, **__):
        raise OSError("network unreachable")

    with patch("butlers.google_account_registry._revoke_token_with_google", new=_fail):
        await disconnect_account(mp4.pool, _ACCOUNT_ID)
    assert any("status = 'revoked'" in str(c[0][0]) for c in c2d.execute.call_args_list)
