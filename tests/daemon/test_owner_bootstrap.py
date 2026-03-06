"""Tests for _ensure_owner_entity() owner bootstrap logic.

Verifies:
- First startup creates owner entity in shared.entities.
- Subsequent startups are no-ops (idempotent via ON CONFLICT).
- If shared.entities does not exist, skips entity creation.
- Exceptions from the pool are caught and logged (non-fatal).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import _ensure_owner_entity

pytestmark = pytest.mark.unit

_OWNER_ENTITY_ID = uuid.uuid4()


def _make_pool(
    *,
    entities_table_exists: bool = True,
    roles_on_entities: bool = True,
    owner_select_before_insert: uuid.UUID | None = None,
    entity_insert_returns: uuid.UUID | None = _OWNER_ENTITY_ID,
    owner_select_after_insert: uuid.UUID | None = None,
    entity_select_returns: uuid.UUID | None = None,
) -> tuple[MagicMock, AsyncMock]:
    """Build a mock asyncpg pool that simulates shared.entities state."""
    conn = AsyncMock()

    # Build the sequence of fetchval returns:
    # 1. to_regclass('shared.entities') IS NOT NULL
    # 2. (if entities exist) information_schema check for roles on entities
    # 3. (if roles on entities) SELECT owner entity by role
    # 4. (if no owner by role) INSERT RETURNING id (entity)
    # 5. (if insert returned None) SELECT owner entity by role (again)
    # 6. (if still none) SELECT id by canonical owner identity
    fetchval_results: list = []

    fetchval_results.append(entities_table_exists)

    if entities_table_exists:
        fetchval_results.append(roles_on_entities)
        if roles_on_entities:
            fetchval_results.append(owner_select_before_insert)
            if owner_select_before_insert is None:
                fetchval_results.append(entity_insert_returns)
                if entity_insert_returns is None:
                    fetchval_results.append(owner_select_after_insert)
                    if owner_select_after_insert is None:
                        fetchval_results.append(entity_select_returns)

    conn.fetchval = AsyncMock(side_effect=fetchval_results)

    pool = MagicMock()
    pool.acquire = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = acquire_ctx

    return pool, conn


class TestEnsureOwnerEntityCreation:
    async def test_creates_entity(self) -> None:
        """First startup creates entity in shared.entities."""
        pool, conn = _make_pool()

        await _ensure_owner_entity(pool)

        # Entity INSERT should have been called with ['owner'] role
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

        await _ensure_owner_entity(pool)

        # SELECT fallback should have been called
        select_found = False
        for call in conn.fetchval.call_args_list:
            sql = call[0][0] if call[0] else ""
            if "SELECT id FROM shared.entities" in sql:
                select_found = True
                break
        assert select_found, "Entity SELECT fallback not found"

    async def test_existing_owner_role_skips_insert(self) -> None:
        """When an owner-role entity exists, INSERT is not attempted."""
        pool, conn = _make_pool(owner_select_before_insert=_OWNER_ENTITY_ID)

        await _ensure_owner_entity(pool)

        for call in conn.fetchval.call_args_list:
            sql = call[0][0] if call[0] else ""
            assert "INSERT INTO shared.entities" not in sql

    async def test_no_contact_insert(self) -> None:
        """No INSERT INTO shared.contacts is issued."""
        pool, conn = _make_pool()

        await _ensure_owner_entity(pool)

        for call in conn.fetchval.call_args_list:
            sql = call[0][0] if call[0] else ""
            assert "INSERT INTO shared.contacts" not in sql
        # conn.execute should not be called at all (no contact insert)
        conn.execute.assert_not_awaited()


class TestEnsureOwnerEntityFallback:
    async def test_skips_entity_without_entities_table(self) -> None:
        """When shared.entities doesn't exist, entity creation is skipped."""
        pool, conn = _make_pool(entities_table_exists=False)

        await _ensure_owner_entity(pool)

        for call in conn.fetchval.call_args_list:
            sql = call[0][0] if call[0] else ""
            assert "INSERT INTO shared.entities" not in sql

    async def test_skips_entity_without_roles_column(self) -> None:
        """When entities.roles column doesn't exist, entity creation is skipped."""
        pool, conn = _make_pool(roles_on_entities=False)

        await _ensure_owner_entity(pool)

        for call in conn.fetchval.call_args_list:
            sql = call[0][0] if call[0] else ""
            assert "INSERT INTO shared.entities" not in sql


class TestEnsureOwnerEntityErrorHandling:
    async def test_exception_is_caught_and_logged(self) -> None:
        """Pool exception is caught; function is non-fatal."""
        pool = MagicMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=acquire_ctx)

        with patch("butlers.daemon.logger") as mock_logger:
            await _ensure_owner_entity(pool)

            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "bootstrap" in warning_msg.lower() or "skipped" in warning_msg.lower()

    async def test_fetchval_exception_is_non_fatal(self) -> None:
        """DB fetchval exception during entity bootstrap is non-fatal."""
        pool, conn = _make_pool()
        original_side_effect = conn.fetchval.side_effect

        # Raise when entity INSERT query is attempted.
        call_count = 0
        has_args = hasattr(original_side_effect, "args")
        results = list(original_side_effect.args[0]) if has_args else []

        async def failing_on_insert(sql, *args):
            nonlocal call_count
            if "INSERT INTO shared.entities" in sql:
                raise Exception("constraint violation")
            idx = call_count
            call_count += 1
            return results[idx] if idx < len(results) else None

        conn.fetchval = AsyncMock(side_effect=failing_on_insert)

        await _ensure_owner_entity(pool)


class TestConcurrentStartupSafety:
    async def test_concurrent_calls_do_not_raise(self) -> None:
        """Multiple concurrent calls to _ensure_owner_entity complete without error."""
        pools = []
        for _ in range(5):
            pool, _conn = _make_pool()
            pools.append(pool)

        await asyncio.gather(*[_ensure_owner_entity(p) for p in pools])

    async def test_concurrent_calls_all_create_entity_with_owner_role(self) -> None:
        """Every concurrent call attempts entity INSERT with 'owner' role."""
        insert_calls: list[tuple] = []

        pools = []
        for _ in range(3):
            pool, conn = _make_pool()

            original_fetchval = conn.fetchval

            async def capturing_fetchval(sql, *args, _orig=original_fetchval):
                if "INSERT INTO shared.entities" in sql:
                    insert_calls.append((sql, args))
                return await _orig(sql, *args)

            conn.fetchval = AsyncMock(side_effect=capturing_fetchval)
            pools.append(pool)

        await asyncio.gather(*[_ensure_owner_entity(p) for p in pools])

        assert len(insert_calls) == 3
        for sql, args in insert_calls:
            assert any("owner" in str(arg).lower() for arg in args)
