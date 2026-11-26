"""``calendar_plan_change`` reads the projection and writes nothing (bu-5ii4gj.11 P1b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from butlers.modules.calendar.plan_tools import plan_change

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
DAY = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
WINDOW = (DAY, DAY + timedelta(days=1))


def _row(instance, hour, *, role="owner", metadata=None, etag=None, all_day=False, **kw):
    start = DAY + timedelta(hours=hour)
    row = {
        "instance_id": instance,
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(hours=1),
        "instance_status": "confirmed",
        "origin_ref": f"prov-{instance}",
        "all_day": all_day,
        "event_status": "confirmed",
        "recurrence_rule": None,
        "event_metadata": metadata or {},
        "event_etag": etag or f"etag-{instance}",
        "calendar_id": "primary",
        "source_time_role": role,
    }
    return {**row, **kw}


def _source(*, success=NOW - timedelta(minutes=5), error=None, full=False, kind="provider_event"):
    return {
        "source_kind": kind,
        "source_metadata": {},
        "last_success_at": success,
        "last_error_at": error,
        "full_sync_required": full,
    }


class _Pool:
    def __init__(self, rows, sources=None, zone="UTC"):
        self.rows = rows
        self.sources = [_source()] if sources is None else sources
        self.zone = zone
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self.sources if "calendar_sync_cursors" in sql else self.rows

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return {"timezone": self.zone} if self.zone else None

    async def execute(self, *a, **k):  # pragma: no cover - the tool must never write
        raise AssertionError("calendar_plan_change must not write")

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return {"timezone": self.zone} if self.zone else None


def _module(pool):
    return SimpleNamespace(_db=SimpleNamespace(pool=pool))


def _clear(pool, **kw):
    return plan_change(
        _module(pool),
        kind="clear_window",
        window_start=WINDOW[0],
        window_end=WINDOW[1],
        now=NOW,
        **kw,
    )


async def test_clearing_a_day_returns_steps_with_calls_authority_and_no_titles():
    attended = {"attendees": [{"self": True, "responseStatus": "accepted"}, {"email": "x@e.test"}]}
    pool = _Pool(
        [
            _row("block", 9, role="butler"),
            _row("solo", 10),
            _row("mtg", 11, role="owner", metadata=attended),
        ]
    )
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        result = await _clear(pool)

    assert result["status"] == "ok" and result["plan_id"].startswith("plan_")
    steps = {s["event_ref"]: s for s in result["steps"]}
    assert steps["block"]["authority"] == "no_park_undo"
    assert steps["solo"]["authority"] == "owner_submit"
    assert steps["mtg"]["action"] == "decline"
    assert "title" not in str(result).lower()
    assert all(isinstance(s["exact_call"]["args"], dict) for s in result["steps"])


async def test_planning_writes_nothing():
    pool = _Pool([_row("solo", 10)])
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        await _clear(pool)
    assert all(not s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in pool.sql)


async def test_a_stale_source_gives_no_steps_and_says_why():
    pool = _Pool([_row("solo", 10)], sources=[_source(success=NOW - timedelta(days=2))])
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        result = await _clear(pool)
    assert result["status"] == "refused" and result["reason"] == "stale_source"
    assert result["steps"] == []


async def test_a_failed_attempt_after_the_last_success_is_stale_too():
    stale = _source(success=NOW - timedelta(minutes=10), error=NOW - timedelta(minutes=1))
    pool = _Pool([_row("solo", 10)], sources=[stale])
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        assert (await _clear(pool))["reason"] == "stale_source"


async def test_a_clear_with_no_owner_zone_is_refused_not_guessed():
    pool = _Pool([_row("solo", 10)], zone=None)
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value=None,
    ):
        result = await _clear(pool)
    assert result["reason"] == "owner_zone_unknown"


async def test_all_day_and_declined_events_are_left_alone():
    declined = {"attendees": [{"self": True, "responseStatus": "declined"}, {"email": "x@e.test"}]}
    pool = _Pool(
        [
            _row("ooo", 0, all_day=True),
            _row("gone", 9, metadata=declined),
            _row("solo", 10),
        ]
    )
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        result = await _clear(pool)
    assert [s["event_ref"] for s in result["steps"]] == ["solo"]
    assert {"event_ref": "ooo", "why": "all_day_event"} in result["skipped"]


async def test_a_shift_moves_by_the_delta_and_needs_one():
    pool = _Pool([_row("solo", 14)])
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        result = await plan_change(
            _module(pool),
            kind="shift_window",
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            shift_minutes=45,
            now=NOW,
        )
        missing = await plan_change(
            _module(pool),
            kind="shift_window",
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            now=NOW,
        )
    (step,) = result["steps"]
    assert (
        step["exact_call"]["args"]["start_at"]
        == (DAY + timedelta(hours=14, minutes=45)).isoformat()
    )
    assert missing["reason"] == "shift_minutes_required"


async def test_a_naive_inverted_or_oversized_window_is_refused_before_any_read():
    pool = _Pool([])
    module = _module(pool)
    naive = await plan_change(
        module, kind="clear_window", window_start=datetime(2026, 3, 10), window_end=WINDOW[1]
    )
    inverted = await plan_change(
        module, kind="clear_window", window_start=WINDOW[1], window_end=WINDOW[0]
    )
    wide = await plan_change(
        module,
        kind="clear_window",
        window_start=WINDOW[0],
        window_end=WINDOW[0] + timedelta(days=3),
        now=NOW,
    )
    assert naive["reason"] == "window_must_be_timezone_aware"
    assert inverted["reason"] == "window_must_end_after_it_starts"
    assert wide["reason"] == "plan_too_large"
    assert pool.sql == []


async def test_the_same_plan_comes_back_on_a_retry():
    pool = _Pool([_row("solo", 10)])
    with patch(
        "butlers.modules.calendar.plan_tools.load_configured_general_timezone",
        return_value="UTC",
    ):
        first, again = await _clear(pool), await _clear(pool)
    assert first["plan_id"] == again["plan_id"] and first["plan_digest"] == again["plan_digest"]
