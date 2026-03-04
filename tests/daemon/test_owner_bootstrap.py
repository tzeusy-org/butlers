"""Tests for _ensure_owner_entity_and_contact() owner bootstrap logic.

Verifies:
- First startup creates owner entity in shared.entities, then owner contact
  in shared.contacts linked via entity_id.
- Subsequent startups are no-ops (idempotent via ON CONFLICT).
- Concurrent startups create exactly one owner (relying on ON CONFLICT DO NOTHING).
- If shared.entities does not exist, falls back to contact-only bootstrap.
- If shared.contacts does not exist the function skips silently.
- If roles column on contacts does not exist the function skips silently.
- Exceptions from the pool are caught and logged (non-fatal).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import _ensure_owner_entity_and_contact

pytestmark = pytest.mark.unit

_OWNER_ENTITY_ID = uuid.uuid4()


def _make_pool(
    *,
    entities_table_exists: bool = True,
    roles_on_entities: bool = True,
    contacts_table_exists: bool = True,
    roles_col_exists: bool = True,
    entity_insert_returns: uuid.UUID | None = _OWNER_ENTITY_ID,
    entity_select_returns: uuid.UUID | None = None,
) -> tuple[MagicMock, AsyncMock]:
    """Build a mock asyncpg pool that simulates shared.entities + shared.contacts state."""
    conn = AsyncMock()

    # Build the sequence of fetchval returns:
    # 1. to_regclass('shared.entities') IS NOT NULL
    # 2. (if entities exist) information_schema check for roles on entities
    # 3. (if roles on entities) INSERT RETURNING id (entity)
    # 4. (if insert returned None) SELECT id (entity)
    # 5. to_regclass('shared.contacts') IS NOT NULL
    # 6. (if contacts exist) information_schema check for roles on contacts
    fetchval_results: list = []

    fetchval_results.append(entities_table_exists)

    if entities_table_exists:
        fetchval_results.append(roles_on_entities)
        if roles_on_entities:
            fetchval_results.append(entity_insert_returns)
            if entity_insert_returns is None:
                fetchval_results.append(entity_select_returns)

    fetchval_results.append(contacts_table_exists)
    if contacts_table_exists:
        fetchval_results.append(roles_col_exists)

    conn.fetchval = AsyncMock(side_effect=fetchval_results)
    conn.execute = AsyncMock()

    pool = MagicMock()
    pool.acquire = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = acquire_ctx

    return pool, conn


class TestEnsureOwnerEntityAndContactFirstBoot:
    async def test_creates_entity_then_contact(self) -> None:
        """First startup creates entity in shared.entities, then contact linked to it."""
        pool, conn = _make_pool()

        await _ensure_owner_entity_and_contact(pool)

        conn.execute.assert_awaited_once()
        call_sql = conn.execute.call_args[0][0]
        assert "INSERT INTO shared.contacts" in call_sql
        assert "entity_id" in call_sql
        # Verify entity_id is passed as parameter
        call_args = conn.execute.call_args[0]
        assert _OWNER_ENTITY_ID in call_args

    async def test_insert_uses_on_conflict_do_nothing(self) -> None:
        """Contact INSERT uses ON CONFLICT DO NOTHING for idempotency."""
        pool, conn = _make_pool()

        await _ensure_owner_entity_and_contact(pool)

        call_sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT DO NOTHING" in call_sql

    async def test_entity_insert_includes_owner_role(self) -> None:
        """Entity INSERT includes 'owner' in the roles array."""
        pool, conn = _make_pool()

        await _ensure_owner_entity_and_contact(pool)

        # Check fetchval calls — the entity INSERT should have ['owner'] as param
        for call in conn.fetchval.call_args_list:
            sql = call[0][0] if call[0] else ""
            if "INSERT INTO shared.entities" in sql:
                assert any("owner" in str(arg) for arg in call[0])
                break
        else:
            pytest.fail("Entity INSERT not found in fetchval calls")

    async def test_existing_entity_is_fetched(self) -> None:
        """When entity INSERT returns None (already exists), fetches existing id."""
        pool, conn = _make_pool(
            entity_insert_returns=None,
            entity_select_returns=_OWNER_ENTITY_ID,
        )

        await _ensure_owner_entity_and_contact(pool)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        assert _OWNER_ENTITY_ID in call_args


class TestEnsureOwnerEntityAndContactFallback:
    async def test_falls_back_without_entities_table(self) -> None:
        """When shared.entities doesn't exist, creates contact without entity_id."""
        pool, conn = _make_pool(entities_table_exists=False)

        await _ensure_owner_entity_and_contact(pool)

        conn.execute.assert_awaited_once()
        call_sql = conn.execute.call_args[0][0]
        assert "INSERT INTO shared.contacts" in call_sql
        assert "entity_id" not in call_sql
        # Roles still passed to contact
        call_args = list(conn.execute.call_args[0])
        assert any("owner" in str(arg) for arg in call_args)

    async def test_falls_back_without_roles_on_entities(self) -> None:
        """When entities.roles column doesn't exist, creates contact without entity_id."""
        pool, conn = _make_pool(roles_on_entities=False)

        await _ensure_owner_entity_and_contact(pool)

        conn.execute.assert_awaited_once()
        call_sql = conn.execute.call_args[0][0]
        assert "INSERT INTO shared.contacts" in call_sql


class TestEnsureOwnerEntityAndContactTableAbsent:
    async def test_skips_when_shared_contacts_missing(self) -> None:
        """Function does nothing when shared.contacts table does not exist."""
        pool, conn = _make_pool(contacts_table_exists=False)

        await _ensure_owner_entity_and_contact(pool)

        conn.execute.assert_not_awaited()

    async def test_no_error_when_table_missing(self) -> None:
        """Function completes without raising when table is absent."""
        pool, conn = _make_pool(contacts_table_exists=False)

        await _ensure_owner_entity_and_contact(pool)


class TestEnsureOwnerEntityAndContactRolesColumnAbsent:
    async def test_skips_when_roles_column_missing(self) -> None:
        """Function does nothing when shared.contacts.roles column does not exist."""
        pool, conn = _make_pool(roles_col_exists=False)

        await _ensure_owner_entity_and_contact(pool)

        conn.execute.assert_not_awaited()


class TestEnsureOwnerEntityAndContactErrorHandling:
    async def test_exception_is_caught_and_logged(self) -> None:
        """Pool exception is caught; function is non-fatal."""
        pool = MagicMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        with patch("butlers.daemon.logger") as mock_logger:
            await _ensure_owner_entity_and_contact(pool)

            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "bootstrap" in warning_msg.lower() or "skipped" in warning_msg.lower()

    async def test_execute_exception_is_non_fatal(self) -> None:
        """DB execute exception during INSERT is caught; function is non-fatal."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock(side_effect=Exception("unique constraint violation"))

        await _ensure_owner_entity_and_contact(pool)


class TestConcurrentStartupSafety:
    async def test_concurrent_calls_do_not_raise(self) -> None:
        """Multiple concurrent calls to _ensure_owner_entity_and_contact complete without error."""
        insert_calls: list[tuple] = []

        async def recording_execute(sql: str, *args) -> None:
            insert_calls.append((sql, args))

        pools = []
        for _ in range(5):
            pool, conn = _make_pool()
            conn.execute = AsyncMock(side_effect=recording_execute)
            pools.append(pool)

        await asyncio.gather(*[_ensure_owner_entity_and_contact(p) for p in pools])

        assert len(insert_calls) == 5
        for sql, _args in insert_calls:
            assert "ON CONFLICT DO NOTHING" in sql

    async def test_concurrent_calls_all_contain_owner_role(self) -> None:
        """Every concurrent INSERT attempt includes the 'owner' role value (as parameter)."""
        insert_calls: list[tuple] = []

        async def capturing_execute(sql: str, *args) -> None:
            insert_calls.append((sql, args))

        pools = []
        for _ in range(3):
            pool, conn = _make_pool()
            conn.execute = AsyncMock(side_effect=capturing_execute)
            pools.append(pool)

        await asyncio.gather(*[_ensure_owner_entity_and_contact(p) for p in pools])

        for sql, args in insert_calls:
            assert any("owner" in str(arg).lower() for arg in args)
