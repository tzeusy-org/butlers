"""Chronicler's source-owned public projection tables (REQ-public-mcp-projection-006).

Everything runs under ``butler_chronicler_rw``, the role the jobs, tools and dashboard route use,
so a missing grant fails here and not on the first override in production.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest
import pytest_asyncio

from alembic import command
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Override, OverrideTarget, Privacy
from butlers.chronicler.public_projection import sweep_public_projection
from butlers.chronicler.storage import insert_override, upsert_episode
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.chronicler._private_memory_setup import prepare_chronicler_current_head_sync

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_ZONE = ZoneInfo("Asia/Singapore")
_SLEEP = ("google_health.measurements", "sleep_episode")
_CALENDAR = "google_calendar.completed"
_days = iter(range(1000))


def _new_day() -> date:
    """A local day no other test in this module's shared database writes to."""
    return date(2024, 1, 1) + timedelta(days=next(_days))


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_ZONE).astimezone(UTC)


@pytest.fixture(scope="module")
def database_url(postgres_container: object) -> Iterator[str]:
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    prepare_chronicler_current_head_sync(url, domain_schema="chronicler")
    yield url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    async def _as_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        await conn.execute("SET search_path = chronicler, public")
        await conn.execute("SET ROLE butler_chronicler_rw")

    dsn = database_url.replace("postgresql+psycopg", "postgresql").replace(
        "postgresql+asyncpg", "postgresql"
    )
    async with asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=6,
        setup=_as_runtime_role,
    ) as created:
        async with asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=1,
            server_settings={"search_path": "chronicler,public"},
        ) as admin:
            await seed_source_registry(admin, sources=INITIAL_SOURCES)
        yield created


async def _episode(
    pool: asyncpg.Pool,
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
        pool,
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


async def _row(pool: asyncpg.Pool, episode_id: uuid.UUID) -> asyncpg.Record | None:
    return await pool.fetchrow("SELECT * FROM episodes_public WHERE id = $1", episode_id)


async def _minutes(pool: asyncpg.Pool, day: date) -> dict[tuple[str, str], int]:
    rows = await pool.fetch(
        "SELECT group_kind, group_key, minutes FROM rollups_public WHERE local_date = $1", day
    )
    return {(row["group_kind"], row["group_key"]): row["minutes"] for row in rows}


async def test_a_sensitive_episode_is_published_without_its_payload(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(
        pool,
        start=_at(day, 1),
        end=_at(day, 8),
        privacy=Privacy.SENSITIVE,
        payload={"heart_rate": 52},
    )
    await sweep_public_projection(pool)
    row = await _row(pool, episode)
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


async def test_the_evidence_layer_has_no_row(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2), layer=Layer.EVIDENCE)
    await sweep_public_projection(pool)
    assert await _row(pool, episode) is None


async def test_a_model_cannot_republish_what_the_owner_restricted(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
    assert (await _row(pool, episode))["effective_privacy"] == "restricted"

    await _override(pool, episode, corrected_privacy=Privacy.NORMAL)
    assert (await _row(pool, episode))["effective_privacy"] == "restricted"
    await sweep_public_projection(pool)
    assert (await _row(pool, episode))["effective_privacy"] == "restricted"
    # The corrected view does take the model's label: this is the one intended difference.
    assert (
        await pool.fetchval("SELECT privacy FROM v_episodes_corrected WHERE id = $1", episode)
        == "normal"
    )


async def test_a_model_can_narrow_and_the_owner_can_lift(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await _override(pool, episode, corrected_privacy=Privacy.SENSITIVE)
    assert (await _row(pool, episode))["effective_privacy"] == "sensitive"
    await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
    await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.NORMAL)
    row = await _row(pool, episode)
    assert (row["effective_privacy"], row["tombstoned"]) == ("normal", False)
    await sweep_public_projection(pool)
    row = await _row(pool, episode)
    assert (row["effective_privacy"], row["tombstoned"]) == ("normal", False)
    assert await _minutes(pool, day) == {("category", "sleep"): 60, ("kind", "sleep_episode"): 60}


async def test_a_restriction_commits_or_rolls_back_with_its_override(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    assert await _minutes(pool, day) == {("category", "sleep"): 60, ("kind", "sleep_episode"): 60}

    async with pool.acquire() as conn:
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
    assert (await _row(pool, episode))["effective_privacy"] == "normal"
    assert await _minutes(pool, day) == {("category", "sleep"): 60, ("kind", "sleep_episode"): 60}


async def test_a_later_note_cannot_undo_a_tombstone(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await _override(pool, episode, submitted_by="owner", corrected_tombstone_at=datetime.now(UTC))
    assert (await _row(pool, episode))["tombstoned"] is True
    assert await _minutes(pool, day) == {}

    await _override(pool, episode, note="this was a nap")
    assert (await _row(pool, episode))["tombstoned"] is True
    await sweep_public_projection(pool)
    assert (await _row(pool, episode))["tombstoned"] is True
    assert await _minutes(pool, day) == {}


async def test_a_later_restriction_cannot_undo_a_tombstone(pool: asyncpg.Pool) -> None:
    """The later restriction rewrites the redaction's reason, so only overrides still say so."""
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await _override(pool, episode, submitted_by="owner", corrected_tombstone_at=datetime.now(UTC))
    await _override(pool, episode, corrected_privacy=Privacy.RESTRICTED)
    await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.NORMAL)
    await sweep_public_projection(pool)
    assert (await _row(pool, episode))["tombstoned"] is True


async def test_a_redacted_source_counts_as_tombstoned_after_the_sweep(
    pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await pool.execute(
        "INSERT INTO daily_repair_source_redactions (source_id, reason_code) "
        "VALUES ($1, 'source_tombstone')",
        episode,
    )
    await sweep_public_projection(pool)
    assert (await _row(pool, episode))["tombstoned"] is True
    assert await _minutes(pool, day) == {}


async def test_the_sweep_repairs_drift_and_removes_rows_with_no_source(
    pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await pool.execute(
        "UPDATE episodes_public SET title = 'drifted', effective_privacy = 'normal' WHERE id = $1",
        episode,
    )
    await pool.execute("UPDATE rollups_public SET minutes = 999 WHERE local_date = $1", day)
    result = await sweep_public_projection(pool)
    assert result["episodes_written"] >= 1
    assert (await _row(pool, episode))["title"] == "Slept"
    assert await _minutes(pool, day) == {("category", "sleep"): 60, ("kind", "sleep_episode"): 60}

    idle = await sweep_public_projection(pool)
    assert idle == {"episodes_written": 0, "episodes_removed": 0}


async def test_a_sweep_cannot_overwrite_a_restriction_committing_beside_it(
    pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)

    async with pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        await _override(conn, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
        sweep = asyncio.create_task(sweep_public_projection(pool))
        done, _ = await asyncio.wait({sweep}, timeout=0.5)
        assert not done
        await transaction.commit()
    await sweep
    assert (await _row(pool, episode))["effective_privacy"] == "restricted"


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
    ],
)
async def test_a_calendar_title_from_inviter_free_text_is_replaced(
    pool: asyncpg.Pool, payload: dict, title: str, expected: str
) -> None:
    day = _new_day()
    episode = await _episode(
        pool,
        source=(_CALENDAR, "scheduled_block"),
        start=_at(day, 9),
        end=_at(day, 10),
        title=title,
        layer=Layer.INTENT,
        payload=payload,
    )
    await sweep_public_projection(pool)
    assert (await _row(pool, episode))["title"] == expected
    # Calendar blocks are planned time, so they never count toward time use.
    assert await _minutes(pool, day) == {}


async def test_a_corrected_title_replaces_a_withheld_calendar_title(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(
        pool,
        source=(_CALENDAR, "scheduled_block"),
        start=_at(day, 9),
        end=_at(day, 10),
        title="12 Secret Rd",
        layer=Layer.INTENT,
        payload={"title_source": "fallback"},
    )
    await sweep_public_projection(pool)
    await _override(pool, episode, submitted_by="owner", corrected_title="Dentist")
    assert (await _row(pool, episode))["title"] == "Dentist"


async def test_rollups_union_overlaps_and_split_at_local_midnight(pool: asyncpg.Pool) -> None:
    day = _new_day()
    next_day = day + timedelta(days=1)
    next(_days)
    await _episode(pool, start=_at(day, 22), end=_at(next_day, 1))
    await _episode(pool, start=_at(day, 23), end=_at(next_day, 0, 30))
    await _episode(pool, start=_at(next_day, 2), end=_at(next_day, 3))
    await _episode(pool, start=_at(next_day, 5), end=None)
    await sweep_public_projection(pool)
    assert await _minutes(pool, day) == {
        ("category", "sleep"): 120,
        ("kind", "sleep_episode"): 120,
    }
    # 00:00-01:00, 02:00-03:00, then the open-ended episode to the end of its first day.
    assert (await _minutes(pool, next_day))[("category", "sleep")] == 60 + 60 + 19 * 60


async def test_a_corrected_span_rewrites_the_days_it_left_and_entered(pool: asyncpg.Pool) -> None:
    day = _new_day()
    later = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await _override(
        pool,
        episode,
        submitted_by="owner",
        corrected_start_at=_at(later, 1),
        corrected_end_at=_at(later, 3),
    )
    assert await _minutes(pool, day) == {}
    assert (await _minutes(pool, later))[("category", "sleep")] == 120


async def test_a_restricted_episode_leaves_its_rollup_in_the_override_transaction(
    pool: asyncpg.Pool,
) -> None:
    day = _new_day()
    kept = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    hidden = await _episode(pool, start=_at(day, 5), end=_at(day, 6))
    await sweep_public_projection(pool)
    assert (await _minutes(pool, day))[("category", "sleep")] == 120
    await _override(pool, hidden, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
    assert (await _minutes(pool, day))[("category", "sleep")] == 60
    assert (await _row(pool, kept))["effective_privacy"] == "normal"


async def test_a_note_on_an_unswept_episode_is_read_by_the_next_sweep(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await _override(pool, episode, submitted_by="owner", corrected_privacy=Privacy.RESTRICTED)
    assert await _row(pool, episode) is None
    await sweep_public_projection(pool)
    assert (await _row(pool, episode))["effective_privacy"] == "restricted"


async def test_a_deleted_episode_takes_its_row_and_rollup_with_it(pool: asyncpg.Pool) -> None:
    day = _new_day()
    episode = await _episode(pool, start=_at(day, 1), end=_at(day, 2))
    await sweep_public_projection(pool)
    await pool.execute("DELETE FROM episodes WHERE id = $1", episode)
    assert await _row(pool, episode) is None
    await sweep_public_projection(pool)
    assert await _minutes(pool, day) == {}


async def test_the_replica_identity_covers_the_published_filter(pool: asyncpg.Pool) -> None:
    identity = await pool.fetch(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'chronicler.episodes_public'::regclass AND i.indisreplident
        """
    )
    assert {row["attname"] for row in identity} == {"id", "effective_privacy", "tombstoned"}
    assert (
        await pool.fetchval(
            "SELECT relreplident::text FROM pg_class "
            "WHERE oid = 'chronicler.episodes_public'::regclass"
        )
        == "i"
    )


def test_the_migration_downgrades_and_reinstalls(database_url: str) -> None:
    config = _build_alembic_config(database_url, chains=["chronicler"], target_schema="chronicler")
    command.downgrade(config, "chronicler@chronicler_030")
    asyncio.run(run_migrations(database_url, chain="chronicler", schema="chronicler"))
