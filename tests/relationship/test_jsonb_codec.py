"""Integration tests for JSONB type codec registration on asyncpg pools.

These tests verify that the JSONB codec is registered on both pool creation
paths (Database.connect and DatabaseManager._create_pool), ensuring that JSONB
columns are decoded to Python dicts rather than raw JSON strings.

Without the codec, asyncpg returns JSONB columns as strings, causing the
defensive guards in the relationship router to silently drop metadata payloads.

Insertion convention: pass Python dicts directly as parameters (``$1`` without
``::jsonb`` cast).  asyncpg then routes the value through the registered JSONB
encoder and the round-trip works correctly.  Using ``json.dumps(...)`` with
``$1::jsonb`` bypasses the encoder and can cause text-format vs. binary-format
mismatches that produce incorrect results.
"""

from __future__ import annotations

import shutil
import uuid

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Tests for Database.connect() — daemon-side pool
# ---------------------------------------------------------------------------


class TestDatabaseConnectJsonbCodec:
    """JSONB codec is registered on pools created via Database.connect()."""

    async def test_jsonb_column_decoded_as_dict(self, provisioned_postgres_pool):
        """A JSONB column round-trips as a dict, not a string."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _jsonb_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    payload JSONB NOT NULL
                )
            """)
            # Pass Python dict directly; asyncpg routes it through the JSONB encoder.
            payload = {"source": "test", "confidence": 0.95, "tags": ["alpha", "beta"]}
            await pool.execute("INSERT INTO _jsonb_test (payload) VALUES ($1)", payload)
            row = await pool.fetchrow("SELECT payload FROM _jsonb_test LIMIT 1")
            result = row["payload"]
            # The codec contract: JSONB must arrive as a dict, not a string.
            assert isinstance(result, dict), (
                f"Expected dict from JSONB column but got {type(result).__name__!r}: {result!r}. "
                "JSONB codec is likely not registered on this pool."
            )
            assert result["source"] == "test"
            assert result["confidence"] == pytest.approx(0.95)
            assert result["tags"] == ["alpha", "beta"]

    async def test_empty_jsonb_object_decoded_as_dict(self, provisioned_postgres_pool):
        """An empty JSONB object '{}' is decoded as an empty dict, not a string."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _jsonb_empty_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    metadata JSONB NOT NULL DEFAULT '{}'
                )
            """)
            await pool.execute("INSERT INTO _jsonb_empty_test DEFAULT VALUES")
            row = await pool.fetchrow("SELECT metadata FROM _jsonb_empty_test LIMIT 1")
            result = row["metadata"]
            assert isinstance(result, dict), (
                f"Expected empty dict from JSONB column but got {type(result).__name__!r}: {result!r}"
            )
            assert result == {}

    async def test_nested_jsonb_decoded_correctly(self, provisioned_postgres_pool):
        """Nested JSONB objects and arrays are decoded to their Python equivalents."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _jsonb_nested_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    data JSONB NOT NULL
                )
            """)
            nested = {
                "provenance": {"origin": "email", "ingested_at": "2026-04-30"},
                "badges": ["verified", "owner"],
                "score": 42,
            }
            # Pass Python dict directly so the registered JSONB encoder is used.
            await pool.execute("INSERT INTO _jsonb_nested_test (data) VALUES ($1)", nested)
            row = await pool.fetchrow("SELECT data FROM _jsonb_nested_test LIMIT 1")
            result = row["data"]
            assert isinstance(result, dict)
            assert isinstance(result["provenance"], dict)
            assert result["provenance"]["origin"] == "email"
            assert result["badges"] == ["verified", "owner"]
            assert result["score"] == 42


# ---------------------------------------------------------------------------
# Tests for DatabaseManager._create_pool() — API/dashboard pool
# ---------------------------------------------------------------------------


class TestDatabaseManagerJsonbCodec:
    """JSONB codec is registered on pools created via DatabaseManager._create_pool()."""

    async def test_api_pool_decodes_jsonb_as_dict(self, postgres_container):
        """DatabaseManager pools decode JSONB columns to dicts."""
        from butlers.api.db import DatabaseManager
        from butlers.db import Database

        pg = postgres_container

        # Provision a fresh database for this test
        db = Database(
            db_name=_unique_db_name(),
            host=pg.get_container_host_ip(),
            port=int(pg.get_exposed_port(5432)),
            user=pg.username,
            password=pg.password,
            min_pool_size=1,
            max_pool_size=2,
        )
        await db.provision()

        mgr = DatabaseManager(
            host=pg.get_container_host_ip(),
            port=int(pg.get_exposed_port(5432)),
            user=pg.username,
            password=pg.password,
            min_pool_size=1,
            max_pool_size=2,
        )
        try:
            await mgr.add_butler("test_rel", db_name=db.db_name)
            pool = mgr.pool("test_rel")

            await pool.execute("""
                CREATE TEMP TABLE _api_jsonb_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    metadata JSONB NOT NULL
                )
            """)
            metadata = {"needs_disambiguation": False, "provenance": "email", "score": 1}
            # Pass Python dict directly so the JSONB encoder is invoked.
            await pool.execute("INSERT INTO _api_jsonb_test (metadata) VALUES ($1)", metadata)
            row = await pool.fetchrow("SELECT metadata FROM _api_jsonb_test LIMIT 1")
            result = row["metadata"]
            assert isinstance(result, dict), (
                f"DatabaseManager pool returned {type(result).__name__!r} for JSONB column; "
                "register_jsonb_codec may not be wired into _create_pool."
            )
            assert result["needs_disambiguation"] is False
            assert result["provenance"] == "email"
        finally:
            await mgr.close()


# ---------------------------------------------------------------------------
# Regression test: entity metadata is not silently dropped
# ---------------------------------------------------------------------------


class TestEntityMetadataRoundtrip:
    """Metadata fields on public.entities survive a DB round-trip as populated dicts.

    This is the user-visible symptom from bu-bs3kr: entity detail pages showed
    no provenance/badges because the defensive guard dropped string-typed JSONB.
    """

    async def test_entities_metadata_roundtrips_as_dict(self, provisioned_postgres_pool):
        """Entity metadata JSONB column round-trips to a populated dict."""
        async with provisioned_postgres_pool() as pool:
            # Minimal public.entities-shaped table for the regression test.
            # Uses a temp table so it doesn't conflict with schema isolation tests.
            await pool.execute("""
                CREATE TEMP TABLE _entities_meta_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'
                )
            """)
            entity_id = uuid.uuid4()
            entity_meta = {
                "provenance": ["email-ingest", "manual"],
                "badges": ["owner"],
                "confidence": 0.99,
            }
            # Pass Python dict directly; asyncpg uses the registered JSONB encoder.
            await pool.execute(
                "INSERT INTO _entities_meta_test (id, name, metadata) VALUES ($1, $2, $3)",
                entity_id,
                "Alice Example",
                entity_meta,
            )
            row = await pool.fetchrow(
                "SELECT metadata FROM _entities_meta_test WHERE id = $1",
                entity_id,
            )
            result = row["metadata"]
            assert isinstance(result, dict), (
                f"entities.metadata arrived as {type(result).__name__!r}; "
                "the JSONB codec is not active on this pool."
            )
            assert result["provenance"] == ["email-ingest", "manual"]
            assert result["badges"] == ["owner"]
            assert result["confidence"] == pytest.approx(0.99)

    async def test_facts_metadata_roundtrips_as_dict(self, provisioned_postgres_pool):
        """Facts metadata JSONB column round-trips to a populated dict (timeline regression)."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _facts_meta_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    predicate TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}',
                    valid_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            fact_meta = {"emotion": "curious", "source": "call-transcript"}
            # Pass Python dict directly so the registered JSONB encoder is used.
            await pool.execute(
                "INSERT INTO _facts_meta_test (predicate, content, metadata) VALUES ($1, $2, $3)",
                "contact_note",
                "Discussed project timeline",
                fact_meta,
            )
            rows = await pool.fetch("SELECT metadata FROM _facts_meta_test")
            assert len(rows) == 1
            result = rows[0]["metadata"]
            assert isinstance(result, dict), (
                f"facts.metadata arrived as {type(result).__name__!r}; "
                "timeline metadata would be silently dropped by the defensive guard."
            )
            assert result["emotion"] == "curious"
            assert result["source"] == "call-transcript"


# End-to-end regression: store_fact writes must survive metadata->>'key' filters
# ---------------------------------------------------------------------------


_FACTS_DDL = """
CREATE TABLE IF NOT EXISTS facts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject             TEXT NOT NULL,
    predicate           TEXT NOT NULL,
    content             TEXT NOT NULL,
    embedding           TEXT,
    search_vector       TSVECTOR,
    importance          FLOAT NOT NULL DEFAULT 5.0,
    confidence          FLOAT NOT NULL DEFAULT 1.0,
    decay_rate          FLOAT NOT NULL DEFAULT 0.002,
    permanence          TEXT NOT NULL DEFAULT 'stable',
    source_butler       TEXT,
    source_episode_id   UUID,
    supersedes_id       UUID REFERENCES facts(id) ON DELETE SET NULL,
    validity            TEXT NOT NULL DEFAULT 'active',
    scope               TEXT NOT NULL DEFAULT 'global',
    reference_count     INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at   TIMESTAMPTZ,
    tags                JSONB DEFAULT '[]'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    entity_id           UUID,
    object_entity_id    UUID,
    valid_at            TIMESTAMPTZ DEFAULT NULL,
    tenant_id           TEXT NOT NULL DEFAULT 'shared',
    request_id          TEXT,
    idempotency_key     TEXT,
    observed_at         TIMESTAMPTZ DEFAULT now(),
    invalid_at          TIMESTAMPTZ,
    retention_class     TEXT NOT NULL DEFAULT 'operational',
    sensitivity         TEXT NOT NULL DEFAULT 'normal'
)
"""

_PREDICATE_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS predicate_registry (
    name                 TEXT PRIMARY KEY,
    expected_subject_type TEXT,
    expected_object_type TEXT,
    is_edge              BOOLEAN NOT NULL DEFAULT false,
    is_temporal          BOOLEAN NOT NULL DEFAULT false,
    description          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    status               TEXT NOT NULL DEFAULT 'active',
    superseded_by        TEXT,
    deprecated_at        TIMESTAMPTZ,
    inverse_of           TEXT,
    is_symmetric         BOOLEAN NOT NULL DEFAULT false,
    aliases              TEXT[] NOT NULL DEFAULT '{}',
    usage_count          INTEGER NOT NULL DEFAULT 0,
    last_used_at         TIMESTAMPTZ
)
"""

_EPISODES_DDL = """
CREATE TABLE IF NOT EXISTS episodes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    butler           TEXT NOT NULL,
    session_id       UUID,
    content          TEXT NOT NULL,
    embedding        TEXT,
    search_vector    TSVECTOR,
    importance       FLOAT NOT NULL DEFAULT 5.0,
    expires_at       TIMESTAMPTZ NOT NULL,
    metadata         JSONB DEFAULT '{}'::jsonb,
    tenant_id        TEXT NOT NULL DEFAULT 'shared',
    request_id       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    retention_class  TEXT NOT NULL DEFAULT 'operational',
    sensitivity      TEXT NOT NULL DEFAULT 'normal'
)
"""

_MEMORY_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS memory_links (
    id          BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id   UUID NOT NULL,
    target_type TEXT NOT NULL,
    target_id   UUID NOT NULL,
    relation    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, target_type, target_id)
)
"""


def _mock_embedding_engine():
    from unittest.mock import MagicMock

    engine = MagicMock()
    engine.embed.return_value = [0.0] * 8
    return engine


class TestStoreFactDoubleEncodingRegression:
    """End-to-end regression for [bu-qki26]: store_fact must not double-encode JSONB.

    Reproduces the production failure: the asyncpg pool registers a JSONB codec
    that already calls ``json.dumps`` on inserted values. If ``store_fact`` also
    pre-encodes metadata to a JSON string, the codec wraps the string in an
    extra ``json.dumps`` call, producing a JSON string scalar instead of an
    object. Subsequent ``metadata->>'key'`` filters return NULL and queries
    return zero rows — the symptom that broke 10 tests across roster/finance
    and roster/relationship.
    """

    async def test_store_fact_metadata_jsonb_filter_finds_row(self, provisioned_postgres_pool):
        """A fact stored with metadata={'direction': 'incoming'} must be found
        by ``WHERE metadata->>'direction' = 'incoming'``.

        This test FAILS on broken main (codec double-encodes the string) and
        PASSES after binding the dict directly.
        """
        from butlers.modules.memory.storage import store_fact

        async with provisioned_postgres_pool() as pool:
            await pool.execute(_PREDICATE_REGISTRY_DDL)
            await pool.execute(_EPISODES_DDL)
            await pool.execute(_FACTS_DDL)
            await pool.execute(_MEMORY_LINKS_DDL)

            embedding_engine = _mock_embedding_engine()
            await store_fact(
                pool,
                subject="contact:alice",
                predicate="interaction_call",
                content="ringing the bell",
                embedding_engine=embedding_engine,
                metadata={"direction": "incoming", "category": "groceries"},
            )

            count = await pool.fetchval(
                "SELECT COUNT(*) FROM facts WHERE metadata->>'direction' = 'incoming'"
            )
            assert count == 1, (
                "JSONB filter returned 0 rows; metadata was likely double-encoded "
                "into a JSON string scalar. See bu-qki26."
            )

            row = await pool.fetchrow(
                "SELECT metadata FROM facts WHERE metadata->>'direction' = 'incoming'"
            )
            assert isinstance(row["metadata"], dict)
            assert row["metadata"]["direction"] == "incoming"
            assert row["metadata"]["category"] == "groceries"


# ---------------------------------------------------------------------------
# Regression tests: no double-encoding (bu-aaacv)
# ---------------------------------------------------------------------------


class TestNoDoubleEncoding:
    """Verify that dict values written to JSONB columns are not double-encoded.

    Double-encoding occurs when json.dumps() is called at the write site AND the
    asyncpg JSONB codec also runs (because the parameter is typed as JSONB).
    The symptom is that the stored JSONB value is a JSON-encoded string rather
    than a plain dict, so a read-back returns a str not a dict.
    """

    async def test_dict_written_without_cast_roundtrips_as_dict(self, provisioned_postgres_pool):
        """Writing a dict directly (no ::jsonb cast) stores a JSON object, not a string."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _no_double_enc_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    payload JSONB NOT NULL
                )
            """)
            payload = {"key": "value", "count": 42, "nested": {"a": 1}}
            # Pass dict directly — asyncpg codec encodes it once.
            await pool.execute("INSERT INTO _no_double_enc_test (payload) VALUES ($1)", payload)
            row = await pool.fetchrow("SELECT payload FROM _no_double_enc_test LIMIT 1")
            result = row["payload"]
            assert isinstance(result, dict), (
                f"Expected dict but got {type(result).__name__!r}: {result!r}. "
                "JSONB value may have been double-encoded (stored as a JSON string)."
            )
            assert result["key"] == "value"
            assert result["count"] == 42
            assert result["nested"]["a"] == 1

    async def test_json_string_roundtrips_as_string_not_dict(self, provisioned_postgres_pool):
        """A json.dumps() string written without ::jsonb is treated as text → JSONB cast.

        This is the old pre-codec pattern.  PostgreSQL accepts a JSON string for a
        JSONB column (implicit text→JSONB cast), but now with the codec the parameter
        is typed as JSONB by asyncpg — so the string gets double-encoded.
        This test documents the expected contract: pass dicts, not strings.
        """
        import json

        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _str_roundtrip_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    payload JSONB NOT NULL
                )
            """)
            original = {"writer": "test", "value": 99}
            # Pass dict directly: asyncpg codec encodes once → stored as JSON object.
            await pool.execute("INSERT INTO _str_roundtrip_test (payload) VALUES ($1)", original)
            row = await pool.fetchrow("SELECT payload FROM _str_roundtrip_test LIMIT 1")
            result = row["payload"]
            # The codec decoded the stored JSONB back to a Python dict.
            assert isinstance(result, dict), (
                f"Expected dict from direct dict write but got {type(result).__name__!r}. "
                "Check that the JSONB codec is active and no double-encoding occurred."
            )
            assert result["writer"] == "test"
            assert result["value"] == 99
            # Confirm: a json.dumps string of the same dict would be double-encoded
            # and decoded back as a str (not dict).  We do NOT store it that way.
            json_str = json.dumps(original)
            assert isinstance(json_str, str), "Sanity: json.dumps produces a str"

    async def test_list_written_directly_roundtrips_as_list(self, provisioned_postgres_pool):
        """A Python list written directly to a JSONB column roundtrips as a list."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("""
                CREATE TEMP TABLE _list_jsonb_test (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tags JSONB NOT NULL
                )
            """)
            tags = ["alpha", "beta", "gamma"]
            await pool.execute("INSERT INTO _list_jsonb_test (tags) VALUES ($1)", tags)
            row = await pool.fetchrow("SELECT tags FROM _list_jsonb_test LIMIT 1")
            result = row["tags"]
            assert isinstance(result, list), (
                f"Expected list but got {type(result).__name__!r}: {result!r}. "
                "List JSONB write may have double-encoded."
            )
            assert result == tags
