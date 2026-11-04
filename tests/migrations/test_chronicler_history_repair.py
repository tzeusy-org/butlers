"""Native historical cache invalidation/replay/atomic-day proof for PR5471."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration


def test_old_spotify_cache_repair_preserves_steam_unrelated_prose_and_health(postgres_container):
    """A >7-day cache must converge without rewriting historic feeder health."""
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
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

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_030"},
    )

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

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_030"},
    )

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
