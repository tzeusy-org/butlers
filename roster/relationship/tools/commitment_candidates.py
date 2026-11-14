"""Owner-chat commitment candidates: the deterministic reducer (bu-jv1moa.11).

REQ-commitment-lifecycle-011, -012 and -015. Switchboard's ``owner_chat.spans``
relay returns the owner's own unforwarded messages from one-to-one user-client
chats (REQ-butler-switchboard-011). This module turns each into at most one
candidate in ``relationship.commitment_candidates``:

* the counterparty is the chat's single non-owner identity, resolved through
  ``resolve_contact_by_channel``; it must be a live, identified, non-owner entity;
* ``detect_commitment`` runs on the text with ``now`` at the message's event time
  in the owner's timezone, so "tomorrow" means the day after the message;
* an action longer than 120 characters is refused, never truncated;
* nothing is written when an active commitment or a live candidate already has
  the fingerprint, or when the source message already has a row or tombstone.

The message text is never stored, logged or returned; only the detected action
is. A candidate is not a commitment: only the owner's signed Track tap promotes
one (REQ-commitment-lifecycle-014). No model runs and no goal is admitted.

Every terminal transition clears the content columns (the migration's CHECK
refuses anything else), candidates lapse silently 14 days after the source
event, and tombstones are deleted once their event is older than that.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.commitments import commitment_fingerprint
from butlers.core.general_settings import resolve_general_timezone
from butlers.core.owner_conditions import get_active_condition
from butlers.core.state import state_get, state_set
from butlers.identity import resolve_contact_by_channel
from butlers.roster_imports import roster_module

_commitments = roster_module("relationship", "tools/commitments")

COMMITMENT_SOURCE: Final = "relationship:commitment"
DIRECTION: Final = "owner_to_other"
CHANNELS: Final = ("whatsapp_user_client", "telegram_user_client")
MAX_ACTION_CHARS: Final = 120
#: The stored marker is a fixed category, never the matched text: the relay forms
#: ("I told <name> I'll", "I promised <name>") would otherwise store a third party's
#: name. One category per family of ``commitments._MARKERS``; rel_042 CHECKs the set.
_MARKER_FAMILIES: Final = (
    (re.compile(r"[Ii]\s+told\s+.+"), "relay_told"),
    (re.compile(r"[Ii]\s+promised\s+.+"), "relay_promised"),
    (re.compile(r"[Ii]\s*['’]ll|[Ii]\s+will"), "first_person_will"),
    (re.compile(r"[Ii]\s*['’]m\s+going\s+to|[Ii]\s+am\s+going\s+to"), "first_person_going_to"),
    (
        re.compile(r"[Ii]\s*['’]ve\s+got\s+to|[Ii]\s+have\s+got\s+to|[Ii]\s+(?:need|have)\s+to"),
        "first_person_obligation",
    ),
)
MARKER_CATEGORIES: Final = frozenset(category for _pattern, category in _MARKER_FAMILIES)


def marker_category(marker: str | None) -> str | None:
    """The fixed category of a detector marker, or None for a form not listed here."""
    if not marker:
        return None
    for pattern, category in _MARKER_FAMILIES:
        if pattern.fullmatch(marker):
            return category
    return None


HORIZON: Final = timedelta(days=14)
CURSOR_KEY: Final = "commitment_candidates:cursor"
#: Pages read per run. The 14-day floor bounds catch-up; the next run continues.
MAX_PAGES: Final = 20
TAINT: Final = "untrusted_origin"
LIVE_STATES: Final = ("pending", "carded")

#: One page of the relay: ``{"spans": [...], "cursor": {...} | None, "more": bool}``.
SpanReader = Callable[[Mapping[str, str] | None], Awaitable[Mapping[str, Any]]]


#: Dark until the signed-confirm composition is installed (bu-atc3xi), together with
#: the ``relationship.commitment_candidate`` ConfirmSource. While False no span is
#: read and no candidate content is created without a surface to answer it.
COMMITMENT_CANDIDATES_LIVE: Final = False


class SpansUnavailable(Exception):
    """The owner-chat projection could not be read; the cursor must not move."""


def relay_span_reader(switchboard_url: Callable[[], str | None]) -> SpanReader:
    """Read pages through Switchboard's ``owner_chat.spans`` relay as Relationship.

    ``switchboard_url`` is resolved on the first read, so a dark run needs no config.
    Any refusal or transport failure is ``SpansUnavailable``.
    """
    from butlers.core.service_plane.client import RelayUnavailable, call_relay
    from butlers.core.service_plane.registry import OWNER_CHAT_SPANS
    from butlers.core.service_plane.relays import RelayRefused

    async def read(cursor: Mapping[str, str] | None) -> Mapping[str, Any]:
        url = switchboard_url()
        if not url:
            raise SpansUnavailable("switchboard_url_unconfigured")
        args = (
            {}
            if cursor is None
            else {"after_received_at": cursor["received_at"], "after_inbox_id": cursor["inbox_id"]}
        )
        try:
            page = await call_relay(
                OWNER_CHAT_SPANS, args, daemon="relationship", switchboard_url=url
            )
        except (RelayRefused, RelayUnavailable, OSError) as exc:
            # OSError: no projected relay token in this workload.
            raise SpansUnavailable(type(exc).__name__) from None
        if not isinstance(page, Mapping) or not isinstance(page.get("spans"), list):
            raise SpansUnavailable("malformed_page")
        return page

    return read


def source_key(
    *, channel: str, endpoint: str, chat: str, provider_message_id: str, direction: str
) -> str:
    """One-way key of a source message and direction (REQ-commitment-lifecycle-012)."""
    material = json.dumps([channel, endpoint, chat, provider_message_id, direction])
    return hashlib.sha256(material.encode()).hexdigest()


def _event_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _text(span: Mapping[str, Any], field: str) -> str | None:
    value = span.get(field)
    return value if isinstance(value, str) and value else None


async def produce_candidate(
    pool: asyncpg.Pool, span: Mapping[str, Any], *, owner_zone: ZoneInfo, now: datetime
) -> str:
    """Write at most one candidate for one span; return a content-free outcome code."""
    channel = _text(span, "source_channel")
    endpoint = _text(span, "endpoint_identity")
    chat = _text(span, "chat_identity")
    counterparty = _text(span, "counterparty_identity")
    message_id = _text(span, "provider_message_id")
    ingestion_id = _text(span, "ingestion_event_id")
    body = _text(span, "text")
    event_time = _event_time(span.get("event_time"))
    if (
        channel not in CHANNELS
        or endpoint is None
        or chat is None
        or counterparty is None
        or message_id is None
        or ingestion_id is None
        or body is None
        or event_time is None
    ):
        return "malformed"
    if event_time > now or now - event_time >= HORIZON:
        return "outside_horizon"
    key = source_key(
        channel=channel,
        endpoint=endpoint,
        chat=chat,
        provider_message_id=message_id,
        direction=DIRECTION,
    )
    seen = await pool.fetchval(
        "SELECT 1 FROM relationship.commitment_candidates WHERE source_key = $1", key
    )
    if seen:
        return "seen"
    contact = await resolve_contact_by_channel(pool, channel, counterparty, raise_on_error=True)
    if (
        contact is None
        or contact.entity_id is None
        or contact.is_unidentified
        or "owner" in contact.roles
    ):
        return "counterparty_unknown"
    signal = _commitments.detect_commitment(body, now=event_time.astimezone(owner_zone))
    if signal is None or signal.direction != DIRECTION:
        return "no_commitment"
    action = signal.action_description
    if not action or len(action) > MAX_ACTION_CHARS:
        return "action_too_long"
    marker = marker_category(signal.marker)
    if marker is None:
        # A detector form with no category here is refused, never stored as text;
        # rel_042's CHECK would refuse it too and poison the page.
        return "marker_unrecognised"
    fingerprint = commitment_fingerprint(
        source=COMMITMENT_SOURCE,
        counterparty_entity_id=str(contact.entity_id),
        action_description=action,
    )
    if await get_active_condition(pool, source=COMMITMENT_SOURCE, fingerprint=fingerprint):
        return "already_tracked"
    # Any unique conflict (the source key, or a live candidate's fingerprint) means a
    # concurrent or earlier run already proposed this; nothing else is written.
    inserted = await pool.fetchval(
        """
        INSERT INTO relationship.commitment_candidates (
            source_key, state, kind, direction, counterparty_entity_id,
            action_description, deadline, confidence, marker, fingerprint, channel,
            evidence_ref, taint, event_time, lapse_at)
        VALUES ($1, 'pending', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12,
                $13::timestamptz, $13::timestamptz + make_interval(hours => 336))
        ON CONFLICT DO NOTHING
        RETURNING 1
        """,
        key,
        signal.kind,
        DIRECTION,
        contact.entity_id,
        action,
        signal.deadline,
        signal.confidence,
        marker,
        fingerprint,
        channel,
        json.dumps({"ingestion_event_id": ingestion_id, "provider_message_id": message_id}),
        TAINT,
        event_time,
    )
    return "created" if inserted else "duplicate"


_CLEAR_CONTENT: Final = """
    kind = NULL, direction = NULL, counterparty_entity_id = NULL, action_description = NULL,
    deadline = NULL, confidence = NULL, marker = NULL, fingerprint = NULL, channel = NULL,
    evidence_ref = NULL, taint = NULL, card_snapshot = NULL, card_expires_at = NULL
"""


async def lapse_and_purge(pool: asyncpg.Pool) -> dict[str, int]:
    """Lapse live candidates past ``lapse_at`` silently; delete aged tombstones."""
    lapsed = await pool.fetch(
        f"""
        UPDATE relationship.commitment_candidates
        SET state = 'lapsed', terminal_at = now(), {_CLEAR_CONTENT}
        WHERE state IN ('pending', 'carded') AND lapse_at <= now()
        RETURNING id
        """
    )
    purged = await pool.fetch(
        """
        DELETE FROM relationship.commitment_candidates
        WHERE state NOT IN ('pending', 'carded')
          AND event_time < now() - make_interval(hours => 336)
        RETURNING id
        """
    )
    return {"lapsed": len(lapsed), "purged": len(purged)}


async def scan(pool: asyncpg.Pool, *, read_spans: SpanReader, live: bool) -> dict[str, Any]:
    """One run: lapse and purge, then (when live) reduce pages after the stored cursor.

    The cursor is written only after a page's candidates are committed, so a crash
    re-reads that page and the unique keys make the replay a no-op. While not live
    no span is read and the cursor stays where it is.

    Poison span (note for activation): any database or resolver failure on one span
    stops the run without moving the cursor, so a span that fails every time blocks
    every later span until it falls behind the projection's 14-day floor. The run
    reports it only as a failed run. Known content limits (action length, marker
    category) are refused per span before any write, so they cannot poison a page.
    """
    result: dict[str, Any] = {**await lapse_and_purge(pool), "outcomes": {}}
    if not live:
        return {**result, "status": "skipped", "reason": "composition_absent"}
    owner_zone = ZoneInfo(await resolve_general_timezone(pool))
    outcomes: dict[str, int] = result["outcomes"]
    cursor = await state_get(pool, CURSOR_KEY)
    for _ in range(MAX_PAGES):
        try:
            page = await read_spans(cursor)
        except SpansUnavailable:
            return {**result, "status": "unavailable"}
        db_now = await pool.fetchval("SELECT now()")
        for span in page.get("spans") or []:
            outcome = await produce_candidate(pool, span, owner_zone=owner_zone, now=db_now)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        next_cursor = page.get("cursor")
        if next_cursor is not None and next_cursor != cursor:
            await state_set(pool, CURSOR_KEY, next_cursor)
            cursor = next_cursor
        if not page.get("more"):
            break
    return {**result, "status": "ok"}
