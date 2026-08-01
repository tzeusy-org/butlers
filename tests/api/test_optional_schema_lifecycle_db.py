"""Real-Postgres regressions for optional-schema lifecycle classification.

The dashboard may query a schema whose optional tables were deliberately never
installed.  That absence is normal.  It must not, however, make an
``UndefinedTableError`` from a table that existed while the dashboard was
running look normal: a post-startup ``DROP TABLE`` is an operationally
degraded source.

These tests use the real core and memory migrations plus actual ``DROP TABLE``
statements.  They intentionally exercise the same asyncpg failures that the
secrets inventory and memory fan-out classifiers receive in production.
"""

from __future__ import annotations

import logging
import shutil
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi import HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.models.memory import ReembedRunRequest
from butlers.api.routers.activity_feed import get_activity_feed
from butlers.api.routers.memory import (
    _fan_out_memory_queries,
    confirm_fact,
    get_butler_memory_stats,
    get_episode,
    get_memory_stats,
    get_reembed_pending,
    inspect_memory,
    list_activity,
    list_episodes,
    retract_fact,
    run_reembed,
)
from butlers.api.routers.preferences import get_preferences
from butlers.api.routers.secrets_v2 import (
    SystemSetRequest,
    _fetch_system_secrets,
    _system_probe_timestamps,
    delete_system_credential,
    get_cli_credential,
    get_system_credential,
    probe_system_credential,
    revoke_cli_credential,
    rotate_cli_credential,
    set_system_credential,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_LOGGER = logging.getLogger("test_optional_schema_lifecycle_db")
_TRACKED_RELATIONS = (
    "butler_secrets",
    "episodes",
    "facts",
    "rules",
    "memory_links",
    "episode_tombstones",
)
_MEMORY_RELATIONS = ("episodes", "facts", "rules", "memory_links", "episode_tombstones")


@pytest.fixture
def migrated_db_url(postgres_container) -> str:
    """Provision one schema with the real core and memory tables."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
        schemas={"core": "lifecycle", "memory": "lifecycle"},
    )


@pytest.fixture
def chronicler_memory_db_url(postgres_container) -> str:
    """Provision Chronicler's domain and private memory schemas separately."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler", "memory"],
        schemas={
            "core": "chronicler",
            "chronicler": "chronicler",
            "memory": "chronicler_mem",
        },
    )


@pytest.fixture
def chronicler_domain_only_db_url(postgres_container) -> str:
    """Provision Chronicler without its optional private memory schema."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
    )


async def _manager_for_schema(
    db_url: str,
    *,
    butler_name: str,
    schema: str,
    memory_schema: str | None = None,
    with_shared_pool: bool = False,
) -> DatabaseManager:
    """Create a real dashboard pool scoped to *schema*."""
    parsed = urlparse(db_url)
    manager = DatabaseManager(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        min_pool_size=1,
        max_pool_size=2,
    )
    add_kwargs = {
        "db_name": parsed.path.lstrip("/"),
        "db_schema": schema,
    }
    if memory_schema is not None:
        add_kwargs["memory_schema"] = memory_schema
    await manager.add_butler(
        butler_name,
        **add_kwargs,
    )
    if with_shared_pool:
        await manager.set_credential_shared_pool(
            parsed.path.lstrip("/"),
            db_schema="public",
        )
    return manager


async def _memory_fact_count(_: str, pool: object) -> int:
    """A real memory-table query used by the fan-out lifecycle assertions."""
    return await pool.fetchval("SELECT count(*) FROM facts") or 0  # type: ignore[attr-defined]


async def _seed_public_secret(
    manager: DatabaseManager,
    *,
    key: str,
    value: str = "public-secret",
) -> None:
    """Seed the shared public relation that a private schema must never shadow."""
    await manager.credential_shared_pool().execute(
        """
        INSERT INTO public.butler_secrets (secret_key, secret_value, category, updated_at)
        VALUES ($1, $2, 'general', now())
        """,
        key,
        value,
    )


async def _shadow_fact_in_public(
    pool: object,
    fact_id,
    *,
    stale_embedding: bool = False,
) -> None:
    """Copy one private fact into a same-named public shadow relation."""
    if stale_embedding:
        await pool.execute(  # type: ignore[attr-defined]
            """
            INSERT INTO lifecycle.facts (
                id, subject, predicate, content, source_butler,
                embedding, embedding_model_version
            )
            VALUES (
                $1, 'owner', 'prefers', 'tea', 'lifecycle',
                array_fill(0::real, ARRAY[384])::vector, 'old-model'
            )
            """,
            fact_id,
        )
    else:
        await pool.execute(  # type: ignore[attr-defined]
            """
            INSERT INTO lifecycle.facts (id, subject, predicate, content, source_butler)
            VALUES ($1, 'owner', 'prefers', 'tea', 'lifecycle')
            """,
            fact_id,
        )
    await pool.execute(  # type: ignore[attr-defined]
        "CREATE TABLE public.facts AS TABLE lifecycle.facts WITH NO DATA"
    )
    await pool.execute(  # type: ignore[attr-defined]
        "INSERT INTO public.facts SELECT * FROM lifecycle.facts WHERE id = $1",
        fact_id,
    )


async def _seed_owner(pool: object) -> object:
    """Ensure the public owner entity exists and return its id."""
    owner_id = await pool.fetchval(  # type: ignore[attr-defined]
        "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
    )
    if owner_id is None:
        owner_id = await pool.fetchval(  # type: ignore[attr-defined]
            """
            INSERT INTO public.entities (canonical_name, entity_type, roles)
            VALUES ('Lifecycle Test Owner', 'person', ARRAY['owner'])
            RETURNING id
            """
        )
    assert owner_id is not None
    return owner_id


async def _shadow_preference_in_public(
    pool: object,
    fact_id: object,
    owner_id: object,
) -> None:
    """Copy an owner preference to a same-named public facts shadow."""
    await pool.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO lifecycle.facts (
            id, subject, predicate, content, importance, source_butler,
            entity_id, scope, permanence
        )
        VALUES (
            $1, 'owner', 'preferences:general_timezone', 'public-shadow', 8.0,
            'lifecycle', $2, 'global', 'stable'
        )
        """,
        fact_id,
        owner_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        "CREATE TABLE public.facts AS TABLE lifecycle.facts WITH NO DATA"
    )
    await pool.execute(  # type: ignore[attr-defined]
        "INSERT INTO public.facts SELECT * FROM lifecycle.facts WHERE id = $1",
        fact_id,
    )


async def _shadow_episode_in_public(pool: object, episode_id: object) -> None:
    """Copy an episode to a same-named public episodes shadow."""
    await pool.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO lifecycle.episodes (id, butler, content, importance)
        VALUES ($1, 'lifecycle', 'must-not-leak', 5.0)
        """,
        episode_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        "CREATE TABLE public.episodes AS TABLE lifecycle.episodes WITH NO DATA"
    )
    await pool.execute(  # type: ignore[attr-defined]
        "INSERT INTO public.episodes SELECT * FROM lifecycle.episodes WHERE id = $1",
        episode_id,
    )


async def _snapshot_chronicler_memory_relations(manager: DatabaseManager) -> None:
    """Capture domain secrets separately from Chronicler's effective memory source."""
    await manager.snapshot_relation_presence("chronicler", ("butler_secrets",))
    await manager.snapshot_memory_relation_presence("chronicler", _MEMORY_RELATIONS)


async def _seed_chronicler_memory(
    pool: object,
    owner_id: object,
) -> tuple[object, object]:
    """Seed distinct private facts and episodes for dashboard read assertions."""
    fact_id = uuid4()
    episode_id = uuid4()
    await pool.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO chronicler_mem.facts (
            id, subject, predicate, content, importance, source_butler,
            entity_id, scope, permanence
        )
        VALUES (
            $1, 'owner', 'preferences:general_timezone', 'Asia/Singapore', 8.0,
            'chronicler', $2, 'global', 'stable'
        )
        """,
        fact_id,
        owner_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO chronicler_mem.episodes (id, butler, content, importance)
        VALUES ($1, 'chronicler', 'private chronicler memory episode', 5.0)
        """,
        episode_id,
    )
    return fact_id, episode_id


async def _create_chronicler_public_memory_shadows(
    pool: object,
    owner_id: object,
) -> tuple[object, object]:
    """Create public shadows that a configured private source must never read."""
    fact_id = uuid4()
    episode_id = uuid4()
    await pool.execute(  # type: ignore[attr-defined]
        """
        CREATE TABLE public.facts (
            id UUID PRIMARY KEY,
            predicate TEXT NOT NULL,
            content TEXT NOT NULL,
            scope TEXT,
            importance DOUBLE PRECISION,
            permanence TEXT,
            created_at TIMESTAMPTZ,
            confidence DOUBLE PRECISION,
            decay_rate DOUBLE PRECISION,
            last_confirmed_at TIMESTAMPTZ,
            entity_id UUID,
            validity TEXT NOT NULL
        )
        """
    )
    await pool.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO public.facts (
            id, predicate, content, scope, importance, permanence, created_at,
            confidence, decay_rate, entity_id, validity
        )
        VALUES (
            $1, 'preferences:general_timezone', 'public-shadow', 'global', 8.0,
            'stable', now(), 1.0, 0.0, $2, 'active'
        )
        """,
        fact_id,
        owner_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        """
        CREATE TABLE public.episodes (
            id UUID PRIMARY KEY,
            content TEXT,
            importance DOUBLE PRECISION,
            consolidation_status TEXT,
            created_at TIMESTAMPTZ,
            session_id UUID
        )
        """
    )
    await pool.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO public.episodes (
            id, content, importance, consolidation_status, created_at
        )
        VALUES ($1, 'public-shadow', 5.0, 'pending', now())
        """,
        episode_id,
    )
    return fact_id, episode_id


async def _copy_chronicler_memory_to_public_shadows(
    pool: object,
    fact_id: object,
    episode_id: object,
) -> None:
    """Copy private rows into public shadow tables with distinguishable values."""
    await pool.execute(  # type: ignore[attr-defined]
        "CREATE TABLE public.facts AS TABLE chronicler_mem.facts WITH NO DATA"
    )
    await pool.execute(  # type: ignore[attr-defined]
        "INSERT INTO public.facts SELECT * FROM chronicler_mem.facts WHERE id = $1",
        fact_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        "UPDATE public.facts SET content = 'public-shadow' WHERE id = $1",
        fact_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        "CREATE TABLE public.episodes AS TABLE chronicler_mem.episodes WITH NO DATA"
    )
    await pool.execute(  # type: ignore[attr-defined]
        "INSERT INTO public.episodes SELECT * FROM chronicler_mem.episodes WHERE id = $1",
        episode_id,
    )
    await pool.execute(  # type: ignore[attr-defined]
        "UPDATE public.episodes SET content = 'public-shadow' WHERE id = $1",
        episode_id,
    )


async def test_configured_chronicler_memory_schema_serves_preferences_and_activity(
    chronicler_memory_db_url: str,
) -> None:
    """Dashboard reads Chronicler memory from ``chronicler_mem``, not its domain schema."""
    manager = await _manager_for_schema(
        chronicler_memory_db_url,
        butler_name="chronicler",
        schema="chronicler",
        memory_schema="chronicler_mem",
    )
    try:
        assert manager.schema_for_butler("chronicler") == "chronicler"
        assert manager.memory_schema_for_butler("chronicler") == "chronicler_mem"
        await _snapshot_chronicler_memory_relations(manager)
        assert manager.relation_observed_since_start("chronicler", "butler_secrets") is True
        assert manager.relation_observed_since_start("chronicler", "facts") is True
        assert manager.relation_observed_since_start("chronicler", "episodes") is True

        pool = manager.pool("chronicler")
        owner_id = await _seed_owner(pool)
        fact_id, episode_id = await _seed_chronicler_memory(pool, owner_id)

        preferences = await get_preferences(predicate=None, db=manager)
        activity = await get_activity_feed("chronicler", limit=50, db=manager)

        assert [(entry.predicate, entry.value) for entry in preferences.data] == [
            ("preferences:general_timezone", "Asia/Singapore"),
        ]
        memory_events = [event for event in activity.events if event.event_type == "memory_write"]
        assert [(event.entity_id, event.summary) for event in memory_events] == [
            (str(episode_id), "private chronicler memory episode"),
        ]
        assert (
            await pool.fetchval("SELECT count(*) FROM chronicler_mem.facts WHERE id = $1", fact_id)
            == 1
        )
    finally:
        await manager.close()


async def test_configured_chronicler_memory_absent_at_boot_skips_public_shadows(
    chronicler_domain_only_db_url: str,
) -> None:
    """An optional private schema absent at boot is empty, never public-backed."""
    manager = await _manager_for_schema(
        chronicler_domain_only_db_url,
        butler_name="chronicler",
        schema="chronicler",
        memory_schema="chronicler_mem",
    )
    try:
        pool = manager.pool("chronicler")
        owner_id = await _seed_owner(pool)
        public_fact_id, public_episode_id = await _create_chronicler_public_memory_shadows(
            pool,
            owner_id,
        )
        await _snapshot_chronicler_memory_relations(manager)
        assert manager.relation_observed_since_start("chronicler", "facts") is False
        assert manager.relation_observed_since_start("chronicler", "episodes") is False

        preferences = await get_preferences(predicate=None, db=manager)
        activity = await get_activity_feed("chronicler", limit=50, db=manager)

        assert preferences.data == []
        assert activity.events == []
        assert await pool.fetchval(
            "SELECT content FROM public.facts WHERE id = $1", public_fact_id
        ) == ("public-shadow")
        assert (
            await pool.fetchval(
                "SELECT content FROM public.episodes WHERE id = $1", public_episode_id
            )
            == "public-shadow"
        )
    finally:
        await manager.close()


@pytest.mark.parametrize("snapshot_memory", [True, False], ids=["post-start-loss", "unknown"])
async def test_configured_chronicler_memory_loss_fails_closed_without_public_fallback(
    chronicler_memory_db_url: str,
    snapshot_memory: bool,
) -> None:
    """A dropped or unsnapshotted private source must return 503 instead of public data."""
    manager = await _manager_for_schema(
        chronicler_memory_db_url,
        butler_name="chronicler",
        schema="chronicler",
        memory_schema="chronicler_mem",
    )
    try:
        pool = manager.pool("chronicler")
        owner_id = await _seed_owner(pool)
        fact_id, episode_id = await _seed_chronicler_memory(pool, owner_id)
        await _copy_chronicler_memory_to_public_shadows(pool, fact_id, episode_id)
        if snapshot_memory:
            await _snapshot_chronicler_memory_relations(manager)
            assert manager.relation_observed_since_start("chronicler", "facts") is True
            assert manager.relation_observed_since_start("chronicler", "episodes") is True
        else:
            assert manager.relation_observed_since_start("chronicler", "facts") is None
            assert manager.relation_observed_since_start("chronicler", "episodes") is None

        await pool.execute("DROP TABLE chronicler_mem.facts CASCADE")
        await pool.execute("DROP TABLE chronicler_mem.episodes CASCADE")

        with pytest.raises(HTTPException) as preferences_error:
            await get_preferences(predicate=None, db=manager)
        with pytest.raises(HTTPException) as activity_error:
            await get_activity_feed("chronicler", limit=50, db=manager)

        assert preferences_error.value.status_code == 503
        assert activity_error.value.status_code == 503
        assert await pool.fetchval("SELECT content FROM public.facts WHERE id = $1", fact_id) == (
            "public-shadow"
        )
        assert await pool.fetchval(
            "SELECT content FROM public.episodes WHERE id = $1", episode_id
        ) == ("public-shadow")
    finally:
        await manager.close()


async def test_absent_at_startup_is_an_expected_optional_schema(
    migrated_db_url: str,
) -> None:
    """A never-migrated schema is skipped by both surfaces without degradation."""
    bootstrap = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    manager: DatabaseManager | None = None
    try:
        assert await bootstrap.pool("lifecycle").fetchval(
            "SELECT to_regclass('public.butler_secrets') IS NOT NULL"
        )
        await bootstrap.pool("lifecycle").execute("CREATE SCHEMA never_installed")
        manager = await _manager_for_schema(
            migrated_db_url,
            butler_name="never_installed",
            schema="never_installed",
        )
        await manager.snapshot_relation_presence("never_installed", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("never_installed", "butler_secrets") is False

        secrets_tracker = DegradedSources(_LOGGER)
        secret_rows = await _fetch_system_secrets(
            manager.pool("never_installed"),
            "never_installed",
            source_schema=manager.schema_for_butler("never_installed"),
            schema_absent_at_start=(
                manager.relation_observed_since_start("never_installed", "butler_secrets") is False
            ),
            tracker=secrets_tracker,
        )
        memory_tracker = DegradedSources(_LOGGER)
        memory_rows = await _fan_out_memory_queries(
            manager,
            query_name="lifecycle-absence",
            query_fn=_memory_fact_count,
            tracker=memory_tracker,
        )

        assert secret_rows == []
        assert secrets_tracker.failed is False
        assert memory_rows == []
        assert memory_tracker.failed is False
    finally:
        if manager is not None:
            await manager.close()
        await bootstrap.close()


async def test_dropped_after_startup_is_a_degraded_source(
    migrated_db_url: str,
) -> None:
    """Real post-startup drops surface on both secrets and memory trackers."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "butler_secrets") is True
        assert manager.relation_observed_since_start("lifecycle", "facts") is True

        pool = manager.pool("lifecycle")
        assert await pool.fetchval("SELECT to_regclass('public.butler_secrets') IS NOT NULL")
        await pool.execute("DROP TABLE butler_secrets")
        await pool.execute("DROP TABLE facts")

        secrets_tracker = DegradedSources(_LOGGER)
        secret_rows = await _fetch_system_secrets(
            pool,
            "lifecycle",
            source_schema=manager.schema_for_butler("lifecycle"),
            schema_absent_at_start=(
                manager.relation_observed_since_start("lifecycle", "butler_secrets") is False
            ),
            tracker=secrets_tracker,
        )
        memory_tracker = DegradedSources(_LOGGER)
        memory_rows = await _fan_out_memory_queries(
            manager,
            query_name="lifecycle-drop",
            query_fn=_memory_fact_count,
            tracker=memory_tracker,
        )

        assert secret_rows == []
        assert secrets_tracker.names == ["lifecycle"]
        assert memory_rows == []
        assert memory_tracker.names == ["lifecycle"]
    finally:
        await manager.close()


async def test_preferences_skip_public_fact_shadow_when_memory_absent_at_startup(
    migrated_db_url: str,
) -> None:
    """A never-installed memory schema must not read public preference facts."""
    bootstrap = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    manager: DatabaseManager | None = None
    try:
        pool = bootstrap.pool("lifecycle")
        await pool.execute("CREATE SCHEMA never_installed")
        owner_id = await _seed_owner(pool)
        await _shadow_preference_in_public(pool, uuid4(), owner_id)

        manager = await _manager_for_schema(
            migrated_db_url,
            butler_name="never_installed",
            schema="never_installed",
        )
        await manager.snapshot_relation_presence("never_installed", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("never_installed", "facts") is False

        preferences = await get_preferences(predicate=None, db=manager)

        assert preferences.data == []
    finally:
        if manager is not None:
            await manager.close()
        await bootstrap.close()


async def test_activity_feed_skips_public_episode_shadow_when_memory_absent_at_startup(
    migrated_db_url: str,
) -> None:
    """A never-installed memory schema must not read public activity episodes."""
    bootstrap = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    manager: DatabaseManager | None = None
    try:
        pool = bootstrap.pool("lifecycle")
        await pool.execute("CREATE SCHEMA never_installed")
        await _shadow_episode_in_public(pool, uuid4())

        manager = await _manager_for_schema(
            migrated_db_url,
            butler_name="never_installed",
            schema="never_installed",
        )
        await manager.snapshot_relation_presence("never_installed", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("never_installed", "episodes") is False

        activity = await get_activity_feed("never_installed", limit=50, db=manager)

        assert activity.events == []
    finally:
        if manager is not None:
            await manager.close()
        await bootstrap.close()


async def test_post_boot_preferences_memory_loss_blocks_public_fact_shadow(
    migrated_db_url: str,
) -> None:
    """A lost private facts table makes preferences unavailable, never public-backed."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "facts") is True
        pool = manager.pool("lifecycle")
        owner_id = await _seed_owner(pool)
        await _shadow_preference_in_public(pool, uuid4(), owner_id)
        await pool.execute("DROP TABLE lifecycle.facts")

        with pytest.raises(HTTPException) as error:
            await get_preferences(predicate=None, db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_post_boot_activity_memory_loss_blocks_public_episode_shadow(
    migrated_db_url: str,
) -> None:
    """A lost private episodes table makes the raw activity feed unavailable."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "episodes") is True
        pool = manager.pool("lifecycle")
        await _shadow_episode_in_public(pool, uuid4())
        await pool.execute("DROP TABLE lifecycle.episodes CASCADE")

        with pytest.raises(HTTPException) as error:
            await get_activity_feed("lifecycle", limit=50, db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_unknown_preferences_memory_loss_blocks_public_fact_shadow(
    migrated_db_url: str,
) -> None:
    """An unsnapshotted facts loss must fail closed instead of reading public.facts."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        pool = manager.pool("lifecycle")
        owner_id = await _seed_owner(pool)
        await _shadow_preference_in_public(pool, uuid4(), owner_id)
        await pool.execute("DROP TABLE lifecycle.facts")
        assert manager.relation_observed_since_start("lifecycle", "facts") is None

        with pytest.raises(HTTPException) as error:
            await get_preferences(predicate=None, db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_unknown_activity_memory_loss_blocks_public_episode_shadow(
    migrated_db_url: str,
) -> None:
    """An unsnapshotted episodes loss must fail closed instead of reading public.episodes."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        pool = manager.pool("lifecycle")
        await _shadow_episode_in_public(pool, uuid4())
        await pool.execute("DROP TABLE lifecycle.episodes CASCADE")
        assert manager.relation_observed_since_start("lifecycle", "episodes") is None

        with pytest.raises(HTTPException) as error:
            await get_activity_feed("lifecycle", limit=50, db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_post_boot_butler_memory_stats_never_reads_public_facts(
    migrated_db_url: str,
) -> None:
    """A dropped private facts table must not become a healthy public-table count."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "facts") is True

        pool = manager.pool("lifecycle")
        await pool.execute(
            "CREATE TABLE public.facts (created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        await pool.execute("INSERT INTO public.facts DEFAULT VALUES")
        await pool.execute("DROP TABLE lifecycle.facts")

        with pytest.raises(HTTPException) as error:
            await get_butler_memory_stats("lifecycle", db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_unknown_butler_memory_stats_schema_loss_is_unavailable(
    migrated_db_url: str,
) -> None:
    """No startup marker must fail closed rather than return invented zero stats."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        await manager.pool("lifecycle").execute("DROP TABLE lifecycle.facts")
        assert manager.relation_observed_since_start("lifecycle", "facts") is None

        with pytest.raises(HTTPException) as error:
            await get_butler_memory_stats("lifecycle", db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_memory_list_detail_and_inspect_do_not_read_public_episodes(
    migrated_db_url: str,
) -> None:
    """Memory fan-out must retain private ownership after a table drops."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    episode_id = uuid4()
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "episodes") is True

        pool = manager.pool("lifecycle")
        await pool.execute(
            """
            CREATE TABLE public.episodes (
                id UUID PRIMARY KEY,
                butler TEXT NOT NULL,
                session_id UUID,
                content TEXT,
                importance DOUBLE PRECISION NOT NULL,
                reference_count INTEGER NOT NULL,
                consolidated BOOLEAN NOT NULL,
                consolidation_status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                last_referenced_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                metadata JSONB NOT NULL
            )
            """
        )
        await pool.execute(
            """
            INSERT INTO public.episodes (
                id, butler, content, importance, reference_count, consolidated,
                consolidation_status, created_at, metadata
            )
            VALUES ($1, 'public', 'must-not-leak', 0.5, 0, false, 'pending', now(), '{}'::jsonb)
            """,
            episode_id,
        )
        await pool.execute("DROP TABLE lifecycle.episodes CASCADE")

        listed = await list_episodes(
            butler=None,
            consolidated=None,
            status=None,
            since=None,
            until=None,
            offset=0,
            limit=50,
            db=manager,
        )
        assert listed.data == []
        assert listed.meta.pools_failed == ["lifecycle"]

        activity = await list_activity(limit=50, db=manager)
        assert activity.data == []
        assert activity.meta.pools_failed == ["lifecycle"]

        with pytest.raises(HTTPException) as detail_error:
            await get_episode(str(episode_id), db=manager)
        assert detail_error.value.status_code == 503

        inspected = await inspect_memory(
            q=None,
            kind="episode",
            offset=0,
            limit=50,
            db=manager,
        )
        assert inspected.data == []
        assert inspected.meta.pools_failed == ["lifecycle"]
    finally:
        await manager.close()


async def test_boot_time_private_absence_can_use_a_public_system_secret(
    migrated_db_url: str,
) -> None:
    """An optional private table absent at boot may legitimately defer to public."""
    bootstrap = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    manager: DatabaseManager | None = None
    key = "BOOT_ABSENT_PUBLIC_KEY"
    try:
        await bootstrap.pool("lifecycle").execute("CREATE SCHEMA boot_absent")
        manager = await _manager_for_schema(
            migrated_db_url,
            butler_name="boot_absent",
            schema="boot_absent",
            with_shared_pool=True,
        )
        await manager.snapshot_relation_presence("boot_absent", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("boot_absent", "butler_secrets") is False
        await _seed_public_secret(manager, key=key)

        detail = await get_system_credential(key, db=manager)

        assert detail.data.source == "shared-public"
        assert detail.data.key == key
        _system_probe_timestamps.pop(key, None)
        probe = await probe_system_credential(key, db=manager)
        assert probe.data.ok is True
        assert (
            await manager.credential_shared_pool().fetchval(
                """
            SELECT last_test_ok
            FROM public.butler_secrets
            WHERE secret_key = $1
            """,
                key,
            )
            is True
        )
    finally:
        _system_probe_timestamps.pop(key, None)
        if manager is not None:
            await manager.close()
        await bootstrap.close()


async def test_post_boot_private_loss_blocks_get_and_probe_public_fallback(
    migrated_db_url: str,
) -> None:
    """A dropped private table is degraded, never a route to a public credential."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    key = "DROPPED_PRIVATE_PUBLIC_KEY"
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "butler_secrets") is True
        await _seed_public_secret(manager, key=key)
        pool = manager.pool("lifecycle")
        await pool.execute("DROP TABLE butler_secrets")

        with pytest.raises(HTTPException) as get_error:
            await get_system_credential(key, db=manager)
        assert get_error.value.status_code == 503

        _system_probe_timestamps.pop(key, None)
        with pytest.raises(HTTPException) as probe_error:
            await probe_system_credential(key, db=manager)
        assert probe_error.value.status_code == 503

        shared_pool = manager.credential_shared_pool()
        assert (
            await shared_pool.fetchval(
                """
            SELECT count(*)
            FROM public.secret_probe_log
            WHERE credential_scope = 'system' AND credential_key = $1
            """,
                key,
            )
            == 0
        )
        cache = await shared_pool.fetchrow(
            """
            SELECT last_test_ok, last_verified
            FROM public.butler_secrets
            WHERE secret_key = $1
            """,
            key,
        )
        assert cache is not None
        assert cache["last_test_ok"] is None
        assert cache["last_verified"] is None
    finally:
        _system_probe_timestamps.pop(key, None)
        await manager.close()


async def test_post_boot_shared_public_loss_blocks_system_credential_get(
    migrated_db_url: str,
) -> None:
    """A shared credential table lost after boot is unavailable, not absent."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    key = "DROPPED_SHARED_PUBLIC_GET_KEY"
    try:
        shared_pool = manager.credential_shared_pool()
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        await manager.snapshot_relation_presence(
            "shared-public",
            ("butler_secrets",),
            pool=shared_pool,
        )
        assert manager.relation_observed_since_start("shared-public", "butler_secrets") is True
        await _seed_public_secret(manager, key=key)
        await shared_pool.execute("DROP TABLE public.butler_secrets")

        with pytest.raises(HTTPException) as error:
            await get_system_credential(key, db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_post_boot_shared_public_loss_blocks_system_credential_probe(
    migrated_db_url: str,
) -> None:
    """A shared credential table lost after boot cannot be probed as missing."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    key = "DROPPED_SHARED_PUBLIC_PROBE_KEY"
    try:
        shared_pool = manager.credential_shared_pool()
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        await manager.snapshot_relation_presence(
            "shared-public",
            ("butler_secrets",),
            pool=shared_pool,
        )
        assert manager.relation_observed_since_start("shared-public", "butler_secrets") is True
        await _seed_public_secret(manager, key=key)
        await shared_pool.execute("DROP TABLE public.butler_secrets")
        _system_probe_timestamps.pop(key, None)

        with pytest.raises(HTTPException) as error:
            await probe_system_credential(key, db=manager)

        assert error.value.status_code == 503
    finally:
        _system_probe_timestamps.pop(key, None)
        await manager.close()


async def test_post_boot_shared_public_loss_blocks_cli_credential_get(
    migrated_db_url: str,
) -> None:
    """A shared CLI credential table lost after boot is unavailable, not absent."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    key = "DROPPED_SHARED_PUBLIC_CLI_KEY"
    try:
        shared_pool = manager.credential_shared_pool()
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        await manager.snapshot_relation_presence(
            "shared-public",
            ("butler_secrets",),
            pool=shared_pool,
        )
        assert manager.relation_observed_since_start("shared-public", "butler_secrets") is True
        await shared_pool.execute(
            """
            INSERT INTO public.butler_secrets (secret_key, secret_value, category, updated_at)
            VALUES ($1, 'cli-secret', 'cli', now())
            """,
            key,
        )
        await shared_pool.execute("DROP TABLE public.butler_secrets")

        with pytest.raises(HTTPException) as error:
            await get_cli_credential(key, db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_unknown_private_absence_blocks_public_fallback(
    migrated_db_url: str,
) -> None:
    """Without a startup marker, a missing private relation is never an optional absence."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    key = "UNKNOWN_PRIVATE_PUBLIC_KEY"
    try:
        await _seed_public_secret(manager, key=key)
        await manager.pool("lifecycle").execute("DROP TABLE butler_secrets")
        assert manager.relation_observed_since_start("lifecycle", "butler_secrets") is None

        with pytest.raises(HTTPException) as error:
            await get_system_credential(key, db=manager)
        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_post_boot_private_loss_cannot_set_or_delete_public_secret(
    migrated_db_url: str,
) -> None:
    """Per-butler mutations must target the private relation, never search_path public."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    insert_key = "DROPPED_PRIVATE_SET_KEY"
    delete_key = "DROPPED_PRIVATE_DELETE_KEY"
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        await _seed_public_secret(manager, key=delete_key, value="must-survive")
        await manager.pool("lifecycle").execute("DROP TABLE butler_secrets")

        with pytest.raises(HTTPException) as set_error:
            await set_system_credential(
                insert_key,
                SystemSetRequest(value="must-not-write", target="lifecycle"),
                db=manager,
            )
        assert set_error.value.status_code == 503

        shared_pool = manager.credential_shared_pool()
        assert (
            await shared_pool.fetchval(
                "SELECT secret_value FROM public.butler_secrets WHERE secret_key = $1",
                insert_key,
            )
            is None
        )

        with pytest.raises(HTTPException) as delete_error:
            await delete_system_credential(delete_key, target="lifecycle", db=manager)
        assert delete_error.value.status_code == 503
        assert (
            await shared_pool.fetchval(
                "SELECT secret_value FROM public.butler_secrets WHERE secret_key = $1",
                delete_key,
            )
            == "must-survive"
        )
    finally:
        await manager.close()


async def test_post_boot_memory_shadow_is_degraded_for_stats_and_catalog(
    migrated_db_url: str,
) -> None:
    """Local memory loss must not make a same-named public table look healthy."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    fact_id = uuid4()
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        pool = manager.pool("lifecycle")
        await _shadow_fact_in_public(pool, fact_id)
        await pool.execute("DROP TABLE lifecycle.facts")

        response = await get_memory_stats(db=manager)
        meta = response.meta.model_dump()

        assert response.data.total_facts == 0
        assert meta["pools_failed"] == ["lifecycle"]
        assert meta["catalog_pools_failed"] == ["lifecycle"]
    finally:
        await manager.close()


async def test_post_boot_memory_shadow_cannot_confirm_public_fact(
    migrated_db_url: str,
) -> None:
    """A dashboard confirmation must never mutate public.facts after local loss."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    fact_id = uuid4()
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        pool = manager.pool("lifecycle")
        await _shadow_fact_in_public(pool, fact_id)
        await pool.execute("DROP TABLE lifecycle.facts")

        with pytest.raises(HTTPException) as error:
            await confirm_fact(str(fact_id), db=manager)

        assert error.value.status_code == 503
        assert (
            await pool.fetchval(
                "SELECT last_confirmed_at IS NULL FROM public.facts WHERE id = $1",
                fact_id,
            )
            is True
        )
    finally:
        await manager.close()


async def test_post_boot_memory_shadow_cannot_retract_public_fact(
    migrated_db_url: str,
) -> None:
    """A dashboard retraction must never mutate public.facts after local loss."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    fact_id = uuid4()
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        pool = manager.pool("lifecycle")
        await _shadow_fact_in_public(pool, fact_id)
        await pool.execute("DROP TABLE lifecycle.facts")

        with pytest.raises(HTTPException) as error:
            await retract_fact(str(fact_id), db=manager)

        assert error.value.status_code == 503
        assert (
            await pool.fetchval("SELECT validity FROM public.facts WHERE id = $1", fact_id)
            == "active"
        )
    finally:
        await manager.close()


async def test_post_boot_memory_shadow_cannot_report_reembed_pending(
    migrated_db_url: str,
) -> None:
    """A private facts drop cannot become a public stale-embedding count."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    fact_id = uuid4()
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        pool = manager.pool("lifecycle")
        await _shadow_fact_in_public(pool, fact_id, stale_embedding=True)
        await pool.execute("DROP TABLE lifecycle.facts")

        with pytest.raises(HTTPException) as error:
            await get_reembed_pending(
                butler="lifecycle",
                current_model="new-model",
                db=manager,
            )

        assert error.value.status_code == 503
        assert (
            await pool.fetchval(
                "SELECT embedding_model_version FROM public.facts WHERE id = $1",
                fact_id,
            )
            == "old-model"
        )
    finally:
        await manager.close()


async def test_post_boot_memory_shadow_cannot_reembed_public_fact(
    migrated_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-embedding run must fail closed before it can update public.facts."""
    from butlers.modules.memory import tools as memory_tools

    class _Engine:
        model_name = "new-model"

        @staticmethod
        def embed_batch(texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _ in texts]

    monkeypatch.setattr(memory_tools, "get_embedding_engine", lambda _: _Engine())
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    fact_id = uuid4()
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        pool = manager.pool("lifecycle")
        await _shadow_fact_in_public(pool, fact_id, stale_embedding=True)
        await pool.execute("DROP TABLE lifecycle.facts")

        with pytest.raises(HTTPException) as error:
            await run_reembed(
                ReembedRunRequest(
                    butler="lifecycle",
                    current_model="new-model",
                    dry_run=False,
                    tiers=["facts"],
                    batch_size=1,
                ),
                db=manager,
            )

        assert error.value.status_code == 503
        assert (
            await pool.fetchval(
                "SELECT embedding_model_version FROM public.facts WHERE id = $1",
                fact_id,
            )
            == "old-model"
        )
    finally:
        await manager.close()


async def test_post_boot_shared_public_loss_blocks_cli_rotation(
    migrated_db_url: str,
) -> None:
    """CLI rotation must surface a dropped shared credential table as unavailable."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    try:
        shared_pool = manager.credential_shared_pool()
        await manager.snapshot_relation_presence(
            "shared-public",
            ("butler_secrets",),
            pool=shared_pool,
        )
        await shared_pool.execute(
            """
            INSERT INTO public.butler_secrets (secret_key, secret_value, category, updated_at)
            VALUES ('CLI_ROTATE_AFTER_DROP', 'old-value', 'cli', now())
            """
        )
        await shared_pool.execute("DROP TABLE public.butler_secrets")

        with pytest.raises(HTTPException) as error:
            await rotate_cli_credential("CLI_ROTATE_AFTER_DROP", db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_post_boot_shared_public_loss_blocks_cli_revocation(
    migrated_db_url: str,
) -> None:
    """CLI revocation must surface a dropped shared credential table as unavailable."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    try:
        shared_pool = manager.credential_shared_pool()
        await manager.snapshot_relation_presence(
            "shared-public",
            ("butler_secrets",),
            pool=shared_pool,
        )
        await shared_pool.execute(
            """
            INSERT INTO public.butler_secrets (secret_key, secret_value, category, updated_at)
            VALUES ('CLI_REVOKE_AFTER_DROP', 'old-value', 'cli', now())
            """
        )
        await shared_pool.execute("DROP TABLE public.butler_secrets")

        with pytest.raises(HTTPException) as error:
            await revoke_cli_credential("CLI_REVOKE_AFTER_DROP", db=manager)

        assert error.value.status_code == 503
    finally:
        await manager.close()


async def test_absent_at_boot_shared_public_cli_source_remains_not_found(
    migrated_db_url: str,
) -> None:
    """A shared CLI table deliberately absent at startup still follows 404 semantics."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
        with_shared_pool=True,
    )
    try:
        shared_pool = manager.credential_shared_pool()
        await shared_pool.execute("DROP TABLE public.butler_secrets")
        await manager.snapshot_relation_presence(
            "shared-public",
            ("butler_secrets",),
            pool=shared_pool,
        )
        assert manager.relation_observed_since_start("shared-public", "butler_secrets") is False

        with pytest.raises(HTTPException) as rotate_error:
            await rotate_cli_credential("CLI_ABSENT_AT_BOOT", db=manager)
        assert rotate_error.value.status_code == 404

        with pytest.raises(HTTPException) as revoke_error:
            await revoke_cli_credential("CLI_ABSENT_AT_BOOT", db=manager)
        assert revoke_error.value.status_code == 404
    finally:
        await manager.close()
