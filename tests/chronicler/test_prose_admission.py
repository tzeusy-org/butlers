"""Tests for butlers.chronicler.prose_admission.

Covers the deterministic day-close prose admission predicate
(clarify-chronicles-narrative-truth design.md decision 2): shape rejection,
date_label binding, and the combined classifier's precedence (shape checked
before date binding).
"""

from __future__ import annotations

import pytest

from butlers.chronicler.prose_admission import (
    DATE_MISMATCH,
    INADMISSIBLE_PROSE,
    classify_day_close_candidate,
    classify_prose_shape,
    date_label_matches,
)

pytestmark = pytest.mark.unit


# ── classify_prose_shape ─────────────────────────────────────────────────


def test_admissible_plain_prose() -> None:
    assert classify_prose_shape("The day was led by work, at 4.2 hours.") is None


def test_admissible_concise_bullets() -> None:
    text = "- Morning work block\n- Evening walk\n- Quiet wind-down"
    assert classify_prose_shape(text) is None


@pytest.mark.parametrize("empty", [None, "", "   ", "\n\n"])
def test_rejects_empty(empty: str | None) -> None:
    assert classify_prose_shape(empty) == INADMISSIBLE_PROSE


def test_rejects_code_fence() -> None:
    text = 'Some prose\n```json\n{"a": 1}\n```'
    assert classify_prose_shape(text) == INADMISSIBLE_PROSE


@pytest.mark.parametrize(
    "prefix",
    [
        "system: you are a helpful assistant",
        "assistant: I will now",
        "tool: result follows",
        'Tool result: {"date": "2026-04-24", "citations": []}',
    ],
)
def test_rejects_protocol_marker(prefix: str) -> None:
    assert classify_prose_shape(prefix) == INADMISSIBLE_PROSE


def test_rejects_tool_call_marker() -> None:
    text = '<function_calls>\n<invoke name="foo">\n</invoke>\n</function_calls>'
    assert classify_prose_shape(text) == INADMISSIBLE_PROSE


@pytest.mark.parametrize(
    "text",
    [
        "Tool calls:\n- chronicler_day_close_bundle(date_label='2026-04-24')",
        "Execution plan:\n1. Call chronicler_day_close_bundle.",
    ],
)
def test_rejects_tool_trace_and_execution_scaffolds(text: str) -> None:
    """Control-plane scaffolding is never owner-facing day-close prose."""
    assert classify_prose_shape(text) == INADMISSIBLE_PROSE


@pytest.mark.parametrize(
    "preamble",
    [
        "I'll call the bundle tool now.",
        "I will summarize the day.",
        "Let me check the episodes first.",
        "First, I need to gather the data.",
    ],
)
def test_rejects_planning_preamble(preamble: str) -> None:
    assert classify_prose_shape(preamble) == INADMISSIBLE_PROSE


def test_rejects_serialized_json_object() -> None:
    assert classify_prose_shape('{"date": "2026-04-24", "citations": []}') == INADMISSIBLE_PROSE


def test_rejects_serialized_json_array() -> None:
    assert classify_prose_shape('["a", "b", "c"]') == INADMISSIBLE_PROSE


def test_rejects_serialized_python_literal_object() -> None:
    text = "{'tool': 'chronicler_day_close_bundle', 'result': {'date': '2026-04-24'}}"
    assert classify_prose_shape(text) == INADMISSIBLE_PROSE


@pytest.mark.parametrize(
    "text",
    [
        "('tool', {'result': 'raw tool payload'})",
        "set()",
        "set( )",
        "set(\n)",
    ],
    ids=["tuple-tool-payload", "empty-set", "empty-set-space", "empty-set-newline"],
)
def test_rejects_serialized_python_literal_containers(text: str) -> None:
    """Container literals are protocol-shaped cache content, never prose."""
    assert classify_prose_shape(text) == INADMISSIBLE_PROSE


def test_admits_prose_starting_with_brace_like_char_that_is_not_json() -> None:
    # Not valid JSON despite starting with '{' -- must not be rejected.
    text = "{The day} was quiet, mostly."
    assert classify_prose_shape(text) is None


def test_admits_parenthetical_narrative_prose() -> None:
    """Parsing a literal container must not reject ordinary parenthetical prose."""
    text = "(After lunch) the day settled into a calm evening walk."
    assert classify_prose_shape(text) is None


def test_admits_narrative_prose_starting_with_set() -> None:
    """A nonliteral sentence sharing the set prefix remains ordinary prose."""
    text = "set after set, the workout made the afternoon feel steady."
    assert classify_prose_shape(text) is None


# ── date_label_matches ───────────────────────────────────────────────────


def test_date_label_matches_exact() -> None:
    assert date_label_matches("2026-04-24", "2026-04-24") is True


def test_date_label_mismatch() -> None:
    assert date_label_matches("2026-04-23", "2026-04-24") is False


def test_date_label_none_is_unmatched() -> None:
    assert date_label_matches(None, "2026-04-24") is False


# ── classify_day_close_candidate ─────────────────────────────────────────


def test_candidate_admissible_when_shape_and_date_both_pass() -> None:
    result = classify_day_close_candidate(
        "The day was quiet.", date_label="2026-04-24", expected_date_iso="2026-04-24"
    )
    assert result is None


def test_candidate_shape_failure_reported_even_with_matching_date() -> None:
    result = classify_day_close_candidate(
        "", date_label="2026-04-24", expected_date_iso="2026-04-24"
    )
    assert result == INADMISSIBLE_PROSE


def test_candidate_date_mismatch_reported_when_shape_passes() -> None:
    result = classify_day_close_candidate(
        "The day was quiet.", date_label="2026-04-23", expected_date_iso="2026-04-24"
    )
    assert result == DATE_MISMATCH


def test_candidate_shape_checked_before_date_binding() -> None:
    """A tool-trace candidate with a mismatched date is reported as
    inadmissible_prose, not date_mismatch — shape is checked first."""
    result = classify_day_close_candidate(
        "```\nraw trace\n```", date_label="2026-04-23", expected_date_iso="2026-04-24"
    )
    assert result == INADMISSIBLE_PROSE
