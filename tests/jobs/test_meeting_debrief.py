"""Unit tests for the meeting debrief pure rules (bu-q7vx1q.12).

Selection, prompting and back-off against real tables live in
``tests/integration/test_meeting_debrief_db.py``; this file pins the rules that need no
database: who counts as "another person", what the prompt says, and how an answer is
validated before anything is written.
"""

from __future__ import annotations

import pytest

from butlers.core.commitments import create_commitment
from butlers.jobs.meeting_debrief import _compose_message, _other_attendees, decode_attendees
from butlers.tools.relationship.meeting_debrief import _prepare_items

pytestmark = pytest.mark.unit

SAM = "11111111-1111-1111-1111-111111111111"
PRIYA = "22222222-2222-2222-2222-222222222222"


class TestOtherAttendees:
    def test_owner_resources_and_declined_guests_are_not_other_people(self) -> None:
        meta = {
            "attendees": [
                {"email": "me@example.test", "self": True},
                {"email": "room-4@example.test", "resource": True},
                {"email": "gone@example.test", "response_status": "declined"},
                {"email": "also.gone@example.test", "responseStatus": "declined"},
                {"email": "  Sam@Example.test ", "displayName": "Sam"},
                {"email": "sam@example.test"},
                {"displayName": "No email"},
            ]
        }

        assert _other_attendees(meta) == [{"email": "sam@example.test", "display_name": ""}]

    def test_missing_or_malformed_attendees_yield_nobody(self) -> None:
        assert _other_attendees({}) == []
        assert _other_attendees({"attendees": "sam@example.test"}) == []


class TestComposeMessage:
    def test_lists_meetings_numbered_and_names_unresolved_attendees_by_email(self) -> None:
        message = _compose_message(
            [
                ("Roadmap", [{"name": "Sam Rivera", "email": "sam@example.test"}]),
                ("Intro call", [{"name": "", "email": "new@example.test"}]),
            ],
            weekly_notice=False,
        )

        assert "2 meetings" in message
        assert "1. Roadmap (Sam Rivera)" in message
        assert "2. Intro call (new@example.test)" in message
        assert "ask weekly" not in message

    def test_weekly_notice_is_appended_only_when_asked(self) -> None:
        message = _compose_message(
            [("Roadmap", [{"name": "Sam", "email": "s@x.test"}])], weekly_notice=True
        )

        assert "1 meeting " in message
        assert "ask weekly" in message


def test_decode_attendees_accepts_text_and_decoded_jsonb() -> None:
    assert decode_attendees('[{"entity_id": null}]') == [{"entity_id": None}]
    assert decode_attendees([{"entity_id": SAM}]) == [{"entity_id": SAM}]
    assert decode_attendees(None) == []


class TestPrepareItems:
    sam = {"entity_id": SAM, "name": "Sam", "email": "sam@example.test"}
    priya = {"entity_id": PRIYA, "name": "Priya", "email": "priya@example.test"}
    stranger = {"entity_id": None, "name": "", "email": "new@example.test"}

    def test_sole_attendee_is_the_default_counterparty(self) -> None:
        (item,) = _prepare_items([{"summary": " Send the deck "}], [self.sam])

        assert item["counterparty_entity_id"] == SAM
        assert item["summary"] == "Send the deck"
        assert (item["kind"], item["direction"]) == ("promise", "owner_to_other")

    def test_several_attendees_without_a_named_counterparty_leave_it_null(self) -> None:
        (item,) = _prepare_items([{"summary": "Send the deck"}], [self.sam, self.priya])

        assert item["counterparty_entity_id"] is None

    def test_unresolved_sole_attendee_gives_a_null_counterparty(self) -> None:
        (item,) = _prepare_items([{"summary": "Send the deck"}], [self.stranger])

        assert item["counterparty_entity_id"] is None

    def test_self_direction_never_carries_a_counterparty(self) -> None:
        (item,) = _prepare_items(
            [{"summary": "Draft the plan", "direction": "self", "counterparty_entity_id": SAM}],
            [self.sam],
        )

        assert item["counterparty_entity_id"] is None

    @pytest.mark.parametrize(
        "bad",
        [
            {"summary": ""},
            {"summary": "x", "kind": "wish"},
            {"summary": "x", "direction": "sideways"},
            {"summary": "x", "sphere": "hobby"},
            {"summary": "x", "deadline": "next friday"},
            {"summary": "x", "counterparty_entity_id": PRIYA},
        ],
    )
    def test_invalid_items_are_refused_before_any_write(self, bad: dict) -> None:
        with pytest.raises(ValueError):
            _prepare_items([{"summary": "fine"}, bad], [self.sam])


class TestSphere:
    async def test_unknown_sphere_is_rejected_before_the_database_is_touched(self) -> None:
        with pytest.raises(ValueError, match="sphere"):
            await create_commitment(
                None,  # type: ignore[arg-type]
                source="relationship:commitment",
                summary="Send the deck",
                kind="promise",
                direction="owner_to_other",
                counterparty_entity_id=None,
                confidence=0.9,
                evidence_opened={"source": "meeting_debrief"},
                action_description="send the deck",
                sphere="hobby",
            )
