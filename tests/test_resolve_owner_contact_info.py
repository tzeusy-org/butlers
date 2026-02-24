"""Tests for resolve_owner_contact_info() in credential_store.

Verifies:
- Owner contact with a telegram contact_info entry resolves the value.
- Primary entry is preferred over non-primary entries.
- Unknown type returns None.
- Missing owner contact returns None.
- Missing tables (shared.contacts / shared.contact_info) return None gracefully.
- Unique constraint violations (duplicate type values) are handled gracefully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import resolve_owner_contact_info

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, fetchrow_return=None, raises: Exception | None = None) -> MagicMock:
    """Build an asyncpg pool mock for resolve_owner_contact_info tests."""
    conn = AsyncMock()

    if raises is not None:
        conn.fetchrow = AsyncMock(side_effect=raises)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def _make_row(value: str) -> MagicMock:
    """Build a minimal asyncpg row mock with a 'value' key."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: value if k == "value" else None)
    return row


# ---------------------------------------------------------------------------
# Happy-path resolution
# ---------------------------------------------------------------------------


class TestResolveOwnerContactInfoSuccess:
    async def test_returns_value_for_known_type(self) -> None:
        """resolve_owner_contact_info returns the contact_info value for a known type."""
        row = _make_row("123456789")
        pool, conn = _make_pool(fetchrow_return=row)

        result = await resolve_owner_contact_info(pool, "telegram")

        assert result == "123456789"

    async def test_queries_with_correct_type(self) -> None:
        """The query passes the info_type as a parameter."""
        row = _make_row("user@example.com")
        pool, conn = _make_pool(fetchrow_return=row)

        await resolve_owner_contact_info(pool, "email")

        conn.fetchrow.assert_awaited_once()
        call_args = conn.fetchrow.await_args
        # Second positional arg is the type parameter
        assert call_args.args[1] == "email"

    async def test_sql_references_owner_role(self) -> None:
        """The query references the owner role in the WHERE clause."""
        row = _make_row("bot_token_value")
        pool, conn = _make_pool(fetchrow_return=row)

        await resolve_owner_contact_info(pool, "telegram_bot_token")

        query = conn.fetchrow.await_args.args[0]
        assert "owner" in query
        assert "shared.contact_info" in query
        assert "shared.contacts" in query

    async def test_orders_primary_first(self) -> None:
        """The query includes ORDER BY is_primary DESC to prefer primary entries."""
        row = _make_row("primary_value")
        pool, conn = _make_pool(fetchrow_return=row)

        await resolve_owner_contact_info(pool, "telegram")

        query = conn.fetchrow.await_args.args[0]
        assert "is_primary" in query

    async def test_strips_whitespace_from_value(self) -> None:
        """Values with surrounding whitespace are stripped before returning."""
        row = _make_row("  987654321  ")
        pool, conn = _make_pool(fetchrow_return=row)

        result = await resolve_owner_contact_info(pool, "telegram")

        assert result == "987654321"


# ---------------------------------------------------------------------------
# None-return cases
# ---------------------------------------------------------------------------


class TestResolveOwnerContactInfoNoneReturned:
    async def test_returns_none_when_no_row(self) -> None:
        """Returns None when no contact_info row is found for the type."""
        pool, _conn = _make_pool(fetchrow_return=None)

        result = await resolve_owner_contact_info(pool, "telegram")

        assert result is None

    async def test_returns_none_for_empty_value(self) -> None:
        """Returns None when the stored value is an empty string."""
        row = _make_row("")
        pool, _conn = _make_pool(fetchrow_return=row)

        result = await resolve_owner_contact_info(pool, "email")

        assert result is None

    async def test_returns_none_for_whitespace_only_value(self) -> None:
        """Returns None when the stored value is whitespace only."""
        row = _make_row("   ")
        pool, _conn = _make_pool(fetchrow_return=row)

        result = await resolve_owner_contact_info(pool, "telegram")

        assert result is None


# ---------------------------------------------------------------------------
# Missing-table / schema error tolerance
# ---------------------------------------------------------------------------


class TestResolveOwnerContactInfoMissingTable:
    async def test_returns_none_on_undefined_table_error(self) -> None:
        """Returns None (does not raise) when shared.contact_info is missing."""

        class UndefinedTableError(Exception):
            pass

        pool, _conn = _make_pool(raises=UndefinedTableError("relation does not exist"))

        result = await resolve_owner_contact_info(pool, "telegram")

        assert result is None

    async def test_returns_none_when_table_message_in_error(self) -> None:
        """Returns None when error message contains 'does not exist'."""
        pool, _conn = _make_pool(raises=Exception("relation shared.contact_info does not exist"))

        result = await resolve_owner_contact_info(pool, "telegram")

        assert result is None

    async def test_reraises_unexpected_errors(self) -> None:
        """Unexpected DB errors (not missing-table) are re-raised."""
        pool, _conn = _make_pool(raises=RuntimeError("DB connection timeout"))

        with pytest.raises(RuntimeError, match="DB connection timeout"):
            await resolve_owner_contact_info(pool, "telegram")

    async def test_reraises_not_null_constraint_violation(self) -> None:
        """NOT NULL constraint errors are re-raised (not swallowed as 'missing table')."""
        # asyncpg NOT NULL violations mention 'column' in their message;
        # _is_missing_column_or_schema_error must NOT match them.
        err = Exception(
            'null value in column "contact_id" of relation "shared.contact_info" '
            "violates not-null constraint"
        )
        pool, _conn = _make_pool(raises=err)

        with pytest.raises(Exception, match="not-null constraint"):
            await resolve_owner_contact_info(pool, "telegram")

    async def test_reraises_permission_denied_for_schema(self) -> None:
        """Schema permission errors are re-raised (not swallowed as 'missing schema')."""
        err = Exception("permission denied for schema shared")
        pool, _conn = _make_pool(raises=err)

        with pytest.raises(Exception, match="permission denied"):
            await resolve_owner_contact_info(pool, "telegram")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestResolveOwnerContactInfoLogging:
    async def test_logs_debug_on_success(self, caplog: pytest.LogCaptureFixture) -> None:
        """A debug-level message is emitted when a value is resolved."""
        import logging

        row = _make_row("12345")
        pool, _conn = _make_pool(fetchrow_return=row)

        with caplog.at_level(logging.DEBUG, logger="butlers.credential_store"):
            await resolve_owner_contact_info(pool, "telegram")

        assert any("telegram" in record.message for record in caplog.records)

    async def test_logs_debug_on_missing_table_skip(self, caplog: pytest.LogCaptureFixture) -> None:
        """A debug-level message is emitted when the table is absent."""
        import logging

        pool, _conn = _make_pool(raises=Exception("relation shared.contact_info does not exist"))

        with caplog.at_level(logging.DEBUG, logger="butlers.credential_store"):
            await resolve_owner_contact_info(pool, "telegram")

        assert any("skipped" in record.message.lower() for record in caplog.records)
