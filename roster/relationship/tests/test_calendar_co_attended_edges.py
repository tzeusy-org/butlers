"""Tests for co-attended edge derivation in run_interaction_sync.

Validates that interaction_sync mints ``co-attended`` edges in
``relationship.entity_facts`` for entities that share a calendar event, and
that entities in *different* events do NOT receive cross-event edges.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Schema SQL (minimal subset needed for co-attended edge tests)
# ---------------------------------------------------------------------------

_CREATE_PUBLIC_ENTITIES_SQL = """
CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      TEXT        NOT NULL DEFAULT '',
    canonical_name VARCHAR     NOT NULL DEFAULT '',
    name           TEXT        NOT NULL DEFAULT '',
    entity_type    VARCHAR     NOT NULL DEFAULT 'other',
    aliases        TEXT[]      NOT NULL DEFAULT '{}',
    metadata       JSONB       DEFAULT '{}'::jsonb,
    roles          TEXT[]      NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_PUBLIC_CONTACTS_SQL = """
CREATE TABLE IF NOT EXISTS public.contacts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT,
    last_name  TEXT,
    entity_id  UUID REFERENCES public.entities(id) ON DELETE SET NULL
)
"""

_CREATE_PUBLIC_CONTACT_INFO_SQL = """
CREATE TABLE IF NOT EXISTS public.contact_info (
    id         UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID    NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT false
)
"""

_CREATE_SWITCHBOARD_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS switchboard"

_CREATE_MESSAGE_INBOX_SQL = """
CREATE TABLE IF NOT EXISTS switchboard.message_inbox (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_context JSONB       NOT NULL DEFAULT '{}',
    direction       TEXT        NOT NULL DEFAULT 'inbound',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_CALENDAR_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS public.calendar_events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT        NOT NULL,
    description TEXT,
    timezone    TEXT        NOT NULL DEFAULT 'UTC',
    starts_at   TIMESTAMPTZ NOT NULL,
    ends_at     TIMESTAMPTZ NOT NULL,
    all_day     BOOLEAN     NOT NULL DEFAULT false,
    status      TEXT        NOT NULL DEFAULT 'confirmed',
    visibility  TEXT        NOT NULL DEFAULT 'default',
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cal_ev_status_check CHECK (status IN ('confirmed', 'tentative', 'cancelled'))
)
"""

_CREATE_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    subject                 TEXT        NOT NULL,
    predicate               TEXT        NOT NULL,
    content                 TEXT        NOT NULL DEFAULT '',
    embedding               TEXT,
    search_vector           tsvector,
    importance              FLOAT       NOT NULL DEFAULT 5.0,
    confidence              FLOAT       NOT NULL DEFAULT 1.0,
    decay_rate              FLOAT       NOT NULL DEFAULT 0.008,
    permanence              TEXT        NOT NULL DEFAULT 'standard',
    source_butler           TEXT,
    source_episode_id       UUID,
    supersedes_id           UUID,
    validity                TEXT        NOT NULL DEFAULT 'active',
    scope                   TEXT        NOT NULL DEFAULT 'global',
    entity_id               UUID,
    object_entity_id        UUID,
    valid_at                TIMESTAMPTZ,
    reference_count         INTEGER     NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_referenced_at      TIMESTAMPTZ,
    last_confirmed_at       TIMESTAMPTZ,
    tags                    JSONB       DEFAULT '[]'::jsonb,
    metadata                JSONB       DEFAULT '{}'::jsonb,
    tenant_id               TEXT        NOT NULL DEFAULT 'owner',
    request_id              TEXT,
    idempotency_key         TEXT,
    observed_at             TIMESTAMPTZ DEFAULT now(),
    invalid_at              TIMESTAMPTZ,
    retention_class         TEXT        NOT NULL DEFAULT 'operational',
    sensitivity             TEXT        NOT NULL DEFAULT 'normal',
    embedding_model_version TEXT        DEFAULT 'unknown'
)
"""

_CREATE_PREDICATE_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS predicate_registry (
    name                  TEXT PRIMARY KEY,
    expected_subject_type TEXT,
    expected_object_type  TEXT,
    is_edge               BOOLEAN NOT NULL DEFAULT false,
    is_temporal           BOOLEAN NOT NULL DEFAULT false,
    description           TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    status                TEXT NOT NULL DEFAULT 'active',
    superseded_by         TEXT,
    deprecated_at         TIMESTAMPTZ,
    inverse_of            TEXT,
    is_symmetric          BOOLEAN NOT NULL DEFAULT false,
    aliases               TEXT[]  NOT NULL DEFAULT '{}',
    usage_count           INTEGER NOT NULL DEFAULT 0,
    last_used_at          TIMESTAMPTZ
)
"""

_CREATE_MEMORY_LINKS_SQL = """
CREATE TABLE IF NOT EXISTS memory_links (
    source_type TEXT NOT NULL,
    source_id   UUID NOT NULL,
    target_type TEXT NOT NULL,
    target_id   UUID NOT NULL,
    relation    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_type, source_id, target_type, target_id)
)
"""

_CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version    INTEGER NOT NULL DEFAULT 1
)
"""

# relationship.entity_facts — must match relationship_assert_fact INSERT (includes observed_at).
_CREATE_RELATIONSHIP_ENTITY_FACTS_SQL = """
CREATE SCHEMA IF NOT EXISTS relationship;
CREATE TABLE IF NOT EXISTS relationship.entity_facts (
    id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    predicate   TEXT        NOT NULL,
    object      TEXT        NOT NULL,
    object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
    src         TEXT        NOT NULL DEFAULT 'test',
    conf        FLOAT       NOT NULL DEFAULT 1.0,
    last_seen   TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    weight      INT,
    verified    BOOL        NOT NULL DEFAULT false,
    "primary"   BOOL,
    validity    TEXT        NOT NULL DEFAULT 'active'
                    CHECK (validity IN ('active', 'retracted', 'superseded')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Required by relationship_assert_fact's ON CONFLICT (subject, predicate, object) clause.
_CREATE_EF_UNIQUE_IDX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
    ON relationship.entity_facts (subject, predicate, object)
    WHERE validity = 'active'
"""

_CREATE_RELATIONSHIP_PREDICATE_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
    predicate   TEXT        NOT NULL PRIMARY KEY,
    kind        TEXT        NOT NULL,
    object_kind TEXT        NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_SEED_PREDICATES_SQL = """
INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind, description)
VALUES
    ('has-email',   'contact',    'literal', 'Email address'),
    ('co-attended', 'relational', 'entity',  'Both entities attended the same event or place.')
ON CONFLICT (predicate) DO NOTHING
"""

# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------


async def _setup_schema(pool) -> None:
    await pool.execute(_CREATE_PUBLIC_ENTITIES_SQL)
    await pool.execute(_CREATE_PUBLIC_CONTACTS_SQL)
    await pool.execute(_CREATE_PUBLIC_CONTACT_INFO_SQL)
    await pool.execute(_CREATE_SWITCHBOARD_SCHEMA_SQL)
    await pool.execute(_CREATE_MESSAGE_INBOX_SQL)
    await pool.execute(_CREATE_CALENDAR_EVENTS_SQL)
    await pool.execute(_CREATE_FACTS_SQL)
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_facts_co_att ON facts (subject, predicate)")
    await pool.execute(_CREATE_RELATIONSHIP_ENTITY_FACTS_SQL)
    await pool.execute(_CREATE_EF_UNIQUE_IDX_SQL)
    await pool.execute(_CREATE_RELATIONSHIP_PREDICATE_REGISTRY_SQL)
    await pool.execute(_SEED_PREDICATES_SQL)
    await pool.execute(_CREATE_PREDICATE_REGISTRY_SQL)
    await pool.execute(_CREATE_MEMORY_LINKS_SQL)
    await pool.execute(_CREATE_STATE_SQL)


async def _make_entity(pool, *, roles: list[str] | None = None) -> uuid.UUID:
    eid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.entities (id, roles) VALUES ($1, $2::text[])",
        eid,
        roles or [],
    )
    return eid


async def _make_contact(pool, *, entity_id: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.contacts (id, first_name, entity_id) VALUES ($1, $2, $3)",
        cid,
        "Test",
        entity_id,
    )
    return cid


async def _link_email(pool, *, entity_id: uuid.UUID, email: str) -> None:
    """Insert entity_facts has-email triple so interaction_sync can resolve the email."""
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity)
        VALUES ($1, 'has-email', $2, 'literal', 'test', 'active')
        ON CONFLICT DO NOTHING
        """,
        entity_id,
        email,
    )


async def _insert_calendar_event(
    pool,
    *,
    title: str = "Team Sync",
    starts_at: datetime | None = None,
    attendees: list[dict] | None = None,
) -> str:
    if starts_at is None:
        starts_at = datetime.now(UTC) - timedelta(hours=2)
    ends_at = starts_at + timedelta(hours=1)
    metadata = {"attendees": attendees} if attendees else {}
    eid = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO public.calendar_events
            (id, title, timezone, starts_at, ends_at, status, metadata)
        VALUES ($1::uuid, $2, 'UTC', $3, $4, 'confirmed', $5)
        """,
        eid,
        title,
        starts_at,
        ends_at,
        metadata,
    )
    return eid


async def _co_attended_edges(pool, entity_a: uuid.UUID, entity_b: uuid.UUID) -> list[dict]:
    """Return active co-attended entity_facts rows between entity_a and entity_b.

    subject is UUID; object is TEXT (entity UUID stored as string in entity_facts).
    """
    return await pool.fetch(
        """
        SELECT subject, predicate, object
        FROM relationship.entity_facts
        WHERE predicate = 'co-attended'
          AND validity = 'active'
          AND (
              (subject = $1 AND object = $2)
              OR (subject = $3 AND object = $4)
          )
        """,
        entity_a,
        str(entity_b),
        entity_b,
        str(entity_a),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_co_attended_edges_minted_for_shared_event(provisioned_postgres_pool):
    """Two attendees at the same event receive co-attended edges in both directions."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        entity_a = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_a)
        await _link_email(pool, entity_id=entity_a, email="alice@example.com")

        entity_b = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_b)
        await _link_email(pool, entity_id=entity_b, email="bob@example.com")

        await _insert_calendar_event(
            pool,
            title="Team Meeting",
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["co_attended_edges_minted"] == 2  # A→B and B→A
        assert result["errors"] == 0

        edges = await _co_attended_edges(pool, entity_a, entity_b)
        assert len(edges) == 2
        subjects = {str(r["subject"]) for r in edges}
        assert str(entity_a) in subjects
        assert str(entity_b) in subjects


async def test_co_attended_edges_all_pairs_for_three_attendees(provisioned_postgres_pool):
    """Three attendees at the same event produce 3 pairs × 2 directions = 6 edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        entities = []
        emails = ["alice@ex.com", "bob@ex.com", "carol@ex.com"]
        for email in emails:
            eid = await _make_entity(pool)
            await _make_contact(pool, entity_id=eid)
            await _link_email(pool, entity_id=eid, email=email)
            entities.append(eid)

        await _insert_calendar_event(
            pool,
            attendees=[{"email": e, "responseStatus": "accepted"} for e in emails]
            + [{"email": "me@owner.com", "responseStatus": "accepted", "self": True}],
        )

        result = await run_interaction_sync(pool)

        assert result["co_attended_edges_minted"] == 6
        assert result["errors"] == 0

        # Every ordered pair should exist.
        total_ef_rows = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE predicate = 'co-attended'"
        )
        assert total_ef_rows == 6


async def test_no_co_attended_edges_for_entities_in_different_events(provisioned_postgres_pool):
    """Entities attending different events do not receive co-attended edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        entity_a = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_a)
        await _link_email(pool, entity_id=entity_a, email="alice@example.com")

        entity_b = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_b)
        await _link_email(pool, entity_id=entity_b, email="bob@example.com")

        # Each entity attends a SEPARATE event — no shared event.
        await _insert_calendar_event(
            pool,
            title="Alice's Meeting",
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )
        await _insert_calendar_event(
            pool,
            title="Bob's Meeting",
            attendees=[
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 2
        assert result["co_attended_edges_minted"] == 0
        assert result["errors"] == 0

        edges = await _co_attended_edges(pool, entity_a, entity_b)
        assert len(edges) == 0


async def test_no_co_attended_edges_for_sole_attendee(provisioned_postgres_pool):
    """A single resolved attendee per event produces no co-attended edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        entity_a = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_a)
        await _link_email(pool, entity_id=entity_a, email="alone@example.com")

        await _insert_calendar_event(
            pool,
            attendees=[
                {"email": "alone@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["co_attended_edges_minted"] == 0
        assert result["errors"] == 0


async def test_co_attended_edges_idempotent_on_second_run(provisioned_postgres_pool):
    """Running interaction_sync twice for the same event does not duplicate edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        entity_a = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_a)
        await _link_email(pool, entity_id=entity_a, email="alice@example.com")

        entity_b = await _make_entity(pool)
        await _make_contact(pool, entity_id=entity_b)
        await _link_email(pool, entity_id=entity_b, email="bob@example.com")

        await _insert_calendar_event(
            pool,
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        first = await run_interaction_sync(pool)
        assert first["co_attended_edges_minted"] == 2

        # Second run: edges already exist → outcome is 'unchanged', not re-minted.
        second = await run_interaction_sync(pool)
        assert second["co_attended_edges_minted"] == 0
        assert second["errors"] == 0

        # Still exactly 2 active rows in entity_facts — no duplicates.
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE predicate = 'co-attended'"
        )
        assert count == 2


async def test_co_attended_edges_minted_key_in_stats(provisioned_postgres_pool):
    """run_interaction_sync always returns co_attended_edges_minted in the stats dict."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        result = await run_interaction_sync(pool)

        assert "co_attended_edges_minted" in result
        assert result["co_attended_edges_minted"] == 0
