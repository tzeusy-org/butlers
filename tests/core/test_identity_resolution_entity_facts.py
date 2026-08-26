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

from butlers.identity import resolve_contact_by_channel, resolve_contacts_by_channel_bulk

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


async def _tombstone_entity(pool, entity_id, key: str, value: str) -> None:
    await pool.execute(
        "UPDATE public.entities "
        "SET metadata = metadata || jsonb_build_object($2::text, $3::text) "
        "WHERE id = $1",
        entity_id,
        key,
        value,
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


async def test_whatsapp_user_client_resolves_like_whatsapp_jid(provisioned_postgres_pool) -> None:
    """Spec: REQ-switchboard-identity-001."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        direct = await _mk_entity(pool, "Direct")
        await _add_fact(pool, direct, "has-handle", "1234567890@s.whatsapp.net")
        by_phone = await _mk_entity(pool, "Phone")
        await _add_fact(pool, by_phone, "has-phone", "441234567890")

        direct_result = await resolve_contact_by_channel(
            pool, "whatsapp_user_client", "1234567890@s.whatsapp.net"
        )
        phone_result = await resolve_contact_by_channel(
            pool, "whatsapp_user_client", "441234567890@s.whatsapp.net"
        )
        bulk_result = await resolve_contacts_by_channel_bulk(
            pool,
            [
                ("whatsapp_user_client", "1234567890@s.whatsapp.net"),
                ("whatsapp_user_client", "441234567890@s.whatsapp.net"),
            ],
        )

        assert direct_result is not None and direct_result.entity_id == direct
        assert phone_result is not None and phone_result.entity_id == by_phone
        assert (
            bulk_result[("whatsapp_user_client", "1234567890@s.whatsapp.net")].entity_id == direct
        )
        assert (
            bulk_result[("whatsapp_user_client", "441234567890@s.whatsapp.net")].entity_id
            == by_phone
        )


@pytest.mark.parametrize(
    "stored_numbers",
    [
        ("441234567890", "441234567890"),
        ("+44 1234 567890", "44 1234 567890"),
    ],
    ids=["identical-exact-objects", "formatted-variants"],
)
async def test_whatsapp_user_client_ambiguous_phone_digits_returns_none(
    provisioned_postgres_pool,
    stored_numbers: tuple[str, str],
) -> None:
    """REQ-switchboard-identity-001: every exact/normalized ambiguity stays unresolved."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        first = await _mk_entity(pool, "First")
        second = await _mk_entity(pool, "Second")
        await _add_fact(pool, first, "has-phone", stored_numbers[0])
        await _add_fact(pool, second, "has-phone", stored_numbers[1])

        value = "441234567890@s.whatsapp.net"
        single_result = await resolve_contact_by_channel(pool, "whatsapp_user_client", value)
        bulk_result = await resolve_contacts_by_channel_bulk(
            pool, [("whatsapp_user_client", value)]
        )

        assert single_result is None
        assert bulk_result[("whatsapp_user_client", value)] is None


@pytest.mark.parametrize(
    ("tombstone_key", "tombstone_value"),
    [
        ("merged_into", "00000000-0000-0000-0000-000000000001"),
        ("deleted_at", "2026-08-24T00:00:00+00:00"),
    ],
    ids=["merged", "deleted"],
)
async def test_whatsapp_resolution_counts_only_live_entities(
    provisioned_postgres_pool,
    tombstone_key: str,
    tombstone_value: str,
) -> None:
    """REQ-switchboard-identity-001: tombstones neither resolve nor create ambiguity."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        exact_value = "15550002001@s.whatsapp.net"
        live_exact = await _mk_entity(pool, "Live Exact")
        dead_exact = await _mk_entity(pool, "Dead Exact")
        await _add_fact(pool, live_exact, "has-handle", exact_value)
        await _add_fact(pool, dead_exact, "has-handle", exact_value)
        await _tombstone_entity(pool, dead_exact, tombstone_key, tombstone_value)

        phone_value = "15550002002@s.whatsapp.net"
        live_phone = await _mk_entity(pool, "Live Phone")
        dead_phone = await _mk_entity(pool, "Dead Phone")
        await _add_fact(pool, live_phone, "has-phone", "15550002002")
        await _add_fact(pool, dead_phone, "has-phone", "+1 555 000 2002")
        await _tombstone_entity(pool, dead_phone, tombstone_key, tombstone_value)

        dead_only_exact_value = "15550002003@s.whatsapp.net"
        dead_only_exact = await _mk_entity(pool, "Dead Only Exact")
        await _add_fact(pool, dead_only_exact, "has-handle", dead_only_exact_value)
        await _tombstone_entity(pool, dead_only_exact, tombstone_key, tombstone_value)

        dead_only_phone_value = "15550002004@s.whatsapp.net"
        dead_only_phone = await _mk_entity(pool, "Dead Only Phone")
        await _add_fact(pool, dead_only_phone, "has-phone", "15550002004")
        await _tombstone_entity(pool, dead_only_phone, tombstone_key, tombstone_value)

        pairs = [
            ("whatsapp_user_client", exact_value),
            ("whatsapp_user_client", phone_value),
            ("whatsapp_user_client", dead_only_exact_value),
            ("whatsapp_user_client", dead_only_phone_value),
        ]
        single_results = [
            await resolve_contact_by_channel(pool, channel_type, channel_value)
            for channel_type, channel_value in pairs
        ]
        bulk_results = await resolve_contacts_by_channel_bulk(pool, pairs)

        assert single_results[0] is not None
        assert single_results[0].entity_id == live_exact
        assert single_results[1] is not None
        assert single_results[1].entity_id == live_phone
        assert single_results[2:] == [None, None]
        assert bulk_results[pairs[0]] is not None
        assert bulk_results[pairs[0]].entity_id == live_exact
        assert bulk_results[pairs[1]] is not None
        assert bulk_results[pairs[1]].entity_id == live_phone
        assert bulk_results[pairs[2]] is None
        assert bulk_results[pairs[3]] is None


# ---------------------------------------------------------------------------
# resolve_contacts_by_channel_bulk (bu-4utdw.3) — the batched N+1 killer used
# by the timeline list endpoint. These exercise the SAME cross-schema join
# (relationship.entity_facts JOIN public.entities) against a real Postgres,
# but through the grouped ``unnest($1::text[], $2::text[])`` query the bulk
# resolver issues for a whole page of ids in one round trip — this is the
# exact bug class (bare cross-schema access under a scoped search_path) that
# caused an 8h main-red via PR #2598, so mocked-pool coverage alone is not
# sufficient for this query.
# ---------------------------------------------------------------------------


async def test_bulk_resolves_multiple_pairs_matching_single_item_resolution(
    provisioned_postgres_pool,
) -> None:
    """Bulk resolution of several distinct channels in one query matches per-item resolution."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        chloe = await _mk_entity(pool, "Chloe Wong")
        await _add_fact(pool, chloe, "has-handle", "telegram:12345")

        owner = await _mk_entity(pool, "Owner", roles=["owner"])
        await _add_fact(pool, owner, "has-email", "owner@example.com")

        result = await resolve_contacts_by_channel_bulk(
            pool,
            [
                ("telegram", "telegram:12345"),
                ("email", "owner@example.com"),
                ("email", "unknown@example.com"),
            ],
        )

        assert result[("telegram", "telegram:12345")] is not None
        assert result[("telegram", "telegram:12345")].entity_id == chloe
        assert result[("telegram", "telegram:12345")].contact_id is None

        assert result[("email", "owner@example.com")] is not None
        assert result[("email", "owner@example.com")].entity_id == owner
        assert result[("email", "owner@example.com")].roles == ["owner"]

        assert result[("email", "unknown@example.com")] is None


async def test_bulk_telegram_user_client_prefix_fallback(provisioned_postgres_pool) -> None:
    """Bulk resolution applies the telegram: prefix fallback candidate, like the single-item path."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Chloe Wong")
        await _add_fact(pool, ent, "has-handle", "telegram:86807245")

        result = await resolve_contacts_by_channel_bulk(
            pool, [("telegram_user_client", "86807245")]
        )

        resolved = result[("telegram_user_client", "86807245")]
        assert resolved is not None
        assert resolved.entity_id == ent
        assert resolved.contact_id is None


async def test_bulk_retracted_triple_does_not_resolve(provisioned_postgres_pool) -> None:
    """validity='retracted' triples are excluded from the batched query too."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Departed")
        await _add_fact(pool, ent, "has-handle", "telegram:55555", validity="retracted")

        result = await resolve_contacts_by_channel_bulk(pool, [("telegram", "telegram:55555")])

        assert result[("telegram", "telegram:55555")] is None


async def test_bulk_duplicate_pairs_resolve_consistently(provisioned_postgres_pool) -> None:
    """The same (channel_type, value) pair appearing twice on a page resolves identically."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        ent = await _mk_entity(pool, "Alice")
        await _add_fact(pool, ent, "has-phone", "+15555551234")

        result = await resolve_contacts_by_channel_bulk(
            pool,
            [("phone", "+15555551234"), ("phone", "+15555551234")],
        )

        # dict keys collapse duplicates; the single resolved entry is correct.
        assert result[("phone", "+15555551234")] is not None
        assert result[("phone", "+15555551234")].entity_id == ent
