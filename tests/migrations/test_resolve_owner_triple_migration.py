"""Integration test: public.resolve_owner_triple SECURITY DEFINER (core_145/core_233).

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
   the exact source-manifest EXECUTE capability and receives correct owner-only results.
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
from urllib.parse import urlparse

import asyncpg
import pytest

from alembic import command
from butlers.config import ApprovalRiskTier
from butlers.migrations import _build_alembic_config
from butlers.modules.approvals.email_guard import check_email_recipient, check_recipient
from butlers.modules.approvals.gate import _make_gate_wrapper
from butlers.testing.migration import (
    async_serving_test_deployment,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
    run_test_alembic_command,
)

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


@pytest.fixture(scope="module")
async def lookup_deployment(postgres_container):
    """Real source-declared identities; owned pools close before DB teardown."""
    async with async_serving_test_deployment(postgres_container) as deployment:
        yield deployment


@pytest.fixture(scope="module")
async def migrated_db_url(lookup_deployment) -> str:
    return lookup_deployment.migration_url


@pytest.fixture(scope="module")
async def lookup_maintenance_pool(postgres_container, lookup_deployment):
    """Independent disposable maintenance is used only for seed/mutation/readback."""
    pool = await asyncpg.create_pool(
        migration_bootstrap_db_url(postgres_container, "butlers"), min_size=1, max_size=2
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(scope="module")
async def migration_pool(lookup_deployment) -> asyncpg.Pool:
    """Retained test parameter now borrows the actual admitted General runtime."""
    database = lookup_deployment.runtime_database("general")
    selected = lookup_deployment.runtime_logins["general"]
    pool = await database.connect()
    try:
        identity = await pool.fetchrow("SELECT current_database(),session_user,current_user")
        assert tuple(identity) == ("butlers", selected.user, selected.role)
        assert selected.user != urlparse(lookup_deployment.migration_url).username
        assert (
            await pool.fetchval(
                "SELECT pg_has_role(session_user, $1, 'MEMBER')",
                urlparse(lookup_deployment.migration_url).username,
            )
            is False
        )
        yield pool
    finally:
        await database.close()


@pytest.fixture(scope="module")
async def seeded_data(lookup_maintenance_pool: asyncpg.Pool) -> dict:
    """Seed owner + non-owner entities and matching ``entity_facts`` rows.

    Returns a dict with the entity UUIDs and the handle values used in tests.
    Owner has two handles: one primary, one non-primary.  The non-owner has
    one handle with the same predicate (to confirm owner-only scoping).
    """
    migration_pool = lookup_maintenance_pool
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
async def isolated_role_pool(migration_pool) -> asyncpg.Pool:
    """General is a real lookup executor and has no direct Relationship reads."""
    yield migration_pool


@pytest.fixture(scope="module")
async def messenger_role_pool(lookup_deployment) -> asyncpg.Pool:
    database = lookup_deployment.runtime_database("messenger")
    selected = lookup_deployment.runtime_logins["messenger"]
    pool = await database.connect()
    try:
        identity = await pool.fetchrow("SELECT current_database(),session_user,current_user")
        assert tuple(identity) == ("butlers", selected.user, selected.role)
        yield pool
    finally:
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
        self, migration_pool: asyncpg.Pool, seeded_data: dict, lookup_maintenance_pool
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
                await lookup_maintenance_pool.execute(
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
            await lookup_maintenance_pool.execute(
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
    lookup_deployment,
    lookup_maintenance_pool,
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
    await asyncio.to_thread(
        run_test_alembic_command, postgres_container, command.downgrade, config, "core_232"
    )

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

    await asyncio.to_thread(
        run_test_alembic_command, postgres_container, command.upgrade, config, "core@core_233"
    )

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

    # A separate source-produced canonical deployment preserves the original
    # random core233→232→233 outcome above. No hand ownership/ACL changes
    # manufacture this finalized non-head Relationship state.
    from butlers.db_authority import grants
    from butlers.db_authority.roles import runtime_profiles
    from butlers.migrations import get_chain_head
    from butlers.testing.migration import (
        async_migrate_test_database,
        async_run_test_alembic_command,
    )

    current_url = lookup_deployment.migration_url
    independent_url = migration_bootstrap_db_url(postgres_container, "butlers")
    profiles = runtime_profiles("butlers")
    full_plans = grants.deployment_function_plans(profiles)
    assert ("core", "public", None) not in full_plans
    relationship_config = _build_alembic_config(
        current_url, chains=["relationship"], target_schema="relationship"
    )
    public_config = _build_alembic_config(current_url, chains=["core"], target_schema="public")

    async def observe_lookup():
        # Independent physical observer closes before the next migration call.
        observer = await asyncpg.connect(independent_url)
        try:
            assert await observer.fetchval("SELECT current_database()") == "butlers"
            row = await observer.fetchrow("""
                SELECT p.oid,pg_get_userbyid(p.proowner) AS owner,p.prosecdef,
                       p.proconfig,md5(p.prosrc) AS body,
                       ARRAY(SELECT (CASE WHEN a.grantee=0 THEN 'PUBLIC'
                           ELSE pg_get_userbyid(a.grantee) END)||':'||a.privilege_type||':'||a.is_grantable
                           FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                           WHERE a.grantee<>p.proowner ORDER BY 1) AS acl
                FROM pg_proc p WHERE p.oid='public.resolve_owner_triple(text,text[])'::regprocedure
            """)
            assert row["owner"] == "identity_lookup_owner" and row["prosecdef"] is True
            assert row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
            return tuple(row)
        finally:
            await observer.close()

    before = await observe_lookup()
    # The real rel037 guard remains authoritative: this test may not erase any
    # classified/candidate/decision data merely to obtain a successful rollback.
    assert (
        await lookup_maintenance_pool.fetchval("""
        SELECT NOT EXISTS(SELECT FROM relationship.entity_facts WHERE
            content_authority IS NOT NULL OR authority_original_entity_id IS NOT NULL
            OR confirmed_at IS NOT NULL OR validity='candidate')
          AND NOT EXISTS(SELECT FROM relationship.fact_approval_context WHERE
            frozen_report IS NOT NULL OR decision_report IS NOT NULL)
          AND NOT EXISTS(SELECT FROM relationship.fact_identity_decisions)
          AND NOT EXISTS(SELECT FROM relationship.fact_approval_rule_context)
    """)
        is True
    )
    downgraded = False
    try:
        await async_run_test_alembic_command(
            postgres_container, command.downgrade, relationship_config, "rel_036"
        )
        downgraded = True
        assert (
            await lookup_maintenance_pool.fetchval(
                "SELECT count(*)=1 FROM relationship.alembic_version WHERE version_num='rel_036'"
            )
            is True
        )
        assert (
            await lookup_maintenance_pool.fetchval(
                "SELECT to_regclass('relationship.fact_identity_decisions') IS NULL"
            )
            is True
        )
        assert await observe_lookup() == before
        # Public is an actual source-admitted NEW ordinary core target. Its
        # original 145/233 consumers now encounter the existing purpose lookup
        # while the real Relationship source stamp is non-head.
        from butlers.db_authority.roles import AuthorityError

        assert (
            await lookup_maintenance_pool.fetchval("""
            SELECT NOT EXISTS(SELECT FROM pg_attribute att
              CROSS JOIN LATERAL aclexplode(att.attacl) a
              WHERE att.attrelid='public.model_catalog'::regclass
                AND att.attname='id' AND a.grantee=0)
        """)
            is True
        )
        await lookup_maintenance_pool.execute("GRANT SELECT(id) ON public.model_catalog TO PUBLIC")
        try:
            with pytest.raises(AuthorityError):
                await async_run_test_alembic_command(
                    postgres_container, command.upgrade, public_config, "core@head"
                )
        finally:
            await lookup_maintenance_pool.execute(
                "REVOKE SELECT(id) ON public.model_catalog FROM PUBLIC"
            )
        assert await observe_lookup() == before
        assert (
            await lookup_maintenance_pool.fetchval("""
            SELECT NOT EXISTS(SELECT FROM pg_attribute att
              CROSS JOIN LATERAL aclexplode(att.attacl) a
              WHERE att.attrelid='public.model_catalog'::regclass
                AND att.attname='id' AND a.grantee=0)
        """)
            is True
        )
        await async_run_test_alembic_command(
            postgres_container, command.upgrade, public_config, "core@head"
        )
        assert (
            await lookup_maintenance_pool.fetchval(
                "SELECT count(*)=1 FROM public.alembic_version WHERE version_num=$1",
                get_chain_head("core"),
            )
            is True
        )
        assert await observe_lookup() == before
    finally:
        if downgraded:
            await async_run_test_alembic_command(
                postgres_container, command.upgrade, relationship_config, "relationship@head"
            )
            await async_migrate_test_database(
                postgres_container,
                current_url,
                plans=(),
                final_plans=(*full_plans, ("core", "public", None)),
            )
    # Serving admission belongs AFTER the actual head producers/full finalizer,
    # not to the temporary administrative rel036 state.
    database = lookup_deployment.runtime_database("general")
    try:
        admitted = await database.connect()
        assert await admitted.fetchval("SELECT current_database()") == "butlers"
        assert await observe_lookup() == before
    finally:
        await database.close()
