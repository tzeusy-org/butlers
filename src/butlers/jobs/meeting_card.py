"""Meeting card: one read-only timed Telegram card ~20 minutes before a meeting (bu-jv1moa.9).

Deterministic, **zero-LLM** Relationship job. For each calendar occurrence that starts within
:data:`CARD_LEAD` and has at least one other attendee, it renders a fixed template from the
prep envelope the relationship prep job already wrote under ``calendar/prep/<event_id>`` (last
met, what the owner owes, what the person owes the owner) and queues one timed insight. It sends
nothing itself: delivery, quiet hours, ``dnd``/``sleeping`` holds, budget and the daily cap all
belong to the insight broker's timed lane.

Selection
---------
Same occurrence rules as the meeting debrief: not cancelled, not all-day, not butler-generated,
not transparent, the owner did not decline, and at least one other attendee remains after the
owner's own entry, room resources and declined guests are removed. A recurring series is carded
per occurrence. A meeting whose other attendees are all non-``active`` (memorial, no_contact,
quiet) still gets a card, but their names and lines are withheld; it carries only the title,
time and place.

Idempotence
-----------
The dedup key is ``relationship:meeting-card:<event_id>:<start epoch>``; the broker's timed lane
refuses a second proposal for a key. A moved meeting has a new start, so it earns one new card
and the old one is never re-sent. The card is queued only inside its own lead window and expires
at the start, so a stale card cannot arrive after the meeting began.

Missing or stale prep
---------------------
No envelope, or one whose ``event_starts_at`` is not this occurrence's start, gives the minimal
card ("no prep context yet"). Prep content is never guessed.

Third-party text
----------------
Title, attendee names, commitment summaries and the location come from invites or other people.
They are stripped of markup and control characters and truncated. A location is never a link:
a URL shows only its host with the separators defanged, and any other text has its separators
defanged, so no chat client can auto-link it. The card names the dashboard Calendar page, whose
URL is built by :func:`butlers.core.dashboard_links.dashboard_link`.

Dark: :data:`MEETING_CARD_LIVE` is off, so the job returns ``{"status": "dark"}`` without reading
or writing, and no roster schedules ``meeting_card_push``. Activation adds the cron and flips the
flag together with the timed lane in one owner-approved change.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg

from butlers.core.dashboard_links import dashboard_link
from butlers.core.delivery_rendering import defang_delivery_text
from butlers.core.state import state_get
from butlers.core.temporal.calendar_provenance import (
    is_explicit_butler_generated,
    is_owner_attending,
    is_transparent,
)
from butlers.jobs.briefing import SGT
from butlers.jobs.calendar_prep import prep_key
from butlers.jobs.meeting_debrief import (
    _decode_metadata,
    _other_attendees,
    _resolve_people,
)

logger = logging.getLogger(__name__)

#: Master switch. Off: the job returns ``{"status": "dark"}`` without reading or writing.
MEETING_CARD_LIVE = False

#: The card is queued once the meeting is this close...
CARD_LEAD = timedelta(minutes=20)
#: ...and not for a meeting that starts sooner than this (it would arrive after it began).
MIN_LEAD = timedelta(minutes=1)

CARD_CATEGORY = "meeting-card"

_PRIORITY = 70
_MAX_TITLE = 80
_MAX_NAME = 40
_MAX_LINE = 90
_MAX_LOCATION = 60
_MAX_ATTENDEES = 4
_MAX_COMMITMENTS = 2
_UNSAFE = re.compile(r"[*_`\[\]<>\x00-\x1f\x7f]")

InsightProposer = Callable[..., Awaitable[dict[str, Any]]]
#: ``(pool, dedup_keys=..., reason=..., now=...) -> number withdrawn`` (the broker's withdraw path).
InsightWithdrawer = Callable[..., Awaitable[int]]

#: Context signals a card passes: an owner in a meeting or focused is who it is for. ``dnd`` and
#: ``sleeping`` still hold it. The lane honors this only for this producer's category.
PASSES_CONTEXT = ["meeting", "focused"]
_KEY_PREFIX = "relationship:meeting-card:"

_PENDING_CARDS_SQL = """
    SELECT dedup_key FROM insight_candidates
    WHERE origin_butler = 'relationship' AND category = 'meeting-card'
      AND lane = 'timed' AND status = 'pending' AND claim_id IS NULL
"""

_STILL_SCHEDULED_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM calendar_events ce
        WHERE ce.id = $1 AND ce.recurrence_rule IS NULL
          AND ce.status <> 'cancelled' AND ce.starts_at = $2
        UNION ALL
        SELECT 1 FROM calendar_event_instances ci
        JOIN calendar_events ce ON ce.id = ci.event_id
        WHERE ce.id = $1 AND ce.recurrence_rule IS NOT NULL
          AND ce.status <> 'cancelled' AND ci.status <> 'cancelled' AND ci.starts_at = $2
    )
"""

_OCCURRENCE_SQL = """
    SELECT ce.id AS event_id, ce.title AS title, ce.location AS location,
           ce.starts_at AS starts_at, ce.metadata AS metadata
    FROM calendar_events ce
    WHERE ce.recurrence_rule IS NULL
      AND ce.status <> 'cancelled'
      AND ce.all_day = false
      AND ce.starts_at > $1
      AND ce.starts_at <= $2
    UNION ALL
    SELECT ce.id, ce.title, ce.location, ci.starts_at, ce.metadata || ci.metadata
    FROM calendar_event_instances ci
    JOIN calendar_events ce ON ce.id = ci.event_id
    WHERE ce.recurrence_rule IS NOT NULL
      AND ce.status <> 'cancelled'
      AND ci.status <> 'cancelled'
      AND ce.all_day = false
      AND ci.starts_at > $1
      AND ci.starts_at <= $2
    ORDER BY starts_at
"""


def clean_text(value: Any, limit: int) -> str:
    """Third-party text as one plain line: markup and control characters removed, truncated."""
    if not isinstance(value, str):
        return ""
    text = _UNSAFE.sub("", " ".join(value.split()))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def inert_text(value: Any, limit: int) -> str:
    """Third-party text as one plain line no chat client can link: cleaned, then defanged.

    Insight delivery sends the text as written (no ``notify`` envelope, so ``render_links_inert``
    never runs), so every piece of invite or person-authored text passes through here.
    """
    return defang_delivery_text(clean_text(value, limit))


def inert_location(value: Any) -> str | None:
    """A location that no chat client can turn into a link, or ``None`` when empty.

    A URL (an online meeting) shows only its host; any other text keeps its words. Both have
    their ``.``, ``:`` and ``@`` separators defanged, so a bare host or address is not linked.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "://" in candidate.split()[0]:
        try:
            host = urlsplit(candidate.split()[0]).hostname
        except ValueError:
            host = None
        return f"online ({defang_delivery_text(host)})" if host else "online"
    text = inert_text(candidate, _MAX_LOCATION)
    return text or None


def _day_label(moment: datetime, now: datetime) -> str:
    local, today = moment.astimezone(SGT), now.astimezone(SGT).date()
    days = (today - local.date()).days
    if days == 0:
        return "today"
    return f"{local.day} {local.strftime('%b')}"


def _stale(envelope: Any, starts_at: datetime) -> bool:
    if not isinstance(envelope, Mapping):
        return True
    raw = envelope.get("event_starts_at")
    if not isinstance(raw, str):
        return True
    try:
        stamped = datetime.fromisoformat(raw)
    except ValueError:
        return True
    return stamped.tzinfo is None or stamped != starts_at


def _commitment_lines(entries: Sequence[Mapping[str, Any]], direction: str, label: str) -> str:
    lines = [
        inert_text(c.get("summary"), _MAX_LINE)
        for c in entries
        if isinstance(c, Mapping) and c.get("direction") == direction
    ]
    lines = [line for line in lines if line][:_MAX_COMMITMENTS]
    return f"{label}: {'; '.join(lines)}" if lines else ""


def render_card(
    *,
    title: str,
    starts_at: datetime,
    location: Any,
    attendees: Sequence[Mapping[str, Any]],
    envelope: Any,
    now: datetime,
    link: str | None,
) -> str:
    """The card text. *attendees* are the people to name (``entity_id`` may be ``None``).

    Pure: the same inputs always give the same text, and nothing here reads a database.
    """
    local = starts_at.astimezone(SGT)
    heading = inert_text(title, _MAX_TITLE) or "Meeting"
    lines = [f"{heading} at {local:%H:%M} ({_day_label(starts_at, now)})"]
    place = inert_location(location)
    if place:
        lines.append(f"Where: {place}")
    prep_by_entity = {}
    if not _stale(envelope, starts_at):
        prep_by_entity = {
            str(a.get("entity_id")): a
            for a in (envelope or {}).get("attendees", [])
            if isinstance(a, Mapping)
        }
    shown = list(attendees)[:_MAX_ATTENDEES]
    for person in shown:
        name = inert_text(person.get("name"), _MAX_NAME) or inert_text(person.get("email"), 40)
        prep = prep_by_entity.get(str(person.get("entity_id")))
        entry = name or "Someone"
        if prep and isinstance(prep.get("last_met"), str):
            try:
                met = datetime.fromisoformat(prep["last_met"])
                entry += f" (last met {met.astimezone(SGT).day} {met.astimezone(SGT):%b})"
            except ValueError:
                pass
        lines.append(f"With: {entry}")
        if prep:
            commitments = [c for c in prep.get("commitments", []) if isinstance(c, Mapping)]
            owed = _commitment_lines(commitments, "owner_to_other", "You owe")
            waiting = _commitment_lines(commitments, "other_to_owner", "Waiting on")
            lines.extend(line for line in (owed, waiting) if line)
    if len(attendees) > len(shown):
        lines.append(f"+{len(attendees) - len(shown)} more")
    if not prep_by_entity or not any(
        str(p.get("entity_id")) in prep_by_entity for p in shown if p.get("entity_id")
    ):
        lines.append("No prep context yet.")
    if link:
        lines.append(f"Calendar: {link}")
    return "\n".join(lines)


async def _withdraw_superseded(
    pool: asyncpg.Pool, insight_withdrawer: InsightWithdrawer, now: datetime
) -> int:
    """Withdraw pending cards whose meeting moved, was cancelled or no longer exists.

    A card's key names its event and start. If no live occurrence starts then, the card describes
    a meeting that is not happening at that time, so it is withdrawn through the broker's path
    (never deleted) and the moved meeting's new card is queued by the normal run.
    """
    try:
        pending = [r["dedup_key"] for r in await pool.fetch(_PENDING_CARDS_SQL)]
    except asyncpg.PostgresError:
        return 0
    stale: list[str] = []
    for key in pending:
        if not key.startswith(_KEY_PREFIX):
            continue
        try:
            event_id, epoch = key[len(_KEY_PREFIX) :].split(":")
            event_uuid = UUID(event_id)
            start = datetime.fromtimestamp(int(epoch), UTC)
            live = await pool.fetchval(_STILL_SCHEDULED_SQL, event_uuid, start)
        except (ValueError, OverflowError, asyncpg.PostgresError):
            continue
        if not live:
            stale.append(key)
    if not stale:
        return 0
    try:
        return int(
            await insight_withdrawer(pool, dedup_keys=stale, reason="meeting_changed", now=now)
        )
    except Exception:
        logger.exception("meeting_card: withdrawing superseded cards raised")
        return 0


async def run_meeting_card(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    insight_proposer: InsightProposer,
    insight_withdrawer: InsightWithdrawer,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue one timed card for each meeting starting within the lead window.

    First withdraws any pending card whose meeting moved or was cancelled.
    """
    del job_args
    if not MEETING_CARD_LIVE:
        return {"status": "dark"}
    now = now or datetime.now(UTC)
    withdrawn = await _withdraw_superseded(pool, insight_withdrawer, now)
    try:
        rows = await pool.fetch(_OCCURRENCE_SQL, now + MIN_LEAD, now + CARD_LEAD)
    except asyncpg.exceptions.UndefinedTableError:
        return {"status": "ok", "considered": 0, "proposed": 0, "skipped": "no_calendar"}

    staged: list[tuple[Any, list[dict[str, str]]]] = []
    emails: set[str] = set()
    for row in rows:
        meta = _decode_metadata(row["metadata"])
        if (
            is_explicit_butler_generated(meta)
            or is_transparent(meta)
            or not is_owner_attending(meta)
        ):
            continue
        others = _other_attendees(meta)
        if others:
            staged.append((row, others))
            emails.update(a["email"] for a in others)
    people = await _resolve_people(pool, emails)

    # Anyone who resolves to the owner is the owner for eligibility: a meeting whose only other
    # address is the owner's own is a solo block and gets no card. A non-active person is still
    # a real attendee (the meeting is carded) but is never named.
    carded: list[tuple[Any, list[dict[str, Any]]]] = []
    for row, others in staged:
        named: list[dict[str, Any]] = []
        real = 0
        for att in others:
            person = people.get(att["email"])
            if person is None:
                real += 1
                named.append(
                    {"entity_id": None, "name": att["display_name"], "email": att["email"]}
                )
            elif person["is_owner"]:
                continue
            else:
                real += 1
                if person["posture"] == "active":
                    named.append({**person, "email": att["email"]})
        if real:
            carded.append((row, named))

    counts = {
        "considered": len(carded),
        "proposed": 0,
        "duplicate": 0,
        "failed": 0,
        "withdrawn": withdrawn,
    }
    link = dashboard_link("/calendar")
    for row, named in carded:
        try:
            envelope = await state_get(pool, prep_key(str(row["event_id"])))
        except asyncpg.PostgresError:
            envelope = None
        message = render_card(
            title=row["title"],
            starts_at=row["starts_at"],
            location=row["location"],
            attendees=named,
            envelope=envelope,
            now=now,
            link=link,
        )
        try:
            result = await insight_proposer(
                pool,
                origin_butler="relationship",
                priority=_PRIORITY,
                category=CARD_CATEGORY,
                dedup_key=(
                    f"relationship:meeting-card:{row['event_id']}:{int(row['starts_at'].timestamp())}"
                ),
                message=message,
                expires_at=row["starts_at"],
                lane="timed",
                deliver_after=now,
                metadata={"passes_context": list(PASSES_CONTEXT)},
                now=now,
            )
        except Exception:
            logger.exception("meeting_card: proposing a card raised")
            counts["failed"] += 1
            continue
        status = (result or {}).get("status", "error")
        if status == "accepted":
            counts["proposed"] += 1
        elif status == "duplicate":
            counts["duplicate"] += 1
        else:
            counts["failed"] += 1
            logger.warning(
                "meeting_card: card not queued (%s)", (result or {}).get("reason", status)
            )
    return {"status": "ok", **counts}
