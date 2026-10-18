"""list_gaps builds its entity and keyset filters with the right placeholders (bu-q7vx1q.71).

Drives the real ``list_gaps`` against a recording fake pool, so the argument numbering is
checked without a database; the real-PG case lives in
``test_knowledge_gaps_entity_filter.py``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory import knowledge_gaps

pytestmark = pytest.mark.unit


class _FakePool:
    def __init__(self) -> None:
        self.conn = MagicMock()
        self.conn.execute = AsyncMock()
        self.conn.fetchval = AsyncMock(return_value="relationship")
        self.conn.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def transaction():
            yield

        self.conn.transaction = transaction

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    @property
    def query(self) -> tuple[str, tuple]:
        sql, *args = self.conn.fetch.await_args.args
        return sql, tuple(args)


_ENTITY = uuid.uuid4()
_AFTER = (datetime(2026, 6, 1, tzinfo=UTC), uuid.uuid4())


async def test_an_unfiltered_listing_has_no_extra_clause():
    pool = _FakePool()

    await knowledge_gaps.list_gaps(pool, limit=10)

    sql, args = pool.query
    assert "AND g.entity_id" not in sql and "(g.asked_at, g.id) <" not in sql
    assert args == (["open", "answerable"], 10)


async def test_the_entity_filter_takes_the_next_placeholder():
    pool = _FakePool()

    await knowledge_gaps.list_gaps(pool, limit=10, entity_id=_ENTITY)

    sql, args = pool.query
    assert "AND g.entity_id = $3" in sql
    assert args == (["open", "answerable"], 10, _ENTITY)


async def test_the_keyset_cursor_alone_keeps_dollar_three_and_four():
    pool = _FakePool()

    await knowledge_gaps.list_gaps(pool, limit=10, after=_AFTER)

    sql, args = pool.query
    assert "(g.asked_at, g.id) < ($3, $4)" in sql
    assert args == (["open", "answerable"], 10, *_AFTER)


async def test_cursor_and_entity_filter_together_number_five_after_the_cursor():
    pool = _FakePool()

    await knowledge_gaps.list_gaps(pool, limit=10, after=_AFTER, entity_id=_ENTITY)

    sql, args = pool.query
    assert "(g.asked_at, g.id) < ($3, $4)" in sql
    assert "AND g.entity_id = $5" in sql
    assert args == (["open", "answerable"], 10, *_AFTER, _ENTITY)
