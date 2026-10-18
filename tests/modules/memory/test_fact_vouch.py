"""Owner vouch for held facts (bu-jv1moa.14, S1).

Vouching is additive: it stamps ``endorsed_at``/``endorsed_by`` and keeps the fact's
authority and taint exactly as they were.  Clearing ``endorsed_at`` restores the prior
state.  The real-Postgres storage behaviour is covered in CI by the integration suite;
these tests pin the contract against a recording connection.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from butlers.modules.memory import storage
from butlers.modules.memory.storage import FactNotEndorsableError, endorse_fact

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_OWNER = uuid.uuid4()


class _Conn:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "FOR UPDATE" in sql:
            return self.row
        if sql.lstrip().startswith("UPDATE"):
            assert "endorsed_at IS NULL" in sql and "validity IN" in sql  # compare-and-swap
            self.row = {**self.row, "endorsed_at": _NOW, "endorsed_by": args[1]}
            return {"endorsed_at": _NOW}
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return "public.memory_catalog" if "to_regclass" in sql else "relationship"

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1"

    @asynccontextmanager
    async def transaction(self):
        yield


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _row(**overrides):
    row = {
        "id": uuid.uuid4(),
        "content_authority": "third_party",
        "endorsed_at": None,
        "endorsed_by": None,
        "validity": "active",
        "tenant_id": "owner",
        "sensitivity": "normal",
    }
    return {**row, **overrides}


def _events(conn):
    return [c for c in conn.calls if "INSERT INTO memory_events" in c[0]]


async def test_vouch_stamps_the_fact_audits_it_and_keeps_authority_unchanged():
    row = _row()
    conn = _Conn(row)

    receipt = await endorse_fact(_Pool(conn), row["id"], endorsed_by=_OWNER)

    assert receipt["changed"] is True
    assert receipt["endorsed_at"] == _NOW and receipt["endorsed_by"] == _OWNER
    assert receipt["content_authority"] == "third_party"
    update = next(sql for sql, _ in conn.calls if sql.lstrip().startswith("UPDATE"))
    assert "content_authority" not in update and "authority_entity_id" not in update
    ((event_sql, event_args),) = _events(conn)
    assert "fact_vouched" in event_sql and "'owner'" in event_sql
    assert event_args[1] == row["id"]


async def test_a_repeated_vouch_returns_the_original_receipt_and_writes_nothing():
    row = _row(endorsed_at=_NOW, endorsed_by=_OWNER)
    conn = _Conn(row)

    receipt = await endorse_fact(_Pool(conn), row["id"], endorsed_by=uuid.uuid4())

    assert receipt["changed"] is False
    assert receipt["endorsed_by"] == _OWNER
    assert not any(sql.lstrip().startswith("UPDATE") for sql, _ in conn.calls)
    assert _events(conn) == []


@pytest.mark.parametrize("validity", ["superseded", "retracted", "expired"])
async def test_a_dead_fact_is_a_typed_conflict_and_changes_nothing(validity):
    conn = _Conn(_row(validity=validity))

    with pytest.raises(FactNotEndorsableError):
        await endorse_fact(_Pool(conn), conn.row["id"], endorsed_by=_OWNER)

    assert not any(sql.lstrip().startswith("UPDATE") for sql, _ in conn.calls)
    assert _events(conn) == []


async def test_an_owner_class_fact_needs_no_vouch():
    conn = _Conn(_row(content_authority="owner"))

    receipt = await endorse_fact(_Pool(conn), conn.row["id"], endorsed_by=_OWNER)

    assert receipt["changed"] is False
    assert _events(conn) == []


async def test_an_unknown_fact_returns_none():
    assert await endorse_fact(_Pool(_Conn(None)), uuid.uuid4(), endorsed_by=_OWNER) is None


async def test_vouch_re_asserts_the_discovery_catalog_entry_for_a_cataloged_fact():
    conn = _Conn(_row())

    await endorse_fact(_Pool(conn), conn.row["id"], endorsed_by=_OWNER)

    assert any("INSERT INTO public.memory_catalog" in sql for sql, _ in conn.calls)


async def test_vouch_never_catalogs_a_write_excluded_sensitivity():
    conn = _Conn(_row(sensitivity="confidential"))

    await endorse_fact(_Pool(conn), conn.row["id"], endorsed_by=_OWNER)

    assert not any("INSERT INTO public.memory_catalog" in sql for sql, _ in conn.calls)


@pytest.mark.parametrize(("kwargs", "actor"), [({}, "session"), ({"actor": "owner"}, "owner")])
async def test_confirm_records_who_confirmed(kwargs, actor):
    class Pool:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, *args):
            self.calls.append((sql, args))
            return "INSERT 0 1"

    pool = Pool()
    memory_id = uuid.uuid4()

    assert await storage.confirm_memory(pool, "fact", memory_id, **kwargs) is True

    ((sql, args),) = pool.calls
    assert "last_confirmed_at = now()" in sql and "memory_events" in sql
    assert args[0] == memory_id and actor in args
