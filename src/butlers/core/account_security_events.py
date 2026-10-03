"""Account-security sensor: publish typed events and record the owner's answer.

bu-q7vx1q.10. ``butlers.account_security`` classifies from metadata;
this module is the I/O half. It never sees message bodies or bearer material:
the only inputs are the already-scrubbed ``normalized_text`` subject line, the
normalized sender, and (when the connector kept it) the Authentication-Results
header.

Slices landed: the event publish (:func:`publish_security_event`) and the
owner-answer door (:func:`record_security_answer`: ``yes`` is a no-op, ``no``
opens a fleet case carrying the provider recovery door). The interactive
"was this you?" prompt that calls :func:`record_security_answer` is deferred.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from butlers.account_security import (
    PROVIDER_RECOVERY_DOORS,
    AccountSecurityClassification,
    classify_account_security,
)
from butlers.core import fleet_cases

logger = logging.getLogger(__name__)

SECURITY_EVENT_TYPE = "switchboard.security_event"
SECURITY_CASE_PREFIX = "account_security:"
_SUBJECT_PREFIX = "Subject:"


def _subject_from_normalized_text(normalized_text: str | None) -> str:
    first_line = (normalized_text or "").split("\n", 1)[0].strip()
    if not first_line.startswith(_SUBJECT_PREFIX):
        return ""
    return html.unescape(first_line[len(_SUBJECT_PREFIX) :]).strip()


def _auth_results_from_raw(raw: Any) -> str | None:
    """Find Authentication-Results in a Gmail ``messages.get`` payload, if kept."""
    if not isinstance(raw, Mapping):
        return None
    payload = raw.get("payload")
    entries = payload.get("headers") if isinstance(payload, Mapping) else raw.get("headers")
    if isinstance(entries, Mapping):
        entries = [{"name": k, "value": v} for k, v in entries.items()]
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, Mapping) and str(entry.get("name", "")).lower() == (
            "authentication-results"
        ):
            value = entry.get("value")
            return value if isinstance(value, str) else None
    return None


def classify_ingest_record(
    *,
    source_channel: str,
    sender_address: str,
    normalized_text: str | None,
    raw: Any,
) -> AccountSecurityClassification | None:
    """Classify an accepted ingest record; ``None`` for anything not an email alert."""
    if source_channel != "email":
        return None
    auth_results = _auth_results_from_raw(raw)
    return classify_account_security(
        sender_address,
        _subject_from_normalized_text(normalized_text),
        headers={"Authentication-Results": auth_results} if auth_results else None,
    )


async def publish_security_event(
    pool: Any,
    classification: AccountSecurityClassification,
    *,
    source_request_id: str,
    external_event_id: str | None,
    observed_at: datetime | None,
) -> str | None:
    """Publish one ``switchboard.security_event``; return its id, or ``None`` if a duplicate.

    Dedup is on (provider, kind, external_event_id): a connector re-fetch that
    slips past ingest dedupe never produces a second event. Without an
    external_event_id the source request id stands in.
    """
    from butlers.core_tools._domain_events import (
        _SwitchboardInProcessRouteClient,
        publish_domain_event,
    )

    dedup_ref = external_event_id or source_request_id
    already = await pool.fetchval(
        """
        SELECT 1 FROM public.domain_events
        WHERE event_type = $1
          AND payload ->> 'provider' = $2
          AND payload ->> 'kind' = $3
          AND coalesce(payload ->> 'external_event_id', payload ->> 'source_request_id') = $4
        LIMIT 1
        """,
        SECURITY_EVENT_TYPE,
        classification.provider,
        classification.kind,
        dedup_ref,
    )
    if already:
        return None
    payload: dict[str, Any] = {
        "kind": classification.kind,
        "provider": classification.provider,
        "provider_domain": classification.provider_domain,
        "sender_verification": classification.sender_verification,
        "source_request_id": source_request_id,
    }
    if external_event_id:
        payload["external_event_id"] = external_event_id
    if observed_at is not None:
        payload["observed_at"] = observed_at.isoformat()
    result = await publish_domain_event(
        pool,
        _SwitchboardInProcessRouteClient(pool),
        event_type=SECURITY_EVENT_TYPE,
        source_butler="switchboard",
        payload=payload,
    )
    if result.get("status") != "ok":
        logger.warning("account-security event publish refused: %s", result.get("error"))
        return None
    return str(result["event_id"])


async def record_security_answer(pool: Any, *, event_id: str, answer: str) -> dict[str, Any]:
    """Record the owner's "was this you?" answer. Idempotent per event id.

    ``yes`` is acknowledged and changes nothing. ``no`` opens (or reuses) one
    fleet case keyed on the event and contributes a single evidence row holding
    the provider's static recovery door. A repeated ``no`` returns the same
    case; a ``yes`` after a ``no`` never closes it (the conservative direction).

    Must run on Switchboard's own pool: ``public.fleet_cases`` INSERT is RLS-restricted to
    ``butler_switchboard_rw`` (core_217). Any other caller forwards through the
    ``open_case`` fleet-case tool instead of calling this directly.
    """
    normalized = answer.strip().lower()
    if normalized not in {"yes", "no"}:
        return {"status": "error", "error": "answer must be 'yes' or 'no'."}
    event = await pool.fetchrow(
        "SELECT id, payload FROM public.domain_events WHERE id = $1::uuid AND event_type = $2",
        event_id,
        SECURITY_EVENT_TYPE,
    )
    if event is None:
        return {"status": "error", "error": f"No security event with id={event_id!r}."}
    if normalized == "yes":
        return {"status": "ok", "answer": "yes", "case_id": None}

    payload = event["payload"]
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    provider = str(payload.get("provider", ""))
    key = f"{SECURITY_CASE_PREFIX}{event_id}"
    try:
        case = await fleet_cases.open_case(pool, correlation_key=key)
    except fleet_cases.FleetCaseError:
        case = await fleet_cases.find_open_case(pool, key)
        if case is None:
            raise
    await fleet_cases.contribute_evidence(
        pool,
        case_id=str(case["id"]),
        contributor="switchboard",
        kind="account_security_event",
        ref=event_id,
        payload={
            "provider": provider,
            "kind": payload.get("kind"),
            "recovery_door": PROVIDER_RECOVERY_DOORS.get(provider),
        },
    )
    return {"status": "ok", "answer": "no", "case_id": str(case["id"])}
