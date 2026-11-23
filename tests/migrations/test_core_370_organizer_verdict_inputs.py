"""core_370: the two organizer-verdict definers answer only what the verdict needs (bu-5ii4gj.6)."""

from __future__ import annotations

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

_IDENTITY = "public.organizer_identity_class(text)"
_RULES = "public.organizer_policy_rules()"


@pytest.fixture
def url(fresh_db) -> str:
    return fresh_db(
        "core",
        "relationship",
        "switchboard",
        schemas={"relationship": "relationship", "switchboard": "switchboard"},
    )


async def _entity(pool: asyncpg.Pool, name: str, **metadata) -> str:
    import json

    return str(
        await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, roles, metadata) "
            "VALUES ($1, '{}', $2::jsonb) RETURNING id",
            name,
            json.dumps(metadata),
        )
    )


async def _email(pool: asyncpg.Pool, entity: str, address: str, validity: str = "active") -> None:
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "validity) VALUES ($1::uuid, 'has-email', $2, 'literal', 'test', $3)",
        entity,
        address,
        validity,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_identity_class_counts_only_live_identified_exact_matches(url: str) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        known = await _entity(pool, "Sam")
        await _email(pool, known, "Sam@Example.Org")
        placeholder = await _entity(pool, "?", unidentified="true")
        await _email(pool, placeholder, "ghost@stranger.test")
        merged = await _entity(pool, "Old", merged_into="x")
        await _email(pool, merged, "old@merged.test")
        stale = await _entity(pool, "Stale")
        await _email(pool, stale, "stale@gone.test", validity="superseded")
        free = await _entity(pool, "Pal")
        await _email(pool, free, "pal@gmail.com")
        twin_a, twin_b = await _entity(pool, "A"), await _entity(pool, "B")
        await _email(pool, twin_a, "twin@dup.test")
        await _email(pool, twin_b, "twin@dup.test")

        async def cls(address: str) -> str:
            return await pool.fetchval(f"SELECT {_IDENTITY.split('(')[0]}($1)", address)

        assert await cls(" SAM@example.org ") == "known_entity"
        assert await cls("other@example.org") == "known_domain"
        assert await cls("ghost@stranger.test") == "none"
        assert await cls("anyone@stranger.test") == "none"
        assert await cls("old@merged.test") == "none"
        assert await cls("stale@gone.test") == "none"
        assert await cls("someone@gmail.com") == "none"  # a freemail domain is never "known"
        assert await cls("pal@gmail.com") == "known_entity"
        assert await cls("twin@dup.test") == "none"  # ambiguous address
        assert await cls("not-an-address") == "none"
        assert await cls("x@%.org") == "none"  # equality only: a wildcard matches nothing
        assert await cls("%@example.org") == "known_domain"  # the domain is exact, the rest inert
        assert await pool.fetchval("SELECT public.organizer_identity_class(NULL)") == "none"
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_policy_rules_return_only_enabled_global_and_calendar_connector_rules(
    url: str,
) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        for scope, enabled, deleted in (
            ("global", True, False),
            ("connector:google_calendar:me@example.com", True, False),
            ("connector:gmail:me@example.com", True, False),
            ("global", False, False),
            ("global", True, True),
        ):
            await pool.execute(
                "INSERT INTO switchboard.ingestion_rules (scope, rule_type, condition, action, "
                "priority, enabled, deleted_at, name) VALUES ($1, 'sender_address', "
                "'{\"address\": \"x@y.test\"}', 'block', 10, $2, "
                "CASE WHEN $3 THEN now() END, 'verdict-test')",
                scope,
                enabled,
                deleted,
            )
        rows = await pool.fetch(
            "SELECT scope FROM public.organizer_policy_rules() WHERE name = 'verdict-test'"
        )
        assert sorted(r["scope"] for r in rows) == [
            "connector:google_calendar:me@example.com",
            "global",
        ]
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_public_has_no_execute_on_either_definer(url: str) -> None:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        for signature in (_IDENTITY, _RULES):
            grantees = await pool.fetch(
                "SELECT a.grantee FROM pg_proc p, aclexplode(p.proacl) a "
                "WHERE p.oid = to_regprocedure($1)",
                signature,
            )
            assert grantees, "an ACL must be set (default PUBLIC execute is revoked)"
            assert all(row["grantee"] != 0 for row in grantees)  # 0 is PUBLIC
    finally:
        await pool.close()


def test_a_second_schema_is_idempotent_and_only_the_last_downgrade_drops(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(first, "core_370")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="travel")
    command.upgrade(second, "core_370")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:

        def exists() -> bool:
            with engine.connect() as conn:
                return bool(
                    conn.execute(
                        text("SELECT to_regprocedure('public.organizer_identity_class(text)')")
                    ).scalar()
                )

        assert exists()
        command.downgrade(first, "core_369")
        assert exists()  # the other schema is still at core_370
        command.downgrade(second, "core_369")
        assert not exists()
    finally:
        engine.dispose()
