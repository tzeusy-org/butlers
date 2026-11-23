"""Plan-change projection over the calendar revision journal (bu-uhbhzq.1 slice 3).

Unit tests run the adapter's Python logic over canned journal rows; the DB test at the bottom
runs its real SQL (prior-version lookup, baseline exclusion, keyset paging) on core_366.
Synthetic data only.
"""

from __future__ import annotations

import ast
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.adapters.calendar_revisions import (
    EVENT_TYPE_PLAN_CHANGED,
    SOURCE_NAME,
    CalendarRevisionsAdapter,
    starts_by_observed_day,
)
from butlers.chronicler.models import Layer, Precision, Privacy
from butlers.testing.fake_pool import AsyncCtx
from butlers.testing.records import Record

_SGT = ZoneInfo("Asia/Singapore")
# 2026-11-24 12:00 SGT, so the local day ends at 2026-11-24 16:00 UTC.
_OBSERVED = datetime(2026, 11, 24, 4, 0, tzinfo=UTC)
_START = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)


def _row(**extra: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "observed_at": _OBSERVED,
        "observation": "change",
        "policy_blocked": False,
        "all_day": False,
        "new_status": "confirmed",
        "new_start": _START + timedelta(hours=2),
        "new_end": _START + timedelta(hours=3),
        "prior_known": True,
        "prior_status": "confirmed",
        "prior_start": _START,
        "prior_end": _START + timedelta(hours=1),
        **extra,
    }


def _pool(*rows: dict[str, Any], table: bool = True) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=table)
    conn.fetch = AsyncMock(return_value=[Record(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=lambda: AsyncCtx(conn))
    return pool


def _chronicler_pool(zone: str = "Asia/Singapore", blocked: str | None = None) -> AsyncMock:
    conn = AsyncMock()
    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=lambda: AsyncCtx(conn))
    pool.fetchrow = AsyncMock(return_value={"timezone": zone, "blocked_reason": blocked})
    return pool


async def _project(
    *rows: dict[str, Any],
    table: bool = True,
    zone: str = "Asia/Singapore",
    configured: str | None = "Asia/Singapore",
    blocked: str | None = None,
):
    emitted: list[Any] = []

    async def upsert(conn: object, event: Any) -> Any:
        emitted.append(event)
        return event

    with (
        patch(
            "butlers.chronicler.adapters.calendar_revisions.upsert_point_event", side_effect=upsert
        ),
        patch(
            "butlers.core.general_settings.load_configured_general_timezone",
            new=AsyncMock(return_value=configured),
        ),
    ):
        result = await CalendarRevisionsAdapter().project(
            _pool(*rows, table=table),
            chronicler_pool=_chronicler_pool(zone, blocked),
            since=None,
        )
    return result, emitted


def test_no_llm_imports_in_the_adapter() -> None:
    import butlers.chronicler.adapters.calendar_revisions as mod

    with open(mod.__file__ or "") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        names = (
            [a.name for a in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
        for name in names:
            assert not name.startswith(("anthropic", "openai", "langchain", "litellm", "llm"))


async def test_a_moved_same_day_instance_emits_one_intent_plan_changed_with_prior_and_new() -> None:
    row = _row()
    result, [event] = await _project(row)
    assert (result.rows_projected, result.point_events) == (1, 1)
    assert event.source_name == SOURCE_NAME and event.event_type == EVENT_TYPE_PLAN_CHANGED
    assert event.layer == Layer.INTENT and event.precision == Precision.EXACT
    assert event.privacy == Privacy.NORMAL and event.occurred_at == _OBSERVED
    assert event.source_ref.endswith(f":{row['id']}")  # keyed on the revision id
    assert event.payload["prior_known"] is True
    assert event.payload["prior"]["start"] == _START.isoformat()
    assert event.payload["new"]["start"] == (_START + timedelta(hours=2)).isoformat()


async def test_only_times_and_status_leave_the_journal() -> None:
    _, [event] = await _project(_row(title="Synthetic secret title", attendees=["x@example.test"]))
    assert set(event.payload) == {
        "prior_known",
        "prior",
        "new",
        "all_day",
        "observation",
        "policy_blocked",
    }
    assert set(event.payload["new"]) == {"start", "end", "status"}
    assert "secret" not in repr(event) and "example.test" not in repr(event)


async def test_it_is_never_attendance_or_activity() -> None:
    _, [event] = await _project(_row())
    assert event.layer == Layer.INTENT and event.layer != Layer.ACTIVITY
    assert "attend" not in repr(event.payload).lower()


async def test_a_missing_prior_emits_with_prior_known_false() -> None:
    row = _row(
        observation="resync",
        prior_known=False,
        prior_status=None,
        prior_start=None,
        prior_end=None,
    )
    _, [event] = await _project(row)
    assert event.payload["prior_known"] is False
    assert event.payload["prior"] == {"start": None, "end": None, "status": None}
    assert event.payload["observation"] == "resync"


async def test_a_future_day_instance_emits_nothing() -> None:
    far = _OBSERVED + timedelta(days=3)
    row = _row(new_start=far, new_end=far + timedelta(hours=1), prior_start=far, prior_end=far)
    result, emitted = await _project(row, _row(new_start=far, prior_known=False, prior_start=None))
    assert emitted == [] and result.point_events == 0


async def test_a_same_day_meeting_moved_to_tomorrow_still_counts() -> None:
    tomorrow = _OBSERVED + timedelta(days=1)
    _, emitted = await _project(
        _row(new_start=tomorrow, new_end=tomorrow + timedelta(hours=1), prior_start=_START)
    )
    assert len(emitted) == 1


async def test_a_past_day_instance_counts() -> None:
    old = _OBSERVED - timedelta(days=9)
    _, emitted = await _project(_row(new_start=old, prior_start=old, new_status="cancelled"))
    assert len(emitted) == 1


async def test_a_revision_that_changes_no_time_or_status_emits_nothing() -> None:
    same = _row(new_start=_START, new_end=_START + timedelta(hours=1))
    result, emitted = await _project(same)
    assert emitted == [] and result.rows_projected == 0


async def test_a_blocked_row_projects_like_any_other() -> None:
    _, [event] = await _project(_row(policy_blocked=True))
    assert event.payload["policy_blocked"] is True


async def test_the_watermark_advances_even_when_nothing_is_emitted() -> None:
    later = _OBSERVED + timedelta(minutes=5)
    result, emitted = await _project(
        _row(observed_at=later, new_start=_START, new_end=_START + timedelta(hours=1))
    )
    assert emitted == [] and result.watermark == later and result.watermark_key


async def test_a_missing_journal_degrades_gracefully() -> None:
    result, emitted = await _project(table=False)
    assert result.skipped and "not found" in (result.skipped_reason or "") and emitted == []


def test_the_local_day_boundary_is_the_owner_timezone() -> None:
    row = _row(
        observed_at=datetime(2026, 11, 24, 15, 59, tzinfo=UTC),  # 23:59 SGT
        new_start=datetime(2026, 11, 24, 16, 0, tzinfo=UTC),  # 00:00 SGT the next day
        prior_start=None,
    )
    assert starts_by_observed_day(row, _SGT) is False
    row["new_start"] = datetime(2026, 11, 24, 15, 59, tzinfo=UTC)
    assert starts_by_observed_day(row, _SGT) is True


# --------------------------------------------------------------------------------------------
# Real SQL on core_366 (Docker).
# --------------------------------------------------------------------------------------------

_db = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


@_db
@pytest.mark.integration
async def test_the_real_query_pairs_each_change_with_its_prior_and_skips_baselines(
    db_pool,
) -> None:
    async def add(event: str, etag: str, observation: str, start_h: int, observed_min: int):
        base = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
        await db_pool.execute(
            "INSERT INTO connectors.calendar_event_revisions (endpoint_identity, calendar_id, "
            "event_id, etag, observation, status, starts_at, ends_at, observed_at) "
            "VALUES ('ep', 'primary', $1, $2, $3, 'confirmed', $4, $5, $6)",
            event,
            etag,
            observation,
            base + timedelta(hours=start_h),
            base + timedelta(hours=start_h + 1),
            base + timedelta(minutes=observed_min),
        )

    await add("e1", "a1", "baseline", 6, 0)
    await add("e1", "a2", "change", 8, 10)  # moved 06:00 -> 08:00
    await add("e2", "b1", "resync", 6, 20)  # no earlier version

    emitted: list[Any] = []

    async def upsert(conn: object, event: Any) -> Any:
        emitted.append(event)
        return event

    with patch(
        "butlers.chronicler.adapters.calendar_revisions.upsert_point_event", side_effect=upsert
    ):
        result = await CalendarRevisionsAdapter(timezone="UTC").project(
            db_pool, chronicler_pool=_chronicler_pool(zone="UTC"), since=None
        )
    assert result.watermark is not None and result.watermark_key

    by_event = {e.payload["new"]["start"]: e for e in emitted}
    moved = next(e for e in emitted if e.payload["prior_known"])
    assert moved.payload["prior"]["start"].startswith("2026-11-24T06:00")
    assert moved.payload["new"]["start"].startswith("2026-11-24T08:00")
    unknown = next(e for e in emitted if not e.payload["prior_known"])
    assert unknown.payload["observation"] == "resync"
    assert len(emitted) == 2 and len(by_event) == 2  # the baseline emitted nothing


async def test_the_configured_zone_moves_the_day_boundary() -> None:
    # Observed 2026-11-24 04:00 UTC; the instance starts at 10:00 UTC the same day.
    row = _row(
        observed_at=datetime(2026, 11, 24, 4, 0, tzinfo=UTC),
        new_start=datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
        new_end=datetime(2026, 11, 24, 11, 0, tzinfo=UTC),
        prior_known=False,
        prior_start=None,
    )
    # Singapore (UTC+8): observed day ends 16:00 UTC, so a 10:00 UTC start is today's plan.
    _, singapore = await _project(row, zone="Asia/Singapore", configured="Asia/Singapore")
    # Los Angeles (UTC-8): observed day ends 08:00 UTC, so 10:00 UTC is tomorrow's plan.
    _, los_angeles = await _project(
        row, zone="America/Los_Angeles", configured="America/Los_Angeles"
    )
    assert len(singapore) == 1 and los_angeles == []


async def test_a_zone_that_disagrees_with_the_rollup_refuses() -> None:
    with pytest.raises(RuntimeError, match="timezone mismatch"):
        await _project(_row(), zone="Asia/Singapore", configured="Europe/London")


async def test_a_blocked_or_missing_repair_state_refuses() -> None:
    with pytest.raises(RuntimeError, match="missing or blocked"):
        await _project(_row(), blocked="replay in progress")


@_db
@pytest.mark.integration
async def test_two_versions_of_one_event_in_one_batch_are_ordered_by_the_provider_clock(
    db_pool,
) -> None:
    """A batch is one transaction, so both rows share observed_at; ``updated`` orders them."""
    observed = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)
    first_id, second_id = (
        "00000000-0000-0000-0000-00000000000f",
        "00000000-0000-0000-0000-000000000001",
    )
    for row_id, etag, updated, start_h in (
        (first_id, "v1", datetime(2026, 11, 24, 1, 0, tzinfo=UTC), 6),  # the older version
        (second_id, "v2", datetime(2026, 11, 24, 2, 0, tzinfo=UTC), 8),  # the newer, smaller id
    ):
        await db_pool.execute(
            "INSERT INTO connectors.calendar_event_revisions (id, endpoint_identity, calendar_id, "
            "event_id, etag, observation, status, starts_at, ends_at, observed_at, "
            "provider_updated) VALUES ($1::uuid, 'ep', 'primary', 'e1', $2, 'change', "
            "'confirmed', $3, $4, $5, $6)",
            row_id,
            etag,
            datetime(2026, 11, 24, start_h, 0, tzinfo=UTC),
            datetime(2026, 11, 24, start_h + 1, 0, tzinfo=UTC),
            observed,
            updated,
        )
    rows = await CalendarRevisionsAdapter(timezone="UTC")._fetch(db_pool, None, None)
    newer = next(r for r in rows if str(r["id"]) == second_id)
    # Despite the smaller id, the newer version's prior is the older one, not the reverse.
    assert newer["prior_known"] is True and newer["prior_start"].hour == 6
    older = next(r for r in rows if str(r["id"]) == first_id)
    assert older["prior_known"] is False
