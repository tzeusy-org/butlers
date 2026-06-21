"""Tests for upsert_owner_entity_info() and delete_owner_entity_info()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import delete_owner_entity_info, upsert_owner_entity_info

pytestmark = pytest.mark.unit


def _make_pool(
    *,
    owner_id: str | None = "owner-uuid-1",
    execute_return: str = "DELETE 1",
    raises: Exception | None = None,
) -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()
    if raises is not None:
        conn.fetchrow = AsyncMock(side_effect=raises)
        conn.execute = AsyncMock(side_effect=raises)
    else:
        if owner_id is not None:
            owner_row = MagicMock()
            owner_row.__getitem__ = MagicMock(side_effect=lambda k: owner_id if k == "id" else None)
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


async def test_upsert_owner_entity_info():
    """Creates/replaces row with ON CONFLICT; respects secured flag; False when no owner
    or missing table; re-raises unexpected errors."""
    # Creates row and verifies INSERT ON CONFLICT with secured=True
    pool, conn = _make_pool(owner_id="owner-uuid-1")
    assert await upsert_owner_entity_info(pool, "google_oauth_refresh", "token-value") is True
    insert_call = conn.execute.call_args_list[0]
    assert insert_call[0][1] == "owner-uuid-1" and insert_call[0][3] == "token-value"
    assert insert_call[0][4] is True  # secured=True

    # secured=False passed through for a whitelisted non-secret type (telegram_api_id)
    pool2, conn2 = _make_pool(owner_id="owner-uuid-1")
    await upsert_owner_entity_info(pool2, "telegram_api_id", "12345", secured=False)
    assert conn2.execute.call_args_list[0][0][4] is False

    # No owner → False, no execute
    pool3, conn3 = _make_pool(owner_id=None)
    assert await upsert_owner_entity_info(pool3, "google_oauth_refresh", "token") is False
    conn3.execute.assert_not_awaited()

    # Missing table → False
    UndefinedTableError = type("UndefinedTableError", (Exception,), {})
    pool4, _ = _make_pool(raises=UndefinedTableError('relation "public.entities" does not exist'))
    assert await upsert_owner_entity_info(pool4, "google_oauth_refresh", "token") is False

    # Unexpected error re-raised
    pool5, _ = _make_pool(raises=RuntimeError("connection lost"))
    with pytest.raises(RuntimeError, match="connection lost"):
        await upsert_owner_entity_info(pool5, "google_oauth_refresh", "token")


async def test_delete_owner_entity_info():
    """Deletes row returns True; no row returns False; no owner→False; missing table→False;
    unexpected error re-raised."""
    # Delete matching row
    pool, conn = _make_pool(owner_id="owner-uuid-1", execute_return="DELETE 1")
    assert await delete_owner_entity_info(pool, "google_oauth_refresh") is True
    delete_call = conn.execute.call_args_list[0]
    assert delete_call[0][1] == "owner-uuid-1"

    # No row to delete
    pool2, _ = _make_pool(owner_id="owner-uuid-1", execute_return="DELETE 0")
    assert await delete_owner_entity_info(pool2, "google_oauth_refresh") is False

    # No owner → False
    pool3, conn3 = _make_pool(owner_id=None)
    assert await delete_owner_entity_info(pool3, "google_oauth_refresh") is False
    conn3.execute.assert_not_awaited()

    # Missing table → False
    UndefinedTableError = type("UndefinedTableError", (Exception,), {})
    pool4, _ = _make_pool(raises=UndefinedTableError('relation "public.entities" does not exist'))
    assert await delete_owner_entity_info(pool4, "google_oauth_refresh") is False

    # Unexpected error re-raised
    pool5, _ = _make_pool(raises=RuntimeError("disk full"))
    with pytest.raises(RuntimeError, match="disk full"):
        await delete_owner_entity_info(pool5, "google_oauth_refresh")
