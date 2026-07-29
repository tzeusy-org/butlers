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
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.core.tool_call_capture import (
    reset_current_runtime_butler_name,
    reset_current_runtime_session_id,
    reset_current_runtime_trigger_source,
    set_current_runtime_butler_name,
    set_current_runtime_session_id,
    set_current_runtime_trigger_source,
)
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
    "episode_tombstones",
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


@contextmanager
def _runtime_provenance_context(*, butler: str, session_id: uuid.UUID, trigger_source: str):
    butler_token = set_current_runtime_butler_name(butler)
    session_token = set_current_runtime_session_id(str(session_id))
    trigger_token = set_current_runtime_trigger_source(trigger_source)
    try:
        yield
    finally:
        reset_current_runtime_trigger_source(trigger_token)
        reset_current_runtime_session_id(session_token)
        reset_current_runtime_butler_name(butler_token)


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


async def _assert_runtime_fact_provenance_guard(db_url: str) -> None:
    """Exercise runtime fact writes and Recent Episodes against real Postgres."""
    from butlers.modules.memory.storage import store_episode, store_fact
    from butlers.modules.memory.tools.context import _fetch_recent_episodes
    from butlers.modules.memory.tools.writing import memory_store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()
        tenant_id = "health"
        butler = "health"
        case_prefix = uuid.uuid4().hex

        async def store_runtime_fact(
            label: str,
            trigger_source: str,
            *,
            session_id: uuid.UUID | None = None,
            source_episode_id: uuid.UUID | None = None,
        ) -> tuple[uuid.UUID, uuid.UUID, dict]:
            runtime_session_id = session_id or uuid.uuid4()
            with _runtime_provenance_context(
                butler=butler,
                session_id=runtime_session_id,
                trigger_source=trigger_source,
            ):
                if source_episode_id is None:
                    response = await memory_store_fact(
                        pool,
                        engine,
                        subject=f"runtime-{case_prefix}-{label}",
                        predicate="preference",
                        content=f"{label} provenance regression",
                        request_context={"tenant_id": tenant_id},
                    )
                    fact_id = uuid.UUID(response["id"])
                else:
                    response = await store_fact(
                        pool,
                        subject=f"runtime-{case_prefix}-{label}",
                        predicate="preference",
                        content=f"{label} provenance regression",
                        embedding_engine=engine,
                        tenant_id=tenant_id,
                        source_episode_id=source_episode_id,
                    )
                    fact_id = response["id"]
            fact = await pool.fetchrow(
                "SELECT source_butler, source_episode_id FROM facts WHERE id = $1",
                fact_id,
            )
            assert fact is not None
            return runtime_session_id, fact_id, dict(fact)

        consolidation_session_id, _, consolidation_fact = await store_runtime_fact(
            "consolidation",
            "schedule:consolidation",
        )
        consolidation_episodes = await pool.fetch(
            "SELECT id FROM episodes WHERE tenant_id = $1 AND session_id = $2",
            tenant_id,
            consolidation_session_id,
        )
        assert consolidation_fact == {"source_butler": butler, "source_episode_id": None}
        assert consolidation_episodes == []

        positive_placeholder_ids: set[uuid.UUID] = set()
        for label, trigger_source in (
            ("daily", "schedule:daily_digest"),
            ("retry", "schedule:consolidation:retry"),
            ("trigger", "trigger"),
        ):
            session_id, _, fact = await store_runtime_fact(label, trigger_source)
            placeholder = await pool.fetchrow(
                """
                SELECT id, metadata
                FROM episodes
                WHERE tenant_id = $1 AND session_id = $2
                """,
                tenant_id,
                session_id,
            )
            assert placeholder is not None
            assert placeholder["metadata"] == {"provenance_placeholder": True}
            assert fact == {"source_butler": butler, "source_episode_id": placeholder["id"]}
            positive_placeholder_ids.add(placeholder["id"])

        explicit_episode_id = await store_episode(
            pool,
            "explicit source episode",
            butler,
            engine,
            tenant_id=tenant_id,
        )
        explicit_session_id, _, explicit_fact = await store_runtime_fact(
            "explicit",
            "schedule:consolidation",
            source_episode_id=explicit_episode_id,
        )
        explicit_session_episodes = await pool.fetch(
            "SELECT id FROM episodes WHERE tenant_id = $1 AND session_id = $2",
            tenant_id,
            explicit_session_id,
        )
        assert explicit_fact == {"source_butler": butler, "source_episode_id": explicit_episode_id}
        assert explicit_session_episodes == []

        existing_session_id = uuid.uuid4()
        existing_episode_id = await store_episode(
            pool,
            "existing same-session source episode",
            butler,
            engine,
            session_id=existing_session_id,
            tenant_id=tenant_id,
        )
        _, _, existing_fact = await store_runtime_fact(
            "existing",
            "schedule:consolidation",
            session_id=existing_session_id,
        )
        existing_session_episodes = await pool.fetch(
            "SELECT id FROM episodes WHERE tenant_id = $1 AND session_id = $2",
            tenant_id,
            existing_session_id,
        )
        assert existing_fact == {"source_butler": butler, "source_episode_id": existing_episode_id}
        assert [row["id"] for row in existing_session_episodes] == [existing_episode_id]

        visible_false_id = await store_episode(
            pool,
            "non-placeholder false metadata",
            butler,
            engine,
            metadata={"provenance_placeholder": False},
            tenant_id=tenant_id,
        )
        visible_absent_id = await store_episode(
            pool,
            "non-placeholder absent metadata",
            butler,
            engine,
            metadata={},
            tenant_id=tenant_id,
        )
        recent_episode_ids = {
            row["id"] for row in await _fetch_recent_episodes(pool, butler, tenant_id, limit=20)
        }
        assert positive_placeholder_ids.isdisjoint(recent_episode_ids)
        assert {visible_false_id, visible_absent_id, explicit_episode_id, existing_episode_id} <= (
            recent_episode_ids
        )
    finally:
        await pool.close()


def test_runtime_fact_provenance_guard_and_recent_episode_filter(memory_migrated_db: str) -> None:
    """Consolidation fact writes must not create recallable placeholder episodes."""
    asyncio.run(_assert_runtime_fact_provenance_guard(memory_migrated_db))


async def _assert_expected_supersession_target_guard(db_url: str) -> None:
    from butlers.modules.memory.storage import StaleSupersessionTargetError, store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=4,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()
        unique_subject = f"expected-target-{uuid.uuid4()}"

        original = await store_fact(
            pool,
            subject=unique_subject,
            predicate="favorite_color",
            content="blue",
            embedding_engine=engine,
            tenant_id="shared",
            source_butler="travel",
        )
        replacement = await store_fact(
            pool,
            subject=unique_subject,
            predicate="favorite_color",
            content="green",
            embedding_engine=engine,
            tenant_id="shared",
            source_butler="travel",
            expected_supersedes_id=original["id"],
        )
        assert replacement["supersedes_id"] == original["id"]

        with pytest.raises(StaleSupersessionTargetError, match="no longer current"):
            await store_fact(
                pool,
                subject=unique_subject,
                predicate="favorite_color",
                content="red",
                embedding_engine=engine,
                tenant_id="shared",
                source_butler="travel",
                expected_supersedes_id=original["id"],
            )

        current = await pool.fetchrow(
            "SELECT id, content FROM facts "
            "WHERE tenant_id = 'shared' AND subject = $1 "
            "AND predicate = 'favorite_color' AND validity IN ('active', 'fading') "
            "AND valid_at IS NULL",
            unique_subject,
        )
        assert current is not None
        assert current["id"] == replacement["id"]
        assert current["content"] == "green"
    finally:
        await pool.close()


def test_expected_supersession_target_is_checked_atomically(memory_migrated_db: str) -> None:
    """A stale target cannot overwrite the fact that replaced it."""
    asyncio.run(_assert_expected_supersession_target_guard(memory_migrated_db))


async def _assert_concurrent_expected_supersession_target_guard(db_url: str) -> None:
    from butlers.modules.memory.storage import StaleSupersessionTargetError, store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=2,
        max_size=4,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()
        unique_subject = f"concurrent-target-{uuid.uuid4()}"
        original = await store_fact(
            pool,
            subject=unique_subject,
            predicate="favorite_color",
            content="blue",
            embedding_engine=engine,
            tenant_id="shared",
            source_butler="travel",
        )

        async def _replace(content: str):
            return await store_fact(
                pool,
                subject=unique_subject,
                predicate="favorite_color",
                content=content,
                embedding_engine=engine,
                tenant_id="shared",
                source_butler="travel",
                expected_supersedes_id=original["id"],
            )

        outcomes = await asyncio.gather(
            _replace("green"),
            _replace("red"),
            return_exceptions=True,
        )
        successes = [result for result in outcomes if isinstance(result, dict)]
        failures = [result for result in outcomes if isinstance(result, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], StaleSupersessionTargetError)
        assert "no longer current" in str(failures[0])

        current = await pool.fetchrow(
            "SELECT id, supersedes_id FROM facts "
            "WHERE tenant_id = 'shared' AND subject = $1 "
            "AND predicate = 'favorite_color' AND validity IN ('active', 'fading') "
            "AND valid_at IS NULL",
            unique_subject,
        )
        assert current is not None
        assert current["id"] == successes[0]["id"]
        assert current["supersedes_id"] == original["id"]
    finally:
        await pool.close()


def test_concurrent_writers_cannot_share_expected_supersession_target(
    memory_migrated_db: str,
) -> None:
    """Only one concurrent writer may consume a live target authority."""
    asyncio.run(_assert_concurrent_expected_supersession_target_guard(memory_migrated_db))


async def _assert_consolidation_target_boundaries(db_url: str) -> None:
    from butlers.modules.memory.consolidation_executor import execute_consolidation
    from butlers.modules.memory.consolidation_parser import ConsolidationResult, UpdatedFact
    from butlers.modules.memory.storage import store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        engine = _fake_embedding_engine()
        suffix = uuid.uuid4().hex
        subject_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            f"boundary-subject-{suffix}",
        )
        object_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            f"boundary-object-{suffix}",
        )
        edge_predicate = await pool.fetchval(
            "SELECT name FROM predicate_registry "
            "WHERE is_edge AND NOT is_temporal ORDER BY name LIMIT 1"
        )
        assert edge_predicate is not None

        cross_tenant = await store_fact(
            pool,
            subject=f"boundary-subject-{suffix}",
            predicate="favorite_color",
            content="blue",
            embedding_engine=engine,
            entity_id=subject_id,
            tenant_id=f"other-{suffix}",
            source_butler="travel",
        )
        cross_source = await store_fact(
            pool,
            subject=f"boundary-subject-{suffix}",
            predicate="preference",
            content="quiet rooms",
            embedding_engine=engine,
            entity_id=subject_id,
            tenant_id="shared",
            source_butler="health",
        )
        edge = await store_fact(
            pool,
            subject=f"boundary-subject-{suffix}",
            predicate=edge_predicate,
            content=f"boundary-object-{suffix}",
            embedding_engine=engine,
            entity_id=subject_id,
            object_entity_id=object_id,
            tenant_id="shared",
            source_butler="travel",
        )

        targets = (cross_tenant["id"], cross_source["id"], edge["id"])
        parsed = ConsolidationResult(
            updated_facts=[
                UpdatedFact(
                    target_id=str(target_id),
                    content="must not overwrite",
                )
                for target_id in targets
            ],
        )
        result = await execute_consolidation(
            pool=pool,
            embedding_engine=engine,
            parsed=parsed,
            source_episode_ids=[],
            butler_name="travel",
            tenant_id="shared",
        )

        assert result["facts_updated"] == 0
        assert result["errors"] == [f"Failed to update fact ({target_id})" for target_id in targets]
        rows = await pool.fetch(
            "SELECT id, validity FROM facts WHERE id = ANY($1::uuid[])",
            list(targets),
        )
        assert {row["id"]: row["validity"] for row in rows} == {
            target_id: "active" for target_id in targets
        }
    finally:
        await pool.close()


def test_consolidation_rejects_cross_tenant_cross_source_and_edge_targets(
    memory_migrated_db: str,
) -> None:
    """Only a same-tenant, same-source live property fact can authorize an update."""
    asyncio.run(_assert_consolidation_target_boundaries(memory_migrated_db))


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

        # source_episode_ids=[] keeps this test focused on catalog write-behind,
        # independent of the derived_from-link loop. (That loop's store_fact
        # dict-vs-UUID bug is fixed in bu-jdi3f; see
        # test_consolidation_creates_episode_links_with_uuid_source_id for the
        # non-empty-episodes regression.)
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


# ---------------------------------------------------------------------------
# Regression: execute_consolidation must pass store_fact's UUID id (not its
# dict return) to create_link's UUID-typed source_id (bu-jdi3f)
# ---------------------------------------------------------------------------


async def _consolidate_with_episode_links(db_url: str) -> dict:
    """Run execute_consolidation with a non-empty ``source_episode_ids`` and
    read back the ``derived_from`` links created for the new fact."""
    import uuid

    from butlers.modules.memory.consolidation_executor import execute_consolidation
    from butlers.modules.memory.consolidation_parser import ConsolidationResult, NewFact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        embedding_engine = _fake_embedding_engine()
        episode_ids = [uuid.uuid4(), uuid.uuid4()]
        parsed = ConsolidationResult(
            new_facts=[
                NewFact(
                    subject="consolidation_user",
                    predicate="preference",
                    content="prefers window seats",
                )
            ]
        )
        result = await execute_consolidation(
            pool,
            embedding_engine,
            parsed,
            episode_ids,
            "general",
        )
        fact_row = await pool.fetchrow(
            "SELECT id FROM facts WHERE subject = 'consolidation_user' AND predicate = 'preference'"
        )
        links = await pool.fetch(
            "SELECT source_type, source_id, target_type, target_id, relation "
            "FROM memory_links "
            "WHERE source_type = 'fact' AND relation = 'derived_from' "
            "AND target_id = ANY($1)",
            episode_ids,
        )
        return {
            "result": result,
            "fact_id": fact_row["id"] if fact_row else None,
            "links": [dict(r) for r in links],
            "episode_ids": episode_ids,
        }
    finally:
        await pool.close()


def test_consolidation_creates_episode_links_with_uuid_source_id(
    memory_migrated_db: str,
) -> None:
    """Regression for bu-jdi3f.

    ``execute_consolidation`` must extract ``store_fact``'s ``["id"]`` UUID
    before handing it to ``create_link``'s UUID-typed ``source_id``. With a
    non-empty ``source_episode_ids`` this produces one ``derived_from`` link
    per source episode. Before the fix the whole dict return was passed as the
    source_id, asyncpg raised an encoding error, and every episode link was
    silently swallowed into the ``errors`` list (fact never linked).

    This MUST run against real Postgres: a mocked pool would happily accept the
    dict source_id and never surface the encoding failure (the exact
    mocked-pool-vs-integration trap class).
    """
    out = asyncio.run(_consolidate_with_episode_links(memory_migrated_db))

    result = out["result"]
    assert result["errors"] == [], f"unexpected consolidation errors: {result['errors']}"
    assert result["facts_created"] == 1
    assert out["fact_id"] is not None

    # One derived_from link per source episode, each anchored on the fact's UUID.
    links = out["links"]
    assert len(links) == len(out["episode_ids"])
    for link in links:
        assert link["source_id"] == out["fact_id"]
        assert link["source_type"] == "fact"
        assert link["target_type"] == "episode"
        assert link["relation"] == "derived_from"
    assert {link["target_id"] for link in links} == set(out["episode_ids"])


# ---------------------------------------------------------------------------
# mem_009: widen facts unique indexes to cover validity='fading' (bu-agj5a)
# ---------------------------------------------------------------------------


async def _raw_copy_active_over_fading_raises(db_url: str) -> None:
    """Teeth: with the widened index, a fresh ACTIVE fact cannot coexist with a
    FADING fact for the same key when supersession is bypassed (the race)."""
    from butlers.modules.memory.storage import store_fact

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:
        engine = _fake_embedding_engine()
        # 1. Real active property fact (no entity_id => (scope, subject, predicate) key).
        res = await store_fact(
            pool,
            subject="teeth_race_subj",
            predicate="teeth_race_pred",
            content="v1",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
        )
        fact_id = res["id"]
        # 2. Decay it to fading -- still the live/current value for its key.
        await pool.execute("UPDATE facts SET validity = 'fading' WHERE id = $1", fact_id)

        # 3. Simulate the concurrent-write race: land a NEW active fact for the
        #    SAME key WITHOUT going through store_fact's supersession, by copying
        #    the fading row verbatim (new id, validity='active'). The widened
        #    unique index must reject it. (Copy non-generated columns so the
        #    row is schema-complete regardless of future column additions.)
        cols = [
            r["column_name"]
            for r in await pool.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'facts' "
                "AND is_generated = 'NEVER' ORDER BY ordinal_position"
            )
        ]
        select_list = ", ".join(
            "gen_random_uuid()" if c == "id" else "'active'" if c == "validity" else c for c in cols
        )
        insert_sql = (
            f"INSERT INTO facts ({', '.join(cols)}) SELECT {select_list} FROM facts WHERE id = $1"
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(insert_sql, fact_id)
    finally:
        await pool.close()


def test_widened_index_rejects_fading_plus_active_coexistence(memory_migrated_db: str) -> None:
    """mem_009: the widened partial unique index forbids a fading fact and a new
    active fact coexisting for the same key -- the concurrent-write race bu-agj5a
    closes. Before mem_009 (validity='active'-only predicate) the raw insert
    succeeds; after, it raises UniqueViolation."""
    asyncio.run(_raw_copy_active_over_fading_raises(memory_migrated_db))


async def _supersede_over_fading_ok(db_url: str) -> dict:
    """The legitimate flow: a fresh write over a FADING fact supersedes it (marks
    old 'superseded' BEFORE inserting new 'active'), so it never trips the widened
    index -- proving the widening does not regress supersession-over-fading."""
    from butlers.modules.memory.storage import store_fact

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:
        engine = _fake_embedding_engine()
        r1 = await store_fact(
            pool,
            subject="teeth_sup_subj",
            predicate="teeth_sup_pred",
            content="v1",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
        )
        old_id = r1["id"]
        await pool.execute("UPDATE facts SET validity = 'fading' WHERE id = $1", old_id)

        r2 = await store_fact(
            pool,
            subject="teeth_sup_subj",
            predicate="teeth_sup_pred",
            content="v2",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
        )
        return {
            "old_id": str(old_id),
            "supersedes_id": str(r2["supersedes_id"]) if r2["supersedes_id"] else None,
            "old_validity": await pool.fetchval("SELECT validity FROM facts WHERE id = $1", old_id),
            "new_validity": await pool.fetchval(
                "SELECT validity FROM facts WHERE id = $1", r2["id"]
            ),
        }
    finally:
        await pool.close()


def test_supersession_over_fading_fact_still_works(memory_migrated_db: str) -> None:
    """mem_009 regression guard: writing a fresh fact for a key whose current fact
    is FADING supersedes the fading fact (does not raise), leaving exactly one
    live active fact -- the legitimate path the widened index must not break."""
    res = asyncio.run(_supersede_over_fading_ok(memory_migrated_db))
    assert res["old_validity"] == "superseded", "fading fact should be superseded, not left live"
    assert res["new_validity"] == "active"
    assert res["supersedes_id"] == res["old_id"], "new fact must link to the superseded fading fact"


# ---------------------------------------------------------------------------
# bu-6gsmh: write-time sensitivity exclusion for public.memory_catalog
# ---------------------------------------------------------------------------


async def _write_behind_catalog_row(db_url: str, *, sensitivity: str) -> dict:
    """Store a fact and a rule with the given sensitivity, write-behind ON.

    Returns whether each landed a public.memory_catalog row, and what
    sensitivity value that row carries (proves the write-behind call now
    actually forwards sensitivity -- it silently dropped it before bu-6gsmh).
    """
    from butlers.modules.memory.storage import store_fact, store_rule

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:
        engine = _fake_embedding_engine()
        unique = uuid.uuid4().hex[:8]

        fact = await store_fact(
            pool,
            subject=f"sens_subj_{unique}",
            predicate=f"sens_pred_{unique}",
            content="secret value" if sensitivity != "normal" else "ordinary value",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            sensitivity=sensitivity,
            enable_shared_catalog=True,
            source_schema="public",
        )
        rule_id = await store_rule(
            pool,
            content=f"sensitivity rule marker {unique}",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            sensitivity=sensitivity,
            enable_shared_catalog=True,
            source_schema="public",
        )

        fact_row = await pool.fetchrow(
            "SELECT sensitivity FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            fact["id"],
        )
        rule_row = await pool.fetchrow(
            "SELECT sensitivity FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            rule_id,
        )
        return {
            "fact_cataloged": fact_row is not None,
            "fact_sensitivity": fact_row["sensitivity"] if fact_row else None,
            "rule_cataloged": rule_row is not None,
            "rule_sensitivity": rule_row["sensitivity"] if rule_row else None,
        }
    finally:
        await pool.close()


@pytest.mark.parametrize("sensitivity", ["pii", "confidential"])
def test_write_behind_excludes_confidential_and_pii_from_catalog(
    memory_migrated_db: str, sensitivity: str
) -> None:
    """store_fact/store_rule write-behind never catalogs pii/confidential rows.

    Owner ruling (bu-6gsmh): defense-in-depth write-time exclusion on top of
    the existing read-time authorization ceiling in search.py.
    """
    result = asyncio.run(_write_behind_catalog_row(memory_migrated_db, sensitivity=sensitivity))
    assert result["fact_cataloged"] is False, result
    assert result["rule_cataloged"] is False, result


def test_write_behind_catalogs_normal_sensitivity_and_forwards_the_value(
    memory_migrated_db: str,
) -> None:
    """A 'normal'-sensitivity fact/rule is still cataloged, and the catalog row's
    sensitivity column now actually reflects it (regression guard for the
    dropped retention_class/sensitivity kwargs discovered while implementing
    bu-6gsmh -- store_fact/store_rule's write-behind call never forwarded
    them, so every catalog row's sensitivity was silently NULL)."""
    result = asyncio.run(_write_behind_catalog_row(memory_migrated_db, sensitivity="normal"))
    assert result["fact_cataloged"] is True, result
    assert result["fact_sensitivity"] == "normal", result
    assert result["rule_cataloged"] is True, result
    assert result["rule_sensitivity"] == "normal", result


async def _backfill_excludes_sensitive(db_url: str) -> dict:
    """Store confidential/pii facts+rules WITHOUT write-behind (simulating a
    pre-existing backlog), then confirm run_memory_catalog_backfill refuses to
    backfill them -- the guard must hold for the backfill path too, or the
    backfill job would keep re-introducing rows the write-time guard and the
    core_183 purge migration both exist to keep out."""
    from butlers.modules.memory.storage import run_memory_catalog_backfill, store_fact, store_rule

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:
        engine = _fake_embedding_engine()
        unique = uuid.uuid4().hex[:8]

        confidential_fact = await store_fact(
            pool,
            subject=f"backfill_sens_subj_{unique}",
            predicate=f"backfill_sens_pred_{unique}",
            content="secret backlog value",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            sensitivity="confidential",
            enable_shared_catalog=False,
        )
        pii_rule_id = await store_rule(
            pool,
            content=f"pii backlog rule marker {unique}",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            sensitivity="pii",
            enable_shared_catalog=False,
        )

        await run_memory_catalog_backfill(pool, source_schema="public", batch_size=200)

        fact_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            confidential_fact["id"],
        )
        rule_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            pii_rule_id,
        )
        return {
            "confidential_fact_cataloged": fact_row is not None,
            "pii_rule_cataloged": rule_row is not None,
        }
    finally:
        await pool.close()


def test_backfill_excludes_confidential_and_pii_facts_and_rules(memory_migrated_db: str) -> None:
    """run_memory_catalog_backfill must not backfill pii/confidential rows,
    matching the live write-behind guard (bu-6gsmh)."""
    result = asyncio.run(_backfill_excludes_sensitive(memory_migrated_db))
    assert result["confidential_fact_cataloged"] is False, result
    assert result["pii_rule_cataloged"] is False, result


async def _backfill_rule_catalog_metadata(
    db_url: str, *, sensitive_sensitivity: str
) -> dict[str, object]:
    """Backfill a normal rule while ensuring a sensitive peer stays excluded."""
    from butlers.modules.memory.storage import run_memory_catalog_backfill, store_rule

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:
        engine = _fake_embedding_engine()
        unique = uuid.uuid4().hex[:8]

        normal_rule_id = await store_rule(
            pool,
            content=f"normal rule metadata marker {unique}",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            retention_class="rule",
            sensitivity="normal",
            enable_shared_catalog=False,
        )
        sensitive_rule_id = await store_rule(
            pool,
            content=f"{sensitive_sensitivity} rule exclusion marker {unique}",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            retention_class="rule",
            sensitivity=sensitive_sensitivity,
            enable_shared_catalog=False,
        )

        await run_memory_catalog_backfill(pool, source_schema="public", batch_size=200)

        normal_row = await pool.fetchrow(
            "SELECT retention_class, sensitivity FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            normal_rule_id,
        )
        sensitive_row = await pool.fetchrow(
            "SELECT source_id FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            sensitive_rule_id,
        )
        return {
            "normal_row": dict(normal_row) if normal_row is not None else None,
            "sensitive_cataloged": sensitive_row is not None,
        }
    finally:
        await pool.close()


@pytest.mark.parametrize("sensitive_sensitivity", ["pii", "confidential"])
def test_backfill_rules_forwards_normal_metadata_without_cataloging_sensitive_rules(
    memory_migrated_db: str, sensitive_sensitivity: str
) -> None:
    """Rules backfill preserves normal metadata and keeps pii/confidential excluded."""
    result = asyncio.run(
        _backfill_rule_catalog_metadata(
            memory_migrated_db, sensitive_sensitivity=sensitive_sensitivity
        )
    )
    assert result["normal_row"] == {"retention_class": "rule", "sensitivity": "normal"}, result
    assert result["sensitive_cataloged"] is False, result
