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


def extract_mic_key(device_name: str) -> str:
    """Extract the ``mic_id`` key value for filter evaluation.

    The key value is the device name exactly as configured in
    ``LIVE_LISTENER_DEVICES`` (the ``"name"`` field).  This matches the
    ``source_key_type="mic_id"`` condition schema ``{"mic_id": "<device_name>"}``.

    Args:
        device_name: The device name from LIVE_LISTENER_DEVICES config.

    Returns:
        The key value to pass to
        :meth:`~butlers.ingestion_policy.IngestionPolicyEvaluator.evaluate`.
    """
    return device_name


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
