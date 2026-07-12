"""Real-DB integration tests for the memory module migration chain.

Verifies that the memory migration chain (mem_001 → mem_002 → mem_003)
applies cleanly against a fresh PostgreSQL instance, produces the expected
schema, and supports a representative fact write-then-read cycle.

Local-dev requirements
----------------------
- Docker must be available and able to pull ``pgvector/pgvector:pg17``.
  The test suite uses testcontainers to spin up a throwaway PostgreSQL
  container for each test session.
- No manual DB setup is required; the fixture handles everything.
- Alternatively, if Docker is unavailable, these tests are automatically
  skipped (``pytest.mark.skipif``).

Run with::

    uv run pytest tests/modules/memory/test_memory_migration_integration.py -q --tb=short
"""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import MagicMock

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Expected memory tables after running the full chain
# ---------------------------------------------------------------------------

_EXPECTED_MEMORY_TABLES = {
    "episodes",
    "facts",
    "rules",
    "memory_links",
    "memory_events",
    "predicate_registry",
    "memory_policies",
    # embedding_versions removed by mem_005 (dead table with 0 runtime references)
    # rule_applications removed by mem_006 (write-orphaned audit table, 0 SELECT consumers)
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_exists_in_schema(db_url: str, schema: str, table: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables"
                    "  WHERE table_schema = :s AND table_name = :t"
                    ")"
                ),
                {"s": schema, "t": table},
            )
            return bool(result.scalar())
    finally:
        engine.dispose()


def _get_column_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
            return {str(r[0]) for r in rows}
    finally:
        engine.dispose()


def _fake_embedding_engine() -> MagicMock:
    """Return a mock embedding engine that returns a deterministic 384-float vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.0] * 384
    engine.model_name = "test-model"
    return engine


# ---------------------------------------------------------------------------
# Fixture: provisioned DB with core + memory migrations applied
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def memory_migrated_db(postgres_container) -> str:
    """Provision a fresh DB, run core then memory migrations, and return its URL.

    Scoped to ``module`` so the container startup and full migration chain
    (which can take 5-10 s) runs only once per test module, keeping the
    total test time well under 30 s.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    # 1. Core chain: creates public.entities, extensions, roles, etc.
    asyncio.run(run_migrations(db_url, chain="core"))

    # 2. Memory chain: mem_001 (schema) → mem_002 (predicates) → mem_003 (wellness)
    asyncio.run(run_migrations(db_url, chain="memory"))

    return db_url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_memory_migration_creates_all_expected_tables(memory_migrated_db: str) -> None:
    """All memory tables exist in the public schema after running the chain."""
    for table in _EXPECTED_MEMORY_TABLES:
        assert _table_exists_in_schema(memory_migrated_db, "public", table), (
            f"Expected table {table!r} to exist after memory migration chain"
        )


def test_facts_table_has_required_columns(memory_migrated_db: str) -> None:
    """The facts table has the SPO columns and key operational columns."""
    cols = _get_column_names(memory_migrated_db, "facts")
    for required in (
        "id",
        "subject",
        "predicate",
        "content",
        "validity",
        "scope",
        "entity_id",
        "object_entity_id",
        "valid_at",
        "idempotency_key",
        "tenant_id",
        "embedding",
    ):
        assert required in cols, f"facts.{required} missing after migration"


def test_predicate_registry_seeded(memory_migrated_db: str) -> None:
    """predicate_registry is non-empty after mem_002 (seed predicates)."""
    engine = create_engine(memory_migrated_db)
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM predicate_registry")).scalar()
        assert count and count > 0, "predicate_registry should be seeded by mem_002"
    finally:
        engine.dispose()


def test_wellness_predicates_seeded(memory_migrated_db: str) -> None:
    """mem_003 wellness predicates (e.g. sleep_session) are present."""
    engine = create_engine(memory_migrated_db)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT name FROM predicate_registry WHERE name = 'sleep_session'")
            ).fetchone()
        assert row is not None, "sleep_session predicate should exist after mem_003"
    finally:
        engine.dispose()


def test_memory_policies_seeded(memory_migrated_db: str) -> None:
    """memory_policies has the 8 expected retention classes."""
    expected_classes = {
        "transient",
        "episodic",
        "operational",
        "personal_profile",
        "health_log",
        "financial_log",
        "rule",
        "anti_pattern",
    }
    engine = create_engine(memory_migrated_db)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT retention_class FROM memory_policies")).fetchall()
        actual = {str(r[0]) for r in rows}
    finally:
        engine.dispose()
    missing = expected_classes - actual
    assert not missing, f"memory_policies missing retention classes: {missing}"


async def _write_and_read_fact(db_url: str) -> dict:
    """Write a fact via store_fact and read it back directly via asyncpg."""
    from butlers.modules.memory.storage import store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        embedding_engine = _fake_embedding_engine()

        result = await store_fact(
            pool,
            subject="test_user",
            predicate="preference",
            content="prefers dark mode",
            embedding_engine=embedding_engine,
            importance=7.0,
            permanence="standard",
            scope="global",
            tenant_id="shared",
        )
        # store_fact returns a dict with "id" (UUID) and "supersedes_id"
        fact_id = result["id"]

        row = await pool.fetchrow(
            "SELECT id, subject, predicate, content, validity, scope, importance"
            " FROM facts WHERE id = $1",
            fact_id,
        )
        return dict(row) if row else {}
    finally:
        await pool.close()


def test_fact_write_and_read_round_trip(memory_migrated_db: str) -> None:
    """store_fact persists a fact that can be read back with correct field values.

    This exercises the full SPO write path against the migrated schema:
    store_fact → facts INSERT → SELECT by id.
    """
    result = asyncio.run(_write_and_read_fact(memory_migrated_db))

    assert result, "Expected a row to be returned after store_fact"
    assert result["subject"] == "test_user"
    assert result["predicate"] == "preference"
    assert result["content"] == "prefers dark mode"
    assert result["validity"] == "active"
    assert result["scope"] == "global"
    assert abs(result["importance"] - 7.0) < 1e-6


async def _supersede_and_search_catalog(db_url: str) -> dict:
    """Store a property fact, supersede it, and probe the discovery catalog.

    Returns a dict capturing the catalog state needed to assert that the
    superseded fact's catalog entry was marked stale and no longer surfaces in
    cross-butler search, while the superseding fact does.
    """
    from butlers.modules.memory.search import search_catalog
    from butlers.modules.memory.storage import store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()

        # First (soon-to-be-superseded) property fact.
        old = await store_fact(
            pool,
            subject="alice",
            predicate="favorite_color",
            content="blue",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )
        old_id = old["id"]

        # Sanity: the old fact is discoverable via the catalog before supersession.
        before = await search_catalog(
            pool,
            "alice favorite_color",
            engine,
            tenant_id="shared",
            mode="keyword",
        )
        before_ids = {r["source_id"] for r in before}

        # Superseding property fact (same subject + predicate, new content).
        new = await store_fact(
            pool,
            subject="alice",
            predicate="favorite_color",
            content="red",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )
        new_id = new["id"]
        assert new["supersedes_id"] == old_id

        # Catalog row for the superseded fact: marked stale.
        stale_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts'"
            " AND source_id = $1",
            old_id,
        )

        after = await search_catalog(
            pool,
            "alice favorite_color",
            engine,
            tenant_id="shared",
            mode="keyword",
        )
        after_ids = {r["source_id"] for r in after}

        return {
            "old_id": old_id,
            "new_id": new_id,
            "before_ids": before_ids,
            "after_ids": after_ids,
            "stale_confidence": stale_row["confidence"] if stale_row else None,
            "stale_invalid_at": stale_row["invalid_at"] if stale_row else None,
        }
    finally:
        await pool.close()


def test_fact_supersession_marks_catalog_entry_stale(memory_migrated_db: str) -> None:
    """Superseding a fact invalidates its catalog entry so it stops surfacing.

    Regression for the unimplemented "Fact supersession updates catalog"
    scenario in the memory-discovery-catalog spec: the superseded fact's
    public.memory_catalog row must be marked stale (confidence=0, invalid_at
    set) and excluded from cross-butler catalog search, while the superseding
    fact remains discoverable.
    """
    result = asyncio.run(_supersede_and_search_catalog(memory_migrated_db))

    old_id = result["old_id"]
    new_id = result["new_id"]

    # Old fact was discoverable before supersession.
    assert old_id in result["before_ids"]

    # Catalog entry for the superseded fact is marked stale.
    assert result["stale_confidence"] == 0
    assert result["stale_invalid_at"] is not None

    # After supersession the stale entry no longer surfaces, but the new one does.
    assert old_id not in result["after_ids"]
    assert new_id in result["after_ids"]


async def _forget_fact_and_rule_and_check_catalog(db_url: str) -> dict:
    """Store a fact and a rule WITH catalog write-behind, forget both via the
    plain (non-correction) ``forget_memory`` path, and read back their
    ``public.memory_catalog`` rows.

    Regression coverage for bu-5ud8p.3: forget_memory previously never
    cascaded to the catalog (``_mark_catalog_stale`` had exactly one caller —
    ``store_fact``'s own supersession cascade), so a retracted fact or
    forgotten rule kept surfacing in cross-butler catalog search indefinitely.
    """
    from butlers.modules.memory.storage import forget_memory, store_fact, store_rule

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()

        fact = await store_fact(
            pool,
            subject="erin",
            predicate="favorite_color",
            content="teal",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )
        fact_id = fact["id"]

        rule_id = await store_rule(
            pool,
            content="Always double-check delivery addresses",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )

        fact_forgotten = await forget_memory(pool, "fact", fact_id)
        rule_forgotten = await forget_memory(pool, "rule", rule_id)

        fact_catalog_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            fact_id,
        )
        rule_catalog_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            rule_id,
        )

        return {
            "fact_forgotten": fact_forgotten,
            "rule_forgotten": rule_forgotten,
            "fact_catalog_row": dict(fact_catalog_row) if fact_catalog_row else None,
            "rule_catalog_row": dict(rule_catalog_row) if rule_catalog_row else None,
        }
    finally:
        await pool.close()


def test_forget_memory_marks_catalog_entries_stale(memory_migrated_db: str) -> None:
    """forget_memory cascades disownment to public.memory_catalog for facts and rules.

    Without this cascade the just-enabled fleet catalog would keep serving a
    retracted fact / forgotten rule to every butler indefinitely (bu-5ud8p.3).
    """
    result = asyncio.run(_forget_fact_and_rule_and_check_catalog(memory_migrated_db))

    assert result["fact_forgotten"] is True
    assert result["rule_forgotten"] is True

    fact_row = result["fact_catalog_row"]
    assert fact_row is not None, "expected a catalog row for the forgotten fact"
    assert fact_row["confidence"] == 0
    assert fact_row["invalid_at"] is not None

    rule_row = result["rule_catalog_row"]
    assert rule_row is not None, "expected a catalog row for the forgotten rule"
    assert rule_row["confidence"] == 0
    assert rule_row["invalid_at"] is not None


async def _forget_fact_via_correction_and_check_catalog(db_url: str) -> dict:
    """Correction-driven forget_memory must ALSO cascade catalog disownment —
    not just the plain path — in the same transaction as the retraction and
    the memory_events audit insert.
    """
    import uuid

    from butlers.modules.memory.storage import forget_memory, store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()

        result = await store_fact(
            pool,
            subject="frank",
            predicate="favorite_color",
            content="maroon",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )
        fact_id = result["id"]

        await forget_memory(
            pool,
            "fact",
            fact_id,
            correction_id=str(uuid.uuid4()),
            correction_reason="wrong color",
        )

        catalog_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            fact_id,
        )
        return {"catalog_row": dict(catalog_row) if catalog_row else None}
    finally:
        await pool.close()


def test_correction_driven_forget_also_marks_catalog_stale(memory_migrated_db: str) -> None:
    """The correction-provenance forget path cascades catalog disownment too (bu-5ud8p.3)."""
    out = asyncio.run(_forget_fact_via_correction_and_check_catalog(memory_migrated_db))
    row = out["catalog_row"]
    assert row is not None
    assert row["confidence"] == 0
    assert row["invalid_at"] is not None


async def _purge_ha_state_fact_and_check_catalog(db_url: str) -> dict:
    """purge_superseded_facts' unconditional ha_state purge must mark the
    corresponding catalog row stale. Unlike the superseded-facts purge path,
    ha_state facts never go through supersession, so store_fact's own
    write-time cascade never touches their catalog row — this exercises
    purge's OWN disownment cascade in isolation (bu-5ud8p.3).
    """
    from butlers.modules.memory.storage import purge_superseded_facts

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        fact_id = await pool.fetchval(
            "INSERT INTO facts (subject, predicate, content, scope, tenant_id,"
            " source_butler, retention_class)"
            " VALUES ('sensor.kitchen', 'ha_state', 'on', 'global', 'shared',"
            " 'home', 'operational') RETURNING id"
        )

        # Simulate a catalog row written before the HA snapshot loop was
        # disabled (store_fact's write-behind, not exercised directly here).
        await pool.execute(
            "INSERT INTO public.memory_catalog"
            " (source_schema, source_table, source_id, source_butler, tenant_id,"
            "  summary, memory_type, confidence)"
            " VALUES ('public', 'facts', $1, 'home', 'shared',"
            "  'sensor.kitchen ha_state: on', 'fact', 1.0)",
            fact_id,
        )

        result = await purge_superseded_facts(pool)

        fact_row = await pool.fetchrow("SELECT id FROM facts WHERE id = $1", fact_id)
        catalog_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            fact_id,
        )
        return {
            "result": result,
            "fact_row": dict(fact_row) if fact_row else None,
            "catalog_row": dict(catalog_row) if catalog_row else None,
        }
    finally:
        await pool.close()


def test_purge_ha_state_facts_marks_catalog_entry_stale(memory_migrated_db: str) -> None:
    """purge_superseded_facts leaves zero LIVE catalog rows for a purged ha_state fact.

    The catalog row itself is retained (butler roles hold no DELETE grant on
    public.memory_catalog — see core_009) but marked stale, matching the
    supersession scenario's "mark stale" semantics (bu-5ud8p.3 [decision]).
    """
    out = asyncio.run(_purge_ha_state_fact_and_check_catalog(memory_migrated_db))

    assert out["result"]["deleted_ha_state"] >= 1
    assert out["fact_row"] is None, "the ha_state fact row must be deleted"

    catalog_row = out["catalog_row"]
    assert catalog_row is not None, "catalog row is retained (no DELETE grant) but must be stale"
    assert catalog_row["confidence"] == 0
    assert catalog_row["invalid_at"] is not None


async def _consolidate_new_fact_with_catalog(db_url: str) -> dict:
    """execute_consolidation must forward enable_shared_catalog/source_schema
    to store_fact so consolidation-derived facts get a catalog row too.

    Regression coverage for bu-5ud8p.3: consolidation_executor.py previously
    dropped this pass-through entirely, so every store_fact/store_rule call it
    made used the default enable_shared_catalog=False regardless of the
    module's own configuration — consolidation output was silently invisible
    to cross-butler catalog search.
    """
    from butlers.modules.memory.consolidation_executor import execute_consolidation
    from butlers.modules.memory.consolidation_parser import ConsolidationResult, NewFact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="gina", predicate="favorite_color", content="gold"),
            ]
        )

        # source_episode_ids=[] deliberately sidesteps an unrelated pre-existing
        # bug in execute_consolidation's derived_from-link loop (store_fact
        # returns a dict, but the loop passes it straight to create_link's
        # UUID-typed source_id parameter) — out of scope for this cascade fix,
        # reported as a discovered follow-up. facts_created increments outside
        # that loop, so it is unaffected.
        exec_result = await execute_consolidation(
            pool,
            engine,
            parsed,
            source_episode_ids=[],
            butler_name="health",
            tenant_id="shared",
            enable_shared_catalog=True,
            source_schema="public",
        )

        catalog_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts'"
            " AND title = 'gina favorite_color'"
        )
        return {
            "facts_created": exec_result["facts_created"],
            "errors": exec_result["errors"],
            "catalog_row_found": catalog_row is not None,
        }
    finally:
        await pool.close()


def test_execute_consolidation_propagates_enable_shared_catalog(memory_migrated_db: str) -> None:
    """A consolidation-derived fact gets a public.memory_catalog row when
    enable_shared_catalog/source_schema are threaded through (bu-5ud8p.3)."""
    out = asyncio.run(_consolidate_new_fact_with_catalog(memory_migrated_db))
    assert out["errors"] == []
    assert out["facts_created"] == 1
    assert out["catalog_row_found"] is True


async def _backfill_and_search_catalog(db_url: str) -> dict:
    """Store a fact/rule/retracted-fact/forgotten-rule WITHOUT catalog write-behind,
    then verify ``run_memory_catalog_backfill`` is the only path that catalogs them.

    Regression coverage for bu-qvnce.15 (memory_catalog default-on + backfill):
    the ~3,600 pre-flip facts/rules predate write-behind, so the backfill job
    is the only mechanism that ever catalogs them.
    """
    from butlers.modules.memory.search import search_catalog
    from butlers.modules.memory.storage import (
        forget_memory,
        run_memory_catalog_backfill,
        store_fact,
        store_rule,
    )

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()

        # Active fact/rule stored with catalog write-behind OFF — simulates the
        # pre-flip backlog. Also a retracted fact and a forgotten rule, which
        # backfill must never catalog.
        fact = await store_fact(
            pool,
            subject="bob",
            predicate="favorite_color",
            content="green",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=False,
        )
        fact_id = fact["id"]

        rule_id = await store_rule(
            pool,
            content="Always confirm before booking travel",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=False,
        )

        retracted_fact = await store_fact(
            pool,
            subject="carol",
            predicate="favorite_color",
            content="purple",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=False,
        )
        retracted_fact_id = retracted_fact["id"]
        await forget_memory(pool, "fact", retracted_fact_id)

        forgotten_rule_id = await store_rule(
            pool,
            content="Never mention the surprise party",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=False,
        )
        await forget_memory(pool, "rule", forgotten_rule_id)

        # "Before" existence check for the specific rows this test cares about
        # (the shared module-scoped fixture DB may already carry catalog rows
        # from other tests in this module, so a table-wide COUNT would be
        # order-dependent — check these exact ids instead).
        fact_cataloged_before = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1)",
            fact_id,
        )
        rule_cataloged_before = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1)",
            rule_id,
        )

        result = await run_memory_catalog_backfill(pool, source_schema="public", batch_size=200)

        # Idempotent re-run: nothing new to backfill the second time.
        result_rerun = await run_memory_catalog_backfill(
            pool, source_schema="public", batch_size=200
        )

        fact_catalog_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            fact_id,
        )
        rule_catalog_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            rule_id,
        )
        retracted_catalog_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            retracted_fact_id,
        )
        forgotten_rule_catalog_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            forgotten_rule_id,
        )

        discovered = await search_catalog(
            pool, "bob favorite_color", engine, tenant_id="shared", mode="keyword"
        )

        return {
            "fact_cataloged_before": bool(fact_cataloged_before),
            "rule_cataloged_before": bool(rule_cataloged_before),
            "result": result,
            "result_rerun": result_rerun,
            "fact_cataloged": fact_catalog_row is not None,
            "rule_cataloged": rule_catalog_row is not None,
            "retracted_fact_cataloged": retracted_catalog_row is not None,
            "forgotten_rule_cataloged": forgotten_rule_catalog_row is not None,
            "fact_discoverable": any(r["source_id"] == fact_id for r in discovered),
        }
    finally:
        await pool.close()


def test_catalog_backfill_is_idempotent_and_excludes_retracted_forgotten(
    memory_migrated_db: str,
) -> None:
    """run_memory_catalog_backfill catalogs active facts/rules, skips on re-run,
    and never catalogs retracted facts or forgotten rules.

    Note: the module-scoped ``memory_migrated_db`` fixture is shared with
    other tests in this file, some of which also write facts/rules without
    catalog write-behind (pre-flip-style backlog) — so the batch this test's
    first backfill call drains may include more than just this test's own
    rows. Assertions therefore check this test's specific ids rather than
    exact backfilled counts (except the second call, which is deterministic:
    nothing new appears between the two back-to-back calls in this test).
    """
    result = asyncio.run(_backfill_and_search_catalog(memory_migrated_db))

    assert result["fact_cataloged_before"] is False
    assert result["rule_cataloged_before"] is False

    # First run catalogs at least this test's two active items (retracted
    # fact and forgotten rule are excluded by the backfill's own filters).
    assert result["result"]["facts_backfilled"] >= 1
    assert result["result"]["rules_backfilled"] >= 1

    # Re-run is a no-op: NOT EXISTS against the UNIQUE key skips already-cataloged rows.
    assert result["result_rerun"]["facts_backfilled"] == 0
    assert result["result_rerun"]["rules_backfilled"] == 0

    assert result["fact_cataloged"] is True
    assert result["rule_cataloged"] is True
    assert result["retracted_fact_cataloged"] is False
    assert result["forgotten_rule_cataloged"] is False
    assert result["fact_discoverable"] is True


async def _forget_fact_via_correction_and_read_event(db_url: str) -> dict:
    """Store a fact, retract it via a correction, and return the audit event + fact.

    Exercises the correction-driven retraction path end to end against the real
    schema: store_fact -> forget_memory(correction_id=...) -> the fact is marked
    ``retracted`` with correction provenance in metadata AND a ``memory_events``
    row is inserted linking the correction_id for audit traceability.
    """
    import uuid

    from butlers.modules.memory.storage import forget_memory, store_fact

    correction_id = str(uuid.uuid4())
    correction_reason = "user says this fact is wrong"

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        embedding_engine = _fake_embedding_engine()

        result = await store_fact(
            pool,
            subject="audit_user",
            predicate="preference",
            content="prefers light mode",
            embedding_engine=embedding_engine,
            importance=5.0,
            permanence="standard",
            scope="global",
            tenant_id="shared",
        )
        fact_id = result["id"]

        forgotten = await forget_memory(
            pool,
            "fact",
            fact_id,
            correction_id=correction_id,
            correction_reason=correction_reason,
        )

        fact_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1",
            fact_id,
        )
        event_row = await pool.fetchrow(
            "SELECT event_type, actor, memory_type, memory_id, payload"
            " FROM memory_events"
            " WHERE event_type = 'correction_driven_retraction' AND memory_id = $1",
            fact_id,
        )
        return {
            "correction_id": correction_id,
            "correction_reason": correction_reason,
            "fact_id": fact_id,
            "forgotten": forgotten,
            "fact": dict(fact_row) if fact_row else {},
            "event": dict(event_row) if event_row else {},
        }
    finally:
        await pool.close()


def test_correction_driven_forget_emits_memory_event(memory_migrated_db: str) -> None:
    """A correction-driven forget_memory inserts an auditable memory_events row.

    Per the module-memory ``correction-provenance`` spec scenario "Correction
    provenance in memory events": retracting a fact via a correction SHALL
    insert a ``memory_events`` row whose event type indicates correction-driven
    retraction and whose payload carries the ``correction_id`` for audit linkage.
    Without this row, correction-driven retractions are invisible in the audit
    log. This guards that the audit event is actually emitted.
    """
    out = asyncio.run(_forget_fact_via_correction_and_read_event(memory_migrated_db))

    assert out["forgotten"] is True

    # The fact itself is retracted and carries correction provenance in metadata.
    fact = out["fact"]
    assert fact, "Expected the fact row to still exist after retraction"
    assert fact["validity"] == "retracted"
    metadata = fact["metadata"] or {}
    assert metadata.get("correction_id") == out["correction_id"]
    assert metadata.get("correction_reason") == out["correction_reason"]

    # The audit event exists, is the right type, and links the correction_id.
    event = out["event"]
    assert event, "Expected a memory_events row for the correction-driven retraction"
    assert event["event_type"] == "correction_driven_retraction"
    assert event["memory_type"] == "fact"
    assert event["memory_id"] == out["fact_id"]
    payload = event["payload"] or {}
    assert payload.get("correction_id") == out["correction_id"]
    assert payload.get("correction_reason") == out["correction_reason"]


async def _reconcile_drifted_catalog_rows(db_url: str) -> dict:
    """Exercise run_memory_catalog_backfill's reverse-reconciliation phase (bu-5ud8p.4).

    Stores a healthy fact/rule plus a "drifted" fact/rule (moved to a terminal
    state via raw SQL, bypassing forget_memory/run_decay_sweep's atomic
    ``_cascade_catalog_disownment``) and a "gone" fact/rule (hard-deleted via
    raw SQL). Reverse reconciliation exists precisely to catch drift that
    bypasses the cascade — e.g. rows cataloged before the cascade existed, or
    a state change made through a path that doesn't call it — so simulating
    that bypass with raw SQL is the faithful way to exercise this code path
    (going through ``forget_memory`` would just re-exercise the cascade
    tested by bu-5ud8p.3, not the reconciliation gap this closes).

    Uses a dedicated, freshly-migrated database (not the module-shared
    ``memory_migrated_db`` fixture) so catalog-row counts are exact and not
    order-dependent on other tests' backlog.
    """
    from butlers.modules.memory.storage import (
        get_catalog_drift_counts,
        run_memory_catalog_backfill,
        store_fact,
        store_rule,
    )

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()

        async def _store_fact(subject: str, content: str) -> object:
            fact = await store_fact(
                pool,
                subject=subject,
                predicate="favorite_color",
                content=content,
                embedding_engine=engine,
                scope="global",
                tenant_id="shared",
                source_butler="health",
                enable_shared_catalog=True,
                source_schema="public",
            )
            return fact["id"]

        async def _store_rule(content: str) -> object:
            return await store_rule(
                pool,
                content=content,
                embedding_engine=engine,
                scope="global",
                tenant_id="shared",
                source_butler="health",
                enable_shared_catalog=True,
                source_schema="public",
            )

        healthy_fact_id = await _store_fact("reconcile_healthy", "teal")
        drifted_fact_id = await _store_fact("reconcile_drifted", "maroon")
        gone_fact_id = await _store_fact("reconcile_gone", "indigo")

        healthy_rule_id = await _store_rule("Always double-check the calendar before booking")
        drifted_rule_id = await _store_rule("Never book travel on a Sunday")
        gone_rule_id = await _store_rule("Never mention the surprise party twice")

        # Simulate drift that bypasses the atomic disownment cascade.
        await pool.execute("UPDATE facts SET validity = 'retracted' WHERE id = $1", drifted_fact_id)
        await pool.execute("DELETE FROM facts WHERE id = $1", gone_fact_id)
        await pool.execute(
            "UPDATE rules SET metadata = metadata || '{\"forgotten\": true}'::jsonb WHERE id = $1",
            drifted_rule_id,
        )
        await pool.execute("DELETE FROM rules WHERE id = $1", gone_rule_id)

        async def _invalid_at(table: str, source_id: object) -> object:
            return await pool.fetchval(
                "SELECT invalid_at FROM public.memory_catalog"
                " WHERE source_schema = 'public' AND source_table = $1 AND source_id = $2",
                table,
                source_id,
            )

        before = {
            "healthy_fact": await _invalid_at("facts", healthy_fact_id),
            "drifted_fact": await _invalid_at("facts", drifted_fact_id),
            "gone_fact": await _invalid_at("facts", gone_fact_id),
            "healthy_rule": await _invalid_at("rules", healthy_rule_id),
            "drifted_rule": await _invalid_at("rules", drifted_rule_id),
            "gone_rule": await _invalid_at("rules", gone_rule_id),
        }

        drift_before = await get_catalog_drift_counts(pool, source_schema="public")

        result = await run_memory_catalog_backfill(pool, source_schema="public", batch_size=500)
        result_rerun = await run_memory_catalog_backfill(
            pool, source_schema="public", batch_size=500
        )

        drift_after = await get_catalog_drift_counts(pool, source_schema="public")

        after = {
            "healthy_fact": await _invalid_at("facts", healthy_fact_id),
            "drifted_fact": await _invalid_at("facts", drifted_fact_id),
            "gone_fact": await _invalid_at("facts", gone_fact_id),
            "healthy_rule": await _invalid_at("rules", healthy_rule_id),
            "drifted_rule": await _invalid_at("rules", drifted_rule_id),
            "gone_rule": await _invalid_at("rules", gone_rule_id),
        }

        return {
            "before": before,
            "after": after,
            "result": result,
            "result_rerun": result_rerun,
            "drift_before": drift_before,
            "drift_after": drift_after,
        }
    finally:
        await pool.close()


def test_backfill_reverse_reconciliation_marks_drifted_rows_stale(postgres_container) -> None:
    """run_memory_catalog_backfill's reconciliation phase marks stale any
    catalog row whose source fact/rule has gone, been forgotten, or reached
    a terminal state outside the atomic disownment cascade -- and leaves
    healthy rows and already-stale rows untouched (idempotent re-run).
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))

    out = asyncio.run(_reconcile_drifted_catalog_rows(db_url))

    # Before reconciliation: every catalog row (including the drifted/gone
    # ones) is still live -- the raw-SQL state changes never touched the
    # catalog, by construction.
    before = out["before"]
    assert before["healthy_fact"] is None
    assert before["drifted_fact"] is None
    assert before["gone_fact"] is None
    assert before["healthy_rule"] is None
    assert before["drifted_rule"] is None
    assert before["gone_rule"] is None

    # First reconciliation pass catalogs the drift.
    result = out["result"]
    assert result["facts_reconciled"] == 2  # drifted_fact + gone_fact
    assert result["rules_reconciled"] == 2  # drifted_rule + gone_rule

    after = out["after"]
    # Healthy rows are untouched.
    assert after["healthy_fact"] is None
    assert after["healthy_rule"] is None
    # Drifted/gone rows are marked stale (invalid_at set), never deleted --
    # the catalog row for the hard-deleted fact/rule still exists.
    assert after["drifted_fact"] is not None
    assert after["gone_fact"] is not None
    assert after["drifted_rule"] is not None
    assert after["gone_rule"] is not None

    # Idempotent re-run: nothing new to reconcile the second time.
    result_rerun = out["result_rerun"]
    assert result_rerun["facts_reconciled"] == 0
    assert result_rerun["rules_reconciled"] == 0

    # get_catalog_drift_counts (the /api/memory/stats gauge's data source):
    # this dedicated DB holds exactly the 6 catalog rows this test created,
    # so exact counts are assertable.
    drift_before = out["drift_before"]
    assert drift_before["live"] == 6
    assert drift_before["stale"] == 0
    assert drift_before["drifted"] == 4  # drifted_fact, gone_fact, drifted_rule, gone_rule

    drift_after = out["drift_after"]
    assert drift_after["live"] == 2  # healthy_fact, healthy_rule
    assert drift_after["stale"] == 4
    assert drift_after["drifted"] == 0  # everything drifted has already been reconciled


def test_migration_is_idempotent(postgres_container) -> None:
    """Running the memory migration chain twice on the same DB does not fail."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    # Second run must succeed without errors
    asyncio.run(run_migrations(db_url, chain="memory"))

    # Tables still exist
    assert _table_exists_in_schema(db_url, "public", "facts")
    assert _table_exists_in_schema(db_url, "public", "predicate_registry")
