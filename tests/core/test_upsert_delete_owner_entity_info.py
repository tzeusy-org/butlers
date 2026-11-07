"""Tests for upsert_owner_entity_info() and delete_owner_entity_info()."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.credential_store import delete_owner_entity_info, upsert_owner_entity_info
from butlers.testing.fake_pool import FakeConnection, FakePool, fake_pool
from butlers.testing.records import row

pytestmark = pytest.mark.unit


def _pool_and_conn(
    *,
    owner_id: str | None = "owner-uuid-1",
    execute_return: str = "DELETE 1",
    raises: Exception | None = None,
) -> tuple[FakePool, FakeConnection]:
    if raises is not None:
        pool = fake_pool(
            fetchrow=AsyncMock(side_effect=raises), execute=AsyncMock(side_effect=raises)
        )
    else:
        owner_row = row(id=owner_id) if owner_id is not None else None
        pool = fake_pool(fetchrow=owner_row, execute=execute_return)
    return pool, pool.connection


async def test_upsert_owner_entity_info():
    """Creates/replaces row with ON CONFLICT; respects secured flag; False when no owner
    or missing table; re-raises unexpected errors."""
    # Creates row and verifies INSERT ON CONFLICT with secured=True
    pool, conn = _pool_and_conn(owner_id="owner-uuid-1")
    assert await upsert_owner_entity_info(pool, "google_oauth_refresh", "token-value") is True
    insert_call = conn.execute.call_args_list[0]
    assert insert_call[0][1] == "owner-uuid-1" and insert_call[0][3] == "token-value"
    assert insert_call[0][4] is True  # secured=True

    # secured=False passed through for a whitelisted non-secret type (telegram_api_id)
    pool2, conn2 = _pool_and_conn(owner_id="owner-uuid-1")
    await upsert_owner_entity_info(pool2, "telegram_api_id", "12345", secured=False)
    assert conn2.execute.call_args_list[0][0][4] is False

    # No owner → False, no execute
    pool3, conn3 = _pool_and_conn(owner_id=None)
    assert await upsert_owner_entity_info(pool3, "google_oauth_refresh", "token") is False
    conn3.execute.assert_not_awaited()

    # Missing table → False
    UndefinedTableError = type("UndefinedTableError", (Exception,), {})
    pool4, _ = _pool_and_conn(
        raises=UndefinedTableError('relation "public.entities" does not exist')
    )
    assert await upsert_owner_entity_info(pool4, "google_oauth_refresh", "token") is False

    # Unexpected error re-raised
    pool5, _ = _pool_and_conn(raises=RuntimeError("connection lost"))
    with pytest.raises(RuntimeError, match="connection lost"):
        await upsert_owner_entity_info(pool5, "google_oauth_refresh", "token")


async def test_delete_owner_entity_info():
    """Deletes row returns True; no row returns False; no owner→False; missing table→False;
    unexpected error re-raised."""
    # Delete matching row
    pool, conn = _pool_and_conn(owner_id="owner-uuid-1", execute_return="DELETE 1")
    assert await delete_owner_entity_info(pool, "google_oauth_refresh") is True
    delete_call = conn.execute.call_args_list[0]
    assert delete_call[0][1] == "owner-uuid-1"

    # No row to delete
    pool2, _ = _pool_and_conn(owner_id="owner-uuid-1", execute_return="DELETE 0")
    assert await delete_owner_entity_info(pool2, "google_oauth_refresh") is False

    # No owner → False
    pool3, conn3 = _pool_and_conn(owner_id=None)
    assert await delete_owner_entity_info(pool3, "google_oauth_refresh") is False
    conn3.execute.assert_not_awaited()

    # Missing table → False
    UndefinedTableError = type("UndefinedTableError", (Exception,), {})
    pool4, _ = _pool_and_conn(
        raises=UndefinedTableError('relation "public.entities" does not exist')
    )
    assert await delete_owner_entity_info(pool4, "google_oauth_refresh") is False

    # Unexpected error re-raised
    pool5, _ = _pool_and_conn(raises=RuntimeError("disk full"))
    with pytest.raises(RuntimeError, match="disk full"):
        await delete_owner_entity_info(pool5, "google_oauth_refresh")
