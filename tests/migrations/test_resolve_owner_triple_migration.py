"""Integration test: public.resolve_owner_triple SECURITY DEFINER (core_145 to core_296).

Closes the gap identified in bu-6ui2o: the mocked-pool unit tests in
``test_gate.py`` and ``test_identity_resolution.py`` cover the Python layer
but leave the actual SQL + cross-SET-ROLE privilege boundary unverified.

Verified:
1. Function exists and is callable after the core migration chain.
2. Owner-only scoping — non-owner entities are never returned even when they
   share the same predicate and candidate value list.
3. ``is_primary`` aggregation (RFC 0017 §2.1) — a sole matched owner is primary
   when any of its matching candidate facts is primary.
4. Schema isolation — a butler role with NO direct read access to
   ``relationship.entity_facts`` can call the SECURITY DEFINER function via
   the EXECUTE grant added in core_145 and receives correct owner-only results.
   Crucially, that same role is blocked from reading the underlying table
   directly, proving the SECURITY DEFINER boundary holds.
5. Ambiguity safety — an identifier shared by owner and external entities
   returns no owner authorization.

Issues: bu-6ui2o, bu-rp2ie7
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from urllib.parse import urlparse

import asyncpg
import pytest

from alembic import command
from butlers.config import ApprovalRiskTier
from butlers.db import Database
from butlers.migrations import _build_alembic_config
from butlers.modules.approvals.email_guard import check_email_recipient, check_recipient
from butlers.modules.approvals.gate import _make_gate_wrapper
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# ──────────────────────────────────────────────────────────────────────────────
# Module-scoped fixtures (provisioned once per test module)
# ──────────────────────────────────────────────────────────────────────────────

_ISOLATED_ROLE = "butler_isolated_rw_core145"
_ISOLATED_ROLE_PASSWORD = "isolated_test_pw_core145"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core, relationship, and Messenger approvals schemas.

    The ``core`` chain creates ``public.entities`` and
    ``public.resolve_owner_triple`` (core_145).  The ``relationship`` chain
    creates ``relationship.entity_facts``.  The approvals chain supplies real
    pending/rule tables under Messenger's schema for guard-level assertions.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "relationship", "approvals"],
        schemas={"relationship": "relationship", "approvals": "messenger"},
    )


@pytest.fixture(scope="module")
async def migration_pool(migrated_db_url: str) -> asyncpg.Pool:
    """Migration-role asyncpg pool to the migrated disposable database."""
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture(scope="module")
async def seeded_data(migration_pool: asyncpg.Pool) -> dict:
    """Seed owner + non-owner entities and matching ``entity_facts`` rows.

    Returns a dict with the entity UUIDs and the handle values used in tests.
    Owner has two handles: one primary, one non-primary.  The non-owner has
    one handle with the same predicate (to confirm owner-only scoping).
    """
    owner_id = await migration_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, $2) RETURNING id",
        "Test Owner",
        ["owner"],
    )
    non_owner_id = await migration_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, $2) RETURNING id",
        "Other Person",
        [],
    )
    owner_primary_handle = "telegram:owner_primary_12345"
    owner_secondary_handle = "telegram:owner_secondary_54321"
    non_owner_handle = "telegram:non_owner_99999"
    owner_primary_email = "owner-primary@example.test"
    owner_secondary_email = "owner-secondary@example.test"
    non_owner_email = "external@example.test"
    case_ambiguous_email = "Case.Owner@Example.Test"
    malformed_owner_email = "malformed-owner-email"
    owner_secondary_whatsapp = "15551234567@s.whatsapp.net"
    non_owner_whatsapp = "15557654321@s.whatsapp.net"
    normalized_ambiguous_whatsapp = "15558889999@s.whatsapp.net"
    malformed_owner_telegram = "bad handle"
    malformed_owner_whatsapp = "owner@not-whatsapp"
    ambiguous_email = "ambiguous@example.test"
    ambiguous_handle = "telegram:ambiguous-11111"
    normalized_ambiguous_handle = "@MixedCaseOwner"
    inactive_owner_email = "inactive-owner@example.test"
    merged_owner_email = "merged-owner@example.test"
    deleted_owner_email = "deleted-owner@example.test"

    # Owner primary handle
    await migration_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-handle', $2, 'literal', 'test', true, 'active')
        """,
        owner_id,
        owner_primary_handle,
    )
    # Owner secondary (non-primary) handle
    await migration_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-handle', $2, 'literal', 'test', false, 'active')
        """,
        owner_id,
        owner_secondary_handle,
    )
    # Non-owner handle — same predicate, different entity (must never be returned)
    await migration_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-handle', $2, 'literal', 'test', NULL, 'active')
        """,
        non_owner_id,
        non_owner_handle,
    )
    await migration_pool.executemany(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-email', $2, 'literal', 'test', $3, 'active')
        """,
        [
            (owner_id, owner_primary_email, True),
            (owner_id, owner_secondary_email, False),
            (non_owner_id, non_owner_email, False),
            (owner_id, ambiguous_email, True),
            (non_owner_id, ambiguous_email, False),
            (owner_id, merged_owner_email, True),
            (owner_id, deleted_owner_email, True),
            (owner_id, case_ambiguous_email, False),
            (non_owner_id, case_ambiguous_email.lower(), False),
            (owner_id, malformed_owner_email, False),
        ],
    )
    await migration_pool.executemany(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-handle', $2, 'literal', 'test', $3, 'active')
        """,
        [
            (owner_id, ambiguous_handle, True),
            (non_owner_id, ambiguous_handle, False),
            (owner_id, "MixedCaseOwner", False),
            (non_owner_id, "telegram:mixedcaseowner", False),
            (owner_id, owner_secondary_whatsapp, False),
            (non_owner_id, non_owner_whatsapp, False),
            (owner_id, normalized_ambiguous_whatsapp, False),
            (owner_id, malformed_owner_telegram, False),
            (owner_id, malformed_owner_whatsapp, False),
        ],
    )
    await migration_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-phone', $2, 'literal', 'test', false, 'active')
        """,
        non_owner_id,
        "+1 (555) 888-9999",
    )
    await migration_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-phone', $2, 'literal', 'test', false, 'active')
        """,
        owner_id,
        "+1 (555) 111-2222",
    )
    await migration_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, "primary", validity)
        VALUES ($1, 'has-email', $2, 'literal', 'test', true, 'retracted')
        """,
        owner_id,
        inactive_owner_email,
    )

    return {
        "owner_id": owner_id,
        "non_owner_id": non_owner_id,
        "owner_primary_handle": owner_primary_handle,
        "owner_secondary_handle": owner_secondary_handle,
        "non_owner_handle": non_owner_handle,
        "owner_primary_email": owner_primary_email,
        "owner_secondary_email": owner_secondary_email,
        "non_owner_email": non_owner_email,
        "case_ambiguous_email": case_ambiguous_email,
        "malformed_owner_email": malformed_owner_email,
        "owner_secondary_whatsapp": owner_secondary_whatsapp,
        "non_owner_whatsapp": non_owner_whatsapp,
        "normalized_ambiguous_whatsapp": normalized_ambiguous_whatsapp,
        "malformed_owner_telegram": malformed_owner_telegram,
        "malformed_owner_whatsapp": malformed_owner_whatsapp,
        "ambiguous_email": ambiguous_email,
        "ambiguous_handle": ambiguous_handle,
        "normalized_ambiguous_handle": normalized_ambiguous_handle,
        "inactive_owner_email": inactive_owner_email,
        "merged_owner_email": merged_owner_email,
        "deleted_owner_email": deleted_owner_email,
    }


@pytest.fixture(scope="module")
async def isolated_role_pool(
    postgres_container,
    migrated_db_url: str,
) -> asyncpg.Pool:
    """Pool connected as a schema-isolated butler role.

    The role has:
    - CONNECT on the database
    - USAGE on the public schema (needed to call public functions)
    - EXECUTE on public.resolve_owner_triple via the PUBLIC grant in core_145

    It does NOT have:
    - USAGE on the relationship schema
    - SELECT on relationship.entity_facts

    This mirrors the real constraint: a non-relationship butler (e.g. messenger)
    runs under SET ROLE butler_<schema>_rw and cannot read relationship.entity_facts
    directly.  The SECURITY DEFINER function bridges that gap.
    """
    parsed = urlparse(migrated_db_url)
    db_name = parsed.path.lstrip("/")

    # The migrated DB URL deliberately authenticates as a NOCREATEROLE normal
    # migration user. Provision this test-only isolated role through the
    # disposable testcontainer control URL instead; the asserted runtime role
    # remains restricted below.
    bootstrap_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
    )
    try:
        # Idempotent: a prior partial run may have left the cluster-global role.
        await bootstrap_conn.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ISOLATED_ROLE}') THEN
                    CREATE ROLE "{_ISOLATED_ROLE}" WITH LOGIN
                        PASSWORD '{_ISOLATED_ROLE_PASSWORD}'
                        NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT;
                END IF;
            END
            $$
            """
        )
        await bootstrap_conn.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO "{_ISOLATED_ROLE}"')
        # Allow calling public.* functions (schema USAGE, no table USAGE).
        await bootstrap_conn.execute(f'GRANT USAGE ON SCHEMA public TO "{_ISOLATED_ROLE}"')
        # Explicitly do NOT grant USAGE on relationship schema or SELECT on entity_facts.
    finally:
        await bootstrap_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=_ISOLATED_ROLE,
        password=_ISOLATED_ROLE_PASSWORD,
        database=db_name,
        min_size=1,
        max_size=2,
    )
    yield pool
    await pool.close()


@pytest.fixture(scope="module")
async def messenger_role_pool(migrated_db_url: str) -> asyncpg.Pool:
    """Production Database pool constrained by the real Messenger runtime role."""
    parsed = urlparse(migrated_db_url)
    database = Database(
        db_name=parsed.path.lstrip("/"),
        schema="messenger",
        role="butler_messenger_rw",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "",
        min_pool_size=1,
        max_pool_size=2,
    )
    pool = await database.connect()
    yield pool
    await database.close()


# ──────────────────────────────────────────────────────────────────────────────
# Test classes
# ──────────────────────────────────────────────────────────────────────────────


class TestFunctionExists:
    """Verify the SECURITY DEFINER function exists after core_145 migration."""

    async def test_function_created_in_public_schema(self, migration_pool: asyncpg.Pool) -> None:
        """public.resolve_owner_triple must exist after the core migration chain."""
        exists = await migration_pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname = 'resolve_owner_triple'
            )
            """
        )
        assert exists, "public.resolve_owner_triple must exist after core migration chain"

    async def test_function_is_security_definer(self, migration_pool: asyncpg.Pool) -> None:
        """The function must carry the SECURITY DEFINER attribute."""
        is_definer = await migration_pool.fetchval(
            """
            SELECT p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = 'resolve_owner_triple'
            """
        )
        assert is_definer is True, (
            "public.resolve_owner_triple must be SECURITY DEFINER so it runs as "
            "its owner (which has relationship-schema read access)"
        )

    async def test_function_returns_correct_columns(self, migration_pool: asyncpg.Pool) -> None:
        """Return type must expose entity_id (uuid) and is_primary (bool)."""
        # Call with empty candidates — returns no rows but proves the return-type
        # matches what the Python identity.py layer expects.
        rows = await migration_pool.fetch(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [],
        )
        assert rows == [], "Empty candidates must return zero rows (column access did not raise)"


class TestOwnerOnlyScoping:
    """Verify that only owner-entity facts are returned (owner-only scoping)."""

    async def test_owner_primary_handle_resolves(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Owner's primary handle resolves to the owner entity with is_primary=True."""
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_primary_handle"]],
        )
        assert row is not None, "Expected a row for the owner's primary handle"
        assert row["entity_id"] == seeded_data["owner_id"], "entity_id must be the owner entity"
        assert row["is_primary"] is True, "is_primary must be True for the primary handle"

    async def test_non_owner_handle_returns_nothing(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Non-owner entity's handle must NOT be returned (owner-only scoping).

        The non-owner entity has the same predicate (has-handle) but its sole
        per-entity aggregate has ``is_owner=false`` and must be refused.
        """
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["non_owner_handle"]],
        )
        assert row is None, (
            "resolve_owner_triple must NOT return non-owner entities; "
            f"got entity_id={row['entity_id'] if row else None}"
        )

    async def test_unsafe_owner_associations_return_nothing(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Ambiguous, inactive, merged, and deleted associations cannot authorize."""
        from butlers.modules.approvals.gate import _resolve_target_contact

        for predicate, value, failure_class in (
            ("has-email", seeded_data["ambiguous_email"], "ambiguous email"),
            ("has-handle", seeded_data["ambiguous_handle"], "ambiguous handle"),
            ("has-email", seeded_data["inactive_owner_email"], "inactive owner fact"),
        ):
            row = await migration_pool.fetchrow(
                "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
                predicate,
                [value],
            )
            assert row is None, (
                f"{failure_class} must fail closed instead of producing owner authorization"
            )

        try:
            for metadata, value, failure_class in (
                (
                    {"merged_into": "00000000-0000-0000-0000-000000000001"},
                    seeded_data["merged_owner_email"],
                    "merged owner entity",
                ),
                (
                    {"deleted_at": "2026-09-13T00:00:00Z"},
                    seeded_data["deleted_owner_email"],
                    "deleted owner entity",
                ),
            ):
                await migration_pool.execute(
                    "UPDATE public.entities SET metadata = $1::jsonb WHERE id = $2",
                    json.dumps(metadata),
                    seeded_data["owner_id"],
                )
                row = await migration_pool.fetchrow(
                    "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
                    "has-email",
                    [value],
                )
                assert row is None, (
                    f"{failure_class} must fail closed instead of producing owner authorization"
                )
                direct = await _resolve_target_contact(
                    migration_pool,
                    {"entity_id": str(seeded_data["owner_id"])},
                )
                assert direct is None, f"direct entity dispatch must also reject a {failure_class}"
        finally:
            await migration_pool.execute(
                "UPDATE public.entities SET metadata = '{}'::jsonb WHERE id = $1",
                seeded_data["owner_id"],
            )

    async def test_mixed_owner_and_non_owner_candidates_are_ambiguous(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Distinct owner/external normalized candidates fail closed as ambiguous."""
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_primary_handle"], seeded_data["non_owner_handle"]],
        )
        assert row is None, "Owner and external candidates must not collapse to an owner match"

    async def test_empty_candidates_returns_nothing(self, migration_pool: asyncpg.Pool) -> None:
        """Empty candidates array must return no rows."""
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [],
        )
        assert row is None, "Empty candidates must return NULL (no match possible)"

    async def test_unknown_predicate_returns_nothing(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Querying with an unregistered predicate must return no rows.

        The WHERE clause ``ef.predicate = p_predicate`` filters by exact match;
        an unknown predicate will produce zero rows regardless of the candidates.
        """
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "nonexistent-predicate",
            [seeded_data["owner_primary_handle"]],
        )
        assert row is None, "Unknown predicate must return NULL"


class TestIsPrimaryAggregation:
    """Verify is_primary aggregation after single-entity authorization.

    Multiple candidate facts may belong to the same owner without creating
    cross-entity ambiguity.  ``bool_or(ef."primary" IS TRUE)`` reports whether
    any matching fact for that sole entity is primary.
    """

    async def test_any_primary_fact_marks_the_sole_matching_owner_primary(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """One primary fact makes the sole matched owner's aggregate primary."""
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_secondary_handle"], seeded_data["owner_primary_handle"]],
        )
        assert row is not None, "Expected a row when both handles are candidates"
        assert row["is_primary"] is True, (
            "RFC 0017 §2.1: any primary candidate for the sole owner must be retained; "
            f"got is_primary={row['is_primary']}"
        )
        assert row["entity_id"] == seeded_data["owner_id"]

    async def test_secondary_handle_resolves_when_sole_candidate(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Secondary (non-primary) handle resolves correctly when it is the only candidate."""
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_secondary_handle"]],
        )
        assert row is not None, "Expected a row for the owner's secondary handle"
        assert row["entity_id"] == seeded_data["owner_id"]
        assert row["is_primary"] is False, "Secondary handle must carry is_primary=False"


class TestSchemaIsolation:
    """Verify the SECURITY DEFINER cross-schema privilege guarantee.

    A schema-isolated butler role (no USAGE on relationship schema, no SELECT
    on relationship.entity_facts) must be able to call
    public.resolve_owner_triple() via the EXECUTE grant added in core_145 and
    receive correct owner-only results — without the function leaking any
    broader relationship-schema read access to the caller.
    """

    async def test_isolated_role_blocked_from_direct_entity_facts_read(
        self, isolated_role_pool: asyncpg.Pool
    ) -> None:
        """Baseline: isolated role must NOT be able to SELECT from relationship.entity_facts.

        This confirms the privilege boundary is enforced before we rely on the
        SECURITY DEFINER path to prove it is crossed.
        """
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await isolated_role_pool.fetchval("SELECT count(*) FROM relationship.entity_facts")

    async def test_isolated_role_can_call_security_definer_function(
        self, isolated_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """SECURITY DEFINER allows the isolated role to resolve owner channels.

        The isolated role has no read access to relationship.entity_facts, but
        the SECURITY DEFINER function runs as its owner (which does have access).
        The result must be the owner entity with correct is_primary.
        """
        row = await isolated_role_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_primary_handle"]],
        )
        assert row is not None, (
            "Isolated role must be able to call public.resolve_owner_triple "
            "via the EXECUTE grant added in core_145 (SECURITY DEFINER mechanism)"
        )
        assert row["entity_id"] == seeded_data["owner_id"], (
            "SECURITY DEFINER must return the owner entity"
        )
        assert row["is_primary"] is True

    async def test_messenger_role_guards_allow_only_intended_owner_channels(
        self, messenger_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """The real Messenger role keeps the full owner/external matrix fail-closed."""
        assert await messenger_role_pool.fetchval("SELECT current_user") == "butler_messenger_rw"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await messenger_role_pool.fetchval("SELECT count(*) FROM relationship.entity_facts")

        wildcard_phone = await messenger_role_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "owner-channel",
            ["phone-digits:1555111____"],
        )
        assert wildcard_phone is None

        async def email_decision(target: str):
            return await check_email_recipient(
                messenger_role_pool,
                email_target=target,
                rule_tool_name="email_send_message",
                rule_match_args={"to": target},
                park_tool_name="email_send_message",
                park_tool_args={"to": target},
                park_summary="email authorization matrix",
                butler_name="messenger",
            )

        async def telegram_decision(target: str):
            return await check_recipient(
                messenger_role_pool,
                channel="telegram",
                target=target,
                rule_tool_name="telegram_send_message",
                rule_match_args={"chat_id": target},
                park_tool_name="telegram_send_message",
                park_tool_args={"chat_id": target},
                park_summary="telegram authorization matrix",
                butler_name="messenger",
            )

        async def whatsapp_decision(target: str):
            return await check_recipient(
                messenger_role_pool,
                channel="whatsapp",
                target=target,
                rule_tool_name="whatsapp_send_message",
                rule_match_args={"recipient": target},
                park_tool_name="whatsapp_send_message",
                park_tool_args={"recipient": target},
                park_summary="whatsapp authorization matrix",
                butler_name="messenger",
            )

        pending_before = await messenger_role_pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE status = 'pending'"
        )

        primary_email = await email_decision(seeded_data["owner_primary_email"])
        assert (primary_email.allowed, primary_email.reason) == (True, "owner")

        owner_telegram = await telegram_decision("owner_secondary_54321")
        assert (owner_telegram.allowed, owner_telegram.reason) == (True, "owner")

        owner_whatsapp = await whatsapp_decision(seeded_data["owner_secondary_whatsapp"])
        assert (owner_whatsapp.allowed, owner_whatsapp.reason) == (True, "owner")

        for target in (
            seeded_data["non_owner_email"],
            seeded_data["ambiguous_email"],
            seeded_data["case_ambiguous_email"].upper(),
            seeded_data["malformed_owner_email"],
        ):
            decision = await email_decision(target)
            assert (decision.allowed, decision.reason) == (False, "parked")

        ambiguous_telegram = await telegram_decision(seeded_data["normalized_ambiguous_handle"])
        assert (ambiguous_telegram.allowed, ambiguous_telegram.reason) == (False, "parked")
        malformed_telegram = await telegram_decision(seeded_data["malformed_owner_telegram"])
        assert (malformed_telegram.allowed, malformed_telegram.reason) == (False, "parked")
        secondary_email = await email_decision(seeded_data["owner_secondary_email"])
        assert (secondary_email.allowed, secondary_email.reason) == (True, "owner")

        external_whatsapp = await whatsapp_decision(seeded_data["non_owner_whatsapp"])
        assert (external_whatsapp.allowed, external_whatsapp.reason) == (False, "parked")
        malformed_whatsapp = await whatsapp_decision(seeded_data["malformed_owner_whatsapp"])
        assert (malformed_whatsapp.allowed, malformed_whatsapp.reason) == (False, "parked")
        ambiguous_whatsapp = await whatsapp_decision(seeded_data["normalized_ambiguous_whatsapp"])
        assert (ambiguous_whatsapp.allowed, ambiguous_whatsapp.reason) == (False, "parked")

        pending_after = await messenger_role_pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE status = 'pending'"
        )

        assert pending_after - pending_before == 9

    async def test_messenger_role_email_wrapper_allows_secondary_owner_address(
        self, messenger_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """The production MCP wrapper authorizes every unique owner email."""

        sent_to: list[str] = []

        async def send_email(to: str, subject: str, body: str) -> dict[str, str]:
            sent_to.append(to)
            return {"status": "sent", "to": to, "subject": subject, "body": body}

        wrapper = _make_gate_wrapper(
            tool_name="email_send_message",
            original_fn=send_email,
            pool=messenger_role_pool,
            expiry_hours=72,
            risk_tier=ApprovalRiskTier.MEDIUM,
            rule_precedence=("contact_role", "standing_rule"),
            butler_name="messenger",
        )

        primary = await wrapper(
            to=seeded_data["owner_primary_email"],
            subject="Owner delivery",
            body="Primary address",
        )
        assert primary["status"] == "sent"

        secondary = await wrapper(
            to=seeded_data["owner_secondary_email"],
            subject="Owner delivery",
            body="Secondary address",
        )
        assert secondary["status"] == "sent"

        for target in (seeded_data["non_owner_email"], seeded_data["ambiguous_email"]):
            blocked = await wrapper(
                to=target,
                subject="External delivery",
                body="Requires review",
                _why="The target is not uniquely associated with the owner.",
                _evidence=[],
            )
            assert blocked["status"] == "pending_approval"

        assert sent_to == [
            seeded_data["owner_primary_email"],
            seeded_data["owner_secondary_email"],
        ]

    async def test_isolated_role_owner_only_scoping_still_enforced(
        self, isolated_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """SECURITY DEFINER does NOT expose non-owner facts — owner scoping is SQL-enforced.

        After proving there is only one live matched entity, the function's
        aggregated ``is_owner`` condition refuses a non-owner result.  Even via
        SECURITY DEFINER the isolated role cannot retrieve non-owner facts.
        """
        row = await isolated_role_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["non_owner_handle"]],
        )
        assert row is None, (
            "SECURITY DEFINER function must apply owner-only scoping; "
            "non-owner handle must return NULL even when called from an isolated role"
        )

    async def test_isolated_role_is_primary_aggregation_preserved(
        self, isolated_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """is_primary aggregation is correct when called from the isolated role.

        Candidate facts for one owner are aggregated inside the definer even
        when the caller has no direct schema access.
        """
        row = await isolated_role_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_secondary_handle"], seeded_data["owner_primary_handle"]],
        )
        assert row is not None
        assert row["is_primary"] is True, (
            "A primary fact for the sole owner must survive the isolated-role path"
        )


async def test_core_233_downgrade_and_reapply_restores_function_behavior(
    postgres_container,
) -> None:
    """The bounded migration lifecycle restores, then reapplies, ambiguity safety."""
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "relationship"],
        {"relationship": "relationship"},
        {"core": "core_233"},
    )
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        owner_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, $2) RETURNING id",
            "Lifecycle Owner",
            ["owner"],
        )
        external_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, $2) RETURNING id",
            "Lifecycle External",
            [],
        )
        await pool.executemany(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, "primary", validity)
            VALUES ($1, 'has-email', 'collision@example.test', 'literal', 'test', $2, 'active')
            """,
            [(owner_id, True), (external_id, False)],
        )
        assert (
            await pool.fetchrow(
                "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
                "has-email",
                ["collision@example.test"],
            )
            is None
        )
    finally:
        await pool.close()

    config = _build_alembic_config(db_url, chains=["core"])
    await asyncio.to_thread(command.downgrade, config, "core_232")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        prior_result = await pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-email",
            ["collision@example.test"],
        )
        assert prior_result is not None
        assert prior_result["entity_id"] == owner_id
    finally:
        await pool.close()

    await asyncio.to_thread(command.upgrade, config, "core@core_233")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        assert (
            await pool.fetchrow(
                "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
                "has-email",
                ["collision@example.test"],
            )
            is None
        )
    finally:
        await pool.close()


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint custody holds (core_293, bu-s11n0s.1; core_295, bu-s11n0s.25; core_296, bu-s11n0s.29)
# ──────────────────────────────────────────────────────────────────────────────

_HOLD_INSERT = """
INSERT INTO public.custody_holds
    (owner_entity_id, target_kind, target_digest, generation, episode_id, reason,
     source_command_id)
VALUES ($1, $2, public.custody_target_digest($2, $3), $4, gen_random_uuid(), 'lost',
        gen_random_uuid())
RETURNING id
"""


async def _release(pool: asyncpg.Pool, hold_id) -> None:
    await pool.execute("UPDATE public.custody_holds SET released_at = now() WHERE id = $1", hold_id)


class TestEndpointCustodyHolds:
    """REQ-endpoint-custody-holds-006: active holds refuse owner resolution.

    REQ-endpoint-custody-holds-002: runtime roles cannot forge hold lifecycle.
    """

    async def test_held_owner_endpoints_stop_resolving_until_release(
        self,
        migration_pool: asyncpg.Pool,
        isolated_role_pool: asyncpg.Pool,
        seeded_data: dict,
    ) -> None:
        """Email, Telegram and WhatsApp-by-phone holds refuse owner; release restores."""
        from butlers.identity import resolve_owner_channel_via_definer

        owner_id = seeded_data["owner_id"]
        await migration_pool.executemany(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, "primary", validity)
            VALUES ($1, $2, $3, 'literal', 'test', true, 'active')
            """,
            [
                (owner_id, "has-email", "Held.Owner@Example.Test"),
                (owner_id, "has-handle", "telegram:900100200"),
            ],
        )
        targets = (
            ("email", "held.owner@example.test", "has-email", "held.owner@example.test"),
            ("telegram_bot", "900100200", "has-handle", "telegram:900100200"),
            ("whatsapp_jid", "15551112222@s.whatsapp.net", "has-phone", "15551112222"),
        )

        async def resolves(channel: str, value: str) -> bool:
            result = await resolve_owner_channel_via_definer(
                isolated_role_pool, channel, value, raise_on_error=True
            )
            return result is not None and result[0].entity_id == owner_id

        for channel, value, _, _ in targets:
            assert await resolves(channel, value), channel

        holds = [
            await migration_pool.fetchval(_HOLD_INSERT, owner_id, kind, key, 1)
            for _, _, kind, key in targets
        ]
        try:
            for channel, value, _, _ in targets:
                assert not await resolves(channel, value), channel
            # An unheld sibling endpoint of the same owner keeps resolving.
            assert await resolves("email", seeded_data["owner_primary_email"])
            # Held means not owner, not ambiguous: the content-blind read agrees.
            assert await isolated_role_pool.fetchval(
                "SELECT public.custody_target_held('has-email', $1)",
                "Held.Owner@Example.Test",
            )
        finally:
            for hold_id in holds:
                await _release(migration_pool, hold_id)

        for channel, value, _, _ in targets:
            assert await resolves(channel, value), channel

        # Generations are monotonic per target and never reset on rehold.
        with pytest.raises(asyncpg.PostgresError, match="next generation"):
            await migration_pool.fetchval(
                _HOLD_INSERT, owner_id, "has-email", "held.owner@example.test", 1
            )
        rehold = await migration_pool.fetchval(
            _HOLD_INSERT, owner_id, "has-email", "held.owner@example.test", 2
        )
        assert not await resolves("email", "held.owner@example.test")
        await _release(migration_pool, rehold)

    async def test_hold_history_is_owner_maintained_and_immutable(
        self,
        migration_pool: asyncpg.Pool,
        messenger_role_pool: asyncpg.Pool,
        seeded_data: dict,
    ) -> None:
        """Runtime roles cannot read, forge, release or delete; history never rewinds."""
        owner_id = seeded_data["owner_id"]
        hold_id = await migration_pool.fetchval(
            _HOLD_INSERT, owner_id, "has-handle", "telegram:immutable-hold-1", 1
        )
        try:
            # Denied outright, or (after a bootstrap DML regrant) zero rows under RLS.
            try:
                visible = await messenger_role_pool.fetchval(
                    "SELECT count(*) FROM public.custody_holds"
                )
            except asyncpg.PostgresError:
                visible = 0
            assert visible == 0
            with pytest.raises(asyncpg.PostgresError):
                await messenger_role_pool.fetchval(
                    _HOLD_INSERT, owner_id, "has-handle", "telegram:forged-hold-1", 1
                )
            for statement in (
                "UPDATE public.custody_holds SET released_at = now() WHERE id = $1",
                "DELETE FROM public.custody_holds WHERE id = $1",
            ):
                try:
                    await messenger_role_pool.execute(statement, hold_id)
                except asyncpg.PostgresError:
                    pass
            row = await migration_pool.fetchrow(
                "SELECT generation, released_at FROM public.custody_holds WHERE id = $1",
                hold_id,
            )
            assert row is not None and row["released_at"] is None
            assert (
                await migration_pool.fetchval(
                    "SELECT count(*) FROM public.custody_holds "
                    "WHERE target_digest = "
                    "public.custody_target_digest('has-handle', 'telegram:forged-hold-1')"
                )
                == 0
            )

            for statement in (
                "DELETE FROM public.custody_holds WHERE id = $1",
                "UPDATE public.custody_holds SET generation = 9 WHERE id = $1",
            ):
                with pytest.raises(asyncpg.PostgresError):
                    await migration_pool.execute(statement, hold_id)
            with pytest.raises(asyncpg.PostgresError):
                await migration_pool.execute("TRUNCATE public.custody_holds")
            # TRUNCATE ... CASCADE follows foreign keys only, so an entity
            # reset can never reach hold history; the guard checks the owner.
            assert (
                await migration_pool.fetchval(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE contype = 'f' AND (conrelid = 'public.custody_holds'::regclass "
                    "OR confrelid = 'public.custody_holds'::regclass)"
                )
                == 0
            )
            with pytest.raises(asyncpg.PostgresError, match="existing owner entity"):
                await migration_pool.fetchval(
                    _HOLD_INSERT, uuid.uuid4(), "has-handle", "telegram:orphan-hold-1", 1
                )
        finally:
            await _release(migration_pool, hold_id)
        with pytest.raises(asyncpg.PostgresError, match="released once"):
            await migration_pool.execute(
                "UPDATE public.custody_holds SET released_at = NULL WHERE id = $1",
                hold_id,
            )

    async def test_hold_covers_every_equivalent_owner_phone_spelling(
        self,
        migration_pool: asyncpg.Pool,
        isolated_role_pool: asyncpg.Pool,
        seeded_data: dict,
    ) -> None:
        """A held phone also demotes the owner's other spellings of that number (core_295).

        '+65 8765 0123' and '87650123' digest differently, and the candidate
        '0087650123' suffix-matches only the unheld local spelling. The hold must
        still refuse owner, even after the held spelling stops being active.
        """
        owner_id = seeded_data["owner_id"]
        fact_ids = [
            await migration_pool.fetchval(
                """
                INSERT INTO relationship.entity_facts
                    (subject, predicate, object, object_kind, src, "primary", validity)
                VALUES ($1, 'has-phone', $2, 'literal', 'test', false, 'active')
                RETURNING id
                """,
                owner_id,
                spelling,
            )
            for spelling in ("+65 8765 0123", "87650123")
        ]

        async def resolves(digits: str) -> bool:
            row = await isolated_role_pool.fetchrow(
                "SELECT entity_id FROM public.resolve_owner_triple($1, $2)",
                "owner-channel",
                [f"phone-digits:{digits}"],
            )
            return row is not None and row["entity_id"] == owner_id

        assert await resolves("0087650123")
        hold_id = await migration_pool.fetchval(
            _HOLD_INSERT, owner_id, "has-phone", "+65 8765 0123", 1
        )
        try:
            assert not await resolves("0087650123")
            assert not await resolves("87650123")
            await migration_pool.execute(
                "UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = $1",
                fact_ids[0],
            )
            assert not await resolves("0087650123")
        finally:
            await _release(migration_pool, hold_id)
            await migration_pool.execute(
                "DELETE FROM relationship.entity_facts WHERE id = ANY($1::uuid[])", fact_ids
            )

    async def test_hold_covers_spellings_beyond_the_candidate_length_window(
        self,
        migration_pool: asyncpg.Pool,
        isolated_role_pool: asyncpg.Pool,
        seeded_data: dict,
    ) -> None:
        """Hold coverage is transitive across spellings of one number (core_296).

        '0065 8765 0123' (12 digits) and '87650123' (8 digits) are too far apart
        for the candidate suffix rule to relate them directly, yet the candidate
        '87650123' matches the unheld spelling. Every owner phone sharing the
        trailing eight digits stays held; a different number keeps resolving.
        """
        owner_id = seeded_data["owner_id"]
        fact_ids = [
            await migration_pool.fetchval(
                """
                INSERT INTO relationship.entity_facts
                    (subject, predicate, object, object_kind, src, "primary", validity)
                VALUES ($1, 'has-phone', $2, 'literal', 'test', false, 'active')
                RETURNING id
                """,
                owner_id,
                spelling,
            )
            for spelling in ("0065 8765 0123", "87650123", "+65 9123 4567")
        ]

        async def resolves(digits: str) -> bool:
            row = await isolated_role_pool.fetchrow(
                "SELECT entity_id FROM public.resolve_owner_triple($1, $2)",
                "owner-channel",
                [f"phone-digits:{digits}"],
            )
            return row is not None and row["entity_id"] == owner_id

        try:
            assert await resolves("87650123")
            hold_id = await migration_pool.fetchval(
                _HOLD_INSERT, owner_id, "has-phone", "0065 8765 0123", 1
            )
            try:
                assert not await resolves("87650123")
                assert not await resolves("6587650123")
                assert await resolves("6591234567")
            finally:
                await _release(migration_pool, hold_id)
            assert await resolves("87650123")
        finally:
            await migration_pool.execute(
                "DELETE FROM relationship.entity_facts WHERE id = ANY($1::uuid[])", fact_ids
            )

    async def test_legacy_predicate_mode_never_resolves_an_unholdable_fact(
        self,
        migration_pool: asyncpg.Pool,
        isolated_role_pool: asyncpg.Pool,
        seeded_data: dict,
    ) -> None:
        """Exact legacy predicates outside the custody universe cannot carry owner (core_295)."""
        owner_id = seeded_data["owner_id"]
        fact_id = await migration_pool.fetchval(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, "primary", validity)
            VALUES ($1, 'has-legacy-owner-id', 'legacy-owner-1', 'literal', 'test', true,
                    'active')
            RETURNING id
            """,
            owner_id,
        )
        try:
            assert (
                await isolated_role_pool.fetchrow(
                    "SELECT entity_id FROM public.resolve_owner_triple($1, $2)",
                    "has-legacy-owner-id",
                    ["legacy-owner-1"],
                )
                is None
            )
        finally:
            await migration_pool.execute(
                "DELETE FROM relationship.entity_facts WHERE id = $1", fact_id
            )


async def test_core_295_moves_custody_functions_to_the_maintenance_owner(
    postgres_container,
) -> None:
    """Only the trusted database owner may replace a function a hold check calls.

    The migration executor is deliberately not the database owner here, so the
    assertion fails while any custody function stays with the executor.
    """
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "relationship"],
        {"relationship": "relationship"},
        {"core": "core_293"},
    )
    executor = urlparse(db_url).username
    db_name = urlparse(db_url).path.lstrip("/")
    trusted_owner = f"custody_owner_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(
        postgres_container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    )
    try:
        await admin.execute(f'CREATE ROLE "{trusted_owner}" NOLOGIN')
        await admin.execute(f'GRANT "{trusted_owner}" TO "{executor}"')
        await admin.execute(f'ALTER DATABASE "{db_name}" OWNER TO "{trusted_owner}"')
        # The executor no longer owns the database; keep its ordinary access.
        await admin.execute(f'GRANT CONNECT, TEMPORARY ON DATABASE "{db_name}" TO "{executor}"')
    finally:
        await admin.close()

    config = _build_alembic_config(db_url, chains=["core"])
    await asyncio.to_thread(command.upgrade, config, "core@head")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        owners = dict(
            await pool.fetch(
                """
                SELECT p.proname, pg_get_userbyid(p.proowner)
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname IN (
                      'custody_target_digest', 'guard_custody_holds', 'custody_target_held'
                  )
                """
            )
        )
        owners["custody_holds"] = await pool.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid = 'public.custody_holds'::regclass"
        )
        assert owners == dict.fromkeys(
            (
                "custody_target_digest",
                "guard_custody_holds",
                "custody_target_held",
                "custody_holds",
            ),
            trusted_owner,
        )
    finally:
        await pool.close()


async def test_core_293_downgrade_refuses_active_hold_and_reapplies(postgres_container) -> None:
    """A rollback never silently restores trust in a held endpoint."""
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "relationship"],
        {"relationship": "relationship"},
        {"core": "core_293"},
    )
    config = _build_alembic_config(db_url, chains=["core"])
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        owner_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, $2) RETURNING id",
            "Custody Owner",
            ["owner"],
        )
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, "primary", validity)
            VALUES ($1, 'has-email', 'custody@example.test', 'literal', 'test', true, 'active')
            """,
            owner_id,
        )
        hold_id = await pool.fetchval(
            _HOLD_INSERT, owner_id, "has-email", "custody@example.test", 1
        )
    finally:
        await pool.close()

    with pytest.raises(Exception, match="hold is active"):
        await asyncio.to_thread(command.downgrade, config, "core_291")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        assert (
            await pool.fetchrow(
                "SELECT entity_id FROM public.resolve_owner_triple($1, $2)",
                "owner-channel",
                ["has-email:custody@example.test"],
            )
            is None
        )
        await _release(pool, hold_id)
    finally:
        await pool.close()

    await asyncio.to_thread(command.downgrade, config, "core_291")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        assert await pool.fetchval("SELECT to_regclass('public.custody_holds')") is None
        assert (
            await pool.fetchval(
                "SELECT entity_id FROM public.resolve_owner_triple($1, $2)",
                "owner-channel",
                ["has-email:custody@example.test"],
            )
            == owner_id
        )
    finally:
        await pool.close()

    await asyncio.to_thread(command.upgrade, config, "core@core_293")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        assert await pool.fetchval("SELECT count(*) FROM public.custody_holds") == 0
        assert (
            await pool.fetchval(
                "SELECT entity_id FROM public.resolve_owner_triple($1, $2)",
                "owner-channel",
                ["has-email:custody@example.test"],
            )
            == owner_id
        )
    finally:
        await pool.close()
