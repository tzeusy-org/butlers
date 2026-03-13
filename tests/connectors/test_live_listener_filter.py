"""Tests for live-listener source filter gate and mic_id key extraction.

Covers tasks 7.1–7.4 from the connector-live-listener openspec:
- SourceFilterEvaluator instantiation per mic with connector_type="live-listener"
- Filter scope: "connector:live-listener:mic:{device_name}"
- mic_id key extraction from device name
- Filter gate positioned after transcription, before discretion
- evaluate_voice_filter passes allowed/blocked decisions correctly
- mic_id matcher in IngestionPolicyEvaluator (unit test of the matcher)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from butlers.connectors.live_listener.envelope import endpoint_identity
from butlers.connectors.live_listener.filter_gate import (
    build_filter_scope,
    create_filter_evaluator,
    evaluate_voice_filter,
    extract_mic_key,
)
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator, PolicyDecision

pytestmark = pytest.mark.unit

_DEVICE = "kitchen"
_BEDROOM = "bedroom"


def _make_evaluator_with_rules(rules: list) -> IngestionPolicyEvaluator:
    """Create an IngestionPolicyEvaluator pre-loaded with given rules.

    Sets _last_loaded_at to now so the TTL refresh is not triggered
    during evaluate() (avoids asyncio.create_task in sync tests).
    """
    evaluator = IngestionPolicyEvaluator(scope="connector:test", db_pool=None)
    evaluator._rules = rules
    evaluator._last_loaded_at = time.monotonic()  # mark as freshly loaded
    return evaluator


# ---------------------------------------------------------------------------
# build_filter_scope
# ---------------------------------------------------------------------------


def test_build_filter_scope_format() -> None:
    """Filter scope should be 'connector:live-listener:mic:{device_name}'."""
    scope = build_filter_scope(_DEVICE)
    assert scope == f"connector:live-listener:mic:{_DEVICE}"


def test_build_filter_scope_uses_endpoint_identity() -> None:
    """Scope must be derived from the canonical endpoint_identity."""
    ep_id = endpoint_identity(_DEVICE)
    expected = f"connector:{ep_id}"
    assert build_filter_scope(_DEVICE) == expected


def test_build_filter_scope_different_mics_different_scopes() -> None:
    """Different mics produce different scopes."""
    assert build_filter_scope("kitchen") != build_filter_scope("bedroom")


# ---------------------------------------------------------------------------
# extract_mic_key
# ---------------------------------------------------------------------------


def test_extract_mic_key_returns_device_name() -> None:
    """mic_id key extraction should return the device name lowercased."""
    assert extract_mic_key("kitchen") == "kitchen"


def test_extract_mic_key_preserves_hyphens_and_digits() -> None:
    """Device names with hyphens and digits should be preserved."""
    assert extract_mic_key("living-room-2") == "living-room-2"


def test_extract_mic_key_normalizes_to_lowercase() -> None:
    """Per spec: mic_id key value is always lowercase (case-normalised)."""
    assert extract_mic_key("Kitchen") == "kitchen"
    assert extract_mic_key("BEDROOM") == "bedroom"
    assert extract_mic_key("Living-Room-2") == "living-room-2"


# ---------------------------------------------------------------------------
# create_filter_evaluator
# ---------------------------------------------------------------------------


def test_create_filter_evaluator_returns_evaluator() -> None:
    """create_filter_evaluator should return an IngestionPolicyEvaluator."""
    mock_pool = MagicMock()
    evaluator = create_filter_evaluator(_DEVICE, mock_pool)
    assert isinstance(evaluator, IngestionPolicyEvaluator)


def test_create_filter_evaluator_scope_is_correct() -> None:
    """The evaluator scope must match the mic's filter scope."""
    mock_pool = MagicMock()
    evaluator = create_filter_evaluator(_DEVICE, mock_pool)
    expected_scope = build_filter_scope(_DEVICE)
    assert evaluator.scope == expected_scope


def test_create_filter_evaluator_none_pool_accepted() -> None:
    """None pool should be accepted (fail-open: no rules loaded)."""
    evaluator = create_filter_evaluator(_DEVICE, db_pool=None)
    assert isinstance(evaluator, IngestionPolicyEvaluator)


def test_create_filter_evaluator_custom_refresh_interval() -> None:
    """Custom refresh_interval_s should be forwarded to the evaluator."""
    mock_pool = MagicMock()
    evaluator = create_filter_evaluator(_DEVICE, mock_pool, refresh_interval_s=30.0)
    assert evaluator._refresh_interval_s == 30.0


def test_create_filter_evaluator_distinct_per_mic() -> None:
    """Each mic pipeline gets a separate evaluator with its own scope."""
    mock_pool = MagicMock()
    ev_kitchen = create_filter_evaluator("kitchen", mock_pool)
    ev_bedroom = create_filter_evaluator("bedroom", mock_pool)
    assert ev_kitchen.scope != ev_bedroom.scope


# ---------------------------------------------------------------------------
# evaluate_voice_filter — pass-through (no rules)
# ---------------------------------------------------------------------------


def test_evaluate_voice_filter_no_rules_passes() -> None:
    """With no rules loaded, evaluate_voice_filter should allow the utterance."""
    evaluator = create_filter_evaluator(_DEVICE, db_pool=None)
    # No ensure_loaded called — evaluator has empty rule set

    decision = evaluate_voice_filter(evaluator, _DEVICE)
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# evaluate_voice_filter — with mocked evaluator
# ---------------------------------------------------------------------------


def test_evaluate_voice_filter_allowed_decision() -> None:
    """evaluate_voice_filter should return the evaluator's decision when allowed."""
    allowed_decision = PolicyDecision(action="pass_through", reason="no rule matched")

    mock_evaluator = MagicMock(spec=IngestionPolicyEvaluator)
    mock_evaluator.evaluate.return_value = allowed_decision

    decision = evaluate_voice_filter(mock_evaluator, _DEVICE)

    assert decision.allowed is True
    mock_evaluator.evaluate.assert_called_once()


def test_evaluate_voice_filter_blocked_decision() -> None:
    """evaluate_voice_filter should propagate block decisions from the evaluator."""
    blocked_decision = PolicyDecision(
        action="block",
        reason="mic_id match -> block",
        matched_rule_type="mic_id",
    )

    mock_evaluator = MagicMock(spec=IngestionPolicyEvaluator)
    mock_evaluator.evaluate.return_value = blocked_decision

    decision = evaluate_voice_filter(mock_evaluator, _DEVICE)

    assert decision.allowed is False


def test_evaluate_voice_filter_builds_envelope_with_correct_channel() -> None:
    """The IngestionEnvelope passed to evaluator.evaluate must have source_channel='voice'."""
    captured_envelopes: list[IngestionEnvelope] = []

    def capture_evaluate(env: IngestionEnvelope) -> PolicyDecision:
        captured_envelopes.append(env)
        return PolicyDecision(action="pass_through", reason="")

    mock_evaluator = MagicMock(spec=IngestionPolicyEvaluator)
    mock_evaluator.evaluate.side_effect = capture_evaluate

    evaluate_voice_filter(mock_evaluator, _DEVICE)

    assert len(captured_envelopes) == 1
    assert captured_envelopes[0].source_channel == "voice"


def test_evaluate_voice_filter_builds_envelope_with_device_as_raw_key() -> None:
    """The IngestionEnvelope's raw_key must be the device name (mic_id)."""
    captured_envelopes: list[IngestionEnvelope] = []

    def capture_evaluate(env: IngestionEnvelope) -> PolicyDecision:
        captured_envelopes.append(env)
        return PolicyDecision(action="pass_through", reason="")

    mock_evaluator = MagicMock(spec=IngestionPolicyEvaluator)
    mock_evaluator.evaluate.side_effect = capture_evaluate

    evaluate_voice_filter(mock_evaluator, _DEVICE)

    assert captured_envelopes[0].raw_key == _DEVICE


# ---------------------------------------------------------------------------
# mic_id matcher in IngestionPolicyEvaluator (_match_mic_id unit tests)
# ---------------------------------------------------------------------------


def test_match_mic_id_exact_match() -> None:
    """A mic_id rule with exact device name should match the envelope."""
    evaluator = _make_evaluator_with_rules(
        [
            {
                "id": "rule-1",
                "rule_type": "mic_id",
                "condition": {"mic_id": "kitchen"},
                "action": "block",
                "priority": 0,
                "name": "block-kitchen",
            }
        ]
    )

    envelope = IngestionEnvelope(source_channel="voice", raw_key="kitchen")
    decision = evaluator.evaluate(envelope)
    assert decision.action == "block"
    assert decision.matched_rule_type == "mic_id"


def test_match_mic_id_no_match() -> None:
    """A mic_id rule should not match a different device name."""
    evaluator = _make_evaluator_with_rules(
        [
            {
                "id": "rule-1",
                "rule_type": "mic_id",
                "condition": {"mic_id": "kitchen"},
                "action": "block",
                "priority": 0,
                "name": "block-kitchen",
            }
        ]
    )

    envelope = IngestionEnvelope(source_channel="voice", raw_key="bedroom")
    decision = evaluator.evaluate(envelope)
    assert decision.action == "pass_through"


def test_match_mic_id_wildcard_matches_any_device() -> None:
    """The '*' wildcard should match any mic_id value."""
    evaluator = _make_evaluator_with_rules(
        [
            {
                "id": "rule-1",
                "rule_type": "mic_id",
                "condition": {"mic_id": "*"},
                "action": "block",
                "priority": 0,
                "name": "block-all",
            }
        ]
    )

    for device in ("kitchen", "bedroom", "office"):
        envelope = IngestionEnvelope(source_channel="voice", raw_key=device)
        decision = evaluator.evaluate(envelope)
        assert decision.action == "block", f"Expected block for device={device}"


def test_match_mic_id_empty_condition_no_match() -> None:
    """An empty mic_id condition (no 'mic_id' key) should not match anything."""
    evaluator = _make_evaluator_with_rules(
        [
            {
                "id": "rule-1",
                "rule_type": "mic_id",
                "condition": {},
                "action": "block",
                "priority": 0,
                "name": "empty",
            }
        ]
    )

    envelope = IngestionEnvelope(source_channel="voice", raw_key="kitchen")
    decision = evaluator.evaluate(envelope)
    assert decision.action == "pass_through"


def test_match_mic_id_pass_through_action() -> None:
    """A mic_id rule with pass_through action should allow the utterance."""
    evaluator = _make_evaluator_with_rules(
        [
            {
                "id": "rule-1",
                "rule_type": "mic_id",
                "condition": {"mic_id": "kitchen"},
                "action": "pass_through",
                "priority": 0,
                "name": "allow-kitchen",
            }
        ]
    )

    envelope = IngestionEnvelope(source_channel="voice", raw_key="kitchen")
    decision = evaluator.evaluate(envelope)
    assert decision.action == "pass_through"
    assert decision.allowed is True


def test_match_mic_id_case_insensitive() -> None:
    """mic_id matching is case-insensitive: raw_key and condition value are both lowercased."""
    # Rule stores lowercase "kitchen" (as configured via UI/API)
    evaluator = _make_evaluator_with_rules(
        [
            {
                "id": "rule-1",
                "rule_type": "mic_id",
                "condition": {"mic_id": "kitchen"},
                "action": "block",
                "priority": 0,
                "name": "block-kitchen",
            }
        ]
    )

    # raw_key comes from extract_mic_key which lowercases, so "Kitchen" → "kitchen"
    envelope = IngestionEnvelope(source_channel="voice", raw_key="kitchen")
    decision = evaluator.evaluate(envelope)
    assert decision.action == "block", "Lowercase raw_key should match lowercase rule condition"

    # Simulate a misconfigured rule condition with mixed case — still matches
    evaluator_mixed = _make_evaluator_with_rules(
        [
            {
                "id": "rule-2",
                "rule_type": "mic_id",
                "condition": {"mic_id": "Kitchen"},
                "action": "block",
                "priority": 0,
                "name": "block-kitchen-mixed",
            }
        ]
    )
    decision_mixed = evaluator_mixed.evaluate(envelope)
    assert decision_mixed.action == "block", "Mixed-case rule condition should still match"


# ---------------------------------------------------------------------------
# Filter gate positioning: after transcription, before discretion
# ---------------------------------------------------------------------------


def test_filter_gate_pipeline_position_concept() -> None:
    """Demonstrate that filter gate is invoked after transcription, before discretion.

    This test documents the expected pipeline order by asserting that:
    - evaluate_voice_filter is a synchronous function (no await needed)
    - It is invoked with only the device_name (no LLM involvement)
    - A blocked decision short-circuits before any LLM call

    This is a contract test rather than an integration test. The actual
    pipeline integration is verified in end-to-end tests (task 8.6).
    """
    blocked_decision = PolicyDecision(
        action="block",
        reason="mic_id match -> block",
        matched_rule_type="mic_id",
    )

    mock_evaluator = MagicMock(spec=IngestionPolicyEvaluator)
    mock_evaluator.evaluate.return_value = blocked_decision
    discretion_called = False

    # --- pipeline simulation ---
    # Step 1: transcription (already done)
    transcript = "Some ambient chatter"
    # Step 2: filter gate
    filter_decision = evaluate_voice_filter(mock_evaluator, _DEVICE)
    # Step 3: discretion — should NOT be reached if blocked
    if filter_decision.allowed:
        discretion_called = True  # pragma: no cover

    assert not discretion_called, "Discretion layer must NOT be called when filter blocks"
    mock_evaluator.evaluate.assert_called_once()
    assert transcript  # transcript was available before filter gate
