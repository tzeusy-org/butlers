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


def _owner(n: int) -> list[Requirement]:
    return [Requirement(f"effect:{uuid4()}", "committed", "owner") for _ in range(n)]


def test_judge_never_raises_on_size():
    # N1: owner anchors grow with every approval and reply; relied effects have no bound.
    owner, relied = _owner(40), [str(uuid4()) for _ in range(30)]
    verdict = judge("completed", owner, relied, _receipts())
    assert verdict.final == "partial" and len(verdict.missing) == 70


def test_persisted_evidence_is_capped_with_a_truncation_marker():
    items = [{"ref": f"effect:{uuid4()}"} for _ in range(evidence.MAX_PERSISTED + 5)]
    kept = evidence.persisted(items)
    assert len(kept) == evidence.MAX_PERSISTED
    assert kept[:-1] == items[: evidence.MAX_PERSISTED - 1]
    assert kept[-1] == {"truncated": 6}
    assert evidence.persisted(items[:3]) == items[:3]
    exact = items[: evidence.MAX_PERSISTED]
    assert evidence.persisted(exact) == exact


def test_a_replan_is_never_refused_because_owner_anchors_grew():
    kept = _owner(10)
    derived = [*kept, *_owner(evidence.MAX_REQUIREMENTS + 20)]
    recorded = evidence.plan_requirements((), derived, kept)
    assert len(recorded) == evidence.OWNER_ANCHOR_RESERVE  # new anchors fill only the reserve
    assert set(kept) <= set(recorded)  # every kept owner anchor survives
    assert all(req.anchor == "owner" for req in recorded)


def test_lead_requirements_take_the_room_before_new_owner_anchors():
    kept = _owner(5)
    lead = _leads(40)
    derived = [*kept, *_owner(evidence.MAX_REQUIREMENTS)]
    recorded = evidence.plan_requirements(lead, derived, kept)
    # The reserve would allow 27 new anchors; the lead's 40 leave room for only 19.
    assert len(recorded) == evidence.MAX_REQUIREMENTS
    assert set(kept) | set(lead) <= set(recorded)


def test_only_lead_requirements_past_the_cap_are_refused():
    kept = _owner(evidence.MAX_REQUIREMENTS - 2)
    fits = [lead_requirement(f"effect:{uuid4()}") for _ in range(2)]
    assert len(evidence.plan_requirements(fits, kept, kept)) == evidence.MAX_REQUIREMENTS
    with pytest.raises(RequirementError):
        evidence.plan_requirements([*fits, lead_requirement(f"child:{uuid4()}")], kept, kept)
    # A lead requirement an owner anchor already covers takes no room.
    covered = Requirement(kept[0].ref, "committed", "lead")
    assert evidence.plan_requirements([*fits, covered], kept, kept) == merge(kept, fits)
    with pytest.raises(RequirementError):
        evidence.plan_requirements(
            [lead_requirement(f"effect:{uuid4()}") for _ in range(65)], (), ()
        )


def _leads(n: int) -> list[Requirement]:
    return [lead_requirement(f"effect:{uuid4()}") for _ in range(n)]


def _next_version(lead, derived, previous) -> tuple[Requirement, ...]:
    kept = [req for req in previous if req.anchor == "owner"]
    return evidence.plan_requirements(lead, derived, kept)


def test_owner_anchors_recorded_for_free_never_crowd_out_a_later_lead():
    # N4: v1 has 10 lead requirements and 100 derived anchors; v2's lead adds one more.
    derived = _owner(100)
    v1 = evidence.plan_requirements(_leads(10), derived, ())
    v2 = _next_version(_leads(11), derived, v1)
    assert sum(req.anchor == "lead" for req in v2) == 11
    assert {req for req in v1 if req.anchor == "owner"} <= set(v2)


def test_a_lead_budget_of_half_the_bound_survives_any_owner_history():
    derived = _owner(evidence.MAX_REQUIREMENTS + 6)
    v1 = evidence.plan_requirements((), derived, ())
    assert len(v1) == evidence.OWNER_ANCHOR_RESERVE
    v2 = _next_version(_leads(evidence.MAX_REQUIREMENTS // 2), derived, v1)
    assert len(v2) == evidence.MAX_REQUIREMENTS


def test_legacy_kept_anchors_past_the_reserve_are_all_kept_and_none_added():
    kept = _owner(evidence.OWNER_ANCHOR_RESERVE + 8)
    recorded = evidence.plan_requirements(_leads(3), [*kept, *_owner(20)], kept)
    assert set(kept) <= set(recorded)
    assert len(recorded) == len(kept) + 3
    budget = evidence.MAX_REQUIREMENTS - len(kept)
    with pytest.raises(RequirementError, match=f"at most {budget}"):
        evidence.plan_requirements(_leads(budget + 1), kept, kept)


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
