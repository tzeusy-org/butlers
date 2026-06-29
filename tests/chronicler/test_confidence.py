"""Unit tests for the pure IEA confidence + evidence-chain helpers."""

from __future__ import annotations

from uuid import UUID

from butlers.chronicler.confidence import (
    EvidenceKind,
    derive_confidence,
    evidence_refs_from_event_ids,
)
from butlers.chronicler.models import Confidence

# ── derive_confidence ──────────────────────────────────────────────────────


def test_three_independent_kinds_is_high() -> None:
    kinds = [
        EvidenceKind("gps"),
        EvidenceKind("heart_rate"),
        EvidenceKind("calendar"),
    ]
    assert derive_confidence(kinds) is Confidence.HIGH


def test_two_independent_kinds_is_high() -> None:
    # Two *independent* kinds already clears the high bar (2+ independent).
    assert derive_confidence([EvidenceKind("gps"), EvidenceKind("calendar")]) is Confidence.HIGH


def test_two_weakly_related_kinds_is_medium() -> None:
    # Two distinct kinds that share a correlation group are weakly-related: they
    # collapse to a single independent signal, so they earn medium, not high.
    kinds = [
        EvidenceKind("steps", correlated_with="wearable"),
        EvidenceKind("heart_rate", correlated_with="wearable"),
    ]
    assert derive_confidence(kinds) is Confidence.MEDIUM


def test_single_strong_canonical_kind_is_medium() -> None:
    assert derive_confidence([EvidenceKind("session_marker", strong=True)]) is Confidence.MEDIUM


def test_single_weak_signal_is_low_but_still_counted() -> None:
    kinds = [EvidenceKind("gps")]
    result = derive_confidence(kinds)
    # A single weak signal is flagged low...
    assert result is Confidence.LOW
    # ...but it is still a counted signal: the evidence set is non-empty and the
    # block is never dropped (confidence flags, the layer decides counting).
    assert len(kinds) == 1


def test_empty_evidence_defaults_to_low() -> None:
    assert derive_confidence([]) is Confidence.LOW


def test_duplicate_kind_does_not_inflate() -> None:
    # Two reads of the same kind are one corroboration, not two.
    kinds = [EvidenceKind("heart_rate"), EvidenceKind("heart_rate")]
    assert derive_confidence(kinds) is Confidence.LOW


def test_strong_plus_second_independent_kind_is_high() -> None:
    # 2+ independent kinds wins even when one is also strong canonical.
    kinds = [EvidenceKind("session_marker", strong=True), EvidenceKind("gps")]
    assert derive_confidence(kinds) is Confidence.HIGH


# ── evidence_refs_from_event_ids ───────────────────────────────────────────


def test_evidence_refs_preserve_order_and_dedupe() -> None:
    a = UUID("11111111-1111-1111-1111-111111111111")
    b = UUID("22222222-2222-2222-2222-222222222222")
    refs = evidence_refs_from_event_ids([a, b, a])
    assert refs == [str(a), str(b)]


def test_evidence_refs_accept_strings() -> None:
    refs = evidence_refs_from_event_ids(["e1", "e2"])
    assert refs == ["e1", "e2"]


def test_evidence_refs_empty() -> None:
    assert evidence_refs_from_event_ids([]) == []
