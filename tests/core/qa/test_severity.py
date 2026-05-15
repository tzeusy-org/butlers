"""Tests for QA severity label helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from butlers.core.qa.severity import (
    _HUMAN_ACTION_MARKERS,
    _sql_string_literal,
    escalated_open_cases_sql,
    failed_with_human_action,
    map_severity,
    state_of_case,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (0, "high"),
        (1, "high"),
        (2, "medium"),
        (3, "low"),
        (4, "low"),
    ],
)
def test_severity_map(severity: int, expected: str) -> None:
    assert map_severity(severity) == expected


def test_severity_map_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Unknown QA severity"):
        map_severity(5)


def _attempt(status: str, error_detail: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, error_detail=error_detail)


def test_failed_with_human_action_unfixable_with_marker_human_action() -> None:
    assert failed_with_human_action(_attempt("unfixable", "Needs human action: inspect PR"))


def test_failed_with_human_action_failed_with_marker_operator() -> None:
    assert failed_with_human_action(_attempt("failed", "Operator must rotate credential"))


def test_failed_with_human_action_failed_with_marker_escalat() -> None:
    assert failed_with_human_action(_attempt("failed", "Escalated after repeated failures"))


def test_failed_with_human_action_failed_without_marker() -> None:
    assert not failed_with_human_action(_attempt("failed", "No matching context"))


def test_failed_with_human_action_terminal_no_error_detail() -> None:
    assert not failed_with_human_action(_attempt("unfixable"))


def test_failed_with_human_action_active_status() -> None:
    assert not failed_with_human_action(_attempt("pr_open", "operator must review CI"))


def test_helper_markers_match_sql_helper() -> None:
    sql = escalated_open_cases_sql()

    assert tuple(_HUMAN_ACTION_MARKERS) == ("human action", "operator", "escalat")
    assert sql.count("ILIKE") == len(_HUMAN_ACTION_MARKERS)
    for marker in _HUMAN_ACTION_MARKERS:
        assert f"%{marker}%" in sql


def test_sql_string_literal_escapes_single_quotes() -> None:
    assert _sql_string_literal("%operator's action%") == "'%operator''s action%'"


def test_state_of_case_uses_failed_with_human_action_marker_rules() -> None:
    assert state_of_case(_attempt("failed", "operator must review credentials")) == "escalated"
    assert (
        state_of_case(_attempt("investigating", "operator must review credentials")) == "diagnose"
    )
