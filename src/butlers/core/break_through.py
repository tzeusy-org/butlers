"""Break-through sensor: does an inbound message match an owner-declared clause (bu-s11n0s.8, S1).

The owner may declare who always reaches them (a person, an authenticated sender domain, a
channel, an account-security kind). This module is the ingest-time half: after an inbound
message is accepted it asks the fixed private evaluator whether a current clause matches, and a
hit is recorded once and announced as a metadata-only ``switchboard.break_through_hit`` event.

Slice 1 is shadow-only. A hit sends nothing and promotes nothing; it is a durable row plus an
event. The urgent notice, the withdrawal probe and the owner surfaces follow in later slices.

Properties this module holds on the ingest hot path:

* **Never fails or blocks ingest.** The whole observation runs under one timeout and any
  failure, including an evaluator that raises, returns ``None``.
* **Unknown is routine.** An unreadable evaluator, a missing interface or a timeout is a miss,
  never a hit. The fault is logged at most once per interval, not once per message.
* **Independent of ingestion rules.** The decision reads only sender, channel and the account
  classification, so a promoted skip rule cannot hide a hit.
* **Exactly once.** The hit row is keyed by ``(clause_id, source_request_id)`` and inserted
  with ``ON CONFLICT DO NOTHING``; only the inserter publishes, so a connector replay is silent.
* **Metadata only.** The event names the clause, channel, resolved sender name and source
  request. It never carries a body, subject, snippet or address.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any

from butlers.core.attention_urgency import accepted_source_origin

logger = logging.getLogger(__name__)

BREAK_THROUGH_EVENT_TYPE = "switchboard.break_through_hit"

# One bound for resolve + evaluate + record + publish. Ingest is a hot path.
SENSOR_TIMEOUT_S = 2.0
_FAULT_LOG_INTERVAL_S = 300.0
_RESOLVABLE_CHANNELS = frozenset({"telegram", "email", "whatsapp"})

_last_fault_log: float | None = None


def _log_fault(error_class: str) -> None:
    """Log a sensor fault at most once per interval: a dark interface faults on every message."""
    global _last_fault_log
    now = time.monotonic()
    if _last_fault_log is not None and now - _last_fault_log < _FAULT_LOG_INTERVAL_S:
        return
    _last_fault_log = now
    logger.warning("break_through_sensor_unavailable error_class=%s", error_class)


async def _resolve_sender(
    pool: Any, channel: str | None, sender: str
) -> tuple[uuid.UUID | None, str | None]:
    """The sender's entity id and display name, or ``(None, None)`` when unresolved."""
    if channel not in _RESOLVABLE_CHANNELS or not sender:
        return None, None
    from butlers.identity import resolve_contact_by_channel

    contact = await resolve_contact_by_channel(pool, channel, sender)
    if contact is None:
        return None, None
    return contact.entity_id, contact.name


async def _matching_clause(pool: Any, origin_record: Mapping[str, str]) -> uuid.UUID | None:
    """The first current owner clause matching the origin, via the fixed evaluator."""
    values = dict(origin_record)
    values["gate"] = "quiet_hours"
    raw = await pool.fetchval("SELECT dashboard_attention.evaluate($1::jsonb)", values)
    data = json.loads(raw) if isinstance(raw, str) else raw
    if type(data) is dict and data.get("cause") == "owner_clause":
        return uuid.UUID(str(data["ref"]))
    return None


async def _observe(
    pool: Any,
    *,
    channel: str,
    sender_identity: str,
    normalized_text: str | None,
    raw: Any,
    source_request_id: str,
) -> str | None:
    sender = sender_identity.strip()
    entity_id, name = await _resolve_sender(pool, channel, sender)
    row = {
        "request_context": {"source_channel": channel, "source_sender_identity": sender},
        "raw_payload": raw,
        "normalized_text": normalized_text,
    }
    origin = accepted_source_origin(row, entity_id)
    if not origin.record():
        return None
    clause_id = await _matching_clause(pool, origin.record())
    if clause_id is None:
        return None
    inserted = await pool.fetchval(
        "INSERT INTO switchboard.break_through_hits (clause_id, source_request_id, channel) "
        "VALUES ($1, $2, $3) ON CONFLICT (clause_id, source_request_id) DO NOTHING "
        "RETURNING clause_id",
        clause_id,
        uuid.UUID(str(source_request_id)),
        origin.channel,
    )
    if inserted is None:
        return None
    payload: dict[str, str] = {
        "clause_id": str(clause_id),
        "channel": origin.channel or channel,
        "source_request_id": str(source_request_id),
    }
    if name:
        payload["sender_name"] = name
    from butlers.core_tools._domain_events import (
        _SwitchboardInProcessRouteClient,
        publish_domain_event,
    )

    result = await publish_domain_event(
        pool,
        _SwitchboardInProcessRouteClient(pool),
        event_type=BREAK_THROUGH_EVENT_TYPE,
        source_butler="switchboard",
        payload=payload,
    )
    if result.get("status") != "ok":
        # The hit row is the durable record; the event is the announcement.
        logger.warning("break_through_event_not_published status=%s", result.get("status"))
        return None
    return str(clause_id)


async def observe_inbound(
    pool: Any,
    *,
    channel: str,
    sender_identity: str,
    normalized_text: str | None,
    raw: Any,
    source_request_id: str,
    timeout_s: float = SENSOR_TIMEOUT_S,
) -> str | None:
    """Record and announce a clause hit for an accepted inbound message.

    Returns the matched clause id when this call recorded a new hit, otherwise ``None``: no
    match, a replay, or any failure. Never raises, never exceeds ``timeout_s``.
    """
    try:
        return await asyncio.wait_for(
            _observe(
                pool,
                channel=channel,
                sender_identity=sender_identity,
                normalized_text=normalized_text,
                raw=raw,
                source_request_id=source_request_id,
            ),
            timeout_s,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Class only: provider and SQL error text is never evidence.
        _log_fault(type(exc).__name__)
        return None
