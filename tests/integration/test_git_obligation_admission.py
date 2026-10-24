"""Real migrated same-owner Git admission, actor locks and independent readback.

REQ-goal-orchestration-007; REQ-capability-authorization-003.
Authored PostgreSQL controls: collection/software collaborators are NOT execution.
No provider, runtime runner, D7 or notification is invoked.
"""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio

from butlers.config import load_config
from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.catalog_reader import DOMAINS
from butlers.core.obligation_admission import run_source_transaction
from butlers.core.obligation_git_admission import bind_private_git_admission
from butlers.core.obligation_git_sources import capture_loaded_git_sources, start_schedule_sources
from butlers.core.runtime_config import RuntimeConfig
from butlers.core.scheduler import tick
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.contracts.test_catalog_publication import registered_catalog
from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog
from tests.integration.test_catalog_publication import runtime_pool
from tests.polling import poll_until
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import configure

pytestmark = pytest.mark.integration


@pytest.fixture
def git_admission_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


@pytest_asyncio.fixture(loop_scope="function")
async def git_admission(git_admission_url, tmp_path):
    url = git_admission_url
    # Explicit owning test configuration, not production activation. The actual
    # staffer Insight notify graph stays absent/refused in the software owner.
    (tmp_path / "butler.toml").write_text("""
[butler]
name="switchboard"
port=41100
type="staffer"
[[butler.schedule]]
name="private-git-read"
cron="* * * * *"
prompt="Read the registered entity graph."
[butler.schedule.obligation]
version=1
capabilities=[{capability="switchboard__entity_graph_walk"}]
""")
    registry = await _complete_core_catalog("switchboard")
    loaded = capture_loaded_git_sources(
        load_config(tmp_path), RuntimeConfig("switchboard"), registry
    )
    async with asyncpg.create_pool(url, init=register_jsonb_codec, min_size=1, max_size=3) as owner:
        await owner.execute(
            "ALTER TABLE public.capability_catalog_control DISABLE ROW LEVEL SECURITY"
        )
        try:
            await owner.execute("UPDATE public.capability_catalog_control SET enabled=true")
        finally:
            await owner.execute(
                "ALTER TABLE public.capability_catalog_control ENABLE ROW LEVEL SECURITY"
            )
        await configure(owner, limits())
        for domain in DOMAINS:
            catalog = registry if domain == "switchboard" else (await registered_catalog(domain))[1]
            async with runtime_pool(url, domain) as writer:
                await CatalogPublisher(writer, domain).publish(catalog)

        async def own_role(connection):
            await connection.execute("SET ROLE butler_switchboard_rw")

        async with asyncpg.create_pool(
            url,
            init=register_jsonb_codec,
            setup=own_role,
            min_size=1,
            max_size=3,
            server_settings={"search_path": "switchboard,public"},
        ) as pool:
            staged = await start_schedule_sources(pool, loaded, mode="dark")
            runtime = bind_private_git_admission(pool, staged, loaded, registry, mode="dark")
            source = runtime.bindings[0][0].source_id
            await pool.execute(
                "UPDATE switchboard.scheduled_tasks SET next_run_at=$2 WHERE id=$1",
                source,
                datetime.now(UTC) - timedelta(minutes=1),
            )
            try:
                yield owner, pool, runtime, source, loaded, registry, url
            finally:
                await runtime.close_admission()


async def candidate(pool, runtime, source):
    selected = await pool.fetchrow("SELECT * FROM switchboard.scheduled_tasks WHERE id=$1", source)
    claim = await runtime.claim_cron(
        pool, selected, next_run_at=selected["next_run_at"] + timedelta(minutes=1), disable=False
    )
    assert claim is not None and claim.submission is not None
    return claim.submission


async def observed(owner, occurrence):
    # Separate acquisition after the production transaction has released its
    # connection; no in-transaction absence promoted to durable readback.
    async with owner.acquire() as independent:
        return await independent.fetchrow(
            "SELECT d.result,d.reason,d.goal_id,g.ceiling,g.exposed,g.contract_digest,"
            "g.occurrence_key,b.goal_id AS budget_goal,a.attempt_id "
            "FROM switchboard.admission_attempt_bindings a "
            "JOIN switchboard.admission_decisions d ON d.id=a.decision_id "
            "LEFT JOIN switchboard.goals g ON g.id=d.goal_id "
            "LEFT JOIN switchboard.budgets b ON b.goal_id=g.id WHERE a.attempt_id=$1",
            occurrence,
        )


async def test_real_registered_scheduler_commits_goal_budget_and_full_tuple(git_admission):
    owner, pool, runtime, source, _, _, url = git_admission
    dispatch = AsyncMock(return_value={"legacy": "unchanged"})
    assert await tick(pool, dispatch, source_runtime=runtime) == 1
    dispatch.assert_awaited_once()
    occurrence = await pool.fetchrow(
        "SELECT * FROM switchboard.obligation_occurrences WHERE source_id=$1", source
    )
    stored = await observed(owner, occurrence["id"])
    assert stored["result"] == "admitted" and stored["goal_id"] == stored["budget_goal"]
    assert stored["exposed"] is True and stored["contract_digest"] == occurrence["revision"]
    assert stored["ceiling"] == [
        {"capability": "switchboard__entity_graph_walk", "resources": {}, "park_only": False}
    ]
    assert stored["attempt_id"] == occurrence["id"]
    assert await tick(pool, dispatch, source_runtime=runtime) == 0
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
    next_submission = await candidate(pool, runtime, source)
    # Actual other-domain role cannot be relabeled Switchboard by a factory.
    async with runtime_pool(url, "health") as wrong_pool:
        refused = bind_private_git_admission(
            wrong_pool,
            replace(runtime, admission=None),
            git_admission[4],
            git_admission[5],
            mode="dark",
        )
        assert await refused.submit_committed(next_submission) is None
        assert await observed(owner, next_submission.occurrence_id) is None
        await refused.close_admission()
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1


async def test_two_real_actors_wait_on_same_attempt_then_one_durable_goal(git_admission):
    owner, pool, runtime, source, loaded, registry, _ = git_admission
    submitted = await candidate(pool, runtime, source)
    pids = asyncio.Queue()

    class TrackedPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as real:
                await pids.put(real.get_server_pid())
                yield real

    first = bind_private_git_admission(
        TrackedPool(), replace(runtime, admission=None), loaded, registry, mode="dark"
    )
    second = bind_private_git_admission(
        TrackedPool(), replace(runtime, admission=None), loaded, registry, mode="dark"
    )
    key = "attempt:" + str(submitted.occurrence_id)
    lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
    actors = []
    try:
        async with asyncio.timeout(4):
            async with owner.acquire() as blocker, blocker.transaction():
                await blocker.execute("SELECT pg_advisory_xact_lock($1::bigint)", lock)
                actors = [
                    asyncio.create_task(r.submit_committed(submitted)) for r in (first, second)
                ]
                backend_ids = [await pids.get(), await pids.get()]
                assert len(set(backend_ids)) == 2

                async def both_waiting():
                    return await owner.fetchval(
                        "SELECT count(*)=2 FROM pg_stat_activity WHERE pid=ANY($1::int[]) AND wait_event_type='Lock'",
                        backend_ids,
                    )

                await poll_until(both_waiting, interval_s=0.01)
            results = await asyncio.gather(*actors)
            assert results[0].goal_id == results[1].goal_id
        stored = await observed(owner, submitted.occurrence_id)
        assert stored["result"] == "admitted"
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 1
    finally:
        for task in actors:
            if not task.done():
                task.cancel()
        await asyncio.gather(*actors, return_exceptions=True)
        await first.close_admission()
        await second.close_admission()


@pytest.mark.parametrize("cancel_first", [True, False], ids=["cancel-before", "admit-before"])
async def test_real_cancellation_order_and_history_never_resurrect_source(
    git_admission, cancel_first
):
    owner, pool, runtime, source, _, _, url = git_admission
    submitted = await candidate(pool, runtime, source)

    async def cancel(repository):
        await repository.cancel_occurrence(source, submitted.occurrence_id)

    if cancel_first:
        await run_source_transaction(pool, "switchboard", cancel)
        result = await runtime.submit_committed(submitted)
        assert result.result == "refused" and result.goal_id is None
    else:
        result = await runtime.submit_committed(submitted)
        assert result.result == "admitted"
        await run_source_transaction(pool, "switchboard", cancel)
        # Immutable history is retained, never a fresh claim/current-work proof.
        assert await runtime.submit_committed(submitted) == result
    stored = await observed(owner, submitted.occurrence_id)
    assert stored["result"] == result.result
    assert (
        await owner.fetchval(
            "SELECT state FROM switchboard.obligation_occurrences WHERE id=$1",
            submitted.occurrence_id,
        )
        == "cancelled"
    )
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == (
        0 if cancel_first else 1
    )


async def test_real_commit_loss_same_identity_and_rollback_have_independent_readback(git_admission):
    owner, pool, runtime, source, loaded, registry, _ = git_admission
    submitted = await candidate(pool, runtime, source)

    class LostCommitReply:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as real:

                class Connection:
                    def __getattr__(self, name):
                        return getattr(real, name)

                    @asynccontextmanager
                    async def transaction(self):
                        async with real.transaction():
                            yield
                        raise OSError("controlled lost commit acknowledgement")

                yield Connection()

    uncertain = bind_private_git_admission(
        LostCommitReply(), replace(runtime, admission=None), loaded, registry, mode="dark"
    )
    assert await uncertain.submit_committed(submitted) is None
    durable = await observed(owner, submitted.occurrence_id)
    assert durable["result"] == "admitted"
    assert (await runtime.submit_committed(submitted)).goal_id == durable["goal_id"]
    await uncertain.close_admission()
    next_submission = await candidate(pool, runtime, source)
    await owner.execute("""
        CREATE FUNCTION switchboard.test_git_budget_refusal() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'controlled budget refusal'; END $$;
        CREATE TRIGGER test_git_budget_refusal BEFORE INSERT ON switchboard.budgets
        FOR EACH ROW EXECUTE FUNCTION switchboard.test_git_budget_refusal();
    """)
    try:
        assert await runtime.submit_committed(next_submission) is None
        assert await observed(owner, next_submission.occurrence_id) is None
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 1
        assert await owner.fetchval("SELECT count(*) FROM switchboard.admission_decisions") == 1
    finally:
        await owner.execute(
            "DROP TRIGGER test_git_budget_refusal ON switchboard.budgets; "
            "DROP FUNCTION switchboard.test_git_budget_refusal()"
        )


async def test_source_cancellation_waits_for_admission_fences_through_real_commit(git_admission):
    owner, pool, runtime, source, loaded, registry, _ = git_admission
    submitted = await candidate(pool, runtime, source)
    locked, release = asyncio.Event(), asyncio.Event()
    cancellation_pid = asyncio.Queue()

    class AdmissionPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as real:

                class Connection:
                    def __getattr__(self, name):
                        return getattr(real, name)

                    async def fetchrow(self, sql, *args):
                        row = await real.fetchrow(sql, *args)
                        if (
                            'SELECT * FROM "switchboard".scheduled_tasks' in sql
                            and "FOR UPDATE" in sql
                        ):
                            # Pause AFTER the real source/inventory/row locks,
                            # never substitute a fixture's successful SQL result.
                            locked.set()
                            await release.wait()
                        return row

                yield Connection()

    class CancellationPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as real:
                await cancellation_pid.put(real.get_server_pid())
                yield real

    gated = bind_private_git_admission(
        AdmissionPool(), replace(runtime, admission=None), loaded, registry, mode="dark"
    )
    actors = []
    try:
        async with asyncio.timeout(4):
            admit = asyncio.create_task(gated.submit_committed(submitted))
            actors.append(admit)
            await locked.wait()

            async def cancel(repository):
                await repository.cancel_occurrence(source, submitted.occurrence_id)

            mutation = asyncio.create_task(
                run_source_transaction(CancellationPool(), "switchboard", cancel)
            )
            actors.append(mutation)
            pid = await cancellation_pid.get()

            async def writer_waiting():
                return await owner.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')",
                    pid,
                )

            await poll_until(writer_waiting, interval_s=0.01)
            assert await observed(owner, submitted.occurrence_id) is None
            release.set()
            result = await admit
            assert result.result == "admitted"
            await mutation
        stored = await observed(owner, submitted.occurrence_id)
        assert stored["goal_id"] == result.goal_id and stored["budget_goal"] == result.goal_id
        assert (
            await owner.fetchval(
                "SELECT state FROM switchboard.obligation_occurrences WHERE id=$1",
                submitted.occurrence_id,
            )
            == "cancelled"
        )
        assert await runtime.submit_committed(submitted) == result
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
    finally:
        release.set()
        for task in actors:
            if not task.done():
                task.cancel()
        await asyncio.gather(*actors, return_exceptions=True)
        await gated.close_admission()
