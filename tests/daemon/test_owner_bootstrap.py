"""Tests for _ensure_owner_entity() owner bootstrap logic.

Verifies:
- First startup creates owner entity in public.entities.
- Subsequent startups are no-ops (idempotent via ON CONFLICT).
- If public.entities does not exist, skips entity creation.
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
    """Build a mock asyncpg pool that simulates public.entities state."""
    conn = AsyncMock()

    # Build the sequence of fetchval returns:
    # 1. to_regclass('public.entities') IS NOT NULL
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


class TestEnsureOwnerEntityBehavior:
    async def test_creation_fallbacks_guards_and_errors(self) -> None:
        """INSERT on fresh start; SELECT fallback when INSERT returns None; idempotent on existing role;
        skips when entities table or roles column missing; exceptions are non-fatal."""
        # Fresh entity: INSERT called; no contacts INSERT
        pool, conn = _make_pool()
        await _ensure_owner_entity(pool)
        insert_sqls = [c[0][0] if c[0] else "" for c in conn.fetchval.call_args_list]
        assert any("INSERT INTO public.entities" in s for s in insert_sqls)
        for s in insert_sqls:
            assert "INSERT INTO public.contacts" not in s
        conn.execute.assert_not_awaited()

        # INSERT=None → SELECT fallback
        pool2, conn2 = _make_pool(entity_insert_returns=None, entity_select_returns=_OWNER_ENTITY_ID)
        await _ensure_owner_entity(pool2)
        sqls2 = [c[0][0] if c[0] else "" for c in conn2.fetchval.call_args_list]
        assert any("SELECT id FROM public.entities" in s for s in sqls2)

        # Existing owner-role entity → no INSERT
        pool3, conn3 = _make_pool(owner_select_before_insert=_OWNER_ENTITY_ID)
        await _ensure_owner_entity(pool3)
        for call in conn3.fetchval.call_args_list:
            assert "INSERT INTO public.entities" not in (call[0][0] if call[0] else "")

        # Skips when entities table or roles column missing
        for kwargs in [{"entities_table_exists": False}, {"roles_on_entities": False}]:
            pool_g, conn_g = _make_pool(**kwargs)
            await _ensure_owner_entity(pool_g)
            for call in conn_g.fetchval.call_args_list:
                assert "INSERT INTO public.entities" not in (call[0][0] if call[0] else "")

        # Pool acquire exception → WARNING logged; non-fatal
        pool_err = MagicMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool_err.acquire = MagicMock(return_value=acquire_ctx)
        with patch("butlers.daemon.logger") as mock_logger:
            await _ensure_owner_entity(pool_err)
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "bootstrap" in warning_msg.lower() or "skipped" in warning_msg.lower()

        # Fetchval exception during INSERT → non-fatal
        pool4, conn4 = _make_pool()
        original_se = conn4.fetchval.side_effect
        call_count = 0
        results = list(original_se.args[0]) if hasattr(original_se, "args") else []

        async def failing_on_insert(sql, *args):
            nonlocal call_count
            if "INSERT INTO public.entities" in sql:
                raise Exception("constraint violation")
            idx = call_count; call_count += 1
            return results[idx] if idx < len(results) else None

        conn4.fetchval = AsyncMock(side_effect=failing_on_insert)
        await _ensure_owner_entity(pool4)  # Should not raise


class TestConcurrentStartupSafety:
    async def test_concurrent_calls_safe(self) -> None:
        """Multiple concurrent calls complete without error; each attempts INSERT with 'owner' role."""
        # 5 concurrent calls complete
        pools5 = [_make_pool()[0] for _ in range(5)]
        await asyncio.gather(*[_ensure_owner_entity(p) for p in pools5])

        # 3 concurrent calls each insert owner role
        insert_calls: list[tuple] = []
        pools3 = []
        for _ in range(3):
            pool, conn = _make_pool()
            original_fetchval = conn.fetchval

            async def capturing_fetchval(sql, *args, _orig=original_fetchval):
                if "INSERT INTO public.entities" in sql:
                    insert_calls.append((sql, args))
                return await _orig(sql, *args)

            conn.fetchval = AsyncMock(side_effect=capturing_fetchval)
            pools3.append(pool)

        await asyncio.gather(*[_ensure_owner_entity(p) for p in pools3])
        assert len(insert_calls) == 3
        for sql, args in insert_calls:
            assert any("owner" in str(arg).lower() for arg in args)
