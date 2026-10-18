"""``meeting_debrief_pending`` re-checks posture for attendees unresolved at snapshot time.

The snapshot keeps an unresolved attendee by raw name and email. If that email later resolves
to an entity that is not ``active`` (or to the owner), the debrief must not name them
(bu-yqxbc8). An email that still resolves to nothing is shown, as before.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.tools.relationship.meeting_debrief import meeting_debrief_pending

pytestmark = pytest.mark.unit

NOW = datetime(2031, 3, 1, 12, 0, tzinfo=UTC)


class _Pool:
    """Routes the three reads the tool makes: debrief rows, email resolution, posture."""

    def __init__(
        self,
        attendees: list[dict[str, Any]],
        people: list[dict[str, Any]],
        active_ids: set[str] | None = None,
    ) -> None:
        self._attendees = attendees
        self._people = people
        self._active_ids = active_ids or set()
        self.resolve_calls: list[list[str]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM meeting_debriefs" in sql:
            return [
                {
                    "id": uuid.uuid4(),
                    "event_id": uuid.uuid4(),
                    "occurrence_start": NOW,
                    "event_title": "Planning",
                    "attendees": json.dumps(self._attendees),
                    "prompted_at": NOW,
                    "batch_position": 1,
                }
            ]
        if "relationship.entity_facts" in sql:
            wanted = list(args[0])
            self.resolve_calls.append(wanted)
            return [p for p in self._people if p["email"] in wanted]
        if "FROM public.entities" in sql:
            ids = {str(i) for i in args[0]}
            return [{"id": uuid.UUID(i)} for i in ids if i in self._active_ids]
        raise AssertionError(sql)


def _person(email: str, posture: str, *, owner: bool = False) -> dict[str, Any]:
    return {
        "entity_id": uuid.uuid4(),
        "email": email,
        "name": "Resolved Later",
        "posture": posture,
        "roles": ["owner"] if owner else [],
    }


def _stranger(email: str = "new@example.test", name: str = "New Person") -> dict[str, Any]:
    return {"entity_id": None, "name": name, "email": email}


async def _shown(attendees, people) -> list[str]:
    result = await meeting_debrief_pending(_Pool(attendees, people))
    return [a["email"] for d in result["debriefs"] for a in d["attendees"]]


@pytest.mark.parametrize("posture", ["memorial", "quiet", "no_contact"])
async def test_unresolved_attendee_that_later_resolves_to_an_inactive_entity_is_not_named(
    posture,
) -> None:
    people = [_person("new@example.test", posture)]
    keeper = _stranger("other@example.test", "Other")
    result = await meeting_debrief_pending(_Pool([_stranger(), keeper], people))

    assert [a["email"] for a in result["debriefs"][0]["attendees"]] == ["other@example.test"]


async def test_debrief_with_only_a_withheld_attendee_is_not_listed() -> None:
    result = await meeting_debrief_pending(
        _Pool([_stranger()], [_person("new@example.test", "no_contact")])
    )

    assert result == {"debriefs": []}


async def test_unresolved_attendee_that_resolves_to_the_owner_is_not_named() -> None:
    people = [_person("new@example.test", "active", owner=True)]

    assert await _shown([_stranger()], people) == []


async def test_attendee_that_still_resolves_to_nothing_is_still_shown() -> None:
    assert await _shown([_stranger()], []) == ["new@example.test"]


async def test_unresolved_attendee_that_now_resolves_to_an_active_entity_is_shown() -> None:
    assert await _shown([_stranger()], [_person("new@example.test", "active")]) == [
        "new@example.test"
    ]


async def test_email_match_ignores_case_and_surrounding_space() -> None:
    people = [_person("new@example.test", "memorial")]

    assert await _shown([_stranger("  New@Example.TEST ")], people) == []


async def test_resolution_is_one_batched_lookup_of_only_the_unresolved_emails() -> None:
    resolved = {"entity_id": str(uuid.uuid4()), "name": "Known", "email": "known@example.test"}
    pool = _Pool([resolved, _stranger(), _stranger("b@example.test")], [], {resolved["entity_id"]})

    await meeting_debrief_pending(pool)

    assert len(pool.resolve_calls) == 1
    assert pool.resolve_calls[0] == ["b@example.test", "new@example.test"]


async def test_withholding_logs_nothing(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        await meeting_debrief_pending(
            _Pool([_stranger(name="Hidden Name")], [_person("new@example.test", "memorial")])
        )

    assert "Hidden Name" not in caplog.text and "new@example.test" not in caplog.text
