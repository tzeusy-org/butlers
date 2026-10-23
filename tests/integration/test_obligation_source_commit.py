"""Owning-source evidence; PostgreSQL cases require actual migrated roles.

REQ-capability-authorization-013, REQ-goal-orchestration-007.
The pure revision control is not transaction, authentication or PostgreSQL proof.
"""

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from butlers.core.goal_admission import Grant
from butlers.core.obligation_sources import (
    DefinitionRevision,
    RuntimeCreationSnapshot,
)
from butlers.core.obligations import DeclarationError, ObligationDeclaration
from tests.polling import poll_until

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from urllib.parse import urlparse

    import asyncpg
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
    await run_migrations(db_url, chain="core", schema="switchboard")
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

    @asynccontextmanager
    async def administrator():
        # Separate disposable-fixture authority, never a runtime role grant.
        from butlers.db import register_jsonb_codec

        url = urlparse(migration_bootstrap_db_url(postgres_container, name))
        connection = await asyncpg.connect(url._replace(scheme="postgresql").geturl(), timeout=5)
        try:
            await register_jsonb_codec(connection)
            yield connection
        finally:
            await connection.close(timeout=5)

    databases = {}
    try:
        for owner in ("health", "general", "switchboard"):
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
        yield SimpleNamespace(
            pool=databases["health"].pool,
            peer=databases["general"].pool,
            switchboard=databases["switchboard"].pool,
            administrator=administrator,
        )
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
            lambda repo, slot=slot: repo.capture(value.source_id, value.revision, slot=slot),
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
                lambda repo, occurrence=occurrence: prepare_dark_submission(
                    repo, value.source_id, occurrence, first
                ),
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


def _git_schedule_material(**changes):
    """Fixture material only; no production context/config producer is asserted."""
    from butlers.core.obligation_schedule_sources import (
        SCHEDULE_FIELDS,
        compile_git_schedule_definition,
    )

    values = dict.fromkeys(SCHEDULE_FIELDS)
    values.update(
        name="git-fixture",
        cron="0 9 * * *",
        dispatch_mode="prompt",
        prompt="fixture narration",
        complexity="workhorse",
        enabled=True,
        timezone="UTC",
        continuity=False,
        task_type="cron",
    )
    values.update(changes)
    return compile_git_schedule_definition(
        owner_domain="health",
        normalized_schedule=values,
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"fixture_only": "no real producer registration"},
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_schedule_and_source_atomic_readback_replay_and_rollback(owning_sources):
    """Actual owning rows/roles, not the recording-connection software control."""
    from uuid import uuid4

    from butlers.core.obligation_admission import SourceUnavailable, run_source_transaction
    from butlers.core.obligation_schedule_sources import stage_git_schedule_definition
    from butlers.core.obligation_sources import ObligationSourceRepository, SourceConflict

    pool, command = owning_sources.pool, uuid4()
    value = _git_schedule_material()

    async def create(repo):
        return await stage_git_schedule_definition(
            repo,
            command_id=command,
            expected_revision=None,
            definition=value,
        )

    first = await run_source_transaction(pool, "health", create)
    source_id = first.definition.source_id
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id,source,prompt,next_run_at FROM health.scheduled_tasks WHERE id=$1", source_id
        )
        assert row["id"] == source_id and row["source"] == "toml"
        assert row["prompt"] == "fixture narration" and row["next_run_at"] is None
        assert await ObligationSourceRepository(conn, "health").read(source_id) == first.definition
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM health.obligation_source_outbox WHERE source_id=$1", source_id
            )
            == 1
        )
    assert await run_source_transaction(pool, "health", create) == first

    async def update(repo):
        return await stage_git_schedule_definition(
            repo,
            command_id=uuid4(),
            source_id=source_id,
            expected_revision=first.definition.revision,
            definition=_git_schedule_material(prompt="new material"),
        )

    second = await run_source_transaction(pool, "health", update)
    assert await run_source_transaction(pool, "health", create) == first
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT prompt FROM health.scheduled_tasks WHERE id=$1", source_id)
            == "new material"
        )  # late replay must not restore old material

    async def caught_refusal(repo):
        async def refuse(*args, **kwargs):
            raise SourceConflict("fixture-after-scheduled-row-write")

        repo.revise = refuse
        try:
            await stage_git_schedule_definition(
                repo,
                command_id=uuid4(),
                source_id=source_id,
                expected_revision=second.definition.revision,
                definition=_git_schedule_material(prompt="must roll back"),
            )
        except SourceConflict:
            pass  # real savepoint must still restore row before outer commit

    await run_source_transaction(pool, "health", caught_refusal)
    async with pool.acquire() as conn, conn.transaction():
        assert (
            await conn.fetchval("SELECT prompt FROM health.scheduled_tasks WHERE id=$1", source_id)
            == "new material"
        )
        assert await ObligationSourceRepository(conn, "health").read(source_id) == second.definition

    class LostReply:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as connection:
                yield connection
            raise OSError("fixture-release-reply-loss")

    uncertain_command = uuid4()

    async def uncertain_update(repo):
        return await stage_git_schedule_definition(
            repo,
            command_id=uncertain_command,
            source_id=source_id,
            expected_revision=second.definition.revision,
            definition=_git_schedule_material(prompt="commit before reply loss"),
        )

    with pytest.raises(SourceUnavailable):
        await run_source_transaction(LostReply(), "health", uncertain_update)
    recovered = await run_source_transaction(pool, "health", uncertain_update)
    assert recovered.definition.generation == second.definition.generation + 1
    async with pool.acquire() as conn, conn.transaction():
        assert (
            await ObligationSourceRepository(conn, "health").read(source_id) == recovered.definition
        )
        assert (
            await conn.fetchval("SELECT prompt FROM health.scheduled_tasks WHERE id=$1", source_id)
            == "commit before reply loss"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_schedule_revision_race_cancel_and_role_shadow_refusals(owning_sources):
    import asyncio
    from uuid import uuid4

    import asyncpg

    from butlers.core.obligation_admission import run_source_transaction
    from butlers.core.obligation_schedule_sources import stage_git_schedule_definition
    from butlers.core.obligation_sources import ObligationSourceRepository, SourceConflict

    pool = owning_sources.pool
    value = _git_schedule_material(name="race-fixture")
    first = await run_source_transaction(
        pool,
        "health",
        lambda repo: stage_git_schedule_definition(
            repo,
            command_id=uuid4(),
            expected_revision=None,
            definition=value,
        ),
    )
    source_id = first.definition.source_id
    occurrences = [
        await run_source_transaction(
            pool,
            "health",
            lambda r, slot=slot: r.capture(
                source_id,
                first.definition.revision,
                slot=slot,
            ),
        )
        for slot in ("pending-slot", "claimed-slot")
    ]
    assert await run_source_transaction(
        pool,
        "health",
        lambda r: r.claim(
            source_id,
            occurrences[1],
            uuid4(),
        ),
    )

    async def compete(prompt):
        return await run_source_transaction(
            pool,
            "health",
            lambda repo: stage_git_schedule_definition(
                repo,
                command_id=uuid4(),
                expected_revision=first.definition.revision,
                source_id=source_id,
                definition=_git_schedule_material(name="race-fixture", prompt=prompt),
            ),
        )

    results = await asyncio.wait_for(
        asyncio.gather(
            compete("left"),
            compete("right"),
            return_exceptions=True,
        ),
        timeout=20,
    )
    assert sum(isinstance(result, SourceConflict) for result in results) == 1
    winner = next(result.definition for result in results if not isinstance(result, BaseException))
    async with pool.acquire() as conn, conn.transaction():
        assert await ObligationSourceRepository(conn, "health").read(source_id) == winner
        states = await conn.fetch(
            "SELECT state FROM health.obligation_occurrences WHERE id=ANY($1::uuid[])", occurrences
        )
        assert len(states) == 2 and all(row["state"] == "cancelled" for row in states)
        # The runtime must not acquire TEMP authority just to construct this attack.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute("CREATE TEMP TABLE scheduled_tasks (id uuid) ON COMMIT DROP")
    async with owning_sources.administrator() as conn, conn.transaction():
        await conn.execute("CREATE TEMP TABLE scheduled_tasks (id uuid) ON COMMIT DROP")
        await conn.execute("SET LOCAL ROLE butler_health_rw")
        await conn.execute("SET LOCAL search_path TO health,public")
        assert await conn.fetchval("SELECT current_user") == "butler_health_rw"
        with pytest.raises(SourceConflict, match="owning_schedule_relation_required"):
            await stage_git_schedule_definition(
                ObligationSourceRepository(conn, "health"),
                command_id=uuid4(),
                expected_revision=winner.revision,
                source_id=source_id,
                definition=value,
            )
    with pytest.raises(SourceConflict, match="owning_role_required"):
        await run_source_transaction(
            owning_sources.peer,
            "health",
            lambda repo: stage_git_schedule_definition(
                repo,
                command_id=uuid4(),
                expected_revision=None,
                definition=value,
            ),
        )
    tombstone = replace(winner, generation=winner.generation + 1, state="deleted")
    await run_source_transaction(
        pool,
        "health",
        lambda r: r.revise(
            uuid4(),
            tombstone,
            expected_revision=winner.revision,
        ),
    )
    with pytest.raises(SourceConflict, match="source_revision_conflict"):
        await run_source_transaction(
            pool,
            "health",
            lambda repo: stage_git_schedule_definition(
                repo,
                command_id=uuid4(),
                expected_revision=tombstone.revision,
                source_id=source_id,
                definition=value,
            ),
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_runtime_deadline_and_toggle_commit_and_historical_replay(owning_sources):
    """Authored owning-role proof; a software snapshot is not D7 admission."""
    from datetime import date
    from uuid import uuid4

    from butlers.core import scheduler
    from butlers.core.obligation_admission import run_source_transaction
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition
    from butlers.core.obligation_sources import SourceConflict
    from butlers.core.temporal.deadlines_db import deadline_delete, deadline_update

    pool = owning_sources.pool
    creator = RuntimeCreationSnapshot(uuid4(), (Grant("health__read"),), True)

    def runtime(values, **kwargs):
        return compile_runtime_schedule_definition(
            owner_domain="health",
            normalized_schedule=values,
            declaration=ObligationDeclaration((Grant("health__read"),)),
            context_material={"fixture_only": "not an authenticated invocation"},
            creator=creator,
            **kwargs,
        )

    async def source(task):
        return await run_source_transaction(pool, "health", lambda r: r.read(task))

    selected = runtime(
        _git_schedule_material(
            name=f"deadline-{uuid4()}",
            cron="0 0 * * *",
            task_type="deadline",
            target_date=date(2099, 1, 1),
            lead_time_days=7,
            alert_thresholds=[{"days_before": 3, "severity": "info"}],
        ).schedule
    )
    task = await scheduler.schedule_create(
        pool,
        name=selected.schedule["name"],
        task_type="deadline",
        prompt=selected.schedule["prompt"],
        target_date=date(2099, 1, 1),
        lead_time_days=7,
        alert_thresholds=selected.schedule["alert_thresholds"],
        obligation_definition=selected,
        obligation_command_id=uuid4(),
    )
    first = await source(task)
    occurrence = await run_source_transaction(
        pool, "health", lambda r: r.capture(task, first.revision, slot="threshold-slot")
    )
    await pool.execute(
        "UPDATE scheduled_tasks SET fired_thresholds=$2,deadline_status='alerted' WHERE id=$1",
        task,
        [{"days_before": 3, "severity": "info"}],
    )
    updated = runtime(selected.schedule | {"target_date": date(2099, 2, 1)})
    arguments = dict(
        target_date=date(2099, 2, 1),
        obligation_definition=updated,
        obligation_command_id=uuid4(),
        obligation_expected_revision=first.revision,
    )
    await deadline_update(pool, task, **arguments)
    second = await source(task)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT current_user") == "butler_health_rw"
        row = await conn.fetchrow(
            "SELECT target_date,fired_thresholds,deadline_status FROM scheduled_tasks WHERE id=$1",
            task,
        )
        assert dict(row) == {
            "target_date": date(2099, 2, 1),
            "fired_thresholds": [],
            "deadline_status": "pending",
        }
        assert (
            await conn.fetchval("SELECT state FROM obligation_occurrences WHERE id=$1", occurrence)
            == "cancelled"
        )
    assert second.generation == first.generation + 1
    # Historical replay may not clear later actual progress.
    await pool.execute("UPDATE scheduled_tasks SET deadline_status='alerted' WHERE id=$1", task)
    await deadline_update(pool, task, **arguments)
    assert (
        await pool.fetchval("SELECT deadline_status FROM scheduled_tasks WHERE id=$1", task)
        == "alerted"
    )
    with pytest.raises(SourceConflict):
        await deadline_update(pool, task, **(arguments | {"target_date": date(2099, 3, 1)}))
    completed = runtime(updated.schedule | {"enabled": False}, deadline_status="completed")
    await deadline_update(
        pool,
        task,
        deadline_status="completed",
        obligation_definition=completed,
        obligation_command_id=uuid4(),
        obligation_expected_revision=second.revision,
    )
    terminal = await source(task)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled,deadline_status FROM scheduled_tasks WHERE id=$1", task
        )
        assert dict(row) == {"enabled": False, "deadline_status": "completed"}
        assert terminal.state == "disabled"
    await deadline_delete(
        pool,
        task,
        obligation_definition=completed,
        obligation_command_id=uuid4(),
        obligation_expected_revision=terminal.revision,
    )
    assert await pool.fetchval("SELECT count(*) FROM scheduled_tasks WHERE id=$1", task) == 0
    assert (await source(task)).state == "deleted"

    cron = runtime(_git_schedule_material(name=f"toggle-{uuid4()}").schedule)
    task = await scheduler.schedule_create(
        pool,
        name=cron.schedule["name"],
        cron=cron.schedule["cron"],
        prompt=cron.schedule["prompt"],
        obligation_definition=cron,
        obligation_command_id=uuid4(),
    )
    first = await source(task)
    disabled = runtime(cron.schedule | {"enabled": False})
    arguments = dict(
        enabled=False,
        obligation_definition=disabled,
        obligation_command_id=uuid4(),
        obligation_expected_revision=first.revision,
    )
    result = await scheduler.schedule_toggle(pool, task, **arguments)
    assert result["observed_enabled"] is False
    assert (await source(task)).state == "disabled"
    assert await pool.fetchval("SELECT next_run_at FROM scheduled_tasks WHERE id=$1", task) is None
    await pool.execute("UPDATE scheduled_tasks SET enabled=true WHERE id=$1", task)
    replay = await scheduler.schedule_toggle(pool, task, **arguments)
    assert replay["status"] == "reconciled" and "observed_enabled" not in replay
    assert await pool.fetchval("SELECT enabled FROM scheduled_tasks WHERE id=$1", task) is True


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_actual_git_scheduler_cas_capture_commit_and_deadline_boundary(
    owning_sources, tmp_path, monkeypatch
):
    """Real owning SQL/readback; AUTHOR_ONLY until a hosted PG body executes.

    The registry/held policy is real source material, not verified fleet or D7
    creator evidence. Two actual connections contend on the same owning lock.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from butlers.config import load_config
    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.core.obligation_git_sources import (
        GitScheduleRuntime,
        capture_loaded_git_sources,
        start_schedule_sources,
    )
    from butlers.core.obligation_sources import ObligationSourceRepository, SourceConflict
    from butlers.core.runtime_config import RuntimeConfig
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

    name = "cas-" + uuid4().hex
    today = datetime.now(UTC).date()
    (tmp_path / "butler.toml").write_text(
        '[butler]\nname="health"\nport=9100\n'
        '[[butler.schedule]]\nname="' + name + '"\ncron="0 9 * * *"\n'
        'prompt="fixture narration"\n[butler.schedule.obligation]\nversion=1\ncapabilities=[]\n'
        '[[butler.schedule]]\nname="' + name + '-deadline"\ncron="0 9 * * *"\n'
        'prompt="fixture deadline"\ntask_type="deadline"\ntarget_date="' + today.isoformat() + '"\n'
        'lead_time_days=7\nalert_thresholds=[{days_before=0,severity="info"}]\n'
        "[butler.schedule.obligation]\nversion=1\ncapabilities=[]\n"
    )
    pool = owning_sources.pool
    snapshot = capture_loaded_git_sources(
        load_config(tmp_path), RuntimeConfig("health"), await _complete_core_catalog("health")
    )
    runtime = await start_schedule_sources(pool, snapshot, mode="dark")
    nominal = datetime.now(UTC) - timedelta(minutes=1)
    async with pool.acquire() as observer:
        source = await observer.fetchval(
            "SELECT id FROM health.scheduled_tasks WHERE name=$1", name
        )
        await observer.execute(
            "UPDATE health.scheduled_tasks SET next_run_at=$2 WHERE id=$1", source, nominal
        )
        selected = await observer.fetchrow(
            "SELECT * FROM health.scheduled_tasks WHERE id=$1", source
        )

    entered, release, second_acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actor_pids = []
    capture = GitScheduleRuntime._capture

    async def gated_capture(self, repository, definition, slot):
        if definition.source_id == source and not entered.is_set():
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        return await capture(self, repository, definition, slot)

    class ActorPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as connection:
                actor_pids.append(connection.get_server_pid())
                if len(actor_pids) == 2:
                    second_acquired.set()
                yield connection

    monkeypatch.setattr(GitScheduleRuntime, "_capture", gated_capture)
    next_slot = nominal + timedelta(days=1)
    async with owning_sources.administrator() as monitor:
        actors = []
        try:
            actors.append(
                asyncio.create_task(
                    runtime.claim_cron(ActorPool(), selected, next_run_at=next_slot, disable=False)
                )
            )
            await asyncio.wait_for(entered.wait(), 10)
            actors.append(
                asyncio.create_task(
                    runtime.claim_cron(ActorPool(), selected, next_run_at=next_slot, disable=False)
                )
            )
            await asyncio.wait_for(second_acquired.wait(), 10)
            assert actor_pids[0] != actor_pids[1]
            assert len(actor_pids) == 2

            async def second_actor_blocked_on_first():
                return await monitor.fetchval(
                    "SELECT wait_event_type='Lock' AND query LIKE '%pg_advisory_xact_lock%' "
                    "AND $2=ANY(pg_blocking_pids(pid)) "
                    "FROM pg_stat_activity WHERE pid=$1 AND datname=current_database()",
                    actor_pids[1],
                    actor_pids[0],
                )

            async with asyncio.timeout(10):
                await poll_until(second_actor_blocked_on_first, interval_s=0.01)
            release.set()
            first, second = await asyncio.wait_for(asyncio.gather(*actors), 10)
            assert first.submission is not None and second is None
            assert first.submission.obligation.created_exposed is False
            assert first.submission.obligation.digest == first.submission.definition.revision
        finally:
            release.set()
            for actor in actors:
                if not actor.done():
                    actor.cancel()
            await asyncio.wait_for(asyncio.gather(*actors, return_exceptions=True), 10)
            monkeypatch.setattr(GitScheduleRuntime, "_capture", capture)
    async with pool.acquire() as observer:
        actual = await observer.fetchrow(
            "SELECT source_key,claim_token FROM obligation_occurrences WHERE id=$1",
            first.submission.occurrence_id,
        )
        assert actual["source_key"] == first.submission.source.key
        assert actual["claim_token"] == first.submission.claim_token
        assert (
            await observer.fetchval(
                "SELECT next_run_at FROM health.scheduled_tasks WHERE id=$1", source
            )
            == next_slot
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_occurrences WHERE source_id=$1 AND state='claimed'",
                source,
            )
            == 1
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_source_outbox WHERE source_id=$1 "
                "AND kind='occurrence_captured'",
                source,
            )
            == 1
        )

    # An actual SQL failure after the new outbox write rolls back the legacy
    # next_run CAS, occurrence, claim and outbox together. No product ACL changes.
    async with pool.acquire() as observer:
        selected = await observer.fetchrow(
            "SELECT * FROM health.scheduled_tasks WHERE id=$1", source
        )
    outbox = ObligationSourceRepository._outbox

    async def fail_after_outbox(self, source_id, revision, kind, reference):
        await outbox(self, source_id, revision, kind, reference)
        if source_id == source and kind == "occurrence_captured":
            await self.connection.execute("SELECT 1/0")

    monkeypatch.setattr(ObligationSourceRepository, "_outbox", fail_after_outbox)
    try:
        with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
            await runtime.claim_cron(
                pool, selected, next_run_at=next_slot + timedelta(days=1), disable=False
            )
    finally:
        monkeypatch.setattr(ObligationSourceRepository, "_outbox", outbox)
    async with pool.acquire() as observer:
        assert (
            await observer.fetchval(
                "SELECT next_run_at FROM health.scheduled_tasks WHERE id=$1", source
            )
            == next_slot
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_occurrences WHERE source_id=$1", source
            )
            == 1
        )
    with pytest.raises(SourceConflict, match="owning_role_required"):
        await runtime.claim_cron(
            owning_sources.peer, selected, next_run_at=next_slot + timedelta(days=1), disable=False
        )

    # Deadline success is already observed by its caller. This transaction
    # records the exact persisted target/threshold and staged identity together.
    async with pool.acquire() as observer:
        deadline = await observer.fetchrow(
            "SELECT * FROM health.scheduled_tasks WHERE name=$1", name + "-deadline"
        )
    threshold = deadline["alert_thresholds"][0]
    submission = await runtime.record_deadline(
        pool,
        deadline,
        threshold=threshold,
        status="alerted",
        fired=[threshold],
        run_at=datetime.now(UTC),
    )
    assert submission is not None
    assert await runtime.read_committed_submission(pool, submission) == submission
    async with pool.acquire() as observer:
        assert await observer.fetchval(
            "SELECT fired_thresholds FROM health.scheduled_tasks WHERE id=$1", deadline["id"]
        ) == [threshold]
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_occurrences WHERE source_id=$1 AND state='claimed'",
                deadline["id"],
            )
            == 1
        )
    # Fresh readback cannot promote an earlier successful claim after a real
    # owning cancellation committed. This is SQL proof only when executed.
    async with pool.acquire() as actor, actor.transaction():
        await ObligationSourceRepository(actor, "health").cancel_occurrence(
            deadline["id"], submission.occurrence_id
        )
    with pytest.raises(SourceConflict, match="source_not_current"):
        await runtime.read_committed_submission(pool, submission)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_actual_insight_model_job_commits_source_and_closed_payload(owning_sources):
    """Authored owning-role PG/readback, NOTRUN until an actual hosted carrier."""
    from datetime import UTC, datetime, timedelta

    from butlers.config import load_config
    from butlers.core.obligation_git_sources import (
        capture_loaded_git_sources,
        start_schedule_sources,
    )
    from butlers.core.runtime_config import RuntimeConfig
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

    pool = owning_sources.switchboard
    config = load_config(_REPO_ROOT / "roster" / "switchboard")
    snapshot = capture_loaded_git_sources(
        config, RuntimeConfig("switchboard"), await _complete_core_catalog("switchboard")
    )
    runtime = await start_schedule_sources(pool, snapshot, mode="dark")
    async with pool.acquire() as observer:
        assert await observer.fetchval("SELECT current_user") == "butler_switchboard_rw"
        row = await observer.fetchrow(
            "SELECT * FROM switchboard.scheduled_tasks WHERE name='insight-delivery-cycle'"
        )
        nominal = datetime.now(UTC) - timedelta(minutes=1)
        await observer.execute(
            "UPDATE switchboard.scheduled_tasks SET next_run_at=$2 WHERE id=$1", row["id"], nominal
        )
        selected = await observer.fetchrow(
            "SELECT * FROM switchboard.scheduled_tasks WHERE id=$1", row["id"]
        )
    claim = await runtime.claim_cron(
        pool, selected, next_run_at=nominal + timedelta(days=1), disable=False
    )
    payload = claim.submission
    assert payload.definition.family == "model_job" and payload.obligation.created_exposed is True
    assert {g.capability for g in payload.obligation.capabilities} == {"switchboard__notify"}
    assert await runtime.read_committed_submission(pool, payload) == payload
    assert (
        await runtime.claim_cron(
            pool, selected, next_run_at=nominal + timedelta(days=1), disable=False
        )
        is None
    )
    async with pool.acquire() as observer:
        occurrence = await observer.fetchrow(
            "SELECT source_key,claim_token,state FROM switchboard.obligation_occurrences WHERE id=$1",
            payload.occurrence_id,
        )
        assert dict(occurrence) == {
            "source_key": payload.source.key,
            "claim_token": payload.claim_token,
            "state": "claimed",
        }
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM switchboard.obligation_source_outbox "
                "WHERE source_id=$1 AND kind='occurrence_captured'",
                payload.definition.source_id,
            )
            == 1
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_creator_acl_converges_after_bootstrap_round_trip(postgres_container):
    """Real ordinary-login metadata and ACLs survive a different DDL creator."""
    import asyncio
    from urllib.parse import urlparse

    import asyncpg

    from alembic import command
    from butlers.migrations import _build_alembic_config, run_migrations
    from butlers.testing.migration import (
        create_migration_db,
        migration_bootstrap_db_url,
        migration_db_name,
    )

    name = migration_db_name()
    ordinary_url = await asyncio.to_thread(create_migration_db, postgres_container, name)
    await run_migrations(ordinary_url, chain="core")
    tables = [
        "obligation_definitions",
        "obligation_revisions",
        "obligation_source_commands",
        "obligation_occurrences",
        "obligation_source_outbox",
    ]
    functions = [
        "obligation_evidence_immutable",
        "obligation_definition_guard",
        "obligation_occurrence_guard",
    ]

    async def ordinary_metadata():
        connection = await asyncpg.connect(ordinary_url, timeout=5)
        try:
            assert (
                await connection.fetchval("SELECT current_user") == urlparse(ordinary_url).username
            )
            anchor = await connection.fetchval(
                "SELECT relowner FROM pg_class WHERE oid='public.scheduled_tasks'::regclass"
            )
            owners = await connection.fetch(
                "SELECT relname,relowner FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='public' AND relname=ANY($1::text[])",
                tables,
            )
            assert len(owners) == 5 and all(row["relowner"] == anchor for row in owners)
            function_owners = await connection.fetch(
                "SELECT proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND proname=ANY($1::text[])",
                functions,
            )
            assert len(function_owners) == 3 and all(
                row["proowner"] == anchor for row in function_owners
            )
            # Separate effective permission checks: a comma-list would mean ANY.
            for role in ("butler_health_rw", "butler_general_rw", "butler_switchboard_rw"):
                for table in tables:
                    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        assert await connection.fetchval(
                            "SELECT has_table_privilege($1,$2,$3)",
                            role,
                            "public." + table,
                            privilege,
                        )
            columns = await connection.fetch(
                "SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=ANY($1::text[]) "
                "ORDER BY table_name,ordinal_position",
                tables,
            )
            assert {row["table_name"] for row in columns} == set(tables)
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            return [tuple(row) for row in columns]
        finally:
            await connection.close(timeout=5)

    before = await ordinary_metadata()
    bootstrap = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, name), chains=["core"]
    )

    def round_trip():
        command.downgrade(bootstrap, "core@-1")
        command.upgrade(bootstrap, "core@head")

    await asyncio.to_thread(round_trip)
    # A NEW ordinary connection observes the committed privileged re-creation.
    assert await ordinary_metadata() == before


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_stamped_schema_without_baseline_keeps_local_owner_and_acl(
    postgres_container,
):
    """A shared-predecessor stamp is not a local scheduled_tasks relation.

    Author-only until actual PostgreSQL execution: preserve the ordinary
    migration identity, independent committed readback and individual ACLs.
    """
    import asyncio
    from urllib.parse import urlparse

    import asyncpg

    from alembic import command
    from butlers.migrations import _build_alembic_config, run_migrations
    from butlers.testing.migration import create_migration_db, migration_db_name

    ordinary_url = await asyncio.to_thread(
        create_migration_db, postgres_container, migration_db_name()
    )
    await run_migrations(ordinary_url, chain="core")
    # This is the real existing shared-schema replay shape, not a copied table
    # stand-in or a replay of foundation under a different actor.
    config = _build_alembic_config(ordinary_url, ["core"], target_schema="health")
    await asyncio.to_thread(command.stamp, config, "core_254")
    before = await asyncpg.connect(ordinary_url, timeout=5)
    try:
        assert await before.fetchval("SELECT to_regclass('health.scheduled_tasks')") is None
        assert await before.fetchval("SELECT version_num FROM health.alembic_version") == "core_254"
    finally:
        await before.close(timeout=5)

    await run_migrations(ordinary_url, chain="core", schema="health")
    connection = await asyncpg.connect(ordinary_url, timeout=5)
    try:
        actor = await connection.fetchval("SELECT current_user")
        assert actor == urlparse(ordinary_url).username
        assert not await connection.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert await connection.fetchval("SELECT to_regclass('health.scheduled_tasks')") is None
        tables = [
            "obligation_definitions",
            "obligation_revisions",
            "obligation_source_commands",
            "obligation_occurrences",
            "obligation_source_outbox",
        ]
        owners = await connection.fetch(
            "SELECT relname,pg_get_userbyid(relowner) AS owner FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='health' AND c.relname=ANY($1::text[])",
            tables,
        )
        assert len(owners) == 5 and all(row["owner"] == actor for row in owners)
        functions = await connection.fetch(
            "SELECT pg_get_userbyid(proowner) AS owner FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='health' AND proname=ANY($1::text[])",
            [
                "obligation_evidence_immutable",
                "obligation_definition_guard",
                "obligation_occurrence_guard",
            ],
        )
        assert len(functions) == 3 and all(row["owner"] == actor for row in functions)
        visible = await connection.fetch(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema='health' AND table_name=ANY($1::text[])",
            tables,
        )
        assert {row["table_name"] for row in visible} == set(tables)
        for table in tables:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRIGGER", "REFERENCES"):
                assert await connection.fetchval(
                    "SELECT has_table_privilege('butler_health_rw',$1,$2)",
                    "health." + table,
                    privilege,
                )
                assert not await connection.fetchval(
                    "SELECT has_table_privilege('butler_general_rw',$1,$2)",
                    "health." + table,
                    privilege,
                )
        assert (
            await connection.fetchval("SELECT version_num FROM health.alembic_version")
            == "core_274"
        )
    finally:
        await connection.close(timeout=5)
