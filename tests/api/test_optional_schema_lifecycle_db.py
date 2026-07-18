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
_TRACKED_RELATIONS = ("butler_secrets", "episodes", "facts", "rules")


@pytest.fixture
def migrated_db_url(postgres_container) -> str:
    """Provision one schema with the real core and memory tables."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
        schemas={"core": "lifecycle", "memory": "lifecycle"},
    )


async def _manager_for_schema(
    db_url: str,
    *,
    butler_name: str,
    schema: str,
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
    await manager.add_butler(
        butler_name,
        db_name=parsed.path.lstrip("/"),
        db_schema=schema,
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
