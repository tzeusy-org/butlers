"""The commitment extraction predicate (bu-s208f, REQ-commitment-lifecycle-007).

These tests never touch a database. They pin the decision the extractor makes
before any entity or ledger contact: *is this sentence an explicit first-person
commitment?* The database consequences of a yes live in
``test_commitment_extraction_db.py``.

Both directions are pinned deliberately. An extractor that never fires passes
every "does not create a commitment" assertion for free, so the curated set
below asserts the positive verdicts and the false-negative count as hard as it
asserts the false-positive rate — a silent extractor fails this file, and so
does a marker-substring matcher.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from butlers.core.commitments import COMMITMENT_DIRECTIONS, COMMITMENT_KINDS
from butlers.tools.relationship.commitments import (
    _MARKERS,
    detect_commitment,
    detect_completion,
)

pytestmark = pytest.mark.unit

# A Monday, so weekday-relative deadlines in the fixtures are unambiguous.
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)

# The curated set REQ-commitment-lifecycle-007's acceptance measures against.
# Five openings and five near-misses.
#
# The negatives are chosen so that the *veto* carries its share of them, not
# just the marker table. A near-miss that contains no first-person marker at
# all ("You should send Maya that book") is refused before the hedge and
# conditional rules are ever consulted, so a set built only from those measures
# one rule and credits the whole predicate. Three of the five negatives below
# therefore carry an explicit marker verbatim, and two of them are refused by
# nothing but the veto:
#
#   * "I should probably get around to calling Sam" — the bead's own example;
#     no marker ("I should" is not one), so marker absence refuses it.
#   * "You'll send Maya that book tomorrow" — the first positive's whole action
#     in the second person, with no hedge to fall back on.
#   * "Priya said she'll send me the invoice" — contains ``'ll send``; the
#     commitment is real but it is not the owner's.
#   * "I'll probably send Sam that book tomorrow" — contains ``I'll`` *and* the
#     first positive's entire action. Only the hedge refuses it.
#   * "If the meeting ends early, I'll call Devi" — contains ``I'll`` verbatim.
#     Only the conditional refuses it.
#
# ``test_..._curated_negatives_exercise_the_veto`` pins that distribution so the
# set cannot quietly decay back into five marker-absence cases.
CURATED_STATEMENTS: tuple[tuple[str, bool], ...] = (
    ("I'll send Sam that book tomorrow.", True),
    ("I promised Maya I'd review her draft this week.", True),
    ("I need to follow up with Priya about the invoice.", True),
    ("I'm going to drop the keys off with Noah on Friday.", True),
    ("I told Devi I would book the table tonight.", True),
    ("I should probably get around to calling Sam.", False),
    ("You'll send Maya that book tomorrow.", False),
    ("Priya said she'll send me the invoice.", False),
    ("I'll probably send Sam that book tomorrow.", False),
    ("If the meeting ends early, I'll call Devi.", False),
)


class TestExplicitOpenings:
    def test_req_commitment_lifecycle_007_explicit_promise_is_detected(self) -> None:
        """ "I'll send Sam that book tomorrow" is a promise to Sam, due tomorrow."""
        signal = detect_commitment("I'll send Sam that book tomorrow.", now=NOW)

        assert signal is not None
        assert signal.kind == "promise"
        assert signal.direction == "owner_to_other"
        assert signal.confidence >= 0.8
        assert signal.counterparty_candidates == ("Sam",)
        assert signal.action_description == "send Sam that book"
        assert signal.deadline is not None
        assert signal.deadline.date() == datetime(2026, 8, 25, tzinfo=UTC).date()

    def test_req_commitment_lifecycle_007_relayed_promise_names_the_counterparty(self) -> None:
        """ "I promised Maya I'd …" carries the counterparty in the marker, not the action."""
        signal = detect_commitment("I promised Maya I'd review her draft this week.", now=NOW)

        assert signal is not None
        assert signal.counterparty_candidates == ("Maya",)
        assert signal.action_description == "review her draft"

    def test_req_commitment_lifecycle_007_follow_up_language_sets_the_kind(self) -> None:
        """RFC 0026 lists "I need to follow up" as explicit; it files as a follow_up."""
        signal = detect_commitment("I need to follow up with Priya about the invoice.", now=NOW)

        assert signal is not None
        assert signal.kind == "follow_up"
        assert signal.confidence >= 0.8

    def test_req_commitment_lifecycle_007_a_contraction_is_not_a_counterparty(self) -> None:
        """A conjoined promise must not offer ``I'll`` to the entity resolver.

        Counterparties are found by scanning for capitalised runs, and "I'll"
        is one. Left unstripped it becomes a candidate name, which costs a
        resolver round-trip and could anchor the commitment to whoever happens
        to match it.
        """
        signal = detect_commitment("I'll send Sam that book tomorrow and I'll call Devi.", now=NOW)

        assert signal is not None
        assert signal.counterparty_candidates == ("Sam", "Devi")

    def test_req_commitment_lifecycle_007_deadline_is_not_part_of_the_action(self) -> None:
        """A restatement on a different day must land on the same action (RFC 0026 §4)."""
        monday = detect_commitment("I'll send Sam that book tomorrow.", now=NOW)
        tuesday = detect_commitment("I'll send Sam that book today.", now=NOW)

        assert monday is not None and tuesday is not None
        assert monday.action_description == tuesday.action_description
        assert monday.deadline != tuesday.deadline


class TestNearMisses:
    def test_req_commitment_lifecycle_007_hedged_statement_is_not_a_commitment(self) -> None:
        """The spec's own counter-example: hedging keeps it below the bar."""
        assert detect_commitment("I should probably get around to calling Sam.", now=NOW) is None

    def test_req_commitment_lifecycle_007_second_person_advice_is_not_a_commitment(self) -> None:
        """Same action, same deadline, wrong person committing.

        Stated without a hedge on purpose: "You should send …" would also be
        refused for its ``should``, which would leave the person-of-the-verb
        rule untested.
        """
        assert detect_commitment("You'll send Maya that book tomorrow.", now=NOW) is None

    def test_req_commitment_lifecycle_007_third_party_promise_is_not_a_commitment(self) -> None:
        """ "she'll send" is a commitment the owner did not make."""
        assert detect_commitment("Priya said she'll send me the invoice.", now=NOW) is None

    def test_req_commitment_lifecycle_007_conditional_promise_is_not_a_commitment(self) -> None:
        """Contains ``I'll`` verbatim; the condition is what disqualifies it."""
        assert detect_commitment("If the meeting ends early, I'll call Devi.", now=NOW) is None

    def test_req_commitment_lifecycle_007_hedge_vetoes_an_otherwise_explicit_marker(self) -> None:
        """The marker alone is not enough — the speaker left themselves an exit."""
        assert detect_commitment("I'll probably send Sam that book tomorrow.", now=NOW) is None

    def test_req_commitment_lifecycle_007_a_question_is_not_a_commitment(self) -> None:
        assert detect_commitment("Should I send Sam that book tomorrow?", now=NOW) is None


class TestCuratedSet:
    def test_req_commitment_lifecycle_007_curated_set_verdicts(self) -> None:
        """Every curated statement gets the verdict it was curated for.

        Asserted per-statement rather than in aggregate so a failure names the
        sentence that moved, and so neither direction can be satisfied by an
        extractor that has stopped firing.
        """
        wrong = [
            (text, expected, detect_commitment(text, now=NOW) is not None)
            for text, expected in CURATED_STATEMENTS
            if (detect_commitment(text, now=NOW) is not None) != expected
        ]
        assert not wrong, f"curated verdicts disagree: {wrong}"

    def test_req_commitment_lifecycle_007_curated_false_positive_rate_under_20_percent(
        self,
    ) -> None:
        """Acceptance criterion 7, measured rather than asserted by construction."""
        negatives = [text for text, expected in CURATED_STATEMENTS if not expected]
        false_positives = [
            text for text in negatives if detect_commitment(text, now=NOW) is not None
        ]

        assert negatives, "the curated set must contain near-misses to measure against"
        rate = len(false_positives) / len(negatives)
        assert rate < 0.2, f"false-positive rate {rate:.0%} on {false_positives}"

    def test_req_commitment_lifecycle_007_curated_set_has_no_false_negatives(self) -> None:
        """The guard that stops a silent extractor from clearing the FP rate."""
        missed = [
            text
            for text, expected in CURATED_STATEMENTS
            if expected and detect_commitment(text, now=NOW) is None
        ]
        assert not missed, f"explicit commitments not detected: {missed}"

    def test_req_commitment_lifecycle_007_curated_negatives_exercise_the_veto(self) -> None:
        """The near-misses must be near enough to reach the hedge/conditional rules.

        A negative with no first-person marker is refused by the marker table
        before the veto is consulted. A curated set made only of those would
        report a 0% false-positive rate while leaving most of the predicate
        unmeasured — so require that at least two negatives match a marker
        verbatim and are refused anyway.
        """
        marker_carrying = [
            text
            for text, expected in CURATED_STATEMENTS
            if not expected and any(pattern.search(text) for pattern, _kind, _conf in _MARKERS)
        ]

        assert len(marker_carrying) >= 2, (
            "curated negatives no longer reach the veto; the false-positive rate "
            f"would measure only the marker table. Marker-carrying negatives: {marker_carrying}"
        )
        assert all(detect_commitment(text, now=NOW) is None for text in marker_carrying)


class TestCompletions:
    def test_req_commitment_lifecycle_008_completion_is_detected(self) -> None:
        signal = detect_completion("I sent Sam the book.")

        assert signal is not None
        assert signal.action_description == "sent Sam the book"
        assert signal.counterparty_candidates == ("Sam",)

    def test_req_commitment_lifecycle_008_perfect_tense_completion_is_detected(self) -> None:
        signal = detect_completion("I've already sent Sam the book.")

        assert signal is not None
        assert signal.counterparty_candidates == ("Sam",)

    def test_req_commitment_lifecycle_008_an_opening_is_never_read_as_a_completion(self) -> None:
        """ "I told Sam I'd send the book" is past tense but opens a promise."""
        assert detect_completion("I told Sam I'd send the book.") is None
        assert detect_commitment("I told Sam I'd send the book.", now=NOW) is not None

    def test_req_commitment_lifecycle_008_a_future_statement_is_not_a_completion(self) -> None:
        assert detect_completion("I'll send Sam the book.") is None


class TestLexiconAgreement:
    def test_req_commitment_lifecycle_007_every_marker_kind_is_a_ledger_kind(self) -> None:
        """The extractor's vocabulary cannot drift from the ledger's."""
        kinds = {kind for _pattern, kind, _confidence in _MARKERS}
        assert kinds <= COMMITMENT_KINDS

    def test_req_commitment_lifecycle_007_every_marker_clears_the_surfacing_floor(self) -> None:
        """REQ-007 auto-creates explicit statements only, at confidence >= 0.8."""
        assert all(confidence >= 0.8 for _pattern, _kind, confidence in _MARKERS)

    def test_req_commitment_lifecycle_007_direction_is_a_ledger_direction(self) -> None:
        signal = detect_commitment("I'll send Sam that book tomorrow.", now=NOW)
        assert signal is not None
        assert signal.direction in COMMITMENT_DIRECTIONS
