"""The evidence predicate a settled goal is judged by (bu-4c7nvp.1.3 PR-C), pure: no database."""

from __future__ import annotations

from uuid import uuid4

import pytest

from butlers.goal_runner import evidence
from butlers.goal_runner.evidence import (
    Receipts,
    Requirement,
    RequirementError,
    judge,
    lead_requirement,
    merge,
)


def _receipts(**kwargs) -> Receipts:
    base = {
        "effects": {},
        "owner_delivered": frozenset(),
        "children": {},
        "consumed": frozenset(),
        "reply_delivered": False,
    }
    return Receipts(**{**base, **kwargs})


def test_completion_evidence_is_dark_by_default():
    assert evidence.EVIDENCE_COMPLETION_LIVE is False


def test_a_requirement_is_one_closed_shape():
    effect, child, inbox = (str(uuid4()) for _ in range(3))
    assert lead_requirement(f"effect:{effect}") == Requirement(
        f"effect:{effect}", "committed", "lead"
    )
    assert lead_requirement(f"child:{child}").evidence == "returned"
    assert lead_requirement(f"inbox:{inbox}").evidence == "consumed"
    for ref in ("reply", f"plan:{effect}", f"effect:{effect.upper()}", "effect:x", 3, "effect"):
        with pytest.raises(RequirementError):
            lead_requirement(ref)  # type: ignore[arg-type]
    for args in (
        (f"child:{child}", "committed", "lead"),
        ("reply", "committed", "owner"),
        (f"reply:{effect}", "owner_delivered", "owner"),
        (f"effect:{effect}", "committed", "model"),
    ):
        with pytest.raises(RequirementError):
            Requirement(*args)
    owner = Requirement(f"effect:{effect}", "owner_delivered", "owner")
    assert Requirement.from_json(owner.to_json()) == owner
    for bad in ({"ref": f"effect:{effect}"}, [owner.ref], {**owner.to_json(), "x": "y"}):
        with pytest.raises(RequirementError):
            Requirement.from_json(bad)


def test_merge_is_canonical_and_an_owner_anchor_wins():
    a, b = sorted(str(uuid4()) for _ in range(2))
    lead_a = lead_requirement(f"effect:{a}")
    owner_a = Requirement(f"effect:{a}", "committed", "owner")
    lead_b = lead_requirement(f"inbox:{b}")
    assert merge([lead_b, lead_a], [owner_a]) == merge([owner_a], [lead_a, lead_b])
    assert merge([lead_a], [owner_a]) == (owner_a,)
    too_many = [lead_requirement(f"effect:{uuid4()}") for _ in range(evidence.MAX_REQUIREMENTS + 1)]
    with pytest.raises(RequirementError):
        merge(too_many)


def test_a_completed_claim_needs_every_proof_and_otherwise_ends_partial():
    sent, child, inbox, relied = (str(uuid4()) for _ in range(4))
    required = [
        Requirement("reply", "owner_delivered", "owner"),
        Requirement(f"effect:{sent}", "owner_delivered", "owner"),
        lead_requirement(f"child:{child}"),
        Requirement(f"inbox:{inbox}", "consumed", "owner"),
    ]
    proved = _receipts(
        effects={sent: "committed", relied: "committed"},
        owner_delivered=frozenset({sent}),
        children={child: "returned"},
        consumed=frozenset({inbox}),
        reply_delivered=True,
    )
    verdict = judge("completed", required, [relied], proved)
    assert verdict.final == "completed" and verdict.missing == ()
    assert len(verdict.satisfied) == 5

    for broken, ref, found in (
        ({"reply_delivered": False}, "reply", None),
        ({"owner_delivered": frozenset()}, f"effect:{sent}", "committed"),
        ({"effects": {sent: "failed", relied: "committed"}}, f"effect:{sent}", "failed"),
        ({"children": {child: "failed"}}, f"child:{child}", "failed"),
        ({"consumed": frozenset()}, f"inbox:{inbox}", None),
        ({"effects": {sent: "committed"}}, f"effect:{relied}", None),
    ):
        verdict = judge("completed", required, [relied], _replace(proved, broken))
        assert verdict.final == "partial", broken
        assert [(m.requirement.ref, m.found) for m in verdict.missing] == [(ref, found)], broken


def test_the_predicate_never_upgrades_a_partial_or_failed_claim():
    effect = str(uuid4())
    receipts = _receipts(effects={effect: "committed"})
    for outcome in ("partial", "failed"):
        assert judge(outcome, [], [effect], receipts).final == outcome
    missing = judge("failed", [lead_requirement(f"effect:{uuid4()}")], [], receipts)
    assert missing.final == "failed" and len(missing.missing) == 1
    assert judge("completed", [], [], receipts).final == "completed"


def _replace(receipts: Receipts, fields: dict) -> Receipts:
    return Receipts(
        **{
            name: fields.get(name, getattr(receipts, name))
            for name in ("effects", "owner_delivered", "children", "consumed", "reply_delivered")
        }
    )
