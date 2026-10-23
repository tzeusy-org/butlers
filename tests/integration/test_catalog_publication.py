"""Actual migrated catalog role/bootstrap/commit/race and admission contracts.

REQ-capability-authorization-002. These require real PostgreSQL, not the
software repository double. No local setup ERROR or collection is a PASS.
"""

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.catalog_reader import DOMAINS, PublishedCatalogProvider
from butlers.core.goal_admission import EvidenceRefused, GoalAdmission, SourceRef
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.contracts.test_catalog_publication import registered_catalog
from tests.polling import poll_until
from tests.switchboard.goal_admission_fixtures import Sources, limits
from tests.switchboard.goal_ledger_fixtures import configure

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def publication_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


@asynccontextmanager
async def runtime_pool(url, domain):
    assert domain in DOMAINS

    async def role(connection):
        await connection.execute(f"SET ROLE butler_{domain}_rw")

    async with asyncpg.create_pool(
        url, init=register_jsonb_codec, setup=role, min_size=1, max_size=3
    ) as pool:
        yield pool


@pytest_asyncio.fixture(loop_scope="function")
async def fleet(publication_url):
    async with asyncpg.create_pool(
        publication_url, init=register_jsonb_codec, min_size=1, max_size=2
    ) as owner:
        # Explicit privileged fixture configuration, not runtime activation.
        # Actual owner can ALTER its own tables; runtime roles cannot.
        await owner.execute(
            "ALTER TABLE public.capability_catalog_control DISABLE ROW LEVEL SECURITY"
        )
        try:
            await owner.execute("UPDATE public.capability_catalog_control SET enabled=true")
        finally:
            await owner.execute(
                "ALTER TABLE public.capability_catalog_control ENABLE ROW LEVEL SECURITY"
            )
        yield owner


async def publish_all(url):
    identities = {}
    for domain in DOMAINS:
        async with runtime_pool(url, domain) as pool:
            publisher = CatalogPublisher(pool, domain)
            _, catalog = await registered_catalog(domain)
            await publisher.publish(catalog)
            identities[domain] = (publisher.instance, publisher.revision)
    return identities


async def test_migrated_complete_fleet_actual_goal_admission_and_reader(publication_url, fleet):
    await publish_all(publication_url)
    await configure(fleet, limits())
    async with runtime_pool(publication_url, "switchboard") as pool:
        result = await GoalAdmission(pool, Sources()).admit_submission(
            SourceRef("message", str(uuid4())),
            uuid4(),
        )
        assert result.result == "admitted"
        # Separate actual owner acquisition observes committed goal and digest.
        stored = await fleet.fetchrow(
            "SELECT catalog_digest,ceiling FROM switchboard.goals WHERE id=$1",
            result.goal_id,
        )
        assert stored["catalog_digest"] and any(
            grant["capability"] == "health__lookup" for grant in stored["ceiling"]
        )
        async with pool.acquire() as connection, connection.transaction():
            snapshot = await PublishedCatalogProvider().snapshot(connection)
            assert stored["catalog_digest"] == snapshot.digest
            health = snapshot.registration("health")
            assert (
                await PublishedCatalogProvider().require_registration(
                    connection,
                    domain="health",
                    revision_id=health.revision_id,
                    generation_digest=health.catalog.generation_digest,
                )
                == health
            )


async def test_real_roles_bootstrap_immutability_and_bad_publisher(
    publication_url,
    fleet,
    postgres_container,
):
    from tests.migrations.test_goal_ledger_migration import bootstrap_again

    # Bootstrap deliberately regrants public DML; non-owner runtime RLS must still fence.
    await asyncio.to_thread(bootstrap_again, postgres_container, publication_url)
    await publish_all(publication_url)
    # Ordinary database-owner maintenance is not a runtime authority. Creator
    # changes during managed re-upgrade must not change this stable ownership.
    posture = await fleet.fetch(
        "SELECT c.relname,c.relowner=d.datdba AS database_owned,"
        "c.relrowsecurity,c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN pg_database d "
        "WHERE n.nspname='public' AND c.relname LIKE 'capability_catalog_%' "
        "AND c.relkind='r' AND d.datname=current_database()"
    )
    assert len(posture) == 5
    assert all(
        r["database_owned"] and r["relrowsecurity"] and not r["relforcerowsecurity"]
        for r in posture
    )
    from tests.migrations.test_goal_ledger_migration import (
        BOOTSTRAP_RUNTIME_ROLES,
        OPTIONAL_BOOTSTRAP_ROLES,
    )

    for role in sorted(BOOTSTRAP_RUNTIME_ROLES | OPTIONAL_BOOTSTRAP_ROLES):
        privileges = await fleet.fetchrow(
            "SELECT rolsuper,rolbypassrls,pg_has_role(r.oid,d.datdba,'MEMBER') AS owner_member "
            "FROM pg_roles r CROSS JOIN pg_database d "
            "WHERE rolname=$1 AND d.datname=current_database()",
            role,
        )
        if privileges:
            assert not any(privileges.values())
            async with fleet.acquire() as connection, connection.transaction():
                await connection.execute(f'SET LOCAL ROLE "{role}"')
                assert await connection.fetchval(
                    "SELECT row_security_active('public.capability_catalog_control'::regclass)"
                )
                try:
                    async with connection.transaction():
                        assert (
                            await connection.execute(
                                "DELETE FROM public.capability_catalog_installations"
                            )
                            == "DELETE 0"
                        )
                except asyncpg.InsufficientPrivilegeError:
                    pass
    original = await fleet.fetchval(
        "SELECT to_jsonb(c) FROM public.capability_catalog_current c WHERE domain='health'"
    )
    async with runtime_pool(publication_url, "relationship") as pool:
        _, catalog = await registered_catalog()
        with pytest.raises(EvidenceRefused):
            await CatalogPublisher(pool, "health").publish(catalog)
        async with pool.acquire() as connection, connection.transaction():
            for sql in (
                "UPDATE public.capability_catalog_control SET enabled=false",
                "DELETE FROM public.capability_catalog_members WHERE domain='health'",
                "UPDATE public.capability_catalog_history SET body='{}'::jsonb WHERE domain='health'",
                "UPDATE public.capability_catalog_current SET active=false WHERE domain='health'",
                "DELETE FROM public.capability_catalog_current WHERE domain='health'",
            ):
                # Each statement is isolated: a refusal cannot poison the rest.
                try:
                    async with connection.transaction():
                        assert await connection.execute(sql) in {"UPDATE 0", "DELETE 0"}
                except asyncpg.InsufficientPrivilegeError:
                    pass
    assert (
        await fleet.fetchval(
            "SELECT to_jsonb(c) FROM public.capability_catalog_current c WHERE domain='health'"
        )
        == original
    )
    async with runtime_pool(publication_url, "health") as pool:
        async with pool.acquire() as connection, connection.transaction():
            with pytest.raises(asyncpg.CheckViolationError):
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE public.capability_catalog_current SET revision_id=$1 WHERE domain='health'",
                        uuid4(),
                    )
            assert (
                await connection.fetchval(
                    "SELECT row_security_active('public.capability_catalog_history'::regclass)"
                )
                is True
            )


async def test_real_replacement_concurrency_retirement_and_stale_refusal(publication_url, fleet):
    await publish_all(publication_url)
    async with runtime_pool(publication_url, "health") as pool:
        first, second = CatalogPublisher(pool, "health"), CatalogPublisher(pool, "health")
        _, catalog = await registered_catalog()
        await asyncio.gather(first.publish(catalog), second.publish(catalog))
        async with pool.acquire() as connection:
            live_first = await first.resolve(connection)
            live_second = await second.resolve(connection)
        assert live_first != live_second
        stale, current = (second, first) if live_first else (first, second)
        # Exercise a same-boot next revision as a real UPDATE, after the two
        # fresh boots have already exercised replacement of an existing row.
        before = await fleet.fetchrow(
            "SELECT epoch,created_at FROM public.capability_catalog_current WHERE domain='health'"
        )
        history_count = await fleet.fetchval(
            "SELECT count(*) FROM public.capability_catalog_history WHERE domain='health'"
        )
        previous_revision = current.revision
        await current.publish(catalog)
        after = await fleet.fetchrow(
            "SELECT epoch,created_at,revision_id FROM public.capability_catalog_current "
            "WHERE domain='health'"
        )
        assert after["epoch"] == before["epoch"] + 1
        assert after["created_at"] == before["created_at"]
        assert after["revision_id"] == current.revision != previous_revision
        assert (
            await fleet.fetchval(
                "SELECT count(*) FROM public.capability_catalog_history WHERE domain='health'"
            )
            == history_count + 1
        )
        await stale.retire()
        with pytest.raises(EvidenceRefused):
            await stale.renew()
        async with runtime_pool(publication_url, "switchboard") as readers:
            async with readers.acquire() as connection, connection.transaction():
                snapshot = await PublishedCatalogProvider().snapshot(connection)
                assert snapshot.registration("health").revision_id == current.revision
                with pytest.raises(EvidenceRefused):
                    await PublishedCatalogProvider().require_registration(
                        connection,
                        domain="health",
                        revision_id=stale.revision,
                        generation_digest=catalog.generation_digest,
                    )
            await current.retire()
            async with readers.acquire() as connection, connection.transaction():
                with pytest.raises(EvidenceRefused):
                    await PublishedCatalogProvider().load(connection)
            with pytest.raises(EvidenceRefused):
                await current.publish(catalog)
            current = CatalogPublisher(pool, "health")
            await current.publish(catalog)
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE public.capability_catalog_current "
                    "SET expires_at=clock_timestamp()-interval '1 second' WHERE domain='health'"
                )
            with pytest.raises(EvidenceRefused):
                await current.renew()
            async with readers.acquire() as connection, connection.transaction():
                with pytest.raises(EvidenceRefused):
                    await PublishedCatalogProvider().load(connection)


async def test_committed_lost_reply_readback_and_rollback(publication_url, fleet):
    await publish_all(publication_url)
    async with runtime_pool(publication_url, "health") as pool:
        publisher = CatalogPublisher(pool, "health")
        _, catalog = await registered_catalog(description="Actual committed replacement.")

        # Wrap only the transport's commit acknowledgement: real SQL/COMMIT
        # occurs first; no fake durable receipt or retry of a new revision.
        class LostCommitPool:
            @asynccontextmanager
            async def acquire(self):
                async with pool.acquire() as connection:

                    class CommitReply:
                        def __getattr__(self, name):
                            return getattr(connection, name)

                        @asynccontextmanager
                        async def transaction(self):
                            async with connection.transaction():
                                yield
                            raise ConnectionError("controlled committed reply loss")

                    yield CommitReply()

        publisher.pool = LostCommitPool()
        with pytest.raises(ConnectionError):
            await publisher.publish(catalog)
        async with pool.acquire() as observer:
            assert await publisher.resolve(observer)
        count = await fleet.fetchval("SELECT count(*) FROM public.capability_catalog_history")
        async with pool.acquire() as connection:
            with pytest.raises(RuntimeError):
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE public.capability_catalog_current SET active=false WHERE domain='health'"
                    )
                    raise RuntimeError("controlled rollback")
        assert (
            await fleet.fetchval("SELECT count(*) FROM public.capability_catalog_history") == count
        )
        async with pool.acquire() as observer:
            assert await publisher.resolve(observer)

        def refuse_history_loss():
            path = (
                Path(__file__).resolve().parents[2]
                / "alembic/versions/core/core_275_capability_catalog_publication.py"
            )
            spec = importlib.util.spec_from_file_location("catalog_history_rollback", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            engine = create_engine(publication_url)
            try:
                with pytest.raises(DBAPIError, match="cannot discard catalog publication history"):
                    with engine.begin() as connection:
                        with Operations.context(MigrationContext.configure(connection)):
                            migration.downgrade()
            finally:
                engine.dispose()

        await asyncio.to_thread(refuse_history_loss)
        assert (
            await fleet.fetchval("SELECT count(*) FROM public.capability_catalog_history") == count
        )
        async with pool.acquire() as observer:
            assert await publisher.resolve(observer)


def test_core_only_replay_and_downgrade_preserves_other_installations(postgres_container):
    url = create_migrated_test_db(postgres_container, migration_db_name(), ["core"])
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_275_capability_catalog_publication.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_migration_control", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.exec_driver_sql(
                        "SELECT enabled FROM public.capability_catalog_control"
                    ).scalar()
                    is False
                )
                assert (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM public.capability_catalog_members"
                    ).scalar()
                    == len(DOMAINS) + 1
                )
                original_schema = connection.exec_driver_sql("SELECT current_schema()").scalar()
                quote = connection.dialect.identifier_preparer.quote
                connection.exec_driver_sql("SET LOCAL search_path TO education,public")
                migration.upgrade()
                connection.exec_driver_sql(
                    f"SET LOCAL search_path TO {quote(original_schema)},public"
                )
                migration.downgrade()
                assert (
                    connection.exec_driver_sql(
                        "SELECT to_regclass('public.capability_catalog_current')"
                    ).scalar()
                    is not None
                )
                connection.exec_driver_sql("SET LOCAL search_path TO education,public")
                migration.downgrade()
                assert (
                    connection.exec_driver_sql(
                        "SELECT to_regclass('public.capability_catalog_current')"
                    ).scalar()
                    is None
                )
    finally:
        engine.dispose()


async def test_real_superseded_publish_waits_then_refuses_without_history_change(
    publication_url, fleet
):
    """B owns the serialized replacement before stale A resumes its publication."""
    await publish_all(publication_url)
    async with runtime_pool(publication_url, "health") as pool:
        old = CatalogPublisher(pool, "health")
        _, catalog = await registered_catalog()
        await old.publish(catalog)
        entered, release, old_acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pids = {}

        class GateConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def execute(self, sql, *args):
                result = await self.connection.execute(sql, *args)
                if "publish_capability_catalog" in sql:
                    entered.set()
                    await asyncio.wait_for(release.wait(), 5)
                return result

        class ActorPool:
            def __init__(self, name):
                self.name = name

            @asynccontextmanager
            async def acquire(self):
                async with pool.acquire() as connection:
                    pids[self.name] = connection.get_server_pid()
                    if self.name == "old":
                        old_acquired.set()
                    yield GateConnection(connection) if self.name == "new" else connection

        new = CatalogPublisher(ActorPool("new"), "health")
        tasks = []
        try:
            tasks.append(asyncio.create_task(new.publish(catalog)))
            await asyncio.wait_for(entered.wait(), 5)
            # Observe the committed baseline while B's new revision is uncommitted.
            before_count = await fleet.fetchval(
                "SELECT count(*) FROM public.capability_catalog_history WHERE domain='health'"
            )
            old.pool = ActorPool("old")
            _, changed = await registered_catalog(description="Stale old finalizer.")
            tasks.append(asyncio.create_task(old.publish(changed)))
            await asyncio.wait_for(old_acquired.wait(), 5)
            assert pids["new"] != pids["old"]

            async def old_publication_waits_on_lock():
                return await fleet.fetchval(
                    "SELECT wait_event_type='Lock' AND query LIKE '%publish_capability_catalog%' "
                    "FROM pg_stat_activity WHERE pid=$1",
                    pids["old"],
                )

            async with asyncio.timeout(5):
                await poll_until(old_publication_waits_on_lock, interval_s=0.01)
            release.set()
            await asyncio.wait_for(tasks[0], 5)
            with pytest.raises(EvidenceRefused):
                await asyncio.wait_for(tasks[1], 5)
            assert (
                await fleet.fetchval(
                    "SELECT revision_id FROM public.capability_catalog_current WHERE domain='health'"
                )
                == new.revision
            )
            assert (
                await fleet.fetchval(
                    "SELECT count(*) FROM public.capability_catalog_history WHERE domain='health'"
                )
                == before_count + 1
            )
            async with runtime_pool(publication_url, "switchboard") as readers:
                async with readers.acquire() as connection, connection.transaction():
                    assert (await PublishedCatalogProvider().snapshot(connection)).registration(
                        "health"
                    ).revision_id == new.revision
            await new.publish(changed)
            assert (
                await fleet.fetchval(
                    "SELECT revision_id FROM public.capability_catalog_current WHERE domain='health'"
                )
                == new.revision
            )
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
