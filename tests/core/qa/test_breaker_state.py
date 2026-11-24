"""Derived QA circuit-breaker state: cause, trip time and the half-open probe schedule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.qa.breaker_state import (
    PROBE_BACKOFF_CAP,
    BreakerState,
    derive_breaker_state,
    probe_backoff,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
PUB = "publication_unavailable: no publisher configured"


def _row(minutes_ago: int, status: str = "failed", detail: str | None = None) -> dict:
    return {
        "status": status,
        "error_detail": detail,
        "closed_at": T0 - timedelta(minutes=minutes_ago),
    }


def test_below_threshold_or_a_success_in_the_window_is_closed() -> None:
    assert derive_breaker_state([_row(1), _row(2), _row(3)], 5) == BreakerState(
        open=False, streak=3
    )
    window = [_row(1), _row(2), _row(3, "pr_merged"), _row(4), _row(5), _row(6)]
    assert derive_breaker_state(window, 5) == BreakerState(open=False, streak=2)
    assert derive_breaker_state([], 5).open is False


def test_threshold_zero_disables_the_breaker() -> None:
    assert derive_breaker_state([_row(i) for i in range(1, 8)], 0).open is False


def test_five_failures_open_it_and_the_first_probe_is_fifteen_minutes_after_the_newest() -> None:
    state = derive_breaker_state([_row(i) for i in range(1, 6)], 5)
    assert state.open and state.streak == 5
    assert state.opened_at == T0 - timedelta(minutes=1)  # the fifth failure closing tripped it
    assert state.last_failure_at == T0 - timedelta(minutes=1)
    assert state.next_probe_at == T0 - timedelta(minutes=1) + timedelta(minutes=15)


def test_each_failed_probe_doubles_the_wait_up_to_the_cap() -> None:
    waits = [probe_backoff(k) for k in range(0, 7)]
    assert waits == [timedelta(minutes=m) for m in (15, 30, 60, 120, 120, 120, 120)]
    assert PROBE_BACKOFF_CAP == timedelta(hours=2)
    state = derive_breaker_state([_row(i) for i in range(1, 8)], 5)  # trip plus two failed probes
    assert state.streak == 7
    assert state.opened_at == T0 - timedelta(minutes=3)
    assert state.next_probe_at == T0 - timedelta(minutes=1) + timedelta(minutes=60)


def test_one_cause_on_every_failure_is_named() -> None:
    state = derive_breaker_state([_row(i, detail=PUB) for i in range(1, 6)], 5)
    assert state.dominant_cause == "publisher_unconfigured"
    assert state.cause_counts == {"publisher_unconfigured": 5}


def test_mixed_causes_are_mixed_with_their_counts() -> None:
    rows = [_row(1), *[_row(i, detail=PUB) for i in range(2, 6)]]
    state = derive_breaker_state(rows, 5)
    assert state.dominant_cause == "mixed"
    assert state.cause_counts == {"execution_failure": 1, "publisher_unconfigured": 4}


def test_unparseable_detail_is_an_execution_failure_never_a_named_structural_cause() -> None:
    state = derive_breaker_state([_row(i, detail="???") for i in range(1, 6)], 5)
    assert state.dominant_cause == "execution_failure"


def test_the_page_copy_of_the_cause_labels_matches_the_canonical_file() -> None:
    """The /qa page and the API must use the same words (frontend copy of the JSON)."""
    import json
    from pathlib import Path

    from butlers.core.qa.breaker_state import CAUSE_LABELS, cause_label

    root = Path(__file__).resolve().parents[3]
    page_copy = json.loads(
        (root / "frontend" / "src" / "lib" / "qa-breaker-cause-labels.json").read_text()
    )
    assert page_copy == CAUSE_LABELS
    assert cause_label("publisher_unconfigured") == "no publisher configured"
    assert cause_label("some_new_cause") == "some new cause"
    assert cause_label(None) is None
