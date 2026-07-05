"""Real-Postgres integration tests for chronicler.routines (bu-whhll.9).

Mocked-pool tests cannot validate the partial-unique-index upsert semantics
this feature depends on for idempotency (``ON CONFLICT (dow_mask) WHERE
origin = 'mined'``) — that class of SQL only proves correct against a real
Postgres backend (see "Mocked-pool vs integration test gap": PR #2598 class,
~8h main-red from SQL that passed mocked-pool tests). These tests run the
real migration chain, the real mining query, the real upsert, and the real
HTTP surface against a migrated Postgres container.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer
from butlers.chronicler.routines import mine_routines
from butlers.chronicler.storage import (
    get_routine,
    list_routines,
    update_routine,
    upsert_episode,
    upsert_mined_routine,
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
_ANCHOR_MONDAY = date(2026, 5, 4)
assert _ANCHOR_MONDAY.weekday() == 0


def _local(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=_TZ).astimezone(UTC)


def _weekdays(start: date, count: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while cursor.weekday() > 4:
        cursor += timedelta(days=1)
    while len(out) < count:
        if cursor.weekday() <= 4:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the chronicler migration chain (public schema, unscoped)."""
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
    await p.execute("TRUNCATE TABLE routines CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


# ── upsert_mined_routine idempotency ───────────────────────────────────────


async def test_upsert_mined_routine_idempotent_on_dow_mask(pool) -> None:
    """Re-running the upsert for the same dow_mask updates in place — no
    duplicate row — proving the partial unique index actually works against
    real Postgres."""
    first = await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,
        window_start_local=time(9, 30),
        window_end_local=time(19, 30),
        label="Mon-Fri 09:30-19:30",
        support_count=25,
        confidence=0.83,
        evidence_summary={"days_observed": 30, "days_supporting": 25},
    )
    second = await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,
        window_start_local=time(9, 0),
        window_end_local=time(18, 0),
        label="Mon-Fri 09:00-18:00",
        support_count=30,
        confidence=1.0,
        evidence_summary={"days_observed": 30, "days_supporting": 30},
    )

    assert second.id == first.id
    assert second.window_start_local == time(9, 0)
    assert second.confidence == pytest.approx(1.0)

    count = await pool.fetchval("SELECT COUNT(*) FROM routines")
    assert count == 1


async def test_upsert_mined_routine_preserves_owner_edits_on_remine(pool) -> None:
    """After the owner disables a mined routine and renames its label, the
    next weekly re-mine must refresh stats but never resurrect the row or
    clobber the owner's edits."""
    routine = await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,
        window_start_local=time(9, 30),
        window_end_local=time(19, 30),
        label="Mon-Fri 09:30-19:30",
        support_count=25,
        confidence=0.83,
        evidence_summary={},
    )
    assert routine.id is not None

    edited = await update_routine(pool, routine.id, enabled=False, label="My actual work hours")
    assert edited is not None
    assert edited.enabled is False
    assert edited.label == "My actual work hours"

    remined = await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,
        window_start_local=time(9, 0),
        window_end_local=time(18, 0),
        label="Mon-Fri 09:00-18:00",  # would-be new mined label
        support_count=29,
        confidence=0.97,
        evidence_summary={"days_observed": 30, "days_supporting": 29},
    )

    assert remined.id == routine.id
    # Owner edits survive.
    assert remined.enabled is False
    assert remined.label == "My actual work hours"
    # Mining-derived stats refreshed.
    assert remined.window_start_local == time(9, 0)
    assert remined.window_end_local == time(18, 0)
    assert remined.support_count == 29
    assert remined.confidence == pytest.approx(0.97)


async def test_declared_origin_not_constrained_by_mined_index(pool) -> None:
    """A declared row (bu-whhll.11) with the SAME dow_mask as a mined row is
    NOT rejected by the partial unique index — it only applies to
    origin='mined'."""
    await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,
        window_start_local=time(9, 30),
        window_end_local=time(19, 30),
        label="Mon-Fri 09:30-19:30",
        support_count=25,
        confidence=0.83,
        evidence_summary={},
    )
    await pool.execute(
        """
        INSERT INTO routines (dow_mask, window_start_local, window_end_local, label, origin)
        VALUES ($1, $2, $3, $4, 'declared')
        """,
        0b0011111,
        time(9, 0),
        time(18, 0),
        "Declared work hours",
    )

    count = await pool.fetchval("SELECT COUNT(*) FROM routines")
    assert count == 2


async def test_list_routines_enabled_only_filter(pool) -> None:
    enabled_routine = await upsert_mined_routine(
        pool,
        dow_mask=0b0000001,
        window_start_local=time(9, 0),
        window_end_local=time(12, 0),
        label="Mon 09:00-12:00",
        support_count=6,
        confidence=1.0,
        evidence_summary={},
    )
    disabled_routine = await upsert_mined_routine(
        pool,
        dow_mask=0b0000010,
        window_start_local=time(9, 0),
        window_end_local=time(12, 0),
        label="Tue 09:00-12:00",
        support_count=6,
        confidence=1.0,
        evidence_summary={},
    )
    await update_routine(pool, disabled_routine.id, enabled=False)

    all_routines = await list_routines(pool)
    assert {r.id for r in all_routines} == {enabled_routine.id, disabled_routine.id}

    enabled_only = await list_routines(pool, enabled_only=True)
    assert {r.id for r in enabled_only} == {enabled_routine.id}


async def test_get_routine_missing_returns_none(pool) -> None:
    assert await get_routine(pool, uuid4()) is None


async def test_update_routine_missing_returns_none(pool) -> None:
    assert await update_routine(pool, uuid4(), enabled=False) is None


# ── mine_routines end-to-end ────────────────────────────────────────────────


async def test_mine_routines_end_to_end_writes_expected_routine(pool) -> None:
    """Insert real activity-layer episodes for a stable Mon-Fri desk-signal
    pattern, run the real mining job, and confirm the expected routine row
    lands via the real query + upsert path (not a mocked pool)."""
    weekdays = _weekdays(_ANCHOR_MONDAY, 6 * 5)
    for i, d in enumerate(weekdays):
        await upsert_episode(
            pool,
            Episode(
                source_name="spotify.session_summary",
                source_ref=f"routines-it-{i}",
                episode_type="listening_episode",
                start_at=_local(d, 9, 30),
                end_at=_local(d, 19, 30),
                layer=Layer.ACTIVITY,
            ),
        )

    # "Now" is the morning of the day right after the mining window ends, so
    # the full 6 weeks of inserted weekdays are included and none are
    # excluded as "still partial".
    mining_end_date = _ANCHOR_MONDAY + timedelta(days=6 * 7)
    now = _local(mining_end_date, 8, 0)

    result = await mine_routines(pool, weeks=6, timezone="Asia/Singapore", now=now)
    assert result["candidates_found"] == 1
    assert result["routines_written"] == 1

    routines = await list_routines(pool)
    assert len(routines) == 1
    routine = routines[0]
    assert routine.dow_mask == 0b0011111
    assert routine.window_start_local == time(9, 30)
    assert routine.window_end_local == time(19, 30)
    assert routine.support_count == 30
    assert routine.confidence == pytest.approx(1.0)
    assert routine.origin.value == "mined"
    assert routine.enabled is True

    # Re-running is idempotent: same row, not a duplicate.
    result2 = await mine_routines(pool, weeks=6, timezone="Asia/Singapore", now=now)
    assert result2["routines_written"] == 1
    routines_after = await list_routines(pool)
    assert len(routines_after) == 1
    assert routines_after[0].id == routine.id


async def test_mine_routines_ignores_intent_layer_episodes(pool) -> None:
    """A calendar (intent-layer) block matching the same window every weekday
    must not, on its own, produce a mined routine — real-Postgres regression
    for the layer-exclusion guard."""
    weekdays = _weekdays(_ANCHOR_MONDAY, 6 * 5)
    for i, d in enumerate(weekdays):
        await upsert_episode(
            pool,
            Episode(
                source_name="google_calendar.completed",
                source_ref=f"routines-intent-it-{i}",
                episode_type="scheduled_block",
                start_at=_local(d, 9, 30),
                end_at=_local(d, 19, 30),
                layer=Layer.INTENT,
            ),
        )

    mining_end_date = _ANCHOR_MONDAY + timedelta(days=6 * 7)
    now = _local(mining_end_date, 8, 0)

    result = await mine_routines(pool, weeks=6, timezone="Asia/Singapore", now=now)
    assert result["candidates_found"] == 0
    assert result["routines_written"] == 0
    assert await list_routines(pool) == []


# ── HTTP API ─────────────────────────────────────────────────────────────


def _build_chronicler_api(pool) -> httpx.ASGITransport:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break
    else:  # pragma: no cover — defensive
        raise AssertionError("chronicler router not registered on the app")
    return httpx.ASGITransport(app=app)


async def test_get_routines_api_lists_rows(pool) -> None:
    await upsert_mined_routine(
        pool,
        dow_mask=0b0011111,
        window_start_local=time(9, 30),
        window_end_local=time(19, 30),
        label="Mon-Fri 09:30-19:30",
        support_count=25,
        confidence=0.83,
        evidence_summary={"description": "continuous desk signals, no movement, no gaming"},
    )

    transport = _build_chronicler_api(pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/chronicler/routines")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["dow_mask"] == 0b0011111
    assert row["label"] == "Mon-Fri 09:30-19:30"
    assert row["origin"] == "mined"
    assert row["enabled"] is True


async def test_patch_routine_api_updates_enabled_and_label(pool) -> None:
    routine = await upsert_mined_routine(
        pool,
        dow_mask=0b0000001,
        window_start_local=time(9, 0),
        window_end_local=time(12, 0),
        label="Mon 09:00-12:00",
        support_count=6,
        confidence=1.0,
        evidence_summary={},
    )

    transport = _build_chronicler_api(pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/chronicler/routines/{routine.id}",
            json={"enabled": False, "label": "Nope, not actually Mondays"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is False
        assert body["label"] == "Nope, not actually Mondays"

        # Re-fetch confirms the write persisted.
        listed = await client.get("/api/chronicler/routines")
        row = next(r for r in listed.json()["data"] if r["id"] == str(routine.id))
        assert row["enabled"] is False
        assert row["label"] == "Nope, not actually Mondays"


async def test_patch_routine_api_404_for_unknown_id(pool) -> None:
    transport = _build_chronicler_api(pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/chronicler/routines/{uuid4()}",
            json={"enabled": False},
        )
    assert resp.status_code == 404
