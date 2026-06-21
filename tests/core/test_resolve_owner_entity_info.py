"""Tests for resolve_owner_entity_info() in credential_store."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import resolve_owner_entity_info

pytestmark = pytest.mark.unit


def _make_pool(*, fetchrow_return=None, raises: Exception | None = None) -> MagicMock:
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
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: value if k == "value" else None)
    return row


async def test_resolve_owner_entity_info():
    """Returns value for known type, strips whitespace, returns None for missing/empty/whitespace;
    SQL checks owner/is_primary; missing table→None; unexpected error re-raised."""
    # Returns value and strips whitespace
    row = _make_row("  987654321  ")
    pool, conn = _make_pool(fetchrow_return=row)
    assert await resolve_owner_entity_info(pool, "telegram") == "987654321"
    assert conn.fetchrow.await_args.args[1] == "telegram"

    # None cases: no row, empty string, whitespace only
    for val in [None, "", "   "]:
        if val is None:
            pool2, _ = _make_pool(fetchrow_return=None)
        else:
            pool2, _ = _make_pool(fetchrow_return=_make_row(val))
        assert await resolve_owner_entity_info(pool2, "telegram") is None

    # Missing table → None
    pool3, _ = _make_pool(raises=Exception("relation public.entity_info does not exist"))
    assert await resolve_owner_entity_info(pool3, "telegram") is None

    # Unexpected error re-raised
    pool4, _ = _make_pool(raises=RuntimeError("DB connection timeout"))
    with pytest.raises(RuntimeError, match="DB connection timeout"):
        await resolve_owner_entity_info(pool4, "telegram")

    # Not-null constraint error re-raised (not swallowed as missing table)
    pool5, _ = _make_pool(
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
