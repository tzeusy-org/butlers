"""Pure-logic tests for the recovery-state derived advisory (bu-317s5 slice 3).

``compute_recovery_state`` classifies the same severity-floor-crossing
symptom rows ``run_insight_scan``'s symptom-trend section already fetches --
no database needed for these; the wiring (``run_insight_scan`` best-effort
publishing ``health.recovery_state``) is covered by the docker-gated
integration tests in ``test_jobs.py``.
"""

from __future__ import annotations

import pytest

from butlers.jobs._roster.health_jobs import compute_recovery_state

pytestmark = pytest.mark.unit


def _row(name: str, severity: int) -> dict:
    return {"name": name, "severity": severity, "occurred_at": None}


def test_no_rows_returns_none():
    assert compute_recovery_state([]) is None


def test_single_moderate_symptom_classifies_as_recovering():
    result = compute_recovery_state([_row("nausea", 4)])
    assert result == {
        "state": "recovering",
        "max_severity": 4,
        "symptom_count": 1,
        "distinct_symptoms": ["nausea"],
        "window_days": 7,
    }


def test_high_severity_symptom_classifies_as_depleted():
    result = compute_recovery_state([_row("migraine", 8)])
    assert result["state"] == "depleted"
    assert result["max_severity"] == 8


def test_severity_exactly_at_depleted_floor_classifies_as_depleted():
    result = compute_recovery_state([_row("migraine", 7)])
    assert result["state"] == "depleted"


def test_severity_just_below_depleted_floor_classifies_as_recovering():
    result = compute_recovery_state([_row("migraine", 6)])
    assert result["state"] == "recovering"


def test_uses_the_max_severity_across_multiple_symptoms():
    rows = [_row("nausea", 4), _row("migraine", 9), _row("fatigue", 5)]
    result = compute_recovery_state(rows)
    assert result["state"] == "depleted"
    assert result["max_severity"] == 9
    assert result["symptom_count"] == 3
    assert result["distinct_symptoms"] == ["fatigue", "migraine", "nausea"]


def test_distinct_symptoms_deduplicates_repeated_names():
    rows = [_row("nausea", 4), _row("nausea", 5), _row("nausea", 3)]
    result = compute_recovery_state(rows)
    assert result["distinct_symptoms"] == ["nausea"]
    assert result["symptom_count"] == 3
