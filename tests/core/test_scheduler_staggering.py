"""Unit tests for cron staggering behavior in butlers.core.scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.scheduler import _next_run, _stagger_offset_seconds

pytestmark = pytest.mark.unit


def test_stagger_offset_is_deterministic() -> None:
    """The same key/cron pair should always produce the same offset."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    first = _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)
    second = _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)

    assert first == second


def test_stagger_offset_is_capped_by_cadence_for_minutely_cron() -> None:
    """Every-minute cron can only shift within the minute to preserve cadence."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    offset = _stagger_offset_seconds("* * * * *", stagger_key="switchboard", now=now)

    assert 0 <= offset <= 59


def test_next_run_without_stagger_key_matches_plain_cron() -> None:
    """No stagger key should preserve existing next-run behavior."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    base = _next_run("0 * * * *", now=now)
    unkeyed = _next_run("0 * * * *", stagger_key=None, now=now)

    assert unkeyed == base


def test_staggered_next_run_preserves_five_minute_cadence() -> None:
    """Chained _next_run() calls keep the original cron interval."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    first = _next_run("*/5 * * * *", stagger_key="general", now=now)
    second = _next_run("*/5 * * * *", stagger_key="general", now=first)

    assert second - first == timedelta(minutes=5)


def test_staggered_next_run_preserves_one_minute_cadence() -> None:
    """Minute-level schedules must still execute every minute."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    first = _next_run("* * * * *", stagger_key="messenger", now=now)
    second = _next_run("* * * * *", stagger_key="messenger", now=first)

    assert second - first == timedelta(minutes=1)
