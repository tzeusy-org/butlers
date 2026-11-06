"""Chronicler's source-owned public projection tables (REQ-public-mcp-projection-006).

Everything runs under ``butler_chronicler_rw``, the role the jobs, tools and dashboard route use,
so a missing grant fails here and not on the first override in production.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from alembic import command
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Override, OverrideTarget, Privacy
from butlers.chronicler.public_projection import sweep_public_projection
from butlers.chronicler.storage import insert_override, upsert_episode
from butlers.migrations import _build_alembic_config, run_migrations
from tests.chronicler._private_memory_setup import prepare_chronicler_current_head_sync

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_ZONE = ZoneInfo("Asia/Singapore")
_SLEEP = ("google_health.measurements", "sleep_episode")
_CALENDAR = "google_calendar.completed"
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
    chronicler_pool: asyncpg.Pool, payload: dict, title: str, expected: str
) -> None:
    day = _new_day()
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


def test_the_migration_downgrades_and_reinstalls(fresh_db) -> None:
    url = _chronicler_url(fresh_db)
    config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
    command.downgrade(config, "chronicler@chronicler_030")
    asyncio.run(run_migrations(url, chain="chronicler", schema="chronicler"))
