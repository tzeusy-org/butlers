"""Native historical cache invalidation/replay/atomic-day proof for PR5471."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration


def _migrated_chronicler_db(postgres_container, revision: str) -> str:
    """Prepare the fixed private memory prerequisite before Chronicler's 030 head."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "chronicler"],
        schemas={"core": "chronicler_mem", "memory": "chronicler_mem", "chronicler": "chronicler"},
        revisions={"chronicler": revision},
    )


def test_chronicler_030_refuses_missing_private_facts_before_schema_changes(postgres_container):
    """A missing private canonical store is not evidence of an empty projection."""
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
    with pytest.raises(
        Exception, match="required canonical storage chronicler_mem.facts is missing"
    ):
        command.upgrade(config, "chronicler@chronicler_030")

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regclass('chronicler.daily_repair_control') IS NULL")
            ).scalar_one()
            assert (  # pinned-revision: selected 029-to-030 prerequisite/refusal and migration-order boundary, not the evolving chain head
                conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).scalar_one()
                == "chronicler_029"
            )
    finally:
        engine.dispose()

    async def check_absent_catalog_is_best_effort() -> None:
        from butlers.modules.memory.search import CatalogReadPolicy
        from butlers.modules.memory.tools.context import _fetch_fleet_knowledge

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
        )
        try:
            assert (
                await pool.fetchval("SELECT pg_catalog.to_regclass('public.memory_catalog')")
                is None
            )
            result = await _fetch_fleet_knowledge(
                pool,
                SimpleNamespace(embed=lambda _query: [0.0] * 384),
                "prompt",
                "general",
                "shared",
                CatalogReadPolicy("normal", ("normal",)),
            )
            assert result == []
        finally:
            await pool.close()

    asyncio.run(check_absent_catalog_is_best_effort())


def test_fleet_context_catalog_probe_preserves_guarded_errors_and_disappearance(
    postgres_container, monkeypatch
):
    """Present catalogs are read, while guarded errors and post-probe disappearance fail closed."""
    from urllib.parse import urlparse

    from butlers.modules.memory.search import CatalogReadPolicy
    from butlers.modules.memory.tools import context as context_impl
    from butlers.modules.memory.tools.context import _fetch_fleet_knowledge

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    source_id = uuid4()
    catalog_id = None
    engine = create_engine(url)
    admin_url = (
        urlparse(postgres_container.get_connection_url())._replace(path=urlparse(url).path).geturl()
    )
    admin_engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            catalog_id = conn.execute(
                text("""INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, source_butler, tenant_id,
                     summary, search_vector, memory_type, title, sensitivity, embedding)
                    VALUES ('foreign_memory', 'facts', :source_id, 'other', 'shared',
                            'bounded native context probe',
                            to_tsvector('english', 'bounded native context probe'),
                            'fact', 'bounded native context probe', 'normal',
                            (ARRAY[1::real, 0::real] || array_fill(0::real, ARRAY[382]))::vector(384))
                    RETURNING id"""),
                {"source_id": source_id},
            ).scalar_one()
            helper_owner = conn.execute(
                text("""SELECT pg_catalog.pg_get_userbyid(proowner)
                    FROM pg_catalog.pg_proc
                    WHERE oid='public.chronicler_catalog_row_visible(uuid)'::pg_catalog.regprocedure""")
            ).scalar_one()
            assert helper_owner == "chronicler_memory_owner"
            helper_acl_query = text("""SELECT
                    pg_catalog.pg_get_userbyid(acl.grantor) AS grantor,
                    COALESCE(pg_catalog.pg_get_userbyid(acl.grantee), 'PUBLIC') AS grantee,
                    acl.privilege_type,
                    acl.is_grantable
                FROM pg_catalog.pg_proc AS procedure_row
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        procedure_row.proacl,
                        pg_catalog.acldefault('f', procedure_row.proowner)
                    )
                ) AS acl
                WHERE procedure_row.oid =
                    'public.chronicler_catalog_row_visible(uuid)'::pg_catalog.regprocedure
                ORDER BY 1, 2, 3, 4""")
            original_helper_acl = tuple(tuple(row) for row in conn.execute(helper_acl_query).all())

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler_mem, public"},
                init=runtime_connection,
            )
            revoked = False
            try:
                policy = CatalogReadPolicy("normal", ("normal",))
                engine = SimpleNamespace(embed=lambda _query: [1.0, *([0.0] * 383)])
                healthy = await _fetch_fleet_knowledge(
                    pool, engine, "bounded native context probe", "general", "shared", policy
                )
                assert any(row["id"] == catalog_id for row in healthy)

                with admin_engine.begin() as admin:
                    admin.exec_driver_sql('SET LOCAL ROLE "chronicler_memory_owner"')
                    admin.exec_driver_sql(
                        "REVOKE EXECUTE ON FUNCTION public.chronicler_catalog_row_visible(uuid) "
                        "FROM butler_chronicler_rw"
                    )
                revoked = True
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await _fetch_fleet_knowledge(
                        pool, engine, "bounded native context probe", "general", "shared", policy
                    )
                with admin_engine.begin() as admin:
                    admin.exec_driver_sql('SET LOCAL ROLE "chronicler_memory_owner"')
                    admin.exec_driver_sql(
                        "GRANT EXECUTE ON FUNCTION public.chronicler_catalog_row_visible(uuid) "
                        "TO butler_chronicler_rw"
                    )
                revoked = False

                original_search = context_impl._search.search_catalog
                guarded_search_started = False

                async def _rename_between_probe_and_search(*args, **kwargs):
                    nonlocal guarded_search_started
                    guarded_search_started = True
                    with admin_engine.begin() as admin:
                        admin.exec_driver_sql(
                            "ALTER TABLE public.memory_catalog "
                            "RENAME TO memory_catalog_f2_disappeared"
                        )
                    try:
                        assert (
                            await pool.fetchval(
                                "SELECT pg_catalog.to_regclass('public.memory_catalog')"
                            )
                            is None
                        )
                        return await original_search(*args, **kwargs)
                    finally:
                        with admin_engine.begin() as admin:
                            admin.exec_driver_sql(
                                "ALTER TABLE public.memory_catalog_f2_disappeared "
                                "RENAME TO memory_catalog"
                            )

                with monkeypatch.context() as patch:
                    patch.setattr(
                        context_impl._search, "search_catalog", _rename_between_probe_and_search
                    )
                    with pytest.raises(asyncpg.UndefinedTableError):
                        await _fetch_fleet_knowledge(
                            pool,
                            engine,
                            "bounded native context probe",
                            "general",
                            "shared",
                            policy,
                        )
                assert guarded_search_started is True
                restored = await _fetch_fleet_knowledge(
                    pool, engine, "bounded native context probe", "general", "shared", policy
                )
                assert any(row["id"] == catalog_id for row in restored)
            finally:
                if revoked:
                    with admin_engine.begin() as admin:
                        admin.exec_driver_sql('SET LOCAL ROLE "chronicler_memory_owner"')
                        admin.exec_driver_sql(
                            "GRANT EXECUTE ON FUNCTION public.chronicler_catalog_row_visible(uuid) "
                            "TO butler_chronicler_rw"
                        )
                await pool.close()

        asyncio.run(scenario())
        with engine.connect() as conn:
            assert (
                tuple(tuple(row) for row in conn.execute(helper_acl_query).all())
                == original_helper_acl
            )
            assert conn.execute(
                text(
                    "SELECT pg_catalog.has_function_privilege('butler_chronicler_rw', "
                    "'public.chronicler_catalog_row_visible(uuid)', 'EXECUTE')"
                )
            ).scalar_one()
    finally:
        engine.dispose()
        admin_engine.dispose()


@pytest.mark.parametrize(
    ("privilege_name", "revoke_sql", "grant_sql", "probe_sql"),
    [
        (
            "memory_catalog UPDATE",
            "REVOKE UPDATE ON TABLE public.memory_catalog FROM {role}",
            "GRANT UPDATE ON TABLE public.memory_catalog TO {role}",
            "SELECT has_table_privilege(current_user, 'public.memory_catalog', 'UPDATE')",
        ),
        (
            "private memory schema USAGE",
            "REVOKE USAGE ON SCHEMA chronicler_mem FROM {role}",
            "GRANT USAGE ON SCHEMA chronicler_mem TO {role}",
            "SELECT has_schema_privilege(current_user, 'chronicler_mem', 'USAGE')",
        ),
        (
            "private facts SELECT",
            "REVOKE SELECT ON TABLE chronicler_mem.facts FROM {role}",
            "GRANT SELECT ON TABLE chronicler_mem.facts TO {role}",
            "SELECT has_table_privilege(current_user, 'chronicler_mem.facts', 'SELECT')",
        ),
        (
            "private facts UPDATE",
            "REVOKE UPDATE ON TABLE chronicler_mem.facts FROM {role}",
            "GRANT UPDATE ON TABLE chronicler_mem.facts TO {role}",
            "SELECT has_table_privilege(current_user, 'chronicler_mem.facts', 'UPDATE')",
        ),
        (
            "memory_catalog SELECT",
            "REVOKE SELECT ON TABLE public.memory_catalog FROM {role}",
            "GRANT SELECT ON TABLE public.memory_catalog TO {role}",
            "SELECT has_table_privilege(current_user, 'public.memory_catalog', 'SELECT')",
        ),
        (
            "entity_graph_edges SELECT",
            "REVOKE SELECT ON TABLE public.entity_graph_edges FROM {role}",
            "GRANT SELECT ON TABLE public.entity_graph_edges TO {role}",
            "SELECT has_table_privilege(current_user, 'public.entity_graph_edges', 'SELECT')",
        ),
        (
            "entity_graph_edges DELETE",
            "REVOKE DELETE ON TABLE public.entity_graph_edges FROM {role}",
            "GRANT DELETE ON TABLE public.entity_graph_edges TO {role}",
            "SELECT has_table_privilege(current_user, 'public.entity_graph_edges', 'DELETE')",
        ),
        (
            "projection invalidator EXECUTE",
            "REVOKE EXECUTE ON FUNCTION public.invalidate_memory_projection"
            "(text,text,uuid[],timestamptz,boolean,boolean) FROM {role}",
            "GRANT EXECUTE ON FUNCTION public.invalidate_memory_projection"
            "(text,text,uuid[],timestamptz,boolean,boolean) TO {role}",
            "SELECT has_function_privilege(current_user, "
            "'public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)', "
            "'EXECUTE')",
        ),
        (
            "public schema USAGE",
            "REVOKE USAGE ON SCHEMA public FROM {role}",
            "GRANT USAGE ON SCHEMA public TO {role}",
            "SELECT has_schema_privilege(current_user, 'public', 'USAGE')",
        ),
    ],
    ids=[
        "catalog_update",
        "private_schema_usage",
        "private_facts_select",
        "private_facts_update",
        "catalog_select",
        "graph_select",
        "graph_delete",
        "invalidator_execute",
        "public_schema_usage",
    ],
)
def test_chronicler_030_requires_effective_runtime_projection_privileges_before_schema_changes(
    postgres_container, privilege_name, revoke_sql, grant_sql, probe_sql
):
    """Empty projections do not waive effective role grants or public schema USAGE."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_029")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            assert (
                conn.execute(text("SELECT count(*) FROM public.memory_catalog")).scalar_one() == 0
            )
            assert (
                conn.execute(text("SELECT count(*) FROM public.entity_graph_edges")).scalar_one()
                == 0
            )
            role_names = (
                conn.execute(
                    text("""WITH RECURSIVE role_tree(role_oid) AS (
                    SELECT oid FROM pg_catalog.pg_roles WHERE rolname='butler_chronicler_rw'
                    UNION
                    SELECT membership.roleid FROM pg_catalog.pg_auth_members AS membership
                    JOIN role_tree AS parent ON membership.member=parent.role_oid
                )
                SELECT pg_catalog.quote_ident(role.rolname)
                FROM role_tree JOIN pg_catalog.pg_roles AS role ON role.oid=role_tree.role_oid""")
                )
                .scalars()
                .all()
            )
            for role in role_names:
                conn.exec_driver_sql(revoke_sql.format(role=role))
            conn.exec_driver_sql(revoke_sql.format(role="PUBLIC"))

        with engine.connect() as conn:
            conn.exec_driver_sql("SET ROLE butler_chronicler_rw")
            assert not conn.execute(text(probe_sql)).scalar_one(), privilege_name

        config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
        if privilege_name == "public schema USAGE":
            # The ordinary migration runner can fail earlier while establishing
            # its configured role/search path. Exercise the unchanged 030 body
            # directly so its own effective-privilege preflight is proved too.
            migration = ScriptDirectory.from_config(config).get_revision("chronicler_030")
            assert migration is not None
            with pytest.raises(
                Exception,
                match="Chronicler runtime memory projection privileges are incomplete",
            ):
                with engine.begin() as conn:
                    context = MigrationContext.configure(conn)
                    with Operations.context(context):
                        migration.module.upgrade()
        else:
            with pytest.raises(
                Exception,
                match="Chronicler runtime memory projection privileges are incomplete",
            ):
                command.upgrade(config, "chronicler@chronicler_030")
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regclass('chronicler.daily_repair_control') IS NULL")
            ).scalar_one()
            assert (  # pinned-revision: selected 029-to-030 prerequisite/refusal and migration-order boundary, not the evolving chain head
                conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).scalar_one()
                == "chronicler_029"
            )

        # Restore just the named required grant. The same migration then passes
        # and effective SET ROLE readback proves the repaired contract.
        with engine.begin() as conn:
            conn.exec_driver_sql(grant_sql.format(role="butler_chronicler_rw"))
        command.upgrade(config, "chronicler@chronicler_030")
        with engine.connect() as conn:
            conn.exec_driver_sql("SET ROLE butler_chronicler_rw")
            assert conn.execute(text(probe_sql)).scalar_one(), privilege_name
    finally:
        engine.dispose()


def test_old_spotify_cache_repair_preserves_steam_unrelated_prose_and_health(postgres_container):
    """A >7-day cache must converge without rewriting historic feeder health."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_029")
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    spotify = now - timedelta(days=10)
    steam = now - timedelta(days=12)
    unrelated = now - timedelta(days=20)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for source in (
                "spotify.session_summary",
                "steam.play_history",
                "chronicler.situation_inferred",
            ):
                conn.execute(
                    text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility) VALUES (:s, 'supported')"""),
                    {"s": source},
                )
            for source, kind, stamp in (
                ("spotify.session_summary", "listening_episode", spotify),
                ("steam.play_history", "play_episode", steam),
            ):
                conn.execute(
                    text("""INSERT INTO chronicler.episodes
                    (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                    VALUES (:s, :s, :k, :a, :b, 'activity', 'private-source-title')"""),
                    {"s": source, "k": kind, "a": stamp, "b": stamp + timedelta(hours=1)},
                )
            for stamp in (spotify, steam, unrelated):
                conn.execute(
                    text("""INSERT INTO chronicler.daily_rollups
                    (local_date, lane, seconds, episode_count, timezone, narrative)
                    VALUES (:d, 'play', 3600, 1, 'Asia/Singapore', :n)"""),
                    {
                        "d": stamp.date(),
                        "n": "private-source-title" if stamp == spotify else "original prose",
                    },
                )
            for kind in ("feeder_dark", "owner_annotation", "lane_share_outlier"):
                conn.execute(
                    text("""INSERT INTO chronicler.daily_rollup_flags
                    (local_date, flag_type, severity, detail, narrative)
                    VALUES (:d, :k, 'info', CAST(:detail AS jsonb), 'keep flag text')"""),
                    {"d": spotify.date(), "k": kind, "detail": '{"historical": true}'},
                )
        config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
        command.upgrade(config, "chronicler@chronicler_030")

        async def scenario():
            from butlers.chronicler.adapters import situation as mod
            from butlers.chronicler.history_repair import rebuild_day, run_rebuild, try_lock

            async def runtime_connection(conn):
                await register_jsonb_codec(conn)
                await conn.execute("SET ROLE butler_chronicler_rw")

            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public"},
                init=runtime_connection,
            )
            try:
                assert await pool.fetchval(
                    "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1",
                    spotify.date(),
                )
                assert (await run_rebuild(pool, now=now))["status"] == "waiting_replay"
                # A failed checkpoint publication must not acknowledge the epoch.
                from butlers.chronicler import history_repair as repair
                from butlers.chronicler.adapters.base import AdapterResult

                checkpoint_before = await pool.fetchrow(
                    "SELECT * FROM projection_checkpoints WHERE source_name=$1",
                    mod.SOURCE_NAME,
                )
                real_checkpoint = repair.upsert_checkpoint

                async def crash_after_checkpoint(conn, *args, **kwargs):
                    await real_checkpoint(conn, *args, **kwargs)
                    raise RuntimeError("checkpoint crash")

                epoch = await repair.replay_epoch(pool)
                async with pool.acquire() as conn:
                    with patch.object(repair, "upsert_checkpoint", crash_after_checkpoint):
                        with pytest.raises(RuntimeError, match="checkpoint crash"):
                            await repair.acknowledge_replay(
                                conn,
                                epoch=epoch,
                                result=AdapterResult(source_name=mod.SOURCE_NAME, watermark=now),
                            )
                assert (
                    await pool.fetchrow(
                        "SELECT * FROM projection_checkpoints WHERE source_name=$1",
                        mod.SOURCE_NAME,
                    )
                    == checkpoint_before
                )
                assert await repair.replay_epoch(pool) == epoch
                with (
                    patch.object(mod, "_now", return_value=now),
                    patch.object(mod, "load_anchors", AsyncMock(return_value=())),
                    patch.object(mod, "resolve_owner_entity_id", AsyncMock(return_value=None)),
                    patch.object(
                        mod.SituationInferredAdapter, "_tail_hold", AsyncMock(return_value=None)
                    ),
                ):
                    result = await mod.SituationInferredAdapter(timezone="Asia/Singapore").run(
                        pool=pool, chronicler_pool=pool
                    )
                assert result.error is None
                assert await pool.fetchval(
                    "SELECT replay_ack=replay_epoch FROM daily_repair_control"
                )
                # A crash after totals/flags but before cursor commit rolls back ALL of them.
                async with pool.acquire() as conn:
                    before = await conn.fetchrow(
                        "SELECT * FROM daily_repair_days WHERE local_date=$1", spotify.date()
                    )
                    with pytest.raises(RuntimeError, match="crash"):
                        async with conn.transaction():
                            assert await try_lock(conn)
                            await rebuild_day(conn, before)
                            raise RuntimeError("crash")
                    assert (
                        await conn.fetchrow(
                            "SELECT * FROM daily_repair_days WHERE local_date=$1", spotify.date()
                        )
                        == before
                    )
                    assert (
                        await conn.fetchval(
                            "SELECT seconds FROM daily_rollups WHERE local_date=$1 AND lane='play'",
                            spotify.date(),
                        )
                        == 3600
                    )
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM daily_repair_archive WHERE local_date=$1",
                            spotify.date(),
                        )
                        == 0
                    )
                await run_rebuild(pool, now=now)
                for stamp, seconds, prose in (
                    (spotify, 0, None),
                    (steam, 3600, None),
                    (unrelated, 3600, "original prose"),
                ):
                    row = await pool.fetchrow(
                        "SELECT seconds, narrative FROM daily_rollups WHERE local_date=$1 AND lane='play'",
                        stamp.date(),
                    )
                    assert (row["seconds"], row["narrative"]) == (seconds, prose)
                flags = await pool.fetch(
                    "SELECT flag_type, narrative, detail FROM daily_rollup_flags WHERE local_date=$1",
                    spotify.date(),
                )
                assert {r["flag_type"] for r in flags} == {"feeder_dark", "owner_annotation"}
                assert all(
                    r["narrative"] == "keep flag text" and r["detail"] == {"historical": True}
                    for r in flags
                )
                # Archive text exists as a positive control, then a privacy
                # correction must redact every copy while retaining audit IDs.
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM daily_repair_archive WHERE content::text LIKE $1",
                        "%private-source-title%",
                    )
                    > 0
                )
                spotify_id = await pool.fetchval(
                    "SELECT id FROM episodes WHERE source_name='spotify.session_summary'"
                )
                await pool.execute(
                    "INSERT INTO overrides (target_kind, target_id, corrected_privacy) "
                    "VALUES ('episode', $1, 'restricted')",
                    spotify_id,
                )
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM daily_repair_archive WHERE $1=ANY(dependency_ids)",
                        spotify_id,
                    )
                    > 0
                )
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM daily_repair_archive "
                        "WHERE $1=ANY(dependency_ids) AND (content IS NOT NULL OR audit_hash IS NOT NULL)",
                        spotify_id,
                    )
                    == 0
                )
                # Timestamp-only replay must not dirty a completed old date.
                generation = await pool.fetchval(
                    "SELECT dirty_generation FROM daily_repair_days WHERE local_date=$1",
                    steam.date(),
                )
                await pool.execute(
                    "UPDATE episodes SET updated_at=now() WHERE source_name='steam.play_history'"
                )
                assert (
                    await pool.fetchval(
                        "SELECT dirty_generation FROM daily_repair_days WHERE local_date=$1",
                        steam.date(),
                    )
                    == generation
                )
                await pool.execute("""UPDATE episodes SET tombstone_at=now()
                    WHERE source_name='steam.play_history'""")
                assert await pool.fetchval(
                    "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1", steam.date()
                )
                # A never-materialized fresh day must still receive its first
                # ordinary flag evaluation, not historical-unknown suppression.
                from zoneinfo import ZoneInfo

                zone = ZoneInfo("Asia/Singapore")
                fresh = datetime.combine(
                    datetime.now(zone).date() - timedelta(days=1),
                    datetime.min.time(),
                    zone,
                ).astimezone(UTC) + timedelta(hours=1)
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM daily_rollups WHERE local_date=$1)",
                    fresh.date(),
                )
                await pool.execute(
                    "INSERT INTO episodes "
                    "(source_name, source_ref, episode_type, start_at, end_at, layer) "
                    "VALUES ('steam.play_history','fresh','play_episode',$1,$2,'evidence')",
                    fresh,
                    fresh + timedelta(hours=1),
                )
                assert not await pool.fetchval(
                    "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1",
                    fresh.date(),
                )
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


@pytest.mark.parametrize(("local_day", "hours"), [("2026-03-08", 23), ("2026-11-01", 25)])
def test_quantitative_repair_respects_dst_and_blocks_mixed_zones(
    postgres_container, local_day, hours
):
    from datetime import date
    from zoneinfo import ZoneInfo

    from butlers.chronicler.adapters.base import AdapterResult
    from butlers.chronicler.history_repair import acknowledge_replay, replay_epoch, run_rebuild
    from butlers.chronicler.rollups import _local_day_bounds_utc

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")

    async def scenario():
        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
            init=register_jsonb_codec,
        )
        day = date.fromisoformat(local_day)
        start, end = _local_day_bounds_utc(day, ZoneInfo("America/New_York"))
        try:
            await pool.execute("UPDATE daily_repair_control SET timezone='America/New_York'")
            await pool.execute("""INSERT INTO source_adapter_state
                (source_name, chronicler_compatibility)
                VALUES ('chronicler.situation_inferred', 'supported')""")
            await pool.execute(
                """INSERT INTO daily_rollups
                (local_date, lane, seconds, episode_count, timezone)
                VALUES ($1, 'play', 86400, 1, 'America/New_York')""",
                day,
            )
            await pool.execute(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer)
                VALUES ('chronicler.situation_inferred', 'dst', 'play_block', $1, $2, 'activity')""",
                start,
                end,
            )
            epoch = await replay_epoch(pool)
            async with pool.acquire() as conn:
                await acknowledge_replay(
                    conn,
                    epoch=epoch,
                    result=AdapterResult(
                        source_name="chronicler.situation_inferred", watermark=datetime.now(UTC)
                    ),
                )
            await pool.execute(
                """INSERT INTO daily_rollups
                (local_date, lane, seconds, episode_count, timezone)
                VALUES ($1, 'work', 123, 1, 'Asia/Singapore')""",
                day + timedelta(days=1),
            )
            assert (await run_rebuild(pool, timezone="America/New_York"))["status"] == "blocked"
            assert (
                await pool.fetchval(
                    "SELECT seconds FROM daily_rollups WHERE local_date=$1 AND lane='play'", day
                )
                == 86400
            )
            await pool.execute("DELETE FROM daily_rollups WHERE timezone='Asia/Singapore'")
            await run_rebuild(pool, timezone="America/New_York")
            assert (
                await pool.fetchval(
                    "SELECT seconds FROM daily_rollups WHERE local_date=$1 AND lane='play'", day
                )
                == hours * 3600
            )
        finally:
            await pool.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("change", "winning_override", "expected_seconds"),
    [
        ("payload", "timing", 3600),
        ("layer", "timing", 0),
        ("privacy", "timing", 0),
        ("move", "timing", 3600),
        ("delete", "timing", 0),
        ("payload", "title_only", 3600),
    ],
)
def test_base_edit_under_unchanged_winning_override_dirties_effective_day(
    postgres_container,
    change,
    winning_override,
    expected_seconds,
):
    """Raw A and effective B must share the source transaction's invalidation."""
    from butlers.chronicler.history_repair import rebuild_day, try_lock

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")

    async def scenario():
        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
        raw = now - timedelta(days=40)
        corrected = now - timedelta(days=20)
        stale = now - timedelta(days=60)
        moved = now - timedelta(days=35)
        effective = corrected if winning_override == "timing" else raw
        try:
            await pool.execute("""INSERT INTO source_adapter_state
                (source_name, chronicler_compatibility)
                VALUES ('steam.play_history', 'supported')""")
            for day in (raw.date(), corrected.date(), stale.date()):
                await pool.execute(
                    """INSERT INTO daily_rollups
                    (local_date, lane, seconds, episode_count, timezone)
                    VALUES ($1, 'play', 3600, 1, 'Asia/Singapore')""",
                    day,
                )
            episode_id = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer, payload)
                VALUES ('steam.play_history','overridden','play_episode',$1,$2,'activity',
                        '{"playtime_minutes":60}'::jsonb)
                RETURNING id""",
                raw,
                raw + timedelta(hours=1),
            )
            await pool.execute(
                """INSERT INTO overrides
                (target_kind, target_id, corrected_start_at, corrected_end_at, created_at)
                VALUES ('episode',$1,$2,$3,$4)""",
                episode_id,
                stale,
                stale + timedelta(hours=1),
                now - timedelta(hours=2),
            )
            if winning_override == "timing":
                await pool.execute(
                    """INSERT INTO overrides
                    (target_kind, target_id, corrected_start_at, corrected_end_at, created_at)
                    VALUES ('episode',$1,$2,$3,$4)""",
                    episode_id,
                    corrected,
                    corrected + timedelta(hours=1),
                    now - timedelta(hours=1),
                )
            else:
                # Latest is a row, not a per-field last-non-NULL merge: this
                # title-only override must NOT inherit the stale timing on C.
                await pool.execute(
                    """INSERT INTO overrides
                    (target_kind, target_id, corrected_title, created_at)
                    VALUES ('episode',$1,'winning title',$2)""",
                    episode_id,
                    now - timedelta(hours=1),
                )
            assert (
                await pool.fetchval(
                    "SELECT start_at FROM v_episodes_corrected WHERE id=$1", episode_id
                )
                == effective
            )
            async with pool.acquire() as conn:
                async with conn.transaction():
                    assert await try_lock(conn)
                    state = await conn.fetchrow(
                        "SELECT * FROM daily_repair_days WHERE local_date=$1 FOR UPDATE",
                        effective.date(),
                    )
                    await rebuild_day(conn, state)
            assert (
                await pool.fetchval(
                    "SELECT seconds FROM daily_rollups WHERE local_date=$1 AND lane='play'",
                    effective.date(),
                )
                == 3600
            )
            await pool.execute("""UPDATE daily_repair_days
                SET totals_pending=FALSE,outlier_pending=FALSE,facts_pending=FALSE,
                    built_generation=dirty_generation""")
            generation = await pool.fetchval(
                "SELECT dirty_generation FROM daily_repair_days WHERE local_date=$1",
                effective.date(),
            )
            await pool.execute(
                """UPDATE episodes
                SET payload=payload,layer=layer,privacy=privacy,updated_at=now() WHERE id=$1""",
                episode_id,
            )
            assert (
                await pool.fetchval(
                    "SELECT dirty_generation FROM daily_repair_days WHERE local_date=$1",
                    effective.date(),
                )
                == generation
            )
            mutations = {
                "payload": "UPDATE episodes SET payload=payload || '{\"playtime_minutes\":30}'::jsonb WHERE id=$1",
                "layer": "UPDATE episodes SET layer='evidence' WHERE id=$1",
                "privacy": "UPDATE episodes SET privacy='restricted' WHERE id=$1",
                "move": "UPDATE episodes SET start_at=$2,end_at=$3 WHERE id=$1",
                "delete": "DELETE FROM episodes WHERE id=$1",
            }
            args = (
                (episode_id, moved, moved + timedelta(hours=1))
                if change == "move"
                else (episode_id,)
            )
            # The base mutation and effective dirty date must roll back together.
            async with pool.acquire() as conn:
                with pytest.raises(RuntimeError, match="source crash"):
                    async with conn.transaction():
                        await conn.execute(mutations[change], *args)
                        assert await conn.fetchval(
                            "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1",
                            effective.date(),
                        )
                        raise RuntimeError("source crash")
            assert not await pool.fetchval(
                "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1", effective.date()
            )
            await pool.execute(mutations[change], *args)
            assert await pool.fetchval(
                "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1", effective.date()
            )
            assert await pool.fetchval(
                "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1", raw.date()
            )
            assert not await pool.fetchval(
                "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1", stale.date()
            )
            if change == "move":
                assert await pool.fetchval(
                    "SELECT totals_pending FROM daily_repair_days WHERE local_date=$1", moved.date()
                )
            async with pool.acquire() as conn:
                async with conn.transaction():
                    assert await try_lock(conn)
                    state = await conn.fetchrow(
                        "SELECT * FROM daily_repair_days WHERE local_date=$1 FOR UPDATE",
                        effective.date(),
                    )
                    await rebuild_day(conn, state)
            assert (
                await pool.fetchval(
                    "SELECT seconds FROM daily_rollups WHERE local_date=$1 AND lane='play'",
                    effective.date(),
                )
                == expected_seconds
            )
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_source_privacy_disposes_generated_fact_and_retries_redacted_projection_cleanup(
    postgres_container,
):
    """Privacy cleanup is synchronous, projection-wide, and idempotent by owned inventory."""
    from zoneinfo import ZoneInfo

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    now = datetime(2026, 7, 15, 12, tzinfo=UTC)
    # The entire D..D28 generated-fact horizon must be closed at fixed now,
    # even when enqueue admission advances its local cutoff to database now().
    day = (now - timedelta(days=29)).date()
    at = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=12)
    episode_id = uuid4()
    fact_id = uuid4()
    missing_fact_id = uuid4()
    unrelated_id = uuid4()
    subject_id = uuid4()
    object_id = uuid4()
    try:
        with engine.begin() as conn:
            # Pin migration-initialized repair admission to the same instant as
            # the explicit rebuild driver before source triggers enqueue dates.
            conn.execute(
                text(
                    "UPDATE chronicler.daily_repair_control SET closed_through=:day WHERE singleton"
                ),
                {"day": now.astimezone(ZoneInfo("Asia/Singapore")).date() - timedelta(days=1)},
            )
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, name in ((subject_id, "f1-test-subject"), (object_id, "f1-test-object")):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, :name, 'other')"
                    ),
                    {"id": entity_id, "name": name},
                )
            conn.execute(
                text("""INSERT INTO chronicler.episodes
                    (id, source_name, source_ref, episode_type, start_at, end_at, layer, title)
                    VALUES (:id, 'steam.play_history', 'f1-privacy', 'play_episode',
                            :start_at, :end_at, 'evidence', 'private source title')"""),
                {"id": episode_id, "start_at": at, "end_at": at + timedelta(hours=1)},
            )
            for generated_fact_id, lane in ((fact_id, "work"), (missing_fact_id, "play")):
                conn.execute(
                    text("""INSERT INTO chronicler_mem.facts
                        (id, subject, predicate, content, confidence, source_butler,
                         content_authority, validity, tags, metadata)
                        VALUES (:id, :subject, 'lane-skew', 'private generated insight', 0.9,
                                'chronicler', 'system', 'active',
                                CAST(:tags AS jsonb), CAST(:metadata AS jsonb))"""),
                    {
                        "id": generated_fact_id,
                        "subject": f"{lane} lane",
                        "tags": f'["chronicler-insight", "lane-skew", "{lane}"]',
                        "metadata": (
                            '{"source":"chronicler","local_date":"'
                            + day.isoformat()
                            + f'","lane":"{lane}","direction":"down","seconds":1,'
                            '"baseline_seconds":2,"delta_seconds":-1,"baseline_sample_days":14}'
                        ),
                    },
                )
            # Core 315 only permits the Chronicler runtime to mint exact private-fact
            # projection identities. Seed these copies under the same role as production.
            conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
            for generated_fact_id in (fact_id, missing_fact_id):
                conn.execute(
                    text("""INSERT INTO public.memory_catalog
                        (source_schema, source_table, source_id, summary, title, confidence)
                        VALUES ('chronicler_mem', 'facts', :id, 'private catalog summary',
                                'private title', 0.9)"""),
                    {"id": generated_fact_id},
                )
                conn.execute(
                    text("""INSERT INTO public.entity_graph_edges
                        (source_schema, source_table, source_id, subject_entity_id,
                         predicate, object_entity_id)
                        VALUES ('chronicler_mem', 'facts', :id, :subject, 'related_to', :object)"""),
                    {"id": generated_fact_id, "subject": subject_id, "object": object_id},
                )
            conn.execute(
                text("""INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, summary, title, confidence)
                    VALUES ('chronicler_mem', 'facts', :id, 'unrelated survivor',
                            'unrelated title', 0.9)"""),
                {"id": unrelated_id},
            )
            conn.execute(
                text("""INSERT INTO public.entity_graph_edges
                    (source_schema, source_table, source_id, subject_entity_id,
                     predicate, object_entity_id)
                    VALUES ('chronicler_mem', 'facts', :id, :subject, 'related_to', :object)"""),
                {"id": unrelated_id, "subject": subject_id, "object": object_id},
            )
            conn.execute(text("RESET ROLE"))
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES (:day, 1, 'test', 'privacy-copy', 'private-hash',
                            CAST('{"title":"private source title"}' AS jsonb),
                            ARRAY[CAST(:episode_id AS uuid)], 'audit_only')"""),
                {"day": day, "episode_id": episode_id},
            )

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public"},
                init=runtime_connection,
            )
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE episodes SET privacy='restricted' WHERE id=$1", episode_id
                        )
                fact = await pool.fetchrow(
                    "SELECT subject, content, embedding, search_vector, confidence, validity, "
                    "invalid_at, tags, metadata FROM chronicler_mem.facts WHERE id=$1",
                    fact_id,
                )
                assert fact["subject"] == "chronicler redacted fact"
                assert fact["content"] == "[removed by source privacy correction]"
                assert fact["embedding"] is None and fact["search_vector"] is None
                assert fact["confidence"] == 0 and fact["validity"] == "retracted"
                assert fact["invalid_at"] is not None and fact["tags"] == []
                assert fact["metadata"]["redacted"] is True
                catalog = await pool.fetchrow(
                    "SELECT summary, title, embedding, search_vector, confidence, invalid_at "
                    "FROM public.memory_catalog WHERE source_schema='chronicler_mem' "
                    "AND source_table='facts' AND source_id=$1",
                    fact_id,
                )
                assert catalog["summary"] == "" and catalog["title"] is None
                assert catalog["embedding"] is None and catalog["search_vector"] is None
                assert catalog["confidence"] == 0 and catalog["invalid_at"] is not None
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM public.entity_graph_edges "
                        "WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1",
                        fact_id,
                    )
                    == 0
                )
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM chronicler.daily_repair_archive "
                        "WHERE $1=ANY(dependency_ids) AND (content IS NOT NULL OR audit_hash IS NOT NULL)",
                        episode_id,
                    )
                    == 0
                )
                disposition = await pool.fetchrow(
                    "SELECT disposition, redacted FROM chronicler.daily_repair_fact_dispositions "
                    "WHERE local_date=$1 AND fact_id=$2 ORDER BY generation DESC LIMIT 1",
                    day,
                    fact_id,
                )
                assert disposition == ("disposed", True)

                # The source becomes ordinary again, but erasure is sticky. An
                # old writer may have reintroduced canonical and projected text;
                # inventory identity, not a mutable metadata flag, must repair it.
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE chronicler.episodes SET privacy='normal' WHERE id=:id"),
                        {"id": episode_id},
                    )
                    conn.execute(
                        text("""UPDATE chronicler_mem.facts SET
                            subject='work lane', content='recreated private insight',
                            embedding=array_fill(0::real, ARRAY[384])::vector(384),
                            search_vector=to_tsvector('simple', 'recreated private insight'),
                            confidence=0.9, validity='active', invalid_at=NULL,
                            tags=CAST(:tags AS jsonb), metadata=CAST(:metadata AS jsonb)
                            WHERE id=:id"""),
                        {
                            "id": fact_id,
                            "tags": "[]",
                            "metadata": '{"source":"chronicler"}',
                        },
                    )
                    conn.execute(
                        text("""UPDATE public.memory_catalog SET summary='stale private copy',
                            title='stale title', embedding=array_fill(0::real, ARRAY[384])::vector(384),
                            search_vector=to_tsvector('simple', 'stale private copy'),
                            confidence=0.9, invalid_at=NULL WHERE source_id=:id"""),
                        {"id": fact_id},
                    )
                    conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
                    conn.execute(
                        text("""INSERT INTO public.entity_graph_edges
                            (source_schema, source_table, source_id, subject_entity_id,
                             predicate, object_entity_id)
                            VALUES ('chronicler_mem', 'facts', :id, :subject,
                                    'related_to', :object)"""),
                        {"id": fact_id, "subject": subject_id, "object": object_id},
                    )
                    conn.execute(text("RESET ROLE"))
                    conn.execute(
                        text("DELETE FROM chronicler_mem.facts WHERE id=:id"),
                        {"id": missing_fact_id},
                    )
                    conn.execute(
                        text("""UPDATE public.memory_catalog SET summary='stale missing copy',
                            title='stale missing title',
                            embedding=array_fill(0::real, ARRAY[384])::vector(384),
                            search_vector=to_tsvector('simple', 'stale missing copy'),
                            confidence=0.9, invalid_at=NULL WHERE source_schema='chronicler_mem'
                              AND source_table='facts' AND source_id=:id"""),
                        {"id": missing_fact_id},
                    )
                    conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
                    conn.execute(
                        text("""INSERT INTO public.entity_graph_edges
                            (source_schema, source_table, source_id, subject_entity_id,
                             predicate, object_entity_id)
                            VALUES ('chronicler_mem', 'facts', :id, :subject,
                                    'related_to', :object)"""),
                        {"id": missing_fact_id, "subject": subject_id, "object": object_id},
                    )
                    conn.execute(text("RESET ROLE"))
                    conn.execute(
                        text("""INSERT INTO chronicler.daily_repair_archive
                            (local_date, generation, row_kind, row_key, audit_hash, content,
                             dependency_ids, text_access)
                            VALUES (:day, 999, 'test', 'privacy-copy-stale', 'stale-hash',
                                    CAST('{"title":"recreated private title"}' AS jsonb),
                                    ARRAY[CAST(:episode_id AS uuid)], 'audit_only')"""),
                        {"day": day, "episode_id": episode_id},
                    )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE episodes SET title='ordinary edit' WHERE id=$1", episode_id
                        )
                catalog = await pool.fetchrow(
                    "SELECT summary, title, embedding, search_vector, confidence, invalid_at "
                    "FROM public.memory_catalog WHERE source_schema='chronicler_mem' "
                    "AND source_table='facts' AND source_id=$1",
                    fact_id,
                )
                assert catalog["summary"] == "" and catalog["title"] is None
                assert catalog["embedding"] is None and catalog["search_vector"] is None
                assert catalog["confidence"] == 0 and catalog["invalid_at"] is not None
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM public.entity_graph_edges "
                        "WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1",
                        fact_id,
                    )
                    == 0
                )
                missing_catalog = await pool.fetchrow(
                    """SELECT summary, title, embedding, search_vector, confidence, invalid_at
                       FROM public.memory_catalog WHERE source_schema='chronicler_mem'
                         AND source_table='facts' AND source_id=$1""",
                    missing_fact_id,
                )
                assert missing_catalog is not None
                assert (missing_catalog["summary"], missing_catalog["title"]) == ("", None)
                assert (
                    missing_catalog["embedding"] is None
                    and missing_catalog["search_vector"] is None
                )
                assert (
                    missing_catalog["confidence"] == 0 and missing_catalog["invalid_at"] is not None
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler_mem.facts WHERE id=$1)",
                    missing_fact_id,
                )
                assert not await pool.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges
                       WHERE source_schema='chronicler_mem' AND source_table='facts'
                         AND source_id=$1)""",
                    missing_fact_id,
                )
                unrelated_catalog = await pool.fetchrow(
                    """SELECT summary, title, confidence, invalid_at
                       FROM public.memory_catalog WHERE source_schema='chronicler_mem'
                         AND source_table='facts' AND source_id=$1""",
                    unrelated_id,
                )
                assert unrelated_catalog is not None
                assert (unrelated_catalog["summary"], unrelated_catalog["title"]) == (
                    "unrelated survivor",
                    "unrelated title",
                )
                assert (
                    unrelated_catalog["confidence"] == 0.9
                    and unrelated_catalog["invalid_at"] is None
                )
                assert await pool.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges
                       WHERE source_schema='chronicler_mem' AND source_table='facts'
                         AND source_id=$1)""",
                    unrelated_id,
                )
                final_fact = await pool.fetchrow(
                    "SELECT subject, content, embedding, search_vector, confidence, validity, "
                    "invalid_at, tags, metadata FROM chronicler_mem.facts WHERE id=$1",
                    fact_id,
                )
                assert final_fact["subject"] == "chronicler redacted fact"
                assert final_fact["content"] == "[removed by source privacy correction]"
                assert final_fact["embedding"] is None and final_fact["search_vector"] is None
                assert final_fact["confidence"] == 0 and final_fact["validity"] == "retracted"
                assert final_fact["invalid_at"] is not None and final_fact["tags"] == []
                assert final_fact["metadata"]["redacted"] is True
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM chronicler.daily_repair_archive "
                        "WHERE $1=ANY(dependency_ids) AND (content IS NOT NULL OR audit_hash IS NOT NULL)",
                        episode_id,
                    )
                    == 0
                )
                # The source generation is now numerically rebuilt and its exact
                # redacted inventory can be reconciled without writing new facts.
                await pool.execute(
                    "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
                )
                from butlers.chronicler.history_repair import run_rebuild

                result = await run_rebuild(pool, now=now)
                assert result["status"] == "complete"
                repaired = await pool.fetchrow(
                    "SELECT facts_pending FROM daily_repair_days WHERE local_date=$1", day
                )
                assert repaired is not None and repaired["facts_pending"] is False
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_pending_fact_inventory_reconciles_only_the_exact_source_generation(postgres_container):
    """A same-generation no-match is sufficient; a later generation cannot reuse it."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    day = (now - timedelta(days=10)).date()
    start = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=12)

    async def runtime_connection(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE butler_chronicler_rw")

    subject_id, object_id = uuid4(), uuid4()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for entity_id, name in (
                (subject_id, f"reconcile-subject-{subject_id}"),
                (object_id, f"reconcile-object-{object_id}"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, :name, 'other')"
                    ),
                    {"id": entity_id, "name": name},
                )
    finally:
        engine.dispose()

    async def scenario():
        from butlers.chronicler.history_repair import run_rebuild

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        try:
            await pool.execute(
                """INSERT INTO source_adapter_state
                   (source_name, chronicler_compatibility)
                   VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING"""
            )
            episode_id = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer)
                VALUES ('steam.play_history','reconcile-no-match','play_episode',$1,$2,'evidence')
                RETURNING id""",
                start,
                start + timedelta(hours=1),
            )
            pending = await pool.fetchrow(
                "SELECT * FROM daily_repair_days WHERE local_date=$1", day
            )
            assert pending is not None
            assert pending["facts_pending"] and pending["totals_pending"]
            dispositions = await pool.fetch(
                """SELECT generation, row_key, fact_id, disposition, dependency_ids
                   FROM daily_repair_fact_dispositions
                   WHERE local_date=$1 AND generation=$2 ORDER BY row_key""",
                day,
                pending["dirty_generation"],
            )
            assert len(dispositions) == 1
            assert dispositions[0]["generation"] == pending["dirty_generation"]
            assert dispositions[0]["row_key"] == "no_match"
            assert dispositions[0]["fact_id"] is None
            assert dispositions[0]["disposition"] == "no_match"
            assert episode_id in dispositions[0]["dependency_ids"]

            await pool.execute(
                "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
            )
            result = await run_rebuild(pool, now=now)
            assert result["status"] == "complete"
            completed = await pool.fetchrow(
                "SELECT * FROM daily_repair_days WHERE local_date=$1", day
            )
            assert completed["built_generation"] == completed["dirty_generation"]
            assert not completed["totals_pending"] and not completed["outlier_pending"]
            assert not completed["facts_pending"]

            # A later unrelated no-match generation cannot skip projection cleanup
            # for an older, durable redaction whose canonical row was purged.
            old_fact_id, old_source_id = uuid4(), uuid4()
            survivor_id = uuid4()
            current_generation = completed["dirty_generation"] + 1
            await pool.execute(
                """UPDATE daily_repair_days
                   SET dirty_generation=$2, facts_pending=TRUE
                   WHERE local_date=$1""",
                day,
                current_generation,
            )
            await pool.execute(
                """INSERT INTO daily_repair_fact_dispositions
                   (local_date, generation, row_key, fact_id, disposition, reason_code,
                    dependency_ids, redacted)
                   VALUES ($1, $2, $3, $4, 'disposed', 'source_privacy',
                           ARRAY[$5::uuid], TRUE)""",
                day,
                completed["dirty_generation"],
                str(old_fact_id),
                old_fact_id,
                old_source_id,
            )
            await pool.execute(
                """INSERT INTO daily_repair_fact_dispositions
                   (local_date, generation, row_key, fact_id, disposition, reason_code,
                    dependency_ids, redacted)
                   VALUES ($1, $2, 'no_match', NULL, 'no_match', 'source_changed',
                           ARRAY[$3::uuid], FALSE)""",
                day,
                current_generation,
                episode_id,
            )
            for fact_id, summary, title in (
                (old_fact_id, "stale redacted copy", "stale redacted title"),
                (survivor_id, "unrelated survivor", "unrelated title"),
            ):
                await pool.execute(
                    """INSERT INTO public.memory_catalog
                       (source_schema, source_table, source_id, summary, title, confidence)
                       VALUES ('chronicler_mem', 'facts', $1, $2, $3, 0.9)""",
                    fact_id,
                    summary,
                    title,
                )
                await pool.execute(
                    """INSERT INTO public.entity_graph_edges
                       (source_schema, source_table, source_id, subject_entity_id,
                        predicate, object_entity_id)
                       VALUES ('chronicler_mem', 'facts', $1, $2, 'related_to', $3)""",
                    fact_id,
                    subject_id,
                    object_id,
                )

            result = await run_rebuild(pool, now=now)
            assert result["status"] == "waiting_facts"
            still_pending = await pool.fetchrow(
                "SELECT dirty_generation, facts_pending FROM daily_repair_days WHERE local_date=$1",
                day,
            )
            assert still_pending["dirty_generation"] == current_generation
            assert still_pending["facts_pending"] is True
            stale = await pool.fetchrow(
                "SELECT summary, title, invalid_at FROM public.memory_catalog WHERE source_id=$1",
                old_fact_id,
            )
            assert stale is not None
            assert (stale["summary"], stale["title"]) == (
                "stale redacted copy",
                "stale redacted title",
            )
            assert stale["invalid_at"] is None
            assert not await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM chronicler_mem.facts WHERE id=$1)", old_fact_id
            )
            assert await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges "
                "WHERE source_schema='chronicler_mem' AND source_table='facts' "
                "AND source_id=$1)",
                old_fact_id,
            )

            # Once the exact stale projection is repaired, the same no-match
            # inventory can clear pending without touching an unrelated fact.
            await pool.execute(
                "UPDATE public.memory_catalog SET summary='', title=NULL, embedding=NULL, "
                "search_vector=NULL, confidence=0, invalid_at=now() "
                "WHERE source_schema='chronicler_mem' AND source_table='facts' "
                "AND source_id=$1",
                old_fact_id,
            )
            await pool.execute(
                "DELETE FROM public.entity_graph_edges "
                "WHERE source_schema='chronicler_mem' AND source_table='facts' "
                "AND source_id=$1",
                old_fact_id,
            )
            result = await run_rebuild(pool, now=now)
            assert result["status"] == "complete"
            cleared = await pool.fetchrow(
                "SELECT dirty_generation, facts_pending FROM daily_repair_days WHERE local_date=$1",
                day,
            )
            assert cleared["dirty_generation"] == current_generation
            assert cleared["facts_pending"] is False
            survivor = await pool.fetchrow(
                "SELECT summary, title, confidence, invalid_at "
                "FROM public.memory_catalog WHERE source_id=$1",
                survivor_id,
            )
            assert survivor is not None
            assert (survivor["summary"], survivor["title"]) == (
                "unrelated survivor",
                "unrelated title",
            )
            assert survivor["confidence"] == 0.9 and survivor["invalid_at"] is None
            assert await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges "
                "WHERE source_schema='chronicler_mem' AND source_table='facts' "
                "AND source_id=$1)",
                survivor_id,
            )

            # A new edit after reset owns a newer generation; the old exact
            # inventory must not clear it or admit reads while replay is stale.
            await pool.execute(
                "UPDATE episodes SET title='post-reset source edit' WHERE id=$1", episode_id
            )
            newer = await pool.fetchrow(
                "SELECT dirty_generation, facts_pending FROM daily_repair_days WHERE local_date=$1",
                day,
            )
            assert newer["dirty_generation"] > cleared["dirty_generation"]
            assert newer["facts_pending"] is True
            result = await run_rebuild(pool, now=now)
            assert result["status"] == "waiting_replay"
            still_newer = await pool.fetchrow(
                "SELECT dirty_generation, facts_pending FROM daily_repair_days WHERE local_date=$1",
                day,
            )
            assert still_newer["dirty_generation"] == newer["dirty_generation"]
            assert still_newer["facts_pending"] is True
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_source_redaction_tombstone_survives_no_match_and_cleans_late_archive_copies(
    postgres_container,
):
    """A source-ID tombstone survives empty fact inventory and cleared privacy flags."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    old_start = (now - timedelta(days=10)).replace(hour=12, minute=0, second=0)
    open_start = (now + timedelta(days=2)).replace(hour=12, minute=0, second=0)

    async def runtime_connection(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE butler_chronicler_rw")

    async def scenario():
        from butlers.chronicler.history_repair import run_rebuild

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        try:
            await pool.execute(
                """INSERT INTO source_adapter_state
                   (source_name, chronicler_compatibility)
                   VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING"""
            )
            old_id = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                VALUES ('steam.play_history','no-match-old','play_episode',$1,$2,'evidence',
                        'private historical title') RETURNING id""",
                old_start,
                old_start + timedelta(hours=1),
            )
            open_id = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                VALUES ('steam.play_history','no-match-open','play_episode',$1,$2,'evidence',
                        'private open title') RETURNING id""",
                open_start,
                open_start + timedelta(hours=1),
            )
            survivor_id = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                VALUES ('steam.play_history','ordinary-survivor','play_episode',$1,$2,'evidence',
                        'ordinary historical title') RETURNING id""",
                old_start + timedelta(hours=2),
                old_start + timedelta(hours=3),
            )
            await pool.execute(
                "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
            )
            assert (await run_rebuild(pool, now=now))["status"] == "complete"

            no_match = await pool.fetchrow(
                """SELECT disposition, redacted FROM daily_repair_fact_dispositions
                   WHERE local_date=$1 AND row_key='no_match'
                   ORDER BY generation DESC LIMIT 1""",
                old_start.date(),
            )
            assert no_match is not None
            assert (no_match["disposition"], no_match["redacted"]) == ("no_match", False)

            # Both IDs are redacted while their privacy overrides are active.
            for episode_id in (old_id, open_id):
                await pool.execute(
                    "INSERT INTO overrides (target_kind, target_id, corrected_privacy) "
                    "VALUES ('episode', $1, 'restricted')",
                    episode_id,
                )
            redacted = await pool.fetch(
                "SELECT source_id, reason_code FROM daily_repair_source_redactions "
                "WHERE source_id=ANY($1::uuid[])",
                [old_id, open_id],
            )
            assert {row["source_id"] for row in redacted} == {old_id, open_id}
            assert all(row["reason_code"] == "source_privacy" for row in redacted)
            assert await pool.fetchval(
                """SELECT NOT EXISTS (
                       SELECT 1 FROM daily_repair_days WHERE local_date=$1
                   )""",
                open_start.date(),
            )

            # Clear the flags, then simulate late/recreated private copies. A later
            # ordinary edit must consult the durable ID tombstone and erase them.
            await pool.execute(
                "DELETE FROM overrides WHERE target_id = ANY($1::uuid[])",
                [old_id, open_id],
            )
            for episode_id, local_day, row_key in (
                (old_id, old_start.date(), "late-old-redacted-copy"),
                (open_id, open_start.date(), "late-open-redacted-copy"),
                (survivor_id, old_start.date(), "ordinary-survivor-copy"),
            ):
                await pool.execute(
                    """INSERT INTO daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES ($1, 999, 'test', $2, 'retained-hash',
                            '{\"private\":\"copied title\"}'::jsonb,
                            ARRAY[$3::uuid], 'audit_only')""",
                    local_day,
                    row_key,
                    episode_id,
                )
            for episode_id in (old_id, open_id, survivor_id):
                await pool.execute(
                    "UPDATE episodes SET title=title || ' corrected' WHERE id=$1", episode_id
                )

            for episode_id in (old_id, open_id):
                stale = await pool.fetchval(
                    """SELECT EXISTS (
                           SELECT 1 FROM daily_repair_archive
                           WHERE $1=ANY(dependency_ids)
                             AND (content IS NOT NULL OR audit_hash IS NOT NULL
                                  OR text_access <> 'redacted')
                       )""",
                    episode_id,
                )
                assert stale is False
            survivor = await pool.fetchrow(
                """SELECT content, audit_hash, text_access FROM daily_repair_archive
                   WHERE $1=ANY(dependency_ids) AND row_key='ordinary-survivor-copy'""",
                survivor_id,
            )
            assert survivor is not None
            assert survivor["content"] == {"private": "copied title"}
            assert survivor["audit_hash"] == "retained-hash"
            assert survivor["text_access"] == "audit_only"
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_expired_source_retention_scan_redacts_and_rebuilds_without_default_ttl(
    postgres_container,
):
    """The real bounded rebuild scans only explicit retention_days and is idempotent."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=8)).replace(hour=12, minute=0, second=0)
    expiry_days = 10
    future = now + timedelta(days=expiry_days + 1)
    day = start.date()

    async def runtime_connection(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE butler_chronicler_rw")

    async def scenario():
        from butlers.chronicler.history_repair import run_rebuild

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        observer = None
        observer_objects = False
        observer_names = None
        try:
            original_timeout = await pool.fetchval("SELECT current_setting('statement_timeout')")
            await pool.execute(
                """INSERT INTO source_adapter_state
                   (source_name, chronicler_compatibility)
                   VALUES ('chronicler.situation_inferred', 'supported') ON CONFLICT DO NOTHING"""
            )
            episode_id = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer,
                 title, retention_days)
                VALUES ('chronicler.situation_inferred','retention-scan','play_block',
                        $1,$2,'activity','retained until explicit expiry',$3) RETURNING id""",
                start,
                start + timedelta(hours=1),
                expiry_days,
            )
            await pool.execute(
                """INSERT INTO daily_rollups
                (local_date, lane, seconds, episode_count, timezone, narrative)
                VALUES ($1, 'play', 3600, 1, 'Asia/Singapore', 'historical rollup')""",
                day,
            )
            await pool.execute(
                "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
            )
            assert (await run_rebuild(pool, now=now))["status"] == "complete"
            assert await pool.fetchval(
                "SELECT seconds=3600 FROM daily_rollups WHERE local_date=$1 AND lane='play'", day
            )
            assert not await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM daily_repair_source_redactions WHERE source_id=$1)",
                episode_id,
            )
            assert (
                await pool.fetchval("SELECT current_setting('statement_timeout')")
                == original_timeout
            )

            # Capture the server-side GUC at three distinct admission write seams.
            # nextval/setval state survives transaction rollback, while the successful
            # path below also proves each later statement receives a smaller budget.
            observer = await asyncpg.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
            suffix = uuid4().hex[:12]
            function_name = f"f2_budget_observer_{suffix}"
            redaction_sequence = f"f2_budget_redaction_{suffix}"
            span_sequence = f"f2_budget_span_{suffix}"
            archive_sequence = f"f2_budget_archive_{suffix}"
            redaction_trigger = f"f2_budget_redaction_{suffix}"
            span_trigger = f"f2_budget_span_{suffix}"
            archive_trigger = f"f2_budget_archive_{suffix}"
            observer_names = (
                function_name,
                redaction_sequence,
                span_sequence,
                archive_sequence,
                redaction_trigger,
                span_trigger,
                archive_trigger,
            )
            await observer.execute(
                f"""CREATE SEQUENCE chronicler.{redaction_sequence};
                    CREATE SEQUENCE chronicler.{span_sequence};
                    CREATE SEQUENCE chronicler.{archive_sequence};
                    GRANT SELECT, USAGE, UPDATE ON SEQUENCE
                        chronicler.{redaction_sequence}, chronicler.{span_sequence},
                        chronicler.{archive_sequence} TO butler_chronicler_rw;
                    CREATE FUNCTION chronicler.{function_name}()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    DECLARE timeout_ms bigint;
                    BEGIN
                        IF TG_ARGV[2] = 'archive' THEN
                            IF NOT (TG_ARGV[0]::uuid = ANY(NEW.dependency_ids)) THEN
                                RETURN NEW;
                            END IF;
                        ELSIF NEW.source_id IS DISTINCT FROM TG_ARGV[0]::uuid THEN
                            RETURN NEW;
                        END IF;
                        SELECT setting::bigint INTO timeout_ms
                        FROM pg_catalog.pg_settings WHERE name='statement_timeout';
                        PERFORM pg_catalog.setval(TG_ARGV[1]::regclass, timeout_ms, TRUE);
                        PERFORM pg_catalog.pg_sleep(0.25);
                        RETURN NEW;
                    END $$;
                    CREATE TRIGGER {redaction_trigger}
                        BEFORE INSERT ON chronicler.daily_repair_source_redactions
                        FOR EACH ROW EXECUTE FUNCTION chronicler.{function_name}(
                            '{episode_id}', 'chronicler.{redaction_sequence}', 'source');
                    CREATE TRIGGER {span_trigger}
                        BEFORE INSERT ON chronicler.daily_repair_retention_spans
                        FOR EACH ROW EXECUTE FUNCTION chronicler.{function_name}(
                            '{episode_id}', 'chronicler.{span_sequence}', 'source');
                    CREATE TRIGGER {archive_trigger}
                        BEFORE UPDATE ON chronicler.daily_repair_archive
                        FOR EACH ROW EXECUTE FUNCTION chronicler.{function_name}(
                            '{episode_id}', 'chronicler.{archive_sequence}', 'archive');"""
            )
            observer_objects = True
            await observer.execute(
                """INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES ($1, 1, 'test', $2, 'budget-hash',
                            '{"title":"budget private copy"}'::jsonb,
                            ARRAY[$3::uuid], 'audit_only')""",
                day,
                f"budget-{suffix}",
                episode_id,
            )

            # Start+the episode's declared retention_days has now passed. The
            # scanner's one invocation must tombstone, archive-redact and queue
            # historical numeric work without a guessed/global retention default.
            assert (await run_rebuild(pool, now=future))["status"] == "complete"
            assert (
                await pool.fetchval("SELECT current_setting('statement_timeout')")
                == original_timeout
            )
            assert observer is not None and observer_names is not None
            redaction_ms = await pool.fetchval(
                f"SELECT last_value FROM chronicler.{redaction_sequence}"
            )
            span_ms = await pool.fetchval(f"SELECT last_value FROM chronicler.{span_sequence}")
            archive_ms = await pool.fetchval(
                f"SELECT last_value FROM chronicler.{archive_sequence}"
            )
            assert 0 < archive_ms < span_ms < redaction_ms <= 5_000
            assert redaction_ms - span_ms >= 100
            assert span_ms - archive_ms >= 100
            marker = await pool.fetchrow(
                "SELECT reason_code FROM daily_repair_source_redactions WHERE source_id=$1",
                episode_id,
            )
            assert marker is not None and marker["reason_code"] == "source_retention"
            assert await pool.fetchval(
                "SELECT seconds=0 FROM daily_rollups WHERE local_date=$1 AND lane='play'", day
            )
            assert not await pool.fetchval(
                """SELECT EXISTS (
                       SELECT 1 FROM daily_repair_archive
                       WHERE $1=ANY(dependency_ids)
                         AND (content IS NOT NULL OR audit_hash IS NOT NULL
                              OR text_access <> 'redacted')
                   )""",
                episode_id,
            )
            generation = await pool.fetchval(
                "SELECT dirty_generation FROM daily_repair_days WHERE local_date=$1", day
            )
            assert (await run_rebuild(pool, now=future))["status"] == "complete"
            assert (
                await pool.fetchval(
                    "SELECT dirty_generation FROM daily_repair_days WHERE local_date=$1", day
                )
                == generation
            )
        finally:
            if observer is not None:
                if observer_objects and observer_names is not None:
                    (
                        function_name,
                        redaction_sequence,
                        span_sequence,
                        archive_sequence,
                        redaction_trigger,
                        span_trigger,
                        archive_trigger,
                    ) = observer_names
                    await observer.execute(
                        f"""DROP TRIGGER IF EXISTS {redaction_trigger}
                                ON chronicler.daily_repair_source_redactions;
                            DROP TRIGGER IF EXISTS {span_trigger}
                                ON chronicler.daily_repair_retention_spans;
                            DROP TRIGGER IF EXISTS {archive_trigger}
                                ON chronicler.daily_repair_archive;
                            DROP FUNCTION IF EXISTS chronicler.{function_name}();
                            DROP SEQUENCE IF EXISTS chronicler.{redaction_sequence};
                            DROP SEQUENCE IF EXISTS chronicler.{span_sequence};
                            DROP SEQUENCE IF EXISTS chronicler.{archive_sequence};"""
                    )
                await observer.close()
            await pool.close()

    asyncio.run(scenario())


def test_retention_redacts_stale_projections_for_missing_canonical_redacted_id(
    postgres_container,
):
    """Prior exact redaction IDs clean projections even after canonical purge."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    day = (datetime.now(UTC) - timedelta(days=20)).date()
    source_start = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=12)
    source_id, missing_fact_id, unrelated_id = uuid4(), uuid4(), uuid4()
    subject_id, object_id = uuid4(), uuid4()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, name in (
                (subject_id, "missing-redacted-subject"),
                (object_id, "missing-redacted-object"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, :name, 'other')"
                    ),
                    {"id": entity_id, "name": name},
                )
            conn.execute(
                text("""INSERT INTO chronicler.episodes
                    (id, source_name, source_ref, episode_type, start_at, end_at, layer, title)
                    VALUES (:id, 'steam.play_history', 'missing-redacted-projection',
                            'play_episode', :start_at, :end_at, 'evidence', 'private source')"""),
                {
                    "id": source_id,
                    "start_at": source_start,
                    "end_at": source_start + timedelta(hours=1),
                },
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_source_redactions
                    (source_id, reason_code) VALUES (:source_id, 'source_retention')"""),
                {"source_id": source_id},
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_retention_spans
                    (source_id, span_number, first_date, last_date, cursor_date, batch_days)
                    VALUES (:source_id, 1, :day, :day, :day, 1)"""),
                {"source_id": source_id, "day": day},
            )
            conn.execute(
                text("""UPDATE chronicler.daily_repair_control
                    SET closed_through=:day WHERE singleton"""),
                {"day": day},
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_fact_dispositions
                    (local_date, generation, row_key, fact_id, disposition, reason_code,
                     dependency_ids, redacted)
                    VALUES (:day, 1, :fact_key, :fact_id, 'disposed', 'source_privacy',
                            ARRAY[CAST(:source_id AS uuid)], TRUE)"""),
                {
                    "day": day,
                    "fact_key": str(missing_fact_id),
                    "fact_id": missing_fact_id,
                    "source_id": source_id,
                },
            )
            for fact_id, summary, title in (
                (missing_fact_id, "stale private summary", "stale private title"),
                (unrelated_id, "unrelated survivor", "unrelated title"),
            ):
                conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
                conn.execute(
                    text("""INSERT INTO public.memory_catalog
                        (source_schema, source_table, source_id, summary, title, confidence)
                        VALUES ('chronicler_mem', 'facts', :id, :summary, :title, 0.9)"""),
                    {"id": fact_id, "summary": summary, "title": title},
                )
                conn.execute(
                    text("""INSERT INTO public.entity_graph_edges
                        (source_schema, source_table, source_id, subject_entity_id,
                         predicate, object_entity_id)
                        VALUES ('chronicler_mem', 'facts', :id, :subject_id,
                                'related_to', :object_id)"""),
                    {"id": fact_id, "subject_id": subject_id, "object_id": object_id},
                )
                conn.exec_driver_sql("RESET ROLE")

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public"},
                init=runtime_connection,
            )
            try:
                affected = await pool.fetchval(
                    "SELECT chronicler.redact_retention_facts($1)", source_id
                )
                assert affected == 1
                stale = await pool.fetchrow(
                    """SELECT summary, title, embedding, search_vector, confidence, invalid_at
                       FROM public.memory_catalog
                       WHERE source_schema='chronicler_mem' AND source_table='facts'
                         AND source_id=$1""",
                    missing_fact_id,
                )
                assert stale is not None
                assert (stale["summary"], stale["title"], stale["confidence"]) == ("", None, 0)
                assert stale["embedding"] is None and stale["search_vector"] is None
                assert stale["invalid_at"] is not None
                assert not await pool.fetchval(
                    """SELECT EXISTS (
                           SELECT 1 FROM public.entity_graph_edges
                           WHERE source_schema='chronicler_mem' AND source_table='facts'
                             AND source_id=$1)""",
                    missing_fact_id,
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler_mem.facts WHERE id=$1)",
                    missing_fact_id,
                )
                disposition = await pool.fetchrow(
                    """SELECT disposition, redacted, reason_code, dependency_ids
                       FROM chronicler.daily_repair_fact_dispositions
                       WHERE local_date=$1 AND fact_id=$2
                       ORDER BY generation DESC LIMIT 1""",
                    day,
                    missing_fact_id,
                )
                assert disposition is not None
                assert (disposition["disposition"], disposition["redacted"]) == ("disposed", True)
                assert disposition["reason_code"] == "source_retention"
                assert source_id in disposition["dependency_ids"]

                survivor = await pool.fetchrow(
                    """SELECT summary, title, confidence, invalid_at
                       FROM public.memory_catalog
                       WHERE source_schema='chronicler_mem' AND source_table='facts'
                         AND source_id=$1""",
                    unrelated_id,
                )
                assert survivor is not None
                assert (survivor["summary"], survivor["title"]) == (
                    "unrelated survivor",
                    "unrelated title",
                )
                assert survivor["confidence"] == 0.9 and survivor["invalid_at"] is None
                assert await pool.fetchval(
                    """SELECT EXISTS (
                           SELECT 1 FROM public.entity_graph_edges
                           WHERE source_schema='chronicler_mem' AND source_table='facts'
                             AND source_id=$1)""",
                    unrelated_id,
                )
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_retention_cursor_timeout_respects_existing_ceiling_and_restores(postgres_container):
    """The cursor limit is numeric milliseconds and preserves the prior GUC exactly."""
    from butlers.chronicler.history_repair import _set_retention_cursor_statement_timeout

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")

    async def scenario():
        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
        )
        try:
            async with pool.acquire() as conn:
                for prior_timeout, expected_ms in (("0", 0), ("2s", 2_000), ("10s", 10_000)):
                    await conn.fetchval(
                        "SELECT set_config('statement_timeout', $1, FALSE)", prior_timeout
                    )
                    try:
                        async with conn.transaction():
                            previous = await _set_retention_cursor_statement_timeout(conn, 5_000)
                            assert previous == prior_timeout
                            effective_ms = await conn.fetchval(
                                """SELECT setting::bigint FROM pg_catalog.pg_settings
                                   WHERE name='statement_timeout'"""
                            )
                            assert effective_ms == (
                                5_000 if expected_ms == 0 else min(5_000, expected_ms)
                            )
                            await conn.fetchval(
                                "SELECT set_config('statement_timeout', $1, TRUE)", previous
                            )
                            restored_ms = await conn.fetchval(
                                """SELECT setting::bigint FROM pg_catalog.pg_settings
                                   WHERE name='statement_timeout'"""
                            )
                            assert restored_ms == expected_ms
                        session_ms = await conn.fetchval(
                            """SELECT setting::bigint FROM pg_catalog.pg_settings
                               WHERE name='statement_timeout'"""
                        )
                        assert session_ms == expected_ms
                    finally:
                        await conn.execute("RESET statement_timeout")
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_retention_cursor_keeps_old_copies_invalid_while_long_span_is_pending(
    postgres_container, monkeypatch
):
    from zoneinfo import ZoneInfo

    from butlers.chronicler import history_repair
    from butlers.chronicler.history_repair import RepairAdmissionError, capture_fact_write_token

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=300)).replace(hour=12, minute=0, second=0)
    end = start + timedelta(days=100)
    expiry = 400
    future = now + timedelta(days=101)
    local_day = start.astimezone(ZoneInfo("Asia/Singapore")).date()
    episode_id, fact_id, reminder_id = uuid4(), uuid4(), uuid4()
    subject_id, object_id = uuid4(), uuid4()

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, name in (
                (subject_id, "retention-subject"),
                (object_id, "retention-object"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, :name, 'other')"
                    ),
                    {"id": entity_id, "name": name},
                )
            conn.execute(
                text("""INSERT INTO chronicler.episodes
                    (id, source_name, source_ref, episode_type, start_at, end_at,
                     layer, title, retention_days)
                    VALUES (:id, 'steam.play_history', 'retention-long', 'play_episode',
                            :start, :end, 'evidence', 'private source', :retention)"""),
                {"id": episode_id, "start": start, "end": end, "retention": expiry},
            )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, confidence, source_butler,
                     content_authority, validity, tags, metadata)
                    VALUES (:id, 'work lane', 'lane-skew', 'private insight', 0.9,
                            'chronicler', 'system', 'active', CAST(:tags AS jsonb),
                            CAST(:metadata AS jsonb))"""),
                {
                    "id": fact_id,
                    "tags": '["chronicler-insight", "lane-skew", "work"]',
                    "metadata": (
                        '{"source":"chronicler","local_date":"'
                        + local_day.isoformat()
                        + '","lane":"work","direction":"down","seconds":1,'
                        '"baseline_seconds":2,"delta_seconds":-1,"baseline_sample_days":14}'
                    ),
                },
            )
            reminder_detail = '{"dark_sources":["steam.play_history"]}'
            conn.execute(
                text("""INSERT INTO chronicler.daily_rollup_flags
                    (local_date, flag_type, severity, detail, narrative)
                    VALUES (:day, 'feeder_dark', 'warning', CAST(:detail AS jsonb),
                            'private reminder')"""),
                {"day": local_day, "detail": reminder_detail},
            )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, confidence, source_butler,
                     content_authority, validity, tags, metadata)
                    VALUES (:id, :subject, 'revisit-low-confidence-block',
                            'private reminder', 0.9, 'chronicler', 'system', 'active',
                            '["chronicler-self-reminder", "feeder_dark"]'::jsonb,
                            CAST(:metadata AS jsonb))"""),
                {
                    "id": reminder_id,
                    "subject": f"{local_day.isoformat()} feeder_dark",
                    "metadata": (
                        '{"source":"chronicler","local_date":"'
                        + local_day.isoformat()
                        + '","flag_type":"feeder_dark","severity":"warning",'
                        + '"detail":{"dark_sources":["steam.play_history"]}}'
                    ),
                },
            )
            conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
            conn.execute(
                text("""INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, summary, title, confidence)
                    VALUES ('chronicler_mem', 'facts', :id, 'private summary', 'private title', 0.9)"""),
                {"id": fact_id},
            )
            conn.execute(
                text("""INSERT INTO public.entity_graph_edges
                    (source_schema, source_table, source_id, subject_entity_id,
                     predicate, object_entity_id)
                    VALUES ('chronicler_mem', 'facts', :id, :subject, 'related_to', :object)"""),
                {"id": fact_id, "subject": subject_id, "object": object_id},
            )
            conn.exec_driver_sql("RESET ROLE")

            for rollup_day, lane, seconds in (
                (local_day, "play", 3600),
                (local_day + timedelta(days=110), "work", 123),
            ):
                conn.execute(
                    text("""INSERT INTO chronicler.daily_rollups
                        (local_date, lane, seconds, episode_count, timezone)
                        VALUES (:day, :lane, :seconds, 1, 'Asia/Singapore')"""),
                    {"day": rollup_day, "lane": lane, "seconds": seconds},
                )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES (:day, 1, 'test', 'long-retention-copy', 'private-hash',
                            CAST('{"title":"private source"}' AS jsonb),
                            ARRAY[CAST(:episode AS uuid)], 'audit_only')"""),
                {"day": local_day, "episode": episode_id},
            )
            # The first scan admits the expired source and increments this day
            # once while redacting its generated facts. The cursor enqueue then
            # increments it once more. Cursor timeouts roll back that enqueue
            # savepoint, so every retry reaches exactly this generation.
            cursor_generation = conn.execute(
                text(
                    "SELECT dirty_generation FROM chronicler.daily_repair_days "
                    "WHERE local_date=:day"
                ),
                {"day": local_day},
            ).scalar_one()
            cursor_timeout_generation = cursor_generation + 2
            conn.exec_driver_sql("""
                CREATE OR REPLACE FUNCTION chronicler.f2_cursor_timeout_delay()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.local_date = TG_ARGV[0]::date
                       AND NEW.dirty_generation = TG_ARGV[1]::bigint THEN
                        PERFORM pg_sleep(30.0);
                    END IF;
                    RETURN NEW;
                END $$
            """)
            conn.exec_driver_sql(f"""
                CREATE TRIGGER f2_cursor_timeout_delay
                BEFORE INSERT OR UPDATE ON chronicler.daily_repair_days
                FOR EACH ROW EXECUTE FUNCTION chronicler.f2_cursor_timeout_delay(
                    '{local_day.isoformat()}', '{cursor_timeout_generation}')
            """)

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            from butlers.chronicler.history_repair import run_rebuild

            app_name = f"f1-retention-cursor-timeout-{uuid4().hex}"
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                min_size=1,
                max_size=1,
                server_settings={
                    "search_path": "chronicler, public",
                    "statement_timeout": "2s",
                    "application_name": app_name,
                },
                init=runtime_connection,
            )
            observer = None
            try:
                observer = await asyncpg.connect(
                    url.replace("postgresql+psycopg2://", "postgresql://"),
                    server_settings={"search_path": "chronicler, public"},
                )
                await pool.execute(
                    "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
                )
                backend_pid = await pool.fetchval("SELECT pg_backend_pid()")

                async def timed_cursor_attempt():
                    task = asyncio.create_task(run_rebuild(pool, now=future))
                    observed_query = None
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + 4.0
                    while not task.done() and loop.time() < deadline:
                        activity = await observer.fetchrow(
                            """SELECT query FROM pg_catalog.pg_stat_activity
                               WHERE pid=$1 AND application_name=$2 AND state='active'
                                 AND wait_event='PgSleep'""",
                            backend_pid,
                            app_name,
                        )
                        if activity is not None:
                            observed_query = activity["query"]
                            if "enqueue_daily_repair" in observed_query:
                                break
                        await asyncio.sleep(0.01)
                    if observed_query is None or "enqueue_daily_repair" not in observed_query:
                        if not task.done():
                            task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        pytest.fail(
                            "expected cursor-stage PgSleep in enqueue_daily_repair; "
                            f"observed top-level query was {observed_query!r}"
                        )
                    return await task, observed_query

                monkeypatch.setattr(history_repair, "MAX_RETENTION_CURSOR_TIMEOUT_MS", 500)
                first, first_query = await timed_cursor_attempt()
                assert "enqueue_daily_repair" in first_query
                assert first.get("retention_admission_timeout") is not True
                assert first["status"] == "pending_retention"
                assert first["retention_timeout"] is True
                assert first["retention_timeout_count"] == 1
                assert first["retention_batch_days"] == 3
                assert await pool.fetchval(
                    "SELECT validity='retracted' AND content='[removed by source privacy correction]' "
                    "FROM chronicler_mem.facts WHERE id=$1",
                    fact_id,
                )
                assert await pool.fetchval(
                    "SELECT validity='retracted' AND content='[removed by source privacy correction]' "
                    "FROM chronicler_mem.facts WHERE id=$1",
                    reminder_id,
                )
                catalog = await pool.fetchrow(
                    "SELECT summary, title, confidence, invalid_at FROM public.memory_catalog "
                    "WHERE source_schema='chronicler_mem' AND source_id=$1",
                    fact_id,
                )
                assert (catalog["summary"], catalog["title"], catalog["confidence"]) == (
                    "",
                    None,
                    0,
                )
                assert catalog["invalid_at"] is not None
                for expected_count, expected_status in (
                    (2, "pending_retention"),
                    (3, "blocked_retention"),
                ):
                    repeated, repeated_query = await timed_cursor_attempt()
                    assert "enqueue_daily_repair" in repeated_query
                    assert repeated.get("retention_admission_timeout") is not True
                    assert repeated["status"] == expected_status
                    assert repeated["retention_timeout"] is True
                    assert repeated["retention_timeout_count"] == expected_count
                    assert repeated["retention_batch_days"] == 1
                assert await pool.fetchval("SELECT current_setting('statement_timeout')") == "2s"
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM public.entity_graph_edges "
                        "WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1",
                        fact_id,
                    )
                    == 0
                )
                assert await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_retention_spans "
                    "WHERE source_id=$1)",
                    episode_id,
                )
                assert not await pool.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_archive
                       WHERE $1=ANY(dependency_ids)
                         AND (content IS NOT NULL OR audit_hash IS NOT NULL
                              OR text_access <> 'redacted'))""",
                    episode_id,
                )
                from butlers.chronicler.history_repair import (
                    pending_numeric_repair_dates,
                    try_shared_lock,
                )

                async with pool.acquire() as conn:
                    async with conn.transaction():
                        assert await try_shared_lock(conn)
                        pending_dates = await pending_numeric_repair_dates(
                            conn,
                            start_date=local_day,
                            end_date=local_day + timedelta(days=110),
                        )
                assert local_day in pending_dates
                assert local_day + timedelta(days=110) not in pending_dates

                # Read through the production route with its real asyncpg pool.
                # The affected stale row must be hidden while the out-of-span
                # materialized control remains available.
                from butlers.roster_imports import roster_module
                from butlers.testing.db_manager import db_manager

                chronicler_api = roster_module("chronicler", "api/router")
                database = db_manager({"chronicler": pool})
                affected = await chronicler_api.get_rollups(
                    rollup_date=local_day, start_date=None, end_date=None, db=database
                )
                assert not affected.data.rollups_source_error
                assert affected.data.days[0].status == "pending_repair"
                assert affected.data.days[0].lanes == []
                unaffected = await chronicler_api.get_rollups(
                    rollup_date=local_day + timedelta(days=110),
                    start_date=None,
                    end_date=None,
                    db=database,
                )
                assert not unaffected.data.rollups_source_error
                assert unaffected.data.days[0].status == "materialized"
                work_lane = next(
                    lane for lane in unaffected.data.days[0].lanes if lane.lane == "work"
                )
                assert work_lane.seconds == 123

                cursor = await pool.fetchrow(
                    """SELECT first_date, cursor_date, timeout_count, batch_days
                       FROM chronicler.daily_repair_retention_spans
                       WHERE source_id=$1 ORDER BY span_number LIMIT 1""",
                    episode_id,
                )
                assert cursor["cursor_date"] == cursor["first_date"]
                assert cursor["timeout_count"] == 3
                assert cursor["batch_days"] == 1
                with pytest.raises(RepairAdmissionError, match="retention repair is pending"):
                    await capture_fact_write_token(
                        pool,
                        day_date=local_day,
                        timezone="Asia/Singapore",
                        baseline_lookback_days=28,
                    )

                with engine.begin() as admin:
                    admin.exec_driver_sql(
                        "DROP TRIGGER f2_cursor_timeout_delay ON chronicler.daily_repair_days"
                    )
                    admin.exec_driver_sql("DROP FUNCTION chronicler.f2_cursor_timeout_delay()")
                monkeypatch.setattr(
                    history_repair,
                    "MAX_RETENTION_CURSOR_TIMEOUT_MS",
                    history_repair.DEFAULT_RETENTION_CURSOR_TIMEOUT_MS,
                )
                for _ in range(50):
                    result = await run_rebuild(pool, now=future)
                    if result["status"] == "complete":
                        break
                else:
                    pytest.fail("retention cursor failed to drain")
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_retention_spans "
                    "WHERE source_id=$1)",
                    episode_id,
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        assert await try_shared_lock(conn)
                        pending_dates = await pending_numeric_repair_dates(
                            conn,
                            start_date=local_day,
                            end_date=local_day + timedelta(days=110),
                        )
                assert local_day not in pending_dates
            finally:
                if observer is not None:
                    await observer.close()
                await pool.close()

        asyncio.run(scenario())
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "DROP TRIGGER IF EXISTS f2_cursor_timeout_delay ON chronicler.daily_repair_days"
            )
            conn.exec_driver_sql("DROP FUNCTION IF EXISTS chronicler.f2_cursor_timeout_delay()")
        engine.dispose()


def test_real_fact_writer_and_source_edit_share_transaction_fence(postgres_container, monkeypatch):
    """A real factory write holds the shared fence; stale tokens stop before provenance."""
    from zoneinfo import ZoneInfo

    from butlers.chronicler.history_repair import (
        RepairAdmissionError,
        capture_fact_write_token,
        make_fact_write_guard,
    )
    from butlers.chronicler.writeback import build_chronicler_fact_writer
    from butlers.modules.memory import storage as memory_storage

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")

    class _Embedding:
        model_name = "f1-native-fence-test"

        @staticmethod
        def embed(_text):
            return [0.0] * 384

    async def scenario():
        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        dsn = url.replace("postgresql+psycopg2://", "postgresql://")
        domain_pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=3,
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        memory_pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=3,
            server_settings={"search_path": "chronicler_mem, public"},
            init=runtime_connection,
        )
        try:
            control = await domain_pool.fetchrow(
                "SELECT timezone, closed_through FROM chronicler.daily_repair_control "
                "WHERE singleton"
            )
            zone = ZoneInfo(control["timezone"])
            day = control["closed_through"] - timedelta(days=90)
            start = datetime.combine(
                day, datetime.min.time().replace(hour=12), tzinfo=zone
            ).astimezone(UTC)
            await domain_pool.execute(
                "INSERT INTO chronicler.source_adapter_state "
                "(source_name, chronicler_compatibility) "
                "VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING"
            )
            episode_id = await domain_pool.fetchval(
                """INSERT INTO chronicler.episodes
                   (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                   VALUES ('steam.play_history', 'f1-writer-fence', 'play_episode', $1, $2,
                           'activity', 'before title') RETURNING id""",
                start,
                start + timedelta(hours=1),
            )
            await domain_pool.execute(
                """INSERT INTO chronicler.daily_rollups
                   (local_date, lane, seconds, episode_count, timezone)
                   VALUES ($1, 'play', 3600, 1, $2)""",
                day,
                control["timezone"],
            )
            # Seed one explicitly ready generation after the fixture's initial source insert.
            await domain_pool.execute(
                "UPDATE chronicler.daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
            )
            await domain_pool.execute(
                """UPDATE chronicler.daily_repair_days
                   SET built_generation=dirty_generation, totals_pending=FALSE,
                       outlier_pending=FALSE, facts_pending=FALSE"""
            )
            token = await capture_fact_write_token(
                domain_pool, day_date=day, timezone=control["timezone"], baseline_lookback_days=0
            )

            writer = build_chronicler_fact_writer(memory_pool, _Embedding())
            original_resolve = memory_storage._resolve_write_provenance_with_conn
            guard_state = {"passed": False, "pid": None}
            resolved_pids = []
            guard_reached = asyncio.Event()
            release_writer = asyncio.Event()

            async def observed_resolve(conn, *args, **kwargs):
                assert guard_state["passed"], (
                    "provenance resolution ran before the transaction guard"
                )
                pid = await conn.fetchval("SELECT pg_backend_pid()")
                assert pid == guard_state["pid"], "guard and provenance used different connections"
                assert conn.is_in_transaction()
                resolved_pids.append(pid)
                return await original_resolve(conn, *args, **kwargs)

            monkeypatch.setattr(
                memory_storage, "_resolve_write_provenance_with_conn", observed_resolve
            )

            async def gated_guard(conn):
                await make_fact_write_guard(token)(conn)
                guard_state["pid"] = await conn.fetchval("SELECT pg_backend_pid()")
                guard_state["passed"] = True
                assert conn.is_in_transaction()
                guard_reached.set()
                await release_writer.wait()

            async def write(subject, guard):
                return await writer(
                    subject=subject,
                    predicate="chronicler-f1-fence-probe",
                    content="a bounded transaction-fence probe",
                    permanence="standard",
                    importance=5.0,
                    confidence=0.9,
                    tags=["f1-native-test"],
                    metadata={"local_date": day.isoformat()},
                    transaction_guard=guard,
                )

            write_task = asyncio.create_task(write("f1 writer first", gated_guard))
            try:
                await asyncio.wait_for(guard_reached.wait(), timeout=10)
                before = await domain_pool.fetchrow(
                    """SELECT dirty_generation, totals_pending, facts_pending
                       FROM chronicler.daily_repair_days WHERE local_date=$1""",
                    day,
                )
                epoch_before = await domain_pool.fetchval(
                    "SELECT replay_epoch FROM chronicler.daily_repair_control WHERE singleton"
                )
                with pytest.raises(asyncpg.PostgresError, match="daily repair busy"):
                    await domain_pool.execute(
                        "UPDATE chronicler.episodes SET title='blocked source edit' WHERE id=$1",
                        episode_id,
                    )
                assert (
                    await domain_pool.fetchval(
                        "SELECT title FROM chronicler.episodes WHERE id=$1", episode_id
                    )
                    == "before title"
                )
                after = await domain_pool.fetchrow(
                    """SELECT dirty_generation, totals_pending, facts_pending
                       FROM chronicler.daily_repair_days WHERE local_date=$1""",
                    day,
                )
                assert tuple(after) == tuple(before)
                assert (
                    await domain_pool.fetchval(
                        "SELECT replay_epoch FROM chronicler.daily_repair_control WHERE singleton"
                    )
                    == epoch_before
                )
            finally:
                release_writer.set()
            first_result = await asyncio.wait_for(write_task, timeout=20)
            assert resolved_pids == [guard_state["pid"]]
            assert (
                await memory_pool.fetchval(
                    "SELECT count(*) FROM chronicler_mem.facts WHERE id=$1", first_result["id"]
                )
                == 1
            )

            stale_token = await capture_fact_write_token(
                domain_pool, day_date=day, timezone=control["timezone"], baseline_lookback_days=0
            )
            await domain_pool.execute(
                "UPDATE chronicler.episodes SET title='accepted invalidation' WHERE id=$1",
                episode_id,
            )
            assert await domain_pool.fetchval(
                "SELECT facts_pending FROM chronicler.daily_repair_days WHERE local_date=$1", day
            )
            resolved_pids.clear()
            guard_state.update(passed=False, pid=None)
            with pytest.raises(RepairAdmissionError, match="state changed|generation is pending"):
                await write("f1 stale writer", make_fact_write_guard(stale_token))
            assert resolved_pids == [], "stale writer reached provenance or canonical storage"
            assert (
                await memory_pool.fetchval(
                    "SELECT count(*) FROM chronicler_mem.facts WHERE subject='f1 stale writer'"
                )
                == 0
            )
        finally:
            await memory_pool.close()
            await domain_pool.close()

    asyncio.run(scenario())


def test_real_narration_discards_delayed_result_after_source_edit(postgres_container, monkeypatch):
    """A source correction commits while LLM work waits, and the old prose is refused."""
    from zoneinfo import ZoneInfo

    from butlers.chronicler.narration import narrate_daily_rollup

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    monkeypatch.setenv("CHRONICLER_NARRATION_ENABLED", "true")

    async def scenario():
        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        dsn = url.replace("postgresql+psycopg2://", "postgresql://")
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        try:
            control = await pool.fetchrow(
                "SELECT timezone, closed_through FROM chronicler.daily_repair_control "
                "WHERE singleton"
            )
            zone = ZoneInfo(control["timezone"])
            day = control["closed_through"] - timedelta(days=12)
            start = datetime.combine(
                day, datetime.min.time().replace(hour=12), tzinfo=zone
            ).astimezone(UTC)
            await pool.execute(
                "INSERT INTO chronicler.source_adapter_state "
                "(source_name, chronicler_compatibility) "
                "VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING"
            )
            episode_id = await pool.fetchval(
                """INSERT INTO chronicler.episodes
                   (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                   VALUES ('steam.play_history', 'f1-narration-race', 'play_episode', $1, $2,
                           'activity', 'original activity') RETURNING id""",
                start,
                start + timedelta(hours=1),
            )
            await pool.execute(
                """INSERT INTO chronicler.daily_rollups
                   (local_date, lane, seconds, episode_count, timezone)
                   VALUES ($1, 'play', 3600, 1, $2)""",
                day,
                control["timezone"],
            )
            # Establish the fixture's baseline as ready; the test mutation below is real-triggered.
            await pool.execute(
                "UPDATE chronicler.daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
            )
            await pool.execute(
                """UPDATE chronicler.daily_repair_days
                   SET built_generation=dirty_generation, totals_pending=FALSE,
                       outlier_pending=FALSE, facts_pending=FALSE"""
            )

            import butlers.chronicler.narration as narration_module

            llm_started = asyncio.Event()
            release_llm = asyncio.Event()

            class _DelayedDispatcher:
                def __init__(self, *_args, **_kwargs):
                    pass

                async def call(self, _prompt, system_prompt=""):
                    llm_started.set()
                    await release_llm.wait()
                    return '{"day_summary":"A stale summary.","flag_labels":{}}'

            monkeypatch.setattr(narration_module, "DiscretionDispatcher", _DelayedDispatcher)
            task = asyncio.create_task(
                narrate_daily_rollup(pool, local_date=day, timezone=control["timezone"])
            )
            try:
                await asyncio.wait_for(llm_started.wait(), timeout=10)
                # This real source trigger must succeed while narration is outside any DB lock.
                await pool.execute(
                    "UPDATE chronicler.episodes SET title='corrected activity' WHERE id=$1",
                    episode_id,
                )
            finally:
                release_llm.set()
            result = await asyncio.wait_for(task, timeout=20)
            assert result["status"] == "waiting_repair"
            assert (
                await pool.fetchval(
                    "SELECT narrative FROM chronicler.daily_rollups "
                    "WHERE local_date=$1 AND lane='play'",
                    day,
                )
                is None
            )
            assert await pool.fetchval(
                "SELECT facts_pending FROM chronicler.daily_repair_days WHERE local_date=$1", day
            )
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_real_steam_status_to_play_rollup_keeps_spotify_uncounted(postgres_container, monkeypatch):
    """Real Steam/Spotify evidence writers feed Chronicler without counting Spotify."""
    from zoneinfo import ZoneInfo

    from butlers.chronicler.adapters.situation import SituationInferredAdapter
    from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
    from butlers.chronicler.adapters.steam_status import SteamStatusAdapter
    from butlers.chronicler.rollups import materialize_daily_rollups
    from butlers.connectors.metrics import ConnectorMetrics
    from butlers.connectors.spotify import (
        ListeningSession,
        SpotifyConnector,
        SpotifyConnectorConfig,
    )
    from butlers.connectors.steam import AccountPollerState, SteamAccountPoller

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            assert conn.execute(
                text("SELECT to_regclass('connectors.steam_status_history') IS NOT NULL")
            ).scalar_one()
            assert conn.execute(
                text("SELECT to_regclass('connectors.spotify_listening_sessions') IS NOT NULL")
            ).scalar_one()
    finally:
        engine.dispose()

    async def scenario():
        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def connector_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE connector_writer")

        dsn = url.replace("postgresql+psycopg2://", "postgresql://")
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            server_settings={"search_path": "chronicler, public"},
            init=runtime_connection,
        )
        now = datetime.now(UTC).replace(microsecond=0)
        try:
            control = await pool.fetchrow(
                "SELECT timezone, closed_through FROM chronicler.daily_repair_control "
                "WHERE singleton"
            )
            timezone = control["timezone"]
            zone = ZoneInfo(timezone)
            day = control["closed_through"]
            local_start = datetime.combine(day, datetime.min.time().replace(hour=8), tzinfo=zone)
            status_times = [
                local_start + timedelta(hours=4),
                local_start + timedelta(hours=4, minutes=20),
                local_start + timedelta(hours=5),
                local_start + timedelta(hours=6, minutes=30),
            ]
            spotify_start = local_start + timedelta(hours=1)
            await pool.execute(
                """INSERT INTO chronicler.source_adapter_state
                   (source_name, chronicler_compatibility)
                   VALUES ('steam.status_history', 'supported'),
                          ('spotify.session_summary', 'supported'),
                          ('chronicler.situation_inferred', 'supported')
                   ON CONFLICT (source_name) DO UPDATE
                   SET chronicler_compatibility=EXCLUDED.chronicler_compatibility"""
            )
            producer_pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=3,
                server_settings={"search_path": "connectors, public"},
                init=connector_connection,
            )
            try:
                state = AccountPollerState(
                    steam_id=76561198000000001,
                    endpoint_identity="gaming:steam:f1-native",
                    api_key="test-only-unused",
                )
                poller = SteamAccountPoller(
                    state=state,
                    db_pool=producer_pool,
                    mcp_client=object(),
                    metrics=ConnectorMetrics(
                        connector_type="steam", endpoint_identity=state.endpoint_identity
                    ),
                )
                snapshots = [
                    {"personastate": 0, "gameextrainfo": None},
                    {"personastate": 1, "gameextrainfo": "Game A", "gameid": "100"},
                    {"personastate": 2, "gameextrainfo": "Game A", "gameid": "100"},
                    {"personastate": 1, "gameextrainfo": "Game B", "gameid": "200"},
                    {"personastate": 0, "gameextrainfo": None},
                ]
                remaining_snapshots = iter(snapshots)

                class _SteamClient:
                    async def get_player_summaries(self, _steam_ids):
                        return [next(remaining_snapshots)]

                steam_envelopes = []

                async def ignore_ingest(envelope, *, data_type):
                    assert data_type == "online_status"
                    steam_envelopes.append(envelope)

                poller._steam_client = _SteamClient()
                poller._maybe_submit = ignore_ingest
                steam_poll_times = iter(
                    [local_start + timedelta(hours=3, minutes=50), *status_times]
                )
                monkeypatch.setattr(
                    "butlers.connectors.steam._now_iso",
                    lambda: next(steam_poll_times).isoformat(),
                )
                for _ in snapshots:
                    await poller._poll_online_status()

                spotify_connector = SpotifyConnector(
                    SpotifyConnectorConfig(switchboard_mcp_url="http://unused.invalid/mcp"),
                    cursor_pool=producer_pool,
                )
                spotify_connector._endpoint_identity = "spotify_user_client:f1-native"
                spotify_connector._spotify_user_id = "f1-native"
                submitted = []

                async def ignore_spotify_ingest(envelope):
                    submitted.append(envelope)

                spotify_connector._submit_envelope = ignore_spotify_ingest
                session = ListeningSession(
                    context_uri="spotify:playlist:f1-native",
                    started_at=spotify_start.astimezone(UTC),
                    context_name="Fixture playlist",
                    track_names=["Fixture track one", "Fixture track two"],
                    last_activity_at=(spotify_start + timedelta(hours=2)).astimezone(UTC),
                    drain_started_at=(spotify_start + timedelta(hours=2)).astimezone(UTC),
                )
                await spotify_connector._emit_session_summary(session, observed_at=now.isoformat())
                assert len(steam_envelopes) == 4
                assert len(submitted) == 1
                assert submitted[0]["event"]["external_event_id"].startswith("spotify:session:")
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM connectors.steam_status_history WHERE steam_id=$1",
                        76561198000000001,
                    )
                    == 4
                )
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM connectors.spotify_listening_sessions "
                        "WHERE endpoint_identity=$1",
                        "spotify_user_client:f1-native",
                    )
                    == 1
                )
            finally:
                await producer_pool.close()

            monkeypatch.setattr("butlers.chronicler.adapters.steam_status._now", lambda: now)
            monkeypatch.setattr("butlers.chronicler.adapters.situation._now", lambda: now)
            steam_result = await SteamStatusAdapter(batch_limit=1).project(
                pool, chronicler_pool=pool, since=None
            )
            spotify_result = await SpotifySessionAdapter(batch_limit=1).project(
                pool, chronicler_pool=pool, since=None
            )
            assert (steam_result.rows_projected, steam_result.episodes_closed) == (2, 2)
            assert spotify_result.rows_projected == 1
            sessions = await pool.fetch(
                """SELECT source_ref, episode_type, start_at, end_at, layer
                   FROM chronicler.episodes WHERE source_name='steam.status_history'
                   ORDER BY start_at"""
            )
            assert len(sessions) == 2
            assert [row["episode_type"] for row in sessions] == ["play_session", "play_session"]
            assert all(row["layer"] == "evidence" for row in sessions)
            assert all(row["end_at"] is not None for row in sessions)
            spotify = await pool.fetchrow(
                """SELECT episode_type, layer FROM chronicler.episodes
                   WHERE source_name='spotify.session_summary'"""
            )
            assert (spotify["episode_type"], spotify["layer"]) == (
                "listening_episode",
                "evidence",
            )

            situation = SituationInferredAdapter(lookback_days=3, timezone=timezone, references=())
            situation_result = await situation.project(pool, chronicler_pool=pool, since=None)
            await situation.save_checkpoint(pool, situation_result)
            blocks = await pool.fetch(
                """SELECT episode_type, start_at, end_at, evidence_refs
                   FROM chronicler.episodes
                   WHERE source_name='chronicler.situation_inferred'
                     AND episode_type='play_block'
                     AND payload->>'local_date'=$1""",
                day.isoformat(),
            )
            assert len(blocks) == 2
            assert all(len(block["evidence_refs"]) == 1 for block in blocks)

            await materialize_daily_rollups(pool, timezone=timezone, lookback_days=3, now=now)
            play = await pool.fetchrow(
                """SELECT seconds, episode_count FROM chronicler.daily_rollups
                   WHERE local_date=$1 AND lane='play'""",
                day,
            )
            assert play["seconds"] == 9_000
            assert play["episode_count"] == 2
        finally:
            await pool.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("malformation", "expected_error"),
    [
        (
            "facts_missing_content",
            "memory canonical/projection shape mismatch at chronicler_mem.facts.content",
        ),
        (
            "facts_wrong_subject_type",
            "memory canonical/projection shape mismatch at chronicler_mem.facts.subject",
        ),
        (
            "catalog_missing_title",
            "memory canonical/projection shape mismatch at public.memory_catalog.title",
        ),
        (
            "catalog_wrong_source_schema_type",
            "memory canonical/projection shape mismatch at public.memory_catalog.source_schema",
        ),
        (
            "canonical_view",
            "canonical Chronicler facts must be the owned private table",
        ),
        (
            "canonical_wrong_owner",
            "canonical Chronicler facts must be the owned private table",
        ),
        (
            "catalog_view",
            "memory projections must be persistent tables",
        ),
        (
            "invalidator_wrong_signature",
            "memory projection invalidation function is missing",
        ),
        (
            "invalidator_security_definer",
            "memory projection invalidation must remain SECURITY INVOKER",
        ),
    ],
)
def test_chronicler_030_refuses_malformed_memory_storage_before_schema_changes(
    postgres_container, malformation, expected_error
):
    """Every malformed canonical/projection prerequisite fails before 030 writes."""
    import re
    from urllib.parse import urlparse, urlunparse

    signature = "public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)"

    # A separately prepared valid database is the positive control for each
    # malformed variant; the negative database starts from the same 029 state.
    valid_url = _migrated_chronicler_db(postgres_container, "chronicler_029")
    valid_config = _build_alembic_config(
        valid_url, chains=["chronicler"], target_schema="chronicler"
    )
    command.upgrade(valid_config, "chronicler@chronicler_030")
    valid_engine = create_engine(valid_url)
    try:
        with valid_engine.connect() as conn:
            assert (  # pinned-revision: selected 029-to-030 prerequisite/refusal and migration-order boundary, not the evolving chain head
                conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).scalar_one()
                == "chronicler_030"
            )
            assert conn.execute(
                text("SELECT to_regclass('chronicler.daily_repair_control') IS NOT NULL")
            ).scalar_one()
    finally:
        valid_engine.dispose()

    url = _migrated_chronicler_db(postgres_container, "chronicler_029")
    admin = urlparse(postgres_container.get_connection_url())
    target = urlparse(url)
    admin_url = urlunparse((admin.scheme, admin.netloc, target.path, "", "", ""))
    from sqlalchemy import event

    admin_engine = create_engine(admin_url)
    engine = create_engine(url)
    observed_write_kinds: list[str] = []
    write_patterns = re.compile(
        r"\b(?:CREATE|ALTER|DROP|TRUNCATE)\s+(?:TEMP(?:ORARY)?\s+)?"
        r"(?:TABLE|SCHEMA|FUNCTION|INDEX)\b|\bINSERT\s+INTO\b|"
        r"\bUPDATE\s+[a-z_][\w.]*\s+SET\b|\bDELETE\s+FROM\b|"
        r"\b(?:GRANT|REVOKE)\s+\w+",
        re.IGNORECASE,
    )

    def observe_before_cursor_execute(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        leading_keyword = statement.lstrip().split(None, 1)[0].upper()
        if leading_keyword in {
            "CREATE",
            "ALTER",
            "DROP",
            "TRUNCATE",
            "INSERT",
            "UPDATE",
            "DELETE",
            "MERGE",
            "GRANT",
            "REVOKE",
        }:
            observed_write_kinds.append(leading_keyword)
        elif leading_keyword == "DO" and write_patterns.search(statement):
            observed_write_kinds.append("DO_WITH_WRITE")

    event.listen(engine, "before_cursor_execute", observe_before_cursor_execute)
    try:
        with admin_engine.begin() as conn:
            if malformation == "facts_missing_content":
                conn.execute(
                    text(
                        "ALTER TABLE chronicler_mem.facts RENAME COLUMN content TO fixture_content"
                    )
                )
            elif malformation == "facts_wrong_subject_type":
                conn.execute(
                    text(
                        "ALTER TABLE chronicler_mem.facts "
                        "ALTER COLUMN subject TYPE varchar USING subject::varchar"
                    )
                )
            elif malformation == "catalog_missing_title":
                conn.execute(
                    text("ALTER TABLE public.memory_catalog RENAME COLUMN title TO fixture_title")
                )
            elif malformation == "catalog_wrong_source_schema_type":
                conn.execute(
                    text(
                        "ALTER TABLE public.memory_catalog "
                        "ALTER COLUMN source_schema TYPE varchar USING source_schema::varchar"
                    )
                )
            elif malformation == "canonical_view":
                conn.execute(text("ALTER TABLE chronicler_mem.facts RENAME TO facts_fixture_table"))
                conn.execute(
                    text(
                        "CREATE VIEW chronicler_mem.facts "
                        "AS SELECT * FROM chronicler_mem.facts_fixture_table"
                    )
                )
            elif malformation == "canonical_wrong_owner":
                conn.execute(text("ALTER TABLE chronicler_mem.facts OWNER TO butler_chronicler_rw"))
            elif malformation == "catalog_view":
                conn.execute(
                    text("ALTER TABLE public.memory_catalog RENAME TO memory_catalog_fixture_table")
                )
                conn.execute(
                    text(
                        "CREATE VIEW public.memory_catalog "
                        "AS SELECT * FROM public.memory_catalog_fixture_table"
                    )
                )
            elif malformation == "invalidator_wrong_signature":
                conn.execute(
                    text(
                        f"ALTER FUNCTION {signature} RENAME TO invalidate_memory_projection_fixture"
                    )
                )
            elif malformation == "invalidator_security_definer":
                conn.execute(text(f"ALTER FUNCTION {signature} SECURITY DEFINER"))
            else:
                raise AssertionError(f"unhandled malformed fixture {malformation}")

        if malformation == "facts_missing_content":
            with pytest.raises(RuntimeError, match="late-guard observer control"):
                with engine.begin() as conn:
                    conn.execute(text("CREATE TEMP VIEW f1_030_observer_control AS SELECT 1 AS id"))
                    raise RuntimeError("late-guard observer control")
            assert observed_write_kinds == ["CREATE"]
            observed_write_kinds.clear()

        config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
        migration = ScriptDirectory.from_config(config).get_revision("chronicler_030")
        assert migration is not None
        with pytest.raises(Exception, match=re.escape(expected_error)):
            with engine.begin() as conn:
                context = MigrationContext.configure(conn)
                with Operations.context(context):
                    migration.module.upgrade()
        assert observed_write_kinds == []

        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regclass('chronicler.daily_repair_control') IS NULL")
            ).scalar_one()
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_class AS relation "
                        "JOIN pg_catalog.pg_namespace AS namespace "
                        "ON namespace.oid=relation.relnamespace "
                        "WHERE namespace.nspname='chronicler' "
                        "AND relation.relname LIKE 'daily_repair_%' "
                        "AND relation.relkind IN ('r','p')"
                    )
                ).scalar_one()
                == 0
            )
            assert (  # pinned-revision: selected 029-to-030 prerequisite/refusal and migration-order boundary, not the evolving chain head
                conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).scalar_one()
                == "chronicler_029"
            )
    finally:
        event.remove(engine, "before_cursor_execute", observe_before_cursor_execute)
        engine.dispose()
        admin_engine.dispose()


def test_chronicler_preparation_cli_and_daemon_migration_phases_use_private_schema(
    postgres_container, tmp_path, monkeypatch
):
    """The supported CLI prep unblocks the real CLI and daemon migration order."""
    from urllib.parse import unquote, urlparse

    from click.testing import CliRunner

    from butlers import migrations
    from butlers.cli import cli
    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon
    from butlers.lifecycle import (
        StartupState,
        _startup_core_migrations,
        _startup_module_migrations,
    )

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    parsed = urlparse(url)
    monkeypatch.setattr(
        "butlers.db.db_params_from_env",
        lambda: {
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "host": parsed.hostname,
            "port": parsed.port,
        },
    )

    butlers_dir = tmp_path / "roster"
    config_dir = butlers_dir / "chronicler"
    config_dir.mkdir(parents=True)
    db_name = parsed.path.lstrip("/")
    config_text = f"""[butler]
name = "chronicler"
port = 41111
description = "Migration-order integration fixture"

[butler.db]
name = "{db_name}"
schema = "chronicler"

[modules.memory]
groups = ["core", "entity"]
memory_schema = "chronicler_mem"
"""
    (config_dir / "butler.toml").write_text(config_text, encoding="utf-8")

    initial_engine = create_engine(url)
    try:
        with initial_engine.connect() as conn:
            initial_versions = {
                row[0]
                for row in conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).all()
            }
    finally:
        initial_engine.dispose()
    assert (
        "chronicler_029" in initial_versions
    )  # pinned-revision: selected 029-to-030 prerequisite/refusal and migration-order boundary, not the evolving chain head
    assert (
        "chronicler_030" not in initial_versions
    )  # pinned-revision: selected 029-to-030 prerequisite/refusal and migration-order boundary, not the evolving chain head
    initial_core_versions = {version for version in initial_versions if version.startswith("core_")}
    assert initial_core_versions
    expected_versions_after_success = (initial_versions - {"chronicler_029"}) | {"chronicler_030"}

    def assert_preparation_not_applied() -> None:
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                assert conn.execute(
                    text("SELECT to_regclass('chronicler_mem.facts') IS NULL")
                ).scalar_one()
                assert conn.execute(
                    text("SELECT to_regclass('chronicler.daily_repair_control') IS NULL")
                ).scalar_one()
                current_versions = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT version_num FROM chronicler.alembic_version")
                    ).all()
                }
                assert current_versions == initial_versions
        finally:
            engine.dispose()

    calls: list[tuple[str, str | None]] = []
    real_migrations_run = migrations.run_migrations

    async def track_cli_migration(db_url, chain="all", schema=None):
        calls.append((chain, schema))
        return await real_migrations_run(db_url, chain=chain, schema=schema)

    monkeypatch.setattr(migrations, "run_migrations", track_cli_migration)
    runner = CliRunner()

    migrate_before_prep = runner.invoke(
        cli, ["db", "migrate", "--only", "chronicler", "--dir", str(butlers_dir)]
    )
    assert migrate_before_prep.exit_code != 0, migrate_before_prep.output
    assert (
        "required canonical storage chronicler_mem.facts is missing" in migrate_before_prep.output
    )
    assert calls == [("core", "chronicler"), ("chronicler", "chronicler")]
    assert_preparation_not_applied()

    config = load_config(config_dir)
    daemon = ButlerDaemon(config_dir=config_dir)
    daemon.config = config
    daemon._modules = daemon._registry.load_from_config(config.modules)
    daemon._module_configs = daemon._validate_module_configs()
    monkeypatch.setattr(daemon, "_build_db_url", lambda: url)

    lifecycle_calls: list[tuple[str, str | None]] = []
    real_chain_at_head = migrations._chain_at_head

    def observe_chain_at_head(db_url, chain, schema):
        # Observe the real per-chain admission probe, never replace a daemon phase.
        lifecycle_calls.append((chain, schema))
        return real_chain_at_head(db_url, chain, schema)

    monkeypatch.setattr(migrations, "_chain_at_head", observe_chain_at_head)
    with pytest.raises(
        Exception, match="required canonical storage chronicler_mem.facts is missing"
    ):
        asyncio.run(_startup_core_migrations(daemon, StartupState()))
    assert lifecycle_calls == [("core", "chronicler"), ("chronicler", "chronicler")]
    assert_preparation_not_applied()

    calls.clear()
    prepared = runner.invoke(
        cli,
        ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir)],
    )
    assert prepared.exit_code == 0, prepared.output
    assert calls == [("core", "chronicler_mem"), ("memory", "chronicler_mem")]

    calls.clear()
    migrated = runner.invoke(
        cli, ["db", "migrate", "--only", "chronicler", "--dir", str(butlers_dir)]
    )
    assert migrated.exit_code == 0, migrated.output
    assert calls == [
        ("core", "chronicler"),
        ("chronicler", "chronicler"),
        ("memory", "chronicler_mem"),
    ]

    lifecycle_calls.clear()
    state = StartupState()
    asyncio.run(_startup_core_migrations(daemon, state))
    asyncio.run(_startup_module_migrations(daemon, state))
    assert lifecycle_calls == [
        ("core", "chronicler"),
        ("chronicler", "chronicler"),
        ("memory", "chronicler_mem"),
    ]

    calls.clear()
    prepared_again = runner.invoke(
        cli,
        ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir)],
    )
    assert prepared_again.exit_code == 0, prepared_again.output
    assert calls == [("core", "chronicler_mem"), ("memory", "chronicler_mem")]

    calls.clear()
    migrated_again = runner.invoke(
        cli, ["db", "migrate", "--only", "chronicler", "--dir", str(butlers_dir)]
    )
    assert migrated_again.exit_code == 0, migrated_again.output
    assert calls == [
        ("core", "chronicler"),
        ("chronicler", "chronicler"),
        ("memory", "chronicler_mem"),
    ]

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            final_versions = {
                row[0]
                for row in conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).all()
            }
            assert final_versions == expected_versions_after_success
            assert initial_core_versions <= final_versions
            assert conn.execute(
                text("SELECT to_regclass('chronicler_mem.facts') IS NOT NULL")
            ).scalar_one()
            assert conn.execute(text("SELECT to_regclass('chronicler.facts') IS NULL")).scalar_one()
            assert conn.execute(
                text("SELECT to_regclass('chronicler.episodes') IS NOT NULL")
            ).scalar_one()
            assert conn.execute(
                text("SELECT to_regclass('chronicler_mem.episodes') IS NOT NULL")
            ).scalar_one()
    finally:
        engine.dispose()


def test_source_change_disposes_exact_D_through_D28_and_preserves_D29_survivors(
    postgres_container,
):
    """The source correction window includes D..D+28, not D+29 or protected facts."""
    import json
    from zoneinfo import ZoneInfo

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            control = conn.execute(
                text("SELECT timezone, closed_through FROM chronicler.daily_repair_control")
            ).one()
        timezone = control.timezone
        day = control.closed_through - timedelta(days=40)
        day28, day29 = day + timedelta(days=28), day + timedelta(days=29)
        source_id, subject_id, object_id = uuid4(), uuid4(), uuid4()
        source_start = datetime.combine(
            day, datetime.min.time().replace(hour=12), tzinfo=ZoneInfo(timezone)
        ).astimezone(UTC)
        detail = {"dark_sources": ["steam.play_history"]}
        lane_by_name = {
            "active_d": "work",
            "terminal_d": "work",
            "terminal_d28": "social",
            "active_d29": "play",
            "owner_d": "eat",
            "owner_device_d28": "rest",
            "lookalike_d28": "travel",
        }
        cases = [
            ("active_d", day, "lane-skew", "chronicler", "system", "active"),
            ("terminal_d", day, "lane-skew", "chronicler", "system", "retracted"),
            ("fading_d28", day28, "sleep-debt", "chronicler", "system", "fading"),
            ("terminal_d28", day28, "lane-skew", "chronicler", "system", "retracted"),
            ("active_d29", day29, "lane-skew", "chronicler", "system", "active"),
            ("owner_d", day, "lane-skew", "chronicler", "owner", "active"),
            ("owner_device_d28", day28, "lane-skew", "chronicler", "owner_device", "active"),
            ("lookalike_d28", day28, "lane-skew", "relationship", "system", "active"),
            (
                "reminder_d",
                day,
                "revisit-low-confidence-block",
                "chronicler",
                "system",
                "active",
            ),
        ]
        fact_ids = {name: uuid4() for name, *_ in cases}
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, name in (
                (subject_id, f"boundary-subject-{subject_id}"),
                (object_id, f"boundary-object-{object_id}"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, :name, 'other')"
                    ),
                    {"id": entity_id, "name": name},
                )
            conn.execute(
                text("""INSERT INTO chronicler.episodes
                    (id, source_name, source_ref, episode_type, start_at, end_at, layer, title)
                    VALUES (:id, 'steam.play_history', 'boundary-source', 'play_episode',
                            :start_at, :end_at, 'evidence', 'before correction')"""),
                {
                    "id": source_id,
                    "start_at": source_start,
                    "end_at": source_start + timedelta(hours=1),
                },
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_days
                    (local_date, timezone, dirty_generation, built_generation,
                     totals_pending, outlier_pending, facts_pending)
                    VALUES (:day, :timezone, 17, 17, FALSE, FALSE, FALSE)"""),
                {"day": day29, "timezone": timezone},
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_rollup_flags
                    (local_date, flag_type, severity, detail, narrative)
                    VALUES (:day, 'feeder_dark', 'warning', CAST(:detail AS jsonb), 'keep reminder')"""),
                {"day": day, "detail": json.dumps(detail)},
            )
            for name, local_day, predicate, source_butler, authority, validity in cases:
                if predicate == "sleep-debt":
                    subject = "sleep"
                    metadata = {
                        "source": "chronicler",
                        "as_of": local_day.isoformat(),
                        "window_days": 28,
                        "debt_seconds": 3600,
                        "shortfall_days": 1,
                    }
                    tags = ["chronicler-insight", "sleep-debt"]
                elif predicate == "revisit-low-confidence-block":
                    subject = f"{local_day.isoformat()} feeder_dark"
                    metadata = {
                        "source": "chronicler",
                        "local_date": local_day.isoformat(),
                        "flag_type": "feeder_dark",
                        "severity": "warning",
                        "detail": detail,
                    }
                    tags = ["chronicler-self-reminder", "feeder_dark"]
                else:
                    lane = lane_by_name[name]
                    subject = f"{lane} lane"
                    metadata = {
                        "source": "chronicler",
                        "local_date": local_day.isoformat(),
                        "lane": lane,
                        "direction": "down",
                        "seconds": 1,
                        "baseline_seconds": 2,
                        "delta_seconds": -1,
                        "baseline_sample_days": 14,
                    }
                    tags = ["chronicler-insight", "lane-skew", lane]
                terminal = validity == "retracted"
                conn.execute(
                    text("""INSERT INTO chronicler_mem.facts
                        (id, subject, predicate, content, confidence, source_butler,
                         content_authority, validity, invalid_at, tags, metadata)
                        VALUES (:id, :subject, :predicate, :content, :confidence, :source,
                                :authority, :validity, :invalid_at,
                                CAST(:tags AS jsonb), CAST(:metadata AS jsonb))"""),
                    {
                        "id": fact_ids[name],
                        "subject": subject,
                        "predicate": predicate,
                        "content": f"historical {name}",
                        "confidence": 0 if terminal else 0.9,
                        "source": source_butler,
                        "authority": authority,
                        "validity": validity,
                        "invalid_at": datetime.now(UTC) if terminal else None,
                        "tags": json.dumps(tags),
                        "metadata": json.dumps(metadata),
                    },
                )
                conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
                conn.execute(
                    text("""INSERT INTO public.memory_catalog
                        (source_schema, source_table, source_id, summary, title, confidence)
                        VALUES ('chronicler_mem', 'facts', :id, :summary, :title, :confidence)"""),
                    {
                        "id": fact_ids[name],
                        "summary": f"summary {name}",
                        "title": f"title {name}",
                        "confidence": 0 if terminal else 0.9,
                    },
                )
                conn.execute(
                    text("""INSERT INTO public.entity_graph_edges
                        (source_schema, source_table, source_id, subject_entity_id,
                         predicate, object_entity_id)
                        VALUES ('chronicler_mem', 'facts', :id, :subject, 'related_to', :object)"""),
                    {"id": fact_ids[name], "subject": subject_id, "object": object_id},
                )
                conn.exec_driver_sql("RESET ROLE")
            for local_day, key in ((day, "d"), (day28, "d28"), (day29, "d29")):
                conn.execute(
                    text("""INSERT INTO chronicler.daily_repair_archive
                        (local_date, generation, row_kind, row_key, audit_hash, content,
                         dependency_ids, text_access)
                        VALUES (:day, 1, 'boundary', :key, 'ordinary-audit',
                                CAST('{"historical":"ordinary text"}' AS jsonb),
                                ARRAY[CAST(:source_id AS uuid)], 'audit_only')"""),
                    {"day": local_day, "key": key, "source_id": source_id},
                )

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public"},
                init=runtime_connection,
            )
            try:
                await pool.execute(
                    "UPDATE chronicler.episodes SET title='corrected source' WHERE id=$1", source_id
                )
                for name in ("active_d", "terminal_d", "fading_d28", "terminal_d28"):
                    local_day = day if name.endswith("_d") else day28
                    disposition = await pool.fetchrow(
                        """SELECT disposition, redacted, dependency_ids FROM
                           chronicler.daily_repair_fact_dispositions
                           WHERE local_date=$1 AND fact_id=$2 ORDER BY generation DESC LIMIT 1""",
                        local_day,
                        fact_ids[name],
                    )
                    assert disposition is not None
                    assert (disposition["disposition"], disposition["redacted"]) == (
                        "disposed",
                        False,
                    )
                    assert source_id in disposition["dependency_ids"]
                    fact = await pool.fetchrow(
                        "SELECT validity, confidence, invalid_at, content FROM chronicler_mem.facts WHERE id=$1",
                        fact_ids[name],
                    )
                    assert fact["validity"] == "retracted" and fact["confidence"] == 0
                    assert (
                        fact["invalid_at"] is not None and fact["content"] == f"historical {name}"
                    )
                    catalog = await pool.fetchrow(
                        """SELECT summary, title, confidence, invalid_at FROM public.memory_catalog
                           WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1""",
                        fact_ids[name],
                    )
                    assert (catalog["summary"], catalog["title"]) == (
                        f"summary {name}",
                        f"title {name}",
                    )
                    assert catalog["confidence"] == 0 and catalog["invalid_at"] is not None
                    assert not await pool.fetchval(
                        """SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges
                           WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1)""",
                        fact_ids[name],
                    )

                kept = await pool.fetchrow(
                    """SELECT disposition, redacted FROM chronicler.daily_repair_fact_dispositions
                       WHERE local_date=$1 AND fact_id=$2 ORDER BY generation DESC LIMIT 1""",
                    day,
                    fact_ids["reminder_d"],
                )
                assert kept == ("kept", False)
                for name in (
                    "owner_d",
                    "owner_device_d28",
                    "lookalike_d28",
                    "active_d29",
                    "reminder_d",
                ):
                    local_day = (
                        day29
                        if name == "active_d29"
                        else day28
                        if name in {"owner_device_d28", "lookalike_d28"}
                        else day
                    )
                    fact = await pool.fetchrow(
                        "SELECT subject, content, validity, confidence, invalid_at "
                        "FROM chronicler_mem.facts WHERE id=$1",
                        fact_ids[name],
                    )
                    assert fact["subject"] == (
                        f"{local_day.isoformat()} feeder_dark"
                        if name == "reminder_d"
                        else f"{lane_by_name[name]} lane"
                    )
                    assert fact["content"] == f"historical {name}"
                    assert fact["validity"] == "active" and fact["confidence"] == 0.9
                    assert fact["invalid_at"] is None
                    assert await pool.fetchval(
                        """SELECT EXISTS (SELECT 1 FROM public.memory_catalog
                           WHERE source_schema='chronicler_mem' AND source_table='facts'
                             AND source_id=$1 AND confidence=0.9 AND invalid_at IS NULL)""",
                        fact_ids[name],
                    )
                    assert await pool.fetchval(
                        """SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges
                           WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1)""",
                        fact_ids[name],
                    )
                    if name != "reminder_d":
                        assert not await pool.fetchval(
                            """SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_fact_dispositions
                               WHERE local_date=$1 AND fact_id=$2)""",
                            local_day,
                            fact_ids[name],
                        )
                assert await pool.fetchval(
                    "SELECT dirty_generation=17 AND NOT facts_pending FROM chronicler.daily_repair_days WHERE local_date=$1",
                    day29,
                )
                assert await pool.fetchval(
                    "SELECT count(*)=3 FROM chronicler.daily_repair_archive WHERE $1=ANY(dependency_ids) "
                    "AND content IS NOT NULL AND audit_hash IS NOT NULL AND text_access='audit_only'",
                    source_id,
                )
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_mandatory_projection_failure_rolls_back_source_and_fact_disposition(postgres_container):
    """A real shared projection error rolls back the earlier canonical mutation."""
    import json
    from zoneinfo import ZoneInfo

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            control = conn.execute(
                text("SELECT timezone, closed_through FROM chronicler.daily_repair_control")
            ).one()
        timezone = control.timezone
        day = control.closed_through - timedelta(days=40)
        source_id, fact_id, subject_id, object_id = uuid4(), uuid4(), uuid4(), uuid4()
        start = datetime.combine(
            day, datetime.min.time().replace(hour=12), tzinfo=ZoneInfo(timezone)
        ).astimezone(UTC)
        metadata = {
            "source": "chronicler",
            "local_date": day.isoformat(),
            "lane": "work",
            "direction": "down",
            "seconds": 1,
            "baseline_seconds": 2,
            "delta_seconds": -1,
            "baseline_sample_days": 14,
        }
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, name in ((subject_id, "fault-subject"), (object_id, "fault-object")):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, :name, 'other')"
                    ),
                    {"id": entity_id, "name": name},
                )
            conn.execute(
                text("""INSERT INTO chronicler.episodes
                    (id, source_name, source_ref, episode_type, start_at, end_at, layer, title)
                    VALUES (:id, 'steam.play_history', 'projection-fault', 'play_episode',
                            :start, :end, 'evidence', 'before failure')"""),
                {"id": source_id, "start": start, "end": start + timedelta(hours=1)},
            )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, confidence, source_butler,
                     content_authority, validity, tags, metadata)
                    VALUES (:id, 'work lane', 'lane-skew', 'private insight', 0.9,
                            'chronicler', 'system', 'active',
                            '["chronicler-insight","lane-skew","work"]'::jsonb,
                            CAST(:metadata AS jsonb))"""),
                {"id": fact_id, "metadata": json.dumps(metadata)},
            )
            conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
            conn.execute(
                text("""INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, summary, title, confidence)
                    VALUES ('chronicler_mem', 'facts', :id, 'private summary', 'private title', 0.9)"""),
                {"id": fact_id},
            )
            conn.execute(
                text("""INSERT INTO public.entity_graph_edges
                    (source_schema, source_table, source_id, subject_entity_id,
                     predicate, object_entity_id)
                    VALUES ('chronicler_mem', 'facts', :id, :subject, 'related_to', :object)"""),
                {"id": fact_id, "subject": subject_id, "object": object_id},
            )
            conn.exec_driver_sql("RESET ROLE")
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES (:day, 1, 'fault-test', 'rollback-copy', 'keep-hash',
                            '{"title":"keep ordinary text"}'::jsonb,
                            ARRAY[CAST(:source AS uuid)], 'audit_only')"""),
                {"day": day, "source": source_id},
            )
            failure_function_sql = f"""CREATE OR REPLACE FUNCTION public.f1_force_projection_failure()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.source_id = '{fact_id}'::uuid THEN
                        RAISE EXCEPTION 'forced projection failure';
                    END IF;
                    RETURN NEW;
                END $$"""
            conn.execute(text(failure_function_sql))
            conn.execute(
                text("""CREATE TRIGGER f1_force_projection_failure
                BEFORE UPDATE ON public.memory_catalog FOR EACH ROW
                EXECUTE FUNCTION public.f1_force_projection_failure()""")
            )

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public"},
                init=runtime_connection,
            )
            try:
                before_source = await pool.fetchrow(
                    "SELECT title FROM chronicler.episodes WHERE id=$1", source_id
                )
                before_fact = await pool.fetchrow(
                    "SELECT validity, confidence, invalid_at, content FROM chronicler_mem.facts WHERE id=$1",
                    fact_id,
                )
                before_catalog = await pool.fetchrow(
                    """SELECT summary, title, confidence, invalid_at FROM public.memory_catalog
                       WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1""",
                    fact_id,
                )
                before_day = await pool.fetchrow(
                    """SELECT dirty_generation, built_generation, totals_pending,
                              outlier_pending, facts_pending FROM chronicler.daily_repair_days
                       WHERE local_date=$1""",
                    day,
                )
                before_epoch = await pool.fetchval(
                    "SELECT replay_epoch FROM chronicler.daily_repair_control WHERE singleton"
                )
                before_dispositions = await pool.fetch(
                    """SELECT generation, row_key, disposition, fact_id, redacted
                       FROM chronicler.daily_repair_fact_dispositions WHERE local_date=$1 ORDER BY generation,row_key""",
                    day,
                )
                with pytest.raises(asyncpg.PostgresError, match="forced projection failure"):
                    await pool.execute(
                        "UPDATE chronicler.episodes SET title='failed correction' WHERE id=$1",
                        source_id,
                    )
                assert (
                    await pool.fetchrow(
                        "SELECT title FROM chronicler.episodes WHERE id=$1", source_id
                    )
                    == before_source
                )
                assert (
                    await pool.fetchrow(
                        "SELECT validity, confidence, invalid_at, content FROM chronicler_mem.facts WHERE id=$1",
                        fact_id,
                    )
                    == before_fact
                )
                assert (
                    await pool.fetchrow(
                        """SELECT summary, title, confidence, invalid_at FROM public.memory_catalog
                       WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1""",
                        fact_id,
                    )
                    == before_catalog
                )
                assert await pool.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges
                       WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1)""",
                    fact_id,
                )
                assert (
                    await pool.fetchrow(
                        """SELECT dirty_generation, built_generation, totals_pending,
                              outlier_pending, facts_pending FROM chronicler.daily_repair_days
                       WHERE local_date=$1""",
                        day,
                    )
                    == before_day
                )
                assert (
                    await pool.fetchval(
                        "SELECT replay_epoch FROM chronicler.daily_repair_control WHERE singleton"
                    )
                    == before_epoch
                )
                assert (
                    await pool.fetch(
                        """SELECT generation, row_key, disposition, fact_id, redacted
                       FROM chronicler.daily_repair_fact_dispositions WHERE local_date=$1 ORDER BY generation,row_key""",
                        day,
                    )
                    == before_dispositions
                )
                assert await pool.fetchval(
                    """SELECT count(*)=1 FROM chronicler.daily_repair_archive
                       WHERE $1=ANY(dependency_ids) AND content IS NOT NULL
                         AND audit_hash='keep-hash' AND text_access='audit_only'""",
                    source_id,
                )

                # The test migration login owns the injected DDL; runtime
                # must not receive trigger/function drop privileges.
                with engine.begin() as conn:
                    conn.execute(
                        text("DROP TRIGGER f1_force_projection_failure ON public.memory_catalog")
                    )
                    conn.execute(text("DROP FUNCTION public.f1_force_projection_failure()"))
                await pool.execute(
                    "UPDATE chronicler.episodes SET title='successful retry' WHERE id=$1", source_id
                )
                fact = await pool.fetchrow(
                    "SELECT validity, confidence, invalid_at, content FROM chronicler_mem.facts WHERE id=$1",
                    fact_id,
                )
                assert fact["validity"] == "retracted" and fact["confidence"] == 0
                assert fact["invalid_at"] is not None and fact["content"] == "private insight"
                catalog = await pool.fetchrow(
                    """SELECT summary, title, confidence, invalid_at FROM public.memory_catalog
                       WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1""",
                    fact_id,
                )
                assert (catalog["summary"], catalog["title"]) == (
                    "private summary",
                    "private title",
                )
                assert catalog["confidence"] == 0 and catalog["invalid_at"] is not None
                assert not await pool.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges
                       WHERE source_schema='chronicler_mem' AND source_table='facts' AND source_id=$1)""",
                    fact_id,
                )
                assert await pool.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_fact_dispositions
                       WHERE local_date=$1 AND fact_id=$2 AND disposition='disposed' AND NOT redacted)""",
                    day,
                    fact_id,
                )
            finally:
                await pool.close()

        try:
            asyncio.run(scenario())
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DROP TRIGGER IF EXISTS f1_force_projection_failure ON public.memory_catalog"
                    )
                )
                conn.execute(text("DROP FUNCTION IF EXISTS public.f1_force_projection_failure()"))
    finally:
        engine.dispose()


def test_admission_deadline_never_cancels_the_commit_after_it(postgres_container, monkeypatch):
    """The deadline cancelling asyncpg's shared cancel future must not reach the next statement.

    Order forced, not timed: a statement's own asyncpg timer fires first, so the savepoint
    rollback waits on the protocol's ``cancel_waiter``, and the admission deadline expires
    during that wait. ``task.cancel()`` cancels that shared future. The outer COMMIT then
    awaits the cancelled future and raises ``CancelledError`` though no task was cancelled.
    With one client timer the sequence cannot start, and the hook here is never reached.
    """
    import butlers.chronicler.history_repair as history_repair

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    monkeypatch.setattr(history_repair, "MAX_RETENTION_ADMISSION_TIMEOUT_MS", 500)
    deadlines: list[asyncio.Timeout] = []
    real_timeout_at = asyncio.timeout_at

    def only_expired_by_the_hook(when: float) -> asyncio.Timeout:
        deadlines.append(real_timeout_at(when + 3600))
        return deadlines[-1]

    class ExpiresDeadlineDuringCancel(asyncpg.Connection):
        def _cancel_current_command(self, waiter):
            super()._cancel_current_command(waiter)
            # Two turns: past the task's wakeup, which suspends it on ``cancel_waiter``.
            loop = asyncio.get_running_loop()
            loop.call_soon(loop.call_soon, deadlines[-1].reschedule, loop.time())

    async def runtime_connection(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE butler_chronicler_rw")

    async def scenario():
        pool = await asyncpg.create_pool(
            url,
            server_settings={"search_path": "chronicler, public", "statement_timeout": "0"},
            init=runtime_connection,
            connection_class=ExpiresDeadlineDuringCancel,
        )
        holder = await asyncpg.connect(url)
        try:
            # Parks admission's first read on a real lock until a timer ends it.
            blocking = holder.transaction()
            await blocking.start()
            await holder.execute(
                "LOCK TABLE chronicler.daily_repair_retention_spans IN ACCESS EXCLUSIVE MODE"
            )
            with patch.object(asyncio, "timeout_at", only_expired_by_the_hook):
                result = await history_repair.run_rebuild(pool, now=datetime.now(UTC))
            await blocking.rollback()
            assert result["status"] == "pending_retention"
            assert result["retention_admission_timeout"] is True
            assert await pool.fetchval("SELECT 1") == 1
        finally:
            await holder.close()
            await pool.close()

    asyncio.run(scenario())


def test_retention_first_admission_has_total_deadline_and_read_barrier(postgres_container):
    """Admission timeout rolls source cleanup back; expiry still holds readers closed."""
    import time
    from zoneinfo import ZoneInfo

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    # Model an expired legacy source that predates migration 030's insert trigger.
    start = (now - timedelta(days=75)).replace(hour=8, minute=0, second=0)
    end = start + timedelta(days=35)
    future = now + timedelta(days=2)
    retention_days = 1
    local_day = start.astimezone(ZoneInfo("Asia/Singapore")).date()
    source_id, fact_id = uuid4(), uuid4()
    subject_id, object_id = uuid4(), uuid4()
    engine = create_engine(url)
    catalog_id = edge_id = None
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, canonical_name in (
                (subject_id, "admission-source-subject"),
                (object_id, "admission-source-object"),
            ):
                conn.execute(
                    text("""INSERT INTO public.entities
                        (id, canonical_name, entity_type)
                        VALUES (:id, :name, 'other')"""),
                    {"id": entity_id, "name": canonical_name},
                )
            # Seed the legacy expired row without its normal AFTER trigger. The
            # production retention scanner and all downstream reads still run normally.
            conn.exec_driver_sql(
                "ALTER TABLE chronicler.episodes DISABLE TRIGGER episodes_daily_repair"
            )
            try:
                conn.execute(
                    text("""INSERT INTO chronicler.episodes
                        (id, source_name, source_ref, episode_type, start_at, end_at,
                         layer, title, retention_days)
                        VALUES (:id, 'steam.play_history', 'admission-deadline',
                                'play_episode', :start_at, :end_at, 'evidence',
                                'private source title', :retention_days)"""),
                    {
                        "id": source_id,
                        "start_at": start,
                        "end_at": end,
                        "retention_days": retention_days,
                    },
                )
            finally:
                conn.exec_driver_sql(
                    "ALTER TABLE chronicler.episodes ENABLE TRIGGER episodes_daily_repair"
                )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, confidence, source_butler,
                     content_authority, validity, tags, metadata)
                    VALUES (:id, 'work lane', 'lane-skew', 'private insight', 0.9,
                            'chronicler', 'system', 'active',
                            '["chronicler-insight", "lane-skew", "work"]'::jsonb,
                            CAST(:metadata AS jsonb))"""),
                {
                    "id": fact_id,
                    "metadata": (
                        '{"source":"chronicler","local_date":"'
                        + local_day.isoformat()
                        + '","lane":"work","direction":"down","seconds":1,'
                        '"baseline_seconds":2,"delta_seconds":-1,"baseline_sample_days":14}'
                    ),
                },
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_rollups
                    (local_date, lane, seconds, episode_count, timezone)
                    VALUES (:day, 'play', 3600, 1, 'Asia/Singapore')"""),
                {"day": local_day},
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES (:day, 1, 'test', 'admission-deadline-copy', 'private-hash',
                            CAST('{"title":"private source"}' AS jsonb),
                            ARRAY[CAST(:source_id AS uuid)], 'audit_only')"""),
                {"day": local_day, "source_id": source_id},
            )
            conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
            catalog_id = conn.execute(
                text("""INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, summary, title, confidence)
                    VALUES ('chronicler_mem', 'facts', :id, 'private summary',
                            'private title', 0.9) RETURNING id"""),
                {"id": fact_id},
            ).scalar_one()
            edge_id = conn.execute(
                text("""INSERT INTO public.entity_graph_edges
                    (source_schema, source_table, source_id, subject_entity_id,
                     predicate, object_entity_id)
                    VALUES ('chronicler_mem', 'facts', :id, :subject,
                            'related_to', :object) RETURNING id"""),
                {"id": fact_id, "subject": subject_id, "object": object_id},
            ).scalar_one()

            conn.exec_driver_sql("RESET ROLE")
            conn.exec_driver_sql("""
                CREATE OR REPLACE FUNCTION chronicler.test_admission_delay()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM pg_sleep(3.0);
                    RETURN NEW;
                END $$
            """)
            conn.exec_driver_sql("""
                CREATE TRIGGER test_admission_delay_redaction
                BEFORE INSERT ON chronicler.daily_repair_source_redactions
                FOR EACH ROW EXECUTE FUNCTION chronicler.test_admission_delay()
            """)
            conn.exec_driver_sql("""
                CREATE TRIGGER test_admission_delay_span
                BEFORE INSERT ON chronicler.daily_repair_retention_spans
                FOR EACH ROW EXECUTE FUNCTION chronicler.test_admission_delay()
            """)

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            from butlers.chronicler.history_repair import (
                RepairAdmissionError,
                capture_fact_write_token,
                pending_numeric_repair_dates,
                run_rebuild,
                try_shared_lock,
            )

            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public", "statement_timeout": "0"},
                init=runtime_connection,
            )
            try:
                closed_through = future.astimezone(ZoneInfo("Asia/Singapore")).date() - timedelta(
                    days=1
                )
                await pool.execute(
                    "UPDATE daily_repair_control SET replay_ack=replay_epoch, closed_through=$1 "
                    "WHERE singleton",
                    closed_through,
                )
                await pool.execute(
                    """UPDATE chronicler.daily_repair_days
                       SET built_generation=dirty_generation, totals_pending=FALSE,
                           outlier_pending=FALSE, facts_pending=FALSE
                       WHERE local_date BETWEEN $1 AND $2""",
                    local_day - timedelta(days=28),
                    local_day,
                )
                eligible = await pool.fetchval(
                    """WITH control AS (
                           SELECT timezone, closed_through
                           FROM chronicler.daily_repair_control WHERE singleton
                       )
                       SELECT EXISTS (
                           SELECT 1
                           FROM chronicler.episodes AS source
                           JOIN chronicler.v_episodes_corrected AS effective
                             ON effective.id = source.id
                           CROSS JOIN control
                           WHERE source.id = $1
                             AND source.retention_days IS NOT NULL
                             AND source.start_at + make_interval(days => source.retention_days) < $2
                             AND source.start_at + make_interval(days => source.retention_days) < now()
                             AND source.tombstone_at IS NULL
                             AND source.privacy IS DISTINCT FROM 'restricted'
                             AND effective.tombstone_at IS NULL
                             AND effective.privacy IS DISTINCT FROM 'restricted'
                             AND (source.start_at AT TIME ZONE control.timezone)::date
                                 <= control.closed_through
                             AND NOT EXISTS (
                                 SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                                 WHERE redaction.source_id = source.id
                             )
                       )""",
                    source_id,
                    future,
                )
                assert eligible is True, "fixture source must match production retention discovery"
                started = time.monotonic()
                first = await run_rebuild(pool, now=future)
                elapsed = time.monotonic() - started
                # Both individually sub-cap trigger statements must execute; their cumulative
                # delay proves the admission deadline covers the whole transaction.
                assert 5.0 <= elapsed < 7.0
                assert first["status"] == "pending_retention"
                assert first["retention_admission_timeout"] is True
                assert await pool.fetchval("SELECT current_setting('statement_timeout')") == "0"

                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_source_redactions "
                    "WHERE source_id=$1)",
                    source_id,
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_retention_spans "
                    "WHERE source_id=$1)",
                    source_id,
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_fact_dispositions "
                    "WHERE fact_id=$1)",
                    fact_id,
                )
                fact = await pool.fetchrow(
                    "SELECT validity, content FROM chronicler_mem.facts WHERE id=$1", fact_id
                )
                assert (fact["validity"], fact["content"]) == ("active", "private insight")
                catalog = await pool.fetchrow(
                    "SELECT summary, title, confidence, invalid_at FROM public.memory_catalog "
                    "WHERE id=$1",
                    catalog_id,
                )
                assert (catalog["summary"], catalog["title"], catalog["confidence"]) == (
                    "private summary",
                    "private title",
                    0.9,
                )
                assert catalog["invalid_at"] is None
                assert await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges WHERE id=$1)", edge_id
                )
                archive = await pool.fetchrow(
                    "SELECT content, audit_hash, text_access FROM chronicler.daily_repair_archive "
                    "WHERE $1=ANY(dependency_ids)",
                    source_id,
                )
                assert archive["content"] == {"title": "private source"}
                assert archive["audit_hash"] == "private-hash"
                assert archive["text_access"] == "audit_only"

                # The source is expired even though the whole admission rolled back.
                assert not await pool.fetchval(
                    "SELECT public.chronicler_catalog_row_visible($1)", catalog_id
                )
                assert not await pool.fetchval(
                    "SELECT public.chronicler_graph_row_visible($1)", edge_id
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        assert await try_shared_lock(conn)
                        pending = await pending_numeric_repair_dates(
                            conn, start_date=local_day, end_date=local_day + timedelta(days=100)
                        )
                assert local_day in pending
                from butlers.roster_imports import roster_module
                from butlers.testing.db_manager import db_manager

                chronicler_api = roster_module("chronicler", "api/router")
                database = db_manager({"chronicler": pool})
                affected = await chronicler_api.get_rollups(
                    rollup_date=local_day, start_date=None, end_date=None, db=database
                )
                assert affected.data.days[0].status == "pending_repair"
                assert affected.data.days[0].lanes == []
                with pytest.raises(RepairAdmissionError, match="retention repair is pending"):
                    await capture_fact_write_token(
                        pool,
                        day_date=local_day,
                        timezone="Asia/Singapore",
                        baseline_lookback_days=28,
                    )

                # Remove only the injected delay and retry the same real source.
                with engine.begin() as admin:
                    admin.exec_driver_sql(
                        "DROP TRIGGER test_admission_delay_redaction "
                        "ON chronicler.daily_repair_source_redactions"
                    )
                    admin.exec_driver_sql(
                        "DROP TRIGGER test_admission_delay_span "
                        "ON chronicler.daily_repair_retention_spans"
                    )
                    admin.exec_driver_sql("DROP FUNCTION chronicler.test_admission_delay()")
                retry = await run_rebuild(pool, now=future)
                assert retry["status"] == "pending_retention"
                assert await pool.fetchval(
                    "SELECT reason_code='source_retention' FROM chronicler.daily_repair_source_redactions "
                    "WHERE source_id=$1",
                    source_id,
                )
                assert await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_retention_spans "
                    "WHERE source_id=$1)",
                    source_id,
                )
                redacted_fact = await pool.fetchrow(
                    "SELECT validity, content FROM chronicler_mem.facts WHERE id=$1", fact_id
                )
                assert redacted_fact["validity"] == "retracted"
                assert redacted_fact["content"] == "[removed by source privacy correction]"
                redacted_catalog = await pool.fetchrow(
                    "SELECT summary, title, confidence, invalid_at FROM public.memory_catalog "
                    "WHERE id=$1",
                    catalog_id,
                )
                assert (
                    redacted_catalog["summary"],
                    redacted_catalog["title"],
                    redacted_catalog["confidence"],
                ) == ("", None, 0)
                assert redacted_catalog["invalid_at"] is not None
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges WHERE id=$1)", edge_id
                )
                redacted_archive = await pool.fetchrow(
                    "SELECT content, audit_hash, text_access FROM chronicler.daily_repair_archive "
                    "WHERE $1=ANY(dependency_ids)",
                    source_id,
                )
                assert redacted_archive["content"] is None
                assert redacted_archive["audit_hash"] is None
                assert redacted_archive["text_access"] == "redacted"
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "DROP TRIGGER IF EXISTS test_admission_delay_redaction "
                "ON chronicler.daily_repair_source_redactions"
            )
            conn.exec_driver_sql(
                "DROP TRIGGER IF EXISTS test_admission_delay_span "
                "ON chronicler.daily_repair_retention_spans"
            )
            conn.exec_driver_sql("DROP FUNCTION IF EXISTS chronicler.test_admission_delay()")
        engine.dispose()


def test_retention_first_admission_respects_stricter_existing_statement_timeout(
    postgres_container,
):
    """A strict session GUC cancels admission without serving stale private copies."""
    import time
    from zoneinfo import ZoneInfo

    from butlers.chronicler.history_repair import RepairAdmissionError, capture_fact_write_token

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=10)).replace(hour=3, minute=0, second=0)
    end = start + timedelta(hours=2)
    future = now + timedelta(days=1)
    local_day = start.astimezone(ZoneInfo("Asia/Singapore")).date()
    source_id, fact_id = uuid4(), uuid4()
    subject_id, object_id = uuid4(), uuid4()
    engine = create_engine(url)
    catalog_id = edge_id = None
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            for entity_id, name in (
                (subject_id, "strict-admission-subject"),
                (object_id, "strict-admission-object"),
            ):
                conn.execute(
                    text("""INSERT INTO public.entities
                        (id, canonical_name, entity_type)
                        VALUES (:id, :name, 'other')"""),
                    {"id": entity_id, "name": name},
                )
            # This is an old source row whose trigger predates admission. The real
            # retention scanner and source correction triggers remain active.
            conn.exec_driver_sql(
                "ALTER TABLE chronicler.episodes DISABLE TRIGGER episodes_daily_repair"
            )
            try:
                conn.execute(
                    text("""INSERT INTO chronicler.episodes
                        (id, source_name, source_ref, episode_type, start_at, end_at,
                         layer, title, retention_days)
                        VALUES (:id, 'steam.play_history', 'strict-admission-timeout',
                                'play_episode', :start, :end, 'evidence',
                                'private source title', 1)"""),
                    {"id": source_id, "start": start, "end": end},
                )
            finally:
                conn.exec_driver_sql(
                    "ALTER TABLE chronicler.episodes ENABLE TRIGGER episodes_daily_repair"
                )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, confidence, source_butler,
                     content_authority, validity, tags, metadata)
                    VALUES (:id, 'work lane', 'lane-skew', 'private insight', 0.9,
                            'chronicler', 'system', 'active',
                            '["chronicler-insight", "lane-skew", "work"]'::jsonb,
                            CAST(:metadata AS jsonb))"""),
                {
                    "id": fact_id,
                    "metadata": (
                        '{"source":"chronicler","local_date":"'
                        + local_day.isoformat()
                        + '","lane":"work","direction":"down","seconds":1,'
                        '"baseline_seconds":2,"delta_seconds":-1,"baseline_sample_days":14}'
                    ),
                },
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_rollups
                    (local_date, lane, seconds, episode_count, timezone)
                    VALUES (:day, 'play', 3600, 1, 'Asia/Singapore')"""),
                {"day": local_day},
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES (:day, 1, 'test', 'strict-admission-copy', 'private-hash',
                            CAST('{"title":"private source"}' AS jsonb),
                            ARRAY[CAST(:source_id AS uuid)], 'audit_only')"""),
                {"day": local_day, "source_id": source_id},
            )
            conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
            catalog_id = conn.execute(
                text("""INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, summary, title, confidence)
                    VALUES ('chronicler_mem', 'facts', :id, 'private summary',
                            'private title', 0.9) RETURNING id"""),
                {"id": fact_id},
            ).scalar_one()
            edge_id = conn.execute(
                text("""INSERT INTO public.entity_graph_edges
                    (source_schema, source_table, source_id, subject_entity_id,
                     predicate, object_entity_id)
                    VALUES ('chronicler_mem', 'facts', :id, :subject,
                            'related_to', :object) RETURNING id"""),
                {"id": fact_id, "subject": subject_id, "object": object_id},
            ).scalar_one()
            conn.exec_driver_sql("RESET ROLE")
            conn.exec_driver_sql("""
                CREATE OR REPLACE FUNCTION chronicler.f2_strict_admission_delay()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM pg_sleep(3.0);
                    RETURN NEW;
                END $$
            """)
            conn.exec_driver_sql("""
                CREATE TRIGGER f2_strict_admission_delay
                BEFORE INSERT ON chronicler.daily_repair_source_redactions
                FOR EACH ROW EXECUTE FUNCTION chronicler.f2_strict_admission_delay()
            """)

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            from butlers.chronicler.history_repair import run_rebuild
            from butlers.roster_imports import roster_module
            from butlers.testing.db_manager import db_manager

            app_name = f"f1-strict-admission-{uuid4().hex}"
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                min_size=1,
                max_size=1,
                server_settings={
                    "search_path": "chronicler, public",
                    "statement_timeout": "2s",
                    "application_name": app_name,
                },
                init=runtime_connection,
            )
            observer = None
            try:
                observer = await asyncpg.connect(
                    url.replace("postgresql+psycopg2://", "postgresql://"),
                    server_settings={"search_path": "chronicler, public"},
                )
                await pool.execute(
                    "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
                )
                started = time.monotonic()
                task = asyncio.create_task(run_rebuild(pool, now=future))
                sleep_witness = False
                poll_until = asyncio.get_running_loop().time() + 4.0
                while not task.done() and asyncio.get_running_loop().time() < poll_until:
                    sleep_witness = await observer.fetchval(
                        """SELECT EXISTS (
                               SELECT 1 FROM pg_catalog.pg_stat_activity
                               WHERE application_name=$1 AND state='active'
                                 AND wait_event='PgSleep'
                           )""",
                        app_name,
                    )
                    if sleep_witness:
                        break
                    await asyncio.sleep(0.05)
                first = await task
                elapsed = time.monotonic() - started
                print(f"F1_STRICT_ADMISSION_ELAPSED_SECONDS={elapsed:.3f}")
                assert sleep_witness is True, "the real first-admission delay trigger must execute"
                assert first["status"] == "pending_retention"
                assert first["retention_admission_timeout"] is True
                assert elapsed < 5.0, "the stricter 2s GUC must win within the 5s total cap"
                assert await pool.fetchval("SELECT current_setting('statement_timeout')") == "2s"
                assert (
                    await pool.fetchval(
                        "SELECT setting::bigint FROM pg_catalog.pg_settings "
                        "WHERE name='statement_timeout'"
                    )
                    == 2_000
                )

                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_source_redactions "
                    "WHERE source_id=$1)",
                    source_id,
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_retention_spans "
                    "WHERE source_id=$1)",
                    source_id,
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_fact_dispositions "
                    "WHERE fact_id=$1)",
                    fact_id,
                )
                source = await pool.fetchrow(
                    "SELECT title, privacy, tombstone_at FROM chronicler.episodes WHERE id=$1",
                    source_id,
                )
                assert (source["title"], source["privacy"], source["tombstone_at"]) == (
                    "private source title",
                    "normal",
                    None,
                )
                fact = await pool.fetchrow(
                    "SELECT validity, content FROM chronicler_mem.facts WHERE id=$1", fact_id
                )
                assert (fact["validity"], fact["content"]) == ("active", "private insight")
                catalog = await pool.fetchrow(
                    "SELECT summary, title, confidence, invalid_at "
                    "FROM public.memory_catalog WHERE id=$1",
                    catalog_id,
                )
                assert (catalog["summary"], catalog["title"], catalog["confidence"]) == (
                    "private summary",
                    "private title",
                    0.9,
                )
                assert catalog["invalid_at"] is None
                assert await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges WHERE id=$1)", edge_id
                )
                archive = await pool.fetchrow(
                    "SELECT content, audit_hash, text_access "
                    "FROM chronicler.daily_repair_archive WHERE $1=ANY(dependency_ids)",
                    source_id,
                )
                assert archive["content"] == {"title": "private source"}
                assert archive["audit_hash"] == "private-hash"
                assert archive["text_access"] == "audit_only"

                # Expiry remains an independent read barrier while admission rolls back.
                assert not await pool.fetchval(
                    "SELECT public.chronicler_catalog_row_visible($1)", catalog_id
                )
                assert not await pool.fetchval(
                    "SELECT public.chronicler_graph_row_visible($1)", edge_id
                )
                chronicler_api = roster_module("chronicler", "api/router")
                database = db_manager({"chronicler": pool})
                affected = await chronicler_api.get_rollups(
                    rollup_date=local_day, start_date=None, end_date=None, db=database
                )
                assert affected.data.days[0].status == "pending_repair"
                assert affected.data.days[0].lanes == []
                with pytest.raises(RepairAdmissionError, match="retention repair is pending"):
                    await capture_fact_write_token(
                        pool,
                        day_date=local_day,
                        timezone="Asia/Singapore",
                        baseline_lookback_days=28,
                    )

                # Remove only the injected delay, then admit the same expired source
                # under the original 2s session GUC.
                with engine.begin() as admin:
                    admin.exec_driver_sql(
                        "DROP TRIGGER f2_strict_admission_delay "
                        "ON chronicler.daily_repair_source_redactions"
                    )
                    admin.exec_driver_sql("DROP FUNCTION chronicler.f2_strict_admission_delay()")
                retry = await run_rebuild(pool, now=future)
                assert retry.get("retention_admission_timeout") is not True
                assert retry.get("retention_timeout") is not True
                assert await pool.fetchval(
                    "SELECT reason_code='source_retention' "
                    "FROM chronicler.daily_repair_source_redactions WHERE source_id=$1",
                    source_id,
                )
                redacted_fact = await pool.fetchrow(
                    "SELECT validity, content FROM chronicler_mem.facts WHERE id=$1", fact_id
                )
                assert (redacted_fact["validity"], redacted_fact["content"]) == (
                    "retracted",
                    "[removed by source privacy correction]",
                )
                redacted_catalog = await pool.fetchrow(
                    "SELECT summary, title, confidence, invalid_at "
                    "FROM public.memory_catalog WHERE id=$1",
                    catalog_id,
                )
                assert (redacted_catalog["summary"], redacted_catalog["title"]) == ("", None)
                assert (
                    redacted_catalog["confidence"] == 0
                    and redacted_catalog["invalid_at"] is not None
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.entity_graph_edges WHERE id=$1)", edge_id
                )
                redacted_archive = await pool.fetchrow(
                    "SELECT content, audit_hash, text_access "
                    "FROM chronicler.daily_repair_archive WHERE $1=ANY(dependency_ids)",
                    source_id,
                )
                assert (
                    redacted_archive["content"] is None and redacted_archive["audit_hash"] is None
                )
                assert redacted_archive["text_access"] == "redacted"
                assert await pool.fetchval("SELECT current_setting('statement_timeout')") == "2s"
            finally:
                try:
                    if observer is not None:
                        await observer.close()
                finally:
                    await pool.close()

        asyncio.run(scenario())
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "DROP TRIGGER IF EXISTS f2_strict_admission_delay "
                "ON chronicler.daily_repair_source_redactions"
            )
            conn.exec_driver_sql("DROP FUNCTION IF EXISTS chronicler.f2_strict_admission_delay()")
        engine.dispose()


def test_retention_first_admission_external_cancellation_rolls_back_prior_writes(
    postgres_container,
):
    """Cancellation during post-update trigger work rolls admission writes back."""
    import time
    from zoneinfo import ZoneInfo

    from butlers.chronicler.history_repair import RepairAdmissionError, capture_fact_write_token

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=10)).replace(hour=3, minute=0, second=0)
    end = start + timedelta(hours=2)
    future = now + timedelta(days=1)
    local_day = start.astimezone(ZoneInfo("Asia/Singapore")).date()
    source_id = uuid4()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                    (source_name, chronicler_compatibility)
                    VALUES ('steam.play_history', 'supported') ON CONFLICT DO NOTHING""")
            )
            # Model a legacy expired row that predates the normal enqueue trigger.
            conn.exec_driver_sql(
                "ALTER TABLE chronicler.episodes DISABLE TRIGGER episodes_daily_repair"
            )
            try:
                conn.execute(
                    text("""INSERT INTO chronicler.episodes
                        (id, source_name, source_ref, episode_type, start_at, end_at,
                         layer, title, retention_days)
                        VALUES (:id, 'steam.play_history', 'external-admission-cancel',
                                'play_episode', :start, :end, 'evidence',
                                'private source title', 1)"""),
                    {"id": source_id, "start": start, "end": end},
                )
            finally:
                conn.exec_driver_sql(
                    "ALTER TABLE chronicler.episodes ENABLE TRIGGER episodes_daily_repair"
                )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_archive
                    (local_date, generation, row_kind, row_key, audit_hash, content,
                     dependency_ids, text_access)
                    VALUES (:day, 1, 'test', 'external-cancel-copy', 'private-hash',
                            CAST('{"title":"private source"}' AS jsonb),
                            ARRAY[CAST(:source_id AS uuid)], 'audit_only')"""),
                {"day": local_day, "source_id": source_id},
            )
            conn.exec_driver_sql("""
                CREATE OR REPLACE FUNCTION chronicler.f2_external_admission_delay()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.text_access = 'redacted'
                       AND TG_ARGV[0]::uuid = ANY(NEW.dependency_ids) THEN
                        PERFORM pg_sleep(30.0);
                    END IF;
                    RETURN NEW;
                END $$
            """)
            conn.exec_driver_sql(f"""
                CREATE TRIGGER f2_external_admission_delay
                AFTER UPDATE ON chronicler.daily_repair_archive
                FOR EACH ROW EXECUTE FUNCTION chronicler.f2_external_admission_delay('{source_id}')
            """)

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            from butlers.chronicler.history_repair import run_rebuild

            app_name = f"f1-external-admission-cancel-{uuid4().hex}"
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                min_size=1,
                max_size=1,
                server_settings={
                    "search_path": "chronicler, public",
                    "statement_timeout": "0",
                    "application_name": app_name,
                },
                init=runtime_connection,
            )
            observer = None
            task = None
            try:
                observer = await asyncpg.connect(
                    url.replace("postgresql+psycopg2://", "postgresql://"),
                    server_settings={"search_path": "chronicler, public"},
                )
                await pool.execute(
                    "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
                )
                started = time.monotonic()
                task = asyncio.create_task(run_rebuild(pool, now=future))
                sleep_witness = False
                poll_until = asyncio.get_running_loop().time() + 4.0
                while (
                    not sleep_witness
                    and not task.done()
                    and asyncio.get_running_loop().time() < poll_until
                ):
                    sleep_witness = await observer.fetchval(
                        """SELECT EXISTS (
                               SELECT 1 FROM pg_catalog.pg_stat_activity
                               WHERE application_name=$1 AND state='active'
                                 AND wait_event='PgSleep'
                                 AND query LIKE '%UPDATE chronicler.daily_repair_archive%'
                           )""",
                        app_name,
                    )
                    if not sleep_witness:
                        await asyncio.sleep(0.05)
                if not sleep_witness:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    pytest.fail("the post-update archive trigger was not observed in PgSleep")

                assert task.cancel() is True
                with pytest.raises(asyncio.CancelledError):
                    await task
                elapsed = time.monotonic() - started
                print(f"F1_EXTERNAL_ADMISSION_CANCEL_ELAPSED_SECONDS={elapsed:.3f}")
                assert elapsed < 5.0, (
                    "external cancellation must settle inside the admission budget"
                )

                # The source-redaction row, retention cursor span, and archive UPDATE
                # happened before the delayed AFTER trigger, but cancellation rolls them back.
                assert await pool.fetchval("SELECT 1") == 1
                assert await pool.fetchval("SELECT current_setting('statement_timeout')") == "0"
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_source_redactions "
                    "WHERE source_id=$1)",
                    source_id,
                )
                assert not await pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_retention_spans "
                    "WHERE source_id=$1)",
                    source_id,
                )
                source = await pool.fetchrow(
                    "SELECT title, privacy, tombstone_at FROM chronicler.episodes WHERE id=$1",
                    source_id,
                )
                assert (source["title"], source["privacy"], source["tombstone_at"]) == (
                    "private source title",
                    "normal",
                    None,
                )
                archive = await pool.fetchrow(
                    "SELECT content, audit_hash, text_access "
                    "FROM chronicler.daily_repair_archive WHERE $1=ANY(dependency_ids)",
                    source_id,
                )
                assert archive["content"] == {"title": "private source"}
                assert archive["audit_hash"] == "private-hash"
                assert archive["text_access"] == "audit_only"
                with pytest.raises(RepairAdmissionError, match="retention repair is pending"):
                    await capture_fact_write_token(
                        pool,
                        day_date=local_day,
                        timezone="Asia/Singapore",
                        baseline_lookback_days=28,
                    )

                with engine.begin() as admin:
                    admin.exec_driver_sql(
                        "DROP TRIGGER f2_external_admission_delay "
                        "ON chronicler.daily_repair_archive"
                    )
                    admin.exec_driver_sql("DROP FUNCTION chronicler.f2_external_admission_delay()")
                retry = await run_rebuild(pool, now=future)
                assert retry.get("retention_admission_timeout") is not True
                assert retry.get("retention_timeout") is not True
                assert await pool.fetchval(
                    "SELECT reason_code='source_retention' "
                    "FROM chronicler.daily_repair_source_redactions WHERE source_id=$1",
                    source_id,
                )
                redacted_archive = await pool.fetchrow(
                    "SELECT content, audit_hash, text_access "
                    "FROM chronicler.daily_repair_archive WHERE $1=ANY(dependency_ids)",
                    source_id,
                )
                assert redacted_archive["content"] is None
                assert redacted_archive["audit_hash"] is None
                assert redacted_archive["text_access"] == "redacted"
                assert await pool.fetchval("SELECT current_setting('statement_timeout')") == "0"
            finally:
                try:
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                finally:
                    try:
                        if observer is not None:
                            await observer.close()
                    finally:
                        await pool.close()

        asyncio.run(scenario())
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "DROP TRIGGER IF EXISTS f2_external_admission_delay "
                "ON chronicler.daily_repair_archive"
            )
            conn.exec_driver_sql("DROP FUNCTION IF EXISTS chronicler.f2_external_admission_delay()")
        engine.dispose()


def test_chronicler_local_fact_visibility_without_030_preserves_safe_rows(postgres_container):
    """Before 030, hide generated system rows without affecting owner or ordinary facts."""
    from butlers.core.chronicler_visibility import local_fact_visibility_condition

    url = _migrated_chronicler_db(postgres_container, "chronicler_029")
    engine = create_engine(url)
    generated_id, owner_id, ordinary_id = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as conn:
            assert conn.execute(
                text("SELECT to_regclass('chronicler_mem.facts_readable') IS NULL")
            ).scalar_one()
            for fact_id, predicate, authority, content in (
                (generated_id, "lane-skew", "system", "pending generated"),
                (owner_id, "lane-skew", "owner", "owner correction"),
                (ordinary_id, "weekly-note", "system", "ordinary note"),
            ):
                conn.execute(
                    text("""INSERT INTO chronicler_mem.facts
                        (id, subject, predicate, content, source_butler, content_authority,
                         validity, tags, metadata)
                        VALUES (:id, :subject, :predicate, :content, 'chronicler',
                                :authority, 'active', '[]'::jsonb, '{}'::jsonb)"""),
                    {
                        "id": fact_id,
                        "subject": f"subject-{fact_id}",
                        "predicate": predicate,
                        "content": content,
                        "authority": authority,
                    },
                )

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler_mem, public"},
                init=runtime_connection,
            )
            try:
                visibility = await local_fact_visibility_condition(
                    pool, memory_schema="chronicler_mem", alias="facts"
                )
                assert visibility is not None
                rows = await pool.fetch(
                    f"""SELECT facts.id, facts.predicate, facts.content_authority
                        FROM chronicler_mem.facts AS facts
                        WHERE {visibility}"""
                )
                assert {row["id"] for row in rows} == {owner_id, ordinary_id}
                assert all(row["id"] != generated_id for row in rows)
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_chronicler_local_reader_keeps_current_generated_fact_and_hides_invalid_rows(
    postgres_container,
):
    """Keep finalized generated facts and hide pending or disposed rows."""
    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    projection_ids = {"catalog": {}, "graph": {}}
    boundary_id, later_id = uuid4(), uuid4()
    generated_id, pending_id, disposed_id, owner_id, ordinary_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    try:
        with engine.begin() as conn:
            closed_through = conn.execute(
                text("SELECT closed_through FROM chronicler.daily_repair_control WHERE singleton")
            ).scalar_one()
            changed_day = closed_through - timedelta(days=29)
            kept_day = changed_day - timedelta(days=1)
            conn.execute(
                text("""UPDATE chronicler.daily_repair_control
                    SET replay_ack=replay_epoch, blocked_reason=NULL, timezone='UTC'
                    WHERE singleton""")
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_days
                    (local_date, timezone, dirty_generation, built_generation,
                     totals_pending, outlier_pending, facts_pending)
                    VALUES (:day, 'UTC', 1, 1, FALSE, FALSE, FALSE)"""),
                {"day": kept_day},
            )
            conn.execute(
                text("""SELECT chronicler.enqueue_daily_repair(
                    :start_at, :start_at + interval '1 second', TRUE,
                    ARRAY[CAST(:dependency AS uuid)])"""),
                {
                    "start_at": datetime.combine(changed_day, datetime.min.time(), tzinfo=UTC),
                    "dependency": uuid4(),
                },
            )
            assert (
                conn.execute(
                    text("""SELECT count(*) FROM chronicler.daily_repair_days
                    WHERE local_date BETWEEN :day AND :day + 28 AND facts_pending"""),
                    {"day": changed_day},
                ).scalar_one()
                == 29
            )
            for fact_id, predicate, authority, subject, tags, metadata in (
                (
                    generated_id,
                    "lane-skew",
                    "system",
                    "work lane",
                    '["chronicler-insight", "lane-skew", "work"]',
                    {
                        "source": "chronicler",
                        "local_date": kept_day.isoformat(),
                        "lane": "work",
                        "direction": "down",
                        "seconds": 3600,
                    },
                ),
                (
                    pending_id,
                    "lane-skew",
                    "system",
                    "home lane",
                    '["chronicler-insight", "lane-skew", "home"]',
                    {
                        "source": "chronicler",
                        "local_date": changed_day.isoformat(),
                        "lane": "home",
                        "direction": "down",
                        "seconds": 3600,
                    },
                ),
                (
                    boundary_id,
                    "lane-skew",
                    "system",
                    "rest lane",
                    '["chronicler-insight", "lane-skew", "rest"]',
                    {
                        "source": "chronicler",
                        "local_date": (changed_day + timedelta(days=28)).isoformat(),
                        "lane": "rest",
                        "direction": "down",
                        "seconds": 3600,
                    },
                ),
                (
                    later_id,
                    "lane-skew",
                    "system",
                    "play lane",
                    '["chronicler-insight", "lane-skew", "play"]',
                    {
                        "source": "chronicler",
                        "local_date": (changed_day + timedelta(days=29)).isoformat(),
                        "lane": "play",
                        "direction": "down",
                        "seconds": 3600,
                    },
                ),
                (
                    disposed_id,
                    "lane-skew",
                    "system",
                    "read lane",
                    '["chronicler-insight", "lane-skew", "read"]',
                    {
                        "source": "chronicler",
                        "local_date": kept_day.isoformat(),
                        "lane": "read",
                        "direction": "down",
                        "seconds": 3600,
                    },
                ),
                (
                    owner_id,
                    "lane-skew",
                    "owner",
                    "owner lane",
                    '["chronicler-insight", "lane-skew", "work"]',
                    {
                        "source": "chronicler",
                        "local_date": kept_day.isoformat(),
                        "lane": "work",
                        "direction": "down",
                        "seconds": 3600,
                    },
                ),
                (
                    ordinary_id,
                    "weekly-note",
                    "system",
                    "ordinary note",
                    "[]",
                    {},
                ),
            ):
                conn.execute(
                    text("""INSERT INTO chronicler_mem.facts
                        (id, subject, predicate, content, source_butler, content_authority,
                         validity, tags, metadata)
                        VALUES (:id, :subject, :predicate, 'local visibility control',
                                'chronicler', :authority, 'active', CAST(:tags AS jsonb),
                                CAST(:metadata AS jsonb))"""),
                    {
                        "id": fact_id,
                        "subject": subject,
                        "predicate": predicate,
                        "authority": authority,
                        "tags": tags,
                        "metadata": json.dumps(metadata),
                    },
                )
            # Shared readers must use the same target-day queue orientation.
            subject_id, object_id = uuid4(), uuid4()
            for entity_id in (subject_id, object_id):
                conn.execute(
                    text(
                        "INSERT INTO public.entities (id, canonical_name, entity_type) "
                        "VALUES (:id, CAST(:id AS text), 'other')"
                    ),
                    {"id": entity_id},
                )
            conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
            for fact_id in (
                generated_id,
                pending_id,
                boundary_id,
                later_id,
                disposed_id,
                owner_id,
                ordinary_id,
            ):
                projection_ids["catalog"][fact_id] = conn.execute(
                    text("""INSERT INTO public.memory_catalog
                        (source_schema, source_table, source_id, source_butler, tenant_id,
                         summary, search_vector, memory_type, title, sensitivity)
                        VALUES ('chronicler_mem', 'facts', :id, 'chronicler', 'shared',
                                'visibility boundary', to_tsvector('english', 'boundary'),
                                'fact', 'boundary', 'normal') RETURNING id"""),
                    {"id": fact_id},
                ).scalar_one()
                projection_ids["graph"][fact_id] = conn.execute(
                    text("""INSERT INTO public.entity_graph_edges
                        (source_schema, source_table, source_id, subject_entity_id,
                         predicate, object_entity_id)
                        VALUES ('chronicler_mem', 'facts', :id, :subject,
                                'related_to', :object) RETURNING id"""),
                    {"id": fact_id, "subject": subject_id, "object": object_id},
                ).scalar_one()
            conn.exec_driver_sql("RESET ROLE")
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_fact_dispositions
                    (local_date, generation, row_key, fact_id, disposition, reason_code,
                     dependency_ids, redacted)
                    VALUES (:day, 1, :row_key, :fact_id, 'disposed', 'source_changed',
                            '{}'::uuid[], FALSE)"""),
                {"day": kept_day, "row_key": str(disposed_id), "fact_id": disposed_id},
            )

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                init=runtime_connection,
            )
            try:
                rows = await pool.fetch(
                    "SELECT id FROM chronicler_mem.facts_readable "
                    "WHERE id = ANY($1::uuid[]) ORDER BY id",
                    [generated_id, pending_id, disposed_id, owner_id, ordinary_id],
                )
                assert generated_id in {row["id"] for row in rows}, (
                    "earlier generated fact stays visible"
                )
                assert {row["id"] for row in rows} == {generated_id, owner_id, ordinary_id}
                boundary_rows = await pool.fetch(
                    "SELECT id FROM chronicler_mem.facts_readable WHERE id=ANY($1::uuid[])",
                    [
                        generated_id,
                        pending_id,
                        boundary_id,
                        later_id,
                        disposed_id,
                        owner_id,
                        ordinary_id,
                    ],
                )
                assert {row["id"] for row in boundary_rows} == {
                    generated_id,
                    later_id,
                    owner_id,
                    ordinary_id,
                }
                for kind, function in (
                    ("catalog", "public.chronicler_catalog_row_visible"),
                    ("graph", "public.chronicler_graph_row_visible"),
                ):
                    visible = {
                        fact_id
                        for fact_id, row_id in projection_ids[kind].items()
                        if await pool.fetchval(f"SELECT {function}($1)", row_id)
                    }
                    assert generated_id in visible, f"{kind}: earlier generated fact stays visible"
                    assert visible == {generated_id, later_id, owner_id, ordinary_id}
                await pool.execute("UPDATE chronicler.daily_repair_days SET facts_pending=FALSE")
                restored = await pool.fetch(
                    "SELECT id FROM chronicler_mem.facts_readable WHERE id=ANY($1::uuid[])",
                    [generated_id, pending_id, disposed_id, owner_id, ordinary_id],
                )
                assert {row["id"] for row in restored} == {
                    generated_id,
                    pending_id,
                    owner_id,
                    ordinary_id,
                }
                for kind, function in (
                    ("catalog", "public.chronicler_catalog_row_visible"),
                    ("graph", "public.chronicler_graph_row_visible"),
                ):
                    assert await pool.fetchval(
                        f"SELECT {function}($1)", projection_ids[kind][pending_id]
                    )
                    assert await pool.fetchval(
                        f"SELECT {function}($1)", projection_ids[kind][boundary_id]
                    )
                    assert await pool.fetchval(
                        f"SELECT {function}($1)", projection_ids[kind][later_id]
                    )
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_chronicler_readers_withhold_pending_fact_before_search_limit_and_graph_walk(
    postgres_container, monkeypatch
):
    """Pending generated rows stay hidden while owner, ordinary, and foreign rows survive."""
    from urllib.parse import urlparse

    from fastapi import HTTPException

    from butlers.api.routers import data_ops
    from butlers.api.routers import entities as entity_api
    from butlers.api.routers import memory as memory_api
    from butlers.core import entity_graph_edges
    from butlers.core.entity_graph_edges import (
        coverage_for_entities,
        dossier_for_entity,
        find_entity_graph_path,
        walk_entity_graph,
    )
    from butlers.modules.memory import catalog_measurement, knowledge_gaps
    from butlers.modules.memory import search as memory_search_impl
    from butlers.modules.memory import tools as memory_tools_impl
    from butlers.modules.memory.search import CatalogReadPolicy, search_catalog
    from butlers.modules.memory.tools import context as memory_context_impl
    from butlers.modules.memory.tools import entities as memory_entities_impl
    from butlers.modules.memory.tools import management as memory_management_impl
    from butlers.modules.memory.tools.context import memory_context
    from butlers.modules.memory.tools.entities import entity_neighbors, entity_resolve
    from butlers.modules.memory.tools.management import memory_stats
    from butlers.modules.memory.tools.preferences import get_preferences
    from butlers.modules.memory.tools.reading import (
        memory_get,
        memory_recall,
    )
    from butlers.modules.memory.tools.reading import (
        memory_search as memory_search_tool,
    )
    from butlers.testing.db_manager import db_manager

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    admin_url = (
        urlparse(postgres_container.get_connection_url())._replace(path=urlparse(url).path).geturl()
    )
    admin_engine = create_engine(admin_url)
    day = None
    generated_id, owner_id, ordinary_id = uuid4(), uuid4(), uuid4()
    preference_id = uuid4()
    preference_owner_id = None
    start_id, stale_neighbor, owner_neighbor, ordinary_neighbor, foreign_neighbor = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    catalog_ids = {}
    graph_ids = {}
    try:
        with engine.begin() as conn:
            day = conn.execute(
                text("SELECT closed_through FROM chronicler.daily_repair_control WHERE singleton")
            ).scalar_one()
            conn.execute(
                text("""UPDATE chronicler.daily_repair_control
                    SET replay_ack=replay_epoch, blocked_reason=NULL, timezone='Asia/Singapore'
                    WHERE singleton""")
            )
            conn.execute(
                text("""INSERT INTO chronicler.daily_repair_days
                    (local_date, timezone, dirty_generation, built_generation,
                     totals_pending, outlier_pending, facts_pending)
                    VALUES (:day, 'Asia/Singapore', 1, 1, FALSE, FALSE, TRUE)
                    ON CONFLICT (local_date) DO UPDATE SET facts_pending=TRUE"""),
                {"day": day},
            )
            for entity_id, name in (
                (start_id, "reader-source"),
                (stale_neighbor, "reader-stale-target"),
                (owner_neighbor, "reader-owner-target"),
                (ordinary_neighbor, "reader-ordinary-target"),
                (foreign_neighbor, "reader-foreign-target"),
            ):
                conn.execute(
                    text("""INSERT INTO public.entities (id, canonical_name, entity_type)
                        VALUES (:id, :name, 'other')"""),
                    {"id": entity_id, "name": name},
                )
            preference_owner = conn.execute(
                text("SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1")
            ).scalar_one_or_none()
            if preference_owner is None:
                preference_owner_id = uuid4()
                conn.execute(
                    text("""INSERT INTO public.entities
                        (id, canonical_name, entity_type, roles)
                        VALUES (:id, :name, 'person', ARRAY['owner']::text[])"""),
                    {
                        "id": preference_owner_id,
                        "name": f"reader-owner-{preference_owner_id}",
                    },
                )
            else:
                preference_owner_id = preference_owner
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, source_butler, content_authority,
                     validity, tags, metadata)
                    VALUES (:id, 'work lane', 'lane-skew',
                            'pending generated cobalt lighthouse cobalt lighthouse cobalt lighthouse',
                            'chronicler', 'system', 'active',
                            '["chronicler-insight", "lane-skew", "work"]'::jsonb,
                            CAST(:metadata AS jsonb))"""),
                {
                    "id": generated_id,
                    "metadata": (
                        '{"source":"chronicler","local_date":"'
                        + day.isoformat()
                        + '","lane":"work","direction":"down","seconds":1}'
                    ),
                },
            )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, source_butler, content_authority,
                     validity, tags, metadata)
                    VALUES (:id, 'owner lane', 'lane-skew', 'owner cobalt lighthouse',
                            'chronicler', 'owner', 'active',
                            '["chronicler-insight", "lane-skew", "work"]'::jsonb,
                            CAST(:metadata AS jsonb))"""),
                {
                    "id": owner_id,
                    "metadata": (
                        '{"source":"chronicler","local_date":"'
                        + day.isoformat()
                        + '","lane":"work","direction":"down","seconds":1}'
                    ),
                },
            )
            conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                    (id, subject, predicate, content, source_butler, content_authority,
                     validity, tags, metadata)
                    VALUES (:id, 'ordinary note', 'weekly-note', 'ordinary cobalt lighthouse',
                            'chronicler', 'system', 'active', '[]'::jsonb, '{}'::jsonb)"""),
                {"id": ordinary_id},
            )
            # The migration fixture leaves search_vector nullable; production fact writers
            # populate it. Seed the test rows so actual full-text readers have real candidates.
            for fact_id in (generated_id, owner_id, ordinary_id):
                conn.execute(
                    text("""UPDATE chronicler_mem.facts
                        SET search_vector=to_tsvector('english', content),
                            last_confirmed_at=now()
                        WHERE id=:id"""),
                    {"id": fact_id},
                )
            # Knowledge-gap capture asks whether an active fact already answers this pair.
            # Give the pending generated fact a real entity so that this exact query seam can
            # prove the hidden row does not turn a question into an answered gap.
            conn.execute(
                text("UPDATE chronicler_mem.facts SET entity_id=:entity WHERE id=:id"),
                {"entity": start_id, "id": generated_id},
            )
            # Make the pending row the first canonical result if a reader applies LIMIT
            # before its source-visibility predicate.
            conn.execute(
                text(
                    "UPDATE chronicler_mem.facts SET created_at=now()+interval '1 day' WHERE id=:id"
                ),
                {"id": generated_id},
            )
            conn.exec_driver_sql("SET LOCAL ROLE butler_chronicler_rw")
            for fact_id, schema, summary in (
                (
                    generated_id,
                    "chronicler_mem",
                    "cobalt lighthouse cobalt lighthouse cobalt lighthouse cobalt lighthouse",
                ),
                (owner_id, "chronicler_mem", "cobalt lighthouse owner"),
                (ordinary_id, "chronicler_mem", "cobalt lighthouse ordinary"),
                (generated_id, "foreign_memory", "cobalt lighthouse foreign same uuid"),
            ):
                row_id = conn.execute(
                    text("""INSERT INTO public.memory_catalog
                        (source_schema, source_table, source_id, source_butler, tenant_id,
                         summary, search_vector, memory_type, title, sensitivity)
                        VALUES (:schema, 'facts', :source_id, 'chronicler', 'shared',
                                :summary, to_tsvector('english', :summary), 'fact',
                                :summary, 'normal') RETURNING id"""),
                    {"schema": schema, "source_id": fact_id, "summary": summary},
                ).scalar_one()
                catalog_ids[(schema, fact_id)] = row_id
            for fact_id, schema, neighbor_id in (
                (generated_id, "chronicler_mem", stale_neighbor),
                (owner_id, "chronicler_mem", owner_neighbor),
                (ordinary_id, "chronicler_mem", ordinary_neighbor),
                (generated_id, "foreign_memory", foreign_neighbor),
            ):
                edge_id = conn.execute(
                    text("""INSERT INTO public.entity_graph_edges
                        (source_schema, source_table, source_id, subject_entity_id,
                         predicate, object_entity_id)
                        VALUES (:schema, 'facts', :source_id, :subject,
                                'related_to', :object) RETURNING id"""),
                    {
                        "schema": schema,
                        "source_id": fact_id,
                        "subject": start_id,
                        "object": neighbor_id,
                    },
                ).scalar_one()
                graph_ids[neighbor_id] = edge_id

            cost_catalog_ids = (
                conn.execute(
                    text("""
                    INSERT INTO public.memory_catalog
                        (source_schema, source_table, source_id, source_butler, tenant_id,
                         summary, search_vector, memory_type, title, sensitivity)
                    SELECT 'unrelated_cost_probe', 'facts', gen_random_uuid(),
                           'unrelated_cost_probe', 'shared',
                           'unrelated bounded visibility sample',
                           to_tsvector('english', 'unrelated bounded visibility sample'),
                           'fact', 'unrelated bounded visibility sample', 'normal'
                    FROM generate_series(1, 512)
                    RETURNING id
                """)
                )
                .scalars()
                .all()
            )
            assert len(cost_catalog_ids) == 512
            for fact_id, first, second in (
                (generated_id, 1.0, 0.0),
                (owner_id, 0.0, 1.0),
                (ordinary_id, 0.0, 1.0),
            ):
                conn.execute(
                    text("""UPDATE public.memory_catalog
                        SET embedding = (
                            ARRAY[CAST(:first AS real), CAST(:second AS real)]
                            || array_fill(0::real, ARRAY[382])
                        )::vector(384)
                        WHERE source_schema='chronicler_mem'
                          AND source_table='facts' AND source_id=:fact_id"""),
                    {"first": first, "second": second, "fact_id": fact_id},
                )

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler_mem, public"},
                init=runtime_connection,
            )
            try:
                top_raw = await pool.fetchrow(
                    """SELECT source_schema, source_id
                       FROM public.memory_catalog
                       WHERE tenant_id='shared' AND invalid_at IS NULL
                         AND search_vector @@ plainto_tsquery('english', $1)
                       ORDER BY ts_rank(search_vector, plainto_tsquery('english', $1)) DESC
                       LIMIT 1""",
                    "cobalt lighthouse",
                )
                assert (top_raw["source_schema"], top_raw["source_id"]) == (
                    "chronicler_mem",
                    generated_id,
                )
                hits = await search_catalog(
                    pool,
                    "cobalt lighthouse",
                    None,
                    tenant_id="shared",
                    memory_type="fact",
                    limit=1,
                    mode="keyword",
                    read_policy=CatalogReadPolicy("normal", ("normal",)),
                )
                assert len(hits) == 1
                assert (hits[0]["source_schema"], hits[0]["source_id"]) != (
                    "chronicler_mem",
                    generated_id,
                )
                # The same UUID under a foreign stored source tuple is not classified as
                # Chronicler and remains an eligible public pointer.
                assert await pool.fetchval(
                    "SELECT public.chronicler_catalog_row_visible($1)",
                    catalog_ids[("foreign_memory", generated_id)],
                )

                async def _omit_local_visibility(*_args, **_kwargs):
                    return None

                # The knowledge-gap answer probe uses the same configured-schema
                # classification as fact readers. Show that the pending row is a real answer
                # without that predicate, then keep it from prematurely closing a gap.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        knowledge_gaps, "local_fact_visibility_condition", _omit_local_visibility
                    )
                    raw_gap_answer = await knowledge_gaps._existing_answer(
                        pool, "chronicler_mem", start_id, "lane-skew"
                    )
                assert raw_gap_answer is not None
                assert raw_gap_answer["ref"] == f"fact:{generated_id}"
                assert (
                    await knowledge_gaps._existing_answer(
                        pool, "chronicler_mem", start_id, "lane-skew"
                    )
                    is None
                )

                # The MCP management summary must count visible local facts, not a pending
                # generated candidate. Its bypass companion confirms the raw count differs.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_management_impl,
                        "local_fact_visibility_condition",
                        _omit_local_visibility,
                    )
                    raw_management_stats = await memory_stats(pool, memory_schema="chronicler_mem")
                assert raw_management_stats["facts"]["active"] == 3
                visible_management_stats = await memory_stats(pool, memory_schema="chronicler_mem")
                assert visible_management_stats["facts"]["active"] == 2

                walked = await walk_entity_graph(pool, entity_id=start_id, max_hops=1)
                walked_ids = {row["entity_id"] for row in walked}
                assert stale_neighbor not in walked_ids
                assert {owner_neighbor, ordinary_neighbor, foreign_neighbor} <= walked_ids
                coverage = await coverage_for_entities(pool, [start_id])
                assert coverage[start_id] == (3, 0)
                dossier = await dossier_for_entity(pool, start_id)
                assert sum(dossier.live_by_source.values()) == 3
                assert {receipt.counterpart_entity_id for receipt in dossier.receipts} == {
                    owner_neighbor,
                    ordinary_neighbor,
                    foreign_neighbor,
                }
                # Path discovery and its final edge re-fetch apply the same row-ID predicate.
                # Neutralizing only that predicate demonstrates the fixture contains a real
                # stale path; the restored production query must withhold it and retain a live one.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        entity_graph_edges, "graph_projection_visible", lambda _alias: "TRUE"
                    )
                    unfiltered_path = await find_entity_graph_path(
                        pool,
                        from_entity_id=start_id,
                        to_entity_id=stale_neighbor,
                        max_hops=1,
                    )
                assert unfiltered_path is not None
                assert [row["id"] for row in unfiltered_path] == [graph_ids[stale_neighbor]]
                assert (
                    await find_entity_graph_path(
                        pool,
                        from_entity_id=start_id,
                        to_entity_id=stale_neighbor,
                        max_hops=1,
                    )
                    is None
                )
                owner_path = await find_entity_graph_path(
                    pool,
                    from_entity_id=start_id,
                    to_entity_id=owner_neighbor,
                    max_hops=1,
                )
                assert owner_path is not None
                assert [row["id"] for row in owner_path] == [graph_ids[owner_neighbor]]

                database = db_manager(
                    {"chronicler": pool},
                    schemas={"chronicler": "chronicler"},
                    memory_schemas={"chronicler": "chronicler_mem"},
                    modules={"chronicler": ["memory"]},
                )

                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_api, "local_fact_visibility_condition", _omit_local_visibility
                    )
                    raw_api_stats = await memory_api.get_memory_stats(db=database)
                    raw_butler_stats = await memory_api.get_butler_memory_stats(
                        name="chronicler", db=database
                    )
                assert raw_api_stats.data.total_facts == 3
                assert raw_api_stats.data.active_facts == 3
                assert raw_butler_stats.data.total_facts == 3
                visible_api_stats = await memory_api.get_memory_stats(db=database)
                visible_butler_stats = await memory_api.get_butler_memory_stats(
                    name="chronicler", db=database
                )
                assert visible_api_stats.data.total_facts == 2
                assert visible_api_stats.data.active_facts == 2
                assert visible_butler_stats.data.total_facts == 2

                # Exercise the catalog HTTP route with its real catalog SQL and a held policy
                # value; the embedding engine and switchboard context are the external edges.
                catalog_database = db_manager({"switchboard": pool})
                catalog_api_engine = SimpleNamespace(embed=lambda _query: [1.0, *([0.0] * 383)])

                catalog_read_policy = CatalogReadPolicy("normal", ("normal",))

                async def _held_catalog_policy(_pool):
                    return catalog_read_policy

                with monkeypatch.context() as catalog_route:
                    catalog_route.setattr(
                        memory_search_impl, "load_catalog_read_policy", _held_catalog_policy
                    )
                    catalog_route.setattr(
                        memory_tools_impl,
                        "get_embedding_engine",
                        lambda *_args, **_kwargs: catalog_api_engine,
                    )
                    with monkeypatch.context() as bypass:
                        bypass.setattr(
                            memory_search_impl,
                            "catalog_projection_visible",
                            lambda _alias: "TRUE",
                        )
                        raw_catalog_api = await memory_api.search_memory_catalog(
                            query="cobalt lighthouse",
                            memory_type="fact",
                            limit=1,
                            mode="semantic",
                            db=catalog_database,
                        )
                    assert len(raw_catalog_api.data) == 1
                    assert raw_catalog_api.data[0].source_schema == "chronicler_mem"
                    assert raw_catalog_api.data[0].source_id == str(generated_id)
                    visible_catalog_api = await memory_api.search_memory_catalog(
                        query="cobalt lighthouse",
                        memory_type="fact",
                        limit=1,
                        mode="semantic",
                        db=catalog_database,
                    )
                assert len(visible_catalog_api.data) == 1
                assert visible_catalog_api.data[0].source_id in {
                    str(owner_id),
                    str(ordinary_id),
                }

                # Revoke the exact grants as their recorded owners in this disposable
                # database. Then run both real guarded SQL paths as the runtime role and
                # restore the original ACLs in a finally block.
                from butlers.core_tools._memory_access import _query_drops_7d

                def _set_local_role(conn, role_name: str) -> None:
                    quoted = '"' + role_name.replace('"', '""') + '"'
                    conn.exec_driver_sql(f"SET LOCAL ROLE {quoted}")

                with admin_engine.connect() as admin_conn:
                    view_owner = admin_conn.execute(
                        text(
                            "SELECT pg_catalog.pg_get_userbyid(relowner) "
                            "FROM pg_catalog.pg_class "
                            "WHERE oid='chronicler_mem.facts_readable'::pg_catalog.regclass"
                        )
                    ).scalar_one()
                    helper_owner = admin_conn.execute(
                        text(
                            "SELECT pg_catalog.pg_get_userbyid(proowner) "
                            "FROM pg_catalog.pg_proc "
                            "WHERE oid='public.chronicler_catalog_row_visible(uuid)'::pg_catalog.regprocedure"
                        )
                    ).scalar_one()
                    original_view_acl = admin_conn.execute(
                        text(
                            "SELECT ARRAY(SELECT ROW(grantor,grantee,privilege_type,is_grantable)::text FROM pg_catalog.aclexplode(relacl) ORDER BY 1) FROM pg_catalog.pg_class "
                            "WHERE oid='chronicler_mem.facts_readable'::pg_catalog.regclass"
                        )
                    ).scalar_one()
                    original_catalog_helper_acl = admin_conn.execute(
                        text(
                            "SELECT ARRAY(SELECT ROW(grantor,grantee,privilege_type,is_grantable)::text FROM pg_catalog.aclexplode(proacl) ORDER BY 1) FROM pg_catalog.pg_proc "
                            "WHERE oid='public.chronicler_catalog_row_visible(uuid)'::pg_catalog.regprocedure"
                        )
                    ).scalar_one()

                with admin_engine.connect() as admin_conn:
                    assert admin_conn.execute(
                        text(
                            "SELECT pg_catalog.has_table_privilege("
                            "'butler_chronicler_rw', "
                            "'chronicler_mem.facts_readable', 'SELECT')"
                        )
                    ).scalar_one()
                    assert admin_conn.execute(
                        text(
                            "SELECT pg_catalog.has_function_privilege("
                            "'butler_chronicler_rw', "
                            "'public.chronicler_catalog_row_visible(uuid)', 'EXECUTE')"
                        )
                    ).scalar_one()

                with admin_engine.begin() as admin_conn:
                    _set_local_role(admin_conn, view_owner)
                    admin_conn.exec_driver_sql(
                        "REVOKE SELECT ON chronicler_mem.facts_readable FROM butler_chronicler_rw"
                    )
                    admin_conn.exec_driver_sql("RESET ROLE")
                    _set_local_role(admin_conn, helper_owner)
                    admin_conn.exec_driver_sql(
                        "REVOKE EXECUTE ON FUNCTION "
                        "public.chronicler_catalog_row_visible(uuid) "
                        "FROM butler_chronicler_rw"
                    )

                try:
                    with admin_engine.connect() as admin_conn:
                        assert not admin_conn.execute(
                            text(
                                "SELECT pg_catalog.has_table_privilege("
                                "'butler_chronicler_rw', "
                                "'chronicler_mem.facts_readable', 'SELECT')"
                            )
                        ).scalar_one()
                        assert not admin_conn.execute(
                            text(
                                "SELECT pg_catalog.has_function_privilege("
                                "'butler_chronicler_rw', "
                                "'public.chronicler_catalog_row_visible(uuid)', 'EXECUTE')"
                            )
                        ).scalar_one()

                    async with pool.acquire() as guarded_connection:
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await _query_drops_7d(
                                guarded_connection,
                                memory_schema="chronicler_mem",
                            )
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await memory_context_impl._fetch_fleet_knowledge(
                                guarded_connection,
                                catalog_api_engine,
                                "cobalt lighthouse",
                                "chronicler",
                                "shared",
                                catalog_read_policy,
                            )
                finally:
                    with admin_engine.begin() as admin_conn:
                        _set_local_role(admin_conn, view_owner)
                        admin_conn.exec_driver_sql(
                            "GRANT SELECT ON chronicler_mem.facts_readable TO butler_chronicler_rw"
                        )
                        admin_conn.exec_driver_sql("RESET ROLE")
                        _set_local_role(admin_conn, helper_owner)
                        admin_conn.exec_driver_sql(
                            "GRANT EXECUTE ON FUNCTION "
                            "public.chronicler_catalog_row_visible(uuid) "
                            "TO butler_chronicler_rw"
                        )

                with admin_engine.connect() as admin_conn:
                    assert (
                        admin_conn.execute(
                            text(
                                "SELECT ARRAY(SELECT ROW(grantor,grantee,privilege_type,is_grantable)::text FROM pg_catalog.aclexplode(relacl) ORDER BY 1) FROM pg_catalog.pg_class "
                                "WHERE oid='chronicler_mem.facts_readable'::pg_catalog.regclass"
                            )
                        ).scalar_one()
                        == original_view_acl
                    )
                    assert (
                        admin_conn.execute(
                            text(
                                "SELECT ARRAY(SELECT ROW(grantor,grantee,privilege_type,is_grantable)::text FROM pg_catalog.aclexplode(proacl) ORDER BY 1) FROM pg_catalog.pg_proc "
                                "WHERE oid='public.chronicler_catalog_row_visible(uuid)'::pg_catalog.regprocedure"
                            )
                        ).scalar_one()
                        == original_catalog_helper_acl
                    )
                    assert admin_conn.execute(
                        text(
                            "SELECT pg_catalog.has_table_privilege("
                            "'butler_chronicler_rw', "
                            "'chronicler_mem.facts_readable', 'SELECT')"
                        )
                    ).scalar_one()
                    assert admin_conn.execute(
                        text(
                            "SELECT pg_catalog.has_function_privilege("
                            "'butler_chronicler_rw', "
                            "'public.chronicler_catalog_row_visible(uuid)', 'EXECUTE')"
                        )
                    ).scalar_one()

                # The public entity dossier API uses the same guarded graph read as the
                # underlying dossier helper. Its bypass companion proves a pending edge is
                # present; the restored route retains the owner, ordinary and foreign edges.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        entity_graph_edges,
                        "graph_projection_visible",
                        lambda _alias: "TRUE",
                    )
                    raw_entity_dossier = await entity_api.get_entity_dossier(
                        entity_id=start_id, limit=10, db=catalog_database
                    )
                assert raw_entity_dossier.data.relationships_known == 4
                assert raw_entity_dossier.data.relationships_withheld == 0
                assert {
                    receipt.counterpart_entity_id
                    for source in raw_entity_dossier.data.sources
                    for receipt in source.receipts
                } == {
                    str(stale_neighbor),
                    str(owner_neighbor),
                    str(ordinary_neighbor),
                    str(foreign_neighbor),
                }
                visible_entity_dossier = await entity_api.get_entity_dossier(
                    entity_id=start_id, limit=10, db=catalog_database
                )
                assert visible_entity_dossier.data.relationships_known == 3
                assert visible_entity_dossier.data.relationships_withheld == 0
                assert {
                    receipt.counterpart_entity_id
                    for source in visible_entity_dossier.data.sources
                    for receipt in source.receipts
                } == {
                    str(owner_neighbor),
                    str(ordinary_neighbor),
                    str(foreign_neighbor),
                }

                async def _omit_local_visibility(*_args, **_kwargs):
                    return None

                # These actual API entrypoints must filter before per-source LIMIT and total
                # count. The bypass control confirms the pending fact would otherwise lead.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_api, "local_fact_visibility_condition", _omit_local_visibility
                    )
                    raw_page = await memory_api.list_facts(
                        q=None,
                        scope=None,
                        validity=None,
                        permanence=None,
                        subject=None,
                        source_episode_id=None,
                        importance_min=None,
                        held=None,
                        offset=0,
                        limit=1,
                        db=database,
                    )
                    assert raw_page.meta.total == 3
                    assert str(raw_page.data[0].id) == str(generated_id)
                    raw_activity = await memory_api.list_activity(limit=1, db=database)
                    assert raw_activity.data[0].type == "fact"
                    assert raw_activity.data[0].id == str(generated_id)
                    raw_inspect = await memory_api.inspect_memory(
                        q="cobalt lighthouse", kind="fact", offset=0, limit=1, db=database
                    )
                    assert raw_inspect.meta.total == 3
                    assert str(raw_inspect.data[0].id) == str(generated_id)

                visible_page = await memory_api.list_facts(
                    q=None,
                    scope=None,
                    validity=None,
                    permanence=None,
                    subject=None,
                    source_episode_id=None,
                    importance_min=None,
                    held=None,
                    offset=0,
                    limit=1,
                    db=database,
                )
                assert visible_page.meta.total == 2
                assert all(str(row.id) != str(generated_id) for row in visible_page.data)
                visible_activity = await memory_api.list_activity(limit=1, db=database)
                assert len(visible_activity.data) == 1
                assert visible_activity.data[0].type == "fact"
                assert visible_activity.data[0].id in {str(owner_id), str(ordinary_id)}
                visible_inspect = await memory_api.inspect_memory(
                    q="cobalt lighthouse", kind="fact", offset=0, limit=1, db=database
                )
                assert visible_inspect.meta.total == 2
                assert all(str(row.id) != str(generated_id) for row in visible_inspect.data)

                with pytest.raises(HTTPException) as missing:
                    await memory_api.get_fact(str(generated_id), db=database)
                assert missing.value.status_code == 404
                assert (await memory_api.get_fact(str(owner_id), db=database)).data.id == str(
                    owner_id
                )
                assert (await memory_api.get_fact(str(ordinary_id), db=database)).data.id == str(
                    ordinary_id
                )

                # The direct memory_get path uses the configured private pool and preserves the
                # same hidden-generated / visible-owner-and-ordinary distinction.
                read_policy = CatalogReadPolicy("normal", ("normal",))
                assert (
                    await memory_get(
                        pool,
                        "fact",
                        str(generated_id),
                        read_policy=read_policy,
                        memory_schema="chronicler_mem",
                    )
                    is None
                )
                assert await memory_get(
                    pool,
                    "fact",
                    str(owner_id),
                    read_policy=read_policy,
                    memory_schema="chronicler_mem",
                )
                assert await memory_get(
                    pool,
                    "fact",
                    str(ordinary_id),
                    read_policy=read_policy,
                    memory_schema="chronicler_mem",
                )

                # Exercise the actual local search and recall implementations against the
                # configured private schema. The bypass makes the generated match visible; the
                # restored calls must omit it while retaining ordinary facts.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_search_impl,
                        "local_fact_visibility_condition",
                        _omit_local_visibility,
                    )
                    raw_search = await memory_search_tool(
                        pool,
                        None,
                        "cobalt lighthouse",
                        types=["fact"],
                        mode="keyword",
                        limit=1,
                        read_policy=read_policy,
                        memory_schema="chronicler_mem",
                    )
                    assert str(raw_search[0]["id"]) == str(generated_id)
                    raw_recall = await memory_recall(
                        pool,
                        SimpleNamespace(embed=lambda _topic: [0.0] * 384),
                        "cobalt lighthouse",
                        limit=10,
                        read_policy=read_policy,
                        memory_schema="chronicler_mem",
                    )
                    assert any(str(row["id"]) == str(generated_id) for row in raw_recall)

                visible_search = await memory_search_tool(
                    pool,
                    None,
                    "cobalt lighthouse",
                    types=["fact"],
                    mode="keyword",
                    limit=1,
                    read_policy=read_policy,
                    memory_schema="chronicler_mem",
                )
                assert len(visible_search) == 1
                assert str(visible_search[0]["id"]) in {str(owner_id), str(ordinary_id)}
                visible_recall = await memory_recall(
                    pool,
                    SimpleNamespace(embed=lambda _topic: [0.0] * 384),
                    "cobalt lighthouse",
                    limit=10,
                    read_policy=read_policy,
                    memory_schema="chronicler_mem",
                )
                assert visible_recall
                assert all(str(row["id"]) != str(generated_id) for row in visible_recall)
                assert {str(row["id"]) for row in visible_recall} & {
                    str(owner_id),
                    str(ordinary_id),
                }

                # Brokered catalog fetch must stop at the actual public-pointer boundary for a
                # hidden row, while an owner pointer still reaches the external route boundary.
                from butlers.config import ButlerType
                from butlers.core_tools import _memory_catalog as catalog_tools
                from butlers.core_tools._base import ToolContext
                from butlers.core_tools._memory_catalog import register_memory_catalog_tools
                from butlers.modules.memory import search as memory_search

                async def _load_read_policy(_pool):
                    return read_policy

                routed: list[dict[str, object]] = []

                async def _dispatch(*_args, **kwargs):
                    routed.append(kwargs)
                    return {"id": str(owner_id), "content": "owner fact"}, None, False

                monkeypatch.setattr(memory_search, "load_catalog_read_policy", _load_read_policy)
                monkeypatch.setattr(catalog_tools, "dispatch_via_switchboard_route", _dispatch)
                registered: dict = {}

                def _core_tool(_group: str):
                    def decorate(function):
                        registered[function.__name__] = function
                        return function

                    return decorate

                register_memory_catalog_tools(
                    ToolContext(
                        daemon=SimpleNamespace(switchboard_client=object()),
                        pool=pool,
                        spawner=None,
                        butler_name="concierge",
                        butler_type=ButlerType.BUTLER,
                        is_switchboard=False,
                        is_messenger=False,
                        route_metrics=None,
                    ),
                    SimpleNamespace(),
                    _core_tool,
                )
                catalog_fetch = registered["memory_catalog_fetch"]
                hidden_fetch = await catalog_fetch(
                    source_schema="chronicler_mem",
                    source_table="facts",
                    source_id=str(generated_id),
                )
                assert hidden_fetch == {"status": "not_found"}
                assert routed == []
                owner_fetch = await catalog_fetch(
                    source_schema="chronicler_mem",
                    source_table="facts",
                    source_id=str(owner_id),
                )
                assert owner_fetch["status"] == "ok"
                assert len(routed) == 1

                # Actual configured local readers must apply the same source disposition
                # before ranking or graph expansion, while preserving ordinary/owner rows.
                with engine.begin() as conn:
                    for fact_id, neighbor_id in (
                        (generated_id, stale_neighbor),
                        (owner_id, owner_neighbor),
                        (ordinary_id, ordinary_neighbor),
                    ):
                        conn.execute(
                            text("""
                                UPDATE chronicler_mem.facts
                                SET entity_id=:entity_id, object_entity_id=:neighbor_id
                                WHERE id=:fact_id
                            """),
                            {
                                "fact_id": fact_id,
                                "entity_id": start_id,
                                "neighbor_id": neighbor_id,
                            },
                        )

                resolved = await entity_resolve(
                    pool, name="reader-source", memory_schema="chronicler_mem"
                )
                assert len(resolved) == 1
                assert resolved[0]["fact_count"] == 2
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_entities_impl,
                        "local_fact_visibility_condition",
                        _omit_local_visibility,
                    )
                    raw_resolved = await entity_resolve(
                        pool, name="reader-source", memory_schema="chronicler_mem"
                    )
                    raw_neighbors = await entity_neighbors(
                        pool,
                        str(start_id),
                        max_depth=1,
                        direction="outgoing",
                        memory_schema="chronicler_mem",
                    )
                assert raw_resolved[0]["fact_count"] == 3
                assert str(stale_neighbor) in {row["entity"]["id"] for row in raw_neighbors}

                visible_neighbors = await entity_neighbors(
                    pool,
                    str(start_id),
                    max_depth=1,
                    direction="outgoing",
                    memory_schema="chronicler_mem",
                )
                visible_neighbor_ids = {row["entity"]["id"] for row in visible_neighbors}
                assert str(stale_neighbor) not in visible_neighbor_ids
                assert {str(owner_neighbor), str(ordinary_neighbor)} <= visible_neighbor_ids

                # Entity listing and detail are separate API fan-outs over the same local
                # canonical facts. Verify both their counts and their bounded fact page.
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_api, "local_fact_visibility_condition", _omit_local_visibility
                    )
                    raw_entity_list = await memory_api.list_entities(
                        q="reader-source",
                        entity_type=None,
                        unidentified=None,
                        archived=False,
                        offset=0,
                        limit=50,
                        db=database,
                    )
                    raw_entity_detail = await memory_api.get_entity(
                        str(start_id), facts_offset=0, facts_limit=20, db=database
                    )
                assert raw_entity_list.meta.total == 1
                assert raw_entity_list.data[0].fact_count == 3
                assert raw_entity_detail.data.fact_count == 3
                assert raw_entity_detail.data.recent_facts_total == 3
                assert str(generated_id) in {
                    str(row.id) for row in raw_entity_detail.data.recent_facts
                }

                visible_entity_list = await memory_api.list_entities(
                    q="reader-source",
                    entity_type=None,
                    unidentified=None,
                    archived=False,
                    offset=0,
                    limit=50,
                    db=database,
                )
                visible_entity_detail = await memory_api.get_entity(
                    str(start_id), facts_offset=0, facts_limit=20, db=database
                )
                assert visible_entity_list.meta.total == 1
                assert visible_entity_list.data[0].fact_count == 2
                assert visible_entity_detail.data.fact_count == 2
                assert visible_entity_detail.data.recent_facts_total == 2
                visible_fact_ids = {str(row.id) for row in visible_entity_detail.data.recent_facts}
                assert str(generated_id) not in visible_fact_ids
                assert {str(owner_id), str(ordinary_id)} <= visible_fact_ids

                # Memory-context assembly uses both profile and recall readers. The bypass
                # demonstrates the generated row is a real candidate before the fixed filter.
                context_engine = SimpleNamespace(embed=lambda _topic: [0.0] * 384)
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        memory_context_impl,
                        "local_fact_visibility_condition",
                        _omit_local_visibility,
                    )
                    bypass.setattr(
                        memory_search_impl,
                        "local_fact_visibility_condition",
                        _omit_local_visibility,
                    )
                    raw_context = await memory_context(
                        pool,
                        context_engine,
                        "cobalt lighthouse",
                        "chronicler",
                        catalog_read_policy=read_policy,
                        request_context={"tenant_id": "shared"},
                        memory_schema="chronicler_mem",
                    )
                assert "pending generated cobalt lighthouse" in raw_context
                visible_context = await memory_context(
                    pool,
                    context_engine,
                    "cobalt lighthouse",
                    "chronicler",
                    catalog_read_policy=read_policy,
                    request_context={"tenant_id": "shared"},
                    memory_schema="chronicler_mem",
                )
                assert "pending generated cobalt lighthouse" not in visible_context
                assert "owner cobalt lighthouse" in visible_context
                assert "ordinary cobalt lighthouse" in visible_context

                # Aggregate lifecycle counts must match visible rows, not raw public pointers.
                raw_fact_total = await pool.fetchval(
                    "SELECT count(*) FROM public.memory_catalog WHERE memory_type='fact'"
                )
                visible_fact_total = await pool.fetchval(
                    """SELECT count(*) FROM public.memory_catalog AS memory_catalog
                       WHERE memory_catalog.memory_type='fact'
                         AND public.chronicler_catalog_row_visible(memory_catalog.id)"""
                )
                maintenance = await catalog_measurement.collect_catalog_maintenance_observability(
                    pool
                )
                fact_lifecycle = next(
                    row for row in maintenance.lifecycle_counts if row["memory_type"] == "fact"
                )
                assert raw_fact_total == 516
                assert visible_fact_total == raw_fact_total - 1
                assert fact_lifecycle["active_count"] == visible_fact_total

                # The dedicated IVFFlat report uses the same fixed row-ID visibility predicate
                # for its candidate count and for approximate/exact result scopes.
                measurement_request = catalog_measurement.CatalogMeasurementRequest(
                    tenant_id="shared",
                    memory_type="fact",
                    allowed_sensitivities=("normal",),
                    limit=1,
                    query_vectors=((0.0,) * catalog_measurement.CATALOG_EMBEDDING_DIMENSIONS,),
                    exact_candidate_cap=1_000,
                )
                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        catalog_measurement,
                        "catalog_projection_visible",
                        lambda _alias: "TRUE",
                    )
                    raw_measurement = await catalog_measurement.measure_catalog_ivfflat(
                        pool, measurement_request
                    )
                assert raw_measurement.observations[0].filtered_candidate_count == 516
                visible_measurement = await catalog_measurement.measure_catalog_ivfflat(
                    pool, measurement_request
                )
                assert visible_measurement.observations[0].filtered_candidate_count == 515

                # A missing exact canonical grant must fail closed for Chronicler pointers,
                # while unrelated rows remain readable and bounded without private lookups.
                import re
                import time

                visibility_columns = (
                    "id",
                    "source_butler",
                    "predicate",
                    "content_authority",
                    "validity",
                    "invalid_at",
                    "subject",
                    "tags",
                    "metadata",
                )
                column_list = ", ".join(visibility_columns)
                column_filter = ", ".join(f"'{column}'" for column in visibility_columns)
                with engine.begin() as conn:
                    acl_before = conn.execute(
                        text(f"""
                            SELECT attname, COALESCE(attacl::text, '<null>')
                            FROM pg_catalog.pg_attribute
                            WHERE attrelid='chronicler_mem.facts'::regclass
                              AND attnum > 0 AND attname IN ({column_filter})
                            ORDER BY attnum
                        """)
                    ).all()
                    assert len(acl_before) == len(visibility_columns)
                    had_exact_select = conn.execute(
                        text(f"""
                            SELECT bool_and(pg_catalog.has_column_privilege(
                                'chronicler_memory_owner', 'chronicler_mem.facts',
                                attname, 'SELECT'))
                            FROM pg_catalog.pg_attribute
                            WHERE attrelid='chronicler_mem.facts'::regclass
                              AND attnum > 0 AND attname IN ({column_filter})
                        """)
                    ).scalar_one()
                    assert had_exact_select is True
                    conn.exec_driver_sql(
                        "REVOKE SELECT ("
                        + column_list
                        + ") ON TABLE chronicler_mem.facts FROM chronicler_memory_owner"
                    )
                    no_exact_select = conn.execute(
                        text(f"""
                            SELECT bool_and(NOT pg_catalog.has_column_privilege(
                                'chronicler_memory_owner', 'chronicler_mem.facts',
                                attname, 'SELECT'))
                            FROM pg_catalog.pg_attribute
                            WHERE attrelid='chronicler_mem.facts'::regclass
                              AND attnum > 0 AND attname IN ({column_filter})
                        """)
                    ).scalar_one()
                    assert no_exact_select is True
                acl_revoked = True
                try:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await pool.fetchval(
                            "SELECT public.chronicler_catalog_row_visible($1)",
                            catalog_ids[("chronicler_mem", generated_id)],
                        )

                    unrelated_sql = """
                        SELECT count(*)
                        FROM public.memory_catalog AS memory_catalog
                        WHERE memory_catalog.source_schema='unrelated_cost_probe'
                          AND memory_catalog.source_table='facts'
                          AND memory_catalog.tenant_id='shared'
                          AND memory_catalog.id = ANY($1::uuid[])
                          AND public.chronicler_catalog_row_visible(memory_catalog.id)
                    """
                    started = time.perf_counter()
                    plan_rows = await pool.fetch(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + unrelated_sql,
                        cost_catalog_ids,
                        timeout=5.0,
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    plan_text = "\n".join(row["QUERY PLAN"] for row in plan_rows)
                    execution = re.search(r"Execution Time:\s*([0-9.]+)\s*ms", plan_text)
                    assert execution is not None
                    assert float(execution.group(1)) < 5_000
                    assert elapsed_ms < 5_000
                    assert await pool.fetchval(unrelated_sql, cost_catalog_ids, timeout=5.0) == 512
                    print(
                        "F2 unrelated catalog visibility cost: "
                        f"rows=512 plan_ms={float(execution.group(1)):.3f} "
                        f"client_ms={elapsed_ms:.3f}"
                    )
                finally:
                    if acl_revoked:
                        with engine.begin() as conn:
                            conn.exec_driver_sql(
                                "GRANT SELECT ("
                                + column_list
                                + ") ON TABLE chronicler_mem.facts TO chronicler_memory_owner"
                            )
                            acl_after = conn.execute(
                                text(f"""
                                    SELECT attname, COALESCE(attacl::text, '<null>')
                                    FROM pg_catalog.pg_attribute
                                    WHERE attrelid='chronicler_mem.facts'::regclass
                                      AND attnum > 0 AND attname IN ({column_filter})
                                    ORDER BY attnum
                                """)
                            ).all()
                            assert acl_after == acl_before

                assert await pool.fetchval(
                    "SELECT public.chronicler_catalog_row_visible($1)",
                    catalog_ids[("chronicler_mem", owner_id)],
                )
                assert await pool.fetchval(
                    "SELECT public.chronicler_catalog_row_visible($1)",
                    catalog_ids[("chronicler_mem", ordinary_id)],
                )

                # Preferences are an ordinary-fact local reader; retain a real owner preference
                # while the same source still contains the pending generated candidate.
                with engine.begin() as conn:
                    conn.execute(
                        text("""INSERT INTO chronicler_mem.facts
                            (id, subject, predicate, content, source_butler,
                             content_authority, validity, entity_id, scope, metadata)
                            VALUES (:id, 'owner', 'preferences:travel_flight_seat', 'window',
                                    'chronicler', 'owner', 'active', :entity, 'travel', '{}'::jsonb)"""),
                        {"id": preference_id, "entity": preference_owner_id},
                    )
                preferences = await get_preferences(pool, memory_schema="chronicler_mem")
                assert [(item["predicate"], item["value"]) for item in preferences] == [
                    ("preferences:travel_flight_seat", "window")
                ]

                # The data export is a real canonical facts reader too. Pending generated
                # facts must not reappear in its NDJSON member while ordinary facts survive.
                import io
                import json
                import zipfile

                async def _omit_export_visibility(*_args, **_kwargs):
                    return None

                with monkeypatch.context() as bypass:
                    bypass.setattr(
                        data_ops,
                        "local_fact_visibility_condition",
                        _omit_export_visibility,
                        raising=False,
                    )
                    raw_export = await data_ops._build_export_zip(pool, "memory", database)
                with zipfile.ZipFile(io.BytesIO(raw_export)) as archive:
                    raw_export_facts = [
                        json.loads(line)
                        for line in archive.read("chronicler_memory_facts.ndjson")
                        .decode()
                        .splitlines()
                    ]
                raw_export_ids = {str(row["id"]) for row in raw_export_facts}
                assert str(generated_id) in raw_export_ids

                export_bytes = await data_ops._build_export_zip(pool, "memory", database)
                with zipfile.ZipFile(io.BytesIO(export_bytes)) as archive:
                    exported_facts = [
                        json.loads(line)
                        for line in archive.read("chronicler_memory_facts.ndjson")
                        .decode()
                        .splitlines()
                    ]
                exported_ids = {str(row["id"]) for row in exported_facts}
                assert {str(owner_id), str(ordinary_id)} <= exported_ids
                assert str(generated_id) not in exported_ids
            finally:
                await pool.close()

        asyncio.run(scenario())
    finally:
        admin_engine.dispose()
        engine.dispose()


def test_drop_transition_count_uses_actual_schema_runtime_role_and_inclusive_window(
    postgres_container,
):
    """Dedicated nullable times are queried on actual migrated tables, not mocked SQL."""
    from butlers.core_tools._memory_access import _query_drops_7d

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            columns = conn.execute(
                text("""SELECT table_name, column_name, is_nullable, column_default, data_type
                    FROM information_schema.columns
                    WHERE table_schema='chronicler_mem'
                    AND (table_name,column_name) IN
                        (('facts','dropped_at'),('rules','forgotten_at'))
                    ORDER BY table_name""")
            ).all()
            assert columns == [
                ("facts", "dropped_at", "YES", None, "timestamp with time zone"),
                ("rules", "forgotten_at", "YES", None, "timestamp with time zone"),
            ]

        async def scenario():
            conn = await asyncpg.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
            try:
                await register_jsonb_codec(conn)
                async with conn.transaction():
                    await conn.execute("SET LOCAL search_path TO chronicler_mem, public")
                    as_of = await conn.fetchval("SELECT now()")
                    cutoff = as_of - timedelta(days=7)
                    for validity, when in [
                        ("superseded", cutoff),
                        ("expired", as_of),
                        ("expired", cutoff - timedelta(microseconds=1)),
                        ("expired", as_of + timedelta(microseconds=1)),
                        ("superseded", None),
                        ("active", as_of),
                        ("retracted", as_of),
                    ]:
                        await conn.execute(
                            "INSERT INTO facts(subject,predicate,content,validity,dropped_at) "
                            "VALUES('native count','ordinary_test','synthetic',$1,$2)",
                            validity,
                            when,
                        )
                    for when in [
                        cutoff,
                        as_of,
                        None,
                        cutoff - timedelta(microseconds=1),
                        as_of + timedelta(microseconds=1),
                    ]:
                        await conn.execute(
                            "INSERT INTO episodes(butler,content,expires_at) "
                            "VALUES('chronicler','synthetic',$1)",
                            when,
                        )
                    for forgotten, when in [
                        (True, cutoff),
                        (True, as_of),
                        (True, None),
                        (True, cutoff - timedelta(microseconds=1)),
                        (True, as_of + timedelta(microseconds=1)),
                        (False, as_of),
                    ]:
                        await conn.execute(
                            "INSERT INTO rules(content,metadata,forgotten_at) "
                            "VALUES('synthetic',$1::jsonb,$2)",
                            {"forgotten": forgotten},
                            when,
                        )
                    await conn.execute("SET LOCAL ROLE butler_chronicler_rw")
                    assert await conn.fetchval("SELECT current_user") == "butler_chronicler_rw"
                    assert await _query_drops_7d(conn, memory_schema="chronicler_mem") == 6
                    # Legacy terminal NULL remains NULL and stays outside the integer count.
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM facts WHERE validity='superseded' "
                            "AND dropped_at IS NULL"
                        )
                        == 1
                    )
            finally:
                await conn.close()

        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_rule_transition_writers_preserve_initial_duplicate_concurrent_and_rollback_times(
    postgres_container,
):
    """Real storage operations preserve OLD persisted state, including legacy NULL."""
    from butlers.modules.memory import storage

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)

    class Embedding:
        model_name = "native-transition-control"

        def embed(self, _text):
            return [0.0] * 384

    async def scenario():
        async def runtime(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler_mem, public"},
            init=runtime,
            min_size=2,
            max_size=2,
        )
        try:
            initial = await storage.store_rule(
                pool,
                "initial forgotten",
                Embedding(),
                metadata={"forgotten": True},
                content_authority="system",
            )
            normal = await storage.store_rule(
                pool,
                "normal",
                Embedding(),
                content_authority="system",
            )
            initial_row = await pool.fetchrow(
                "SELECT created_at,forgotten_at FROM rules WHERE id=$1",
                initial,
            )
            assert initial_row["forgotten_at"] == initial_row["created_at"]
            assert await pool.fetchval("SELECT forgotten_at FROM rules WHERE id=$1", normal) is None
            results = await asyncio.gather(
                storage._forget_plain(pool, "rule", normal, memory_schema="chronicler_mem"),
                storage._forget_plain(pool, "rule", normal, memory_schema="chronicler_mem"),
            )
            assert results == [True, True]
            first = await pool.fetchval("SELECT forgotten_at FROM rules WHERE id=$1", normal)
            assert first is not None
            assert await storage._forget_with_correction_provenance(
                pool,
                "rule",
                normal,
                correction_id="native-duplicate",
                correction_reason=None,
                memory_schema="chronicler_mem",
            )
            assert (
                await pool.fetchval("SELECT forgotten_at FROM rules WHERE id=$1", normal) == first
            )
            legacy = await pool.fetchval(
                "INSERT INTO rules(content,metadata) VALUES('legacy','{\"forgotten\":true}') "
                "RETURNING id"
            )
            assert await storage._forget_plain(
                pool,
                "rule",
                legacy,
                memory_schema="chronicler_mem",
            )
            assert await storage._forget_with_correction_provenance(
                pool,
                "rule",
                legacy,
                correction_id="native-legacy",
                correction_reason=None,
                memory_schema="chronicler_mem",
            )
            assert await pool.fetchval("SELECT forgotten_at FROM rules WHERE id=$1", legacy) is None
            rollback = await pool.fetchval(
                "INSERT INTO rules(content) VALUES('rollback') RETURNING id"
            )
            with engine.begin() as conn:
                conn.exec_driver_sql("""CREATE FUNCTION chronicler_mem.reject_transition_audit()
                    RETURNS trigger LANGUAGE plpgsql AS $$BEGIN
                    RAISE EXCEPTION 'positioned transition audit refusal'; END$$""")
                conn.exec_driver_sql("""CREATE TRIGGER reject_transition_audit BEFORE INSERT
                    ON chronicler_mem.memory_events FOR EACH ROW
                    EXECUTE FUNCTION chronicler_mem.reject_transition_audit()""")
            try:
                with pytest.raises(asyncpg.RaiseError, match="positioned transition audit refusal"):
                    await storage._forget_with_correction_provenance(
                        pool,
                        "rule",
                        rollback,
                        correction_id="native-rollback",
                        correction_reason=None,
                        memory_schema="chronicler_mem",
                    )
                row = await pool.fetchrow(
                    "SELECT metadata,forgotten_at FROM rules WHERE id=$1", rollback
                )
                assert row["forgotten_at"] is None
                assert row["metadata"].get("forgotten") is not True
            finally:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "DROP TRIGGER reject_transition_audit ON chronicler_mem.memory_events"
                    )
                    conn.exec_driver_sql("DROP FUNCTION chronicler_mem.reject_transition_audit()")
        finally:
            await pool.close()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_drop_timestamp_migration_preserves_legacy_null_and_roundtrips(postgres_container):
    """An actual mem017→018 upgrade adds no inferred terminal history."""
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "chronicler"],
        schemas={"core": "chronicler_mem", "memory": "chronicler_mem", "chronicler": "chronicler"},
        revisions={"memory": "mem_017", "chronicler": "chronicler_029"},
    )
    engine = create_engine(url)
    config = _build_alembic_config(url, chains=["memory"], target_schema="chronicler_mem")
    try:
        with engine.begin() as conn:
            fact_id = conn.execute(
                text("""INSERT INTO chronicler_mem.facts
                (subject,predicate,content,validity,invalid_at)
                VALUES('legacy','ordinary_test','synthetic','superseded','2000-01-01Z')
                RETURNING id""")
            ).scalar_one()
            rule_id = conn.execute(
                text("""INSERT INTO chronicler_mem.rules(content,metadata)
                VALUES('legacy',jsonb_build_object('forgotten',true)) RETURNING id""")
            ).scalar_one()
        for _ in range(2):
            command.upgrade(config, "memory@mem_018")
            with engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT dropped_at FROM chronicler_mem.facts WHERE id=:id"),
                        {"id": fact_id},
                    ).scalar_one()
                    is None
                )
                assert (
                    conn.execute(
                        text("SELECT forgotten_at FROM chronicler_mem.rules WHERE id=:id"),
                        {"id": rule_id},
                    ).scalar_one()
                    is None
                )
                assert conn.execute(
                    text("SELECT invalid_at FROM chronicler_mem.facts WHERE id=:id"),
                    {"id": fact_id},
                ).scalar_one() == datetime(2000, 1, 1, tzinfo=UTC)
            command.downgrade(config, "mem_017")
        command.upgrade(config, "memory@mem_018")
    finally:
        engine.dispose()


def test_counted_fact_writers_and_decay_stamp_actual_transition_rows(
    postgres_container, monkeypatch
):
    """Exercise real writer SQL on canonical tables; embedding is the only provider seam."""
    from uuid import UUID

    from butlers.entity_fact_repoint import repoint_facts_on_conn
    from butlers.modules.memory import storage
    from butlers.tools.finance.alerts import alert_configure
    from butlers.tools.relationship import facts as quick_facts
    from butlers.tools.relationship import gifts, loans

    url = _migrated_chronicler_db(postgres_container, "chronicler_030")

    class Embedding:
        model_name = "native-writer-transition"

        def embed(self, _text):
            return [0.1] * 384

    monkeypatch.setattr(gifts, "_get_embedding_engine", lambda: Embedding())
    monkeypatch.setattr(loans, "_get_embedding_engine", lambda: Embedding())

    async def scenario():
        async def runtime(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler_mem, public"},
            init=runtime,
            min_size=1,
            max_size=3,
        )
        try:
            entity, other = uuid4(), uuid4()
            for key in (entity, other):
                await pool.execute(
                    "INSERT INTO public.entities(id,canonical_name,entity_type) VALUES($1,$2,'person')",
                    key,
                    f"synthetic transition {key}",
                )
            await pool.execute("""INSERT INTO predicate_registry(name,is_edge,inverse_of)
                VALUES('native_parent',true,'native_child'),('native_child',true,'native_parent')""")
            legacy_fact = await pool.fetchval(
                "INSERT INTO facts(subject,predicate,content,entity_id,object_entity_id,validity) "
                "VALUES('native source','native_parent','legacy',$1,$2,'superseded') RETURNING id",
                entity,
                other,
            )
            first = await storage.store_fact(
                pool,
                "native source",
                "native_parent",
                "old value",
                Embedding(),
                entity_id=entity,
                object_entity_id=other,
                content_authority="system",
            )
            old_inverse = await pool.fetchval(
                "SELECT id FROM facts WHERE entity_id=$1 AND predicate='native_child' "
                "AND validity='active'",
                other,
            )
            await storage.store_fact(
                pool,
                "native source",
                "native_parent",
                "new value",
                Embedding(),
                entity_id=entity,
                object_entity_id=other,
                content_authority="system",
            )
            for key in (first["id"], old_inverse):
                row = await pool.fetchrow(
                    "SELECT validity,dropped_at,invalid_at FROM facts WHERE id=$1", key
                )
                assert row["validity"] == "superseded"
                assert row["dropped_at"] is not None
                assert row["dropped_at"] == row["invalid_at"]
            assert (
                await pool.fetchval(
                    "SELECT dropped_at FROM facts WHERE id=$1",
                    legacy_fact,
                )
                is None
            )
            alert = await alert_configure(pool, "large_transaction", threshold=10)
            await alert_configure(pool, "large_transaction", threshold=20)
            assert await pool.fetchval(
                "SELECT dropped_at IS NOT NULL FROM facts WHERE id=$1", UUID(alert["fact_id"])
            )
            quick = await quick_facts._fact_set_spo(pool, entity, "native_color", "old")
            await quick_facts._fact_set_spo(pool, entity, "native_color", "new")
            assert await pool.fetchval(
                "SELECT dropped_at IS NOT NULL FROM facts WHERE id=$1", quick["id"]
            )
            for kind, module in [("loan", loans), ("gift", gifts)]:
                key = await pool.fetchval(
                    "INSERT INTO facts(subject,predicate,content,scope,entity_id,valid_at,metadata) "
                    "VALUES($1,$2,'synthetic','relationship',$3,now(),$4) RETURNING id",
                    f"entity:{entity}:{kind}:native",
                    kind,
                    entity,
                    {"status": "idea"} if kind == "gift" else {"settled": False},
                )
                if kind == "gift":
                    await module.gift_update_status(pool, key, "purchased")
                else:
                    await module.loan_settle(pool, key)
                row = await pool.fetchrow(
                    "SELECT dropped_at,invalid_at FROM facts WHERE id=$1", key
                )
                assert row["dropped_at"] is not None
                assert row["dropped_at"] == row["invalid_at"]
            # All four confidence conflict branches of the actual repoint primitive.
            for edge in (False, True):
                for source_wins in (False, True):
                    src, dst, anchor = uuid4(), uuid4(), uuid4()
                    for key in (src, dst, anchor):
                        await pool.execute(
                            "INSERT INTO public.entities(id,canonical_name,entity_type) VALUES($1,$2,'person')",
                            key,
                            f"synthetic repoint {key}",
                        )
                    keys = []
                    for index, key in enumerate((src, dst)):
                        keys.append(
                            await pool.fetchval(
                                "INSERT INTO facts(subject,predicate,content,entity_id,object_entity_id,confidence) "
                                "VALUES($1,$2,'synthetic',$3,$4,$5) RETURNING id",
                                f"repoint:{key}",
                                f"native_repoint_{src}",
                                anchor if edge else key,
                                key if edge else None,
                                (0.9 if source_wins else 0.1) if index == 0 else 0.5,
                            )
                        )
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            stats = await repoint_facts_on_conn(conn, src, dst)
                    assert stats["edge_facts_superseded" if edge else "facts_superseded"] == 1
                    loser = keys[1] if source_wins else keys[0]
                    assert await pool.fetchval(
                        "SELECT dropped_at IS NOT NULL FROM facts WHERE id=$1", loser
                    )
            expired_fact = await pool.fetchval("""INSERT INTO facts
                (subject,predicate,content,confidence,decay_rate,created_at,retention_class)
                VALUES('decay','native_decay','synthetic',0.001,1,now()-interval '10 days','native_test')
                RETURNING id""")
            archive_class = await pool.fetchval(
                "SELECT retention_class FROM memory_policies WHERE archive_before_delete AND min_retrieval_confidence > 0 LIMIT 1"
            )
            assert archive_class is not None
            archived_fact = await pool.fetchval(
                """INSERT INTO facts
                (subject,predicate,content,confidence,decay_rate,created_at,retention_class)
                VALUES('archive decay','native_archive_decay','synthetic',0.001,1,
                       now()-interval '10 days',$1) RETURNING id""",
                archive_class,
            )
            expired_rule = await pool.fetchval("""INSERT INTO rules
                (content,confidence,decay_rate,created_at,retention_class)
                VALUES('synthetic',0.001,1,now()-interval '10 days','native_test') RETURNING id""")
            stats = await storage.run_decay_sweep(pool)
            assert stats["facts_expired"] >= 1
            assert stats["rules_expired"] >= 1
            fact_time = await pool.fetchval(
                "SELECT dropped_at FROM facts WHERE id=$1", expired_fact
            )
            rule_time = await pool.fetchval(
                "SELECT forgotten_at FROM rules WHERE id=$1", expired_rule
            )
            assert fact_time is not None and fact_time == rule_time
            assert (
                await pool.fetchval("SELECT dropped_at FROM facts WHERE id=$1", archived_fact)
                == fact_time
            )
            await storage.run_decay_sweep(pool)
            assert (
                await pool.fetchval("SELECT dropped_at FROM facts WHERE id=$1", expired_fact)
                == fact_time
            )
            assert (
                await pool.fetchval("SELECT forgotten_at FROM rules WHERE id=$1", expired_rule)
                == rule_time
            )
        finally:
            await pool.close()

    asyncio.run(scenario())
