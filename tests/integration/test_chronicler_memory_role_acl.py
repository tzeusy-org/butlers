"""Real migrated PG controls for bounded Chronicler private-memory provisioning.

REQ-butler-chronicler-011; REQ-database-security-011 (managed bootstrap).
No runtime/shared-user or fixture-only GRANT substitutes for mem_015. These
bodies require genuine PostgreSQL; collection alone proves no ACL outcome.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.modules.memory.storage import run_decay_sweep
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _before_private_grant(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "chronicler", "memory"],
        {"core": "chronicler", "chronicler": "chronicler", "memory": "chronicler_mem"},
        revisions={"memory": "mem_014"},
    )


@pytest.fixture
def before_private_grant(postgres_container):
    # Synchronous migrated dependency resolves BEFORE any async test body.
    return _before_private_grant(postgres_container)


@pytest.fixture(scope="module")
def private_grant_database(postgres_container):
    url = _before_private_grant(postgres_container)
    asyncio.run(run_migrations(url, chain="memory", schema="chronicler_mem"))
    return url


@asynccontextmanager
async def _runtime_pool(url, role="butler_chronicler_rw", schema="chronicler_mem"):
    # Fixed test identities only; no request-derived role or search path.
    assert role in {"butler_chronicler_rw", "butler_general_rw"}
    assert schema in {"chronicler_mem", "chronicler"}

    async def setup(connection):
        await connection.execute(f'SET ROLE "{role}"')
        await connection.execute(f'SET search_path TO "{schema}", public')
        assert await connection.fetchval("SELECT current_user") == role

    pool = await asyncpg.create_pool(
        url, min_size=1, max_size=2, init=register_jsonb_codec, setup=setup
    )
    try:
        yield pool
    finally:
        await pool.close()


async def _catalog(url):
    connection = await asyncpg.connect(url)
    try:
        # No data/payload values: ownership, ACLs, trigger origin and function
        # security/search-path settings only, including protected public code.
        return {
            "schemas": await connection.fetch(
                "SELECT nspname,nspowner,nspacl::text FROM pg_namespace ORDER BY nspname"
            ),
            "relations": await connection.fetch(
                "SELECT n.nspname,c.relname,c.relowner,c.relacl::text FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname IN ('chronicler_mem','chronicler','public') "
                "ORDER BY n.nspname,c.relname"
            ),
            "functions": await connection.fetch(
                "SELECT n.nspname,p.oid,p.proowner,p.proacl::text,p.prosecdef,p.proconfig "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname IN ('chronicler_mem','public') ORDER BY p.oid"
            ),
            "triggers": await connection.fetch(
                "SELECT t.tgrelid,t.tgname,t.tgfoid,t.tgtype FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='chronicler_mem' ORDER BY t.tgrelid,t.tgname"
            ),
        }
    finally:
        await connection.close()


def _revision_on_connection(connection, direction="upgrade", *, target_schema="chronicler_mem"):
    module = importlib.import_module(
        "butlers.modules.memory.migrations.015_grant_chronicler_private_memory"
    )
    config = _build_alembic_config(
        connection.engine.url.render_as_string(hide_password=False),
        ["memory"],
        target_schema=target_schema,
    )
    # The production runner supplies this exact selected-target option through
    # Alembic's environment, independently of the effective search_path.
    with EnvironmentContext(config, None) as environment:
        context = MigrationContext.configure(connection, environment_context=environment)
        with Operations.context(context):
            getattr(module, direction)()


def _reapply(url, schema="chronicler_mem"):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            assert schema in {"chronicler_mem", "general"}
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            _revision_on_connection(connection, target_schema=schema)
    finally:
        engine.dispose()


async def test_actual_revision_omission_then_restore_private_crud_maintenance_and_provenance(
    before_private_grant,
):
    url = before_private_grant
    baseline = await _catalog(url)
    async with _runtime_pool(url) as pool:
        assert not await pool.fetchval("SELECT has_schema_privilege('chronicler_mem','USAGE')")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await pool.fetch("SELECT id FROM chronicler_mem.episodes")
    # Actual normal-login Alembic execution, not a fixture GRANT or SQL mirror.
    await run_migrations(url, chain="memory", schema="chronicler_mem")
    after = await _catalog(url)
    assert after["functions"] == baseline["functions"]
    assert after["triggers"] == baseline["triggers"]
    assert [r for r in after["relations"] if r["nspname"] != "chronicler_mem"] == [
        r for r in baseline["relations"] if r["nspname"] != "chronicler_mem"
    ]
    assert [r for r in after["schemas"] if r["nspname"] != "chronicler_mem"] == [
        r for r in baseline["schemas"] if r["nspname"] != "chronicler_mem"
    ]
    episode, fact, rule = (uuid.uuid4() for _ in range(3))
    async with _runtime_pool(url) as pool:
        async with pool.acquire() as connection, connection.transaction():
            assert await connection.fetchval("SELECT current_user") == "butler_chronicler_rw"
            await connection.execute(
                "INSERT INTO episodes(id,butler,content,content_authority) "
                "VALUES($1,'chronicler','synthetic private episode','system')",
                episode,
            )
            await connection.execute(
                "INSERT INTO facts(id,subject,predicate,content,source_episode_id,decay_rate) "
                "VALUES($1,'synthetic subject','is','synthetic fact',$2,0)",
                fact,
                episode,
            )
            await connection.execute(
                "INSERT INTO rules(id,content,source_episode_id,decay_rate) "
                "VALUES($1,'synthetic rule',$2,0)",
                rule,
                episode,
            )
            await connection.execute("UPDATE facts SET importance=6 WHERE id=$1", fact)
            await connection.execute("UPDATE rules SET confidence=0.75 WHERE id=$1", rule)
            await connection.execute("UPDATE episodes SET importance=6 WHERE id=$1", episode)
        # Different acquisition observes committed real runtime-role results.
        assert await pool.fetchval("SELECT importance FROM facts WHERE id=$1", fact) == 6
        assert await pool.fetchval("SELECT confidence FROM rules WHERE id=$1", rule) == 0.75
        assert isinstance(await run_decay_sweep(pool), dict)
        # Invoke the production trigger from the domain path: its owning private
        # tombstone must be written even though current_schema is chronicler.
        async with pool.acquire() as connection:
            await connection.execute('SET search_path TO "chronicler", public')
            await connection.execute("DELETE FROM chronicler_mem.episodes WHERE id=$1", episode)
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM episode_tombstones WHERE episode_id=$1", episode
            )
            == 1
        )
        assert (
            await pool.fetchval("SELECT source_episode_id FROM facts WHERE id=$1", fact) == episode
        )
        await pool.execute("DELETE FROM facts WHERE id=$1", fact)
        await pool.execute("DELETE FROM rules WHERE id=$1", rule)
        assert await pool.fetchval("SELECT count(*) FROM facts WHERE id=$1", fact) == 0


async def test_fixed_surface_foreign_denial_missing_acl_and_idempotence(private_grant_database):
    url = private_grant_database
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE chronicler_mem.synthetic_unrelated(id UUID)"))
        before = await _catalog(url)
        await asyncio.to_thread(_reapply, url)
        assert await _catalog(url) == before
        async with _runtime_pool(url) as pool:
            assert not await pool.fetchval("SELECT has_schema_privilege('chronicler_mem','CREATE')")
            for relation in ["synthetic_unrelated", "alembic_version"]:
                assert not await pool.fetchval(
                    "SELECT has_table_privilege($1,'SELECT')", f"chronicler_mem.{relation}"
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await pool.fetch(f'SELECT * FROM chronicler_mem."{relation}"')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.execute("DELETE FROM memory_policies")
        async with _runtime_pool(url, "butler_general_rw") as pool:
            assert not await pool.fetchval("SELECT has_schema_privilege('chronicler_mem','USAGE')")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.fetch("SELECT id FROM chronicler_mem.episodes")
        # Real negative ACL fault; only production mem_015 repairs it.
        with engine.begin() as connection:
            connection.execute(
                text("REVOKE SELECT ON chronicler_mem.facts FROM butler_chronicler_rw")
            )
        async with _runtime_pool(url) as pool:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.fetch("SELECT id FROM facts")
        await asyncio.to_thread(_reapply, url)
        async with _runtime_pool(url) as pool:
            assert isinstance(await pool.fetch("SELECT id FROM facts"), list)
        # Objects created later gain no implicit/default privilege.
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE chronicler_mem.synthetic_future(id UUID)"))
        async with _runtime_pool(url) as pool:
            assert not await pool.fetchval(
                "SELECT has_table_privilege('chronicler_mem.synthetic_future','SELECT')"
            )
    finally:
        engine.dispose()


async def test_wrong_target_is_noop_and_untrusted_objects_or_missing_role_refuse(
    before_private_grant,
    postgres_container,
):
    url = before_private_grant
    before = await _catalog(url)
    await asyncio.to_thread(_reapply, url, "general")
    assert await _catalog(url) == before
    name = unquote(urlparse(url).username)
    admin_url = migration_bootstrap_db_url(postgres_container, unquote(urlparse(url).path[1:]))

    def faults():
        engine = create_engine(admin_url)
        try:
            for sql in [
                "ALTER SCHEMA chronicler_mem OWNER TO butler_general_rw",
                "ALTER TABLE chronicler_mem.facts OWNER TO butler_general_rw",
                "ALTER ROLE butler_chronicler_rw RENAME TO synthetic_absent_chronicler_role",
                "ALTER ROLE butler_chronicler_rw CREATEDB",
            ]:
                # Faults are transaction-local, including the cluster role name;
                # rollback restores it before any subsequent case/connection.
                with pytest.raises(DBAPIError) as error, engine.begin() as connection:
                    connection.execute(text(sql))
                    quoted = name.replace('"', '""')
                    connection.execute(text(f'SET LOCAL ROLE "{quoted}"'))
                    connection.execute(text("SET LOCAL search_path TO chronicler_mem,public"))
                    _revision_on_connection(connection)
                assert getattr(error.value.orig, "pgcode", None) == "55000"
            with pytest.raises(DBAPIError) as error, engine.begin() as connection:
                quoted = name.replace('"', '""')
                connection.execute(text(f'SET LOCAL ROLE "{quoted}"'))
                connection.execute(text("SET LOCAL search_path TO pg_temp"))
                _revision_on_connection(connection)
            assert getattr(error.value.orig, "pgcode", None) == "55000"
            # Effective fallback never satisfies an explicitly selected private target.
            with pytest.raises(DBAPIError) as error, engine.begin() as connection:
                quoted = name.replace('"', '""')
                connection.execute(text(f'SET LOCAL ROLE "{quoted}"'))
                connection.execute(text("SET LOCAL search_path TO general,public"))
                _revision_on_connection(connection, target_schema="chronicler_mem")
            assert getattr(error.value.orig, "pgcode", None) == "55000"
            # The production runner also supports default-public/no target.
            # It must emit no private grant even when search_path is private.
            # USAGE is actually absent here, so an accidental grant cannot hide
            # behind idempotent catalog equality on an already-granted fixture.
            for selected_target in (None, "public"):
                with engine.begin() as connection:
                    quoted = name.replace('"', '""')
                    connection.execute(text(f'SET LOCAL ROLE "{quoted}"'))
                    connection.execute(text("SET LOCAL search_path TO chronicler_mem,public"))
                    assert (
                        connection.execute(
                            text(
                                "SELECT has_schema_privilege('butler_chronicler_rw', "
                                "'chronicler_mem', 'USAGE')"
                            )
                        ).scalar_one()
                        is False
                    )
                    _revision_on_connection(connection, target_schema=selected_target)
                    assert (
                        connection.execute(
                            text(
                                "SELECT has_schema_privilege('butler_chronicler_rw', "
                                "'chronicler_mem', 'USAGE')"
                            )
                        ).scalar_one()
                        is False
                    )
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL search_path TO general,public"))
                _revision_on_connection(connection, "downgrade", target_schema="general")
            # An explicitly different legitimate target never grants private
            # access, even if search_path happens to name the private schema.
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL search_path TO chronicler_mem,public"))
                _revision_on_connection(connection, target_schema="general")
        finally:
            engine.dispose()

    await asyncio.to_thread(faults)
    assert await _catalog(url) == before
    async with _runtime_pool(url) as pool:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await pool.fetch("SELECT id FROM chronicler_mem.episodes")
    # First establish the legitimate private grants through the normal
    # production revision, then prove a runtime role cannot act as its owner.
    await run_migrations(url, chain="memory", schema="chronicler_mem")
    before = await _catalog(url)
    # An actual runtime role cannot supply migration ownership/grant authority.
    engine = create_engine(url)
    try:
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
            connection.execute(text("SET LOCAL search_path TO chronicler_mem,public"))
            _revision_on_connection(connection)
    finally:
        engine.dispose()
    assert await _catalog(url) == before


async def test_downgrade_refuses_lossy_revoke_and_preserves_preexisting_grants(
    before_private_grant,
):
    url = before_private_grant
    engine = create_engine(url)
    retained_fact = uuid.uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO chronicler_mem.facts(id,subject,predicate,content) "
                    "VALUES(:id,'synthetic rollback subject','is','retained private data')"
                ),
                {"id": retained_fact},
            )
            # Deliberately preexisting identical/unrelated ACLs are not a
            # positive private-access substitute (there is still no USAGE).
            connection.execute(
                text("GRANT SELECT, REFERENCES ON chronicler_mem.facts TO butler_chronicler_rw")
            )
        await run_migrations(url, chain="memory", schema="chronicler_mem")
        before = await _catalog(url)
        config = _build_alembic_config(url, ["memory"], target_schema="chronicler_mem")
        with pytest.raises(DBAPIError) as error:
            await asyncio.to_thread(command.downgrade, config, "mem_014")
        assert getattr(error.value.orig, "pgcode", None) == "55000"
        assert await _catalog(url) == before
        async with _runtime_pool(url) as pool:
            assert await pool.fetchval("SELECT has_table_privilege('facts','SELECT, REFERENCES')")
            assert (
                await pool.fetchval("SELECT content FROM facts WHERE id=$1", retained_fact)
                == "retained private data"
            )
        # No version advance pretending rollback, and no data/ACL loss.
        connection = await asyncpg.connect(url)
        try:
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM chronicler_mem.alembic_version WHERE version_num='mem_015'"
                )
                == 1
            )
        finally:
            await connection.close()
    finally:
        engine.dispose()


async def test_actual_auxiliary_writes_keep_append_only_and_policy_bounds(private_grant_database):
    """Exercise the fixed non-CRUD relations through the real runtime role."""
    url = private_grant_database
    gap, conversation, origin, event, source, target = (uuid.uuid4() for _ in range(6))
    predicate = "synthetic_" + uuid.uuid4().hex
    async with _runtime_pool(url) as pool:
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "INSERT INTO memory_links(source_type,source_id,target_type,target_id,relation) "
                "VALUES('episode',$1,'fact',$2,'derived_from')",
                source,
                target,
            )
            await connection.execute(
                "INSERT INTO memory_events(id,event_type,actor) VALUES($1,'synthetic','chronicler')",
                event,
            )
            await connection.execute(
                "INSERT INTO predicate_registry(name,status) VALUES($1,'proposed')", predicate
            )
            await connection.execute(
                "UPDATE predicate_registry SET usage_count=1 WHERE name=$1", predicate
            )
            await connection.execute(
                "INSERT INTO knowledge_gaps(id,entity_id,predicate,question_summary) "
                "VALUES($1,$2,$3,'synthetic question')",
                gap,
                source,
                predicate,
            )
            await connection.execute(
                "INSERT INTO knowledge_gap_origins(id,gap_id,conversation_id,channel) "
                "VALUES($1,$2,$3,'telegram')",
                origin,
                gap,
                conversation,
            )
            await connection.execute(
                "UPDATE knowledge_gaps SET status='answerable' WHERE id=$1", gap
            )
            await connection.execute(
                "UPDATE knowledge_gap_origins SET delivered_at=now() WHERE id=$1", origin
            )
        # Independent post-commit acquisitions under SET ROLE witness both
        # writes and the existing predicate search-vector trigger.
        assert (
            await pool.fetchval(
                "SELECT usage_count FROM predicate_registry WHERE name=$1", predicate
            )
            == 1
        )
        assert await pool.fetchval(
            "SELECT search_vector IS NOT NULL FROM predicate_registry WHERE name=$1", predicate
        )
        assert (
            await pool.fetchval("SELECT status FROM knowledge_gaps WHERE id=$1", gap)
            == "answerable"
        )
        assert await pool.fetchval(
            "SELECT delivered_at IS NOT NULL FROM knowledge_gap_origins WHERE id=$1", origin
        )
        assert await pool.fetchval("SELECT count(*) FROM memory_events WHERE id=$1", event) == 1
        assert (
            await pool.fetchval("SELECT count(*) FROM memory_links WHERE source_id=$1", source) == 1
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await pool.execute("DELETE FROM memory_events WHERE id=$1", event)


@pytest.mark.parametrize(
    ("operation", "fault"),
    [
        ("forget_fact", None),
        ("correct_fact", None),
        ("forget_rule", None),
        ("correct_rule", None),
        ("retire_rule", None),
        ("correct_episode", None),
        ("forget_fact", "catalog"),
        ("correct_fact", "graph"),
        ("correct_rule", "graph"),
        ("retire_rule", "graph"),
    ],
)
async def test_domain_pool_explicit_private_schema_disowns_exact_provenance_atomically(
    private_grant_database, operation, fault
):
    """F2: qualified canonical writes, audit and projections share one owner.

    Duplicate UUID/domain projections are positive controls for the historical
    current_schema() bug. Actual PostgreSQL trigger errors prove rollback after
    canonical writes (and after the correction event), without mocked cascades.
    """
    from butlers.modules.memory.storage import forget_memory, retire_rule

    url = private_grant_database
    memory_id, entity_id = uuid.uuid4(), uuid.uuid4()
    memory_type = (
        "rule"
        if operation in {"forget_rule", "correct_rule", "retire_rule"}
        else ("episode" if operation == "correct_episode" else "fact")
    )
    table = {"fact": "facts", "rule": "rules", "episode": "episodes"}[memory_type]
    control = await asyncpg.connect(url)
    await register_jsonb_codec(control)
    trigger = f"f2_{memory_id.hex}"
    try:
        # Fixed lookalike fixture tables, not replacement production schemas.
        if memory_type != "episode":
            await control.execute(
                f'CREATE TABLE IF NOT EXISTS chronicler."{table}" '
                f'(LIKE chronicler_mem."{table}" INCLUDING DEFAULTS)'
            )
        await control.execute(
            "CREATE TABLE IF NOT EXISTS chronicler.memory_events "
            "(LIKE chronicler_mem.memory_events INCLUDING DEFAULTS)"
        )
        await control.execute(
            "INSERT INTO public.entities(id,canonical_name,entity_type) "
            "VALUES($1,'F2 synthetic owner','person')",
            entity_id,
        )
        if memory_type == "fact":
            values = "(id,subject,predicate,content) VALUES($1,$2,'is','F2 synthetic')"
            args = (memory_id, f"f2-{memory_id}")
        elif memory_type == "rule":
            values = "(id,content) VALUES($1,'F2 synthetic rule')"
            args = (memory_id,)
        else:
            values = "(id,butler,content) VALUES($1,'chronicler','F2 synthetic episode')"
            args = (memory_id,)
        await control.execute(f'INSERT INTO chronicler_mem."{table}" {values}', *args)
        if memory_type != "episode":
            await control.execute(f'INSERT INTO chronicler."{table}" {values}', *args)
            for schema in ("chronicler", "chronicler_mem"):
                await control.execute(
                    "INSERT INTO public.memory_catalog(source_schema,source_table,source_id,"
                    "summary,confidence) VALUES($1,$2,$3,'F2 synthetic catalog',0.75)",
                    schema,
                    table,
                    memory_id,
                )
                await control.execute(
                    "INSERT INTO public.entity_graph_edges(source_schema,source_table,source_id,"
                    "subject_entity_id,predicate,object_entity_id) VALUES($1,$2,$3,$4,'knows',$4)",
                    schema,
                    table,
                    memory_id,
                    entity_id,
                )
        await control.execute(
            "INSERT INTO chronicler.memory_events(event_type,memory_type,memory_id,payload) "
            "VALUES('F2_domain_control',$1,$2,'{}'::jsonb)",
            memory_type,
            memory_id,
        )
        domain_events_before = await control.fetch(
            "SELECT * FROM chronicler.memory_events WHERE memory_id=$1", memory_id
        )
        assert len(domain_events_before) == 1
        baseline = await control.fetchrow(
            f'SELECT * FROM chronicler_mem."{table}" WHERE id=$1', memory_id
        )
        domain_baseline = (
            None
            if memory_type == "episode"
            else await control.fetchrow(
                f'SELECT * FROM chronicler."{table}" WHERE id=$1', memory_id
            )
        )
        catalog_before = await control.fetch(
            "SELECT * FROM public.memory_catalog WHERE source_id=$1 ORDER BY source_schema",
            memory_id,
        )
        graph_before = await control.fetch(
            "SELECT * FROM public.entity_graph_edges WHERE source_id=$1 ORDER BY source_schema",
            memory_id,
        )
        if fault:
            relation, event = (
                ("memory_catalog", "UPDATE")
                if fault == "catalog"
                else ("entity_graph_edges", "DELETE")
            )
            await control.execute(
                f'CREATE FUNCTION public."{trigger}"() RETURNS trigger LANGUAGE plpgsql AS $$ '
                f"BEGIN IF OLD.source_id = '{memory_id}'::uuid AND "
                "OLD.source_schema = 'chronicler_mem' THEN RAISE EXCEPTION 'F2 injected fault'; "
                "END IF; RETURN OLD; END $$"
            )
            await control.execute(
                f'CREATE TRIGGER "{trigger}" BEFORE {event} ON public.{relation} '
                f'FOR EACH ROW EXECUTE FUNCTION public."{trigger}"()'
            )
        async with _runtime_pool(url, schema="chronicler") as pool:
            assert await pool.fetchval("SELECT current_schema()") == "chronicler"
            assert not await pool.fetchval(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            )

            async def mutate():
                if operation == "retire_rule":
                    return await retire_rule(pool, memory_id, memory_schema="chronicler_mem")
                return await forget_memory(
                    pool,
                    memory_type,
                    memory_id,
                    memory_schema="chronicler_mem",
                    correction_id=str(uuid.uuid4()) if operation.startswith("correct_") else None,
                )

            if fault:
                with pytest.raises(asyncpg.RaiseError, match="F2 injected fault"):
                    await mutate()
            else:
                assert await mutate()
        after = await control.fetchrow(
            f'SELECT * FROM chronicler_mem."{table}" WHERE id=$1', memory_id
        )
        events = await control.fetchval(
            "SELECT count(*) FROM chronicler_mem.memory_events WHERE memory_id=$1", memory_id
        )
        assert (
            await control.fetch(
                "SELECT * FROM chronicler.memory_events WHERE memory_id=$1", memory_id
            )
            == domain_events_before
        )
        if memory_type != "episode":
            assert (
                await control.fetchrow(f'SELECT * FROM chronicler."{table}" WHERE id=$1', memory_id)
                == domain_baseline
            )
        if fault:
            assert after == baseline
            assert events == 0
            assert (
                await control.fetch(
                    "SELECT * FROM public.memory_catalog WHERE source_id=$1 ORDER BY source_schema",
                    memory_id,
                )
                == catalog_before
            )
            assert (
                await control.fetch(
                    "SELECT * FROM public.entity_graph_edges WHERE source_id=$1 ORDER BY source_schema",
                    memory_id,
                )
                == graph_before
            )
        else:
            assert events == int(operation.startswith("correct_"))
            if memory_type == "fact":
                assert after["validity"] == "retracted"
            elif memory_type == "rule" and operation == "retire_rule":
                assert after["retired_at"] is not None
            elif memory_type == "rule":
                assert after["metadata"]["forgotten"] is True
                if operation == "correct_rule":
                    assert "correction_id" in after["metadata"]
            else:
                assert after["expires_at"] <= await control.fetchval("SELECT now()")
            if memory_type != "episode":
                rows = await control.fetch(
                    "SELECT * FROM public.memory_catalog WHERE source_id=$1 ORDER BY source_schema",
                    memory_id,
                )
                assert rows[0] == catalog_before[0]  # domain lookalike stays live, byte-identical
                assert rows[1]["confidence"] == 0 and rows[1]["invalid_at"] is not None
                assert await control.fetch(
                    "SELECT * FROM public.entity_graph_edges WHERE source_id=$1 ORDER BY source_schema",
                    memory_id,
                ) == [graph_before[0]]
            else:
                assert await control.fetch(
                    "SELECT * FROM public.memory_catalog WHERE source_id=$1 ORDER BY source_schema",
                    memory_id,
                ) == catalog_before
                assert await control.fetch(
                    "SELECT * FROM public.entity_graph_edges WHERE source_id=$1 ORDER BY source_schema",
                    memory_id,
                ) == graph_before
    finally:
        if fault:
            relation = "memory_catalog" if fault == "catalog" else "entity_graph_edges"
            await control.execute(f'DROP TRIGGER IF EXISTS "{trigger}" ON public.{relation}')
            await control.execute(f'DROP FUNCTION IF EXISTS public."{trigger}"()')
        await control.close()


async def test_legacy_unqualified_correction_uses_callers_schema_for_event_and_projection(
    private_grant_database,
):
    """Omitted memory_schema keeps same-connection current_schema semantics."""
    from butlers.modules.memory.storage import forget_memory

    control = await asyncpg.connect(private_grant_database)
    await register_jsonb_codec(control)
    memory_id, entity_id = uuid.uuid4(), uuid.uuid4()
    correction_id = str(uuid.uuid4())
    try:
        await control.execute(
            'CREATE TABLE IF NOT EXISTS chronicler."facts" '
            'LIKE chronicler_mem."facts" INCLUDING DEFAULTS'
        )
        await control.execute(
            "CREATE TABLE IF NOT EXISTS chronicler.memory_events "
            "(LIKE chronicler_mem.memory_events INCLUDING DEFAULTS)"
        )
        await control.execute(
            "INSERT INTO public.entities(id,canonical_name,entity_type) "
            "VALUES($1,'F2 legacy synthetic owner','person')",
            entity_id,
        )
        for schema in ("chronicler", "chronicler_mem"):
            await control.execute(
                f'INSERT INTO "{schema}"."facts"(id,subject,predicate,content) '
                "VALUES($1,$2,'is','F2 legacy synthetic fact')",
                memory_id,
                f"f2-legacy-{memory_id}",
            )
            await control.execute(
                "INSERT INTO public.memory_catalog(source_schema,source_table,source_id,summary,confidence) "
                "VALUES($1,'facts',$2,'F2 legacy projection',0.75)",
                schema,
                memory_id,
            )
            await control.execute(
                "INSERT INTO public.entity_graph_edges(source_schema,source_table,source_id,"
                "subject_entity_id,predicate,object_entity_id) VALUES($1,'facts',$2,$3,'knows',$3)",
                schema,
                memory_id,
                entity_id,
            )
        await control.execute(
            "INSERT INTO chronicler.memory_events(event_type,memory_type,memory_id,payload) "
            "VALUES('F2_legacy_control','fact',$1,'{}'::jsonb)",
            memory_id,
        )
        domain_events_before = await control.fetch(
            "SELECT * FROM chronicler.memory_events WHERE memory_id=$1 ORDER BY id", memory_id
        )
        private_events_before = await control.fetch(
            "SELECT * FROM chronicler_mem.memory_events WHERE memory_id=$1 ORDER BY id", memory_id
        )
        domain_fact_before = await control.fetchrow(
            "SELECT * FROM chronicler.facts WHERE id=$1", memory_id
        )
        private_fact_before = await control.fetchrow(
            "SELECT * FROM chronicler_mem.facts WHERE id=$1", memory_id
        )
        assert domain_fact_before["validity"] == "active"
        catalog_before = await control.fetch(
            "SELECT * FROM public.memory_catalog WHERE source_id=$1 ORDER BY source_schema",
            memory_id,
        )
        graph_before = await control.fetch(
            "SELECT * FROM public.entity_graph_edges WHERE source_id=$1 ORDER BY source_schema",
            memory_id,
        )

        async with _runtime_pool(private_grant_database, schema="chronicler") as pool:
            assert await pool.fetchval("SELECT current_schema()") == "chronicler"
            assert await forget_memory(
                pool,
                "fact",
                memory_id,
                correction_id=correction_id,
            )

        domain_fact_after = await control.fetchrow(
            "SELECT * FROM chronicler.facts WHERE id=$1", memory_id
        )
        private_fact_after = await control.fetchrow(
            "SELECT * FROM chronicler_mem.facts WHERE id=$1", memory_id
        )
        assert domain_fact_after["validity"] == "retracted"
        assert domain_fact_after["metadata"]["correction_id"] == correction_id
        assert private_fact_after == private_fact_before

        domain_events_after = await control.fetch(
            "SELECT * FROM chronicler.memory_events WHERE memory_id=$1 ORDER BY id", memory_id
        )
        private_events_after = await control.fetch(
            "SELECT * FROM chronicler_mem.memory_events WHERE memory_id=$1 ORDER BY id", memory_id
        )
        assert domain_events_after[: len(domain_events_before)] == domain_events_before
        assert len(domain_events_after) == len(domain_events_before) + 1
        assert domain_events_after[-1]["event_type"] == "correction_driven_retraction"
        assert domain_events_after[-1]["payload"]["correction_id"] == correction_id
        assert private_events_after == private_events_before

        catalog_after = await control.fetch(
            "SELECT * FROM public.memory_catalog WHERE source_id=$1 ORDER BY source_schema",
            memory_id,
        )
        catalog_before_private = next(
            row for row in catalog_before if row["source_schema"] == "chronicler_mem"
        )
        catalog_after_domain = next(
            row for row in catalog_after if row["source_schema"] == "chronicler"
        )
        catalog_after_private = next(
            row for row in catalog_after if row["source_schema"] == "chronicler_mem"
        )
        assert catalog_after_domain["confidence"] == 0
        assert catalog_after_domain["invalid_at"] is not None
        assert catalog_after_private == catalog_before_private
        graph_after = await control.fetch(
            "SELECT * FROM public.entity_graph_edges WHERE source_id=$1 ORDER BY source_schema",
            memory_id,
        )
        graph_before_private = next(
            row for row in graph_before if row["source_schema"] == "chronicler_mem"
        )
        assert graph_after == [graph_before_private]
    finally:
        await control.close()


async def test_explicit_memory_schema_rejects_unsafe_identifier(private_grant_database):
    from butlers.modules.memory.storage import forget_memory

    async with _runtime_pool(private_grant_database, schema="chronicler") as pool:
        with pytest.raises(ValueError, match="Unsafe memory schema identifier"):
            await forget_memory(
                pool,
                "fact",
                uuid.uuid4(),
                memory_schema="chronicler_mem.bad",
            )


@pytest.mark.parametrize(
    ("relation", "privilege"),
    [
        ("memory_catalog", "SELECT"),
        ("memory_catalog", "UPDATE"),
        ("entity_graph_edges", "SELECT"),
        ("entity_graph_edges", "DELETE"),
        ("function", "EXECUTE"),
    ],
)
async def test_invoker_underlying_acl_failure_rolls_back_private_correction(
    private_grant_database, relation, privilege
):
    from butlers.modules.memory.storage import forget_memory

    control = await asyncpg.connect(private_grant_database)
    await register_jsonb_codec(control)
    memory_id, entity_id = uuid.uuid4(), uuid.uuid4()
    target = (
        "FUNCTION public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)"
        if relation == "function" else f"TABLE public.{relation}"
    )
    try:
        await control.execute(
            "INSERT INTO public.entities(id,canonical_name,entity_type) "
            "VALUES($1,'synthetic ACL owner','person')",
            entity_id,
        )
        await control.execute(
            "INSERT INTO chronicler_mem.facts(id,subject,predicate,content) "
            "VALUES($1,$2,'is','synthetic ACL fact')",
            memory_id,
            f"acl-{memory_id}",
        )
        await control.execute(
            "INSERT INTO public.memory_catalog(source_schema,source_table,source_id,summary,confidence) "
            "VALUES('chronicler_mem','facts',$1,'synthetic ACL summary',0.75)",
            memory_id,
        )
        await control.execute(
            "INSERT INTO public.entity_graph_edges(source_schema,source_table,source_id,"
            "subject_entity_id,predicate,object_entity_id) "
            "VALUES('chronicler_mem','facts',$1,$2,'knows',$2)",
            memory_id,
            entity_id,
        )
        before = await control.fetchrow("SELECT * FROM chronicler_mem.facts WHERE id=$1", memory_id)
        catalog = await control.fetchrow(
            "SELECT * FROM public.memory_catalog WHERE source_id=$1", memory_id
        )
        graph = await control.fetchrow(
            "SELECT * FROM public.entity_graph_edges WHERE source_id=$1", memory_id
        )
        # Test inputs are the fixed parametrized relation/privilege allowlist.
        await control.execute(f"REVOKE {privilege} ON {target} FROM butler_chronicler_rw")
        async with _runtime_pool(private_grant_database, schema="chronicler") as pool:
            assert not await pool.fetchval(
                "SELECT has_function_privilege($1,$2)" if relation == "function"
                else "SELECT has_table_privilege($1,$2)",
                "public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)"
                if relation == "function" else "public." + relation,
                privilege,
            ), "negative ACL fixture must actually remove inherited effective privilege"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await forget_memory(
                    pool,
                    "fact",
                    memory_id,
                    memory_schema="chronicler_mem",
                    correction_id=str(uuid.uuid4()),
                )
        assert (
            await control.fetchrow("SELECT * FROM chronicler_mem.facts WHERE id=$1", memory_id)
            == before
        )
        assert (
            await control.fetchval(
                "SELECT count(*) FROM chronicler_mem.memory_events WHERE memory_id=$1", memory_id
            )
            == 0
        )
        assert (
            await control.fetchrow(
                "SELECT * FROM public.memory_catalog WHERE source_id=$1", memory_id
            )
            == catalog
        )
        assert (
            await control.fetchrow(
                "SELECT * FROM public.entity_graph_edges WHERE source_id=$1", memory_id
            )
            == graph
        )
    finally:
        await control.execute(f"GRANT {privilege} ON {target} TO butler_chronicler_rw")
        await control.close()
