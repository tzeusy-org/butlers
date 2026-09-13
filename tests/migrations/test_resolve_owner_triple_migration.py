"""Integration test: public.resolve_owner_triple SECURITY DEFINER (core_145/core_233).

Closes the gap identified in bu-6ui2o: the mocked-pool unit tests in
``test_gate.py`` and ``test_identity_resolution.py`` cover the Python layer
but leave the actual SQL + cross-SET-ROLE privilege boundary unverified.

Verified:
1. Function exists and is callable after the core migration chain.
2. Owner-only scoping — non-owner entities are never returned even when they
   share the same predicate and candidate value list.
3. ``is_primary`` ordering (RFC 0017 §2.1) — primary channels sort first when
   multiple owner handles are provided as candidates.
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

import shutil
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.db import Database
from butlers.modules.approvals.email_guard import check_email_recipient
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
    """Provision a DB with core + relationship chains (includes core_145).

    The ``core`` chain creates ``public.entities`` and
    ``public.resolve_owner_triple`` (core_145).  The ``relationship`` chain
    creates ``relationship.entity_facts`` in the ``relationship`` schema.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "relationship"],
        schemas={"relationship": "relationship"},
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

    owner_primary_handle = "telegram:owner-primary-12345"
    owner_secondary_handle = "telegram:owner-secondary-54321"
    non_owner_handle = "telegram:non-owner-99999"
    owner_primary_email = "owner-primary@example.test"
    ambiguous_email = "ambiguous@example.test"
    ambiguous_handle = "telegram:ambiguous-11111"

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
            (owner_id, ambiguous_email, True),
            (non_owner_id, ambiguous_email, False),
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
        ],
    )

    return {
        "owner_id": owner_id,
        "non_owner_id": non_owner_id,
        "owner_primary_handle": owner_primary_handle,
        "owner_secondary_handle": owner_secondary_handle,
        "non_owner_handle": non_owner_handle,
        "owner_primary_email": owner_primary_email,
        "ambiguous_email": ambiguous_email,
        "ambiguous_handle": ambiguous_handle,
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
        strict_role_enforcement=True,
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

        The non-owner entity has the same predicate (has-handle) but is not in
        the owner role.  The WHERE clause ``'owner' = ANY(e.roles)`` must
        exclude it entirely.
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

    async def test_owner_and_external_channel_collisions_return_nothing(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """Email and Telegram collisions are ambiguous, not owner-safe."""
        for predicate, value in (
            ("has-email", seeded_data["ambiguous_email"]),
            ("has-handle", seeded_data["ambiguous_handle"]),
        ):
            row = await migration_pool.fetchrow(
                "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
                predicate,
                [value],
            )
            assert row is None, (
                f"A {predicate} identifier attached to owner and external entities must "
                "fail closed; owner-only filtering must not erase the ambiguity"
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


class TestIsPrimaryOrdering:
    """Verify is_primary ordering (RFC 0017 §2.1).

    The function uses ``ORDER BY ef."primary" DESC NULLS LAST LIMIT 1``, so
    when multiple owner handles match, the primary one must be returned.
    """

    async def test_primary_handle_preferred_when_both_are_candidates(
        self, migration_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """When both primary and non-primary owner handles are candidates,
        the primary handle is returned (RFC 0017 §2.1).
        """
        row = await migration_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            # Order in the list must not matter; SQL ORDER BY "primary" DESC wins.
            [seeded_data["owner_secondary_handle"], seeded_data["owner_primary_handle"]],
        )
        assert row is not None, "Expected a row when both handles are candidates"
        assert row["is_primary"] is True, (
            "RFC 0017 §2.1: primary channel must be preferred over non-primary; "
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

    async def test_messenger_role_email_guard_allows_only_primary_owner_fallback(
        self, messenger_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """The real Messenger role can authorize an exact primary owner email only."""
        assert await messenger_role_pool.fetchval("SELECT current_user") == "butler_messenger_rw"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await messenger_role_pool.fetchval("SELECT count(*) FROM relationship.entity_facts")

        decision = await check_email_recipient(
            messenger_role_pool,
            email_target=seeded_data["owner_primary_email"],
            rule_tool_name="email_send_message",
            rule_match_args={"to": seeded_data["owner_primary_email"]},
            park_tool_name="email_send_message",
            park_tool_args={"to": seeded_data["owner_primary_email"]},
            park_summary="primary owner email",
        )

        assert decision.allowed is True
        assert decision.reason == "owner"

    async def test_isolated_role_owner_only_scoping_still_enforced(
        self, isolated_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """SECURITY DEFINER does NOT expose non-owner facts — owner scoping is SQL-enforced.

        The function body's WHERE clause (``'owner' = ANY(e.roles)``) filters
        results to the owner entity only.  Even via SECURITY DEFINER the
        isolated role cannot retrieve non-owner facts.
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

    async def test_isolated_role_is_primary_ordering_preserved(
        self, isolated_role_pool: asyncpg.Pool, seeded_data: dict
    ) -> None:
        """is_primary ordering is correct when called from the isolated role.

        Confirms that the full ORDER BY "primary" DESC NULLS LAST logic executes
        correctly even when the caller has no direct schema access.
        """
        row = await isolated_role_pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            "has-handle",
            [seeded_data["owner_secondary_handle"], seeded_data["owner_primary_handle"]],
        )
        assert row is not None
        assert row["is_primary"] is True, (
            "Primary handle must be preferred over secondary even via isolated role path"
        )
