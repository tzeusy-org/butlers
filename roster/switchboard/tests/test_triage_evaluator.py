"""Unit tests for the deterministic triage evaluator.

Covers rule matching for all four rule types, evaluation order,
thread affinity precedence, fail-open behavior, and pass_through semantics.

See docs/switchboard/pre_classification_triage.md §4.2 and §5.
"""

from __future__ import annotations

import pytest

from butlers.tools.switchboard.triage.evaluator import (
    TriageEnvelope,
    evaluate_triage,
    make_triage_envelope_from_ingest,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _rule(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    rule_type: str = "sender_domain",
    condition: dict | None = None,
    action: str = "pass_through",
    priority: int = 10,
    created_at: str = "2026-02-01T00:00:00Z",
) -> dict:
    return {
        "id": id,
        "rule_type": rule_type,
        "condition": condition or {},
        "action": action,
        "priority": priority,
        "created_at": created_at,
    }


def _email_envelope(
    *,
    sender: str = "user@example.com",
    headers: dict | None = None,
    mime_parts: list[str] | None = None,
) -> TriageEnvelope:
    return TriageEnvelope(
        sender_address=sender,
        source_channel="email",
        headers=headers or {},
        mime_parts=mime_parts or [],
    )


# ---------------------------------------------------------------------------
# No rules → pass_through
# ---------------------------------------------------------------------------


class TestEmptyRuleSet:
    def test_no_rules_returns_pass_through(self) -> None:
        envelope = _email_envelope(sender="anyone@example.com")
        decision = evaluate_triage(envelope, [])
        assert decision.decision == "pass_through"
        assert decision.matched_rule_id is None
        assert decision.matched_rule_type is None

    def test_pass_through_bypasses_llm_is_false(self) -> None:
        envelope = _email_envelope()
        decision = evaluate_triage(envelope, [])
        assert decision.bypasses_llm is False


# ---------------------------------------------------------------------------
# Thread affinity (highest precedence)
# ---------------------------------------------------------------------------


class TestThreadAffinity:
    def test_thread_affinity_overrides_rules(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "suffix"},
                action="route_to:finance",
                priority=10,
            )
        ]
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules, thread_affinity_target="relationship")
        assert decision.decision == "route_to"
        assert decision.target_butler == "relationship"
        assert decision.matched_rule_type == "thread_affinity"
        assert decision.matched_rule_id is None

    def test_thread_affinity_none_falls_through_to_rules(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="route_to:finance",
                priority=10,
            )
        ]
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules, thread_affinity_target=None)
        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"

    def test_bypasses_llm_true_for_thread_affinity(self) -> None:
        envelope = _email_envelope()
        decision = evaluate_triage(envelope, [], thread_affinity_target="health")
        assert decision.bypasses_llm is True


# ---------------------------------------------------------------------------
# sender_domain rule type
# ---------------------------------------------------------------------------


class TestSenderDomainRules:
    def test_exact_domain_match(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"

    def test_exact_domain_no_match_for_subdomain(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="alerts@mail.chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"

    def test_suffix_domain_matches_exact(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "suffix"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"

    def test_suffix_domain_matches_subdomain(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "delta.com", "match": "suffix"},
                action="route_to:travel",
            )
        ]
        envelope = _email_envelope(sender="confirm@mail.delta.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "travel"

    def test_suffix_domain_no_false_positive(self) -> None:
        """notdelta.com should NOT match suffix 'delta.com'."""
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "delta.com", "match": "suffix"},
                action="route_to:travel",
            )
        ]
        envelope = _email_envelope(sender="user@notdelta.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"

    def test_domain_match_is_case_insensitive_sender(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="Alerts@CHASE.COM")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"

    def test_matched_rule_id_returned(self) -> None:
        rule_id = "aaaaaaaa-0000-0000-0000-000000000001"
        rules = [
            _rule(
                id=rule_id,
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.matched_rule_id == rule_id
        assert decision.matched_rule_type == "sender_domain"


# ---------------------------------------------------------------------------
# sender_address rule type
# ---------------------------------------------------------------------------


class TestSenderAddressRules:
    def test_exact_address_match(self) -> None:
        rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "alerts@chase.com"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"

    def test_address_case_insensitive(self) -> None:
        rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "alerts@chase.com"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="ALERTS@CHASE.COM")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"

    def test_address_no_match(self) -> None:
        rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "alerts@chase.com"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="other@chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"


# ---------------------------------------------------------------------------
# header_condition rule type
# ---------------------------------------------------------------------------


class TestHeaderConditionRules:
    def test_present_op_matches_existing_header(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "List-Unsubscribe", "op": "present"},
                action="metadata_only",
            )
        ]
        envelope = _email_envelope(headers={"List-Unsubscribe": "<mailto:unsub@example.com>"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "metadata_only"

    def test_present_op_no_match_when_header_absent(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "List-Unsubscribe", "op": "present"},
                action="metadata_only",
            )
        ]
        envelope = _email_envelope(headers={})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"

    def test_header_key_comparison_is_case_insensitive(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "list-unsubscribe", "op": "present"},
                action="metadata_only",
            )
        ]
        envelope = _email_envelope(headers={"List-Unsubscribe": "yes"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "metadata_only"

    def test_equals_op_matches_exact_value(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Precedence", "op": "equals", "value": "bulk"},
                action="low_priority_queue",
            )
        ]
        envelope = _email_envelope(headers={"Precedence": "bulk"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "low_priority_queue"

    def test_equals_op_no_match_different_value(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Precedence", "op": "equals", "value": "bulk"},
                action="low_priority_queue",
            )
        ]
        envelope = _email_envelope(headers={"Precedence": "list"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"

    def test_contains_op_matches_substring(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={
                    "header": "Auto-Submitted",
                    "op": "equals",
                    "value": "auto-generated",
                },
                action="skip",
            )
        ]
        envelope = _email_envelope(headers={"Auto-Submitted": "auto-generated"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "skip"

    def test_contains_op_matches_partial_header(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "X-Spam-Status", "op": "contains", "value": "YES"},
                action="skip",
            )
        ]
        envelope = _email_envelope(headers={"X-Spam-Status": "YES, score=8.0"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "skip"

    def test_equals_op_with_whitespace_trimming(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Precedence", "op": "equals", "value": "bulk"},
                action="low_priority_queue",
            )
        ]
        envelope = _email_envelope(headers={"Precedence": " bulk "})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "low_priority_queue"


# ---------------------------------------------------------------------------
# mime_type rule type
# ---------------------------------------------------------------------------


class TestMimeTypeRules:
    def test_exact_mime_match(self) -> None:
        rules = [
            _rule(
                rule_type="mime_type",
                condition={"type": "text/calendar"},
                action="route_to:relationship",
            )
        ]
        envelope = _email_envelope(mime_parts=["text/plain", "text/calendar"])
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "relationship"

    def test_wildcard_subtype_matches(self) -> None:
        rules = [
            _rule(
                rule_type="mime_type",
                condition={"type": "image/*"},
                action="metadata_only",
            )
        ]
        envelope = _email_envelope(mime_parts=["text/plain", "image/jpeg"])
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "metadata_only"

    def test_wildcard_no_match_different_main_type(self) -> None:
        rules = [
            _rule(
                rule_type="mime_type",
                condition={"type": "image/*"},
                action="metadata_only",
            )
        ]
        envelope = _email_envelope(mime_parts=["text/plain", "application/pdf"])
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"

    def test_mime_no_match_empty_parts(self) -> None:
        rules = [
            _rule(
                rule_type="mime_type",
                condition={"type": "text/calendar"},
                action="route_to:relationship",
            )
        ]
        envelope = _email_envelope(mime_parts=[])
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"

    def test_mime_case_insensitive(self) -> None:
        rules = [
            _rule(
                rule_type="mime_type",
                condition={"type": "text/calendar"},
                action="route_to:relationship",
            )
        ]
        envelope = _email_envelope(mime_parts=["TEXT/CALENDAR"])
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"


# ---------------------------------------------------------------------------
# Evaluation order: priority ASC, first match wins
# ---------------------------------------------------------------------------


class TestEvaluationOrder:
    def test_lower_priority_wins(self) -> None:
        """Rule with priority=5 must be evaluated before priority=10."""
        rules = [
            _rule(
                id="id-a",
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="skip",
                priority=5,
            ),
            _rule(
                id="id-b",
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "suffix"},
                action="route_to:finance",
                priority=10,
            ),
        ]
        # Pre-sorted by priority (evaluator expects caller to sort)
        envelope = _email_envelope(sender="alerts@chase.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "skip"
        assert decision.matched_rule_id == "id-a"

    def test_first_matching_rule_wins(self) -> None:
        """Given two rules that both match, first in list wins."""
        rules = [
            _rule(
                id="id-first",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="metadata_only",
                priority=1,
            ),
            _rule(
                id="id-second",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        envelope = _email_envelope(sender="user@example.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.matched_rule_id == "id-first"
        assert decision.decision == "metadata_only"

    def test_non_matching_rule_skipped(self) -> None:
        """Rule that does not match is skipped; next rule is evaluated."""
        rules = [
            _rule(
                id="id-a",
                rule_type="sender_address",
                condition={"address": "specific@example.com"},
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-b",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        envelope = _email_envelope(sender="other@example.com")
        decision = evaluate_triage(envelope, rules)
        # id-a doesn't match, id-b does
        assert decision.matched_rule_id == "id-b"
        assert decision.decision == "route_to"
        assert decision.target_butler == "general"


# ---------------------------------------------------------------------------
# All action types
# ---------------------------------------------------------------------------


class TestActionTypes:
    def test_action_route_to_parses_target(self) -> None:
        rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "paypal.com", "match": "suffix"},
                action="route_to:finance",
            )
        ]
        envelope = _email_envelope(sender="service@paypal.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"
        assert decision.bypasses_llm is True

    def test_action_skip(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Auto-Submitted", "op": "equals", "value": "auto-generated"},
                action="skip",
            )
        ]
        envelope = _email_envelope(headers={"Auto-Submitted": "auto-generated"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "skip"
        assert decision.bypasses_llm is True

    def test_action_metadata_only(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "List-Unsubscribe", "op": "present"},
                action="metadata_only",
            )
        ]
        envelope = _email_envelope(headers={"List-Unsubscribe": "yes"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "metadata_only"
        assert decision.bypasses_llm is True

    def test_action_low_priority_queue(self) -> None:
        rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Precedence", "op": "equals", "value": "bulk"},
                action="low_priority_queue",
            )
        ]
        envelope = _email_envelope(headers={"Precedence": "bulk"})
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "low_priority_queue"
        assert decision.bypasses_llm is True

    def test_action_explicit_pass_through(self) -> None:
        """An explicit pass_through action rule is a valid 'exception override'."""
        rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
            )
        ]
        envelope = _email_envelope(sender="vip@example.com")
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"
        assert decision.bypasses_llm is False


# ---------------------------------------------------------------------------
# Unknown/bad rule type — skip with logging
# ---------------------------------------------------------------------------


class TestBadRuleHandling:
    def test_unknown_rule_type_skipped(self) -> None:
        """Unknown rule_type is skipped; evaluation continues to next rule."""
        rules = [
            _rule(
                id="id-bad",
                rule_type="totally_unknown",
                condition={"foo": "bar"},
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-good",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        envelope = _email_envelope(sender="user@example.com")
        decision = evaluate_triage(envelope, rules)
        # Unknown rule skipped, good rule evaluated
        assert decision.matched_rule_id == "id-good"
        assert decision.decision == "route_to"


# ---------------------------------------------------------------------------
# make_triage_envelope_from_ingest adapter
# ---------------------------------------------------------------------------


class TestMakeTriageEnvelopeFromIngest:
    def _base_email(self, **overrides) -> dict:
        base = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "email",
                "provider": "gmail",
                "endpoint_identity": "user@gmail.com",
            },
            "event": {
                "external_event_id": "<msg1@example.com>",
                "observed_at": "2026-01-01T00:00:00Z",
            },
            "sender": {"identity": "alerts@chase.com"},
            "payload": {
                "raw": {"subject": "Your Statement", "body": "Balance: $100"},
                "normalized_text": "Your Statement\nBalance: $100",
            },
            "control": {"ingestion_tier": "full"},
        }
        base.update(overrides)
        return base

    def test_sender_address_extracted(self) -> None:
        env = self._base_email()
        triage_env = make_triage_envelope_from_ingest(env)
        assert triage_env.sender_address == "alerts@chase.com"

    def test_sender_address_lowercased(self) -> None:
        env = self._base_email()
        env["sender"]["identity"] = "ALERTS@CHASE.COM"
        triage_env = make_triage_envelope_from_ingest(env)
        assert triage_env.sender_address == "alerts@chase.com"

    def test_source_channel_extracted(self) -> None:
        env = self._base_email()
        triage_env = make_triage_envelope_from_ingest(env)
        assert triage_env.source_channel == "email"

    def test_headers_extracted_from_raw(self) -> None:
        env = self._base_email()
        env["payload"]["raw"]["headers"] = {
            "List-Unsubscribe": "<mailto:unsub@example.com>",
            "Precedence": "bulk",
        }
        triage_env = make_triage_envelope_from_ingest(env)
        assert "List-Unsubscribe" in triage_env.headers
        assert triage_env.headers["Precedence"] == "bulk"

    def test_mime_parts_extracted_from_attachments(self) -> None:
        env = self._base_email()
        env["payload"]["attachments"] = [
            {"media_type": "text/calendar", "storage_ref": "s3://x", "size_bytes": 1024}
        ]
        triage_env = make_triage_envelope_from_ingest(env)
        assert "text/calendar" in triage_env.mime_parts

    def test_mime_parts_from_raw_mime_parts(self) -> None:
        env = self._base_email()
        env["payload"]["raw"]["mime_parts"] = [
            {"type": "text/html"},
            {"type": "image/png"},
        ]
        triage_env = make_triage_envelope_from_ingest(env)
        assert "text/html" in triage_env.mime_parts
        assert "image/png" in triage_env.mime_parts

    def test_thread_id_extracted(self) -> None:
        env = self._base_email()
        env["event"]["external_thread_id"] = "thread-abc-123"
        triage_env = make_triage_envelope_from_ingest(env)
        assert triage_env.thread_id == "thread-abc-123"

    def test_missing_fields_graceful(self) -> None:
        """Minimal envelope with missing optional fields does not raise."""
        env = {
            "schema_version": "ingest.v1",
            "source": {"channel": "telegram"},
            "sender": {},
            "payload": {},
        }
        triage_env = make_triage_envelope_from_ingest(env)
        assert triage_env.sender_address == ""
        assert triage_env.headers == {}
        assert triage_env.mime_parts == []
        assert triage_env.thread_id is None
