"""Actual source capture under a finite single-slot scheduler, not PG/MCP proof.

REQ-relationship-facts-006: released pool capacity cannot weaken frozen source
attribution, its concurrent winner, or cancellation/changed-source refusal.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
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

    def acquire(self, *, timeout=None):
        return self._acquire_slot(timeout)

    @asynccontextmanager
    async def _acquire_slot(self, seconds):
        async with asyncio.timeout(seconds):
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

    async def fetchrow(self, query, *_args, **_options):
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


@pytest.mark.parametrize("changed", ["sender", "payload", "text", "lifecycle", "missing"])
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
                elif changed == "lifecycle":
                    pool.row["lifecycle_state"] = "dead_letter"
                else:
                    pool.row = None
            release.set()
            if changed == "missing":
                with pytest.raises(ValueError, match="accepted source is unavailable"):
                    await task
            else:
                with pytest.raises(ValueError, match="accepted source.*changed"):
                    await task
        if changed == "missing":
            assert pool.row is None
        else:
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


@pytest.mark.parametrize(
    "case", ["shared", "exhausted", "acquire-timeout", "query-timeout", "cancel-cleanup"]
)
async def test_initial_snapshot_shares_driver_budget_and_preserves_cleanup(monkeypatch, case):
    """Software driver collaborators enforce budgets; parked cleanup is caller-owned."""
    budget = 0.2
    monkeypatch.setattr(fact_authority, "_CAPTURE_TIMEOUT_SECONDS", budget)
    pool = SourcePool()
    issuer = FactSourceContextRegistry(pool)
    resolutions = []

    async def resolve(*args, **kwargs):
        resolutions.append(True)
        return await pool.owning_resolution(*args, **kwargs)

    issuer.resolve_identity = resolve
    inbox = uuid.uuid4()
    acquire = pool.acquire
    fetchrow = pool.fetchrow
    acquire_budgets, query_budgets, initial_queries, acquisition_elapsed = [], [], [], []
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first = True

    @asynccontextmanager
    async def driver_acquire(**options):
        nonlocal first
        if not first:
            async with acquire(**options) as connection:
                yield connection
            return
        first = False
        seconds = options.get("timeout")
        assert seconds is not None and 0 < seconds <= budget
        acquire_budgets.append(seconds)
        entered.set()
        if case == "cancel-cleanup":
            try:
                async with asyncio.timeout(seconds):
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Driver content timeout closes before its cancellation cleanup.
                cleaning.set()
                await release.wait()
                raise
        if case == "exhausted":
            # Position a late completed acquisition at the deadline boundary.
            # This collaborator deliberately returns late, not a driver proof.
            await asyncio.sleep(budget * 1.25)
            async with acquire() as connection:
                yield connection
            return
        started = asyncio.get_running_loop().time()
        async with AsyncExitStack() as stack:
            async with asyncio.timeout(seconds):
                if case == "acquire-timeout":
                    await asyncio.Event().wait()
                await asyncio.sleep(budget / 4)
                connection = await stack.enter_async_context(acquire(timeout=seconds))
            acquisition_elapsed.append(asyncio.get_running_loop().time() - started)
            yield connection

    async def driver_fetchrow(query, *args, **options):
        initial = "message_inbox" in query and "FOR UPDATE" not in query
        if initial:
            initial_queries.append(True)
            seconds = options.get("timeout")
            assert seconds is not None, "snapshot query needs a driver deadline"
            assert 0 < seconds < acquire_budgets[0], "query must use the remaining budget"
            assert seconds <= acquire_budgets[0] - acquisition_elapsed[0], (
                "acquisition time must be deducted from query allowance"
            )
            query_budgets.append(seconds)
            async with asyncio.timeout(seconds):
                if case == "query-timeout":
                    await asyncio.Event().wait()
                return await fetchrow(query, *args, **options)
        return await fetchrow(query, *args, **options)

    pool.acquire = driver_acquire
    pool.fetchrow = driver_fetchrow
    if case == "shared":
        async with asyncio.timeout(1):
            report = await issuer.capture_accepted_report(inbox, pool.entity_id)
        assert report.authority == "owner"
        assert pool.row["request_context"]["_fact_source_report"] == report.to_record()
        assert issuer._reports[inbox] == report
        assert issuer._source_digests[inbox] == fact_authority.accepted_source_digest(pool.row)
        assert len(query_budgets) == 1
    elif case == "cancel-cleanup":
        task = asyncio.create_task(issuer.capture_accepted_report(inbox, None))
        try:
            async with asyncio.timeout(1):
                await entered.wait()
                task.cancel()
                await cleaning.wait()
            await asyncio.sleep(budget * 2)
            assert not task.done(), "capture timer interrupted parked driver cleanup"
            assert query_budgets == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    else:
        async with asyncio.timeout(1):
            with pytest.raises(TimeoutError):
                await issuer.capture_accepted_report(inbox, pool.entity_id)
        if case in {"exhausted", "acquire-timeout"}:
            assert initial_queries == []
        else:
            assert len(query_budgets) == 1
    if case != "shared":
        assert resolutions == []
        assert "_fact_source_report" not in pool.row["request_context"]
        assert inbox not in issuer._reports
        assert inbox not in issuer._source_digests
    # Every negative has the same healthy commit/cache and reusable-slot companion.
    pool.acquire, pool.fetchrow = acquire, fetchrow
    async with asyncio.timeout(1), pool.acquire():
        pass
    async with asyncio.timeout(1):
        report = await issuer.capture_accepted_report(inbox, pool.entity_id)
    assert report.authority == "owner"
    assert pool.row["request_context"]["_fact_source_report"] == report.to_record()
    assert issuer._reports[inbox] == report
