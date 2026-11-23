"""bu-jv1moa.64: a composite (message_id, attachment_id) grant scope, enforced by the broker.

A capability that declares ``resource_pair`` is scoped by one synthetic grant key whose values
are length-prefixed encoded pairs, never by two independent sets. One that does not declare it
behaves exactly as before.
"""

from __future__ import annotations

import itertools

import pytest

from butlers.capability_broker.service import _in_scope
from butlers.core.goal_admission import Grant, intersect
from butlers.core.resource_pairs import encode_pair, pair_key, pair_scope
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from tests.capability_broker.helpers import capability

pytestmark = pytest.mark.unit

PAIR = ("message_id", "attachment_id")
KEY = pair_key(PAIR)


def _materialize(**overrides):
    return capability(
        "attachment_materialize",
        resource_params=PAIR,
        resource_pair=PAIR,
        selection_bound=frozenset(),
        **overrides,
    )


def _grant(*refs: tuple[str, str]) -> Grant:
    return Grant("general__attachment_materialize", (pair_scope(PAIR, refs),))


def _args(message_id, attachment_id):
    return {"message_id": message_id, "attachment_id": attachment_id}


def test_the_admitted_pair_is_in_scope_and_nothing_else():
    grant = _grant(("m1", "a1"), ("m1", "a2"))
    cap = _materialize()
    assert _in_scope(cap, grant, _args("m1", "a1"))
    assert _in_scope(cap, grant, _args("m1", "a2"))
    assert not _in_scope(cap, grant, _args("m2", "a1"))
    assert not _in_scope(cap, grant, _args("m1", "a3"))


def test_two_admitted_refs_are_never_recombined_into_a_cross_product():
    grant = _grant(("m1", "a1"), ("m2", "a2"))
    cap = _materialize()
    assert _in_scope(cap, grant, _args("m1", "a1"))
    assert _in_scope(cap, grant, _args("m2", "a2"))
    # Each id is individually admitted, but the pair is not.
    assert not _in_scope(cap, grant, _args("m1", "a2"))
    assert not _in_scope(cap, grant, _args("m2", "a1"))


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"message_id": "m1"},
        {"attachment_id": "a1"},
        _args("m1", ""),
        _args("", "a1"),
        _args("m1", None),
        _args(["m1"], ["a1"]),
        _args(1, 1),
    ],
)
def test_a_missing_empty_list_or_non_string_pair_is_out_of_scope(args):
    assert not _in_scope(_materialize(), _grant(("m1", "a1")), args)


def test_a_grant_with_no_pair_scope_has_an_empty_scope():
    cap = _materialize()
    assert not _in_scope(cap, Grant("general__attachment_materialize"), _args("m1", "a1"))


def test_a_stray_restriction_key_is_still_refused():
    stray = Grant("general__attachment_materialize", (("bogus", ("x",)),))
    assert not _in_scope(_materialize(), stray, _args("m1", "a1"))


def test_an_id_containing_the_old_separator_or_digits_cannot_forge_a_pair():
    grant = _grant(("a", "bc"))
    cap = _materialize()
    for forged in [("ab", "c"), ("a/b", "c"), ("a:", "bc"), ("1:a", "bc")]:
        if forged == ("a", "bc"):
            continue
        assert not _in_scope(cap, grant, _args(*forged)), forged
    # The encoding is injective over every split of every short string.
    seen = {}
    alphabet = "a1:/ "
    for text in map("".join, itertools.product(alphabet, repeat=4)):
        for i in range(1, len(text)):
            value = encode_pair(text[:i], text[i:])
            assert seen.setdefault(value, (text[:i], text[i:])) == (text[:i], text[i:])


def test_the_scope_rejects_ids_the_exact_identifier_rule_forbids():
    with pytest.raises(ValueError):
        Grant("x", (pair_scope(PAIR, [("m*", "a")]),))
    with pytest.raises(ValueError):
        pair_scope(PAIR, [("", "a")])
    with pytest.raises(ValueError):
        pair_scope(PAIR, [])


def test_intersecting_pair_scopes_never_widens_to_a_cross_product():
    left = (_grant(("m1", "a1"), ("m2", "a2")),)
    right = (_grant(("m1", "a2"), ("m2", "a2")),)
    (narrowed,) = intersect(left, right)
    assert dict(narrowed.resources)[KEY] == (encode_pair("m2", "a2"),)
    cap = _materialize()
    assert _in_scope(cap, narrowed, _args("m2", "a2"))
    assert not _in_scope(cap, narrowed, _args("m1", "a1"))
    assert not _in_scope(cap, narrowed, _args("m1", "a2"))
    assert not _in_scope(cap, narrowed, _args("m2", "a1"))


def test_an_unscoped_parent_leaves_the_childs_pair_scope_alone():
    child = (_grant(("m1", "a1")),)
    parent = (Grant("general__attachment_materialize"),)
    assert intersect(child, parent) == child


def _reference_in_scope(capability_, grant, args):
    """The scope check as it was before the pair rule (the behaviour to preserve)."""
    restrictions = dict(grant.resources)
    if not set(restrictions) <= set(capability_.resource_params):
        return False
    if capability_.selection_bound - set(restrictions):
        return False
    for name, permitted in restrictions.items():
        value = args.get(name)
        values = [value] if type(value) is str else value
        if type(values) is not list or not values:
            return False
        if any(type(item) is not str or item not in permitted for item in values):
            return False
    return True


@pytest.mark.parametrize("selection_bound", [frozenset(), frozenset({"b"})])
def test_a_capability_without_the_declaration_behaves_exactly_as_before(selection_bound):
    cap = capability("tool", resource_params=("a", "b"), selection_bound=selection_bound)
    assert cap.resource_pair is None
    grants = [
        Grant("general__tool"),
        Grant("general__tool", (("a", ("x",)),)),
        Grant("general__tool", (("b", ("y", "z")),)),
        Grant("general__tool", (("a", ("x",)), ("b", ("y",)))),
        Grant("general__tool", (("c", ("x",)),)),
        Grant("general__tool", (pair_scope(("a", "b"), [("x", "y")]),)),
    ]
    values = [None, "x", "y", ["x"], ["x", "y"], [], 3, ""]
    for grant in grants:
        for a, b in itertools.product(values, values):
            args = {k: v for k, v in (("a", a), ("b", b)) if v is not None}
            assert _in_scope(cap, grant, args) == _reference_in_scope(cap, grant, args), (
                grant,
                args,
            )


def test_only_attachment_materialize_declares_a_pair_today():
    declared = {
        item.canonical_name: item.resource_pair
        for item in TOOL_PRESENTATION_INVENTORY
        if item.resource_pair is not None
    }
    assert declared == {"attachment_materialize": PAIR}


def test_a_grant_with_both_per_param_sets_and_the_pair_key_enforces_every_one():
    from butlers.core.goal_admission import decode_grants, encode_grants

    grant = Grant(
        "general__attachment_materialize",
        (("message_id", ("m1", "m2")), pair_scope(PAIR, [("m1", "a1"), ("m2", "a2")])),
    )
    cap = _materialize()
    assert _in_scope(cap, grant, _args("m1", "a1"))
    assert _in_scope(cap, grant, _args("m2", "a2"))
    # In the pair set's message but not paired with this attachment.
    assert not _in_scope(cap, grant, _args("m1", "a2"))
    # A pair in the pair set that the per-param message set does not allow.
    narrowed = Grant(
        "general__attachment_materialize",
        (("message_id", ("m1",)), pair_scope(PAIR, [("m1", "a1"), ("m2", "a2")])),
    )
    assert _in_scope(cap, narrowed, _args("m1", "a1"))
    assert not _in_scope(cap, narrowed, _args("m2", "a2"))
    # The combined grant survives the stored form.
    assert decode_grants(encode_grants((grant,))) == (grant,)


def test_the_pair_key_survives_the_stored_grant_codec():
    from butlers.core.goal_admission import decode_grants, encode_grants

    grants = (_grant(("m1", "a1"), ("a:b", "c/d")), Grant("general__other"))
    stored = encode_grants(grants)
    assert KEY in stored[0]["resources"]
    assert decode_grants(stored) == tuple(sorted(grants, key=lambda g: g.capability))
    assert decode_grants(json_roundtrip(stored)) == decode_grants(stored)


def json_roundtrip(value):
    import json

    return json.loads(json.dumps(value))
