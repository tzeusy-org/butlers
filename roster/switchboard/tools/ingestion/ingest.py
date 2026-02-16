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
- Returns 202 Accepted with canonical request reference
- Duplicate submissions return the same request reference (idempotent)

Design notes:
- Reuses `IngestEnvelopeV1` contract validation (no forked semantics)
- Deduplication strategy follows `butlers-9aq.4` guidance
- Lifecycle persistence uses partitioned `message_inbox` from `butlers-9aq.9`
- Unique index on dedupe_key (migration sw_010) prevents race conditions
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

from butlers.tools.switchboard.routing.contracts import (
    IngestEnvelopeV1,
    parse_ingest_envelope,
)

logger = logging.getLogger(__name__)


class IngestAcceptedResponse(BaseModel):
    """Response payload for accepted ingest submissions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    status: str = "accepted"
    duplicate: bool = False


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
    content_repr = f"{envelope.payload.normalized_text}:{envelope.sender.identity}"
    content_hash = hashlib.sha256(content_repr.encode()).hexdigest()[:16]
    time_bucket = event.observed_at.strftime("%Y%m%d%H")  # hourly window
    return (
        f"hash:{source.channel}:{source.endpoint_identity}:"
        f"{envelope.sender.identity}:{time_bucket}:{content_hash}"
    )


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


def _build_request_context(
    envelope: IngestEnvelopeV1,
    *,
    request_id: UUID,
    received_at: datetime,
) -> dict[str, Any]:
    """Build canonical request context from ingest envelope.

    This function assigns the immutable request-context fields that will
    be propagated through routing and fanout.
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

    return context


async def ingest_v1(
    pool: asyncpg.Pool,
    payload: Mapping[str, Any],
) -> IngestAcceptedResponse:
    """Accept and persist an `ingest.v1` envelope submission.

    This is the canonical ingestion boundary for connector submissions.
    It parses, validates, deduplicates, and persists the ingest envelope,
    returning a canonical request reference.

    Authentication and authorization are enforced at the MCP transport layer
    before this function is called. See module docstring for details.

    Parameters
    ----------
    pool:
        Database connection pool for Switchboard butler.
    payload:
        Raw ingest envelope payload (must validate as `ingest.v1`).

    Returns
    -------
    IngestAcceptedResponse
        Canonical request reference with `request_id` and duplicate status.

    Raises
    ------
    ValueError
        If the payload fails `ingest.v1` validation.
    RuntimeError
        If database persistence fails unexpectedly.
    """
    # 1. Parse and validate envelope using canonical contract model
    try:
        envelope = parse_ingest_envelope(payload)
    except Exception as exc:
        logger.warning("Ingest envelope validation failed: %s", exc)
        raise ValueError(f"Invalid ingest.v1 envelope: {exc}") from exc

    # 2. Compute stable dedupe key
    dedupe_key = _compute_dedupe_key(envelope)

    # 3. Check for existing request (idempotent duplicate handling)
    existing = await _find_request_by_dedupe_key(pool, dedupe_key)

    if existing:
        # Duplicate submission — return existing request reference
        logger.info(
            "Duplicate ingest submission detected for dedupe_key=%s, "
            "returning existing request_id=%s",
            dedupe_key,
            existing["request_id"],
        )
        return IngestAcceptedResponse(
            request_id=existing["request_id"],
            status="accepted",
            duplicate=True,
        )

    # 4. Assign canonical request context
    request_id = _generate_uuid7()
    received_at = datetime.now(UTC)

    request_context = _build_request_context(
        envelope,
        request_id=request_id,
        received_at=received_at,
    )
    # Embed dedupe_key in request_context for lookup
    request_context["dedupe_key"] = dedupe_key
    request_context["dedupe_strategy"] = "connector_api"

    # 5. Build raw_payload and normalized_text
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

    # 7. Ensure partition exists for received_at
    await pool.execute(
        "SELECT switchboard_message_inbox_ensure_partition($1)",
        received_at,
    )

    # 8. Insert into message_inbox lifecycle store
    try:
        await pool.execute(
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
                'accepted', 'message_inbox.v2', '{}'::jsonb, $2, $2
            )
            """,
            request_id,
            received_at,
            json.dumps(request_context),
            json.dumps(raw_payload),
            normalized_text,
            attachments_json,
        )
    except asyncpg.UniqueViolationError:
        # Race condition: another worker already inserted this dedupe_key
        # Re-fetch and return existing request_id
        existing = await _find_request_by_dedupe_key(pool, dedupe_key)
        if existing:
            logger.info(
                "Race condition: duplicate insertion detected for dedupe_key=%s, "
                "returning existing request_id=%s",
                dedupe_key,
                existing["request_id"],
            )
            return IngestAcceptedResponse(
                request_id=existing["request_id"],
                status="accepted",
                duplicate=True,
            )
        # Should not reach here, but fail-safe
        raise RuntimeError(
            f"Unique violation for dedupe_key={dedupe_key} but no existing row found"
        )
    except Exception as exc:
        logger.error("Failed to persist ingest envelope: %s", exc, exc_info=True)
        raise RuntimeError(f"Failed to persist ingest envelope: {exc}") from exc

    logger.info(
        "Accepted ingest submission: request_id=%s, dedupe_key=%s, source=%s/%s, sender=%s",
        request_id,
        dedupe_key,
        envelope.source.channel,
        envelope.source.endpoint_identity,
        envelope.sender.identity,
    )

    return IngestAcceptedResponse(
        request_id=request_id,
        status="accepted",
        duplicate=False,
    )
