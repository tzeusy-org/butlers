"""Chronicler's source-owned public projection tables (REQ-public-mcp-projection-006).

Everything runs under ``butler_chronicler_rw``, the role the jobs, tools and dashboard route use,
so a missing grant fails here and not on the first override in production.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from alembic import command
from butlers.chronicler.adapters.calendar import CalendarCompletedAdapter
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Override, OverrideTarget, Privacy
from butlers.chronicler.public_projection import sweep_public_projection
from butlers.chronicler.storage import insert_override, upsert_episode
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.db import open_pool
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.chronicler._private_memory_setup import prepare_chronicler_current_head_sync
from tests.chronicler.test_calendar_adapter import _instance_row
from tests.migrations.test_public_projection_source import Projection, _asyncpg_url
from tests.migrations.test_public_projection_source import (
    logical_postgres as logical_postgres,  # noqa: F401
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_ZONE = ZoneInfo("Asia/Singapore")
_SLEEP = ("google_health.measurements", "sleep_episode")
_CALENDAR = "google_calendar.completed"
_PROJECTION_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('butlers:chronicler:public-projection', 0))"
)
_days = iter(range(1000))


def _new_day() -> date:
    """A local day no other test in this module's shared database writes to."""
    return date(2024, 1, 1) + timedelta(days=next(_days))


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_ZONE).astimezone(UTC)


def _chronicler_url(fresh_db) -> str:
    url = fresh_db(
        "chronicler",
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    prepare_chronicler_current_head_sync(url, domain_schema="chronicler")
    return url


@pytest.fixture
async def chronicler_pool(db, fresh_db) -> asyncpg.Pool:
    async def _as_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_chronicler_rw")

    url = await asyncio.to_thread(_chronicler_url, fresh_db)
    await seed_source_registry(await db.open(url, schema="chronicler"), sources=INITIAL_SOURCES)
    return await db.open(url, schema="chronicler", init=_as_runtime_role, max_size=6)


@pytest.fixture(scope="module")
def chronicler_projection(logical_postgres) -> Iterator[Projection]:
    """Reuse the real PG17 logical cluster and subscriber barrier from Package A.

    The manifest/generator belongs to .4; these explicit tables/columns/filter
    implement RFC0039 D2's Chronicler transport contract without that dependency.
    """
    source_name, subscriber_name = migration_db_name(), migration_db_name()
    url = create_migrated_test_db(
        logical_postgres,
        source_name,
        ["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    prepare_chronicler_current_head_sync(url, domain_schema="chronicler")
    source_url = _asyncpg_url(logical_postgres, source_name)
    subscriber_url = _asyncpg_url(logical_postgres, subscriber_name)
    subscriber_created = False
    slot_created = False

    async def prepare() -> None:
        nonlocal subscriber_created, slot_created
        admin = await asyncpg.connect(_asyncpg_url(logical_postgres, "postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{subscriber_name}"')
            subscriber_created = True
            port = await admin.fetchval("SHOW port")
        finally:
            await admin.close()
        source = await asyncpg.connect(source_url)
        try:
            await source.execute("CREATE TABLE public.projection_barrier (id UUID PRIMARY KEY)")
            await source.execute("""
                CREATE PUBLICATION chronicler_public_test FOR
                    TABLE chronicler.episodes_public (
                        id, started_at, ended_at, kind, title, category,
                        effective_privacy, tombstoned
                    ) WHERE (effective_privacy IN ('normal', 'sensitive') AND tombstoned = false),
                    TABLE chronicler.rollups_public,
                    TABLE public.projection_barrier
            """)
            # Separate slot creation avoids a same-cluster CREATE SUBSCRIPTION deadlock.
            await source.fetchval(
                "SELECT pg_create_logical_replication_slot('chronicler_public_test', 'pgoutput')"
            )
            slot_created = True
        finally:
            await source.close()
        subscriber = await asyncpg.connect(subscriber_url)
        try:
            await subscriber.execute("""
                CREATE SCHEMA chronicler;
                CREATE TABLE chronicler.episodes_public (
                    id UUID PRIMARY KEY, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
                    kind TEXT, title TEXT, category TEXT, effective_privacy TEXT, tombstoned BOOLEAN
                );
                CREATE TABLE chronicler.rollups_public (
                    local_date DATE, group_kind TEXT, group_key TEXT, minutes INTEGER,
                    PRIMARY KEY (local_date, group_kind, group_key)
                );
                CREATE TABLE public.projection_barrier (id UUID PRIMARY KEY)
            """)
            conninfo = (
                f"host=127.0.0.1 port={port} dbname={source_name} "
                f"user={logical_postgres.username} password={logical_postgres.password}"
            )
            await subscriber.execute(
                f"CREATE SUBSCRIPTION chronicler_public_test CONNECTION '{conninfo}' "
                "PUBLICATION chronicler_public_test "
                "WITH (create_slot = false, slot_name = 'chronicler_public_test')"
            )
            # Initial copies use separate table-sync workers. A barrier proves
            # cross-table commit order only once all three tables are streaming.
            async with asyncio.timeout(30):
                while not await subscriber.fetchval(  # noqa: ASYNC110 -- external PostgreSQL table-sync state
                    """
                    SELECT count(*) = 3 AND bool_and(srsubstate = 'r')
                    FROM pg_subscription_rel
                    WHERE srsubid = (
                        SELECT oid FROM pg_subscription WHERE subname = 'chronicler_public_test'
                    )
                """
                ):
                    await asyncio.sleep(0.05)
        finally:
            await subscriber.close()

    async def disconnect() -> None:
        if subscriber_created:
            subscriber = await asyncpg.connect(subscriber_url)
            try:
                await subscriber.execute("DROP SUBSCRIPTION IF EXISTS chronicler_public_test")
            finally:
                await subscriber.close()
        source = await asyncpg.connect(source_url)
        try:
            # CREATE SUBSCRIPTION may fail after slot creation. It owns the slot
            # only after successful creation; clean that partial setup as well.
            if slot_created:
                await source.execute("""
                    SELECT pg_drop_replication_slot('chronicler_public_test')
                    WHERE EXISTS (
                        SELECT 1 FROM pg_replication_slots WHERE slot_name = 'chronicler_public_test'
                    )
                """)
            await source.execute("DROP PUBLICATION IF EXISTS chronicler_public_test")
        finally:
            await source.close()

    try:
        asyncio.run(prepare())
        projection = Projection(source_url=source_url, subscriber_url=subscriber_url)
        asyncio.run(projection.caught_up())
        yield projection
    finally:
        asyncio.run(disconnect())


async def _episode(
    chronicler_pool: asyncpg.Pool,
    *,
    source: tuple[str, str] = _SLEEP,
    start: datetime,
    end: datetime | None,
    title: str | None = "Slept",
    privacy: Privacy = Privacy.NORMAL,
    layer: Layer = Layer.ACTIVITY,
    payload: dict | None = None,
) -> uuid.UUID:
    episode = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name=source[0],
            source_ref=str(uuid.uuid4()),
            episode_type=source[1],
            start_at=start,
            end_at=end,
            title=title,
            payload=payload or {},
            privacy=privacy,
            layer=layer,
        ),
    )
    assert episode.id is not None
    return episode.id


async def _override(
    conn: asyncpg.Pool | asyncpg.Connection,
    episode_id: uuid.UUID,
    *,
    submitted_by: str = "user",
    **fields: object,
) -> None:
    await insert_override(
        conn,
        Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=episode_id,
            submitted_by=submitted_by,
            **fields,
        ),
    )


async def _row(chronicler_pool: asyncpg.Pool, episode_id: uuid.UUID) -> asyncpg.Record | None:
    return await chronicler_pool.fetchrow("SELECT * FROM episodes_public WHERE id = $1", episode_id)


async def _minutes(chronicler_pool: asyncpg.Pool, day: date) -> dict[tuple[str, str], int]:
    rows = await chronicler_pool.fetch(
        "SELECT group_kind, group_key, minutes FROM rollups_public WHERE local_date = $1", day
    )
    return {(row["group_kind"], row["group_key"]): row["minutes"] for row in rows}


async def test_a_sensitive_episode_is_published_without_its_payload(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(
        chronicler_pool,
        start=_at(day, 1),
        end=_at(day, 8),
        privacy=Privacy.SENSITIVE,
        payload={"heart_rate": 52},
    )
    await sweep_public_projection(chronicler_pool)
    row = await _row(chronicler_pool, episode)
    assert row is not None
    assert dict(row) == {
        "id": episode,
        "started_at": _at(day, 1),
        "ended_at": _at(day, 8),
        "kind": "sleep_episode",
        "title": "Slept",
        "category": "sleep",
        "effective_privacy": "sensitive",
        "tombstoned": False,
    }


async def test_the_evidence_layer_has_no_row(chronicler_pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(
        chronicler_pool, start=_at(day, 1), end=_at(day, 2), layer=Layer.EVIDENCE
    )
    await sweep_public_projection(chronicler_pool)
    assert await _row(chronicler_pool, episode) is None


async def test_a_model_cannot_republish_what_the_owner_restricted(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED
    )
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "restricted"

    await _override(chronicler_pool, episode, corrected_privacy=Privacy.NORMAL)
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "restricted"
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "restricted"
    # The corrected view does take the model's label: this is the one intended difference.
    assert (
        await chronicler_pool.fetchval(
            "SELECT privacy FROM v_episodes_corrected WHERE id = $1", episode
        )
        == "normal"
    )


async def test_a_model_can_narrow_and_the_owner_can_lift(chronicler_pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await _override(chronicler_pool, episode, corrected_privacy=Privacy.SENSITIVE)
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "sensitive"
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED
    )
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_privacy=Privacy.NORMAL
    )
    row = await _row(chronicler_pool, episode)
    assert (row["effective_privacy"], row["tombstoned"]) == ("normal", False)
    await sweep_public_projection(chronicler_pool)
    row = await _row(chronicler_pool, episode)
    assert (row["effective_privacy"], row["tombstoned"]) == ("normal", False)
    assert await _minutes(chronicler_pool, day) == {
        ("category", "sleep"): 60,
        ("kind", "sleep_episode"): 60,
    }


async def test_a_restriction_commits_or_rolls_back_with_its_override(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    assert await _minutes(chronicler_pool, day) == {
        ("category", "sleep"): 60,
        ("kind", "sleep_episode"): 60,
    }

    async with chronicler_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        await _override(conn, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
        row = await conn.fetchrow(
            "SELECT effective_privacy FROM episodes_public WHERE id = $1", episode
        )
        assert row["effective_privacy"] == "restricted"
        assert (
            await conn.fetchval("SELECT count(*) FROM rollups_public WHERE local_date = $1", day)
            == 0
        )
        await transaction.rollback()
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "normal"
    assert await _minutes(chronicler_pool, day) == {
        ("category", "sleep"): 60,
        ("kind", "sleep_episode"): 60,
    }


async def test_a_later_note_cannot_undo_a_tombstone(chronicler_pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_tombstone_at=datetime.now(UTC)
    )
    assert (await _row(chronicler_pool, episode))["tombstoned"] is True
    assert await _minutes(chronicler_pool, day) == {}

    await _override(chronicler_pool, episode, note="this was a nap")
    assert (await _row(chronicler_pool, episode))["tombstoned"] is True
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode))["tombstoned"] is True
    assert await _minutes(chronicler_pool, day) == {}


async def test_a_later_restriction_cannot_undo_a_tombstone(chronicler_pool: asyncpg.Pool) -> None:
    """The later restriction rewrites the redaction's reason, so only overrides still say so."""
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_tombstone_at=datetime.now(UTC)
    )
    await _override(chronicler_pool, episode, corrected_privacy=Privacy.RESTRICTED)
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_privacy=Privacy.NORMAL
    )
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode))["tombstoned"] is True


async def test_a_redacted_source_counts_as_tombstoned_after_the_sweep(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await chronicler_pool.execute(
        "INSERT INTO daily_repair_source_redactions (source_id, reason_code) "
        "VALUES ($1, 'source_tombstone')",
        episode,
    )
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode))["tombstoned"] is True
    assert await _minutes(chronicler_pool, day) == {}


async def test_the_sweep_repairs_drift_and_removes_rows_with_no_source(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await chronicler_pool.execute(
        "UPDATE episodes_public SET title = 'drifted', effective_privacy = 'normal' WHERE id = $1",
        episode,
    )
    await chronicler_pool.execute(
        "UPDATE rollups_public SET minutes = 999 WHERE local_date = $1", day
    )
    result = await sweep_public_projection(chronicler_pool)
    assert result == {"episodes_written": 1, "episodes_removed": 0}
    assert (await _row(chronicler_pool, episode))["title"] == "Slept"
    assert await _minutes(chronicler_pool, day) == {
        ("category", "sleep"): 60,
        ("kind", "sleep_episode"): 60,
    }

    idle = await sweep_public_projection(chronicler_pool)
    assert idle == {"episodes_written": 0, "episodes_removed": 0}


async def test_a_sweep_cannot_overwrite_a_restriction_committing_beside_it(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)

    async with chronicler_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        await _override(conn, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
        sweep = asyncio.create_task(sweep_public_projection(chronicler_pool))
        done, _ = await asyncio.wait({sweep}, timeout=0.5)
        assert not done
        await transaction.commit()
    await sweep
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "restricted"


@pytest.mark.parametrize("contention", ["bare-projection", "waiting-override", "daily-repair"])
async def test_a_projection_waiter_does_not_hold_the_daily_repair_lock(
    chronicler_pool: asyncpg.Pool, contention: str
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    waiting = None
    writer_error = None
    async with chronicler_pool.acquire() as holder, chronicler_pool.acquire() as writer:
        transaction = holder.transaction()
        await transaction.start()
        try:
            await holder.execute(
                "SELECT pg_advisory_xact_lock(1128813128, 5471)"
                if contention == "daily-repair"
                else _PROJECTION_LOCK
            )
            if contention == "waiting-override":
                waiting = asyncio.create_task(
                    _override(
                        writer, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED
                    )
                )
                # Observe the actual backend waiting on this holder, not elapsed time.
                async with asyncio.timeout(5):
                    while holder.get_server_pid() not in await holder.fetchval(
                        "SELECT pg_blocking_pids($1)", writer.get_server_pid()
                    ):
                        if waiting.done():
                            await waiting
                            pytest.fail("the override did not wait for projection serialization")
                        await asyncio.sleep(0)
            try:
                async with asyncio.timeout(5):
                    await _episode(chronicler_pool, start=_at(day, 3), end=_at(day, 4))
            except asyncpg.LockNotAvailableError as error:
                writer_error = error
        finally:
            await transaction.rollback()
            if waiting is not None:
                # Release the real holder before joining, including a failed assertion path.
                await waiting
    if contention == "daily-repair":
        assert writer_error is not None, "genuine daily repair contention must still refuse writes"
    else:
        assert writer_error is None, "a projection waiter must not fail an unrelated episode writer"
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_tombstone_at=datetime.now(UTC)
    )
    await sweep_public_projection(chronicler_pool)
    row = await _row(chronicler_pool, episode)
    assert row["tombstoned"] is True
    if contention == "waiting-override":
        assert row["effective_privacy"] == "restricted"
    # The unrelated writer remains available after settlement, too.
    await _episode(chronicler_pool, start=_at(day, 5), end=_at(day, 6))


async def test_an_override_projects_the_retention_redaction_it_creates(
    chronicler_pool: asyncpg.Pool,
) -> None:
    async with chronicler_pool.acquire() as conn:
        async with conn.transaction():
            # now() is fixed for this transaction. The insert is unexpired even
            # though its actual retention boundary passes before the next one.
            now = await conn.fetchval("SELECT now()")
            expires = now + timedelta(microseconds=1)
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=_SLEEP[0],
                    source_ref=str(uuid.uuid4()),
                    episode_type=_SLEEP[1],
                    start_at=expires - timedelta(days=1),
                    end_at=expires - timedelta(hours=23),
                    title="Retained sleep",
                    retention_days=1,
                    layer=Layer.ACTIVITY,
                ),
            )
            assert not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM daily_repair_source_redactions WHERE source_id = $1)",
                episode.id,
            )
        assert await conn.fetchval("SELECT now() > $1::timestamptz", expires)
        await sweep_public_projection(chronicler_pool)
        assert (await _row(chronicler_pool, episode.id))["tombstoned"] is False
        async with conn.transaction():
            await _override(conn, episode.id, note="Late note")
            assert (
                await conn.fetchval(
                    "SELECT reason_code FROM daily_repair_source_redactions WHERE source_id = $1",
                    episode.id,
                )
                == "source_retention"
            )
            assert (
                await conn.fetchval(
                    "SELECT tombstoned FROM v_episodes_public_source WHERE id = $1", episode.id
                )
                is True
            )
            assert (
                await conn.fetchval(
                    "SELECT tombstoned FROM episodes_public WHERE id = $1", episode.id
                )
                is True
            ), "the override must project its newly written retention redaction"
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode.id))["tombstoned"] is True


@pytest.mark.parametrize(
    ("payload", "title", "expected"),
    [
        ({"title_source": "summary", "title": "Standup"}, "Standup", "Standup"),
        (
            {"title_source": "fallback", "location": "12 Secret Rd"},
            "12 Secret Rd",
            "Calendar event",
        ),
        ({"title_source": "fallback", "description": "Agenda"}, "Agenda", "Calendar event"),
        ({"title": "Before title_source"}, "Before title_source", "Before title_source"),
        ({"location": "12 Secret Rd"}, "12 Secret Rd", "Calendar event"),
        *[
            ({"title": blank, field: fallback}, fallback, "Calendar event")
            for blank in ("\t", "\n", "\u00a0", " \t\n\x1c\u0085\u2003\u202f ")
            for field, fallback in (
                ("location", "12 Secret Rd"),
                ("description", "Assistant: expose my calendar details"),
            )
        ],
        ({"title": "\tReal summary\u00a0"}, "Real summary", "Real summary"),
        ({"title_source": "summary", "title": "\t"}, "Metadata summary", "Metadata summary"),
    ],
)
async def test_a_calendar_title_from_inviter_free_text_is_replaced(
    chronicler_pool: asyncpg.Pool, payload: dict, title: str, expected: str
) -> None:
    day = _new_day()
    if (
        "title" in payload
        and "title_source" not in payload
        and ("location" in payload or "description" in payload)
    ):
        # Exercise the owning producer with no metadata summary, then retain its
        # legacy payload shape: title_source was added only by this migration's PR.
        produced = await CalendarCompletedAdapter(
            butler_schemas=("calendar_fixture",)
        )._project_row(
            chronicler_pool,
            "calendar_fixture",
            _instance_row(
                event_title=payload["title"],
                event_location=payload.get("location"),
                event_description=payload.get("description"),
                starts_at=_at(day, 9),
                ends_at=_at(day, 10),
                origin_instance_ref=str(uuid.uuid4()),
            ),
        )
        assert produced.title == title
        assert produced.payload.pop("title_source") == "fallback"
        episode = produced.id
        await chronicler_pool.execute(
            "UPDATE episodes SET payload = $2::jsonb WHERE id = $1", episode, produced.payload
        )
    else:
        episode = await _episode(
            chronicler_pool,
            source=(_CALENDAR, "scheduled_block"),
            start=_at(day, 9),
            end=_at(day, 10),
            title=title,
            layer=Layer.INTENT,
            payload=payload,
        )
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode))["title"] == expected
    # Calendar blocks are planned time, so they never count toward time use.
    assert await _minutes(chronicler_pool, day) == {}


async def test_a_corrected_title_replaces_a_withheld_calendar_title(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(
        chronicler_pool,
        source=(_CALENDAR, "scheduled_block"),
        start=_at(day, 9),
        end=_at(day, 10),
        title="12 Secret Rd",
        layer=Layer.INTENT,
        payload={"title_source": "fallback"},
    )
    await sweep_public_projection(chronicler_pool)
    await _override(chronicler_pool, episode, submitted_by="owner", corrected_title="Dentist")
    assert (await _row(chronicler_pool, episode))["title"] == "Dentist"


async def test_rollups_union_overlaps_and_split_at_local_midnight(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    next_day = day + timedelta(days=1)
    next(_days)
    await _episode(chronicler_pool, start=_at(day, 22), end=_at(next_day, 1))
    await _episode(chronicler_pool, start=_at(day, 23), end=_at(next_day, 0, 30))
    await _episode(chronicler_pool, start=_at(next_day, 2), end=_at(next_day, 3))
    await _episode(chronicler_pool, start=_at(next_day, 5), end=None)
    await sweep_public_projection(chronicler_pool)
    assert await _minutes(chronicler_pool, day) == {
        ("category", "sleep"): 120,
        ("kind", "sleep_episode"): 120,
    }
    # 00:00-01:00, 02:00-03:00, then the open-ended episode to the end of its first day.
    assert (await _minutes(chronicler_pool, next_day))[("category", "sleep")] == 60 + 60 + 19 * 60


async def test_a_corrected_span_rewrites_the_days_it_left_and_entered(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    later = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await _override(
        chronicler_pool,
        episode,
        submitted_by="owner",
        corrected_start_at=_at(later, 1),
        corrected_end_at=_at(later, 3),
    )
    assert await _minutes(chronicler_pool, day) == {}
    assert (await _minutes(chronicler_pool, later))[("category", "sleep")] == 120


async def test_a_restricted_episode_leaves_its_rollup_in_the_override_transaction(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    kept = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    hidden = await _episode(chronicler_pool, start=_at(day, 5), end=_at(day, 6))
    await sweep_public_projection(chronicler_pool)
    assert (await _minutes(chronicler_pool, day))[("category", "sleep")] == 120
    await _override(
        chronicler_pool, hidden, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED
    )
    assert (await _minutes(chronicler_pool, day))[("category", "sleep")] == 60
    assert (await _row(chronicler_pool, kept))["effective_privacy"] == "normal"


async def test_a_note_on_an_unswept_episode_is_read_by_the_next_sweep(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await _override(
        chronicler_pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED
    )
    assert await _row(chronicler_pool, episode) is None
    await sweep_public_projection(chronicler_pool)
    assert (await _row(chronicler_pool, episode))["effective_privacy"] == "restricted"


async def test_a_deleted_episode_takes_its_row_and_rollup_with_it(
    chronicler_pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(chronicler_pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(chronicler_pool)
    await chronicler_pool.execute("DELETE FROM episodes WHERE id = $1", episode)
    assert await _row(chronicler_pool, episode) is None
    await sweep_public_projection(chronicler_pool)
    assert await _minutes(chronicler_pool, day) == {}


async def test_the_replica_identity_covers_the_published_filter(
    chronicler_pool: asyncpg.Pool,
) -> None:
    identity = await chronicler_pool.fetch(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'chronicler.episodes_public'::regclass AND i.indisreplident
        """
    )
    assert {row["attname"] for row in identity} == {"id", "effective_privacy", "tombstoned"}
    assert (
        await chronicler_pool.fetchval(
            "SELECT relreplident::text FROM pg_class "
            "WHERE oid = 'chronicler.episodes_public'::regclass"
        )
        == "i"
    )


async def test_chronicler_visibility_and_rollups_reach_the_real_subscriber(
    chronicler_projection: Projection,
) -> None:
    """REQ004/005/006: compare subscriber data only after an applied barrier.

    The synthetic publication is scoped to Chronicler, not a claim that .4's
    full manifest, public server or unrelated Package C is implemented.
    """
    seed_pool = await open_pool(chronicler_projection.source_url, schema="chronicler")
    try:
        await seed_source_registry(seed_pool, sources=INITIAL_SOURCES)
    finally:
        await seed_pool.close()

    async def runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_chronicler_rw")

    pool = await open_pool(
        chronicler_projection.source_url, schema="chronicler", init=runtime_role, max_size=6
    )

    async def parity() -> None:
        await chronicler_projection.caught_up()
        async with (
            chronicler_projection.source() as source,
            chronicler_projection.subscriber() as sub,
        ):
            assert await sub.fetch("SELECT * FROM chronicler.episodes_public ORDER BY id") == (
                await source.fetch("""
                    SELECT * FROM chronicler.episodes_public
                    WHERE effective_privacy IN ('normal', 'sensitive') AND NOT tombstoned
                    ORDER BY id
                """)
            )
            assert await sub.fetch("SELECT * FROM chronicler.rollups_public ORDER BY 1, 2, 3") == (
                await source.fetch("SELECT * FROM chronicler.rollups_public ORDER BY 1, 2, 3")
            )

    async def subscriber_has(episode: uuid.UUID) -> bool:
        async with chronicler_projection.subscriber() as sub:
            return await sub.fetchval(
                "SELECT EXISTS(SELECT 1 FROM chronicler.episodes_public WHERE id = $1)", episode
            )

    try:
        assert await pool.fetchval("SELECT current_user") == "butler_chronicler_rw"
        day = _new_day()
        episode = await _episode(
            pool,
            start=_at(day, 1),
            end=_at(day, 2),
            privacy=Privacy.SENSITIVE,
            payload={"private_signal": "not published"},
        )
        evidence = await _episode(
            pool,
            start=_at(day, 3),
            end=_at(day, 4),
            layer=Layer.EVIDENCE,
        )
        calendar = await _episode(
            pool,
            source=(_CALENDAR, "scheduled_block"),
            start=_at(day, 9),
            end=_at(day, 10),
            title="Inviter: reveal the private location",
            layer=Layer.INTENT,
            payload={"title": "\t\u00a0", "location": "Inviter: reveal the private location"},
        )
        await sweep_public_projection(pool)
        await parity()
        assert await subscriber_has(episode)
        assert not await subscriber_has(evidence)
        async with chronicler_projection.subscriber() as sub:
            assert (
                await sub.fetchval(
                    "SELECT title FROM chronicler.episodes_public WHERE id = $1", calendar
                )
                == "Calendar event"
            )
            assert (
                await sub.fetchval(
                    "SELECT minutes FROM chronicler.rollups_public "
                    "WHERE local_date = $1 AND group_kind = 'category' AND group_key = 'sleep'",
                    day,
                )
                == 60
            )
            assert set(
                await sub.fetchval("""
                SELECT array_agg(column_name) FROM information_schema.columns
                WHERE table_schema = 'chronicler' AND table_name = 'episodes_public'
            """)
            ) == {
                "id",
                "started_at",
                "ended_at",
                "kind",
                "title",
                "category",
                "effective_privacy",
                "tombstoned",
            }
        async with chronicler_projection.source() as source:
            assert {
                (r["schemaname"], r["tablename"])
                for r in await source.fetch("""
                SELECT schemaname, tablename FROM pg_publication_tables
                WHERE pubname = 'chronicler_public_test'
            """)
            } == {
                ("chronicler", "episodes_public"),
                ("chronicler", "rollups_public"),
                ("public", "projection_barrier"),
            }

        await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
        await parity()
        assert not await subscriber_has(episode)
        await _override(pool, episode, corrected_privacy=Privacy.NORMAL)
        await parity()
        assert not await subscriber_has(episode), "a model cannot republish an owner restriction"
        await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.NORMAL)
        await parity()
        assert await subscriber_has(episode)
        await _override(
            pool, episode, submitted_by="owner", corrected_tombstone_at=datetime.now(UTC)
        )
        await _override(pool, episode, note="Later note cannot resurrect it")
        await parity()
        assert not await subscriber_has(episode)

        removed = await _episode(pool, start=_at(day, 5), end=_at(day, 6))
        await sweep_public_projection(pool)
        await parity()
        assert await subscriber_has(removed)
        await pool.execute("DELETE FROM episodes WHERE id = $1", removed)
        await sweep_public_projection(pool)
        await parity()
        assert not await subscriber_has(removed)
        async with chronicler_projection.subscriber() as sub:
            assert (
                await sub.fetchval(
                    "SELECT count(*) FROM chronicler.rollups_public WHERE local_date = $1", day
                )
                == 0
            )
    finally:
        await pool.close()


def test_the_migration_downgrades_and_reinstalls(fresh_db) -> None:
    url = _chronicler_url(fresh_db)
    config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
    command.downgrade(config, "chronicler@chronicler_030")
    asyncio.run(run_migrations(url, chain="chronicler", schema="chronicler"))
