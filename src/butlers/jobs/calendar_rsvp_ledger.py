"""The docket's agreement ledger writer (bu-5ii4gj.9 S5, REQ-calendar-invitation-docket-006).

Records, per policy and version, how the owner dealt with each prepared answer. It is the only
writer, and only through ``public.calendar_rsvp_ledger_record`` (core_390), which is idempotent per
card. The ledger is informational: nothing here or anywhere reads it to widen autonomy, auto-approve
or create a policy, and a contract test pins that only this module and the dashboard's read name it.

Outcomes: ``approved_as_prepared``, ``owner_rejected``, ``answered_same`` (the owner answered
outside the card with the card's own verdict) and ``answered_elsewhere``. There is no "edited"
outcome, and a system rejection records nothing unless the owner's own answer made the card stale.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

OUTCOMES = ("approved_as_prepared", "owner_rejected", "answered_same", "answered_elsewhere")
_APPROVED = frozenset({"approved", "executed"})
_ANSWER_FOR_VERDICT = {"accepted": "accepted", "declined": "declined", "tentative": "tentative"}


# The actors the approvals path records for the owner's own decision: the dashboard's approve route
# and the owner's Telegram card. Anything else (``system:*`` sweeps, expiry, this job) is not the
# owner, and an unknown actor is never counted as one.
OWNER_ACTORS = frozenset({"dashboard:rest-api", "owner@telegram"})
_RECORDED_LIMIT = 4096
_recorded: dict[str, None] = {}


def decided_outcome(status: str | None, decided_by: str | None) -> str | None:
    """The outcome of a card the owner decided, or ``None`` for anything else."""
    if decided_by not in OWNER_ACTORS:
        return None
    if status in _APPROVED:
        return "approved_as_prepared"
    if status == "rejected":
        return "owner_rejected"
    return None


def answered_outcome(verdict: str | None, current_status: str | None) -> str | None:
    """The outcome when the owner answered the invitation outside the card, else ``None``.

    A card is parked for an unanswered invitation, so any provider answer now is the owner's own.
    """
    status = (current_status or "").strip().lower()
    if status in ("", "needsaction") or status not in _ANSWER_FOR_VERDICT.values():
        return None
    return (
        "answered_same"
        if _ANSWER_FOR_VERDICT.get(verdict or "") == status
        else "answered_elsewhere"
    )


def owner_response(rows: Sequence[Mapping[str, Any]], entry_id: str) -> str | None:
    """The owner's own response status on one calendar row, ``None`` when it is not there."""
    for row in rows:
        if str(row.get("instance_id")) != entry_id:
            continue
        metadata = row.get("event_metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except ValueError:
                return None
        attendees = metadata.get("attendees") if isinstance(metadata, Mapping) else None
        for attendee in attendees if isinstance(attendees, list) else []:
            if isinstance(attendee, Mapping) and attendee.get("self") in (True, "true"):
                raw = attendee.get("response_status", attendee.get("responseStatus"))
                return raw if isinstance(raw, str) else None
    return None


async def record_outcome(
    pool: Any, *, action_id: Any, marker: Mapping[str, Any], outcome: str | None
) -> bool | None:
    """Record one outcome: ``True`` new, ``False`` already recorded, ``None`` nothing to record.

    Raises :class:`LedgerUnavailable` when the write fails."""
    if outcome is None:
        return None
    if outcome not in OUTCOMES:
        raise ValueError(f"not a ledger outcome: {outcome!r}")
    key = str(action_id)
    if key in _recorded:
        return False  # already written by this process: no database round trip
    try:
        written = bool(
            await pool.fetchval(
                "SELECT public.calendar_rsvp_ledger_record($1, $2, $3, $4)",
                action_id if isinstance(action_id, uuid.UUID) else uuid.UUID(str(action_id)),
                uuid.UUID(str(marker["policy_id"])),
                int(marker["policy_version"]),
                outcome,
            )
        )
        _recorded[key] = None
        if len(_recorded) > _RECORDED_LIMIT:
            _recorded.pop(next(iter(_recorded)))
        return written
    except Exception:  # noqa: BLE001 - the ledger is informational: a failure is counted, not fatal
        logger.warning("rsvp ledger: outcome not recorded", exc_info=True)
        raise LedgerUnavailable from None


class LedgerUnavailable(Exception):
    """The ledger write failed; the docket counts it and carries on."""
