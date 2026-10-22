"""Dark admission decisions consume verified infrastructure evidence, never prompts."""

from dataclasses import replace

import pytest

from butlers.core.goal_admission import (
    Evidence,
    EvidenceRefused,
    Grant,
    Obligation,
    StafferContract,
    derive_admission,
    intersect,
)
from tests.switchboard.goal_admission_fixtures import catalog

pytestmark = pytest.mark.unit


def test_verified_origins_and_exposure():
    for owner, forwarded in ((True, False), (True, True), (False, False)):
        plan = derive_admission(
            Evidence("message", "channel", owner=owner, forwarded=forwarded), catalog()
        )
        assert plan.origin == ("owner" if owner else "third_party")
        assert plan.exposed == (forwarded or not owner)
        grants = {g.capability: g for g in plan.ceiling}
        assert "finance.pay" not in grants and "health.approve" not in grants
        if not owner:
            assert "health.profile" not in grants
            assert "home.actuate" not in grants and "health.schedule" not in grants
            assert grants["messenger.send"].park_only
            assert not grants["messenger.notify"].park_only
    # An email owner assertion without RFC0017 channel authorization is not owner.
    assert (
        derive_admission(Evidence("message", "email", owner=False), catalog()).origin
        == "third_party"
    )
    with pytest.raises(EvidenceRefused):
        derive_admission(Evidence("feed", "device"), catalog())
    obligation = Obligation("health", digest="subscription-v1")
    plan = derive_admission(Evidence("feed", "device", obligation=obligation), catalog())
    assert plan.origin == "obligation" and plan.exposed
    assert {g.capability for g in plan.ceiling} == {
        "health.read",
        "health.record",
        "health.profile",
        "messenger.notify",
    }


def test_caused_and_runtime_obligation_ceiling():
    third = derive_admission(Evidence("message", "mail"), catalog())
    requested = (Grant("health.record"), Grant("home.actuate"))
    bounded = intersect(requested, third.ceiling)
    assert bounded == (Grant("health.record"),)
    plan = derive_admission(
        Evidence(
            "occurrence",
            "schedule",
            obligation=Obligation("health", "v1", requested, created_exposed=True),
        ),
        catalog(),
    )
    assert plan.exposed
    assert intersect(plan.ceiling, (Grant("health.record"),)) == bounded
    assert intersect(
        (Grant("health.read", (("entity", ("a", "b")),)),),
        (Grant("health.read", (("entity", ("b", "c")),)),),
    ) == (Grant("health.read", (("entity", ("b",)),)),)
    assert intersect((Grant("messenger.send"),), (Grant("messenger.send", park_only=True),))[
        0
    ].park_only


def test_staffer_contract_is_closed():
    contract = StafferContract("qa", "qa", "git-v1", (Grant("health.read"),), "catalog-v1")
    plan = derive_admission(Evidence("staffer", "qa", staffer=contract), catalog())
    assert plan.origin == "staffer" and plan.exposed
    assert plan.ceiling == contract.capabilities
    for grants in (
        (Grant("finance.pay"),),
        (Grant("missing.tool"),),
        (Grant("health.approve"),),
        (Grant("health.read"), Grant("health.read")),
    ):
        with pytest.raises(EvidenceRefused):
            derive_admission(
                Evidence("staffer", "qa", staffer=replace(contract, capabilities=grants)), catalog()
            )
    for invalid in (replace(contract, catalog_digest="stale"), replace(contract, version=2)):
        with pytest.raises(EvidenceRefused):
            derive_admission(Evidence("staffer", "qa", staffer=invalid), catalog())
    with pytest.raises(ValueError):
        Grant("health.*")


def test_domain_input_stamp_is_not_an_owner_assertion():
    obligation = Obligation("health", "v1", created_exposed=False)
    for stamp in (None, False, True):
        plan = derive_admission(
            Evidence("domain", "health", obligation=obligation, untainted_owner_stamp=stamp),
            catalog(),
        )
        assert plan.exposed == (stamp is not True)
    contract = StafferContract(
        "concierge", "concierge", "v1", (Grant("health.read"),), "catalog-v1"
    )
    assert derive_admission(
        Evidence("staffer", "concierge", staffer=contract, outside_content=True), catalog()
    ).exposed


def test_limits_are_explicit_finite_and_uncached():
    from butlers.core.runtime_config import GoalAdmissionConfig

    with pytest.raises(ValueError):
        GoalAdmissionConfig.model_validate({})
    from tests.switchboard.goal_admission_fixtures import limits

    valid = GoalAdmissionConfig.model_validate(limits())
    assert valid.chain_limit == 2
    for value in ("NaN", "Infinity", "-1"):
        bad = limits()
        bad["origins"][0]["budget"]["max_spend"] = value
        with pytest.raises(ValueError):
            GoalAdmissionConfig.model_validate(bad)
