"""Real migrated feed admission; authored here, collection is not PG execution.

REQ-capability-authorization-003; REQ-goal-orchestration-007.
No Home foreign-schema write, recorder stand-in, provider or runner is used.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
import pytest
import pytest_asyncio

from butlers.config import load_config
from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.catalog_reader import DOMAINS
from butlers.core.obligation_feed_admission import start_feed_admission
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.contracts.test_catalog_publication import registered_catalog
from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog, _roster_catalog
from tests.core.test_feed_obligation_admission import ROOT, envelope, registered_ingest
from tests.core_tools.test_switchboard_ingest_policy_tier import _FakeBuffer
from tests.integration.test_catalog_publication import runtime_pool
from tests.polling import poll_until
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import configure

pytestmark = pytest.mark.integration


@pytest.fixture
def feed_url(postgres_container):
    # The synchronous migration/extension producer owns its own event loop.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


@pytest_asyncio.fixture(loop_scope="function")
async def migrated_feed(feed_url, monkeypatch):
    async with asyncpg.create_pool(
        feed_url, init=register_jsonb_codec, min_size=1, max_size=4
    ) as owner:
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
        registry = await _complete_core_catalog("switchboard")
        home = await _roster_catalog("home")
        for domain in DOMAINS:
            catalog = (
                registry
                if domain == "switchboard"
                else home
                if domain == "home"
                else (await registered_catalog(domain))[1]
            )
            async with runtime_pool(feed_url, domain) as writer:
                await CatalogPublisher(writer, domain).publish(catalog)

        async def own_role(connection):
            await connection.execute("SET ROLE butler_switchboard_rw")

        async with asyncpg.create_pool(
            feed_url,
            init=register_jsonb_codec,
            setup=own_role,
            min_size=1,
            max_size=4,
            server_settings={"search_path": "switchboard,public"},
        ) as pool:
            # Test-owned registered connector endpoint, no live connector/activation.
            await pool.execute(
                "INSERT INTO switchboard.connector_registry (connector_type,endpoint_identity,capabilities) VALUES ('home_assistant','ha:test',$1::jsonb)",
                {"channel_kind": "feed"},
            )
            daemon = SimpleNamespace(
                config=load_config(ROOT / "roster/switchboard"),
                _tool_catalog=registry,
                _pipeline=object(),
                _buffer=_FakeBuffer(),
            )
            daemon.obligation_feed_runtime = start_feed_admission(
                pool, daemon, registry, mode="dark"
            )
            ingest = await registered_ingest(monkeypatch, pool, daemon)
            try:
                yield owner, pool, daemon, ingest, registry, feed_url
            finally:
                await daemon.obligation_feed_runtime.aclose()


async def settled(runtime):
    # One owning operation's existing five-second cap, no retry.
    async with asyncio.timeout(6):
        while tasks := set(runtime._pending) | set(runtime._watchers):
            await asyncio.wait(tasks)


async def observed(owner, request):
    # A different acquisition after the production transaction has returned.
    async with owner.acquire() as independent:
        return await independent.fetchrow(
            "SELECT d.id,d.result,d.reason,g.id AS goal_id,g.origin,g.exposed,g.ceiling,"
            "g.contract_digest,b.goal_id AS budget_goal,a.attempt_id "
            "FROM switchboard.admission_attempt_bindings a "
            "JOIN switchboard.admission_decisions d ON d.id=a.decision_id "
            "LEFT JOIN switchboard.goals g ON g.id=d.goal_id "
            "LEFT JOIN switchboard.budgets b ON b.goal_id=g.id WHERE a.attempt_id=$1",
            uuid5(NAMESPACE_URL, f"butlers:feed:{request}"),
        )


async def accept_without_hook(daemon, ingest):
    runtime = daemon.obligation_feed_runtime
    daemon.obligation_feed_runtime = None
    try:
        response = await ingest(**envelope())
    finally:
        daemon.obligation_feed_runtime = runtime
    return UUID(response["request_id"]), runtime


async def test_real_registered_feed_commits_exposed_goal_budget_and_stable_replay(migrated_feed):
    owner, pool, daemon, ingest, _, _ = migrated_feed
    response = await ingest(**envelope())
    await settled(daemon.obligation_feed_runtime)
    request = UUID(response["request_id"])
    stored = await observed(owner, request)
    assert stored["result"] == "admitted" and stored["goal_id"] == stored["budget_goal"]
    assert stored["origin"] == "obligation" and stored["exposed"] is True
    assert stored["contract_digest"] and len(stored["ceiling"]) == 7
    assert all(g["capability"].startswith("home__") for g in stored["ceiling"])
    await pool.execute(
        "UPDATE switchboard.connector_registry SET capabilities=$1::jsonb WHERE connector_type='home_assistant' AND endpoint_identity='ha:test'",
        {"channel_kind": "message"},
    )
    duplicate = await ingest(**envelope())
    await settled(daemon.obligation_feed_runtime)
    assert duplicate["duplicate"] and duplicate["request_id"] == str(request)
    assert await observed(owner, request) == stored
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
    assert len(daemon._buffer.enqueue_calls) == 1


async def test_real_registry_row_wait_then_refusal_has_independent_zero_goal(migrated_feed):
    owner, pool, daemon, ingest, _, _ = migrated_feed
    request, runtime = await accept_without_hook(daemon, ingest)
    pids = asyncio.Queue()

    class TrackedPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as connection:
                await pids.put(connection.get_server_pid())
                yield connection

    runtime.pool = TrackedPool()
    async with asyncio.timeout(3):
        async with owner.acquire() as blocker, blocker.transaction():
            await blocker.execute(
                "UPDATE switchboard.connector_registry SET capabilities=$1::jsonb WHERE connector_type='home_assistant' AND endpoint_identity='ha:test'",
                {"channel_kind": "message"},
            )
            holder = blocker.get_server_pid()
            runtime.offer(request)
            contender = await pids.get()

            async def waiting():
                # Exact holder/contender, real row-lock barrier, no runtime monitor grant.
                return await owner.fetchval(
                    "SELECT $2::int=ANY(pg_blocking_pids($1::int))", contender, holder
                )

            await poll_until(waiting, interval_s=0.01)
    await settled(runtime)
    stored = await observed(owner, request)
    assert stored["result"] == "refused" and stored["goal_id"] is None
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
    assert (
        await owner.fetchval("SELECT count(*) FROM switchboard.message_inbox WHERE id=$1", request)
        == 1
    )


async def test_real_commit_reply_loss_resolves_same_attempt_without_second_budget(migrated_feed):
    owner, pool, daemon, ingest, _, _ = migrated_feed
    request, runtime = await accept_without_hook(daemon, ingest)

    class LostReplyPool:
        lost = False

        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as connection:
                proxy = SimpleNamespace(
                    **{
                        name: getattr(connection, name)
                        for name in (
                            "fetch",
                            "fetchrow",
                            "fetchval",
                            "execute",
                            "is_in_transaction",
                        )
                    }
                )

                @asynccontextmanager
                async def transaction():
                    async with connection.transaction():
                        yield
                    if not self.lost:
                        self.lost = True
                        raise ConnectionError("controlled committed reply loss")

                proxy.transaction = transaction
                yield proxy

    runtime.pool = LostReplyPool()
    runtime.offer(request)
    await settled(runtime)
    stored = await observed(owner, request)
    assert stored["result"] == "admitted"
    runtime.offer(request)
    await settled(runtime)
    assert await observed(owner, request) == stored
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 1


@pytest.mark.parametrize("fault", ["wrong_role", "temp_shadow"])
async def test_real_source_role_and_temp_shadow_never_promote_accepted_feed(migrated_feed, fault):
    owner, pool, daemon, ingest, registry, url = migrated_feed
    request, runtime = await accept_without_hook(daemon, ingest)
    if fault == "wrong_role":
        async with runtime_pool(url, "health") as foreign:
            runtime.pool = foreign
            runtime.offer(request)
            await settled(runtime)
    else:
        async with owner.acquire() as connection:
            await connection.execute("CREATE TEMP TABLE message_inbox (id uuid)")
            try:
                await connection.execute("SET ROLE butler_switchboard_rw")

                class Held:
                    @asynccontextmanager
                    async def acquire(self):
                        yield connection

                runtime.pool = Held()
                runtime.offer(request)
                await settled(runtime)
            finally:
                await connection.execute("RESET ROLE")
                await connection.execute("DROP TABLE pg_temp.message_inbox")
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
    assert (
        await owner.fetchval("SELECT count(*) FROM switchboard.message_inbox WHERE id=$1", request)
        == 1
    )
