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

import asyncpg
import pytest

from butlers.owner_bootstrap import _ensure_owner_entity

pytestmark = pytest.mark.unit

_OWNER_ENTITY_ID = uuid.uuid4()


def _make_pool(
    *,
    entities_table_exists: bool = True,
    roles_on_entities: bool = True,
    owner_select_before_insert: uuid.UUID | None = None,
    entity_insert_returns: uuid.UUID | None = _OWNER_ENTITY_ID,
    owner_select_after_insert: uuid.UUID | None = None,
) -> tuple[MagicMock, AsyncMock]:
    """Build a mock asyncpg pool that simulates public.entities state."""
    conn = AsyncMock()

    # Build the sequence of fetchval returns:
    # 1. to_regclass('public.entities') IS NOT NULL
    # 2. (if entities exist) information_schema check for roles on entities
    # 3. (if roles on entities) SELECT owner entity by role
    # 4. (if no owner by role) INSERT RETURNING id (entity)
    # 5. (if insert returned None) SELECT owner entity by role (again)
    # 6. (if still none) warning is logged; no further DB query
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

    # Return queued results in order, then None for any further calls (the Phase 2
    # owner-telegram-handle seed's existence check) so the seed is a clean no-op in
    # these entity-creation focused tests.
    _fetchval_iter = iter(fetchval_results)

    async def _fetchval(*_args: object, **_kwargs: object) -> object:
        try:
            return next(_fetchval_iter)
        except StopIteration:
            return None

    conn.fetchval = AsyncMock(side_effect=_fetchval)

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
        # INSERT=None → SELECT fallback (select by role), still None → warning logged
        pool2, conn2 = _make_pool(entity_insert_returns=None, owner_select_after_insert=None)
        with patch("butlers.owner_bootstrap.logger") as mock_logger2:
            await _ensure_owner_entity(pool2)
        sqls2 = [c[0][0] if c[0] else "" for c in conn2.fetchval.call_args_list]
        assert any("SELECT id FROM public.entities" in s for s in sqls2)
        mock_logger2.warning.assert_called()

        # INSERT=None → SELECT fallback finds the entity → no warning
        pool2b, conn2b = _make_pool(
            entity_insert_returns=None, owner_select_after_insert=_OWNER_ENTITY_ID
        )
        with patch("butlers.owner_bootstrap.logger") as mock_logger2b:
            await _ensure_owner_entity(pool2b)
        sqls2b = [c[0][0] if c[0] else "" for c in conn2b.fetchval.call_args_list]
        assert any("SELECT id FROM public.entities" in s for s in sqls2b)
        mock_logger2b.warning.assert_not_called()

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
        with patch("butlers.owner_bootstrap.logger") as mock_logger:
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
            idx = call_count
            call_count += 1
            return results[idx] if idx < len(results) else None

        conn4.fetchval = AsyncMock(side_effect=failing_on_insert)
        await _ensure_owner_entity(pool4)  # Should not raise


class TestSeedOwnerTelegramHandle:
    """Phase 2: mirror the owner's entity_info telegram_chat_id into a resolvable triple."""

    async def test_seeds_prefixed_handle_from_chat_id(self) -> None:
        from butlers.owner_bootstrap import _seed_owner_telegram_handle

        conn = AsyncMock()
        # 1. current schema → relationship; 2. relationship schema reachable → True;
        # 3. tables_ready → True; 4. chat_id lookup → "206570151"
        conn.fetchval = AsyncMock(side_effect=["relationship", True, True, "206570151"])
        conn.execute = AsyncMock()

        await _seed_owner_telegram_handle(conn, _OWNER_ENTITY_ID)

        conn.execute.assert_awaited_once()
        insert_sql, *args = conn.execute.call_args.args
        assert "relationship.entity_facts" in insert_sql
        assert "has-handle" in insert_sql
        assert "ON CONFLICT" in insert_sql  # idempotent
        assert args[0] == _OWNER_ENTITY_ID
        assert args[1] == "telegram:206570151"  # canonical prefixed form

    async def test_no_chat_id_is_noop(self) -> None:
        from butlers.owner_bootstrap import _seed_owner_telegram_handle

        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=["relationship", True, True, None])
        conn.execute = AsyncMock()
        await _seed_owner_telegram_handle(conn, _OWNER_ENTITY_ID)
        conn.execute.assert_not_awaited()

    async def test_missing_tables_is_noop(self) -> None:
        from butlers.owner_bootstrap import _seed_owner_telegram_handle

        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=["relationship", True, False])
        conn.execute = AsyncMock()
        await _seed_owner_telegram_handle(conn, _OWNER_ENTITY_ID)
        conn.execute.assert_not_awaited()

    async def test_inaccessible_relationship_schema_is_quiet_noop(self) -> None:
        """Butler roles without USAGE on ``relationship`` skip without warning.

        Under SET ROLE schema isolation every butler except ``relationship``
        lacks USAGE on that schema, so ``to_regclass('relationship.…')`` raises
        InsufficientPrivilegeError. That is the designed posture, not a failure:
        probe the privilege first and return quietly instead of logging a
        traceback on every daemon startup.
        """
        from butlers.owner_bootstrap import _seed_owner_telegram_handle

        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=["relationship", False])
        conn.execute = AsyncMock()

        with patch("butlers.owner_bootstrap.logger") as mock_logger:
            await _seed_owner_telegram_handle(conn, _OWNER_ENTITY_ID)

        conn.execute.assert_not_awaited()
        mock_logger.warning.assert_not_called()
        # The current-schema and privilege probes ran — no to_regclass against
        # relationship.
        assert conn.fetchval.await_count == 2
        probe_sql = conn.fetchval.await_args_list[1].args[0]
        assert "has_schema_privilege" in probe_sql
        assert "to_regclass" not in probe_sql

    async def test_non_relationship_schema_is_noop_without_permission_warning(self) -> None:
        from butlers.owner_bootstrap import _seed_owner_telegram_handle

        conn = AsyncMock()

        async def _fetchval(sql: str, *_args: object) -> object:
            if "current_schema()" in sql:
                return "travel"
            if "relationship.entity_facts" in sql:
                raise asyncpg.InsufficientPrivilegeError(
                    "permission denied for schema relationship"
                )
            raise AssertionError(f"unexpected query: {sql}")

        conn.fetchval = AsyncMock(side_effect=_fetchval)
        conn.execute = AsyncMock()

        with patch("butlers.owner_bootstrap.logger") as mock_logger:
            await _seed_owner_telegram_handle(conn, _OWNER_ENTITY_ID)

        conn.execute.assert_not_awaited()
        mock_logger.warning.assert_not_called()


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
