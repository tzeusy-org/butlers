"""Prepared-action door rendering in the insight digest (bu-2jtfw.11).

A candidate whose linked prepared action is still actionable must render a
door (verb set); one that is already terminal must render without a door and
with an honest factual line -- never a dead approve button. Pure-function
coverage at the real seam (``roster.switchboard.tools.insight.broker``'s
``render_prepared_action_door`` / ``prepared_action_terminal_line`` /
``_decorate_candidate_with_door``) -- no DB needed, since delivery_cycle's
live status resolution is a separate, already-isolated concern
(``_resolve_prepared_action_statuses``).
"""

from __future__ import annotations

import uuid

import pytest

from butlers.tools.switchboard.insight.broker import (
    _decorate_candidate_with_door,
    prepared_action_terminal_line,
    render_prepared_action_door,
)

pytestmark = pytest.mark.unit


def _candidate(**overrides: object) -> dict:
    base = {
        "id": uuid.uuid4(),
        "origin_butler": "relationship",
        "message": "Avery is overdue for a check-in.",
        "prepared_action_id": uuid.uuid4(),
    }
    base.update(overrides)
    return base


class TestRenderPreparedActionDoor:
    def test_no_linked_action_renders_no_door(self) -> None:
        assert render_prepared_action_door(None, "pending") is None

    def test_unresolvable_status_renders_no_door(self) -> None:
        assert render_prepared_action_door(uuid.uuid4(), None) is None

    @pytest.mark.parametrize("status", ["pending", "approved"])
    def test_live_status_renders_the_verb_set(self, status: str) -> None:
        prepared_action_id = uuid.uuid4()
        door = render_prepared_action_door(prepared_action_id, status)
        assert door is not None
        assert door["prepared_action_id"] == str(prepared_action_id)
        assert door["verbs"] == ["approve", "dismiss"]

    @pytest.mark.parametrize("status", ["rejected", "expired", "executed", "abandoned"])
    def test_terminal_status_renders_no_door(self, status: str) -> None:
        assert render_prepared_action_door(uuid.uuid4(), status) is None


class TestPreparedActionTerminalLine:
    def test_live_status_has_no_terminal_line(self) -> None:
        assert prepared_action_terminal_line("pending") is None
        assert prepared_action_terminal_line("approved") is None
        assert prepared_action_terminal_line(None) is None

    def test_expired_prepared_action_gets_the_honest_line(self) -> None:
        assert prepared_action_terminal_line("expired") == "(prepared reply expired)"

    @pytest.mark.parametrize(
        "status",
        ["rejected", "expired", "executed", "abandoned"],
    )
    def test_every_terminal_status_has_a_distinct_honest_line(self, status: str) -> None:
        line = prepared_action_terminal_line(status)
        assert line is not None
        assert line.startswith("(") and line.endswith(")")


class TestDecorateCandidateWithDoor:
    def test_live_action_appends_the_door_affordance(self) -> None:
        candidate = _candidate()
        decorated = _decorate_candidate_with_door(candidate, "pending")
        assert decorated["message"] == f"{candidate['message']} [reply approve/dismiss]"
        # Original candidate dict is untouched -- pure with respect to input.
        assert candidate["message"] == "Avery is overdue for a check-in."

    def test_terminal_action_renders_without_a_door_and_with_the_honest_line(self) -> None:
        candidate = _candidate()
        decorated = _decorate_candidate_with_door(candidate, "expired")
        assert decorated["message"] == f"{candidate['message']} (prepared reply expired)"
        assert "[reply approve/dismiss]" not in decorated["message"]

    def test_unresolvable_status_leaves_the_message_unchanged(self) -> None:
        candidate = _candidate()
        decorated = _decorate_candidate_with_door(candidate, None)
        assert decorated["message"] == candidate["message"]

    def test_candidate_without_a_prepared_action_is_unaffected(self) -> None:
        candidate = _candidate(prepared_action_id=None)
        decorated = _decorate_candidate_with_door(candidate, None)
        assert decorated["message"] == candidate["message"]
