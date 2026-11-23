"""Commit a durable evidence row before a connector acknowledges (bu-uhbhzq.4).

``connectors.evidence_journal`` serves the sources that have no evidence table of their own.
A connector calls :func:`commit_envelope` (or :func:`commit`) first, then submits to
Switchboard, then advances its checkpoint or acknowledges. A failed write raises
:class:`EvidenceNotCommitted`, so nothing is submitted, checkpointed or acknowledged. A failed
submit keeps the row: the journal is evidence, not a second replay queue.

Privacy: ``raw`` holds exactly what the envelope's ``message_inbox.raw_payload`` would hold at
its ingestion tier, after the shared bearer scrub, and never more. Metadata-tier envelopes
carry ``payload.raw = null``, so their journal rows keep headers and ids only. Tier-3 and
policy-blocked events are never submitted, so a connector never calls this for them.

Standalone on purpose: the connector runtime kit (bu-ep60gq.1) can absorb it later.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from butlers.ingestion_bearer_scrub import scrub_evidence_json, scrub_stored_record

logger = logging.getLogger(__name__)

CAPTURE_SCHEMA_VERSION: Final = 1
TIERS: Final = frozenset({"full", "metadata"})

# ``raw`` is passed as text and cast, so the write works on a pool with or without a jsonb codec.
_INSERT = """
INSERT INTO connectors.evidence_journal (
    source, endpoint_identity, provider_id, provider_revision, event_time, observed_at,
    capture_schema_version, ingestion_tier, idempotency_key, raw
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, ($10::text)::jsonb)
ON CONFLICT (source, endpoint_identity, provider_id, provider_revision) DO NOTHING
RETURNING id
"""


class EvidenceNotCommitted(RuntimeError):
    """The evidence row could not be written; the caller must acknowledge nothing.

    The message is a fixed label and the cause is chained, so no content reaches a log.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"evidence journal write failed ({reason})")
        self.reason = reason


def _strip_nul(value: Any) -> Any:
    """Remove U+0000 from every string and key; PostgreSQL text and jsonb cannot hold it."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


def inbox_equivalent_raw(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """The ``raw_payload`` the Switchboard would store for ``envelope``, minus its own additions.

    The same keys the ingest path writes (source, event, sender, payload.raw and
    payload.normalized_text, and the two control tiers), so the journal is a subset of the
    inbox row at the same tier.
    """
    source = envelope.get("source") or {}
    event = envelope.get("event") or {}
    payload = envelope.get("payload") or {}
    control = envelope.get("control") or {}
    sender = {k: v for k, v in (envelope.get("sender") or {}).items() if v is not None}
    return {
        "source": {
            "channel": source.get("channel"),
            "provider": source.get("provider"),
            "endpoint_identity": source.get("endpoint_identity"),
        },
        "event": {
            "external_event_id": event.get("external_event_id"),
            "external_conversation_id": event.get("external_conversation_id"),
            "reply_target_ref": event.get("reply_target_ref"),
            "external_thread_id": event.get("external_thread_id"),
            "observed_at": event.get("observed_at"),
        },
        "sender": sender,
        "payload": {
            "raw": payload.get("raw"),
            "normalized_text": payload.get("normalized_text"),
        },
        "control": {
            "policy_tier": control.get("policy_tier"),
            "ingestion_tier": control.get("ingestion_tier", "full"),
        },
    }


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise ValueError("observed_at must be a datetime or an ISO-8601 string")


async def commit(
    pool: Any,
    *,
    source: str,
    endpoint_identity: str,
    provider_id: str,
    observed_at: datetime | str,
    raw: Any,
    tier: str,
    provider_revision: str = "",
    event_time: datetime | None = None,
    idempotency_key: str | None = None,
    capture_schema_version: int = CAPTURE_SCHEMA_VERSION,
) -> bool:
    """Insert one evidence row; ``True`` if it is new, ``False`` if it already existed.

    ``raw`` is scrubbed for bearer material and stripped of NUL characters before the write.
    Any failure (including a scrub failure and a missing pool) raises ``EvidenceNotCommitted``.
    A ``tier`` other than ``full`` or ``metadata`` is a programming error (tier 3 events are
    never submitted) and raises ``ValueError``.
    """
    if tier not in TIERS:
        raise ValueError(f"evidence tier must be one of {sorted(TIERS)}")
    if pool is None:
        raise EvidenceNotCommitted("no_pool")
    try:
        scrubbed = _strip_nul(scrub_evidence_json(_strip_nul(raw), provider_domain=source))
        inserted = await pool.fetchval(
            _INSERT,
            source,
            _strip_nul(endpoint_identity),
            _strip_nul(provider_id),
            _strip_nul(provider_revision),
            event_time,
            _as_datetime(observed_at),
            capture_schema_version,
            tier,
            _strip_nul(idempotency_key),
            json.dumps(scrubbed, default=str),
        )
    except EvidenceNotCommitted:
        raise
    except Exception as exc:
        # Class name only, and no chained cause: the original text can echo message content.
        raise EvidenceNotCommitted(type(exc).__name__) from None
    return inserted is not None


async def commit_envelope(
    pool: Any,
    envelope: Mapping[str, Any],
    *,
    provider_revision: str = "",
    event_time: datetime | None = None,
    provider_id: str | None = None,
) -> bool:
    """Commit the evidence row for an ``ingest.v1`` envelope about to be submitted.

    ``source`` is the envelope's channel, ``provider_id`` defaults to its external event id,
    ``observed_at`` is the envelope's event clock reading and the tier and idempotency key come
    from its control block. The stored ``raw`` is :func:`inbox_equivalent_raw`, passed through
    the same scrub the Switchboard applies to the stored record.
    """
    source = envelope["source"]
    event = envelope["event"]
    control = envelope.get("control") or {}
    raw = inbox_equivalent_raw(envelope)
    try:
        raw, _text, _hits = scrub_stored_record(
            raw, str(raw["payload"].get("normalized_text") or "")
        )
    except Exception as exc:
        # Class name only, and no chained cause: the original text can echo message content.
        raise EvidenceNotCommitted(type(exc).__name__) from None
    return await commit(
        pool,
        source=str(source["channel"]),
        endpoint_identity=str(source["endpoint_identity"]),
        provider_id=str(provider_id if provider_id is not None else event["external_event_id"]),
        provider_revision=provider_revision,
        event_time=event_time,
        observed_at=event["observed_at"],
        raw=raw,
        tier=str(control.get("ingestion_tier", "full")),
        idempotency_key=control.get("idempotency_key"),
    )


__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "EvidenceNotCommitted",
    "TIERS",
    "commit",
    "commit_envelope",
    "inbox_equivalent_raw",
]
