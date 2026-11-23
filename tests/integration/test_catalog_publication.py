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


async def test_dark_qa_finding_admission_uses_current_catalog_and_one_journal(
    publication_url, fleet, tmp_path, monkeypatch
):
    """Owner-side harness proof only: no released QA peer authentication is claimed."""
    from dataclasses import replace

    from butlers.core.goal_admission import AdmissionUnavailable
    from butlers.core.qa import staffer_source
    from butlers.core.qa.dashboard_inbox import (
        DashboardIdentity,
        DashboardInbox,
        DashboardReport,
        InboxRefused,
    )
    from butlers.core.qa.findings import insert_finding
    from butlers.core.qa.staffer_source import QaTriageEvidence, triage_source

    await publish_all(publication_url)
    await configure(fleet, limits())
    enabled = [True]
    async with runtime_pool(publication_url, "qa") as qa:
        store = DashboardInbox(qa, enabled=lambda: enabled[0], repo_root=Path.cwd())
        await store.accept(
            DashboardIdentity(uuid4(), uuid4(), "closed-qa-admission"),
            DashboardReport("ValueError", "qa.test:admit", 2, "synthetic input"),
        )
        finding = (await store.claim())[0]
        patrol = await qa.fetchval(
            "INSERT INTO public.qa_patrols(status,log_lookback_minutes) "
            "VALUES('running',15) RETURNING id"
        )
        finding_id = await insert_finding(qa, patrol, finding, None)
        source = triage_source(finding_id)

        class OwnerReader:
            # Explicit stand-in for the unreleased authenticated owner boundary.
            # Only QA's own runtime pool reads its storage, never Switchboard's.
            async def load(self, requested):
                try:
                    return await store.acknowledged(requested)
                except InboxRefused:
                    raise EvidenceRefused("source_evidence_unavailable") from None

        async with runtime_pool(publication_url, "switchboard") as runtime:
            reader = QaTriageEvidence(OwnerReader())
            engine = GoalAdmission(runtime, reader)
            # The default closed boundary and forged source refuse before admission.
            for denied_reader, denied_source in (
                (QaTriageEvidence(), source),
                (reader, SourceRef("staffer", "qa:triage:" + str(uuid4()))),
                (reader, SourceRef("staffer", "owner:" + str(finding_id))),
            ):
                denied = await GoalAdmission(runtime, denied_reader).admit_submission(
                    denied_source, uuid4()
                )
                assert denied.result == "refused"

            definition = tmp_path / "qa_triage_v1.toml"
            definition.write_text('version=1\ncapabilities=["health__lookup"]\n')
            with monkeypatch.context() as patch:
                patch.setattr(staffer_source, "_DEFINITION", definition)
                denied_attempt = uuid4()
                refused = await engine.admit_submission(source, denied_attempt)
                assert refused.result == "refused"
            assert await engine.admit_submission(source, denied_attempt) == refused
            # A complete but expired real fleet publication is not an empty
            # grant set. No goal may be admitted until a fresh publication verifies.
            async with runtime_pool(publication_url, "health") as health:
                await health.execute(
                    "UPDATE public.capability_catalog_current "
                    "SET expires_at=clock_timestamp()-interval '1 second' WHERE domain='health'"
                )
                denied_catalog = await engine.admit_submission(source, uuid4())
                assert denied_catalog.result == "refused"
                _, catalog = await registered_catalog("health")
                await CatalogPublisher(health, "health").publish(catalog)
            # Missing limits refuse without a goal. The same attempt remains refused.
            await fleet.execute(
                "UPDATE switchboard.runtime_config SET goal_admission_config=NULL "
                "WHERE butler_name='switchboard'"
            )
            denied_limits = await engine.admit_submission(source, uuid4())
            assert (
                denied_limits.result == "refused" and denied_limits.reason == "limits_unavailable"
            )
            await configure(fleet, limits())

            # Lose the acknowledgement after the real goal/decision transaction commits.
            class CommitUncertain:
                @asynccontextmanager
                async def acquire(self):
                    async with runtime.acquire() as real:

                        class Connection:
                            def __getattr__(self, name):
                                return getattr(real, name)

                            @asynccontextmanager
                            async def transaction(self):
                                nested = real.is_in_transaction()
                                async with real.transaction():
                                    yield
                                if not nested:
                                    raise OSError("synthetic lost acknowledgement")

                        yield Connection()

            attempt = uuid4()
            before = await fleet.fetchval("SELECT count(*) FROM switchboard.goals")
            with pytest.raises(AdmissionUnavailable):
                await GoalAdmission(CommitUncertain(), reader).admit_submission(source, attempt)
            results = await asyncio.gather(
                engine.admit_submission(source, attempt),
                *(engine.admit_submission(source, uuid4()) for _ in range(4)),
            )
            assert all(result == results[0] for result in results)
            assert results[0].result == "admitted"
            assert await fleet.fetchval("SELECT count(*) FROM switchboard.goals") == before + 1
            goal = await fleet.fetchrow(
                "SELECT origin,exposed,ceiling,contract_digest FROM switchboard.goals WHERE id=$1",
                results[0].goal_id,
            )
            assert goal["origin"] == "staffer" and goal["exposed"] and goal["ceiling"] == []
            async with runtime.acquire() as conn, conn.transaction():
                facts = await reader.load(conn, source)
                assert facts.finding["id"] == finding_id and facts.staffer.capabilities == ()
                assert facts.staffer.digest == goal["contract_digest"]
                # A stale catalog binding cannot derive a new admission plan.
                stale = replace(facts, staffer=replace(facts.staffer, catalog_digest="old"))
                from butlers.core.goal_admission import derive_admission

                with pytest.raises(EvidenceRefused):
                    derive_admission(stale, await PublishedCatalogProvider().load(conn))
            enabled[0] = False
            assert await engine.admit_submission(source, uuid4()) == results[0]
            # A newly acknowledged source may not use disabled QA evidence.
            with pytest.raises(EvidenceRefused):
                async with runtime.acquire() as conn, conn.transaction():
                    await reader.load(conn, source)


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
