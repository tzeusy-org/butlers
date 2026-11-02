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

Every test writes to real migrated JSONB columns: ``public.state.value`` and
``public.entities.metadata`` from the core chain, ``facts.metadata`` from the
memory chain.
"""

from __future__ import annotations

import shutil
import uuid
from urllib.parse import urlsplit

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


async def _put_state(pool, value: object) -> str:
    """Write *value* to the real ``state.value`` JSONB column; return its key."""
    key = f"jsonb-codec-{uuid.uuid4().hex[:12]}"
    await pool.execute("INSERT INTO state (key, value) VALUES ($1, $2)", key, value)
    return key


async def _get_state(pool, key: str) -> object:
    return await pool.fetchval("SELECT value FROM state WHERE key = $1", key)


# ---------------------------------------------------------------------------
# Tests for Database.connect() — daemon-side pool
# ---------------------------------------------------------------------------


class TestDatabaseConnectJsonbCodec:
    """JSONB codec is registered on pools created via Database.connect()."""

    async def test_jsonb_column_decoded_as_dict(self, db_pool):
        """A JSONB column round-trips as a dict, not a string."""
        pool = db_pool
        # Pass Python dict directly; asyncpg routes it through the JSONB encoder.
        payload = {"source": "test", "confidence": 0.95, "tags": ["alpha", "beta"]}
        result = await _get_state(pool, await _put_state(pool, payload))
        # The codec contract: JSONB must arrive as a dict, not a string.
        assert isinstance(result, dict), (
            f"Expected dict from JSONB column but got {type(result).__name__!r}: {result!r}. "
            "JSONB codec is likely not registered on this pool."
        )
        assert result["source"] == "test"
        assert result["confidence"] == pytest.approx(0.95)
        assert result["tags"] == ["alpha", "beta"]

    async def test_empty_jsonb_object_decoded_as_dict(self, db_pool):
        """An empty JSONB object '{}' is decoded as an empty dict, not a string."""
        pool = db_pool
        # public.entities.metadata defaults to '{}'::jsonb.
        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ($1) RETURNING id",
            f"empty-metadata-{uuid.uuid4().hex[:8]}",
        )
        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", entity_id)
        result = row["metadata"]
        assert isinstance(result, dict), (
            f"Expected empty dict from JSONB column but got {type(result).__name__!r}: {result!r}"
        )
        assert result == {}

    async def test_nested_jsonb_decoded_correctly(self, db_pool):
        """Nested JSONB objects and arrays are decoded to their Python equivalents."""
        pool = db_pool
        nested = {
            "provenance": {"origin": "email", "ingested_at": "2026-04-30"},
            "badges": ["verified", "owner"],
            "score": 42,
        }
        # Pass Python dict directly so the registered JSONB encoder is used.
        result = await _get_state(pool, await _put_state(pool, nested))
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

    async def test_api_pool_decodes_jsonb_as_dict(self, postgres_container, fresh_db):
        """DatabaseManager pools decode JSONB columns to dicts."""
        from butlers.api.db import DatabaseManager

        pg = postgres_container
        db_name = urlsplit(fresh_db("core")).path.lstrip("/")

        mgr = DatabaseManager(
            host=pg.get_container_host_ip(),
            port=int(pg.get_exposed_port(5432)),
            user=pg.username,
            password=pg.password,
            min_pool_size=1,
            max_pool_size=2,
        )
        try:
            await mgr.add_butler("test_rel", db_name=db_name)
            pool = mgr.pool("test_rel")

            metadata = {"needs_disambiguation": False, "provenance": "email", "score": 1}
            # Pass Python dict directly so the JSONB encoder is invoked.
            result = await _get_state(pool, await _put_state(pool, metadata))
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

    async def test_entities_metadata_roundtrips_as_dict(self, db_pool):
        """Entity metadata JSONB column round-trips to a populated dict."""
        pool = db_pool
        entity_id = uuid.uuid4()
        entity_meta = {
            "provenance": ["email-ingest", "manual"],
            "badges": ["owner"],
            "confidence": 0.99,
        }
        # Pass Python dict directly; asyncpg uses the registered JSONB encoder.
        await pool.execute(
            "INSERT INTO public.entities (id, canonical_name, metadata) VALUES ($1, $2, $3)",
            entity_id,
            "Alice Example",
            entity_meta,
        )
        row = await pool.fetchrow(
            "SELECT metadata FROM public.entities WHERE id = $1",
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

    async def test_facts_metadata_roundtrips_as_dict(self, db):
        """Facts metadata JSONB column round-trips to a populated dict (timeline regression)."""
        pool = await db("core", "memory")
        fact_meta = {"emotion": "curious", "source": "call-transcript"}
        # Pass Python dict directly so the registered JSONB encoder is used.
        await pool.execute(
            "INSERT INTO facts (subject, predicate, content, metadata) VALUES ($1, $2, $3, $4)",
            "contact:alice",
            "contact_note",
            "Discussed project timeline",
            fact_meta,
        )
        rows = await pool.fetch("SELECT metadata FROM facts")
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


def _mock_embedding_engine():
    from unittest.mock import MagicMock

    engine = MagicMock()
    # The real facts.embedding is vector(384); a unit vector keeps cosine distance defined.
    engine.embed.return_value = [1.0] + [0.0] * 383
    engine.model_name = "test-model"
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

    async def test_store_fact_metadata_jsonb_filter_finds_row(self, db):
        """A fact stored with metadata={'direction': 'incoming'} must be found
        by ``WHERE metadata->>'direction' = 'incoming'``.

        This test FAILS on broken main (codec double-encodes the string) and
        PASSES after binding the dict directly.
        """
        from butlers.modules.memory.storage import store_fact

        # facts, episodes, predicate_registry and memory_links from the memory chain.
        pool = await db("core", "memory")

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

    async def test_dict_written_without_cast_roundtrips_as_dict(self, db_pool):
        """Writing a dict directly (no ::jsonb cast) stores a JSON object, not a string."""
        pool = db_pool
        payload = {"key": "value", "count": 42, "nested": {"a": 1}}
        # Pass dict directly — asyncpg codec encodes it once.
        result = await _get_state(pool, await _put_state(pool, payload))
        assert isinstance(result, dict), (
            f"Expected dict but got {type(result).__name__!r}: {result!r}. "
            "JSONB value may have been double-encoded (stored as a JSON string)."
        )
        assert result["key"] == "value"
        assert result["count"] == 42
        assert result["nested"]["a"] == 1

    async def test_json_string_roundtrips_as_string_not_dict(self, db_pool):
        """A json.dumps() string written to a JSONB column gets double-encoded by the codec.

        This is the old pre-codec pattern.  With the JSONB codec registered, asyncpg
        treats any Python value passed to a JSONB parameter as already-decoded — so a
        str like '{"k": 1}' is encoded to JSON again, producing '"{\\"k\\": 1}"' in
        the DB.  The round-trip then returns a str (not a dict).

        This test locks in that behavior so a reintroduction of json.dumps() at
        production call sites is caught immediately.
        """
        import json

        pool = db_pool
        original = {"writer": "test", "value": 99}
        # Pass dict directly: codec encodes once → stored as JSON object.
        key = await _put_state(pool, original)
        result = await _get_state(pool, key)
        assert isinstance(result, dict), (
            f"Expected dict from direct dict write but got {type(result).__name__!r}. "
            "Check that the JSONB codec is active and no double-encoding occurred."
        )
        assert result["writer"] == "test"
        assert result["value"] == 99

        # Now demonstrate the double-encoding trap: passing json.dumps output
        # as a string to a codec-backed pool causes the string itself to be
        # JSON-encoded, so the DB stores a JSON string value (not an object).
        await pool.execute("DELETE FROM state WHERE key = $1", key)
        json_str = json.dumps(original)
        str_result = await _get_state(pool, await _put_state(pool, json_str))
        # The round-trip returns a str, not a dict — proving double-encoding occurred.
        assert isinstance(str_result, str), (
            f"Expected str from json.dumps write (double-encoding) but got "
            f"{type(str_result).__name__!r}. "
            "This confirms the codec does NOT accept pre-serialized strings."
        )

    async def test_list_written_directly_roundtrips_as_list(self, db_pool):
        """A Python list written directly to a JSONB column roundtrips as a list."""
        pool = db_pool
        tags = ["alpha", "beta", "gamma"]
        result = await _get_state(pool, await _put_state(pool, tags))
        assert isinstance(result, list), (
            f"Expected list but got {type(result).__name__!r}: {result!r}. "
            "List JSONB write may have double-encoded."
        )
        assert result == tags
