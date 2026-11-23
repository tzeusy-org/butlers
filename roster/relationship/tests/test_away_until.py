"""away-until reads for nudge deferral (bu-s11n0s.14, slice S2c). Pure, no database."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.roster_imports import roster_module

_away = roster_module("relationship", "tools/away_until")

pytestmark = pytest.mark.unit

_TODAY = date(2026, 10, 22)


def _fact(until: str, *, days_ago: int = 0) -> dict:
    return {
        "object": until,
        "observed_at": datetime(2026, 10, 20, tzinfo=UTC) - timedelta(days=days_ago),
    }


def test_a_past_date_means_not_away():
    assert _away.current_away_until([_fact("2026-10-21")], _TODAY) is None


def test_today_is_still_away_and_the_nudge_returns_the_day_after():
    until = _away.current_away_until([_fact("2026-10-22")], _TODAY)
    assert until == _TODAY
    assert _away.deferred_until(until) == date(2026, 10, 23)


def test_most_recently_observed_unexpired_fact_wins_over_a_later_date():
    rows = [_fact("2026-11-30", days_ago=5), _fact("2026-10-25", days_ago=0)]
    assert _away.current_away_until(rows, _TODAY) == date(2026, 10, 25)


def test_expired_newer_fact_does_not_hide_an_older_unexpired_one():
    rows = [_fact("2026-10-01", days_ago=0), _fact("2026-10-28", days_ago=9)]
    assert _away.current_away_until(rows, _TODAY) == date(2026, 10, 28)


@pytest.mark.parametrize("bad", ["next week", "2026-1-5", "", None, 20261030])
def test_malformed_objects_are_ignored(bad):
    assert _away.current_away_until([{"object": bad, "observed_at": _NOW}], _TODAY) is None


_NOW = datetime(2026, 10, 20, tzinfo=UTC)


async def test_away_until_by_entity_returns_only_entities_away_today():
    away_id, back_id, none_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {"subject": away_id, **_fact("2026-10-30")},
            {"subject": back_id, **_fact("2026-10-10")},
        ]
    )

    result = await _away.away_until_by_entity(pool, [away_id, back_id, none_id], _TODAY)

    assert result == {away_id: date(2026, 10, 30)}
    assert "relationship.entity_facts" in pool.fetch.await_args.args[0]


async def test_away_until_by_entity_skips_the_query_for_no_entities():
    pool = MagicMock()
    pool.fetch = AsyncMock()
    assert await _away.away_until_by_entity(pool, [], _TODAY) == {}
    pool.fetch.assert_not_awaited()
