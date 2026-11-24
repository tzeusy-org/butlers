"""core_373: the participants table and the attendee identity claim read (bu-5ii4gj.3 S1).

Real migrated databases only, synthetic people. The function answers exact normalized addresses
with claimant ids and no names, capped at 200 addresses, to the butler runtime roles only.
"""

from __future__ import annotations

import json
import shutil

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_CLAIMS = "public.attendee_identity_claims(text[])"
_CALL = "public.attendee_identity_claims"


@pytest.fixture
def url(fresh_db) -> str:
    return fresh_db(
        "core",
        "relationship",
        "switchboard",
        schemas={"relationship": "relationship", "switchboard": "switchboard"},
    )


async def _entity(pool, name, *, roles=(), **metadata) -> str:
    return str(
        await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, roles, metadata) "
            "VALUES ($1, $2::text[], $3::jsonb) RETURNING id",
            name,
            list(roles),
            json.dumps(metadata),
        )
    )


async def _email(pool, entity, address, validity="active") -> None:
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "validity) VALUES ($1::uuid, 'has-email', $2, 'literal', 'test', $3)",
        entity,
        address,
        validity,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_the_claims_are_exact_live_and_nameless(url: str) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        sam = await _entity(pool, "Sam")
        await _email(pool, sam, "Sam@Example.Org")
        me = await _entity(pool, "Me", roles=["owner"])
        await _email(pool, me, "me@example.org")
        twin_a, twin_b = await _entity(pool, "A"), await _entity(pool, "B")
        await _email(pool, twin_a, "twin@dup.test")
        await _email(pool, twin_b, "twin@dup.test")
        merged = await _entity(pool, "Old", merged_into="x")
        await _email(pool, merged, "old@merged.test")
        gone = await _entity(pool, "Gone", deleted_at="2026-01-01")
        await _email(pool, gone, "gone@deleted.test")
        ghost = await _entity(pool, "?", unidentified="true")
        await _email(pool, ghost, "ghost@stranger.test")
        stale = await _entity(pool, "Stale")
        await _email(pool, stale, "stale@gone.test", validity="superseded")

        async def claims(*addresses: str):
            return await pool.fetch(f"SELECT * FROM {_CALL}($1::text[])", list(addresses))

        rows = await claims(
            " SAM@example.org ",
            "me@example.org",
            "twin@dup.test",
            "old@merged.test",
            "gone@deleted.test",
            "ghost@stranger.test",
            "stale@gone.test",
            "none@nowhere.test",
        )
        found = sorted((r["email_norm"], str(r["entity_id"]), r["is_owner"]) for r in rows)
        assert found == sorted(
            [
                ("sam@example.org", sam, False),
                ("me@example.org", me, True),
                ("twin@dup.test", twin_a, False),  # both claimants come back; the resolver
                ("twin@dup.test", twin_b, False),  # decides it is ambiguous
            ]
        )
        assert set(rows[0].keys()) == {"email_norm", "entity_id", "is_owner"}  # no names
        assert await claims("sam@example.org%") == []  # equality only, never a wildcard
        assert await claims("%@example.org") == []
        assert await claims("sam@example.org", "sam@example.org") == await claims("sam@example.org")
        assert await pool.fetch(f"SELECT * FROM {_CALL}(NULL)") == []
        assert await claims() == []
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_input_is_capped_at_two_hundred_addresses(url: str) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        ok = [f"u{i}@x.test" for i in range(200)]
        assert await pool.fetch(f"SELECT * FROM {_CALL}($1::text[])", ok) == []
        with pytest.raises(asyncpg.PostgresError, match="at most 200"):
            await pool.fetch(f"SELECT * FROM {_CALL}($1::text[])", [*ok, "extra@x.test"])
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_public_has_no_execute_and_the_function_is_a_pinned_definer(url: str) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        grantees = await pool.fetch(
            "SELECT a.grantee FROM pg_proc p, aclexplode(p.proacl) a "
            "WHERE p.oid = to_regprocedure($1)",
            _CLAIMS,
        )
        assert grantees and all(row["grantee"] != 0 for row in grantees)  # 0 is PUBLIC
        flags = await pool.fetchrow(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = to_regprocedure($1)", _CLAIMS
        )
        assert flags["prosecdef"] and flags["proconfig"] == ["search_path=pg_catalog, pg_temp"]
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_participants_table_enforces_its_shape(url: str) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        source = await pool.fetchval(
            "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
            "display_name, writable, metadata) VALUES ('provider:google:primary', "
            "'provider_event', 'user', 'google', 'primary', 'Primary', true, '{}') RETURNING id"
        )
        event = await pool.fetchval(
            "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
            "ends_at, status, metadata, source_butler) VALUES ($1, 'e', 'T', 'UTC', now(), "
            "now() + interval '1 hour', 'confirmed', '{}', 'general') RETURNING id",
            source,
        )
        person = await _entity(pool, "Pal")

        async def insert(email, resolution, entity=None, role="required"):
            await pool.execute(
                "INSERT INTO calendar_event_participants (event_id, email_norm, role, "
                "response_status, entity_id, resolution, resolver_version) "
                "VALUES ($1, $2, $3, 'accepted', $4::uuid, $5, '1')",
                event,
                email,
                role,
                entity,
                resolution,
            )

        await insert("a@x.test", "exact", person)
        with pytest.raises(asyncpg.UniqueViolationError):
            await insert("a@x.test", "exact", person)  # one row per (event, address)
        with pytest.raises(asyncpg.CheckViolationError):
            await insert("b@x.test", "exact")  # exact needs an entity
        with pytest.raises(asyncpg.CheckViolationError):
            await insert("c@x.test", "ambiguous", person)  # ambiguous names no one
        with pytest.raises(asyncpg.CheckViolationError):
            await insert("r@resource.calendar.google.com", "resource", person, "resource")
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(None, "exact", person)  # only a manual row may omit the address
        with pytest.raises(asyncpg.CheckViolationError):
            await insert("d@x.test", "unresolved", None, "attendee")  # closed role set
        await insert(None, "manual", person)
        with pytest.raises(asyncpg.UniqueViolationError):
            await insert(None, "manual", person)  # one manual row per (event, entity)
        await pool.execute("DELETE FROM calendar_events WHERE id = $1", event)
        assert await pool.fetchval("SELECT count(*) FROM calendar_event_participants") == 0
    finally:
        await pool.close()


def test_two_schemas_install_once_and_only_the_last_downgrade_drops_the_function(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="travel")
    command.upgrade(first, "core_373")
    command.upgrade(second, "core_373")
    command.upgrade(second, "core_373")  # an idempotent replay
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:

        def function() -> bool:
            with engine.connect() as conn:
                return bool(conn.execute(text(f"SELECT to_regprocedure('{_CLAIMS}')")).scalar())

        def table(schema: str) -> bool:
            with engine.connect() as conn:
                return bool(
                    conn.execute(
                        text("SELECT to_regclass(:n)"),
                        {"n": f"{schema}.calendar_event_participants"},
                    ).scalar()
                )

        assert function() and table("general") and table("travel")
        command.downgrade(first, "core_372")
        assert function() and not table("general") and table("travel")
        command.downgrade(second, "core_372")
        assert not function() and not table("travel")
    finally:
        engine.dispose()


def test_a_later_run_converges_a_stray_grantee_away(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(first, "core_373")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {_CLAIMS} TO PUBLIC"))
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema="travel"), "core_373"
        )
        with engine.connect() as conn:
            public = conn.execute(
                text(
                    "SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a "
                    "WHERE p.oid = to_regprocedure(:s) AND a.grantee = 0"
                ),
                {"s": _CLAIMS},
            ).scalar()
        assert public == 0
    finally:
        engine.dispose()


def test_only_the_butlers_hosting_the_calendar_module_may_execute(postgres_container) -> None:
    from butlers.db_authority.roles import butler_roles, module_roles

    hosts, everyone = set(module_roles("calendar")), set(butler_roles())
    assert hosts and everyone - hosts  # the roster has hosts and non-hosts, or this proves nothing
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for role in sorted(everyone):
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                ).scalar()
                if not exists:
                    conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema="general"), "core_373"
        )
        with engine.connect() as conn:
            granted = set(
                conn.execute(
                    text(
                        "SELECT r.rolname FROM pg_proc p, aclexplode(p.proacl) a "
                        "JOIN pg_roles r ON r.oid = a.grantee "
                        "WHERE p.oid = to_regprocedure(:s) AND a.grantee <> p.proowner"
                    ),
                    {"s": _CLAIMS},
                ).scalars()
            )
        assert granted == hosts
    finally:
        engine.dispose()
