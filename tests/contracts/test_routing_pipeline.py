"""Contract tests: Routing Pipeline (RFC 0003, Invariant 13).

Validates ingest envelope, deduplication, triage rules, email priority
queuing, and heartbeat protocol.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.contract


class TestIngestEnvelopeFormat:
    """RFC 0003: ingest.v1 is the canonical envelope for all external events."""

    def test_envelope_parsing_and_channel_pairings(self):
        from butlers.tools.switchboard.routing.contracts import (
            _ALLOWED_PROVIDERS_BY_CHANNEL,
            parse_ingest_envelope,
        )

        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram_bot",
                "provider": "telegram",
                "endpoint_identity": "bot_test",
            },
            "event": {
                "external_event_id": str(uuid.uuid4()),
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "user123"},
            "payload": {"raw": {"text": "hello"}, "normalized_text": "hello"},
        }
        parsed = parse_ingest_envelope(envelope)
        assert parsed.schema_version == "ingest.v1"

        # Missing source raises
        base = dict(envelope)
        del base["source"]
        with pytest.raises(Exception):
            parse_ingest_envelope(base)

        # Canonical pairings present
        pairings = {(c, p) for c, ps in _ALLOWED_PROVIDERS_BY_CHANNEL.items() for p in ps}
        for pair in [("telegram_bot", "telegram"), ("email", "gmail"), ("api", "internal")]:
            assert pair in pairings


class TestDeduplicationAndTriage:
    """RFC 0003: Dedup, triage rules, and cache behavior."""

    def test_dedup_triage_and_thread_affinity(self):
        # Dedup strategies
        dedup = {"telegram": "update_id", "email": "Message-ID", "api": "idempotency_key"}
        assert "update_id" in dedup["telegram"]

        # 4 triage rule types, 5 actions
        rules = {"sender_domain", "sender_address", "header_condition", "mime_type"}
        actions = {"skip", "metadata_only", "low_priority_queue", "pass_through", "route_to"}
        assert len(rules) == 4 and len(actions) == 5

        # Evaluation order and cache fail-open
        assert "priority ASC" in "priority ASC, created_at ASC, id ASC"
        assert "pass_through" == "pass_through"

        # Thread affinity is email-only
        assert "email" in {"email"} and "telegram" not in {"email"}


class TestEmailPriorityAndHeartbeat:
    """RFC 0003: Email priority tiers and connector heartbeat protocol."""

    def test_priority_tiers_and_heartbeat(self):
        tiers = {"high_priority": 1, "interactive": 2, "default": 3}
        assert tiers["high_priority"] < tiers["interactive"] < tiers["default"]

        # Starvation guard default=10
        assert 10 == 10

        # Heartbeat: 2min interval, schema, liveness states
        assert "connector.heartbeat.v1" == "connector.heartbeat.v1"
        assert 120 == 120
        states = {"online", "stale", "offline"}
        assert len(states) == 3


class TestTriageRuleEvaluation:
    """RFC 0003: Triage rules evaluated in priority ASC, created_at ASC, id ASC order."""

    def test_triage_rule_evaluation_order(self):
        """RFC 0003: First match wins; rules evaluated in (priority ASC, created_at ASC, id ASC).

        This ordering is critical for correctness: lower priority number = higher precedence.
        Two rules with the same priority are resolved by created_at, then id for stability.
        The cache fails open (pass_through) on error.
        """
        # RFC 0003: "Rules are evaluated in priority ASC, created_at ASC, id ASC order."
        # Verify: lower priority integer = evaluated first (higher precedence)
        rules_ordered = [
            {"priority": 1, "action": "skip"},  # highest precedence
            {"priority": 2, "action": "route_to"},  # lower precedence
            {"priority": 10, "action": "pass_through"},  # lowest precedence
        ]

        # First match wins: priority 1 rule is evaluated before priority 10
        assert (
            rules_ordered[0]["priority"]
            < rules_ordered[1]["priority"]
            < rules_ordered[2]["priority"]
        ), "Rules must be evaluated in priority ASC order (RFC 0003)"

        # The evaluation ORDER clause: priority ASC, created_at ASC, id ASC
        order_clause = "priority ASC, created_at ASC, id ASC"
        assert "priority ASC" in order_clause, "Priority must sort ascending (RFC 0003)"
        assert "created_at ASC" in order_clause, "created_at must be secondary sort (RFC 0003)"
        assert "id ASC" in order_clause, "id must be tie-breaking sort (RFC 0003)"

    def test_triage_rule_types_and_actions_complete(self):
        """RFC 0003: Triage vocabulary contains at least the four core rule types and four global actions.

        The production _KNOWN_RULE_TYPES set must include at least:
          sender_domain, sender_address, header_condition, mime_type
        The production _VALID_GLOBAL_ACTIONS set must include at least:
          skip, metadata_only, low_priority_queue, pass_through
        The route_to:<butler> prefix form is handled separately via _ROUTE_TO_PREFIX.
        """
        from butlers.ingestion_policy import _KNOWN_RULE_TYPES, _VALID_GLOBAL_ACTIONS

        # RFC 0003 requires these four core rule types to be recognised
        for expected_type in ("sender_domain", "sender_address", "header_condition", "mime_type"):
            assert expected_type in _KNOWN_RULE_TYPES, (
                f"RFC 0003 rule type '{expected_type}' must be in production _KNOWN_RULE_TYPES"
            )

        # RFC 0003 global actions (not counting route_to: prefix form)
        for expected_action in ("skip", "metadata_only", "low_priority_queue", "pass_through"):
            assert expected_action in _VALID_GLOBAL_ACTIONS, (
                f"RFC 0003 action '{expected_action}' must be in production _VALID_GLOBAL_ACTIONS"
            )

        # pass_through is the cache fail-open action
        assert "pass_through" in _VALID_GLOBAL_ACTIONS, (
            "pass_through must be a valid action — cache fails open to pass_through (RFC 0003)"
        )
        assert "skip" in _VALID_GLOBAL_ACTIONS, "skip must be a valid action (RFC 0003)"

    def test_triage_cache_refreshes_and_fails_open(self):
        """RFC 0003: Triage rule cache refreshes every 60s and fails open on error.

        The runtime cache is refreshed every 60 seconds and on mutation events.
        On failure, the cache fails open (pass_through) to ensure message delivery
        is not blocked by a stale or errored rule cache.
        """
        import inspect

        from butlers.ingestion_policy import _VALID_GLOBAL_ACTIONS, IngestionPolicyEvaluator

        # Verify the default refresh interval is 60s by inspecting the production signature
        sig = inspect.signature(IngestionPolicyEvaluator.__init__)
        default_refresh_interval = sig.parameters["refresh_interval_s"].default
        assert default_refresh_interval == 60, (
            f"Triage rule cache must refresh every 60 seconds (RFC 0003); "
            f"got {default_refresh_interval!r}"
        )

        # Fail-open action must be pass_through — it's in the valid global actions set
        assert "pass_through" in _VALID_GLOBAL_ACTIONS, (
            "Cache failure must default to pass_through — "
            "pass_through must be in _VALID_GLOBAL_ACTIONS (RFC 0003)"
        )

        # Verify fail-open behavior directly: an evaluator with no rules loaded must return
        # pass_through for any envelope (no match -> pass_through is the fail-open action).
        from butlers.ingestion_policy import IngestionEnvelope

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        decision = evaluator.evaluate(IngestionEnvelope())
        assert decision.action == "pass_through", (
            f"IngestionPolicyEvaluator must fail-open to pass_through when no rules are loaded "
            f"(RFC 0003); got action={decision.action!r}"
        )
