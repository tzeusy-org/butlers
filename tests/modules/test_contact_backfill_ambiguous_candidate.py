"""Ambiguous synced contacts become duplicate candidates instead of being dropped.

[bu-60pwv6.40] The resolver returns ``ambiguous_name`` / ``ambiguous_channel`` when it cannot
pick one local match. The engine creates a NEW entity flagged ``duplicate_candidate`` and its
``contacts_source_links`` row in one transaction, logs a ``contact_sync_ambiguous`` warning
(entity id, provider, account, external id, strategy, reason; never the display name), and the
next sync resolves by source link. A name that cannot be made unique skips the contact instead
of failing the run.
"""

from __future__ import annotations

import logging
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
    """Transactional connection that models Postgres's aborted-transaction state.

    An error raised outside a savepoint aborts the transaction: every later statement fails
    until the transaction rolls back. An error inside a savepoint only rolls the savepoint
    back, so the caller may continue.
    """

    def __init__(self, *, collisions: int = 0) -> None:
        self.collisions = collisions  # leading INSERTs that violate the name constraint
        self.entities: list[dict] = []
        self.links: list[tuple] = []
        self.inserts = 0
        self.depth = 0
        self.aborted = False

    def _check(self) -> None:
        if self.aborted:
            raise asyncpg.InFailedSQLTransactionError("current transaction is aborted")

    @asynccontextmanager
    async def transaction(self):
        self.depth += 1
        outermost = self.depth == 1
        mark = (len(self.entities), len(self.links))
        try:
            yield
        except BaseException:
            if outermost:
                del self.entities[mark[0] :]
                del self.links[mark[1] :]
                self.aborted = False  # rollback ends the aborted state
            elif not self.aborted:
                del self.entities[mark[0] :]
                del self.links[mark[1] :]
            raise
        finally:
            self.depth -= 1

    async def fetchrow(self, sql, entity_id, canonical, entity_type, aliases):
        assert "INSERT INTO public.entities" in sql
        self._check()
        self.inserts += 1
        if self.inserts <= self.collisions:
            if self.depth == 1:  # no savepoint: the whole transaction is now aborted
                self.aborted = True
            raise asyncpg.UniqueViolationError("duplicate key")
        row = {
            "id": entity_id,
            "canonical_name": canonical,
            "aliases": aliases,
            "metadata": {},
        }
        self.entities.append(row)
        return row

    async def execute(self, sql, *args):
        self._check()
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


def _contact(external_id: str = _EXTERNAL_ID) -> CanonicalContact:
    return CanonicalContact(
        external_id=external_id,
        etag="e1",
        display_name=_NAME,
        first_name="Alex",
        last_name="Smith",
    )


def _engine(conn: _Conn, strategy: str = "ambiguous_name", account: str = "acct-1"):
    engine = ContactBackfillEngine(_Pool(conn), provider="telegram", account_id=account)
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
async def test_ambiguous_contact_creates_flagged_entity_link_and_log(caplog, strategy):
    conn = _Conn()
    with caplog.at_level(logging.WARNING):
        await _engine(conn, strategy)(_contact())

    assert len(conn.entities) == 1 and len(conn.links) == 1
    entity = conn.entities[0]
    assert entity["metadata"]["duplicate_candidate"] is True
    assert conn.links[0][3] == entity["id"]
    records = [r for r in caplog.records if r.getMessage() == "contact_sync_ambiguous"]
    assert len(records) == 1
    record = records[0]
    assert record.entity_id == str(entity["id"])
    assert (record.provider, record.account_id) == ("telegram", "acct-1")
    assert (record.external_id, record.strategy) == (_EXTERNAL_ID, strategy)
    assert record.reason == "created_as_duplicate_candidate"
    logged = " ".join(str(v) for v in vars(record).values())
    assert _NAME not in logged and "Smith" not in logged


async def test_candidate_name_suffix_is_the_entity_id_prefix_and_not_derived_from_the_contact():
    conn = _Conn()
    engine = _engine(conn)
    await engine(_contact())
    await engine(_contact())  # same external id again: a distinct entity and suffix

    first, second = conn.entities
    for entity in (first, second):
        assert entity["canonical_name"] == f"{_NAME} [{entity['id'].hex[:8]}]"
        assert _NAME in entity["aliases"]
        assert _EXTERNAL_ID not in entity["canonical_name"]
    assert first["canonical_name"] != second["canonical_name"]


async def test_next_sync_resolves_by_source_link_and_creates_no_second_entity():
    conn = _Conn()
    engine = _engine(conn)
    await engine(_contact())
    entity_id = conn.entities[0]["id"]

    engine._resolver.resolve = AsyncMock(return_value=(entity_id, "source_link"))
    engine._writer.update_contact = AsyncMock(return_value={})
    engine._writer.upsert_source_link = AsyncMock()
    engine._pool.fetchval = AsyncMock(return_value=1)
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


async def test_one_name_collision_retries_in_a_savepoint_and_still_creates_the_entity():
    # Without the savepoint, the collision would leave the transaction aborted and the
    # retry INSERT would fail with InFailedSQLTransactionError.
    conn = _Conn(collisions=1)
    await _engine(conn)(_contact())

    assert conn.inserts == 2 and len(conn.entities) == 1 and len(conn.links) == 1


async def test_double_collision_skips_the_contact_and_the_run_continues(caplog):
    conn = _Conn(collisions=2)
    engine = _engine(conn)
    with caplog.at_level(logging.WARNING):
        await engine(_contact("people/colliding"))  # returns normally, writes nothing

    assert conn.entities == [] and conn.links == []
    record = next(r for r in caplog.records if r.getMessage() == "contact_sync_ambiguous")
    assert record.reason == "candidate_name_unavailable" and record.entity_id is None
    assert record.external_id == "people/colliding"

    await engine(_contact("people/other"))  # the next contact in the run is written

    assert len(conn.entities) == 1 and len(conn.links) == 1
    assert conn.links[0][2] == "people/other"
