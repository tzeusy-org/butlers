"""Canonical ingestion API for connector submissions.

This module provides the private Switchboard ingest API surface that connectors
use to submit `ingest.v1` envelopes and receive canonical request references.

Authentication and Authorization:
    This is a PRIVATE API for authenticated MCP tool calls only. Authentication
    and authorization are enforced at the MCP transport layer by the butler
    framework before this function is invoked. Connectors authenticate via MCP
    client certificates or butler-specific tokens managed by the framework.

    This function trusts that the caller has been validated and has permission
    to submit to the specified source endpoint. There is no per-butler or
    per-endpoint authorization logic within this function.

Key behaviors:
- Parses and validates `ingest.v1` envelopes using canonical contract models
- Assigns canonical request context (request_id, received_at, etc.)
- Performs deduplication based on source identity and idempotency keys
- Runs unified ingestion policy evaluation (replaces legacy triage) before returning
- Returns 202 Accepted with canonical request reference and policy decision
- Duplicate submissions return the same request reference (idempotent)

Design notes:
- Reuses `IngestEnvelopeV1` contract validation (no forked semantics)
- Deduplication strategy follows `butlers-9aq.4` guidance
- Lifecycle persistence uses partitioned `message_inbox` from `butlers-9aq.9`
- Unique index on dedupe_key (migration sw_010) prevents race conditions
- Ingestion policy evaluation via IngestionPolicyEvaluator(scope='global')
  per unified-ingestion-policy design (D5-D7).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from opentelemetry import metrics as otel_metrics
from pydantic import BaseModel, ConfigDict

from butlers.core.metrics import ButlerMetrics
from butlers.ingestion_policy import (
    IngestionEnvelope,
    IngestionPolicyEvaluator,
    PolicyDecision,
)
from butlers.tools.switchboard.routing.contracts import (
    IngestEnvelopeV1,
    parse_ingest_envelope,
)
from butlers.tools.switchboard.triage.thread_affinity import (
    ThreadAffinitySettings,
    lookup_thread_affinity,
)

logger = logging.getLogger(__name__)


def _strip_null_bytes(value: Any) -> Any:
    """Recursively strip \\x00 from strings, dicts, and lists.

    PostgreSQL text/jsonb columns reject null bytes (\\u0000).  External
    payloads (e.g. email bodies, webhook data) may contain them, so we
    sanitize at the ingestion boundary.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_null_bytes(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_strip_null_bytes(v) for v in value)
    return value


# Module-level metrics instance for the switchboard ingest boundary.
# Safe to construct before init_metrics() is called; recordings are no-ops
# until a real MeterProvider is installed.
_ingest_metrics = ButlerMetrics("switchboard")

# ---------------------------------------------------------------------------
# Ingestion policy telemetry (replaces legacy TriageTelemetry)
# ---------------------------------------------------------------------------

_INGESTION_METER_NAME = "butlers.switchboard"

_ALLOWED_RULE_TYPES = frozenset(
    {
        "sender_domain",
        "sender_address",
        "header_condition",
        "mime_type",
        "thread_affinity",
        "substring",
        "chat_id",
        "channel_id",
    }
)
_ALLOWED_ACTIONS = frozenset(
    {"skip", "metadata_only", "low_priority_queue", "pass_through", "route_to", "block"}
)
_ALLOWED_PASS_THROUGH_REASONS = frozenset({"no_match", "cache_unavailable", "rules_disabled"})
_ALLOWED_RESULTS = frozenset({"matched", "pass_through", "error"})


def _safe_action(action: str) -> str:
    if action.startswith("route_to:"):
        return "route_to"
    if action in _ALLOWED_ACTIONS:
        return action
    return "unknown"


class _IngestionPolicyTelemetry:
    """Lightweight telemetry for ingestion policy evaluation metrics."""

    def __init__(self) -> None:
        meter = otel_metrics.get_meter(_INGESTION_METER_NAME)
        self.rule_matched = meter.create_counter(
            "butlers.switchboard.triage.rule_matched",
            unit="1",
            description="Messages matched by a deterministic ingestion policy rule.",
        )
        self.pass_through = meter.create_counter(
            "butlers.switchboard.triage.pass_through",
            unit="1",
            description="Messages that passed through to LLM classification.",
        )
        self.evaluation_latency_ms = meter.create_histogram(
            "butlers.switchboard.triage.evaluation_latency_ms",
            unit="ms",
            description="End-to-end ingestion policy evaluation latency in milliseconds.",
        )

    def record_rule_matched(
        self,
        *,
        rule_type: str,
        action: str,
        source_channel: str,
    ) -> None:
        safe_rt = rule_type if rule_type in _ALLOWED_RULE_TYPES else "unknown"
        self.rule_matched.add(
            1,
            {
                "rule_type": safe_rt,
                "action": _safe_action(action),
                "source_channel": str(source_channel)[:32] if source_channel else "unknown",
            },
        )

    def record_pass_through(self, *, source_channel: str, reason: str) -> None:
        safe_reason = reason if reason in _ALLOWED_PASS_THROUGH_REASONS else "no_match"
        self.pass_through.add(
            1,
            {
                "source_channel": str(source_channel)[:32] if source_channel else "unknown",
                "reason": safe_reason,
            },
        )

    def record_evaluation_latency(self, *, latency_ms: float, result: str) -> None:
        safe_result = result if result in _ALLOWED_RESULTS else "unknown"
        self.evaluation_latency_ms.record(latency_ms, {"result": safe_result})


_POLICY_TELEMETRY: _IngestionPolicyTelemetry | None = None


def _get_policy_telemetry() -> _IngestionPolicyTelemetry:
    global _POLICY_TELEMETRY
    if _POLICY_TELEMETRY is None:
        _POLICY_TELEMETRY = _IngestionPolicyTelemetry()
    return _POLICY_TELEMETRY


class IngestAcceptedResponse(BaseModel):
    """Response payload for accepted ingest submissions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    status: str = "accepted"
    duplicate: bool = False
    triage_decision: str | None = None
    """The deterministic triage decision applied to this message.

    One of: route_to, skip, metadata_only, low_priority_queue, pass_through.
    None for duplicates (triage was applied on first submission).
    """
    triage_target: str | None = None
    """Target butler name, populated only when triage_decision='route_to'."""


def _generate_uuid7() -> UUID:
    """Generate a UUIDv7-compatible UUID.

    UUIDv7 embeds a timestamp in the most significant bits for
    time-ordered uniqueness.
    """
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def _compute_content_hash_key(envelope: IngestEnvelopeV1) -> str:
    """Compute content-hash dedup key independent of connector-specific identifiers.

    Used as a secondary dedup check to catch the same logical message submitted
    by different connectors (e.g. telegram_bot and telegram_user_client) that may
    produce different primary idempotency keys.
    """
    content_repr = f"{envelope.payload.normalized_text}:{envelope.sender.identity}"
    content_hash = hashlib.sha256(content_repr.encode()).hexdigest()[:16]
    time_bucket = envelope.event.observed_at.strftime("%Y%m%d%H")  # hourly window
    return (
        f"hash:{envelope.source.channel}:{envelope.source.endpoint_identity}"
        f":{envelope.sender.identity}:{time_bucket}:{content_hash}"
    )


def _compute_dedupe_key(envelope: IngestEnvelopeV1) -> str:
    """Compute stable deduplication key from ingest envelope.

    Strategy:
    - Priority 1: Use explicit idempotency_key if provided
    - Priority 2: Use external_event_id + source identity (if meaningful)
    - Priority 3: Fall back to content hash + source identity + time window

    The dedupe key must be stable across retries for the same logical event.

    Placeholder event IDs (e.g., "placeholder", "unknown", "none") are treated
    as missing and fall through to content hash deduplication.
    """
    source = envelope.source
    event = envelope.event
    control = envelope.control

    # Priority 1: explicit idempotency key
    if control.idempotency_key:
        return f"idem:{source.channel}:{source.endpoint_identity}:{control.idempotency_key}"

    # Priority 2: external event ID (canonical for most sources)
    # Exclude placeholder values that are not meaningful stable identifiers
    if event.external_event_id and event.external_event_id.lower() not in {
        "placeholder",
        "unknown",
        "none",
        "",
    }:
        return (
            f"event:{source.channel}:{source.provider}:"
            f"{source.endpoint_identity}:{event.external_event_id}"
        )

    # Priority 3: content hash fallback (for sources without stable event IDs)
    # This is less stable but provides basic protection against immediate duplicates
    return _compute_content_hash_key(envelope)


async def _find_request_by_dedupe_key(pool: asyncpg.Pool, dedupe_key: str) -> asyncpg.Record | None:
    """Find the latest request_id for a given dedupe_key.

    This query uses the unique index on (request_context ->> 'dedupe_key')
    created by migration sw_010 for efficient lookup.
    """
    return await pool.fetchrow(
        """
        SELECT (request_context ->> 'request_id')::uuid AS request_id
        FROM message_inbox
        WHERE request_context ->> 'dedupe_key' = $1
        ORDER BY received_at DESC
        LIMIT 1
        """,
        dedupe_key,
    )


async def _find_request_by_content_hash(
    pool: asyncpg.Pool, content_hash_key: str
) -> asyncpg.Record | None:
    """Find a request whose stored content_hash_key matches.

    Used as a secondary dedup check to catch the same logical message submitted
    by different connectors with different primary idempotency keys.
    """
    return await pool.fetchrow(
        """
        SELECT (request_context ->> 'request_id')::uuid AS request_id
        FROM message_inbox
        WHERE request_context ->> 'content_hash_key' = $1
        ORDER BY received_at DESC
        LIMIT 1
        """,
        content_hash_key,
    )


def _build_request_context(
    envelope: IngestEnvelopeV1,
    *,
    request_id: UUID,
    received_at: datetime,
    triage_decision: PolicyDecision | None = None,
) -> dict[str, Any]:
    """Build canonical request context from ingest envelope.

    This function assigns the immutable request-context fields that will
    be propagated through routing and fanout. Policy decision metadata is
    embedded when available for downstream pipeline visibility.

    The ``triage_decision`` parameter name is kept for backward compatibility
    with downstream consumers that read ``triage_*`` keys from request_context,
    but it now accepts a ``PolicyDecision`` from IngestionPolicyEvaluator.
    """
    source = envelope.source
    event = envelope.event
    sender = envelope.sender
    control = envelope.control

    context: dict[str, Any] = {
        "request_id": str(request_id),
        "received_at": received_at.isoformat(),
        "source_channel": source.channel,
        "source_endpoint_identity": source.endpoint_identity,
        "source_sender_identity": sender.identity,
    }

    # Optional fields
    if event.external_thread_id:
        context["source_thread_identity"] = event.external_thread_id

    if control.idempotency_key:
        context["idempotency_key"] = control.idempotency_key

    if control.trace_context:
        context["trace_context"] = control.trace_context

    # Tier annotation (always stored; defaults to "full" for backward compat)
    context["ingestion_tier"] = control.ingestion_tier

    # Policy decision annotation: embed for downstream pipeline visibility.
    # Keys use "triage_" prefix for backward compatibility with existing consumers.
    if triage_decision is not None:
        context["triage_decision"] = triage_decision.action
        if triage_decision.target_butler:
            context["triage_target"] = triage_decision.target_butler
        if triage_decision.matched_rule_id:
            context["triage_rule_id"] = triage_decision.matched_rule_id
        if triage_decision.matched_rule_type:
            context["triage_rule_type"] = triage_decision.matched_rule_type

    return context


def _make_ingestion_envelope(
    payload: Mapping[str, Any],
) -> IngestionEnvelope:
    """Build an IngestionEnvelope from a raw ingest.v1 envelope dict.

    This is the adapter between the raw connector payload and the unified
    IngestionEnvelope type consumed by IngestionPolicyEvaluator.
    """
    sender = payload.get("sender") or {}
    sender_address = str(sender.get("identity") or "").lower()

    source = payload.get("source") or {}
    source_channel = str(source.get("channel") or "")

    payload_section = payload.get("payload") or {}
    raw = payload_section.get("raw") or {}

    # Extract headers from raw payload (email-specific)
    raw_headers = raw.get("headers") or {}
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}
    else:
        headers = {}

    # Extract MIME parts from attachments or payload
    mime_parts: list[str] = []
    attachments = payload_section.get("attachments") or []
    for att in attachments:
        if isinstance(att, dict) and att.get("media_type"):
            mime_parts.append(str(att["media_type"]).lower())

    # Also extract from raw.mime_parts if present
    raw_mime = raw.get("mime_parts") or []
    for part in raw_mime:
        if isinstance(part, dict) and part.get("type"):
            mime_parts.append(str(part["type"]).lower())
        elif isinstance(part, str):
            mime_parts.append(part.lower())

    event = payload.get("event") or {}
    thread_id = event.get("external_thread_id")

    # Build raw_key based on channel
    raw_key = ""
    if source_channel == "email":
        raw_key = sender_address
    elif source_channel in ("telegram",):
        raw_key = str(source.get("endpoint_identity") or "")
    elif source_channel in ("discord",):
        raw_key = str(source.get("endpoint_identity") or "")

    return IngestionEnvelope(
        sender_address=sender_address,
        source_channel=source_channel,
        headers=headers,
        mime_parts=mime_parts,
        thread_id=str(thread_id) if thread_id else None,
        raw_key=raw_key,
    )


def _run_policy_evaluation(
    payload: Mapping[str, Any],
    evaluator: IngestionPolicyEvaluator,
    *,
    source_channel: str,
    thread_affinity_target: str | None = None,
) -> PolicyDecision:
    """Run unified ingestion policy evaluation with telemetry.

    Uses
    ``IngestionPolicyEvaluator.evaluate()`` for rule matching and wraps
    the call with triage telemetry for backward-compatible metrics.

    Thread affinity is handled before rule evaluation: when a
    ``thread_affinity_target`` is provided, a ``route_to`` decision is
    returned immediately without consulting the evaluator's rule set.

    Fail-open: if evaluation raises, returns pass_through.

    Parameters
    ----------
    payload:
        Raw ingest.v1 envelope payload dict.
    evaluator:
        Pre-loaded IngestionPolicyEvaluator(scope='global').
    source_channel:
        Source channel string for telemetry attributes.
    thread_affinity_target:
        Pre-resolved thread affinity butler name (from lookup_thread_affinity).
        When set, bypasses rule evaluation with a route_to decision.
    """
    telemetry = _get_policy_telemetry()
    t0 = time.monotonic()
    result_label = "pass_through"

    try:
        # Thread affinity has highest precedence (unchanged from legacy pipeline)
        if thread_affinity_target:
            decision = PolicyDecision(
                action="route_to",
                target_butler=thread_affinity_target,
                matched_rule_id=None,
                matched_rule_type="thread_affinity",
                reason=f"thread affinity match -> {thread_affinity_target}",
            )
            telemetry.record_rule_matched(
                rule_type="thread_affinity",
                action=f"route_to:{thread_affinity_target}",
                source_channel=source_channel,
            )
            result_label = "matched"
            return decision

        envelope = _make_ingestion_envelope(dict(payload))
        decision = evaluator.evaluate(envelope)

        if decision.action == "pass_through":
            telemetry.record_pass_through(
                source_channel=source_channel,
                reason="no_match",
            )
            result_label = "pass_through"
        else:
            telemetry.record_rule_matched(
                rule_type=decision.matched_rule_type or "unknown",
                action=decision.action
                if not decision.target_butler
                else f"route_to:{decision.target_butler}",
                source_channel=source_channel,
            )
            result_label = "matched"

        return decision

    except Exception:
        logger.exception(
            "Unexpected error during ingestion policy evaluation; failing open (pass_through)"
        )
        result_label = "error"
        return PolicyDecision(
            action="pass_through",
            reason="ingestion policy evaluation error",
        )
    finally:
        latency_ms = (time.monotonic() - t0) * 1000
        telemetry.record_evaluation_latency(
            latency_ms=latency_ms,
            result=result_label,
        )


async def ingest_v1(
    pool: asyncpg.Pool,
    payload: Mapping[str, Any],
    *,
    policy_evaluator: IngestionPolicyEvaluator | None = None,
    thread_affinity_settings: ThreadAffinitySettings | None = None,
    enable_thread_affinity: bool = True,
) -> IngestAcceptedResponse:
    """Accept and persist an `ingest.v1` envelope submission.

    This is the canonical ingestion boundary for connector submissions.
    It parses, validates, deduplicates, applies unified ingestion policy
    evaluation (including thread-affinity lookup), and persists the ingest
    envelope, returning a canonical request reference.

    Authentication and authorization are enforced at the MCP transport layer
    before this function is called. See module docstring for details.

    Parameters
    ----------
    pool:
        Database connection pool for Switchboard butler.
    payload:
        Raw ingest envelope payload (must validate as `ingest.v1`).
    policy_evaluator:
        An ``IngestionPolicyEvaluator(scope='global')`` instance with rules
        already loaded via ``ensure_loaded()``. Pass ``None`` to bypass
        policy evaluation entirely (no triage annotation).
    thread_affinity_settings:
        Pre-loaded ThreadAffinitySettings. When None and enable_thread_affinity
        is True, settings will be fetched from the DB on each call.
    enable_thread_affinity:
        When False, thread-affinity lookup is skipped entirely. Defaults True.
        Set to False in tests or when thread-affinity is not yet deployed.

    Returns
    -------
    IngestAcceptedResponse
        Canonical request reference with `request_id`, duplicate status,
        and triage decision annotation.

    Raises
    ------
    ValueError
        If the payload fails `ingest.v1` validation.
    RuntimeError
        If database persistence fails unexpectedly.
    """
    # Best-effort source extraction for metrics before full validation
    _source_for_metrics = "unknown"
    try:
        src = payload.get("source") if hasattr(payload, "get") else None
        if isinstance(src, dict):
            _source_for_metrics = src.get("channel", "unknown") or "unknown"
    except Exception:
        pass

    # 1. Parse and validate envelope using canonical contract model
    try:
        envelope = parse_ingest_envelope(payload)
    except Exception as exc:
        logger.warning("Ingest envelope validation failed: %s", exc)
        _ingest_metrics.record_ingest_result(source=_source_for_metrics, outcome="validation_error")
        raise ValueError(f"Invalid ingest.v1 envelope: {exc}") from exc

    # 2. Compute stable dedupe key
    dedupe_key = _compute_dedupe_key(envelope)

    # 3. Check for existing request (idempotent duplicate handling)
    existing = await _find_request_by_dedupe_key(pool, dedupe_key)

    # 3a. Secondary content-hash check: catches cross-connector duplicates where
    # different connectors produce different primary keys for the same message.
    # Only runs when the primary key used a connector-specific strategy (not
    # already a content hash).
    if not existing and not dedupe_key.startswith("hash:"):
        content_hash_key = _compute_content_hash_key(envelope)
        existing = await _find_request_by_content_hash(pool, content_hash_key)
        if existing:
            logger.info(
                "Cross-connector duplicate detected via content hash: "
                "primary_key=%s, content_hash_key=%s, existing_request_id=%s",
                dedupe_key,
                content_hash_key,
                existing["request_id"],
            )

    if existing:
        # Duplicate submission — return existing request reference
        logger.info(
            "Duplicate ingest submission detected for dedupe_key=%s, "
            "returning existing request_id=%s",
            dedupe_key,
            existing["request_id"],
        )
        _ingest_metrics.record_ingest_result(source=envelope.source.channel, outcome="success")
        return IngestAcceptedResponse(
            request_id=existing["request_id"],
            status="accepted",
            duplicate=True,
            triage_decision=None,  # Triage was applied on first submission
            triage_target=None,
        )

    # 4. Run ingestion policy evaluation (before classification runtime spawn).
    # Thread-affinity lookup runs before rule evaluation (pipeline order):
    #   1. Thread-affinity lookup in routing history (highest precedence)
    #   2. Ingestion policy rules (via IngestionPolicyEvaluator)
    #   3. LLM classification fallback (pass_through)
    #
    # policy_evaluator=None means caller did not provide one — skip annotation
    triage_decision: PolicyDecision | None = None
    source_channel = envelope.source.channel
    thread_id: str | None = None
    if envelope.event.external_thread_id:
        thread_id = str(envelope.event.external_thread_id)

    # 4a. Thread-affinity lookup (email only, before rule evaluation)
    affinity_target: str | None = None
    if enable_thread_affinity and source_channel == "email" and thread_id:
        try:
            affinity_result = await lookup_thread_affinity(
                pool,
                thread_id,
                source_channel,
                settings=thread_affinity_settings,
            )
            if affinity_result.outcome.produces_route:
                affinity_target = affinity_result.target_butler
                logger.debug(
                    "Thread affinity hit: thread=%s -> butler=%s",
                    thread_id,
                    affinity_target,
                )
        except Exception:
            logger.exception(
                "Thread affinity lookup raised unexpectedly; failing open (no affinity)"
            )

    if policy_evaluator is not None:
        triage_decision = _run_policy_evaluation(
            payload,
            policy_evaluator,
            source_channel=source_channel,
            thread_affinity_target=affinity_target,
        )
        logger.debug(
            "Policy decision for source=%s sender=%s: %s",
            source_channel,
            envelope.sender.identity,
            triage_decision.action,
        )

    # 5. Assign canonical request context
    request_id = _generate_uuid7()
    received_at = datetime.now(UTC)

    request_context = _build_request_context(
        envelope,
        request_id=request_id,
        received_at=received_at,
        triage_decision=triage_decision,
    )
    # Embed dedupe_key in request_context for lookup
    request_context["dedupe_key"] = dedupe_key
    # Store content-hash key for cross-connector dedup (secondary lookup).
    # When the primary key is already a content hash, no separate key is needed.
    if not dedupe_key.startswith("hash:"):
        request_context["content_hash_key"] = _compute_content_hash_key(envelope)
    request_context["dedupe_strategy"] = "connector_api"

    # 6. Build raw_payload and normalized_text
    # For Tier 2 (metadata), payload.raw is None per contract
    ingestion_tier = envelope.control.ingestion_tier
    raw_payload = {
        "source": {
            "channel": envelope.source.channel,
            "provider": envelope.source.provider,
            "endpoint_identity": envelope.source.endpoint_identity,
        },
        "event": {
            "external_event_id": envelope.event.external_event_id,
            "external_thread_id": envelope.event.external_thread_id,
            "observed_at": envelope.event.observed_at.isoformat(),
        },
        "sender": {
            "identity": envelope.sender.identity,
        },
        "payload": {
            "raw": envelope.payload.raw,
            "normalized_text": envelope.payload.normalized_text,
        },
        "control": {
            "policy_tier": envelope.control.policy_tier,
            "ingestion_tier": ingestion_tier,
        },
    }

    normalized_text = envelope.payload.normalized_text

    # 6a. Serialize attachments if present
    attachments_json = None
    if envelope.payload.attachments:
        attachments_json = json.dumps(
            [
                {
                    "media_type": att.media_type,
                    "storage_ref": att.storage_ref,
                    "size_bytes": att.size_bytes,
                    "filename": att.filename,
                    "width": att.width,
                    "height": att.height,
                }
                for att in envelope.payload.attachments
            ]
        )

    # 6b. Strip null bytes — PostgreSQL rejects \u0000 in text/jsonb columns
    normalized_text = _strip_null_bytes(normalized_text)
    request_context = _strip_null_bytes(request_context)
    raw_payload = _strip_null_bytes(raw_payload)
    if attachments_json is not None:
        attachments_json = _strip_null_bytes(attachments_json)

    # 7. Ensure partition exists for received_at — committed immediately,
    # OUTSIDE any transaction so that DDL (CREATE TABLE IF NOT EXISTS) cannot
    # be rolled back by a subsequent failure inside the dedup transaction.
    #
    # Background: switchboard_message_inbox_ensure_partition() uses DDL
    # (CREATE TABLE IF NOT EXISTS ... PARTITION OF message_inbox).
    # PostgreSQL allows DDL inside a transaction, but a transaction rollback
    # also drops any tables created within it.  If ensure_partition is called
    # inside the advisory-lock transaction and the transaction rolls back (e.g.
    # shared.ingestion_events missing, network error, unique violation), the
    # newly created partition is dropped and subsequent inserts keep failing in
    # a tight loop until the problem is resolved.
    #
    # Running ensure_partition on an auto-commit connection (pool.execute, not
    # conn.execute inside a transaction) makes the partition creation durable
    # regardless of what happens later in the dedup transaction.
    try:
        await pool.execute(
            "SELECT switchboard_message_inbox_ensure_partition($1)",
            received_at,
        )
    except Exception as exc:
        logger.error(
            "Failed to ensure message_inbox partition for received_at=%s: %s",
            received_at,
            exc,
            exc_info=True,
        )
        _ingest_metrics.record_ingest_result(source=source_channel, outcome="db_error")
        raise RuntimeError(f"Failed to ensure message_inbox partition: {exc}") from exc

    # 8. Dedup-safe insert: acquire a dedicated connection and serialise on
    # the dedupe_key via pg_advisory_xact_lock so that concurrent submissions
    # for the same logical event cannot both pass the duplicate check.
    #
    # Background: the unique index on message_inbox includes received_at
    # (required by PostgreSQL partitioning) so two rows with the same
    # dedupe_key but different received_at timestamps can both INSERT
    # successfully.  The advisory lock eliminates that race.
    lifecycle_state = "metadata_ref" if ingestion_tier == "metadata" else "accepted"

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Serialise concurrent inserts for the same dedupe_key
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", dedupe_key)

                # Also lock on content-hash key to serialize cross-connector races
                inner_content_hash_key = (
                    request_context.get("content_hash_key")
                    if not dedupe_key.startswith("hash:")
                    else None
                )
                if inner_content_hash_key:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        inner_content_hash_key,
                    )

                # Re-check inside lock — another insert may have committed
                # between the optimistic check (step 3) and acquiring the lock
                existing = await conn.fetchrow(
                    """
                    SELECT (request_context ->> 'request_id')::uuid AS request_id
                    FROM message_inbox
                    WHERE request_context ->> 'dedupe_key' = $1
                    ORDER BY received_at DESC
                    LIMIT 1
                    """,
                    dedupe_key,
                )
                # Cross-connector re-check inside lock
                if not existing and inner_content_hash_key:
                    existing = await conn.fetchrow(
                        """
                        SELECT (request_context ->> 'request_id')::uuid AS request_id
                        FROM message_inbox
                        WHERE request_context ->> 'content_hash_key' = $1
                        ORDER BY received_at DESC
                        LIMIT 1
                        """,
                        inner_content_hash_key,
                    )
                if existing:
                    logger.info(
                        "Duplicate detected inside advisory lock for dedupe_key=%s, "
                        "returning existing request_id=%s",
                        dedupe_key,
                        existing["request_id"],
                    )
                    _ingest_metrics.record_ingest_result(source=source_channel, outcome="success")
                    return IngestAcceptedResponse(
                        request_id=existing["request_id"],
                        status="accepted",
                        duplicate=True,
                        triage_decision=None,
                        triage_target=None,
                    )

                # Insert into message_inbox lifecycle store
                await conn.execute(
                    """
                    INSERT INTO message_inbox (
                        id,
                        received_at,
                        request_context,
                        raw_payload,
                        normalized_text,
                        attachments,
                        lifecycle_state,
                        schema_version,
                        processing_metadata,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3::jsonb, $4::jsonb, $5, $6::jsonb,
                        $7, 'message_inbox.v2', '{}'::jsonb, $2, $2
                    )
                    """,
                    request_id,
                    received_at,
                    json.dumps(request_context),
                    json.dumps(raw_payload),
                    normalized_text,
                    attachments_json,
                    lifecycle_state,
                )

                # Insert canonical ingestion event — same UUID7, same transaction.
                # This row is the durable, normalised first-class record of every
                # accepted ingest; downstream sessions reference it via FK.
                await conn.execute(
                    """
                    INSERT INTO shared.ingestion_events (
                        id,
                        received_at,
                        source_channel,
                        source_provider,
                        source_endpoint_identity,
                        source_sender_identity,
                        source_thread_identity,
                        external_event_id,
                        dedupe_key,
                        dedupe_strategy,
                        ingestion_tier,
                        policy_tier,
                        triage_decision,
                        triage_target
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                    )
                    """,
                    request_id,
                    received_at,
                    _strip_null_bytes(envelope.source.channel),
                    _strip_null_bytes(envelope.source.provider),
                    _strip_null_bytes(envelope.source.endpoint_identity),
                    _strip_null_bytes(envelope.sender.identity),
                    _strip_null_bytes(envelope.event.external_thread_id),
                    _strip_null_bytes(envelope.event.external_event_id),
                    _strip_null_bytes(dedupe_key),
                    "connector_api",
                    ingestion_tier,
                    envelope.control.policy_tier,
                    triage_decision.action if triage_decision is not None else None,
                    triage_decision.target_butler if triage_decision is not None else None,
                )
    except Exception as exc:
        logger.error("Failed to persist ingest envelope: %s", exc, exc_info=True)
        _ingest_metrics.record_ingest_result(source=source_channel, outcome="db_error")
        raise RuntimeError(f"Failed to persist ingest envelope: {exc}") from exc

    logger.info(
        "Accepted ingest submission: request_id=%s, dedupe_key=%s, source=%s/%s, "
        "sender=%s, ingestion_tier=%s, lifecycle_state=%s, triage=%s",
        request_id,
        dedupe_key,
        envelope.source.channel,
        envelope.source.endpoint_identity,
        envelope.sender.identity,
        ingestion_tier,
        lifecycle_state,
        triage_decision.action if triage_decision else "n/a",
    )

    _ingest_metrics.record_ingest_result(source=source_channel, outcome="success")
    return IngestAcceptedResponse(
        request_id=request_id,
        status="accepted",
        duplicate=False,
        triage_decision=triage_decision.action if triage_decision else None,
        triage_target=triage_decision.target_butler if triage_decision else None,
    )
