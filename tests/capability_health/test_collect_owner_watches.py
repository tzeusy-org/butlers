"""The owner-watch tick's health record becomes an ``owner_watches`` observation (bu-4c7nvp.1.5.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_health.collectors import probe_owner_watches
from butlers.capability_health.model import reduce
from butlers.watches.health import MAX_AGE_SECONDS, StateReporter

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
pytestmark = pytest.mark.unit


class Pool:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.writes = value, error, []

    async def fetchval(self, sql, key):
        if self.error is not None:
            raise self.error
        return self.value

    async def execute(self, sql, key, value):
        self.writes.append((key, json.loads(value)))


def record(state, reason, age=0):
    return json.dumps(
        {"state": state, "reason": reason, "at": (NOW - timedelta(seconds=age)).isoformat()}
    )


async def probe(pool, live=True):
    return await probe_owner_watches(pool, live=live, clock=lambda: NOW)


async def test_flag_off_is_unconfigured_whatever_the_row_says():
    seen = await probe(Pool(record("up", "tick_ok")), live=False)
    assert (seen.state, seen.cause) == ("unconfigured", "not_configured")


async def test_a_fresh_ok_report_is_available_at_its_own_time():
    seen = await probe(Pool(record("up", "tick_ok", age=120)))
    assert (seen.state, seen.cause) == ("available", None)
    assert seen.observed_at == NOW - timedelta(seconds=120)


@pytest.mark.parametrize(
    ("reason", "cause"),
    [
        ("no_snapshot_provider", "provider_unavailable"),
        ("sources_unreadable", "signal_unmeasurable"),
    ],
)
async def test_a_blind_report_is_degraded_with_its_own_cause(reason, cause):
    seen = await probe(Pool(record("degraded", reason)))
    assert (seen.state, seen.cause) == ("degraded", cause)


@pytest.mark.parametrize(
    "pool",
    [
        Pool(None),  # no report yet
        Pool(record("up", "tick_ok", age=MAX_AGE_SECONDS + 1)),  # old news
        Pool(record("up", "unheard_of")),  # an unknown word
        Pool(record("degraded", "tick_ok")),  # not a pair the tick writes
        Pool("not json"),
        Pool(json.dumps({"state": "up", "reason": "tick_ok", "at": "2026-11-24T09:00:00"})),
        Pool(
            json.dumps(
                {"state": "up", "reason": "tick_ok", "at": (NOW + timedelta(hours=1)).isoformat()}
            )
        ),
        Pool(error=ConnectionError("down")),
    ],
)
async def test_anything_else_is_unmeasured_never_available(pool):
    seen = await probe(pool)
    assert seen.state is None and seen.cause is None
    assert reduce("owner_watches", [seen], NOW).unmeasurable


async def test_the_reporter_writes_only_closed_words_and_a_time():
    pool = Pool()
    await StateReporter(pool, now=lambda: NOW).report(
        "system.dep.owner_watches", "degraded", "sources_unreadable"
    )
    ((key, value),) = pool.writes
    assert key == "owner_watch:tick_health"
    assert value == {"state": "degraded", "reason": "sources_unreadable", "at": NOW.isoformat()}


async def test_a_report_slightly_ahead_of_this_clock_is_still_fresh():
    seen = await probe(Pool(record("up", "tick_ok", age=-20)))
    assert seen.state == "available"


async def test_a_report_far_ahead_of_this_clock_is_unmeasured():
    seen = await probe(Pool(record("up", "tick_ok", age=-120)))
    assert seen.state is None


@pytest.mark.parametrize(
    "value",
    [
        {"state": ["up"], "reason": "tick_ok"},
        {"state": "up", "reason": {"x": 1}},
        {"state": {"a": 1}, "reason": None},
        {"state": 5, "reason": "tick_ok"},
    ],
    ids=["list state", "dict reason", "dict state", "int state"],
)
async def test_an_unhashable_or_non_word_value_is_unmeasured(value):
    row = json.dumps({**value, "at": NOW.isoformat()})
    seen = await probe(Pool(row))
    assert seen.state is None
