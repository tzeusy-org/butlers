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
from butlers.modules.memory.storage import endorse_fact

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


@pytest.mark.parametrize("authority", ["owner", "owner_device", None, "system"])
async def test_a_non_held_fact_needs_no_vouch(authority):
    conn = _Conn(_row(content_authority=authority))
    before = dict(conn.row)

    receipt = await endorse_fact(_Pool(conn), conn.row["id"], endorsed_by=_OWNER)

    assert receipt == {
        "fact_id": before["id"],
        "content_authority": authority,
        "endorsed_at": None,
        "endorsed_by": None,
        "changed": False,
    }
    assert conn.row == before
    assert len(conn.calls) == 1 and "FOR UPDATE" in conn.calls[0][0]
    assert _events(conn) == []


async def test_an_unknown_fact_returns_none():
    assert await endorse_fact(_Pool(_Conn(None)), uuid.uuid4(), endorsed_by=_OWNER) is None


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
