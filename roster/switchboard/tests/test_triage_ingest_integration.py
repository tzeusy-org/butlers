"""Unit tests for ingestion policy evaluation in the ingest pipeline.

Tests that ingest_v1 correctly applies IngestionPolicyEvaluator decisions and
embeds them in the request_context and response. Uses mocked DB pool (no Docker
required).

These are unit-level tests focused on policy evaluation integration semantics.
Integration-with-DB tests live in test_ingest_tier.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from butlers.ingestion_policy import (
    IngestionPolicyEvaluator,
    PolicyDecision,
)
from butlers.tools.switchboard.ingestion.ingest import (
    IngestAcceptedResponse,
    _make_ingestion_envelope,
    _run_policy_evaluation,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_evaluator_with_rules(rules: list[dict]) -> IngestionPolicyEvaluator:
    """Create an IngestionPolicyEvaluator with pre-loaded rules (no DB)."""
    import time

    evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
    evaluator._rules = rules
    evaluator._last_loaded_at = time.monotonic()  # Mark as freshly loaded
    return evaluator


def _valid_rule(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    rule_type: str = "sender_domain",
    condition: dict | None = None,
    action: str = "route_to:finance",
    priority: int = 10,
) -> dict:
    return {
        "id": id,
        "rule_type": rule_type,
        "condition": condition or {"domain": "chase.com", "match": "suffix"},
        "action": action,
        "priority": priority,
    }


def _base_email_payload(
    *,
    sender: str = "user@example.com",
    message_id: str = "<msg001@example.com>",
    mailbox: str = "gmail:user:alice@gmail.com",
) -> dict:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": mailbox,
        },
        "event": {
            "external_event_id": message_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": sender},
        "payload": {
            "raw": {"subject": "Test Subject", "body": "Test body"},
            "normalized_text": "Test Subject\nTest body",
        },
        "control": {"ingestion_tier": "full"},
    }


# ---------------------------------------------------------------------------
# _run_policy_evaluation unit tests
# ---------------------------------------------------------------------------


class TestRunPolicyEvaluation:
    def test_matched_rule_returns_decision(self) -> None:
        payload = _base_email_payload(sender="alerts@chase.com")
        evaluator = _make_evaluator_with_rules([_valid_rule(action="route_to:finance")])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="email")
        assert decision.action == "route_to"
        assert decision.target_butler == "finance"

    def test_no_match_returns_pass_through(self) -> None:
        payload = _base_email_payload(sender="unknown@random.com")
        evaluator = _make_evaluator_with_rules([_valid_rule(action="route_to:finance")])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="email")
        assert decision.action == "pass_through"

    def test_thread_affinity_returns_route_to(self) -> None:
        """Thread affinity target bypasses rule evaluation."""
        payload = _base_email_payload(sender="unknown@random.com")
        evaluator = _make_evaluator_with_rules([])
        decision = _run_policy_evaluation(
            payload,
            evaluator,
            source_channel="email",
            thread_affinity_target="finance",
        )
        assert decision.action == "route_to"
        assert decision.target_butler == "finance"
        assert decision.matched_rule_type == "thread_affinity"

    def test_thread_affinity_overrides_rules(self) -> None:
        """Thread affinity takes precedence over matching rules."""
        payload = _base_email_payload(sender="alerts@chase.com")
        evaluator = _make_evaluator_with_rules([_valid_rule(action="route_to:finance")])
        decision = _run_policy_evaluation(
            payload,
            evaluator,
            source_channel="email",
            thread_affinity_target="relationship",
        )
        assert decision.action == "route_to"
        assert decision.target_butler == "relationship"
        assert decision.matched_rule_type == "thread_affinity"

    def test_exception_in_evaluator_fails_open(self) -> None:
        """If evaluator.evaluate raises, _run_policy_evaluation returns pass_through."""
        payload = _base_email_payload()
        evaluator = _make_evaluator_with_rules([])

        with patch.object(
            evaluator,
            "evaluate",
            side_effect=RuntimeError("evaluator exploded"),
        ):
            decision = _run_policy_evaluation(payload, evaluator, source_channel="email")

        assert decision.action == "pass_through"

    def test_all_action_types_returned(self) -> None:
        for action, expected_action in [
            ("skip", "skip"),
            ("metadata_only", "metadata_only"),
            ("low_priority_queue", "low_priority_queue"),
            ("pass_through", "pass_through"),
            ("route_to:travel", "route_to"),
        ]:
            payload = _base_email_payload(sender="alerts@chase.com")
            evaluator = _make_evaluator_with_rules([_valid_rule(action=action)])
            decision = _run_policy_evaluation(payload, evaluator, source_channel="email")
            assert decision.action == expected_action, (
                f"Expected {expected_action!r} for action={action!r}, got {decision.action!r}"
            )


# ---------------------------------------------------------------------------
# _make_ingestion_envelope adapter
# ---------------------------------------------------------------------------


class TestMakeIngestionEnvelope:
    def test_sender_address_extracted(self) -> None:
        payload = _base_email_payload(sender="alerts@chase.com")
        env = _make_ingestion_envelope(payload)
        assert env.sender_address == "alerts@chase.com"

    def test_sender_address_lowercased(self) -> None:
        payload = _base_email_payload(sender="ALERTS@CHASE.COM")
        env = _make_ingestion_envelope(payload)
        assert env.sender_address == "alerts@chase.com"

    def test_source_channel_extracted(self) -> None:
        payload = _base_email_payload()
        env = _make_ingestion_envelope(payload)
        assert env.source_channel == "email"

    def test_headers_extracted(self) -> None:
        payload = _base_email_payload()
        payload["payload"]["raw"]["headers"] = {
            "List-Unsubscribe": "<mailto:unsub@example.com>",
        }
        env = _make_ingestion_envelope(payload)
        assert "List-Unsubscribe" in env.headers

    def test_mime_parts_from_attachments(self) -> None:
        payload = _base_email_payload()
        payload["payload"]["attachments"] = [
            {"media_type": "text/calendar", "storage_ref": "s3://x", "size_bytes": 1024}
        ]
        env = _make_ingestion_envelope(payload)
        assert "text/calendar" in env.mime_parts

    def test_raw_key_for_email(self) -> None:
        payload = _base_email_payload(sender="alerts@chase.com")
        env = _make_ingestion_envelope(payload)
        assert env.raw_key == "alerts@chase.com"


# ---------------------------------------------------------------------------
# Triage decision embedded in context
# ---------------------------------------------------------------------------


class TestTriageDecisionEmbedding:
    def test_triage_decision_in_request_context(self) -> None:
        """route_to decision must appear in request_context for downstream pipeline."""
        from datetime import UTC, datetime

        from butlers.tools.switchboard.ingestion.ingest import _build_request_context
        from butlers.tools.switchboard.routing.contracts import (
            IngestControlV1,
            IngestEnvelopeV1,
            IngestEventV1,
            IngestPayloadV1,
            IngestSenderV1,
            IngestSourceV1,
        )

        # Build a minimal envelope
        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="email", provider="gmail", endpoint_identity="box@gmail.com"
            ),
            event=IngestEventV1(
                external_event_id="<evt001@ex.com>",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="alerts@chase.com"),
            payload=IngestPayloadV1(
                raw={"subject": "Hi"},
                normalized_text="Hi",
            ),
            control=IngestControlV1(ingestion_tier="full"),
        )

        triage_decision = PolicyDecision(
            action="route_to",
            target_butler="finance",
            matched_rule_id="rule-uuid-001",
            matched_rule_type="sender_domain",
            reason="sender_domain match -> route_to:finance",
        )

        import uuid

        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
            triage_decision=triage_decision,
        )

        assert context["triage_decision"] == "route_to"
        assert context["triage_target"] == "finance"
        assert context["triage_rule_id"] == "rule-uuid-001"
        assert context["triage_rule_type"] == "sender_domain"

    def test_no_triage_decision_absent_from_context(self) -> None:
        """When triage_decision is None, no triage_* keys in context."""
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.ingestion.ingest import _build_request_context
        from butlers.tools.switchboard.routing.contracts import (
            IngestControlV1,
            IngestEnvelopeV1,
            IngestEventV1,
            IngestPayloadV1,
            IngestSenderV1,
            IngestSourceV1,
        )

        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="email", provider="gmail", endpoint_identity="box@gmail.com"
            ),
            event=IngestEventV1(
                external_event_id="<evt002@ex.com>",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="user@example.com"),
            payload=IngestPayloadV1(
                raw={"subject": "Hi"},
                normalized_text="Hi",
            ),
            control=IngestControlV1(ingestion_tier="full"),
        )

        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
            triage_decision=None,
        )

        assert "triage_decision" not in context
        assert "triage_target" not in context
        assert "triage_rule_id" not in context

    def test_pass_through_decision_embedded(self) -> None:
        """Pass-through triage decision is embedded in context."""
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.ingestion.ingest import _build_request_context
        from butlers.tools.switchboard.routing.contracts import (
            IngestControlV1,
            IngestEnvelopeV1,
            IngestEventV1,
            IngestPayloadV1,
            IngestSenderV1,
            IngestSourceV1,
        )

        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="email", provider="gmail", endpoint_identity="box@gmail.com"
            ),
            event=IngestEventV1(
                external_event_id="<evt003@ex.com>",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="user@example.com"),
            payload=IngestPayloadV1(
                raw={"subject": "Hi"},
                normalized_text="Hi",
            ),
            control=IngestControlV1(ingestion_tier="full"),
        )

        triage_decision = PolicyDecision(
            action="pass_through",
            reason="no rule matched",
        )

        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
            triage_decision=triage_decision,
        )

        assert context["triage_decision"] == "pass_through"
        assert "triage_target" not in context  # No target for pass_through
        assert "triage_rule_id" not in context  # No rule matched


# ---------------------------------------------------------------------------
# IngestAcceptedResponse includes triage fields
# ---------------------------------------------------------------------------


class TestIngestAcceptedResponseTriageFields:
    def test_response_has_triage_decision_field(self) -> None:
        from uuid import uuid4

        resp = IngestAcceptedResponse(
            request_id=uuid4(),
            status="accepted",
            duplicate=False,
            triage_decision="route_to",
            triage_target="finance",
        )
        assert resp.triage_decision == "route_to"
        assert resp.triage_target == "finance"

    def test_response_triage_fields_default_none(self) -> None:
        from uuid import uuid4

        resp = IngestAcceptedResponse(
            request_id=uuid4(),
            status="accepted",
            duplicate=True,
        )
        assert resp.triage_decision is None
        assert resp.triage_target is None

    def test_response_is_frozen(self) -> None:
        from uuid import uuid4

        resp = IngestAcceptedResponse(
            request_id=uuid4(),
            status="accepted",
            duplicate=False,
        )
        with pytest.raises(Exception):
            resp.triage_decision = "something"  # type: ignore[misc]
