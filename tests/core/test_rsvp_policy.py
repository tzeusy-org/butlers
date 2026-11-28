"""The closed predicate vocabulary and the pure evaluator (bu-5ii4gj.9 S2).

REQ-calendar-invitation-docket-001 and -002: a closed vocabulary with no title or description,
first match by position, disagreeing matches prepare nothing, an unknown fact never matches.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from butlers.core.temporal.rsvp_policy import (
    Evaluation,
    InvitationFacts,
    Policy,
    PolicyError,
    evaluate_policies,
    parse_predicate,
    parse_predicates,
)

pytestmark = pytest.mark.unit


def _policy(
    pid: str,
    predicates: list[dict],
    *,
    position: int = 1,
    verdict: str = "declined",
    send_updates: str = "none",
    version: int = 1,
    revoked_at=None,
) -> Policy:
    return Policy.from_row(
        {
            "id": pid,
            "position": position,
            "predicates": predicates,
            "verdict": verdict,
            "send_updates": send_updates,
            "version": version,
            "revoked_at": revoked_at,
        }
    )


FOCUS = {"kind": "overlaps_focus_or_out_of_office", "value": True}
FACTS = InvitationFacts(
    organizer_verdict="known_entity",
    organizer_roles=frozenset({"manager"}),
    overlaps_focus_or_out_of_office=True,
    conflicts_with_accepted=False,
    outside_declared_hours=False,
    recurring=True,
    attendee_count=6,
    lead_time_hours=48,
)


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "title_contains", "value": "board"},
        {"kind": "description", "value": "x"},
        {"kind": "location", "value": "x"},
        {"kind": "recurring", "value": 1},
        {"kind": "organizer_verdict", "value": "first_contact"},
        {"kind": "organizer_role", "value": "Manager"},
        {"kind": "attendee_count", "value": 5},
        {"kind": "attendee_count", "value": 5, "op": "eq"},
        {"kind": "attendee_count", "value": True, "op": "lte"},
        {"kind": "lead_time_hours", "value": 99999, "op": "lte"},
        {"kind": "recurring", "value": True, "op": "lte"},
        {"kind": "recurring", "value": True, "note": "x"},
        "recurring",
    ],
)
def test_a_predicate_outside_the_vocabulary_is_refused(raw) -> None:
    with pytest.raises(PolicyError):
        parse_predicate(raw)


def test_a_policy_has_one_to_eight_distinct_predicates() -> None:
    with pytest.raises(PolicyError):
        parse_predicates([])
    with pytest.raises(PolicyError):
        parse_predicates([FOCUS, FOCUS])
    with pytest.raises(PolicyError):
        parse_predicates("recurring")


def test_the_facts_carry_no_title_or_description() -> None:
    names = {f.name for f in dataclasses.fields(InvitationFacts)}
    assert not names & {"title", "summary", "description", "location", "attendees", "notes"}


def test_every_predicate_must_hold_and_the_match_names_the_facts() -> None:
    policy = _policy(
        "p1",
        [
            FOCUS,
            {"kind": "organizer_role", "value": "manager"},
            {"kind": "attendee_count", "value": 10, "op": "lte"},
            {"kind": "lead_time_hours", "value": 24, "op": "gte"},
        ],
        version=3,
    )
    result = evaluate_policies([policy], FACTS)
    assert result.status == "prepared"
    match = result.prepared
    assert match is not None and (match.policy_id, match.version) == ("p1", 3)
    assert {m.kind: m.observed for m in match.matched} == {
        "overlaps_focus_or_out_of_office": True,
        "organizer_role": "manager",
        "attendee_count": 6,
        "lead_time_hours": 48,
    }
    other = dataclasses.replace(FACTS, attendee_count=11)
    assert evaluate_policies([policy], other).status == "no_match"


def test_an_unknown_fact_never_matches() -> None:
    policy = _policy("p1", [{"kind": "recurring", "value": False}])
    assert evaluate_policies([policy], InvitationFacts()).status == "no_match"
    negated = _policy("p2", [{"kind": "outside_declared_hours", "value": False}])
    assert evaluate_policies([negated], InvitationFacts(outside_declared_hours=None)).status == (
        "no_match"
    )


def test_a_revoked_policy_matches_nothing() -> None:
    policy = _policy("p1", [FOCUS], revoked_at=datetime(2026, 7, 1, tzinfo=UTC))
    assert evaluate_policies([policy], FACTS) == Evaluation("no_match")


def test_agreeing_matches_use_the_first_by_position() -> None:
    late = _policy("b", [FOCUS], position=5)
    early = _policy("a", [{"kind": "recurring", "value": True}], position=2)
    result = evaluate_policies([late, early], FACTS)
    assert result.status == "prepared" and result.prepared is not None
    assert result.prepared.policy_id == "a"
    assert [m.policy_id for m in result.matches] == ["a", "b"]


def test_disagreeing_matches_prepare_nothing_and_are_all_named() -> None:
    declines = _policy("a", [FOCUS], position=1, verdict="declined")
    accepts = _policy("b", [{"kind": "recurring", "value": True}], position=2, verdict="accepted")
    result = evaluate_policies([accepts, declines], FACTS)
    assert result.status == "disagree" and result.prepared is None
    assert result.disagreeing == ("a", "b")
    differing_updates = _policy("c", [FOCUS], position=3, send_updates="all")
    assert evaluate_policies([declines, differing_updates], FACTS).status == "disagree"


def test_evaluation_is_the_same_whatever_order_the_policies_arrive_in() -> None:
    policies = [
        _policy("a", [FOCUS], position=1),
        _policy("b", [{"kind": "recurring", "value": True}], position=1),
        _policy("c", [{"kind": "recurring", "value": True}], position=0),
    ]
    forward = evaluate_policies(policies, FACTS)
    assert forward == evaluate_policies(list(reversed(policies)), FACTS)
    assert forward.prepared is not None and forward.prepared.policy_id == "c"
