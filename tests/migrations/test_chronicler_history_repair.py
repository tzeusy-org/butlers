"""Native historical cache invalidation/replay/atomic-day proof for PR5471."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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
            assert (
                conn.execute(
                    text("SELECT version_num FROM chronicler.alembic_version")
                ).scalar_one()
                == "chronicler_029"
            )
    finally:
        engine.dispose()


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
            assert (
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
    url = _migrated_chronicler_db(postgres_container, "chronicler_030")
    engine = create_engine(url)
    day = (datetime.now(UTC) - timedelta(days=10)).date()
    at = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=12)
    episode_id = uuid4()
    fact_id = uuid4()
    missing_fact_id = uuid4()
    unrelated_id = uuid4()
    subject_id = uuid4()
    object_id = uuid4()
    try:
        with engine.begin() as conn:
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
                    conn.execute(
                        text("""INSERT INTO public.entity_graph_edges
                            (source_schema, source_table, source_id, subject_entity_id,
                             predicate, object_entity_id)
                            VALUES ('chronicler_mem', 'facts', :id, :subject,
                                    'related_to', :object)"""),
                        {"id": fact_id, "subject": subject_id, "object": object_id},
                    )
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
                    conn.execute(
                        text("""INSERT INTO public.entity_graph_edges
                            (source_schema, source_table, source_id, subject_entity_id,
                             predicate, object_entity_id)
                            VALUES ('chronicler_mem', 'facts', :id, :subject,
                                    'related_to', :object)"""),
                        {"id": missing_fact_id, "subject": subject_id, "object": object_id},
                    )
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

                result = await run_rebuild(pool, now=datetime.now(UTC))
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
        try:
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

            # Start+the episode's declared retention_days has now passed. The
            # scanner's one invocation must tombstone, archive-redact and queue
            # historical numeric work without a guessed/global retention default.
            assert (await run_rebuild(pool, now=future))["status"] == "complete"
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

        async def runtime_connection(conn):
            await register_jsonb_codec(conn)
            await conn.execute("SET ROLE butler_chronicler_rw")

        async def scenario():
            from butlers.chronicler.history_repair import run_rebuild

            pool = await asyncpg.create_pool(
                url.replace("postgresql+psycopg2://", "postgresql://"),
                server_settings={"search_path": "chronicler, public", "statement_timeout": "2s"},
                init=runtime_connection,
            )
            try:
                await pool.execute(
                    "UPDATE daily_repair_control SET replay_ack=replay_epoch WHERE singleton"
                )
                monkeypatch.setattr(history_repair, "MAX_RETENTION_CURSOR_TIMEOUT_MS", 1)
                first = await run_rebuild(pool, now=future)
                assert first["status"] == "pending_retention"
                assert first["retention_timeout"] is True
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
                    """SELECT first_date, cursor_date, timeout_count
                       FROM chronicler.daily_repair_retention_spans
                       WHERE source_id=$1 ORDER BY span_number LIMIT 1""",
                    episode_id,
                )
                assert cursor["cursor_date"] == cursor["first_date"]
                assert cursor["timeout_count"] >= 1
                with pytest.raises(RepairAdmissionError, match="retention repair is pending"):
                    await capture_fact_write_token(
                        pool,
                        day_date=local_day,
                        timezone="Asia/Singapore",
                        baseline_lookback_days=28,
                    )

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
                await pool.close()

        asyncio.run(scenario())
    finally:
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
            assert (
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
                    conn.execute(text("CREATE TEMP TABLE f1_030_observer_control (id integer)"))
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
            assert (
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
    assert "chronicler_029" in initial_versions
    assert "chronicler_030" not in initial_versions
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

    import butlers.lifecycle as lifecycle

    lifecycle_calls: list[tuple[str, str | None]] = []
    real_lifecycle_migrations_run = lifecycle.run_migrations

    async def track_lifecycle_migration(db_url, chain="all", schema=None):
        lifecycle_calls.append((chain, schema))
        return await real_lifecycle_migrations_run(db_url, chain=chain, schema=schema)

    monkeypatch.setattr(lifecycle, "run_migrations", track_lifecycle_migration)
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
