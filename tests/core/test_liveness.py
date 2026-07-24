"""Tests for butlers.core.liveness — the shared last_seen_at +
liveness_ttl_seconds staleness formula (bu-dvzya).

Cross-consumer parity coverage (registry.py's _derive_eligibility_state vs.
InfraStateSource's heartbeat-stale check) lives in
tests/core/qa/test_infra_state.py, alongside the InfraStateSource test
helpers it reuses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.liveness import (
    CLOCK_SKEW_TOLERANCE,
    DEFAULT_LIVENESS_TTL_SECONDS,
    is_liveness_stale,
    normalize_liveness_ttl_seconds,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# is_liveness_stale
# ---------------------------------------------------------------------------


def test_missing_last_seen_is_stale():
    assert is_liveness_stale(None, ttl_seconds=300, now=_NOW) is True


def test_fresh_within_ttl_is_not_stale():
    last_seen = _NOW - timedelta(seconds=100)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_exactly_at_ttl_boundary_is_not_stale():
    # last_seen_at + ttl == now is still fresh (>=, not strictly >).
    last_seen = _NOW - timedelta(seconds=300)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_just_past_ttl_boundary_is_stale():
    last_seen = _NOW - timedelta(seconds=301)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is True


def test_future_within_skew_tolerance_is_not_stale():
    last_seen = _NOW + timedelta(minutes=1)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_exactly_at_skew_tolerance_boundary_is_not_stale():
    last_seen = _NOW + CLOCK_SKEW_TOLERANCE
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_future_beyond_skew_tolerance_is_stale():
    # Without the skew guard, the unbounded TTL window would keep this
    # "fresh" forever; a future-dated timestamp must not evade detection.
    last_seen = _NOW + timedelta(minutes=10)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is True


def test_custom_ttl_overrides_default():
    last_seen = _NOW - timedelta(seconds=100)
    assert is_liveness_stale(last_seen, ttl_seconds=50, now=_NOW) is True
    assert is_liveness_stale(last_seen, ttl_seconds=150, now=_NOW) is False


@pytest.mark.parametrize("raw_ttl", [None, "not-a-number", 0, -5])
def test_malformed_ttl_falls_back_to_default(raw_ttl):
    stale_anchor = _NOW - timedelta(seconds=DEFAULT_LIVENESS_TTL_SECONDS + 10)
    fresh_anchor = _NOW - timedelta(seconds=DEFAULT_LIVENESS_TTL_SECONDS - 10)
    assert is_liveness_stale(stale_anchor, ttl_seconds=raw_ttl, now=_NOW) is True
    assert is_liveness_stale(fresh_anchor, ttl_seconds=raw_ttl, now=_NOW) is False


def test_now_defaults_to_current_time_when_omitted():
    # A last_seen_at from "just now" against the real wall clock must read fresh.
    assert is_liveness_stale(datetime.now(UTC), ttl_seconds=300) is False


# ---------------------------------------------------------------------------
# normalize_liveness_ttl_seconds
# ---------------------------------------------------------------------------


def test_normalize_liveness_ttl_seconds_positive_passthrough():
    assert normalize_liveness_ttl_seconds(120) == 120


@pytest.mark.parametrize("raw", [None, "bogus", 0, -1, [], {}])
def test_normalize_liveness_ttl_seconds_defaults_on_bad_input(raw):
    assert normalize_liveness_ttl_seconds(raw) == DEFAULT_LIVENESS_TTL_SECONDS


def test_normalize_liveness_ttl_seconds_custom_default():
    assert normalize_liveness_ttl_seconds(None, default=60) == 60
