"""bu-jv1moa.64 PR2: message admission scopes a pair-bound capability to the request's refs."""

from __future__ import annotations

import json

import pytest

from butlers.core.goal_admission import (
    MAX_ATTACHMENT_REFS,
    Capability,
    CatalogView,
    Evidence,
    EvidenceRefused,
    derive_admission,
)
from butlers.core.resource_pairs import (
    MAX_ID_LENGTH,
    encode_pair,
    lazy_attachment_refs,
    pair_key,
)
from butlers.tools.attachment_materialize import _routed_refs

pytestmark = pytest.mark.unit

PAIR = ("message_id", "attachment_id")
MAT = "switchboard__attachment_materialize"
VIEW = CatalogView(
    "d",
    (
        Capability(MAT, "switchboard", "read", resource_pair=PAIR),
        Capability("health__lookup", "health", "read"),
    ),
)


def _grants(evidence):
    return {g.capability: g for g in derive_admission(evidence, VIEW).ceiling}


@pytest.mark.parametrize("owner", [False, True])
def test_the_pair_bound_grant_carries_exactly_the_requests_refs(owner):
    refs = (("m1", "a1"), ("m1", "a2"))
    grants = _grants(Evidence("message", "mail", owner=owner, attachment_refs=refs))
    assert dict(grants[MAT].resources) == {
        pair_key(PAIR): tuple(sorted(encode_pair(*ref) for ref in refs))
    }
    # Every other capability keeps its plain, unscoped grant.
    assert grants["health__lookup"].resources == ()


@pytest.mark.parametrize("owner", [False, True])
def test_no_refs_means_a_grant_with_no_pair_scope(owner):
    assert _grants(Evidence("message", "mail", owner=owner))[MAT].resources == ()


def test_an_id_the_exact_identifier_rule_forbids_is_dropped_and_the_rest_kept():
    evidence = Evidence("message", "mail", attachment_refs=(("m*", "a1"), ("m1", "a1")))
    assert dict(_grants(evidence)[MAT].resources)[pair_key(PAIR)] == (encode_pair("m1", "a1"),)
    only_bad = Evidence("message", "mail", attachment_refs=(("m[", "a1"),))
    assert _grants(only_bad)[MAT].resources == ()


def test_only_a_message_may_carry_refs_and_the_shape_is_checked():
    with pytest.raises(EvidenceRefused):
        Evidence("feed", "x", attachment_refs=(("m", "a"),))
    for bad in ([("m", "a")], (("m",),), (("m", ""),), (("m", 3),), (("m", "a" * 1025),)):
        with pytest.raises(EvidenceRefused):
            Evidence("message", "x", attachment_refs=bad)  # type: ignore[arg-type]
    too_many = tuple((f"m{i}", "a") for i in range(MAX_ATTACHMENT_REFS + 1))
    with pytest.raises(EvidenceRefused):
        Evidence("message", "x", attachment_refs=too_many)


ATTACHMENTS = [
    {"source_message_id": "m1", "source_attachment_id": "a2"},
    {"source_message_id": "m1", "source_attachment_id": "a1"},
    {"source_message_id": "m1", "source_attachment_id": "a1"},
    {"source_message_id": "m1"},
    {"storage_ref": "blob:x"},
    "not-a-dict",
]


def test_lazy_refs_are_sorted_distinct_and_need_both_ids():
    assert lazy_attachment_refs(ATTACHMENTS) == (("m1", "a1"), ("m1", "a2"))
    assert lazy_attachment_refs(json.dumps(ATTACHMENTS)) == (("m1", "a1"), ("m1", "a2"))
    for nothing in (None, "", "{", "{}", 3, {}, []):
        assert lazy_attachment_refs(nothing) == ()


def test_the_routing_context_check_reads_refs_the_same_way_admission_does():
    assert _routed_refs({"attachments": ATTACHMENTS}) == set(lazy_attachment_refs(ATTACHMENTS))
    assert _routed_refs(None) == set() and _routed_refs({"attachments": "x"}) == set()


def _lazy(message_id, attachment_id):
    return {"source_message_id": message_id, "source_attachment_id": attachment_id}


def test_a_realistic_long_gmail_attachment_id_is_a_ref_and_admits(caplog):
    gmail_id = "ANGjdJ" + "x" * 594  # base64url, several hundred characters
    refs = lazy_attachment_refs([_lazy("m1", gmail_id)])
    assert refs == (("m1", gmail_id),) and len(gmail_id) == 600
    grants = _grants(Evidence("message", "mail", attachment_refs=refs))
    assert dict(grants[MAT].resources)[pair_key(PAIR)] == (encode_pair("m1", gmail_id),)


def test_an_over_long_id_drops_only_its_own_ref_and_the_message_still_admits(caplog):
    too_long = "y" * (MAX_ID_LENGTH + 1)
    attachments = [_lazy("m1", "a1"), _lazy("m1", too_long), _lazy(too_long, "a2"), _lazy("m1", "")]
    with caplog.at_level("INFO", logger="butlers.core.resource_pairs"):
        refs = lazy_attachment_refs(attachments)
    assert refs == (("m1", "a1"),)
    # A content-blind count is logged, never an id.
    assert [r.getMessage() for r in caplog.records] == [
        "dropped 3 attachment refs with an empty or over-long id"
    ]
    assert too_long not in caplog.text
    # The edge of the cap is kept; the Evidence check is only a backstop behind the filter.
    assert lazy_attachment_refs([_lazy("m", "z" * MAX_ID_LENGTH)]) == (("m", "z" * MAX_ID_LENGTH),)
    grants = _grants(Evidence("message", "mail", attachment_refs=refs))
    assert dict(grants[MAT].resources)[pair_key(PAIR)] == (encode_pair("m1", "a1"),)
