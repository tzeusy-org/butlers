"""bu-q7vx1q.88.3: the per-rule "except people I know" option on block and skip rules.

A rule whose condition carries ``except_known_contacts`` is passed over only when the envelope
says the sender is a known contact. A stranger, an unclassified sender (``None``) and an
unflagged rule keep today's behavior, and later rules still apply after a passed-over rule.
"""

from __future__ import annotations

import time

import pytest

from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator


def _evaluator(scope: str, rules: list[dict]) -> IngestionPolicyEvaluator:
    evaluator = IngestionPolicyEvaluator(scope=scope, db_pool=None)
    evaluator._last_loaded_at = time.monotonic()
    evaluator._rules = rules
    return evaluator


def _rule(rule_id: str, action: str, *, domain: str = "bulk.example", **condition) -> dict:
    return {
        "id": rule_id,
        "rule_type": "sender_domain",
        "condition": {"domain": domain, "match": "exact", **condition},
        "action": action,
        "priority": 10,
        "name": None,
        "created_at": "2026-02-01T00:00:00Z",
        "created_by": "dashboard",
        "promoted_from_suggestion_id": None,
    }


def _envelope(known_contact: bool | None) -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address="someone@bulk.example",
        source_channel="email",
        known_contact=known_contact,
    )


@pytest.mark.parametrize(
    ("scope", "action"), [("connector:gmail:acct", "block"), ("global", "skip")]
)
@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("known", [True, False, None])
def test_only_a_known_contact_passes_a_flagged_block_or_skip_rule(
    scope: str, action: str, flag: bool, known: bool | None
) -> None:
    condition = {"except_known_contacts": True} if flag else {}
    evaluator = _evaluator(scope, [_rule("r1", action, **condition)])

    decision = evaluator.evaluate(_envelope(known))

    if flag and known is True:
        assert decision.action == "pass_through"
        assert decision.matched_rule_id is None
    else:
        assert decision.action == action
        assert decision.matched_rule_id == "r1"


def test_a_later_rule_still_applies_after_a_passed_over_rule() -> None:
    evaluator = _evaluator(
        "global",
        [
            _rule("excepted", "skip", except_known_contacts=True),
            _rule("later", "metadata_only"),
        ],
    )

    decision = evaluator.evaluate(_envelope(True))

    assert (decision.action, decision.matched_rule_id) == ("metadata_only", "later")


def test_the_option_is_ignored_on_actions_that_cannot_honor_it() -> None:
    evaluator = _evaluator("global", [_rule("r1", "metadata_only", except_known_contacts=True)])

    assert evaluator.evaluate(_envelope(True)).action == "metadata_only"
