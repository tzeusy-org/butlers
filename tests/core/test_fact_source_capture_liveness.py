"""Actual source capture under a finite single-slot scheduler, not PG/MCP proof.

REQ-relationship-facts-006: released pool capacity cannot weaken frozen source
attribution, its concurrent winner, or cancellation/changed-source refusal.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from butlers.core import fact_authority
from butlers.core.fact_authority import FactSourceContextRegistry, FactWriteContext

pytestmark = pytest.mark.asyncio


class SourcePool:
    """One owned slot and a durable row; pool queries acquire the same slot.

    The double models the contention boundary only. It makes no PostgreSQL,
    locking, network, schema or privilege assertion.
    """

    def __init__(self):
        self.slot = asyncio.Semaphore(1)
        self.entity_id = uuid.uuid4()
        self.created_at = datetime.now(UTC)
        self.row = {
            "request_context": {
                "source_channel": "email",
                "source_sender_identity": "a@example.test",
            },
            "raw_payload": {"message": "Synthetic accepted source"},
            "normalized_text": "Synthetic accepted source",
            "lifecycle_state": "processing",
        }

    @asynccontextmanager
    async def acquire(self, *, timeout=None):
        async with asyncio.timeout(timeout):
            await self.slot.acquire()
        try:
            yield self
        finally:
            self.slot.release()

    @asynccontextmanager
    async def transaction(self):
        original = copy.deepcopy(self.row)
        try:
            yield
        except BaseException:
            self.row = original
            raise

    async def fetchrow(self, query, *_args):
        if "message_inbox" in query:
            return copy.deepcopy(self.row)
        if "public.entities" in query:
            return {"id": self.entity_id, "created_at": self.created_at, "roles": ["owner"]}
        raise AssertionError("Unexpected synthetic query")

    async def execute(self, query, _inbox, record):
        assert "UPDATE switchboard.message_inbox" in query
        self.row["request_context"].update(copy.deepcopy(record))

    async def owning_resolution(self, *_args, **_kwargs):
        # A pool-level routing lookup must use a slot independently of capture.
        async with self.acquire():
            await asyncio.sleep(0)
        return SimpleNamespace(entity_id=self.entity_id)


async def test_single_slot_capture_settles_and_issues_a_verifiable_source():
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    issuer.resolve_identity = pool.owning_resolution
    inbox = uuid.uuid4()
    async with asyncio.timeout(1):
        report = await issuer.capture_accepted_report(inbox, pool.entity_id)
    assert report == FactWriteContext("owner", pool.entity_id, pool.created_at, pool.entity_id)
    assert pool.row["request_context"]["_fact_source_report"] == report.to_record()
    # Verify's pool-level query is represented by the same source double.
    token = issuer.issue(inbox, "relationship")
    assert await issuer.verify(token, "relationship", "incarnation", "invocation") == report
    async with asyncio.timeout(1), pool.acquire():
        pass


async def test_concurrent_capture_reuses_committed_winner_instead_of_stale_resolution():
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    paused, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        await pool.owning_resolution(*args, **kwargs)
        paused.set()
        await release.wait()
        return SimpleNamespace(entity_id=pool.entity_id)

    issuer.resolve_identity = resolve
    inbox = uuid.uuid4()
    task = asyncio.create_task(issuer.capture_accepted_report(inbox, pool.entity_id))
    try:
        async with asyncio.timeout(1):
            await paused.wait()
            winner = FactWriteContext("mixed")
            async with pool.acquire():
                pool.row["request_context"]["_fact_source_report"] = winner.to_record()
            release.set()
            assert await task == winner
        assert issuer._reports[inbox] == winner
        assert pool.row["request_context"]["_fact_source_report"] == winner.to_record()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("changed", ["sender", "payload", "text", "lifecycle"])
async def test_changed_durable_source_cannot_admit_the_earlier_resolution(changed):
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    paused, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        await pool.owning_resolution(*args, **kwargs)
        paused.set()
        await release.wait()
        return SimpleNamespace(entity_id=pool.entity_id)

    issuer.resolve_identity = resolve
    inbox = uuid.uuid4()
    task = asyncio.create_task(issuer.capture_accepted_report(inbox, None))
    try:
        async with asyncio.timeout(1):
            await paused.wait()
            async with pool.acquire():
                if changed == "sender":
                    pool.row["request_context"]["source_sender_identity"] = "b@example.test"
                elif changed == "payload":
                    pool.row["raw_payload"] = {"message": "Different source"}
                elif changed == "text":
                    pool.row["normalized_text"] = "Different text"
                else:
                    pool.row["lifecycle_state"] = "dead_letter"
            release.set()
            with pytest.raises(ValueError, match="accepted source.*changed"):
                await task
        assert "_fact_source_report" not in pool.row["request_context"]
        assert inbox not in issuer._reports
        assert inbox not in issuer._source_digests
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_lookup_leaves_no_report_and_the_pool_reusable():
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    paused = asyncio.Event()

    async def resolve(*args, **kwargs):
        await pool.owning_resolution(*args, **kwargs)
        paused.set()
        await asyncio.Event().wait()

    issuer.resolve_identity = resolve
    inbox = uuid.uuid4()
    task = asyncio.create_task(issuer.capture_accepted_report(inbox, None))
    try:
        async with asyncio.timeout(1):
            await paused.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "_fact_source_report" not in pool.row["request_context"]
        assert inbox not in issuer._reports
        async with asyncio.timeout(1), pool.acquire():
            pass
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_capture_deadlines_bound_pool_wait_and_keep_unresolved_lookup_honest(monkeypatch):
    monkeypatch.setattr(fact_authority, "_CAPTURE_TIMEOUT_SECONDS", 0.05)
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    inbox = uuid.uuid4()
    async with pool.acquire():
        async with asyncio.timeout(1):
            with pytest.raises(TimeoutError):
                await issuer.capture_accepted_report(inbox, None)
    assert inbox not in issuer._reports

    async def unavailable(*_args, **_kwargs):
        await asyncio.Event().wait()

    issuer.resolve_identity = unavailable
    async with asyncio.timeout(1):
        report = await issuer.capture_accepted_report(inbox, None)
    assert report == FactWriteContext("third_party")
    assert pool.row["request_context"]["_fact_source_report"] == report.to_record()
    async with asyncio.timeout(1), pool.acquire():
        pass


@pytest.mark.parametrize("interruption", ["cancel", "deadline"])
async def test_interrupted_freeze_rolls_back_without_admitting_partial_authority(
    monkeypatch, interruption
):
    monkeypatch.setattr(fact_authority, "_CAPTURE_TIMEOUT_SECONDS", 0.05)
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    issuer.resolve_identity = pool.owning_resolution
    paused = asyncio.Event()
    execute = pool.execute

    async def interrupted_execute(*args):
        await execute(*args)
        paused.set()
        await asyncio.Event().wait()

    pool.execute = interrupted_execute
    inbox = uuid.uuid4()
    task = asyncio.create_task(issuer.capture_accepted_report(inbox, pool.entity_id))
    try:
        async with asyncio.timeout(1):
            await paused.wait()
            if interruption == "cancel":
                task.cancel()
                error = asyncio.CancelledError
            else:
                error = TimeoutError
            with pytest.raises(error):
                await task
        assert "_fact_source_report" not in pool.row["request_context"]
        assert inbox not in issuer._reports
        assert inbox not in issuer._source_digests
        pool.execute = execute
        async with asyncio.timeout(1):
            report = await issuer.capture_accepted_report(inbox, pool.entity_id)
        assert report.authority == "owner"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
