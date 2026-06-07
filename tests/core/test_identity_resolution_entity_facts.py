"""Integration tests: resolve_contact_by_channel against a live relationship.entity_facts.

bu-w2zo6 — the spec scenario from ``relationship-facts/spec.md``:

    WHEN an incoming Telegram message arrives with chat_id 12345
    AND a triple (subject=ent-7, predicate='has-handle', object='telegram:12345',
        object_kind='literal', validity='active') exists in relationship.entity_facts
    THEN resolve_contact_by_channel('telegram', 'telegram:12345') MUST return a
        ResolvedContact with entity_id=ent-7
    AND the returned shape MUST NOT include a contact_id (it is None post bead 7).

``tests/core/test_identity.py`` already covers resolution against a *mocked* pool
(asserting the SQL string + the returned dataclass).  This module is the missing
counterpart: it exercises the real ``relationship.entity_facts`` SQL path — the
predicate map, the ``object_kind='literal'`` / ``validity='active'`` filters, the
``public.entities`` join (roles + canonical_name), and the
``telegram_user_client`` prefix fallback — against an actual Postgres instance.

Spec anchor: Brief §3 (deterministic Finder) + relationship-facts/spec.md
(Telegram-resolves-via-has-handle scenario).  Re-implementation landed in bead 7
(bu-akads / task 10.7).
"""

from __future__ import annotations

import shutil

import pytest

from butlers.identity import resolve_contact_by_channel

# Minimal schema the resolver touches: public.entities (join target) and
# relationship.entity_facts (the triple store).  Mirrors the real migration DDL
# closely enough for the resolver's SELECT.  contacts/contact_info are NOT
# created — contact_id must be None post bead 7, so the resolver never reads them.
_PROVISION_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS relationship;

CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT NOT NULL,
    roles          TEXT[] NOT NULL DEFAULT '{}',
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS relationship.entity_facts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject     UUID NOT NULL,
    predicate   TEXT NOT NULL,
    object      TEXT,
    object_kind TEXT NOT NULL DEFAULT 'literal',
    validity    TEXT NOT NULL DEFAULT 'active',
    src         TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _mk_entity(pool, name: str, *, roles: list[str] | None = None):
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, $2) RETURNING id",
        name,
        roles or [],
    )


async def _add_fact(
    pool,
    subject,
    predicate: str,
    obj: str,
    *,
    object_kind: str = "literal",
    validity: str = "active",
):
    await pool.execute(
        "INSERT INTO relationship.entity_facts "
        "(subject, predicate, object, object_kind, validity, src) "
        "VALUES ($1, $2, $3, $4, $5, 'test')",
        subject,
        predicate,
        obj,
        object_kind,
        validity,
    )


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def test_telegram_resolves_via_has_handle_triple(provisioned_postgres_pool) -> None:
    """Spec scenario: prefixed telegram handle resolves to its entity; contact_id is None."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent7 = await _mk_entity(pool, "Chloe Wong")
        await _add_fact(pool, ent7, "has-handle", "telegram:12345")

        result = await resolve_contact_by_channel(pool, "telegram", "telegram:12345")

        assert result is not None, "active has-handle triple must resolve"
        assert result.entity_id == ent7
        # MUST NOT surface a contact_id — entity_id is authoritative post bead 7.
        assert result.contact_id is None
        assert result.name == "Chloe Wong"
        assert result.roles == []


async def test_owner_roles_propagate_from_entities_join(provisioned_postgres_pool) -> None:
    """The public.entities join carries roles=['owner'] through to ResolvedContact."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        owner = await _mk_entity(pool, "Owner", roles=["owner"])
        await _add_fact(pool, owner, "has-email", "owner@example.com")

        result = await resolve_contact_by_channel(pool, "email", "owner@example.com")

        assert result is not None
        assert result.entity_id == owner
        assert result.roles == ["owner"]
        assert result.contact_id is None


async def test_phone_resolves_via_has_phone_triple(provisioned_postgres_pool) -> None:
    """phone channel maps to has-phone and resolves against the real SQL path."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Alice")
        await _add_fact(pool, ent, "has-phone", "+15555551234")

        result = await resolve_contact_by_channel(pool, "phone", "+15555551234")

        assert result is not None
        assert result.entity_id == ent


async def test_telegram_user_client_prefix_fallback(provisioned_postgres_pool) -> None:
    """Raw telegram_user_client id resolves via the 'telegram:'-prefixed fallback query.

    This is the realistic ingestion path: the daemon passes the bare chat id and
    the resolver retries with the ``telegram:`` prefix used by rel_019.
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Chloe Wong")
        await _add_fact(pool, ent, "has-handle", "telegram:86807245")

        result = await resolve_contact_by_channel(pool, "telegram_user_client", "86807245")

        assert result is not None
        assert result.entity_id == ent
        assert result.contact_id is None


async def test_retracted_triple_does_not_resolve(provisioned_postgres_pool) -> None:
    """validity='retracted' triples are filtered out by the live SQL (returns None)."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Departed")
        await _add_fact(pool, ent, "has-handle", "telegram:55555", validity="retracted")

        result = await resolve_contact_by_channel(pool, "telegram", "telegram:55555")

        assert result is None, "retracted triple must not resolve"


async def test_non_literal_object_kind_does_not_resolve(provisioned_postgres_pool) -> None:
    """Only object_kind='literal' triples are eligible; entity references are skipped."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Ref")
        await _add_fact(pool, ent, "has-handle", "telegram:77777", object_kind="entity")

        result = await resolve_contact_by_channel(pool, "telegram", "telegram:77777")

        assert result is None, "non-literal object_kind must not resolve"


async def test_unknown_handle_returns_none(provisioned_postgres_pool) -> None:
    """No matching triple → None (not an error)."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        result = await resolve_contact_by_channel(pool, "telegram", "telegram:does-not-exist")

        assert result is None
