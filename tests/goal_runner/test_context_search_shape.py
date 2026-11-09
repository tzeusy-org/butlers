"""``context.search``: the closed query, the fusion and the packet bounds (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.goal_runner import context_search as cs
from butlers.goal_runner.context_search import (
    ContextSearchLimits,
    SourceHit,
    SourceResult,
    parse_query,
)

LIMITS = ContextSearchLimits()


def refused(arguments, limits=LIMITS) -> Reason:
    with pytest.raises(BrokerRefused) as error:
        parse_query(arguments, limits)
    return error.value.reason


def test_a_minimal_query_takes_the_default_k():
    query = parse_query({"query": "dentist"}, LIMITS)
    assert (query.query, query.k, query.anchors, query.regions) == ("dentist", 10, (), ())


def test_every_s5_key_is_accepted():
    anchor = str(uuid.uuid4())
    query = parse_query(
        {
            "query": "dentist",
            "anchors": [anchor],
            "since": "2026-01-01T00:00:00+00:00",
            "until": "2026-02-01T00:00:00+00:00",
            "regions": ["health"],
            "kinds": ["fact"],
            "k": 3,
        },
        LIMITS,
    )
    assert query.anchors == (anchor,)
    assert query.since == datetime(2026, 1, 1, tzinfo=UTC)
    assert (query.regions, query.kinds, query.k) == (("health",), ("fact",), 3)


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        "dentist",
        {},
        {"k": 3},
        {"query": "x", "extra": 1},
        {"query": "x", "grants": ["a"]},  # authority is never a model argument
        {"query": "x", "source": "health"},
        {"query": ""},
        {"query": "   "},
        {"query": 5},
        {"query": "x" * 1025},
        {"query": "é" * 513},  # bounded in UTF-8 bytes, not characters
        {"query": "\ud800"},
        {"query": "x", "k": 0},
        {"query": "x", "k": 21},
        {"query": "x", "k": True},
        {"query": "x", "k": 2.5},
        {"query": "x", "anchors": ["not-a-uuid"]},
        {"query": "x", "anchors": [str(uuid.uuid4()).upper()]},
        {"query": "x", "anchors": [str(uuid.uuid4())] * 2},
        {"query": "x", "anchors": [str(uuid.uuid4()) for _ in range(17)]},
        {"query": "x", "anchors": "abc"},
        {"query": "x", "regions": [str(n) for n in range(17)]},
        {"query": "x", "kinds": [""]},
        {"query": "x", "kinds": ["a" * 65]},
        {"query": "x", "as_of": "2026-01-01T00:00:00"},  # no zone: a guess
        {"query": "x", "as_of": "yesterday"},
        {"query": "x", "since": "2026-02-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
        {"query": "x", "since": "2026-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
        {"query": "x", "as_of": "2026-01-01T00:00:00Z", "since": "2025-01-01T00:00:00Z"},
    ],
)
def test_anything_outside_the_closed_shape_refuses_policy(arguments):
    assert refused(arguments) is Reason.POLICY


def test_limits_come_from_the_snapshot_not_the_defaults():
    small = ContextSearchLimits(query_bytes=4, k_max=2)
    assert refused({"query": "abcde"}, small) is Reason.POLICY
    assert refused({"query": "abc", "k": 3}, small) is Reason.POLICY
    assert parse_query({"query": "abc"}, small).k == 2  # the default never exceeds the cap


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), None, True])
def test_a_missing_or_invalid_limit_refuses_to_exist(bad):
    with pytest.raises(ValueError):
        ContextSearchLimits(total_s=bad)


def test_a_per_domain_deadline_cannot_outlast_the_total():
    with pytest.raises(ValueError):
        ContextSearchLimits(per_domain_s=3.0, total_s=2.0)


def hit(ref, authority="domain", excerpt="text", **kwargs):
    return SourceHit(ref, "r", "fact", excerpt, authority=authority, **kwargs)


def outcomes(**by_source):
    return {name: cs._Outcome("ok", SourceResult(tuple(hits))) for name, hits in by_source.items()}


def test_fusion_ranks_within_each_source_and_never_compares_raw_scores():
    merged = cs._fused(outcomes(a=[hit("a1"), hit("a2")], b=[hit("b1"), hit("b2")]))
    # Equal ranks tie on the fused score and fall back to the source name, then the ref.
    assert [i.hit.ref for i in merged] == ["a1", "b1", "a2", "b2"]


def test_credence_orders_display_before_rank():
    merged = cs._fused(
        outcomes(
            a=[hit("third", "third_party"), hit("unknown", "bogus")],
            b=[hit("owner", "owner"), hit("fix", "owner_correction")],
        )
    )
    assert [i.hit.ref for i in merged] == ["fix", "owner", "third", "unknown"]


def test_the_merge_is_deterministic_whatever_order_sources_finish_in():
    first = cs._fused(outcomes(a=[hit("a1")], b=[hit("b1")]))
    second = cs._fused(outcomes(b=[hit("b1")], a=[hit("a1")]))
    assert first == second


def test_a_failed_source_contributes_nothing_to_the_merge():
    merged = cs._fused({"a": cs._Outcome("timeout"), "b": outcomes(b=[hit("b1")])["b"]})
    assert [i.source for i in merged] == ["b"]


def packet(items, k=20, **limits):
    return cs._packet(items, k, ContextSearchLimits(**limits))


def test_k_omits_whole_items_and_counts_them():
    items = cs._fused(outcomes(a=[hit(f"a{n}") for n in range(5)]))
    kept, omitted = packet(items, k=2)
    assert [e["ref"] for e in kept] == ["a:a0", "a:a1"]
    assert omitted == {"count": 3, "reason": "k"}


def test_the_packet_budget_omits_whole_items_never_clipping_a_reference():
    items = cs._fused(outcomes(a=[hit(f"a{n}", excerpt="x" * 400) for n in range(8)]))
    kept, omitted = packet(items, packet_tokens=400)
    assert 0 < len(kept) < 8
    assert omitted == {"count": 8 - len(kept), "reason": "packet_budget"}
    assert all(e["ref"].startswith("a:a") and e["authority"] == "domain" for e in kept)


def test_an_excerpt_is_bounded_and_says_so():
    items = cs._fused(outcomes(a=[hit("a1", excerpt="y" * 1000), hit("a2", excerpt="short")]))
    kept, omitted = packet(items, excerpt_chars=100)
    assert [len(e["excerpt"]) for e in kept] == [100, 5]
    assert [e["excerpt_truncated"] for e in kept] == [True, False]
    assert omitted == {"count": 0, "reason": None}


def test_an_unknown_authority_is_labelled_unknown_not_passed_through():
    kept, _ = packet(cs._fused(outcomes(a=[hit("a1", authority="trusted-by-me")])))
    assert kept[0]["authority"] == "unknown"


def test_each_entry_carries_provenance():
    when = datetime(2026, 3, 1, tzinfo=UTC)
    kept, _ = packet(
        cs._fused(outcomes(a=[hit("a1", observed_at=when, conflict_set="c1")])),
    )
    assert kept[0] == {
        "ref": "a:a1",
        "source": "a",
        "region": "r",
        "kind": "fact",
        "excerpt": "text",
        "excerpt_truncated": False,
        "observed_at": when.isoformat(),
        "authority": "domain",
        "conflict_set": "c1",
    }
