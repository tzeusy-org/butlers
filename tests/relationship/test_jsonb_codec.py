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
