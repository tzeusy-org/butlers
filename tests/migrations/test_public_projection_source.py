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
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

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
    "public.projection_barrier": "id UUID PRIMARY KEY",
}
_SUBSCRIBER_DDL = (
    "CREATE SCHEMA general; CREATE SCHEMA relationship; CREATE SCHEMA health; "
    + " ".join(
        f"CREATE TABLE {table} ({columns});" for table, columns in _SUBSCRIBER_TABLES.items()
    )
)

# Parity with the source expressions, written independently of the migration's SQL.
_ENTITY_PARITY = """
SELECT id FROM public.entities
WHERE merged_into::text IS DISTINCT FROM CASE
        WHEN metadata ->> 'merged_into' ~ '^[0-9a-fA-F-]{36}$'
        THEN lower(metadata ->> 'merged_into') END
   OR deleted IS DISTINCT FROM (
        metadata ->> 'deleted_at' IS NOT NULL
        OR metadata ->> 'tombstone' IS NOT DISTINCT FROM 'true'
        OR (metadata ->> 'merged_into' IS NOT NULL
            AND metadata ->> 'merged_into' !~ '^[0-9a-fA-F-]{36}$'))
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

    subscriber_name = migration_db_name()
    source_url = _asyncpg_url(logical_postgres, source_name)
    subscriber_url = _asyncpg_url(logical_postgres, subscriber_name)
    admin_url = _asyncpg_url(logical_postgres, "postgres")

    async def _connect() -> None:
        admin = await asyncpg.connect(admin_url)
        try:
            await admin.execute(f'CREATE DATABASE "{subscriber_name}"')
            port = await admin.fetchval("SHOW port")
        finally:
            await admin.close()
        source = await asyncpg.connect(source_url)
        try:
            await source.execute("CREATE TABLE public.projection_barrier (id UUID PRIMARY KEY)")
            await source.execute(_PUBLICATION)
            await source.fetchval(
                "SELECT pg_create_logical_replication_slot('butlers_public_sub', 'pgoutput')"
            )
        finally:
            await source.close()
        subscriber = await asyncpg.connect(subscriber_url)
        try:
            await subscriber.execute(_SUBSCRIBER_DDL)
            user = logical_postgres.username
            password = logical_postgres.password
            conninfo = (
                f"host=127.0.0.1 port={port} dbname={source_name} user={user} password={password}"
            )
            await subscriber.execute(
                f"CREATE SUBSCRIPTION butlers_public_sub CONNECTION '{conninfo}' "
                "PUBLICATION butlers_public_v1 "
                "WITH (create_slot = false, slot_name = 'butlers_public_sub')"
            )
        finally:
            await subscriber.close()

    asyncio.run(_connect())
    built = Projection(source_url=source_url, subscriber_url=subscriber_url)
    asyncio.run(built.caught_up())
    yield built

    async def _disconnect() -> None:
        subscriber = await asyncpg.connect(subscriber_url)
        try:
            await subscriber.execute("DROP SUBSCRIPTION butlers_public_sub")
        finally:
            await subscriber.close()

    asyncio.run(_disconnect())


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


@pytest.mark.parametrize("role", ["butler_general_rw", "butler_relationship_rw"])
async def test_a_butler_role_writes_stubs_without_holding_the_counts_table(
    projection: Projection, role: str
) -> None:
    """An edge writer with no privilege on the counts table still keeps them, through the definer.

    Butler roles read the table and never write it, and ``chronicler_memory_owner`` deletes edges
    holding nothing on it, so the invariant must not rest on what a bootstrap replay grants.
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
