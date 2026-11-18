"""Pure claim rules for automated-evidence facts (bu-s11n0s.14, slice S2b). No database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.roster_imports import roster_module

_facts = roster_module("relationship", "tools/automated_evidence_facts")
AWAY_UNTIL_PREDICATE = _facts.AWAY_UNTIL_PREDICATE
UNDELIVERABLE_EMAIL_PREDICATE = _facts.UNDELIVERABLE_EMAIL_PREDICATE
Claim = _facts.Claim
claims_for = _facts.claims_for

_RECEIVED = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)


def _auto_reply(**overrides: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "original_sent": True,
        "sender_was_recipient": True,
        "sender_authenticated": True,
        "away_until": "2026-11-24",
        "failed_recipients": [],
    }
    evidence.update(overrides)
    return evidence


def _bounce(**overrides: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "original_sent": True,
        "sender_was_recipient": False,
        "sender_authenticated": True,
        "away_until": None,
        "failed_recipients": ["gone@example.test"],
    }
    evidence.update(overrides)
    return evidence


def test_bound_authenticated_auto_reply_claims_away_until_for_the_sender() -> None:
    claims = claims_for("auto_reply", _auto_reply(), "Ada@Example.test", _RECEIVED)

    assert claims == (Claim("ada@example.test", AWAY_UNTIL_PREDICATE, "2026-11-24"),)


@pytest.mark.parametrize(
    "overrides",
    [
        {"original_sent": False},
        {"sender_was_recipient": False},
        {"sender_authenticated": False},
        {"away_until": None},
        {"away_until": "24/11/2026"},
        {"away_until": "2026-11-16"},  # before receipt
        {"away_until": "2027-05-17"},  # beyond 180 days
        {"original_sent": "true"},  # a string is not a verified check
    ],
)
def test_auto_reply_without_every_check_claims_nothing(overrides: dict[str, Any]) -> None:
    assert claims_for("auto_reply", _auto_reply(**overrides), "ada@example.test", _RECEIVED) == ()


def test_auto_reply_from_a_senderless_row_claims_nothing() -> None:
    assert claims_for("auto_reply", _auto_reply(), "unknown", _RECEIVED) == ()


def test_bounce_claims_each_failed_address_once() -> None:
    evidence = _bounce(failed_recipients=["Gone@Example.test", "gone@example.test", "b@x.test"])

    claims = claims_for("bounce", evidence, "mailer-daemon@googlemail.com", _RECEIVED)

    assert claims == (
        Claim("b@x.test", UNDELIVERABLE_EMAIL_PREDICATE, "b@x.test"),
        Claim("gone@example.test", UNDELIVERABLE_EMAIL_PREDICATE, "gone@example.test"),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"original_sent": False},  # backscatter: the owner never sent the original
        {"sender_authenticated": False},
        {"failed_recipients": []},
        {"failed_recipients": "gone@example.test"},
        {"failed_recipients": ["not-an-address"]},
    ],
)
def test_unbound_or_unauthenticated_bounce_claims_nothing(overrides: dict[str, Any]) -> None:
    evidence = _bounce(**overrides)

    assert claims_for("bounce", evidence, "mailer-daemon@googlemail.com", _RECEIVED) == ()


@pytest.mark.parametrize("flag", ["original_sent", "sender_authenticated"])
@pytest.mark.parametrize("value", [False, None, "true", 1, "yes"])
def test_a_forged_dsn_needs_both_the_sent_original_and_gmails_authentication(
    flag: str, value: Any
) -> None:
    # A co-recipient can send a DSN-shaped message: either check alone is not enough, and only
    # the JSON literal true counts as a passed check.
    evidence = _bounce(**{flag: value})

    assert claims_for("bounce", evidence, "mailer-daemon@googlemail.com", _RECEIVED) == ()


def test_kind_and_evidence_must_agree() -> None:
    assert claims_for("bounce", _auto_reply(), "ada@example.test", _RECEIVED) == ()
    assert claims_for("auto_reply", _bounce(), "ada@example.test", _RECEIVED) == ()
    assert claims_for(None, _auto_reply(), "ada@example.test", _RECEIVED) == ()
    assert claims_for("auto_reply", None, "ada@example.test", _RECEIVED) == ()
    assert claims_for("auto_reply", ["not", "a", "dict"], "ada@example.test", _RECEIVED) == ()
