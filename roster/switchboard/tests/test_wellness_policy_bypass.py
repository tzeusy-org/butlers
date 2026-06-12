"""Integration test: wellness envelope triggers policy-bypass path (no LLM spawn).

Verifies that a ``source_channel='wellness'`` envelope processed through
``_run_policy_evaluation`` produces a ``route_to:health`` decision, which
causes the pipeline to set ``triage_decision='route_to'`` and
``triage_target='health'`` in ``request_context`` — activating the
policy-bypass path and skipping the Switchboard-side LLM spawn.

This test mirrors the seeded rule from migration ``007_wellness_route_rule.py``
and confirms:
  1. The source_channel='wellness' rule produces action='route_to', target='health'.
  2. The decision is correctly embedded in request_context by _build_request_context.
  3. A non-wellness source_channel still falls through to pass_through.

Follows the pattern from test_triage_ingest_integration.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.ingestion_policy import (
    IngestionPolicyEvaluator,
    PolicyDecision,
)
from butlers.tools.switchboard.ingestion.ingest import (
    _build_request_context,
    _make_ingestion_envelope,
    _run_policy_evaluation,
)
from butlers.tools.switchboard.routing.contracts import (
    IngestControlV1,
    IngestEnvelopeV1,
    IngestEventV1,
    IngestPayloadV1,
    IngestSenderV1,
    IngestSourceV1,
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
    evaluator._last_loaded_at = time.monotonic()
    return evaluator


def _wellness_rule() -> dict:
    """The rule seeded by migration 007_wellness_route_rule.py."""
    return {
        "id": "00000000-0000-0000-0001-000000000080",
        "rule_type": "source_channel",
        "condition": {"source_channel": "wellness"},
        "action": "route_to:health",
        "priority": 10,
    }


def _wellness_payload() -> dict:
    """Minimal ingest.v1 payload with source_channel='wellness'."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "google_health",
            "endpoint_identity": "google_health:owner",
        },
        "event": {
            "external_event_id": "gh-steps-2026-04-25",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "owner@example.com"},
        "payload": {
            "raw": {"predicate": "steps", "value": 8432},
            "normalized_text": "steps: 8432",
        },
        "control": {"ingestion_tier": "full"},
    }


def _make_wellness_envelope_v1() -> IngestEnvelopeV1:
    """Build an IngestEnvelopeV1 for a wellness event."""
    return IngestEnvelopeV1(
        schema_version="ingest.v1",
        source=IngestSourceV1(
            channel="wellness",
            provider="google_health",
            endpoint_identity="google_health:owner",
        ),
        event=IngestEventV1(
            external_event_id="gh-steps-2026-04-25",
            observed_at=datetime.now(UTC).isoformat(),
        ),
        sender=IngestSenderV1(identity="owner@example.com"),
        payload=IngestPayloadV1(
            raw={"predicate": "steps", "value": 8432},
            normalized_text="steps: 8432",
        ),
        control=IngestControlV1(ingestion_tier="full"),
    )


def _ha_wellness_payload() -> dict:
    """Minimal ingest.v1 payload with source_channel='wellness', provider='home_assistant'.

    Mirrors a Withings-shaped blood-pressure reading promoted onto the wellness
    channel by the Home Assistant connector (RFC 0003 Amendment 1).
    """
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "home_assistant",
            "endpoint_identity": "home_assistant:owner",
        },
        "event": {
            "external_event_id": "ha-sensor.withings_systolic-2026-06-12",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "sensor.withings_systolic"},
        "payload": {
            "raw": {
                "wellness_measurement": {
                    "metric": "blood_pressure_systolic",
                    "value": 118,
                    "unit": "mmHg",
                    "valid_at": datetime.now(UTC).isoformat(),
                    "source_entity_id": "sensor.withings_systolic",
                }
            },
            "normalized_text": "blood_pressure_systolic: 118 mmHg",
        },
        "control": {"ingestion_tier": "full"},
    }


def _make_ha_wellness_envelope_v1() -> IngestEnvelopeV1:
    """Build an IngestEnvelopeV1 for a Home Assistant wellness event."""
    return IngestEnvelopeV1(
        schema_version="ingest.v1",
        source=IngestSourceV1(
            channel="wellness",
            provider="home_assistant",
            endpoint_identity="home_assistant:owner",
        ),
        event=IngestEventV1(
            external_event_id="ha-sensor.withings_systolic-2026-06-12",
            observed_at=datetime.now(UTC).isoformat(),
        ),
        sender=IngestSenderV1(identity="sensor.withings_systolic"),
        payload=IngestPayloadV1(
            raw={
                "wellness_measurement": {
                    "metric": "blood_pressure_systolic",
                    "value": 118,
                    "unit": "mmHg",
                    "valid_at": datetime.now(UTC).isoformat(),
                    "source_entity_id": "sensor.withings_systolic",
                }
            },
            normalized_text="blood_pressure_systolic: 118 mmHg",
        ),
        control=IngestControlV1(ingestion_tier="full"),
    )


# ---------------------------------------------------------------------------
# Tests: policy evaluation
# ---------------------------------------------------------------------------


class TestWellnessPolicyBypass:
    def test_wellness_source_channel_matches_rule(self) -> None:
        """A wellness envelope matches the seeded rule and produces route_to:health."""
        payload = _wellness_payload()
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.action == "route_to"
        assert decision.target_butler == "health"

    def test_wellness_rule_type_is_source_channel(self) -> None:
        """Matched rule type is 'source_channel' (not sender_domain etc.)."""
        payload = _wellness_payload()
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.matched_rule_type == "source_channel"

    def test_wellness_decision_bypasses_llm(self) -> None:
        """PolicyDecision.bypasses_llm is True for route_to:health."""
        payload = _wellness_payload()
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.bypasses_llm is True

    def test_non_wellness_channel_falls_through(self) -> None:
        """A non-wellness source_channel does not match the wellness rule."""
        payload = _wellness_payload()
        payload["source"]["channel"] = "email"
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="email")
        assert decision.action == "pass_through"

    def test_no_rules_loaded_returns_pass_through(self) -> None:
        """Empty rule set falls through to pass_through regardless of source_channel."""
        payload = _wellness_payload()
        evaluator = _make_evaluator_with_rules([])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.action == "pass_through"


# ---------------------------------------------------------------------------
# Tests: request_context embedding
# ---------------------------------------------------------------------------


class TestWellnessPolicyContextEmbedding:
    def test_triage_decision_and_target_in_context(self) -> None:
        """route_to:health decision is embedded as triage_decision/triage_target."""
        envelope = _make_wellness_envelope_v1()
        triage_decision = PolicyDecision(
            action="route_to",
            target_butler="health",
            matched_rule_id="00000000-0000-0000-0001-000000000080",
            matched_rule_type="source_channel",
            reason="source_channel match -> route_to:health",
        )
        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
            triage_decision=triage_decision,
        )
        assert context["triage_decision"] == "route_to"
        assert context["triage_target"] == "health"

    def test_triage_rule_type_in_context(self) -> None:
        """triage_rule_type='source_channel' is embedded in request_context."""
        envelope = _make_wellness_envelope_v1()
        triage_decision = PolicyDecision(
            action="route_to",
            target_butler="health",
            matched_rule_id="00000000-0000-0000-0001-000000000080",
            matched_rule_type="source_channel",
            reason="source_channel match -> route_to:health",
        )
        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
            triage_decision=triage_decision,
        )
        assert context["triage_rule_type"] == "source_channel"
        assert context["triage_rule_id"] == "00000000-0000-0000-0001-000000000080"

    def test_source_channel_in_context(self) -> None:
        """source_channel='wellness' is present in request_context."""
        envelope = _make_wellness_envelope_v1()
        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
        )
        assert context["source_channel"] == "wellness"

    def test_make_ingestion_envelope_source_channel(self) -> None:
        """_make_ingestion_envelope extracts source_channel='wellness' correctly."""
        payload = _wellness_payload()
        env = _make_ingestion_envelope(payload)
        assert env.source_channel == "wellness"


# ---------------------------------------------------------------------------
# Tests: Home Assistant wellness provider (RFC 0003 Amendment 1)
# ---------------------------------------------------------------------------


class TestHomeAssistantWellnessPolicyBypass:
    """A wellness/home_assistant envelope rides the same sw_007 policy-bypass route.

    The route is keyed solely on source_channel='wellness', so the new
    home_assistant provider traverses the route_to:health bypass identically to
    google_health, with no Switchboard-side LLM session spawned.
    """

    def test_ha_wellness_envelope_validates(self) -> None:
        """The HA wellness envelope passes channel/provider contract validation."""
        envelope = _make_ha_wellness_envelope_v1()
        assert envelope.source.channel == "wellness"
        assert envelope.source.provider == "home_assistant"

    def test_ha_wellness_source_channel_matches_rule(self) -> None:
        """The HA wellness envelope matches the seeded rule -> route_to:health."""
        payload = _ha_wellness_payload()
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.action == "route_to"
        assert decision.target_butler == "health"

    def test_ha_wellness_rule_type_is_source_channel(self) -> None:
        """Matched rule type is 'source_channel' for the HA wellness envelope."""
        payload = _ha_wellness_payload()
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.matched_rule_type == "source_channel"

    def test_ha_wellness_decision_bypasses_llm(self) -> None:
        """PolicyDecision.bypasses_llm is True -> no Switchboard LLM spawn."""
        payload = _ha_wellness_payload()
        evaluator = _make_evaluator_with_rules([_wellness_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="wellness")
        assert decision.bypasses_llm is True


class TestHomeAssistantWellnessContextEmbedding:
    def test_ha_triage_decision_and_target_in_context(self) -> None:
        """route_to:health decision embeds as triage_decision/triage_target for HA."""
        envelope = _make_ha_wellness_envelope_v1()
        triage_decision = PolicyDecision(
            action="route_to",
            target_butler="health",
            matched_rule_id="00000000-0000-0000-0001-000000000080",
            matched_rule_type="source_channel",
            reason="source_channel match -> route_to:health",
        )
        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
            triage_decision=triage_decision,
        )
        assert context["triage_decision"] == "route_to"
        assert context["triage_target"] == "health"

    def test_ha_source_channel_in_context(self) -> None:
        """source_channel='wellness' is present in request_context for HA envelopes."""
        envelope = _make_ha_wellness_envelope_v1()
        context = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime.now(UTC),
        )
        assert context["source_channel"] == "wellness"

    def test_make_ingestion_envelope_ha_source_channel(self) -> None:
        """_make_ingestion_envelope extracts source_channel='wellness' for HA payloads."""
        payload = _ha_wellness_payload()
        env = _make_ingestion_envelope(payload)
        assert env.source_channel == "wellness"
