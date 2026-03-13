"""Source filter gate for the live-listener voice connector.

Implements task 7.1–7.3 from the connector-live-listener openspec:

- ``SourceFilterEvaluator`` instantiation per mic pipeline with
  ``connector_type="live-listener"``
- Filter gate wired into the pipeline **after** transcription and
  **before** the discretion layer (saves LLM calls on blocked utterances)
- ``mic_id`` key extraction from device name config

The filter gate uses :class:`~butlers.ingestion_policy.IngestionPolicyEvaluator`
with scope ``"connector:live-listener:{endpoint_identity}"``.

Pipeline position (per spec Section 7, filter-gate requirement):
  transcription → [filter_gate] → discretion → envelope → submission

Blocked utterances are silently dropped at the connector; the Switchboard
is never called, saving both LLM discretion cost and network round-trips.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from butlers.connectors.live_listener.envelope import endpoint_identity
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator, PolicyDecision

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_CONNECTOR_TYPE = "live-listener"

# GAP-3: track rule IDs that have already received a one-time WARNING for
# having a source_key_type other than "mic_id".  This set persists for the
# lifetime of the process (per-evaluator, per rule ID) so the warning fires
# exactly once, regardless of how many times the rules are refreshed.
_warned_non_mic_id_rule_ids: set[str] = set()


def build_filter_scope(device_name: str) -> str:
    """Return the ingestion policy scope string for a mic pipeline.

    Format: ``"connector:live-listener:{endpoint_identity}"``

    Args:
        device_name: The device name from LIVE_LISTENER_DEVICES config.
    """
    ep_id = endpoint_identity(device_name)
    return f"connector:{ep_id}"


def create_filter_evaluator(
    device_name: str,
    db_pool: asyncpg.Pool | None,
    refresh_interval_s: float = 60.0,
) -> IngestionPolicyEvaluator:
    """Create a pre-wired :class:`~butlers.ingestion_policy.IngestionPolicyEvaluator`
    for a mic pipeline.

    The evaluator uses scope ``"connector:live-listener:{endpoint_identity}"``
    so that per-mic ingestion rules loaded from the DB apply correctly.

    Args:
        device_name: The device name from LIVE_LISTENER_DEVICES config.
        db_pool: asyncpg pool for rule loading.  May be ``None`` — if so,
            the evaluator runs with an empty rule set (fail-open: pass all).
        refresh_interval_s: TTL for the in-memory rule cache. Default 60 s.

    Returns:
        A configured :class:`~butlers.ingestion_policy.IngestionPolicyEvaluator`
        instance ready for :meth:`~butlers.ingestion_policy.IngestionPolicyEvaluator.ensure_loaded`
        and subsequent ``evaluate()`` calls.
    """
    scope = build_filter_scope(device_name)
    return IngestionPolicyEvaluator(
        scope=scope,
        db_pool=db_pool,
        refresh_interval_s=refresh_interval_s,
    )


def warn_non_mic_id_rules(evaluator: IngestionPolicyEvaluator) -> None:
    """Emit a one-time WARNING for each loaded rule whose ``rule_type`` is not ``mic_id``.

    The live-listener connector only understands ``source_key_type="mic_id"`` rules.
    Any other rule type loaded into the evaluator's scope will never match a voice
    utterance and should be flagged so operators can correct mis-configured rules.

    Per spec: "filters with any other source_key_type are skipped with a one-time
    WARNING log per filter ID."

    Call this after :meth:`~butlers.ingestion_policy.IngestionPolicyEvaluator.ensure_loaded`
    to emit the warnings as soon as rules are known.

    Args:
        evaluator: The per-mic evaluator whose loaded rules should be inspected.
    """
    for rule in evaluator.rules:
        rule_type = rule.get("rule_type", "")
        rule_id = str(rule.get("id", "")) or None
        if rule_type != "mic_id" and rule_id and rule_id not in _warned_non_mic_id_rule_ids:
            _warned_non_mic_id_rule_ids.add(rule_id)
            logger.warning(
                "live-listener: filter rule id=%s has source_key_type=%r which is not "
                "'mic_id'; rule will be skipped for voice utterances (scope=%s)",
                rule_id,
                rule_type,
                evaluator.scope,
            )


def extract_mic_key(device_name: str) -> str:
    """Extract the ``mic_id`` key value for filter evaluation.

    The key value is the device name normalised to **lowercase** as required
    by the connector-source-filter-enforcement spec:

        *"the key value is always lowercase"*

    This matches the ``source_key_type="mic_id"`` condition schema
    ``{"mic_id": "<device_name>"}``.

    Args:
        device_name: The device name from LIVE_LISTENER_DEVICES config.

    Returns:
        The key value to pass to
        :meth:`~butlers.ingestion_policy.IngestionPolicyEvaluator.evaluate`.
    """
    return device_name.lower()


def evaluate_voice_filter(
    evaluator: IngestionPolicyEvaluator,
    device_name: str,
) -> PolicyDecision:
    """Evaluate the source filter gate for a voice utterance.

    Builds an :class:`~butlers.ingestion_policy.IngestionEnvelope` with
    ``source_channel="voice"`` and ``raw_key={device_name}``, then delegates
    to the evaluator.

    Pipeline position: called **after** transcription and **before** discretion.

    Args:
        evaluator: The per-mic :class:`~butlers.ingestion_policy.IngestionPolicyEvaluator`.
        device_name: The device name from LIVE_LISTENER_DEVICES config.

    Returns:
        :class:`~butlers.ingestion_policy.PolicyDecision` with ``.allowed``
        True when the utterance should proceed to discretion, False when it
        should be dropped.
    """
    mic_key = extract_mic_key(device_name)
    envelope = IngestionEnvelope(
        source_channel="voice",
        raw_key=mic_key,
    )
    decision = evaluator.evaluate(envelope)

    if not decision.allowed:
        logger.debug(
            "live-listener: utterance blocked by source filter: mic=%s reason=%s",
            device_name,
            decision.reason,
        )

    return decision
