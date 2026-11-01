"""Dossier receipts and totals share one read snapshot (REQ-entity-graph-010)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from butlers.core.entity_graph_edges import dossier_for_entity

pytestmark = pytest.mark.unit


class _Pool:
    """Advance the visible graph after every query, like a concurrent writer."""

    def __init__(self, entity: uuid.UUID) -> None:
        self.version = 0
        self.acquisitions = 0
        self.releases = 0
        self.transactions: list[dict] = []
        self.reads: list[tuple[str, int, bool]] = []
        self.edge = {
            "id": uuid.uuid4(),
            "source_schema": "relationship",
            "source_table": "entity_facts",
            "source_id": uuid.uuid4(),
            "subject_entity_id": entity,
            "predicate": "knows",
            "object_entity_id": uuid.uuid4(),
            "sensitivity": "normal",
            "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
        }

    @asynccontextmanager
    async def acquire(self):
        self.acquisitions += 1
        try:
            yield _Connection(self)
        finally:
            self.releases += 1

    def read(self, query, snapshot=None, *, in_transaction=False):
        version = self.version if snapshot is None else snapshot
        if "GROUP BY source_schema" in query:
            kind = "groups"
            result = [
                {
                    "source_schema": "relationship",
                    "source_table": "entity_facts",
                    "live": 1 + version,
                }
            ]
        elif "withheld_reason IS NOT NULL" in query:
            kind, result = "withheld", 2 + version
        else:
            kind, result = "receipts", [self.edge.copy() for _ in range(1 + version)]
        self.reads.append((kind, version, in_transaction))
        self.version += 1
        return result

    async def fetch(self, query, *args):
        return self.read(query)

    async def fetchval(self, query, *args):
        return self.read(query)


class _Connection:
    def __init__(self, pool: _Pool) -> None:
        self.pool = pool
        self.snapshot = None
        self.in_transaction = False

    @asynccontextmanager
    async def transaction(self, **options):
        self.pool.transactions.append(options)
        self.snapshot = self.pool.version if options.get("isolation") == "repeatable_read" else None
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    async def fetch(self, query, *args):
        return self.pool.read(query, self.snapshot, in_transaction=self.in_transaction)

    async def fetchval(self, query, *args):
        return self.pool.read(query, self.snapshot, in_transaction=self.in_transaction)


async def test_dossier_reads_receipts_and_totals_from_one_snapshot():
    entity = uuid.uuid4()
    pool = _Pool(entity)

    read = await dossier_for_entity(pool, entity)

    # Later queries must not see the writer's intervening graph versions.
    assert len(read.receipts) == 1
    assert read.receipts[0].edge_id == pool.edge["id"]
    assert read.live_by_source == {("relationship", "entity_facts"): 1}
    assert read.withheld == 2
    assert pool.version == 3
    assert pool.acquisitions == pool.releases == 1
    assert pool.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert pool.reads == [("groups", 0, True), ("receipts", 0, True), ("withheld", 0, True)]
