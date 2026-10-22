"""Owning-source evidence; PostgreSQL cases require actual migrated roles.

REQ-capability-authorization-013, REQ-goal-orchestration-007.
The pure revision control is not transaction, authentication or PostgreSQL proof.
"""

from dataclasses import replace
from uuid import UUID

import pytest

from butlers.core.goal_admission import Grant
from butlers.core.obligation_sources import (
    DefinitionRevision,
    RuntimeCreationSnapshot,
)
from butlers.core.obligations import DeclarationError, ObligationDeclaration


@pytest.mark.unit
def test_material_revision_binds_creation_and_refuses_widening():
    creator = RuntimeCreationSnapshot(UUID(int=1), (Grant("health__read"),), True)
    revision = DefinitionRevision(
        "health",
        "schedule",
        UUID(int=2),
        1,
        "a" * 64,
        ObligationDeclaration((Grant("health__read"),)),
        "runtime",
        creator,
    )
    assert revision.source.creator.exposed
    assert DefinitionRevision.decode(revision.encode()) == revision
    variants = (
        replace(revision, generation=2),
        replace(revision, material_digest="b" * 64),
        replace(revision, creator=replace(creator, invocation_id=UUID(int=3))),
        replace(revision, creator=replace(creator, exposed=False)),
        replace(revision, state="disabled"),
    )
    assert len({revision.revision, *(v.revision for v in variants)}) == 6
    for kwargs in (
        {"creator": None},
        {"generation": True},
        {"generation": 0},
        {"material_digest": "caller-clock"},
        {"origin": "request"},
        {"declaration": ObligationDeclaration((Grant("health__write"),))},
    ):
        with pytest.raises(DeclarationError):
            replace(revision, **kwargs)


@pytest.fixture(scope="module")
async def owning_sources(postgres_container):
    """Actual core-only migration/bootstrap and production per-schema roles."""
    import asyncio
    from types import SimpleNamespace
    from urllib.parse import urlparse

    import psycopg2

    from butlers.db import Database
    from butlers.migrations import run_migrations
    from butlers.testing.migration import (
        create_migrated_test_db,
        init_db_sql_for_dbapi,
        migration_bootstrap_db_url,
        migration_db_name,
    )

    name = migration_db_name()
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        ["core"],
        {"core": "health"},
    )
    await run_migrations(db_url, chain="core", schema="general")
    parsed = urlparse(db_url)

    # Replay the production bootstrap after migrations, not a hand-written ACL.
    def bootstrap_again():
        bootstrap_url = urlparse(migration_bootstrap_db_url(postgres_container, name))
        with psycopg2.connect(bootstrap_url._replace(scheme="postgresql").geturl()) as conn:
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('butlers.connecting_user', %s, false)", (parsed.username,)
                )
                cursor.execute(init_db_sql_for_dbapi())

    await asyncio.to_thread(bootstrap_again)
    databases = {}
    try:
        for owner in ("health", "general"):
            db = Database(
                db_name=parsed.path.lstrip("/"),
                schema=owner,
                role=f"butler_{owner}_rw",
                host=parsed.hostname,
                port=parsed.port,
                user=parsed.username,
                password=parsed.password,
                min_pool_size=1,
                max_pool_size=4,
                strict_role_enforcement=True,
            )
            await db.connect()
            databases[owner] = db
        yield SimpleNamespace(pool=databases["health"].pool, peer=databases["general"].pool)
    finally:
        for db in databases.values():
            await db.close()


def _definition(source_id):
    return DefinitionRevision(
        "health",
        "schedule",
        source_id,
        1,
        "a" * 64,
        ObligationDeclaration((Grant("health__read"),)),
        "runtime",
        RuntimeCreationSnapshot(UUID(int=100), (Grant("health__read"),), True),
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_role_commit_cas_and_immutable_creation(owning_sources):
    import asyncio
    from uuid import uuid4

    import asyncpg

    from butlers.core.obligation_admission import run_source_transaction
    from butlers.core.obligation_sources import ObligationSourceRepository, SourceConflict

    pool = owning_sources.pool
    value, command = _definition(uuid4()), uuid4()

    async def create(repo):
        return await repo.revise(command, value, expected_revision=None)

    assert await run_source_transaction(pool, "health", create) == value
    # Independent pool acquisition proves committed body, not staged return data.
    async with pool.acquire() as conn, conn.transaction():
        assert await conn.fetchval("SELECT current_user") == "butler_health_rw"
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='switchboard' AND c.relname='goals')"
        )
        # The repository needs no Switchboard chain or cross-schema tables.
        repo = ObligationSourceRepository(conn, "health")
        assert await repo.read(value.source_id) == value
        assert (
            await conn.fetchval(
                "SELECT jsonb_typeof(creator) FROM obligation_definitions WHERE id=$1",
                value.source_id,
            )
            == "object"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM obligation_source_outbox WHERE source_id=$1",
                value.source_id,
            )
            == 1
        )
        for sql, args in (
            (
                "UPDATE obligation_definitions SET creator='{}',generation=generation+1,"
                "revision=$2 WHERE id=$1",
                (value.source_id, "b" * 64),
            ),
            ("DELETE FROM obligation_revisions WHERE source_id=$1", (value.source_id,)),
        ):
            with pytest.raises(asyncpg.RaiseError):
                async with conn.transaction():
                    await conn.execute(sql, *args)
    updated = replace(value, generation=2, material_digest="b" * 64)

    async def revise(repo):
        return await repo.revise(uuid4(), updated, expected_revision=value.revision)

    await run_source_transaction(pool, "health", revise)
    assert await run_source_transaction(pool, "health", create) == value  # lost-reply replay

    async def rebound_command(repo):
        return await repo.revise(command, updated, expected_revision=value.revision)

    with pytest.raises(SourceConflict, match="command_binding_conflict"):
        await run_source_transaction(pool, "health", rebound_command)

    async def stale(repo):
        return await repo.revise(uuid4(), value, expected_revision=None)

    with pytest.raises(SourceConflict, match="source_revision_conflict"):
        await run_source_transaction(pool, "health", stale)

    async def competing_revision(material):
        return await run_source_transaction(
            pool,
            "health",
            lambda repo: repo.revise(
                uuid4(),
                replace(updated, generation=3, material_digest=material),
                expected_revision=updated.revision,
            ),
        )

    outcomes = await asyncio.wait_for(
        asyncio.gather(
            competing_revision("c" * 64),
            competing_revision("d" * 64),
            return_exceptions=True,
        ),
        timeout=20,
    )
    committed = [result for result in outcomes if isinstance(result, DefinitionRevision)]
    assert len(committed) == 1
    assert sum(isinstance(result, SourceConflict) for result in outcomes) == 1
    async with pool.acquire() as conn, conn.transaction():
        assert (
            await ObligationSourceRepository(conn, "health").read(value.source_id) == committed[0]
        )
    async with owning_sources.peer.acquire() as conn, conn.transaction():
        with pytest.raises(SourceConflict, match="owning_role_required"):
            await ObligationSourceRepository(conn, "health").read(value.source_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval("SELECT count(*) FROM health.obligation_definitions")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_occurrence_race_cancel_and_final_claim_fence(owning_sources):
    import asyncio
    from uuid import uuid4

    from butlers.core.obligation_admission import prepare_dark_submission, run_source_transaction
    from butlers.core.obligation_sources import SourceConflict

    pool, value = owning_sources.pool, _definition(uuid4())

    async def create(repo):
        return await repo.revise(uuid4(), value, expected_revision=None)

    await run_source_transaction(pool, "health", create)

    async def capture(repo):
        return await repo.capture(value.source_id, value.revision, slot="persisted-nominal-due")

    left, right = await asyncio.wait_for(
        asyncio.gather(
            run_source_transaction(pool, "health", capture),
            run_source_transaction(pool, "health", capture),
        ),
        timeout=20,
    )
    assert left == right
    first, second = uuid4(), uuid4()

    async def claim(repo, token):
        return await repo.claim(value.source_id, left, token)

    claimed = await asyncio.wait_for(
        asyncio.gather(
            run_source_transaction(pool, "health", lambda r: claim(r, first)),
            run_source_transaction(pool, "health", lambda r: claim(r, second)),
        ),
        timeout=20,
    )
    assert sorted(claimed) == [False, True]
    winner = first if claimed[0] else second
    assert await run_source_transaction(pool, "health", lambda r: claim(r, winner))
    candidate = await run_source_transaction(
        pool,
        "health",
        lambda r: prepare_dark_submission(r, value.source_id, left, winner),
    )
    assert candidate.source == value.source.occurrence("persisted-nominal-due")
    assert candidate.definition.creator.exposed
    await run_source_transaction(
        pool,
        "health",
        lambda r: r.cancel_occurrence(value.source_id, left),
    )
    await run_source_transaction(
        pool,
        "health",
        lambda r: r.cancel_occurrence(value.source_id, left),
    )
    with pytest.raises(SourceConflict, match="source_not_current"):
        await run_source_transaction(
            pool,
            "health",
            lambda r: prepare_dark_submission(r, value.source_id, left, winner),
        )
    # A replay never rearms a cancelled occurrence or creates another outbox event.
    assert await run_source_transaction(pool, "health", capture) == left
    pending, claimed_occurrence = [
        await run_source_transaction(
            pool,
            "health",
            lambda repo: repo.capture(value.source_id, value.revision, slot=slot),
        )
        for slot in ("next-pending-slot", "next-claimed-slot")
    ]
    assert await run_source_transaction(
        pool, "health", lambda repo: repo.claim(value.source_id, claimed_occurrence, first)
    )
    disabled = replace(value, generation=2, state="disabled")
    await run_source_transaction(
        pool,
        "health",
        lambda repo: repo.revise(uuid4(), disabled, expected_revision=value.revision),
    )
    for occurrence in (pending, claimed_occurrence):
        with pytest.raises(SourceConflict, match="source_not_current"):
            await run_source_transaction(
                pool,
                "health",
                lambda repo: prepare_dark_submission(repo, value.source_id, occurrence, first),
            )
    deleted = replace(value, generation=3, state="deleted")
    await run_source_transaction(
        pool,
        "health",
        lambda r: r.revise(uuid4(), deleted, expected_revision=disabled.revision),
    )
    with pytest.raises(SourceConflict, match="source_not_current"):
        await run_source_transaction(pool, "health", capture)
    with pytest.raises(SourceConflict, match="source_revision_conflict"):
        await run_source_transaction(
            pool,
            "health",
            lambda r: r.revise(
                uuid4(),
                replace(value, generation=4),
                expected_revision=deleted.revision,
            ),
        )
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM obligation_occurrences WHERE source_id=$1",
                value.source_id,
            )
            == 3
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM obligation_source_outbox WHERE source_id=$1 "
                "AND kind='occurrence_cancelled'",
                value.source_id,
            )
            == 3
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_real_rollback_and_lost_commit_reply_reconcile_same_command(owning_sources):
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.core.obligation_admission import SourceUnavailable, run_source_transaction
    from butlers.core.obligation_sources import ObligationSourceRepository

    pool, value, command = owning_sources.pool, _definition(uuid4()), uuid4()

    async def write(repo):
        return await repo.revise(command, value, expected_revision=None)

    async def rolled_back(repo):
        await write(repo)
        raise RuntimeError("synthetic-failure-after-write-before-commit")

    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await run_source_transaction(pool, "health", rolled_back)
    async with pool.acquire() as conn, conn.transaction():
        assert await ObligationSourceRepository(conn, "health").read(value.source_id) is None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM obligation_source_outbox WHERE source_id=$1",
                value.source_id,
            )
            == 0
        )

    class LostReply:
        @asynccontextmanager
        async def acquire(self):
            # SQL and commit are REAL; only loss of the release reply is injected.
            async with pool.acquire() as connection:
                yield connection
            raise OSError("synthetic-release-reply-loss")

    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await run_source_transaction(LostReply(), "health", write)
    async with pool.acquire() as conn, conn.transaction():
        assert await ObligationSourceRepository(conn, "health").read(value.source_id) == value
    assert await run_source_transaction(pool, "health", write) == value
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM obligation_source_commands WHERE id=$1",
                command,
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM obligation_source_outbox WHERE source_id=$1",
                value.source_id,
            )
            == 1
        )
