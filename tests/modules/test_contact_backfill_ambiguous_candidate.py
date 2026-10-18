"""Ambiguous synced contacts become duplicate candidates instead of being dropped.

[bu-60pwv6.40] The resolver returns ``ambiguous_name`` / ``ambiguous_channel`` when it cannot
pick one local match. The engine creates a NEW entity flagged ``duplicate_candidate`` and its
``contacts_source_links`` row in one transaction, logs a content-free ``contact_sync_ambiguous``
warning, and the next sync resolves by source link.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.modules.contacts.backfill import ContactBackfillEngine
from butlers.modules.contacts.sync import CanonicalContact

pytestmark = pytest.mark.unit

_EXTERNAL_ID = "people/c-ambiguous-1"
_NAME = "Alex Smith"


class _Conn:
    """Transactional connection recording statements; rolls back what the txn wrote."""

    def __init__(self, *, collide_first_insert: bool = False) -> None:
        self.collide_first_insert = collide_first_insert
        self.entities: list[dict] = []
        self.links: list[tuple] = []
        self.inserts = 0
        self.depth = 0
        self._snapshot: tuple[int, int] | None = None

    @asynccontextmanager
    async def transaction(self):
        outer = self.depth == 0
        self.depth += 1
        mark = (len(self.entities), len(self.links))
        try:
            yield
        except BaseException:
            del self.entities[mark[0] :]
            del self.links[mark[1] :]
            raise
        finally:
            self.depth -= 1
            del outer

    async def fetchrow(self, sql, canonical, entity_type, aliases):
        assert "INSERT INTO public.entities" in sql
        self.inserts += 1
        if self.collide_first_insert and self.inserts == 1:
            raise asyncpg.UniqueViolationError("duplicate key")
        row = {"id": uuid.uuid4(), "canonical_name": canonical, "aliases": aliases, "metadata": {}}
        self.entities.append(row)
        return row

    async def execute(self, sql, *args):
        if sql.lstrip().startswith("UPDATE public.entities SET metadata"):
            metadata, entity_id = args
            next(e for e in self.entities if e["id"] == entity_id)["metadata"] = metadata
        elif "INSERT INTO contacts_source_links" in sql:
            self.links.append(args)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        # Reappearance lookup only; the pool has no execute/insert path, so any
        # autonomous (non-transactional) write would raise AttributeError.
        self.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _contact() -> CanonicalContact:
    return CanonicalContact(
        external_id=_EXTERNAL_ID,
        etag="e1",
        display_name=_NAME,
        first_name="Alex",
        last_name="Smith",
    )


def _engine(conn: _Conn, strategy: str = "ambiguous_name"):
    engine = ContactBackfillEngine(_Pool(conn), provider="telegram", account_id="acct-1")
    engine._resolver.resolve = AsyncMock(return_value=(None, strategy))
    for name in (
        "upsert_contact_info",
        "upsert_addresses",
        "upsert_important_dates",
        "upsert_labels",
    ):
        setattr(engine._writer, name, AsyncMock())
    return engine


@pytest.mark.parametrize("strategy", ["ambiguous_name", "ambiguous_channel"])
async def test_ambiguous_contact_creates_flagged_entity_link_and_content_free_log(caplog, strategy):
    conn = _Conn()
    with caplog.at_level(logging.WARNING):
        await _engine(conn, strategy)(_contact())

    assert len(conn.entities) == 1 and len(conn.links) == 1
    assert conn.entities[0]["metadata"]["duplicate_candidate"] is True
    assert conn.links[0][3] == conn.entities[0]["id"]
    records = [r for r in caplog.records if r.getMessage() == "contact_sync_ambiguous"]
    assert len(records) == 1
    record = records[0]
    assert (record.provider, record.account_id, record.strategy) == ("telegram", "acct-1", strategy)
    assert record.reason == "created_as_duplicate_candidate"
    logged = " ".join(str(v) for v in vars(record).values())
    assert _EXTERNAL_ID not in logged and _NAME not in logged


async def test_next_sync_resolves_by_source_link_and_creates_no_second_entity():
    conn = _Conn()
    engine = _engine(conn)
    await engine(_contact())
    entity_id = conn.entities[0]["id"]

    engine._resolver.resolve = AsyncMock(return_value=(entity_id, "source_link"))
    engine._writer.update_contact = AsyncMock(return_value={})
    engine._writer.upsert_source_link = AsyncMock()
    pool_exists = AsyncMock(return_value=1)
    engine._pool.fetchval = pool_exists
    await engine(_contact())

    assert len(conn.entities) == 1 and conn.inserts == 1
    engine._writer.update_contact.assert_awaited_once()


async def test_failure_after_entity_creation_leaves_neither_row():
    conn = _Conn()
    engine = _engine(conn)
    engine._writer.upsert_source_link = AsyncMock(side_effect=RuntimeError("link write failed"))

    with pytest.raises(RuntimeError):
        await engine(_contact())

    assert conn.entities == [] and conn.links == []


async def test_name_collision_creates_a_distinct_entity_not_the_existing_one():
    conn = _Conn(collide_first_insert=True)
    await _engine(conn)(_contact())

    assert len(conn.entities) == 1
    entity = conn.entities[0]
    assert entity["canonical_name"] != _NAME and entity["canonical_name"].startswith(_NAME)
    assert _NAME in entity["aliases"]
    assert entity["metadata"]["duplicate_candidate"] is True
    assert _EXTERNAL_ID not in entity["canonical_name"]
