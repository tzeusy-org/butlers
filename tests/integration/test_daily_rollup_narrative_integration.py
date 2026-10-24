"""Real-Postgres integration tests for the daily-rollup narrative columns
and the once-daily labeling pass (bu-v9y18, telemetry-distillation bead 6,
migration chronicler_020).

Mocked-pool tests cannot prove the central design guarantee this bead
depends on: that a label written to ``daily_rollup_flags.narrative`` (or
``daily_rollups.narrative``) survives a *later* re-run of the deterministic
jobs that own those tables (``rollups.materialize_daily_rollups``,
``flags.evaluate_and_write_daily_flags`` — both re-upsert on every run,
hourly, over a trailing window per ``chronicler_rollup_daily``). If either
upsert's SQL ever grows a reference to ``narrative`` in its ``SET`` clause,
a label would be silently wiped within the hour; only a real re-run against
a real Postgres row proves it is not. These tests run the real migration
chain (through chronicler_020) against a migrated Postgres container.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.flags import (
    FLAG_SLEEP_MISSING,
    SOURCE_CRON_MINUTES,
    evaluate_and_write_daily_flags,
)
from butlers.chronicler.models import Episode, Layer
from butlers.chronicler.narration import narrate_daily_rollup
from butlers.chronicler.rollups import materialize_daily_rollups
from butlers.chronicler.storage import (
    list_daily_rollup_flags,
    list_daily_rollups,
    mark_source_active,
    set_daily_rollup_day_narrative,
    set_daily_rollup_flag_narrative,
    upsert_checkpoint,
    upsert_daily_rollup,
    upsert_daily_rollup_flag,
    upsert_episode,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_TZ = ZoneInfo("Asia/Singapore")
_LOCAL_DATE = date(2026, 7, 1)
_OTHER_DATE = date(2026, 7, 2)


def _local(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=_TZ).astimezone(UTC)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE daily_rollup_flags, daily_rollups CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events CASCADE")
    await p.execute("TRUNCATE TABLE source_adapter_state, projection_checkpoints CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _mark_all_healthy(pool, *, now: datetime) -> None:
    """Mark every source the flag rules gate as active with a fresh
    checkpoint relative to *now*, so `evaluate_and_write_daily_flags` never
    suppresses a behavioral flag as feeder-dark in these tests. Mirrors
    ``test_daily_rollup_flags_integration.py``'s helper of the same name."""
    fresh_at = now - timedelta(minutes=1)
    for source_name in SOURCE_CRON_MINUTES:
        await mark_source_active(pool, source_name, active=True)
        await upsert_checkpoint(pool, source_name, watermark=fresh_at, success=True)
        await pool.execute(
            "UPDATE projection_checkpoints SET last_success_at = $2, last_run_at = $2 "
            "WHERE source_name = $1",
            source_name,
            fresh_at,
        )


async def test_narrative_columns_default_to_null(pool) -> None:
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0
    )
    await upsert_daily_rollup_flag(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, severity="warning", detail={}
    )

    rollups = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)

    assert rollups[0].narrative is None
    assert flags[0].narrative is None


async def test_set_day_narrative_writes_every_lane_row_for_the_date_only(pool) -> None:
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0
    )
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="work", seconds=100, episode_count=1
    )
    await upsert_daily_rollup(
        pool, local_date=_OTHER_DATE, lane="sleep", seconds=0, episode_count=0
    )

    rows_updated = await set_daily_rollup_day_narrative(
        pool, local_date=_LOCAL_DATE, narrative="A quiet day."
    )
    assert rows_updated == 2

    same_day_rows = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    assert {r.narrative for r in same_day_rows} == {"A quiet day."}

    other_day_rows = await list_daily_rollups(pool, local_date=_OTHER_DATE)
    assert other_day_rows[0].narrative is None


async def test_set_flag_narrative_targets_only_the_named_flag(pool) -> None:
    await upsert_daily_rollup_flag(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, severity="warning", detail={}
    )
    await upsert_daily_rollup_flag(
        pool, local_date=_LOCAL_DATE, flag_type="routine_break", severity="info", detail={}
    )

    updated = await set_daily_rollup_flag_narrative(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, narrative="No sleep logged."
    )
    assert updated is not None
    assert updated.narrative == "No sleep logged."

    flags_by_type = {
        f.flag_type: f for f in await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    }
    assert flags_by_type[FLAG_SLEEP_MISSING].narrative == "No sleep logged."
    assert flags_by_type["routine_break"].narrative is None


async def test_set_flag_narrative_is_noop_for_nonexistent_flag(pool) -> None:
    result = await set_daily_rollup_flag_narrative(
        pool, local_date=_LOCAL_DATE, flag_type="lane_share_outlier", narrative="whatever"
    )
    assert result is None


async def test_flag_narrative_survives_flag_reevaluation(pool) -> None:
    """The central design guarantee: `flags.py`'s hourly reconciliation must
    never wipe a label written to the dedicated `narrative` column, even
    though it fully overwrites `detail` on every run for a flag that still
    holds."""
    now = _local(_LOCAL_DATE, 23, 0)
    await _mark_all_healthy(pool, now=now)

    # Zero sleep, healthy sources -> sleep_missing holds.
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0
    )
    result = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=now
    )
    assert result["flags"][FLAG_SLEEP_MISSING] is True

    await set_daily_rollup_flag_narrative(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, narrative="No sleep logged."
    )

    # Re-run the deterministic evaluator again (same conditions still hold,
    # mirroring the hourly chronicler_rollup_daily re-evaluation).
    result_again = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=now
    )
    assert result_again["flags"][FLAG_SLEEP_MISSING] is True

    flags_by_type = {
        f.flag_type: f for f in await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    }
    assert flags_by_type[FLAG_SLEEP_MISSING].narrative == "No sleep logged."


async def test_day_narrative_survives_rollup_rematerialization(pool) -> None:
    """Same guarantee, for `rollups.py`'s upsert against `daily_rollups`."""
    d = _LOCAL_DATE
    await upsert_episode(
        pool,
        Episode(
            source_name="steam.play_history",
            source_ref="narration-it-gaming-1",
            episode_type="play_episode",
            start_at=_local(d, 20, 0),
            end_at=_local(d, 22, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    now = _local(d + timedelta(days=1), 1, 0)

    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)
    await set_daily_rollup_day_narrative(
        pool, local_date=d, narrative="Played games in the evening."
    )

    # Re-run the materializer (idempotent re-run over the same trailing window).
    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)

    rows = await list_daily_rollups(pool, local_date=d)
    assert {r.narrative for r in rows} == {"Played games in the evening."}


async def test_narrate_daily_rollup_end_to_end_against_real_rows(pool, monkeypatch) -> None:
    """Exercises the real DB reads/writes of the orchestrator end-to-end;
    the LLM call itself is stubbed (no real network/model dependency in
    integration tests)."""
    d = _LOCAL_DATE
    now = _local(d + timedelta(days=1), 1, 0)

    await _mark_all_healthy(pool, now=now)
    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)
    await evaluate_and_write_daily_flags(pool, local_date=d, timezone="Asia/Singapore", now=now)

    class _StubDispatcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def call(self, prompt: str, system_prompt: str = "") -> str:
            return '{"day_summary": "An ordinary day.", "flag_labels": {}}'

    monkeypatch.setattr("butlers.chronicler.narration.DiscretionDispatcher", _StubDispatcher)

    result = await narrate_daily_rollup(pool, local_date=d, timezone="Asia/Singapore")

    assert result["status"] == "labeled"
    rows = await list_daily_rollups(pool, local_date=d)
    assert {r.narrative for r in rows} == {"An ordinary day."}


@pytest.fixture(scope="module")
async def chronicler_sources(postgres_container):
    """Real core+Chronicler chains and production bootstrap/runtime role.

    Separate from the historical narrative-only fixture above. No substitute
    tables, runtime privilege grants or protected-history cleanup.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from urllib.parse import urlparse

    import psycopg2

    from butlers.db import Database
    from butlers.testing.migration import init_db_sql_for_dbapi, migration_bootstrap_db_url

    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        ["core", "chronicler"],
        {"core": "chronicler", "chronicler": "chronicler"},
    )
    parsed = urlparse(url)

    def bootstrap():
        admin_url = urlparse(migration_bootstrap_db_url(postgres_container, name))
        with psycopg2.connect(admin_url._replace(scheme="postgresql").geturl()) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('butlers.connecting_user', %s, false)", (parsed.username,)
                )
                cursor.execute(init_db_sql_for_dbapi())

    await asyncio.to_thread(bootstrap)

    @asynccontextmanager
    async def administrator():
        admin_url = urlparse(migration_bootstrap_db_url(postgres_container, name))
        connection = await asyncpg.connect(
            admin_url._replace(scheme="postgresql").geturl(), timeout=5
        )
        try:
            await register_jsonb_codec(connection)
            yield connection
        finally:
            await connection.close(timeout=5)

    db = Database(
        db_name=parsed.path.lstrip("/"),
        schema="chronicler",
        role="butler_chronicler_rw",
        host=parsed.hostname,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        min_pool_size=1,
        max_pool_size=4,
        strict_role_enforcement=True,
    )
    try:
        await db.connect()
        await db.pool.execute(
            "INSERT INTO chronicler.scheduled_tasks "
            "(name,cron,dispatch_mode,job_name,timezone,source) "
            "VALUES ('chronicler_narrate_daily','20 1 * * *','job',"
            "'chronicler_narrate_daily','Asia/Singapore','db')"
        )
        yield SimpleNamespace(pool=db.pool, administrator=administrator)
    finally:
        await db.close()


async def _source_day_inputs(pool, local_date):
    from butlers.chronicler.narration import (
        _SYSTEM_PROMPT,
        _fetch_top_episode_titles,
        _local_day_bounds_utc,
        build_narration_prompt,
    )

    await upsert_daily_rollup(
        pool, local_date=local_date, lane="work", seconds=100, episode_count=1
    )
    await upsert_daily_rollup_flag(
        pool, local_date=local_date, flag_type="sleep_missing", severity="warning", detail={}
    )
    rollups = await list_daily_rollups(pool, local_date=local_date)
    flags = await list_daily_rollup_flags(pool, local_date=local_date)
    start, end = _local_day_bounds_utc(local_date, _TZ)
    titles = await _fetch_top_episode_titles(pool, day_start_utc=start, day_end_utc=end)
    return dict(
        local_date=local_date,
        timezone="Asia/Singapore",
        rollup_rows=rollups,
        flag_rows=flags,
        top_episode_titles=titles,
        user_message=build_narration_prompt(
            local_date=local_date, rollup_rows=rollups, flag_rows=flags, top_episode_titles=titles
        ),
        system_prompt=_SYSTEM_PROMPT,
    )


async def test_registered_chronicler_source_commits_before_legacy_model(
    chronicler_sources, monkeypatch
):
    """Actual registered caller and SQL commit; model transport remains replaced."""
    from uuid import UUID

    from butlers import scheduled_jobs
    from butlers.chronicler import jobs
    from butlers.core.obligation_chronicler_sources import JOB

    pool, seen = chronicler_sources.pool, []
    day = date(2025, 1, 1)
    await _source_day_inputs(pool, day)
    monkeypatch.setenv("BUTLERS_CHRONICLER_DARK_PREPARATION", "1")
    monkeypatch.setenv("CHRONICLER_NARRATION_ENABLED", "true")
    monkeypatch.setattr(jobs, "_now", lambda: _local(day + timedelta(days=1), 1))

    class Dispatcher:
        def __init__(self, *_args, **_kwargs):
            pass

        async def call(self, message, *, system_prompt):
            async with pool.acquire() as observer:
                assert await observer.fetchval("SELECT current_user") == "butler_chronicler_rw"
                rows = await observer.fetch(
                    "SELECT body FROM obligation_revisions "
                    "WHERE body->'payload'->'material'->>'local_date'=$1",
                    day.isoformat(),
                )
                assert len(rows) == 1
                assert rows[0]["body"]["family"] == "model_job"
                assert rows[0]["body"]["declaration"]["capabilities"] == []
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM obligation_occurrences WHERE source_id=$1",
                        UUID(rows[0]["body"]["source_id"]),
                    )
                    == 1
                )
            seen.append((message, system_prompt))
            return (
                '{"day_summary":"A quiet day.","flag_labels":{"sleep_missing":"No sleep logged."}}'
            )

    monkeypatch.setattr("butlers.chronicler.narration.DiscretionDispatcher", Dispatcher)
    result = await scheduled_jobs._run_chronicler_narrate_daily_job(pool, None)
    assert result["status"] == "labeled" and len(seen) == 1
    assert JOB == "chronicler_narrate_daily"
    async with pool.acquire() as observer:
        assert (
            await observer.fetchval("SELECT narrative FROM daily_rollups WHERE local_date=$1", day)
            == "A quiet day."
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_source_outbox o JOIN obligation_revisions r "
                "ON r.source_id=o.source_id AND r.revision=o.revision "
                "WHERE o.kind='occurrence_captured' "
                "AND r.body->'payload'->'material'->>'local_date'=$1",
                day.isoformat(),
            )
            == 1
        )


async def test_chronicler_source_concurrent_duplicate_committed_readback(chronicler_sources):
    import asyncio

    from butlers.core.obligation_chronicler_sources import ChroniclerPreparation, ChroniclerRuntime

    pool = chronicler_sources.pool
    inputs = await _source_day_inputs(pool, date(2025, 1, 2))
    async with asyncio.timeout(6):
        first, second = await asyncio.gather(
            ChroniclerPreparation(pool, None).prepare_committed(**inputs),
            ChroniclerPreparation(pool, None).prepare_committed(**inputs),
        )
    assert first == second
    async with pool.acquire() as observer, observer.transaction():
        assert await ChroniclerRuntime().read_submission_in_transaction(observer, first) == first
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_occurrences WHERE source_id=$1",
                first.definition.source_id,
            )
            == 1
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM obligation_source_outbox "
                "WHERE source_id=$1 AND kind='occurrence_captured'",
                first.definition.source_id,
            )
            == 1
        )
        assert first.obligation.capabilities == () and first.obligation.created_exposed
        assert first.definition.creator is None


async def test_chronicler_source_outbox_failure_rolls_back_all_rows(
    chronicler_sources, monkeypatch
):
    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.core.obligation_chronicler_sources import ChroniclerPreparation
    from butlers.core.obligation_sources import ObligationSourceRepository

    pool = chronicler_sources.pool
    inputs = await _source_day_inputs(pool, date(2025, 1, 3))
    original = ObligationSourceRepository._outbox
    source_ids = []

    async def refuse(self, source_id, *args):
        source_ids.append(source_id)
        await original(self, source_id, *args)
        raise OSError("synthetic unavailable outbox acknowledgement")

    monkeypatch.setattr(ObligationSourceRepository, "_outbox", refuse)
    with pytest.raises(SourceUnavailable):
        await ChroniclerPreparation(pool, None).prepare_committed(**inputs)
    async with pool.acquire() as observer:
        for table in (
            "obligation_definitions",
            "obligation_revisions",
            "obligation_source_outbox",
            "obligation_occurrences",
            "obligation_source_commands",
        ):
            key = "id" if table == "obligation_definitions" else "source_id"
            assert (
                await observer.fetchval(
                    f"SELECT count(*) FROM {table} WHERE {key}=$1", source_ids[0]
                )
                == 0
            )
    monkeypatch.setattr(ObligationSourceRepository, "_outbox", original)
    assert await ChroniclerPreparation(pool, None).prepare_committed(**inputs)


async def test_chronicler_source_material_fence_through_commit(chronicler_sources):
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.core.obligation_chronicler_sources import (
        ChroniclerPreparation,
        ChroniclerRuntime,
        _registration,
        reconcile_chronicler_source,
    )
    from butlers.core.obligation_sources import ObligationSourceRepository, SourceConflict
    from tests.polling import poll_until

    pool = chronicler_sources.pool
    inputs = await _source_day_inputs(pool, date(2025, 1, 4))
    first = await ChroniclerPreparation(pool, None).prepare_committed(**inputs)
    task = None
    writer = await pool.acquire()
    try:
        async with pool.acquire() as held, held.transaction():
            holder_pid, contender_pid = (
                await held.fetchval("SELECT pg_backend_pid()"),
                await writer.fetchval("SELECT pg_backend_pid()"),
            )
            assert await ChroniclerRuntime().read_submission_in_transaction(held, first) == first
            task = asyncio.create_task(
                writer.execute(
                    "UPDATE chronicler.daily_rollups SET seconds=101 WHERE local_date=$1",
                    inputs["local_date"],
                )
            )
            async with chronicler_sources.administrator() as monitor:

                async def waiting():
                    if task.done():
                        task.result()
                        pytest.fail("material writer completed before source commit")
                    return await monitor.fetchval(
                        "SELECT $1::int=ANY(pg_blocking_pids($2::int)) "
                        "AND EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$2 "
                        "AND datname=current_database() AND wait_event_type='Lock')",
                        holder_pid,
                        contender_pid,
                    )

                async with asyncio.timeout(3):
                    await poll_until(waiting, interval_s=0.02)
            assert not task.done()
        async with asyncio.timeout(5):
            await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await pool.release(writer)
    async with pool.acquire() as observer, observer.transaction():
        with pytest.raises(SourceConflict, match="material_changed"):
            await ChroniclerRuntime().read_submission_in_transaction(observer, first)
    _, registration = _registration()
    snapshot = SimpleNamespace(
        owner_domain="chronicler",
        source_sha256=registration["source_sha"],
        config_digest=registration["config_digest"],
    )
    async with pool.acquire() as observer, observer.transaction():
        repository = ObligationSourceRepository(observer, "chronicler")
        await repository.lock_git_inventory()
        command = uuid4()
        await repository.read_command(command)
        await repository.lock_source(first.definition.source_id)
        await reconcile_chronicler_source(repository, first.definition, snapshot, command)
    async with pool.acquire() as observer:
        assert (
            await observer.fetchval(
                "SELECT state FROM obligation_definitions WHERE id=$1", first.definition.source_id
            )
            == "deleted"
        )
        assert (
            await observer.fetchval(
                "SELECT state FROM obligation_occurrences WHERE id=$1", first.occurrence_id
            )
            == "cancelled"
        )


async def test_chronicler_source_real_role_and_material_shadow_refuse(chronicler_sources):
    from butlers.core.obligation_chronicler_sources import ChroniclerPreparation, ChroniclerRuntime
    from butlers.core.obligation_sources import SourceConflict

    inputs = await _source_day_inputs(chronicler_sources.pool, date(2025, 1, 5))
    first = await ChroniclerPreparation(chronicler_sources.pool, None).prepare_committed(**inputs)
    async with chronicler_sources.administrator() as admin:
        await admin.execute("SET search_path=chronicler,public")
        async with admin.transaction():
            with pytest.raises(SourceConflict, match="owning_role_required"):
                await ChroniclerRuntime().read_submission_in_transaction(admin, first)
        # Fixture administrator creates a same-backend shadow before entering the
        # actual runtime role; production runtime receives no TEMP privilege.
        await admin.execute("CREATE TEMP TABLE daily_rollups(id uuid)")
        await admin.execute("SET ROLE butler_chronicler_rw")
        async with admin.transaction():
            with pytest.raises(SourceConflict, match="material_shadowed"):
                await ChroniclerRuntime().read_submission_in_transaction(admin, first)
        await admin.execute("RESET ROLE")
