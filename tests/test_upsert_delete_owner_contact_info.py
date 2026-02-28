"""Tests for upsert_owner_contact_info() and delete_owner_contact_info().

Verifies:
- Upsert creates a new contact_info row on the owner contact.
- Upsert replaces an existing row (value change).
- Upsert returns False when owner contact is missing.
- Upsert returns False when tables don't exist.
- Delete removes matching rows and returns True.
- Delete returns False when nothing to delete.
- Delete returns False when owner contact or tables missing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import delete_owner_contact_info, upsert_owner_contact_info

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    owner_id: str | None = "owner-uuid-1",
    execute_return: str = "DELETE 1",
    raises: Exception | None = None,
) -> tuple[MagicMock, AsyncMock]:
    """Build an asyncpg pool mock for upsert/delete owner_contact_info tests."""
    conn = AsyncMock()

    if raises is not None:
        conn.fetchrow = AsyncMock(side_effect=raises)
        conn.execute = AsyncMock(side_effect=raises)
    else:
        # fetchrow returns the owner contact row (or None)
        if owner_id is not None:
            owner_row = MagicMock()
            owner_row.__getitem__ = MagicMock(
                side_effect=lambda k: owner_id if k == "id" else None
            )
            conn.fetchrow = AsyncMock(return_value=owner_row)
        else:
            conn.fetchrow = AsyncMock(return_value=None)

        conn.execute = AsyncMock(return_value=execute_return)

    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


# ---------------------------------------------------------------------------
# upsert_owner_contact_info
# ---------------------------------------------------------------------------


class TestUpsertOwnerContactInfo:
    async def test_creates_new_row(self) -> None:
        """Upsert inserts a new contact_info row on the owner contact."""
        pool, conn = _make_pool(owner_id="owner-uuid-1")
        result = await upsert_owner_contact_info(pool, "google_oauth_refresh", "token-value")
        assert result is True

        # Should have: fetchrow (owner), execute (DELETE), execute (INSERT)
        assert conn.fetchrow.await_count == 1
        assert conn.execute.await_count == 2

        # First execute: DELETE existing
        delete_call = conn.execute.call_args_list[0]
        assert "DELETE" in delete_call[0][0]
        assert delete_call[0][1] == "owner-uuid-1"
        assert delete_call[0][2] == "google_oauth_refresh"

        # Second execute: INSERT new
        insert_call = conn.execute.call_args_list[1]
        assert "INSERT" in insert_call[0][0]
        assert insert_call[0][1] == "owner-uuid-1"
        assert insert_call[0][2] == "google_oauth_refresh"
        assert insert_call[0][3] == "token-value"
        assert insert_call[0][4] is True  # secured=True

    async def test_replaces_existing_row(self) -> None:
        """Upsert with a different value replaces the existing row."""
        pool, conn = _make_pool(owner_id="owner-uuid-1")
        result = await upsert_owner_contact_info(pool, "google_oauth_refresh", "new-token")
        assert result is True
        # The DELETE + INSERT pattern handles replacement
        insert_call = conn.execute.call_args_list[1]
        assert insert_call[0][3] == "new-token"

    async def test_returns_false_when_no_owner_contact(self) -> None:
        """Upsert returns False when no owner contact exists."""
        pool, conn = _make_pool(owner_id=None)
        result = await upsert_owner_contact_info(pool, "google_oauth_refresh", "token")
        assert result is False
        # Should not have tried to execute any writes
        conn.execute.assert_not_awaited()

    async def test_returns_false_when_table_missing(self) -> None:
        """Upsert returns False when shared.contacts table doesn't exist."""
        UndefinedTableError = type("UndefinedTableError", (Exception,), {})
        exc = UndefinedTableError('relation "shared.contacts" does not exist')
        pool, _ = _make_pool(raises=exc)
        result = await upsert_owner_contact_info(pool, "google_oauth_refresh", "token")
        assert result is False

    async def test_respects_secured_flag(self) -> None:
        """Upsert passes the secured flag to the INSERT."""
        pool, conn = _make_pool(owner_id="owner-uuid-1")
        await upsert_owner_contact_info(
            pool, "google_oauth_refresh", "token", secured=False
        )
        insert_call = conn.execute.call_args_list[1]
        assert insert_call[0][4] is False  # secured=False

    async def test_reraises_unexpected_errors(self) -> None:
        """Non-table-missing errors are re-raised."""
        exc = RuntimeError("connection lost")
        pool, _ = _make_pool(raises=exc)
        with pytest.raises(RuntimeError, match="connection lost"):
            await upsert_owner_contact_info(pool, "google_oauth_refresh", "token")


# ---------------------------------------------------------------------------
# delete_owner_contact_info
# ---------------------------------------------------------------------------


class TestDeleteOwnerContactInfo:
    async def test_deletes_matching_row(self) -> None:
        """Delete removes the matching contact_info row and returns True."""
        pool, conn = _make_pool(owner_id="owner-uuid-1", execute_return="DELETE 1")
        result = await delete_owner_contact_info(pool, "google_oauth_refresh")
        assert result is True

        # Should have: fetchrow (owner), execute (DELETE)
        assert conn.fetchrow.await_count == 1
        delete_call = conn.execute.call_args_list[0]
        assert "DELETE" in delete_call[0][0]
        assert delete_call[0][1] == "owner-uuid-1"
        assert delete_call[0][2] == "google_oauth_refresh"

    async def test_returns_false_when_nothing_to_delete(self) -> None:
        """Delete returns False when no matching row exists."""
        pool, _ = _make_pool(owner_id="owner-uuid-1", execute_return="DELETE 0")
        result = await delete_owner_contact_info(pool, "google_oauth_refresh")
        assert result is False

    async def test_returns_false_when_no_owner_contact(self) -> None:
        """Delete returns False when no owner contact exists."""
        pool, conn = _make_pool(owner_id=None)
        result = await delete_owner_contact_info(pool, "google_oauth_refresh")
        assert result is False
        conn.execute.assert_not_awaited()

    async def test_returns_false_when_table_missing(self) -> None:
        """Delete returns False when tables don't exist."""
        UndefinedTableError = type("UndefinedTableError", (Exception,), {})
        exc = UndefinedTableError('relation "shared.contacts" does not exist')
        pool, _ = _make_pool(raises=exc)
        result = await delete_owner_contact_info(pool, "google_oauth_refresh")
        assert result is False

    async def test_reraises_unexpected_errors(self) -> None:
        """Non-table-missing errors are re-raised."""
        exc = RuntimeError("disk full")
        pool, _ = _make_pool(raises=exc)
        with pytest.raises(RuntimeError, match="disk full"):
            await delete_owner_contact_info(pool, "google_oauth_refresh")
