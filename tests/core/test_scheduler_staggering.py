"""Unit tests for cron staggering behavior in butlers.core.scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.scheduler import _next_run, _stagger_offset_seconds

pytestmark = pytest.mark.unit


def test_stagger_offset_determinism_and_cap() -> None:
    """Same key/cron pair always produces same offset; every-minute cron capped within minute."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    first = _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)
    assert first == _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)

    offset = _stagger_offset_seconds("* * * * *", stagger_key="switchboard", now=now)
    assert 0 <= offset <= 59


def test_next_run_stagger_and_cadence() -> None:
    """No stagger key matches plain cron; staggered key preserves 5-min and 1-min cadences."""
    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    base = _next_run("0 * * * *", now=now)
    assert _next_run("0 * * * *", stagger_key=None, now=now) == base

    # 5-minute cadence preserved
    first = _next_run("*/5 * * * *", stagger_key="general", now=now)
    second = _next_run("*/5 * * * *", stagger_key="general", now=first)
    assert second - first == timedelta(minutes=5)

    # 1-minute cadence preserved
    first_m = _next_run("* * * * *", stagger_key="messenger", now=now)
    second_m = _next_run("* * * * *", stagger_key="messenger", now=first_m)
    assert second_m - first_m == timedelta(minutes=1)
