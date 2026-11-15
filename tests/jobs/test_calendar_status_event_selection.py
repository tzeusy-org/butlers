"""Out-of-office and working-location events never reach meeting prep or meeting cards.

bu-s11n0s.4 S2: core_341 stores Google's ``eventType``. A declared status
(:data:`~butlers.core.temporal.calendar_provenance.STATUS_EVENT_TYPES`) is not a meeting, so
every prep selector filters it in SQL. These tests pin that each selector names exactly the
shared set, so the SQL and the Python rule cannot drift apart. The real-Postgres proof is in
``tests/integration/test_calendar_status_event_selection_pg.py``.
"""

from __future__ import annotations

import re

import pytest

from butlers.core.temporal.calendar_provenance import STATUS_EVENT_TYPES
from butlers.jobs import calendar_prep, meeting_card

pytestmark = pytest.mark.unit

_EXCLUSION = re.compile(r"ce\.event_type NOT IN \(([^)]*)\)")


def _excluded_types(sql: str) -> list[set[str]]:
    return [
        {value.strip().strip("'") for value in match.split(",")}
        for match in _EXCLUSION.findall(sql)
    ]


@pytest.mark.parametrize(
    ("sql", "branches"),
    [
        # One-off events and recurring occurrences each filter.
        (meeting_card._OCCURRENCE_SQL, 2),
        # Relationship and email prep share one upcoming-event selector.
        (calendar_prep._UPCOMING_LINKED_EVENTS_SQL, 1),
    ],
    ids=["meeting_card", "calendar_prep"],
)
def test_every_prep_selector_excludes_exactly_the_status_types(sql: str, branches: int) -> None:
    found = _excluded_types(sql)
    assert len(found) == branches
    assert all(types == set(STATUS_EVENT_TYPES) for types in found)
