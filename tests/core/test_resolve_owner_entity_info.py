"""Tests for resolve_owner_entity_info() in credential_store."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import resolve_owner_entity_info
from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import row

pytestmark = pytest.mark.unit


def _pool_and_conn(*, fetchrow_return=None, raises: Exception | None = None):
    fetchrow = AsyncMock(side_effect=raises) if raises is not None else fetchrow_return
    pool = fake_pool(fetchrow=fetchrow)
    return pool, pool.connection


async def test_resolve_owner_entity_info():
    """Returns value for known type, strips whitespace, returns None for missing/empty/whitespace;
    SQL checks owner/is_primary; missing table→None; unexpected error re-raised."""
    # Returns value and strips whitespace
    pool, conn = _pool_and_conn(fetchrow_return=row(value="  987654321  "))
    assert await resolve_owner_entity_info(pool, "telegram") == "987654321"
    assert conn.fetchrow.await_args.args[1] == "telegram"

    # None cases: no row, empty string, whitespace only
    for val in [None, "", "   "]:
        if val is None:
            pool2, _ = _pool_and_conn(fetchrow_return=None)
        else:
            pool2, _ = _pool_and_conn(fetchrow_return=row(value=val))
        assert await resolve_owner_entity_info(pool2, "telegram") is None

    # Missing table → None
    pool3, _ = _pool_and_conn(raises=Exception("relation public.entity_info does not exist"))
    assert await resolve_owner_entity_info(pool3, "telegram") is None

    # Unexpected error re-raised
    pool4, _ = _pool_and_conn(raises=RuntimeError("DB connection timeout"))
    with pytest.raises(RuntimeError, match="DB connection timeout"):
        await resolve_owner_entity_info(pool4, "telegram")

    # Not-null constraint error re-raised (not swallowed as missing table)
    pool5, _ = _pool_and_conn(
        raises=Exception('null value in column "entity_id" violates not-null constraint')
    )
    with pytest.raises(Exception, match="not-null constraint"):
        await resolve_owner_entity_info(pool5, "telegram")


async def test_resolve_owner_telegram_recipient_prefers_chat_id(monkeypatch):
    """resolve_owner_telegram_recipient returns the numeric chat id when present,
    even if a @username handle is also stored.
    """
    import butlers.credential_store as cs

    async def fake_resolve(_pool, info_type):
        return {"telegram_chat_id": "206570151", "telegram": "@Tzeusy"}.get(info_type)

    monkeypatch.setattr(cs, "resolve_owner_entity_info", AsyncMock(side_effect=fake_resolve))
    pool = MagicMock()
    assert await cs.resolve_owner_telegram_recipient(pool) == "206570151"


async def test_resolve_owner_telegram_recipient_falls_back_to_username(monkeypatch):
    """Falls back to the @username handle only when no numeric chat id is stored."""
    import butlers.credential_store as cs

    async def fake_resolve(_pool, info_type):
        return {"telegram": "@Tzeusy"}.get(info_type)

    monkeypatch.setattr(cs, "resolve_owner_entity_info", AsyncMock(side_effect=fake_resolve))
    pool = MagicMock()
    assert await cs.resolve_owner_telegram_recipient(pool) == "@Tzeusy"


async def test_resolve_owner_telegram_recipient_none_when_unconfigured(monkeypatch):
    """Returns None when neither the chat id nor the username handle is stored."""
    import butlers.credential_store as cs

    monkeypatch.setattr(cs, "resolve_owner_entity_info", AsyncMock(return_value=None))
    pool = MagicMock()
    assert await cs.resolve_owner_telegram_recipient(pool) is None
