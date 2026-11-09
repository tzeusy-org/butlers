"""Owner-pinned ties keep an even split (bu-dhloi1.193).

The owner gave General two override rows at the same priority and asked for equal weight.
Evidence-based scoring picks the best of a tie once each candidate has enough samples, so the
split used to collapse to one model. When every tied candidate is pinned by a butler override
the owner has already decided; selection round-robins instead. Catalog-default ties (no
override) keep evidence scoring, and breaker/unverified exclusion happens in SQL before this
runs, so excluded entries never appear here.
"""

from __future__ import annotations

import uuid

import pytest

from butlers.core.model_routing import (
    WINNER_REASON_EVIDENCE_SCORE,
    WINNER_REASON_OWNER_EVEN_SPLIT,
    WINNER_REASON_ROUND_ROBIN,
    _rank_candidates,
    _score_row,
    _select_resolved_row,
)

pytestmark = pytest.mark.unit


def _row(name: str, rn: int, total: int, *, pinned: bool | None, p50: float = 1000.0) -> dict:
    row = {
        "id": uuid.uuid5(uuid.NAMESPACE_URL, name),
        "model_id": name,
        "rn": rn,
        "total": total,
        "effective_tier": "workhorse",
        "effective_priority": 50,
        "success_count": 20,  # enough samples that evidence scoring engages
        "failure_count": 0,
        "p50_duration_ms": p50,
        "p95_duration_ms": p50,
    }
    if pinned is not None:
        row["owner_pinned"] = pinned
    return row


def _pair(pinned_a: bool | None, pinned_b: bool | None) -> list[dict]:
    # "slow" would lose every evidence comparison: a split must not depend on it.
    return [
        _row("fast", 0, 2, pinned=pinned_a, p50=500),
        _row("slow", 1, 2, pinned=pinned_b, p50=400_000),
    ]


def test_unpinned_ties_still_pick_the_evidence_winner():
    rows = _pair(False, False)
    scores = {r["id"]: _score_row(r, None) for r in rows}
    assert all(s.score is not None for s in scores.values())
    for counter in range(4):
        assert _select_resolved_row(rows, counter)["model_id"] == "fast"
        winner, reason = _rank_candidates(rows, scores, counter)
        assert (winner["model_id"], reason) == ("fast", WINNER_REASON_EVIDENCE_SCORE)


def test_rows_without_the_pin_column_keep_evidence_scoring():
    rows = _pair(None, None)
    assert {_select_resolved_row(rows, c)["model_id"] for c in range(4)} == {"fast"}


def test_pinned_ties_alternate_even_when_one_candidate_would_win_on_evidence():
    rows = _pair(True, True)
    picks = [_select_resolved_row(rows, counter)["model_id"] for counter in range(6)]
    assert picks == ["fast", "slow"] * 3  # an exact even split, whatever the scores say


def test_pinned_ties_report_the_owner_split_as_the_receipt_reason():
    rows = _pair(True, True)
    scores = {r["id"]: _score_row(r, None) for r in rows}
    picks = [_rank_candidates(rows, scores, counter) for counter in range(4)]
    assert [w["model_id"] for w, _ in picks] == ["fast", "slow", "fast", "slow"]
    assert {reason for _, reason in picks} == {WINNER_REASON_OWNER_EVEN_SPLIT}


def test_a_mixed_tie_is_not_an_owner_split():
    # One pinned, one catalog default: the owner decided nothing about this tie.
    rows = _pair(True, False)
    assert {_select_resolved_row(rows, c)["model_id"] for c in range(4)} == {"fast"}


def test_three_pinned_candidates_split_three_ways():
    rows = [_row(n, i, 3, pinned=True) for i, n in enumerate(("a", "b", "c"))]
    assert [_select_resolved_row(rows, c)["model_id"] for c in range(6)] == list("abcabc")


def test_single_candidate_is_unaffected():
    assert _select_resolved_row([_row("solo", 0, 1, pinned=True)], 5)["model_id"] == "solo"


def test_round_robin_reason_stays_for_unscored_unpinned_ties():
    rows = [_row("a", 0, 2, pinned=False), _row("b", 1, 2, pinned=False)]
    for r in rows:
        r["success_count"] = 0
    scores = {r["id"]: _score_row(r, None) for r in rows}
    assert _rank_candidates(rows, scores, 1)[1] == WINNER_REASON_ROUND_ROBIN
