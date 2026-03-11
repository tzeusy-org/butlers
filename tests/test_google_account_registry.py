"""Tests for the Google Account Registry (butlers.google_account_registry).

Covers:
- create_google_account: success, first-account-is-primary, duplicate email,
  limit enforcement, refresh token persistence.
- list_google_accounts: empty list, ordering (primary first).
- get_google_account: by email, by UUID str, by UUID object, primary default,
  missing primary raises MissingGoogleCredentialsError, missing account raises
  GoogleAccountNotFoundError.
- set_primary_account: atomic swap, non-existent account raises.
- disconnect_account: soft revoke (status=revoked, token deleted, auto-promote),
  hard delete (entity cascade, auto-promote), single account no auto-promote,
  non-existent account raises.

All tests use asyncpg pool/connection mocks (no live DB).
"""

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 11, 0, 0, 0, tzinfo=UTC)

_ACCOUNT_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_ENTITY_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
_ACCOUNT_ID_2 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
_ENTITY_ID_2 = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _make_account_row(
    *,
    id: uuid.UUID = _ACCOUNT_ID,
    entity_id: uuid.UUID = _ENTITY_ID,
    email: str | None = "test@gmail.com",
    display_name: str | None = "Test User",
    is_primary: bool = True,
    granted_scopes: list[str] | None = None,
    status: str = "active",
    connected_at: datetime = _NOW,
    last_token_refresh_at: datetime | None = None,
) -> MagicMock:
    """Build a minimal asyncpg row mock for a google_accounts row."""
    scopes = granted_scopes or []
    data = {
        "id": id,
        "entity_id": entity_id,
        "email": email,
        "display_name": display_name,
        "is_primary": is_primary,
        "granted_scopes": scopes,
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
# GoogleAccount._from_row
# ---------------------------------------------------------------------------


class TestGoogleAccountFromRow:
    def test_round_trip(self) -> None:
        row = _make_account_row(granted_scopes=["gmail.modify"])
        account = GoogleAccount._from_row(row)
        assert account.id == _ACCOUNT_ID
        assert account.entity_id == _ENTITY_ID
        assert account.email == "test@gmail.com"
        assert account.display_name == "Test User"
        assert account.is_primary is True
        assert account.granted_scopes == ["gmail.modify"]
        assert account.status == "active"
        assert account.connected_at == _NOW
        assert account.last_token_refresh_at is None

    def test_null_scopes_become_empty_list(self) -> None:
        row = _make_account_row(granted_scopes=None)
        account = GoogleAccount._from_row(row)
        assert account.granted_scopes == []


# ---------------------------------------------------------------------------
# create_google_account
# ---------------------------------------------------------------------------


class TestCreateGoogleAccount:
    async def test_creates_first_account_as_primary(self) -> None:
        """First account should have is_primary=True."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        # count_active returns 0, has_primary returns None (no row)
        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_count_row(0),  # _count_active_accounts
                None,  # duplicate email check
                None,  # _has_primary_account
                _make_id_row(_ENTITY_ID),  # _create_companion_entity
                _make_account_row(is_primary=True),  # INSERT RETURNING
            ]
        )

        account = await create_google_account(pool, email="first@gmail.com")

        assert account.is_primary is True

    async def test_creates_subsequent_account_not_primary(self) -> None:
        """Second account should not be primary."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        existing_primary_row = MagicMock()
        existing_primary_row.__getitem__ = MagicMock(return_value=True)

        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_count_row(1),  # _count_active_accounts
                None,  # duplicate email check
                existing_primary_row,  # _has_primary_account → has a row
                _make_id_row(_ENTITY_ID_2),  # _create_companion_entity
                _make_account_row(
                    id=_ACCOUNT_ID_2,
                    entity_id=_ENTITY_ID_2,
                    email="second@gmail.com",
                    is_primary=False,
                ),  # INSERT RETURNING
            ]
        )

        account = await create_google_account(pool, email="second@gmail.com")

        assert account.is_primary is False

    async def test_raises_on_duplicate_email(self) -> None:
        """Duplicate email raises GoogleAccountAlreadyExistsError."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_count_row(1),  # _count_active_accounts
                _make_id_row(_ACCOUNT_ID),  # duplicate email check returns existing row
            ]
        )

        with pytest.raises(GoogleAccountAlreadyExistsError, match="already connected"):
            await create_google_account(pool, email="test@gmail.com")

    async def test_raises_on_limit_exceeded(self) -> None:
        """Creating an account when limit is reached raises GoogleAccountLimitExceededError."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_count_row(10),  # _count_active_accounts → at limit
            ]
        )

        with pytest.raises(GoogleAccountLimitExceededError, match="limit reached"):
            await create_google_account(pool, email="new@gmail.com")

    async def test_persists_refresh_token_when_provided(self) -> None:
        """Refresh token is stored in entity_info when provided."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_count_row(0),
                None,  # no duplicate
                None,  # no existing primary
                _make_id_row(_ENTITY_ID),
                _make_account_row(),
            ]
        )

        await create_google_account(
            pool,
            email="test@gmail.com",
            refresh_token="refresh-abc-123",
        )

        # Verify execute was called (for INSERT into entity_info)
        assert conn.execute.await_count >= 1
        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("entity_info" in c for c in execute_calls)

    async def test_scope_tracking(self) -> None:
        """Granted scopes are passed through to the INSERT."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        scopes = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
        ]

        conn.fetchrow = AsyncMock(
            side_effect=[
                _make_count_row(0),
                None,
                None,
                _make_id_row(_ENTITY_ID),
                _make_account_row(granted_scopes=scopes),
            ]
        )

        account = await create_google_account(pool, email="test@gmail.com", scopes=scopes)

        assert account.granted_scopes == scopes

    async def test_custom_limit_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GOOGLE_MAX_ACCOUNTS env var controls the soft limit."""
        monkeypatch.setenv("GOOGLE_MAX_ACCOUNTS", "3")

        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(side_effect=[_make_count_row(3)])

        with pytest.raises(GoogleAccountLimitExceededError):
            await create_google_account(pool, email="x@gmail.com")


# ---------------------------------------------------------------------------
# list_google_accounts
# ---------------------------------------------------------------------------


class TestListGoogleAccounts:
    async def test_returns_empty_list_when_no_accounts(self) -> None:
        conn = _FakeConn()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        result = await list_google_accounts(pool)

        assert result == []

    async def test_returns_accounts_in_order(self) -> None:
        """Primary account should come first."""
        conn = _FakeConn()
        pool = _make_pool(conn)

        primary_row = _make_account_row(id=_ACCOUNT_ID, email="primary@gmail.com", is_primary=True)
        secondary_row = _make_account_row(
            id=_ACCOUNT_ID_2, email="secondary@gmail.com", is_primary=False
        )

        conn.fetch = AsyncMock(return_value=[primary_row, secondary_row])

        accounts = await list_google_accounts(pool)

        assert len(accounts) == 2
        assert accounts[0].is_primary is True
        assert accounts[0].email == "primary@gmail.com"
        assert accounts[1].is_primary is False

    async def test_fetch_query_contains_ordering(self) -> None:
        """Query should ORDER BY is_primary DESC, connected_at ASC."""
        conn = _FakeConn()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        await list_google_accounts(pool)

        call_sql = conn.fetch.call_args[0][0]
        assert "is_primary DESC" in call_sql
        assert "connected_at ASC" in call_sql


# ---------------------------------------------------------------------------
# get_google_account
# ---------------------------------------------------------------------------


class TestGetGoogleAccount:
    async def test_returns_primary_when_no_account_arg(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(return_value=_make_account_row(is_primary=True))

        account = await get_google_account(pool)

        assert account.is_primary is True
        call_sql = conn.fetchrow.call_args[0][0]
        assert "is_primary = true" in call_sql

    async def test_raises_missing_credentials_when_no_primary(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(MissingGoogleCredentialsError):
            await get_google_account(pool)

    async def test_lookup_by_email_string(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(return_value=_make_account_row(email="test@gmail.com"))

        account = await get_google_account(pool, "test@gmail.com")

        assert account.email == "test@gmail.com"
        call_sql = conn.fetchrow.call_args[0][0]
        assert "email = $1" in call_sql

    async def test_lookup_by_uuid_string(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))

        account = await get_google_account(pool, str(_ACCOUNT_ID))

        assert account.id == _ACCOUNT_ID
        call_sql = conn.fetchrow.call_args[0][0]
        assert "id = $1" in call_sql

    async def test_lookup_by_uuid_object(self) -> None:
        conn = _FakeConn()
        pool = _make_pool(conn)

        conn.fetchrow = AsyncMock(return_value=_make_account_row(id=_ACCOUNT_ID))

        account = await get_google_account(pool, _ACCOUNT_ID)

        assert account.id == _ACCOUNT_ID

    async def test_raises_not_found_for_missing_email(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(GoogleAccountNotFoundError, match="missing@gmail.com"):
            await get_google_account(pool, "missing@gmail.com")

    async def test_raises_not_found_for_missing_uuid(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(GoogleAccountNotFoundError):
            await get_google_account(pool, uuid.uuid4())


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

        # Should clear old primary and set new one.
        execute_calls = [str(call[0][0]) for call in conn.execute.call_args_list]
        assert any("is_primary = false" in c for c in execute_calls)
        update_new_call = [c for c in execute_calls if "is_primary = true" in c]
        assert len(update_new_call) >= 1

    async def test_raises_not_found_for_missing_account(self) -> None:
        conn = _FakeConn()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(conn)

        with pytest.raises(GoogleAccountNotFoundError):
            await set_primary_account(pool, uuid.uuid4())


# ---------------------------------------------------------------------------
# disconnect_account
# ---------------------------------------------------------------------------


class TestDisconnectAccount:
    async def _make_disconnect_pool(
        self,
        *,
        account_exists: bool = True,
        was_primary: bool = True,
        has_next: bool = True,
        has_refresh_token: bool = True,
    ) -> tuple[MagicMock, list[_FakeConn]]:
        """Build a pool that returns two connections (one per pool.acquire() call)."""
        # First acquire: fetch account details + refresh token.
        conn1 = _FakeConn()
        # Second acquire: transaction for cleanup.
        conn2 = _FakeConn()

        if not account_exists:
            conn1.fetchrow = AsyncMock(return_value=None)
        else:

            def make_account_row_for_disconnect() -> MagicMock:
                data = {
                    "id": _ACCOUNT_ID,
                    "entity_id": _ENTITY_ID,
                    "is_primary": was_primary,
                    "status": "active",
                }
                row = MagicMock()
                row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
                return row

            token_row = _make_value_row("refresh-token") if has_refresh_token else None

            conn1.fetchrow = AsyncMock(
                side_effect=[
                    make_account_row_for_disconnect(),  # account details
                    token_row,  # refresh token
                ]
            )

        if has_next:
            conn2.fetchrow = AsyncMock(return_value=_make_id_row(_ACCOUNT_ID_2))
        else:
            conn2.fetchrow = AsyncMock(return_value=None)

        conns = [conn1, conn2]
        idx = [-1]

        pool = MagicMock()

        def _acquire() -> MagicMock:
            idx[0] += 1
            conn = conns[min(idx[0], len(conns) - 1)]
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=conn)
            ctx.__aexit__ = AsyncMock(return_value=None)
            return ctx

        pool.acquire = MagicMock(side_effect=_acquire)
        return pool, conns

    async def test_soft_disconnect_marks_revoked(self) -> None:
        pool, conns = await self._make_disconnect_pool()
        conn2 = conns[1]

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ):
            await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        assert any("status = 'revoked'" in c for c in execute_calls)

    async def test_soft_disconnect_deletes_entity_info(self) -> None:
        pool, conns = await self._make_disconnect_pool()
        conn2 = conns[1]

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ):
            await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        assert any("DELETE FROM shared.entity_info" in c for c in execute_calls)

    async def test_auto_promotes_oldest_when_was_primary(self) -> None:
        """After disconnecting the primary, oldest active account is auto-promoted."""
        pool, conns = await self._make_disconnect_pool(was_primary=True, has_next=True)
        conn2 = conns[1]

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ):
            await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        # Should set is_primary = true on the next account.
        assert any("is_primary = true" in c for c in execute_calls)

    async def test_no_auto_promote_when_no_remaining(self) -> None:
        """No auto-promote when the disconnected account was the only one."""
        pool, conns = await self._make_disconnect_pool(was_primary=True, has_next=False)
        conn2 = conns[1]

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ):
            await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        assert not any("is_primary = true" in c for c in execute_calls)

    async def test_no_auto_promote_when_not_primary(self) -> None:
        """Non-primary account disconnect does not trigger auto-promote."""
        pool, conns = await self._make_disconnect_pool(was_primary=False, has_next=True)
        conn2 = conns[1]

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ):
            await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        assert not any("is_primary = true" in c for c in execute_calls)

    async def test_hard_delete_removes_entity(self) -> None:
        pool, conns = await self._make_disconnect_pool()
        conn2 = conns[1]

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ):
            await disconnect_account(pool, _ACCOUNT_ID, hard_delete=True)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        assert any("DELETE FROM shared.entities" in c for c in execute_calls)

    async def test_raises_not_found_for_missing_account(self) -> None:
        pool, _ = await self._make_disconnect_pool(account_exists=False)

        with pytest.raises(GoogleAccountNotFoundError):
            await disconnect_account(pool, uuid.uuid4())

    async def test_revocation_failure_does_not_block_cleanup(self) -> None:
        """A network error during revocation must not prevent local cleanup."""
        pool, conns = await self._make_disconnect_pool()
        conn2 = conns[1]

        async def _fail(*_: object, **__: object) -> None:
            raise OSError("network unreachable")

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=_fail,
        ):
            # Should not raise; local cleanup should still proceed.
            await disconnect_account(pool, _ACCOUNT_ID)

        execute_calls = [str(call[0][0]) for call in conn2.execute.call_args_list]
        assert any("status = 'revoked'" in c for c in execute_calls)

    async def test_no_revocation_when_no_refresh_token(self) -> None:
        """When no refresh token exists, revocation endpoint is not called."""
        pool, _ = await self._make_disconnect_pool(has_refresh_token=False)

        with patch(
            "butlers.google_account_registry._revoke_token_with_google",
            new=AsyncMock(),
        ) as mock_revoke:
            await disconnect_account(pool, _ACCOUNT_ID)

        mock_revoke.assert_not_awaited()
