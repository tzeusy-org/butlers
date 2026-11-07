"""The primary-side substrate of the public MCP projection, over real logical replication.

RFC 0039 D2; REQ-public-mcp-projection-004, -005, -007 and -013. The manifest and its generator
arrive with bead ``bu-y66o00.4``, so :data:`_PUBLICATION` is written by hand here with the filters
D2 and D4 state. It runs on a PostgreSQL 17 cluster at ``wal_level=logical`` with one source and
one subscriber database, so "absent from the projection" is an observation of the subscriber after
a barrier row has been applied, never an inference from the primary.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from alembic import command
from butlers.core.entity_graph_edges import (
    delete_entity_graph_edge,
    project_entity_graph_edge,
    project_or_withhold_entity_graph_edge,
    withhold_entity_graph_edge,
)
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.migrations.projection import (
    drop_replica_identity,
    ensure_replica_identity,
    replica_identity_index_name,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration

_MEMORY_SCHEMAS = ("general", "relationship", "home", "health")
_EDGE_KEYS = (
    "general.facts:knows",
    "relationship.facts:met_at",
    "relationship.entity_facts:works-at",
)
_COUNT_SOURCES = ("general.facts", "relationship.facts", "relationship.entity_facts")
_GENERAL_PREDICATES = ("knows", "likes")
_RELATIONSHIP_PREDICATES = ("met_at",)
_ENTITY_FACT_PREDICATES = ("works-at",)


def _literals(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_FACT_COLUMNS = "(id, subject, predicate, sensitivity, validity)"
_PUBLICATION = f"""
CREATE PUBLICATION butlers_public_v1 FOR
    TABLE public.entities (
        id, canonical_name, entity_type, aliases, roles, merged_into,
        deleted, unidentified, placeholder_name
    ) WHERE (deleted = false AND unidentified = false AND placeholder_name = false),
    TABLE general.facts {_FACT_COLUMNS} WHERE (
        sensitivity = 'normal' AND validity IN ('active', 'fading')
        AND predicate IN ({_literals(_GENERAL_PREDICATES)})
    ),
    TABLE relationship.facts {_FACT_COLUMNS} WHERE (
        sensitivity = 'normal' AND validity IN ('active', 'fading')
        AND predicate IN ({_literals(_RELATIONSHIP_PREDICATES)})
    ),
    TABLE health.facts {_FACT_COLUMNS} WHERE (validity = 'active'),
    TABLE relationship.entity_facts (id, subject, predicate, object, validity) WHERE (
        validity = 'active' AND predicate IN ({_literals(_ENTITY_FACT_PREDICATES)})
    ),
    TABLE public.entity_graph_edges (
        id, subject_entity_id, predicate, object_entity_id, sensitivity, created_at, publish_key
    ) WHERE (sensitivity = 'normal' AND publish_key IN ({_literals(_EDGE_KEYS)})),
    TABLE public.entity_graph_withheld_counts (entity_id, source_key, withheld)
        WHERE (source_key IN ({_literals(_COUNT_SOURCES)})),
    TABLE public.projection_barrier
"""

_FACT_TABLE = "id UUID PRIMARY KEY, subject TEXT, predicate TEXT, sensitivity TEXT, validity TEXT"
_SUBSCRIBER_TABLES = {
    "public.entities": (
        "id UUID PRIMARY KEY, canonical_name TEXT, entity_type TEXT, aliases TEXT[], roles TEXT[], "
        "merged_into UUID, deleted BOOLEAN, unidentified BOOLEAN, placeholder_name BOOLEAN"
    ),
    "general.facts": _FACT_TABLE,
    "relationship.facts": _FACT_TABLE,
    "health.facts": _FACT_TABLE,
    "relationship.entity_facts": (
        "id UUID PRIMARY KEY, subject UUID, predicate TEXT, object TEXT, validity TEXT"
    ),
    "public.entity_graph_edges": (
        "id UUID PRIMARY KEY, subject_entity_id UUID, predicate TEXT, object_entity_id UUID, "
        "sensitivity TEXT, created_at TIMESTAMPTZ, publish_key TEXT"
    ),
    "public.entity_graph_withheld_counts": (
        "entity_id UUID, source_key TEXT, withheld INTEGER, PRIMARY KEY (entity_id, source_key)"
    ),
}


def _subscriber_ddl(tables: Mapping[str, str]) -> str:
    """The selective subscriber schema, shared by every source projection fixture."""
    schemas = dict.fromkeys(table.split(".")[0] for table in tables)
    return (
        " ".join(f"CREATE SCHEMA {schema};" for schema in schemas if schema != "public")
        + " "
        + " ".join(f"CREATE TABLE {table} ({columns});" for table, columns in tables.items())
    )


# Parity with the source expressions, written independently of the migration's SQL.
_ENTITY_PARITY = """
SELECT id FROM public.entities
WHERE merged_into::text IS DISTINCT FROM CASE
        WHEN metadata ->> 'merged_into' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        THEN lower(metadata ->> 'merged_into') END
   OR deleted IS DISTINCT FROM (
        metadata ->> 'deleted_at' IS NOT NULL
        OR metadata ->> 'tombstone' IS NOT DISTINCT FROM 'true'
        OR metadata ->> 'archived' IS NOT DISTINCT FROM 'true'
        OR metadata ->> 'archived_at' IS NOT NULL
        OR NOT listed
        OR (metadata ->> 'merged_into' IS NOT NULL
            AND metadata ->> 'merged_into' !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'))
   OR unidentified IS DISTINCT FROM COALESCE(metadata ->> 'unidentified' = 'true', false)
   OR placeholder_name IS DISTINCT FROM COALESCE(
        canonical_name = 'Unknown (' || (metadata ->> 'source_channel') || ' '
            || (metadata ->> 'source_value') || ')', false)
"""
_PUBLISH_KEY_PARITY = """
SELECT id FROM public.entity_graph_edges
WHERE publish_key IS DISTINCT FROM CASE
    WHEN withheld_reason IS NULL THEN source_schema || '.' || source_table || ':' || predicate
    ELSE '' END
"""
_COUNT_PARITY = """
WITH expected AS (
    SELECT subject_entity_id AS entity_id, source_schema || '.' || source_table AS source_key,
           count(*)::integer AS withheld
    FROM public.entity_graph_edges WHERE withheld_reason IS NOT NULL GROUP BY 1, 2
)
SELECT expected.entity_id, counts.entity_id
FROM expected FULL JOIN public.entity_graph_withheld_counts AS counts
  USING (entity_id, source_key)
WHERE expected.withheld IS DISTINCT FROM counts.withheld
"""


@pytest.fixture(scope="module")
def logical_postgres() -> Iterator[object]:
    """A cluster of its own: logical decoding needs ``wal_level=logical`` at server start."""
    from testcontainers.postgres import PostgresContainer

    container = (
        PostgresContainer("pgvector/pgvector:pg17")
        .with_kwargs(shm_size="256m")
        .with_command("postgres -c wal_level=logical -c max_replication_slots=8")
    )
    with container as started:
        yield started


def _asyncpg_url(container: object, database: str) -> str:
    """The superuser URL without the SQLAlchemy driver suffix, which asyncpg refuses."""
    url = migration_bootstrap_db_url(container, database)
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


@dataclass(frozen=True)
class Projection:
    source_url: str
    subscriber_url: str

    @asynccontextmanager
    async def source(self) -> AsyncIterator[asyncpg.Connection]:
        conn = await asyncpg.connect(self.source_url)
        try:
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def subscriber(self) -> AsyncIterator[asyncpg.Connection]:
        conn = await asyncpg.connect(self.subscriber_url)
        try:
            yield conn
        finally:
            await conn.close()

    async def caught_up(self) -> None:
        """Wait until a row written now has applied; apply order covers every earlier commit."""
        marker = uuid.uuid4()
        async with self.source() as source:
            await source.execute("INSERT INTO public.projection_barrier VALUES ($1)", marker)
        async with self.subscriber() as subscriber:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if await subscriber.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM public.projection_barrier WHERE id = $1)", marker
                ):
                    return
                await asyncio.sleep(0.05)
        raise AssertionError("the subscriber did not apply the barrier row within 30 seconds")


@pytest.fixture(scope="module")
def projection(logical_postgres: object) -> Iterator[Projection]:
    source_name = migration_db_name()
    migrated_url = create_migration_db(logical_postgres, source_name)
    for schema in _MEMORY_SCHEMAS:
        asyncio.run(run_migrations(migrated_url, chain="core", schema=schema))
        asyncio.run(run_migrations(migrated_url, chain="memory", schema=schema))
    asyncio.run(run_migrations(migrated_url, chain="relationship", schema="relationship"))

    with replicated_projection(
        logical_postgres,
        source_name,
        publication=_PUBLICATION,
        publication_name="butlers_public_v1",
        subscription_name="butlers_public_sub",
        subscriber_tables=_SUBSCRIBER_TABLES,
    ) as built:
        yield built


@contextmanager
def replicated_projection(
    logical_postgres: object,
    source_name: str,
    *,
    publication: str,
    publication_name: str,
    subscription_name: str,
    subscriber_tables: Mapping[str, str],
) -> Iterator[Projection]:
    """Attach the shared logical subscriber topology to an already migrated source.

    Callers provide reviewed publication SQL and only the public subscriber
    columns. The source barrier, schema construction, slot, readiness and
    partial-setup cleanup have one owner across Package A and Chronicler.
    """
    subscriber_name = migration_db_name()
    source_url = _asyncpg_url(logical_postgres, source_name)
    subscriber_url = _asyncpg_url(logical_postgres, subscriber_name)
    admin_url = _asyncpg_url(logical_postgres, "postgres")
    tables = {**subscriber_tables, "public.projection_barrier": "id UUID PRIMARY KEY"}
    subscriber_created = False
    slot_created = False

    async def connect() -> None:
        nonlocal subscriber_created, slot_created
        admin = await asyncpg.connect(admin_url)
        try:
            await admin.execute(f'CREATE DATABASE "{subscriber_name}"')
            subscriber_created = True
            port = await admin.fetchval("SHOW port")
        finally:
            await admin.close()
        source = await asyncpg.connect(source_url)
        try:
            await source.execute("CREATE TABLE public.projection_barrier (id UUID PRIMARY KEY)")
            await source.execute(publication)
            # A separate slot avoids same-cluster CREATE SUBSCRIPTION deadlock.
            await source.fetchval(
                "SELECT pg_create_logical_replication_slot($1, 'pgoutput')", subscription_name
            )
            slot_created = True
        finally:
            await source.close()
        subscriber = await asyncpg.connect(subscriber_url)
        try:
            await subscriber.execute(_subscriber_ddl(tables))
            user = logical_postgres.username
            password = logical_postgres.password
            conninfo = (
                f"host=127.0.0.1 port={port} dbname={source_name} user={user} password={password}"
            )
            await subscriber.execute(
                f"CREATE SUBSCRIPTION {subscription_name} CONNECTION '{conninfo}' "
                f"PUBLICATION {publication_name} "
                f"WITH (create_slot = false, slot_name = '{subscription_name}')"
            )
            # Initial copies have separate workers; only streaming tables share
            # the commit-order guarantee used by Projection.caught_up().
            async with asyncio.timeout(30):
                while not await subscriber.fetchval(  # noqa: ASYNC110 -- external PostgreSQL table-sync state
                    """
                    SELECT count(*) = $2 AND bool_and(srsubstate = 'r')
                    FROM pg_subscription_rel
                    WHERE srsubid = (SELECT oid FROM pg_subscription WHERE subname = $1)
                    """,
                    subscription_name,
                    len(tables),
                ):
                    await asyncio.sleep(0.05)
        finally:
            await subscriber.close()

    async def disconnect() -> None:
        if subscriber_created:
            subscriber = await asyncpg.connect(subscriber_url)
            try:
                await subscriber.execute(f"DROP SUBSCRIPTION IF EXISTS {subscription_name}")
            finally:
                await subscriber.close()
        source = await asyncpg.connect(source_url)
        try:
            # Subscription setup can fail after creating its separately owned slot.
            if slot_created:
                await source.execute(
                    "SELECT pg_drop_replication_slot($1) "
                    "WHERE EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = $1)",
                    subscription_name,
                )
            await source.execute(f"DROP PUBLICATION IF EXISTS {publication_name}")
        finally:
            await source.close()

    try:
        asyncio.run(connect())
        built = Projection(source_url=source_url, subscriber_url=subscriber_url)
        asyncio.run(built.caught_up())
        yield built
    finally:
        asyncio.run(disconnect())


async def _entity(
    conn: asyncpg.Connection, name: str | None = None, metadata: dict | None = None
) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ($1, 'person', $2::jsonb) RETURNING id",
        name or f"Person {uuid.uuid4()}",
        json.dumps(metadata or {}),
    )


async def _fact(
    conn: asyncpg.Connection,
    schema: str,
    predicate: str,
    sensitivity: str = "normal",
    validity: str = "active",
) -> uuid.UUID:
    return await conn.fetchval(
        f"INSERT INTO {schema}.facts (subject, predicate, content, sensitivity, validity) "
        "VALUES ($4, $1, 'content', $2, $3) RETURNING id",
        predicate,
        sensitivity,
        validity,
        f"subject-{uuid.uuid4()}",
    )


async def _entity_fact(conn: asyncpg.Connection, subject: uuid.UUID, predicate: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, $2, $3, 'literal', 'test') RETURNING id",
        subject,
        predicate,
        f"object-{uuid.uuid4()}",
    )


async def _projected(conn: asyncpg.Connection, table: str, row_id: uuid.UUID) -> bool:
    return await conn.fetchval(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE id = $1)", row_id)


async def _assert_parity(source: asyncpg.Connection) -> None:
    assert await source.fetch(_ENTITY_PARITY) == []
    assert await source.fetch(_PUBLISH_KEY_PARITY) == []
    assert await source.fetch(_COUNT_PARITY) == []


async def _edge_id(
    source: asyncpg.Connection, schema: str, table: str, row: uuid.UUID
) -> uuid.UUID:
    return await source.fetchval(
        "SELECT id FROM public.entity_graph_edges "
        "WHERE source_schema = $1 AND source_table = $2 AND source_id = $3",
        schema,
        table,
        row,
    )


# -- ensure_replica_identity ---------------------------------------------------------------------


@pytest.fixture
def helper_connection(projection: Projection) -> Iterator[object]:
    engine = create_engine(projection.source_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TEMPORARY TABLE helper_subject ("
                    "id UUID PRIMARY KEY, kind TEXT NOT NULL, loose TEXT) ON COMMIT DROP"
                )
            )
            yield connection
    finally:
        engine.dispose()


def _identity(connection: object, table: str) -> tuple[str, str | None]:
    row = connection.execute(
        text(
            "SELECT c.relreplident::text, "
            "(SELECT ic.relname::text FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid "
            " WHERE i.indrelid = c.oid AND i.indisreplident) "
            "FROM pg_class c WHERE c.oid = to_regclass(:table)"
        ),
        {"table": table},
    ).one()
    return row[0], row[1]


def test_helper_sets_the_identity_idempotently_and_drops_it(helper_connection: object) -> None:
    with Operations.context(MigrationContext.configure(helper_connection)):
        first = ensure_replica_identity("helper_subject", ("id",), ("kind",))
        assert ensure_replica_identity("helper_subject", ("id",), ("kind",)) == first
        assert first == replica_identity_index_name("helper_subject")
        assert _identity(helper_connection, "helper_subject") == ("i", first)
        drop_replica_identity("helper_subject")
        assert _identity(helper_connection, "helper_subject") == ("d", None)


def test_helper_refuses_a_nullable_or_missing_column(helper_connection: object) -> None:
    with Operations.context(MigrationContext.configure(helper_connection)):
        with pytest.raises(ValueError, match="loose"):
            ensure_replica_identity("helper_subject", ("id",), ("kind", "loose"))
        with pytest.raises(ValueError, match="absent"):
            ensure_replica_identity("helper_subject", ("id",), ("absent",))
        with pytest.raises(ValueError, match="kind"):
            ensure_replica_identity("no_such_table", ("id",), ("kind",))


def test_helper_refuses_an_existing_index_of_another_shape(helper_connection: object) -> None:
    index = replica_identity_index_name("helper_subject")
    helper_connection.execute(text(f"CREATE UNIQUE INDEX {index} ON helper_subject (kind, id)"))
    with Operations.context(MigrationContext.configure(helper_connection)):
        with pytest.raises(ValueError, match="does not match"):
            ensure_replica_identity("helper_subject", ("id",), ("kind",))
    assert _identity(helper_connection, "helper_subject") == ("d", None)


@pytest.mark.parametrize(
    "definition",
    [
        "CREATE INDEX {index} ON helper_subject (id, kind)",
        "CREATE UNIQUE INDEX {index} ON helper_subject (id, kind) WHERE kind <> ''",
    ],
)
def test_helper_refuses_a_non_unique_or_partial_index(
    helper_connection: object, definition: str
) -> None:
    index = replica_identity_index_name("helper_subject")
    helper_connection.execute(text(definition.format(index=index)))
    with Operations.context(MigrationContext.configure(helper_connection)):
        with pytest.raises(ValueError, match="does not match"):
            ensure_replica_identity("helper_subject", ("id",), ("kind",))


def test_helper_refuses_an_unsafe_identifier() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        ensure_replica_identity("facts; DROP TABLE x", ("id",), ("kind",))


# -- REQ-public-mcp-projection-007: entity flags --------------------------------------------------


@pytest.mark.parametrize(
    ("name", "metadata", "expected"),
    [
        ("Plain {u}", {}, (None, False, False, False)),
        ("Deleted {u}", {"deleted_at": "2026-11-06"}, (None, True, False, False)),
        ("Null delete {u}", {"deleted_at": None}, (None, False, False, False)),
        ("Forgotten {u}", {"tombstone": True}, (None, True, False, False)),
        ("Not forgotten {u}", {"tombstone": False}, (None, False, False, False)),
        ("Archived {u}", {"archived": True}, (None, True, False, False)),
        ("Legacy archive {u}", {"archived_at": "2026-11-06"}, (None, True, False, False)),
        ("Bad pointer {u}", {"merged_into": "not-a-uuid"}, (None, True, False, False)),
        ("False pointer {u}", {"merged_into": False}, (None, True, False, False)),
        ("Unidentified {u}", {"unidentified": True}, (None, False, True, False)),
        ("Text false {u}", {"unidentified": "false"}, (None, False, False, False)),
        (
            "Unknown (email {u}@example.com)",
            {"unidentified": True, "source_channel": "email", "source_value": "{u}@example.com"},
            (None, False, True, True),
        ),
        (
            "Renamed {u}",
            {"source_channel": "email", "source_value": "{u}@example.com"},
            (None, False, False, False),
        ),
        ("Channel only {u}", {"source_channel": "email"}, (None, False, False, False)),
    ],
)
async def test_flags_equal_their_metadata_on_insert(
    projection: Projection, name: str, metadata: dict, expected: tuple
) -> None:
    unique = uuid.uuid4().hex
    rendered = json.loads(json.dumps(metadata).replace("{u}", unique))
    async with projection.source() as source:
        row_id = await _entity(source, name.replace("{u}", unique), rendered)
        row = await source.fetchrow(
            "SELECT merged_into, deleted, unidentified, placeholder_name "
            "FROM public.entities WHERE id = $1",
            row_id,
        )
        assert tuple(row) == expected
        assert await source.fetch(_ENTITY_PARITY) == []


async def test_a_merge_sets_the_pointer_in_the_same_transaction_and_replicates(
    projection: Projection,
) -> None:
    async with projection.source() as source:
        survivor = await _entity(source)
        merged = await _entity(source)
        async with source.transaction():
            await source.execute(
                "UPDATE public.entities SET metadata = metadata || jsonb_build_object("
                "'merged_into', $2::text) WHERE id = $1",
                merged,
                str(survivor),
            )
            assert (
                await source.fetchval(
                    "SELECT merged_into FROM public.entities WHERE id = $1", merged
                )
                == survivor
            )
    await projection.caught_up()
    async with projection.subscriber() as subscriber:
        assert (
            await subscriber.fetchval(
                "SELECT merged_into FROM public.entities WHERE id = $1", merged
            )
            == survivor
        )


async def test_a_soft_deleted_entity_leaves_the_projection(projection: Projection) -> None:
    async with projection.source() as source:
        row_id = await _entity(source)
        await projection.caught_up()
        async with projection.subscriber() as subscriber:
            assert await _projected(subscriber, "public.entities", row_id)
            await source.execute(
                "UPDATE public.entities SET metadata = metadata || "
                """'{"deleted_at": "2026-11-06"}'::jsonb WHERE id = $1""",
                row_id,
            )
            await projection.caught_up()
            assert not await _projected(subscriber, "public.entities", row_id)


async def test_a_forgotten_entity_leaves_the_projection(projection: Projection) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        row_id = await _entity(source)
        await projection.caught_up()
        assert await _projected(subscriber, "public.entities", row_id)
        # The dashboard forget route's statement, roster/relationship/api/router.py.
        await source.execute(
            "UPDATE public.entities SET metadata = "
            "jsonb_set(COALESCE(metadata, '{}'), '{tombstone}', 'true') WHERE id = $1",
            row_id,
        )
        await projection.caught_up()
        assert not await _projected(subscriber, "public.entities", row_id)


async def test_archive_and_unlist_leave_the_projection_and_reversal_returns(
    projection: Projection,
) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        archived = await _entity(source)
        unlisted = await _entity(source)
        await projection.caught_up()
        await source.execute(
            "UPDATE public.entities SET metadata = metadata || '{\"archived\": true}'::jsonb "
            "WHERE id = $1",
            archived,
        )
        await source.execute("UPDATE public.entities SET listed = false WHERE id = $1", unlisted)
        await projection.caught_up()
        assert not await _projected(subscriber, "public.entities", archived)
        assert not await _projected(subscriber, "public.entities", unlisted)

        await source.execute(
            "UPDATE public.entities SET metadata = metadata - 'archived' WHERE id = $1", archived
        )
        await source.execute("UPDATE public.entities SET listed = true WHERE id = $1", unlisted)
        await projection.caught_up()
        assert await _projected(subscriber, "public.entities", archived)
        assert await _projected(subscriber, "public.entities", unlisted)
        await _assert_parity(source)


async def test_a_promoted_sender_stays_out_until_renamed(projection: Projection) -> None:
    address = f"{uuid.uuid4().hex}@example.com"
    async with projection.source() as source:
        row_id = await _entity(
            source,
            f"Unknown (email {address})",
            {"unidentified": True, "source_channel": "email", "source_value": address},
        )
        await projection.caught_up()
        async with projection.subscriber() as subscriber:
            assert not await _projected(subscriber, "public.entities", row_id)

            # The dashboard's promote statement, which clears the flag without renaming.
            promoted = await source.execute(
                "UPDATE public.entities SET metadata = metadata - 'unidentified', "
                "updated_at = now() "
                "WHERE id = $1 AND (metadata->>'unidentified')::boolean IS TRUE",
                row_id,
            )
            assert promoted == "UPDATE 1"
            await projection.caught_up()
            assert not await _projected(subscriber, "public.entities", row_id)
            assert not await subscriber.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.entities "
                "WHERE canonical_name LIKE '%' || $1 || '%' "
                "OR array_to_string(aliases, ' ') LIKE '%' || $1 || '%')",
                address,
            )

            new_name = f"Named {uuid.uuid4()}"
            await source.execute(
                "UPDATE public.entities SET canonical_name = $2 WHERE id = $1", row_id, new_name
            )
            await projection.caught_up()
            assert (
                await subscriber.fetchval(
                    "SELECT canonical_name FROM public.entities WHERE id = $1", row_id
                )
                == new_name
            )
        await _assert_parity(source)


# -- REQ-public-mcp-projection-005: later restriction ---------------------------------------------


async def test_a_reclassified_fact_arrives_as_a_delete(projection: Projection) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        row_id = await _fact(source, "general", "knows")
        await projection.caught_up()
        assert await _projected(subscriber, "general.facts", row_id)
        await source.execute("UPDATE general.facts SET sensitivity = 'pii' WHERE id = $1", row_id)
        await projection.caught_up()
        assert not await _projected(subscriber, "general.facts", row_id)


async def test_a_fact_leaving_the_allowlist_or_lifecycle_leaves_the_projection(
    projection: Projection,
) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        left_allowlist = await _fact(source, "general", "knows")
        fading = await _fact(source, "general", "knows")
        retired = await _fact(source, "general", "knows")
        await source.execute(
            "UPDATE general.facts SET predicate = 'contact_phone' WHERE id = $1", left_allowlist
        )
        await source.execute("UPDATE general.facts SET validity = 'fading' WHERE id = $1", fading)
        await source.execute(
            "UPDATE general.facts SET validity = 'retracted' WHERE id = $1", retired
        )
        await projection.caught_up()
        assert not await _projected(subscriber, "general.facts", left_allowlist)
        assert await _projected(subscriber, "general.facts", fading)
        assert not await _projected(subscriber, "general.facts", retired)


async def test_a_retracted_relationship_fact_leaves_the_projection(projection: Projection) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        person = await _entity(source)
        row_id = await _entity_fact(source, person, "works-at")
        await projection.caught_up()
        assert await _projected(subscriber, "relationship.entity_facts", row_id)
        await source.execute(
            "UPDATE relationship.entity_facts SET validity = 'retracted' WHERE id = $1", row_id
        )
        await projection.caught_up()
        assert not await _projected(subscriber, "relationship.entity_facts", row_id)


async def test_a_deleted_health_fact_is_erased_from_the_projection(projection: Projection) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        row_id = await _fact(source, "health", "medication", sensitivity="confidential")
        await projection.caught_up()
        assert await _projected(subscriber, "health.facts", row_id)
        await source.execute("DELETE FROM health.facts WHERE id = $1", row_id)
        await projection.caught_up()
        assert not await _projected(subscriber, "health.facts", row_id)


async def test_updates_and_deletes_succeed_on_every_published_filtered_table(
    projection: Projection,
) -> None:
    """A filter column outside the replica identity would fail these statements on the primary."""
    async with projection.source() as source:
        person = await _entity(source)
        other = await _entity(source)
        await source.execute("UPDATE public.entities SET aliases = '{a}' WHERE id = $1", person)
        for schema, predicate in (
            ("general", "knows"),
            ("relationship", "met_at"),
            ("health", "medication"),
        ):
            row_id = await _fact(source, schema, predicate)
            await source.execute(
                f"UPDATE {schema}.facts SET content = 'changed' WHERE id = $1", row_id
            )
            await source.execute(f"DELETE FROM {schema}.facts WHERE id = $1", row_id)
        fact = await _entity_fact(source, person, "works-at")
        await source.execute("UPDATE relationship.entity_facts SET conf = 0.5 WHERE id = $1", fact)
        await source.execute("DELETE FROM relationship.entity_facts WHERE id = $1", fact)

        edge_source = uuid.uuid4()
        await project_entity_graph_edge(
            source,
            source_schema="general",
            source_table="facts",
            source_id=edge_source,
            subject_entity_id=person,
            predicate="knows",
            object_entity_id=other,
        )
        await source.execute(
            "UPDATE public.entity_graph_edges SET updated_at = now() WHERE source_id = $1",
            edge_source,
        )
        await delete_entity_graph_edge(
            source, source_schema="general", source_table="facts", source_id=edge_source
        )

        await withhold_entity_graph_edge(
            source,
            source_schema="general",
            source_table="facts",
            source_id=edge_source,
            subject_entity_id=person,
            sensitivity="pii",
        )
        await source.execute(
            "UPDATE public.entity_graph_withheld_counts SET withheld = withheld WHERE entity_id = $1",
            person,
        )
        await delete_entity_graph_edge(
            source, source_schema="general", source_table="facts", source_id=edge_source
        )
        await source.execute(
            "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", [person, other]
        )
        await _assert_parity(source)


async def test_the_counts_grants_and_definer_are_as_narrow_as_intended(
    projection: Projection,
) -> None:
    async with projection.source() as source:
        assert not await source.fetchval(
            "SELECT has_table_privilege('connector_writer', "
            "'public.entity_graph_withheld_counts', 'SELECT')"
        )
        function = await source.fetchrow(
            "SELECT prosecdef, proconfig, proacl::text AS acl FROM pg_proc "
            "WHERE oid = 'public.entity_graph_edges_sync_withheld_counts()'::regprocedure"
        )
        assert function["prosecdef"] is True
        assert function["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert not any(entry.startswith("=") for entry in function["acl"].strip("{}").split(","))


# -- REQ-public-mcp-projection-013: edges and withheld counts -------------------------------------


async def test_a_predicate_change_rewrites_the_publish_key(projection: Projection) -> None:
    async with projection.source() as source:
        person, other = await _entity(source), await _entity(source)
        row = uuid.uuid4()
        common = {
            "source_schema": "relationship",
            "source_table": "entity_facts",
            "source_id": row,
            "subject_entity_id": person,
            "object_entity_id": other,
        }
        await project_entity_graph_edge(source, predicate="works-at", **common)
        key = "SELECT publish_key FROM public.entity_graph_edges WHERE source_id = $1"
        assert await source.fetchval(key, row) == "relationship.entity_facts:works-at"
        await project_entity_graph_edge(source, predicate="member-of", **common)
        assert await source.fetchval(key, row) == "relationship.entity_facts:member-of"
        await _assert_parity(source)


async def test_edges_the_facts_withhold_never_reach_the_projection(
    projection: Projection,
) -> None:
    """A filter on sensitivity alone would publish each of these as a live edge."""
    async with projection.source() as source, projection.subscriber() as subscriber:
        person, other = await _entity(source), await _entity(source)
        await projection.caught_up()
        excluded = [
            ("home", "facts", "knows"),
            ("relationship", "entity_facts", "dunbar_tier_override"),
            ("relationship", "facts", "contact_phone"),
            ("general", "facts", "off_allowlist"),
            ("health", "facts", "treated_by"),
        ]
        ids = []
        for schema, table, predicate in excluded:
            row = uuid.uuid4()
            await project_entity_graph_edge(
                source,
                source_schema=schema,
                source_table=table,
                source_id=row,
                subject_entity_id=person,
                predicate=predicate,
                object_entity_id=other,
            )
            ids.append(await _edge_id(source, schema, table, row))
        published = uuid.uuid4()
        await project_entity_graph_edge(
            source,
            source_schema="general",
            source_table="facts",
            source_id=published,
            subject_entity_id=person,
            predicate="knows",
            object_entity_id=other,
        )
        await projection.caught_up()
        for edge in ids:
            assert not await _projected(subscriber, "public.entity_graph_edges", edge)
        published_id = await _edge_id(source, "general", "facts", published)
        row = await subscriber.fetchrow(
            "SELECT predicate, object_entity_id FROM public.entity_graph_edges WHERE id = $1",
            published_id,
        )
        assert tuple(row) == ("knows", other)


async def test_a_health_stub_is_counted_on_the_primary_but_not_published(
    projection: Projection,
) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        person = await _entity(source)
        await withhold_entity_graph_edge(
            source,
            source_schema="health",
            source_table="facts",
            source_id=uuid.uuid4(),
            subject_entity_id=person,
            sensitivity="confidential",
        )
        assert (
            await source.fetchval(
                "SELECT withheld FROM public.entity_graph_withheld_counts "
                "WHERE entity_id = $1 AND source_key = 'health.facts'",
                person,
            )
            == 1
        )
        await projection.caught_up()
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.entity_graph_withheld_counts WHERE entity_id = $1)",
            person,
        )


async def test_a_reclassified_fact_takes_its_edge_and_updates_the_count(
    projection: Projection,
) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        person, other = await _entity(source), await _entity(source)
        fact = await _fact(source, "general", "knows")
        common = {
            "source_schema": "general",
            "source_table": "facts",
            "source_id": fact,
            "subject_entity_id": person,
            "predicate": "knows",
            "object_entity_id": other,
        }
        await project_or_withhold_entity_graph_edge(source, sensitivity="normal", **common)
        edge = await _edge_id(source, "general", "facts", fact)
        await projection.caught_up()
        assert await _projected(subscriber, "public.entity_graph_edges", edge)
        count = (
            "SELECT withheld FROM public.entity_graph_withheld_counts "
            "WHERE entity_id = $1 AND source_key = 'general.facts'"
        )
        assert await subscriber.fetchval(count, person) is None

        # The writer's reclassification path: the fact turns pii and its edge turns into a stub.
        async with source.transaction():
            await source.execute("UPDATE general.facts SET sensitivity = 'pii' WHERE id = $1", fact)
            await project_or_withhold_entity_graph_edge(source, sensitivity="pii", **common)
        assert (
            await source.fetchval(
                "SELECT publish_key FROM public.entity_graph_edges WHERE id = $1", edge
            )
            == ""
        )
        await projection.caught_up()
        assert not await _projected(subscriber, "public.entity_graph_edges", edge)
        assert not await _projected(subscriber, "general.facts", fact)
        assert await subscriber.fetchval(count, person) == 1
        await _assert_parity(source)

        # Reversal: the count row leaves, as a delete, and the edge returns.
        await project_or_withhold_entity_graph_edge(source, sensitivity="normal", **common)
        await projection.caught_up()
        assert await _projected(subscriber, "public.entity_graph_edges", edge)
        assert await subscriber.fetchval(count, person) is None
        await _assert_parity(source)


async def test_counts_follow_every_edge_transition(projection: Projection) -> None:
    async with projection.source() as source:
        person, survivor = await _entity(source), await _entity(source)
        rows = [uuid.uuid4() for _ in range(3)]
        for row in rows:
            await withhold_entity_graph_edge(
                source,
                source_schema="general",
                source_table="facts",
                source_id=row,
                subject_entity_id=person,
                sensitivity="pii",
            )
        count = (
            "SELECT withheld FROM public.entity_graph_withheld_counts "
            "WHERE entity_id = $1 AND source_key = 'general.facts'"
        )
        assert await source.fetchval(count, person) == 3
        await _assert_parity(source)

        # A repeat of the same stub changes nothing.
        await withhold_entity_graph_edge(
            source,
            source_schema="general",
            source_table="facts",
            source_id=rows[0],
            subject_entity_id=person,
            sensitivity="confidential",
        )
        assert await source.fetchval(count, person) == 3

        # An identity merge moves a stub to the survivor.
        await source.execute(
            "UPDATE public.entity_graph_edges SET subject_entity_id = $1 WHERE source_id = $2",
            survivor,
            rows[0],
        )
        assert await source.fetchval(count, person) == 2
        assert await source.fetchval(count, survivor) == 1

        await delete_entity_graph_edge(
            source, source_schema="general", source_table="facts", source_id=rows[1]
        )
        assert await source.fetchval(count, person) == 1
        await _assert_parity(source)

        # Deleting the subject cascades through both the edge and its count.
        await source.execute("DELETE FROM public.entities WHERE id = $1", person)
        assert await source.fetchval(count, person) is None
        await _assert_parity(source)


async def test_only_a_count_change_waits_on_the_source_lock(projection: Projection) -> None:
    """A stub write waits on its source's lock; a live edge and another source's stub do not."""
    async with projection.source() as source, projection.source() as holder:
        person, other = await _entity(source), await _entity(source)
        await holder.execute("BEGIN")
        await holder.execute(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('butlers:entity-graph-withheld:general.facts', 0))"
        )

        await project_entity_graph_edge(
            source,
            source_schema="general",
            source_table="facts",
            source_id=uuid.uuid4(),
            subject_entity_id=person,
            predicate="knows",
            object_entity_id=other,
        )
        await withhold_entity_graph_edge(
            source,
            source_schema="home",
            source_table="facts",
            source_id=uuid.uuid4(),
            subject_entity_id=person,
            sensitivity="pii",
        )

        # The lock is taken at commit, so an open transaction neither waits nor holds it.
        await source.execute("BEGIN")
        await withhold_entity_graph_edge(
            source,
            source_schema="general",
            source_table="facts",
            source_id=uuid.uuid4(),
            subject_entity_id=person,
            sensitivity="pii",
        )
        blocked = asyncio.create_task(source.execute("COMMIT"))
        done, _ = await asyncio.wait({blocked}, timeout=0.5)
        assert not done
        await holder.execute("COMMIT")
        await blocked
        await _assert_parity(source)


async def test_a_stub_written_and_its_entity_deleted_in_one_transaction_commits(
    projection: Projection,
) -> None:
    """The deferred count must not insert for an entity the transaction already removed."""
    async with projection.source() as source:
        person = await _entity(source)
        async with source.transaction():
            await withhold_entity_graph_edge(
                source,
                source_schema="general",
                source_table="facts",
                source_id=uuid.uuid4(),
                subject_entity_id=person,
                sensitivity="pii",
            )
            await source.execute("DELETE FROM public.entities WHERE id = $1", person)
        await _assert_parity(source)


async def test_a_rolled_back_savepoint_takes_its_count_with_it(projection: Projection) -> None:
    async with projection.source() as source:
        person = await _entity(source)
        async with source.transaction():
            with pytest.raises(RuntimeError):
                async with source.transaction():
                    await withhold_entity_graph_edge(
                        source,
                        source_schema="general",
                        source_table="facts",
                        source_id=uuid.uuid4(),
                        subject_entity_id=person,
                        sensitivity="pii",
                    )
                    raise RuntimeError("roll back to the savepoint")
        assert not await source.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.entity_graph_withheld_counts WHERE entity_id = $1)",
            person,
        )
        await _assert_parity(source)


async def test_hard_entity_deletes_spanning_sources_take_no_source_lock(
    projection: Projection,
) -> None:
    """Two deletes in opposite source order would otherwise deadlock at commit."""
    async with projection.source() as source, projection.source() as holder:
        person = await _entity(source)
        for schema in ("general", "home"):
            await withhold_entity_graph_edge(
                source,
                source_schema=schema,
                source_table="facts",
                source_id=uuid.uuid4(),
                subject_entity_id=person,
                sensitivity="pii",
            )
        await holder.execute("BEGIN")
        await holder.execute(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('butlers:entity-graph-withheld:general.facts', 0))"
        )
        async with asyncio.timeout(5):
            await source.execute("DELETE FROM public.entities WHERE id = $1", person)
        await holder.execute("ROLLBACK")
        await _assert_parity(source)


@pytest.mark.parametrize("role", ["butler_general_rw", "butler_relationship_rw"])
async def test_a_butler_role_writes_stubs_without_holding_the_counts_table(
    projection: Projection, role: str
) -> None:
    """An edge writer with no privilege on the counts table still keeps them, through the definer.

    Butler roles read the table and never write it, so the invariant must not rest on what a
    bootstrap replay grants.
    """
    async with projection.source() as source:
        person = await _entity(source)
        row = uuid.uuid4()
        tx = source.transaction()
        await tx.start()
        try:
            await source.execute(f'SET LOCAL ROLE "{role}"')
            await source.fetchval("SELECT count(*) FROM public.entity_graph_withheld_counts")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with source.transaction():
                    await source.execute(
                        "INSERT INTO public.entity_graph_withheld_counts VALUES ($1, 'x', 1)",
                        person,
                    )
            await source.execute("RESET ROLE")
            await source.execute(f'REVOKE ALL ON public.entity_graph_withheld_counts FROM "{role}"')
            await source.execute(f'SET LOCAL ROLE "{role}"')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with source.transaction():
                    await source.execute(
                        "SELECT 1 FROM public.entity_graph_withheld_counts LIMIT 1"
                    )
            await withhold_entity_graph_edge(
                source,
                source_schema="general",
                source_table="facts",
                source_id=row,
                subject_entity_id=person,
                sensitivity="pii",
            )
            await delete_entity_graph_edge(
                source, source_schema="general", source_table="facts", source_id=row
            )
            await source.execute("RESET ROLE")
            await _assert_parity(source)
        finally:
            await tx.rollback()


async def test_every_published_edge_has_a_published_source_fact(projection: Projection) -> None:
    async with projection.source() as source, projection.subscriber() as subscriber:
        person, other = await _entity(source), await _entity(source)
        memory_fact = await _fact(source, "general", "knows")
        relation_fact = await _entity_fact(source, person, "works-at")
        for schema, table, row, predicate in (
            ("general", "facts", memory_fact, "knows"),
            ("relationship", "entity_facts", relation_fact, "works-at"),
        ):
            await project_entity_graph_edge(
                source,
                source_schema=schema,
                source_table=table,
                source_id=row,
                subject_entity_id=person,
                predicate=predicate,
                object_entity_id=other,
            )
        await projection.caught_up()
        projected = {
            row["id"] for row in await subscriber.fetch("SELECT id FROM public.entity_graph_edges")
        }
        sources = await source.fetch(
            "SELECT id, source_schema, source_table, source_id FROM public.entity_graph_edges "
            "WHERE id = ANY($1::uuid[]) AND source_id = ANY($2::uuid[])",
            list(projected),
            [memory_fact, relation_fact],
        )
        assert len(sources) == 2
        for edge in sources:
            table = f"{edge['source_schema']}.{edge['source_table']}"
            assert await _projected(subscriber, table, edge["source_id"]), table


# -- Migration shape ------------------------------------------------------------------------------


def test_core_319_defers_the_shared_trigger_and_only_the_last_schema_restores_it(
    logical_postgres: object,
) -> None:
    url = create_migrated_test_db(
        logical_postgres,
        migration_db_name(),
        ["core"],
        schemas={"core": "general"},
    )
    asyncio.run(run_migrations(url, chain="core", schema="home"))

    async def _takes_source_lock_at_commit() -> tuple[bool, bool]:
        conn = await asyncpg.connect(url)
        try:
            body = await conn.fetchval(
                "SELECT prosrc FROM pg_proc "
                "WHERE oid = 'public.entity_graph_edges_sync_withheld_counts()'::regprocedure"
            )
            deferred = await conn.fetchval(
                "SELECT tgdeferrable AND tginitdeferred FROM pg_trigger "
                "WHERE tgname = 'trg_entity_graph_edges_withheld_counts'"
            )
            return "pg_advisory_xact_lock" in body, deferred
        finally:
            await conn.close()

    def _downgrade(schema: str) -> None:
        config = _build_alembic_config(url, chains=["core"], target_schema=schema)
        command.downgrade(config, "core@core_318")

    assert asyncio.run(_takes_source_lock_at_commit()) == (True, True)
    _downgrade("home")
    assert asyncio.run(_takes_source_lock_at_commit()) == (True, True)
    _downgrade("general")
    assert asyncio.run(_takes_source_lock_at_commit()) == (False, False)


def test_core_318_backfills_existing_rows_once_and_downgrades_last(
    logical_postgres: object,
) -> None:
    name = migration_db_name()
    url = create_migrated_test_db(
        logical_postgres,
        name,
        ["core"],
        schemas={"core": "general"},
        revisions={"core": "core_317"},
    )
    address = "sender@example.com"

    async def _seed() -> dict[str, uuid.UUID]:
        conn = await asyncpg.connect(url)
        try:
            merged_target = await _entity(conn, "Survivor")
            seeded = {
                "survivor": merged_target,
                "merged": await _entity(conn, "Merged", {"merged_into": str(merged_target)}),
                "deleted": await _entity(conn, "Deleted", {"deleted_at": "2026-11-06"}),
                "promoted": await _entity(
                    conn,
                    f"Unknown (email {address})",
                    {"source_channel": "email", "source_value": address},
                ),
            }
            stub = uuid.uuid4()
            await withhold_entity_graph_edge(
                conn,
                source_schema="health",
                source_table="facts",
                source_id=stub,
                subject_entity_id=merged_target,
                sensitivity="confidential",
            )
            await project_entity_graph_edge(
                conn,
                source_schema="general",
                source_table="facts",
                source_id=uuid.uuid4(),
                subject_entity_id=merged_target,
                predicate="knows",
                object_entity_id=seeded["deleted"],
            )
            return seeded
        finally:
            await conn.close()

    seeded = asyncio.run(_seed())
    asyncio.run(run_migrations(url, chain="core", schema="general"))

    async def _check_upgraded() -> None:
        conn = await asyncpg.connect(url)
        try:
            await _assert_parity(conn)
            flags = "SELECT merged_into, deleted, placeholder_name FROM public.entities WHERE id=$1"
            assert tuple(await conn.fetchrow(flags, seeded["merged"])) == (
                seeded["survivor"],
                False,
                False,
            )
            assert tuple(await conn.fetchrow(flags, seeded["deleted"])) == (None, True, False)
            assert tuple(await conn.fetchrow(flags, seeded["promoted"])) == (None, False, True)
            assert (
                await conn.fetchval(
                    "SELECT publish_key FROM public.entity_graph_edges "
                    "WHERE withheld_reason IS NULL"
                )
                == "general.facts:knows"
            )
            assert (
                await conn.fetchval(
                    "SELECT withheld FROM public.entity_graph_withheld_counts "
                    "WHERE entity_id = $1 AND source_key = 'health.facts'",
                    seeded["survivor"],
                )
                == 1
            )
            identity = await conn.fetch(
                "SELECT c.oid::regclass::text AS relation, c.relreplident::text AS mode "
                "FROM pg_class c WHERE c.oid IN ("
                "'public.entities'::regclass, 'public.entity_graph_edges'::regclass, "
                "'public.entity_graph_withheld_counts'::regclass)"
            )
            assert {row["mode"] for row in identity} == {"i", "d"}
            modes = {row["relation"].removeprefix("public."): row["mode"] for row in identity}
            assert modes == {
                "entities": "i",
                "entity_graph_edges": "i",
                "entity_graph_withheld_counts": "d",
            }
        finally:
            await conn.close()

    asyncio.run(_check_upgraded())

    # A second schema replays the chain over the shared objects without resetting them.
    async def _drift_marker() -> int:
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetchval(
                "SELECT xmin::text::bigint FROM public.entities ORDER BY id LIMIT 1"
            )
        finally:
            await conn.close()

    before = asyncio.run(_drift_marker())
    asyncio.run(run_migrations(url, chain="core", schema="home"))
    assert asyncio.run(_drift_marker()) == before

    def _downgrade(schema: str) -> None:
        config = _build_alembic_config(url, chains=["core"], target_schema=schema)
        command.downgrade(config, "core@core_317")

    async def _has_shared_objects() -> bool:
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetchval(
                "SELECT to_regclass('public.entity_graph_withheld_counts') IS NOT NULL"
            )
        finally:
            await conn.close()

    # Only the last schema leaving removes what the others still use.
    _downgrade("home")
    assert asyncio.run(_has_shared_objects())
    _downgrade("general")
    assert not asyncio.run(_has_shared_objects())
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND ("
                        "(table_name = 'entities' AND column_name IN "
                        "('merged_into', 'deleted', 'unidentified', 'placeholder_name')) "
                        "OR (table_name = 'entity_graph_edges' AND column_name = 'publish_key'))"
                    )
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_finance_overlay_backfill_and_writer_parity(fresh_db) -> None:
    """A real accepted transaction must expose exactly the legacy reader overlay."""
    url = fresh_db(
        "core",
        "finance",
        schemas={"core": "finance", "finance": "finance"},
        revisions={"finance": "finance_019"},
    )
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            row = (
                conn.execute(
                    text("""
                INSERT INTO finance.transactions
                    (posted_at, merchant, amount, currency, direction, category, metadata)
                VALUES (now(), 'RAW merchant', 12.30, 'USD', 'debit', 'groceries',
                        '{"normalized_merchant":"Shown merchant","inferred_category":"shown"}')
                RETURNING to_jsonb(transactions) AS source_row,
                    COALESCE(metadata->>'normalized_merchant', merchant) AS expected_merchant,
                    COALESCE(metadata->>'inferred_category', category) AS expected_category
            """)
                )
                .mappings()
                .one()
            )
            assert row["source_row"]["merchant"] == "RAW merchant"
            assert row["expected_merchant"] == "Shown merchant"
            before = row["source_row"]
        asyncio.run(run_migrations(url, chain="finance", schema="finance"))
        with engine.begin() as conn:
            after = conn.execute(
                text("SELECT to_jsonb(t) FROM finance.transactions t WHERE id=:id"),
                {"id": before["id"]},
            ).scalar_one()
            assert after.get("display_merchant") == row["expected_merchant"]
            assert after.get("display_category") == row["expected_category"]
            assert {key: after[key] for key in before} == before
            for metadata, merchant, category in (
                ({}, "RAW merchant", "groceries"),
                ({"normalized_merchant": "", "inferred_category": ""}, "", ""),
                (
                    {"normalized_merchant": None, "inferred_category": None},
                    "RAW merchant",
                    "groceries",
                ),
                (
                    {"normalized_merchant": "Replaced", "inferred_category": "other"},
                    "Replaced",
                    "other",
                ),
            ):
                current = conn.execute(
                    text(
                        "UPDATE finance.transactions SET metadata=CAST(:meta AS jsonb) "
                        "WHERE id=:id RETURNING display_merchant,display_category"
                    ),
                    {"meta": json.dumps(metadata), "id": before["id"]},
                ).one()
                assert tuple(current) == (merchant, category)
    finally:
        engine.dispose()


_C_KEYS = {
    "finance": {
        **dict.fromkeys(
            (
                "transactions",
                "accounts",
                "balance_snapshots",
                "bills",
                "subscriptions",
                "budgets",
                "categories",
                "merchant_mappings",
                "recurring_groups",
                "obligation_ledger",
                "transaction_corrections",
                "payment_destinations",
                "facts",
                "json_fields_public",
            ),
            "id",
        ),
        "claim_match_bindings": "claim_id",
    },
    "health": {
        **dict.fromkeys(
            (
                "facts",
                "measurements_public",
                "medications_public",
                "conditions_public",
                "json_fields_public",
                "calendar_sources",
                "calendar_events",
                "calendar_event_instances",
                "medications",
                "medication_doses",
                "conditions",
                "meals",
                "symptoms",
                "research",
            ),
            "id",
        ),
        "visit_briefs": "event_id",
    },
}


@pytest.fixture(scope="module")
def finance_health_projection(logical_postgres: object) -> Iterator[Projection]:
    """Cold daemon order, real roles, full non-JSON source columns and one shared subscriber."""
    name = migration_db_name()
    url = create_migration_db(logical_postgres, name)
    for domain in _C_KEYS:
        for chain in ("core", domain, "memory"):
            asyncio.run(run_migrations(url, chain=chain, schema=domain))
    asyncio.run(_seed_finance_health_json_sources(url))
    engine = create_engine(url)
    tables: dict[str, str] = {}
    entries: list[str] = []
    try:
        with engine.connect() as conn:
            for domain, source_map in _C_KEYS.items():
                for table, key in source_map.items():
                    columns = (
                        conn.execute(
                            text("""
                        SELECT attname,format_type(atttypid,atttypmod) AS type
                        FROM pg_attribute WHERE attrelid=to_regclass(:name) AND attnum>0
                            AND NOT attisdropped AND atttypid<>'jsonb'::regtype
                            AND attname<>'embedding'
                            AND NOT public.mcp_json_credential(public.mcp_json_tokens(attname))
                        ORDER BY attnum
                    """),
                            {"name": f"{domain}.{table}"},
                        )
                        .mappings()
                        .all()
                    )
                    assert columns and key in {col["attname"] for col in columns}
                    tables[f"{domain}.{table}"] = ", ".join(
                        f'"{col["attname"]}" {col["type"]}'
                        + (" PRIMARY KEY" if col["attname"] == key else "")
                        for col in columns
                    )
                    entry = (
                        f"TABLE {domain}.{table} ("
                        + ",".join(f'"{col["attname"]}"' for col in columns)
                        + ")"
                    )
                    if table == "facts":
                        entry += " WHERE (validity = 'active')"
                    entries.append(entry)
    finally:
        engine.dispose()
    publication = "CREATE PUBLICATION finance_health_public FOR " + ",".join(
        [*entries, "TABLE public.projection_barrier"]
    )
    with replicated_projection(
        logical_postgres,
        name,
        publication=publication,
        publication_name="finance_health_public",
        subscription_name="finance_health_sub",
        subscriber_tables=tables,
    ) as built:
        yield built


async def _health_fact(
    conn: asyncpg.Connection, predicate: str, metadata: object, **values: object
) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO health.facts(subject,predicate,content,metadata,sensitivity,scope,valid_at)
        VALUES ($6,$1,'Unchanged content',$2::jsonb,$3,$4,$5) RETURNING id
    """,
        predicate,
        json.dumps(metadata),
        values.get("sensitivity", "confidential"),
        values.get("scope", "arbitrary"),
        values.get("valid_at"),
        "someone else " + uuid.uuid4().hex,
    )


async def _seed_finance_health_json_sources(url: str) -> None:
    """Populate every actual D4 JSONB source before initial copy, using real schemas."""
    source = await asyncpg.connect(url)
    try:
        account = await source.fetchval("""
            INSERT INTO finance.accounts(institution,type,metadata)
            VALUES('C fixture','checking','{"notes":"account"}') RETURNING id
        """)
        await source.execute(
            """
            INSERT INTO finance.balance_snapshots(account_id,balance,as_of_date,metadata)
            VALUES($1,12.30,current_date,'{"notes":"balance"}')
        """,
            account,
        )
        await source.execute("""
            INSERT INTO finance.bills(payee,amount,currency,due_date,frequency,status,metadata)
            VALUES('C fixture',1,'USD',current_date,'one_time','pending','{"notes":"bill"}');
            INSERT INTO finance.subscriptions(service,amount,currency,frequency,next_renewal,status,
                metadata,field_claims)
            VALUES('C fixture',1,'USD','monthly',current_date,'active','{"notes":"subscription"}',
                '[{"field":"renewal","value":"2030-01-01","epoch":1,"resolved":true}]');
            INSERT INTO finance.merchant_mappings(raw_pattern,normalized_merchant,category,metadata)
            VALUES('C fixture','C fixture','groceries','{"notes":"merchant"}');
            INSERT INTO finance.transactions(posted_at,merchant,amount,currency,direction,category,
                metadata,raw_data)
            VALUES(now(),'C fixture',1,'USD','debit','groceries','{"notes":"transaction"}',
                '{"notes":"raw"}');
            INSERT INTO finance.facts(subject,predicate,content,sensitivity,metadata,tags)
            VALUES('not owner','contact_phone','C fixture','confidential','{"notes":"fact"}',
                '["tag"]');
            INSERT INTO health.medications(name,dosage,frequency,schedule)
            VALUES('C fixture','1','daily','["08:00"]');
            INSERT INTO health.meals(type,description,nutrition)
            VALUES('lunch','C fixture','{"notes":"nutrition"}');
            INSERT INTO health.research(title,content,tags)
            VALUES('C fixture','content','["tag"]');
            INSERT INTO health.visit_briefs(event_id,event_start,period_start,digest)
            VALUES('text:key.\\value',now(),now(),'{"notes":"brief"}');
        """)
        calendar = await source.fetchval("""
            INSERT INTO health.calendar_sources(source_key,source_kind,metadata)
            VALUES('C fixture','calendar','{"notes":"source"}') RETURNING id
        """)
        event = await source.fetchval(
            """
            INSERT INTO health.calendar_events(source_id,origin_ref,title,timezone,starts_at,ends_at,
                source_butler,metadata)
            VALUES($1,'C fixture','Appointment','UTC',now(),now()+interval '1 hour','health',
                '{"attendees":[{"email":"allowed@example.invalid"}]}') RETURNING id
        """,
            calendar,
        )
        await source.execute(
            """
            INSERT INTO health.calendar_event_instances(event_id,source_id,origin_instance_ref,
                timezone,starts_at,ends_at,metadata)
            VALUES($1,$2,'C fixture','UTC',now(),now()+interval '1 hour','{"notes":"instance"}')
        """,
            event,
            calendar,
        )
        await _health_fact(source, "medication", {"name": "Initial", "notes": "initial copy"})
        await source.execute("UPDATE health.facts SET tags='[\"initial tag\"]'")
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_finance_health_catalog_and_initial_copy(
    finance_health_projection: Projection,
) -> None:
    p = finance_health_projection
    changed: dict[tuple[str, str, str], object] = {}
    async with p.source() as source, p.subscriber() as subscriber:
        for domain, tables in _C_KEYS.items():
            for table, key in tables.items():
                actual = await source.fetch(
                    """
                    SELECT a.attname FROM pg_constraint c
                    CROSS JOIN LATERAL unnest(c.conkey) AS k(num)
                    JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num
                    WHERE c.conrelid=$1::regclass AND c.contype='p'
                """,
                    f"{domain}.{table}",
                )
                assert [r["attname"] for r in actual] == [key]
                json_columns = await source.fetch(
                    """
                    SELECT attname FROM pg_attribute WHERE attrelid=$1::regclass
                    AND atttypid='jsonb'::regtype AND NOT attisdropped
                """,
                    f"{domain}.{table}",
                )
                triggers = await source.fetch(
                    """
                    SELECT tgargs,tgtype FROM pg_trigger WHERE tgrelid=$1::regclass
                        AND tgname='trg_mcp_json_fields'
                """,
                    f"{domain}.{table}",
                )
                assert bool(triggers) == bool(json_columns)
                if json_columns:
                    assert len(triggers) == 1 and triggers[0]["tgtype"] == 29
                    args = bytes(triggers[0]["tgargs"]).decode().rstrip("\0").split("\0")
                    assert args[:2] == [key, str(table == "facts").lower()]
                    assert set(args[2:]) == {row["attname"] for row in json_columns}
                assert await subscriber.fetchval(f"SELECT count(*) FROM {domain}.{table}") == (
                    await source.fetchval(
                        f"SELECT count(*) FROM {domain}.{table}"
                        + (" WHERE validity='active'" if table == "facts" else "")
                    )
                )
            # Every actual JSONB column has an initial scalar leaf, not merely a
            # registered trigger or an empty initial-copy table.
            for row in await source.fetch(
                """
                SELECT c.relname,a.attname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid WHERE n.nspname=$1
                AND c.relname=ANY($2::text[]) AND a.atttypid='jsonb'::regtype AND NOT a.attisdropped
            """,
                domain,
                list(tables),
            ):
                assert await subscriber.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM {domain}.json_fields_public "
                    "WHERE source_table=$1 AND (path=$2 OR starts_with(path,$2||'.')))",
                    f"{domain}.{row['relname']}",
                    row["attname"],
                )
            assert await source.fetch(f"SELECT * FROM {domain}.json_fields_public ORDER BY id") == (
                await subscriber.fetch(f"SELECT * FROM {domain}.json_fields_public ORDER BY id")
            )
        assert (
            await source.fetchval("""
            SELECT count(*) FROM pg_publication_tables p
            CROSS JOIN LATERAL unnest(p.attnames) col
            JOIN pg_attribute a ON a.attrelid=(quote_ident(p.schemaname)||'.'||
                quote_ident(p.tablename))::regclass AND a.attname=col
            WHERE pubname='finance_health_public' AND a.atttypid='jsonb'::regtype
        """)
            == 0
        )
        for domain, tables in _C_KEYS.items():
            for table, key in tables.items():
                columns = await source.fetch(
                    "SELECT attname FROM pg_attribute "
                    "WHERE attrelid=$1::regclass AND atttypid='jsonb'::regtype AND NOT attisdropped",
                    f"{domain}.{table}",
                )
                for col in columns:
                    column = col["attname"]
                    row = await source.fetchrow(
                        f"SELECT {key} AS key,{column} AS value FROM {domain}.{table} LIMIT 1"
                    )
                    assert row is not None
                    value = json.loads(row["value"])
                    marker = f"allowed:{domain}.{table}.{column}"
                    if column == "field_claims":
                        value.append(
                            {"field": "renewal", "value": marker, "epoch": 2, "resolved": False}
                        )
                    elif isinstance(value, dict):
                        value["round_trip"] = marker
                    else:
                        assert isinstance(value, list)
                        value.append(marker)
                    await source.execute(
                        f"UPDATE {domain}.{table} SET {column}=$2::jsonb WHERE {key}=$1",
                        row["key"],
                        json.dumps(value),
                    )
                    assert await source.fetchval(
                        f"SELECT EXISTS(SELECT 1 FROM {domain}.json_fields_public "
                        "WHERE source_table=$1 AND row_id=$2 AND value_text=$3)",
                        f"{domain}.{table}",
                        str(row["key"]),
                        marker,
                    )
                    changed[domain, table, key] = row["key"]
    await p.caught_up()
    async with p.source() as source, p.subscriber() as subscriber:
        for domain in _C_KEYS:
            assert await source.fetch(f"SELECT * FROM {domain}.json_fields_public ORDER BY id") == (
                await subscriber.fetch(f"SELECT * FROM {domain}.json_fields_public ORDER BY id")
            )
        for (domain, table, key), identity in changed.items():
            await source.execute(f"DELETE FROM {domain}.{table} WHERE {key}=$1", identity)
    await p.caught_up()
    async with p.subscriber() as subscriber:
        for (domain, table, key), identity in changed.items():
            assert not await subscriber.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {domain}.json_fields_public "
                "WHERE source_table=$1 AND row_id=$2)",
                f"{domain}.{table}",
                str(identity),
            )
            assert not await subscriber.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {domain}.{table} WHERE {key}=$1)", identity
            )


@pytest.mark.asyncio
async def test_finance_health_json_paths_credentials_and_lifecycle(
    finance_health_projection: Projection,
) -> None:
    p = finance_health_projection
    payload = {
        "amount": 12.3,
        "string": "12.30",
        "truth": True,
        "null": None,
        "a.b": {"x\\y": [1, {"0": "object", "[0]": "literal"}]},
        "a": {"b": "nested"},
        "c.d": "literal collision",
        "c": {"d": "nested collision"},
        "": "empty",
        "last_four": "4242",
        "secretary": "allowed",
        "tokenization": "allowed",
        "contact": "allowed",
        "accessToken": "omit",
        "Client-Secret": {"neighbor": "omit"},
        "api_key": "omit",
        "simplefin_access_url": "omit",
        "portal.password": "omit",
        "api": {"key": "omit", "description": "allowed"},
        "array": [{"auth": "omit", "notes": "allowed"}],
    }
    payload.update(
        {
            key: "omit"
            for key in (
                "secret",
                "token",
                "password",
                "credential",
                "otp",
                "cvv",
                "card",
                "private_key",
                "access_key",
                "client_key",
                "signing_key",
                "passcode",
                "passphrase",
                "pin",
                "bearer",
                "cookie",
                "auth",
                "private key",
                "access-key",
            )
        }
    )
    async with p.source() as source:
        async with source.transaction():
            await source.execute("SET LOCAL ROLE butler_finance_rw")
            assert await source.fetchval("SELECT current_user") == "butler_finance_rw"
            row = await source.fetchrow(
                """
                INSERT INTO finance.transactions(posted_at,merchant,amount,currency,direction,
                    category,metadata,raw_data,deleted_at)
                VALUES(now(),'Raw',12.30,'USD','debit','groceries',$1::jsonb,
                    '{"amount":"other column"}',now()) RETURNING id,created_at,updated_at
            """,
                json.dumps(payload),
            )
            assert (
                await source.fetchval(
                    "SELECT display_merchant FROM finance.transactions WHERE id=$1", row["id"]
                )
                == "Raw"
            )
        fields = {
            r["path"]: (r["value_text"], r["value_numeric"], r["value_type"])
            for r in await source.fetch(
                "SELECT * FROM finance.json_fields_public WHERE row_id=$1", str(row["id"])
            )
        }
        expected_paths = {
            "metadata.amount",
            "metadata.string",
            "metadata.truth",
            "metadata.null",
            r"metadata.a\.b.x\\y.[0]",
            r"metadata.a\.b.x\\y.[1].0",
            r"metadata.a\.b.x\\y.[1].\[0\]",
            "metadata.a.b",
            r"metadata.c\.d",
            "metadata.c.d",
            "metadata.",
            "metadata.last_four",
            "metadata.secretary",
            "metadata.tokenization",
            "metadata.contact",
            "metadata.api.description",
            "metadata.array.[0].notes",
            "raw_data.amount",
        }
        assert set(fields) == expected_paths
        assert fields["metadata.truth"] == ("true", None, "boolean")
        assert fields["metadata.null"] == (None, None, "null")
        assert str(fields["metadata.amount"][1]) == "12.3"
        assert fields["metadata.string"] == ("12.30", None, "string")
        assert fields["raw_data.amount"] == ("other column", None, "string")
        assert await source.fetchval(
            "SELECT bool_and(id=jsonb_build_array(source_table,row_id,path)::text)"
            " FROM finance.json_fields_public WHERE row_id=$1",
            str(row["id"]),
        )
        # Rollback restores both the source and its derived leaves.
        transaction = source.transaction()
        await transaction.start()
        await source.execute("UPDATE finance.transactions SET metadata='{}' WHERE id=$1", row["id"])
        assert not await source.fetchval(
            "SELECT EXISTS(SELECT 1 FROM finance.json_fields_public "
            "WHERE row_id=$1 AND path='metadata.amount')",
            str(row["id"]),
        )
        await transaction.rollback()
        assert await source.fetchval(
            "SELECT count(*) FROM finance.json_fields_public WHERE row_id=$1", str(row["id"])
        ) == len(fields)
    await p.caught_up()
    async with p.source() as source, p.subscriber() as subscriber:
        query = "SELECT * FROM finance.json_fields_public WHERE row_id=$1 ORDER BY path"
        assert await source.fetch(query, str(row["id"])) == await subscriber.fetch(
            query, str(row["id"])
        )
        new_id = uuid.uuid4()
        await source.execute(
            "UPDATE finance.transactions SET id=$2,metadata='[true,null,\"x\"]' WHERE id=$1",
            row["id"],
            new_id,
        )
    await p.caught_up()
    async with p.source() as source, p.subscriber() as subscriber:
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM finance.json_fields_public WHERE row_id=$1)",
            str(row["id"]),
        )
        assert await source.fetch(query, str(new_id)) == await subscriber.fetch(query, str(new_id))
        assert (
            await subscriber.fetchval(
                "SELECT count(*) FROM finance.json_fields_public WHERE row_id=$1", str(new_id)
            )
            == 4
        )
        await source.execute("DELETE FROM finance.transactions WHERE id=$1", new_id)
    await p.caught_up()
    async with p.subscriber() as subscriber:
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM finance.json_fields_public WHERE row_id=$1)", str(new_id)
        )


@pytest.mark.asyncio
async def test_health_typed_shapes_and_qualification(finance_health_projection: Projection) -> None:
    p = finance_health_projection
    async with p.source() as source:
        async with source.transaction():
            await source.execute("SET LOCAL ROLE butler_health_rw")
            assert await source.fetchval("SELECT current_user") == "butler_health_rw"
            measured = await _health_fact(
                source,
                "measurement_blood_pressure",
                {
                    "value": {
                        "systolic": 120,
                        "a:b.c": 80,
                        "pin": 1234,
                        "accessToken": 42,
                        "bool": True,
                        "null": None,
                        "string": "81",
                        "object": {"x": 1},
                    },
                    "notes": " measurement note ",
                },
            )
            medication = await _health_fact(
                source,
                "medication",
                {
                    "name": "Med",
                    "dosage": "5 mg",
                    "frequency": "daily",
                    "schedule": ["08:00", "", "08:00", None, False],
                    "active": False,
                    "notes": " note ",
                },
            )
            condition = await _health_fact(
                source,
                "condition",
                {
                    "name": "Condition",
                    "status": "managed",
                    "diagnosed_at": "not a timestamp",
                    "notes": " condition note ",
                },
                sensitivity="pii",
            )
            scalar = await _health_fact(
                source,
                "measurement_new_metric",
                {"value": 1.25, "unit": None},
                valid_at=datetime(2021, 1, 2, tzinfo=UTC),
            )
            defaults = await _health_fact(source, "medication", {})
        readings = await source.fetch(
            "SELECT * FROM health.measurements_public WHERE id LIKE $1 ORDER BY id",
            str(measured) + "%",
        )
        assert {r["id"] for r in readings} == {
            str(measured) + ":systolic",
            str(measured) + ":a:b.c",
        }
        assert {r["metric"] for r in readings} == {
            "measurement_blood_pressure.systolic",
            "measurement_blood_pressure.a:b.c",
        }
        assert all(r["unit"] == "mmHg" and r["measured_at"] is None for r in readings)
        assert (
            await source.fetchval(
                "SELECT unit FROM health.measurements_public WHERE id=$1", str(scalar)
            )
            is None
        )
        assert await source.fetchval(
            "SELECT measured_at FROM health.measurements_public WHERE id=$1", str(scalar)
        ) == datetime(2021, 1, 2, tzinfo=UTC)
        med = await source.fetchrow(
            "SELECT * FROM health.medications_public WHERE id=$1", medication
        )
        assert med["schedule"] == ["08:00", "", "08:00", None, None]
        assert med["active"] is False and med["notes"] == " note "
        assert med["recorded_at"] == await source.fetchval(
            "SELECT created_at FROM health.facts WHERE id=$1", medication
        )
        assert (
            await source.fetchval(
                "SELECT diagnosed_at FROM health.conditions_public WHERE id=$1", condition
            )
            == "not a timestamp"
        )
        assert tuple(
            await source.fetchrow(
                "SELECT name,schedule,active FROM health.medications_public WHERE id=$1", defaults
            )
        ) == ("", [], True)
        await source.execute(
            "UPDATE health.facts SET metadata=$2::jsonb WHERE id=$1",
            defaults,
            json.dumps({"name": None, "schedule": None, "active": None, "notes": False}),
        )
        assert tuple(
            await source.fetchrow(
                "SELECT name,schedule,active,notes FROM health.medications_public WHERE id=$1",
                defaults,
            )
        ) == (None, None, None, None)
        # Full metadata breadth keeps even unsupported typed shapes as permitted leaves.
        assert (
            await source.fetchval(
                "SELECT value_type FROM health.json_fields_public "
                "WHERE row_id=$1 AND path='metadata.notes'",
                str(defaults),
            )
            == "boolean"
        )
        await source.execute(
            'UPDATE health.facts SET metadata=\'{"schedule":"09:00"}\' WHERE id=$1', defaults
        )
        assert await source.fetchval(
            "SELECT schedule FROM health.medications_public WHERE id=$1", defaults
        ) == ["09:00"]
        await source.execute(
            'UPDATE health.facts SET metadata=\'{"schedule":{},"active":"bad"}\' WHERE id=$1',
            defaults,
        )
        assert tuple(
            await source.fetchrow(
                "SELECT schedule,active FROM health.medications_public WHERE id=$1", defaults
            )
        ) == (None, None)
        trial = source.transaction()
        await trial.start()
        await source.execute("UPDATE health.facts SET validity='expired' WHERE id=$1", medication)
        assert not await source.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.medications_public WHERE id=$1)", medication
        )
        await trial.rollback()
        assert await source.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.medications_public WHERE id=$1)", medication
        )
    await p.caught_up()
    async with p.source() as source, p.subscriber() as subscriber:
        for table in (
            "measurements_public",
            "medications_public",
            "conditions_public",
            "json_fields_public",
        ):
            assert await source.fetch(f"SELECT * FROM health.{table} ORDER BY id") == (
                await subscriber.fetch(f"SELECT * FROM health.{table} ORDER BY id")
            )
        await source.execute(
            "UPDATE health.facts SET predicate='medication',metadata='{}' WHERE id=$1", measured
        )
        assert not await source.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.measurements_public WHERE id LIKE $1)",
            str(measured) + "%",
        )
        assert await source.fetchval(
            "SELECT active FROM health.medications_public WHERE id=$1", measured
        )
        await source.execute("UPDATE health.facts SET validity='fading' WHERE id=$1", medication)
        await source.execute("DELETE FROM health.facts WHERE id=$1", condition)
        new_id = uuid.uuid4()
        await source.execute("UPDATE health.facts SET id=$2 WHERE id=$1", defaults, new_id)
    await p.caught_up()
    async with p.subscriber() as subscriber:
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.medications_public WHERE id=$1)", medication
        )
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.conditions_public WHERE id=$1)", condition
        )
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.json_fields_public WHERE row_id=$1)",
            str(medication),
        )
        assert not await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.medications_public WHERE id=$1)", defaults
        )
        assert await subscriber.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health.medications_public WHERE id=$1)", new_id
        )


def test_finance_health_peer_downgrade_preserves_other_domain(logical_postgres: object) -> None:
    """Leaving one domain/core peer cannot remove the other domain's live boundary."""
    url = create_migration_db(logical_postgres, migration_db_name())
    for domain in ("finance", "health"):
        for chain in ("core", domain, "memory"):
            asyncio.run(run_migrations(url, chain=chain, schema=domain))

    def downgrade(chain: str, schema: str, revision: str) -> None:
        command.downgrade(_build_alembic_config(url, target_schema=schema), f"{chain}@{revision}")

    engine = create_engine(url)
    try:
        # Core can leave first without cascading through live domain consumers.
        downgrade("core", "finance", "core_319")
        downgrade("finance", "finance", "finance_019")
        with engine.begin() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('finance.json_fields_public')")).scalar()
                is None
            )
            assert conn.execute(
                text("SELECT to_regprocedure('public.mcp_sync_json_fields()')")
            ).scalar()
            fact = conn.execute(
                text("""
                INSERT INTO health.facts(subject,predicate,content,metadata)
                VALUES('peer survivor','medication','source','{"name":"Survives"}') RETURNING id
            """)
            ).scalar_one()
            assert (
                conn.execute(
                    text("SELECT name FROM health.medications_public WHERE id=:id"), {"id": fact}
                ).scalar_one()
                == "Survives"
            )
        # Reupgrade the first peer while the second remains fully installed.
        asyncio.run(run_migrations(url, chain="core", schema="finance"))
        asyncio.run(run_migrations(url, chain="finance", schema="finance"))
        downgrade("health", "health", "health_006")
        downgrade("core", "health", "core_319")
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regprocedure('public.mcp_sync_json_fields()')")
            ).scalar()
            assert (
                conn.execute(text("SELECT count(*) FROM finance.json_fields_public")).scalar() == 0
            )
        downgrade("finance", "finance", "finance_019")
        downgrade("core", "finance", "core_319")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT to_regprocedure('public.mcp_sync_json_fields()')")
                ).scalar()
                is None
            )
        asyncio.run(run_migrations(url, chain="core", schema="health"))
        asyncio.run(run_migrations(url, chain="health", schema="health"))
        asyncio.run(run_migrations(url, chain="memory", schema="health"))
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT name FROM health.medications_public WHERE id=:id"), {"id": fact}
                ).scalar_one()
                == "Survives"
            )
    finally:
        engine.dispose()
