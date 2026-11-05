"""RFC 0038 S1 invocation labels: representation, join and fail-closed readings."""

import pytest

from butlers.core.goal_labels import (
    CATEGORIES,
    EXPOSURE_CAUSES,
    Label,
    LabelRefused,
    admission_label,
    legacy_label,
    output_label,
)

pytestmark = pytest.mark.unit

OWNER = Label.owner()


def test_the_six_categories_and_closed_exposure_causes_are_exact():
    assert CATEGORIES == {
        "health",
        "finance",
        "location",
        "routine",
        "relationship_notes",
        "credentials",
    }
    assert EXPOSURE_CAUSES == ("ingress", "read", "child", "inbox", "recovery", "legacy_unknown")


def test_unknown_provenance_is_world_with_every_category():
    unknown = Label.unknown()
    assert (unknown.instr, unknown.data, unknown.sens) == ({"world"}, {"world"}, CATEGORIES)
    assert unknown.exposed and not OWNER.exposed


def test_join_is_componentwise_union_and_never_narrows():
    health = Label(frozenset({"owner"}), frozenset({"external:mail-7"}), frozenset({"health"}))
    finance = Label(frozenset({"owner_device"}), frozenset({"owner"}), frozenset({"finance"}))
    joined = health.join(finance)
    assert joined.instr == {"owner", "owner_device"}
    assert joined.data == {"external:mail-7", "owner"}
    assert joined.sens == {"health", "finance"}
    assert joined == finance.join(health) and joined.covers(health) and joined.covers(finance)
    assert not health.covers(joined)
    assert joined.join(joined) == joined


@pytest.mark.parametrize(
    "principal",
    ["owner", "owner_device"],
)
def test_only_owner_principals_are_owner_authoritative(principal):
    assert not Label(frozenset({principal}), frozenset({principal}), frozenset()).exposed


@pytest.mark.parametrize("principal", ["world", "domain:finance", "external:mail-7"])
def test_any_other_principal_exposes(principal):
    assert Label(frozenset({"owner"}), frozenset({principal}), frozenset()).exposed
    assert Label(frozenset({principal}), frozenset({"owner"}), frozenset()).exposed


@pytest.mark.parametrize(
    ("version", "instr", "data", "sens"),
    [
        (2, ["owner"], ["owner"], []),  # unknown schema version
        (1, ["owner", "admin"], ["owner"], []),  # unknown principal kind
        (1, ["owner"], ["external:Mail@x"], []),  # a sender address is not an opaque id
        (1, ["owner"], ["owner"], ["health", "politics"]),  # unknown category
        (1, [], ["owner"], []),  # no instruction provenance
        (1, ["owner"], [], []),  # no data provenance
        (1, ["owner", "owner"], ["owner"], []),  # not a set
        (1, None, ["owner"], []),  # missing component
    ],
)
def test_unknown_or_missing_members_refuse_and_are_never_dropped(version, instr, data, sens):
    with pytest.raises(LabelRefused):
        Label.from_row(version, instr, data, sens)


def test_a_row_round_trips_in_canonical_order():
    label = Label(
        frozenset({"owner", "world"}), frozenset({"owner"}), frozenset({"routine", "finance"})
    )
    row = label.to_row()
    assert row == (1, ["owner", "world"], ["owner"], ["finance", "routine"])
    assert Label.from_row(*row) == label
    assert label.canonical() == {
        "v": 1,
        "i": ["owner", "world"],
        "d": ["owner"],
        "c": ["finance", "routine"],
    }


def test_admission_labels_follow_the_admission_exposure():
    assert admission_label(False) == OWNER
    assert admission_label(True) == Label.unknown()


def test_legacy_rows_keep_their_boolean_and_assume_every_category():
    assert legacy_label(True) == Label.unknown()
    clean = legacy_label(False)
    assert (clean.instr, clean.data, clean.sens) == ({"owner"}, {"owner"}, CATEGORIES)
    assert not clean.exposed


@pytest.mark.parametrize(
    ("trust", "untainted", "exposes"),
    [
        ("trusted", None, False),
        ("untrusted", None, True),
        ("stamped", True, False),
        ("stamped", False, True),
        ("stamped", None, True),  # a missing stamp is not owner authority
    ],
)
def test_a_read_output_label_follows_trust_and_stamp(trust, untainted, exposes):
    label = output_label(trust, untainted, frozenset({"health"}))
    assert label.exposed is exposes
    assert label.sens == {"health"}


def test_an_unknown_output_trust_is_world():
    assert output_label("mystery", None, frozenset()).data == {"world"}
