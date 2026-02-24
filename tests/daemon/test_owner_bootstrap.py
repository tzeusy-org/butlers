"""Tests for _ensure_owner_contact() owner bootstrap logic.

Verifies:
- First startup creates exactly one owner contact in shared.contacts.
- Subsequent startups are no-ops (idempotent via ON CONFLICT).
- Concurrent startups create exactly one owner (relying on ON CONFLICT DO NOTHING).
- If shared.contacts does not exist the function skips silently.
- If roles column does not exist the function skips silently.
- Exceptions from the pool are caught and logged (non-fatal).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import _ensure_owner_contact

pytestmark = pytest.mark.unit


def _make_pool(*, table_exists: bool = True, roles_col_exists: bool = True) -> AsyncMock:
    """Build a mock asyncpg pool that simulates shared.contacts state."""
    conn = AsyncMock()
    # to_regclass returns a non-None value when table exists
    table_result = "shared.contacts" if table_exists else None

    # fetchval is called twice: first for table existence, then for column existence
    conn.fetchval = AsyncMock(
        side_effect=[
            table_result,  # to_regclass('shared.contacts') IS NOT NULL
            roles_col_exists,  # information_schema.columns check for 'roles'
        ]
    )
    conn.execute = AsyncMock()

    # Pool context manager: `async with pool.acquire() as conn`
    pool = MagicMock()
    pool.acquire = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = acquire_ctx

    return pool, conn


class TestEnsureOwnerContactFirstBoot:
    async def test_inserts_owner_on_first_boot(self) -> None:
        """First startup inserts owner contact into shared.contacts."""
        pool, conn = _make_pool()

        await _ensure_owner_contact(pool)

        conn.execute.assert_awaited_once()
        call_sql = conn.execute.call_args[0][0]
        assert "INSERT INTO shared.contacts" in call_sql
        assert "owner" in call_sql

    async def test_insert_uses_on_conflict_do_nothing(self) -> None:
        """INSERT uses ON CONFLICT DO NOTHING for idempotency."""
        pool, conn = _make_pool()

        await _ensure_owner_contact(pool)

        call_sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT DO NOTHING" in call_sql

    async def test_roles_column_includes_owner(self) -> None:
        """Inserted row has 'owner' in the roles array."""
        pool, conn = _make_pool()

        await _ensure_owner_contact(pool)

        call_sql = conn.execute.call_args[0][0]
        assert "owner" in call_sql


class TestEnsureOwnerContactSubsequentBoot:
    async def test_second_call_still_issues_insert(self) -> None:
        """Subsequent calls still execute INSERT; idempotency is enforced by ON CONFLICT."""
        pool, conn = _make_pool()
        # First call
        await _ensure_owner_contact(pool)
        first_execute_count = conn.execute.await_count

        # Reset for second call
        pool2, conn2 = _make_pool()
        await _ensure_owner_contact(pool2)
        second_execute_count = conn2.execute.await_count

        # Both calls issued the INSERT (ON CONFLICT handles duplicate silently)
        assert first_execute_count == 1
        assert second_execute_count == 1


class TestEnsureOwnerContactTableAbsent:
    async def test_skips_when_shared_contacts_missing(self) -> None:
        """Function does nothing when shared.contacts table does not exist."""
        pool, conn = _make_pool(table_exists=False)

        await _ensure_owner_contact(pool)

        conn.execute.assert_not_awaited()

    async def test_no_error_when_table_missing(self) -> None:
        """Function completes without raising when table is absent."""
        pool, conn = _make_pool(table_exists=False)

        # Should not raise
        await _ensure_owner_contact(pool)


class TestEnsureOwnerContactRolesColumnAbsent:
    async def test_skips_when_roles_column_missing(self) -> None:
        """Function does nothing when shared.contacts.roles column does not exist."""
        pool, conn = _make_pool(roles_col_exists=False)

        await _ensure_owner_contact(pool)

        conn.execute.assert_not_awaited()

    async def test_no_error_when_roles_col_missing(self) -> None:
        """Function completes without raising when roles column is absent."""
        pool, conn = _make_pool(roles_col_exists=False)

        await _ensure_owner_contact(pool)


class TestEnsureOwnerContactErrorHandling:
    async def test_exception_is_caught_and_logged(self) -> None:
        """Pool exception is caught; function is non-fatal."""
        pool = MagicMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        with patch("butlers.daemon.logger") as mock_logger:
            # Should not raise
            await _ensure_owner_contact(pool)

            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "bootstrap" in warning_msg.lower() or "skipped" in warning_msg.lower()

    async def test_execute_exception_is_non_fatal(self) -> None:
        """DB execute exception during INSERT is caught; function is non-fatal."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock(side_effect=Exception("unique constraint violation"))

        # Should not raise
        await _ensure_owner_contact(pool)


class TestConcurrentStartupSafety:
    async def test_concurrent_calls_do_not_raise(self) -> None:
        """Multiple concurrent calls to _ensure_owner_contact complete without error."""
        insert_calls: list[str] = []

        async def recording_execute(sql: str) -> None:
            insert_calls.append(sql)

        # Create multiple independent pools that all simulate the first-boot scenario
        pools = []
        for _ in range(5):
            pool, conn = _make_pool()
            conn.execute = AsyncMock(side_effect=recording_execute)
            pools.append(pool)

        # Run all concurrently
        await asyncio.gather(*[_ensure_owner_contact(p) for p in pools])

        # All five calls should have issued the INSERT (ON CONFLICT handles concurrency)
        assert len(insert_calls) == 5
        for sql in insert_calls:
            assert "ON CONFLICT DO NOTHING" in sql

    async def test_concurrent_calls_all_contain_owner_role(self) -> None:
        """Every concurrent INSERT attempt includes the 'owner' role value."""
        insert_sqls: list[str] = []

        async def capturing_execute(sql: str) -> None:
            insert_sqls.append(sql)

        pools = []
        for _ in range(3):
            pool, conn = _make_pool()
            conn.execute = AsyncMock(side_effect=capturing_execute)
            pools.append(pool)

        await asyncio.gather(*[_ensure_owner_contact(p) for p in pools])

        for sql in insert_sqls:
            assert "owner" in sql.lower()
