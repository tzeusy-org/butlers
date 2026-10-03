"""Meeting debrief answers: turning the owner's reply into commitments or a recorded "none".

bu-q7vx1q.12. ``butlers.jobs.meeting_debrief`` records and prompts; this module records the
owner's answer. Two MCP tools in ``roster/relationship/modules/tools.py`` (group
``tracking``) wrap the functions here, and the ``meeting-debrief`` session skill tells the
session when to call them.

The session does not get to invent a counterparty. Each commitment's counterparty must be
an attendee frozen into the debrief's snapshot (or null), so a reply cannot attach an
obligation to someone who was not in the meeting. Everything is validated before the first
write, so a bad item cannot leave a half-captured answer; the commitment fingerprint makes
a retry after a mid-way failure re-confirm rather than duplicate.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core.commitments import (
    COMMITMENT_DIRECTIONS,
    COMMITMENT_KINDS,
    COMMITMENT_SPHERES,
    create_commitment,
)
from butlers.jobs.meeting_debrief import active_entity_ids, decode_attendees

logger = logging.getLogger(__name__)

__all__ = [
    "DEBRIEF_CONFIDENCE",
    "DEBRIEF_EVIDENCE_SOURCE",
    "meeting_debrief_answer",
    "meeting_debrief_pending",
]

COMMITMENT_SOURCE = "relationship:commitment"

#: ``evidence_opened.source`` for commitments opened from a debrief answer.
DEBRIEF_EVIDENCE_SOURCE = "meeting_debrief"

#: The owner stated it in answer to an explicit question, so it clears the surfacing floor.
DEBRIEF_CONFIDENCE = 0.9

_ANSWERABLE_STATES = ("pending", "expired")


async def meeting_debrief_pending(pool: asyncpg.Pool) -> dict[str, Any]:
    """List prompted, unanswered debriefs in the order the owner was shown them.

    Attendees who are no longer ``active`` are left out, so the session never names them.
    """
    rows = await pool.fetch(
        """
        SELECT id, event_id, occurrence_start, event_title, attendees, prompted_at, batch_position
        FROM meeting_debriefs
        WHERE state = 'pending' AND prompted_at IS NOT NULL
        ORDER BY prompted_at DESC, batch_position, id
        """
    )
    decoded = [(r, decode_attendees(r["attendees"])) for r in rows]
    active = await active_entity_ids(
        pool, {a["entity_id"] for _, atts in decoded for a in atts if a["entity_id"]}
    )
    debriefs = []
    for row, atts in decoded:
        kept = [a for a in atts if a["entity_id"] is None or a["entity_id"] in active]
        if not kept:
            continue
        debriefs.append(
            {
                "debrief_id": str(row["id"]),
                "number": row["batch_position"],
                "event_title": row["event_title"],
                "occurrence_start": row["occurrence_start"].isoformat(),
                "prompted_at": row["prompted_at"].isoformat(),
                "attendees": kept,
            }
        )
    return {"debriefs": debriefs}


def _prepare_items(
    items: list[dict[str, Any]], attendees: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate answer items against the snapshot. Raises ``ValueError`` on the first bad one."""
    known = {a["entity_id"] for a in attendees if a["entity_id"]}
    sole = attendees[0]["entity_id"] if len(attendees) == 1 else None
    prepared = []
    for index, item in enumerate(items, start=1):
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError(f"item {index}: summary is required")
        kind = item.get("kind") or "promise"
        direction = item.get("direction") or "owner_to_other"
        if kind not in COMMITMENT_KINDS:
            raise ValueError(f"item {index}: kind must be one of {sorted(COMMITMENT_KINDS)}")
        if direction not in COMMITMENT_DIRECTIONS:
            raise ValueError(
                f"item {index}: direction must be one of {sorted(COMMITMENT_DIRECTIONS)}"
            )
        sphere = item.get("sphere")
        if sphere is not None and sphere not in COMMITMENT_SPHERES:
            raise ValueError(f"item {index}: sphere must be one of {sorted(COMMITMENT_SPHERES)}")
        deadline = item.get("deadline")
        if deadline is not None:
            try:
                datetime.fromisoformat(str(deadline))
            except ValueError:
                raise ValueError(f"item {index}: deadline must be an ISO-8601 timestamp") from None
        counterparty = item.get("counterparty_entity_id")
        if counterparty is not None and str(counterparty) not in known:
            raise ValueError(f"item {index}: counterparty is not an attendee of this meeting")
        if counterparty is None and direction != "self":
            counterparty = sole
        if direction == "self":
            counterparty = None
        prepared.append(
            {
                "summary": summary.strip(),
                "kind": kind,
                "direction": direction,
                "sphere": sphere,
                "deadline": deadline,
                "counterparty_entity_id": str(counterparty) if counterparty else None,
            }
        )
    return prepared


async def meeting_debrief_answer(
    pool: asyncpg.Pool,
    *,
    debrief_id: str,
    commitments: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Record the owner's answer to one debrief.

    An empty or missing *commitments* list records ``none_agreed``; otherwise each item
    (``summary`` plus optional ``kind``, ``direction``, ``counterparty_entity_id``,
    ``deadline``, ``sphere``) opens a commitment whose evidence names the meeting.
    Returns ``{"status": "none_agreed"|"captured"|"already_answered"|"not_found"|"invalid"}``.
    """
    try:
        debrief_uuid = uuid.UUID(str(debrief_id))
    except ValueError:
        return {"status": "not_found", "debrief_id": str(debrief_id)}

    row = await pool.fetchrow(
        """
        SELECT id, event_id, occurrence_start, event_title, attendees, state
        FROM meeting_debriefs WHERE id = $1
        """,
        debrief_uuid,
    )
    if row is None:
        return {"status": "not_found", "debrief_id": str(debrief_id)}
    if row["state"] not in _ANSWERABLE_STATES:
        return {"status": "already_answered", "state": row["state"]}

    opened: list[dict[str, Any]] = []
    items = commitments or []
    if items:
        try:
            prepared = _prepare_items(items, decode_attendees(row["attendees"]))
        except ValueError as exc:
            return {"status": "invalid", "reason": str(exc)}

        occurrence_start: datetime = row["occurrence_start"]
        evidence = {
            "source": DEBRIEF_EVIDENCE_SOURCE,
            "event_id": str(row["event_id"]),
            "occurrence_start": occurrence_start.isoformat(),
            "debrief_id": str(row["id"]),
            "event_title": row["event_title"],
            "session_id": session_id,
        }
        for item in prepared:
            try:
                transition = await create_commitment(
                    pool,
                    source=COMMITMENT_SOURCE,
                    summary=item["summary"],
                    kind=item["kind"],
                    direction=item["direction"],
                    counterparty_entity_id=item["counterparty_entity_id"],
                    confidence=DEBRIEF_CONFIDENCE,
                    evidence_opened=dict(evidence),
                    action_description=item["summary"],
                    deadline=item["deadline"],
                    sphere=item["sphere"],
                )
            except ValueError as exc:
                return {"status": "invalid", "reason": str(exc)}
            if transition is None:  # unreachable: DEBRIEF_CONFIDENCE is above the creation floor
                return {"status": "invalid", "reason": "commitment was not created"}
            opened.append(
                {
                    "status": (
                        "created"
                        if transition.transition in ("opened", "reopened")
                        else "confirmed"
                    ),
                    "fingerprint": transition.fingerprint,
                    "counterparty_entity_id": item["counterparty_entity_id"],
                }
            )

    state = "captured" if opened else "none_agreed"
    await pool.execute(
        """
        UPDATE meeting_debriefs
        SET state = $2, answered_at = now(), session_id = $3
        WHERE id = $1 AND state = ANY($4::text[])
        """,
        debrief_uuid,
        state,
        session_id,
        list(_ANSWERABLE_STATES),
    )
    return {"status": state, "debrief_id": str(row["id"]), "commitments": opened}
