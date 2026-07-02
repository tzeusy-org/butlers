"""Regression coverage for JSON-safe dropped-intent serialization (bu-gh9yq).

``chronicler_day_close_bundle`` returns its result dict directly as an MCP
tool response. ``reconciled.dropped_intents`` carries raw ``datetime``
objects on ``DroppedIntent.intent`` (the canonical reconciled episode dict),
unlike ``episodes``/``events`` which are normalized to ISO-8601 strings by
``bundle_assembler._serialise_items``. Without conversion, a day with a
dropped calendar intent would return a payload containing raw ``datetime``
objects that JSON serialization of the tool result would reject.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from butlers.chronicler.reconciliation import DroppedIntent
from roster.chronicler.modules import _serialise_dropped_intents

pytestmark = pytest.mark.unit


def test_serialise_dropped_intents_converts_datetimes_to_iso_strings() -> None:
    start = datetime(2026, 6, 1, 9, tzinfo=UTC)
    end = datetime(2026, 6, 1, 10, tzinfo=UTC)
    dropped = DroppedIntent(
        intent={
            "canonical_title": "Gym",
            "canonical_start_at": start,
            "canonical_end_at": end,
        },
        contradicting_activity={"source_name": "home_assistant.history"},
        overlap_fraction=1.0,
    )

    [payload] = _serialise_dropped_intents([dropped])

    assert payload["start_at"] == start.isoformat()
    assert payload["end_at"] == end.isoformat()
    # The whole point: this must not raise TypeError on raw datetime objects.
    json.dumps(payload)


def test_serialise_dropped_intents_passes_through_non_datetime_values() -> None:
    """String-shaped windows (e.g. already-ISO rows) are left untouched."""
    dropped = DroppedIntent(
        intent={
            "title": "Gym",
            "start_at": "2026-06-01T09:00:00+00:00",
            "end_at": "2026-06-01T10:00:00+00:00",
        },
        contradicting_activity={"source_name": "home_assistant.history"},
        overlap_fraction=0.75,
        reason="custom reason",
    )

    [payload] = _serialise_dropped_intents([dropped])

    assert payload == {
        "title": "Gym",
        "start_at": "2026-06-01T09:00:00+00:00",
        "end_at": "2026-06-01T10:00:00+00:00",
        "reason": "custom reason",
        "overlap_fraction": 0.75,
    }


def test_serialise_dropped_intents_empty_list_is_empty() -> None:
    assert _serialise_dropped_intents([]) == []
