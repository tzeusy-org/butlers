"""Real migrated, role-enforced local calendar projection fixtures.

These helpers never start CalendarModule, resolve credentials, or call a provider.
The migration login is used only for disposable setup/cleanup and fault injection;
the projection writer and interaction job share the actual Relationship role.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg

from butlers.core.approvals_hooks import register_approval_hooks, unregister_approval_hooks
from butlers.db import register_jsonb_codec, schema_search_path
from butlers.migrations import get_chain_head, run_migrations
from butlers.modules.approvals.email_guard import check_email_recipient, check_recipient
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.calendar import AttendeeInfo, CalendarEvent, CalendarModule, EventStatus
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

_CHAINS = ("core", "relationship", "contacts", "memory", "approvals")
_DATABASES: dict[asyncpg.Pool, InteractionSyncDatabase] = {}


def create_interaction_sync_database(postgres_container: object) -> InteractionSyncDatabase:
    """Follow lifecycle's core -> roster -> enabled-module ordering at real heads."""
    dsn = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=list(_CHAINS),
        schemas=dict.fromkeys(_CHAINS, "relationship"),
    )
    asyncio.run(run_migrations(dsn, chain="core", schema="switchboard"))
    asyncio.run(run_migrations(dsn, chain="switchboard", schema="switchboard"))
    return InteractionSyncDatabase(dsn)


class InteractionSyncDatabase:
    """Callable pool factory, provisioned once per test module and reset per case."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._controls_migrated = False
        self._checked_topology = False

    @asynccontextmanager
    async def control(self, schema: str = "relationship") -> AsyncIterator[asyncpg.Pool]:
        pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=2,
            init=register_jsonb_codec,
            server_settings={"search_path": schema_search_path(schema)},
        )
        try:
            yield pool
        finally:
            await pool.close()

    async def _assert_heads(self, pool: asyncpg.Pool, schema: str, chains: tuple[str, ...]) -> None:
        rows = await pool.fetch(f'SELECT version_num FROM "{schema}".alembic_version')
        assert {row["version_num"] for row in rows} == {get_chain_head(c) for c in chains}

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[asyncpg.Pool]:
        async with self.control() as control:
            if not self._checked_topology:
                await self._assert_heads(control, "relationship", _CHAINS)
                await self._assert_heads(control, "switchboard", ("core", "switchboard"))
                # Named-schema migration must not accidentally provision the public table.
                assert (
                    await control.fetchval("SELECT to_regclass('public.calendar_events')") is None
                )
                self._checked_topology = True
            await control.execute(
                "TRUNCATE relationship.facts, relationship.entity_facts, "
                "relationship.contact_entity_map, relationship.calendar_sources, "
                "relationship.state, switchboard.message_inbox CASCADE"
            )
            await control.execute(
                "DELETE FROM public.entities WHERE canonical_name LIKE 'projection-test-%'"
            )
            if self._controls_migrated:
                await control.execute(
                    "TRUNCATE public.calendar_sources, health.calendar_sources CASCADE"
                )

        async def set_relationship_role(conn: asyncpg.Connection) -> None:
            # Like Database._setup_connection, this runs on EVERY pool acquisition.
            await conn.execute('SET ROLE "butler_relationship_rw"')

        pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=3,
            init=register_jsonb_codec,
            setup=set_relationship_role,
            server_settings={"search_path": schema_search_path("relationship")},
        )
        hooks = register_approval_hooks(
            pool,
            email_guard=check_email_recipient,
            recipient_guard=check_recipient,
            park_pending_action=park_pending_action,
        )
        _DATABASES[pool] = self
        try:
            async with pool.acquire() as conn:
                assert await conn.fetchval("SELECT current_user") == "butler_relationship_rw"
                assert await conn.fetchval("SELECT current_schema()") == "relationship"
                assert await conn.fetchval("SHOW search_path") == "relationship,public"
                assert await conn.fetchval(
                    "SELECT has_table_privilege(current_user, 'switchboard.message_inbox', 'SELECT')"
                )
            yield pool
        finally:
            _DATABASES.pop(pool)
            unregister_approval_hooks(pool, hooks)
            await pool.close()

    async def migrate_controls(self) -> None:
        """Genuine public and inaccessible foreign controls, never copied table DDL."""
        if self._controls_migrated:
            return

        def migrate() -> None:
            for schema in ("public", "health"):
                asyncio.run(run_migrations(self._dsn, chain="core", schema=schema))

        await asyncio.to_thread(migrate)
        async with self.control() as control:
            for schema in ("public", "health"):
                await self._assert_heads(control, schema, ("core",))
        self._controls_migrated = True


def database_for(pool: asyncpg.Pool) -> InteractionSyncDatabase:
    return _DATABASES[pool]


async def make_entity(pool: asyncpg.Pool, *, roles: list[str] | None = None) -> uuid.UUID:
    entity_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.entities (id, canonical_name, entity_type, roles) "
        "VALUES ($1, $2, 'person', $3)",
        entity_id,
        f"projection-test-{entity_id}",
        roles or [],
    )
    return entity_id


async def make_contact_anchor(pool: asyncpg.Pool, entity_id: uuid.UUID) -> uuid.UUID:
    contact_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO relationship.contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        contact_id,
        entity_id,
    )
    return contact_id


async def link_identity(
    pool: asyncpg.Pool, *, entity_id: uuid.UUID, predicate: str, value: str
) -> None:
    owner = await pool.fetchval(
        "SELECT 'owner' = ANY(roles) FROM public.entities WHERE id = $1", entity_id
    )
    await relationship_assert_fact(
        pool,
        subject=entity_id,
        predicate=predicate,
        object=value,
        object_kind="literal",
        src="owner-bootstrap" if owner else "test",
    )


async def insert_message(
    pool: asyncpg.Pool, *, received_at: datetime, request_context: dict, direction: str
) -> None:
    # The runtime's existing Switchboard exception is READ-ONLY. Setup writes as
    # migration authority and uses the actual partition-maintenance function.
    async with database_for(pool).control("switchboard") as control:
        await control.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)
        await control.execute(
            "INSERT INTO switchboard.message_inbox "
            "(received_at, request_context, direction, normalized_text) VALUES ($1, $2, $3, $4)",
            received_at,
            request_context,
            direction,
            "synthetic interaction",
        )


async def project_event(
    pool: asyncpg.Pool,
    *,
    title: str = "Team Sync",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    status: str = "confirmed",
    attendees: list[dict] | None = None,
) -> uuid.UUID:
    """Persist a synthetic provider-shaped event via the real LOCAL projection writer."""
    starts_at = starts_at or datetime.now(UTC) - timedelta(hours=2)
    ends_at = ends_at or starts_at + timedelta(hours=1)
    module = CalendarModule()
    module._db = SimpleNamespace(pool=pool)
    module._butler_name = "relationship"
    source_id = await module._ensure_calendar_source(
        source_key="provider:google:synthetic-test",
        source_kind="provider",
        lane="user",
        provider="google",
        calendar_id="synthetic-test",
        writable=False,
    )
    assert source_id is not None
    normalized = [
        AttendeeInfo.model_validate(
            {
                "response_status" if key == "responseStatus" else key: value
                for key, value in att.items()
            }
        )
        for att in attendees or []
    ]
    event = CalendarEvent(
        event_id=str(uuid.uuid4()),
        title=title,
        start_at=starts_at,
        end_at=ends_at,
        timezone="UTC",
        status=EventStatus(status),
        attendees=normalized,
    )
    await module._project_provider_changes(
        source_id=source_id,
        provider_name="google",
        calendar_id="synthetic-test",
        updated_events=[event],
        cancelled_ids=[],
    )
    row = await pool.fetchrow(
        "SELECT e.id, e.metadata, e.source_id, i.source_id AS instance_source_id "
        "FROM calendar_events e JOIN calendar_event_instances i ON i.event_id = e.id "
        "JOIN calendar_sources s ON s.id = e.source_id WHERE e.origin_ref = $1",
        event.event_id,
    )
    assert row is not None
    assert row["source_id"] == row["instance_source_id"] == source_id
    assert row["metadata"]["attendees"] == [module._attendee_to_payload(a) for a in normalized]
    return row["id"]
